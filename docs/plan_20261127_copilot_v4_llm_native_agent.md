# Shine Video Studio — AI 管家 V4（LLM-Native 全链路诊断与自主项目操作）重构方案

- **文档版本**：v1.0
- **制定日期**：2026-11-27
- **适用基线**：`main`（`cd01db6`，主生产链路 8 镜全流程与 16:9 五视图定妆照已收口）
- **最高工程纪律**：**一套事实、一个裁决、一个执行者、运行时源码零修改**

## 一、现状问题与重构定调

当前主生产链路（七阶段/四阶段、素材中心、可配置 LoRA、16:9 五视图定妆照、D12 换图修订与交付）已经稳定跑通，但嵌入式 AI 管家仍停留在早期「关键词规则 + 纯文本复述 + 少量老动作」的形态，存在两大根本缺陷：

1. **非 LLM 内置能力鸡肋**：`support/planner.py` 依赖 14 组硬编码中文关键词猜意图，`support/diagnostics.py` 主要只看 Setup 环境状态，既无法理解真实口语需求，也无法定位具体项目业务问题（如哪一镜报错、D12 绑定脱节、分镜底盘过期、H3 提示词串角色、多视角 LoRA 缺失）。
2. **接入 LLM 后依然“瞎眼断手”**：
   - `support/agent.py` 在日常对话轮次禁止 LLM 调用任何工具，只能基于一份粗糙的静态 `snapshot` 输出 ≤700 tokens 的纯文本；
   - `copilot/read_tools.py` 仅有 5 个偏环境层的只读工具，完全看不到镜头表、素材绑定、失败 `attempt`、H3 编译提示词、LoRA 列表与 GPU 租约；
   - `copilot/declarations.py` 与 `support/actions.py`（单文件 3,000+ 行巨型 `if/elif`）只有 16 个早期写动作，过去几周建成的素材生图/配方/五视图、素材中心、冻结镜换图、单镜修订重拍、交付与异常对账均未接入。

### 本次 V4 重构核心定调

1. **彻底删除无 LLM 伪自然语言对话模式**：未配置 LLM 时，AI 管家收口为**确定性「项目体检与快捷定位卡片」**，不再保留关键词猜意图与模板对话；全部自然语言理解、诊断与规划彻底交给已配置的 LLM。
2. **对话态开放全链路只读查证**：已配置 LLM 时，日常对话轮次直接开放**全链路只读诊断工具**（零写入、零副作用、无需授权），让 LLM 先查清真实数据库台账、提示词与日志再给出精准结论。
3. **用户授权后拥有全量项目操作完整权限（源码零修改）**：除修改代码、执行 Shell/Git/安装脚本与替代人工艺术签收外，AI 管家在用户授权后可覆盖**全部项目业务操作**，支持单步对话卡片确认与项目级自主多步编排。

## 二、四原则架构收口总览

| 原则 | V4 重构后的唯一承载点 | 禁止事项（红线） |
|---|---|---|
| **一套事实** | 新增统一确定性体检接口 `GET /api/v1/support/health-check` + 14 个只读诊断工具（全部直调现有业务权威 reader：`director/overview`、`materialprep/service`、`asset_center`、`h3/rulebook`、`comfyops/*`） | 禁止在 `support/` 内另建第二套项目状态表或前端静态状态副本；禁止只读工具缓存陈旧快照 |
| **一个裁决** | `copilot/declarations.py` 保持唯一能力声明真源；所有写操作统一经过 `task_grant` / `support/actions.py` 唯一权限与参数裁决口，并复用各原业务状态机门禁 | 禁止绕过原业务门禁（如授权校验、幂等键、并发版本号、LoRA 基座校验）；禁止盲目重放 `UNKNOWN` 外部提交 |
| **一个执行者** | 统一收口为单一 LLM Tool-Calling 引擎（对话态只读查证循环 + 授权态 `copilot/tool_loop.py`）；`support/actions.py` 按业务域拆分注册表，每项写能力直接委托原业务执行器 | 禁止保留 `planner.py` 关键词路由与 LLM 双轨竞争；禁止在助手内另起第二套任务队列或渲染器 |
| **运行时源码零修改** | 写工具注册表硬隔离文件系统/源码/Shell/Git；继续由 `test_copilot_source_zero_write_v31.py` 守门；不触碰 `worker.py`、`comfy/client.py`、`preproduction.py` | 禁止新增任何写源码、改内置模板、执行终端命令或替代人工勾选「艺术签收」的能力 |

## 三、模块 1：删除无 LLM 伪对话，收敛为「确定性项目体检卡片」

### 3.1 退场与清理清单

- 退场 `backend/shine/support/planner.py` 中的 `_INTENT_RULES`、`detect_intent()` 及基于正则猜意图的规划路径（保留或迁移有价值的纯读取辅助函数至对应权威模块）。
- 退场 `backend/shine/support/nlu.py` 与 `diagnostics.py` 中的 `_keyword_rule()` 关键词模板问答分支。
- 退场 `backend/shine/support/agent.py` 中「无 LLM 仍拼装模板聊天回复」的分支；未配置 `support_agent` LLM 路由时，`POST /api/v1/support/sessions/{id}/messages` 明确返回 `409 COPILOT_LLM_REQUIRED`（附配置入口提示），前端不显示聊天输入框。

### 3.2 新增确定性项目体检接口（`GET /api/v1/support/health-check`）

未配 LLM 与已配 LLM 共用同一份确定性体检事实（只读、零副作用、毫秒级返回）：

| 体检维度 | 权威数据来源 | 输出阻塞项与快捷定位 |
|---|---|---|
| **基础环境与可选能力** | `comfyops/setup_summary.summary()` | H3 / 素材出图 / 画风 LoRA / 角色五视图 / LLM 路由缺失或未核验 → 跳转 `#d/setup` |
| **项目阶段与底盘过期** | `director/overview.build_overview()`、`director_storyboard_state()` | 大纲未确认、风格/方案变更导致 `stale_for_basis` → 跳转对应创作/分镜页 |
| **素材缺件与 D12 脱节** | `materialprep/service.evaluate_materials()`、`shot_binding` 投影 | 缺主素材、候选待晋升、或已晋升但冻结镜仍用旧图（0/N 回绑） → 跳转 `#d/materials` |
| **镜头失败与待核对** | `Job` / `LibraryRenderTask` / `MaterialItem` 状态 | `FAILED`、`UNKNOWN`、`RECONCILE_REQUIRED` 任务清单及精确 `error_code` → 跳转对应生产/素材页 |
| **GPU 租约与渲染器健康** | `comfyops/scheduler`、`renderer_release`、`image_renderer_state` | 悬空租约、异常退出渲染器、显存占用建议 → 跳转 `#d/setup` |

## 四、模块 2：给 LLM 装上真正的“眼睛”（14 个全链路只读诊断工具）

将只读工具集从 5 个扩展至 **14 个**，并在**日常对话态直接向 LLM 开放**（无需用户预先点击任务授权；单轮对话上限 6 次工具调用，受上下文预算严格裁剪，所有参数受当前会话/项目范围校验）：

| 工具名 | 复用的权威读取源 | 解决的实际项目排查场景 |
|---|---|---|
| `inspect_environment` | `comfyops/setup_summary` + `image_vram_advice` | 环境就绪度、缺件列表、本机显存档位建议 |
| `read_resource_inventory` | `comfyops/resource_discovery` | ComfyUI 安装目录、受管/收编状态 |
| `read_node_inventory` | 实时 `/object_info`（不拉起渲染器） | 核对 ComfyUI 实际已加载节点（H3 / Qwen / LoRA） |
| `inspect_project_overview` | `director/overview` + `director/journey` | 项目当前阶段、有效镜数（`confirmed_shot_count`）、分集/导演包/活跃运行与阻塞项 |
| `inspect_literary_and_story` | `AgentArtifact`（大纲 / 故事圣经 / 实体目录 / 分镜书） | 核查剧本角色档案、世界观设定、时代与道具定义是否完整 |
| `inspect_shots_and_bindings` | `Shot` + 镜头资产绑定 + 最新 `Job`/`Attempt` | 逐镜查看时长、台词、连续性模式（`last_frame`/`CUT`）、绑定资产、最新渲染状态与 `error_code` |
| `inspect_shot_prompt_and_evidence` | `h3/rulebook` + `Job.request` + 归档图哈希 | 读取指定镜头的真实 H3 编译提示词、编译器版本、参考图槽位，排查串角色/串道具/接拍冲突 |
| `inspect_materials_and_loras` | `materialprep/service` + `comfyops/loras` + `character_sheet` | 查看素材需求、候选与 D12 绑定投影、已装 H3/Qwen LoRA 列表、画风配方与五视图就绪状态 |
| `inspect_asset_center` | `asset_center/service` + `LibraryRenderTask` | 查看素材中心资产库、衍生版本（含 16:9 定妆照子图）与近期生图任务状态 |
| `read_production_run` | `ProductionRun` + `ProductionRunEpisode` + 子序列 | 查看生产运行状态、逐集/逐镜通过数、成片交付单号与耗时 |
| `inspect_deliveries_and_library` | `api_delivery` + `api_library` | 查看成片交付历史（v1/v2）、解码 QC 状态与人工签收状态 |
| `inspect_leases_and_workers` | `resource_leases` + `WorkerInstance` + 渲染器进程状态 | 诊断卡死租约、离线 Worker、僵尸渲染器进程 |
| `read_recent_audit_events` | `AuditLog`（按项目/实体/错误过滤） | 回溯最近 30 条关键业务事件与失败堆栈上下文 |
| `read_original_task` | `support/actions.read_under_grant` | 回读已触发写动作关联的原业务任务权威结果 |

### 对话态只读调查循环（`backend/shine/copilot/chat_investigator.py`）

- 取消 `support/agent.py` 对 LLM 禁用工具的限制与 `700 tokens` 过小输出上限（调整至合理回复预算，如 `2000 tokens`）。
- 流程：用户提问 → LLM 自主调用 1～6 个只读工具查证真实台账 → 综合真实 ID/错误码/提示词证据作答；若需要执行修复或项目推进，LLM 同时输出结构化**建议操作（单步动作卡片或项目级任务授权草案）**，交由用户确认。

## 五、模块 3：给 LLM 装上全量项目操作的“手”（用户授权后完整权限）

### 5.1 拆分 `support/actions.py` 为模块化注册表

将 149 KB 的单文件 `support/actions.py` 按业务域拆分为统一注册表协议（`ActionHandler`：`preview()` / `execute()` / `readback()` / `rollback()`）：

- `backend/shine/support/action_handlers/setup.py`（环境与资源）
- `backend/shine/support/action_handlers/project_literary.py`（项目、原文、文学与分镜）
- `backend/shine/support/action_handlers/materials.py`（项目素材准备与 D12 换图）
- `backend/shine/support/action_handlers/asset_center.py`（素材中心与 LoRA 管理）
- `backend/shine/support/action_handlers/production.py`（导演包、拍片、重拍、交付与异常恢复）

### 5.2 全量项目写操作能力矩阵（用户授权后可用）

所有动作均在 `copilot/declarations.py` 唯一声明，并直接调用原业务 API/Service 执行器：

| 业务域 | 新增 / 保留写动作 (`action_id`) | 委托的原权威执行器 / 接口 |
|---|---|---|
| **基础环境与资源** | `setup_repair`、`setup_reconcile`、`resource_install_plan`、`managed_release`、`image_runtime_validate` | `setup_preparation.enqueue`、`renderer_release`、`POST /setup/image-runtime/validate` |
| **项目与原文** | `project_create`、`project_settings_update`、`document_import_text`、`document_switch_basis` | `api_film` / `api_story` 原执行器 |
| **文学与分镜** | `story_outline_generate`、`story_outline_confirm`、`storyboard_book_confirm`（冻结风格与分集方案）、`literary_scripts_generate`、`storyboard_generate`、`storyboard_confirm`、`shot_revise`（修订单镜分镜/连续性/台词） | `api_story`、`api_director`、`api_streamline`、`api_segments`（经 `check_continuation_opening` 门禁） |
| **项目素材准备** | `material_derive`、`material_preferences_update`（切换 `character_sheet_mode`）、`material_generate`（支持 `style_preset` / `style_loras` / `character_sheet_mode`）、`material_regenerate`、`material_promote`、`shot_swap_material_binding`（D12 冻结镜显式换图） | `api_material`、`api_streamline`、`materialprep/service`、`api_segments` |
| **素材中心与 LoRA** | `asset_center_image_generate`（T2I/I2I/INPAINT/MULTIREF/WARDROBE/RGBA）、`asset_center_character_sheet_generate`（16:9 五视图定妆照）、`asset_center_version_promote`、`asset_center_assign_to_project`、`lora_import_local`、`lora_update`、`lora_delete` | `api_asset_center`、`api_loras`（经 `evaluate_style_lora_selection` 与 `_ensure_lora_not_in_use`） |
| **导演包、生产与交付** | `director_package_create`、`production_start`、`production_control`（暂停/继续/取消）、`shot_rerender`（按镜重拍）、`episode_delivery_assemble`（装配成片交付）、`workflow_user_copy`、`workflow_graph_register` | `api_director`、`api_segments`、`api_delivery`、`api_comfy` |
| **异常自愈与偏好** | `task_reconcile`（对 `UNKNOWN`/`RECONCILE_REQUIRED` 执行安全对账）、`stale_run_recover`、`support_web_search_update` | 既有对账与恢复执行器 |

### 5.3 两级平滑授权交互（消除割裂感）

1. **单步对话动作卡片（高频单步操作）**：
   - LLM 在对话中查清问题并提出单步修复动作（如「将第 8 镜场景底图从 `9616e8b2` 换为 `bca2133a`」）→ 对话流内直接渲染结构化动作卡片（展示目标对象、变更参数与预计影响）→ 用户点击「确认执行」→ 原执行器落账执行并自动把真实回执喂回对话流。
2. **项目级完整授权（自主多步闭环）**：
   - 升级现有 `task_grant` 支持「当前项目全链路自主授权」（默认绑定当前 `project_id`，保留步数上限、墙钟上限、是否允许 GPU/下载的显式开关，用户可随时一键撤销）。
   - 授权后，`copilot/tool_loop.py` 挂载全部已授权的只读 + 写工具，LLM 可连续自主完成如「定位第 8 镜双提灯 → 重生成圣殿底图 → 晋升 → 修订第 8 镜换图 → 重拍第 8 镜 → 装配 v2 成片」的完整闭环；遇到 `UNKNOWN`、业务门禁冲突或预算耗尽时按设计暂停并向用户汇报真实证据。
   - **绝对安全红线**：`acceptance_status`（人工艺术签收）、源码/脚本写入、任意系统命令、跨项目越权修改均硬拒绝。

## 六、前端改造（`ShineCopilot.tsx` 与 `CopilotSettingsPage.tsx`）

1. **未配置 LLM 视图**：隐藏聊天输入框与旧规则建议按钮，直接渲染**「项目体检面板」**（调用 `/api/v1/support/health-check`），分组列出环境、分镜、素材、渲染与租约状态及一键跳转按钮，顶部提示「配置文本模型后开启 AI 管家对话与自主项目操作」。
2. **已配置 LLM 视图**：
   - 顶部紧凑展示当前体检摘要徽标（点击展开全部体检项）；
   - 对话消息支持折叠展示「AI 查证过程」（列出本轮调用的只读工具名、查到的真实 ID 与耗时，透明可审计）；
   - 对话流内直接嵌入「单步操作确认卡片」与「开启本项目自主执行授权」一键按钮，消除旧版需要手工填 `planInputs` 表单的割裂流程。

## 七、影响面分析、测试迁移与分阶段里程碑

### 7.1 影响面与保护红线

- **受保护文件零改动**：`backend/shine/worker.py`、`backend/shine/comfy/client.py`、`backend/shine/comfyops/preproduction.py` 与 `client_identity.py` 锁定集全程不触碰，不破坏 VLM 校准指纹。
- **既有 15 个助手测试文件治理**：
  - `test_copilot_source_zero_write_v31.py`、`test_copilot_grant_durable_v31.py`、`test_copilot_objective_budget_v31.py`：核心安全与持久性红线测试，**必须保持 100% 原样通过**；
  - `test_support_agent.py`、`test_support_copilot_v2.py`、`test_support_copilot_v21.py`、`test_support_copilot_v3.py`、`shineCopilot.test.tsx`、`copilotFirstGoal.test.tsx`：原断言「无 LLM 关键词规则回复」的用例迁移为「无 LLM 确定性体检卡片 + `409 COPILOT_LLM_REQUIRED`」及「有 LLM 对话态只读工具查证」用例；
  - 每次变更 `declarations.py` 后必须重新运行 `generate_shine_copilot_contracts.py` 与 `generate_copilot_knowledge.py` 并通过 `--check`。

### 7.2 分阶段实施与验收计划

| 阶段 | 核心交付 | 验收标准 |
|---|---|---|
| **M1：清理无 LLM 伪对话 + 确定性体检 + 14 个只读工具** | 删除关键词猜意图路径；实现 `/support/health-check`；补齐 14 个只读工具并接入对话态 `chat_investigator.py` | 未配 LLM 时体检接口精准报出真实项目阻塞项且聊天接口拒收 `409`；接入 LLM 后问具体镜头/素材/提示词问题能自动调用只读工具并引用真实 ID/错误码作答 |
| **M2：`actions.py` 模块化 + 全量项目写能力接入** | 按 5 大业务域拆分 `action_handlers/`；在 `declarations.py` 注册全量项目写动作；升级项目级完整授权；同步 Copilot 契约与审计码 | 覆盖全部 7 大业务域写动作的单步执行与 `tool_loop` 多步串联单测通过；`test_copilot_source_zero_write_v31.py` 与 `generate_shine_copilot_contracts --check` 全绿 |
| **M3：前端 `ShineCopilot.tsx` 体验重塑** | 未配 LLM 体检卡片视图；已配 LLM 查证过程折叠、对话内单步动作卡片、一键项目级自主授权与撤销 | 前端 `tsc`、`vitest`（含新增 V4 助手用例）与 `vite build` 全绿；无手工表单割裂感 |
| **M4：真实 LLM + 真实项目端到端浏览器验收** | 隔离栈 + 真实 LLM（DeepSeek）+ 真实浏览器实测：① 未配 LLM 体检卡片准确性；② 已配 LLM 自主诊断真实项目问题（如查第 8 镜提示词/绑定/失败原因）；③ 用户授权后自主完成多步项目操作（推导素材/切五视图开关/生图/换图/重拍或控制生产）；全量 8 分片回归 | 全链路实测通过，证据截图与验收报告入库 `docs/ACCEPTANCE_20261127_COPILOT_V4.md`，停机后 GPU 租约与进程零残留 |
