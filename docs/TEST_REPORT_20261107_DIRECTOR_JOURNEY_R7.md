# 测试报告：导演模式全旅程第 7 轮（2026-11-07）

仓库：`F:\ClaudeWorkSpace\projects\shine-video-studio`，基线 `bafb241`（本轮改动见 §1）。
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
