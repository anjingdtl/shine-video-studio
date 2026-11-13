# Shine Launcher V1.0｜独立 Windows 启动器与项目生命周期管理工程建设方案

> 类型：可执行工程设计 / 待开发 / 非完成验收证明
> 日期：2026-11-13
> 仓库：https://github.com/anjingdtl/shine-video-studio
> 独立方案分支：feat/shine-launcher-v1-20261113
> 文档基线：main@8e91f7edfe0c1c2a820f7063dbca448145c70a21
> 开发方式：Agent 在**独立 Git worktree + 单独实现分支**中施工，最终验收后经 PR 合入 main，绝不直接修改主分支。
> 参考：feat/unified-deployment-upgrade-v2-20261110 分支中的 docs/SHINE_UNIFIED_DEPLOYMENT_V2_ENGINEERING_PLAN_20261110.md，仅为历史方案参考；不从该旧分支合并过时代码。
> 状态：PLAN_ONLY。本次方案提交仅增加当前 MD，不代表已有独立 EXE、GitHub 更新、全自动安装或真实 Windows 新机验收通过。

## 0. 产品决策（具有最高优先级）

**最终产品**：Shine Launcher.exe 是真正独立编译的 Windows 桌面应用，不是 cmd/ps1 快捷方式，也不是依靠工作台正在运行的 WebView 页面。用户从 GitHub Clone 或发行 ZIP 获取项目时，直接可双击该 EXE，选择项目安装位置/已有旧版目录，检查本机 Node.js、npm、uv、Python、FFmpeg、ComfyUI、显卡/模型/自定义节点/工作流；在批准明确的计划后，一键安装、修复、升级、启动与安全停止 Shine 视频工作台。

**双生命周期，不强关联**：
1. Launcher 有自身版本、独立发布通道、独立自更新/回退功能；只有 Launcher 自身安全、协议或功能变化才更新 Launcher。
2. Studio（Shine 视频工作台主体）有自己的稳定版/Beta/开发版、GitHub 代码/发布包版本管理，工作台功能更新原则上不更换 Launcher EXE。
3. Node、Python、uv、ComfyUI、模型、节点、工作流的兼容组合归属于**Studio 的版本化部署清单**，由 Launcher 解释和执行。发现官方上游新版只是信息，不自动修改可运行的固定组合。
4. 通过稳定的 Deployment Protocol 协议版本协作：Studio 在兼容协议范围内可自由进化；协议不支持时 Launcher 必须 fail closed，提示需要更新 Launcher，不能执行未知指令。
5. Launcher 即使在 Python/Node/ComfyUI 全部缺失、Studio API/Worker 不能启动、NVIDIA 驱动异常或无网络时，也必须可以打开、显示准确的本机诊断及离线恢复入口。
6. 用户的制作业务永远留在 Studio；Launcher 只负责软件、依赖、目录、版本、运行、修复和诊断。
7. 主要面向 Windows 10/11 x64，安装用户免管理员权限、无需用户自己运行命令行。其他平台非 V1 目标。

**明确保留的项目不变量**：
- MiniMax-H3 Ref2VA INT8 ConvRot 是当前正式视频生成默认组合；其他低显存候选不能被自动替代或解锁。
- 人物/场景/道具/音色主资产需用户审批，冻结风格、镜头绑定、审片和外部请求不确定结果继续受原有业务合同约束。
- 旧版 SQLite、项目、素材、影片、LLM 密钥、模型权重和用户自定义 ComfyUI 不能因更新/修复被无声覆盖、删除、替换或移走。
- 云端 Agent 或构建 CI 不是用户本机运行时依赖。
- 没有真实证明的安装/模型能力不得显示 READY 或 PASS。

## 1. 远端实际工程盘点（以 main@8e91f7ed 为准）

| 当前路径 | 现实能力 | Launcher 施工边界 |
| --- | --- | --- |
| 根目录 启动 Shine 工作台.cmd、scripts/start_dev.ps1 | 开发环境启动入口，要求已有依赖 | 保留作为开发/故障恢复入口；正式用户入口转换到独立 EXE |
| packaging/windows/SETUP.cmd、 一键安装并启动.cmd、START_APP.cmd、STOP_APP.cmd | 发行 ZIP 的脚本式安装/启动/停止 | 迁移为 EXE 的兼容 fallback；不得为删除脚本而破坏旧版/测试 |
| scripts/install_once.ps1、setup_wizard.ps1、bootstrap.ps1 | PowerShell 5.1 自举、选盘、工具链固定版本下载、Web 首次配置 | 优先复用核心逻辑，增加机器可读状态桥，不再强制用户看控制台 |
| scripts/start_shine.ps1、stop_shine.py、verify_stack_operations.ps1 | uv.lock/npm lock 更新、Alembic、API/Worker/Web 启停、进程身份核验 | 抽象受控调用，不能按 PID 盲杀、不能从打开 Launcher 直接触发数据库迁移 |
| backend/shine/comfyops/host_env.py、recommendation.py、variants.py | 只读 GPU/RAM/磁盘/依赖探测和固定 variant 资格 | 同一事实来源；Bootstrap 期仅做与之相容的最小探测 |
| backend/shine/comfyops/existing_installation.py、adoption.py | 有限目录发现、权重哈希核验、严格 ComfyUI 收编 | 允许用户指定旧版路径；先只读，条件满足才收编，其他情况隔离重建 |
| backend/shine/comfyops/installer.py、installation.py、installation_worker.py、instances.py、runtime_dependencies.py | 已有哈希固定下载、部分断点续传、安装尝试、节点依赖、实例快照/回滚 | Launcher 调用/编排这些受控路径，不新造并行包管理器 |
| backend/shine/comfyops/deployment.py、service_validation.py、profile_gate.py | 版本化 H3 部署目录、工作流合同、服务实测、生产准入 | 复用权威准入，严禁按文件存在直接标记生产 READY |
| config/deployment/*、config/workflows/* | 固定工具版本、H3/可选审片和 Qwen 模型清单、工作流 | 作为 Studio 能力依赖清单源，扩展稳定协议包装，避免二次硬编码 |
| frontend/src/components/SetupPanel.tsx、setupExecution.ts | Web 环境向导、安装进度及回执续接 | 仅做功能去重和状态桥；先保证 Launcher 能在 Web 故障时运行 |
| scripts/build_test_package.ps1、.github/workflows/ci.yml | 从已提交树生成发行 ZIP、文件校验，CI 分层执行 | 新增 Launcher 可复现 Windows 构建、二进制交付与版本独立发布 |
| AGENTS.md | 强制开发/发行/进程/GPU/迁移/后端八分片回归规范 | 实施前阅读最新文件；新增独立 Launcher 规范，不静默取代既有安全要求 |

目前**没有**独立的 Rust/C# Launcher EXE、Launcher 单独发布通道、稳定 Deployment Protocol、可供 Clone 根目录直接双击的已构建原生二进制。因此这些都是需要实际开发和 Windows 构建验证的交付项。

当前仓库主线正在高频变化，尤其导演模式和资源业务；该工程不能覆盖其他分支代码。此前 V2 部署方案的“版本升级事务、能力包依赖图、旧版迁移”应作为后台技术合同继续沿用，但不再把 Web 向导当作用户唯一安装入口。

## 2. 总体架构与模块职责

    [GitHub Clone / Source ZIP / Studio Release ZIP / 本机旧安装]
                           |
                   Shine Launcher.exe
                  (独立原生 Windows GUI)
                           |
          +----------------+----------------+
          |                |                |
    只读环境/目录扫描   独立项目版本管理  独立 Launcher 自更新
          |                |                |
          +------- Deployment Protocol v1 ---+
                           |
         版本化差异计划 + 用户批准 + 耐久事务/日志
                           |
              受控部署执行器 / 兼容桥接层
                           |
       +----------+---------+-----------+----------+
       | 工具依赖 | Studio 版本 | ComfyUI/模型 | 数据备份 |
       +----------+---------+-----------+----------+
                           |
          进程身份核验 → API/Worker/Web 启停
                           |
                  Shine Studio 工作台
             导演 / 素材 / Agent / 成片
     
架构分三平面：
- Bootstrap Plane（Launcher 原生核心）：无任何 Studio Python/Node 依赖，读配置、读取文件和原生系统信息、旧版发现、HTTP 更新元数据、可信文件下载/校验、启动/停止协调、恢复 journal；当 Studio 损坏时仍可工作。
- Deployment Plane（版本化部署协议 + 执行）：将 Studio 需求解析为有限类型的允许动作；优先复用现有 PowerShell/后端安装与实例子计划。协议不可注入任意 shell 命令。
- Studio Plane：原业务 FastAPI/React/SQLite/Worker/受管 ComfyUI；准备完成后 Launcher 可控制启动并打开本机浏览器。

**唯一命令执行所有者**：原生 Launcher 负责跨版本安装事务和 pre-API 阶段；Studio API 负责它运行后已经有审计/批准的项目内安装步骤；不得再建立第三套会自启动或并发修改同一 ComfyUI 的代理。

## 3. 原生桌面技术选型与必须先做的 PoC

**首选**：Rust + Slint（本地 EXE，Winit 窗口 + CPU software renderer），Rust 负责状态机、路径/下载/子进程/身份锁，Slint 负责界面。完整用户端不应需要 Cargo、Rust、Python、Node、管理员、WebView2、GPU 驱动，亦不应依赖已启动的 localhost API。

**不可略过的选择门槛**：
1. 在干净 Windows 10/11 x64、无 Node/Python/ComfyUI、禁用 GPU 加速、中文系统/目录下，独立 EXE 双击打开并能进行只读探测。
2. Slint 官方 Winit software renderer 可以无 GPU，但官方文档说明通用软件渲染器对西文以外文本存在限制；**必须真机验证简体中文字体/输入、DPI、中文目录、无障碍和深色模式**。若中文渲染不符合产品质量，先尝试受支持的 Skia software renderer；仍不符时将 C# WPF self-contained 作为备选，变更须在设计记录（ADR）解释，不能私自退回 WebView 包装。
3. Slint 许可：需核对官方 Community/Royalty-free 桌面许可，按许可显示 Slint Attribution/声明；本仓库 MIT 不等于 Slint 自身也为 MIT。可选 GPLv3/商业许可证须单独法律评估，不能自动混淆。官方资料：https://slint.dev/get-started、https://github.com/slint-ui/slint/blob/master/LICENSE.md、https://docs.slint.dev/latest/docs/slint/guide/backends-and-renderers/backend_winit/。
4. Windows 原生编译需要明确 MSVC/UCRT/动态运行库依赖，若支持单 EXE 交付须在真正的无开发工具 VM 上验证，不能仅凭 Rust 编译成功推断可运行。
5. GUI 主线程不得执行大文件哈希/下载或同步子进程；后台有 cancel token、进度事件和限速，退出后保留安全 checkpoint。

**推荐源码目录（不约束内部具体模块文件名）**：

    launcher/
      Cargo.toml
      Cargo.lock
      build.rs
      src/
        main.rs
        app_state.rs
        env_probe.rs
        installation_registry.rs
        protocol.rs
        update_channels.rs
        download.rs
        runner.rs
        process_ownership.rs
        upgrade.rs
        recovery.rs
        telemetry_local.rs
      ui/
        main.slint
        components/
      tests/
        protocol_fixtures/
        legacy_installations/
      README.md
      LICENSE_NOTICES.md
    config/launcher/
      launcher-release-policy.json
      trusted-publishers.json
      deployment-protocol-v1.schema.json
      test-fixtures/
    packaging/launcher/
      build_windows.ps1
      verify_windows.ps1
      README.md

名称可随技术栈调整，但 Launcher 工程必须独立锁定 Rust/Slint 版本与构建参数，其编译产物不依赖 Studio 的前端 npm/后端 Python 安装成功。

## 4. 双版本管理：Launcher、Studio 与依赖三者独立

### 4.1 身份与通道

| 生命周期 | 版本身份 | 更新触发 | 独立性 |
| --- | --- | --- | --- |
| Launcher | Launcher SemVer + build hash + executable SHA | 安全修复、GUI/更新器/协议支持改变 | Studio 普通更新完全不触发 |
| Studio | Studio 版本/commit/build SHA + ZIP 文件清单 | Stable/Beta/Development 有已验证新版本 | Launcher 可以保持不变 |
| 工具/运行时 | 与 Studio 版本或 capability manifest 绑定的 component revision + hashes | 安装计划要求且通过兼容检验 | 不因 Node/Python 官方上游有新版本就擅自升级 |
| Deployment Protocol | protocol_version / min_launcher_version / feature flags | 只有协议不兼容才要求 Launcher 更新 | 后向兼容时 Studio 可单独发布 |

Studio 侧保留三条通道：
- Stable：正式已验证、带不可变 release artifacts、完整文件哈希/签名的安装包；普通用户默认。
- Beta：预发布包，显式选择，提示可能不稳定。
- Development：GitHub main 或某一指定分支/commit，只有显式启用开发者模式才开放；开发分支源码不是稳定版，不做隐式生产升级。

Launcher 自身有单独稳定发布通道（例如独立前缀 launcher-vX.Y.Z 的 GitHub tag/release、独立 launcher manifest URL）；**不得**因 Studio tag、commit 变更而触发 Launcher 发布。若 Repo 同时有 Studio 和 Launcher Releases，版本列表按独立的 tag prefix 或专用资产元数据筛选，不按“仓库最新 Release”误取另一个产品。

### 4.2 官方上游更新≠受支持版本升级

Launcher 能只读展示 Node、Python、ComfyUI、节点等上游新版本及发布日期/变更提示，但**自动安装仅允许 Studio 当前兼容清单内的固定组合**。例如 ComfyUI 上游 HEAD 比固定版本新，不应自动 git pull 外部实例并破坏 H3。

上游查询采用缓存、ETag、超时、限频、代理支持、断网降级；GitHub API 匿名限流时显示具体原因和最近缓存时间。来源未验证/离线时可继续已安装 Studio 的启动与诊断。

## 5. Deployment Protocol v1：让 Studio 高频更新无需重编 Launcher

### 5.1 协议的责任边界

Studio 每次发行附带 deployment manifest，声明该发行所需的工具、运行时、模型/节点/工作流和版本策略；Launcher 验证来源、schema、签名/校验、资格后组装有序操作计划。Launcher 是执行授权的边界，Studio manifest 是**配置数据而非可执行程序**。

在同一协议主版本下允许：
- 增加受支持的可选模型能力包/工作流、修改精确哈希与来源；
- 改用新的 Node/Python/ComfyUI 固定版本及相应迁移策略（执行器已支持的动作）；
- 调整启动的 API/Web 运行参数中可白名单表示的字段、存储预算和部署次序；
- 通过已有语义操作执行需要的数据备份、受管实例构建、节点安装和验证。

**禁止**由 manifest 携带 arbitrary command/script/PowerShell expression/shell syntax/任意 exe URL 并让 Launcher 执行。新操作类型需要协议升级或注册的受信任适配器，不允许模板字符串替换成危险命令。

### 5.2 协议参考 Schema（字段示例，正式 Schema 由 Agent 给出）

    {
      "protocol_version": "1.0",
      "studio": {
        "name": "Shine Video Studio",
        "version": "0.3.0",
        "channel": "stable",
        "commit": "<full-commit-sha>",
        "build_id": "<immutable-build-id>"
      },
      "compatibility": {
        "min_launcher_version": "1.0.0",
        "supported_protocol_major": 1,
        "platform": ["windows-x64"]
      },
      "artifacts": [
        {
          "id": "studio-package",
          "url": "https://.../ShineStudio.zip",
          "sha256": "<64-hex>",
          "size_bytes": 123456
        }
      ],
      "toolchain_catalog": "config/deployment/tools-windows-x64.json",
      "capability_catalogs": [
        "config/deployment/h3-ref2va-windows-x64.json",
        "config/deployment/qwen-image-2.1-windows-x64.json"
      ],
      "workflow_root": "config/workflows/",
      "operations": ["probe", "install", "repair", "start", "stop", "upgrade"],
      "data_policy": "preserve-and-backup",
      "source_identity": "<manifest-hash>"
    }

示意不是实际发行版本、URL、大小或签名。正式机器可读 Schema 应以现有 Pydantic deployment catalog/variants 为源，做轻量包装，不再维护第二份手工模型 SHA。所有指向相对路径的资源必须**在经过验证的版本根目录内**解析，拒绝路径穿越、UNC/网络共享和符号链接逃逸。清单不得将远端 mutable main 当作 stable 的不可变身份。

### 5.3 兼容握手

- Launcher 读取 manifest 先做 Schema/签名/版本检查，再运行设备探测。
- 相同 protocol major 且 feature set 可识别：继续生成 plan。
- 新增未知可选字段：可忽略，但未知的**必需能力/操作**必须拒绝执行。
- manifest 要求的 min_launcher_version 更高：提示先升级 Launcher；升级失败保持旧版运行/诊断可用。
- protocol major 不支持：标记 PROTOCOL_UNSUPPORTED，不执行未知安装步骤。
- 本地 Studio 旧版不提供该清单：采用专门的 legacy adaptor、只读扫描与受控迁移；**不得**伪造新旧固定版本兼容证明。
- 稳定发布的下载物必须具备强制的文件长度、SHA256 和独立可信的发布身份/签名校验；TLS + 同地址的 SHA256 只证明传输一致，不能替代来源真实性。首版可通过固定受信公钥签名 manifest 或维护经核实的 publisher/pinned release 信任链；未实现签名验证时不得声称“安全自动更新”已闭环。
- Launcher 自身更新不能依赖刚升级的 Studio manifest；其发布身份由 Launcher 预置的可信密钥/信任锚负责。

## 6. GitHub Clone 与 Release：真实 EXE 的分发合同

**用户需求是双击独立 EXE，不能要求 Clone 用户先装 Rust/编译源码。**

V1 强制提供两个等效的分发入口：
1. Clone / GitHub Source ZIP：项目根目录有已构建、与 Launcher 源码版本相匹配、在 Windows 10/11 clean VM 检验的 ShineLauncher.exe（或者与本规范同等可双击的原生路径）；不得只提供源码、仅提供 .cmd 或要求用户先从另一个网页下载 GUI。
2. Studio 发布 ZIP：包含同一稳定 Launcher 二进制及 Studio 的发行 manifest/代码，双击行为一致；Studio 每次发布**复用**既有稳定 Launcher 版本，不要求重新编译 Launcher。

**二进制进入 Git 的代价须管理**：首版可将经可复现构建、SHA-256/签名/发布说明验证的相对小体积稳定 EXE 提交至明确路径并复制/软逻辑映射到项目根；只在 Launcher 版本升级时更新，不随 Studio commits 重复提交；设置体积预算并避免无意义提交。不得采用“Git LFS 二进制但用户未安装 LFS 便无法启动”的路径；不得仅放指向 Release 的 .url 并冒充 Clone 即可打开。Windows EXE 的实际复制、压缩包清单与 Git 版本身份必须自动核对。

**构建/发布拆分**：
- launcher-build：独立 Windows 构建任务，锁定 Rust toolchain、Cargo.lock、Slint 与第三方许可证，生成 EXE、SHA256、SBOM/依赖清单、构建来源与简单可运行检查。
- launcher-release：仅 Launcher 标签/显式授权触发，保留上一个可用 EXE，单独发布 launcher-vX.Y.Z 及签名/校验资产。
- studio-release：继续从 Studio 已提交树打包，嵌入**已经发布的 Launcher 二进制**，更新 Studio 自身 build info 和部署清单，不触发 launcher-build。
- 尽量复用仓库已有 .github/workflows/ci.yml 的分层机制，额外 Windows 构建可以限定为触及 launcher/**、协议相关文件或手工触发，避免主线导演模式每次 push 都重编 Rust 或跑重型真实模型。
- 若 GitHub CI 额度不足，可由本地 Windows 开发机完成可追溯构建并提交验证证据；禁止声称云端 Linux 单元测试等同于 Windows clean VM 可运行验收。

**代码签名**：推荐后续为 Windows Authenticode 签名；首版无签名时必须告知 SmartScreen 风险，不指导用户关闭杀毒/安全防护来绕过错误。哈希/发行签名/公钥与代码签名属于不同层次，应分别记录。

## 7. Windows Launcher 功能布局与产品交互

Launcher 启动后无需等待 Studio 才能打开。主界面仅六个明确功能区，工程详情默认折叠：
1. 总览/一键启动：Launcher 版本、已选 Studio 安装、Studio 当前/最新稳定版、最近诊断状态、一个“启动工作台”主操作。
2. 本机环境检测：Windows/GPU/显存/内存/空间/工具版本/ComfyUI/节点/权重/工作流；显示已发现、已验证、未知、需修复等区分。
3. 安装目录与已有旧版：新装目录选择、自动发现、用户点选旧目录、用户项目/数据库与缓存归属，明确多实例。
4. 版本更新：Studio 更新通道/差异/下载/回滚，Launcher 独立更新栏，上游 Node/Python/ComfyUI 更新资讯与当前兼容版本分别展示。
5. AI 环境部署：所选能力包、缺失资源、下载体积/峰值空间、自动安装/修复、受管实例/工作流实际验证进度。
6. 运行与诊断：API/Worker/Web/ComfyUI 的准确 PID/port/health、停止/重启、受管服务 GPU 释放状态、安装日志和诊断包（不含密钥/素材）。

所有重要状态由单一持久化状态源生成，不能让 Launcher 显示“已成功升级”而实际 Studio 仍指向旧版/Mock。用户选择旧版目录后自动探测，展示可复用资源及升级影响，一次必要确认后自动执行**可确定、经过授权和安全验证**的后续操作。多个旧版/不明进程/许可不符则仅需最少必要用户确认。

界面参考语言应面向普通影视用户：
- 可用：准备好了，可以打开工作台。
- 待修复：缺少 1 项节点依赖，点击修复。
- 更新：有新的稳定版，预计下载多少、保留旧版与作品。
- 阻断：正在制作第 N 集，必须等待安全停机后升级。
- 未验证：模型文件已就位，但尚未进行真实生成验证。
不要用 pip、venv、SHA、JSON、job object 作为首页主要文字，这些放入技术详情/诊断。

## 8. 启动器原生职责与复用边界

Launcher **必须自己实现或通过系统 API 实现**：
- 配置加载、稳定安装 ID 和目录注册表、旧版安装只读发现；
- Windows OS/GPU/存储/工具的 bootstrap 最小探测（在 Python/Node 缺失时可运行）；
- 远端可信版本发现（HTTPS、限流、缓存、代理、超时、证书检查）和离线只读操作；
- 下载/校验 Studio/Launcher bootstrap 包、进度通知、临时文件隔离、分段恢复与哈希校验；
- 受控子进程运行（禁用 shell 注入，参数数组、固定 exe 路径、受信任脚本）、所有权记录与超时；
- 元数据持久化 journal/双实例互斥、升级阶段恢复；
- Launcher 自更新的辅助进程/启动器版本切换及失败回退，不通过覆盖运行中的 exe 实现；
- Web/API/Worker 尚未启动时的故障修复/恢复；
- 管理多套 Studio 安装的稳定身份，不能误认服务归属。

Launcher **优先复用现有子系统**：
- PowerShell 5.1 scripts/bootstrap.ps1 的工具下载、版本 pin、已有安装环境桥；
- scripts/start_shine.ps1/stop_shine.py 和进程身份核对，而不是随意重新实现 Python 服务所有权；
- ComfyUI 的 already approved download/plan/instance/adoption/verification 机制；
- 现有 SHA 固定的 catalogs、variant qualification、managed recipe 和工作流校验；
- 既有下载续传、安装尝试回执和服务验证，状态通过稳定机器可读桥接；对于 API 未就绪的阶段由原生 bootstrap 处理。

**桥接规则**：
- 提供唯一 versioned launcher-bridge 操作协议（JSON request+response，UTF-8，稳定 error code、阶段和进度）；在 Studio 未启动前，基于原生能力或 PowerShell 5.1 能力完成，待 API/Worker 健康后复用现有部署 API。
- 不让 GUI 直接解析 PowerShell 标准输出中的中文提示字符串来推断成功。
- 一条有写入效果的子计划只能由一个执行者持有，必须持久记录 executor type、installation_id、plan hash 和 receipt。
- 对 Studio 主程序版本升级/数据库迁移执行跨版本管理，不能让旧的 start_shine 自动 Alembic 在“仅预检/安装依赖”时提前迁移生产库。
- App 未运行也必须提供完整日志/错误状态、诊断结果与恢复入口。
- GUI 可以保持后台安装窗口隐藏，但必须保留可查看的命令结果/日志，不得静默吞错误。

## 9. 本机稳定安装身份、旧版目录接管和多实例

建议注册目录为 %LOCALAPPDATA%\Shine\Launcher\installations.json（是否使用 Roaming 和最终路径须结合现有数据目录实测）；全局注册包含稳定 installation_id、studio_path、data_dir、db_identity、secrets_ref、runtime_roots、verified_model_cache、channel、current version/commit/manifest SHA、last healthy release、recovery receipt。

Launcher 自己的 binary/设置放在独立 %LOCALAPPDATA%\Shine\Launcher 或明确的 launcher/ 安装位置；Studio/Runtime/Models/Data/Cache 用独立逻辑分区，但**旧用户不强制迁移目录**，用映射方式接管。

Legacy 用户流程：
1. 先读安装注册表；再有限扫描原有 .env、SHINE_DATA_DIR、backend/.venv、build_info、SQLite、旧安装收据及目录结构。
2. 用户可直接用“选择旧版目录”指定旧源代码/发行包/ComfyUI 或 Studio 根路径，Launcher 只读识别并展示证据。
3. 多个候选时需要明确用户选择；不按目录名字或最新修改时间擅自覆盖。
4. 绑定已有数据、模型缓存和运行时后，生成升级计划；升级前创建旧库一致性备份与配置回执；旧 Launcher/启动脚本仍可作为后备。
5. 仅新版激活通过后更新安装注册表/快捷方式，旧版至少保留一个恢复点。

**不允许**把新版 Clone 到新文件夹后，自动创建一个新的空数据库并把原始旧版作品隐藏掉。不能修改其他应用的 ComfyUI；严格收编必须已有代码的完整签名/节点/适配器约束通过，不能因为路径发现就强行 adopt。Node/Python 已存在但不匹配时使用独立环境，不在系统全局重装。

所有路径必须 UTF-16/Unicode 安全、long path/空格/中文正确处理；阻断 UNC、越界符号链接、同名不同内容和非本安装所有权目录的写入。

## 10. 安装、升级、回滚与自更新的两个隔离状态机

### 10.1 Studio/依赖升级

DISCOVER → PLAN → CONSENT → PRE_DOWNLOAD → VERIFY → QUIESCE_OWNED_PROCESSES → BACKUP → STAGE → APPLY_DEPENDENCIES → MIGRATE_DB → VALIDATE_CANDIDATE → SWITCH_STUDIO → HEALTH_CHECK → COMPLETE。

失败分支：PAUSED/RETRYABLE、NEEDS_REVIEW、ROLLBACK_PENDING、ROLLED_BACK、BLOCKED。状态保存在独立 journal，不能只存在待迁移的 Studio DB。由稳定 launcher/upgrader 保持跨重启可读；计划绑定用户批准的依赖、目标软件、磁盘预算、许可证和 hash。多进程/多个 Launcher/旧脚本不能并发触碰同一个安装。

Studio 更新：
- stable 一律拿不可变 release artifacts，先下载校验到独立 versions/<id>/ 目录；
- 开发模式默认绝不在 dirty source checkout 运行 git reset --hard、git pull、强制 checkout。提供“保留本地改动/另建版本目录/拒绝更新”选项；
- .env、secrets、用户资料与真正的 data_dir 独立保存，绝不在阶段切换时被复制回假生产库；
- SQLite 迁移前待任务结束，使用 sqlite backup / WAL 一致性快照验证，迁移失败恢复**仅能在目标版未接受新业务写入的情况下自动回滚**；如果新版本已经产生写入，则保存双边证据并进入 RECONCILE_REQUIRED；
- 运行时模型复用需要 SHA256/当前实例消费路径证据；版本组合不兼容时新建隔离 Python/ComfyUI，不改外部原目录；
- 进程拥有权必须复核 PID/birth/必要 Job Object/端口三证，不把 PID 重用或 UNKNOWN 当作已经停止；
- 执行计划磁盘峰值按模型、缓存、Python 虚拟环境、旧版保留、DB 和实例快照动态预算；不能随意降低现有 180GiB 参考安全阈值。

### 10.2 Launcher 自更新

LAUNCHER_CHECK → FETCH_SIGNED_METADATA → VERIFY → STAGE_NEW_EXE → PREPARE_HELPER → EXIT_OLD_PROCESS → SWITCH_LAUNCHER → FIRST_BOOT_HEALTH → COMPLETE 或 ROLLBACK。

细则：
- Studio 不主动要求每个 commit 都更新 Launcher；仅当 deployment manifest 的 min_launcher_version 不兼容时提示更新。
- 主 EXE 运行中不得自覆盖；使用单独受信任 updater helper，必要时由 Windows 原生恢复机制回滚。
- Launcher 更新只触碰 Launcher binary/其私有配置，不访问 Studio 的生产 SQLite、模型/资产/作品。
- 自更新失败时旧 Launcher 仍可独立打开/检查环境并启动已有 Studio。
- 独立稳定版更新源只识别 launcher 发布通道及签名，不能误下载 Studio ZIP 当成自身更新。
- 当前程序不可更新/用户无网络/签名不符时保留旧版本和准确阻断状态。

## 11. AI 能力包与 ComfyUI 完整性

工作台的 capability bundle 属于 Studio 版 manifest，不属于 Launcher 硬编码；Launcher 只理解通用 install/verify/bind/activate 操作。

每个能力闭包包括：模型权重及确切散列、ComfyUI 来源/修订、node package revisions、依赖 wheel/来源及 SHA、兼容 Python/Torch/显卡要求、模型目录绑定、工作流 graph+parameter_map+media_transfers+required_nodes、服务/模型真实验证合同。

正式 H3 的已支持组合遵守 config/deployment/variants/h3-ref2va-int8-convrot.json 与 profile_gate；候选 W6A8/NVFP4 不自动启用或下载未固定资源。H3 独立 latent upscale 尚不具备全套上游 pin/节点/实机证据，不得显示“已完整安装”。

Qwen Image 2.1 当前 config/deployment/qwen-image-2.1-windows-x64.json 主要声明三项模型权重，具体文生图/编辑等工作流能力应以 backend/shine/materialprep/qwen_workflow.py 及当前受支持的 node/runtime 合同逐项核查。没有闭环的编辑/多图能力应明确 DEFERRED，不能使用“全部能力已安装”标签。

ComfyUI 现有目录分三路处理：
- 直接复用（STRICT_ADOPT）：源码/节点/适配器/模型身份全符合，并已获用户授权；
- 模型复用、运行时隔离（REUSE_MODELS_ISOLATE_RUNTIME）：模型精确哈希可复用，外部 ComfyUI 版本或节点不符；
- 全新受管安装（FRESH_INSTALL）：没有可复用资源。

用户授权的 extra_model_paths 只读有界扫描，防符号链接/网络路径/目录越界。下载引擎优先复用现有 .part 续传/固定源/文件哈希；Node、ComfyUI、Python/PyTorch 自身的“有新版”只作为资讯，若 Studio 尚未批准固定兼容组合不能直接更新。

分层显示：DISCOVERED / FILES_VERIFIED / ENV_COMPATIBLE / WORKFLOW_CONTRACT_READY / MANAGED_RUNTIME_READY / REAL_GENERATION_VERIFIED。未做真实模型加载/出图或视频推理时，不能因为进程/HTTP/object_info 通过就自动提升到真实出片 PASS。

## 12. 一键运行与停止的操作合同

启动前：
- 读取选中 Installation ID 与 active Studio 版本；
- 只读判断该版本的 Python/Node/数据库迁移/运行配置是否已经处于可启动状态；
- 识别本安装自己的 API/Worker/Web/受管 ComfyUI：PID、birth、必要命名对象、端口和健康响应一致；
- 已运行且 Web 200 + API/Worker 一致 → 打开当前正确 Web 地址，不重复开启新服务；
- 半启动 → 按既有 ownership 规则停止/归档后重启；端口被他人占用 → 不杀对方进程，自动换端口，浏览器打开真实监听 URL；
- 修复失败时保留现场和日志；未知 PID/锁/GPU lease 不得盲删。

停止时：
- 停止本 Installation ID 明确拥有的 Worker/API/Web/ComfyUI；拒绝停止无所有权进程；
- 正在生产、未知外部请求、待核对 GPU lease 时保留阻断和提示，不得强行结束；
- 服务端进程未停干净不能声称“已释放显存”，且不能自动拉第二个 ComfyUI。

**启动器退出不等于强制终止生产进程。** 后台运行语义需明确：普通退出 Launcher 可让受管工作台保持运行；用户显式选择“停止全部受管服务”才请求安全关停。Windows 退出/重新登录/睡眠恢复后的状态再探测必须准确。

## 13. 工程安全和升级供应链

- 发布包来源固定为仓库所有者明确授权的 GitHub Release/可信版本清单，TLS、发布签名/哈希、解压路径安全和资源白名单不可省略；SHA256 不能替代签名本身。
- 拒绝从 manifest 执行任意未知命令、拉取任意远端脚本、把网络响应拼接进 PowerShell -Command、修改系统级 PATH/Python/CUDA 或调用未验证安装器。
- 原生子进程调用采用固定 argv、无 shell=True、stdin 非交互/独立日志、执行身份和权限范围受控；调用 ps1 使用可信的已校验本地脚本文件和参数映射，PowerShell 5.1 编码规范仍保持。
- 下载哈希/来源/许可/目标版本不一致一律 BLOCKED，缓存也要重新校验可信身份；恶意 ZIP traversal、zip bomb、NTFS junction escape、同名大小写冲突均纳入测试。
- Secrets 不存于 GUI state/日志/注册表明文；导出诊断不包含密钥、用户源素材、作品内容或模型被禁止分发的数据。
- 未获得明确同意，不把本机文件、用户作品、模型或设备身份上传第三方；联网更新仅发送必要的普通版本请求。
- 遇到新的运行时或依赖组合，先检测资源和兼容性，禁止盲目通过 pip/npm 全局更新解决。
- 打包二进制要提供 SPDX/SBOM/第三方许可清单及 Slint attribution，并检查 H3/ComfyUI 上游许可复用边界。

## 14. Git worktree 隔离施工与跨分支冲突策略（必须严格执行）

本方案分支基于 main@8e91f7ed，不锁定未来 main；云端 Agent 必须在**独立 Git worktree**施工，不能直接在本机用户开发工作区、main 或其他 Agent worktree 修改文件。

建议命令，仅在确认当前仓库目录/分支状态及 worktree 不冲突后使用：

    git fetch origin
    git worktree list
    git worktree add -b feat/shine-launcher-impl-20261113 ../shine-launcher-agent origin/feat/shine-launcher-v1-20261113
    cd ../shine-launcher-agent
    git status --short
    git branch --show-current
    git merge-base HEAD origin/main

如果实现分支或 worktree 已存在，沿用正确的独立工作区或使用新的唯一命名，**不得强行覆盖工作树或重建他人 worktree**。

隔离要求：
- Cargo target、node_modules、Python venv、临时测试 SQLite、Windows 打包输出和缓存分别位于本 worktree 的私有目录或独立缓存根；
- 不启动/关闭主线开发机的 Studio、ComfyUI、Worker/port/GPU 租约；模拟和集成测试必须指向隔离 data_dir 和独立端口；
- 没有显式真实硬件测试授权不得访问、停服或迁移用户真实数据库/作品/模型目录；
- 优先只改 launcher/**、config/launcher/**、packaging/launcher/**、文档/新测试。对于 scripts/install_once.ps1、bootstrap.ps1、start_shine.ps1、build_test_package.ps1、AGENTS.md、CI 和入口 cmd，必须先和**最新 main** 逐文件比对，薄适配、不大规模重写；
- 当前正在进行的导演模式、素材中心、LLM 及文学流水线开发不属于本分支施工范围。不要改 worker.py/preproduction.py/质检规则以绕过验证。
- 在创建 PR 之前 git fetch origin，比对 origin/main；需要与主线同步时优先用独立 worktree 内安全 rebase/merge（先提交且工作树干净），遇到冲突逐项处理，不使用 reset --hard、force push 或覆盖 main。
- 任何启动入口/数据库/数据位置更改，同时通过既有 AGENTS.md 两条入口强制测试；不能因为 V1 新 EXE 设计就删除可恢复的脚本路径。

## 15. 分阶段开发计划（可并行测试，不可并行破坏版本身份）

### L0 — 原生技术 PoC 与许可证检查（P0）

交付：Windows 可启动的真正 GUI EXE（不使用 Python/Node/ComfyUI/WebView2）、中文渲染、No GPU 软件渲染、文件选取、独立日志；Rust/Slint 锁版本与许可证归属 ADR。

验收：Windows 10/11 clean VM 双击；空/离线/无 NVIDIA 驱动也可打开；CPU 不阻塞 UI。若缺 Windows，标 PENDING_WINDOWS，不冒充 PASS。

### L1 — 独立安装身份与环境检查（P0）

交付：只读 Host probe、工具清单/版本读取、ComfyUI、旧 Studio/数据路径发现、稳定 installation registry、目录选择、多安装实例保护；兼容桥接。

验收：新机、旧 Clone、旧 ZIP、指定旧路径、无依赖、多 ComfyUI、中文盘符、网络共享拒绝；所有发现无写入。

### L2 — Studio/Launcher 双版本与协议（P0）

交付：Deployment Protocol v1 Schema + validator、Studio Stable/Beta/Dev 通道、Launcher 自身独立发布/自更新、最小协议兼容、可信 manifest、GitHub 更新缓存与代理。

验收：Studio commit 变化不改变 Launcher EXE SHA；Launcher 升级不触碰 Studio/用户数据；未知必需字段/protocol 不执行；无网可启动旧版；发布来源伪造/签名错误被拒绝。

### L3 — 安装、升级、恢复与一键运行（P0）

交付：协议到现有 bootstrap/download/installation/instances/profiles 的受控桥；Studio 安装/原位升级/修复/旧库备份和恢复；Launcher 自更新 helper；进程/运行检测、一键启动。

验收：不同路径新旧升级、并发锁、磁盘耗尽/下载失败、迁移失败和可恢复性、半启动和端口冲突；不得丢失旧用户数据、密码、主资产、已冻结版本；Mock 不冒充真实能力。

### L4 — 能力包闭环与正式发行（P0）

交付：模型—节点—依赖—工作流的统一能力视图；H3 默认组合完整映射；Qwen 子能力独立验证；Git Clone 根目录预构建 EXE；Studio Release 包复用稳定 Launcher；Launcher 独立 Release assets/校验/签名。

验收：Clone/ZIP 启动器一致、版本可追溯、二进制 SHA 固定、用户无需安装开发工具、未验证能力明确 UNKNOWN/PENDING；无过度下载或节点路径错位。

### L5 — 全量回归与真实验收（Merge Gate）

交付：所有自动化测试、恶意输入/升级恢复故障注入、Windows clean VM/旧版升级真实证据、发行包完整性、主线冲突复核、验收报告及 PR 说明。

必要：只在通过标准所要求的 Windows 真机/虚拟机验证且关键 P0 无未关闭缺陷时，才将 PR 标为 Ready to merge；若云端无真实 Windows，保留 Draft PR/PENDING_LOCAL_ACCEPTANCE，禁止宣称全 PASS 或自动合入 main。

## 16. 测试与准入矩阵（要求可追踪到自动化测试）

| 编号 | 场景 | 通过条件 |
| --- | --- | --- |
| LA01 | 没有 Python/Node/ComfyUI/网络/可用 NVIDIA 驱动 | Launcher.exe 真正打开，准确展示状态，无崩溃 |
| LA02 | 中文路径、空格、Win 10/11、DPI/字体 | 简体中文显示正常，目录选择/读写可用，无编码错误 |
| LA03 | Git clone / GitHub source ZIP / Studio Release ZIP | 根目录同一 EXE 可双击，用户无需先装 Rust 或编译 |
| LA04 | Studio 单独更新多次 | Launcher EXE SHA 不变；无需重新发行 Launcher |
| LA05 | Launcher 自更新 | Studio 版本、DB、模型/素材 SHA 不变；失败恢复旧 Launcher |
| LA06 | Stable/Beta/Dev 三通道 | 仅选择通道更新，开发 dirty tree 不被强制重置 |
| LA07 | 上游 Node/Python/ComfyUI 有新版但不兼容 | 只显示通知，不安装、不改变受管运行时 |
| LA08 | 协议过高/签名错/未知操作/过期清单 | 明确拒绝，不执行任何未知命令 |
| LA09 | 旧版 Studio 另一个目录/多候选 | 选择后仍使用原 DB 与数据，绝无空库冒充升级 |
| LA10 | 旧版升级中断/数据库迁移失败 | 可恢复或进入安全核对态，旧项目/媒体/密钥不丢 |
| LA11 | 新版开始写数据后想回退 | 拒绝用旧快照盲覆盖，进入 RECONCILE_REQUIRED |
| LA12 | 已有不兼容 ComfyUI，但已验证模型权重 | 保留外部目录、隔离重建、复用正确模型 SHA |
| LA13 | 权重齐全但缺节点/节点依赖 | 只补必要组件，live object_info/工作流校验通过 |
| LA14 | H3 deferred upscale / 未验证低显存候选 | 不会被默认下载/开启，不冒充正式 READY |
| LA15 | Qwen 图像子能力未完整闭环 | 分项展示已支持/待验证，不错误承诺图像编辑全功能 |
| LA16 | 下载断点/代理失效/磁盘不足 | 已验证部分复用，精确错误码，安全可恢复 |
| LA17 | 双 Launcher 并发/旧安装脚本并发 | 同一安装强互斥，不重复迁移、覆盖或创建实例 |
| LA18 | API 端口被他人占用、半启动、PID 重用 | 不盲杀、自动换端口、浏览器打开真实 Web 地址 |
| LA19 | 真实活动 H3 Job/未知 GPU lease | 安装/升级被阻断或等待，不能中断生产/释放未知租约 |
| LA20 | 错误 release 资产/篡改二进制/ZIP traversal | 校验失败，拒绝执行/解压，保留诊断 |
| LA21 | 全新 Windows 真机完整 H3 部署 | 文件/服务/工作流/真实 GPU 生成分层证明，不混淆 Mock |
| LA22 | 本机已有视频工作台数据和资产 | 项目数量、DB/媒体 SHA、LLM 配置引用保持一致 |
| LA23 | Launcher 仅更新自身 | 无数据库、模型库、ComfyUI/Studio 改写 |
| LA24 | 对 main 同期新增导演功能做兼容 | 工作树无覆盖，前后测试与 diff 证明业务功能不回退 |

**自动化三层**：
- 快速：Cargo fmt/clippy/test、Schema/协议兼容性、Rust 状态机/解析器/目录安全、Python/PowerShell 相关定向单测；每次提交。
- 集成：独立数据目录/端口的可重现 Windows/跨平台模拟、假 GitHub 元数据/恶意包/可恢复升级、UI 状态/执行桥、现有 Python integration / TypeScript build；阶段收尾。
- 真实：clean Windows + MSI/VC Runtime 独立性 + 双击 EXE + 旧版数据迁移 + ComfyUI 受管启动 + 被选模型真实推理；可使用本地开发机并记录硬件、日志、commit/manifest/hash，云端无设备不能假造。

仓库最新 AGENTS.md 已采用后端完整测试**8 分片并行**及相应错误检查规范；Agent 以实时 AGENTS.md 为准，不能使用过期测试计数或仅单一 pytest 输出代替全量回归。

## 17. PR、最终验收与合并主线的强制流程

1. **设计分支只承载本 MD**。Agent 不要直接在 feat/shine-launcher-v1-20261113 或 main 上大范围施工；使用独立实现工作分支+worktree。
2. 每阶段建立有意义的本地提交及验收记录（Rust/协议/入口/升级/模型/测试）；绝不把用户素材、数据库、API Key、权重、真实输出或私密日志提交 Git。
3. 开发期间推送实现分支是本次明确授权范围；创建 Draft PR 到 main，附文档链接、最新 main 基线、源码变更、Launcher EXE 版本/哈希、兼容性、测试矩阵与未验证项。不得直接推 main、不得 force push。
4. 与主线同步时对当前 HEAD 重新读取 AGENTS.md 和可能变化的包装/启动/CI 文件；只在独立 worktree 解决冲突。PR 需展示不包含无关导演/文学/素材业务重构。
5. 完成 L0-L5 的适用项目和 PR 检查，**独立 Windows 真机首启、Cloned EXE、旧版升级保数据、自更新回滚等 P0 必测项不可缺**。没有这些实测，PR 可以存在但应保持 Draft，不宣称最终 PASS。
6. 最终验收报告以真实证据区分：SOURCE_IMPLEMENTED、STATIC_PASS、INTEGRATION_PASS、WINDOWS_GUI_PASS、STUDIO_UPGRADE_PASS、REAL_RUNTIME_PASS、REAL_GENERATION_PASS、PENDING / BLOCKED；更新测试规范、安装说明和已知边界。
7. 仅在 PR 所有强制检查通过、关键功能无阻断、实现与最新 main 无冲突、所需真机证据完成、仓库 ruleset/权限允许时通过 PR 合入 main。若不具备 merge 权限或缺少最终验收材料，**不可绕过保护规则**；报告给仓库所有者完成最终批准/合并。禁止通过直接 push main 代替 PR。
8. 合并后 main 仍应保持 Studio 原有命令行恢复链兼容；正式用户唯一推荐入口为 Launcher EXE；独立 Launcher 后续按自身节奏发布而不受 Studio 普通 commit 触发。

## 18. 云端 Agent 开工/收尾强制输出

**开工报告必须有**：
- origin/main 真实 HEAD、方案分支 HEAD、worktree 实际路径、实现分支名、merge-base；
- 已阅读实时 AGENTS.md 及方案，列出现存启动/安装/部署能力的复用清单；
- Rust+Slint 中文+CPU 渲染 PoC 选型风险，依赖许可、更新信任根；
- 预计修改文件列表、共享文件冲突点、可安全隔离的开发/测试环境；
- 本次真实 Windows/GPU 测试是否可用及明确限制。

**收尾报告必须有**：
- 关键模块和阶段完成表，所有改动的文件及 commit SHA；
- Launcher 编译版本、EXE SHA、来源/发布方式、独立更新链；
- Deployment Protocol v1 Schema/兼容性及现有 catalog 适配；
- 旧版本接管与数据保护/恢复对账；
- 每条关键测试命令、输出、PASS/FAIL 和未执行原因；
- Windows 真机/虚拟机实际测试报告（不可用则 PENDING，不得伪造）；
- PR URL、基线/冲突状态、是否满足 merge gate；未满足则明确 Draft/阻断；
- 若已按授权完成 PR merge，报告合并 commit 及 main 最新 SHA；若因权限/证据不可用无法合并，明确后续必要条件。

## 最终产品验收定义

**Launcher 实际可用**：用户拿到 GitHub Clone/ZIP 直接双击 ShineLauncher.exe，在没有 Python/Node/ComfyUI 的机器也能打开，自动检测和识别旧版；选择安装目录后可通过一个 GUI 安装/修复符合 Studio 能力清单的依赖；Launcher 可安全管理 Studio 和上游版本信息、启动/停止/升级/恢复；Studio 普通功能更新不改变 Launcher binary；启动器自身可独立更新、失败不破坏原工作台；所有模型/节点/工作流能力以实证分层展示。

**不能通过**：仅新增 rust 源码而没有真正可双击的 EXE、EXE 仍依赖 Node/Python、只包装旧 CMD/PS 页面、没有旧版识别/备份回退、没有两个独立版本通道、缺乏签名/来源校验的自动下载、声称了未经真实运行验证的模型/节点/工作流、没有 Windows clean VM 测试却宣布最终 PASS，或直接推送 main 绕过 PR。

---

官方技术参考：
- Slint Winit 渲染后端：https://docs.slint.dev/latest/docs/slint/guide/backends-and-renderers/backend_winit/
- Slint 渲染限制：https://docs.slint.dev/latest/docs/slint/guide/backends-and-renderers/backends_and_renderers/
- Slint 许可：https://github.com/slint-ui/slint/blob/master/LICENSE.md
- 仓库当前发行规范：AGENTS.md、scripts/build_test_package.ps1、packaging/windows/START_HERE.md
- 仓库现有 ComfyUI 资源核验：backend/shine/comfyops/ 与 config/deployment/
