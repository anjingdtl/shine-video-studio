# 全片持续生产：提交 3f27053 独立验收

- 日期：2026-11-06（Asia/Shanghai）。
- 验收对象：`main / 3f2705396c32c266b912a729f0c7a06ffd6b86c3`。
- 依据：`DIRECTOR_WORKFLOW_AND_LONG_FORM_PRODUCTION_PLAN_20261105.md`、收尾修复提示词 V2、前轮 18 条独立反例、当前源码及实际 Worker 入口。
- **结论：NOT_ACCEPTED。前轮 18 条反例全部通过，但新增 3 条集成反例失败，暴露 2 类 P1 阻塞。暂不能接受“确认后由单个常驻 Worker 完成全片拍摄与逐集装配”的交付。**
- 本轮只做独立验收与记录，没有修改业务代码，没有调用真实文本/生图/H3 模型，没有执行下载、安装或正式库迁移。

## 1. P1：全片推进提前返回，拍摄与装配执行器被饿死

位置：

- `backend/shine/worker.py:4049`：`_advance_production_runs` 返回非零后立即返回 `run:{advanced}`，后面的 `run_pending_deliveries` 和 `run_configured_series_once` 不再执行。
- `backend/shine/production/runner.py:1008`、`:1028`：即使只是等待、`changes=[]`，仍返回并计数 `action="advanced"`。
- `backend/shine/production/runner.py:649`、`:965`：已存在且摘要相同的 PENDING 装配单仍返回 ID，再被当作 `episode_queued`；仅过滤空 changes 不能彻底修复。

复现 A：在隔离库通过真实 API 创建并确认导演包、启动包含 2 镜的 Run（每批 1 镜），连续执行 4 次真实 `_run_configured_once`。保留真实 Run 推进与数据库状态机，用 spy 观察下游执行器是否获得调用。

```text
worker ticks = ['run:1', 'run:1', 'run:1', 'run:1']
execution handlers = []
series = ['QUEUED']
```

复现 B：在隔离测试中预先完成两批镜头，生成真实 PENDING 分集交付单，再执行 4 次 Worker tick：

```text
pending delivery ticks = ['run:1', 'run:1', 'run:1', 'run:1']
execution handlers = []
```

影响：Run 可以持续心跳、看似在推进，但它自身创建的拍摄任务或装配单没有机会执行。常驻 Worker 不能形成自动全片闭环；其他排在后面的任务也可能被拖住。重复等待或增加 tick 数不会改变这个调度条件。

测试边界：未伪造 H3 成功；管理配置门控替换为非空测试对象，reviewer 配置和无关恢复替换为空操作，下游真实外部执行器替换为调用 spy。断言只判断“是否获得执行机会”，不声称已执行真实 H3。装配反例预置完成态仅用于构造待装配输入，不作为自动拍摄证据。

修复与复验要求：

1. 同一常驻 Worker 应能在 Run 编排后继续服务其 series/交付执行器；无进展检查不能永久优先于真正待执行工作。
2. 区分检查、首次入队、真正状态变化；已有 PENDING/ASSEMBLING 单不能反复冒充新入队。
3. 必须通过实际 Worker 调度入口验证“派发→series 执行→批次通过→交付执行→Run 完成”，不得只手工调用 `advance_run` 或直接把 series 标为完成。
4. 新增持续等待和已有 PENDING 单两种回归，保留暂停、取消、预算、UNKNOWN 与租约门控。

## 2. P1：创意剧本任务在 Worker 中断后永久 RUNNING

位置：`backend/shine/director/creative.py:339`–`:356`、`:362`–`:368`、`:398`–`:423`。

领取只扫描 QUEUED，写入 RUNNING/lease_owner 时没有设置租约到期时间；当前恢复入口没有覆盖 `creative_script_run`。结束回写也没有任务状态、owner、版本三项 fencing。

独立复现：确认创意简报、真实 API 创建任务，在进入 `call_material_agent` 时注入 `SystemExit` 模拟 Worker 丢失，未发出任何真实 provider 请求。随后明确把任务租约设为已过期，使用新 Worker 的真实 `run_configured_once` 调度两次：

```text
creative initial claim expiry = None
creative restart ticks = [None, None]
status = RUNNING
owner = dead-worker
expiry = 已过期
```

影响：生成剧本任务失去执行者后不能自动核对或退出 RUNNING，用户会持续等待。即使人工补上过期时间，重启 Worker 也不处理。

修复与复验要求：

1. 给任务设置并续租，纳入现有恢复与审计链；不能另起第二套队列。
2. 持久化发送意图与外部结果阶段，区分明确未发送、成功已落库、结果不确定。UNKNOWN 必须核对或进入 NEEDS_REVIEW，不能靠过期自动重放。
3. 成功/失败落库必须校验当前 owner、状态和 state_version；失效执行者不得迟到覆盖，也不得重复生成 ProjectDocument。
4. 覆盖领取后未发送中断、发送后响应丢失、结果落库前中断、旧 owner 迟到回写等窗口，并测试重启后的真实 Worker 入口。

## 3. 独立验证记录

证据目录：`.local-acceptance/acceptance-3f27053-20261106/`。其中源码探针只操作测试临时库；网页使用独立 `web-data/shine.db`。该目录被 Git 忽略，不进入安装包。

原始 18 条反例 SHA-256：`ca245176c94e9daa7dca986636399522f6c5da4080917dc81c35736f77b29b7f`；新增探针 SHA-256：`9bb7fc329d33d3b1d9aab0add6a6c4a1347c21e2fcfd9c4f2cfea6823053d0a1`。日志与截图哈希另存 `verification-status.json`。

| 检查 | 本轮结果 | 证据 |
| --- | --- | --- |
| 前轮原始 18 条反例（未修改） | **18 passed**，9.40s，EXIT=0 | `original-18.log`；原文件位于 `../longform-recheck-20261106/test_recheck.py` |
| 新增 Worker/剧本恢复集成反例 | **3 failed**，2.53s，EXIT=1 | `test_new_paths.py`、`new-path-probes.log` |
| 完整后端套件 | **1767 passed / 1 skipped / 0 failed**，1253.94s，EXIT=0 | `backend-full.log`、`backend-full.xml` |
| Ruff | **PASS**：All checks passed | `python -m ruff check backend/shine backend/tests` |
| 前端测试 | **21 文件 / 148 passed**，59.45s，EXIT=0 | `frontend-tests.log` |
| TypeScript/Vite 构建 | **PASS**，保留 >500 kB bundle 警告 | `frontend-build.log` |
| 新库迁移 | **PASS**，head=`f180b0010001` | `migration.log` |
| Alembic 漂移检查 | **PASS**，No new upgrade operations detected | `alembic-check.log` |

完整后端唯一跳过项：`test_real_comfyui_starts_behind_the_managed_startup`，需要 `SHINE_REAL_COMFY_PROBE=1` 及本地 ComfyUI 安装；本轮未启用真实探测。保留两项警告：Starlette/AnyIO 的既有弃用提示，以及 `input_assessment.py:30` 的 NULL 主键查询提示。全量套件不包含隔离目录内新增的 3 条失败探针，不能以正式套件全绿覆盖这些失败。

命令（除前端命令外均从仓库根运行）：

```powershell
backend/.venv/Scripts/python.exe -m pytest .local-acceptance/longform-recheck-20261106/test_recheck.py -q -s --tb=short
backend/.venv/Scripts/python.exe -m pytest .local-acceptance/acceptance-3f27053-20261106/test_new_paths.py -q -s --tb=short
backend/.venv/Scripts/python.exe -m pytest backend/tests -q -o addopts='' --junitxml=.local-acceptance/acceptance-3f27053-20261106/backend-full.xml
backend/.venv/Scripts/python.exe -m ruff check backend/shine backend/tests
# frontend/ 下
npm.cmd test -- --no-file-parallelism
npm.cmd run build
```

新库迁移在该隔离目录的 `SHINE_DATA_DIR` / `SHINE_DATABASE_URL` 下执行 `alembic -c backend/alembic.ini upgrade head` 与 `check`；未指向正式数据库。

## 4. 真实浏览器复验与需求边界

隔离 API `8151`、Vite `5211`、mock provider、未启动 Worker。创建测试作品“本轮独立验收 3f27053”，不使用个人素材。不是拦截网络响应的 UI 测试。

| 需求 | 本轮观察 | 可接受的结论 |
| --- | --- | --- |
| 输入想法 | 可保存想法和锁定事实、确认简报 v1；点击生成后准确提示 `CREATIVE_SCRIPT_ROLE_NOT_CONFIGURED` | UI 和未配置门控通过；真实剧本生成未运行 |
| 导入现成剧本 | 成功上传合成剧本，显示文件与 SHA 前缀 `0b948298`；素材页自动选择新剧本 | 入口联动通过；不等于全套剧本统筹已验证 |
| 先按剧本分集 | 无已确认大纲时生成分集按钮禁用，提示先确认大纲 | 前置门控通过；正式物化全链未在浏览器执行 |
| 机器推荐 | 实际检测 RTX 5080 / 15.9 GiB、RAM 31.7 GiB、空闲约 322.8 GB；展示 H3、生图组合要求与下载量 | 检测展示通过；推荐明确为“估算，尚未实测” |
| 内嵌导演包、全片执行、逐集装配 | 源码与隔离探针核对，实际 Worker 调度失败 | **BLOCKED**：不能把组件单测或手动推进认作无人值守完成 |
| 素材人工确认、风格冻结 | 本轮未新增正式素材、未冻结风格 | 不产生新的人工确认或艺术验收证据 |

截图：`ui-dual-entry.png`、`ui-recommendation.png`。

## 5. 证据等级、交付与收尾

- **已确认改善**：原始 18 条反例全部复绿；双入口、导入联动、推荐页面与分集前置门控实际可见。
- **NOT_ACCEPTED**：2 类 P1 仍阻断当前核心目标，需修复后重新独立验收。
- **NOT_RUN**：真实文本/生图/H3、三集多批次自动交付、8h/24h 长时生产、真实成片人工验收、Windows 包构建与安装。本轮没有把这些记成通过。
- **明确边界**：任意跨 series 续接仍是建设方已披露的未实现能力；首版连续链留同批的策略与真实长片效果不能混为一谈。
- 修复提交改变了 Worker 等受 `client_review_identity` 钉住的文件，旧 VLM 能力校准与冻结计划不能直接作为当前代码的真实生产证据；后续真实验收遵循现有修订与重新冻结规则。
- 建设方报告 `ACCEPTANCE_DIRECTOR_LONG_FORM_CLOSEOUT_20261106.md` 中“未提交、feature 分支、8141/5201 仍运行”等文字与本轮现场不一致；当前核实为 `main / 3f27053`，8141/5201 未运行。该问题属于交付记录需更新，不替代上述功能阻塞。
- 本轮隔离服务在浏览器复验后按 PID/命令/父进程链核对并收尾；正式 8133/5193 栈和既有 Worker 不由本轮重启、停机或迁移。最终状态与证据摘要见同目录 `verification-status.json`。

修复顺序：先消除实际 Worker 调度饥饿，再补创意任务租约/恢复/迟到回写保护；随后用同一常驻 Worker 完成离线整链与重启核对，最后开展真实小样、多集交付与长时验收。已有通过版本、未知外部任务和人工确认边界必须保留。
