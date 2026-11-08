# 四阶段收尾独立验收 · main@631d9a0

**2026-11-08 · NOT ACCEPTED**

本地和远端 `main` 均指向 `631d9a07c6be5911a88e138bb037da3d369f3d79`，实现提交为 `5b9301924111b8004d658ae6d9f6987a285ceaa5`。上一轮账本记载的「未合并」与回滚指引属于交付时状态。现在切回 `main` 仍是同一实现。验收使用隔离 SQLite、合成输入、mock 或故障替身，以及真实 Chromium 的定向测试。 [实现提交](https://github.com/anjingdtl/shine-video-studio/commit/5b9301924111b8004d658ae6d9f6987a285ceaa5) · [收尾账本](FOUR_STAGE_CLOSEOUT_EVIDENCE_20261108.md)

## 1. 阻断与复现

| 编号 | 级别 | 独立证据与验收条件 |
| --- | --- | --- |
| **R01 · 同一恢复授权可消费两次**（F04） | **P0 · API + Worker 复现** | 用同一个 UNKNOWN task、同一 `expected_state_version` 连发两次 `POST …/new-attempt`。均为 202，创建第 2、3 次尝试，Worker 对替身发送两次。原 task 只写 `superseded_by_task_id`，版本未消费。一次用户决定最多产生一次新 attempt，顺序/并发重复及丢响应后重试都须返回同一回执或具名冲突。 [服务](../backend/shine/director/pipeline.py#L2303) |
| **R02 · 局部重做覆盖整卷回执**（F09） | **P0 · API 复现** | 两场都出草案，应用第 1 场；仅重做第 2 场，卷记录只剩第 2 场。应用第 2 场后交付 422 `PIPELINE_SCENE_WITHOUT_SHOT`。独立场次已应用的回执必须保留，受影响的连续性依赖须有明确状态。 [入口](../backend/shine/api_pipeline.py#L233) · [覆盖写入](../backend/shine/director/pipeline.py#L1642) |
| **R03 · 旧大纲生成的蓝图仍可确认**（F01、F02） | **P0 · API 复现** | Worker 生成蓝图后，在同一分卷计划下替换为新确认大纲，将旧大纲标记 stale。确认旧蓝图仍为 200。确认仅比较计划 hash、当前覆盖是否齐全，未比较生成时与当前的有序大纲 ID/hash。确认、读取与生产须共用当前依赖验证。`bible_basis_equivalent` 目前只有测试调用，正式交付还需验证剧本实际使用的蓝图。 [蓝图确认](../backend/shine/director/pipeline.py#L887) · [等价判断](../backend/shine/director/pipeline.py#L1042) |
| **R04 · 镜头升版后交付命中旧包**（F07、F11） | **P0 · API + revision 注入复现** | 完整 mock 卷首次交付可启动。增加一个 Shot 的 revision，保留 ID 和时长，再次交付返回相同 package、`reused=true`，同时 readiness 含 `SHOT_REVISION_CHANGED`。同一完整快照才能幂等复用。新镜头、绑定或工作流等版本应触发明确的重审或新交付，并保留旧包历史。 [幂等 key](../backend/shine/director/pipeline.py#L1912) |
| **R05 · 正式分集合约无法进入卷交付**（F06） | **P0 · API 复现** | 新建并确认当前 EpisodeScript、应用分镜后交付，422 `extra_forbidden`。`_handoff_episodes` 给 `EpisodePlanPayload` 的单集项加了当前 schema 未声明的 `episode_script_id` 与 `episode_script_sha256`。更新场次并生成新分镜后的探针也报相同错误。现有成功链只验证 `episode_basis=volume_scope`。正式分集必须能交付，场次版本错配必须有单独具名门禁。 [分集投影](../backend/shine/director/pipeline.py#L1769) · [生产合同](../backend/shine/director/contracts.py) · [现有成功链](../backend/tests/test_pipeline.py#L346) |
| **R06 · 缺少分片仍通过生产交接**（F06、F09） | **P0 · API + 故障注入复现** | 注入一场两个分片，让 `#2` 抛 HTTP 422，另外两个单元成功。草案返回 `INCOMPLETE`，应用成功单元后 handoff 仍 201 且 `startable=true`。异常只进入响应 rejections，持久记录丢掉失败分片，交付只比较 section 集合。覆盖必须按预期有序 unit/chunk + revision 对账。 [异常处理](../backend/shine/director/pipeline.py#L1591) · [交付覆盖](../backend/shine/director/pipeline.py#L1865) |
| **R07 · 浏览器刷新后无法继续应用已有分镜**（F09、F11） | **P1 · 真实 Chromium 复现** | 编译后的 React + 临时 FastAPI/SQLite，LLM 为 mock。真实点击「生成分镜草案」出现两个应用按钮，刷新后两按钮消失，DB 中仍有两个有效回执。总览不返回持久分镜、apply 和 handoff 状态，面板只读组件 state。刷新须还原该卷可操作项。 [总览](../backend/shine/api_pipeline.py#L33) · [面板](../frontend/src/components/FourStagePanel.tsx#L25) |
| **R08 · 续跑动作、生产入口和版本选择仍缺口**（F02、F09、F11） | **P1 · 静态** | `reconcileAndRetry` 吞掉对账异常，跳过 UNKNOWN 后仍报「已记录未发送」并请求整卷。四阶段任务缺角色匹配的独立对账入口。分镜仍在同步 HTTP 内循环，卷记录在最后才写。交付后仅显示截短 ID，生产页默认拿最新包。需要服务端可恢复单元和真实对账结果、卷级选择和指定 package 入口，以及分集/卷界确认 UI。 [恢复](../frontend/src/components/FourStagePanel.tsx#L162) · [交付回执](../frontend/src/components/FourStagePanel.tsx#L444) · [生产页选包](../frontend/src/components/DirectorProductionRunPanel.tsx#L115) |

对于 R05，源码显示已确认 EpisodeScript 的选择以 `approved_at` 和卷 ID 为准，当前场次与大纲 revision 没有逐项核验。此项风险需要在 schema 修复后用正负集合同用例验证。历史四阶段包还须回归：缺 `creative_lineage` 的既有包当前不会触发新增核验。保持历史可读，旧四阶段包进入新生产前应重新审查；普通旧导演包保留原有路径。 [分集选择](../backend/shine/director/pipeline.py#L2173) · [包准入](../backend/shine/director/package.py#L418)

## 2. 原清单复核

| 原项 | 结论 |
| --- | --- |
| F01 | 当前卷范围及跨项目过滤通过现有回归；R03 阻断确认链。 |
| F02 | 草稿进入 Worker 通过；正式发布时的蓝图依据复审仍缺，见 R03/R08。 |
| F03、F05 | 在现有隔离回归范围内通过。包括结果事务 fence、孤儿采纳、分镜远程同意守卫与撤回后的零调用。 |
| F04 | 普通 resume 拒绝 UNKNOWN 通过；R01 阻断新尝试授权的单次消费。 |
| F06、F07 | 缺整个场次及编辑后旧包阻断通过；R04/R05/R06 未通过。 |
| F08、F10 | 能力标识与手工卷区间回归通过。真实 128K 能力、跨文件和实际生产输入边界尚未实测。 |
| F09、F11 | 按场切片、选卷二和相同回执复用有覆盖；R02/R04/R06/R07/R08 未通过。 |

## 3. 验证账本

- **独立定向回归** `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_four_stage_closeout.py backend/tests/test_pipeline.py backend/tests/test_creative_output_budget.py backend/tests/test_film_unknown_outcomes.py backend/tests/test_material_text_consent.py -ra`，66 passed。`test_director_worker_scheduling.py` 与 `test_existing_comfy_adoption.py` 单独复跑 14 passed。
- **前端** `npm.cmd test` 26 文件、171 项通过，`npm.cmd run build` 通过。Chromium 定向复现见 R07，四阶段浏览器全旅程待跑。
- **静态与迁移** CI 同口径 Ruff lint、`git diff --check 7081104..HEAD`、Alembic 隔离空库 `upgrade head/current/check` 通过，单头 `f180b0020001`。额外 `ruff format --check` 不通过（全局 252 文件，含此次 7 个受检 Python 文件）。CI 当前运行 lint，格式问题应按此次触及的范围收敛。
- **全量测试口径** 上轮本机日志实读为 1985 passed、1 skipped。本轮独立 `pytest backend/tests -ra` 见 1 个失败标记，确认 P0 后在约半程中止，未取得最终用例汇总。本轮结论以完成的定向回归、反例和远端 CI 为准。
- **远端** `frontend` 与既有 `web-e2e` 成功，Linux `backend` 为 14 failed、1835 passed、137 skipped。旧基线 `7081104` 有相同 14 个失败 node ID，集中在 Windows 安装/硬件资格/服务校验和死 renderer 租约测试。当前 `backend-windows` 采样时仍运行，旧基线 Windows job 有 5 项失败，涉及安装合同、GPU 释放确认及系统盘限制。 [当前 CI](https://github.com/anjingdtl/shine-video-studio/actions/runs/42448890805) · [旧基线 Linux 日志](https://github.com/anjingdtl/shine-video-studio/actions/runs/42441218281/job/128610651533) · [旧基线 Windows 日志](https://github.com/anjingdtl/shine-video-studio/actions/runs/42441218281/job/128610651364)
- **真实生产** `REAL_LLM`、`REAL_H3`、`VOLUME_DELIVERED`、`HUMAN_ACCEPTED` 均为 `NOT_RUN`。本机读检时 `127.0.0.1:8000` 不可连接，常用渲染端口未见监听。当前软件阻断已经成立。真实验收仍需独立实例、合规 Profile 与用户授权、经确认的 GPU lease 和 workflow，证据按层记录。

下一轮执行见 [本机 Agent 二轮修复 prompt](LOCAL_AGENT_FOUR_STAGE_REACCEPT_FIX_PROMPT_20261108.md)。
