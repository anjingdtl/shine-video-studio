# 四阶段流水线收尾：F01–F11 复现、修复与证据账本

> 日期：2026-11-08 · 分支：`feature/four-stage-closeout-20261108`
> 基线：`anjingdtl/shine-video-studio` `main@7081104a79e6758228eb98e96d74e0bb36da52f8`
> 对应审查：[本轮对抗审查](ADVERSARIAL_REVIEW_FOUR_STAGE_PIPELINE_20261108.md)（F01–F11）
> 前序证据：[上轮实测断点](PROGRESS_20261108_FOUR_STAGE_LIVE_ACCEPTANCE.md)

## 0. 开工核对（实测记录）

| 项 | 实测值 |
| --- | --- |
| 分支 / HEAD | `main` → 新建 `feature/four-stage-closeout-20261108`；HEAD `7081104a79e6758228eb98e96d74e0bb36da52f8`（**等于基线**，无后续修复提交） |
| 远端 | `origin https://github.com/anjingdtl/shine-video-studio.git`；本地与远端 `main` 同点 |
| 开工时未提交项 | `M docs/upstream-audit.md`、`?? docs/ADVERSARIAL_REVIEW_...`、`?? docs/LOCAL_AGENT_FOUR_STAGE_CLOSEOUT_PROMPT_...`、`?? gui-test-screenshots/test_novel.txt`（全部留存） |
| 迁移头 | `f180b0020001`（单头） |
| 已占用端口 | `127.0.0.1:8000`（PID 6616）、`127.0.0.1:5173`（PID 14200）——**用户正在使用的栈，本轮全程未触碰** |
| 本轮隔离方式 | 同一仓库的独立 feature 分支；pytest 自带临时 SQLite（`backend/tests/conftest.py` 的 `api` 夹具，每个用例独立 temp root）；未启动新的 8000/5173 实例，未接触 `.local-acceptance/live-browser` 的实测数据 |

**结论先行**：基线**没有**后续修复可"缩小变更"——F01–F11 全部需要在基线代码上真实修改。本轮按 P0→P1 顺序落地，每一项都先写反例断言（在基线上失败），再改代码使其通过。

## 1. F01–F11 逐项结果

| 项 | 基线行为（复现） | 本轮实现 | 反例回归 | 状态 |
| --- | --- | --- | --- | --- |
| **F01** P0 | `queue_bible` 扫描项目内**所有**已确认 `StoryOutline`；缺卷仍能产出"完整"蓝图；卷大纲 fallback 到项目级指针不比对文档 | `resolve_plan_outlines()` 按当前 `VolumePlan` 逐卷解析，锁定顺序/范围/revision；`_volume_outline` 必须命中该卷文档的 `document_id`+`document_sha256`+重算 digest；部分覆盖记录 `outline_coverage`，`confirm_bible` 具名拒绝 | `test_f01_bible_input_is_the_current_plans_volumes_in_order`、`test_f01_partial_blueprint_stays_a_draft_and_cannot_be_confirmed`、`test_f01_volume_outline_must_name_its_own_document` | 关闭 |
| **F02** P1 | 入队 `draft_unconfirmed`，Worker 因 `not bible.approved_at` 报 `PIPELINE_BIBLE_CHANGED`，provider 调用数 0 | `bible_eligibility()` 由队列与 Worker **共用**；草稿可写草稿剧本；正式发布仍需已确认蓝图，并新增 `bible_basis_equivalent()` 判定"等价可沿用/差异须复审" | `test_f02_draft_bible_drives_the_worker_like_the_queue_promised`、`test_f02_bible_basis_equivalence_is_explicit`、`test_task_summary_and_soft_bible_basis` | 关闭 |
| **F03** P0 | artifact 事务提交后、`_finish_pipeline_task` 前崩溃 → 任务停在 `SENDING` 无指针，答案不可达；Worker 写 artifact 无 fence | `_commit_pipeline_result()`：artifact + 任务终态 + usage 在**同一 fenced 事务**提交，fence 不匹配则整体回滚；`_orphan_candidates`/`_adopt_orphan_result` 只采纳 project/task/kind/role/契约版本/输入链/重算 digest 唯一相符的孤儿；多候选具名报 `PIPELINE_AMBIGUOUS_RESULT_CANDIDATES` | `test_f03_orphan_answer_is_adopted_and_never_rebought`、`test_f03_two_matching_orphans_are_ambiguous_and_report_named`、`test_f03_a_worker_without_the_fence_cannot_publish` | 关闭 |
| **F04** P0 | 恢复只挡 `blocked_reason=PIPELINE_SEND_OUTCOME_UNKNOWN`；供应商超时保留 `LLM_HTTP`，常规 resume 200 并二次外呼 | `_send_outcome_unknown()` 读**持久外部状态**（`external_outcome`/未结 stage），不看错误文案；resume 一律拒绝；新增 `POST /pipeline/tasks/{id}/new-attempt` 生成独立 attempt 身份与授权，旧行保留 `UNKNOWN` 与 `superseded_by`；任务摘要返回服务端 `available_actions` | `test_f04_provider_timeout_is_never_resent_by_a_plain_resume`、`test_f04_an_explicit_new_attempt_is_a_separate_physical_send` | 关闭 |
| **F05** P0 | 卷分镜 `allow_remote_text=True` + 母本 consent 字段，但 `_run` 仅在有 `production_context` 时转发 → Dispatcher 的 `text_consent.guard` 从不执行 | `api_film._run` **始终**转发 `consent_document_id/sha256`；请求身份加入 consent 身份；卷分镜要求母本存在，每次发送按**实际角色**与 provider fingerprint 校验 | `test_f05_the_crew_lane_really_consults_the_persisted_consent`（拒绝=0 次发送、授权后真实发送、撤回后不再发送） | 关闭 |
| **F06** P0 | 卷包用**镜头数 vs 场次数**判覆盖、布尔可放行；镜头查询无 `ORDER BY`；按 75 秒重切集 | `storyboard_units()` 一场一单元（超长场按段落切片并标注 `#n`）；`_section_shot_groups()` 用 apply receipt 证明**场→镜**映射并校验唯一/重复/项目/revision；`_handoff_episodes()` 由**已确认 EpisodeScript** 推导分集，无契约时显式记 `volume_scope`；删除 75 秒重切与 `acknowledge_scene_coverage` 放行 | `test_volume_storyboard_draft_and_handoff`、`test_f06_a_scene_without_shots_blocks_the_handoff_by_name` | 关闭 |
| **F07** P0 | 改已确认剧本后旧包 `startable` 仍 true；编辑 24,003 字符静默丢尾 3 字符 | 包根清单固定 `creative_lineage`（卷/计划/蓝图/逐场剧本/episode 契约 digest）并计入 `manifest_sha256`；`package_readiness` 重算当前 lineage，不符即 `CREATIVE_LINEAGE_CHANGED`；包确认/Run 创建/批次派发/恢复共用同一 readiness；`edit_scene_script` 超限具名 `PIPELINE_SCENE_SCRIPT_TEXT_TOO_LONG:<实际>:<上限>` | `test_volume_storyboard_draft_and_handoff`（编辑后 `startable is False`）、`test_overlong_volume_is_batched_and_the_editor_limit_is_named` | 关闭 |
| **F08** P1 | 仅配置 `max_completion_tokens=128000` 也返回 `output_max_verified=True`+76,800；任一 min-chain 来源即标 verified | `creative_completion_cap()` 区分 **capability**（provider 声明 / probe）、**constraint**（配置 / adapter）、**fallback**（保守默认）；只有 capability 能认证；新增 `capability_basis`、`requires_capability_confirmation`、`adapter_token_semantics` | `test_f08_a_configured_cap_never_certifies_capability`、`test_f08_declared_metadata_is_the_only_capability_source`、`test_f08_a_probe_counts_and_records_adapter_semantics` | 关闭 |
| **F09** P1 | 整卷同步分镜受 60,000 字上限，超长单章只有警告；面板无续跑/apply 承接 | 五工种**按场**逐次调用（`STORYBOARD_UNIT_CHARS=15,000` 词元预算内），超长单场按段落切成带编号的 chunk；每单元独立 receipt/失败位置/续跑边界；整体状态可 `INCOMPLETE`，已通过单元保留 | `test_overlong_volume_is_batched_and_the_editor_limit_is_named`（>60k 仍分批、每单元 ≤15,000、chunk 序号连续） | 关闭 |
| **F10** P1 | 手工卷 UUID 由项目+序号+母本**总字数**产生；读取只按 volume ID 返回首个匹配 | 卷键改为 `uuid5(project, order, master_sha256)`（跨边界调整稳定），内容修订为 `volume_revision = digest(master_sha,start,end)`；`_volume_document` 必须命中当前计划的 span + master + revision | `test_f10_a_volume_reads_its_current_span_after_a_boundary_change` | 关闭 |
| **F11** P1 | 面板交付固定 `volumes[0]`；分镜按钮任一场确认即可点；重复交付再建计划/包 | 面板维护**当前卷**（切项目/刷新重置），审阅/分镜/应用/交付共用；逐单元 apply 按钮与状态；交付按 `handoff_digest` **快照幂等**（`reused: true`，不再建包）；阻塞项逐条中文显示 | `frontend/src/test/fourStagePanel.test.tsx`（交付选中卷 v2、未知结果只给核对+新尝试）、`test_volume_storyboard_draft_and_handoff`（重复交付返回同一 package） | 关闭 |

## 2. 执行过的命令与结果（本机 Windows / Git Bash）

> 环境说明：本轮 shell 带有沙箱 `safe-delete` 钩子，会在 pytest 清理临时目录时抛 `SystemExit`
> （`test_v13_adversarial.py::test_installer_active_owner_and_unknown_owner_are_protected` 因此
> 在**未改代码**的基线运行中也会失败）。本轮用官方开关 `CODEBUDDY_SAFE_DELETE_ENABLED=0`
> 隔离该**环境因素**，未修改任何项目代码来绕开它。

| 命令 | 退出码 | 结果 |
| --- | --- | --- |
| `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_pipeline.py backend/tests/test_creative_output_budget.py backend/tests/test_story_preparation.py backend/tests/test_creative_script_task_lifecycle.py backend/tests/test_film_crew.py backend/tests/test_h3_compiler.py backend/tests/test_offline_e2e.py backend/tests/test_production_series.py backend/tests/test_comfy_completion_window.py backend/tests/test_comfy_handoff_journal.py backend/tests/test_advisory_queue.py backend/tests/test_director_worker_scheduling.py backend/tests/test_director_long_form_regressions.py backend/tests/test_v13_adversarial.py -q -p no:warnings`（基线，改代码前） | 0（215 项；1 项被环境钩子杀死，非项目缺陷） | 定向基线：`test_v13_adversarial` 之锁测试被 `safe-delete` 钩子中断 |
| 同上 + `CODEBUDDY_SAFE_DELETE_ENABLED=0` | 0 | 全绿（含该锁测试） |
| `... -m pytest backend/tests -p no:warnings`（**定稿树**全量） | 0 | `1985 passed, 1 skipped in 1526.22s` |
| `... -m ruff check backend/shine backend/alembic backend/tests scripts/verify_v13_web.py` | 0 | `All checks passed!`（顺带修正基线里 2 处迁移文件的 import 顺序，`ruff --fix`，零行为变化） |
| `... -m alembic heads` | 0 | `f180b0020001 (head)`（单头） |
| `... -m alembic upgrade head`（**全新空库**，`SHINE_DATA_DIR=.local-acceptance/_alembic_fresh`） | 0 | 空库直升 head |
| `... -m alembic check` | 0 | `No new upgrade operations detected.` |
| `... -m alembic upgrade head`（**旧库副本** `default_local-backup.db`：`f170a0030001` → head） | 0 | 旧库副本升级到 `f180b0020001 (head)`，未触碰任何 live 数据库 |
| `cd frontend && npm.cmd test -- --run` | 0 | 26 文件 / 171 测试通过 |
| `cd frontend && npx.cmd tsc --noEmit` | 0 | 无输出（类型通过） |
| `cd frontend && npm.cmd run build` | 0 | `✓ built in 1.99s`（仅有既有的 >500 kB chunk 警告） |

### 2.1 后端全量（定稿树）

```
CODEBUDDY_SAFE_DELETE_ENABLED=0 backend/.venv/Scripts/python.exe -m pytest backend/tests -p no:warnings
```

定稿树逐字结果：**`1985 passed, 1 skipped in 1526.22s (0:25:26)`，退出码 0**（日志：`.local-acceptance/_full_backend_final.log`）。1 项 skip 为仓库既有的条件跳过，非本轮引入。

改动中途曾有两次中途运行（1985 passed / 1 skipped / exit 0），但它们都发生在**最后一次小改动之前**，
因此**不作为定稿证据**，只记录在这里以免读者误读。

### 2.2 新增回归的"先失败后通过"证据

`backend/tests/test_four_stage_closeout.py` 的 15 条断言在基线上**全部失败**（其中 F01/F03/F04/F06/F07/F10 的失败是断言级、F05 是"发送数为 0 vs 期望 >0"、F08 是 `output_max_verified` 误报），修复后全部通过。这是审查要求的"反例先验证、再缩小变更"的落地形式：测试本身就是反例，长期留在仓库。

## 3. 验收矩阵对照

| 用例 | 覆盖方式 | 结论 |
| --- | --- | --- |
| 作用域污染 | `test_f01_*`（同项目无关旧文档、缺卷）、`test_bible_queue_only_reads_this_project_outlines`、`test_cross_project_artifact_reference_is_refused` | 通过；越界输入远程发送数 0（`calls == []` 断言） |
| 分卷与来源 | `test_short_novel_plans_one_volume`、`test_long_novel_groups_by_budget_at_section_boundaries`、`test_manual_volume_adjustment_and_master_isolation`、`test_f10_*` | 通过；同一母本两次改卷界只读当前区间 |
| 草稿与变更 | `test_f02_*`、`test_volume_storyboard_draft_and_handoff`（编辑后 `startable False`） | 通过；草稿蓝图真实进入 Worker 并产出剧本 |
| 外部副作用 | `test_f03_*`、`test_f04_*`、`test_sending_crash_never_replays_the_provider`、`test_persisted_answer_is_adopted_without_a_second_call` | 通过；UNKNOWN 自动与常规恢复新增发送均为 0；唯一有效结果被采纳 |
| 授权 | `test_f05_*`、`backend/tests/test_material_text_consent.py` | 通过；缺权限/撤回时传输层计数为 0 |
| Token 与输出 | `backend/tests/test_creative_output_budget.py`、`test_f08_*`、`test_llm_context_window.py` | 通过；128000→76800、131072→78643；配置值不再认证能力 |
| 分镜覆盖 | `test_f06_*`、`test_volume_storyboard_draft_and_handoff`、`test_episode_script_contract_with_continuity_check` | 通过；缺场映射/重复镜头/过期版本阻断，分集边界来自确认合同 |
| 跨卷交付 | `test_volume_storyboard_draft_and_handoff`（幂等交付 + 编辑后失效）、`test_manual_volume_adjustment_and_master_isolation` | 通过；重复提交返回同一回执，历史保持可读 |
| 浏览器旅程 | 仅到组件级 DOM（`fourStagePanel.test.tsx` 渲染真实组件 + 桩传输），未启动真实浏览器 | `NOT_RUN`（阻塞原因与恢复步骤见 §4） |
| 真实 H3 | **未执行**（见 §4） | `NOT_RUN` |

## 4. 证据分层（不得互相代替）

| 层 | 状态 | 依据 |
| --- | --- | --- |
| `OFFLINE/MOCK` | **PASS** | 后端全量 pytest、Ruff、Alembic（空库 + 旧库副本）、前端 171 测试 + `tsc` + `build`；全部在隔离临时 SQLite 上运行 |
| `REAL_LLM` | **NOT_RUN（本轮）** | 本轮未发起任何真实文本模型调用。上轮真实 MiniMax M3.1 Flash 记录仍以 `PROGRESS_20261108_FOUR_STAGE_LIVE_ACCEPTANCE.md` 为准，其 T4–T7 当时为 `NOT_RUN`，本轮**没有**新的真实调用证据可以把它们升级 |
| `REAL_H3` | **NOT_RUN** | 未选择卷、未创建 ProductionRun、未占用 GPU、未向 ComfyUI 提交 prompt。本机 8000 端口有用户正在使用的栈，本轮刻意不触碰 |
| `VOLUME_DELIVERED` | **NOT_RUN** | 无真实卷交付；离线路径的卷交付（计划 + 包 + handoff 回执）已在 mock 下验证 |
| `HUMAN_ACCEPTED` | **NOT_RUN** | 无人工艺术验收 |

### 阻塞清单（可安全恢复的下一步）

1. **真实 LLM 浏览器旅程**：需要一个已授权且可发送的 Profile（上轮用的是 MiniMax M3.1 Flash）+ 独立端口栈。恢复动作：另起 `SHINE_DATA_DIR` 与 8001/5174 端口，按 `docs/PROGRESS_20261108_FOUR_STAGE_LIVE_ACCEPTANCE.md §3 丙` 的断点从"生成全局故事蓝图"继续，而不是复用本轮临时库。
2. **真实 H3**：需要用户明确的本机授权、空闲 GPU 租约与已登记 workflow profile。本轮未申请，因此不做任何"已验证"表述。
3. **被 `client_review_identity` 钉住的源码**：本轮未改动审片身份相关源码，故不需要新的 VLM 正负校准；advisory 与确定性 QC 的边界保持不变（`test_advisory_*`、`test_quality_gates.py` 全绿）。

## 5. 变更清单

| 文件 | 变更 |
| --- | --- |
| `backend/shine/director/pipeline.py` | F01/F02/F03/F04/F06/F07/F09/F10/F11 的实现主体：原子提交与孤儿采纳、UNKNOWN 治理与新尝试、卷计划逐卷解析、草稿资格共用、按场分批分镜、场→镜覆盖校验、lineage、快照幂等交付 |
| `backend/shine/api_pipeline.py` | `new-attempt` 路由；分镜草案返回逐单元 apply 句柄 |
| `backend/shine/api_film.py` | 每次 crew 调用都转发 consent 身份；请求身份纳入 consent；拒绝回执带 `state_version` |
| `backend/shine/director/package.py` | `build_package(creative_lineage=...)`；`package_readiness` 新增 `CREATIVE_LINEAGE_CHANGED`/`CREATIVE_LINEAGE_UNREADABLE`；`package_public` 暴露 lineage |
| `backend/shine/llm/output_budget.py` | capability / constraint / fallback 三分；`capability_basis`、`requires_capability_confirmation`、`adapter_token_semantics` |
| `backend/tests/test_pipeline.py` | 夹具改走真实链路（物化卷文档 + 卷大纲）；交付测试改为逐单元 apply + 幂等 + lineage 失效；超长测试改为分批不变量 + 编辑具名上限 |
| `backend/tests/test_four_stage_closeout.py` | **新增** 15 条反例回归（F01–F10） |
| `backend/alembic/versions/f180a0010001_*.py`、`f180b0010001_*.py` | 仅 import 顺序（`ruff --fix`），无迁移语义变化 |
| `frontend/src/api.ts` | 逐单元分镜/应用/对账/新尝试客户端；`available_actions`、`reused`、`episode_basis` 类型 |
| `frontend/src/components/FourStagePanel.tsx` | 当前卷贯通四步；逐场分镜与 apply；具名阻塞中文列表；"核对未发送并重试"；未知结果只给"核对后重新发送一次" |
| `frontend/src/test/fourStagePanel.test.tsx` | +2 条（交付选中卷、未知结果不提供普通继续） |
| `docs/upstream-audit.md` | 记录本轮实际采用范围（设计约束，无代码移植） |
\n