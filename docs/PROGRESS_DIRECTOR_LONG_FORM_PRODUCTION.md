# 进度：导演流程重构与全片持续生产

- 文档建立：2026-11-06（Asia/Shanghai）
- 依据：`docs/DIRECTOR_WORKFLOW_AND_LONG_FORM_PRODUCTION_PLAN_20261105.md`
- 执行提示词：`docs/LOCAL_AGENT_DIRECTOR_WORKFLOW_AND_LONG_FORM_PRODUCTION_PROMPT_20261105.md`
- 验收记录：[ACCEPTANCE_DIRECTOR_LONG_FORM_PRODUCTION.md](ACCEPTANCE_DIRECTOR_LONG_FORM_PRODUCTION.md)
- 设计决定：[ADR 0007](adr/0007-director-package-and-long-form-run.md)

## 1. 开工时的真实基线

| 项目 | 实际情况 |
| --- | --- |
| 分支 / HEAD | `main` / `fca6cabf2564187a641342e6ef3276e0e9a47dbc`（`baa6ea1` 是其祖先） |
| 开工时未提交内容 | 仅 `.zcode/` 未跟踪；`scripts/build_test_package.ps1` 当时**没有**未提交修改（提示词里写的是拟定时的观察，已过期） |
| 主方案与本提示词 | 已被上一个提交 `fca6cab` 收录，工作树干净 |
| 后端虚拟环境 | `backend/.venv`（Python 3.12）存在 |
| 本机验收环境 | `SHINE_DATA_DIR=.local-acceptance/live-browser`，provider `comfyui`，`SHINE_SETUP_RECIPE` 指向受管运行配置 |
| 验收库 schema | `f170a0030001`（**未**升级到本轮新增迁移） |
| 验收库内容 | 11 个项目、3 个镜头、3 个 PASSED job + 1 FAILED、1 个 COMPLETED series + 1 个 NEEDS_REVIEW series、15 个 CANDIDATE_READY 素材项 |
| 运行中的进程 | 一个常驻 Worker（`worker-c6ff70ab509b`，pid 21404，phase `idle`，心跳正常），**属于用户环境，本轮未接管、未终止** |
| API / 渲染器 | `127.0.0.1:8000` 与 `127.0.0.1:8188` 均无监听（绕代理直连返回连接失败） |
| 本机硬件 | RTX 5080（16303 MB 显存）、32418 MB 内存、目标盘可用 83.11 GB |

## 2. M0–M6 状态

| 阶段 | 状态 | 已完成 | 未完成 |
| --- | --- | --- | --- |
| M0 基线与合同 | IMPLEMENTED + OFFLINE_VERIFIED | 导演包/分集计划/运行三层数据模型、ADR 0007、状态转换、范围摘要与幂等键 | 无 |
| M1 双入口与六阶段 | IMPLEMENTED + OFFLINE_VERIFIED | 六阶段完成态 `/journey`（服务端推导）、分集方案派生/确认、导演包确认、前端六阶段导航与新面板 | 剧本入口仍复用既有文档导入 + 大纲确认链路，未新增独立的“想法→简报”表单页 |
| M2 机器推荐 | IMPLEMENTED + OFFLINE_VERIFIED + 本机真实只读检测 | 规则化推荐（每卡显存、内存、磁盘、复用量、证据等级）、`POST /setup/deployment-recommendations` | 未跑真实安装/小样，因此没有任何组合被标成 `measured` |
| M3 导演包与全片计划 | IMPLEMENTED + OFFLINE_VERIFIED | 不可变包（根 + 分集清单）、就绪检查、确认、启动幂等、活动 Run 唯一 | 旧项目只读迁移预览未做（旧项目仍可直接新建包） |
| M4 持续生产与恢复 | IMPLEMENTED + OFFLINE_VERIFIED | Worker 同一 tick 推进、一次只派一个 series、批次幂等、局部阻塞、暂停/恢复/取消/对账、预算与磁盘闸门 | 真实多集自动生产、8 小时/24 小时长跑未执行 |
| M5 分集后期与总览 | IMPLEMENTED + OFFLINE_VERIFIED | DeliveryOrder 范围扩展、跨 series 有序 EDL、分页装配、逐集交付、运行总览/事件/阻塞、待审清单 | 字幕变更只重装配的专项用例未单独写；合集（全片拼接）未实现 |
| M6 真实验收与发行 | BLOCKED / NOT_RUN | 只读环境核对、推荐结论、回归 | 渲染器未启动、磁盘低于 H3 组合门槛、无本轮真实生产授权 |

## 3. 已实现的接口

```
GET  /api/v1/projects/{id}/journey
GET  /api/v1/projects/{id}/episode-plans
POST /api/v1/projects/{id}/episode-plans
POST /api/v1/projects/{id}/episode-plans/{version}/confirm
GET  /api/v1/projects/{id}/director-packages
POST /api/v1/projects/{id}/director-packages
GET  /api/v1/director-packages/{id}
GET  /api/v1/director-packages/{id}/episodes
GET  /api/v1/director-packages/{id}/readiness
POST /api/v1/director-packages/{id}/confirm
GET  /api/v1/projects/{id}/production-runs
POST /api/v1/projects/{id}/production-runs
GET  /api/v1/production-runs/{id}
GET  /api/v1/production-runs/{id}/episodes
GET  /api/v1/production-runs/{id}/events
GET  /api/v1/production-runs/{id}/blockers
POST /api/v1/production-runs/{id}/pause|resume|cancel|reconcile
POST /api/v1/setup/deployment-recommendations
```

写操作沿用 same-origin loopback、`X-Shine-Local-Action: 1` 与 local-user 审计。API 只落库，不提交任何外部调用。

## 4. 迁移

`backend/alembic/versions/f180a0010001_long_form_production.py`（`down_revision = f170a0030001`）

- 新增：`director_episode_plans`、`director_package_versions`、`director_package_episodes`、`production_runs`、`production_run_episodes`、`production_run_batches`、`production_run_events`。
- 扩展：`delivery_orders.series_id` 可空 + `scope_kind` / `scope_id` / `input_digest` + 部分唯一索引 `uq_delivery_order_scope_version`。
- 已在隔离临时库验证：`upgrade head`、`alembic check`（无 drift）；旧 head（`f170a0030001`）带 2 条历史交付订单升级后行数、`(series_id, version)` 唯一性、分集范围唯一性全部保持。
- **未对 `.local-acceptance/live-browser/shine.db` 执行迁移**（真实环境数据改动需单独授权）。

## 5. 确切测试命令与结果

```powershell
# 仓库根
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_director_long_form.py -q
# 24 passed
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_deployment_recommendation.py -q
# 10 passed
backend\.venv\Scripts\python.exe -m ruff check backend/shine backend/tests
# All checks passed!

# frontend
npm.cmd run build        # tsc -b + vite build 通过
npx.cmd vitest run --no-file-parallelism   # 19 files / 136 tests passed
```

完整后端套件（2026-11-06 16:50–17:09，18m51s）：**1734 项执行 / 1672 passed / 51 failed + 10 errors / 1 skipped**。
61 项失败全部是本机沙箱的批量删除拦截（`SAFE_DELETE_BULK_CONFIRM_REQUIRED` → `SystemExit: 1`），
按文件单独复跑后 60 项全部通过；剩 1 项（`test_owned_process_group`）因沙箱把 `Path.unlink` 换成回收站实现
而无法断言 `PermissionError`。详见验收文档 §8。**这一结果不记作全绿。**

> 环境注意：`npm.cmd test` 的并行模式在本机沙箱里会触发 vitest worker 缓存写入 `EPERM`（`node-brokered-fs-shim`），导致部分文件被中断。串行模式（`--no-file-parallelism`）稳定通过，因此前端以串行结果为准，并行结果记为 INTERRUPTED。

## 6. 未决问题

1. 本机磁盘可用 83.11 GB，低于 H3 部署组合声明的 180 GB 门槛；推荐接口如实返回 `DISK_INSUFFICIENT`。要跑真实 H3，需要先释放/改到满足门槛的盘。
2. 渲染器（`127.0.0.1:8188`）当前未监听；受管运行配置存在，但启动渲染器属于真实生产动作，本轮未执行。
3. 迁移尚未应用到验收库；在应用之前，旧 Worker 与旧 API 仍应与旧 schema 配套使用。
4. 旧项目“只读迁移预览 → 生成包草案”未实现；当前旧项目可直接新建分集方案与导演包。
5. 合集（把全部已交付分集拼成一条全片）未实现；逐集交付已可用。

## 6.1 浏览器全旅程穿越（2026-11-06 晚）

- 新增 `scripts/journey_longform_browser.py`：真实 Chromium 逐步点击，17 步全通过，截图落 `.local-acceptance/journey-shots/`。
- 内置浏览器的 DOM 快照为空、截图恒超 10 MiB（裁切被忽略），因此改用 Playwright 走同一条真栈；详见验收文档 §9.1。
- 修掉 4 个真实界面缺陷：风格冻结按钮名与说明不符、无分镜可点「生成分集草案」、演示态运行谎称会推进、窄屏步骤条丢失阶段名且按钮换行。
- 前端 20 文件 140 测试与 build 全绿；后端无改动。

## 7. 下一项可执行动作

在本机具备授权与磁盘条件后：迁移验收库 → 启动 API 与 Worker（同一代码版本）→ 在 Web 里确认分集方案与导演包 → 用 2–3 个镜头的合成镜头集跑一次真实 H3 小样，然后才能进入 3 集自动生产与 8 小时持续运行验收。

## 8. 收尾修复轮（2026-11-06 深夜，分支 codex/longform-closeout-20261106）

以第二次独立验收的 18 条反例为入口，完成状态/身份正确性与主需求闭环；细节与证据见
[收尾验收报告](ACCEPTANCE_DIRECTOR_LONG_FORM_CLOSEOUT_20261106.md)。要点：

- **F01–F08 全部修复**：本 Run 产物归属（`run_owned_passed_jobs`）与计划完整范围；暂停/预算传播到子
  series（`control_reason` 记录暂停主体，resume 只恢复本 Run 暂停的）；预算分支 CAS fencing（陈旧对象不能
  复活取消终态）；根 manifest/scope/policy 三哈希校验 + 每次派发重跑 readiness；政策类型化（strict+forbid，
  API 422）并落到每个 job；NEEDS_REVIEW 汇总与公开恢复路径（reconcile/resume）；取消两段式
  （CANCEL_REQUESTED→外部工作落定→CANCELLED）；心跳与真实进展分离。独立反例 18/18 转绿并迁入
  `backend/tests/test_director_long_form_regressions.py`（24 项）。
- **G01 双入口**：`director/creative.py`（版本化 CreativeBrief→确认→creative_script_run 任务，远程文字同意、
  UNKNOWN 不重放→生成普通剧本文档进同一大纲链路）+ 第一阶段 `ScriptEntryPanel`（想法通栏输入 + 导入上传）。
- **G02 先分集**：sections 模式分集计划（按已确认大纲场次）→ `materialize` 落到各场已应用分镜的镜头 →
  导演包冻结记录 `section_ids` 血缘；按镜头分组保留为兼容入口。
- **G03 机器推荐**：`DeploymentRecommendationCard` 真机检测→完整组合推荐（要求/下载/复用/证据等级/具名
  阻塞/可跳过生图），交给既有安装计划逐项确认。
- **G04 后期合同**：`EpisodePostContract`（入出点+字幕版本，音频混音按名拒绝）→ EDL 应用并对照实测媒体
  校验；字幕钉哈希、超长时码拒绝；修订=新交付版本、旧版保留、不重跑 H3。
- **T01–T04**：装配 `AssemblyHeartbeat`（活跃装配不被跨租期抢占、失联可收回、迟到回写被拒）；连续链不跨批
  （边界证明）；`/health` 暴露 schema 身份 + 前端 `SchemaNotice`；journey loading/error/ready 三态与旧文案清理。
- **回归**：后端全量 1768/0/1 既有跳过；ruff 通过；新库与旧库副本迁移到 `f180b0010001`（正式库副本 48 表
  2074 行零变化）；前端 21 文件 148 测试 + build 通过；隔离栈真实 Web 验收（双入口、分集门控、上传、
  真机推荐）完成。
- **未运行（如实）**：真实生图/H3 小样、3 集多批次自动交付、8h/24h 长时与人工艺术验收；跨 series 媒体
  续接为已声明首版边界。改动未提交/未打包（等待授权）。
