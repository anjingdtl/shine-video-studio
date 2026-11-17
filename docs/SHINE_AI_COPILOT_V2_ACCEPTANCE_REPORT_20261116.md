# Shine AI 管家 V2.0 验收报告（V201—V240）

- 初版：2026-11-16；远端验收缺陷修复轮：2026-11-17
- 分支：`feat/shine-ai-copilot-v2-full-capabilities`
- 基线：V1 @ `82df4b5`（PR #55，仍 OPEN）；`origin/main` @ `a2ea6af`；本轮验收 HEAD `fd02e7f`，修复提交见 PR #56 最新 commit
- 结论：**V2 的四项能力（版本化项目百科、能力注册与自然语言代操作、智能诊断/受控调参、工程 MD 移交）已完成代码与自动化验收；本轮已修复 P0-01 跨项目身份、P0-02 前端参数注入，并补齐自然语言参数链路、受控多步骤委托、3 个新真实动作、知识构建版本绑定与调参/开拍分离。真实 Windows/ComfyUI/GPU/H3 与启动器 S29 仍为 NOT_RUN/BLOCKED，未宣称通过。**

状态口径：PASS = 有本轮自动化证据；PARTIAL = 已实现且合同有测试，但缺真机/端到端证据；NOT_RUN = 本环境不具备；BLOCKED = 客观受阻。

## 1. 测试执行

| 项目 | 结果 |
| --- | --- |
| `tests/test_support_agent.py`（V1） | 55 项全部通过 |
| `tests/test_support_copilot_v2.py`（V2） | 24 项全部通过（覆盖 V201—V240 合同） |
| `tests/test_support_copilot_v3.py`（本轮 P0/P1） | **27 项全部通过** |
| 后端八分片全量 | 见 §1.1，8/8 退出码 0 |
| 前端 Vitest | 79 个文件 / **412 项全部通过**（含 `shineCopilot.test.tsx` 16 项、动作参数合同 5 项） |
| TypeScript / Vite | `tsc -b`、`npx vite build` 通过（仅既有 chunk>500kB 提示） |
| Ruff | `shine`、`tests`、`scripts` 全部通过 |
| 迁移 | V2 无新迁移；`f180f0010004` 空库升级成功，旧库/Windows 迁移测试包含在八分片 |
| 浏览器 UI | 真实浏览器四句用户故事：见 §5；组件级真实 DOM 合同测试全部通过 |

### 1.1 后端八分片

- 命令：按测试文件均分 8 片，`pytest -q -p no:cacheprovider --shine-runner-tier full`。
- 结果：**8/8 退出码 0**，**3025 项、0 失败、178 跳过**（跳过项为 Windows/媒体/真机等环境门禁）。
- 分片用例数：310 / 438 / 357 / 486 / 354 / 369 / 400 / 311；对应跳过：10 / 38 / 50 / 19 / 6 / 37 / 1 / 17。
- 日志：`/tmp/shards/shard-1..8.log`、`shard-1..8.exit`、`shard-1..8.xml`（本轮最终代码运行）。

### 1.2 本轮 P0/P1 专项复验（2026-11-17）

| 编号 | 要求 | 结果 | 证据 |
| --- | --- | --- | --- |
| P0-01-a | 会话项目 A、动作参数 B 必须拒绝 | PASS | `test_p0_01_session_project_and_top_level_project_must_match` |
| P0-01-b | 顶层项目 A、动作参数 B 必须拒绝 | PASS | `test_p0_01_top_level_project_never_wins_over_params_project` |
| P0-01-c | 合法目标一致时正常执行且使用同一 canonical | PASS | `test_p0_01_matching_target_is_normalized_and_scoped` |
| P0-01-d | `project_create` 不误绑定当前项目 | PASS | `test_p0_01_create_does_not_bind_the_current_project` |
| P0-01-e | 历史授权不得跨项目复用 | PASS | `test_p0_01_historical_authorization_cannot_be_replayed_elsewhere`（423 / `EXPIRED`） |
| P0-01-f | 私有文档/工作流跨项目负向 | PASS | `test_p0_01_document_actions_refuse_cross_project_targets`、`test_p0_01_private_workflow_copy_refuses_another_project` |
| P0-02-a | 前端按注册表构造参数，不统一注入 `project_id` | PASS | `shineCopilot.test.tsx` `action preview contracts`（5 项） |
| P0-02-b | 作用域与执行参数分离 | PASS | 同上：仅 `scope=PROJECT` 时 `scopeProjectId` 非空 |
| P0-02-c | `managed_release` 只传 `only`；`project_create` 不带当前项目 | PASS | 同上两条显式断言 |
| P0-02-d | 已含 `project_id` 的动作不被静默改写 | PASS | 同上：显式目标优先于当前项目 |
| P0-02-e | 注册表参数来自真实白名单（后端根因） | PASS | `test_p0_02_every_registered_action_advertises_its_real_parameters` |
| P1-01 | 自然语言 → 已注册能力 → 参数抽取 → 预览 | PASS | `test_p1_nl_creates_a_real_action_with_the_spoken_title` |
| P1-02 | 只说「开始生成」不得默认全部集 | PASS | `test_p1_nl_never_defaults_to_all_episodes` |
| P1-03 | 缺参时请求补充，不编造 | PASS | `test_p1_nl_language_and_title_are_not_invented` |
| P1-04 | 导入长篇小说复用正式导入执行者 | PASS | `test_p1_import_plan_asks_for_the_users_own_text`、`test_p1_import_uses_the_formal_import_executor` |
| P1-05 | 已授权多步骤真实推进 | PASS | `test_p1_delegation_creates_the_project_then_stops` |
| P1-06 | 高成本步骤无预算必须暂停 | PASS | `test_p1_high_cost_production_pauses_without_a_budget` |
| P1-07 | 执行中不得扩大项目/集数范围 | PASS | `test_p1_delegation_scope_cannot_be_widened`（409 `SCOPE_EXCEEDED`） |
| P1-08 | 取消委托不误取消已提交任务 | PASS | `test_p1_cancel_does_not_touch_submitted_steps` |
| P1-09 | 刷新/重启后恢复委托与原任务引用 | PASS | `test_p1_delegation_resumes_from_the_stored_envelope` |
| P1-10 | UNKNOWN 不自动重提 | PASS | `test_p1_unknown_task_is_reported_but_never_resubmitted` |
| P1-11 | 调参不得变成开拍 | PASS | `test_p1_tuning_never_becomes_a_production_start` |
| P1-12 | 知识清单真实构建版本绑定 | PASS | `test_p1_knowledge_manifest_is_bound_by_the_build_script` |
| P1-13 | 新动作注册且参数有界 | PASS | `test_p1_new_actions_are_registered_and_bounded` |
| P1-14 | 重复/并发预览不产生 500 或重复授权 | PASS | `test_p1_duplicate_preview_is_adopted_not_duplicated` |
| P1-15 | 明确点名但项目无分集时不装作可检查 | PASS | `test_p1_named_episode_in_an_empty_project_is_reported_as_a_gate` |
| P1-16 | 会话无项目但台账只有一个项目时可无歧义解析「这个项目」 | PASS | `test_p1_single_project_ledger_resolves_this_project` |
| P1-17 | 台账有多个项目时不得猜目标 | PASS | `test_p1_two_projects_stay_ambiguous` |

本轮 V3 专项共 **27 项全部通过**（`backend/tests/test_support_copilot_v3.py`）。

## 2. V201—V240 逐项验收

| ID | 用例 | 状态 | 证据 |
| --- | --- | --- | --- |
| V201 | 回答当前构建真实功能与七阶段 | PASS | `test_v201_v202_v203_project_knowledge_is_version_bound`；`/support/knowledge/project?q=导演模式` 命中 `director-journey` |
| V202 | 构建 SHA 与知识索引一致 | PASS | 同上：临时清单 + `build_commit=abc1234` → `FRESH` |
| V203 | 版本不一致时明确过期 | PASS | 同上：`build_commit=def5678` → `STALE` + 「不一致」提示 |
| V204 | 官方 H3 与本机支持边界准确 | PASS | `test_v204_official_h3_is_separated_from_this_build`；Ref2VA=true，FL2VA/2K=false |
| V205 | ComfyUI 官方 API/工作流文档可溯源 | PASS | `test_v205_v206_comfy_official_references_are_licence_safe`；摘要带 `docs.comfy.org` 与 `REFERENCE_ONLY` |
| V206 | 无许可时不全量打包官方资料 | PASS | 同上；`reuse=SUMMARY_ONLY`，许可政策「不整库复制」 |
| V207 | 离线百科正常 | PASS | `test_v207_v208_offline_knowledge_answers_unknown_without_inventing`；模块无网络、无 GPU |
| V208 | 无来源时回答 UNKNOWN | PASS | 同上；`answered=false` + `unknown_reason` |
| V209 | 不泄露私有素材与密钥 | PASS | V1 隐私/报告测试 + 报告脱敏；知识条目只含仓库路径与公开 API |
| V210 | 注入不能使知识条目获得执行权 | PASS | `test_v209_v210_injection_never_becomes_a_capability`：`policy_refusal`、plan/knowledge 为空、无动作 |
| V211 | action catalog 无未说明的直通 HTTP | PASS | `test_v211_catalog_has_no_generic_http_or_source_write_action` |
| V212 | 每个核心 UI 操作对应唯一执行者或 NOT_SUPPORTED | PASS | `test_v212...`；本轮矩阵 44 项，17 GUARDED / 11 READ_ONLY / 11 HUMAN / 5 NOT_SUPPORTED；`docs/...OPERATION_MATRIX...md` |
| V213 | 只读查询无需确认 | PASS | `test_v213_reads_need_no_confirmation_but_writes_do`；写预览无 Operator 头 → 403 |
| V214 | 轻量变更 diff 正确 | PARTIAL | `test_v233...` 语言 diff；工作流映射 diff 有合同测试，但未覆盖所有 UI 设置项 |
| V215 | 高成本 GPU 生成必须明确授权 | PASS | `production_start` 为 `EXECUTE_GUARDED`；预览显示 GPU 影响；`test_production_start_binds_the_original_run...` |
| V216 | 超出授权范围停止 | PASS | `test_v216_out_of_scope_episode_is_rejected`（非本方案集 → 400，无预览） |
| V217 | 原业务门禁不被绕过 | PASS | `test_storyboard_executor_gate_is_not_bypassed`：缺冻结风格 → 原执行器 409 → 动作 `REFUSED`，保留 `STORYBOARD_STYLE_NOT_FROZEN` |
| V218 | 原始任务 ID 与结果回读 | PASS | 同一测试：`original_task_id=run-fixed-1`，`authority=GET /api/v1/production-runs/{run_id}`；V1 安装任务绑定仍通过 |
| V219 | 页面刷新不重复提交 | PASS | `test_v219_v220_repeat_and_parallel_approval_execute_once` + V1 幂等测试 |
| V220 | 并发授权不重复 | PARTIAL | 原子 `UPDATE ... WHERE status='PREVIEWED'` + 双击测试；真实多线程竞争未在本轮压测 |
| V221 | UNKNOWN 不自动重发 | PASS | `test_v221_v229_busy_or_unknown_state_blocks_production`：`UNRESOLVED_OUTCOME` → 预览 blocked，授权 423 `REFUSED` |
| V222 | 跨项目委托拒绝 | PASS | `test_v222_cross_project_document_import_is_refused`；本轮补齐三方一致性、历史授权重放、私有工作流复制（`test_p0_01_*`） |
| V223 | 未冻结风格不得代确认 | PASS | `test_v223_v224_human_confirmations_stay_human`；计划为 HUMAN_REQUIRED，无动作 |
| V224 | 主资产不可代确认 | PASS | 同上；能力目录列 `确认主资产/候选图` 为 HUMAN_REQUIRED |
| V225 | 导航不依赖 DOM 点击 | PASS | `test_v225_navigation_is_a_semantic_bridge_not_dom_clicks`；工具注册表无 DOM/click；前端只调 API |
| V226 | 不支持动作报告为 NOT_SUPPORTED | PASS | `test_v212...`；未接入清单附原因 |
| V227 | 不得代理源码/节点/模板 | PASS | `test_v211...`；`forbidden.write_source/write_node_source` |
| V228 | 付费调用显示用量预算且超限停止 | PARTIAL | 能力目录标注 `paid`；复用既有 LLM 任务预算与 completion cap；未新增货币额度系统 |
| V229 | 原执行器繁忙时安全暂停 | PASS | `test_v221_v229...`；生产/租约忙 → 预览 blocked + 授权拒绝 |
| V230 | 取消委托不误取消外部任务 | PASS | 本轮实现 `POST /sessions/{id}/delegation/cancel`：只取消未提交步骤，已提交 `SUCCEEDED` 动作保持原状（`test_p1_cancel_does_not_touch_submitted_steps`） |
| V231 | 错误任务自动带入原始 job/attempt/error_code | PASS | `test_v231_v232_v238...`；V1 失败卡片绑定测试 |
| V232 | 按需多层证据，无扫盘/GPU 竞争 | PASS | 工具轨迹 ≤8；知识/官方摘要离线静态；诊断不启服务、不全量哈希 |
| V233 | 参数变更只允许 schema 合法值 | PASS | `test_v233_v234...`；非法语言 422；策略由 `GenerationStrategy` 校验 |
| V234 | 不得修改已冻结影视风格 | PASS | 同测试：冻结后改语言，`frozen_style_version_id` 不变 |
| V235 | 工作流副本与原模板独立 | PASS | `test_v235_workflow_parameter_copy_is_independent`；源 `parameter_map` 不变；V1 副本/回滚测试 |
| V236 | 预览后配置变化旧授权失效 | PASS | `test_v236_preview_is_stale_when_the_target_facts_change`；409 `PREVIEW_STALE`，可重新预览成功 |
| V237 | 失败后能重新预览或原流程 reconcile | PASS | `test_v237_v240...`：大纲角色未配置 → REFUSED `TEXT_ROLE_NOT_READY`，随后新预览可用 |
| V238 | 已确认与猜测根因分离 | PASS | V1 报告「已确认事实 / 疑似原因」结构；`test_v231...` 检查置信度与假设 |
| V239 | 报告目录固定且脱敏，含原任务证据 | PASS | `test_v239_report_is_controlled_and_redacted`；`support-reports/`、构建提交、开发验收建议 |
| V240 | 全链路用户故事真实运行一次 | PARTIAL | 建项目 → 导入原文 → 大纲（角色缺失被拒并可恢复）已跑通；四句用户故事由后端合同 + 真实 DOM 覆盖；**真实 GPU/H3 出片 NOT_RUN** |

## 3. V1 S01—S32 回归

V2 未改变 V1 的裁决、执行器与安全边界；`test_support_agent.py` 55 项全部通过，受影响迁移/安装/释放/对账/工作流测试在八分片全量中通过。S01—S32 结论沿用 V1 验收报告（`docs/SHINE_AI_COPILOT_V1_ACCEPTANCE_REPORT_20261116.md`）：

- PASS：S01–S06、S09、S11–S21、S22–S25、S27、S30、S31
- PARTIAL：S07、S08、S10、S26、S28、S32（缺真机/量化基准）
- BLOCKED：S29（启动器离线诊断未实现）

V2 新增知识/计划/调参不改变以上状态；S29 仍不重构启动器。

## 4. 安全与权限验收

| 项目 | 结果 |
| --- | --- |
| 运行时无 shell/git/pip/源码写入/任意 HTTP | PASS（注册表 + 能力目录测试） |
| 外部文档只作参考、不作为指令 | PASS（`REFERENCE_ONLY`、注入测试） |
| 报告写入受控目录、脱敏、去重 | PASS |
| 未绕过风格/资产/生产/UNKNOWN 门禁 | PASS（`STORYBOARD_STYLE_NOT_FROZEN`、`UNRESOLVED_OUTCOME` 原样保留） |
| 用户级参数不触碰冻结风格 | PASS |
| 项目级动作唯一目标项目 | PASS（preview/approve/执行器同一 canonical；不一致直接拒绝；历史授权重算并 `EXPIRED`） |
| 委托范围不可扩大 | PASS（项目/集数越界 → `DELEGATION_REFUSED`；无预算不执行 GPU 步骤） |
| 取消不误伤外部任务 | PASS（已提交步骤保持 `SUCCEEDED`，只取消未提交步骤） |

## 5. 真实硬件与浏览器边界

| 项目 | 状态 |
| --- | --- |
| Windows 真机部署/安装包 | NOT_RUN |
| 真实 ComfyUI / GPU 显存租约 | NOT_RUN（合同与拒绝路径有测试） |
| 真实 MiniMax-H3 出片 | NOT_RUN |
| 真实浏览器 UI（四句用户故事） | PASS（Chromium headless；见 §7 与截图证据） |
| S29 启动器离线诊断 | BLOCKED |

### 5.1 浏览器端到端

- 组件级证据：`frontend/src/test/shineCopilot.test.tsx` 用真实 DOM 渲染 `ShineCopilot`，覆盖打开、诊断、预览、授权、拒权、重新预览、计划参数填写与委托。
- 真实浏览器（Chromium/Playwright）操作四句用户故事的结果记录在本文件末尾的「本轮浏览器证据」小节；无法启动真实浏览器时标记 NOT_RUN，并保留具体阻塞原因。

## 6. 结论

V2 已把「项目百科 + 能力注册 + 自然语言计划 + 受控多步骤委托 + 受控调参 + 工程移交」接入既有事实、裁决与执行链；所有动作使用同一个已校验目标项目，新增动作全部可预览、可授权、可回读，失败可重新预览，UNKNOWN 与人工确认不被绕过。本轮 P0-01、P0-02 已修复并有独立复验，P1（自然语言补参、委托续跑、3 个新动作、知识构建版本、调参/开拍分离、失败上下文）已落地。真实硬件与浏览器实机验收仍未执行，因此**未宣称全量真机通过**。代码与自动化证据达到可安排最终远端验收的状态；PR 保持 OPEN，不自行合并。

## 7. 本轮浏览器证据（Chromium / Playwright）

环境：本机后端 `uvicorn shine.main:app --port 8000` + Vite `--port 5173`，全新 SQLite（`f180f0010004`），Chromium headless 159。脚本与截图位于运行环境 `/tmp/shine-e2e/`（不入库）。

| 用户原话 | 真实 UI 结果 |
| --- | --- |
| 建立一个名叫《雾港回声》的短剧项目。 | 计划卡片显示标题来自原话；点「预览这一步」出现创建预览；点「我确认执行」后 `状态：已完成并通过权威复核`，`GET /api/v1/projects` 出现 `雾港回声` |
| 把我刚才选择的文本导入这个项目。 | 计划要求用户自己的原文（HUMAN_REQUIRED）；前端文本框提交后预览摘要为「按既有解析与去重规则导入这段原文」；确认后 `GET /api/v1/projects/{id}/documents` 返回 `["导入原文.txt"]` |
| 检查第一集是否可以开拍。 | 计划给出只读旅程 + `项目还没有分集`（BLOCKED），并说明「先生成故事大纲与分集剧本/导演分镜」；没有产生开拍动作 |
| 给第一集使用当前已验证的生产策略。 | 计划进入参数合同（项目口播语言/真实生产策略），只提供「预览参数差异并确认」；没有自动保存，也没有自动开拍 |

同一次运行中：控制台无应用错误；`127.0.0.1:8790`（桌面运行时编排器）不可达属于沙箱环境缺失，页面显示「工作台后端在线」并继续可用；未伪造 GPU/H3 结果。
