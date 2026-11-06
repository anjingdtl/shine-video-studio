# 《Shine 视频工作台》全片持续生产：收尾修复 V3 轮进度（2026-11-06）

- 基线：`main / 3f2705396c32c266b912a729f0c7a06ffd6b86c3`（本轮开始前重新核对 HEAD 与 status）。
- 上一轮独立验收：`docs/ACCEPTANCE_DIRECTOR_LONG_FORM_3F27053_20261106.md`（结论 **NOT_ACCEPTED**，2 类 P1）。
- 本轮任务入口：`docs/LOCAL_AGENT_DIRECTOR_LONG_FORM_CLOSEOUT_FIX_PROMPT_V3_20261106.md`。
- 本轮分级验收报告：`docs/ACCEPTANCE_DIRECTOR_LONG_FORM_CLOSEOUT_V3_20261106.md`。
- 本轮测试报告：`docs/TEST_REPORT_20261106_DIRECTOR_LONG_FORM_V3.md`。
- 产品范围：`docs/DIRECTOR_WORKFLOW_AND_LONG_FORM_PRODUCTION_PLAN_20261105.md`。

## 当前结论

**上一轮的两个 P1 已修复，核心目标的离线整链已闭合；真实 H3 与长时生产仍未执行。**

- 上轮新增的 3 条独立集成反例：**3 failed → 3 passed**；前轮 18 条独立反例保持 **18 passed**
  （原始探针文件未改动，SHA-256 重新核对一致）。
- 完整后端套件 **1782 passed / 1 skipped / 0 failed**（基线 1767 + 本轮 15 条新增正式回归）。
- 新增**只由常驻 Worker 调度入口驱动**的整链回归：3 集 / 6 批次 / 6 镜 → 逐集真实 FFmpeg 装配 →
  Run COMPLETED。
- **仍不能宣称整体验收通过**：真实 H3 出片、真实多集无人值守交付、8h/24h 长时生产、
  人工艺术验收均为 **NOT_RUN**；Windows 测试包已从**已提交且干净的树**构建并通过 `-Verify`，但**干净机安装未跑**。
- **一个必须公开的后果**：本轮改动了 `backend/shine/worker.py`，该文件在 `client_review_identity` 的
  钉住清单内 → 旧 VLM revision 与被冻结计划**按设计失效**，不能作为当前代码的真实生产证据。

## 本轮修复（均有回归覆盖）

### 1. P1-A：全片推进饿死拍摄与装配执行器

**根因（三处叠加）**：

1. `production/runner.py::advance_run()` 无条件返回 `action="advanced"`，即使本拍只做了一次检查。
2. `advance_runs()` 见到 `advanced` 就计数 → `worker.py::_advance_production_runs()` 返回非零 →
   `_run_configured_once()` 立即 `return "run:N"`，**后面的交付执行器与 series 执行器永远没有机会**。
3. `_ensure_episode_delivery()` 遇到**已存在且摘要相同**的 PENDING/ASSEMBLING/DELIVERED 订单仍返回
   订单 id，被记成 `episode_queued` → 每拍都像有新工作 → 装配执行器同样被饿死。

**附带问题**：`advance_runs` 按 `created_at` 排序且 `limit=1`，最老的 Run 永远排在最前，
**多个 Run 之间没有公平性**。

**修复**：

- 运行层区分**检查过**（`observations`）、**真实状态变化**（`actions`）、**新工作入队**
  （`batch_dispatched` / `episode_queued`）、**复用已有待执行单**（`episode_delivery_pending`）。
  只有真实变化算 `progressed`，也只有真实变化移动 `last_progress_at`（等待不再伪装成工作）。
- `_ensure_episode_delivery()` 返回 `(order_id, created)`；复用单不再进入进度集合。
- `advance_runs()` 返回 `RunAdvanceSummary(checked, progressed, dispatched, queued)`。
- 排序改为 `updated_at ASC, created_at ASC` → **真正的轮转**（每次推进都刷新 `updated_at`）。
- tick 只在 `progressed` 时占用本拍，否则继续走交付、series 与普通队列。

**没有**新增第二个 Worker 平台、旁路脚本或多开 Worker；GPU 串行与租约保护仍在 series 机制内；
**没有**无限轮询、硬编码 sleep、无条件刷新进度时钟或假标 COMPLETED。

**为什么原套件遗漏**：原套件全部**直接调用 `advance_run(...)`**，把"返回了 advanced"当作成功断言。
它验证运行层内部状态机，**从不经过 `advance_run` 与 `_run_configured_once` 之间那一段** ——
缺陷恰好在两者之间，于是"后端全绿"与"常驻 Worker 实际会饿死"可以同时成立。

### 2. P1-B：创意剧本任务的领取、续租、恢复与迟到写回保护

**根因**：`director/creative.py::run_pending_creative_task()` 只扫描 QUEUED；写 RUNNING/lease_owner 时
**不写 `lease_expires_at`**；Worker 恢复入口**不覆盖 `creative_script_run`**；`finish()` 与成功写文档
**没有状态/owner/state_version 三项 fencing**。Worker 崩溃后任务永久停在 RUNNING/dead-worker，
人工补过期时间也没有任何调度入口捡起。

**修复：持久阶段机（`trace.stage` 是权威进度）**

| 阶段 | 含义 | 过期后被重新领取时的动作 |
| --- | --- | --- |
| `DRAFT` | 尚未发送，可证明未发出 | 继续发送 |
| `SENDING` | 发送意图已落库，结果未知 | **不重放**，进 NEEDS_REVIEW（`CREATIVE_SCRIPT_OUTCOME_UNKNOWN`） |
| `RESPONSE` | 回答已落库为 `CreativeScriptDraft` 产物，文档未写 | **复用已存回答**写文档，**不再买第二次模型调用** |
| `DOCUMENT` | 文档已入库 | 只收敛任务状态，**绝不写第二份文档** |

- 领取写 `lease_expires_at`（30 分钟），运行期由 `_renew_creative_claim()` 续租（只认自己的
  `state_version + lease_owner + status`）。
- 领取范围含 QUEUED 与 **RUNNING 且租约已过期**；`lease_expires_at IS NULL` 的历史孤儿同样算过期。
- **发送意图与稳定请求身份在外部调用之前落库**。
- **回答先落库再写文档**：模型返回后先写成版本化 `AgentArtifact`（`CreativeScriptDraft`），
  再写 `ProjectDocument`；两者之间崩溃，重启后从已存回答继续。
- 所有写回走条件 UPDATE 的三项 fencing。**租约失效的旧 owner 迟到写回被拒绝**：不覆盖终态、
  不复活已终结任务、不创建重复 ProjectDocument。文档与任务终态在**同一事务**提交。
- `before_call` 守卫新增所有权校验：任务已不属于本 Worker 时**根本不会把请求发出去**。
- 角色、provider 指纹、同意证据、确认后的简报 hash 仍全部校验；密钥不进数据库、日志或前端。

**为什么原套件遗漏**：原套件只覆盖"不确定结果不重放"，**没有覆盖 Worker 丢失**：既没有
"领取后进程消失"，也没有"过期后由重启的 Worker 入口捡起"，更没有迟到写回。原实现根本没有租约，
也没有恢复入口，所以留下 `RUNNING/dead-worker` 不会让任何原有用例变红。

### 3. 真实 Worker 整链回归（新增）

`backend/tests/test_director_worker_integration.py`：3 集 × 每集 2 镜（每批 1 镜 → 每集 2 个 series
→ 全片 6 个 series），**只由 `worker.run_configured_once()` 驱动到底**。

- **保留为真实产品代码路径**：Worker tick 顺序与公平性、Run 推进与批次派发、series 领取与逐镜顺序、
  依赖镜头的"源作业真实通过才创建"、job/attempt 记录、技术质检（FFprobe）与五道质量门、
  交付单生成与 input digest、**真实 FFmpeg 逐集装配**、Run 的 COMPLETED 判定。
- **被替换的只有渲染边界**：物理 ComfyUI 进程（GPU 租约与 PID/birth/Job-Object 归属证明）与 H3
  推理本身，由假 ComfyUI HTTP 服务替代；**GPU 独占准入仍走产品自己的 `ResourceScheduler`**，
  模型阶段仍然串行。
- **证据等级只到 OFFLINE_VERIFIED**：合成测试图 ≠ 真实 MiniMax-H3 产物。
- 断言：无重复提交（`prompt_count == 6`）、无漏镜（6 个计划镜头各渲染一次）、每集只用自己镜头的
  EDL、每集装出约 12 秒、**全部必需集交付后** Run 才 COMPLETED（逐拍检查"提前完成"）。
- 附带说明：三集内容字节相同，因此三集交付**共用一份产物行** —— 这是 `assembly.py` 的既有幂等行为
  （同一 reel 不重复登记），不是缺陷；逐集正确性改用**每集自己的 EDL** 断言。

### 4. 前端状态展示

创意任务投影新增 `stage` / `reconciliation` / `next_step`，界面能区分**中断、核对、失败**并给出
下一步，不会永远停在"生成中"。

### 5. 浏览器实证（隔离实例，新代码）

隔离数据目录 `.local-acceptance/closeout-v3-20261106/web-data`，隔离 API 8152 / Web 5212，
迁移 head `f180b0010001` 且 `alembic check` 无漂移。**原 8133/5193 栈全程未动**。

- 真实 Worker 持有请求时被**受控终止**（PID/命令行/创建时间已证）→ 租约到期 →
  **重启的 Worker** 把任务收敛为 `NEEDS_REVIEW / CREATIVE_SCRIPT_OUTCOME_UNKNOWN`。
- 界面显示「结果待核对（不会自动重发）」+ 下一步。
- 挂起端点全程只接受 **1 次**外部请求 → **未知提交没有被重放**；未产生文档与草稿产物。

## 当前阻塞

1. **真实 H3 出片（BLOCKED）**：需要受管 ComfyUI 实例与 H3 权重，本轮未获新增下载/安装授权。
   真实生产验收须按「真实部署小样 → 三集多批次自动交付 → 8h → 24h」递进。
2. **8h / 24h 长时生产（NOT_RUN）**：必须有足够真实工作量实跑；**不得**以等待、暂停或空等凑时长；
   活跃运行时长与成片时长分别报告。
3. **VLM 能力重新校准（BLOCKED，按设计）**：`worker.py` 变更使旧 revision 与被冻结计划失效。
   真实生产验收前必须重新执行**真实正负校准**、发布新 revision、弃用旧 revision、重新分析并
   **人工重新冻结**计划；旧证据不得复用。
4. **人工艺术验收（NOT_RUN）**：技术通过不等于艺术通过。
5. **Windows 安装包的干净机安装与安装后联调（NOT_RUN）**：包已构建并通过 `-Verify`，
   干净机实际安装与安装后真实 H3 联调仍未执行。
6. **跨 series 任意媒体续接仍未实现**（沿用上轮已披露的边界）。首版保留"连续链不跨批"策略，
   超出单链资源/时长边界时具名阻塞，不偷偷切断依赖。本轮未扩建，也未用它做任何宣传。

## 证据索引

- 独立反例（原始文件未改动）：
  `.local-acceptance/longform-recheck-20261106/test_recheck.py`
  SHA-256 `ca245176c94e9daa7dca986636399522f6c5da4080917dc81c35736f77b29b7f` → **18 passed**。
  `.local-acceptance/acceptance-3f27053-20261106/test_new_paths.py`
  SHA-256 `9bb7fc329d33d3b1d9aab0add6a6c4a1347c21e2fcfd9c4f2cfea6823053d0a1` → 修复前 **3 failed**，
  修复后 **3 passed**。
- 本轮证据目录：`.local-acceptance/closeout-v3-20261106/`
  （`backend-full-final.log/.xml`、`original-18-final.log`、`new-path-probes-final.log`、
  `creative-recovery-evidence.txt`、`hang.log`、`verification-status.json`、
  `ui-creative-reconciliation-needs-review.png`、隔离数据目录与身份核对式收尾脚本）。
  该目录被 Git 忽略，**不进入安装包**。
- 新增正式回归：
  `backend/tests/test_director_worker_scheduling.py`（7 条）、
  `backend/tests/test_creative_script_task_lifecycle.py`（7 条）、
  `backend/tests/test_director_worker_integration.py`（1 条）。
- 本轮分级验收报告：`docs/ACCEPTANCE_DIRECTOR_LONG_FORM_CLOSEOUT_V3_20261106.md`。
- 本轮测试报告：`docs/TEST_REPORT_20261106_DIRECTOR_LONG_FORM_V3.md`。
- Windows 测试包：`releases/ShineVideoStudio-20261106-b58d9a3-win64.zip`
  SHA-256 `095479a8f42cded1f16446d1311f62b813d5f41add749ba40c6767ec154bb9d1`，
  `-Verify` 逐文件 SHA-256 复核 600 个文件通过；`releases/` 被 Git 忽略，**不进入版本库**，
  上一版 zip 未被覆盖（现共 22 个）。

## 本轮代码与文档变更

**生产代码**

- `backend/shine/production/runner.py` — `_ensure_episode_delivery` 返回 `(id, created)`；
  `advance_run` 诚实区分检查与进展；新增 `RunAdvanceSummary`；`advance_runs` 轮转排序。
- `backend/shine/worker.py` — `_advance_production_runs` 返回 `RunAdvanceSummary`；
  tick 只在 `progressed` 时占用本拍。**该文件在 `client_review_identity` 钉住清单内。**
- `backend/shine/director/creative.py` — 租约/续租/过期回收；阶段机；发送意图与请求身份前置落库；
  `CreativeScriptDraft` 产物；全项 fencing；`stage`/`reconciliation`/`next_step` 投影。

**测试**

- 新增 `backend/tests/test_director_worker_scheduling.py`、
  `backend/tests/test_creative_script_task_lifecycle.py`、
  `backend/tests/test_director_worker_integration.py`。
- 更新 `backend/tests/test_director_long_form.py` 的运行层断言（返回值由整数改为具名摘要，
  **行为不变**：mock 主机与无受管方案时依然不派发任何批次）。

**安装包同步（提交 `b58d9a3`）**

- `scripts/build_test_package.ps1` — `known_blockers` 与 `closed_in_this_build` 静态字段同步为
  本轮事实：审片重校准因 `worker.py` 变更而**变为必需且尚未完成**、真实多集无人值守交付与
  8h/24h 未执行；以及本轮真正关闭的三项。
- `packaging/windows/START_HERE.md` — 新增本轮更新小节，并修正已知限制段落。
- `packaging/windows/TESTER_CHECKLIST.md` — 新增两条可实机验证项：整片运行真正推进并逐集只交付一次；
  故意中断执行者后创意任务进入「待核对」而不是一直「生成中」。

**提交与推送**

- `b2c0733` `fix(director): stop the run layer from starving its own executors`
- `b58d9a3` `chore(packaging): sync the tester hand-off with the V3 closeout round`
- 两者均已推送到 `origin/main`；远端 `main` = `b58d9a3e06eb2a62b7f6756a9c98da91aa3f43f0`，
  0 ahead / 0 behind，工作树干净。

**前端**

- `frontend/src/api.ts`、`frontend/src/components/ScriptEntryPanel.tsx` — 任务阶段与下一步展示。

**文档**

- `docs/ACCEPTANCE_DIRECTOR_LONG_FORM_CLOSEOUT_V3_20261106.md`（本轮分级验收报告）。
- `docs/TEST_REPORT_20261106_DIRECTOR_LONG_FORM_V3.md`（本轮测试报告）。
- `docs/PROGRESS_20261106_DIRECTOR_LONG_FORM_V3.md`（本文件）。
- `AGENTS.md` 追加「2026-11-06 收尾修复 V3 轮」规则。
