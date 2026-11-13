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
| L0 原生 EXE 技术验证 | `SOURCE_IMPLEMENTED / PENDING_WINDOWS` | Rust/Slint GUI、中文界面源码、software renderer、后台只读环境探测、无环境依赖的 smoke-check、Windows 构建/校验脚本和 CI 工作流。 | 尚未运行 Windows 构建工作流；没有 Windows 10/11 真机 GUI 启动、中文字体/输入、DPI、中文目录及无 GPU/无 Python/Node 的实测证据；没有可追溯 Windows EXE 进入 Clone。 |
| L1 安装登记与旧版识别 | `PARTIAL` | 显式选择路径后的只读目录扫描、数据库/数据目录证据、路径保护、本机原子写入的 per-user 安装登记。 | 新安装目录流程、ComfyUI/模型/节点全量识别、多实例 UI 和 Windows 只读性/中文路径验收未完成。 |
| L2 通道与协议 | `PARTIAL / FAIL_CLOSED` | Deployment Protocol V1 schema、兼容解析、规范化哈希身份、范围限定的 Ed25519 校验、Stable/Beta/Development 与 Launcher 通道元数据扫描、缓存及不可变开发引用解析。 | 可信发布者密钥库为空；没有资源下载器、安装器或 Launcher 自更新/回滚。远端安装/更新明确禁用。仓库没有匹配的现行 release tags。 |
| L3 安装、升级与恢复 | `NOT_STARTED` | 无。 | 安装事务、耐久 journal、备份/回滚、进程身份核验/生命周期管理、诊断和修复均未实现。 |
| L4 能力包与分发 | `NOT_STARTED` | 协议配置结构和 Windows ZIP 打包/自校验脚本已建立。 | H3/Qwen 能力清单与 Studio 权威目录对接、ComfyUI/节点/运行时部署、真实模型验证、Git Clone 可直接双击的 EXE、Launcher 独立发行均未完成。ZIP 脚本尚无真实 Windows 执行证据。 |
| L5 最终回归 | `NOT_STARTED` | Linux 环境下的仓库回归已完成，见下节。 | Windows 新机启动、旧版保数据升级/回滚、Windows ZIP 和 Clone 一致性、Windows 专项测试及真实 GPU/模型验收均待执行。 |

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
| `cargo build --locked --release`（Linux） | 通过；生成 Linux ELF，不是 Windows 交付物 |
| `cargo run --locked --release -- --smoke-check` | 通过；只读检查宿主信息，不创建 GUI 窗口 |
| 后端完整回归 | 按 `AGENTS.md` 八分片规范执行，204 个测试文件均已分配，8/8 分片退出码为 0；收集 2,485 个测试项。Windows-only 进程/端口测试在 Linux 跳过，Windows 真实执行仍待验收 |
| `CODEBUDDY_SAFE_DELETE_ENABLED=0 work/backend-venv/bin/python -m ruff check backend` | 通过 |
| Alembic 隔离数据库 | 升级到 `f180c0020001`；`alembic check` 报告无新增迁移操作 |
| 前端 `npx vitest run --no-file-parallelism` | 42 个文件、273 项通过；有现存 React `act(...)` 测试警告 |
| 前端 `npm run build` | TypeScript 与 Vite 构建通过；有现存大 bundle 提示 |
| `git diff --check` | 通过 |

Windows workflow 前六次执行均完成 Windows x64 源码编译。第一次 Windows 单测 14/21 通过；Windows `canonicalize` 返回本机 `\\?\C:\...` 扩展盘符路径，被原先只接受普通盘符的白名单拒绝。修正后，第二次测试 15/22 通过；逐段重建的重解析点检查仍误判。现已对原始路径及各级父目录直接读取元数据，缺失路径仍继续检查父目录，并加上 UNC/Verbatim UNC/设备路径拒绝与符号链接祖先测试。第三次 Windows 测试 20/22 通过，余下 2 项仅是测试期望未规范化路径；测试现对照 `canonicalize` 结果。第四次 Windows 单测 22/22 通过，但 Windows Clippy 报告三个仅在 Windows cfg 下生效的末尾 `return`；这些分支现改为尾表达式，Linux 上 Windows MSVC 目标 Clippy 检查已通过。第五次 Windows 单测 22/22、Clippy 与 Release 编译通过，但 `$LASTEXITCODE` 无法可靠取得 GUI 子系统 EXE 的退出码。构建与校验脚本现用 `Start-Process -Wait -PassThru` 检查真实 `ExitCode`。第六次 Windows 单测、Clippy、Release 编译、smoke-check 与 EXE hash/build identity 校验全部通过；ZIP 打包发现许可证文件名和脚本路径不一致。许可证文件已按 `LICENSE_NOTICES.md` 的标准路径重命名；构建身份改为明确绑定 PR head commit，并在校验中要求完整 40 位 SHA。第七次 Windows workflow 待确认。Linux 本地 Launcher 回归 24/24 通过。

后端 Linux 回归中发现若干测试实际依赖 Windows named mutex、Windows TCP 表或 Windows 管理进程启动。仅为这些既有测试补充 `windows_only` 维度和 `os.name != "nt"` 跳过条件，未修改业务实现、未放宽断言或安全标准。分片日志和隔离数据库位于本机忽略目录，不作为提交内容。

## 交付边界与风险

- 本次提交不包含 Windows EXE，也不把 Linux ELF 冒充 Windows 启动器。CI 生成的 Windows 构建身份必须保留 `windows_gui_acceptance=PENDING_LOCAL_ACCEPTANCE`，CI smoke-check 不启动窗口。
- `config/launcher/trusted-publishers.json` 当前没有生产信任锚；协议签名验证失败关闭。TLS、同地址哈希或 GitHub 元数据扫描均不构成安全安装授权。
- Launcher 当前不会安装、覆盖、迁移或停止 Studio、ComfyUI、用户数据/数据库、模型、密钥配置或任何真实进程；它也不能提供完整修复能力。
- 未经真实模型推理验证的能力保持未验证，不能显示 READY。
- L0 Windows 真机验收、L3/L4/L5 实现和完整 P0 证据仍是发布及合并门槛。后续应在 PR 的 Windows workflow 成功后取回构建 artifact，核对来源、hash 和 ZIP，再由 Windows 10/11 环境执行人工 GUI、数据保留、升级回退及 GPU/模型验收。

## 当前 PR 决定

按用户给定的合并边界，当前只允许保持 Draft PR 供审阅和后续迭代；没有真实 Windows P0 验收证据时不得转为可合并状态，不得绕过 PR 修改或合入 main。
