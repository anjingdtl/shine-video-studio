//! Controlled execution of the repository's own maintenance scripts.
//!
//! The launcher never assembles a command line and never asks a shell to
//! interpret one. It resolves one of a fixed set of repository scripts, checks
//! that the resolved file is a regular file inside the installation it belongs
//! to, and runs it with an argument *array* and no shell. That keeps the
//! deployment plane declarative: a deployment manifest can never smuggle a
//! command into the launcher.
//!
//! Success is decided by the exit code and by the machine readable receipts
//! the scripts themselves write. The launcher never parses the human readable
//! Chinese progress text to decide whether an installation worked.

use std::ffi::OsString;
use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};

use crate::path_safety::{regular_file_without_reparse, reject_reparse_components};

const MAX_CAPTURED_BYTES: usize = 256 * 1024;
static RUN_SEQUENCE: AtomicU64 = AtomicU64::new(0);

/// The scripts the launcher is allowed to run.
///
/// Each entry is a fixed, repository owned file. There is no way to extend
/// this list from a manifest, an environment variable or user input.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ScriptKind {
    /// `scripts/bootstrap.ps1` — toolchain and runtime provisioning.
    Bootstrap,
    /// `scripts/install_once.ps1` — first-run install orchestration.
    InstallOnce,
    /// `scripts/start_shine.ps1` — API / worker / web lifecycle.
    StartShine,
    /// `scripts/stop_shine.py` — ownership checked shutdown.
    StopShine,
    /// `scripts/check_environment.ps1` — read-only environment report.
    CheckEnvironment,
}

impl ScriptKind {
    pub fn relative_path(self) -> &'static str {
        match self {
            Self::Bootstrap => "scripts/bootstrap.ps1",
            Self::InstallOnce => "scripts/install_once.ps1",
            Self::StartShine => "scripts/start_shine.ps1",
            Self::StopShine => "scripts/stop_shine.py",
            Self::CheckEnvironment => "scripts/check_environment.ps1",
        }
    }

    pub fn label(self) -> &'static str {
        match self {
            Self::Bootstrap => "环境与依赖准备",
            Self::InstallOnce => "一键安装",
            Self::StartShine => "启动工作台",
            Self::StopShine => "停止工作台",
            Self::CheckEnvironment => "环境检查",
        }
    }

    fn interpreter(self) -> Interpreter {
        match self {
            Self::StopShine => Interpreter::Python,
            _ => Interpreter::PowerShell,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Interpreter {
    PowerShell,
    Python,
}

/// One planned script run.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ScriptInvocation {
    pub kind: ScriptKind,
    pub arguments: Vec<String>,
    pub working_directory: PathBuf,
    pub timeout: Duration,
    /// Extra environment entries, applied to the child only.
    pub environment: Vec<(String, String)>,
}

impl ScriptInvocation {
    pub fn new(kind: ScriptKind, install_root: &Path) -> Self {
        Self {
            kind,
            arguments: Vec::new(),
            working_directory: install_root.to_path_buf(),
            timeout: default_timeout(kind),
            environment: Vec::new(),
        }
    }

    pub fn with_arguments(mut self, arguments: Vec<String>) -> Self {
        self.arguments = arguments;
        self
    }

    pub fn with_environment(mut self, key: &str, value: &str) -> Self {
        self.environment.push((key.to_owned(), value.to_owned()));
        self
    }

    pub fn with_timeout(mut self, timeout: Duration) -> Self {
        self.timeout = timeout;
        self
    }
}

/// The observable result of a script run.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct ExecutionOutcome {
    pub kind: String,
    pub label: String,
    pub argv: Vec<String>,
    pub exit_code: Option<i32>,
    pub duration_ms: u128,
    pub stdout_tail: String,
    pub stderr_tail: String,
    pub timed_out: bool,
    pub log_path: Option<PathBuf>,
}

impl ExecutionOutcome {
    pub fn succeeded(&self) -> bool {
        !self.timed_out && self.exit_code == Some(0)
    }

    /// A short, user facing explanation of what happened.
    pub fn summary(&self) -> String {
        if self.timed_out {
            return format!("{}超时，未确认完成。", self.label);
        }
        match self.exit_code {
            Some(0) => format!("{}已完成。", self.label),
            Some(code) => {
                let detail = self
                    .stderr_tail
                    .lines()
                    .rev()
                    .find(|line| !line.trim().is_empty())
                    .map(|line| line.trim().to_owned());
                match detail {
                    Some(detail) => format!("{}失败（退出码 {code}）：{detail}", self.label),
                    None => format!("{}失败（退出码 {code}）。", self.label),
                }
            }
            None => format!("{}未能启动。", self.label),
        }
    }
}

#[derive(Debug)]
pub enum RunnerError {
    /// The installation does not contain the requested script.
    ScriptMissing(String),
    /// The script path resolved outside the installation, or is not a file.
    ScriptNotTrusted(String),
    /// The interpreter for this script is not available on this machine.
    InterpreterMissing(String),
    /// The child could not be started at all.
    Spawn(String),
    /// The script could not be launched on this platform.
    UnsupportedPlatform(String),
}

impl std::fmt::Display for RunnerError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::ScriptMissing(path) => write!(formatter, "安装目录中缺少脚本：{path}"),
            Self::ScriptNotTrusted(path) => {
                write!(formatter, "脚本不是受信任的普通文件：{path}")
            }
            Self::InterpreterMissing(name) => write!(formatter, "本机缺少 {name}，无法执行该步骤"),
            Self::Spawn(message) => write!(formatter, "无法启动脚本进程：{message}"),
            Self::UnsupportedPlatform(message) => formatter.write_str(message),
        }
    }
}

impl std::error::Error for RunnerError {}

fn default_timeout(kind: ScriptKind) -> Duration {
    match kind {
        // Installing tools and models is slow but bounded; the script itself
        // reports progress and can be re-run.
        ScriptKind::Bootstrap | ScriptKind::InstallOnce => Duration::from_secs(3 * 60 * 60),
        ScriptKind::StartShine => Duration::from_secs(10 * 60),
        ScriptKind::StopShine => Duration::from_secs(5 * 60),
        ScriptKind::CheckEnvironment => Duration::from_secs(10 * 60),
    }
}

/// Resolve the absolute path of a trusted script inside an installation.
pub fn resolve_script(install_root: &Path, kind: ScriptKind) -> Result<PathBuf, RunnerError> {
    let relative = kind.relative_path();
    let candidate = install_root.join(relative);
    if !candidate.starts_with(install_root) {
        return Err(RunnerError::ScriptNotTrusted(relative.to_owned()));
    }
    reject_reparse_components(&candidate)
        .map_err(|_| RunnerError::ScriptNotTrusted(relative.to_owned()))?;
    match regular_file_without_reparse(&candidate) {
        Ok(true) => Ok(candidate),
        Ok(false) => Err(RunnerError::ScriptMissing(relative.to_owned())),
        Err(_) => Err(RunnerError::ScriptNotTrusted(relative.to_owned())),
    }
}

/// Locate the Python interpreter of an installation.
///
/// The managed environment is preferred; a system interpreter is never used
/// to run studio code because that would couple the installation to whatever
/// happens to be on the machine's PATH.
pub fn resolve_installation_python(
    install_root: &Path,
    bootstrap_root: Option<&Path>,
) -> Result<PathBuf, RunnerError> {
    let mut candidates = Vec::new();
    if let Some(bootstrap) = bootstrap_root {
        candidates.push(bootstrap.join("python").join("python.exe"));
        candidates.push(bootstrap.join("python").join("bin").join("python3"));
    }
    candidates.push(
        install_root
            .join("backend")
            .join(".venv")
            .join("Scripts")
            .join("python.exe"),
    );
    candidates.push(
        install_root
            .join("backend")
            .join(".venv")
            .join("bin")
            .join("python"),
    );
    for candidate in candidates {
        if regular_file_without_reparse(&candidate).unwrap_or(false) {
            return Ok(candidate);
        }
    }
    Err(RunnerError::InterpreterMissing(
        "该安装目录下的 Python 运行环境".to_owned(),
    ))
}

fn powershell_executable() -> Option<OsString> {
    for candidate in ["powershell.exe", "pwsh.exe"] {
        if which(candidate) {
            return Some(OsString::from(candidate));
        }
    }
    None
}

/// Minimal `which` lookup that never runs a shell.
fn which(program: &str) -> bool {
    let Some(path) = std::env::var_os("PATH") else {
        return false;
    };
    std::env::split_paths(&path).any(|directory| {
        let candidate = directory.join(program);
        candidate.is_file()
    })
}

/// Run one planned script and collect a machine readable outcome.
pub fn run_script(
    invocation: &ScriptInvocation,
    install_root: &Path,
    bootstrap_root: Option<&Path>,
    log_directory: Option<&Path>,
) -> Result<ExecutionOutcome, RunnerError> {
    let script_path = resolve_script(install_root, invocation.kind)?;
    let interpreter = match invocation.kind.interpreter() {
        Interpreter::PowerShell => {
            let executable = powershell_executable().ok_or_else(|| {
                RunnerError::InterpreterMissing("Windows PowerShell 5.1".to_owned())
            })?;
            let mut argv = vec![
                executable,
                OsString::from("-NoProfile"),
                OsString::from("-NonInteractive"),
                OsString::from("-ExecutionPolicy"),
                OsString::from("Bypass"),
                OsString::from("-File"),
                script_path.clone().into_os_string(),
            ];
            argv.extend(invocation.arguments.iter().map(OsString::from));
            argv
        }
        Interpreter::Python => {
            let python = resolve_installation_python(install_root, bootstrap_root)?;
            let mut argv = vec![
                python.into_os_string(),
                script_path.clone().into_os_string(),
            ];
            argv.extend(invocation.arguments.iter().map(OsString::from));
            argv
        }
    };

    let mut command = Command::new(&interpreter[0]);
    command
        .args(&interpreter[1..])
        .current_dir(&invocation.working_directory)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    for (key, value) in &invocation.environment {
        command.env(key, value);
    }
    apply_hidden_window(&mut command);

    let started = Instant::now();
    let mut child = command
        .spawn()
        .map_err(|error| RunnerError::Spawn(error.to_string()))?;

    let stdout = child.stdout.take();
    let stderr = child.stderr.take();
    let stdout_handle = stdout.map(|mut pipe| {
        thread::spawn(move || {
            let mut buffer = Vec::new();
            let _ = pipe.read_to_end(&mut buffer);
            buffer
        })
    });
    let stderr_handle = stderr.map(|mut pipe| {
        thread::spawn(move || {
            let mut buffer = Vec::new();
            let _ = pipe.read_to_end(&mut buffer);
            buffer
        })
    });

    let mut timed_out = false;
    let exit_code = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status.code(),
            Ok(None) => {
                if started.elapsed() >= invocation.timeout {
                    timed_out = true;
                    let _ = child.kill();
                    let _ = child.wait();
                    break None;
                }
                std::thread::sleep(Duration::from_millis(50));
            }
            Err(_) => {
                let _ = child.kill();
                let _ = child.wait();
                break None;
            }
        }
    };

    let stdout_bytes = stdout_handle
        .and_then(|handle| handle.join().ok())
        .unwrap_or_default();
    let stderr_bytes = stderr_handle
        .and_then(|handle| handle.join().ok())
        .unwrap_or_default();

    let argv: Vec<String> = interpreter
        .iter()
        .map(|part| part.to_string_lossy().into_owned())
        .collect();
    let stdout_tail = tail_of(&stdout_bytes);
    let stderr_tail = tail_of(&stderr_bytes);
    let duration_ms = started.elapsed().as_millis();

    let log_path = log_directory.and_then(|directory| {
        write_run_log(
            directory,
            invocation.kind,
            &argv,
            exit_code,
            timed_out,
            &stdout_bytes,
            &stderr_bytes,
        )
        .ok()
    });

    Ok(ExecutionOutcome {
        kind: invocation.kind.relative_path().to_owned(),
        label: invocation.kind.label().to_owned(),
        argv,
        exit_code,
        duration_ms,
        stdout_tail,
        stderr_tail,
        timed_out,
        log_path,
    })
}

fn tail_of(bytes: &[u8]) -> String {
    let start = bytes.len().saturating_sub(MAX_CAPTURED_BYTES);
    let slice = &bytes[start..];
    let text = String::from_utf8_lossy(slice);
    let mut lines: Vec<&str> = text.lines().collect();
    if lines.len() > 40 {
        lines = lines.split_off(lines.len() - 40);
    }
    lines.join("\n")
}

fn write_run_log(
    directory: &Path,
    kind: ScriptKind,
    argv: &[String],
    exit_code: Option<i32>,
    timed_out: bool,
    stdout: &[u8],
    stderr: &[u8],
) -> Result<PathBuf, std::io::Error> {
    fs::create_dir_all(directory)?;
    let sequence = RUN_SEQUENCE.fetch_add(1, Ordering::Relaxed);
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let file_name = format!(
        "{stamp}-{sequence}-{}",
        kind.relative_path().replace('/', "_")
    );
    let path = directory.join(file_name);
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&path)?;
    writeln!(file, "# argv: {}", argv.join(" "))?;
    writeln!(file, "# exit: {exit_code:?} timed_out: {timed_out}")?;
    writeln!(file, "--- stdout ---")?;
    file.write_all(stdout)?;
    writeln!(file)?;
    writeln!(file, "--- stderr ---")?;
    file.write_all(stderr)?;
    file.sync_all()?;
    Ok(path)
}

#[cfg(windows)]
fn apply_hidden_window(command: &mut Command) {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    command.creation_flags(CREATE_NO_WINDOW);
}

#[cfg(not(windows))]
fn apply_hidden_window(_command: &mut Command) {}

#[cfg(test)]
mod tests {
    use std::fs;
    use std::path::Path;

    use tempfile::{TempDir, tempdir_in};

    use super::{
        ExecutionOutcome, RunnerError, ScriptInvocation, ScriptKind, resolve_installation_python,
        resolve_script, run_script,
    };

    fn isolated_tempdir() -> TempDir {
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("target/test-tmp");
        fs::create_dir_all(&root).unwrap();
        tempdir_in(root).unwrap()
    }

    fn fixture_install(root: &Path) {
        fs::create_dir_all(root.join("scripts")).unwrap();
        fs::write(
            root.join("scripts/bootstrap.ps1"),
            "param([string]$Name = '')\nWrite-Output \"bootstrap $Name\"\nexit 0\n",
        )
        .unwrap();
        fs::write(
            root.join("scripts/start_shine.ps1"),
            "Write-Output 'start'\nexit 0\n",
        )
        .unwrap();
        fs::write(
            root.join("scripts/install_once.ps1"),
            "Write-Error 'boom'\nexit 7\n",
        )
        .unwrap();
    }

    #[test]
    fn a_trusted_script_is_resolved_inside_the_installation() {
        let temp = isolated_tempdir();
        let root = temp.path().join("studio");
        fixture_install(&root);
        let resolved = resolve_script(&root, ScriptKind::Bootstrap).unwrap();
        assert!(resolved.starts_with(&root));
        assert!(resolved.ends_with("scripts/bootstrap.ps1"));
    }

    #[test]
    fn a_missing_script_is_reported_instead_of_being_substituted() {
        let temp = isolated_tempdir();
        let root = temp.path().join("studio");
        fs::create_dir_all(&root).unwrap();
        let error = resolve_script(&root, ScriptKind::StartShine).unwrap_err();
        assert!(matches!(error, RunnerError::ScriptMissing(_)));
    }

    #[cfg(unix)]
    #[test]
    fn a_symlinked_script_is_not_trusted() {
        let temp = isolated_tempdir();
        let root = temp.path().join("studio");
        let scripts = root.join("scripts");
        fs::create_dir_all(&scripts).unwrap();
        let target = temp.path().join("outside.ps1");
        fs::write(&target, "Write-Output 'x'\n").unwrap();
        std::os::unix::fs::symlink(&target, scripts.join("bootstrap.ps1")).unwrap();
        let error = resolve_script(&root, ScriptKind::Bootstrap).unwrap_err();
        assert!(matches!(error, RunnerError::ScriptNotTrusted(_)));
    }

    #[test]
    fn every_script_kind_maps_to_a_fixed_repository_path() {
        assert_eq!(
            ScriptKind::Bootstrap.relative_path(),
            "scripts/bootstrap.ps1"
        );
        assert_eq!(
            ScriptKind::InstallOnce.relative_path(),
            "scripts/install_once.ps1"
        );
        assert_eq!(
            ScriptKind::StartShine.relative_path(),
            "scripts/start_shine.ps1"
        );
        assert_eq!(
            ScriptKind::StopShine.relative_path(),
            "scripts/stop_shine.py"
        );
        assert_eq!(
            ScriptKind::CheckEnvironment.relative_path(),
            "scripts/check_environment.ps1"
        );
    }

    #[test]
    fn a_failing_script_produces_a_machine_readable_failure() {
        let temp = isolated_tempdir();
        let root = temp.path().join("studio");
        fixture_install(&root);
        let logs = temp.path().join("logs");

        // The scripts are PowerShell; on a machine without PowerShell the run
        // is refused instead of silently reporting success.
        let invocation = ScriptInvocation::new(ScriptKind::InstallOnce, &root);
        match run_script(&invocation, &root, None, Some(&logs)) {
            Ok(outcome) => {
                assert_eq!(outcome.exit_code, Some(7));
                assert!(!outcome.succeeded());
                assert!(outcome.summary().contains("失败"));
            }
            Err(error) => {
                assert!(matches!(error, RunnerError::InterpreterMissing(_)));
            }
        }
    }

    #[test]
    fn the_installation_python_is_never_taken_from_the_system_path() {
        let temp = isolated_tempdir();
        let root = temp.path().join("studio");
        fs::create_dir_all(root.join("backend/.venv/Scripts")).unwrap();
        fs::write(root.join("backend/.venv/Scripts/python.exe"), b"x").unwrap();
        let resolved = resolve_installation_python(&root, None).unwrap();
        assert!(resolved.starts_with(&root));
    }

    #[test]
    fn a_missing_installation_python_is_reported() {
        let temp = isolated_tempdir();
        let root = temp.path().join("studio");
        fs::create_dir_all(&root).unwrap();
        assert!(resolve_installation_python(&root, None).is_err());
    }

    #[test]
    fn an_outcome_without_an_exit_code_never_claims_success() {
        let outcome = ExecutionOutcome {
            kind: "scripts/start_shine.ps1".to_owned(),
            label: "启动工作台".to_owned(),
            argv: Vec::new(),
            exit_code: None,
            duration_ms: 1,
            stdout_tail: String::new(),
            stderr_tail: String::new(),
            timed_out: true,
            log_path: None,
        };
        assert!(!outcome.succeeded());
        assert!(outcome.summary().contains("超时"));
    }
}
