//! Client for the versioned launcher bridge.
//!
//! The bridge is the single place where the native launcher asks the studio
//! about facts the studio owns: process identity, production activity, a
//! consistent database snapshot. Every call is a JSON request on stdin and a
//! JSON response on stdout, so the answer is machine readable and the
//! launcher never has to interpret human prose.

use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

use crate::runner::{RunnerError, resolve_installation_python};

pub const BRIDGE_PROTOCOL: &str = "shine-launcher-bridge/1";
const BRIDGE_RELATIVE_PATH: &str = "scripts/launcher_bridge.py";
const BRIDGE_TIMEOUT: Duration = Duration::from_secs(120);

#[derive(Debug)]
pub enum BridgeError {
    /// The installation does not contain the bridge script.
    ScriptMissing(String),
    /// The bridge script could not be started.
    Spawn(String),
    /// The bridge did not answer inside the timeout.
    Timeout,
    /// The bridge answered with a protocol error.
    Rejected { code: String, message: String },
    /// The bridge answered with a malformed response.
    InvalidResponse(String),
}

impl std::fmt::Display for BridgeError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::ScriptMissing(path) => {
                write!(formatter, "安装目录中缺少桥接脚本：{path}")
            }
            Self::Spawn(message) => write!(formatter, "无法启动桥接进程：{message}"),
            Self::Timeout => formatter.write_str("桥接操作超时"),
            Self::Rejected { code, message } => {
                write!(formatter, "桥接操作被拒绝（{code}）：{message}")
            }
            Self::InvalidResponse(message) => {
                write!(formatter, "桥接返回的数据无效：{message}")
            }
        }
    }
}

impl std::error::Error for BridgeError {}

impl From<RunnerError> for BridgeError {
    fn from(error: RunnerError) -> Self {
        match error {
            RunnerError::ScriptMissing(path) => Self::ScriptMissing(path),
            RunnerError::InterpreterMissing(name) => {
                Self::Spawn(format!("本机缺少 {name}，无法执行桥接操作"))
            }
            other => Self::Spawn(other.to_string()),
        }
    }
}

/// A successful bridge response.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct BridgeResponse {
    pub ok: bool,
    pub protocol: String,
    pub operation: Option<String>,
    #[serde(default)]
    pub result: Option<Value>,
    #[serde(default)]
    pub error: Option<BridgeErrorBody>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct BridgeErrorBody {
    pub code: String,
    pub message: String,
}

/// The launcher side of the bridge.
pub struct BridgeClient {
    python: PathBuf,
    script: PathBuf,
}

impl BridgeClient {
    /// Build a client for one installation.
    ///
    /// The interpreter is always the installation's own managed Python, never
    /// whatever happens to be first on the machine's PATH.
    pub fn new(install_root: &Path, bootstrap_root: Option<&Path>) -> Result<Self, BridgeError> {
        let script = install_root.join(BRIDGE_RELATIVE_PATH);
        if !script.starts_with(install_root) {
            return Err(BridgeError::ScriptMissing(BRIDGE_RELATIVE_PATH.to_owned()));
        }
        if !script.is_file() {
            return Err(BridgeError::ScriptMissing(BRIDGE_RELATIVE_PATH.to_owned()));
        }
        let python = resolve_installation_python(install_root, bootstrap_root)?;
        Ok(Self { python, script })
    }

    /// Run one bridge operation.
    pub fn call(&self, operation: &str, arguments: Value) -> Result<Value, BridgeError> {
        let request = json!({
            "protocol": BRIDGE_PROTOCOL,
            "operation": operation,
            "arguments": arguments,
        });
        let mut payload = serde_json::to_vec(&request)
            .map_err(|error| BridgeError::InvalidResponse(error.to_string()))?;

        let mut command = Command::new(&self.python);
        command
            .arg(&self.script)
            .current_dir(
                self.script
                    .parent()
                    .and_then(Path::parent)
                    .unwrap_or_else(|| Path::new(".")),
            )
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        apply_hidden_window(&mut command);

        let mut child = command
            .spawn()
            .map_err(|error| BridgeError::Spawn(error.to_string()))?;
        if let Some(stdin) = child.stdin.as_mut() {
            stdin
                .write_all(&payload)
                .map_err(|error| BridgeError::Spawn(error.to_string()))?;
        }
        drop(child.stdin.take());
        payload.clear();

        let started = Instant::now();
        loop {
            match child.try_wait() {
                Ok(Some(_)) => break,
                Ok(None) => {
                    if started.elapsed() >= BRIDGE_TIMEOUT {
                        let _ = child.kill();
                        let _ = child.wait();
                        return Err(BridgeError::Timeout);
                    }
                    std::thread::sleep(Duration::from_millis(30));
                }
                Err(error) => return Err(BridgeError::Spawn(error.to_string())),
            }
        }

        let output = child
            .wait_with_output()
            .map_err(|error| BridgeError::Spawn(error.to_string()))?;
        let text = String::from_utf8_lossy(&output.stdout);
        let response: BridgeResponse = serde_json::from_str(text.trim())
            .map_err(|error| BridgeError::InvalidResponse(error.to_string()))?;
        if !response.ok {
            let body = response.error.unwrap_or(BridgeErrorBody {
                code: "UNKNOWN".to_owned(),
                message: "桥接未提供错误说明".to_owned(),
            });
            return Err(BridgeError::Rejected {
                code: body.code,
                message: body.message,
            });
        }
        response
            .result
            .ok_or_else(|| BridgeError::InvalidResponse("桥接响应缺少结果".to_owned()))
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

/// The observed state of a running workbench stack.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StackState {
    /// No process record exists.
    NotRunning,
    /// Every recorded process is alive and owns its port.
    Running,
    /// Some recorded processes are alive and some are not.
    HalfStarted,
    /// A recorded port is now owned by a different process.
    PortConflict,
    /// The record exists but the operating system cannot describe the
    /// processes; a human has to reconcile it.
    Unverifiable,
    /// The record exists but nothing it names is alive any more.
    StaleRecord,
    /// The bridge answered with a state this launcher does not know.
    Unknown(String),
}

impl StackState {
    pub fn parse(value: &str) -> Self {
        match value {
            "not_running" => Self::NotRunning,
            "running" => Self::Running,
            "half_started" => Self::HalfStarted,
            "port_conflict" => Self::PortConflict,
            "unverifiable" => Self::Unverifiable,
            "stale_record" => Self::StaleRecord,
            other => Self::Unknown(other.to_owned()),
        }
    }

    /// A short explanation for a non technical user.
    pub fn user_message(&self) -> &'static str {
        match self {
            Self::NotRunning => "工作台当前没有运行。",
            Self::Running => "工作台正在运行，可以直接打开。",
            Self::HalfStarted => "工作台只启动了一部分，需要重新启动。",
            Self::PortConflict => "端口被其他程序占用，需要换用空闲端口。",
            Self::Unverifiable => "无法确认部分进程的身份，需要人工核对后处理。",
            Self::StaleRecord => "存在上次残留的运行记录，可以安全清理。",
            Self::Unknown(_) => "运行状态未知，请查看诊断信息。",
        }
    }
}

/// One recorded process and what is known about it right now.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProcessStatus {
    pub name: Option<String>,
    pub pid: Option<u32>,
    pub listener_pid: Option<u32>,
    pub state: String,
    pub owners: Vec<OwnerStatus>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct OwnerStatus {
    pub pid: Option<u32>,
    pub state: String,
}

/// The full stack status reported by the bridge.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct StackStatus {
    pub state: StackState,
    pub manifest_present: bool,
    pub recorded_at: Option<String>,
    pub data_dir: Option<String>,
    pub api_port: Option<u16>,
    pub web_port: Option<u16>,
    pub api_port_owner: Option<u32>,
    pub web_port_owner: Option<u32>,
    pub api_answers: bool,
    pub web_answers: bool,
    pub port_conflict: bool,
    pub processes: Vec<ProcessStatus>,
}

impl StackStatus {
    /// The browser URL of the workbench, when one is actually listening.
    pub fn web_url(&self) -> Option<String> {
        let port = self.web_port?;
        if self.web_answers {
            Some(format!("http://127.0.0.1:{port}/"))
        } else {
            None
        }
    }

    pub fn summary(&self) -> String {
        let mut text = String::from(self.state.user_message());
        if let Some(url) = self.web_url() {
            text.push_str(&format!("\n打开地址：{url}"));
        }
        text
    }
}

/// Real production work that blocks a maintenance step.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProductionActivity {
    pub state: String,
    pub real_production_jobs: usize,
    pub exclusive_leases: usize,
    pub blocks_maintenance: bool,
    pub active_jobs: Vec<ProductionJob>,
    pub active_leases: Vec<ProductionLease>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProductionJob {
    pub id: String,
    pub status: String,
    pub provider: String,
    pub real_provider: bool,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProductionLease {
    pub id: String,
    pub owner: String,
    pub gpu: String,
    pub exclusive: bool,
}

/// Parse a stack status document.
pub fn parse_stack_status(value: &Value) -> Result<StackStatus, BridgeError> {
    let state = value
        .get("state")
        .and_then(Value::as_str)
        .ok_or_else(|| BridgeError::InvalidResponse("缺少运行状态".to_owned()))?;
    let processes = value
        .get("processes")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .map(|item| ProcessStatus {
                    name: item.get("name").and_then(Value::as_str).map(str::to_owned),
                    pid: item
                        .get("pid")
                        .and_then(Value::as_u64)
                        .map(|value| value as u32),
                    listener_pid: item
                        .get("listener_pid")
                        .and_then(Value::as_u64)
                        .map(|value| value as u32),
                    state: item
                        .get("state")
                        .and_then(Value::as_str)
                        .unwrap_or("unknown")
                        .to_owned(),
                    owners: item
                        .get("owners")
                        .and_then(Value::as_array)
                        .map(|owners| {
                            owners
                                .iter()
                                .map(|owner| OwnerStatus {
                                    pid: owner.get("pid").and_then(Value::as_u64).map(|v| v as u32),
                                    state: owner
                                        .get("state")
                                        .and_then(Value::as_str)
                                        .unwrap_or("unknown")
                                        .to_owned(),
                                })
                                .collect()
                        })
                        .unwrap_or_default(),
                })
                .collect()
        })
        .unwrap_or_default();

    Ok(StackStatus {
        state: StackState::parse(state),
        manifest_present: value
            .get("manifest_present")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        recorded_at: value
            .get("recorded_at")
            .and_then(Value::as_str)
            .map(str::to_owned),
        data_dir: value
            .get("data_dir")
            .and_then(Value::as_str)
            .map(str::to_owned),
        api_port: value
            .get("api_port")
            .and_then(Value::as_u64)
            .map(|v| v as u16),
        web_port: value
            .get("web_port")
            .and_then(Value::as_u64)
            .map(|v| v as u16),
        api_port_owner: value
            .get("api_port_owner")
            .and_then(Value::as_u64)
            .map(|v| v as u32),
        web_port_owner: value
            .get("web_port_owner")
            .and_then(Value::as_u64)
            .map(|v| v as u32),
        api_answers: value
            .get("api_answers")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        web_answers: value
            .get("web_answers")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        port_conflict: value
            .get("port_conflict")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        processes,
    })
}

/// Parse a production activity document.
pub fn parse_production_activity(value: &Value) -> Result<ProductionActivity, BridgeError> {
    Ok(ProductionActivity {
        state: value
            .get("state")
            .and_then(Value::as_str)
            .unwrap_or("unknown")
            .to_owned(),
        real_production_jobs: value
            .get("real_production_jobs")
            .and_then(Value::as_u64)
            .unwrap_or(0) as usize,
        exclusive_leases: value
            .get("exclusive_leases")
            .and_then(Value::as_u64)
            .unwrap_or(0) as usize,
        blocks_maintenance: value
            .get("blocks_maintenance")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        active_jobs: value
            .get("active_jobs")
            .and_then(Value::as_array)
            .map(|jobs| {
                jobs.iter()
                    .map(|job| ProductionJob {
                        id: job
                            .get("id")
                            .and_then(Value::as_str)
                            .unwrap_or_default()
                            .to_owned(),
                        status: job
                            .get("status")
                            .and_then(Value::as_str)
                            .unwrap_or_default()
                            .to_owned(),
                        provider: job
                            .get("provider")
                            .and_then(Value::as_str)
                            .unwrap_or_default()
                            .to_owned(),
                        real_provider: job
                            .get("real_provider")
                            .and_then(Value::as_bool)
                            .unwrap_or(false),
                    })
                    .collect()
            })
            .unwrap_or_default(),
        active_leases: value
            .get("active_leases")
            .and_then(Value::as_array)
            .map(|leases| {
                leases
                    .iter()
                    .map(|lease| ProductionLease {
                        id: lease
                            .get("id")
                            .and_then(Value::as_str)
                            .unwrap_or_default()
                            .to_owned(),
                        owner: lease
                            .get("owner")
                            .and_then(Value::as_str)
                            .unwrap_or_default()
                            .to_owned(),
                        gpu: lease
                            .get("gpu")
                            .and_then(Value::as_str)
                            .unwrap_or_default()
                            .to_owned(),
                        exclusive: lease
                            .get("exclusive")
                            .and_then(Value::as_bool)
                            .unwrap_or(false),
                    })
                    .collect()
            })
            .unwrap_or_default(),
    })
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::{BridgeResponse, StackState, parse_production_activity, parse_stack_status};

    #[test]
    fn a_running_stack_reports_its_real_browser_address() {
        let value = json!({
            "state": "running",
            "manifest_present": true,
            "api_port": 8000,
            "web_port": 5173,
            "api_port_owner": 100,
            "web_port_owner": 101,
            "api_answers": true,
            "web_answers": true,
            "port_conflict": false,
            "processes": [{
                "name": "api",
                "pid": 100,
                "listener_pid": 100,
                "state": "same",
                "owners": [{"pid": 100, "state": "same"}]
            }]
        });
        let status = parse_stack_status(&value).unwrap();
        assert_eq!(status.state, StackState::Running);
        assert_eq!(status.web_url().as_deref(), Some("http://127.0.0.1:5173/"));
        assert!(status.summary().contains("正在运行"));
    }

    #[test]
    fn a_half_started_stack_has_no_browser_address() {
        let value = json!({
            "state": "half_started",
            "manifest_present": true,
            "web_port": 5173,
            "web_answers": false,
            "processes": []
        });
        let status = parse_stack_status(&value).unwrap();
        assert_eq!(status.state, StackState::HalfStarted);
        assert!(status.web_url().is_none());
        assert!(status.summary().contains("重新启动"));
    }

    #[test]
    fn a_port_conflict_is_reported_as_such() {
        let value = json!({
            "state": "port_conflict",
            "manifest_present": true,
            "api_port": 8000,
            "web_port": 5173,
            "port_conflict": true,
            "processes": []
        });
        let status = parse_stack_status(&value).unwrap();
        assert_eq!(status.state, StackState::PortConflict);
        assert!(status.port_conflict);
        assert!(status.summary().contains("端口"));
    }

    #[test]
    fn a_rejected_bridge_call_carries_a_stable_code() {
        let response = BridgeResponse {
            ok: false,
            protocol: "shine-launcher-bridge/1".to_owned(),
            operation: Some("sqlite_backup".to_owned()),
            result: None,
            error: Some(crate::bridge::BridgeErrorBody {
                code: "DATABASE_MISSING".to_owned(),
                message: "找不到工作台数据库".to_owned(),
            }),
        };
        assert!(!response.ok);
        let error = response.error.unwrap();
        assert_eq!(error.code, "DATABASE_MISSING");
    }

    #[test]
    fn production_activity_only_blocks_on_real_work() {
        let mock = parse_production_activity(&json!({
            "state": "idle",
            "real_production_jobs": 0,
            "exclusive_leases": 0,
            "blocks_maintenance": false,
            "active_jobs": [{
                "id": "job-1",
                "status": "RUNNING",
                "provider": "mock",
                "real_provider": false
            }],
            "active_leases": []
        }))
        .unwrap();
        assert!(!mock.blocks_maintenance);

        let real = parse_production_activity(&json!({
            "state": "active",
            "real_production_jobs": 1,
            "exclusive_leases": 1,
            "blocks_maintenance": true,
            "active_jobs": [{
                "id": "job-2",
                "status": "RUNNING",
                "provider": "comfyui",
                "real_provider": true
            }],
            "active_leases": [{
                "id": "lease-1",
                "owner": "worker",
                "gpu": "gpu-0",
                "exclusive": true
            }]
        }))
        .unwrap();
        assert!(real.blocks_maintenance);
        assert_eq!(real.active_jobs.len(), 1);
        assert!(real.active_leases[0].exclusive);
    }

    #[test]
    fn an_unknown_state_is_preserved_instead_of_being_guessed() {
        let status = parse_stack_status(&json!({
            "state": "something-new",
            "manifest_present": true,
            "processes": []
        }))
        .unwrap();
        assert!(matches!(status.state, StackState::Unknown(_)));
    }
}
