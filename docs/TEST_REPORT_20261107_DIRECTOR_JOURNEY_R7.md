# 测试报告：导演模式全旅程第 7 轮（2026-11-07）

R7 代码与 pytest 基线：`bafb241`（第 7 轮改动见 §1）；本次真实续跑从 `main/53acb1f`（`origin/main`）开始，未改源代码。
后端 venv：`backend\.venv`；pytest 均在**仓库根**运行（`backend/tests` 内相对路径依赖仓库根，AGENTS 2026-11-05 已记录）。
全量 pytest 在**沙箱外**运行（沙箱删除守卫 50 次阈值会让后半段假失败，AGENTS 已记录）。

## 1. 本轮代码改动（唯一两处，均已回归）

| 文件 | 改动 |
| --- | --- |
| `backend/shine/agents/crew_prompts.py` | 导演提示词两处补强：①无 `editorial_timing_plan` 时"一 beat 一 shot、禁用 cuts、cut 只折叠更晚的 beat"；②"台词说话方（即使不露面）的主素材必须出现在片段 asset_ids" |
| `backend/tests/test_crew_prompt_contract.py` | 为上述两句各加断言（`test_without_a_cut_table…`、`test_the_director_prompt_states_the_official_reference_rules`） |

> 诊断期曾在 `agents/film.py` 加过环境变量门控的答案转储钩子（`SHINE_DEBUG_DUMP_ANSWERS*`），**已整体还原**，未进入提交；`git diff` 只含上表两文件。

## 2. 测试结果

| 命令（仓库根） | 结果 |
| --- | --- |
| `backend\.venv\Scripts\python.exe -m pytest backend/tests/test_crew_prompt_contract.py backend/tests/test_film_crew.py backend/tests/test_story_section_run.py backend/tests/test_story_preparation.py -q` | **58 passed**，exit 0（日志 `.local-acceptance/r7/_pytest_targeted.log`） |
| `backend\.venv\Scripts\python.exe -m ruff check backend/shine backend/tests` | **All checks passed** |
| `backend\.venv\Scripts\python.exe -m pytest backend/tests -q`（沙箱外） | **1800 passed, 1 skipped, 0 failed，exit 0**（日志 `.local-acceptance/r7/_pytest_full_r7.log`） |

前端：本轮零前端改动，未重跑 `tsc/vitest/build`（上轮 bafb241 已全绿；改动面仅后端提示词与测试）。

## 3. 真实链路验证（非 pytest，作为本轮验收证据）

- 26 场景 + 6 人物素材：真实 Qwen-Image-2.1 出图，候选 `content_sha256` 与磁盘字节逐一比对一致（32/32），VLM 目检（无人影/无文字水印/画质正常）后 promote，全部 `renderer_owned=true`、`offline=false`。
- 宽方案 `12908aec`：真实渲染器 `live_object_info` 校验 `missing_nodes=[]`、`missing_inputs=[]`。
- 分镜链：orders 1–8、10、11 真实 MiniMax 五角色链 + 仲裁 + apply-camera 全通（30 镜头入账）；两处提示词缺陷的修复各自以"同一场景修复前连续失败 ≥3 次、修复后一次通过"验证（S3 / S8）。
- GPU：出图与校验后渲染器均已停止（`outcome=stopped`，`active_processes=0`），显存回基线（~1.4 GiB），8188 无监听。

## 4. 未验证/未跑的

- 真实 H3 多镜 Run（步骤 5）本轮未开始。
- VLM 正负校准（目标 ①）仍未做；本轮未改 `worker.py`/`preproduction.py`，不触发 `client_review_identity` 重校准义务。
- 前端 tsc/vitest/build 未重跑（本轮零前端改动；上轮基线 bafb241 已全绿）。

## 5. 第 8 轮真实续跑（2026-11-07）

### 环境与工作树

- 初始 Git：`main` / `53acb1f` / `origin/main`，工作树干净；SQLite schema `f180b0010001` 与程序 head 一致。
- 使用 `pwsh -NoProfile -File .local-acceptance/_hold_stack.ps1` 启动指定 live-browser 栈；API `/health=200`、Worker online、真实 MiniMax M3.1 Flash 被实际调用。此阶段没有启动 H3 renderer。
- 结束时使用 `backend\.venv\Scripts\python.exe scripts/stop_shine.py --data-dir .local-acceptance/live-browser`；wrapper 返回 `STACK_EXITED`，随后 8000/5173/8188 均无 listener。

### 真实 API 链路

- 订单 9 原 CameraPlan `529c502d-010d-4692-8ca2-f31862b4946c` 的 `apply-camera` 重试得到 422 `FILM_HANDOFF_REJECTED`。本地同构重建 ShotSpec 得到两镜完全相同的 Pydantic 根因：`every dialogue speaker must be listed in characters`；`speaker_id=广播` 未进入 characters，与旧 continuity fact 相冲突。
- 改一条 continuity fact 后创建新身份 task `46468f92-fbdf-4462-8059-7379fcf002a3`，成功产出 CameraPlan `d1e1bf7e-ca43-4104-a770-ca44e4b085c0`；`apply-camera=200`，3 镜入账。
- `.local-acceptance/r7/storyboard.py` 按订单单独执行 12–17、21，得到 4、3、1、4、3、3、4 镜，所有相应 `apply-camera=200`。订单 12 和 14 有一次 `LOCKED_FACTS_CHANGED`，resume 后 DB 终态均为 `SUCCEEDED`；订单 14 后续镜头符合 1-shot 结果。
- 早期只读审计见订单 18、19、20 子任务 `f1022cae…`（editor）、`4d53e72f…`（director）、`b95be68d…`（editor）为 `LLM_TIMEOUT / external_outcome=UNKNOWN`；没有 provider request id、HTTP 状态或响应 hash。一次性 `model_reconcile` grant + `abandon-remote-response` 将旧任务标成 `ABANDONED / UNKNOWN / automatic_replay=false`。当时记录的“没有重发”后来经审计证实不准确，详见 §7.5。
- 旧 task trace `phase=WAITING_PROVIDER`，且没有 provider response id/hash。该 phase 在 `provider.complete(...)` 前记录；`LLM_TIMEOUT` 属于已进入发送阶段但无回复，不能据此证明未发送。后续 `STORY_UNKNOWN_SUBTASK_REPLACED` 事件说明 UI 曾允许新 identity 请求；事件明细及修复见 §7.5。
- 目标恢复后的只读链路审计发现：order 18 的 CameraPlan/ContinuityReport 虽存在，但场记 `needs_review` 且有 2 个 issues，未产生匹配 arbitration；order 19 在 Director timeout 前无 CameraPlan；order 20 修复后的 CameraPlan/ContinuityReport 可 preflight，但 editor UNKNOWN，唯一旧 EditIntentPlan 引用了修复前输入。section materializer 只消费父 task `trace.results` 中的 CameraPlan 和对应 apply receipt，而三个父 task 的 `results` 均空；独立低层 apply-camera 不会把结果写入 section run。没有执行这类会产生孤儿镜头的操作，也没有发起新的模型调用。
- Provider 核对审计只查阅官方文档和本机窗口枚举：ChatCompletion v2 的 `id` 是响应字段，Token Plan API 是剩余额度查询，均不能凭当前无 response id 的超时记录逐条核对调用；没有已打开的浏览器控制台会话。未读取 API key、未登录、未进行任何 provider API 写入或生成调用。
- `POST /api/v1/projects/3707a9ed-eced-435b-b69a-0b1ecdd5f146/episode-plans/1/materialize` 返回 409 `EPISODE_SECTION_NOT_STORYBOARDED`，pending 精确为 ep-04 的三个 section `05916709…`、`a68e9f18…`、`6a4ecc4a…`，均 `STORYBOARD_NOT_APPLIED`。episode plan 仍只有 CONFIRMED v1；v2 未生成。
- 最终 `GET /journey` 为 54 个项目镜头、0 个 planned shots、`plan_materialized=false`；本轮没有导演包、production run、新 H3 attempt 或 delivery order。项目仍保留上一轮唯一真实产物：job `aa8baa09-0c1f-4627-a874-193a1a3647e1` 为 `PASSED`、`quality_verification=real`，artifact SHA-256 `92b862e64eb7b4e31b2961c455503ff1f0b8fc4bf7163f184ea2bef8a718a190`（2,049,538 bytes），`last_qc_result=needs_review`。因此不构成整片完成或任何新镜头的最终验收。

### 本轮测试范围与边界

- 此续跑没有源代码改动，因此本轮没有重新跑 pytest/Ruff、前端构建或迁移检查；第 7 轮已有 `1800 passed, 1 skipped` 等结果仍只是第 7 轮测试证据。
- 真实远程 MiniMax 调用与真实应用分镜属于本机 API/Worker 集成验证；未知 outcome 不是 mock，也不能视为成功。
- 没有进行 H3 推理、视频解码、ffprobe、EDL/交付核对。VLM 正负校准仍未做，`pending_review` / 未完成状态保持原样。
- 订单 18–20 只有在可核对外部 UNKNOWN 的新证据或 `NOT_SENT_PROVEN` 证明出现后，才可继续；目前不得通过改输入另建近似请求绕过。

## 6. 串行模型队列优化与真实 Run 续跑（2026-11-07）

### 自动化回归

| 命令 | 结果 |
| --- | --- |
| `backend\.venv\Scripts\python.exe -m pytest backend/tests -q`（资源修复后、run-batch read-model 小改之前） | **100% 完成，exit 0，1 skipped，0 failed**；Starlette deprecation 与一条既有 SQLAlchemy SAWarning |
| `backend\.venv\Scripts\python.exe -m pytest backend/tests/test_serial_stages.py backend/tests/test_gpu_release_confirmation.py backend/tests/test_comfy_adapter.py backend/tests/test_v13_preparation.py backend/tests/test_production_series.py backend/tests/test_director_long_form_regressions.py -q` | exit 0；覆盖过期 WAITING 回收、ACTIVE reconciliation、异步模型释放确认、safe resume 与 series 回归 |
| `backend\.venv\Scripts\python.exe -m pytest backend/tests/test_director_long_form_regressions.py backend/tests/test_production_series.py -q` | 在恢复队列状态修改后 exit 0 |
| `backend\.venv\Scripts\python.exe -m pytest backend/tests/test_director_long_form_regressions.py -q` | 在最终 read-model 修改后 exit 0；含「series=QUEUED、旧 batch=RUNNING 时 API read model 展示 QUEUED」断言 |
| `backend\.venv\Scripts\ruff.exe check backend/shine/production/runner.py backend/shine/comfyops/scheduler.py backend/shine/comfy/client.py backend/tests/test_director_long_form_regressions.py backend/tests/test_serial_stages.py backend/tests/test_gpu_release_confirmation.py` | **All checks passed** |
| `frontend/`：`npm.cmd test -- --run` | **150 passed，21 files** |
| `frontend/`：`npm.cmd run build` | exit 0；仅 Vite 提示 chunk >500kB |

完整 backend suite 没有在真实 H3 正在跑时再次启动，避免测试过程接触 8188 或打断 renderer；最后两个 runner 状态变更由 targeted regressions 覆盖。当前 live Worker/API 是改动前启动的进程，代码已通过文件级回归，但只有到安全重启边界后才能生效；不为部署代码中断正在执行的外部 H3 job。

### 实际浏览器与 H3 证据

- 内置浏览器 `http://127.0.0.1:5173/#d/run` 对「继续安全可恢复批次」进行真实点击；产品回 toast「已恢复生产」。操作者是所有者授权委托的 `local-user`；数据库 `PRODUCTION_SERIES_RECOVERED_PRE_SUBMIT` 事件记录 actor 与 `external_submission=NOT_SENT_PROVEN`。该按钮本身只恢复有证据未提交的请求；旧 task 的其他显式替换入口问题见 §7.5。
- 浏览器刷新时显示 17/64 镜通过、2/6 批次已通过、0/4 集已交付。随后数据库 05:29 UTC 快照为 Run `e2da69c8-5b28-45b4-83e8-102b7c09a997`=`RUNNING`、19/64 shots passed、3/6 batches passed、delivery 0/4。恰有 1 个 ACTIVE H3 lease、1 个 RUNNING H3 job；另有 2 个 series 队列等待。浏览器当时仍把一些 series=QUEUED 的批次显示成「进行中」；原因是运行进程未载入安全恢复/read-model 修复，不能据此声称 UI 阻塞已全部修完。
- 05:29 UTC `.local-acceptance/r7/serial_run_artifact_verification_20261107.json`：本 Run 已落盘的 19 个 artifact 全部重算 sha256，和 artifact/attempt SHA 一致，bytes 一致，非 mock；`ffprobe` 全为 H.264/1024×576/约24fps，19 次 `ffmpeg -f null -` 均 exit 0 且 stderr 空。仍未交付任何集，尚无 EDL/交付单的最终验收证据。
- MiniMax quota 不影响外部请求状态定义。早先停在 `WAITING_RESOURCE` 的调用可证明尚未触发 provider；三条真实 `WAITING_PROVIDER` 超时仍是 `UNKNOWN`。同一 run 的三次新 identity 请求后来由审计发现，见 §7.5。
- 当前结果是**真实多镜串行制作正在推进，不是全片完成**。目标仍要求最终 64 镜逐项 artifact/SHA/ffprobe/decode、EDL 顺序、episode delivery 与 UI 完成态全部实证；`pending_review` 仍不能写成 `accepted`。VLM 正负校准尚未完成。

## 7. 第 9 轮最终收尾（64 镜 / 4 集，2026-11-07）

本节 supersedes §6 中“19 镜、delivery 0/4”的进行中快照；不改写其历史时间点结果。

### 7.1 真实 H3 与逐镜技术证据

- Run `e2da69c8-5b28-45b4-83e8-102b7c09a997`=`COMPLETED`；64/64 jobs `PASSED`、
  64/64 artifacts real/non-mock；每个落盘 SHA 和字节数与 artifact 及 attempt
  `output_sha256` 一致；H.264 1024×576、24/1 fps `ffprobe` 规格匹配；64 次全片
  `ffmpeg -f null -` 均 exit 0 且 stderr 为空。12/12 batches `PASSED`，4/4 episodes
  `DELIVERED`，0 blocker、0 active series、0 live lease。
- 逐镜身份、SHA、bytes、`ffprobe`、decode 结果在本机忽略文件
  `.local-acceptance/r7/final_run_verification_20261107.json`，有 64 shots / 4 deliveries，
  顶层 `errors=[]`。原始逐镜本机记录不纳入 Git；分集汇总及 EDL digest 见进度 §9。
- EP01：18 镜，157.479329 秒，42,949,685 bytes，成片 SHA-256
  `a48d642b93923bf578f3a2876fa361eb0be3acdfce2fc65bbde49d5ab442cbc7`。
- EP02：15 镜，110.104329 秒，25,032,533 bytes，成片 SHA-256
  `b0ae474fe36fcf039e67b676f8e2adbdaf746d41d41e664ebaec7869c585b40b`。
- EP03：14 镜，111.312663 秒，28,997,482 bytes，成片 SHA-256
  `bc5f239ea7c35a587769835164af2a66b78313b589b9693bd03a9a8eb3afb1a9`。
- EP04：17 镜，124.687663 秒，33,283,273 bytes，成片 SHA-256
  `dc0a9f1b155c8344217cd3155203035c60aad3d98795237f69ce460ff6e1a594`。
- 四份交付的 EDL clip order 与 run manifest 的 expected jobs 逐项一致；`delivery_order_match`、
  `edl_digest_match`、`clip_artifacts_match` 均 true。交付 SHA、EDL payload 原文 SHA、
  input digest 的逐集映射见进度 §9。

### 7.2 浏览器与验收边界

- 内置浏览器真实读取导演 Run 页并打开「成片中心」，显示 4 集交付、64/64 镜完成；
  打开 ep-04 预览后，播放器从加载态显示真实画面。各集仍显示「待你确认」，未代点
  最终艺术确认，`pending_review` 未改成 `accepted`。
- Advisory：ASR 29 条 `UNAVAILABLE`（未配置），VLM 68 条 `UNAVAILABLE`（审片路由
  不可用）；队列排空。VLM 正负 calibration 未做；这些 advisory 不伪装成通过证据。
- Run 完成后经官方 wrapper 停止/重启工作台；空闲状态为 API/Worker online、
  `h3_connected=false`、`renderer_online=false`、`running_jobs=0`、`series_active=0`。

### 7.3 MiniMax M3.1 Flash 限制优化

- MiniMax 官方 API Console 将 `MiniMax-M3.1-Flash` 标为 1M context，并在示例请求中
  使用 `max_completion_tokens=16384`：[MiniMax API Console](https://solutions.minimaxi.com/debug/text)。
- agent 输入 context 和 JSON schema 采用紧凑 JSON 编码；M3.1 Flash 默认/模型 ceiling
  使用 16,384 输出 token。未裁剪语义上下文。此上限参照官方示例预算，不是模型最大值。
- 订单 18–20 历史超时没有 provider response/body、HTTP status、`id`、`base_resp` 或 usage；
  台账只有 `LLM_TIMEOUT / external_outcome=UNKNOWN`。故不能确认是 context overflow，也不能
  用额度图片证明逐请求成功/失败。三条旧 UNKNOWN 后续被显式替换出新 task identity，不能说“未重放”；详见 §7.5。

### 7.4 自动化测试、启动器与迁移检查

| 命令 / 检查 | 结果 |
| --- | --- |
| `backend\\.venv\\Scripts\\python.exe -m pytest backend/tests -q` | exit 0；1,915 项收集，1 skipped、1,914 passed、0 failed；Starlette `BlockingPortal` deprecation、既有 `input_assessment.py` SAWarning |
| MiniMax/context/UNKNOWN/串行资源/series 专项 pytest | exit 0，无失败；Starlette `BlockingPortal` deprecation warning |
| `backend\\.venv\\Scripts\\ruff.exe check backend/shine backend/tests` | All checks passed |
| `frontend/` `npm.cmd test -- --run` | 150 passed，21 files |
| `frontend/` `npm.cmd run build` | exit 0；Vite 提示 JS chunk >500 kB |
| `backend\\.venv\\Scripts\\alembic.exe -c backend\\alembic.ini check` | `No new upgrade operations detected.` |
| `pwsh -NoProfile -File scripts\\verify_stack_operations.ps1` | PASS：custom proxy、NoWorker、重复启动拒绝、Worker restart、verified shutdown |
| 默认端口冲突入口（临时 detached worktree，隔离 `.local-data`） | 8000 被独立监听占用时，`start_dev.ps1` 自动选 API 8001 / Web 5173；两端 HTTP 200；停止后 8001/5173 无 listener，独立 8000 listener 未被碰触 |
| `git diff --check` | 通过 |

另一次 `alembic check -c backend\\alembic.ini` 把全局选项写在子命令后，CLI 返回 usage / exit 1；
改为上表的 `alembic -c backend\\alembic.ini check` 后通过，属于命令参数顺序错误。
启动器半启动残栈（A3）本轮未单独复现；干净官方 wrapper 重启、默认端口冲突和停止检查均有真实证据。
发布包仍需在最终 commit 的 clean tree 上运行 `scripts\\build_test_package.ps1 -OutDir ... -Verify`；
未运行前不得描述为安装包已发布。

## 7.5 UNKNOWN outcome 安全边界修复

- 审计确认三条 `STORY_UNKNOWN_SUBTASK_REPLACED` 事件；actor=`local-user`，旧 task 仍为
  `ABANDONED / LLM_TIMEOUT / UNKNOWN`，后续新 identity 均成功。尽管所有者授权代行，三次替换
  仍违反 `external_outcome=UNKNOWN` 只核对、不重发的门禁；作为本次执行错误如实保留，未删除或
  改写任何 task、artifact、素材或视频。具体 task / audit ids 与限制见进度 §10.1。
- API、Film dispatcher、gateway、单场素材和分镜/大纲前端现拒绝以新身份替换 UNKNOWN；核对路径
  只接受身份链完整的原结果或 `NOT_SENT_PROVEN`，不会在核对时发起 provider 请求。gateway 输入与
  schema 改为紧凑 JSON，MiniMax M3.1 Flash 默认及 ceiling 设为 16,384 completion tokens；不截断
  用户事实或上下文。历史请求报文没有保存，无法确认原超时是否 context overflow。
- 定向回归覆盖 `test_material_scene_safety.py`、`test_story_preparation.py`、
  `test_story_section_run.py`、`test_storyboard.py`、`test_film_unknown_outcomes.py`、
  `test_llm_gateway.py` 和相应前端测试。不得把旧 task 的 `UNKNOWN` 改写成成功/失败。

## 7.6 导演阶段范围与一键排产 UI 修复

- 后端 `/journey` 返回本次确认计划顺序内的 `planned_shot_ids`；新后端断言确认其准确等于已确认
  episode plan 的 shot 顺序。前端分镜计数、一键排产候选和进度摘要分别使用计划镜数、计划 ID
  集合与 production facts，不再把历史 project jobs 合并成当前 Run 计数。
- 现场项目保有 70 个 project shots，本次 episode plan/run 仅含 64 个。内置浏览器真实点击后，
  看到“已应用 64 个计划镜头（项目共 70 镜）”“自动制作全部 64 个镜头”“当前制作列表仅包含
  已确认分集中的 64 镜；项目另有 6 镜不属于本次计划”，以及“64/64 个计划镜头已通过”；任务
  明细仍保留项目历史记录并明确注明不作为本次状态。四集交付页仍显示 4/4，并保留艺术确认状态。
- `backend/tests/test_director_long_form.py` 的范围计数/ID回归通过；`frontend/src/test/directorEntries.test.tsx`
  导演阶段范围交互测试通过。更新后的完整 suite 结果另列 §7.7。

## 7.7 最终回归、栈与发布状态

- 最终验证时使用官方 `scripts\\stop_shine.py --data-dir .local-acceptance\\live-browser` 完成栈停止，
  再用唯一 wrapper `pwsh -NoProfile -File .local-acceptance\\_hold_stack.ps1` 启动；HTTP `/journey`
  的事实为 project shots=70、confirmed plan=64、planned ID count=64、production=64/64；system
  为 Worker 在线、0 pending、0 running、0 active series、renderer offline。工作台空闲，没有视频重跑。
- 此轮源码最终回归取 §7.10；旧 §7.4 数字不代表本轮改动。Windows 发布包未构建，本轮只提交并推送代码与验收文档。
- 开发机环境在一次隔离 worktree 清理过程中误删了 junction 指向的共享 `backend/.venv` 与
  `frontend/node_modules`；分别以锁定 `uv sync --project backend --locked` 和 `npm.cmd ci
  --prefer-offline` 恢复，未触及本机 production DB 或生成媒体。npm 报告 6 项依赖 advisories
  （3 moderate、1 high、2 critical）；没有升级或改写 lockfile。
- 64/64 H3 技术交付不等于艺术 acceptance：四集仍待用户逐集确认；ASR/VLM advisory 不可用，
  VLM positive/negative calibration 未运行。安装包仅在最终提交树通过 build/verify 后才算验证。

## 7.8 外语/无对白人声问题：提示词核查与产品修复

- 以 API 返回的生产 Run episode manifest 确定 64 个 shot IDs，再逐个读取该 Run 的 `PASSED` job、
  最终 `compiled_prompt` 及原 shot spec。26 镜有对白，38 镜无对白。无对白 38/38 份旧 prompt
  都包含显式禁声句，但同时重复了整部作品的全部 locked story facts；其中包括只属于其他镜头的
  “When Shen Yan says ...”语句。另一个广播对白镜头把音频写成“filtered vocalization begins”及
  “muffled, filtered voice begins reading”，超出了 `<d>[Chinese]...` 的原文台词限制。以上是实际
  已送编译 prompt 的冲突/过宽线索，不是对音轨语言的识别结果。具体 job、prompt SHA 和完整说明见
  进度 §12。
- 代码修复：`shine-h3-compiler/1.2.1` 将锁定事实明确标为纯背景连续性信息；无对白时即使语言设置
  缺失也发出 silence contract；有对白时只允许 shot 内显式台词，禁止额外 voice-over/旁白/背景语音，
  并将视觉说明中的 voice/reading 明确绑定到该台词。规则包的固定版本/hash/section 顺序保持不变。
- 质量修复：ASR advisory 对 `basic` / `full` 范围的全部镜头排队。无对白镜头出现识别文本时产生
  `UNEXPECTED_SPEECH_IN_DIALOGUE_FREE_SHOT`；对白镜头出现脚本台词以外的识别文本时产生
  `UNSCRIPTED_SPEECH_IN_DIALOGUE_SHOT`。两者均为 `uncertain`，只交由人工复核；不会制造
  hard failure 或阻断下一镜。交付页显示未配置/缺失/未完成的 ASR 范围，并将 ASR flagged 结果提示复核。
- 真实旧 Run 证据仍是 26 条 ASR `UNAVAILABLE / ASR_ROUTE_NOT_CONFIGURED`，38 条没有 ASR task；
  本轮没有传送音频到云端、重跑 H3、覆盖已交付媒体或改变 `pending_review`。FFprobe/AAC/decode 只证明
  音轨存在和技术完整，不能识别语言。原作品内具体哪几秒为何种语言依然未核实；修复合同已测但尚未用
  新的真实 H3 镜头验证模型服从度。
- 相关回归：`backend/tests/test_h3_compiler.py` 与 `backend/tests/test_advisory_queue.py` 定向结果
  27 passed；前端交付 ASR 提示测试 7 passed。最终全量 suite/build/Ruff/Alembic 与内置浏览器证据
  在本节收尾时更新；此处的 `UNAVAILABLE` 与旧工件情况不得写成已经核验或已修复成片内容。

## 7.9 64 镜分段列表与分页修复

- 根因已用现场 API 复现：默认 `generation-segments` 响应只有 5 条，映射到 Run `e2da69c8-5b28-45b4-83e8-102b7c09a997` 的最后 batch `d1b84083-a9b8-4648-9b14-87b06f4e0d92`。同一 Run 的后台 ledger 显示 4 集、12 batch，shot 数 18+15+14+17=64；不是 H3 缺产 59 镜。
- 后端现在从最新 `ProductionRun` 汇总所有 episode `shot_ids`，以原 batch `series_id` 读取当前选版；前端每页 6 镜并显示页码、范围、总镜数和集序。重拍后的选版请求使用该镜头来源 batch ID。提示说明：PASSED 视觉/声音问题走显式重拍并保留旧版；失败、`NEEDS_REVIEW`、`UNKNOWN` 返回原 Run/job 对账，绝不盲目重发。
- 自动化：分段跨 batch series/多集顺序、retake 证据链与 ASR advisory 的定向后端回归 `12 passed`。`frontend/src/test/directorContinuation.test.tsx` 覆盖 64 镜首/中/末页（11 页、最多 6 卡、末页 4 卡）及单镜重拍；前端全量结果见 §7.10。
- 浏览器真实页面第 2 页显示镜头 7–12，第 11 页显示镜头 61–64；64/64 镜顺序完整。当前 Run 现场两个 passed 镜头因历史 `PENDING_WORKFLOW_MAPPING` 记录显示为不可重拍。审计记录确认其中一条已带 `COMFY_HANDOFF_PRE_SUBMIT_LEASE_RELEASED`，另一条有 pre-submit mapping evidence 且无 `COMFY_SUBMIT_INTENT`；两者的旧 attempt 均无外部 prompt、artifact 或未释放 lease。显式 retake 先把此完整证据链写成 `ExternalJobReconciliation.result=NOT_SENT_PROVEN` 与 audit，再创建新 job；旧 job/attempt 不变。`UNKNOWN` 反例仍以 `409 GENERATION_RUNNING_OR_RECONCILIATION_REQUIRED` 被拒绝，且不会写 `NOT_SENT_PROVEN`。
- 该修复不改数据库 schema、Run、H3 artifact、episode delivery 或 `pending_review` 状态。最终全量 suite/build 和下载命名验证见 §7.10。

## 7.10 最终回归与分集文件名

- 文件下载名根因是 media endpoint 把内容寻址存储路径中的 SHA-256 文件名回传给浏览器。`Content-Disposition` 现在根据 Project title 与 artifact 所属的 ProductionRun episode 生成 `项目名-第N集.mp4`；episode ordinal 从该 run 的实际有序 episode rows 计算，避免假设 position 从 0 或 1 开始。后续装配版本加 `-vN`，非 episode 输出使用 `项目名-成片.mp4`。非法 Windows 文件名字符替换、标题限长。底层 artifact 路径、字节、SHA 与交付 ledger 均未修改。
- `backend/tests/test_director_long_form.py::test_the_episode_is_assembled_from_two_series_and_the_run_completes` 断言真实测试装配 episode 的下载响应名为 `整部作品-第1集.mp4`；加上 `backend/tests/test_delivery_orders.py`，定向运行结果 `20 passed`。首次断言试跑发现测试错误地假设 episode.position 为零基；实现改按该 run 的排序 rows 求序号后复测通过。
- `backend\.venv\Scripts\python.exe -m pytest backend/tests -o addopts='' -q`：exit 0，收集 1,827 项，`1826 passed, 1 skipped, 0 failed`，2 warnings（Starlette `BlockingPortal` deprecation；既有 `input_assessment.py` SQLAlchemy NULL identity warning），用时 1353.19s。
- `backend\.venv\Scripts\ruff.exe check backend/shine backend/tests`：`All checks passed!`。`backend\.venv\Scripts\alembic.exe -c backend\alembic.ini check`：`No new upgrade operations detected.`
- `frontend/` `npm.cmd test -- --run`：`154 passed`，21 files；`npm.cmd run build`：`tsc -b` 与 Vite build exit 0，JS chunk 641.42 kB，仍有 >500 kB 提示。
- 栈操作脚本 `pwsh -NoProfile -File scripts\verify_stack_operations.ps1` exit 0：custom proxy、NoWorker、duplicate refusal、Worker restart 和 verified shutdown；脚本使用隔离临时目录及 mock，不代表真实 H3 验证。之后按唯一入口先 stop 再运行 `.local-acceptance\_hold_stack.ps1`；启动清理并 release 18 条本安装残留 managed-backend 记录，API `8000` / Web `5173` / Worker 在线。真实 Run API 仍为 `COMPLETED`、4/4 集、12/12 批、64/64 镜；`pending_jobs=0`、`running_jobs=0`、`h3_connected=false`。另有 3 个历史 failed、5 个历史 blocked job，未被触碰或重试。
- 内置浏览器在重启后真实显示 4/4 集、64/64 镜，分段分页第 1 页是 1–6/64；重启前翻至第 11 页显示 61–64/64。四个 production artifact 的媒体 GET 响应均为 HTTP 200，`Content-Disposition` 逐一解码为 `夜航船 真实长片 20261106-第1集.mp4`、`...-第2集.mp4`、`...-第3集.mp4`、`...-第4集.mp4`；测试仅读取响应头并关闭流，没有另存四个大文件。
- `git diff --check` 通过；本轮未构建 Windows 发布包或 GitHub Release。64/64 技术交付仍为 `pending_review`，ASR/VLM 不可用，VLM 正负 calibration 未做；无新 H3 重拍，也未提升艺术 acceptance。
