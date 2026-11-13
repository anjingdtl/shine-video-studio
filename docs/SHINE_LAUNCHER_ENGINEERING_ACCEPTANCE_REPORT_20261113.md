# Shine Launcher V1.0 工程阶段验收报告

- 报告日期：2026-11-13
- 实现分支：`feat/shine-launcher-impl-20261113`
- 独立 worktree：`/workspace/shine-launcher-agent`
- 工程方案：`9dfa76b6e9fbef4751ce42bc8fc50503ef51df0a`
- 当前主线：`origin/main` 的 `1a55c41509924b898a587901b0aa5aa5ac71cc70`，已合入实现分支；合并提交 `3a3a532`

总体验收：**部分完成；PR #13 保持 Draft，不合入 main。**

## 验收结论

已交付 Rust + Slint 原生 Launcher 原型、中文界面源码、CPU 软件渲染配置、本机只读环境探测、旧版目录识别/登记基础，以及 Deployment Protocol V1 schema、兼容性校验和版本元数据扫描基础。Launcher 的读取与 smoke-check 路径不依赖 Studio 的 Python、Node、API、Worker、ComfyUI 或 GPU 服务。

GitHub Actions 已生成真实 Windows x64 EXE，并成功把验证后的 EXE、哈希 sidecar 和 build-info 发布到实现分支根目录。CI smoke-check 只做宿主探测并退出，没有显示 GUI 窗口。因此尚不能确认 Windows 10/11 真机上的中文显示、无运行环境启动、DPI、中文路径和用户交互。根目录 EXE 当前来自合并前的源提交，最新分支 push 的构建与发布需要再次验证。

本次合并后的 Linux 回归中，后端 212 个测试文件、2,517 项经八分片全部通过；前端 47 个文件、278 项通过；前端生产构建、Ruff 和隔离 Alembic 迁移检查通过。旧安装保数据升级/回退、完整安装与恢复交易、Launcher 自更新、真实 GPU/模型验证仍未实现或未验收。按工程方案的 P0 门槛，PR 必须保持 Draft。

## L0–L5 状态

| 阶段 | 状态 | 已完成 | 未完成 / 阻断 |
| --- | --- | --- | --- |
| L0 原生 EXE 技术验证 | `SOURCE_IMPLEMENTED / PENDING_GUI_ACCEPTANCE` | Rust/Slint GUI、中文界面源码、software renderer、后台只读环境探测；Windows Server 2025 workflow 通过格式、编译、22 项单测、Clippy、Release EXE、宿主 smoke-check、build identity/hash 和 ZIP 解包复核。 | GUI 未实际打开；Windows 10/11 中文字体/输入、DPI、中文路径、无 GPU/无 Python/Node 的 GUI 操作仍待真机。 |
| L1 安装登记与旧版识别 | `PARTIAL` | 显式选目录后的只读扫描、数据库/数据目录证据、路径保护、per-user 原子安装登记。 | 新安装目录流程、ComfyUI/模型/节点全量识别、多实例管理 UI、Windows 只读性和中文路径验收。 |
| L2 通道与 Deployment Protocol V1 | `PARTIAL / FAIL_CLOSED` | 版本化 schema、解析和兼容性判断、规范化源身份、范围限定 Ed25519 校验、Stable/Beta/Development 和独立 Launcher 元数据扫描、缓存及不可变开发引用解析。 | 可信发布者密钥库为空；无可信资源下载器、Studio 安装/升级器或 Launcher 自更新/回滚；没有已核实的现行发行 tag。远程部署保持禁用。 |
| L3 安装、升级与恢复 | `NOT STARTED` | 无可验收实现。 | 安装事务/journal、备份/回滚、依赖修复、进程身份核验与生命周期管理、工作台启动、诊断和恢复。 |
| L4 能力包与分发 | `PARTIAL` | Windows ZIP 的构建、解包和身份校验通过；push-only publisher 已将哈希校验的 Windows EXE 发布到实现分支根目录。 | H3/Qwen 模型—节点—依赖—工作流完整能力包、部署验证、独立 Launcher 正式发行及 Studio 兼容性未完成；模型未经真实推理不得标 READY。 |
| L5 最终回归与验收 | `PARTIAL / PENDING_LOCAL_ACCEPTANCE` | 当前主线合并后的 Linux 后端/前端回归、构建、静态检查、迁移检查完成；Windows CI 构建和 ZIP 自检成功。 | P0 Windows GUI、旧版保数据升级和回退、断点续传/异常退出、Studio 与 Launcher 独立更新、Clone/Release 一致性、真实 GPU/模型推理及完整安装恢复验收仍待完成。 |

## 验证证据

| 检查 | 结果 |
| --- | --- |
| Windows CI 构建/测试/打包 | [workflow run 43420629491](https://github.com/anjingdtl/shine-video-studio/actions/runs/43420629491) 通过：Windows x64 22 项 Rust 单测、Clippy、Release EXE、host-only smoke-check、build identity/hash、ZIP 构建和解包后复核。CI 未启动 GUI。 |
| Windows 构建工件身份 | run `43420629491` 的 EXE SHA-256：`54a09b62df64fcc7ddb7afbd2a7e2046893d3c08d850d7bef6203e40ef683dba`；ZIP SHA-256：`1e58ddae5e7723413413d33a5c6dd2445777dcef177c1d3fdb65881dc83fdf75`；Actions artifact digest：`6a9aa1c1dd9c8f27f97b93bfc99ce0c0d0b9ea533d7fd6986a10e2b878106eab`。 |
| Clone 根目录 EXE 发布 | [push-only publisher run 43422636863](https://github.com/anjingdtl/shine-video-studio/actions/runs/43422636863) 成功。当前根目录 `ShineLauncher.exe` 为 3,778,048 字节，SHA-256 `be07c704605d79646e4fd266649f1979b846b50d6fe6a2a0ddbace6bcb252607`；build-info 的 source commit 为 `f41e60940968eea6555041db3204b760986f7051`，`windows_gui_acceptance=PENDING_LOCAL_ACCEPTANCE`。后续 push 应触发重新构建/发布，需再核对产物。 |
| Linux Launcher 检查 | 在本阶段前序运行中 `cargo fmt --all -- --check`、`cargo check --locked --all-targets`、`cargo test --locked --lib`（24 项）、Clippy 和 Windows MSVC target check 通过。当前 shell 没有 Cargo 工具链，故未在合入最新 main 后重跑；main 合并及本次修复没有改动 Launcher Rust 源码。Windows 真正编译由上面的 Windows runner 完成。 |
| 后端完整回归 | 按当前 `AGENTS.md` 八分片规范按测试文件分组，212 个文件、2,517 项；八片最终退出码均为 0。首次收集暴露 `test_startup_guard.py` 在 Linux 用 Windows `where` 命令的问题；已改为 `shutil.which`，复跑受影响的 `chunk-ac` 并通过。其他七片首轮均通过。Windows-only 进程/端口测试仍待 Windows 执行。日志与隔离 DB 在忽略目录 `work/final-backend-shards/`。 |
| 前端测试 | `npx vitest run --no-file-parallelism`：47 个文件、278 项通过；输出含既有 React `act(...)` 警告。 |
| 前端生产构建 | main 合并后 `npm run build` 通过 TypeScript 与 Vite 构建；存在既有大 bundle advisory。 |
| Python 静态检查 | `CODEBUDDY_SAFE_DELETE_ENABLED=0 work/backend-venv/bin/python -m ruff check backend` 通过。 |
| 数据库迁移 | 仅对 `work/final-migration/shine.sqlite3` 执行 Alembic；升级至 `f180c0030001`，`alembic check` 显示 `No new upgrade operations detected.` 未访问真实用户项目数据库。 |
| 差异格式检查 | 在提交前执行 `git diff --check`。 |

## 主要实现与安全边界

- Launcher 源码与 Windows workflow 位于独立实现分支；最新已检查 main 提交已合并，未直接修改 main，也未覆盖其他分支或 worktree。
- `config/launcher/trusted-publishers.json` 没有生产信任锚。签名验证失败关闭；GitHub metadata、HTTPS 或哈希本身均不构成部署授权。
- 当前 Launcher 不安装、覆盖、迁移或停止 Studio、ComfyUI、用户数据库/项目、影片、素材、密钥、模型或真实进程；因此还不能提供完整故障修复能力。
- EXE sidecar/build-info 追溯当前发布的文件哈希和源提交，但 Launcher 尚未签名，Windows SmartScreen/发布者信任也未经处理。
- 尚未完成模型和工作流的真实推理验证；任何对应能力都不得显示 READY。

## 最终 PR 决定

PR [#13](https://github.com/anjingdtl/shine-video-studio/pull/13) 指向 `main`，保持 Draft。当前分支已合并到 `origin/main` 的 `1a55c41`；提交后由 Windows CI 再次构建并验证 Clone 根目录产物。没有真实 Windows GUI、旧版数据保留升级/回退及 GPU/模型 P0 证据时，不得将 PR 标记 Ready 或合并 main。
