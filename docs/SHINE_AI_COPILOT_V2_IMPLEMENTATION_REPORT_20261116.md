# Shine AI 管家 V2.0 工程实施报告

- 初版：2026-11-16；远端源码验收缺陷修复轮：2026-11-17
- 建设依据：`docs/SHINE_AI_COPILOT_V2_KNOWLEDGE_AUTOMATION_DIAGNOSTICS_PLAN_20261116.md`
  （方案提交 `3c84c9eedbed1a672ef267d42e26004fe1a7a1c5`，在 V2 分支 cherry-pick 为 `e9198d2`）
- V1 基线：`feat/shine-ai-copilot-support-agent-v1` @ `82df4b534c12609cced97c6cbaf7a1391d3632bf`（PR #55，仍是 V2 的 PR base）
- V2 分支：`feat/shine-ai-copilot-v2-full-capabilities`
- 独立 Worktree：`/workspace/.worktrees/shine-copilot-v2`
- 本轮验收基线 HEAD：`fd02e7f7e3c62d76de205ff61634c0b6cd4bab8f`；修复提交见 PR #56 最新 commit
- `origin/main` 基线：`a2ea6af`（V2 未直接改 main，也未把 V2 堆叠到 PR #55 分支）
- 迁移：V2 未新增表；复用 `f180f0010004`（V1 的动作级指纹列）

## 0. 本轮（远端验收缺陷修复）变更摘要

| 编号 | 缺陷 | 修复 | 关键文件 |
| --- | --- | --- | --- |
| P0-01 | 顶层 `project_id` 与 `params.project_id` 不一致时未拒绝，动作可能作用到错误项目 | 新增唯一规范化 `resolve_target_project()`：区分会话项目/当前项目/动作目标项目；多于一个不同值即 `ActionInvalid`；`preview_action`/`approve_action`/执行器三处使用同一个 canonical `target_project_id`；`project_create` 不继承当前项目；历史授权在 approve 时重算并置 `EXPIRED`+`TARGET_PROJECT_CONFLICT`；私有工作流跨项目复制在预览即拒绝 | `backend/shine/support/actions.py` |
| P0-02 | 前端给所有动作统一追加 `project_id`，导致不接受该字段的动作报错 | 新增 `actionPayload()`：按 Capability Registry 白名单过滤参数；作用域与执行参数分离；`managed_release` 只传 `only`；`project_create` 不传当前项目；已声明的目标项目不被静默改写；后端注册表参数改为真实白名单并按 action_id 去重覆盖 | `frontend/src/components/ShineCopilot.tsx`、`backend/shine/support/capability_catalog.py` |
| P1-01 | 自然语言只按关键词选类别，产生空参数计划 | 规则抽取标题/语言/集数/操作目标；新增受 Schema 约束的 `nlu.enrich_plan()`（只补齐已注册动作的 title/description/language，LLM 不可用静默回退，绝不编造 ID/正文） | `backend/shine/support/planner.py`、`backend/shine/support/nlu.py` |
| P1-02 | 计划只读，多步骤仍需逐项点击 | 新增受控委托信封（存 `SupportSession.context_json`，无新表、无第二执行器）：顺序推进、预算门禁、人工步骤暂停、失败停止、UNKNOWN 不重提、取消不误伤已提交任务、刷新可恢复 | `backend/shine/support/delegation.py`、`backend/shine/api_support.py` |
| P1-03 | 3 项已有正式 API 的操作未接入 | 新增真实动作 `document_switch_basis`（唯一执行者 `services.switch_script_basis`）、`production_control`（原 `production.runner` 的 pause/resume/cancel）、`literary_scripts_generate`（原 `pipeline.queue_scene_scripts`） | `actions.py`、`services.py`、`api_story.py`、`tools.py` |
| P1-04 | 知识清单 `source_commit` 为空，无法真实验证版本 | 构建注入 `SHINE_BUILD_COMMIT` 或 `BUILD_INFO.json.source_commit`；未注入→UNKNOWN，一致→FRESH，不一致→STALE；打包脚本在 staging 生成清单，`--check` 进 CI，避免自引用死循环 | `project_knowledge.py`、`backend/scripts/generate_copilot_knowledge.py`、`scripts/build_test_package.ps1`、`.github/workflows/ci.yml` |
| P1-05 | 调参预览可直接生成 `production_start` | `_preview_strategy` 只返回「待使用策略」：`proposed_action=null`、`apply_via=production_start`、`scope=NEXT_PRODUCTION_ONLY`，明确不会保存也不会自动开拍 | `backend/shine/support/parameters.py` |
| P1-06 | 失败诊断缺少动作上下文 | `_public()` 输出 `failure_context`：action_id、目标项目、原任务 ID、原错误码、阶段、目标配置、已执行结果、指纹、可否重新预览 | `backend/shine/support/actions.py` |

## 1. 架构结论（与 V2 方案逐条对照）

| V2 方案要求 | 本轮落地 | 事实来源 |
| --- | --- | --- |
| 一套事实 | 知识、计划、调参、诊断都读既有台账；未建第二个资源台账或第二套状态 | `support/project_knowledge.py`、`planner.py`、`parameters.py`、`director/overview.py` |
| 一个裁决 | 环境/生产/冻结/资产/交付仍由原有门禁裁决；AI 不产生 READY | `setup_verdict`、`package_readiness`、`GenerationStrategy`、`Policy` 原样复用 |
| 一个执行者 | 每个写动作映射到一个既有服务/路由执行者，无并行执行器 | 能力目录 `executor` 字段；`actions.py` 委托分支 |
| 代码零修改 | 运行时没有 shell/git/patch/任意 HTTP/源码写入工具；工程问题只写受控 MD | `tools.py` 注册表、`reports.py`、能力目录 `forbidden` |
| 不新增重型依赖 | 知识检索为轻量确定性打分；无 embedding、无 GPU、无网络 | `project_knowledge.search`、`official_docs.search` |

## 2. 交付模块

### 2.1 新增

| 文件 | 作用 |
| --- | --- |
| `backend/shine/support/project_knowledge.py` | 版本化项目百科：19 个真实模块条目、`IMPLEMENTED/PARTIAL/PLANNED/BLOCKED/NOT_SUPPORTED` 状态、来源路径/API/测试证据、BM25 风格离线检索、构建 SHA 绑定与 `FRESH/STALE/UNKNOWN` |
| `backend/shine/support/official_docs.py` | ComfyUI / MiniMax-H3 官方来源元数据、许可与再分发策略（`SUMMARY_ONLY` / `LINK_ONLY`）、12 条自编短摘要、H3 官方能力与 Shine 本机能力的边界表 |
| `backend/shine/support/capability_catalog.py` | 权威能力注册中心：13 个可委托动作、7 个只读能力、44 项 UI→API→执行者覆盖矩阵、人工确认/永久禁止/未接入清单与工程原因；显式条目按 action_id 覆盖通用 fallback |
| `backend/shine/support/planner.py` | 有界只读计划：建项目（标题/语言抽取）、导入原文、制作准备、素材缺口、显存释放、调参、建项目、导入原文、生产控制、分集剧本、切换底稿、故事大纲；步骤只能指向注册能力或人工步骤 |
| `backend/shine/support/nlu.py` | 受 Schema 约束的结构化补参：只补齐已注册动作允许的 title/description/language；模型不能产生 action_id 或 API 路径；失败静默回退，不编造参数 |
| `backend/shine/support/delegation.py` | 受控多步骤委托：授权范围、预算、顺序推进、人工暂停、失败停止、UNKNOWN 不重提、取消不误伤；只保存原任务引用，不建生产队列 |
| `backend/tests/test_support_copilot_v3.py` | 本轮新增 27 项：P0-01 跨项目、P0-02 参数合同、NL 抽取、导入、委托续跑/UNKNOWN、调参、知识构建版本、注册表 |
| `backend/shine/support/parameters.py` | 用户级参数合同：项目口播语言、工作流参数映射副本、生产策略（变体/加速/二次采样/RelayVSR X2）；差异预览与 schema 校验，无写操作；策略只作为「待使用策略」 |
| `backend/shine/support/knowledge_manifest.json` | 知识清单与内容哈希（构建时由脚本重新生成并记录提交号） |
| `backend/scripts/generate_copilot_knowledge.py` | 构建/发布时重建知识清单：版本、来源提交、内容哈希；`--check` 校验真实构建产物 |
| `docs/SHINE_AI_COPILOT_V2_OPERATION_MATRIX_20261116.md` | 全业务操作覆盖矩阵（469 条方法级路由的审计结论与 44 项操作映射） |

### 2.2 扩展

| 文件 | 变更 |
| --- | --- |
| `backend/shine/support/tools.py` | 动作白名单新增 `project_create`、`document_import_text`、`project_settings_update`、`story_outline_generate`、`storyboard_generate`、`production_start`；仍无 shell/源码/Git/任意 HTTP |
| `backend/shine/support/actions.py` | 新动作的预览、动作级指纹、授权、委托、回读；执行器拒绝保留原错误码（含裸 `STORYBOARD_STYLE_NOT_FROZEN`）；生产绑定 `run_id` 并读真实 Run/集状态；用户级操作不占 GPU 门禁但保留既有业务门禁 |
| `backend/shine/support/agent.py` | 意图路由（百科/诊断/操作）；答案中加入「项目百科」「操作计划」；计划中的可执行步骤进入 `recommended_actions`（仍需预览确认）；审计记录 intent/plan/知识命中 |
| `backend/shine/api_support.py` | 新增 `GET /knowledge/project`、`GET /knowledge/sources`、`GET /capabilities/actions`、`POST /plans/preview`、`GET /parameters/contracts`、`POST /parameters/preview`、`POST /parameters/confirm`；动作预览/授权扩展到 10 类 |
| `backend/shine/services.py` | 抽出唯一执行者 `create_project`、`update_project_settings`、`import_project_document`、`MAX_SCRIPT_BYTES`，供原路由与管家动作共用（路由行为不变） |
| `backend/shine/main.py`、`api_agent.py` | 路由改为调用上述服务，避免第二套导入/创建逻辑 |
| `frontend/src/components/ShineCopilot.tsx` | 在既有浮窗中展示项目百科引用、操作计划与步骤状态，可直接预览计划步骤；预览面板显示集数、门禁缺项、参数差异与待配置角色 |
| `frontend/src/styles.css` | 计划/百科样式，沿用既有主题变量 |
| `frontend/src/test/shineCopilot.test.tsx` | 新增计划 + 百科 + 计划步骤预览测试 |

### 2.3 未新增（有意为之）

- 没有新的数据库表或第二个任务系统；计划只存在于会话消息与响应中。
- 没有新的 ComfyUI/GPU/安装/工作流调度器。
- 没有浏览器 DOM 点击或硬编码选择器；前端只展示并调用正式 API。
- 没有联网抓取正文；官方知识是自编摘要 + 来源链接，联网检索仍由 V1 的显式开关控制。

## 3. 能力目录与覆盖

| 状态 | 数量 | 含义 |
| --- | --- | --- |
| GUARDED | 17 | 可由自然语言委托，但必须预览 + 用户明确授权 |
| READ_ONLY | 11 | 只读查询/百科/计划 |
| HUMAN_REQUIRED | 11 | 风格冻结、主资产、备料决策、镜头确认、审片交付等必须人工 |
| NOT_SUPPORTED | 5 | 镜头逐字编辑、候选图批量生成、字幕逐句编辑、节点图数值改写、外部发布，附工程原因 |
| 合计 | 44 | 逐页面操作矩阵（覆盖全局、导演模式、素材中心、设置、成片库） |

后端共 469 条方法级路由操作 / 431 个唯一路径（FastAPI 应用实测）。覆盖率不是「全部可代理」的同义词：未列入矩阵的操作不计入覆盖率，NOT_SUPPORTED 项不提供假动作。

## 4. 用户操作链路

```
自然语言 / 失败卡片
→ IntentRouter（百科 / 诊断 / 操作）
→ EvidenceSnapshot（既有裁决 + 台账）
→ BoundedPlanner（≤8 步、只读；规则抽取标题/语言/集数/目标）
→ NluEnricher（LLM Gateway，受 JSON Schema 约束，只补已注册动作的文本参数）
→ CapabilityRegistry（action_id + schema + 原执行者）
→ 唯一 canonical target_project_id（名称 → 实际 ID）
→ 预览（差异/资源/门禁）→ 用户明确授权
→ 受控委托（顺序推进；预算；人工步骤暂停；失败停止；UNKNOWN 不重提）
→ 原服务/原 Worker/原 Run 引擎
→ 回读原任务/原裁决 → 成功 / 失败 / 待核对
→ 无法处理：受控脱敏 MD
```

- 自然语言无法确定必要参数时（例如只说了「开始生成」），计划明确 `requires_input`，不默认扩大集数、不产生开拍动作。
- 原文导入必须由用户在本机选择文件或粘贴文本；模型不会重写、重传或替换用户正文。
- 重复点击、刷新、网络重试由 `request_key` + `attempt_no` + `atomic claim` + `target_fingerprint` 挡住；并发相同的预览会复用同一个授权单。
- 委托只保存授权范围、预算与原任务引用；刷新/重启后通过 `refresh` 读取原任务状态继续，UNKNOWN 不自动重发。
- 执行器拒绝时，动作进入 `REFUSED` 并保存原错误码；用户可在原页面重新预览。

## 5. 安全边界

- 运行时工具注册表不存在 shell / Python exec / Git / pip / 源码或节点源码写入 / 任意 HTTP / 任意文件路径。
- 报告只能写入 `<data_dir>/support-reports`，文件名白名单，24 小时指纹去重。
- 官方文档只保留自编摘要与链接；未核实许可前不整库复制，也不打包模型权重。
- 外部文本为 `REFERENCE_ONLY`；注入指令会让动作/知识条目失去执行意义。
- 未替用户确认风格冻结、主资产、备料、镜头确认与最终交付。

## 6. 验证结果摘要

- 后端定向：`tests/test_support_agent.py` 55 项 + `tests/test_support_copilot_v2.py` 24 项 + `tests/test_support_copilot_v3.py` 27 项全部通过。
- 后端八分片全量：**3025 项、0 失败、178 跳过、8/8 退出码 0**（分片 310/438/357/486/354/369/400/311）。
- 前端：79 个测试文件 / **412 项** Vitest 全部通过；`tsc -b` 与 `vite build` 通过（仅既有 chunk>500kB 提示）。
- Ruff：`shine`、`tests`、`scripts` 全部通过。
- 迁移：V2 未新增迁移；`f180f0010004` 空库升级成功（health `schema_up_to_date=true`），旧库/Windows 迁移测试继续通过。
- 浏览器：Chromium/Playwright 真实四句用户故事通过（创建项目 → 导入原文 → 第一集开拍检查 → 生产策略）；见验收报告 §7。
- 真实硬件：Windows 真机、真实 ComfyUI/GPU、真实 H3、安装包实机、启动器离线诊断（S29）仍为 NOT_RUN/BLOCKED，未伪造通过。

## 7. 明确未完成 / 边界

1. 镜头逐字改写、字幕逐句编辑、候选图批量生成、工作流节点图数值级改写、对外发布：API 或产品能力缺少稳定的自然语言合同与回读验证，标记 NOT_SUPPORTED 并给出原因；需要用户在对应页面亲自操作。
2. 原文导入：必须由用户提供本机文件或文本；AI 只调用正式导入执行者，绝不生成或替换用户小说正文。
3. 付费预算：目录标注 paid，复用既有 LLM/任务预算与 completion cap；委托预算限制 GPU/集数/付费动作次数，但未新增货币额度系统。
4. 知识版本：开发检出未注入构建提交号时状态为 UNKNOWN（明确提示）；发布构建由 `generate_copilot_knowledge.py --commit` 与 `BUILD_INFO.json` 绑定，CI 用 `--check` 校验真实清单，避免 Git 提交与清单自引用。
5. 生产暂停/继续/取消依赖真实 `ProductionRun` 状态；本环境无真实运行任务，只验证了拒绝/回读合同。
6. S29 启动器离线诊断未实现，本轮未重构启动器。
7. Windows/ComfyUI/H3 真机验收未执行（NOT_RUN）。
