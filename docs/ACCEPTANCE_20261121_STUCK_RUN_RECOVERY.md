# 验收记录：中断拍摄恢复解阻（2026-11-21）

用户报障（真机《女巫1221-7》，工作栈中途关闭后重启）：
1. 顶栏「有任务待核对 · 去处理」只送进基础配置，没有直达卡住作品的视频生产页；
2. ⑥ 视频生产页对中断的拍摄没有任何可继续的入口，页面假装「正在生成」。

## 1. 根因（台账实证）

- job `f00b3cf9…`：`NEEDS_REVIEW / COMFY_ACTIVATION_RECONCILIATION_REQUIRED`，
  有外部 ACK（`ec8794b5…`）、无产物；渲染器随旧栈死亡。
- 恢复路径活锁：`recover_expired_jobs` 每拍把「重新阻塞一个已阻塞的 job」记成进展，
  同一 job 累计 23799 条 `COMFY_HANDOFF_RECOVERY_BLOCKED` 审计（约 100ms 一条），
  tick 永远返回 `reconciled`，series/run 执行器拿不到拍子 → series/run 永远 RUNNING，
  人工决定面永远不露出。违背「检查过 ≠ 有进展」。
- 放弃后仍无处续拍：重拍裁决 `_blocker` 只认 PASSED 源；根序列 NEEDS_REVIEW 时
  选用无法收敛批次/分集；`reconcile_abandoned_series` 不读「租约已 RELEASED」事实。

## 2. 修复（全部收敛到既有事实/裁决/执行者，无新增状态、无新增执行链）

| 位置 | 收敛 |
| --- | --- |
| `worker.py::_block_interrupted_handoff` | 仅真实写入新结论时返回 True 并写审计 |
| `worker.py::_handoff_decision_pending` + `_recover_one_job` | 已记录且无变化的阻塞结论返回 0；渲染器仍活且归属可证时不短路（历史可采用） |
| `production_mode.py::summarize_production_mode` | 唯一裁决携带 `focus{project_id,run_id}`（租约→job→series item→run batch） |
| `schemas.py` | `ProductionModeStatus.focus` 登记进严格响应模型 |
| `App.tsx::handleModeAction` | `check_fix` + `LEASE_RECONCILE_REQUIRED` + focus → 直达该作品⑥页 |
| `DirectorShootPanel` | 运行卡就地挂载既有执行者：未知结果→「放弃未知结果（保留产物，不重投）」（同 `abandonJob`）+「去⑦重拍此镜」；已放弃→只指重拍；NEEDS_REVIEW 标题如实 |
| `api_segments.py::_blocker` | 承认已结清放弃（`USER_ABANDONED_UNRECONCILED`）的 FAILED 源可重拍（新请求，非重投）；未结清仍拒 |
| `api_segments.py::select_replacement` | 根序列 NEEDS_REVIEW 且全镜选用通过时把选用写回根 items（旧 job 留历史） |
| `production_series.py::reconcile_abandoned_series` | 租约已 RELEASED + 全镜通过 → 合并结论 COMPLETED；批次/分集/交付/Run 由既有 advance 收敛 |

## 3. 回归

- 新增 `backend/tests/test_stuck_run_recovery.py`（4 例）：
  1. 重复核对不再计数/刷审计（首拍 1，后续 0）；
  2. 真实 `_run_configured_once` tick：reconciled → series:NEEDS_REVIEW → run NEEDS_REVIEW，
     裁决 focus 指向该作品与运行；
  3. 无待核对租约时 focus 为 None；
  4. 整链：放弃→⑦重拍（202）→选用（202，根 items 写回）→序列 COMPLETED→
     ⑥既有恢复入口→分集交付单创建。
- 新增 `frontend/src/test/stuckRunRecovery.test.tsx`（4 例）：顶栏直达⑥、放弃/重拍入口、
  已放弃只指重拍、范围外不显示。
- 定向批次：handoff/recovery/scheduling/machine_recheck/concurrency（全绿）；
  production_mode/system_status_identity/comfy_completion_window（首跑因响应模型未登记
  focus 与活锁短路误伤活渲染器采用路径而红，修复后全绿）；
  generation_segments/production_series/video_library（见 §5）。
- 前端全量 vitest 92 文件 / 469 例、tsc、Vite build、Ruff 全绿。
- 后端 8 分片全量：见 §5。

## 4. 真机验证（D:\Shine\data，开发栈）

- ⑥ 新决定面在当前栈浏览器实测可见；**用户本人点击新「放弃未知结果」按钮**完成放弃
  （job state_version 3→4，`USER_ABANDONED_UNRECONCILED`，租约 RELEASED）——新入口真实闭环。
- 监督进程重启安全门（`RUNTIME_CONFIGURATION_REQUIRES_IDLE_AND_RECONCILED_WORK`）
  在放弃前正确拒绝重启；放弃后重启加载活锁修复。
- 重启后：series/run 推进到 NEEDS_REVIEW；审计 spam 停止（6s 内计数不变）；
  ⑥ 标题「运行等待人工核对」，已放弃镜只显「去⑦重拍此镜」。
- 用户后续动作（既有入口）：⑦ 重拍该镜 → 选用 → ⑥「继续安全可恢复批次」→ 整集交付。
  重拍是新的真实渲染与艺术决定，留给用户执行。

## 5. 全量与真机复核（已补）

- 后端 8 分片全量（最终代码，`run_backend_shards.ps1 -WorkRoot F:\ShineTest`，
  290 文件）：8/8 exit 0，`shards_with_failure_markers=0`，逐分片 0 条
  FAILED/ERROR。日志 `F:\ShineTest\shards-20261121-182147-724\chunk-*.log`。
  清理时个别 basetemp 路径因文件占用删除被拒（脚本如实报告，保留最近 1 次运行）。
- 最终重启后⑦ `can_regenerate=true` 真机复核：shot 14
  `can_regenerate=True / blocker=None`，恢复链全部入口在真机可用。

## 6. 诚实边界

- 本轮改 `worker.py`（`client_review_identity` 钉住）：旧 VLM revision 按设计失效；
  真实正负校准仍未完成，**不得声称已校准**。
- 无新的真实 H3 出片；整链第 4 例为离线调度/媒体夹具验证（OFFLINE_VERIFIED），
  不是艺术验收。历史产物与审计不重写。