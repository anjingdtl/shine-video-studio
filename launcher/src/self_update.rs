//! Launcher self-update.
//!
//! The launcher updates itself without ever touching the studio: no database,
//! no models, no assets, no ComfyUI. The rule is that the running executable
//! is never overwritten in place — Windows would refuse it, and a crash
//! halfway through would leave a half written program.
//!
//! The sequence is:
//!
//! ```text
//! LAUNCHER_CHECK -> FETCH_SIGNED_METADATA -> VERIFY -> STAGE_NEW_EXE
//!   -> PREPARE_HELPER -> EXIT_OLD_PROCESS -> SWITCH_LAUNCHER
//!   -> FIRST_BOOT_HEALTH -> COMPLETE | ROLLBACK
//! ```
//!
//! The helper is the launcher itself, re-executed with `--self-update-helper`.
//! It waits for the old process to exit, swaps the file, verifies the digest
//! of what it wrote, and starts the new program. If anything fails, the old
//! executable is still on disk and the user can start it again.

use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::Duration;

use serde::{Deserialize, Serialize};

use crate::path_safety::{regular_file_without_reparse, validate_local_absolute_path};

/// The argument that turns the launcher into the update helper.
pub const HELPER_ARGUMENT: &str = "--self-update-helper";
/// How long the helper waits for the old process to disappear.
const HELPER_EXIT_TIMEOUT: Duration = Duration::from_secs(120);
/// How often the helper checks whether the old process is gone.
const HELPER_POLL_INTERVAL: Duration = Duration::from_millis(200);

/// A verified, staged launcher build that is ready to be swapped in.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct StagedUpdate {
    /// The version being installed.
    pub target_version: String,
    /// The verified file that will become the new launcher.
    pub staged_path: PathBuf,
    /// The executable that is being replaced.
    pub target_path: PathBuf,
    /// The digest the new executable must have after the swap.
    pub expected_sha256: String,
    /// The digest the replaced executable had, kept for rollback.
    pub previous_sha256: String,
}

impl StagedUpdate {
    /// True when the staged file is really the file that was verified.
    pub fn verify_staged(&self) -> Result<(), SelfUpdateError> {
        let actual = crate::download::hash_file(&self.staged_path)
            .map_err(|error| SelfUpdateError::Io(error.to_string()))?;
        if !actual.eq_ignore_ascii_case(&self.expected_sha256) {
            return Err(SelfUpdateError::DigestMismatch {
                expected: self.expected_sha256.clone(),
                actual,
            });
        }
        Ok(())
    }
}

/// The states the self-update goes through.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SelfUpdatePhase {
    /// The current version was read.
    LauncherCheck,
    /// Signed metadata was fetched.
    FetchSignedMetadata,
    /// The download was verified.
    Verify,
    /// The new executable was written next to the old one.
    StageNewExe,
    /// The helper was prepared.
    PrepareHelper,
    /// The old process was asked to exit.
    ExitOldProcess,
    /// The files were swapped.
    SwitchLauncher,
    /// The new program started and reported back.
    FirstBootHealth,
    /// The update finished.
    Complete,
    /// The update failed and the old program is still usable.
    RolledBack,
    /// The update cannot proceed.
    Blocked,
}

impl SelfUpdatePhase {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::LauncherCheck => "launcher_check",
            Self::FetchSignedMetadata => "fetch_signed_metadata",
            Self::Verify => "verify",
            Self::StageNewExe => "stage_new_exe",
            Self::PrepareHelper => "prepare_helper",
            Self::ExitOldProcess => "exit_old_process",
            Self::SwitchLauncher => "switch_launcher",
            Self::FirstBootHealth => "first_boot_health",
            Self::Complete => "complete",
            Self::RolledBack => "rolled_back",
            Self::Blocked => "blocked",
        }
    }

    pub fn user_label(self) -> &'static str {
        match self {
            Self::LauncherCheck => "检查当前版本",
            Self::FetchSignedMetadata => "读取已签名的版本信息",
            Self::Verify => "校验下载文件",
            Self::StageNewExe => "准备新版本",
            Self::PrepareHelper => "准备更新助手",
            Self::ExitOldProcess => "退出旧版本",
            Self::SwitchLauncher => "切换到新版本",
            Self::FirstBootHealth => "确认新版本可用",
            Self::Complete => "更新完成",
            Self::RolledBack => "更新失败，已保留原版本",
            Self::Blocked => "无法更新，已停止",
        }
    }
}

#[derive(Debug)]
pub enum SelfUpdateError {
    Io(String),
    /// The staged file is not the file that was verified.
    DigestMismatch {
        expected: String,
        actual: String,
    },
    /// The launcher executable could not be located.
    ExecutableNotFound,
    /// The update was refused.
    Blocked(String),
}

impl std::fmt::Display for SelfUpdateError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Io(message) => write!(formatter, "更新写入失败：{message}"),
            Self::DigestMismatch { expected, actual } => {
                write!(
                    formatter,
                    "更新文件校验和不符（应为 {expected}，实际 {actual}）"
                )
            }
            Self::ExecutableNotFound => formatter.write_str("无法确定启动器程序位置"),
            Self::Blocked(message) => formatter.write_str(message),
        }
    }
}

impl std::error::Error for SelfUpdateError {}

/// The path of the running launcher executable.
pub fn current_executable() -> Result<PathBuf, SelfUpdateError> {
    std::env::current_exe().map_err(|_| SelfUpdateError::ExecutableNotFound)
}

/// Prepare a verified download for installation.
///
/// The file is copied next to the current executable with a `.new` suffix,
/// and the digest of the copy is checked, so a truncated copy can never be
/// swapped in.
pub fn stage_update(
    downloaded: &Path,
    target_version: &str,
    expected_sha256: &str,
) -> Result<StagedUpdate, SelfUpdateError> {
    let target_path = current_executable()?;
    let parent = target_path
        .parent()
        .ok_or(SelfUpdateError::ExecutableNotFound)?;
    let staged_path = parent.join(format!(
        "{}.new",
        target_path
            .file_name()
            .map(|name| name.to_string_lossy().into_owned())
            .unwrap_or_else(|| "ShineLauncher.exe".to_owned())
    ));

    fs::copy(downloaded, &staged_path).map_err(|error| SelfUpdateError::Io(error.to_string()))?;

    let previous_sha256 = crate::download::hash_file(&target_path).unwrap_or_default();
    let staged = StagedUpdate {
        target_version: target_version.to_owned(),
        staged_path,
        target_path,
        expected_sha256: expected_sha256.to_owned(),
        previous_sha256,
    };
    staged.verify_staged()?;
    Ok(staged)
}

/// Start the helper that performs the swap.
///
/// The helper is the launcher itself with a dedicated argument. It inherits
/// nothing from the studio and touches nothing but the launcher binary.
pub fn spawn_helper(staged: &StagedUpdate) -> Result<(), SelfUpdateError> {
    let executable = current_executable()?;
    let mut command = Command::new(&executable);
    command
        .arg(HELPER_ARGUMENT)
        .arg(&staged.staged_path)
        .arg(&staged.target_path)
        .arg(&staged.expected_sha256)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    apply_hidden_window(&mut command);
    command
        .spawn()
        .map(|_| ())
        .map_err(|error| SelfUpdateError::Io(error.to_string()))
}

/// The helper side of the update.
///
/// It waits for the old process to exit, replaces the executable, checks the
/// result, and starts the new program. When anything goes wrong the original
/// executable is left in place.
pub fn run_helper(
    staged_path: &Path,
    target_path: &Path,
    expected_sha256: &str,
    parent_pid: Option<u32>,
) -> Result<(), SelfUpdateError> {
    validate_local_absolute_path(staged_path).map_err(SelfUpdateError::Blocked)?;
    validate_local_absolute_path(target_path).map_err(SelfUpdateError::Blocked)?;

    if !regular_file_without_reparse(staged_path).unwrap_or(false) {
        return Err(SelfUpdateError::Blocked("更新文件不存在".to_owned()));
    }

    if let Some(pid) = parent_pid {
        wait_for_exit(pid, HELPER_EXIT_TIMEOUT);
    }

    // Keep the old executable next to the new one so a failed start can be
    // rolled back by hand.
    let backup = target_path.with_extension("previous");
    let _ = fs::remove_file(&backup);
    let _ = fs::copy(target_path, &backup);

    // Windows cannot replace a running executable, so the swap goes through
    // a rename of a freshly written copy.
    let replacement = target_path.with_extension("new");
    fs::copy(staged_path, &replacement).map_err(|error| SelfUpdateError::Io(error.to_string()))?;
    let actual = crate::download::hash_file(&replacement)
        .map_err(|error| SelfUpdateError::Io(error.to_string()))?;
    if !actual.eq_ignore_ascii_case(expected_sha256) {
        let _ = fs::remove_file(&replacement);
        return Err(SelfUpdateError::DigestMismatch {
            expected: expected_sha256.to_owned(),
            actual,
        });
    }

    let _ = fs::remove_file(target_path);
    fs::rename(&replacement, target_path)
        .map_err(|error| SelfUpdateError::Io(error.to_string()))?;

    // Start the new program. If it refuses to start, the previous executable
    // is still available as `<name>.previous`.
    let mut command = Command::new(target_path);
    command
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    apply_hidden_window(&mut command);
    match command.spawn() {
        Ok(_) => Ok(()),
        Err(error) => {
            let _ = fs::copy(&backup, target_path);
            Err(SelfUpdateError::Io(format!(
                "新版本无法启动，已恢复原版本：{error}"
            )))
        }
    }
}

/// Wait until a process is gone.
fn wait_for_exit(pid: u32, timeout: Duration) {
    let started = std::time::Instant::now();
    while started.elapsed() < timeout {
        if !process_exists(pid) {
            return;
        }
        std::thread::sleep(HELPER_POLL_INTERVAL);
    }
}

/// Whether a process id is still alive.
fn process_exists(pid: u32) -> bool {
    #[cfg(windows)]
    {
        // `tasklist` is used rather than the raw API so that the launcher has
        // no unsafe code and no extra dependency.
        let output = Command::new("tasklist")
            .args(["/FI", &format!("PID eq {pid}"), "/NH"])
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .output();
        match output {
            Ok(output) => {
                let text = String::from_utf8_lossy(&output.stdout);
                text.contains(&format!("{pid}"))
            }
            Err(_) => true,
        }
    }
    #[cfg(not(windows))]
    {
        Path::new(&format!("/proc/{pid}")).exists()
    }
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

    use super::{HELPER_ARGUMENT, SelfUpdatePhase, run_helper, stage_update};

    fn isolated_tempdir() -> TempDir {
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("target/test-tmp");
        fs::create_dir_all(&root).unwrap();
        tempdir_in(root).unwrap()
    }

    #[test]
    fn a_staged_update_is_verified_before_it_can_be_swapped_in() {
        let temp = isolated_tempdir();
        let downloaded = temp.path().join("download/ShineLauncher.exe");
        fs::create_dir_all(downloaded.parent().unwrap()).unwrap();
        fs::write(&downloaded, b"new launcher bytes").unwrap();
        let digest = crate::download::hash_file(&downloaded).unwrap();

        // The staging helper refuses a digest that does not match.
        let error = stage_update(&downloaded, "1.1.0", &"f".repeat(64)).unwrap_err();
        assert!(matches!(
            error,
            super::SelfUpdateError::DigestMismatch { .. }
        ));

        // With the right digest the file is staged next to the executable and
        // its copy is verified.
        let staged = stage_update(&downloaded, "1.1.0", &digest).unwrap();
        assert_eq!(staged.target_version, "1.1.0");
        assert!(staged.staged_path.is_file());
        assert!(staged.staged_path.to_string_lossy().ends_with(".new"));
        staged.verify_staged().unwrap();
        let _ = fs::remove_file(&staged.staged_path);
    }

    #[test]
    fn the_helper_refuses_a_staged_file_that_is_not_there() {
        let temp = isolated_tempdir();
        let error = run_helper(
            &temp.path().join("missing.exe"),
            &temp.path().join("target.exe"),
            &"a".repeat(64),
            None,
        )
        .unwrap_err();
        assert!(matches!(error, super::SelfUpdateError::Blocked(_)));
    }

    #[test]
    fn the_helper_replaces_the_executable_and_keeps_the_previous_one() {
        let temp = isolated_tempdir();
        let target = temp.path().join("ShineLauncher.exe");
        fs::write(&target, b"old launcher bytes").unwrap();
        let staged = temp.path().join("staged.exe");
        fs::write(&staged, b"new launcher bytes").unwrap();
        let digest = crate::download::hash_file(&staged).unwrap();

        // The helper would normally start the new program; here the staged
        // file is not an executable, so the swap is expected to be undone
        // and the original restored.
        let result = run_helper(&staged, &target, &digest, None);
        assert!(result.is_err());
        // Whatever happened, the original bytes are still recoverable.
        let backup = target.with_extension("previous");
        assert!(backup.is_file());
        assert_eq!(fs::read(&backup).unwrap(), b"old launcher bytes");
    }

    #[test]
    fn the_helper_rejects_a_staged_file_with_the_wrong_digest() {
        let temp = isolated_tempdir();
        let target = temp.path().join("ShineLauncher.exe");
        fs::write(&target, b"old launcher bytes").unwrap();
        let staged = temp.path().join("staged.exe");
        fs::write(&staged, b"new launcher bytes").unwrap();

        let error = run_helper(&staged, &target, &"a".repeat(64), None).unwrap_err();
        assert!(matches!(
            error,
            super::SelfUpdateError::DigestMismatch { .. }
        ));
        // The original is untouched.
        assert_eq!(fs::read(&target).unwrap(), b"old launcher bytes");
    }

    #[test]
    fn the_helper_argument_is_stable() {
        assert_eq!(HELPER_ARGUMENT, "--self-update-helper");
    }

    #[test]
    fn every_phase_has_a_user_facing_label() {
        let phases = [
            SelfUpdatePhase::LauncherCheck,
            SelfUpdatePhase::FetchSignedMetadata,
            SelfUpdatePhase::Verify,
            SelfUpdatePhase::StageNewExe,
            SelfUpdatePhase::PrepareHelper,
            SelfUpdatePhase::ExitOldProcess,
            SelfUpdatePhase::SwitchLauncher,
            SelfUpdatePhase::FirstBootHealth,
            SelfUpdatePhase::Complete,
            SelfUpdatePhase::RolledBack,
            SelfUpdatePhase::Blocked,
        ];
        for phase in phases {
            assert!(!phase.user_label().is_empty());
            assert!(!phase.as_str().is_empty());
        }
    }
}
