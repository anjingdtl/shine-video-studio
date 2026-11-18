# 2026-11-18 接手轮：整备 60% 解阻、恢复入口解互锁与管家口径收敛 验收记录

> 基线：本地 `main` @ `7f73554`（Antigravity 半成品工作树，15 个修改文件 + 未跟踪 `.trae/`）。
> 本轮就地补全修复与验收，沿用同一 checkout，未切换分支、未开 worktree、未丢弃他人改动。
> 三原则贯穿：SQLite 台账是唯一事实；`machine_verdict`/`runtime_configuration` 空闲门禁各保留语义；
> `POST /api/v1/setup/prepare/execute → setup_preparation.enqueue → 常驻 Worker` 是整备唯一任务链。

## 1. 五个隔离复现（先红后绿）

| # | 复现（接手时已验证红） | 修复 | 回归 |
| --- | --- | --- | --- |
| 1 | RECIPE 失败后同请求 `enqueue` 得新 `task_id`、从 SCAN 重来、已完成步骤归零，与 UI「前面完成的步骤会保留」承诺不符 | `setup_preparation.enqueue`：FAILED/CANCELLED 且请求身份完全一致时走 `_resume_stopped_task`——同一 `task_id` 续跑到首个未完成步骤，DONE/SKIPPED 与合法回执保留，FAILED 步骤重置 PENDING，旧 `claim_token`/租约/`validation_delegation` 一并失效；不同请求仍按原语义 SUPERSEDED | `test_setup_preparation_chain.py::test_an_identical_request_after_failure_resumes_from_the_first_unfinished_step` |
| 2 | 停摆安装记录（plan 摘要错 / attempt plan_hash 不匹配 / 包条目缺 spec）被静默收编 `RECONCILED_NO_RETRY`，且 `stage` 仍是 `NEEDS_REVIEW` | `local.reconcile_stopped_isolated_attempts` 与 `reconcile_failed_install` 同一套硬校验（`graph_hash(plan.payload)==plan.sha256`、`attempt.payload["plan_hash"]`、executor/isolated/kind、每个包条目必须有合法 spec）；观察循环包进 savepoint，坏包回滚不再留下前一包的部分晋升；status 与 stage 一次写成 `RECONCILED_NO_RETRY`（`installation.TERMINAL_STAGES` 同步收录，`attempt_view.terminal` 因此一致） | `test_setup_chain_convergence.py::test_a_stopped_attempt_with_a_wrong_plan_digest_stays_blocked` / `…missing_its_spec_stays_blocked` / `…writes_one_consistent_terminal_state` |
| 3 | 到期 `workflow_validation:*` 租约只有租约行时凭「全部已知端口空闲」被标 RELEASED——空台账+过期不构成安全释放 | `_release_orphan_ephemeral_renderer_lease` 改为按**本租约自己的目标**举证：`workflow_validation:` 需要 `validate_registered_workflow` 新写入的 `workflow-validation:<lease_id>` journal（PID/birth + named Job Object + 端口，`renderer_is_gone` 三证）；旧记录无 journal 证明不了就保留核对。`image:` 需要「无任何 image 启动 journal + 无在途 image 工作 + 全机静默」的启动前证明；有 journal 的仍走 `release_dead_renderer_lease` 既有记录路径 | `test_comfy_handoff_journal.py::test_an_expired_validation_lease_without_target_evidence_stays_blocked` / `…with_a_proven_gone_target_releases` |
| 4 | PAUSED 序列带已结清失败史（FAILED、无租约、`provider_job_id` 空、`user_abandon/abandoned` 核对在案）仍 409 `PRODUCTION_SERIES_EXTERNAL_WORK_UNRECONCILED`——「存在任意 attempt」即永久拒绝 | `production/runner._job_submission_settled`：终态 FAILED/CANCELLED + 无活租约 +（最新 `ExternalJobReconciliation.result != "requires_reconciliation"`，或无外部 ID/ack/prompt graph/submit intent 的硬证据）判为已结清，`_series_has_unreconciled_submission` 跳过；真正不确定的尝试原样拒绝 | `test_production_series.py::test_a_settled_failed_job_does_not_block_cancelling_the_series` / `…genuinely_uncertain_attempt_still_refuses_cancellation` |
| 5 | 机器裁决 RUNNING、任务已 USER_ABANDONED、attempt 码 `STRATEGY_RENDERER_OBJECT_INFO_UNAVAILABLE` 被按 `object_info` 关键词误诊为「ComfyUI 缺少工作流需要的节点」 | `diagnostics._CODE_RULES` 新增准确映射（渲染器预检不可达≠缺节点，节点结论只认真实 `/object_info` 清单）；`_keyword_rule` 对含 `object_info_unavailable` 的文本不再落 NODE_MISSING | `test_support_agent.py::test_an_unreachable_renderer_preflight_is_not_diagnosed_as_missing_nodes` |

## 2. 生产恢复入口解互锁（P3/P4/P5）

- **P3 序列取消**：即上表 #4。`cancel_series_in_session` 与 `cancel_run` 共用的 `_series_has_inflight_work` 因同一裁决自动受益；已通过版本、FK、产物、外部 ID 全部保留，取消后单独 Job 置 stale 不可被普通队列认领（原有语义不变）。
- **P5 序列恢复**：`production_series.resume_series_in_session`（`/api/v1/production-series/{id}/resume` 与 `resume_series` 共用）接受**已证结清**的 `NEEDS_REVIEW` 序列：`_series_has_inflight_work` 为假（无未决提交、租约已 RELEASED、无活渲染器）时清失败记账回 `QUEUED`，已结清的失败镜头留作历史，Worker 从首个未完成镜头继续；真正未决的仍 409 具名拒绝。PAUSED/PAUSE_REQUESTED 原语义不变（owner 存活保 RUNNING）。
  回归：`test_production_series.py::test_a_settled_needs_review_series_resumes_at_its_first_unfinished_shot` / `…unresolved_needs_review_series_still_refuses_resume`。
- **P4 三方互锁**：`runtime_orchestrator/executor._reconcile` 在只读证据之后，把「渲染器已三证死亡」的独占租约委托给 Worker 恢复拍同一个执行者 `reconcile_dead_renderer_leases` 收尾（不新增第二条释放链），随后重读租约事实——stop 的 `RUNTIME_WORK_IN_FLIGHT` 预检与 abandon 的租约条件因此能真实收敛；渲染器仍活、归属不明、PID 重用的保护全部保留（弃用人工 `Stop-Process` 的正路仍是既有受管停机/释放链）。

## 3. 日常整备从 60% 解开

- **空闲门禁唯一裁决**：`runtime_configuration.idle_configuration_blockers`（半成品已有）继续作为唯一事实源，`require_idle_configuration`、`_step_recipe` 停步详情、工作栈快照、管家证据快照同读一份；可选组件（VLM/ASR/RelayVSR）缺件或静止失败不挡 H3，仍占实际资源的可选操作保留冲突保护（既有语义）。
- **先对账再解析**：`worker._reconcile_pre_runtime_control_and_leases` 是**每 tick 唯一**推进控制操作的位置（`_run_configured_once` 里的重复调用删除），并补上 storage-migration 守卫；runtime 失效（`ACTIVE_RUNTIME_*`）时安装队列/整备/文本整备照常推进，控制面与死渲染器租约对账不再被解析门挡住。
- **异常分支收敛到本任务样本**：`run_pending_image_items(..., run_id=)` 新增运行范围过滤（`claim_next_item` 同签名扩展；run 范围内不回退到无关 LibraryRenderTask）。runtime 失效下仅当整备任务自身有 `image_sample_run_id` 时才驱动该批小样，不再全局抢最老 item。
- **安装对账复用**：`_step_reconcile`/`reconcile_installation` 驱动的 `reconcile_stopped_isolated_attempts` 收敛到与 `reconcile_failed_install` 相同的硬校验与写回（上表 #2）；已核实字节最多 `FILES_VERIFIED_PENDING_RUNTIME`，不重下不重装。
- **搬迁容忍统一**：半成品给 `configuration_identity` 的按目录名例外提取为 `instances.relocated_managed_root` 唯一判定，`deployment.generate_configuration` 两个消费点同用；受管自有行搬迁后按**当前锚定**读写（`root = ladder_root`），历史 root 只作记录；真实端口/固定文件/图合同变更仍具名失效。
- **重复定义合并**：`RELAYVSR_COMPONENTS` 以 `deployment.py` 为唯一定义（`setup_summary` 引用之）。
- **VALIDATE 一条链**：沿用 R5 的 supervisor 委托合同未动；本轮修复的续跑语义（#1）显式作废过期 `validation_delegation`，晚到回执不能激活新任务。

## 4. 管家与报告口径（P1 及附件三报障）

- `STRATEGY_RENDERER_OBJECT_INFO_UNAVAILABLE` 精确映射（#5）；61ffdb33 一类「指向已放弃旧任务」的报告因此不再误导排查方向。
- `context.build_evidence_snapshot` 的 `idle_blockers` 读取失败时是 `None`（无法核实），不再伪装成空列表；`diagnostics._apply_setup_diagnosis_detail` 区分「无法核实（不能断定已清空）」「为空（可从断点继续）」「逐项列出」三种口径；`root_cause_confirmed=True` 只授予权威读取确认的事实（运行配置 INVALID+具名码、整备停步+错误码），阻塞台账项降为「已观测」。
- `_unblock_actions`：取消生产运行的建议只指向**实际阻塞配置门禁的 run**（`idle_blockers.production_series.run_id` 关联），不再凭「页面唯一未完 run」推荐取消任意运行；`managed_release` 仍由占用裁决守卫。
- 工作栈快照/前端 `WorkStackMonitor`：`idle_blockers_unreadable` 如实显示「读取失败，不能据此判断为空」。
- `errorActions.ts`：`ACTIVE_RUNTIME_CONFIGURATION_CHANGED`、`RUNTIME_CONFIGURATION_REQUIRES_IDLE_AND_RECONCILED_WORK` 前端映射（半成品已有，本轮保留），`serverMessage` 码仅回裸码时用静态文案兜底。

## 5. P2（12GB 默认规格）复查结论

- 事实链已钉住：`graph_compiler.WIDTH_DEFAULT/HEIGHT_DEFAULT = 1344/768`、24fps、`17k+5` 帧网格（10s→243 帧）；未指定 `output_spec` 时节点 31 按此默认编译；`size_blockers` 只裁「未映射」，不静默改尺寸。标准目录 `h3-ref2va-windows-x64.json` 声明 `min_vram_mb=15360`，低端候选显式 `"unknown"`（AGENTS 2026-11-07 口径）。
- 回归：`test_output_spec_and_identity.py::test_the_unspecified_output_compiles_at_the_pinned_default_frame` 钉住默认框架与「不静默降分辨率」合同。
- **留待真机**：672×384/4s 成功 vs 1344×768/10s 全量 offload 是用户 12GB 单机实测；按「不把单机实测写成全局上限」，容量门禁需先在真机把「分辨率×帧数×变体」的实测边界登记进目录（`min_vram_mb`/variant requirements），再由既有 production admission 唯一裁决消费。本轮未改默认值、未降分辨率、未改镜长。

## 6. 验证层次与证据

- **L1/L2 合成回归**：五个复现 8 红测 + 1 保护用例全部转绿；定向 25 个模块（idle_configuration、runtime_identity_relocation、setup_chain_convergence、setup_preparation_chain、setup_preparation_instance、setup_one_stop_v41、setup_missing_verdict、setup_machine_reuse、installation_queue、service_validation、offline_e2e、comfy_completion_window、advisory_queue、runtime_orchestrator、runtime_control_hardening、production_series、managed_process_release、comfy_handoff_journal、support_agent、support_copilot_v2、output_spec_and_identity、audit_event_codes、machine_progress、workstacks 相关等）全绿，退出码 0、无 FAILED/ERROR 行。
- **后端全量 8 分片（固定根 `F:\ShineTest`）**：首轮 `shards-20261118-213843-523` 暴露 2 个与产品无关的登记缺口（新审计码未登记前端清单；`test_machine_progress` 的 stub 未跟上 R5 共用规划器读取 catalog_file），修复后复跑 `shards-20261118-220541-447`：**274 个测试文件、8/8 chunk exit 0、`shards_with_failure_markers=0`**，basetemp 与旧轮次按脚本自动清理（保留最近 1 轮日志）。
- **前端**：`npx vitest run --no-file-parallelism` 83 文件 / **433 用例全过**（新增工作栈「无法核实」口径 2 例）；`npx tsc --noEmit` exit 0；`npm run build` exit 0。
- **静态/合同/迁移**：Ruff `backend shine scripts/shine_supervisor.py` 全过；Copilot 合同 `--check` `contracts ok: app=2.2.0 actions=14 knowledge=22 discovered=427`；`git diff --check` 干净；隔离库 `alembic upgrade head + check`「No new upgrade operations detected」（本轮零 schema 迁移）。
- **L3 Windows 旧库升级（本机 RTX 5080，隔离库）**：`F:\ShineTest\接手轮旧库升级 20261118\data`（中文+空格路径）——先用 alembic 造 `f180b0010001`（56 表）旧库，`start_shine.ps1 -DataDir … -AutoPorts` 实际启动把库迁到 `f180f0010006`（90 表）；API 8001 / Web 5174 均 200（8000/5173 上的日常栈零扰动）；`/setup/verdict` 如实 `SETUP_REQUIRED`、`machine_progress` 同一裁决；`prepare/execute` 真实入口排队 → 同请求幂等返回同 `task_id`（RUNNING）→ `prepare/cancel` 回「已取消；已经完成的步骤会保留」；`/setup/work-stacks` worker 在线、`idle_blockers=0`、`unreadable=false`；`stop_shine.py` 退出码 0、端口释放。
- **L4 真实服务合同/真实 H3 新出片**：`PENDING / NOT_RUN`。本轮未在真实渲染器上重做 VALIDATE 链、未发起新的真实 H3 出片；`RECONCILED`→`PROFILES`→`VALIDATE` 全链在真机的行为待下一轮真机验收。
- **VLM/审片身份披露**：本轮修改了 `worker.py`、`handoff_startup.py`、`service_validation.py`（后两者在 `client_review_identity` 钉住清单内），旧 VLM 校准修订按设计失效；本机当前无可用的受引导 VLM 审片能力，真实正/负校准记为**待验**，`advisory_model_reviews` 仍不在受管渲染关键路径上。

## 7. 诚实边界

- a0799961 的 `LEASE_RECONCILE_REQUIRED` 在现场台账里的真实 owner 未取到（截图/报告无 owner/ID/进程证据），本轮只能给出：reconcile 控制操作现在能收敛「三证死亡」租约（§2 P4），管家把该码指向既有核对/释放动作且不再把无关 `RUNTIME_BACKEND_TRIAL` 任务当责任对象；该用户机的最终根因仍以其台账为准。
- b30ebb92 的 `h3:INSTANCE_IDENTITY_CHANGED` 行是主因还是同次告警未在旧库反例中核定；搬迁容忍已统一到唯一判定（§3），真机 `J:\svs\...` 场景的复验留待用户机。
- 「取消后单独 Job 不可被普通队列重新认领」「保留已通过版本/产物/外部 ID」等既有保护未放宽，均有反例锁定。
- 本轮未提交、未推送：commit/push/发布按交接等待另行授权。
