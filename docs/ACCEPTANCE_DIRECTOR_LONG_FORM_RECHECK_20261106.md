# 全片持续生产：第二次独立验收报告

- 日期：2026-11-06，Asia/Shanghai。
- 验收基线：`main / 8a4e72b5b9656a0e9c7f3ecd49c0e296fc5e9a17`。开工时唯一未跟踪文件是上一版收尾提示词。
- 依据：[建设方案](DIRECTOR_WORKFLOW_AND_LONG_FORM_PRODUCTION_PLAN_20261105.md)。
- 总体结论：**NOT ACCEPTED**。存在可复现的生产范围/产物选择、控制状态、冻结合同及恢复缺陷；双入口等已确认能力尚未完整交付。
- 本轮未修改产品代码，未修改正式数据库或主素材，未启动真实生图/H3。复现仅使用隔离数据库、合成元数据和 FFmpeg 测试视频。

## 1. 本轮证据与检查范围

证据目录：`.local-acceptance/longform-recheck-20261106/`，通常被 Git 忽略，但在原工作区可读。核心文件：

| 文件 | 内容 |
| --- | --- |
| `verification-status.json` | 最终结果、实际提交、证据 hash、临时服务收尾与验收结论 |
| `test_recheck.py` | 独立反例测试；断言应有的正确行为，当前失败即缺陷证据，不是正常测试套件的一部分 |
| `independent-probes.log` | 18 条独立失败断言、关键观测和短 traceback |
| `existing-targeted.log` | 原有导演长片与推荐专项回归，34 项通过 |
| `backend-full.log` | 本轮完整后端回归原始日志；最终结果见第 6 节 |
| `frontend-tests.log`、`frontend-build.log` | 本轮前端回归与构建 |
| `migration.log`、`alembic-check.log` | 新隔离库升级与 schema drift 检查 |
| `deployment-check.json` | 原栈和隔离栈的路由与数据库版本对照 |
| `ui-missing-idea.png` | “创意与剧本”实际跳到仅支持已有剧本的素材准备页 |
| `ui-shot-first-episode.png` | 没有镜头就不能分集，实际顺序与剧本统筹先分集的方案不一致 |
| `ui-setup-no-combination-recommendation.png`、`ui-setup.txt` | 同版本基础配置只读检测及界面记录 |

源码审查与反例共同使用。旧报告中的通过或阻塞结论没有自动沿用；本轮新 API 和 Web 使用新库、新端口，且未启动 Worker 或 renderer。

最终复现脚本 SHA-256：`ca245176c94e9daa7dca986636399522f6c5da4080917dc81c35736f77b29b7f`。脚本共 15 个测试函数，含一个 4 组参数化测试，共 18 个用例。

## 2. 已复现缺陷：18 条断言归为 8 组

### F01 [P1] 使用非本 Run 的镜头，提前装配并错误完成全片

位置：`production/runner.py` 的 `_current_passed_jobs`、`_ensure_episode_delivery`、`advance_run`；`production/episode_delivery.py` 的 `_passed_jobs`、`build_episode_edl`。

复现：一集两镜、每批一镜，两个镜头都有历史通过产物。启动新 Run，仅完成第一批，再推进。

实际结果：

- 批次状态为 `PASSED / QUEUED`，已经生成分集 `PENDING` 交付订单。
- 执行装配后再推进，Run 变成 `COMPLETED`，第二个必需批次仍是 `QUEUED`。
- 单独向相同镜头添加一个不属于本 Run series item 的更新通过 job，待装配订单选择了这个无关 job。

原因：按 shot/revision 全局查最新通过 job，且只检查已经创建的批次，未验证完整批次计划及本运行的实际产物归属。共 3 条失败断言。

### F02 [P1] 暂停/预算只改 Run，没有阻止子 series 执行

位置：`production/runner.py` 的 `pause_run`、`advance_run`。

实际结果：

- 子 series 原为 `QUEUED`，暂停后 Run=`PAUSED`，series 仍=`QUEUED`。
- 子 series 原为 `RUNNING`，暂停后 Run=`PAUSED`，series 仍=`RUNNING`，没有安全边界暂停请求。
- 墙钟预算已耗尽，Run=`PAUSED/BUDGET_EXHAUSTED`，series 仍=`QUEUED`。

这证明 Run 停止推进不等于已有 series 停止领取或推进。共 3 条失败断言，未用真实 GPU 执行来证明超预算推理。

### F03 [P1] 未受 fencing 保护的预算分支会覆盖取消终态

复现：会话 A 读取一个预算耗尽的旧 Run；会话 B 调用正常取消动作并提交；会话 A 再执行旧对象的推进。

实际结果：已取消 Run 从 `CANCELLED` 被覆盖成 `PAUSED/BUDGET_EXHAUSTED`。预算分支在 `_claim_run` 前写状态，未使用当前状态与版本的条件更新。共 1 条失败断言。

### F04 [P1] 包启动后的身份变化与根 manifest 完整性未阻止执行

位置：`director/package.py` 的 `package_readiness`，以及批次派发路径。

两条独立复现：

1. 包确认并创建 Run 后，改变已登记 workflow 的合法 seed 输入及其 graph hash。readiness 正确返回 `WORKFLOW_PROFILE_CHANGED`、不可启动，但该 Run 下一次推进仍创建了 `QUEUED` series。
2. 改变已确认包根 manifest 内的政策内容、保留原来的根 hash 和 policy hash。readiness 仍为 `startable=true`，没有任何 blocker。

第 2 条是隔离数据库中的完整性破坏测试，不代表存在通过公网修改包的接口。共 2 条失败断言。

### F05 [P1/P2] 冻结返工政策未传到任务，政策参数未做类型和范围校验

实际结果：

- 包明确冻结 `auto_repair=false`、`max_repairs_per_shot=0`，将项目默认自动修复明确设为 true 后，实际生成 Job 的 `max_semantic_retries` 仍为 2。
- 以下政策均获 HTTP 201，而非在 API 边界拒绝：非数字 `batch_shot_limit`，负数 `max_wall_seconds`，负数 `max_repairs_per_shot`，负数 `min_free_gb`。

共 5 条失败断言。已经证明政策传播和校验缺陷，但本轮没有实际执行真实模型返工，也不将“任务允许两次”夸大为已经额外消费两次 GPU。

### F06 [P1] NEEDS_REVIEW 不汇总，问题解除后没有正常恢复路径

位置：`_sync_batch_from_series`、`reconcile_run`、`resume_run`。

实际结果：

- 子 series=`NEEDS_REVIEW` 且已标记资源释放，batch 仍=`QUEUED`，Run 仍=`RUNNING`；该条件下批次不再推进，也不产生相应阻塞状态。
- 另一个失败批次在证据恢复后已经核对为 `PASSED`，但 episode 仍=`BLOCKED`、Run 仍=`NEEDS_REVIEW`；调用现有 reconcile/resume 后仍不能继续。

共 2 条失败断言。恢复步骤不能要求用户直接改数据库，也不能为了继续而重放未知提交。

### F07 [P1] 尚有已提交运行任务时，取消已经宣布完成

复现：隔离元数据中，series/item/job 都处于运行，finalization 尚未收尾；调用 Run 取消。

实际结果：Run 已为 `CANCELLED`，对应 Job 仍为 `RUNNING`。没有真实渲染进程被创建或终止。共 1 条失败断言，证明取消终态与待核外部工作脱节。

### F08 [P2] 空转也更新真实进展时间

复现：派发第一批后，没有任何批次/产物变化，再执行一次 beat。

实际结果：`last_progress_at` 再次前移，掩盖等待或卡住状态。心跳时间和真实业务进展必须分开。共 1 条失败断言。

## 3. 已确认功能缺口

| 编号 | 结论 | 证据与边界 |
| --- | --- | --- |
| G01 | 缺真正的想法入口和统一前期剧本链 | 同版本隔离 Web 新建作品后，点击第 1 阶段跳到素材页，只能选择已有剧本；页面要求去旧制作页上传。旧实施报告也承认缺少想法表单 |
| G02 | 分集发生在分镜之后 | 同版本 UI 的生成分集按钮因无镜头而禁用；`api_director.create_episode_plan` 明确要求已有 shots。它能给现有镜头分组，但不是方案要求的剧本统筹先分集 |
| G03 | 新模型组合推荐未接入前端主流程 | `frontend/src` 未发现推荐 API 的调用；基础配置检测后显示已有安装与固定组件，没有推荐/资源节省/质量偏好的完整组合选择。不是“没有任何检测或复用能力” |
| G04 | 分集 EDL 仍是整条顺序拼接，剪辑/字幕合同未完整固定 | `build_episode_edl` 固定 0 到实际全长；创建 episode 订单未见绑定字幕版本，执行器仅在订单已有字幕 revision 时才烧录。当前证据为源码观察，尚未对非默认剪辑与字幕做失败复现 |

可选全片合集尚未实现是已知边界，可单独排期；双入口、先剧本分集和推荐 UI 都是已确认主需求，不能擅自降为可选。

## 4. 部署核对：旧栈不匹配，新隔离栈路由正常

| 检查 | 原有栈 | 本轮隔离栈 |
| --- | --- | --- |
| API | 8133 | 8137 |
| Web | 5193 | 5197 |
| 数据库 | `.local-acceptance/live-browser/shine.db`，只读核对 | `.local-acceptance/longform-recheck-20261106/web-data/shine.db` |
| Alembic | `f170a0030001` | `f180a0010001` |
| 新导演包/Run/journey 路由 | OpenAPI 中匹配数 0 | OpenAPI 中匹配数 15 |
| 导演包页面 | 上轮浏览器观察 `Not Found` | 本轮正常打开，并明确显示先建立镜头的限制 |

这是部署交付问题，不能据此认定源码根本没实现导演包 API。原有 API/Worker 未重启、原库未迁移。隔离栈没有接入真实模型，也没有以 Mock 冒充真实生产。

另一个独立界面问题仍成立：`App.tsx` 在 journey 请求失败时将值设为 null，首页 fallback 使用“全部选定范围已交付”。上轮原栈已见该文案；本轮对相同代码进行了复核。隔离栈成功返回 journey 时会正确显示尚未完成，不能把两种情况混写。

本轮只读检测看到 F 盘约 322.84 GiB 可用，并发现已有 ComfyUI。旧报告的 83 GB 空间阻塞未直接复用；本轮没有因此宣称既有模型、节点或完整真实生产能力已重新验证。

## 5. 仍需验证的风险，不作为已实跑缺陷

- 长分集装配的租期为 2400 秒，执行路径未见独立 heartbeat 或子进程身份记录；多段累计装配可能超过该租期。需要模拟活跃装配跨租期、双 Worker、进程失联与原子发布的测试，不能只提高超时时间。
- 非默认剪辑入出点、批准字幕、声音设置、纯后期修订不重拍的完整交付路径。
- 真正跨 series 媒体承接、长链、预算恢复、取消后 renderer 所有权和资源核对的综合行为。
- worker 身份钉住文件发生变化后的真实评审校准、重新分析及冻结。
- 真实三集生产、8 小时、24 小时和人工艺术验收。当前先被 P1 正确性问题阻挡，本轮不启动耗时生产。

## 6. 本轮回归结果

| 检查 | 本轮结果 |
| --- | --- |
| 独立反例 | **18 failed / 1 warning / EXIT=1**，这是预期行为断言被产品违反的证据 |
| 原有 long-form + recommendation | **34 项通过 / EXIT=0**；说明原有回归未覆盖上述反例 |
| 前端串行测试 | **20 文件 / 140 passed / EXIT=0** |
| TypeScript + Vite | **通过**；bundle > 500 kB 警告保留 |
| Ruff | **All checks passed** |
| 新隔离库 Alembic upgrade | **到 f180a0010001** |
| 新隔离库 Alembic check | **No new upgrade operations detected** |
| 后端完整套件 | **1731 passed / 1 skipped / 2 warnings / EXIT=0**；单独 collect 核对共 1732 项 |
| 真实生图/H3/长时/艺术验收 | **NOT_RUN** |

复现命令（仓库根，Python 3.12）：

```powershell
backend\.venv\Scripts\python.exe -m pytest .local-acceptance/longform-recheck-20261106/test_recheck.py -q -s --tb=short
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_director_long_form.py backend/tests/test_deployment_recommendation.py -q
backend\.venv\Scripts\python.exe -m pytest backend/tests -q
backend\.venv\Scripts\python.exe -m ruff check backend/shine backend/tests
```

前端目录执行 `npm.cmd test -- --no-file-parallelism`、`npm.cmd run build`。本轮未修改产品实现，不将现有回归通过当作反例已经修复。

完整套件保留 Starlette/AnyIO 弃用告警和 `quality/input_assessment.py` 的空主键 SQLAlchemy 告警，均未被隐藏。本次正常跑完，不沿用上一报告的沙箱失败归因。独立反例在 `.local-acceptance` 中，不包含在 `backend/tests` 全量的 1732 项内，因此两组结果并不矛盾。

## 7. 下一步

先修复 F01–F07 的产物身份、控制与恢复，再补 G01–G04 的必要能力和 F08 的观测语义。独立反例应迁入正式 regression 套件，修复后保持相同的业务断言；不能删除、改成 xfail 或取消隔离/生产门禁来转绿。

后续执行依据本次新版提示词。真实生产之前重新核对提交、数据、模型、工作流、授权与资源状态；历史授权不自动扩大为正式库迁移或长时运行许可。

## 8. 本轮收尾

- 完整后端与前端测试进程均已结束，没有挂起的本轮 pytest。
- 本轮创建的 8137 API、5197 Vite 已按 PID、父进程和命令身份核对后停止；隔离数据库和证据保留供接手复现。
- 浏览器验收临时标签已关闭，三张截图已落盘。
- 原有 8133 API、5193 Vite、原 Worker 未停止、重启或迁移；没有启动 H3 renderer、实际推理或长时任务。
- 本轮仅新增复验报告、V2 提示词与忽略目录内的证据，没有修改业务代码、提交、推送或构建 Windows 发布包。
