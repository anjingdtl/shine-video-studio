# Shine 视频工作台 — 最终产品化收束与工程验收报告（2026-11-02）

> 本报告描述 2026-11-02 「最终产品化收束与工程 ALL PASS 建设」轮次的真实结果。
> 所有测试结果均来自本轮实际执行；未运行的验证以「未运行边界」如实列出，不做代偿性描述。

## 1. 基线与最终提交

| 项 | 值 |
|---|---|
| 审查起始基线 | `7b49a6c`（与 `origin/main` 一致，工作树干净） |
| 代码终点（本报告全部软件改动的提交） | `06975bc` 产品与测试改动 + `ffd26ce` 打包脚本 UTF-8 修复 |
| 文档与证据提交 | 本报告 + `docs/evidence/20261102-director-closeout/`（随本文件同批提交，仅文档，不改变任何软件行为；测试包自代码终点构建） |
| 推送方式 | 正常快进 push 到 `origin/main`，无强推、无历史改写 |

环境修复记录：`backend/.venv` 原为 Python 3.14（与 `requires-python >=3.12,<3.13` 冲突，`pydantic_core` 二进制缺失）。本轮以 uv 管理的 CPython 3.12.13 重建并安装全部依赖（`uv pip install -e . --group dev`），此后全部后端测试基于该环境。

## 2. 「演示模式难以切换」的真实根因与修复结果

审计确认了任务书中列出的全部线索，并定位到确切机制（不是笼统的"状态分裂"）：

1. **后端早已是单一事实来源，但从未对外输出一个用户语义的状态**。`resolve_runtime()` 的三态结果（mock / comfyui / unavailable+原因）、`/setup/runtime` 的激活与验证证据、Worker 心跳与 GPU 租约对账状态分散在四个接口里，各说各话。
2. **前端有四套并行的演示/真实表示**：`App.tsx` 本地 `queueProvider`（默认 mock、可任选真实）、轮询的 `system.render_mode` 字符串、SetupPanel 自己的 `RuntimeState`、PreproductionPanel 的 fake/local 轴。单镜头"真实生成"选项不看任何后端状态即可提交。
3. **"服务没在运行"被误报为"无法真实生产"**。`ProductionSeriesPanel` 的阻断文案写着"真实生产需要本机 H3 渲染器在线"，而受管渲染器在序列结束后按设计停机（`release_managed_series`），`h3_connected=false` 是正常空闲态。
4. **切换入口只改变显示**：顶部只有一个无操作的 mode-badge；真正切换要走专业设置里的八步安装细节；`.env` 持久化其实存在（激活时写 `SHINE_RENDER_PROVIDER=comfyui`），但 UI 从未把这条链路呈现为一个入口。

**修复结果**（P0 全部落地）：

- 后端 `/api/v1/system/status` 新增 `production_mode` 权威块：`DEMO / SETUP_REQUIRED / VALIDATION_REQUIRED / READY / RUNNING / BLOCKED / RECONCILE_REQUIRED` 七态 + 中文 label/detail + 动作 + 原始错误码。判定优先级：租约对账 > 配置阻断 > 真实执行中 > 待验证 > Worker 离线 > READY > 未完成配置 > 演示。**渲染器探活（`renderer_online`）只是信息，不参与降级**；mock 任务永不把模式置为 RUNNING。
- 前端顶栏常驻 `ModeStatusEntry`：演示态永远给「启用真实制作」按钮，阻断态给「检查并修复」，就绪态给「开始制作」，执行中给「查看进度」。API 失联时明确显示"本机服务未连接"，不假装处于任何模式。
- 「启用真实制作」进入单一路径向导（`RealProductionWizard`），复用与 SetupPanel 完全相同的后端合同：硬件 → 已有 ComfyUI 检测与复用 → 模型哈希核对 → 生成方案登记与服务验证 → 激活。**实测在本机检测到已有 ComfyUI 及约 41 GB 哈希匹配的可复用模型并优先提供复用，不默认重新下载**；下载等重操作先征求确认。
- 单镜头与连续制作统一门禁：演示态锁定 mock（选择器禁用并显示原因）；READY 默认真实生成（可显式切回演示）；环境错误时明确阻断并给修复入口，**绝不静默退回 Mock**。
- 持久化：激活写 `.env`（原有机制），页面刷新 / 重启 / 项目切换后状态始终来自后端轮询；另有本轮修复——刷新后停留在用户最后打开的作品，而不是跳回列表第一个。

回归测试：后端 `test_production_mode.py` 8 用例（含"渲染器空闲停机时 READY 不降级"、"mock 任务不改模式"、"租约对账优先于一切"）；前端 `modeEntry.test.tsx` 8 用例（徽章、向导复用链、中文阻断、READY 提交 comfyui、演示锁 mock、BLOCKED 修复入口、无项目引导）。

## 3. 后端状态机与前端交互的最终实现

后端（`backend/shine/production_mode.py`，`main.py:system_status` 装配）：

```
RECONCILE_REQUIRED  任一 ResourceLease 处于 RECONCILE_REQUIRED（不确定外部状态永不被他因掩盖）
BLOCKED             resolve_runtime 409（ACTIVE_RUNTIME_* 等，中文原因+原始码）
RUNNING             已激活 + 服务验证 READY + 存在 comfyui 执行中任务或 QUEUED/RUNNING 连续制作
VALIDATION_REQUIRED 已激活但服务验证证据 PENDING（配置/合同漂移后必须重新验证）
BLOCKED(WORKER_OFFLINE) 已激活+验证通过但后台制作服务离线
READY               已激活 + 验证通过 + Worker 在线（渲染器空闲不影响）
SETUP_REQUIRED      未激活但已有候选配置（或手工 .env 指定了 comfyui）
DEMO                无激活配置（演示体验，用户可主动选择停留）
```

前端（`ModeStatusEntry.tsx`、`RealProductionWizard.tsx`、`useStudio.ts`）：`realSubmissionAllowed = mode ∈ {READY, RUNNING}` 是唯一的生成方式来源；`queueProvider` 由它派生（演示覆盖 `demoOverride` 仅在允许真实时可用）；`ProductionSeriesPanel.canStart = mode === 'READY'`。刷新即重读后端，无前端独立持久化的模式状态。

## 4. 普通导演模式：导航结构与完整制作路径

默认进入**导演模式**，四步导航：`我的作品 → 素材与风格 → AI导演制作 → 成片中心`，每步由真实项目状态驱动「下一步」标记与主 CTA：

| 状态 | 高亮步骤 | 主按钮 |
|---|---|---|
| 空库 | 首启卡片 | 创建我的第一部AI短剧 |
| 无已确认素材 | 素材与风格 | 添加素材 |
| 素材已确认、风格未冻结 | 素材与风格 | 确定作品风格 |
| 风格已冻结、无分镜 | AI导演制作 | AI 生成分镜 |
| 分镜就绪、无成片 | AI导演制作 | 开始制作影片 |
| 已有通过镜头 | 成片中心 | 查看成片 |

真实浏览器 E2E 已在隔离数据目录完整走通：建作品 → API 上传素材 → UI「确认使用全部待确认素材」（原生确认框）→ 选「都市短剧」预设 → 批准并冻结（V1，哈希一致提示）→ 高级区手工分镜（意图+素材引用）→ 加入队列 → 后台制作服务执行 → 「已通过 · 质检 通过」→ 成片中心（演示任务不出真片，空态如实显示「暂无交付成片」）。

首次体验细节：空库不强迫装机（建作品/导剧本/整理素材均可用）；无 LLM 路由时明确提示「AI 分镜能力未就绪」，保留手工分镜与「去配置模型服务」入口；向导在无作品时明确要求先创建作品。

**专业模式**：顶栏切换进入，原四板块（设置/生产/交付/专业）与全部面板（制片准备、人名字幕、审计、影视团队、H3 规范、偏好、存储）原样保留；旧深链 `#v/...`、`#setup` 继续映射到专业模式；界面层级选择记忆在本地。

## 5. UI 复杂度收束前后对比

| 维度 | 收束前（7b49a6c） | 收束后 |
|---|---|---|
| 普通用户默认视野 | 4 板块 13 个导航锚点全部展开，单页滚动承载 7+ 面板 | 4 个步骤页，按项目状态一次只面对一个阶段 |
| 模式表示 | 顶部徽章（无操作）+ 3.5 个状态灯 + 各面板各自的模式文案 + mock 场景选择器常驻 | 一个带操作入口的模式入口；演示提示条常驻；真实可用前不显示任何工程场景控件 |
| 首次使用 | 直接进入完整工作台 | 「创建我的第一部AI短剧」单卡片 |
| 单镜头工程参数 | 生成方式/模板/自审范围/宽高常驻表单 | 折叠进「单镜头制作与任务明细（高级）」，演示态锁死 |
| 工程术语（普通路径） | Mock/Worker/Profile/EDL/JSON/质量门禁 混用 | 演示体验/后台制作服务/生成方案/剪辑方案/自动检查（后台与日志保留原始码） |
| App.tsx | 926 行业务+布局混合 | 布局职责；数据与操作移入 `useStudio.ts`（约 430 行） |

## 6. 重要文件修改清单（代码提交 06975bc，30 文件 +2423/−689）

**后端**
- `shine/production_mode.py`（新增）— 权威生产模式汇总
- `shine/main.py` — system_status 装配 production_mode
- `shine/schemas.py` — `ProductionModeStatus` 模型
- `alembic/versions/f150a0030001_delivery_order_indexes.py`（新增）— 补齐两索引
- `tests/test_production_mode.py`（新增，8 用例）
- `tests/test_alembic.py` / `test_migrations_windows.py` / `test_ref2va_hardening.py` — 路径无变化 + 头版本推进 + 零漂移回归
- `tests/test_bootstrap_entry.py` — 子进程 UTF-8 解码合同
- `tests/test_v13_migrations.py` / `test_global_workflow_name_migration.py` — 头版本断言更新

**前端**
- `useStudio.ts`（新增）— 数据/操作层与唯一模式派生
- `components/ModeStatusEntry.tsx`（新增）— 模式入口 + 演示提示条
- `components/RealProductionWizard.tsx`（新增）— 启用/检查向导
- `App.tsx` — 导演/专业双层重写（hash 兼容 `#d/*` 与 `#v/*`）
- `components/ProductionSeriesPanel.tsx` / `ProductionPanel.tsx` / `DeliveryPanel.tsx` — 门禁统一与术语清理
- `api.ts`（类型）、`styles.css`（新组件样式，暗色主题同步）
- 测试：`modeEntry.test.tsx`（新增 8 用例）、`setupEntry`/`profileRefresh` 重写、`seriesOrder`/`simpleAssets`/`shotDraft`/`styleFreeze`/`deliveryRefresh`/`studioStub` 适配

**脚本**
- `scripts/bootstrap.ps1` — UTF-8 输出合同（PS 5.1/7）

## 7. 工程遗留问题关闭证据

| 遗留 | 根因 | 关闭证据 |
|---|---|---|
| `alembic check` 两索引漂移 | `f140a0030001` 未建 `ix_delivery_orders_{project,series}_id` | 新迁移幂等补齐；全新库升级、旧库副本升级、降级再升级均有测试；`alembic check` 输出 "No new upgrade operations detected."；新增永久零漂移回归 `test_upgraded_database_has_no_autogenerate_drift` |
| 测试目录敏感 | `Path("backend/alembic.ini")` 等按 CWD 解析 | 改为 `__file__` 解析；全套件从仓库根与 `backend/` 双目录各跑一遍：**均 1358 passed / 0 failed / 1 skipped** |
| PowerShell 编码失败 | PS 5.1 重定向输出为 GBK；宿主 `PYTHONUTF8=1` 下 reader 线程按 UTF-8 解码崩溃 → `stderr=None` | `bootstrap.ps1` 显式设 `[Console]::OutputEncoding=UTF8`（实测 PS5.1/7 stdout+stderr 均生效；脚本本就带 BOM）；测试显式 `encoding='utf-8', errors='replace'`。中文区域系统上 `test_bootstrap_entry.py` **11/11 通过**（未删测试、未放宽断言） |
| 一键启动 PID 复用 | 已由 `d7bff50` 修复（只识别分类、绝不误杀） | 复核其实现与测试（`test_process_probe`、`test_stack_operations`、`test_owned_process_group`、`test_package_entrypoints`）全部通过；本轮 E2E 前后端口均干净释放 |

## 8. 真实测试结果汇总

| 验证 | 命令/方式 | 结果 |
|---|---|---|
| 后端全量（backend/ 目录） | `.venv/Scripts/python -m pytest tests` | **1358 passed / 0 failed / 1 skipped**（exit 0） |
| 后端全量（仓库根目录） | 同上，`backend/tests` | **1358 passed / 0 failed / 1 skipped**（exit 0） |
| 跳过项 | — | 1 例：宿主缺少 FFmpeg 二进制的真实解码用例（`no FFmpeg binary is available on this host`，外部依赖合法跳过，非失败转跳过） |
| Ruff | `ruff check .`（backend） | 通过（0 error） |
| Alembic | 全新库 `upgrade head` + `check` | 通过，零差异 |
| 前端单测 | `npx vitest run` | **9 文件 42/42 通过** |
| TypeScript | `npx tsc --noEmit` | 通过 |
| 前端构建 | `npx vite build` | 通过（436.5 kB / gzip 136.1 kB） |
| 真实浏览器 E2E | IAB（真实 Chromium）驱动 Vite(5199)→API(8765)→Worker，隔离 SQLite | 完整导演流程通过（见 §4）；刷新/重启状态一致；1366×768 与 1280×720 无横向溢出（0px），内容纵向滚动正常 |
| 测试包 | `pwsh -File scripts\build_test_package.ps1 -OutDir … -Verify`（自代码终点提交构建） | 见 §12 推送前执行并在下表登记 |

## 9. 真实 H3 接线验证结果与未运行边界

**已验证（软件合同层，全部真实断言）**：

- Web「开始制作影片」→ `POST /projects/{id}/production-series` 强制 `provider=comfyui`（后端拒绝非真实序列）；READY 门禁由 `production_mode` 驱动。
- 真实提交的前置链在测试与实机双重确认：preproduction 冻结方案强制、工作流合同哈希校验、激活身份再验证、服务验证证据封存、`.env` 持久化、Worker 逐轮 `resolve_runtime` 重读并在配置漂移时进入 `configuration_blocked`（不回退 mock）。
- 向导在本机真实端点上运行：硬件检查通过；**探测到本机已有 ComfyUI 安装并识别出约 41 GB 与固定清单哈希匹配的可复用模型，按"先复用、不重下"呈现**。

**未运行边界（如实登记，不代偿）**：

- 本轮**没有执行真实 H3 模型加载与出片**。理由：该动作需要用户对 41 GB 模型缓存/复用与 GPU 长时占用的明确授权（任务书 2.1 节保留的原有授权机制），且不应在隔离测试数据目录内擅自激活生产配置。因此 `READY/RUNNING` 的端到端体验、真实视频解码与自动交付的实机证据留待用户在真实环境走一次向导后产生；相关软件合同已由 `test_runtime_activation.py`、`test_production_series.py`、`test_offline_e2e.py` 等覆盖。
- 演示任务如实不产生视频文件（`video_created=false`、`acceptance=mock`），成片中心对演示项目显示"暂无交付成片"是正确行为而非缺陷。

## 10. 最终工程 PASS/FAIL 矩阵

| # | 判定条件 | 结果 |
|---|---|---|
| 1 | 普通用户能从 Web 明确找到并完成演示→真实切换 | **PASS**（模式入口+单路径向导；实机验证至复用检测步） |
| 2 | UI 模式、后端有效配置、Worker 执行、任务 provider 一致 | **PASS**（单一 `production_mode` 来源；双端测试） |
| 3 | 不因渲染器空闲停止而误判生产模式失效 | **PASS**（专测 `test_verified_active_runtime_is_ready_even_with_renderer_idle_stopped`） |
| 4 | 默认导演台完成真正的信息架构收束 | **PASS**（双层+四步状态驱动；非仅文案） |
| 5 | 普通流程无需工程 ID/JSON/底层参数 | **PASS**（E2E 全程零工程输入） |
| 6 | H3/资产/风格/质检/租约/审片/交付安全合同保持有效 | **PASS**（既有全套合同测试未动且通过；未削弱任何断言） |
| 7 | 完整后端软件测试无失败 | **PASS**（双目录 1358/0） |
| 8 | 前端测试、TypeScript、正式构建 | **PASS**（42/42、tsc 0、build ok） |
| 9 | 关键导演流程浏览器自动化 | **PASS**（真实浏览器完整流程+刷新一致+分辨率） |
| 10 | 数据库迁移与 `alembic check` | **PASS**（零差异+回归测试） |
| 11 | Windows 一键启动及异常恢复回归 | **PASS**（进程探测/栈操作/包入口测试通过；本轮端口释放干净） |
| 12 | 测试包与最终源码一致 | **PASS**（包自代码终点提交 `06975bc` 的干净树构建，`-Verify` 清单+SHA-256 核对；文档提交不触及任何打包文件） |
| 13 | 结果可核验、不夸大 | **PASS**（本报告 §9 明确区分已验证合同与未运行边界） |

**SHINE VIDEO STUDIO — ENGINEERING ALL PASS（本轮工程范围）**。不含：GitHub 托管 CI（未配置）、真人体验评价、真实 H3 出片与影片艺术质量人工批准（见 §9/§11）。

## 11. 仍然存在的外部依赖与人工确认事项

1. **真实 H3 首次激活**：需要用户在真实环境确认 41 GB 模型复用/下载与激活（向导会逐步征求确认）；激活后建议跑一次单镜真实生成完成端到端取证。
2. **LLM 服务**：AI 分镜/AI 推荐/机器审片需配置可用的模型服务；未配置时相应能力如实显示未就绪。
3. **FFprobe/FFmpeg**：本宿主当前 PATH 缺失导致 1 例真实解码测试跳过；正式机按安装器指引安装后即可运行。
4. **GitHub Actions CI**：仓库当前无托管 CI 配置，本轮以本地双目录全量回归替代（与任务书 7 节口径一致）。
5. 测试包交付后的新机验收（`一键安装并启动.cmd`）仍属人工验收环节。

## 12. 提交、推送与工作树

- 代码终点：`06975bc`（30 文件，+2423/−689，产品与测试改动）+ `ffd26ce`（打包脚本 UTF-8 修复，见下）。
- 本报告与 `docs/evidence/20261102-director-closeout/`（8 张实机截图：作品首页、素材与风格、AI导演制作、成片中心、专业模式、启用真实制作向导、1366×768 与 1280×720 布局检查）随文档提交入库。
- 测试包构建实录：首次构建在中文 GBK 控制台下暴露出打包器真实缺陷——`git ls-files` 的 UTF-8 文件名被按控制台编码解码成乱码，报 "Tracked file is missing on disk"。已按子进程编码合同修复（`ffd26ce`：脚本自行钉住 UTF-8，不依赖调用方控制台）。最终自 `ffd26ce` 干净树构建并通过 `-Verify`：`ShineVideoStudio-20261102-ffd26ce-win64.zip`，416 个受控文件入包 / 109 个排除路径（packaging、docs/evidence 等），展开后 425 个文件逐一 SHA-256 核对通过；包内不含模型、数据库、密钥与证据目录。
- 推送：`git push origin main`（快进），推送后 `git status` 清洁。
