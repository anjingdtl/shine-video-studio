# Shine Launcher V1.0 工程验收报告

- 报告日期：2026-11-14
- 实现分支：`feat/shine-launcher-v1-final-closeout-20261114`
- 独立 worktree：`/workspace/shine-launcher-final-agent`
- Pull Request：<https://github.com/anjingdtl/shine-video-studio/pull/17>
- 合并基线：`origin/main@1c7cfd32`（合并最新 main 之后；分支 HEAD `1ae6a0cb`）
- 工程方案：`docs/SHINE_LAUNCHER_STANDALONE_V1_ENGINEERING_PLAN_20261113.md`
- 上一轮报告：`docs/SHINE_LAUNCHER_ENGINEERING_ACCEPTANCE_REPORT_20261113.md`

> 本报告按**实际证据**分级。凡是本轮没有真实跑过的，一律标 `PENDING` 或
> `BLOCKED`，不以"代码已写"或"CI 只过了编译"冒充通过。

## 0. 总体结论

上一轮的 Launcher 只是一份可运行的只读原型：能看环境、能扫目录、能列版本，
但**没有安装执行链、没有升级事务、没有可信发布者、没有能力包、没有自更新**。
本轮把这些逐项补齐，形成可交付的 V1.0 工程实现。

**合入姿态**：本 PR 由仓库所有者授权合入 `main`。合入表示"代码已进入主线"，
**不表示 Windows GUI 真人验收、GPU 与模型推理验收已经通过**——这三项在本轮
环境里客观不可得，已在第 5 节逐条列出阻断条件与解除方式。

## 1. 上一轮遗留缺陷的关闭情况

| # | 上一轮远端验收提出的缺陷 | 本轮处置 | 证据 |
| --- | --- | --- | --- |
| 1 | 只有检测、扫描、登记，没有真正的一键安装或启动执行链 | **已关闭**。`runner.rs` 固定脚本白名单 + 参数数组 + 禁止 shell；`studio.rs` 串起 `install_once.ps1` / `start_shine.ps1` / `stop_shine.py`；新增 `scripts/launcher_bridge.py` 作为版本化 JSON 桥接 | `launcher/src/runner.rs`、`launcher/src/studio.rs`、`scripts/launcher_bridge.py`、Rust 单测 107 项 |
| 2 | 自更新、Studio 升级和恢复事务未实现 | **已关闭（源码级）**。`self_update.rs` 助手进程换 EXE；`upgrade.rs` 不可变计划 + 14 阶段状态机；`journal.rs` 追加式持久日志 + 跨进程事务锁；`download.rs` 断点续传；`archive.rs` 安全解包 | `launcher/src/self_update.rs`、`upgrade.rs`、`journal.rs`、`download.rs`、`archive.rs` 及各自单测 |
| 3 | 可信发布者密钥列表为空，真实远端安装保持关闭 | **部分关闭**。公钥信任锚已落地并生效；**私钥入库是阻断项**（见 5.3） | `config/launcher/trusted-publishers.json`、`launcher/src/bin/sign_manifest.rs`、`.github` 发布流程 |
| 4 | H3/Qwen 模型、节点、依赖、工作流没有接入 Launcher 完整部署 | **部分关闭**。`capability.rs` 按 `config/deployment` 清单与变体装配能力包并分层呈现；候选与 DEFERRED 能力不自动下载。**真实模型下载与推理未验证**（见 5.2） | `launcher/src/capability.rs`、Rust 单测 |
| 5 | 安装身份与目录绑定，移动目录可能改变 Installation ID | **已关闭**。身份改为随机持久化令牌 + 目录内身份文件；目录搬迁/换盘/重新 Clone 不改身份；0.1.x 路径哈希 ID 作为别名继续可解析 | `launcher/src/identity.rs`、`installation_registry.rs` 及单测 |
| 6 | Windows CI 只验证 `--smoke-check`，没有真实 GUI 操作验收 | **部分关闭**。新增无界面 `--self-test`（真实驱动注册表/升级状态机/能力规划/协议校验）与 `--verify-manifest`；CI 改为双自检。**真人 GUI 操作仍 PENDING**（见 5.1） | `launcher/src/main.rs`、`launcher/packaging/build_windows.ps1`、`launcher/packaging/ci/launcher-windows.yml` |
| 7 | 上一轮报告称 PR #13 保持 Draft，但该 PR 实际已合并 | **已更正**。本报告与实现状态文档均按真实状态表述；PR #13 于 2026-11-13 前后以 merge commit 合入 main | 本报告第 0 节、PR #17 描述 |
| 8 | 主线近期 CI 存在 Ruff 和浏览器 E2E 失败 | **已核实，未在本 PR 内修复**。本轮实测 `ruff check backend` 在当前 main 上**全部通过**（ruff 0.12.3），Ruff 失败已不复现；浏览器 E2E（`web-e2e`）在 PR 上按条件 skip，未取得证据。后端另有 **2 条 RelayVSR 既有回归**、前端另有 **13 条既有失败**，均在纯净 main 检出上复现，属导演/素材业务线，本 PR 不改业务代码 —— 详见第 4.5 节 | 本报告第 4.5 节；纯净 `origin/main@1c7cfd32` 检出复现 |

## 2. L0–L5 分阶段状态

| 阶段 | 状态 | 已完成 | 未完成 / 阻断 |
| --- | --- | --- | --- |
| L0 原生 EXE 技术验证 | `SOURCE_IMPLEMENTED / WINDOWS_CI_PASS / GUI_PENDING` | Rust + Slint、简体中文界面、CPU 软件渲染、无 Studio 依赖的只读探测；Windows Release 构建、`--smoke-check`、`--self-test` 双自检 | Windows 10/11 真人双击、中文字体/输入、DPI、中文路径、禁用 GPU 加速 |
| L1 稳定安装身份与旧版识别 | `IMPLEMENTED / LINUX_TESTED` | 随机持久化身份、目录内身份文件、路径搬迁保持身份、0.1.x 注册表自动迁移与别名、并发注册读改写同锁不丢记录、用户自有目录是链接时仍可用、启动器自有目录必须是真实对象 | Windows 只读性、ComfyUI/模型/节点全量识别、多实例管理界面 |
| L2 通道与 Deployment Protocol V1 | `IMPLEMENTED / TRUST_ANCHOR_LIVE / RELEASE_PENDING` | 协议 schema 与校验、JCS 规范化源身份、Ed25519 范围化校验、**studio 与 launcher 分离的两条正式公钥信任锚**、Stable/Beta/Development 与独立 Launcher 元数据扫描、缓存、签名工具与发布流程 | 生产私钥未入库前自动更新**安全拒绝**；GitHub 尚无正式 Launcher/Studio Release |
| L3 安装、升级与恢复 | `IMPLEMENTED / LINUX_TESTED / WINDOWS_PENDING` | 受控执行器、事务日志与跨进程锁、不可变升级计划（确认后哈希冻结）、一致性数据库快照、断点续传下载、安全解包、回滚判定（新版本已写入即转人工核对）、一键安装/启动/停止 | 真实机完整安装、旧版保数据升级与回滚、磁盘不足与网络中断恢复、端口冲突与半启动的 Windows 实测 |
| L4 能力包与正式发行 | `PARTIAL` | H3/Qwen 能力包按项目清单装配，分层状态；候选与 DEFERRED 能力不自动下载；签名发布流程；Clone/ZIP 的构建与校验脚本 | 模型真实推理未验证（一律不标 READY）；仓库根目录 `ShineLauncher.exe` 仍为 0.1.0 原型二进制，需在 Windows 按新源码重建 |
| L5 全量回归与最终验收 | `PARTIAL / PENDING_LOCAL_ACCEPTANCE` | Rust 107 项单测、Clippy 零告警、fmt、后端八分片、前端、ruff、迁移、Linux Release 构建与双自检；PR 的 Windows CI | Windows 10/11 GUI 真人验收、旧版保数据升级回滚、真实 GPU/模型推理、正式发行物 |

## 3. 安全设计要点（逐条对应方案要求）

- **不盲杀进程**：停止动作一律交给后端既有的 `stop_shine.py`。它按
  `PID + OS 出生身份` 核对后才动手，核对不出的只归档不终止（LA18、LA19）。
- **不打断生产**：安装/升级前经桥接查询真实生产任务与独占 GPU 租约，命中即
  `Blocked`，并把原因显示给用户（LA19）。
- **不盲回滚**：新版本一旦写入数据，回滚改为 `RECONcile_REQUIRED`，禁止用旧
  快照覆盖（LA11）。
- **不越权信任**：部署清单不能携带任意命令；协议不支持的必需能力直接拒绝；
  清单内相对路径必须在已验证的版本根内解析，拒绝路径穿越（LA08、LA20）。
- **不静默降级**：签名校验失败、发布者未知、密钥范围不符一律 fail closed，
  并在界面明确说明阻断原因。
- **密钥不进仓库**：私钥只存在于仓库外的安全目录，必须写入 GitHub Secret；
  仓库内只有公钥。
- **状态分层**：文件在磁盘 ≠ 环境兼容 ≠ 受管就绪 ≠ 真实生成已验证，界面与
  能力报告逐层区分（LA14、LA15）。

## 4. 验证证据

### 4.1 Rust（Linux，本轮实际执行）

```
cd launcher
cargo +1.100.0 fmt --all -- --check          # 通过
cargo +1.100.0 clippy --locked --all-targets -- -D warnings   # 0 告警
cargo +1.100.0 test --locked --lib            # 107 passed / 0 failed
cargo +1.100.0 build --release --bin shine-launcher            # 成功
```

自检与清单校验（Release 二进制实际输出）：

```
$ ./shine-launcher --self-test
Shine Launcher 自检通过
本机探测：Debian GNU/Linux 12
事务日志：写入成功
可信发布者：2 个（shine-launcher-release-2026、shine-studio-release-2026）
部署协议：已用正式发布者密钥验证通过（shine-studio-release-2026）
升级计划：冻结哈希 4aadb8ad6370572e0c94a2bd93517068fa9b9cc50d305ac48d568713fb2bb430
回滚判定：新版本已写入数据时拒绝盲目回退

$ ./shine-launcher --verify-manifest \
    ../config/launcher/test-fixtures/deployment-protocol-v1-signed-production.json
清单校验通过
发布者：shine-studio-release-2026
source_identity：4c32c9713eabd2d3ed6d0daf69608ce0f9c7cbf5b495bdbbf6a693106c774354
工作台版本：0.3.0
```

### 4.2 信任链端到端（真实生产密钥）

正式 studio 发布者密钥生成的清单，已作为**回归固件**提交
（`config/launcher/test-fixtures/deployment-protocol-v1-signed-production.json`，
只含公钥签名）。`protocol::tests::a_manifest_signed_by_the_production_publisher_verifies_against_the_committed_anchor`
证明仓库内的信任锚真实可验证该清单；`a_tampered_production_manifest_stops_verifying`
证明任何篡改都会立刻导致验证失败。签名与校验共用同一套 RFC 8785 规范化实现，
不存在"签名端与校验端算法不同"的缝隙。

### 4.3 本轮发现并修复的真实缺陷

| 缺陷 | 影响 | 修复 |
| --- | --- | --- |
| 0.1.x 注册表因字段集不同无法反序列化 | **老用户升级后启动器直接打不开** | 新增 v1 专用结构与迁移路径，注册表 schema 升到 v2 |
| 注册表读与写分离在同一把锁之外 | 两个启动器同时登记会**丢失一个安装** | 新增 `register_install_root_atomic`，读改写同锁 |
| `DiscoverySource` 枚举改名破坏旧文件兼容 | 老注册表反序列化失败 | 保留旧变体名作为 serde 别名 |
| 路径安全检查遍历全部祖先链 | **用户主目录被重定向时启动器完全不可用** | 改为按所有权分层：启动器自有目录必须是真实对象，用户自有目录不检查 |
| `UpgradePhase::PATH` 数组长度与元素数不符 | 阶段推进可能落到错误状态 | 修正为 14 项并加测试覆盖完整路径 |

### 4.4 回归（本轮实际执行）

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 后端全量 | `backend/.venv/python -m pytest backend/tests -q`，按 `AGENTS.md` 规定的 8 分片并行（`split -n r/8`，命令前加 `CODEBUDDY_SAFE_DELETE_ENABLED=0`） | 见下方"回归实跑记录" |
| 前端全量 | `npx vitest run --no-file-parallelism` | 见下方 |
| 前端构建 | `npm run build` | 见下方 |
| Python 静态检查 | `ruff check backend` | 见下方 |
| 数据库迁移 | 隔离目录 `alembic upgrade head` + `alembic check` | 见下方 |
| Windows 构建与自检 | PR 上的 `shine-launcher-windows` 工作流 | 见下方 |

### 4.5 回归实跑记录

> 本节按本轮实际输出回填。基线 commit：`origin/main@1c7cfd32`（合并最新 main 后
> 的分支 HEAD 为 `1ae6a0cb`）。所有命令在 Linux 沙箱内实际执行。

#### 4.5.1 后端 8 分片并行回归

严格按 `AGENTS.md` 的规定执行：按**文件**均分 8 片（不是按用例切），
命令前加 `CODEBUDDY_SAFE_DELETE_ENABLED=0`：

```bash
ls backend/tests/test_*.py > all-tests.txt      # 219 个测试文件
split -n r/8 all-tests.txt chunk-               # 28/28/28/27/27/27/27/27
for f in chunk-*; do
  (CODEBUDDY_SAFE_DELETE_ENABLED=0 \
   backend-venv/bin/python -m pytest $(tr '\n' ' ' < "$f") \
   -q -p no:cacheprovider > "$f.log" 2>&1; echo "$(basename $f) exit=$?" >> exit.txt) &
done
wait
grep -H "^FAILED\|^ERROR" chunk-*.log
```

环境：Python 3.12.16 独立 venv（`/workspace/backend-venv`），pytest 8.4.1、
ruff 0.12.3，系统 ffmpeg/ffprobe 均在位。

| 分片 | 测试文件 | 选中用例 | 退出码 |
| --- | --- | --- | --- |
| chunk-aa | 28 | 433 | 1 |
| chunk-ab | 28 | 236 | 1 |
| chunk-ac | 28 | 415 | 0 |
| chunk-ad | 27 | 337 | 0 |
| chunk-ae | 27 | 293 | 0 |
| chunk-af | 27 | 259 | 0 |
| chunk-ag | 27 | 248 | 0 |
| chunk-ah | 27 | 406 | 1 |
| **合计** | **219** | **2627** | 3 片非零 |

按 `AGENTS.md` 的判定口径，**8 分片并行回归未全绿**，失败共 4 条，已逐条定性：

| 失败用例 | 分片 | 定性 | 证据 |
| --- | --- | --- | --- |
| `test_sdk_role_process.py::test_child_timeout_waits_for_exit_and_never_replays` | aa | **并发时序敏感，非本分支引入** | 单独跑该模块 29/29 全通过；首次 8 分片运行时同文件另一条用例也失败，重跑时反而通过——典型的进程启动握手超时抖动 |
| `test_runtime_activation.py::test_real_resident_worker_observes_activation_and_blocks_changed_files` | ab | **并发时序敏感，非本分支引入** | 同上，单独跑该模块全通过；失败点为常驻进程在超时内未回报启动成功 |
| `test_relay_upscale_stage.py::test_full_chain_with_available_backend` | ah | **main 既回归，非本分支引入** | 在**纯净 `origin/main@1c7cfd32` 检出**上单跑该文件，**同样失败**（`FileNotFoundError: 'missing-ffmpeg'`） |
| `test_relay_upscale_stage.py::test_not_exactly_2x_is_a_named_refusal` | ah | **main 既有回归，非本分支引入** | 同上，纯净 main 检出同样失败 |

关键佐证：

- `git diff origin/main..HEAD -- backend/` **为空**——本分支对后端**零改动**，
  上述失败不可能由本分支代码引起。
- `test_relay_upscale_stage.py` 的失败根因是 RelayVSR 新增链路里
  `relayvsr.mux_source_audio()` 直接 `subprocess.run(['missing-ffmpeg', ...])`，
  未捕获 `FileNotFoundError`（只判 `returncode != 0`），导致"缺 ffmpeg"这一预期
  场景抛出未捕获异常而不是返回命名拒绝。该文件属于 RelayVSR 业务线，
  **不在本任务授权范围内，本轮不修**，已如实登记为 main 既有回归。

> 结论：本分支不引入任何后端回归；后端回归在 main 上同样存在，需由 RelayVSR
> 业务线负责人修复。

#### 4.5.2 Python 静态检查

```bash
backend-venv/bin/python -m ruff check backend
```

→ `All checks passed!`（ruff 0.12.3，无告警）。**PASS**

#### 4.5.3 数据库迁移（隔离目录）

```bash
export SHINE_DATABASE_URL="sqlite:////workspace/migration-check/shine.sqlite3"
backend-venv/bin/python -m alembic upgrade head
backend-venv/bin/python -m alembic check
backend-venv/bin/python -m alembic current
```

→ 升级链路 `f180b0070001 → f180d0010001` 全部执行成功；
`alembic check` 输出 `No new upgrade operations detected`；
`alembic current` = `f180d0010001 (head)`。**PASS**（仅对隔离库执行，未触碰
任何真实数据目录）

#### 4.5.4 前端

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 构建 | `npm run build` | **PASS** — `✓ 1644 modules transformed`，`✓ built in 1m 41s`，产物 `dist/assets/index-6cNBTYgy.js` 981.63 kB |
| 单测 | `npx vitest run --no-file-parallelism` | **FAIL（main 既有回归）** — Test Files 3 failed / 46 passed (49)；Tests 13 failed / 274 passed (287)；耗时 420.35 s |

13 条失败用例的**确切名称**（3 个文件 / 13 条）：

`src/test/styleFreeze.test.tsx`（9 条）

1. `风格冻结：批准内容必须等于冻结快照 > 未保存修改直接冻结，核对正文与对白比例 70`
2. `风格冻结：批准内容必须等于冻结快照 > 未保存修改直接冻结，核对正文与对白比例 null`
3. `影片类型 > 选择动画后保存的草稿带上 film_type，并展示固定英文范例`
4. `影片类型 > 刷新工作台不丢弃未保存的影片类型选择`
5. `风格冻结（下拉交互） > 免密冻结仍保持保存→授权→冻结顺序，授权后草稿变化被拒绝并保留选项`
6. `风格冻结（下拉交互） > 首次通过下拉选满七项后直接点击批准并冻结即可成功`
7. `风格冻结（下拉交互） > 保存失败（网络错误）时下拉选择保留（故障注入）`
8. `风格冻结（下拉交互） > 携带七项确认内容，签发前的其他页面修改被拒绝时保留选项`
9. `风格冻结（下拉交互） > 不把不匹配的服务端冻结结果报告为与屏幕一致`

`src/test/shotDraft.test.tsx`（2 条）

10. `分镜提交失败必须保留输入 > 失败后修正参数重新提交：只创建一个镜头，成功后按约定清空`
11. `镜头选择器写入真实 ShotSpec（P01/P02） > 人物来自本项目已批准主资产，选择后进入 POST spec 并带景别/运镜`

`src/test/simpleAssets.test.tsx`（2 条）

12. `锁定失败会保留已批准状态；再次确认只补锁定，不重复批准`
13. `项目锁定素材直接进入全局候选，不自动晋升`

判定：`git diff origin/main..HEAD -- frontend/` **为空**——本分支对前端**零改动**，
13 条失败全部落在**导演模式 / 风格冻结 / 素材中心**业务线上，与启动器无关，
且与在飞的素材整备中心工作线重叠。本任务明令不得借机大规模改造无关导演业务，
**本轮不修**，如实登记为 main 既有回归。

#### 4.5.5 Rust 与 Windows CI

| 检查 | 结果 |
| --- | --- |
| `cargo fmt --all -- --check` | **PASS**（无差异） |
| `cargo clippy --locked --all-targets -- -D warnings` | **PASS**（0 告警） |
| `cargo test --locked --lib` | **PASS** — `107 passed; 0 failed`（合并最新 main 后复跑仍为 107/0） |
| Release 构建 + `--self-test` | **PASS** |
| `--verify-manifest`（生产公钥） | **PASS** |
| PR 工作流 `ci`（L1 backend quick / L2 backend integration / Frontend build） | **PASS**（run `43545658016` → success） |
| PR 工作流 `shine-launcher-windows` | **PASS**（run `43545657951` → success） |

分支合入最新 main 后的复跑记录：GitHub 侧 `update_pull_request_branch`
把最新 main 合入分支，HEAD 由 `d5547580` 变为 `1ae6a0cb`，两个工作流在该
HEAD 上**重新执行且均成功**，Rust 单测与 clippy 在合并后本地复跑仍全绿。

## 5. 明确阻断项与解除条件

以下三项在**本轮环境客观不可得**，不得以任何方式声称通过。

### 5.1 Windows 10/11 真人 GUI 验收 — `PENDING`

云端只有 `windows-latest`（Windows Server）CI，没有桌面会话，没有中文字体、
DPI 缩放与真实鼠标交互。CI 只做无界面自检，**不能冒充真人 GUI 验收**。

解除条件（本地 Windows 10/11 x64）：

1. 全新机器，不装 Python / Node / Rust / ComfyUI，双击 `ShineLauncher.exe`
   能打开；
2. 简体中文显示正常，中文路径与含空格路径可读写，DPI 100%/150% 下布局不破；
3. 完整安装 → 启动 → 打开工作台 → 停止 → 再启动，全链路截图与日志；
4. 已有旧版目录升级后，**项目数量、数据库、模型 SHA、LLM 配置引用保持一致**；
5. 网络中断、磁盘不足、安装中途失败后重试，均能恢复且不产生半安装状态；
6. 把 `windows_gui_acceptance` 从 `PENDING_LOCAL_ACCEPTANCE` 改为 `PASSED`。

### 5.2 真实 GPU 与模型推理 — `BLOCKED`

本轮无 NVIDIA 硬件，H3 / Qwen 权重未下载、未加载、未推理。因此能力报告中
这些能力**一律不显示为 READY**，只显示"文件已就位/尚未做真实验证"。

解除条件：在有 NVIDIA 显卡的机器上完成一次真实 Ref2VA 出片与一次 Qwen 出图，
把 run 级证据（任务 ID、产物 SHA、GPU 型号、驱动版本）写入验收文档。

### 5.3 生产签名私钥入库 — `BLOCKED`

正式私钥已生成，但**故意不提交仓库**，当前只存在于仓库外的安全目录：

```
/workspace/shine-release-signing/shine-studio-release-seed.b64
/workspace/shine-release-signing/shine-launcher-release-seed.b64
```

解除条件（由仓库所有者执行）：

1. 把两份 base64 种子分别写入 GitHub Secret
   `SHINE_STUDIO_RELEASE_SIGNING_KEY` 与 `SHINE_LAUNCHER_RELEASE_SIGNING_KEY`；
2. 删除本地副本；
3. 打 `studio-v*` / `launcher-v*` tag 触发 `.github` 发布流程，产出签名清单；
4. 启动器 `--verify-manifest` 复核通过后，自动更新才会真正开启。

**在此之前，自动更新保持安全拒绝**——这是要求的行为，不是缺陷，也**不得**
通过绕过信任机制来"让它看起来能用"。

### 5.4 仓库根目录 EXE 需要重建 — `PENDING`

仓库根目录的 `ShineLauncher.exe` 仍是 0.1.0 原型二进制（SHA-256
`be07c704…`），与本轮 1.0.0 源码不对应。CI 的发布 job 会在工作流安装到
`.github/workflows/` 后自动重建并替换它；在那之前，Clone 用户拿到的仍是旧
原型，需要本机重建。

## 6. 交付物清单

| 交付项 | 位置 | 状态 |
| --- | --- | --- |
| Launcher V1.0 源代码 | `launcher/src/**`、`launcher/ui/main.slint` | 已交付 |
| Windows EXE | `launcher/target/release/shine-launcher.exe`（CI 产出）、仓库根目录 `ShineLauncher.exe` | CI 已产出新版；根目录待重建 |
| Launcher 与 Studio 双版本管理 | `launcher/src/protocol.rs`、`update_channels.rs`、`self_update.rs`、`config/launcher/launcher-release-policy.json` | 已交付 |
| Deployment Protocol V1 运行与安全验证 | `config/launcher/deployment-protocol-v1.schema.json`、`trusted-publishers.json`、`launcher/src/protocol.rs` | 已交付 |
| 一键安装、旧版升级、回滚与故障恢复 | `launcher/src/studio.rs`、`upgrade.rs`、`journal.rs`、`download.rs`、`archive.rs`、`scripts/launcher_bridge.py` | 已交付（源码级） |
| Node/Python/ComfyUI/模型/节点/工作流部署 | `launcher/src/capability.rs`、`launcher/src/runner.rs`、复用 `backend/shine/comfyops/` | 已交付（清单驱动） |
| GitHub Clone 与发行 ZIP 一致性 | `launcher/packaging/package_windows.ps1`、`verify_windows.ps1` | 已交付 |
| 自动化测试 | `launcher/src/**` 的 107 项单测 | 已交付 |
| CI 工作流 | `launcher/packaging/ci/`（安装到 `.github/workflows/`） | 已交付源文件，安装待本地执行 |
| 更新后的工程验收 MD | 本文件、`docs/SHINE_LAUNCHER_IMPLEMENTATION_STATUS.md` | 已交付 |

## 7. PR 与合并状态

- PR：<https://github.com/anjingdtl/shine-video-studio/pull/17>（ready，非 Draft）
- 实现分支：`feat/shine-launcher-v1-final-closeout-20261114`
- 合并基线：`main@1c7cfd32`。分支在合并前通过 GitHub 的
  `update_pull_request_branch` 合入了**最新 main**（HEAD 由 `d5547580` 变为
  `1ae6a0cb`），因此 PR 不落后于主线，也不会把主线的导演业务改动算进本 PR
  的 diff。
- 合并前的 CI 状态（HEAD `1ae6a0cb`）：`ci` **success**（L1 backend quick /
  L2 backend integration / Frontend build 全绿；L3 三项按分支条件 skip）、
  `shine-launcher-windows` **success**。
- 合并方式：仓库 `main` 受 ruleset `main-owner-only-push` 保护，仅 admin 可更新；
  本 PR 由所有者授权，以 PR 正常合并，**不绕过保护规则、不直接推 main**。
- 合并 commit 的 SHA 以 GitHub 上 PR #17 的记录为准（本报告不预填，避免写入
  与实际不符的假 SHA）。
- 合并后的遗留项：第 5 节全部阻断项按原样保留在主线，不因合并而消失。
  **合入 ≠ Windows GUI 真人验收通过、≠ GPU 与模型推理验收通过、≠ 私钥已入库。**

## 8. 已知限制

1. Launcher 的安装执行链依赖仓库既有 PowerShell 脚本；在 Windows 上首次运行
   需要 PowerShell 5.1（系统自带）。
2. `--self-test` 与 `--verify-manifest` 是无界面模式，用于 CI 与人工核对；
   它们不覆盖 GUI 交互。
3. 能力报告基于文件与清单核对，真实生成验证需要在有 GPU 的机器上补做。
4. `install_workflows.ps1` 需要在 Windows 上执行一次，把 CI 工作流装到
   `.github/workflows/`；自动化身份无权写该目录。
5. 仓库根目录 EXE 需要在 Windows 上重建，本轮云端无法产出 Windows 二进制。
