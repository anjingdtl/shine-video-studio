# 测试报告：全片持续生产收尾修复 V3 轮（2026-11-06）

- 被测对象：`main / 3f2705396c32c266b912a729f0c7a06ffd6b86c3` + 本轮改动，现已提交为 `b2c0733` / `b58d9a3`（见第 6 节）。
- 关联文档：`docs/PROGRESS_20261106_DIRECTOR_LONG_FORM_V3.md`、
  `docs/ACCEPTANCE_DIRECTOR_LONG_FORM_CLOSEOUT_V3_20261106.md`。
- 执行环境：Windows，项目 Python 3.12 虚拟环境 `backend\.venv`，仓库根目录执行。
- 证据目录：`.local-acceptance/closeout-v3-20261106/`（Git 忽略，不进入安装包）。

## 1. 结论摘要

| 检查 | 结果 | 退出码 |
| --- | --- | --- |
| 前轮 18 条独立反例 | **18 passed**（8.45s） | **0** |
| 上轮失败的 3 条独立集成反例 | **3 passed**（2.16s） | **0** |
| 完整后端套件 | **1782 passed / 1 skipped / 0 failed**（1296.71s） | **0** |
| Ruff（`backend/shine` + `backend/tests`） | **All checks passed!** | **0** |
| 前端单测（21 文件 / 148 用例） | **148 passed**（59.53s） | **0** |
| 前端构建（`tsc -b` + `vite build`） | **PASS** | **0** |
| 隔离新库 `alembic upgrade head` | head = `f180b0010001` | **0** |
| 隔离新库 `alembic check` | **No new upgrade operations detected** | **0** |

**未执行（NOT_RUN）**：真实 H3 出片、真实多集无人值守交付、8h/24h 长时生产、人工艺术验收、
以及 Windows 安装包在**干净机器上的实际安装与安装后联调**（包本身已构建并通过 `-Verify`，见 2.6 节）。
详见第 5 节。

## 2. 命令与原始结果

### 2.1 独立反例（前轮 18 条，原始探针未改动）

```powershell
backend\.venv\Scripts\python.exe -m pytest .local-acceptance/longform-recheck-20261106/test_recheck.py -q
# 18 passed, 1 warning in 8.45s        EXIT=0
```

文件 SHA-256 `ca245176c94e9daa7dca986636399522f6c5da4080917dc81c35736f77b29b7f`（与上轮记录一致，
**本轮未编辑**）。日志：`original-18-final.log`。

### 2.2 上轮失败的 3 条集成反例

```powershell
backend\.venv\Scripts\python.exe -m pytest .local-acceptance/acceptance-3f27053-20261106/test_new_paths.py -q
# 3 passed, 1 warning in 2.16s         EXIT=0   （修复前：3 failed, EXIT=1）
```

文件 SHA-256 `9bb7fc329d33d3b1d9aab0add6a6c4a1347c21e2fcfd9c4f2cfea6823053d0a1`（**本轮未编辑**）。
日志：`new-path-probes-final.log`。

修复前的原始复现输出（本轮开始时）：

```text
worker ticks= ['run:1', 'run:1', 'run:1', 'run:1'] execution handlers= [] series= ['QUEUED']
creative initial claim expiry= None
creative restart ticks= [None, None] status= RUNNING owner= dead-worker expiry= 已过期
pending delivery ticks= ['run:1', 'run:1', 'run:1', 'run:1'] execution handlers= []
3 failed
```

修复后：

```text
worker ticks= ['run:1', 'series:series-serviced', 'series:series-serviced', 'series:series-serviced']
creative initial claim expiry= 2026-11-06 04:27:48.412319
creative restart ticks= ['creative-script', None] status= NEEDS_REVIEW owner= None expiry= None
pending delivery ticks= ['delivery:1', 'delivery:1', 'delivery:1', 'delivery:1']
3 passed
```

### 2.3 完整后端套件

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests -q -o addopts='' \
  --junitxml=.local-acceptance/closeout-v3-20261106/backend-full-final.xml
# 1782 passed, 1 skipped, 2 warnings in 1296.71s (0:21:36)     EXIT=0
```

JUnit：`backend-full-final.xml`；日志：`backend-full-final.log`。

**基线对照**：上轮独立验收为 **1767 passed / 1 skipped / 0 failed**。
本轮 **1782 = 1767 + 15 条新增正式回归**（7 调度 + 7 创意任务 + 1 真实 Worker 整链）。
净增失败 **0**。

**被跳过的唯一一项**：`test_real_comfyui_starts_behind_the_managed_startup`，需要
`SHINE_REAL_COMFY_PROBE=1` 与本机真实 ComfyUI 安装；本轮未启用真实探测。

**保留的两项既有警告**：Starlette/AnyIO 的弃用提示；`input_assessment.py:30` 的 NULL 主键查询提示。

### 2.4 一次被污染的套件（保留作对照）

本轮**第一次**跑完整套件时，在套件运行期间改动了被 `client_review_identity` 钉住的
`backend/shine/worker.py`，导致 53 条原生 VLM 用例以 `REVIEW_CLIENT_SOURCE_CHANGED_SINCE_LOAD`
变红（13 failed + 40 errors）。

```text
shine/quality/client_identity.py:53: ValueError: REVIEW_CLIENT_SOURCE_CHANGED_SINCE_LOAD
13 failed, 1729 passed, 1 skipped, 40 errors in 1234.93s
```

**这不是产品回归**：`client_identity.py` 在导入时记录被钉文件的源码哈希，每次使用时重新比对；
运行中改动被钉文件按设计使身份失效。单独复跑 `backend/tests/test_managed_startup.py` 为
**14 passed**。最终套件在**无并发改动**下重跑，全绿。该对照日志保留在 `backend-full.log` /
`backend-full.xml`，便于复验者自行判断。

### 2.5 Ruff / 前端 / 迁移

```powershell
backend\.venv\Scripts\python.exe -m ruff check backend/shine backend/tests
# All checks passed!     EXIT=0

# frontend/ 目录
npm.cmd test -- --no-file-parallelism      # 21 files / 148 passed (59.53s)   EXIT=0
npm.cmd run build                          # tsc -b && vite build -> PASS         EXIT=0
#   （保留既有 >500 kB bundle 警告，与基线一致）

# 隔离新库（SHINE_DATA_DIR / SHINE_DATABASE_URL 指向 .local-acceptance/closeout-v3-20261106/web-data）
backend\.venv\Scripts\python.exe -m alembic -c backend/alembic.ini upgrade head   # EXIT=0, head f180b0010001
backend\.venv\Scripts\python.exe -m alembic -c backend/alembic.ini check         # EXIT=0, No new upgrade operations detected
```

### 2.6 Windows 测试包（构建并校验）

```powershell
pwsh -NoProfile -File scripts\build_test_package.ps1 -OutDir releases -Verify
# copied 591 tracked files, skipped 116 excluded paths
# staged 599 files, 8648892 bytes
# verified 600 files by SHA-256 after Expand-Archive        EXIT=0
```

| 项 | 值 |
| --- | --- |
| 包 | `releases/ShineVideoStudio-20261106-b58d9a3-win64.zip`（3 015 520 字节） |
| SHA-256 | `095479a8f42cded1f16446d1311f62b813d5f41add749ba40c6767ec154bb9d1`（`.sha256` 旁文件与重算值三方一致） |
| BUILD_INFO | `source_commit = b58d9a3e06eb2a62b7f6756a9c98da91aa3f43f0`，`source_tree = CLEAN_TREE`，`production_ready = false` |
| 禁入内容 | 数据库 0、模型权重 0、验收证据 0、虚拟环境/`node_modules` 0、生成媒体 0；唯一 `.env` 匹配是受版本控制的 `.env.example` 模板 |
| 历史保留 | `releases/` 共 22 个 zip，上一版未被覆盖 |
| 编码 | `scripts/build_test_package.ps1` UTF-8 带 BOM；`packaging/windows/*.cmd` 五个入口文件纯 ASCII |

**未执行**：干净 Windows 机器上的实际安装、60.8 GB 下载与安装后真实 H3 联调。

## 3. 本轮新增回归的覆盖内容

### 3.1 `backend/tests/test_director_worker_scheduling.py`（7 条）

| 用例 | 断言 |
| --- | --- |
| `test_the_worker_serves_the_series_after_advancing_a_run` | Run 派发批次后**必须**把 tick 让给 series 执行器 |
| `test_the_worker_serves_a_pending_episode_delivery` | 已存在的 PENDING 订单**必须**到达装配执行器 |
| `test_the_run_layer_reports_a_check_as_a_check` | `checked=1` 且 `progressed=0`：检查不是进展 |
| `test_an_existing_pending_order_is_not_new_work` | 复用单不算进展，且订单不被复制 |
| `test_two_runs_take_turns_instead_of_one_starving_the_other` | 两个 Run 都被推进（`updated_at` 轮转） |
| `test_the_run_layer_does_not_swallow_the_ordinary_queue` | Run 层不消费普通队列，也不阻止自己派发 |
| `test_a_run_is_never_completed_before_every_episode_is_delivered` | 部分完成的影片不是 COMPLETED |

### 3.2 `backend/tests/test_creative_script_task_lifecycle.py`（7 条）

| 用例 | 断言 |
| --- | --- |
| `test_a_creative_task_is_claimed_under_a_lease` | 领取带 owner 与到期时间 |
| `test_a_worker_that_dies_before_sending_leaves_a_reconcilable_task` | 过期后由**重启的 Worker 入口**收敛为 NEEDS_REVIEW，**不重放**，不写文档 |
| `test_a_saved_answer_is_reused_instead_of_bought_twice` | 已存回答被复用，**第二次模型调用被拒** |
| `test_a_written_script_is_never_written_twice` | 文档只写一份 |
| `test_two_workers_never_both_own_one_creative_task` | 条件领取只允许一个 owner；旧 owner 写回被拒 |
| `test_a_repeated_request_returns_the_same_task_and_never_replays` | 重复点击同一任务、一次模型调用 |
| `test_the_creative_task_keeps_its_own_draft_artifact` | 回答落成版本化 `CreativeScriptDraft` 产物 |

### 3.3 `backend/tests/test_director_worker_integration.py`（1 条，整链）

3 集 / 6 批次 / 6 镜，**只由 `worker.run_configured_once()` 驱动**。断言：

- Run 达到 `COMPLETED`，且**逐拍检查**没有"提前完成"（任一集未交付时 COMPLETED 即失败）。
- 3 集全部 `DELIVERED`，6 个批次 `PASSED`，6 个 series `COMPLETED/RELEASED`，3 条分集交付单 `DELIVERED`。
- 6 个计划镜头各渲染一次（无漏镜、无重镜），外部提交次数 `prompt_count == 6`（无重复提交）。
- 每集 EDL 只含**自己**的两个镜头（无跨 Run 借片）。
- 每集装出约 12 秒；装配产物 `is_mock=False` 且有真实字节。

**被替换的边界（必须声明）**：物理 ComfyUI 进程与 H3 推理由假 ComfyUI HTTP 服务替代；
GPU 独占准入仍走产品自己的 `ResourceScheduler`，FFmpeg 装配是真实的。
**证据等级只到 OFFLINE_VERIFIED** —— 合成测试图不是真实 MiniMax-H3 产物。

## 4. 浏览器复核（隔离实例，新代码）

隔离数据目录 `.local-acceptance/closeout-v3-20261106/web-data`，隔离 API 8152 / Web 5212；
**复核期间原 8133（PID 12168）/ 5193（PID 26676）栈未被本轮触碰**；它随后自行退出，
本轮用既定入口 `scripts\start_dev.ps1` 重启为 API 8000 / Web 5173（15:38:14，health 200，
schema 已是最新），现在跑的是已提交的修复代码。

| 场景 | 观察 | 结论 |
| --- | --- | --- |
| 双入口 | 入口 A（想法）与入口 B（导入剧本）同页，共用「原文 + 大纲确认」链路 | 通过 |
| 简报版本化 | 第 1/2/3 版各自留痕，确认后才出现「生成剧本」 | 通过 |
| 任务状态展示 | 「剧本任务：正在撰写」+「下一步：请求已经发出，正在等待结果……」 | 通过 |
| **Worker 中断后的核对路径** | 真实 Worker 持有请求时被受控终止（身份已证）→ 租约到期 → 重启的 Worker 收敛为 `NEEDS_REVIEW / CREATIVE_SCRIPT_OUTCOME_UNKNOWN`；界面显示「结果待核对（不会自动重发）」+ 下一步 | **通过** |
| 未知提交不重放 | 挂起端点全程只接受 **1 次**外部请求；文档 0 份、草稿产物 0 个 | 通过 |

证据：`creative-recovery-evidence.txt`、`hang.log`、
`ui-creative-reconciliation-needs-review.png`（SHA-256
`4d9acb3cc1eb16376805adc277cd5d07bffd3357a93ac69885ce3dc5136ab6df`）。

## 5. 未执行的验证（NOT_RUN / BLOCKED）

本轮**没有**执行下列任何一项，也没有把它们记成通过：

- **真实 H3 出片（BLOCKED）**：需要受管 ComfyUI 实例与 H3 权重，本轮未获新增下载/安装授权。
- **真实多集无人值守交付（NOT_RUN）**。
- **8 小时 / 24 小时持续生产（NOT_RUN）**：不得以等待、暂停或空等凑时长；活跃运行时长与成片时长
  须分别报告。
- **人工艺术验收（HUMAN_REVIEW，NOT_RUN）**：技术通过不等于艺术通过。
- **Windows 安装包的干净机安装与安装后联调（NOT_RUN）**：包已从**已提交且干净的树** `b58d9a3` 构建并通过
  `-Verify`（逐文件 SHA-256 复核 600 个文件）；**干净机实际安装、60.8 GB 下载与安装后真实 H3 联调仍未执行**。
- **VLM 能力重新校准（BLOCKED，按设计）**：本轮改动了被 `client_review_identity` 钉住的
  `backend/shine/worker.py`，旧 revision 与被冻结计划按设计失效；真实生产验收前必须重新执行真实
  正负校准、发布新 revision、弃用旧 revision、重新分析并**人工重新冻结**计划，旧证据不得复用。
- **跨 series 任意媒体续接**：仍未实现（沿用上轮已披露的边界），未用它做任何宣传。

## 6. 被测树的最终状态

- HEAD `b58d9a3e06eb2a62b7f6756a9c98da91aa3f43f0`，分支 `main`，**已提交并推送**到 `origin/main`
  （0 ahead / 0 behind），工作树干净。
  - `b2c0733` `fix(director): stop the run layer from starving its own executors`
  - `b58d9a3` `chore(packaging): sync the tester hand-off with the V3 closeout round`
- schema `f180b0010001`（与迁移 head 一致）。
- 提交内容：`AGENTS.md`、`backend/shine/director/creative.py`、`backend/shine/production/runner.py`、
  `backend/shine/worker.py`、`backend/tests/test_director_long_form.py`、
  `frontend/src/api.ts`、`frontend/src/components/ScriptEntryPanel.tsx`；
  新增 `backend/tests/test_creative_script_task_lifecycle.py`、
  `backend/tests/test_director_worker_integration.py`、
  `backend/tests/test_director_worker_scheduling.py`，以及 `docs/` 下的本轮文档；
  安装包同步提交另含 `packaging/windows/START_HERE.md`、
  `packaging/windows/TESTER_CHECKLIST.md`、`scripts/build_test_package.ps1`。
- 新增正式回归文件 SHA-256：
  `test_director_worker_integration.py` = `dd920d404a0c59144e67f102ae1cb9590fa925795e3e5ba9c3d49077c2c30d42`；
  `test_director_worker_scheduling.py` = `c441c9a06b8e1b2ee705303fd8818fccf8e419e4e51f00e2d84323ed072f6c95`；
  `test_creative_script_task_lifecycle.py` = `0cddf5355aba8d469300cc48b0d37d5e5032c6978fe44c05408643a3f26f5768`。
- **正式数据库、个人素材、通过版本、冻结合同、候选资产与历史审计未触碰**；本轮所有测试库均在
  隔离目录。证据：仓库 `.local-data/shine.db` 的最后写入时间是 **2026/11/5 18:36:18**，早于本轮开始。
- 本轮自己启动的隔离服务已按身份核对全部停止（14912 / 15552 / 34296 / 32064）。
- **原有 8133/5193 栈**：15:07 仍在、15:26 已不在；本轮的收尾脚本按 PID + 命令行双重核对，**未触碰**这两个
  PID（唯一相关崩溃是无关的 `AweSun.exe` 远程控制代理）。随后本轮用既定入口 `scripts\start_dev.ps1`
  重启了开发机栈：**API 8000 / Web 5173**，15:38:14 拉起，health 200、schema 已是最新、页面 200，
  现在跑的是**已提交的修复代码**。
