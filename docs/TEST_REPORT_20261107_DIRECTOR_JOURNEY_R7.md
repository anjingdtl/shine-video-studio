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
- 订单 18、19、20 父任务分别以 `NEEDS_REVIEW / REJECTED` 结束。子任务 `f1022cae…`（editor）、`4d53e72f…`（director）、`b95be68d…`（editor）分别 `LLM_TIMEOUT` 且 `external_outcome=UNKNOWN`；没有 provider request id、HTTP 状态、响应 hash，自动重放均为 false。每条均按一次性 `model_reconcile` grant + `abandon-remote-response` API 由受托 `local-user` 明确放弃丢失响应，返回 `ABANDONED / UNKNOWN / automatic_replay=false`，没有重发。
- 本续跑后的只读审计仍见三条 `ABANDONED / LLM_TIMEOUT / UNKNOWN`，trace `phase=WAITING_PROVIDER`，没有 provider response id/hash。源码中该 phase 在 `provider.complete(...)` 前记录；`LLM_TIMEOUT` 属于发送阶段超时且响应未知，不能据此证明未发送。未发现可按 `NOT_SENT_PROVEN` 恢复的证据，没有重试。
- 目标恢复后的只读链路审计发现：order 18 的 CameraPlan/ContinuityReport 虽存在，但场记 `needs_review` 且有 2 个 issues，未产生匹配 arbitration；order 19 在 Director timeout 前无 CameraPlan；order 20 修复后的 CameraPlan/ContinuityReport 可 preflight，但 editor UNKNOWN，唯一旧 EditIntentPlan 引用了修复前输入。section materializer 只消费父 task `trace.results` 中的 CameraPlan 和对应 apply receipt，而三个父 task 的 `results` 均空；独立低层 apply-camera 不会把结果写入 section run。没有执行这类会产生孤儿镜头的操作，也没有发起新的模型调用。
- `POST /api/v1/projects/3707a9ed-eced-435b-b69a-0b1ecdd5f146/episode-plans/1/materialize` 返回 409 `EPISODE_SECTION_NOT_STORYBOARDED`，pending 精确为 ep-04 的三个 section `05916709…`、`a68e9f18…`、`6a4ecc4a…`，均 `STORYBOARD_NOT_APPLIED`。episode plan 仍只有 CONFIRMED v1；v2 未生成。
- 最终 `GET /journey` 为 54 个项目镜头、0 个 planned shots、`plan_materialized=false`；本轮没有导演包、production run、新 H3 attempt 或 delivery order。项目仍保留上一轮唯一真实产物：job `aa8baa09-0c1f-4627-a874-193a1a3647e1` 为 `PASSED`、`quality_verification=real`，artifact SHA-256 `92b862e64eb7b4e31b2961c455503ff1f0b8fc4bf7163f184ea2bef8a718a190`（2,049,538 bytes），`last_qc_result=needs_review`。因此不构成整片完成或任何新镜头的最终验收。

### 本轮测试范围与边界

- 此续跑没有源代码改动，因此本轮没有重新跑 pytest/Ruff、前端构建或迁移检查；第 7 轮已有 `1800 passed, 1 skipped` 等结果仍只是第 7 轮测试证据。
- 真实远程 MiniMax 调用与真实应用分镜属于本机 API/Worker 集成验证；未知 outcome 不是 mock，也不能视为成功。
- 没有进行 H3 推理、视频解码、ffprobe、EDL/交付核对。VLM 正负校准仍未做，`pending_review` / 未完成状态保持原样。
- 订单 18–20 只有在可核对外部 UNKNOWN 的新证据或 `NOT_SENT_PROVEN` 证明出现后，才可继续；目前不得通过改输入另建近似请求绕过。
