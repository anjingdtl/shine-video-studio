# 《Shine 视频工作台》V4 收尾进度（2026-11-03 晚间补充轮 + 收尾轮）

- 记录时间：2026-11-03 18:05（Asia/Shanghai）；**收尾轮更新：2026-11-03 20:50**
- 已推送基线：代码 `784cf36`，V4 报告 `e7b3b99`，后续 `4d8123a`（远端 `main` 一致）
- 本轮改动：见本文末“收尾轮代码与文档变更”
- 主验收数据：`.local-acceptance/real-user-journey-final/data/`
- 证据目录：`.local-acceptance/real-user-journey-final/` 与 `.local-acceptance/real-user-journey-final-v4/evidence/`

## 当前结论

**收尾轮结论：原“当前阻塞”已解除，正式项目镜头已由产品化恢复路径真实转为 `PASSED`，系列 `COMPLETED/RELEASED`，GPU 租约 `RELEASED`，交付订单 `DELIVERED`。本轮没有重投任何 H3 任务，没有降门禁，没有把 Mock 证据当真实证据。**

自动测试全绿（后端全量 `1472 passed / 1 skipped / 0 failed`；Ruff `All checks passed!`；Alembic 单头无漂移；前端 build `EXIT=0`、Vitest 93 passed）。真实生产链路：正式镜头 `f0173fbe…` 从已持久化产物恢复并通过技术 QC + 五道门禁 + 人工逐项审片；成片中心播放与真实 MP4 下载、SHA/ffprobe 核对均已完成。收尾轮还做了一次**独立只读代码复核**，发现的 8 个可复现缺陷已全部修复并补回归（R8）。

**仍未验证、如实保留的边界：**带人声的真实 H3 重渲染未做（对白语言设置已验证到提示词层，未验证模型实际口音）；正式作品版权/审美/发行审批仍为 `PENDING_HUMAN`；GitHub Actions 仍为 `WAIVED_EXTERNAL_BILLING`。

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

## 当前阻塞（已于收尾轮解除）

> 本节保留现场描述作为历史记录；解除过程与结果见下方“收尾轮”。**当前没有阻塞项。**

正式项目第二次真实渲染：

- 系列：`a6b97b5e-1cb3-4daa-ae71-409686fe806d`，当时 `RUNNING`，`worker-e308a263796e`，`lease_id=96971172-9ad9-4518-b82a-3f0e2de5abb0`。
- 任务：`f0173fbe-41c0-4ee0-ac26-9c6fecc74635`，当时 `RUNNING`，`state_version=2`。
- 外部提交：`52e492e7-ecc6-4e52-ba4b-b1f55361a267`，已确认完成并下载输出。
- 已持久化产物：artifact `469ee15a-2330-43b9-8f06-ac6f00acf6a3`，SHA-256 `4f094edb972b5a1ffdce08d276553127912de8effbecdcac22f6577e1cba4585`，3,865,537 B，`video/mp4`。
- 崩溃点：worker 进程在 `17:53` 完成字节采集后、QC 完成前丢失（执行记录 `process_lost`，无异常栈）；`job_attempts` 为 `ACKNOWLEDGED`，`output_sha256` 与 artifact 一致，`reconciliation_state` 为空。
- 现场状态：`8188` 已无监听，渲染器不在；GPU 租约仍为 `ACTIVE` 但已过期；系列租约也已过期。**不要盲重投**。

原建议路径（已实现）：在 artifact SHA、attempt `output_sha256`、job provider id 与已记录 prompt graph hash 都可核对时，重新执行 technical QC 和门禁并落库，不重新提交 H3；若该路径不可用，则先用操作员“放弃不确定任务”路径核对渲染器已停止，再重跑新系列。

## 收尾轮（2026-11-03 晚间后段）

### R1. 产品化恢复「产物已落盘但 QC 前丢进程」

- `worker.py` 新增 `_persisted_render_artifact()`：严格核对四段身份链——`artifact.sha256 == attempt.output_sha256`、`attempt.prompt_graph_sha256` 非空、job/attempt/artifact 三者的外部 prompt id 一致、`artifact.attempt_id/job_id/project_id` 匹配且产物非 mock——四段全部吻合才认领已落盘产物。
- `worker.py` 新增 `_recover_from_persisted_artifact()`：**不连渲染器、不重投**，从已落盘字节重跑技术 QC 与五道门禁并落库：attempt `reconciliation_state=adopted_persisted_artifact`、`ExternalJobReconciliation(action=recover_persisted_artifact, resubmitted=false)`、审计 `COMFY_RECONCILED_PERSISTED_ARTIFACT`，交接日志归并为 `RECONCILE_COMPLETE`（reason `OUTPUT_ALREADY_PERSISTED`）。字节在落盘后被改动时仍按 `ARTIFACT_HASH_MISMATCH` 拒绝。
- `_recover_comfy_job()`：已核对产物的分支**优先于**外部 history 查询；`_recover_one_job()`：已核对产物时跳过「交接中断」封锁。
- 边界：**交接已被对账完成（journal=`RECONCILE_COMPLETE`）的历史镜头不受新路径影响**，不改写既有结论；只有未对账的中断交接可被已核对产物接管。
- 回归：`test_comfy_completion_window.py` 新增「产物已持久化 → 不重投 → QC 通过 → PASSED」用例；`COMFY_HANDOFF_RECONCILIATION_REQUIRED` 加入可对账失败码。

### R2. 确证已死的渲染器 → 租约可释放

- `comfyops/handoff_startup.py` 新增 `startup_record_for_lease()`、`renderer_is_gone()`、`release_dead_renderer_lease()`、`reconcile_dead_renderer_leases()`：独占 GPU 租约视为对活进程的占用声明，**PID/birth 身份 + 命名 Job Object + 端口空闲**三重证据齐备才释放；证明不了就保持 `RECONCILE_REQUIRED` 阻塞。
- `worker.py` 租约循环：渲染器确证已死则走释放路径，不再一律置 `RECONCILE_REQUIRED`；`_block_interrupted_handoff` 不再把已 `RELEASED` 的租约改回 `RECONCILE_REQUIRED`。
- 回归：`test_comfy_handoff_journal.py`（死渲染器释放 / 存活渲染器不释放）、`test_production_series.py`（陈旧系列释放死渲染器租约；无法证明则保留租约）。

### R3. 系列状态与租约释放同事务

- `comfyops/production_series.py`：完成的系列**只有在 GPU 租约真正释放后**才写 `COMPLETED/RELEASED`，租约释放与状态迁移在**同一事务**内提交；已提交过的释放是幂等的，不是第二次释放。
- `run_production_series` 遇被阻塞的镜头改为 `NEEDS_REVIEW`，不再误标 `COMPLETED`；`reconcile_series` 也能修复「已完成但镜头尚未判定」的系列；`_sync_items` 通过时清空 `item.failure_code`。

### R4. 按当前冻结合同重新校验已产出媒体

- `comfyops/preproduction.py` 新增 `_render_bindings()`、`_plan_for_manifest()`、`revalidate_job_against_current_plan()`：比较**渲染相关绑定**（风格、规则包、母版身份、模型、H3 工作流），排除 VLM/ASR 审片能力修订；按 requirement 的 family/entity_id 与母版身份比较，忽略每次分析重建的 requirement 行 id。
- `api_operator.py` 新增 `POST /api/v1/operator/jobs/{job_id}/preproduction/revalidate`（本地操作员动作，写 `PREPRODUCTION_REVALIDATED` 审计）。**渲染绑定一旦变化一律拒绝**；只有审片能力修订变化才允许显式重新绑定。
- 回归：`test_v13_preparation.py` 新增 3 组用例（重新校验成功 / 渲染合同变更时拒绝 / requirement 行重写不改变绑定）。

### R5. 对白语言设置（用户反馈：成片对白听不懂）

真实问题不是「语言没设」，而是**提示词从头到尾是英文**：H3 在没有明确指令时按默认英文发声，无台词的镜头甚至会自己造人声。

- `h3/compiler.py`（`shine-h3-compiler/1.0.0` → `1.1.0` → `1.1.1`）：`overall_soundscape` 显式写出「发声合同」——有台词时 `All spoken dialogue must be spoken in {项目语言}, matching the original line text exactly`；**没有台词时明确要求 `do not produce speech, voice-over or vocal sounds of any kind`**。
- 语言名称以英文规范名写入提示词（新增 `shine/h3/spoken_language.py`）：官方规则包禁止 `<d>…</d>` 之外出现 CJK，原样写入「中文」会让建任务被 `H3_ENGLISH` 拒绝。现在「中文/粤语/日本語…」在保存时规范为 `Chinese/Cantonese/Japanese…`；无法规范的名字在 API 边界以 422 明确拒绝。
- 前端：导演模式「AI 导演制作」页新增「对白语言」下拉（中文/English/日本語/한국어/粤语/Français/Deutsch/Español），保存项目 `language` 并说明它会写进 H3 提示词；设置板块的「叙事语言」改为同一枚举。
- 回归：`test_h3_compiler.py`（发声合同 + 语言规范化）、`test_storyboard.py`（API 边界拒绝/规范化）、`frontend/src/test/storyboard.test.tsx`（对白语言保存 + 失败态 + 未知值回退）。

### R6. 审片能力修订重校准（`client_review_identity` 失效是设计行为）

R1–R4 触及 `worker.py` / `preproduction.py` / `handoff_startup.py`，按规则必须重新做真实正/负校准并发布新修订、弃用旧修订、重新 analyze + freeze 计划。R8 又触及这批文件，因此**做了两轮**校准，最终状态：

- 真实校准（本地 `qwen3-vl-4b-bnb4`，`client_review_sha256=8b8d9bd3…`）：正向 `3f82719e-49a7-4cc9-911c-57c94b5f0e22` → `passed`（confidence 0.98，0 findings）；负向 `87a00a00-41b2-4789-b98f-4e4409db401e` → `failed`（confidence 0.85，7 findings）。
- 最终批准修订 `03f32432-4c94-4476-8580-e1a14e9fc37f`，绑定当前 `client_review_identity`；此前发布/迭代产生的 `82906927…`、`f930e8ec…`、`43ca215d…`、`ed841a10…`、`e83b9b54…` 已 `DEPRECATED`，`839aeef6…` 保持 DRAFT。
- 冻结计划 `7832906a-d383-4eaa-a94d-0e0b510769d2`：`FROZEN`，绑定 H3 `93da6ba3…`、VLM `03f32432…`、ASR `1f0263da…`；`readiness(offline=false)` = `SHOOT_READY`，无阻塞项。
- 已产出镜头按新冻结合同重新校验：`preproduction_manifest_hash` `e421c90c…` → `55b03fe7…`，状态仍 `PASSED`、`quality_verification=manual_verified`。

### R7. 真实恢复结果与验收

| 对象 | 收尾轮之后的状态 |
| --- | --- |
| 镜头 `f0173fbe-41c0-4ee0-ac26-9c6fecc74635` | `PASSED`，`quality_verification=real`，`acceptance_status=accepted`，`acceptance_final_gate=passed` |
| attempt `6470491c-ab11-4946-be6e-137af762623f` | `reconciliation_state=adopted_persisted_artifact`，**未重投**（`resubmitted=false`） |
| 五道门禁 | `technical=passed`（ffprobe + FFmpeg 全帧解码）、`semantic=passed`、`style=passed`（真实本地 VLM 0 findings / 0.98）、`audio=not_applicable`（本镜无台词）、`final=passed`（人工逐项审片） |
| 系列 `a6b97b5e-1cb3-4daa-ae71-409686fe806d` | `COMPLETED / RELEASED`，`completed_count=1/1`，`lease_id=None` |
| GPU 租约 `96971172-9ad9-4518-b82a-3f0e2de5abb0` | `RELEASED`（走「已证明死亡」释放路径） |
| 交付订单 `ac7f0298-4411-4c71-9f29-f7588686e2ba` | `DELIVERED`，成片 artifact `6c65ee08-193e-4b02-9c95-b58e08f649bd` |
| 成片 MP4 | 3,644,345 B，SHA-256 `04dc2cff7ce579b89ca54ff754d24dad6a6c9aee305dd857f7f0bbd91bb03241`；ffprobe：mp4 / h264 768x1344 / 24fps / AAC 48kHz 立体声 / 9.44 s；独立 `ffmpeg -f null -` 全片解码 exit 0 |

人工审片（浏览器 OperatorReview，本地操作员 `local-user`，逐项具体证据）：7 项「通过」+ 3 项「不适用」（本镜 `dialogue` 为空）；**「已人工试听」未勾选**，本轮没有做人工听辨，不冒充。「动作与运镜」一项如实留档：画幅从 0.0s 膝上中景持续收紧到 8.5s 手与怀表特写（人脸出画），比冻结分镜「保持同一机位距离、不要推近」更近；按「有动机的连续推进、落点在剧情道具、轴线与光线未变」判定可用，差异原文写进证据。

### R8. 独立复核轮：8 个缺陷全部修复并补回归

对收尾轮 diff 做了一次独立只读代码复核。结论：核心安全属性（持久化产物恢复绝不重投 H3、租约只在可证死亡时释放）成立，但发现 8 个可复现缺陷，全部修复并补回归：

| # | 缺陷 | 修复 |
| --- | --- | --- |
| D1 | 「已完成但有阻塞镜头」的系列改判 `NEEDS_REVIEW` 是空操作（终态守卫直接返回），但 `changed` 仍 +1 | `_correct_completed_series_to_review()`：显式纠正 + 只在真变化时计数 + 审计 `SERIES_COMPLETION_CORRECTED` |
| D2 | 渲染绑定的 `models` 没排除审片模型，换审片模型被误判为合同变化 | `_render_bindings()` 按非审片工作流的 `model_ids`/`node_package_ids` 过滤；工作流修订缺失时保守不过滤 |
| D3 | 语言名含 CJK 时提示词触发 `H3_ENGLISH`，建任务 422（本轮新引入的失败模式） | 新增 `shine/h3/spoken_language.py`；编译器与 API 边界双重保证英文提示词里不出现 CJK |
| D4 | `renderer_is_gone()` 缺命名 Job Object 证据；且把 `process_identity` 的 `ValueError`（不确定性）当成死亡 | 新增只读 `named_group_active_processes()`；改为 PID/birth + 命名 Job Object + 端口三重证据，任一不可证即阻塞 |
| D5 | 认领记录先于字节校验落库，字节被篡改也会留下「已认领」证据并使该镜头永远无法再复核 | 字节校验前移到认领之前：SHA/大小不符 → `ARTIFACT_HASH_MISMATCH` + `NEEDS_REVIEW`，不写认领记录、不翻 journal |
| D6 | 前端保存失败后永远显示「保存中」；未知语言下拉无选中项 | 增加第三态「保存失败」；未知值回退默认 |
| D7 | `reconcile_dead_renderer_leases()` 未防 SQLite 忙，瞬时写锁会打死常驻 Worker | 捕获 `OperationalError` 延后到下一拍 |
| D8 | 交接 journal 已被判 `RECONCILE_COMPLETE` 时已落盘产物被永久孤立 | 「已对账但镜头尚无结论且存在已核对产物」时继续质检；已有结论的镜头依旧不被改写 |

另修既有缺陷：`owned_process.py` 的 `ctypes.INT` 不存在（应为 `ctypes.c_int`），`terminate_named_group()` 在真机直接 `AttributeError`。

修复触及 pinned 文件，因此**再次**重做真实校准并发布新修订（`03f32432…`）、重新冻结计划（`7832906a…`）、重新校验镜头（见 R6）。

## 未完成事项

1. ~~恢复或重跑正式项目当前镜头，直到出现真实的 `PASSED` + technical `passed` + 最终门禁结论。~~ → **已达成**（R7）。
2. ~~通过本地操作员分项审片决定 `manual_verified` 或真实 `failed`。~~ → **已达成**（`manual_verified`，逐项证据）。
3. ~~浏览器复验正式系列、成片中心播放、真实 MP4 下载（文件 SHA 与 ffprobe）。~~ → **已达成**（R7）。
4. ~~更新 V4 最终报告并重新跑完整后端套件、Ruff、Alembic、前端构建/Vitest。~~ → **已达成**（见 `docs/TEST_REPORT_20261103.md`）。
5. **仍待**：从最终提交构建 Windows 测试包（`pwsh -File scripts\build_test_package.ps1 -OutDir <dir> -Verify`，必须干净树）并校验 `BUILD_INFO.json`、`PACKAGE_MANIFEST.json` 与入口脚本。
6. **明确未做（如实保留）**：带人声的真实 H3 重渲染，以验证对白语言设置在模型侧的实际口音；正式作品版权、审美与正式发行审批。

## 证据索引

- 完整后端测试日志：`.local-acceptance/real-user-journey-final/logs/full-backend-final2-20261103.log`
- 收尾轮后端全量日志：`.local-acceptance/backend-suite-final-20261103.log`
- 定向回归日志：`.local-acceptance/real-user-journey-final/logs/targeted-20261103.log`
- 前端构建：`.local-acceptance/real-user-journey-final-v4/evidence/frontend-build-final4-20261103.log`
- 前端 Vitest：`.local-acceptance/real-user-journey-final-v4/evidence/frontend-vitest-final4-20261103.log`
- VLM 重校准：`.local-acceptance/vlm-recalibration-20261103.json`
- 正式项目帧证据：`.local-acceptance/real-user-journey-final-v4/evidence/final-frames/`
- **收尾轮成片与帧证据**：`.local-acceptance/real-user-journey-final-v4/evidence/final-film/`（`final-film-6c65ee08.mp4`）、`.local-acceptance/real-user-journey-final-v4/evidence/final-shot-frames/`

## 本轮代码与文档变更

- `backend/shine/worker.py`：提交保护移出外部调用、写竞争容忍、激活对账采纳。
- `backend/shine/comfyops/production_series.py`：系列心跳写竞争容忍、结果归并/释放恢复。
- `backend/shine/operator_review.py`：放弃路径的 activation/startup 所有权检查。
- `backend/shine/quality/advisory_queue.py`：引导式建议审片使用 `TrackedProvider`。
- `backend/shine/comfyops/preproduction.py`：项目 profile 与冻结修订的精确同图复用。
- 回归：`test_comfy_completion_window.py`、`test_production_series.py`、`test_v13_e2e.py`、`test_workflow_profile_scope.py`。
- 文档：本文件、`docs/TEST_REPORT_20261103.md`、`AGENTS.md` 补充。

## 收尾轮代码与文档变更（2026-11-03 晚间后段）

代码：

- `backend/shine/worker.py`：`_persisted_render_artifact()`、`_recover_from_persisted_artifact()`；`_recover_comfy_job()` 优先走已核对产物分支；`_recover_one_job()` 跳过已核对产物的交接封锁；`_handoff_state()` 区分已对账/未对账；租约循环在渲染器确证已死时释放租约；`_finalize_comfy_attempt` 保留 `adopted_persisted_artifact` 状态。
- `backend/shine/comfyops/handoff_startup.py`：`startup_record_for_lease()`、`renderer_is_gone()`、`release_dead_renderer_lease()`、`reconcile_dead_renderer_leases()`。
- `backend/shine/comfyops/preproduction.py`：`_render_bindings()`、`_plan_for_manifest()`、`revalidate_job_against_current_plan()`。
- `backend/shine/comfyops/production_series.py`：租约释放与 `COMPLETED/RELEASED` 同事务；阻塞镜头 → `NEEDS_REVIEW`；`reconcile_series` 修复未判定镜头；`_sync_items` 清空 `failure_code`。
- `backend/shine/api_operator.py`：`POST /api/v1/operator/jobs/{job_id}/preproduction/revalidate`。
- `backend/shine/h3/compiler.py`：发声合同（`shine-h3-compiler/1.1.1`）。
- `backend/shine/h3/spoken_language.py`（新增）：语言名规范化，英文提示词里不出现 CJK。
- `backend/shine/main.py`：项目语言在 API 边界规范化或 422 拒绝。
- `backend/shine/comfyops/owned_process.py`：只读 `named_group_active_processes()`；修掉 `ctypes.INT` 既有缺陷。
- 前端：`frontend/src/constants.ts` 抽出 `DIALOGUE_LANGUAGES`；`frontend/src/components/StoryboardPanel.tsx` 对白语言下拉 + 失败态 + 未知值回退；`frontend/src/components/SettingsPanel.tsx` 叙事语言改为同一枚举；`frontend/src/styles.css` 样式；`frontend/src/test/storyboard.test.tsx` 回归。

回归：

- `backend/tests/test_comfy_completion_window.py`：产物已持久化 → 不重投 → QC 通过 → PASSED；**落盘字节被篡改 → 不认领、不留认领证据 → `ARTIFACT_HASH_MISMATCH`**。
- `backend/tests/test_comfy_handoff_journal.py`：死渲染器释放租约 / 存活渲染器不释放 / **命名 Job Object 仍有成员则不释放** / **进程身份不可读则不释放**。
- `backend/tests/test_production_series.py`：陈旧系列释放死渲染器租约；无法证明则保留租约；**已完成但有阻塞镜头的系列被纠正为 `NEEDS_REVIEW`**。
- `backend/tests/test_v13_preparation.py`：重新校验成功 / 渲染合同变更时拒绝 / requirement 行重写不改变绑定 / **审片模型不算渲染绑定**。
- `backend/tests/test_h3_compiler.py`：发声合同 / **语言名以英文规范名写入提示词**。
- `backend/tests/test_storyboard.py`：**项目语言在 API 边界被规范化或明确拒绝**。
- `frontend/src/test/storyboard.test.tsx`：对白语言保存 / **保存失败如实显示** / **未知语言回退默认**。

文档：

- `docs/REAL_USER_JOURNEY_FINAL_ACCEPTANCE_V4.md`：新增第十二章（QC 前丢进程的镜头被真实恢复）。
- 本文件：原「当前阻塞」改为已解除，新增 R1–R7 收尾轮记录。
- `docs/TEST_REPORT_20261103.md`：新增收尾轮测试记录。
- `AGENTS.md`：新增收尾轮不变量。
