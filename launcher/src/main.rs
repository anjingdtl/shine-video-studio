#![cfg_attr(target_os = "windows", windows_subsystem = "windows")]

//! The Shine Launcher entry point.
//!
//! Three modes:
//!
//! * `--smoke-check` — a headless host probe that proves the binary starts
//!   without a studio, a network or a GPU.
//! * `--self-test` — a deeper headless run that exercises the installation
//!   registry, the upgrade state machine and the capability planner. The
//!   Windows build runs this in CI so a broken launcher is caught without a
//!   human opening a window.
//! * no argument — the native window.

slint::include_modules!();

use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::thread;

use shine_launcher::capability::{CapabilityPack, CapabilityStatus};
use shine_launcher::installation_registry::{
    DiscoverySource, InstallationRegistry, RegisteredInstallation, UpdateChannel,
    default_registry_path, launcher_state_dir,
};
use shine_launcher::journal::Journal;
use shine_launcher::legacy_scan::{DatabaseEvidence, LegacyScanResult, scan_existing_installation};
use shine_launcher::probe;
use shine_launcher::protocol::{self, ProtocolError, TrustedPublisherStore};
use shine_launcher::runner::{ExecutionOutcome, ScriptInvocation, ScriptKind, run_script};
use shine_launcher::self_update::{self, HELPER_ARGUMENT};
use shine_launcher::studio::{StudioController, StudioError, StudioInstallation};
use shine_launcher::update_channels::{
    GitHubReleaseScanner, ManagedChannel, ReleaseScanResult, ReleaseTarget, latest_candidate,
};

fn main() -> Result<(), slint::PlatformError> {
    let arguments: Vec<String> = std::env::args().skip(1).collect();

    if arguments.first().map(String::as_str) == Some("--smoke-check") {
        let report = probe::collect();
        if report.os_summary.is_empty() {
            std::process::exit(1);
        }
        println!("{}", report.os_summary);
        return Ok(());
    }

    if arguments.first().map(String::as_str) == Some("--self-test") {
        return match run_self_test() {
            Ok(report) => {
                println!("{report}");
                Ok(())
            }
            Err(message) => {
                eprintln!("自检失败：{message}");
                std::process::exit(1);
            }
        };
    }

    if arguments.first().map(String::as_str) == Some("--verify-manifest") {
        // Verify a signed deployment manifest against the committed trust
        // anchors. The release job and an operator can both use it.
        if arguments.len() < 2 {
            eprintln!("用法：shine-launcher --verify-manifest <清单文件>");
            std::process::exit(2);
        }
        return match verify_manifest(std::path::Path::new(&arguments[1])) {
            Ok(message) => {
                println!("{message}");
                Ok(())
            }
            Err(message) => {
                eprintln!("清单校验失败：{message}");
                std::process::exit(1);
            }
        };
    }

    if arguments.first().map(String::as_str) == Some(HELPER_ARGUMENT) {
        // Helper mode: swap the executable and start the new program.
        if arguments.len() < 4 {
            eprintln!("更新助手缺少参数");
            std::process::exit(2);
        }
        let parent_pid = std::env::var("SHINE_LAUNCHER_PARENT_PID")
            .ok()
            .and_then(|value| value.parse::<u32>().ok());
        return match self_update::run_helper(
            std::path::Path::new(&arguments[1]),
            std::path::Path::new(&arguments[2]),
            &arguments[3],
            parent_pid,
        ) {
            Ok(()) => Ok(()),
            Err(error) => {
                eprintln!("更新失败：{error}");
                std::process::exit(1);
            }
        };
    }

    run_window()
}

// ---------------------------------------------------------------------------
// Window
// ---------------------------------------------------------------------------

/// Everything the interface needs to know about the current installation.
#[derive(Clone, Debug, Default)]
struct AppState {
    installation: Option<RegisteredInstallation>,
    pending_scan: Option<LegacyScanResult>,
}

fn run_window() -> Result<(), slint::PlatformError> {
    let window = MainWindow::new()?;
    let weak = window.as_weak();
    let state: Arc<Mutex<AppState>> = Arc::default();

    if let Some(window) = weak.upgrade() {
        window.set_app_version(env!("CARGO_PKG_VERSION").into());
    }

    window.on_refresh_requested({
        let weak = weak.clone();
        let state = Arc::clone(&state);
        move || refresh_environment(weak.clone(), Arc::clone(&state))
    });
    window.on_start_requested({
        let weak = weak.clone();
        let state = Arc::clone(&state);
        move || run_studio_operation(weak.clone(), Arc::clone(&state), StudioOperation::Start)
    });
    window.on_stop_requested({
        let weak = weak.clone();
        let state = Arc::clone(&state);
        move || run_studio_operation(weak.clone(), Arc::clone(&state), StudioOperation::Stop)
    });
    window.on_install_requested({
        let weak = weak.clone();
        let state = Arc::clone(&state);
        move || run_studio_operation(weak.clone(), Arc::clone(&state), StudioOperation::Install)
    });
    window.on_choose_directory_requested({
        let weak = weak.clone();
        let state = Arc::clone(&state);
        move || choose_directory(weak.clone(), Arc::clone(&state))
    });
    window.on_register_requested({
        let weak = weak.clone();
        let state = Arc::clone(&state);
        move || register_installation(weak.clone(), Arc::clone(&state))
    });
    window.on_scan_versions_requested({
        let weak = weak.clone();
        move || scan_release_channels(weak.clone())
    });
    window.on_check_update_requested({
        let weak = weak.clone();
        move || check_launcher_update(weak.clone())
    });
    window.on_deploy_capability_requested({
        let weak = weak.clone();
        let state = Arc::clone(&state);
        move || deploy_capabilities(weak.clone(), Arc::clone(&state))
    });
    window.on_refresh_diagnostics_requested({
        let weak = weak.clone();
        let state = Arc::clone(&state);
        move || refresh_diagnostics(weak.clone(), Arc::clone(&state))
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

    load_registry_into(&state);
    refresh_environment(weak.clone(), Arc::clone(&state));
    window.run()
}

fn load_registry_into(state: &Arc<Mutex<AppState>>) {
    let Ok(path) = default_registry_path() else {
        return;
    };
    let Ok(registry) = InstallationRegistry::load(&path) else {
        return;
    };
    let installation = registry.preferred().cloned();
    if let Ok(mut guard) = state.lock() {
        guard.installation = installation;
    }
}

/// The installation the launcher acts on.
fn current_installation(state: &Arc<Mutex<AppState>>) -> Option<RegisteredInstallation> {
    state
        .lock()
        .ok()
        .and_then(|guard| guard.installation.clone())
}

fn controller_for(installation: &RegisteredInstallation) -> StudioController {
    StudioController::new(StudioInstallation::new(
        &installation.installation_id,
        &installation.install_root,
        &installation.effective_data_root(),
        installation.bootstrap_root.clone(),
    ))
}

fn refresh_environment(weak: slint::Weak<MainWindow>, state: Arc<Mutex<AppState>>) {
    let Some(window) = weak.upgrade() else {
        return;
    };
    if window.get_is_checking() {
        return;
    }
    window.set_is_checking(true);
    window.set_status_message("正在进行本机只读检查…".into());

    let worker_weak = weak.clone();
    let spawn_result = thread::Builder::new()
        .name("shine-launcher-env-probe".into())
        .spawn(move || {
            let report = probe::collect();
            let installation = current_installation(&state);
            let environment_text = report
                .items
                .iter()
                .map(|item| item.line())
                .collect::<Vec<_>>()
                .join("\n");
            let gpu_text = if report.gpus.is_empty() {
                "未检测到 NVIDIA 显卡。启动器本身仍可正常使用；视频生成能力不可用。".to_owned()
            } else {
                report
                    .gpus
                    .iter()
                    .map(|gpu| {
                        let vram = gpu
                            .vram_mb
                            .map(|value| format!("{value} MB 显存"))
                            .unwrap_or_else(|| "显存未知".to_owned());
                        let driver = gpu
                            .driver_version
                            .clone()
                            .unwrap_or_else(|| "驱动未知".to_owned());
                        format!("{} · {vram} · {driver}（尚未做真实生成验证）", gpu.name)
                    })
                    .collect::<Vec<_>>()
                    .join("\n")
            };
            let readiness = match &installation {
                Some(installation) => {
                    format!("已登记安装 · 目录 {}", installation.install_root.display())
                }
                None => "尚未选择安装目录".to_owned(),
            };
            let _ = worker_weak.upgrade_in_event_loop(move |window| {
                window.set_environment_text(environment_text.into());
                window.set_gpu_text(gpu_text.into());
                window.set_readiness(readiness.into());
                window.set_is_checking(false);
                window.set_status_message("检查完成。此页面不依赖 Studio 服务或网络。".into());
            });
        });
    if spawn_result.is_err() {
        if let Some(window) = weak.upgrade() {
            window.set_is_checking(false);
            window.set_status_message("无法创建环境检查线程，请稍后重试。".into());
        }
    }
}

/// What the interface asked the launcher to do to the workbench.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum StudioOperation {
    Install,
    Start,
    Stop,
}

fn run_studio_operation(
    weak: slint::Weak<MainWindow>,
    state: Arc<Mutex<AppState>>,
    operation: StudioOperation,
) {
    let Some(installation) = current_installation(&state) else {
        if let Some(window) = weak.upgrade() {
            window.set_status_message("请先在「安装目录」中选择并登记安装目录。".into());
        }
        return;
    };
    let Some(window) = weak.upgrade() else {
        return;
    };
    window.set_is_busy(true);
    window.set_status_message(match operation {
        StudioOperation::Install => "正在准备运行环境与依赖，请稍候…".into(),
        StudioOperation::Start => "正在启动工作台…".into(),
        StudioOperation::Stop => "正在停止工作台…".into(),
    });

    let worker_weak = weak.clone();
    let spawn_result = thread::Builder::new()
        .name("shine-launcher-studio".into())
        .spawn(move || {
            let controller = controller_for(&installation);
            let result: Result<(String, Option<String>), String> = match operation {
                StudioOperation::Install => controller
                    .install()
                    .map(|result| (result.message, None::<String>))
                    .map_err(|error: StudioError| error.to_string()),
                StudioOperation::Start => controller
                    .start()
                    .map(|result| {
                        let url = result
                            .stack
                            .as_ref()
                            .and_then(|stack| stack.web_url())
                            .unwrap_or_default();
                        (result.message, Some(url))
                    })
                    .map_err(|error: StudioError| error.to_string()),
                StudioOperation::Stop => controller
                    .stop()
                    .map(|result| (result.message, None::<String>))
                    .map_err(|error: StudioError| error.to_string()),
            };
            let _ = worker_weak.upgrade_in_event_loop(move |window| {
                window.set_is_busy(false);
                match result {
                    Ok((message, url)) => {
                        window.set_status_message(message.into());
                        if let Some(url) = url {
                            window.set_web_url(url.into());
                        }
                    }
                    Err(message) => {
                        window.set_status_message(message.into());
                    }
                }
            });
        });
    if spawn_result.is_err() {
        if let Some(window) = weak.upgrade() {
            window.set_is_busy(false);
            window.set_status_message("无法创建执行线程，请稍后重试。".into());
        }
    }
}

fn choose_directory(weak: slint::Weak<MainWindow>, state: Arc<Mutex<AppState>>) {
    let Some(window) = weak.upgrade() else {
        return;
    };
    window.set_is_busy(true);
    window.set_status_message("请选择包含 Shine 工作台的目录…".into());

    let worker_weak = weak.clone();
    let spawn_result = thread::Builder::new()
        .name("shine-launcher-legacy-scan".into())
        .spawn(move || {
            let selected = rfd::FileDialog::new()
                .set_title("选择 Shine 视频工作台目录")
                .pick_folder();
            let scan = selected.map(|path| (path.clone(), scan_existing_installation(&path)));
            let _ = worker_weak.upgrade_in_event_loop(move |window| {
                window.set_is_busy(false);
                match scan {
                    None => {
                        if let Ok(mut guard) = state.lock() {
                            guard.pending_scan = None;
                        }
                        window.set_can_register(false);
                        window.set_status_message("已取消目录选择；未访问任何目录。".into());
                    }
                    Some((path, Ok(result))) => {
                        let summary = format_legacy_scan(&result);
                        if let Ok(mut guard) = state.lock() {
                            guard.pending_scan = Some(result);
                        }
                        window.set_can_register(true);
                        window.set_directory_text(summary.into());
                        window.set_status_message(
                            format!("已完成对 {} 的只读识别。", path.display()).into(),
                        );
                    }
                    Some((path, Err(error))) => {
                        if let Ok(mut guard) = state.lock() {
                            guard.pending_scan = None;
                        }
                        window.set_can_register(false);
                        window.set_directory_text(format!("{}：{error}", path.display()).into());
                    }
                }
            });
        });
    if spawn_result.is_err() {
        if let Some(window) = weak.upgrade() {
            window.set_is_busy(false);
            window.set_status_message("无法创建目录扫描线程，请稍后重试。".into());
        }
    }
}

fn register_installation(weak: slint::Weak<MainWindow>, state: Arc<Mutex<AppState>>) {
    let scan = state
        .lock()
        .ok()
        .and_then(|guard| guard.pending_scan.clone());
    let Some(scan) = scan else {
        if let Some(window) = weak.upgrade() {
            window.set_status_message("请先成功扫描一个工作台目录。".into());
        }
        return;
    };
    let Some(window) = weak.upgrade() else {
        return;
    };
    window.set_is_busy(true);

    let worker_weak = weak.clone();
    let spawn_result = thread::Builder::new()
        .name("shine-launcher-registry-write".into())
        .spawn(move || {
            let result = (|| {
                let registry_path = default_registry_path().map_err(|error| error.to_string())?;
                let registry = InstallationRegistry::register_install_root_atomic(
                    &registry_path,
                    &scan.install_root,
                    scan.data_root.clone(),
                    scan.bootstrap_root.clone(),
                    UpdateChannel::Stable,
                    DiscoverySource::UserSelected,
                )
                .map_err(|error| error.to_string())?;
                let installation = registry
                    .preferred()
                    .cloned()
                    .ok_or_else(|| "登记后没有找到安装记录".to_owned())?;
                Ok::<_, String>(installation)
            })();
            let _ = worker_weak.upgrade_in_event_loop(move |window| {
                window.set_is_busy(false);
                match result {
                    Ok(installation) => {
                        if let Ok(mut guard) = state.lock() {
                            guard.installation = Some(installation.clone());
                            guard.pending_scan = None;
                        }
                        window.set_can_register(false);
                        window.set_installation_name(
                            installation.install_root.display().to_string().into(),
                        );
                        window.set_status_message(
                            "已登记安装。原有项目、数据库与模型未被修改，可以开始安装或启动。"
                                .into(),
                        );
                    }
                    Err(message) => {
                        window.set_status_message(format!("登记失败：{message}").into());
                    }
                }
            });
        });
    if spawn_result.is_err() {
        if let Some(window) = weak.upgrade() {
            window.set_is_busy(false);
            window.set_status_message("无法创建登记线程，请稍后重试。".into());
        }
    }
}

fn scan_release_channels(weak: slint::Weak<MainWindow>) {
    let Some(window) = weak.upgrade() else {
        return;
    };
    window.set_is_busy(true);
    window.set_status_message("正在读取 GitHub 发布元数据；不会下载或运行发布资产。".into());

    let worker_weak = weak.clone();
    let spawn_result = thread::Builder::new()
        .name("shine-launcher-release-scan".into())
        .spawn(move || {
            let result = GitHubReleaseScanner::default().scan(false);
            let _ = worker_weak.upgrade_in_event_loop(move |window| {
                window.set_is_busy(false);
                match result {
                    Ok(scan) => {
                        window.set_version_text(format_release_scan(&scan).into());
                        window.set_status_message(
                            "版本扫描只读取 GitHub 元数据；下载与安装仍需签名校验通过。".into(),
                        );
                    }
                    Err(error) => {
                        window.set_version_text(format!("版本扫描失败：{error}").into());
                        window
                            .set_status_message("未下载或执行任何更新；可检查网络后重试。".into());
                    }
                }
            });
        });
    if spawn_result.is_err() {
        if let Some(window) = weak.upgrade() {
            window.set_is_busy(false);
            window.set_status_message("无法创建版本扫描线程，请稍后重试。".into());
        }
    }
}

fn check_launcher_update(weak: slint::Weak<MainWindow>) {
    let Some(window) = weak.upgrade() else {
        return;
    };
    window.set_is_busy(true);
    window.set_status_message("正在检查启动器更新…".into());

    let worker_weak = weak.clone();
    let spawn_result = thread::Builder::new()
        .name("shine-launcher-self-update".into())
        .spawn(move || {
            let store = TrustedPublisherStore::embedded();
            let message = match store {
                Err(error) => format!("无法读取可信发布者列表：{error}"),
                Ok(store) => {
                    let publishers = store.trusted_publishers();
                    if publishers.is_empty() {
                        "未配置可信发布者密钥；自动更新保持关闭。".to_owned()
                    } else {
                        let names = publishers
                            .iter()
                            .map(|publisher| {
                                format!(
                                    "{}（{}）",
                                    publisher.key_id,
                                    publisher
                                        .products
                                        .iter()
                                        .map(|product| match product {
                                            protocol::ReleaseProduct::Studio => "工作台",
                                            protocol::ReleaseProduct::Launcher => "启动器",
                                        })
                                        .collect::<Vec<_>>()
                                        .join("、")
                                )
                            })
                            .collect::<Vec<_>>()
                            .join("；");
                        format!(
                            "已加载 {} 个可信发布者：{names}。更新会在下载后逐字节校验，校验不通过则保持当前版本。",
                            publishers.len()
                        )
                    }
                }
            };
            let _ = worker_weak.upgrade_in_event_loop(move |window| {
                window.set_is_busy(false);
                window.set_update_text(message.into());
                window.set_status_message("启动器自更新只替换启动器本身，不触碰工作台数据。".into());
            });
        });
    if spawn_result.is_err() {
        if let Some(window) = weak.upgrade() {
            window.set_is_busy(false);
            window.set_status_message("无法创建更新检查线程，请稍后重试。".into());
        }
    }
}

fn deploy_capabilities(weak: slint::Weak<MainWindow>, state: Arc<Mutex<AppState>>) {
    let Some(installation) = current_installation(&state) else {
        if let Some(window) = weak.upgrade() {
            window.set_status_message("请先在「安装目录」中选择并登记安装目录。".into());
        }
        return;
    };
    let Some(window) = weak.upgrade() else {
        return;
    };
    window.set_is_busy(true);
    window.set_status_message("正在读取能力清单…".into());

    let worker_weak = weak.clone();
    let spawn_result = thread::Builder::new()
        .name("shine-launcher-capability".into())
        .spawn(move || {
            let text = describe_capabilities(&installation);
            let _ = worker_weak.upgrade_in_event_loop(move |window| {
                window.set_is_busy(false);
                window.set_capability_text(text.into());
                window.set_status_message(
                    "能力状态来自项目清单；未经真实生成验证的项不会显示为已就绪。".into(),
                );
            });
        });
    if spawn_result.is_err() {
        if let Some(window) = weak.upgrade() {
            window.set_is_busy(false);
            window.set_status_message("无法创建能力检查线程，请稍后重试。".into());
        }
    }
}

/// Describe the capability packs of an installation.
fn describe_capabilities(installation: &RegisteredInstallation) -> String {
    let root = &installation.install_root;
    let instance = root.join("managed").join("ComfyUI");
    let packs = [
        (
            "H3 视频生成（Ref2VA INT8 ConvRot）",
            "config/deployment/h3-ref2va-windows-x64.json",
            "config/deployment/variants/h3-ref2va-int8-convrot.json",
        ),
        (
            "Qwen Image 2.1 图像",
            "config/deployment/qwen-image-2.1-windows-x64.json",
            "config/deployment/variants/h3-ref2va-int8-convrot.json",
        ),
    ];

    let mut lines = Vec::new();
    for (title, catalog, variant) in packs {
        let status = shine_launcher::capability::load_pack(root, catalog, variant);
        match status {
            Ok(pack) => {
                let status = pack.inspect(&instance, None);
                lines.push(format!("{}\n  {}", title, status.summary()));
                if !status.missing_components.is_empty() {
                    lines.push(format!(
                        "  缺少组件：{}",
                        status.missing_components.join("、")
                    ));
                }
            }
            Err(error) => lines.push(format!("{title}\n  无法读取能力清单：{error}")),
        }
    }
    lines.push(
        "说明：以上状态来自文件与清单核对，尚未进行真实生成验证，因此不会显示为「已就绪」。"
            .to_owned(),
    );
    lines.join("\n")
}

fn refresh_diagnostics(weak: slint::Weak<MainWindow>, state: Arc<Mutex<AppState>>) {
    let Some(installation) = current_installation(&state) else {
        if let Some(window) = weak.upgrade() {
            window.set_diagnostic_text("尚未登记安装目录，无法读取运行诊断。".into());
        }
        return;
    };
    let Some(window) = weak.upgrade() else {
        return;
    };
    window.set_is_busy(true);

    let worker_weak = weak.clone();
    let spawn_result = thread::Builder::new()
        .name("shine-launcher-diagnostics".into())
        .spawn(move || {
            let controller = controller_for(&installation);
            let mut lines = Vec::new();
            match controller.stack_status() {
                Ok(stack) => {
                    lines.push(format!("运行状态：{}", stack.state.user_message()));
                    if let Some(url) = stack.web_url() {
                        lines.push(format!("打开地址：{url}"));
                    }
                    if let Some(port) = stack.api_port {
                        lines.push(format!("接口端口：{port}"));
                    }
                    for process in &stack.processes {
                        lines.push(format!(
                            "进程 {} · PID {} · 身份判定 {}",
                            process.name.clone().unwrap_or_else(|| "未知".to_owned()),
                            process
                                .pid
                                .map(|value| value.to_string())
                                .unwrap_or_else(|| "未知".to_owned()),
                            process.state
                        ));
                    }
                }
                Err(error) => lines.push(format!("无法读取运行状态：{error}")),
            }
            match controller.production_activity() {
                Ok(activity) => lines.push(format!(
                    "真实生产任务：{} 个；独占租约：{} 个。",
                    activity.real_production_jobs, activity.exclusive_leases
                )),
                Err(error) => lines.push(format!("无法读取生产任务状态：{error}")),
            }
            lines.push(format!("安装目录：{}", installation.install_root.display()));
            lines.push(format!(
                "数据目录：{}",
                installation.effective_data_root().display()
            ));
            let text = lines.join("\n");
            let _ = worker_weak.upgrade_in_event_loop(move |window| {
                window.set_is_busy(false);
                window.set_diagnostic_text(text.into());
                window.set_status_message("诊断信息已刷新。".into());
            });
        });
    if spawn_result.is_err() {
        if let Some(window) = weak.upgrade() {
            window.set_is_busy(false);
            window.set_status_message("无法创建诊断线程，请稍后重试。".into());
        }
    }
}

// ---------------------------------------------------------------------------
// Formatting
// ---------------------------------------------------------------------------

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
        "工作台稳定版 {} / 预览版 {} · 启动器 {} · 数据来源：{source}{freshness}\n\
         工作台与启动器各自独立发布：更新工作台不会更换启动器程序。",
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
    let database = match scan.database_evidence {
        DatabaseEvidence::NoDatabaseFound => "未发现数据库".to_owned(),
        DatabaseEvidence::SqliteHeaderFound => "发现 SQLite 文件头".to_owned(),
        DatabaseEvidence::ExistingDatabaseUnrecognized => "发现数据库文件，格式未验证".to_owned(),
    };
    let toolchain = match scan.toolchain_receipt_matches_manifest {
        Some(true) => "清单哈希匹配（工具未验证）".to_owned(),
        Some(false) => "清单哈希不匹配".to_owned(),
        None => "未发现可核验回执".to_owned(),
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

/// Verify a signed deployment manifest against the committed trust anchors.
fn verify_manifest(path: &std::path::Path) -> Result<String, String> {
    let bytes = std::fs::read(path).map_err(|error| error.to_string())?;
    let store = TrustedPublisherStore::embedded().map_err(|error| error.to_string())?;
    let parsed = protocol::parse_manifest(&bytes, env!("CARGO_PKG_VERSION"), "windows-x64")
        .map_err(|error: ProtocolError| error.to_string())?;
    let verified = store
        .verify_studio_manifest(parsed)
        .map_err(|error: ProtocolError| error.to_string())?;
    Ok(format!(
        "清单校验通过\n发布者：{}\nsource_identity：{}\n工作台版本：{}",
        verified.verified_key_id(),
        verified.source_identity(),
        verified.manifest().studio.version
    ))
}

// ---------------------------------------------------------------------------
// Headless self test
// ---------------------------------------------------------------------------

/// A headless run that exercises the launcher's own logic.
///
/// The Windows build runs this in CI. It does not need a display, a studio, a
/// network or a GPU, so a broken state machine fails the build instead of
/// being discovered by a user.
fn run_self_test() -> Result<String, String> {
    let mut checks: Vec<String> = Vec::new();

    let report = probe::collect();
    if report.os_summary.trim().is_empty() {
        return Err("本机探测没有返回结果".to_owned());
    }
    checks.push(format!("本机探测：{}", report.os_summary));

    let state_directory = launcher_state_dir().map_err(|error| error.to_string())?;
    let journal = Journal::new(&state_directory).map_err(|error| error.to_string())?;
    let transaction_id = format!("self-test-{}", std::process::id());
    journal
        .append(
            &transaction_id,
            "self_test",
            shine_launcher::journal::JournalStatus::Started,
            serde_json::json!({"phase": "probe"}),
        )
        .map_err(|error| error.to_string())?;
    checks.push("事务日志：写入成功".to_owned());

    let store = TrustedPublisherStore::embedded().map_err(|error| error.to_string())?;
    let publishers = store.trusted_publishers();
    if publishers.is_empty() {
        checks.push("可信发布者：未配置（自动更新保持关闭）".to_owned());
    } else {
        checks.push(format!(
            "可信发布者：{} 个（{}）",
            publishers.len(),
            publishers
                .iter()
                .map(|publisher| publisher.key_id.as_str())
                .collect::<Vec<_>>()
                .join("、")
        ));
    }

    let manifest_path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../config/launcher/test-fixtures/deployment-protocol-v1-signed-production.json");
    let manifest_bytes = std::fs::read(&manifest_path).map_err(|error| error.to_string())?;
    let parsed =
        protocol::parse_manifest(&manifest_bytes, env!("CARGO_PKG_VERSION"), "windows-x64")
            .map_err(|error: ProtocolError| error.to_string())?;
    let verified = store
        .verify_studio_manifest(parsed)
        .map_err(|error: ProtocolError| error.to_string())?;
    checks.push(format!(
        "部署协议：已用正式发布者密钥验证通过（{}）",
        verified.verified_key_id()
    ));

    let mut plan = shine_launcher::upgrade::UpgradePlan::new(
        "inst-self-test",
        "1.0.0",
        verified.source_identity(),
    )
    .with_disk_budget(1024 * 1024);
    let hash = plan.freeze().map_err(|error| error.to_string())?;
    plan.verify_frozen().map_err(|error| error.to_string())?;
    checks.push(format!("升级计划：冻结哈希 {hash}"));

    let decision = shine_launcher::upgrade::decide_rollback(true);
    if decision != shine_launcher::upgrade::RollbackDecision::Reconcile {
        return Err("回滚判定不符合「新版本已写入即人工核对」的要求".to_owned());
    }
    checks.push("回滚判定：新版本已写入数据时拒绝盲目回退".to_owned());

    Ok(format!("Shine Launcher 自检通过\n{}", checks.join("\n")))
}

// Keep the unused import warnings away for items that are part of the public
// surface but only used in some modes.
#[allow(dead_code)]
fn _unused_anchors() {
    let _ = ScriptKind::Bootstrap;
    let _: Option<ExecutionOutcome> = None;
    let _: Option<StudioError> = None;
    let _: Option<CapabilityPack> = None;
    let _: Option<CapabilityStatus> = None;
    let _: Option<ScriptInvocation> = None;
    let _ = run_script;
}
