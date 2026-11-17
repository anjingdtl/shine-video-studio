# Shine AI 管家 V2.1 — 兼容性与自动同步矩阵

- 日期：2026-11-17
- 应用版本：`2.1.0`
- 契约版本：`shine-copilot-contracts-2026.11.17-v21`
- 数据来源：`backend/shine/support/generated/copilot_contracts.json`（由 `backend/shine/copilot/declarations.py` 生成）
- 后端路由审计：464 个方法级 `/api/v1` 路由；矩阵 44 项；`DISCOVERED` 425 条（未声明路由，恒 `executable=false`）

## 1. 可执行能力（14）

| action_id | 原业务 API | 请求模型 | 回读接口 | 权限级别 | 合同版本 | AI 状态 | 知识来源 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `setup_repair` | `POST /api/v1/setup/prepare/execute` | `shine.api_setup:PrepareRequest` | `GET /api/v1/setup/verdict` | PREVIEW | r2 | GUARDED | setup-verdict |
| `setup_reconcile` | `POST /api/v1/setup/reconcile` | `shine.api_setup:ReconcileRequest` | `GET /api/v1/setup/verdict` | PREVIEW | r2 | GUARDED | model-download-verify |
| `managed_release` | `POST /api/v1/setup/managed-processes/release` | `shine.api_setup:ManagedReleaseRequest` | `GET /api/v1/setup/verdict` | PREVIEW | r2 | GUARDED | comfy-scheduler |
| `workflow_user_copy` | `POST /api/v1/workflow-profiles` | `shine.schemas:WorkflowProfileCreate` | `GET /api/v1/workflow-profiles` | PREVIEW | r2 | GUARDED | workflow-assembly |
| `project_create` | `POST /api/v1/projects` | `shine.schemas:CreateProject` | `GET /api/v1/projects/{project_id}` | PREVIEW | r2 | GUARDED | web-workbench |
| `document_import_text` | `POST /api/v1/projects/{project_id}/documents` | 多部分表单 | `GET /api/v1/projects/{project_id}/script-basis` | PREVIEW | r2 | GUARDED | novel-import |
| `project_settings_update` | `PATCH /api/v1/projects/{project_id}/settings` | `shine.schemas:ProjectSettings` | `GET /api/v1/projects/{project_id}` | PREVIEW | r2 | GUARDED | web-workbench |
| `story_outline_generate` | `POST /api/v1/projects/{project_id}/story-preparation/outlines` | `shine.api_story:OutlineRequest` | `GET /api/v1/projects/{project_id}/story-preparation` | EXECUTE_GUARDED | r2 | GUARDED | literary-four-stages |
| `storyboard_generate` | `POST /api/v1/projects/{project_id}/director/storyboard/generate` | `shine.api_streamline:DirectorStoryboardBody` | `GET /api/v1/projects/{project_id}/director/storyboard/state` | EXECUTE_GUARDED | r2 | GUARDED | storyboard-agents |
| `production_start` | `POST /api/v1/projects/{project_id}/director/production/start` | `shine.api_streamline:ShootBody` | `GET /api/v1/production-runs/{run_id}` | EXECUTE_GUARDED | r2 | GUARDED | production-qc-delivery |
| `document_switch_basis` | `POST /api/v1/projects/{project_id}/script-basis` | `shine.api_story:ScriptBasisRequest` | `GET /api/v1/projects/{project_id}/script-basis` | PREVIEW | r2 | GUARDED | novel-import |
| `production_control` | `POST /api/v1/production-runs/{run_id}/pause|resume|cancel` | 路径 + 原状态机 | `GET /api/v1/production-runs/{run_id}` | EXECUTE_GUARDED | r2 | GUARDED | production-qc-delivery |
| `literary_scripts_generate` | `POST /api/v1/projects/{project_id}/pipeline/volumes/{volume_id}/scripts` | 路径 | `GET /api/v1/projects/{project_id}/pipeline/volumes/{volume_id}` | EXECUTE_GUARDED | r2 | GUARDED | literary-four-stages |
| `support_web_search_update` | `POST /api/v1/support/preferences` | `shine.api_support:PreferenceUpdate` | `GET /api/v1/support/preferences` | PREVIEW | r2 | GUARDED | support-agent |

## 2. 只读能力（8）

| action_id | 原业务 API | 回读接口 | 权限级别 | 状态 | 知识来源 |
| --- | --- | --- | --- | --- | --- |
| `read_project_journey` | `GET /api/v1/projects/{project_id}/journey` | 同左 | READ | READ_ONLY | director-journey |
| `read_missing_materials` | `GET /api/v1/projects/{project_id}/director/overview` | 同左 | READ | READ_ONLY | asset-center |
| `read_production_run` | `GET /api/v1/production-runs/{run_id}` | 同左 | READ | READ_ONLY | production-qc-delivery |
| `read_task_failure` | 任务/尝试台账 | jobs/job_attempts ledger | READ | READ_ONLY | support-agent |
| `search_project_knowledge` | `GET /api/v1/support/knowledge/project` | `GET /api/v1/support/knowledge/sources` | READ | READ_ONLY | copilot-auto-sync |
| `search_official_docs` | `GET /api/v1/support/knowledge` | 官方来源索引 | READ | READ_ONLY | copilot-auto-sync |
| `read_parameter_contracts` | `GET /api/v1/support/parameters/contracts` | workflow_profiles / GenerationStrategy | READ | READ_ONLY | second-pass-upscale |
| `read_video_library` | `GET /api/v1/projects/{project_id}/video-library` | 同左 | READ | READ_ONLY | video-library |

## 3. 操作矩阵（44 项）

| AI 状态 | 数量 | 含义 |
| --- | --- | --- |
| READ_ONLY | 11 | 可查询、可解释，不可修改 |
| GUARDED | 17 | 需预览并明确授权后委托原执行者 |
| HUMAN_REQUIRED | 11 | 必须人工确认，AI 只解释 |
| NOT_SUPPORTED | 5 | 当前未接入，附真实工程原因 |
| COVERED / REVIEW_REQUIRED / DISCOVERED / FORBIDDEN（矩阵内） | 0 | 不用于矩阵行；`DISCOVERED` 单列在产物中 |

矩阵与 V2 保持 44 项一致（V2.1 不新增用户操作，只改变契约来源与版本绑定）。

## 4. 知识条目（21）

| feature_id | 状态 | 验证 | 能力关联 |
| --- | --- | --- | --- |
| web-workbench | IMPLEMENTED | AUTOMATED | project_create, project_settings_update |
| director-journey | IMPLEMENTED | AUTOMATED | read_project_journey |
| novel-import | IMPLEMENTED | AUTOMATED | document_import_text, document_switch_basis |
| literary-four-stages | IMPLEMENTED | AUTOMATED | story_outline_generate, literary_scripts_generate |
| director-style | IMPLEMENTED | AUTOMATED | —（人工冻结） |
| storyboard-agents | IMPLEMENTED | AUTOMATED | storyboard_generate |
| asset-center | IMPLEMENTED | AUTOMATED | read_missing_materials |
| qwen-image | IMPLEMENTED | AUTOMATED | —（候选图人工） |
| minimax-h3 | **PARTIAL** | **HARDWARE_REQUIRED** | production_start |
| variants-quantization | IMPLEMENTED | AUTOMATED | — |
| workflow-assembly | IMPLEMENTED | AUTOMATED | workflow_user_copy |
| second-pass-upscale | IMPLEMENTED | HARDWARE_REQUIRED | read_parameter_contracts |
| comfy-scheduler | IMPLEMENTED | AUTOMATED | managed_release |
| model-download-verify | IMPLEMENTED | AUTOMATED | setup_reconcile |
| llm-routing | IMPLEMENTED | AUTOMATED | —（人工路由） |
| production-qc-delivery | IMPLEMENTED | HARDWARE_REQUIRED | production_start, production_control, read_production_run |
| windows-deployment | **PARTIAL** | **NOT_RUN** | —（S29 BLOCKED） |
| setup-verdict | IMPLEMENTED | AUTOMATED | setup_repair |
| support-agent | IMPLEMENTED | AUTOMATED | read_task_failure |
| video-library | IMPLEMENTED | AUTOMATED | read_video_library |
| copilot-auto-sync | IMPLEMENTED | AUTOMATED | search_project_knowledge, search_official_docs |

## 5. 自动同步结果（本轮）

| 同步面 | 结果 | 证据 |
| --- | --- | --- |
| 知识索引由声明生成 | 完成：21 条，字段齐全，`source_revision` 来自构建注入 | `test_knowledge_index_is_generated_with_the_required_fields` |
| 能力目录由声明生成 | 完成：14 写 + 8 读，参数顺序与 V2 合同一致 | `test_knowledge_and_capabilities_are_derived_not_hand_maintained` |
| 参数语义由原 Pydantic Schema 提供 | 完成：标题 160、描述 4000、默认值与枚举来自请求模型；执行器专属参数保留显式适配 | `test_resolved_parameter_contract_comes_from_the_request_schema` |
| 新增可选参数无需固定分支 | 完成：只改 Schema + 声明，参数即可预览并传到原执行器 | `test_new_optional_schema_parameter_flows_to_the_executor` |
| 未知参数明确拒绝 | 完成：`INVALID_ACTION_PARAMS` | `test_unknown_parameter_is_rejected_not_silently_dropped` |
| 参数白名单不再重复维护 | 完成：声明 + `copilot/params.py` 单一解析；`actions.py` 不再有固定字段 if/elif | `test_contract_check_passes_on_the_committed_build` |
| 完整 API 请求合同指纹 | 完成：路径/查询/头参数、请求体字段、类型、必填、默认、枚举、约束、嵌套结构、权限 | `test_api_contract_diff_classifies_field_changes` |
| 兼容变更不失效旧授权 | 完成：新增可选字段 → `AVAILABLE` + compatible changes | `test_api_contract_state_blocks_incompatible_and_allows_compatible` |
| 不兼容变更使旧授权过期 | 完成：类型/枚举/必填/默认/嵌套/权限/删除 → `CONTRACT_STALE` | 同上 + `test_contract_change_invalidates_a_stored_authorization` |
| 新增功能自动进入索引 | 完成：临时真实声明 → 生成 → 加载 → 查询/执行 | `test_new_feature_declaration_reaches_the_runtime` |
| 生成确定性 | 完成：两次生成字节一致 | `test_generation_is_deterministic` |
| CI 合同门禁 | 完成：`--check` 进 L1，12 项校验 | `test_contract_check_passes_on_the_committed_build` |
| 未声明路由不授权 | 完成：425 条 `DISCOVERED`，`executable=false` | `test_undeclared_or_unreviewed_api_never_executes` |
| 参数删除即 CI FAIL | 完成：`parameters removed` + `revision not bumped` | `test_contract_check_fails_when_a_parameter_is_removed` |
| 执行者/回读失配即 CI FAIL | 完成 | `test_executor_and_readback_drift_is_a_build_failure` |
| 旧授权遇合同变化 | 完成：423 `CONTRACT_CHANGED` + `EXPIRED` | `test_contract_change_invalidates_a_stored_authorization` |
| 旧委托遇合同变化 | 完成：`PAUSED` + `CONTRACT_CHANGED`，未执行 | `test_delegation_pauses_when_the_contract_changed` |
| 委托状态统一持久化 | 完成：`support_delegations` 表；新数据库会话可读 DONE/WAITING/PAUSED/FAILED/CANCELLED | `test_delegation_state_is_readable_from_a_new_database_session` |
| 计划不可变身份 | 完成：每次预览生成 `plan_id` + `plan_hash` | `test_plan_identity_is_immutable_and_old_plan_cannot_authorize_new` |
| 旧计划按钮不能执行新计划 | 完成：`PLAN_CHANGED` | 同上 |
| 重复授权整份委托幂等 | 完成：`authorization_key` 唯一；不重复建项目/开生产 | `test_repeated_authorization_never_duplicates_the_project` |
| 并发授权数据库级幂等 | 完成：唯一约束拒绝第二个同键授权 | `test_authorization_key_is_unique_at_the_database_level` |
| 进程重启不重复提交已完成步骤 | 完成：已完成步骤直接复用，不再提交 | `test_completed_step_is_not_resubmitted_after_restart` |
| 旧委托无合同 | 完成：`PAUSED` + `CONTRACT_UNVERIFIED` | `test_legacy_delegation_without_a_contract_is_not_executed` |
| 已完成委托不重提 | 完成 | `test_completed_delegation_is_never_resubmitted` |
| 知识过期不锁死执行 | 完成：`STALE` 仅影响说明，动作仍 `AVAILABLE` | `test_knowledge_staleness_does_not_lock_execution` |
| 单一动作合同失配局部降级 | 完成：构建身份 `PARTIAL`，仅该动作 `CONTRACT_STALE` | `test_single_action_contract_mismatch_degrades_only_that_action` |
| 构建身份四指纹交叉核对 | 完成：artifact/manifest 提交、知识、合同、API Schema | `test_build_identity_detects_manifest_and_artifact_commit_mismatch` |
| 全量 API 合同失配不再显示 FRESH | 完成：`STALE` + `api_matches=false` | `test_full_api_contract_mismatch_is_not_reported_fresh` |
| 产物缺失/损坏精确降级 | 完成：知识 UNKNOWN，动作 `CONTRACT_UNAVAILABLE` | `test_damaged_index_degrades_to_unknown_not_crash` |
| 运行时篡改不能自授权 | 完成：伪造动作 `CONTRACT_NOT_DECLARED` | `test_runtime_artifact_cannot_self_grant` |
| Windows 安装包携带合同 | 完成：打包脚本生成 + `--check`；实机安装 NOT_RUN | `scripts/build_test_package.ps1` |

## 6. 未支持 / 未验证（保持诚实）

| 项目 | 状态 | 原因 |
| --- | --- | --- |
| 单镜头逐字改写、字幕逐句编辑 | NOT_SUPPORTED | 无稳定参数合同与差异回读 |
| 候选图批量生成、对外发布 | NOT_SUPPORTED | 付费/GPU + 人工选片；无发布 API |
| 工作流节点图数值级改写 | NOT_SUPPORTED | 无节点级参数 schema |
| Windows 实机安装包完整流程 | NOT_RUN | 本环境无 Windows |
| 真实 ComfyUI / GPU 租约 / MiniMax-H3 出片 | NOT_RUN | 本环境无 GPU/H3 |
| 启动器 S29 离线诊断 | BLOCKED | 未实现，独立依赖 |
| 旧库迁移 | 通过（V2 无新迁移，`f180f0010004`） | 八分片包含迁移测试 |
