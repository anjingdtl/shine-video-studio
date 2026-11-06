# 全片持续生产：收尾修复 V3 交付与分级验收报告

- 日期：2026-11-06（Asia/Shanghai）。
- 任务入口：`docs/LOCAL_AGENT_DIRECTOR_LONG_FORM_CLOSEOUT_FIX_PROMPT_V3_20261106.md`。
- 核实基线：`main / 3f2705396c32c266b912a729f0c7a06ffd6b86c3`（本轮开始前重新核对 HEAD 与 status 一致）。
- 上轮独立验收依据：`docs/ACCEPTANCE_DIRECTOR_LONG_FORM_3F27053_20261106.md`（结论 NOT_ACCEPTED，2 类 P1）。
- 产品范围：`docs/DIRECTOR_WORKFLOW_AND_LONG_FORM_PRODUCTION_PLAN_20261105.md`。
- 本轮改动**未提交、未推送**（无提交/推送授权）。工作树相对基线的改动见第 6 节。

## 1. 结论

- **两个 P1 已修复**：上轮新增的 3 条独立集成反例从 **3 failed** 变为 **3 passed**；前轮 18 条反例保持 **18 passed**（原始探针文件未改动，SHA-256 已重新核对）。
- **整链缺口已补**：新增一条**只由常驻 Worker 调度入口驱动**的整链回归——Web/API 确认导演包 → Run 派发 → series 领取与逐镜拍摄 → 各批次通过 → 分集交付入队 → 真实 FFmpeg 逐集装配 → Run 完成。测试不手工调用 `advance_run`、不手工把 job/series 标为 PASSED、不手工补交付。
- **仍不能宣称整体验收通过**：真实 H3 出片、真实多集无人值守交付、8h/24h 长时生产、人工艺术验收本轮**均未执行**（NOT_RUN）。Windows 测试包已从**已提交且干净的树**构建并通过 `-Verify`，但**未在干净机器上安装**。详见第 5 节的分级结论。
- **一个必须公开的后果**：本轮改动了 `backend/shine/worker.py`，该文件在 `client_review_identity` 的钉住清单内。按既有规则，旧 VLM revision 与被冻结计划**按设计失效**，不能作为当前代码的真实生产证据。

## 2. P1-A：全片推进饿死拍摄与装配执行器

### 2.1 根因

三处叠加，任何一处单独修都不够：

1. `backend/shine/production/runner.py` 的 `advance_run()` **无条件**返回 `action="advanced"`，即使本拍只做了一次"检查"、什么都没变。
2. `advance_runs()` 见到 `action="advanced"` 就计数，于是 `_advance_production_runs()` 永远返回非零；`backend/shine/worker.py` 的 `_run_configured_once()` 见到非零就 `return f"run:{advanced}"`，**后面的 `run_pending_deliveries()` 与 `run_configured_series_once()` 永远得不到执行机会**。
3. `_ensure_episode_delivery()` 在遇到**已存在且摘要相同**的 PENDING/ASSEMBLING/DELIVERED 订单时照样返回订单 id，调用方把它记成 `episode_queued` 动作 → 每拍都被当成"新工作" → 装配执行器同样被饿死。

此外 `advance_runs()` 按 `created_at` 排序且 `limit=1`，最老的 Run 永远排在最前：**多个 Run 之间也没有公平性**。

### 2.2 修复

- `advance_run()` 现在区分四件事：**检查过**（`observations`）、**真实状态变化**（`actions`）、**新工作入队**（`batch_dispatched` / `episode_queued`）、**复用已有待执行单**（`episode_delivery_pending`）。只有前两类里的真实变化才算 `progressed`；没有真实变化时返回 `action="waiting"`，且**不移动 `last_progress_at`**（等待不再伪装成工作）。
- `_ensure_episode_delivery()` 改为返回 `(order_id, created)`，`created=False` 的复用单不再进入进度集合。
- `advance_runs()` 返回新的 `RunAdvanceSummary(checked, progressed, dispatched, queued)`，**只有 `progressed` 才算这一拍有了成果**。
- `advance_runs()` 的排序改为 `updated_at ASC, created_at ASC`：每次推进都会刷新 `updated_at`（Run 的 state_version 变化），因此这是一次**真正的轮转**，而不是永远挑同一行的先到先服务。
- `worker.py` 的 tick 改为 `if run_step.progressed: return f"run:{run_step.progressed}"`。**只做检查的 Run 把这一拍让给 series、逐集装配与普通队列**。
- 没有新增第二个 Worker 平台、没有旁路脚本、没有多开 Worker；GPU 串行与租约保护仍在 series 机制内，未被触碰；没有引入无限轮询、硬编码 sleep、无条件刷新进度时钟或假标 COMPLETED。

### 2.3 为什么原套件遗漏

原套件全部**直接调用 `run_engine.advance_run(...)`**（`backend/tests/test_director_long_form.py:258`），并把"返回了 advanced"当作成功断言。它验证的是**运行层的内部状态机**，从来没有验证**Worker 的 tick 是否把这一拍让给了下游执行器**。换句话说：缺陷发生在 `advance_run` 与 `_run_configured_once` **之间**，而原套件从不经过那一段。
被上轮独立反例抓到的那条路径（`worker._run_configured_once`）在正式套件里没有对应用例，于是"后端全绿"与"常驻 Worker 实际会饿死"可以同时成立。

## 3. P1-B：创意剧本任务的领取、续租、恢复与迟到写回保护

### 3.1 根因

`backend/shine/director/creative.py` 的 `run_pending_creative_task()` 领取时**只扫描 QUEUED**；写入 RUNNING/lease_owner 时**不写 `lease_expires_at`**；Worker 的恢复入口**不覆盖 `creative_script_run`**；`finish()` 与成功写文档都**没有状态/owner/state_version 三项 fencing**。结果：Worker 崩溃后任务永久停在 RUNNING/dead-worker，人工补上过期时间也不会被任何调度入口捡起。

### 3.2 修复

新增持久阶段机，任务的 `trace.stage` 现在是权威进度：

| 阶段 | 含义 | 过期后被重新领取时的动作 |
| --- | --- | --- |
| `DRAFT` | 尚未发送，可证明未发出 | 继续发送 |
| `SENDING` | 发送意图已落库，结果未知 | **不重放**，进入 `NEEDS_REVIEW`（`CREATIVE_SCRIPT_OUTCOME_UNKNOWN`） |
| `RESPONSE` | 回答已落库（`CreativeScriptDraft` 产物），文档未写 | **复用已存回答**写文档，不再消耗一次模型调用 |
| `DOCUMENT` | 文档已入库 | 只收敛任务状态，绝不写第二份文档 |

此外：

- 领取写 `lease_expires_at`（30 分钟），运行期由 `_renew_creative_claim()` 续租；续租只认自己的 `state_version + lease_owner + status`。
- 领取范围含 **QUEUED** 与 **RUNNING 且租约已过期**（`lease_expires_at IS NULL` 的历史孤儿同样算过期）。
- **发送意图在外部调用之前落库**（`stage=SENDING`、`sent_at`、`request_identity`）。
- **回答先落库再写文档**：模型返回后先写成版本化 `AgentArtifact`（`CreativeScriptDraft`），再写 `ProjectDocument`；两者之间崩溃，重启后从已存回答继续，**不再买第二次模型调用**。
- 所有成功/失败写回都走条件 UPDATE 的三项 fencing（`state_version` + `lease_owner` + `status`）。**租约失效的旧 owner 迟到写回被拒绝**，不会覆盖终态、不会复活已终结任务、不会创建重复文档。文档与任务的终态在**同一事务**提交。
- 角色、provider 指纹、同意证据与确认后的简报 hash 仍然全部校验；密钥不进数据库、日志或前端。
- `before_call` 守卫新增所有权校验：任务已不属于本 Worker 时**根本不会把请求发出去**。
- 前端现在能显示**中断、核对、失败与下一步**（`stage` / `reconciliation` / `next_step`），不会永远停在"生成中"。

### 3.3 为什么原套件遗漏

原套件只覆盖了"不确定结果不重放"（`test_an_uncertain_script_call_is_reviewed_and_never_replayed`），**没有覆盖 Worker 丢失**：既没有"领取后进程消失"的用例，也没有"过期后由重启的 Worker 入口捡起"的用例，更没有迟到写回的用例。任务即使留下 `RUNNING/dead-worker` 也不会让任何原有用例变红，因为**原实现根本没有租约，也没有恢复入口**。

## 4. 本轮新增的正式回归（`backend/tests/`）

原探针文件保持原样、可独立复跑；等价且更完整的断言迁入正式套件。

| 文件 | 条数 | 覆盖 |
| --- | --- | --- |
| `test_director_worker_scheduling.py` | 7 | Run 让出 tick、复用 PENDING 单不算新工作、多个 Run 的公平性、普通队列共存、不提前 COMPLETED；含上轮两条独立反例的正式版 |
| `test_creative_script_task_lifecycle.py` | 7 | 租约与续租、发送前中断→核对、已存回答复用且不再买第二次、文档只写一次、两个 Worker 竞争、旧 owner 迟到写回被拒、重复点击返回同一任务 |
| `test_director_worker_integration.py` | 1 | **真实 Worker 调度入口的整链**（见下） |

### 4.1 真实 Worker 整链回归的边界（必须声明）

`test_the_resident_worker_finishes_a_whole_film_without_a_hand`：3 集 × 每集 2 镜（每批 1 镜 → 每集 2 个 series → 全片 6 个 series），**只由 `worker.run_configured_once()` 驱动**。

- **保留为真实的产品代码路径**：Worker 的 tick 顺序与公平性、Run 推进与批次派发、series 领取与逐镜顺序、依赖镜头的"源作业真实通过才创建"、job/attempt 记录、技术质检（FFprobe）与五道质量门、交付单生成与 input digest、**真实 FFmpeg 逐集装配**、Run 的 COMPLETED 判定。
- **被替换的只有渲染边界**：物理 ComfyUI 进程（它的 GPU 租约与 PID/birth/Job-Object 归属证明）与 H3 推理本身，由一个假 ComfyUI HTTP 服务替代；**GPU 独占准入仍走产品自己的 `ResourceScheduler` 台账**，模型阶段仍然串行。
- **证据等级：OFFLINE_VERIFIED。合成测试图 ≠ 真实 MiniMax-H3 产物**，本文件任何内容都不得读作真实成片质量。
- 断言覆盖：无重复提交（`prompt_count == 6`，每镜一次）、无漏镜（6 个计划镜头各被渲染一次）、每集只用自己镜头的 EDL、每集装出约 12 秒、全部必需集交付后 Run 才 COMPLETED（逐拍检查"提前完成"）。
- 附带说明：三集内容字节相同，因此三集交付**共用一份产物行**——这是 `assembly.py` 的既有幂等行为（同一 reel 不重复登记），不是缺陷；因此逐集正确性用**每集自己的 EDL**断言。

### 4.2 复验命令与退出码（本轮实际执行）

全部从仓库根 `F:\ClaudeWorkSpace\projects\shine-video-studio` 运行；后端使用项目 Python 3.12
虚拟环境 `backend\.venv\Scripts\python.exe`。

| 命令 | 结果 | 退出码 |
| --- | --- | --- |
| `backend\.venv\Scripts\python.exe -m pytest .local-acceptance/longform-recheck-20261106/test_recheck.py -q` | **18 passed**（8.45s） | **0** |
| `backend\.venv\Scripts\python.exe -m pytest .local-acceptance/acceptance-3f27053-20261106/test_new_paths.py -q` | **3 passed**（2.16s；修复前为 3 failed） | **0** |
| `backend\.venv\Scripts\python.exe -m pytest backend/tests -q -o addopts='' --junitxml=.local-acceptance/closeout-v3-20261106/backend-full-final.xml` | **1782 passed / 1 skipped / 0 failed**（1296.71s） | **0** |
| `backend\.venv\Scripts\python.exe -m ruff check backend/shine backend/tests` | **All checks passed!** | **0** |
| `frontend/`: `npm.cmd test -- --no-file-parallelism` | **21 文件 / 148 passed**（59.53s） | **0** |
| `frontend/`: `npm.cmd run build` | **PASS**（tsc -b + vite build；保留既有 >500 kB bundle 警告） | **0** |
| 隔离新库 `alembic -c backend/alembic.ini upgrade head` | head = `f180b0010001` | **0** |
| 隔离新库 `alembic -c backend/alembic.ini check` | **No new upgrade operations detected** | **0** |

基线对照：上轮独立验收的完整后端套件为 **1767 passed / 1 skipped / 0 failed**；本轮 **1782** =
1767 + 15 条新增正式回归（7 调度 + 7 创意任务 + 1 真实 Worker 整链）。

**被跳过的唯一一项**：`test_real_comfyui_starts_behind_the_managed_startup`，需要
`SHINE_REAL_COMFY_PROBE=1` 与本机真实 ComfyUI 安装；本轮未启用真实探测。

保留的两项既有警告：Starlette/AnyIO 弃用提示，以及 `input_assessment.py:30` 的 NULL 主键查询提示。

### 4.3 一次被污染的完整套件（保留作对照，未删除）

本轮第一次跑完整套件时**在套件运行期间改动了被 `client_review_identity` 钉住的
`backend/shine/worker.py`**，导致 53 条原生 VLM 用例以
`REVIEW_CLIENT_SOURCE_CHANGED_SINCE_LOAD` 变红（13 failed + 40 errors）。**这不是产品回归**：
`shine/quality/client_identity.py` 在导入时记录被钉文件的源码哈希，每次使用时重新比对，
运行中改动被钉文件按设计使身份失效。最终套件在**无并发改动**下重跑，全绿。该对照日志保留在
`backend-full.log` / `backend-full.xml`，便于复验者自行判断。

### 4.4 Windows 测试包（构建并校验通过）

从**已提交且干净的树** `b58d9a3` 构建（`source_tree = CLEAN_TREE`）。该提交是本轮代码与安装包同步的提交；其后仅追加了记录本包证据的文档提交，不改产品代码：

```powershell
pwsh -NoProfile -File scripts\build_test_package.ps1 -OutDir releases -Verify
# BUILD_EXIT=0
# copied 591 tracked files, skipped 116 excluded paths; staged 599 files, 8648892 bytes
# verified 600 files by SHA-256 after Expand-Archive
```

| 项 | 值 |
| --- | --- |
| 包 | `releases/ShineVideoStudio-20261106-b58d9a3-win64.zip`（3 015 520 字节） |
| SHA-256 | `095479a8f42cded1f16446d1311f62b813d5f41add749ba40c6767ec154bb9d1`（与 `.sha256` 旁文件及重算值三方一致） |
| BUILD_INFO | `source_commit = b58d9a3e06eb2a62b7f6756a9c98da91aa3f43f0`，`source_tree = CLEAN_TREE`，`channel = TEST_BUILD`，`status = PARTIAL`，`production_ready = false` |
| 禁入内容复核 | 数据库 0、模型权重 0、验收证据 0、虚拟环境/`node_modules` 0、生成媒体 0；唯一的 `.env` 匹配是受版本控制的 `.env.example` 模板（无任何真实凭据） |
| 历史保留 | `releases/` 现有 22 个 zip，**上一版未被覆盖** |
| 编码 | `scripts/build_test_package.ps1` 为 UTF-8 **带 BOM**；`packaging/windows/*.cmd` 五个入口文件均为**纯 ASCII** |

安装包的 `known_blockers` 与 `closed_in_this_build` 静态字段已同步为本轮事实：审片重校准**因 `worker.py` 变更而变为必需且尚未完成**、真实多集无人值守交付与 8h/24h 未执行，以及本轮真正关闭的三项。

**仍未执行**：干净 Windows 机器上的实际安装、60.8 GB 下载、安装后真实 H3 联调。

## 5. 分级验收结论

| 层级 | 状态 | 依据 |
| --- | --- | --- |
| `IMPLEMENTED` | **通过** | 两个 P1 的代码修复；租约/续租/恢复/迟到写回保护；`RunAdvanceSummary` 与调度公平性；前端 `stage`/`next_step` 展示 |
| `OFFLINE_VERIFIED` | **通过** | 18 条原反例 + 3 条新反例 + 15 条新增正式回归 + 真实 Worker 整链（3 集 / 6 批次 / 6 镜）+ 后端全量套件 + Ruff + Alembic + 前端 148 测试与构建 |
| `REAL_DEPLOYMENT_VERIFIED` | **未变（沿用上轮）** | 本轮未下载、未安装、未改机器部署 |
| `REAL_IMAGE_VERIFIED` | **未变（沿用上轮）** | 本轮未调用真实生图 |
| `REAL_H3_VERIFIED` | **NOT_RUN** | 本轮**没有**一次真实 MiniMax-H3 出片。本机真实渲染链需要受管 ComfyUI 实例与 H3 权重，本轮未获新增授权、未下载 |
| `LONG_RUN_VERIFIED` | **NOT_RUN** | 8 小时 / 24 小时真实持续生产**未执行**。没有任何脚本用等待或暂停凑时长 |
| `HUMAN_REVIEW` | **NOT_RUN** | 未做人工艺术验收；技术通过不等于艺术通过 |
| `WINDOWS_PACKAGE` | **构建并校验通过；安装未跑** | 从已提交且干净的树 `b58d9a3` 构建，`-Verify` 逐文件 SHA-256 复核 600 个文件；**干净机安装与安装后联调本轮未执行** |

### 5.1 真实用户旅程的浏览器复核（隔离实例，新代码）

隔离数据目录 `.local-acceptance/closeout-v3-20261106/web-data`，隔离 API 8152 / Web 5212，迁移 head `f180b0010001` 且 `alembic check` 无漂移。**原 8133/5193 栈全程未动**（PID 12168 / 26676 复核仍在监听）。

| 旅程 | 观察 | 结论 |
| --- | --- | --- |
| 双入口 | 入口 A（想法）与入口 B（导入剧本）同页并存，共用「原文 + 大纲确认」链路 | 通过 |
| 简报版本化与确认 | 第 1/2/3 版各自留痕，确认后才有「生成剧本」 | 通过 |
| 剧本任务状态展示 | 「剧本任务：正在撰写」+「下一步：请求已经发出，正在等待结果……」 | 通过（本轮新增） |
| **Worker 中断后的核对路径** | 真实 Worker 持有请求时被**受控终止**（身份已证：PID/命令行/创建时间），租约到期后**重启的 Worker** 把任务收敛为 `NEEDS_REVIEW / CREATIVE_SCRIPT_OUTCOME_UNKNOWN`；界面显示「结果待核对（不会自动重发）」+ 下一步 | **通过（本轮核心修复）** |
| 未知提交不重放 | 挂起端点全程只接受 **1 次**外部请求；未产生文档、未产生草稿产物 | 通过 |

证据：`.local-acceptance/closeout-v3-20261106/creative-recovery-evidence.txt`、`hang.log`、
`ui-creative-reconciliation-needs-review.png`（SHA-256
`4d9acb3cc1eb16376805adc277cd5d07bffd3357a93ac69885ce3dc5136ab6df`）。
截图里顶部显示「工作台后端失联」是**预期**的：截图拍摄时本轮的隔离 API 已按收尾要求停止；
画面中的任务状态与下一步来自停止前的真实响应。

### 5.2 建设方案其余链路

本轮**未改动**下列既有链路，其状态沿用上轮验收并由本轮全量回归继续覆盖：

- 剧本统筹产生素材需求 → 已有/上传/生成候选混用 → **逐项人工确认后晋升** → 用户冻结风格（AI 不代替批准）。
- 确认大纲 → 按叙事边界先分集 → 各场次分镜实际应用 → 物化成镜头 → 冻结导演包（`materialize` 只认**已应用**分镜的镜头）。
- 内嵌导演包直接启动生产；进度区区分等待、阻塞、拍摄、装配、交付与艺术待审；页面切换不取消后台工作。
- 机器检测 → 明确视频/生图组合及复用/下载量 → 用户确认安装计划 → 实际可用性验证（未实测组合只标 `estimated`）。
- 剪辑入出点与字幕修订走当前合同，新交付版本保留旧版且**不重跑 H3**。
- **跨 series 任意媒体续接仍未实现**（上轮已披露的未实现能力）；首版保留"连续链不跨批"策略，超出单链资源/时长边界时具名阻塞，不偷偷切断依赖。本轮未扩建该能力，也未用它做任何宣传。

## 6. 本轮改动清单与最终状态

### 6.1 生产代码

- `backend/shine/production/runner.py` — `_ensure_episode_delivery` 返回 `(id, created)`；`advance_run` 诚实区分检查与进展；新增 `RunAdvanceSummary`；`advance_runs` 轮转排序。
- `backend/shine/worker.py` — `_advance_production_runs` 返回 `RunAdvanceSummary`；tick 只在 `progressed` 时占用本拍。（**该文件在 `client_review_identity` 钉住清单内**）
- `backend/shine/director/creative.py` — 租约/续租/过期回收；`DRAFT/SENDING/RESPONSE/DOCUMENT` 阶段机；发送意图与请求身份前置落库；`CreativeScriptDraft` 产物；全项 fencing；`stage`/`reconciliation`/`next_step` 投影。

### 6.2 测试与前端

- 新增 `backend/tests/test_director_worker_scheduling.py`、`backend/tests/test_creative_script_task_lifecycle.py`、`backend/tests/test_director_worker_integration.py`。
- 更新 `backend/tests/test_director_long_form.py` 的运行层断言（返回值语义由整数改为具名摘要，行为不变：mock 主机与无受管方案时依然 **不派发任何批次**）。
- `frontend/src/api.ts`、`frontend/src/components/ScriptEntryPanel.tsx` — 任务阶段与下一步的展示。

### 6.3 最终现场

- HEAD：`b58d9a3e06eb2a62b7f6756a9c98da91aa3f43f0`（`b2c0733` 代码 + `b58d9a3` 安装包同步），分支 `main`，
  **已提交并推送**到 `origin/main`（0 ahead / 0 behind），工作树干净。
- schema：`f180b0010001`（与迁移 head 一致，`alembic check` 无漂移）。
- 进程：本轮自己启动的隔离服务（API 34296 / Web 32064 / Worker 30180→14912 / 挂起端点 15552）**全部按身份核对后停止**；原 8133（PID 12168）与 5193（PID 26676）**未重启、未停机、未迁移**。
- 数据库：正式库、个人素材、通过版本、冻结合同、候选资产与历史审计**未触碰**；本轮所有测试库均为隔离目录。

## 7. 剩余阻塞与下一步

1. **真实 H3 出片（BLOCKED）**：本机真实渲染需要受管 ComfyUI 实例与 H3 权重，本轮未获新增下载/安装授权。需用户授权后按「真实部署小样 → 三集多批次自动交付 → 8h → 24h」递进。
2. **8h / 24h 长时生产（NOT_RUN）**：必须在有足够真实工作量时实跑，不得以等待、暂停或空等凑时长；活跃运行时长与成片时长分别报告。
3. **VLM 能力重新校准（BLOCKED，按设计）**：`worker.py` 变更使旧 revision 与被冻结计划失效。真实生产验收前必须重新执行**真实正负校准**、发布新 revision、弃用旧 revision、重新分析并**人工重新冻结**计划；旧证据不得复用。
4. **人工艺术验收（NOT_RUN）**：技术通过不等于艺术通过；需人工集中审阅跨集身份、服装、对白、连续性、字幕与剪辑。
5. **Windows 安装包的干净机安装与安装后联调（NOT_RUN）**：包本身已从已提交且干净的树 `b58d9a3` 构建并通过 `-Verify`（见 4.4 节），但**在干净 Windows 机器上的实际安装、60.8 GB 下载与安装后真实 H3 联调仍未执行**。
6. **提交与推送（本轮已按用户指示完成）**：`b2c0733`（代码、测试、报告）与 `b58d9a3`（安装包静态字段与测试者文档同步）已推送到 `main`；远端 `main` = `b58d9a3e06eb2a62b7f6756a9c98da91aa3f43f0`。

### 7.1 下一次独立验收的最短启动步骤

```powershell
# 0) 基线核对
git -C F:\ClaudeWorkSpace\projects\shine-video-studio rev-parse HEAD   # 期望含 b2c0733 / b58d9a3 的 main 顶端（基线 3f27053 已是上一轮）

# 1) 独立反例（原文件不得修改）
backend\.venv\Scripts\python.exe -m pytest .local-acceptance/longform-recheck-20261106/test_recheck.py -q
backend\.venv\Scripts\python.exe -m pytest .local-acceptance/acceptance-3f27053-20261106/test_new_paths.py -q

# 2) 正式回归（从仓库根）
backend\.venv\Scripts\python.exe -m pytest backend/tests -q
backend\.venv\Scripts\python.exe -m ruff check backend/shine backend/tests
# frontend/ 下：npm.cmd test -- --no-file-parallelism ; npm.cmd run build

# 3) 新库迁移
#   用隔离 SHINE_DATA_DIR / SHINE_DATABASE_URL 执行
#   backend\.venv\Scripts\python.exe -m alembic -c backend/alembic.ini upgrade head
#   backend\.venv\Scripts\python.exe -m alembic -c backend/alembic.ini check
```

## 8. 证据目录

`.local-acceptance/closeout-v3-20261106/`（被 Git 忽略，不进入安装包）：

- `backend-full-final.log` / `backend-full-final.xml` — 最终完整后端套件。
- `backend-full.log` / `backend-full.xml` — 第一次（并发受污染的）完整套件，**保留作对照**：其 53 个失败全部源于我在套件运行期间改动了被钉住的 `worker.py`（`REVIEW_CLIENT_SOURCE_CHANGED_SINCE_LOAD`），不是产品回归；最终套件在无并发改动下重跑。
- `creative-recovery-evidence.txt` — 创意任务台账快照（中断前后、终态）。
- `hang.log` — 挂起端点只接受 1 次外部请求的证据。
- `api.log` / `web.log` / `worker.log` / `worker2.log` — 隔离实例日志。
- `web-data/` — 隔离数据目录（含 `shine.db`）。
- `stop_isolated.ps1` / `stop_orphan_test_servers.ps1` — 本轮临时服务的**身份核对式收尾脚本**。

原始探针 SHA-256（重新核对，未改动）：
`test_recheck.py` = `ca245176c94e9daa7dca986636399522f6c5da4080917dc81c35736f77b29b7f`；
`test_new_paths.py` = `9bb7fc329d33d3b1d9aab0add6a6c4a1347c21e2fcfd9c4f2cfea6823053d0a1`。

本轮新增正式回归 SHA-256：
`test_director_worker_integration.py` = `dd920d404a0c59144e67f102ae1cb9590fa925795e3e5ba9c3d49077c2c30d42`；
`test_director_worker_scheduling.py` = `c441c9a06b8e1b2ee705303fd8818fccf8e419e4e51f00e2d84323ed072f6c95`；
`test_creative_script_task_lifecycle.py` = `0cddf5355aba8d469300cc48b0d37d5e5032c6978fe44c05408643a3f26f5768`。