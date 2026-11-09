# Shine 视频工作台｜LLM 多模型配置与 Agent 路由改造方案

**性质**：技术建设方案（未实施）；**核对日期**：2026-11-09，Asia/Singapore。  
**远端仓**：[anjingdtl/shine-video-studio](https://github.com/anjingdtl/shine-video-studio)  
**核对基线**：`main` → `888956edeac6e98f032f0e883e3e83d1a7ca92bb`（通过 GitHub `main...SHA` 比较确认为 identical）。  
**审阅方式**：远端提交、API 与前端源文件静态审阅；**未运行**本地 Windows 实机测试、真实模型调用，也未改动业务代码。  
**优先级**：P0；目标为“不推翻既有网关、不迁移密钥、不影响 H3 视频生成”的用户体验与调用可靠性升级。

## 1. 决策摘要

- **架构**：保留 `ProviderProfile`（当前一个配置 = 一组 API 地址 + Model ID + 密钥引用 + 生成参数）和 `AgentRoute`（一个角色指向一个 Profile）作为 v1 运行时权威数据；沿用 `LLMGateway`、安全密钥存储、资源租约、审计和现有 AgentTaskRecord，不另造 Agent 执行系统。
- **交互**：提供独立的 **AI 模型管理**工作区，一级展示“模型库”和“Agent 分配”两部分；界面统一服务“设置”“导演基础配置”“新机向导”。日常用户只面对“添加模型 → 检查模型 → 分配工作”的流程；协议、token、超时等折叠进高级设置。
- **模型维度**：支持多个不同平台、不同 URL、不同 Model ID 并存；同一 API 下增加模型，采用后端安全复制配置/凭据，不让 API Key 回传浏览器。不将模型 ID 当作服务商，亦不凭模型名称猜测接口 URL。
- **分配维度**：以每个 **实际后端调用 role_id** 为准，按角色单独选择 Profile；支持多选角色的事务性批量绑定、只填未分配角色、按场景建议、变更预览；**不自动切换、轮询或回退到另一个模型**。
- **新机**：首次运行可只配置一套真实 LLM 走通最短路径，后续再细分角色；保留配置、保留模型检查证据边界；新机器的 DPAPI 密钥不能直接当作可移植凭据。
- **分阶段**：P0 做已支持功能的整合与缺口修复，尽量不改数据库；P1 可选择性建设“API 连接账号 / 其下多模型”的物理双层存储，必须单独迁移和复核，不作为 P0 验收前置条件。

## 2. 实际仓库基线与已确认问题

| 层级 | 已有代码 / 事实 | 影响 / 改造判定 |
|---|---|---|
| 数据模型 | `backend/shine/models.py`：`ProviderProfile` 存 `name/kind/base_url/model/api_key_ref/capabilities/generation_options/timeout/...`；`AgentRoute` 按 `role` 唯一索引 | 多模型、多角色数据底座**已经存在**；P0 不应复制一套新表 |
| Provider | `backend/shine/llm/gateway.py`：OpenAI-compatible、Anthropic Messages、Ollama、Mock；`complete_for_role` 从角色路由取模型 | 只调整配置和路由；不能更换 H3 视频核心；运行时失配应明确报错而不是默默 fallback |
| 模型 API | `backend/shine/api_llm.py`：创建/更新/删除、密钥写入/轮换、连接测试、真实生成验收、单角色及批量路由 | API 基础充分；扩展批量差异分配与可编辑资料，尽量复用已有事务逻辑 |
| 安全 | `backend/shine/llm/secrets.py`：Windows DPAPI、其他系统 0600 文件，密钥位于 SQLite 外；轮换写新后切换 | 禁止明文导出/展示；同配置派生新 Model ID 应服务端单独加密复制密钥；不能复用同一 secret ref 后让删除动作误删其他模型凭据 |
| 当前管理 UI | `frontend/src/components/LLMConfiguration.tsx`：页面平铺输入框，新增 API/Model ID 需手填；仅暴露少数角色“用作…”按钮；已存在列表，但缺完整角色矩阵与直观编辑 | 日常多 LLM 管理操作密度高且不易看出谁调用谁；删除被路由的 Profile 后端返回 409，但界面缺先迁移路由指引 |
| UI 包装 | `frontend/src/components/LLMSetupPanel.tsx` 复用 `LLMConfiguration`，同时提供“一键覆盖全部文本工种”；`SettingsPanel.tsx` 和 `DirectorSetupPanel.tsx` 内复用 | 同一管理器应统一多入口；不应在普通用户的“配置模型”里同时暴露整块高级 ComfyUI 工作流 JSON |
| 新机就绪 | `backend/shine/comfyops/llm_readiness.py` 的 `TEXT_ROLES` 当前仅有 12 项，区分 `CONNECTION_READY` / `GENERATION_READY`，并通过配置指纹识别过期证据 | 就绪门禁已有，必须复用；不应用“连接可达”冒充“真实生成可用” |
| **角色漏项** | `backend/shine/schemas.py` 的 `AgentRole` 包含 `four_stage_bible_run`、`four_stage_scene_script_run`；`backend/shine/director/pipeline.py` 确实通过 `profile_for_role` 调用这两项，但不在上面的 12 项清单里 | **P0 优先修复**：新机“用于全部文本工种”不覆盖四阶段专用角色，可能导致后续四阶段运行提示未配置 |
| 模态混用 | `LLMConfiguration.tsx` 把 `vlm_reviewer`、`asr_reviewer` 也放入通用 LLM 的快捷按钮；但本地审片语音/视觉有独立 provider/runtime 和授权机制 | 文本模型角色与 VLM / ASR 路由必须分组并各自验证能力；不可让文本模型配置 UI 暗示普通聊天 LLM 可以直接进行声画推理 |
| 长文预算 | `backend/shine/llm/output_budget.py`、`gateway.py` 已有上下文估算、生成前溢出阻止、创作轮次 60% 上限与探针证据 | 必须保留；不要在 UI 硬编码“所有模型都有 1M / 128K”，只展示确证值与来源 |
| 任务审计 | `AgentTaskRecord` 已记录 `model_profile_id`、`model_id`、输入哈希/状态等，四阶段还记录 provider 指纹 | 配置变更必须遵守在途任务的身份/对账合同；不允许同一任务悄悄中途换模型 |
| 新机配置 | `backend/shine/api_setup.py` 提供 `/setup/llm-readiness`；`DirectorSetupPanel.tsx` 展示摘要；`scripts/install_once.ps1`、`scripts/bootstrap.ps1` 管新机安装 | LLM 是独立联网文本能力；不能让配置 LLM 触发 60GB 模型下载，也不能因 H3 未安装而禁止先录 API |

**注意**：这是确认过的代码路径/行为，不等于本次改造已经完成或真实 LLM 已验收。

## 3. 用户工作流与信息架构

### 3.1 单一用户入口

```
导演基础配置 → ② AI 文本模型 → [AI 模型管理]
设置 → AI 模型管理 → [同一组件与同一后端数据]
新机首次引导 → 加入一个真实模型 → 测试 → 给入门场景分配角色 → 继续

AI 模型管理
 ├─ 模型库：搜索 / 添加 / 复制 / 编辑 / 禁用 / 检查 / 删除
 ├─ Agent 分配：按实际工作阶段分组、逐角色指定、批量指定、变更预览
 └─ 高级（折叠）：协议 URL、模型能力证据、token、思考深度、超时、重试、密钥维护
```

### 3.2 模型库卡片（默认简洁）

每卡显示：**自定义名称**、提供商/协议、**Model ID**、服务地址简写、密钥状态（不显示密钥）、`已连接` / `可生成 JSON` / `证据过期` / `需处理`（分别基于真实证据）、最后检查时间、被多少个 Agent 使用。操作仅留 `测试`、`编辑`、`分配`、`···`。高级信息按需展开，不能将 Mock 的测试结果标“真实可用”。

新增流程：
1. 选择接口协议 `OpenAI 兼容`、`Anthropic 原生`、`Ollama 本地`；演示 Mock 只能从“开发测试”高级入口创建。
2. 录入或确认 Base URL、API Key；键入 Model ID，或在“读取模型列表”可用时从**服务实际返回**的 ID 选择。允许模型目录不支持时手工输入，但不能宣称已在目录确认。
3. 选择“仅保存”或“保存并检查”；连接测试和少量真实 JSON 生成测试分开。真实调用前提示可能计费，使用无项目素材的固定探针。
4. 保存后可以单独选择 Agent，或在明确确认下“分配到当前未配置工种”。

修改流程：支持编辑 `name/kind/base_url/model/timeout/retry/temperature/generation_options`（按接口能力展示），键值单独轮换。修改后清理对应过时证据；页面应基于服务端读回显示是否保存成功，不能凭 HTTP 超时断言“没有写入”。

同一账号添加另一模型：`复制模型配置` → 输入新昵称与 Model ID → **后端**读取旧密钥并重新加密写入新 profile 的独立 secret 文件（仅用户明确确认时），避免前端读取明文；旧/新模型可独立修改和删除。此 P0 复制方式**不提供共享轮换**，界面提示“原模型更新密钥不会自动同步到复制的模型”。

### 3.3 Agent 分配（主视图）

用表格代替 Profile 卡片旁的大量“用作…”按钮：

| 工作阶段 | 实际角色 `role_id` | 建议在 UI 显示 | 备注 |
|---|---|---|---|
| 长文生产 | `scriptwriter`、`four_stage_bible_run`、`four_stage_scene_script_run` | 故事大纲、全剧蓝图、分场剧本 | 后两项是实际现存、此前新机清单漏掉的独立角色 |
| 剧本与镜头规划 | `script_planner`、`shot_planner` | 剧本规划、镜头规划 | 按实际 API 调用分配 |
| 影视制作团队 | `director`、`cinematographer`、`continuity_supervisor`、`sound_director`、`editor`、`producer` | 导演、摄影、场记、声音、剪辑、制片 | 所有角色可使用同一个 Profile，也可各自不同 |
| 工程/审查 | `comfyops`、`repair_planner`、`qc_reviewer` | 环境助理、修复规划、文本质量审核 | 保持与声画审片不同的类型说明 |
| 专业多模态（单独） | `vlm_reviewer`、`asr_reviewer` | 画面审片、语音核对 | 不并入“文本模型全部分配”；按现有本地审片/语音能力门禁独立展示 |

矩阵行：`角色 | 当前模型 | 状态 | 更换（下拉）`。顶部提供“将模型分配给勾选角色”“为未配置的角色批量补齐”“场景推荐”三种操作。**覆写已有绑定必须显示影响预览并由用户确认**。不提供隐式随机模型、静默替换或未授权远端上传。

同一个 Profile 可以指派多个角色；一个 role_id 同时只能有一个当前默认 Profile。若未来支持主备，必须设计显式故障转移策略、同意/费用/数据出境检查及任务快照，**本期不做**。

## 4. P0 后端改动（尽量零数据库迁移）

### 4.1 权威文本角色目录

- 在 `backend/shine/llm/role_catalog.py` 增加唯一的 typed 文本角色清单：14 个 `role_id`、中文名、分组、业务入口、是否必需、能力类型、依赖阶段。`llm_readiness.py`、`api_llm.py`、前端角色选项统一从此目录生成，避免常量分叉。
- 清单应通过静态搜索/单测与**实际 `profile_for_role` / `complete_for_role` 调用点**比对；四阶段两个专用角色不能遗漏；未来新增 Agent 需同时加入契约。
- `GET /api/v1/llm/roles` 返回文本角色目录、当前 route、profile 有效性、检查/生成证据、涉及的产品阶段。多模态 VLM/ASR 单列能力接口，不能冒充文本 Agent。

### 4.2 保留和扩展模型 API

现有 endpoint 原样保留以兼容旧界面与脚本：

- `GET/POST /api/v1/provider-profiles`、`PUT/DELETE /api/v1/provider-profiles/{id}`
- `PUT/DELETE /api/v1/provider-profiles/{id}/key`
- `POST /api/v1/provider-profiles/{id}/test`、`POST .../test-generation`
- `GET/PUT /api/v1/agent-routes`、`PUT /api/v1/agent-routes/bulk`、`DELETE /api/v1/agent-routes/{role}`
- `GET /api/v1/setup/llm-readiness`

建议最小增量 endpoint：

| Endpoint | 核心契约 |
|---|---|
| `GET /api/v1/llm/overview` | 整合可显示的模型/路由/角色/各场景 readiness；只读、无 API Key 明文 |
| `GET /api/v1/llm/roles` | 后端权威角色目录；前端不再硬编码决定哪些角色需要配置 |
| `POST /api/v1/provider-profiles/{id}/duplicate` | `{name, model, copy_credential, generation_options?}`；服务端可选择性复制加密密钥到**新** secret ref，事务回滚时清理新密文 |
| `POST /api/v1/agent-routes/preview` | 传目标映射，返回将新设/覆写/移除的角色与在途任务影响；无副作用 |
| `PUT /api/v1/agent-routes/batch` | `{updates: [{role, provider_profile_id}], expected_revision?}`；单事务变更不同角色至不同 Profile，校验角色合法性、Profile enabled/kind/密钥能力，乐观锁防并发旧配置覆写 |
| `GET /api/v1/llm/readiness?workflow=<scenario>` | 按“导入文本/长文四阶段/影视团队/真实生产”等用途返回只涵盖相关角色的 readiness；**保留**旧 `/setup/llm-readiness` 兼容，但要让它包含全部 14 个真实文本角色，明确连接与生成测试状态 |

`batch` 不得改变现在的 `bulk` 语义；应允许一次发起异构映射，保证全部成功或全部回滚。删除被使用的模型仍保持 409，但提供 `roles_in_use` 和明确“先改派/停用”的 UI 指引；彻底删除无引用且无在途任务后才清理密钥。路由到禁用/Mock/无凭据 Profile 时，非演示真实任务要 fail closed，并给出清晰错误，而不是悄悄走本地 deterministic 或其他模型。

### 4.3 探测与状态边界

- 独立显示：**已保存**、**连接/认证**、**模型目录可列出**、**结构化生成成功**、**业务能力/模型效果未验证**。成功的 `/models` 不代表可生成；JSON 探针通过不代表实际剧本质量。
- 对部分第三方 OpenAI 兼容接口：若 `/models` 不支持，按“模型目录不可验证”如实记录，允许用户显式启动固定无素材生成探针；只有生成成功并满足安全/身份条件，才可显示 `GENERATION_VERIFIED_CATALOG_UNAVAILABLE`，不得伪造“列表确认”。更改既有 readiness 等级前先写回归契约。
- 指纹扩充时包含真正影响调用的 endpoint、Model ID、凭据版本、生成参数；配置变更触发状态失效。已验证的 token 上限标注官方/服务商/用户声明来源；不能默认任何模型有 1M 上下文和 128K 输出。
- 沿用 `output_budget.py` 的创作任务 60% 一轮输出上限、预发送窗口判定、失败分类、未知结果对账；不可为适配前端而移除。

### 4.4 改派对在途任务的影响

当前 `AgentTaskRecord` 已保存选用的模型 ID 与 profile ID，部分任务有供应商指纹/远程文本同意。改派时不得把老队列项目不经确认地丢给新的模型：

1. Preview 告知有哪些 `QUEUED/RUNNING/NEEDS_REVIEW` 任务引用被改派角色；当前调用结束不受路由 UI 刷新的影响。
2. P0 的**保守兼容方案**：对会被影响的排队/运行任务，阻止危险改派或要求先暂停/对账；对于原本已有一致性检查的任务，沿用 `PROVIDER_CHANGED` 一类具名状态，不自动重发。
3. P1 才可把“冻结的 LLM 调用快照（profile_id/config_hash/密钥版本/同意身份）”明确加入所有队列消费者；只能供同一个任务/同一个版本使用。修改后任务不能隐式重新选择默认模型。
4. 路由写入必须留审计，不记录明文 key，且对失败或 HTTP 超时采用读取服务器状态再判断的协议。

## 5. P0 前端改造文件

| 文件 | 修改建议 |
|---|---|
| `frontend/src/components/LLMConfiguration.tsx` | 重构为模型库主界面；新增列表检索、卡片/表格、模态编辑、复制、状态、关联角色数；移除零散的硬编码角色快捷按钮 |
| `frontend/src/components/LLMSetupPanel.tsx` | 成为薄编排组件、调用统一管理器；新机推荐只补必要的未配置角色，不默认覆写用户已指定的模型 |
| **新建** `frontend/src/components/AgentModelRouting.tsx` | 独立角色矩阵、批量预览、确认、保存、状态空态/错态 |
| `frontend/src/components/SettingsPanel.tsx` | 仅挂载统一 AI 模型管理器；ComfyUI 专业工作流仍归高级设置 |
| `frontend/src/components/DirectorSetupPanel.tsx` | “② 模型与生成方案”内把 LLM 简要配置放前面、H3 方案独立显示；避免为了配 LLM 而铺开专业工作流 JSON |
| `frontend/src/components/SetupPanel.tsx` | 不改变已验证的下载/安装计划与 consent；只对接 LLM readiness 摘要与下一步引导，不耦合 H3 下载 |
| `frontend/src/api.ts`、`frontend/src/constants.ts` | 加入 overview/batch/preview 类型；中文文案从后端角色目录或集中映射取值，保留对旧版本 API 的容错提示 |
| `frontend/src/styles.css` | 简洁的模型卡片、分配矩阵、状态辅助色、移动/桌面响应式布局，避免页面再次工程化 |

**默认 UI 信息层级**：常规只展示模型昵称、平台、Model ID、状态和路由；高级选项隐藏 `base_url`、`max_completion_tokens`、`reasoning_effort`、`temperature`、`retry`、`timeout` 和探针原始证据。不要把 ComfyUI 工作流 Profile 与文本 ProviderProfile 混用“模型 Profile”一词。

## 6. 新机配置与安装关联

### 6.1 新机首次引导（不改安装器核心）

1. **安装 Shine 主程序**：沿用 `scripts/install_once.ps1`、`scripts/bootstrap.ps1`、`scripts/build_test_package.ps1`，继续默认非系统盘、独立数据目录、断点下载/安装计划；本项目方案不要求修改 H3 / Qwen 模型分发清单。
2. **配置 AI 文本模型**：设置页/导演基础配置公用统一 AI 模型管理。输入真实 API 或本机 Ollama 地址；保存后进行无素材连接探针、可选的实际生成探针；若 API 不可达，提示网络、代理、服务地址、模型 ID、权限等，不自动切换到 Mock。
3. **快速分配**：优先显示“文字创作路径”所需角色；新手可把同一模型显式分配至全部**尚未设置**的文本工种（包括四阶段两个角色）；高级用户可逐行指定不同 LLM。覆盖已存在绑定另开显式确认。
4. **分阶段就绪**：分别报告 `TEXT_PIPELINE_READY`、`H3_ENV_READY`、`VOICE_READY`、`REVIEW_READY`，任何一个结果都不得代替其他结果。仅做文字任务不以 GPU/视频模型为先决条件。
5. **重装/升级**：默认复用 `SHINE_DATA_DIR` 内 SQLite + secret 文件；安装包不包含用户 Key。旧 Profile/Route IDs 和任务审计原样保留；迁移/重置前预览并备份。

### 6.2 跨电脑迁移和安全

- 同一 Windows 用户与同一 DPAPI 环境下可继续使用已存储的密钥，但**跨电脑、跨 Windows 用户、重装系统**不得假定原密钥能解密。可迁移不含凭据的 Profile/Role 声明，提示每个需认证的 Provider 重新填 Key、重新探测，再将角色状态转为就绪。
- P0 不做 API Key 明文导出，导出 JSON 需含 schema 版本、Profile 公开字段/ID 映射、路由映射，排除 `api_key_ref`、`api_key`、探针中的敏感信息及本地路径；导入必须预览重名/ID 冲突，默认不覆写现有配置。
- 若要正式提供导入导出，作为 P1 独立开发，不能作为新机最短成功路径的前置条件。禁止安装/调试日志打印密钥、Authorization、项目剧本文本。

### 6.3 哪些已有部署能力绝不能改坏

- 本机端口继续绑定 loopback，远程服务使用用户明确配置；不改变安装计划/批准/凭证/哈希以及新机 H3/ComfyUI 模型身份。
- 真实生成和真实项目文本外发必须继续遵守当前项目的 `allow_remote_text` 等明确授权；无素材生成探针与真实剧本发送应给出明显不同的说明。
- H3 唯一视频核心不变；VLM、ASR、语音 TTS、Qwen Image 与文本 LLM 分属不同能力和租约/授权；禁止“把普通文字模型配置成视频/语音引擎”。

## 7. 分期开发建议

| 里程碑 | 范围 | 完成定义 |
|---|---|---|
| **P0-A：权威角色目录与新机缺口** | 14 个文本 role_id、场景就绪和兼容旧 readiness、四阶段默认映射修复 | 新机分配明确覆盖四阶段 2 个角色；所有后端真实文本消费者有角色目录测试 |
| **P0-B：模型库 UI** | 可编辑/复制 Profile、检查证据、密钥状态、清晰失败提示 | 同一接口新加模型无需重新手录敏感密钥，旧模型不受影响 |
| **P0-C：角色矩阵与事务批量** | 预览、单角色绑定、异构批量、只补缺项、改派安全门禁 | 失败不留半分配；队列任务不静默换模型 |
| **P0-D：新机双入口与验收** | DirectorSetup/Settings 统一、前后端回归、旧库迁移/不迁移检查、Windows 体验 | 新机从零配两种真实 LLM + 按 Agent 分配，全流程可用；H3 安装逻辑未回归 |
| **P1 可选：真实“账号→模型”双层管理** | 当一服务商下有大量模型或共享密钥轮换需求时，引入 `llm_connections` 与受控迁移；先保留旧 `provider_profiles` 作为兼容桥 | 共享账号只转一次密钥，所有派生模型引用该账号；旧数据和历史任务安全迁移；独立 PR 审查 |

**不建议**在 P0 同时实施新 Provider SDK、模型自动故障转移、多账号负载均衡、按角色自动选最便宜模型、自动上传项目素材、GPU 调度重做、H3 工作流重构。它们会扩大影响面，削弱此次改造精度。

## 8. 必须覆盖的验收场景

| ID | 用例 | 合格条件 |
|---|---|---|
| L01 | 空数据库新建首个 OpenAI-compatible Profile | 保存、测试、刷新可读；Key 不在 API 响应中 |
| L02 | 第二个不同域名/协议 Provider | 两者独立，URL/密钥不串用 |
| L03 | 同一服务复制成第二模型 | 不把密钥发回前端；独立 Secret ref；删 A 不影响 B |
| L04 | 编辑模型 URL、Model ID、token 限制 | 原证据变过期，需重新验收 |
| L05 | 连接成功但生成测试失败 | 显示连接级成功、生成级失败，不混淆 |
| L06 | 不提供 `/models` 的兼容平台 | 不虚构模型列表；明确生成探针能验证/不能验证的边界 |
| L07 | 缺密钥、401/403、429、超时、模型不存在 | 清晰分类，不误报 PASS，不自动重发不明状态 |
| L08 | 14 文本角色逐行赋予不同 Profile | 实际任务读取到对应 Profile，无跨角色污染 |
| L09 | 四阶段专用 2 角色走新机批量配置 | 后续全局蓝图/分场剧本不会因角色漏配置卡住 |
| L10 | 多角色批量失败/中断 | 完整回滚或按服务端状态对账，不存在半改派 |
| L11 | 只补未配置 vs 覆盖全部 | 默认不覆写已有选择；覆盖需确认并显示差异 |
| L12 | 任务排队/运行时改变角色绑定 | 不静默改执行模型；有冻结/阻断/具名重启处理 |
| L13 | 旧库原 Profile、Route、Secret | 向前兼容、旧数据全保留；如无 DB migration 则明确记录 |
| L14 | 同一 Windows 重启 + 更换 PC 模拟不可读 DPAPI | 本机有效配置保留；跨机不假装密钥可用，提示重新输入 |
| L15 | DirectorSetup 与高级 Settings 来回切换 | 数据一致；没有两套状态/两套权限/工程设置大铺屏 |
| L16 | Mock、VLM/ASR 与真实文本 LLM | Demo 不能混入真实门禁，声画审核单独展示 |
| L17 | 不使用 LLM 的 H3 功能 | 安装、模型加载与视频任务逻辑无回归 |
| L18 | 创作上下文/输出额度 | 60% 单轮限制、提前拒绝超窗、探测来源真实保留 |
| L19 | 审计与网络/密钥安全 | 审计只记录 IDs/变更项，API 日志无 key；本机访问仍 loopback |
| L20 | 真实提供商和实际浏览器验收 | 至少 2 家真实 API、各 1 个角色完整输出；区分离线回归与 REAL_LLM PASS |

测试建议：保留 `backend/tests/test_creative_output_budget.py`、`backend/tests/test_setup_llm_readiness.py` 和现有 API/Worker/Film/FourStage/E2E 回归，新增 `test_llm_role_catalog.py`、`test_llm_route_batch.py`、`test_llm_profile_duplicate.py`、`test_llm_new_machine_flow.py` 与前端 `llmManager.test.tsx`、`agentModelRouting.test.tsx`。完整执行：后端 `pytest`、`ruff`、Alembic 空库和旧库副本、前端 `npm test`/`npm run build`，Windows 实机跑启动与新机引导；记录每项 PASSED / MOCK_VERIFIED / NOT_RUN，未做真实 API 不得声称生成 PASS。

## 9. 开发约束与 Codex 云端实施提示词

本方案已规划存放于独立分支 `feat/llm-multi-model-agent-routing-20261109`，禁止直接修改 main。云端 Codex 可在该工作分支中建设、修改与测试，但**必须保持本地工作区变更，不得自行执行 git commit / git push，不得创建 PR 或合并**；由用户复核后另行决定如何提交。

> 在仓库 `anjingdtl/shine-video-studio` 最新 main 基线上实施 `Shine_LLM多模型配置与Agent路由改造方案_20261109.md`。先读取 AGENTS.md、现有 api_llm.py、llm/gateway.py、llm_readiness.py、schemas.py、models.py，以及 LLMConfiguration/LLMSetupPanel/DirectorSetupPanel/SettingsPanel 等代码与关联测试，核实最新提交是否改变本方案前提。优先 P0：复用 ProviderProfile/AgentRoute/LLMGateway/SecretStore 及当前 AgentTaskRecord 账本，建设唯一文本角色目录（务必包含 four_stage_bible_run 和 four_stage_scene_script_run）、简洁多模型管理 UI、逐 Agent 路由矩阵、事务性批量分配、模型安全复制与编辑、调用前就绪/在途任务影响保护，统一新机向导/导演基础配置/设置的 LLM 能力。不允许自动隐藏模型故障、跨模型 fallback、泄露 API Key、破坏 DPAPI 轮换、绕过授权、把文本 LLM 当作 VLM/ASR/H3 视频核心，或改动 H3/ComfyUI 下载器。保留现有接口兼容性，新增端点须配安全与失败测试。实施前写出代码级变更清单，实施后执行后端测试、Ruff、前端测试/构建、迁移（如有）及真实浏览器回归；真实 API/Windows 未执行的证据只能标 NOT_RUN。形成中文实施报告、风险/回滚说明和本地待提交变更清单；**禁止自行提交、推送、创建 PR 或合并**。禁止未经验证宣称 full PASS。

## 10. 远端核对入口（固定版本）

- [LLM API](https://github.com/anjingdtl/shine-video-studio/blob/888956edeac6e98f032f0e883e3e83d1a7ca92bb/backend/shine/api_llm.py)
- [LLM Gateway](https://github.com/anjingdtl/shine-video-studio/blob/888956edeac6e98f032f0e883e3e83d1a7ca92bb/backend/shine/llm/gateway.py)
- [Provider/AgentRoute 表](https://github.com/anjingdtl/shine-video-studio/blob/888956edeac6e98f032f0e883e3e83d1a7ca92bb/backend/shine/models.py)
- [角色 schema](https://github.com/anjingdtl/shine-video-studio/blob/888956edeac6e98f032f0e883e3e83d1a7ca92bb/backend/shine/schemas.py)
- [新机 LLM readiness](https://github.com/anjingdtl/shine-video-studio/blob/888956edeac6e98f032f0e883e3e83d1a7ca92bb/backend/shine/comfyops/llm_readiness.py)
- [四阶段真实调用](https://github.com/anjingdtl/shine-video-studio/blob/888956edeac6e98f032f0e883e3e83d1a7ca92bb/backend/shine/director/pipeline.py)
- [当前配置页](https://github.com/anjingdtl/shine-video-studio/blob/888956edeac6e98f032f0e883e3e83d1a7ca92bb/frontend/src/components/LLMConfiguration.tsx)
- [新机 LLM 页](https://github.com/anjingdtl/shine-video-studio/blob/888956edeac6e98f032f0e883e3e83d1a7ca92bb/frontend/src/components/LLMSetupPanel.tsx)
- [导演基础配置](https://github.com/anjingdtl/shine-video-studio/blob/888956edeac6e98f032f0e883e3e83d1a7ca92bb/frontend/src/components/DirectorSetupPanel.tsx)