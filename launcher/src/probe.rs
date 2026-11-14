//! Read-only host probing.
//!
//! The probe runs before anything is installed and must therefore work on a
//! machine that has no Python, no Node, no ComfyUI and no GPU driver. It
//! never writes, and it never blocks the interface for long.
//!
//! The report distinguishes four things that are easy to confuse and that a
//! user must never be shown as the same:
//!
//! * *installed* — a file is on disk;
//! * *verified* — the file is there and its digest matches;
//! * *compatible* — the versions line up with what the studio needs;
//! * *unverified* — everything is in place, but nobody has actually run a
//!   real generation on this machine.

use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

use sysinfo::{Disks, System};

const COMMAND_TIMEOUT: Duration = Duration::from_millis(1500);
const POLL_INTERVAL: Duration = Duration::from_millis(30);

/// How far a probed item has actually been proven.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ProbeStatus {
    /// Everything is in place and nothing is known to be missing.
    Ready,
    /// The item was checked and matched what was expected.
    Verified,
    /// The item is present and compatible, but was not verified in depth.
    Compatible,
    /// Something is present but wrong, and the user has to repair it.
    NeedsRepair,
    /// The item is in place, but no real run has proved it works.
    Unverified,
    /// The item is not installed.
    Missing,
    /// The probe could not tell.
    Unknown,
}

impl ProbeStatus {
    /// The wording shown to a non technical user.
    pub fn label(self) -> &'static str {
        match self {
            Self::Ready => "已就绪",
            Self::Verified => "已验证",
            Self::Compatible => "兼容可用",
            Self::NeedsRepair => "需要修复",
            Self::Unverified => "已就位，尚未做真实验证",
            Self::Missing => "未安装",
            Self::Unknown => "无法确认",
        }
    }
}

/// One probed item.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ProbeItem {
    pub name: String,
    pub value: String,
    pub status: ProbeStatus,
}

impl ProbeItem {
    fn new(name: &str, value: String, status: ProbeStatus) -> Self {
        Self {
            name: name.to_owned(),
            value,
            status,
        }
    }

    /// The one line form shown in the interface.
    pub fn line(&self) -> String {
        format!("{}：{}（{}）", self.name, self.value, self.status.label())
    }
}

/// What was found for one GPU.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct GpuInfo {
    pub name: String,
    pub vram_mb: Option<u64>,
    pub driver_version: Option<String>,
}

/// The complete read-only report.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ProbeReport {
    pub os_summary: String,
    pub cpu_summary: String,
    pub memory_summary: String,
    pub disk_summary: String,
    pub node: String,
    pub npm: String,
    pub python: String,
    pub uv: String,
    pub ffmpeg: String,
    pub gpu: String,
    /// The structured items, used by the new interface.
    pub items: Vec<ProbeItem>,
    /// The GPUs that were found.
    pub gpus: Vec<GpuInfo>,
    /// The ComfyUI instances that were found.
    pub comfyui_instances: Vec<PathBuf>,
    /// The custom node directories that were found.
    pub node_directories: Vec<PathBuf>,
    /// The model files that were found.
    pub model_files: Vec<PathBuf>,
    /// The workflow files that were found.
    pub workflow_files: Vec<PathBuf>,
}

impl Default for ProbeReport {
    fn default() -> Self {
        Self {
            os_summary: "未知".to_owned(),
            cpu_summary: "未知".to_owned(),
            memory_summary: "未知".to_owned(),
            disk_summary: "未知".to_owned(),
            node: "未检测到".to_owned(),
            npm: "未检测到".to_owned(),
            python: "未检测到".to_owned(),
            uv: "未检测到".to_owned(),
            ffmpeg: "未检测到".to_owned(),
            gpu: "未检测到".to_owned(),
            items: Vec::new(),
            gpus: Vec::new(),
            comfyui_instances: Vec::new(),
            node_directories: Vec::new(),
            model_files: Vec::new(),
            workflow_files: Vec::new(),
        }
    }
}

/// Collect the full read-only report.
pub fn collect() -> ProbeReport {
    let mut system = System::new();
    system.refresh_memory();

    let os_name = System::name().unwrap_or_else(|| std::env::consts::OS.to_owned());
    let os_version = System::os_version().unwrap_or_else(|| "版本未知".to_owned());
    let cpu_count = thread::available_parallelism()
        .map(|count| count.get())
        .unwrap_or(1);
    let total_memory = system.total_memory();

    let disk_summary = launcher_disk_summary();
    let node = check_command("node", &["--version"], "Node.js");
    let npm = check_npm();
    let python = check_python();
    let uv = check_command("uv", &["--version"], "uv");
    let ffmpeg = check_ffmpeg();
    let (gpu, gpus) = check_nvidia();

    let mut items = vec![
        ProbeItem::new(
            "操作系统",
            format!("{os_name} {os_version}"),
            ProbeStatus::Ready,
        ),
        ProbeItem::new(
            "处理器",
            format!("{cpu_count} 个逻辑处理器"),
            ProbeStatus::Ready,
        ),
        ProbeItem::new(
            "内存",
            if total_memory == 0 {
                "内存信息不可用".to_owned()
            } else {
                format!("{} RAM", format_bytes(total_memory))
            },
            if total_memory == 0 {
                ProbeStatus::Unknown
            } else {
                ProbeStatus::Ready
            },
        ),
        ProbeItem::new("磁盘空间", disk_summary.clone(), ProbeStatus::Ready),
        ProbeItem::new("Node.js", node.clone(), tool_status(&node)),
        ProbeItem::new("npm", npm.clone(), tool_status(&npm)),
        ProbeItem::new("Python", python.clone(), tool_status(&python)),
        ProbeItem::new("uv", uv.clone(), tool_status(&uv)),
        ProbeItem::new("FFmpeg", ffmpeg.clone(), tool_status(&ffmpeg)),
    ];

    for (index, gpu_info) in gpus.iter().enumerate() {
        let vram = gpu_info
            .vram_mb
            .map(|value| format!("{value} MB 显存"))
            .unwrap_or_else(|| "显存未知".to_owned());
        let driver = gpu_info
            .driver_version
            .as_ref()
            .map(|value| format!("驱动 {value}"))
            .unwrap_or_else(|| "驱动未知".to_owned());
        items.push(ProbeItem::new(
            if index == 0 {
                "显卡"
            } else {
                "显卡（附加）"
            },
            format!("{} · {vram} · {driver}", gpu_info.name),
            // A GPU that reports its name and memory is present; whether it
            // can actually run a model is a separate, later question.
            ProbeStatus::Unverified,
        ));
    }
    if gpus.is_empty() {
        items.push(ProbeItem::new(
            "显卡",
            "未检测到 NVIDIA 显卡（不影响启动器打开）".to_owned(),
            ProbeStatus::Missing,
        ));
    }

    ProbeReport {
        os_summary: format!("{os_name} {os_version}"),
        cpu_summary: format!("{cpu_count} 个逻辑处理器"),
        memory_summary: if total_memory == 0 {
            "内存信息不可用".to_owned()
        } else {
            format!("{} RAM", format_bytes(total_memory))
        },
        disk_summary,
        node,
        npm,
        python,
        uv,
        ffmpeg,
        gpu,
        items,
        gpus,
        comfyui_instances: Vec::new(),
        node_directories: Vec::new(),
        model_files: Vec::new(),
        workflow_files: Vec::new(),
    }
}

/// The status of a tool that was probed by running it.
fn tool_status(value: &str) -> ProbeStatus {
    if value.contains("未检测到") || value.contains("缺少") {
        ProbeStatus::Missing
    } else if value.contains("超时") || value.contains("检查失败") || value.contains("无法")
    {
        ProbeStatus::Unknown
    } else {
        // The tool runs. Whether it is the *right* version is decided by the
        // deployment plan, not here.
        ProbeStatus::Unverified
    }
}

/// Inventory an installation's ComfyUI, models, nodes and workflows.
///
/// This is strictly read-only: it lists what exists and never verifies a
/// model by loading it.
pub fn inspect_installation(install_root: &Path) -> ProbeInventory {
    let mut inventory = ProbeInventory::default();

    let managed = install_root.join("managed");
    let candidates = [
        install_root.join("ComfyUI"),
        install_root.join("comfyui"),
        managed.join("ComfyUI"),
        install_root.join("runtime").join("ComfyUI"),
    ];
    for candidate in candidates {
        if candidate.join("main.py").is_file() {
            inventory.comfyui_instances.push(candidate);
        }
    }

    for instance in &inventory.comfyui_instances {
        let custom_nodes = instance.join("custom_nodes");
        if custom_nodes.is_dir() {
            if let Ok(entries) = std::fs::read_dir(&custom_nodes) {
                for entry in entries.flatten() {
                    if entry.path().is_dir() {
                        inventory.node_directories.push(entry.path());
                    }
                }
            }
        }
        let models = instance.join("models");
        collect_files(&models, &mut inventory.model_files, 4);
    }

    let workflows = install_root.join("config").join("workflows");
    collect_files(&workflows, &mut inventory.workflow_files, 3);
    inventory
}

/// What an installation contains.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct ProbeInventory {
    pub comfyui_instances: Vec<PathBuf>,
    pub node_directories: Vec<PathBuf>,
    pub model_files: Vec<PathBuf>,
    pub workflow_files: Vec<PathBuf>,
}

fn collect_files(root: &Path, into: &mut Vec<PathBuf>, depth: usize) {
    if depth == 0 || !root.is_dir() {
        return;
    }
    let Ok(entries) = std::fs::read_dir(root) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            collect_files(&path, into, depth - 1);
        } else if path.is_file() {
            into.push(path);
        }
    }
}

fn launcher_disk_summary() -> String {
    let executable = std::env::current_exe().ok();
    let disks = Disks::new_with_refreshed_list();
    let selected = executable
        .as_deref()
        .and_then(|exe| {
            disks
                .list()
                .iter()
                .filter(|disk| exe.starts_with(disk.mount_point()))
                .max_by_key(|disk| disk.mount_point().as_os_str().len())
        })
        .or_else(|| {
            disks
                .list()
                .iter()
                .find(|disk| disk.mount_point() == Path::new("/"))
        });

    selected.map_or_else(
        || "磁盘空间信息不可用".to_owned(),
        |disk| format!("{} 可用", format_bytes(disk.available_space())),
    )
}

fn check_python() -> String {
    #[cfg(windows)]
    {
        let launcher = run_version_command("py", &["-3", "--version"]);
        if launcher != "未检测到" {
            return format!("Python Launcher：{launcher}");
        }
        if let Some(path) = find_path_entry("python.exe") {
            return run_version_command(&path.to_string_lossy(), &["--version"]);
        }
        "未检测到可直接使用的 Python".to_owned()
    }

    #[cfg(not(windows))]
    {
        let result = run_version_command("python", &["--version"]);
        if result == "未检测到" {
            let fallback = run_version_command("py", &["-3", "--version"]);
            if fallback != "未检测到" {
                return format!("Python Launcher：{fallback}");
            }
        }
        result
    }
}

fn check_npm() -> String {
    #[cfg(windows)]
    {
        if find_path_entry("npm.cmd").is_some() {
            "npm.cmd 已找到（未执行脚本）".to_owned()
        } else {
            "未检测到 npm.cmd".to_owned()
        }
    }

    #[cfg(not(windows))]
    {
        check_command("npm", &["--version"], "npm")
    }
}

#[cfg(windows)]
fn find_path_entry(filename: &str) -> Option<std::path::PathBuf> {
    std::env::split_paths(&std::env::var_os("PATH")?).find_map(|directory| {
        let candidate = directory.join(filename);
        let path_text = candidate.to_string_lossy().to_ascii_lowercase();
        (candidate.is_file() && !path_text.contains("\\windowsapps\\")).then_some(candidate)
    })
}

fn check_ffmpeg() -> String {
    run_version_command("ffmpeg", &["-version"])
}

/// Read the GPU name, memory and driver version in one query.
fn check_nvidia() -> (String, Vec<GpuInfo>) {
    let output = run_version_command(
        "nvidia-smi",
        &[
            "--query-gpu=name,memory.total,driver_version",
            "--format=csv,noheader,nounits",
        ],
    );
    if output == "未检测到" {
        return (
            "未检测到 NVIDIA 工具（不影响 Launcher 启动）".to_owned(),
            Vec::new(),
        );
    }
    if output.starts_with("检测超时") {
        return (
            "NVIDIA 检查超时（不影响 Launcher 启动）".to_owned(),
            Vec::new(),
        );
    }
    if output.starts_with("已找到") {
        return (
            "已找到 NVIDIA 工具，但无法读取显卡信息".to_owned(),
            Vec::new(),
        );
    }

    let mut gpus = Vec::new();
    for line in output.lines() {
        let parts: Vec<&str> = line.split(',').map(str::trim).collect();
        if parts.is_empty() {
            continue;
        }
        let name = parts[0].to_owned();
        let vram_mb = parts.get(1).and_then(|value| value.parse::<u64>().ok());
        let driver_version = parts.get(2).map(|value| (*value).to_owned());
        if name.is_empty() {
            continue;
        }
        gpus.push(GpuInfo {
            name,
            vram_mb,
            driver_version,
        });
    }
    if gpus.is_empty() {
        return (
            "已找到 NVIDIA 工具，但无法读取显卡信息".to_owned(),
            Vec::new(),
        );
    }
    let summary = gpus
        .iter()
        .map(|gpu| {
            let vram = gpu
                .vram_mb
                .map(|value| format!("{value}MB"))
                .unwrap_or_else(|| "显存未知".to_owned());
            format!("{}（{vram}）", gpu.name)
        })
        .collect::<Vec<_>>()
        .join("、");
    (summary, gpus)
}

fn check_command(command: &str, args: &[&str], name: &str) -> String {
    let result = run_version_command(command, args);
    if result == "未检测到" {
        result
    } else {
        format!("{name}：{result}")
    }
}

fn run_version_command(command: &str, args: &[&str]) -> String {
    let mut child = match spawn_probe(command, args) {
        Ok(child) => child,
        Err(_) => return "未检测到".to_owned(),
    };

    let started = Instant::now();
    loop {
        match child.try_wait() {
            Ok(Some(_)) => break,
            Ok(None) if started.elapsed() < COMMAND_TIMEOUT => thread::sleep(POLL_INTERVAL),
            Ok(None) => {
                let _ = child.kill();
                let _ = child.wait();
                return "检测超时".to_owned();
            }
            Err(_) => {
                let _ = child.kill();
                let _ = child.wait();
                return "检查失败".to_owned();
            }
        }
    }

    match child.wait_with_output() {
        Ok(output) => {
            let text = if output.stdout.is_empty() {
                String::from_utf8_lossy(&output.stderr).into_owned()
            } else {
                String::from_utf8_lossy(&output.stdout).into_owned()
            };
            let first_line = text.lines().next().unwrap_or_default().trim();
            if output.status.success() && !first_line.is_empty() {
                first_line.chars().take(100).collect()
            } else if output.status.success() {
                "已安装（未返回版本）".to_owned()
            } else {
                "已找到，版本检查失败".to_owned()
            }
        }
        Err(_) => "检查失败".to_owned(),
    }
}

#[cfg(windows)]
fn spawn_probe(command: &str, args: &[&str]) -> std::io::Result<Child> {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;

    Command::new(command)
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .creation_flags(CREATE_NO_WINDOW)
        .spawn()
}

#[cfg(not(windows))]
fn spawn_probe(command: &str, args: &[&str]) -> std::io::Result<Child> {
    Command::new(command)
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
}

fn format_bytes(bytes: u64) -> String {
    const UNITS: [&str; 5] = ["B", "KiB", "MiB", "GiB", "TiB"];
    let mut value = bytes as f64;
    let mut unit = 0;
    while value >= 1024.0 && unit < UNITS.len() - 1 {
        value /= 1024.0;
        unit += 1;
    }
    format!("{value:.1} {}", UNITS[unit])
}

#[cfg(test)]
mod tests {
    use std::fs;
    use std::path::Path;

    use tempfile::{TempDir, tempdir_in};

    use super::{ProbeStatus, collect, format_bytes, inspect_installation, tool_status};

    fn isolated_tempdir() -> TempDir {
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("target/test-tmp");
        fs::create_dir_all(&root).unwrap();
        tempdir_in(root).unwrap()
    }

    #[test]
    fn formats_memory_and_disk_sizes() {
        assert_eq!(format_bytes(0), "0.0 B");
        assert_eq!(format_bytes(1024), "1.0 KiB");
        assert_eq!(format_bytes(1024 * 1024), "1.0 MiB");
    }

    #[test]
    fn the_host_probe_produces_the_required_offline_status() {
        let report = collect();
        assert!(!report.os_summary.trim().is_empty());
        assert!(!report.cpu_summary.trim().is_empty());
        assert!(!report.memory_summary.trim().is_empty());
        assert!(!report.disk_summary.trim().is_empty());
        assert!(!report.items.is_empty());
    }

    #[test]
    fn a_missing_tool_is_distinguished_from_a_broken_one() {
        assert_eq!(tool_status("未检测到"), ProbeStatus::Missing);
        assert_eq!(tool_status("Node.js：v22.0.0"), ProbeStatus::Unverified);
        assert_eq!(tool_status("检测超时"), ProbeStatus::Unknown);
    }

    #[test]
    fn every_probe_status_has_a_distinct_user_facing_meaning() {
        let statuses = [
            ProbeStatus::Ready,
            ProbeStatus::Verified,
            ProbeStatus::Compatible,
            ProbeStatus::NeedsRepair,
            ProbeStatus::Unverified,
            ProbeStatus::Missing,
            ProbeStatus::Unknown,
        ];
        let labels: Vec<&str> = statuses.iter().map(|status| status.label()).collect();
        let unique: std::collections::HashSet<&&str> = labels.iter().collect();
        assert_eq!(unique.len(), labels.len());
    }

    #[test]
    fn an_installation_without_comfyui_reports_nothing_found() {
        let temp = isolated_tempdir();
        let inventory = inspect_installation(temp.path());
        assert!(inventory.comfyui_instances.is_empty());
        assert!(inventory.node_directories.is_empty());
        assert!(inventory.model_files.is_empty());
    }

    #[test]
    fn a_managed_comfyui_with_nodes_and_models_is_inventoried() {
        let temp = isolated_tempdir();
        let root = temp.path();
        let instance = root.join("managed/ComfyUI");
        fs::create_dir_all(instance.join("custom_nodes/KJNodes")).unwrap();
        fs::write(instance.join("main.py"), "# comfy\n").unwrap();
        fs::create_dir_all(instance.join("models/diffusion_models")).unwrap();
        fs::write(
            instance.join("models/diffusion_models/h3.safetensors"),
            b"x",
        )
        .unwrap();
        fs::create_dir_all(root.join("config/workflows")).unwrap();
        fs::write(root.join("config/workflows/h3.json"), "{}").unwrap();

        let inventory = inspect_installation(root);
        assert_eq!(inventory.comfyui_instances.len(), 1);
        assert_eq!(inventory.node_directories.len(), 1);
        assert_eq!(inventory.model_files.len(), 1);
        assert_eq!(inventory.workflow_files.len(), 1);
    }
}
