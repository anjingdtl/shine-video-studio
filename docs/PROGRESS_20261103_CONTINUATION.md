# 《Shine 视频工作台》V4 收尾进度（2026-11-03 晚间补充轮）

- 记录时间：2026-11-03 18:05（Asia/Shanghai）
- 已推送基线：代码 `784cf36`，V4 报告 `e7b3b99`（远端 `main` 一致）
- 本轮改动：未提交（见本文末“本轮代码与文档变更”）
- 主验收数据：`.local-acceptance/real-user-journey-final/data/`
- 证据目录：`.local-acceptance/real-user-journey-final/` 与 `.local-acceptance/real-user-journey-final-v4/evidence/`

## 当前结论

**自动测试全通过（后端 1455 passed / 1 skipped / 0 failed；前端 build EXIT=0、Vitest 90 passed）；真实三镜生产保持既有全通过证据；正式新作品在风格修正后的第二次真实渲染已完成字节采集，但在 QC 前 worker 进程丢失，任务、系列和租约处于待恢复状态。因此本轮不能宣称“正式项目当前镜头全 PASS”，更不能宣称整个目标已完成。**

真实已完成、可直接复核的证据：

- 回归作品「Shine真人模拟验收短剧（建议式审片回归）」三镜真实 H3 系列 `0dd7ad85-3fca-43f0-90cf-d8bbf078baf9`：`COMPLETED`、3/3 镜 `PASSED`、`quality_verification=real`；三镜 technical QC 全部 `passed`（ffprobe 全帧解码）。
- 该系列自动交付订单 `20d6bcda-8318-4957-8d71-8630d75c4c67`：`DELIVERED`，成片 artifact `e64ba9fd-8676-445e-8a45-8407bffa8b99`，SHA-256 `9039fc307f3dee89d09039a7f50dd603a36d460efd1bfde38f05f914a370e99c`，9,927,251 B。浏览器播放与真实下载已在 V4 主报告中留证。
- 正式项目 `ff6096c5-2e46-4a16-aafb-ebd4889ece39` 的预制作冻结流程已打通：Style V2 `7c3ed722-7bd0-4a52-96e1-2e2c4c0a7624`、shot revision 4、真实文本输入评估 `1422ad65-0cea-47f0-a555-e65fddb000bd`（7/7 `consistent`，`proceed_to_preflight`）、冻结计划 `3187c16d-3b80-4ea8-a3d7-f6047d2873eb`（`SHOOT_READY`，绑定 H3 `93da6ba3`、VLM `d91a16c3`、ASR `1f0263da`）。

## 本轮修复（均有回归覆盖）

### 1. 提交保护不再跨外部调用持有 SQLite 写锁

首次正式系列 `3f703b11…` 期间，worker 在 15:52:21 记录 `worker_heartbeat_failed error_type=OperationalError`，随后出现 `COMFY_HEARTBEAT_RECONCILIATION_REQUIRED`。根因是 `BEGIN IMMEDIATE` 提交保护在外部上传/提交期间长时间持有写锁，托管心跳线程拿不到写锁并按“丢失租约”处理。

修复：

- `worker.py` 在完成 claim/preproduction/activation 校验后先提交事务，再调用 `service.submit_prepared(...)`；外部调用期间不再持写锁。
- `heartbeat()` 把 SQLite `database is locked/busy` 视为短暂写竞争并跳过本次脉冲，下一次脉冲重新校验租约，而不是直接终止任务。
- 新增回归：`test_activation_uncertainty_adopts_completed_work_without_resubmitting`（完成外部历史可采纳、绝不重投）。

### 2. 系列心跳与“所有权证明丢失后”的结果归并

真实系列 `c48dd1cd…` 在镜头 `PASSED`、租约已 `RELEASED` 后仍被标成 `NEEDS_REVIEW / SERIES_OWNERSHIP_LOST`。同时系列心跳线程在短暂 SQLite 写竞争下会永久停止续约。

修复（`comfyops/production_series.py`）：

- `SeriesHeartbeat` 对 `database is locked/busy` 延迟到下一拍重试，不再误报所有权丢失。
- `reconcile_abandoned_series()` 新增两条恢复：
  - 租约仍为 `RECONCILE_REQUIRED/ACTIVE` 且所有镜头已通过：仅在进程身份与命名 Job Object 可证明时停止渲染器，然后在同一事务中释放租约并置 `COMPLETED/RELEASED`；
  - 租约已经释放、但系列仍是 `NEEDS_REVIEW`：仅在所有镜头已通过时把结果归并为 `COMPLETED`，不重投。
- 回归：`test_a_passed_shot_stranded_in_review_finishes_and_releases_its_renderer`。

### 3. 激活不确定性的安全采纳

新增 `_activation_owned_alive()`：仅当激活日志的 `last_confirmed_stage=CONFIRMED`、启动记录租约一致、记录的渲染器 PID/创建身份与命名 Job Object 仍可证明属于本任务时，才允许读取 ComfyUI 历史并采纳已完成输出；否则继续阻断。`COMFY_ACTIVATION_RECONCILIATION_REQUIRED` 已加入可对账失败码。该路径永不重新提交。

### 4. 操作员放弃路径的所有权校验

`operator_review.abandon_unreconciled_job()` 原先只检查 handoff 日志，可能对只有 activation/startup 日志的任务释放仍存活渲染器的租约。现同时检查 startup 记录的进程身份与端口；放弃后把 activation 日志归并为 `RECONCILE_COMPLETE`。真实首次阻塞任务 `5734f736…` 已通过该路径安全放弃，租约 `bfe72683…` 已 `RELEASED`。

### 5. 引导式建议审片的输入证据校验

正式镜头首次建议式 VLM 审片失败为 `SEMANTIC_REVIEW_INVALID_JSON: STYLE_INPUT_EVIDENCE_NOT_VERIFIED`。根因是 advisory 队列把原始 `{artifact_id, sha256}` pin 直接交给 reviewer，而 reviewer 只接受由数据库重建的 `VerifiedInputEvidence`。

修复（`quality/advisory_queue.py`）：引导式 VLM 建议审片通过 `TrackedProvider` 适配器执行，与生产路径使用同一套证据重建与执行身份绑定。修复后用真实 VLM 重跑正式镜头的建议审片，任务 `17536ac0…` 由 `UNAVAILABLE` 恢复为 `COMPLETED`，结果为 `failed` 并记录 `STYLE_INPUT_*` 真实冲突（这正是后续修正 Style 的依据，而非基础设施错误）。

### 6. 风格修正与真实 VLM 重校准

- 首次冻结风格是“明亮日光”，与已确认的雨夜故事板和首批真实帧冲突。已通过本地操作员风格冻结流程建立 Style V2（雨夜低照度、冷色低饱和、酒馆暖光侧光），旧风格 V1 保留不变。
- 为使文本输入自洽，镜头修订到 revision 4：固定机位距离、去掉隐含推近，明确安静雨声与克制悬疑节奏；真实 qc_reviewer 文本评估 7/7 项 `consistent`，推荐 `proceed_to_preflight`。
- 本轮对 `worker.py`/`preproduction.py` 等 pinned review client 文件的修改会按设计触发 `NATIVE_REVIEW_CLIENT_CHANGED`。已在最终代码上重新完成两条真实 VLM 校准调用：
  - 正向 `09a42818-6e52-43ef-abd9-ddf2e7414480`：`passed`，confidence 0.98，findings 0；
  - 负向 `4cfcbcb8-d410-4147-aca6-7c52f1da0224`：`failed`，confidence 0.85，7 条 findings。
- 新 VLM 修订 `d91a16c3-600c-4b04-af35-7be954398e73` 已发布为 `PRODUCTION_APPROVED`；旧修订 `08174d46…` 已 `DEPRECATED`。

## 当前阻塞（需要后续 Agent 接着处理）

正式项目第二次真实渲染：

- 系列：`a6b97b5e-1cb3-4daa-ae71-409686fe806d`，当前 `RUNNING`，`worker-e308a263796e`，`lease_id=96971172-9ad9-4518-b82a-3f0e2de5abb0`。
- 任务：`f0173fbe-41c0-4ee0-ac26-9c6fecc74635`，当前 `RUNNING`，`state_version=2`。
- 外部提交：`52e492e7-ecc6-4e52-ba4b-b1f55361a267`，已确认完成并下载输出。
- 已持久化产物：artifact `469ee15a-2330-43b9-8f06-ac6f00acf6a3`，SHA-256 `4f094edb972b5a1ffdce08d276553127912de8effbecdcac22f6577e1cba4585`，3,865,537 B，`video/mp4`。
- 崩溃点：worker 进程在 `17:53` 完成字节采集后、QC 完成前丢失（执行记录 `process_lost`，无异常栈）；`job_attempts` 为 `ACKNOWLEDGED`，`output_sha256` 与 artifact 一致，`reconciliation_state` 为空。
- 现场状态：`8188` 已无监听，渲染器不在；GPU 租约仍为 `ACTIVE` 但已过期；系列租约也已过期。**不要盲重投**。

建议后续优先实现/使用“从已持久化 artifact 继续 QC”的恢复路径：在 artifact SHA、attempt `output_sha256`、job provider id 与已记录 prompt graph hash 都可核对时，重新执行 technical QC 和门禁并落库，不重新提交 H3；若该路径不可用，则先用操作员“放弃不确定任务”路径核对渲染器已停止，再重跑新系列。

## 未完成事项

1. 恢复或重跑正式项目当前镜头，直到出现真实的 `PASSED` + technical `passed` + 最终门禁结论。
2. 通过本地操作员分项审片（浏览器实际播放）决定 `manual_verified` 或真实 `failed`；不得把模型建议冒充人工审批。
3. 浏览器复验正式系列、成片中心播放、真实 MP4 下载（文件 SHA 与 ffprobe）。
4. 重新构建 Windows 测试包（必须从最终提交构建），校验 `BUILD_INFO.json`、`PACKAGE_MANIFEST.json` 与入口脚本。
5. 更新 V4 最终报告并重新跑完整后端套件、Ruff、Alembic、前端构建/Vitest；推送 `main` 后再报告远端一致性。

## 证据索引

- 完整后端测试日志：`.local-acceptance/real-user-journey-final/logs/full-backend-final2-20261103.log`
- 定向回归日志：`.local-acceptance/real-user-journey-final/logs/targeted-20261103.log`
- 前端构建：`.local-acceptance/real-user-journey-final-v4/evidence/frontend-build-final4-20261103.log`
- 前端 Vitest：`.local-acceptance/real-user-journey-final-v4/evidence/frontend-vitest-final4-20261103.log`
- VLM 重校准：`.local-acceptance/vlm-recalibration-20261103.json`
- 正式项目帧证据：`.local-acceptance/real-user-journey-final-v4/evidence/final-frames/`

## 本轮代码与文档变更

- `backend/shine/worker.py`：提交保护移出外部调用、写竞争容忍、激活对账采纳。
- `backend/shine/comfyops/production_series.py`：系列心跳写竞争容忍、结果归并/释放恢复。
- `backend/shine/operator_review.py`：放弃路径的 activation/startup 所有权检查。
- `backend/shine/quality/advisory_queue.py`：引导式建议审片使用 `TrackedProvider`。
- `backend/shine/comfyops/preproduction.py`：项目 profile 与冻结修订的精确同图复用。
- 回归：`test_comfy_completion_window.py`、`test_production_series.py`、`test_v13_e2e.py`、`test_workflow_profile_scope.py`。
- 文档：本文件、`docs/TEST_REPORT_20261103.md`、`AGENTS.md` 补充。
