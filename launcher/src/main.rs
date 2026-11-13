#![cfg_attr(target_os = "windows", windows_subsystem = "windows")]

slint::include_modules!();

use shine_launcher::installation_registry::{
    InstallationRegistry, RegisteredInstallation, UpdateChannel, default_registry_path,
};
use shine_launcher::legacy_scan::{DatabaseEvidence, LegacyScanResult, scan_existing_installation};
use shine_launcher::probe;
use shine_launcher::update_channels::{
    GitHubReleaseScanner, ManagedChannel, ReleaseScanResult, ReleaseTarget, latest_candidate,
};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::thread;

fn main() -> Result<(), slint::PlatformError> {
    if std::env::args().any(|arg| arg == "--smoke-check") {
        let report = probe::collect();
        if report.os_summary.is_empty() {
            std::process::exit(1);
        }
        return Ok(());
    }

    let window = MainWindow::new()?;
    let weak = window.as_weak();
    let pending_scan: Arc<Mutex<Option<LegacyScanResult>>> = Arc::default();

    window.on_refresh_requested({
        let weak = weak.clone();
        move || refresh_environment(weak.clone())
    });
    window.on_about_requested({
        let weak = weak.clone();
        move || {
            if let Some(window) = weak.upgrade() {
                window.set_show_about(true);
            }
        }
    });
    window.on_close_about_requested({
        let weak = weak.clone();
        move || {
            if let Some(window) = weak.upgrade() {
                window.set_show_about(false);
            }
        }
    });
    window.on_choose_legacy_requested({
        let weak = weak.clone();
        let pending_scan = Arc::clone(&pending_scan);
        move || choose_legacy_directory(weak.clone(), Arc::clone(&pending_scan))
    });
    window.on_register_legacy_requested({
        let weak = weak.clone();
        let pending_scan = Arc::clone(&pending_scan);
        move || register_legacy_installation(weak.clone(), Arc::clone(&pending_scan))
    });
    window.on_scan_versions_requested({
        let weak = weak.clone();
        move || scan_release_channels(weak.clone())
    });
    if let Some(window) = weak.upgrade() {
        window.set_app_version(env!("CARGO_PKG_VERSION").into());
    }
    refresh_environment(weak);
    window.run()
}

fn refresh_environment(weak: slint::Weak<MainWindow>) {
    if let Some(window) = weak.upgrade() {
        if window.get_is_checking() {
            return;
        }
        window.set_is_checking(true);
        window.set_status_message("正在进行本机只读检查…".into());
    } else {
        return;
    }

    let worker_weak = weak.clone();
    let spawn_result = thread::Builder::new()
        .name("shine-launcher-env-probe".into())
        .spawn(move || {
            let report = probe::collect();
            let registry_summary = load_registry_summary();
            let _ = worker_weak.upgrade_in_event_loop(move |window| {
                window.set_os_summary(report.os_summary.into());
                window.set_cpu_summary(report.cpu_summary.into());
                window.set_memory_summary(report.memory_summary.into());
                window.set_disk_summary(report.disk_summary.into());
                window.set_node_summary(report.node.into());
                window.set_npm_summary(report.npm.into());
                window.set_python_summary(report.python.into());
                window.set_uv_summary(report.uv.into());
                window.set_ffmpeg_summary(report.ffmpeg.into());
                window.set_gpu_summary(report.gpu.into());
                window.set_registry_summary(registry_summary.into());
                window.set_is_checking(false);
                window.set_status_message("检查完成。此页面不依赖 Studio 服务或网络。".into());
            });
        });
    if spawn_result.is_err() {
        let _ = weak.upgrade_in_event_loop(|window| {
            window.set_is_checking(false);
            window.set_status_message("无法创建环境检查线程，请稍后重试。".into());
        });
    }
}

fn choose_legacy_directory(
    weak: slint::Weak<MainWindow>,
    pending_scan: Arc<Mutex<Option<LegacyScanResult>>>,
) {
    if let Some(window) = weak.upgrade() {
        if window.get_is_scanning_legacy() {
            return;
        }
        window.set_is_scanning_legacy(true);
        window.set_can_register_installation(false);
        window.set_legacy_scan_summary("请选择包含 Shine Studio 的本机目录…".into());
    } else {
        return;
    }

    let worker_weak = weak.clone();
    let worker_state = Arc::clone(&pending_scan);
    let spawn_result = thread::Builder::new()
        .name("shine-launcher-legacy-scan".into())
        .spawn(move || {
            let selected = rfd::FileDialog::new()
                .set_title("选择 Shine 视频工作台目录")
                .pick_folder();
            let scan = selected.map(|path| (path.clone(), scan_existing_installation(&path)));
            let _ = worker_weak.upgrade_in_event_loop(move |window| {
                window.set_is_scanning_legacy(false);
                match scan {
                    None => {
                        *worker_state
                            .lock()
                            .unwrap_or_else(|error| error.into_inner()) = None;
                        window.set_can_register_installation(false);
                        window.set_legacy_scan_summary("已取消目录选择；未访问任何目录。".into());
                    }
                    Some((path, Ok(result))) => {
                        let summary = format_legacy_scan(&result);
                        *worker_state
                            .lock()
                            .unwrap_or_else(|error| error.into_inner()) = Some(result);
                        window.set_can_register_installation(true);
                        window.set_legacy_scan_summary(summary.into());
                        window.set_status_message(
                            format!("已完成对 {} 的只读识别。", path.display()).into(),
                        );
                    }
                    Some((path, Err(error))) => {
                        *worker_state
                            .lock()
                            .unwrap_or_else(|error| error.into_inner()) = None;
                        window.set_can_register_installation(false);
                        window
                            .set_legacy_scan_summary(format!("{}：{error}", path.display()).into());
                    }
                }
            });
        });
    if spawn_result.is_err() {
        if let Some(window) = weak.upgrade() {
            window.set_is_scanning_legacy(false);
            window.set_legacy_scan_summary("无法创建旧版目录扫描线程，请稍后重试。".into());
        }
    }
}

fn register_legacy_installation(
    weak: slint::Weak<MainWindow>,
    pending_scan: Arc<Mutex<Option<LegacyScanResult>>>,
) {
    let scan = pending_scan
        .lock()
        .unwrap_or_else(|error| error.into_inner())
        .clone();
    let Some(scan) = scan else {
        if let Some(window) = weak.upgrade() {
            window.set_status_message("请先成功扫描一个 Shine Studio 目录。".into());
        }
        return;
    };

    let worker_weak = weak.clone();
    let spawn_result = thread::Builder::new()
        .name("shine-launcher-registry-write".into())
        .spawn(move || {
            let result = (|| {
                let registry_path = default_registry_path().map_err(|error| error.to_string())?;
                let record = RegisteredInstallation::from_legacy_scan(&scan, UpdateChannel::Stable)
                    .map_err(|error| error.to_string())?;
                let registry = InstallationRegistry::upsert_atomic(&registry_path, record)
                    .map_err(|error| error.to_string())?;
                Ok::<_, String>(registry.installations.len())
            })();
            let _ = worker_weak.upgrade_in_event_loop(move |window| match result {
                Ok(count) => {
                    window.set_can_register_installation(false);
                    window.set_registry_summary(
                        format!("已登记 Shine 安装：{count} 个（运行能力未验证）").into(),
                    );
                    window.set_status_message(
                        "注册路径已安全写入 Launcher 用户注册表。Studio 目录与数据未修改。".into(),
                    );
                }
                Err(error) => {
                    window.set_status_message(format!("登记失败：{error}").into());
                }
            });
        });
    if spawn_result.is_err() {
        if let Some(window) = weak.upgrade() {
            window.set_status_message("无法创建注册表写入线程，请稍后重试。".into());
        }
    }
}

fn scan_release_channels(weak: slint::Weak<MainWindow>) {
    if let Some(window) = weak.upgrade() {
        if window.get_is_scanning_versions() {
            return;
        }
        window.set_is_scanning_versions(true);
        window.set_release_summary("正在读取 GitHub 发布元数据；不会下载或运行发布资产。".into());
    } else {
        return;
    }

    let worker_weak = weak.clone();
    let spawn_result = thread::Builder::new()
        .name("shine-launcher-release-scan".into())
        .spawn(move || {
            let result = GitHubReleaseScanner::default().scan(false);
            let _ = worker_weak.upgrade_in_event_loop(move |window| {
                window.set_is_scanning_versions(false);
                match result {
                    Ok(result) => {
                        window.set_release_summary(format_release_scan(&result).into());
                        window.set_status_message(
                            "版本扫描只读取 GitHub 元数据；由于受信发布者密钥未配置，安装和自更新保持关闭。".into(),
                        );
                    }
                    Err(error) => {
                        window.set_release_summary(format!("版本扫描失败：{error}").into());
                        window.set_status_message(
                            "未下载或执行任何更新；可检查网络后重试。".into(),
                        );
                    }
                }
            });
        });
    if spawn_result.is_err() {
        if let Some(window) = weak.upgrade() {
            window.set_is_scanning_versions(false);
            window.set_release_summary("无法创建版本扫描线程，请稍后重试。".into());
        }
    }
}

fn format_release_scan(scan: &ReleaseScanResult) -> String {
    let version = |target, channel| {
        latest_candidate(&scan.candidates, target, channel)
            .map(|candidate| candidate.version.to_string())
            .unwrap_or_else(|| "—".to_owned())
    };
    let source = if scan.from_cache {
        "缓存"
    } else {
        "刚更新"
    };
    let freshness = if scan.stale {
        " · 缓存已过期"
    } else {
        ""
    };
    format!(
        "Studio 稳定 {} / Beta {} · Launcher {} · {source}{freshness}",
        version(ReleaseTarget::Studio, ManagedChannel::Stable),
        version(ReleaseTarget::Studio, ManagedChannel::Beta),
        version(ReleaseTarget::Launcher, ManagedChannel::Stable),
    )
}

fn format_legacy_scan(scan: &LegacyScanResult) -> String {
    let data_root = scan
        .data_root
        .as_ref()
        .map(|path| path.display().to_string())
        .unwrap_or_else(|| "未发现（未创建）".to_owned());
    let database = match &scan.database_evidence {
        DatabaseEvidence::NoDatabaseFound => "未发现数据库".to_owned(),
        DatabaseEvidence::SqliteHeaderFound => "发现 SQLite 文件头".to_owned(),
        DatabaseEvidence::ExistingDatabaseUnrecognized => "发现数据库文件，格式未验证".to_owned(),
    };
    let toolchain = match scan.toolchain_receipt_matches_manifest {
        Some(true) => "清单哈希匹配（工具未验证）",
        Some(false) => "清单哈希不匹配",
        None => "未发现可核验回执",
    };
    let runtime_receipt = if scan.runtime_receipt_found {
        "发现（未验证）"
    } else {
        "未发现"
    };
    let bootstrap_root = scan
        .bootstrap_root
        .as_ref()
        .map(|path| path.display().to_string())
        .unwrap_or_else(|| "未配置".to_owned());
    let warnings = scan
        .warnings
        .iter()
        .take(2)
        .cloned()
        .collect::<Vec<_>>()
        .join("；");
    format!(
        "已识别目录：{}\n数据目录：{} · 启动依赖根：{} · {}\n固定工具目录 {} 个 · 工具回执 {} · Studio 回执 {}\n模型目录 {} 个 / ComfyUI {} 个（均未验证）{}",
        scan.install_root.display(),
        data_root,
        bootstrap_root,
        database,
        scan.tool_roots_unverified.len(),
        toolchain,
        runtime_receipt,
        scan.model_directories_unverified.len(),
        scan.comfyui_directories_unverified.len(),
        if warnings.is_empty() {
            String::new()
        } else {
            format!("\n提示：{warnings}")
        },
    )
}

fn load_registry_summary() -> String {
    match default_registry_path().and_then(|path: PathBuf| InstallationRegistry::load(&path)) {
        Ok(registry) => format!(
            "已登记 Shine 安装：{} 个（运行能力未验证）",
            registry.installations.len()
        ),
        Err(_) => "Launcher 安装注册表尚未创建或暂不可用".to_owned(),
    }
}
