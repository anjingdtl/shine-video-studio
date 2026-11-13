use std::path::Path;
use std::process::{Child, Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

use sysinfo::{Disks, System};

const COMMAND_TIMEOUT: Duration = Duration::from_millis(900);
const POLL_INTERVAL: Duration = Duration::from_millis(30);

#[derive(Debug)]
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
}

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
    let gpu = check_nvidia_smi();

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
        return "未检测到可直接使用的 Python".to_owned();
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
        return if find_path_entry("npm.cmd").is_some() {
            "npm.cmd 已找到（未执行脚本）".to_owned()
        } else {
            "未检测到 npm.cmd".to_owned()
        };
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

fn check_nvidia_smi() -> String {
    let status = run_version_command("nvidia-smi", &["--query-gpu=name", "--format=csv,noheader"]);
    if status == "未检测到" {
        "未检测到 NVIDIA 工具（不影响 Launcher 启动）".to_owned()
    } else if status.starts_with("检测超时") {
        "NVIDIA 检查超时（不影响 Launcher 启动）".to_owned()
    } else {
        status
            .lines()
            .next()
            .unwrap_or("检测到 NVIDIA GPU")
            .to_owned()
    }
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
    use super::format_bytes;

    #[test]
    fn formats_memory_and_disk_sizes() {
        assert_eq!(format_bytes(0), "0.0 B");
        assert_eq!(format_bytes(1024), "1.0 KiB");
        assert_eq!(format_bytes(1024 * 1024), "1.0 MiB");
    }

    #[test]
    fn host_probe_produces_required_offline_status() {
        let report = super::collect();
        assert!(!report.os_summary.trim().is_empty());
        assert!(!report.cpu_summary.trim().is_empty());
        assert!(!report.memory_summary.trim().is_empty());
        assert!(!report.disk_summary.trim().is_empty());
    }
}
