//! The Studio execution chain.
//!
//! This is the part the previous launcher never had: a real path from "the
//! user pressed a button" to "the workbench is running, and here is proof".
//!
//! The launcher does not reimplement the installation. It orchestrates the
//! repository's own scripts through the controlled runner, and it asks the
//! bridge for the facts that decide whether the result is real. The rules
//! that matter:
//!
//! * A step only starts when the installation can actually run it.
//! * A step that touches a running workbench first asks whether real
//!   production work is in flight, and refuses to interrupt it.
//! * Success is decided by the exit code and by the machine readable state,
//!   never by reading Chinese progress text.
//! * Nothing is reported as "ready" that has not been observed ready.

use std::path::{Path, PathBuf};
use std::time::Duration;

use serde_json::json;

use crate::bridge::{
    BridgeClient, BridgeError, ProductionActivity, StackStatus, parse_production_activity,
    parse_stack_status,
};
use crate::runner::{ExecutionOutcome, ScriptInvocation, ScriptKind, run_script};

/// One installation the launcher can act on.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct StudioInstallation {
    pub installation_id: String,
    pub install_root: PathBuf,
    pub data_root: PathBuf,
    pub bootstrap_root: Option<PathBuf>,
}

impl StudioInstallation {
    pub fn new(
        installation_id: &str,
        install_root: &Path,
        data_root: &Path,
        bootstrap_root: Option<PathBuf>,
    ) -> Self {
        Self {
            installation_id: installation_id.to_owned(),
            install_root: install_root.to_path_buf(),
            data_root: data_root.to_path_buf(),
            bootstrap_root,
        }
    }

    /// Where the launcher keeps the run logs of this installation.
    pub fn log_directory(&self) -> PathBuf {
        self.data_root.join("run").join("launcher")
    }
}

/// Why an operation could not be carried out.
#[derive(Debug)]
pub enum StudioError {
    /// The installation is missing a script the operation needs.
    NotInstalled(String),
    /// The studio is busy with real production work.
    ProductionInFlight(Box<ProductionActivity>),
    /// The script ran and failed.
    ScriptFailed(Box<ExecutionOutcome>),
    /// The script ran but the resulting state is not what was required.
    StateNotReached { expected: String, actual: String },
    /// The bridge could not answer.
    Bridge(BridgeError),
    /// The platform cannot run this operation.
    Unsupported(String),
}

impl std::fmt::Display for StudioError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::NotInstalled(detail) => write!(formatter, "安装尚未就绪：{detail}"),
            Self::ProductionInFlight(activity) => write!(
                formatter,
                "工作台正在执行真实任务（{} 个进行中的任务，{} 个独占租约），已阻止本次操作。",
                activity.real_production_jobs, activity.exclusive_leases
            ),
            Self::ScriptFailed(outcome) => formatter.write_str(&outcome.summary()),
            Self::StateNotReached { expected, actual } => {
                write!(formatter, "预期状态为 {expected}，实际为 {actual}")
            }
            Self::Bridge(error) => write!(formatter, "{error}"),
            Self::Unsupported(message) => formatter.write_str(message),
        }
    }
}

impl std::error::Error for StudioError {}

impl From<BridgeError> for StudioError {
    fn from(error: BridgeError) -> Self {
        Self::Bridge(error)
    }
}

/// What a successful operation produced.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct OperationResult {
    pub outcome: ExecutionOutcome,
    pub stack: Option<StackStatus>,
    pub message: String,
}

/// The launcher side of one installation.
pub struct StudioController {
    installation: StudioInstallation,
}

impl StudioController {
    pub fn new(installation: StudioInstallation) -> Self {
        Self { installation }
    }

    pub fn installation(&self) -> &StudioInstallation {
        &self.installation
    }

    fn bridge(&self) -> Result<BridgeClient, StudioError> {
        BridgeClient::new(
            &self.installation.install_root,
            self.installation.bootstrap_root.as_deref(),
        )
        .map_err(StudioError::Bridge)
    }

    /// Read the current state of the workbench without changing anything.
    pub fn stack_status(&self) -> Result<StackStatus, StudioError> {
        let bridge = self.bridge()?;
        let value = bridge.call(
            "stack_status",
            json!({"data_dir": self.installation.data_root.to_string_lossy()}),
        )?;
        parse_stack_status(&value).map_err(StudioError::Bridge)
    }

    /// Report whether real production work is in flight.
    pub fn production_activity(&self) -> Result<ProductionActivity, StudioError> {
        let bridge = self.bridge()?;
        let value = bridge.call(
            "active_production",
            json!({"data_dir": self.installation.data_root.to_string_lossy()}),
        )?;
        parse_production_activity(&value).map_err(StudioError::Bridge)
    }

    /// Run the first-time installation.
    ///
    /// The work is done by the repository's own installer, which picks a
    /// drive, prepares the toolchain and writes the receipts. The launcher
    /// only supervises it and reports the outcome.
    pub fn install(&self) -> Result<OperationResult, StudioError> {
        let invocation =
            ScriptInvocation::new(ScriptKind::InstallOnce, &self.installation.install_root)
                .with_arguments(vec![
                    "-StorageRoot".to_owned(),
                    self.installation
                        .install_root
                        .to_string_lossy()
                        .into_owned(),
                    "-NoLaunch".to_owned(),
                ])
                .with_environment(
                    "SHINE_DATA_DIR",
                    &self.installation.data_root.to_string_lossy(),
                )
                .with_timeout(Duration::from_secs(3 * 60 * 60));

        let outcome = run_script(
            &invocation,
            &self.installation.install_root,
            self.installation.bootstrap_root.as_deref(),
            Some(&self.installation.log_directory()),
        )
        .map_err(|error| StudioError::NotInstalled(error.to_string()))?;

        if !outcome.succeeded() {
            return Err(StudioError::ScriptFailed(Box::new(outcome)));
        }
        Ok(OperationResult {
            message: "安装完成，可以启动工作台。".to_owned(),
            outcome,
            stack: None,
        })
    }

    /// Prepare the toolchain and runtime for an installation.
    pub fn bootstrap(&self) -> Result<OperationResult, StudioError> {
        let mut arguments = vec![
            "-InstallRoot".to_owned(),
            self.installation
                .install_root
                .to_string_lossy()
                .into_owned(),
            "-DataDir".to_owned(),
            self.installation.data_root.to_string_lossy().into_owned(),
            "-NoLaunch".to_owned(),
            "-NoPathUpdate".to_owned(),
        ];
        if let Some(bootstrap) = &self.installation.bootstrap_root {
            arguments.push("-StorageRoot".to_owned());
            arguments.push(bootstrap.to_string_lossy().into_owned());
        }
        let invocation =
            ScriptInvocation::new(ScriptKind::Bootstrap, &self.installation.install_root)
                .with_arguments(arguments)
                .with_environment(
                    "SHINE_DATA_DIR",
                    &self.installation.data_root.to_string_lossy(),
                );

        let outcome = run_script(
            &invocation,
            &self.installation.install_root,
            self.installation.bootstrap_root.as_deref(),
            Some(&self.installation.log_directory()),
        )
        .map_err(|error| StudioError::NotInstalled(error.to_string()))?;

        if !outcome.succeeded() {
            return Err(StudioError::ScriptFailed(Box::new(outcome)));
        }
        Ok(OperationResult {
            message: "运行环境已准备完成。".to_owned(),
            outcome,
            stack: None,
        })
    }

    /// Start the workbench.
    ///
    /// Ports are chosen automatically, so a port that is already taken by
    /// another program moves the workbench to a free one instead of failing
    /// or — worse — killing the other program.
    pub fn start(&self) -> Result<OperationResult, StudioError> {
        // A start must never race an in-flight production run of the same
        // installation: the script would find the ports busy and refuse.
        let activity = self.production_activity()?;
        if activity.blocks_maintenance {
            return Err(StudioError::ProductionInFlight(Box::new(activity)));
        }

        let invocation =
            ScriptInvocation::new(ScriptKind::StartShine, &self.installation.install_root)
                .with_arguments(vec![
                    "-DataDir".to_owned(),
                    self.installation.data_root.to_string_lossy().into_owned(),
                    "-AutoPorts".to_owned(),
                ])
                .with_environment(
                    "SHINE_DATA_DIR",
                    &self.installation.data_root.to_string_lossy(),
                )
                .with_timeout(Duration::from_secs(10 * 60));

        let outcome = run_script(
            &invocation,
            &self.installation.install_root,
            self.installation.bootstrap_root.as_deref(),
            Some(&self.installation.log_directory()),
        )
        .map_err(|error| StudioError::NotInstalled(error.to_string()))?;

        if !outcome.succeeded() {
            return Err(StudioError::ScriptFailed(Box::new(outcome)));
        }

        let stack = self.stack_status()?;
        let message = match stack.web_url() {
            Some(url) => format!("工作台已启动，打开 {url} 即可使用。"),
            None => "工作台已启动，但网页地址尚未确认，请查看诊断信息。".to_owned(),
        };
        Ok(OperationResult {
            outcome,
            stack: Some(stack),
            message,
        })
    }

    /// Stop the workbench.
    ///
    /// The stop is delegated to the repository's own stop script, which
    /// verifies the recorded process identities before it touches anything.
    /// The launcher never decides on its own that a process may be killed.
    pub fn stop(&self) -> Result<OperationResult, StudioError> {
        let invocation =
            ScriptInvocation::new(ScriptKind::StopShine, &self.installation.install_root)
                .with_arguments(vec![
                    "--data-dir".to_owned(),
                    self.installation.data_root.to_string_lossy().into_owned(),
                ]);

        let outcome = run_script(
            &invocation,
            &self.installation.install_root,
            self.installation.bootstrap_root.as_deref(),
            Some(&self.installation.log_directory()),
        )
        .map_err(|error| StudioError::NotInstalled(error.to_string()))?;

        if !outcome.succeeded() {
            return Err(StudioError::ScriptFailed(Box::new(outcome)));
        }
        Ok(OperationResult {
            message: "工作台已停止。".to_owned(),
            outcome,
            stack: None,
        })
    }

    /// Take a consistent snapshot of the workbench database.
    pub fn backup_database(&self, destination: &Path) -> Result<String, StudioError> {
        let bridge = self.bridge()?;
        let value = bridge.call(
            "sqlite_backup",
            json!({
                "data_dir": self.installation.data_root.to_string_lossy(),
                "destination": destination.to_string_lossy(),
            }),
        )?;
        Ok(value
            .get("destination")
            .and_then(|value| value.as_str())
            .unwrap_or_default()
            .to_owned())
    }

    /// Report whether the database was written after a given moment.
    ///
    /// This is what keeps a rollback from overwriting work the new version
    /// has already accepted.
    pub fn database_written_since(&self, unix_seconds: f64) -> Result<bool, StudioError> {
        let bridge = self.bridge()?;
        let value = bridge.call(
            "database_write_state",
            json!({
                "data_dir": self.installation.data_root.to_string_lossy(),
                "since_unix": unix_seconds,
            }),
        )?;
        Ok(value
            .get("written_since")
            .and_then(|value| value.as_bool())
            .unwrap_or(false))
    }
}

#[cfg(test)]
mod tests {
    use std::fs;
    use std::path::Path;

    use tempfile::{TempDir, tempdir_in};

    use super::{StudioController, StudioError, StudioInstallation};

    fn isolated_tempdir() -> TempDir {
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("target/test-tmp");
        fs::create_dir_all(&root).unwrap();
        tempdir_in(root).unwrap()
    }

    fn controller(temp: &Path) -> StudioController {
        let install_root = temp.join("studio");
        let data_root = temp.join("data");
        fs::create_dir_all(&install_root).unwrap();
        fs::create_dir_all(&data_root).unwrap();
        StudioController::new(StudioInstallation::new(
            "inst-test",
            &install_root,
            &data_root,
            Some(temp.join("bootstrap")),
        ))
    }

    #[test]
    fn an_installation_without_the_bridge_cannot_report_a_stack_status() {
        let temp = isolated_tempdir();
        let controller = controller(temp.path());
        // The fixture has no managed Python, so the bridge cannot start. The
        // important part is that this is an error and not a fake "stopped".
        match controller.stack_status() {
            Err(StudioError::Bridge(_)) => {}
            other => panic!("unexpected result: {other:?}"),
        }
    }

    #[test]
    fn the_log_directory_lives_under_the_data_directory() {
        let temp = isolated_tempdir();
        let controller = controller(temp.path());
        let logs = controller.installation().log_directory();
        assert!(logs.starts_with(&controller.installation().data_root));
        assert!(logs.ends_with("launcher"));
    }

    #[test]
    fn an_installation_that_cannot_run_a_script_reports_it_as_not_installed() {
        let temp = isolated_tempdir();
        let controller = controller(temp.path());
        match controller.install() {
            Err(StudioError::NotInstalled(_)) => {}
            other => panic!("unexpected result: {other:?}"),
        }
    }
}
