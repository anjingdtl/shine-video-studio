# Shine Launcher V1.0 工程阶段验收报告

报告日期：2026-11-13 | 实现分支：`feat/shine-launcher-impl-20261113` | 独立 worktree：`/workspace/shine-launcher-agent`

方案提交：`9dfa76b6e9fbef4751ce42bc8fc50503ef51df0a` | 开发基线：`origin/main` / `8e91f7edfe0c1c2a820f7063dbca448145c70a21`

总体验收：**部分完成；不得合并 main**

## 验收结论

已在独立 Rust + Slint 工程中交付可编译的原生 Launcher 原型、中文界面源代码、CPU 软件渲染配置、本机只读检查、旧版目录只读识别与本机安装登记，以及 Deployment Protocol V1 的 schema、解析和兼容性校验基础。Launcher 的读取、校验和 smoke-check 路径不依赖 Studio 的 Python、Node、API、Worker、ComfyUI 或 GPU。

Linux 上 Rust、前端、后端分片、静态检查和隔离数据库迁移检查均通过。当前环境不能执行真实 Windows GUI/字体/DPI/中文路径验收，也没有提供 GPU，因此不能据此确认独立 EXE 能在 Windows 新机启动、中文显示正常、或 AI 能力可用。Windows 工作流只能在推送后由 GitHub Actions 实际运行；仓库当前也尚无已验证 EXE。按方案的验收门槛，本分支不得合入 main。

## 阶段状态

| 阶段 | 状态 | 已交付内容 | 阻断项 |
| --- | --- | --- | --- |
| L0 原生 EXE 技术验证 | `SOURCE_IMPLEMENTED / PENDING_GUI_ACCEPTANCE` | Rust/Slint GUI、中文界面源码、software renderer、后台只读环境探测。Windows Server 2025 workflow 通过格式、源码编译、22 项单测、Clippy、Release EXE、宿主 smoke-check 与身份/hash 校验。 | CI 未启动可视窗口；Windows 10/11 真机 GUI、中文字体/输入、DPI、中文路径和无 GPU/无 Python/Node 交互验收仍待本地执行。 |
| L1 安装登记与旧版识别 | `PARTIAL` | 显式选择路径后的只读目录扫描、数据库/数据目录证据、路径保护、本机原子写入的 per-user 安装登记。 | 新安装目录流程、ComfyUI/模型/节点全量识别、多实例 UI 和 Windows 只读性/中文路径验收未完成。 |
| L2 通道与协议 | `PARTIAL / FAIL_CLOSED` | Deployment Protocol V1 schema、兼容解析、规范化哈希身份、范围限定的 Ed25519 校验、Stable/Beta/Development 与 Launcher 通道元数据扫描、缓存及不可变开发引用解析。 | 可信发布者密钥库为空；没有资源下载器、安装器或 Launcher 自更新/回滚。远端安装/更新明确禁用。仓库没有匹配的现行 release tags。 |
| L3 安装、升级与恢复 | `NOT_STARTED` | 无。 | 安装事务、耐久 journal、备份/回滚、进程身份核验/生命周期管理、诊断和修复均未实现。 |
| L4 能力包与分发 | `PARTIAL` | Windows workflow 在 Windows runner 上构建并验证 ZIP 解包；已配置 push-only publisher，在实现分支 push 后将通过校验的 EXE、SHA-256 和 source commit 元数据提交到仓库根目录。 | Publisher 自动提交还需由下一次 branch push 实际验证；H3/Qwen 清单、ComfyUI/节点/运行时部署、真实模型验证与独立 Launcher 发行均未完成。 |
| L5 最终回归 | `NOT_STARTED` | Linux 全仓库回归和一次完整 Windows x64 构建、单测、Clippy、EXE smoke/hash 与 ZIP 自检已通过。 | Windows 新机 GUI、旧版保数据升级/回滚、Clone 根目录自动产物提交、Windows 10/11、GPU/模型验收仍待执行。 |

## 已执行的检查

所有构建、缓存、测试数据库和临时文件均留在独立 worktree 的忽略目录 `work/` 或 `launcher/target/` 中。

| 检查 | 结果 |
| --- | --- |
| `cargo fmt --all -- --check` | 通过 |
| `cargo check --locked --all-targets` | 通过 |
| `cargo test --locked --lib` | 24 项通过 |
| `cargo clippy --locked --all-targets -- -D warnings` | 通过 |
| `cargo check --locked --target x86_64-pc-windows-msvc --all-targets` | 通过；这是 Linux 上的交叉目标类型检查，不是 Windows EXE 构建或 GUI 验收 |
| `cargo clippy --locked --target x86_64-pc-windows-msvc --all-targets -- -D warnings` | 通过；Linux 上启用 Windows cfg 的交叉目标静态检查，不替代 Windows runner |
| GitHub Actions Windows workflow | [run 43420629491](https://github.com/anjingdtl/shine-video-studio/actions/runs/43420629491) 通过：Windows 22 项单测、Clippy、Release 编译、依赖独立 smoke-check、EXE/build identity/hash 校验、ZIP 打包和解压后复核均成功 |
| `cargo build --locked --release`（Linux） | 通过；生成 Linux ELF，不是 Windows 交付物 |
| `cargo run --locked --release -- --smoke-check` | 通过；只读检查宿主信息，不创建 GUI 窗口 |
| 后端完整回归 | 按 `AGENTS.md` 八分片规范执行，204 个测试文件均已分配，8/8 分片退出码为 0；收集 2,485 个测试项。Windows-only 进程/端口测试在 Linux 跳过，Windows 真实执行仍待验收 |
| `CODEBUDDY_SAFE_DELETE_ENABLED=0 work/backend-venv/bin/python -m ruff check backend` | 通过 |
| Alembic 隔离数据库 | 升级到 `f180c0020001`；`alembic check` 报告无新增迁移操作 |
| 前端 `npx vitest run --no-file-parallelism` | 42 个文件、273 项通过；有现存 React `act(...)` 测试警告 |
| 前端 `npm run build` | TypeScript 与 Vite 构建通过；有现存大 bundle 提示 |
| `git diff --check` | 通过 |

Windows workflow 前六次执行陆续修复了扩展盘符、重解析点校验、规范化路径断言、Windows-only Clippy 与 GUI 子系统 smoke 退出码问题。第七次 [Windows workflow run 43420629491](https://github.com/anjingdtl/shine-video-studio/actions/runs/43420629491) 全部通过：22 项 Windows 单测、Clippy、Release 编译、smoke-check、EXE hash/build identity 和 ZIP 解包复核。artifact source commit 为 `4d9514180a537dde89df3125901b787d83784746`；EXE SHA-256 为 `54a09b62df64fcc7ddb7afbd2a7e2046893d3c08d850d7bef6203e40ef683dba`；发布 ZIP SHA-256 为 `1e58ddae5e7723413413d33a5c6dd2445777dcef177c1d3fdb65881dc83fdf75`；GitHub Actions artifact 的 SHA-256 为 `6a9aa1c1dd9c8f27f97b93bfc99ce0c0d0b9ea533d7fd6986a10e2b878106eab`。构建身份明确保留 `windows_gui_acceptance=PENDING_LOCAL_ACCEPTANCE`；CI 没有启动实际 GUI 窗口。下一次 push-only workflow 将提交经验证的 `ShineLauncher.exe`、hash 和构建信息到实现分支根目录；该发布步骤尚待执行。Linux 本地 Launcher 回归 24/24 通过。

后端 Linux 回归中发现若干测试实际依赖 Windows named mutex、Windows TCP 表或 Windows 管理进程启动。仅为这些既有测试补充 `windows_only` 维度和 `os.name != "nt"` 跳过条件，未修改业务实现、未放宽断言或安全标准。分片日志和隔离数据库位于本机忽略目录，不作为提交内容。

## 交付边界与风险

- 当前分支头尚未包含根目录 Windows EXE；受限 publisher job 会在 push build 全部通过后将真实 Windows artifact 提交到实现分支，不使用 Linux ELF 代替。CI 构建身份必须保留 `windows_gui_acceptance=PENDING_LOCAL_ACCEPTANCE`，smoke-check 不启动窗口。
- `config/launcher/trusted-publishers.json` 当前没有生产信任锚；协议签名验证失败关闭。TLS、同地址哈希或 GitHub 元数据扫描均不构成安全安装授权。
- Launcher 当前不会安装、覆盖、迁移或停止 Studio、ComfyUI、用户数据/数据库、模型、密钥配置或任何真实进程；它也不能提供完整修复能力。
- 未经真实模型推理验证的能力保持未验证，不能显示 READY。
- L0 Windows 真机验收、L3/L4/L5 实现和完整 P0 证据仍是发布及合并门槛。后续应在 PR 的 Windows workflow 成功后取回构建 artifact，核对来源、hash 和 ZIP，再由 Windows 10/11 环境执行人工 GUI、数据保留、升级回退及 GPU/模型验收。

## 当前 PR 决定

按用户给定的合并边界，当前只允许保持 Draft PR 供审阅和后续迭代；没有真实 Windows P0 验收证据时不得转为可合并状态，不得绕过 PR 修改或合入 main。
