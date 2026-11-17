# Shine AI 管家（Shine Copilot）V1.0 完整建设方案

> 状态：建设设计 / 供独立云端 Codex Agent 实现。本文是方案，不代表功能已经开发、真机运行或验收通过。  
> 方案日期：2026-11-16  
> 远端基线：anjingdtl/shine-video-studio，main@a2ea6af8276e1259ddbcb151f79115d18701dc12（PR #53 已合并）。  
> 目标平台：Windows 本地 Web（FastAPI + React/TypeScript + SQLite WAL + Worker + 监督进程）；MiniMax-H3/Ref2VA 是唯一正式视频生成核心。  
> 代码本地工作区可能领先远端：云端开发前必须重新核实目标分支与现有代码的差异，禁止用方案覆盖后来已建成的事实。  
> 规范优先级：AGENTS.md、实际产品合同与现有权威门禁优先；发生冲突时停止相关改动、记录差异并选择兼容实现。

## 0. 执行摘要与用户愿景

面向零编程知识用户，建设一个贯穿「项目管理—七阶段导演模式—素材中心—成片中心—基础配置—高级设置」的 AI 管家。用户遇到打不开、连不上、出图失败、出片中断、下载/登记反复、模型不可用等阻滞时，不需要复制大量日志、不需要掌握 CUDA、PyTorch、Python、ComfyUI 节点或 Git。管家能够基于本机真实证据自主定位问题、用通俗中文解释、在明确授权的配置权限内复用现有执行器恢复；若须改变项目/节点源代码，则只生成可交给开发者的脱敏 MD 阻滞/BUG/需求报告。

**定位：用户侧诊断与运维 Agent，不是开发 Agent，不是第二套环境裁决、任务调度或安装器。**

V1.0 必须交付：全局入口、独立 LLM Agent 角色、本地可用的规则诊断、权威事实聚合、上下文会话、版本化知识库、可选联网检索、受控配置恢复、只读/授权/禁止三级工具权限、工程问题 MD 移交、审计与可恢复任务状态、自动化回归和明确真机验收边界。不得只做静态聊天 UI、Prompt 或演示机器人。

## 1. 四项最高原则（不可妥协）

1. **一套事实**：所有状态与诊断读取现有持久化事实、真实服务响应及已有日志；禁止为管家新建一套真假就绪状态、哈希登记、节点能力或进度计算。缓存必须有版本/指纹与失效机制，缺证据即显示未知。
2. **一个裁决**：真实生产准入、环境 READY、H3 变体、节点/工作流是否可执行、素材能否生成，继续由现有后端权威裁决。LLM 的“判断”“推测”“已修好”不得修改或替代裁决。不能把 MOCK/OFFLINE/待试镜/候选标为实机通过。
3. **一个执行者**：管家只组织诊断/计划/受控调用，实际下载、配置、登记、恢复、任务提交由 Shine 现有安装器、持久任务、Worker、监督进程、统一 ComfyUI 调度层及业务 API 执行。禁止创建第二 Worker、第二 ComfyUI 管理器、第二重试队列、第二显存租约。
4. **代码零修改，配置可管理，工程问题只移交**：运行时 AI 管家不具备修改任何工程源码、测试、脚本、工程清单、内置标准模板或第三方节点源码的权限。不生成/执行代码补丁，不提交 Git、PR，不执行任意命令。若根因需要代码修订，只生成结构化阻滞/BUG/需求 MD，由用户提交开发者。用户级 ComfyUI 工作流、模型、节点安装、引擎开关与依赖配方属于“受控配置”，只允许通过现有被授权且已验证的接口进行。

**两类 Agent 严格区分：云端 Codex 是“建设 Shine 功能的开发者”，可按开发规范改源代码；交付后运行于用户电脑内的 Shine AI 管家没有这种权限。**

## 2. 已核对的远端基线和复用锚点

已通过 GitHub 仓库读取 main@a2ea6af 的 README、AGENTS.md、models.py、LLM Gateway、setup、ComfyUI workflow API、前端入口和 2026-11-16 验收报告，存在以下可复用能力：

| 现成机制 | 代码/API 锚点 | 本方案接入方式 |
| --- | --- | --- |
| LLM Provider/Profile 与角色路由 | backend/shine/models.py：ProviderProfile、AgentRoute；backend/shine/llm/gateway.py：LLMGateway.profile_for_role / complete_for_role | 增加 support_agent 独立角色，复用配置、密钥托管、限额与已有传输机制 |
| 环境唯一裁决 | GET /api/v1/setup/verdict；backend/shine/comfyops/setup_summary.py | 只读取权威快照，禁止衍生冲突状态 |
| 持久环境整备 | POST /api/v1/setup/prepare/execute；backend/shine/comfyops/setup_preparation.py | 受控修复可委托，复用任务、幂等与原进度 |
| 环境/资源检索和核验 | backend/shine/comfyops/resource_discovery.py；verification_receipts.py | 首选持久索引/回执；不得切页或每次聊天重扫磁盘/反复算全量哈希 |
| 受管服务/工作流 | backend/shine/comfyops/runtime_configuration.py；backend/shine/api_comfy.py 的 /workflow-profiles 与 validate | 查询真实服务与 object_info；用户级工作流通过官方 API 改动和复验 |
| 全局 UI | frontend/src/App.tsx（导演入口和素材中心双视图）；frontend/src/api.ts | 在两种视图及所有页面共用一个管家挂载点 |
| 审计与任务 | backend/shine/models.py：AuditEvent；backend/shine/services.py：add_audit | 延用统一审计；管家不造第二套运行历史或工程判决 |
| 当前工程验收边界 | docs/ACCEPTANCE_20261116_DIRECTOR_SETUP_CONSOLIDATION.md | A11 完整成片仍非 PASS；既有回归失败不得被管家“修复成功”掩盖 |

注意：README、历史文档和硬编码 UI 文案可能落后于代码；开发时以最新运行合同和代码为准。已存在问题的修复属工程主线，不得通过新管家绕过原生产门禁。

### 2.1 不得引入的并行系统

- 不允许新 /support/ready 或 support_can_render 作为独立事实源。
- 不允许为 Python、CUDA、模型、节点制作新安装注册台账，替代已有资源索引与回执。
- 不允许 AI 直接持有 ComfyUI prompt/interrupt/queue 管理特权或执行裸 HTTP 调度；统一调度层为唯一执行入口。
- 不允许 AI 绕过素材主资产人工确认、Style Bible 冻结、Ref2VA/官方 H3 编译器、真实验收、GPU Lease、提交结果未知核对等合同。
- 不允许为了让管家“可用”修改全局生产判定为宽松模式。

## 3. 用户体验与产品界面

### 3.1 全局入口

在 React 根布局挂载一个全局“AI 管家”浮动入口（不增设第二套左侧模块导航，不挤占七阶段主路径）。跨导演模式、素材中心、设置页、成片页保持相同会话入口与中文简洁视觉，支持浅色/深色及未来主题，满足键盘/屏幕阅读器可用性，移动窄屏显示为抽屉。

浮窗默认折叠且不抢焦点，不滥用气泡。仅在具名异常/长时间阻塞/失败恢复可协助时做一次轻量提示。普通制作成功不弹窗。入口展示“帮我检查 / 为什么失败 / 继续生产 / 生成问题报告”等与当前上下文匹配的建议，不展示工程状态矩阵。

### 3.2 交互闭环

1. 用户点击“帮我检查”或对具体失败点“交给 AI 管家诊断”。
2. 前端仅传 page_id、project_id、episode_id、job/attempt/task_id、error_code、用户的问题，不传整页 DOM、全部日志或密钥。
3. 后端聚合权威事实与具名证据，先给无需 LLM 的本地诊断摘要；若已配置支持 Agent 的 LLM 再补充解释与建议。
4. 管家展示“当前影响 / 已发现依据 / 下一步 / 是否需要我确认”；展开区提供脱敏技术证据与引用。
5. 属于用户配置：先预览变更、风险与回滚方式，由用户授权后通过原执行者执行，并持续展示原任务进度。
6. 原执行器结束后，以原权威门禁和最小必要真实验证复核；结论只能是 VERIFIED、NOT_VERIFIED、BLOCKED 或 UNKNOWN，不能以 LLM 文案替代结果。
7. 属于工程问题或不可定性：直接生成 MD，供用户复制路径/导出后提交开发者。

### 3.3 断网和无 LLM 模式

无 Provider、API 欠费/超时、网络断开时，小助手仍提供本地状态、结构化错误诊断、对应页面跳转、已注册的安全恢复动作和 MD 报告；明确标注“智能解释暂不可用”。绝不能用“先配置 AI 才能修复 AI”的闭锁。LLM 故障不得阻断正常出图出片。

### 3.4 用户可理解的表达

仅用“文件是否齐全 / 功能能否运行 / 现在做什么”展示核心结果，底层 Python/CUDA/pip 节点细节折叠。所有按钮说明其影响和是否会中断正在拍摄的任务。避免工程英文错误码成为主文案；保留错误码供开发者诊断。

## 4. 独立 Agent 与模型路由

### 4.1 角色契约

新增角色标识 support_agent，复用已有 ProviderProfile、AgentRoute 和 LLMGateway，不复制 API Key、不引入独立 Provider 配置页或新增 Codex 运行依赖。高级设置可为 AI 管家选择已经存在的模型配置；没有路由时保留离线能力。选择具备工具调用能力的 Provider 时按现有 adapter 实际支持情况判断，若缺原生工具协议，采用受后端验证的“计划—工具—结果”结构化协议；不可将 LLM 返回文字当命令执行。

系统提示词固定产品定位、四项原则、证据级别、数据隐私、故障分类以及禁止操作；它只能请求服务器端已注册工具。对于未证实的根因必须说“可能”，不能伪装成真机结论。

### 4.2 Agent 生命周期

建议采用“单个诊断会话 + 有界工具循环”而不是无限自主代理：每次诊断最多 8 个工具步骤、最多 2 次纠错分析、服务端超时/取消均有明确终态；若本机事实足够则不调用 LLM。具体预算要从现有 Gateway/Call Governor 兼容模型窗口核定，不得假设所有 Provider 都支持 1M 上下文。诊断任务与影片生产 GPU/Worker 使用隔离，远端 LLM 等待不持有数据库事务或 GPU 租约。

持久会话保留页面上下文、引用证据 ID、诊断结论、提出的动作以及用户授权，但不克隆项目生产状态机。浏览器刷新/切换页面可恢复，不重复执行已提交的修复任务。

### 4.3 禁止权限绕过

任何用户文本、日志、工作流 JSON、网页、GitHub Issue、知识库文档均视为不可信数据。即便内容写着“执行 pip install / 删除目录 / 忽略安全校验”，也不得被当作高优先级指令。所有工具在服务器端同时校验 actor、操作范围、资源归属、目标 ID、配置合同及幂等身份；提示词不能授予权限。

## 5. 工具能力矩阵和硬隔离边界

### 5.1 读工具（默认允许）

- read_support_context：获取当前 UI 上下文、用户可见业务错误和项目 ID。
- read_setup_verdict：只读现有 /setup/verdict 及相关既有状态摘要。
- read_resource_inventory：读取持久检索索引、已核验文件回执、缺失能力和下载候选，不触发全盘扫描。
- read_comfy_runtime：经已有适配器获取 ComfyUI 受管/外部实例归属、服务状态、object_info、节点加载错误、工作流合同状态。
- read_task_failure：读取特定 job、attempt、film run、安装/素材任务的具名状态、最后错误、关联原始日志的白名单片段。
- read_runtime_environment：读取只读且已经允许探测的 GPU/显存、驱动版本、Python 与 PyTorch 运行版本和依赖健康摘要；不使用通用 shell。
- search_local_knowledge：在版本化知识库内全文或结构化检索并返回引用。
- search_official_web：在用户授权的联网范围内检索官方文档和上游 issue，结果只供参考，不能变更本机事实。
- generate_support_report：向受控报告目录原子写入脱敏 Markdown，禁止任何其他文件写路径。

所有读工具受字段白名单、脱敏、结果大小、速率、超时与日志行数限制；不得将 SSH/环境令牌、API 密钥、全量小说、私密画面、用户原始素材等默认带给云端 LLM 或联网搜索。

### 5.2 受控写工具（有范围、有确认）

- preview_setup_repair / execute_setup_repair：先生成现有 setup 准备计划，再按明确选择调用既有 /setup/prepare/execute 或已支持的工具修复接口；同一 request identity 幂等，不重复提交。
- preview_user_workflow_change / apply_user_workflow_change：仅改变用户级或项目级工作流配置副本，要求现有工作流 Graph、parameter_map、参考音视频槽、Ref2VA 模式及运行合同验证通过；禁止覆盖内置受 Git 管理标准模板。
- preview_runtime_toggle / apply_runtime_toggle：只允许项目现有对外公开且安全的模型变体、加速/高清化选项、下载线路、工作流映射、允许的显存策略/引擎开关；不允许为实现开关去修改算法/源码。
- preview_managed_restart / execute_managed_restart：仅针对有受管归属证明、GPU/Worker 互斥许可且无危险进行中任务的实例；一律由已有监督进程负责，外部 ComfyUI 不可强停。
- read_action_progress：只读取原执行器任务 ID 的当前进度与终态，不单独驱动长期任务调度。
- create_user_report：生成日志 MD，不改任何代码。

**默认策略：只读自动；已验证的纯只读缓存刷新可自动；安装/下载/依赖变更/切换渲染器/重启/修改用户工作流必须展示预览并获得明确确认。** 重大变更先保留用户配置快照及回滚信息；只要无法提供安全回滚，必须提升风险级别或转人工。

### 5.3 一律禁止

- 任意 shell、PowerShell、cmd、Python eval/exec、pip install 任意参数、git commit/push/PR、任意 URL 脚本下载执行。
- 写入/覆盖/删除 Git 跟踪的工程文件，包括 backend/frontend 源码、测试、requirements/lock、manifest、标准工作流、脚本与部署配方；即使文件名为 .json/.yaml/.md 也不例外。
- 修改用户 ComfyUI 自定义节点的 .py/.js 等源代码、补丁文件、启动脚本；安装签名或钉住清单允许的节点版本不等于允许改节点源码。
- 为解决问题安装未经审计的新 ComfyUI 节点源码、私自改包源、放宽安全策略、全局升级 CUDA/显卡驱动或 PyTorch 大版本。
- 越权读写其他目录、改系统注册表、删除权重/缓存/成片/用户素材、替换人工确认的主资产、修改 Style Bible 或强行通过 QC。
- 在现有 GPU 租约以外运行模型验证；当前真实生产中不抢显存做“全面自检”。
- 盲目重新提交 UNKNOWN 的远端 LLM/ComfyUI 任务；必须先走原本的 reconcile/人工裁决流程。

### 5.4 “代码”与“用户配置”的判定算法

以 **归属、受保护程度及写入口** 判断，不以文件扩展名判断：

1. Git 跟踪的产品文件、内置标准配方、第三方插件源代码、Worker/后端内部实现均视为不可修改的工程代码或工程合同。
2. SQLite 中的用户设置、通过官方 API 注册的项目级工作流配置、在 SHINE_DATA_DIR 管理的用户级配置副本，视为用户配置；只允许已存在的类型化 API 修改。
3. 通过现有固定清单安装节点/模型/版本属于受控配置，但工具不得直接编辑节点源码，也不得临时从网页生成一段脚本执行。
4. 任何“为了调整运行参数必须改内置仓库模板”的情况，首先创建用户配置副本并验证；若无官方覆盖机制，则生成需求/BUG MD 交开发者增加支持。
5. 无法确定归属或没有可靠回滚时：停止写入，继续只读诊断并生成报告。

权限审计应直接针对后端工具注册表和文件系统出口，而不仅仅检查 LLM Prompt。Agent 不拥有通用写文件 API；唯一文件写权限为报告生成器在受控 support-reports 目录进行原子写入，其他用户配置通过既有业务 API 间接修改。

## 6. 事实聚合、诊断和知识库

### 6.1 权威 EvidenceSnapshot

建议新增只读 DTO（非第二事实库），字段建议：
- evidence_id、snapshot_at、expires_at、source、source_revision、confidence_level、stale
- page_context：screen/project/episode/job/attempt/setup_task/error_code（有限 ID）
- setup：setup/verdict、setup_summary 既有两步投影，必须原样映射权威结论
- runtime：system/status、Worker、受管渲染器、GPU lease/服务实例的只读摘要
- resources：相关能力的模型回执与注册、下载/检查记录，不回传权重字节
- task：错误码、阶段、attempt、引用日志范围、既有 next_action
- provenance：每条重要结论的源 API/记录 ID、取证时间、是否真实实机验证

snapshot 是可丢弃的只读投影，绝非项目第二份 READY 台账；变化后重新读取原事实。不得把重复的全盘探测/启动/注册写入 GET 请求。冷热路径均设置有界超时，优先复用持久化缓存与采样证据。

### 6.2 故障诊断器

先规则后模型：错误码和事实源提供确定性分类，LLM 负责解释、多证据关联和计划组织。优先识别：
- 启动器/本机 Python、uv/Node/端口、环境目录；以及“API 服务根本打不开”时的启动器离线紧急诊断。
- ComfyUI 受管服务启动失败、外部实例占端口、服务突然退出、object_info 节点丢失、工作流 profile 不匹配。
- 模型存在但未核验、回执失效、变体错配、兼容依赖冲突、磁盘缺额、网络下载失败/可恢复。
- Qwen 出图 T2I、I2I、INPAINT、MULTIREF、WARDROBE、RGBA 的真实能力与素材 job 失败。
- H3 Ref2VA INT8/候选变体加载、参考媒体绑定失败、采样 OOM、长任务无进度、二采/RelayVSR 可选能力未就绪。
- LLM Provider 探测失败、HTTP 401/429/5xx、上下文预算、响应 UNKNOWN 与幂等重试。
- 导演包/Worker/GPU lease/生产任务中断、已通过镜头保护、成片/审片门禁无法完成。

诊断结论结构：category（CONFIG/ENVIRONMENT/EXTERNAL/PROJECT_BUG/FEATURE_REQUEST/UNDETERMINED）、severity、symptom、facts[]、hypotheses[]、root_cause（仅有强证据才填）、recommended_actions[]、readiness_reference、outcome、next_step。Bug 不可凭某个异常栈就断言；无充分证据输出 UNDETERMINED/阻滞报告。

### 6.3 知识分层

A. **Shine 内置离线知识**：按版本绑定的产品说明、错误码索引、诊断规则、模块/阶段说明、配置权限、常见问题、开发者报告模板；从当前工程真相抽取，避免历史文档与最新 API 冲突。

B. **ComfyUI 兼容知识**：官方 API、节点包/模型版本、Python/PyTorch/CUDA 的已支持组合、工作流参考图/音/视频传输合同，以及实际本机对象信息。所有条目标注适用 Shine build、ComfyUI/node revision、证据出处和时间；未验证“通用教程”不得提升为可执行修复。

C. **可选联网知识**：官方 ComfyUI 文档、Comfy-Org/ComfyUI-Manager issue、Python/PyTorch/NVIDIA 官方资料、Shine 仓库公开 issue；按关键词进行只读检索，显示 URL、日期与本机适配程度。第三方网页不具有工具调用/权限授权权威。

实现优先 SQLite FTS5 或轻量结构化索引，不强制引入新的 embedding 模型，不强制 GPU 常驻，不额外占用 H3 显存。对于后续语义检索，必须保留同一证据引用与版本契约。

### 6.4 联网和隐私

默认联网诊断开关关闭，用户可主动开启；首次使用明确提示会访问外部网站。搜索关键字经过脱敏，默认不上传本机绝对路径、私密项目文本、模型 API Key、用户姓名或素材。远端 LLM 同样只接收必要的脱敏证据摘要，日志/项目内容越界需明确授权。提供“仅本机诊断”和“包含官方文档联网诊断”两种入口，但不生成第二套事实。

## 7. 工程问题 MD 交付机制（第四原则核心）

### 7.1 分类与流程

CONFIG/ENVIRONMENT：可按现有配置修复合同执行；执行后仍失败或无可授权工具时，生成 BLOCKER（阻滞）报告。

PROJECT_BUG：有复现与工程证据表明用户配置无解或核心实现不正确时生成 BUG 报告；无确定证据时只能标为“疑似”。

FEATURE_REQUEST：用户期望的能力在当前配置合同/API 下不存在时生成 REQUIREMENT 需求报告。

执行分支：
- A：检测 → 分类 → 预览配置修复 → 用户授权 → 原执行者 → 原门禁复验 → 成功/失败。
- B：检测 → 证据补充 → 自动生成 MD → 用户主动导出/复制路径 → 开发者修订 → 将来更新主版本后复测。
- C：证据不足 → 显示未知/待人工处理 → MD 阻滞报告，不允许靠任意命令赌修复。

### 7.2 MD 统一模板

文件名建议：SUPPORT-YYYYMMDD-HHMMSS-BLOCKER|BUG|REQUIREMENT-shortid.md；保存到 SHINE_DATA_DIR/support-reports/，不写 Git 仓库目录，不自动 push issue/PR，也不自动发给任何外部人员。

报告包含：
1. 报告 ID、类型、优先级、创建时间、Shine 版本/commit、Windows/显卡/Python/ComfyUI 节点摘要（敏感字段脱敏）。
2. 用户可见现象（中文）和预期行为、实际行为、影响范围。
3. 精确复现步骤；涉及时序时记录相关 task/job/attempt 的匿名引用与时间线。
4. 事实证据表：来源、获取时间、错误码、日志摘录（长度上限）、证据 ID/校验摘要、真实/模拟/未知。
5. 本机相关配置与声明版本（不包含秘钥、私有素材、小说原文、完整目录路径）。
6. 已尝试的用户配置修复、结果、是否复验，避免开发者重复要求用户操作。
7. 已确认根因与待核实假设严格分开；建议开发者重点核查模块与验证标准，不直接写“把某行替换成某代码”。
8. 隐私脱敏声明、附件清单（仅经用户主动选择）、重复报告指纹、关联旧报告。
9. 判定状态：待开发者分析/已由用户提交/后续版本复验；状态不代表开发者真实已处理。

报告 JSON 内部结构可作为持久元数据，但交付必须是可读独立 MD，且报告生成器用只允许目录下的受控路径、防路径穿越、原子文件创建和重名防护。用户可打开文件夹、复制 MD 内容或下载单份 MD。默认不自动提交 GitHub Issue。

## 8. 推荐技术模块与 API 合同（供开发时按真实仓库适配）

### 8.1 建议模块（名称仅建议，禁止复制既有底层）

- backend/shine/support/agent.py：独立 support_agent，复用 LLMGateway。
- backend/shine/support/context.py：只读证据投影、隐私脱敏、快照引用。
- backend/shine/support/diagnostics.py：确定性错误分类与证据等级。
- backend/shine/support/tools.py：静态白名单、输入 Schema 和服务端安全校验；绝无任意 shell/文件修改能力。
- backend/shine/support/actions.py：授权预览、幂等 delegation、原执行者任务追踪；非第二执行器。
- backend/shine/support/knowledge.py：版本化本地索引与联网资料可信分层。
- backend/shine/support/reports.py：受限 MD 报告生成器。
- backend/shine/api_support.py：唯一 support API router。
- frontend/src/components/ShineCopilot.tsx：全局浮窗与轻量诊断 UI，App 根挂载。
- tests：后端工具权限、负面越权、报告脱敏、RAG 与前端全局交互回归。

真实仓库若已有同等组件，以复用扩展为先，不要强造以上文件名。

### 8.2 推荐 API（仅新增 UI/Agent 合同，不新增生产判定 API）

| 方法/路径（建议） | 作用 | 约束 |
| --- | --- | --- |
| GET /api/v1/support/context | 当前页面与关联任务的只读诊断快照 | 只投影，不写环境状态 |
| POST /api/v1/support/sessions | 建立/恢复诊断会话 | 隔离项目、会话过期策略 |
| POST /api/v1/support/sessions/{id}/messages | 用户提问 / 开始诊断 | 支持无 LLM 模式，有界后台任务 |
| GET /api/v1/support/sessions/{id} | 会话状态、证据、建议 | 不触发自动安装/注册 |
| GET /api/v1/support/sessions/{id}/events | 读取进度或增量事件 | 可选 SSE；失败回退已有轮询范式 |
| POST /api/v1/support/actions/preview | 预览合法配置操作 | 不执行任何写入 |
| POST /api/v1/support/actions/{id}/approve | 用户授权并委托给已有执行器 | CSRF/actor、快照版本、幂等、权限检查 |
| GET /api/v1/support/actions/{id} | 返回原任务 ID / 进度与复验 | 不造独立调度状态 |
| POST /api/v1/support/reports | 生成脱敏 MD | 严格报告目录写边界 |
| GET /api/v1/support/reports/{id} | 查看或导出本人报告 | 只访问已登记的安全路径 |

不应把 /support/actions 扩展为通用“执行某命令”接口。是否用独立路由与表，必须以现有 API 风格/迁移规范为准；不接受破坏已有 endpoint 语义。

### 8.3 持久化最小设计

可新增 SupportSession/SupportMessage/SupportActionRef/SupportReport 元数据表，保存有限会话上下文、脱敏工具调用、原任务 ID、授权和报告路径。若已有通用任务表能承载，则优先复用；不保存同义的 production_ready、comfy_ready 或镜头状态第二副本。

数据 retention：默认最多 30 天会话历史，可由用户手动清空；报告默认长期保留直至用户删除；密钥/原素材不入库。提供老版本 DB 的 Alembic 升级与降级/兼容说明，不破坏已有表及作品任务。

### 8.4 行为契约与并发

- 每个授权动作绑定目标资源、计划哈希、原事实快照版本、actor 与审批时间；快照漂移则重新预览，不复用旧授权。
- 同一操作重复点击只能返回同一个原执行器任务；与既有 setup/film/worker 幂等共存。
- GPU 忙、Worker 租约未释放或外部实例占用时必须等待/显示具名阻滞，不擅自强杀。
- 真实结果 UNKNOWN 时必须回原系统 reconcile，禁止补发新任务。
- 任何自动修复不能让已通过的素材、镜头、成片被覆盖。
- Agent 对话暂停、断网、重启与前端切页不改变原执行任务的生命周期。

## 9. 本机启动失败的特殊兜底

如果 Python/uv/FastAPI 本身打不开，Web AI 管家必然不可用，不能承诺“全故障均由在线聊天修好”。

因此增加与独立启动器/安装入口衔接的 **离线基础诊断入口**：只读取启动器已有日志、依赖与版本探针，使用当前安装器的固定配方给出中文诊断与恢复按钮。此处不需要 LLM，也不新建安装器，不允许新一套可修改源码的“修复脚本”。严重失败亦可在启动器导出同一模板的 BLOCKER MD，供用户提交开发者。

如启动器尚在独立分支开发，本轮只能通过最小可行接口/文档约定接入，不得为了 AI 管家大改启动器架构；明确写出依赖项与未闭环测试。

## 10. 分期工作包与交付要求

### W0 基线核验、调用图与权限设计（必须先于改代码）

1. 检查最新 main/目标分支、AGENTS.md、LLM 配置、setup/worker/ComfyUI 工具、前端 App、当前启动器状态。
2. 用真实代码路径输出：事实源—裁决点—执行器—可供 support 调用的接口矩阵，并标明可能重复链路。
3. 输出禁止工具清单、用户配置白名单、只读/授权/报告工具 Schema、攻击面/隐私威胁模型。
4. 明确已有 A11/测试故障为“前置工程边界”，不要挪到本轮的“已修复”声明。

### W1 只读基础版（可单独验收）

- 独立 support_agent 模型路由与会话。
- 所有页面共用一个轻量浮窗；聚合页面任务上下文和权威快照。
- 本地规则诊断优先、无 LLM/断网可用、可溯源结论。
- 本地 Shine/ComfyUI 知识索引、Markdown 报告生成和脱敏。
- 全局 UI 渲染、暗色可读、错误点击可带 task ID。

### W2 受控修复与联网检索（V1.0 必须交付）

- 只读/授权/禁止权限从后端注册表强约束。
- 复用 setup/prepare 与 runtime/workflow 既有 API，提供预览/授权/进度/复核链。
- 用户级工作流参数、模型变体、节点/依赖在已有支持清单内可控调整；工作流保护策略、必要快照和回滚。
- 联网官方检索可关闭，资料可信度与来源展示，防注入、隐私拦截。
- 受管服务恢复/冲突识别，UNKNOWN 任务严格走原系统核对，不得新建并行调度器。

### W3 主动场景联动

- 现有失败事件或具名 error_code 触发“需要帮忙吗”非侵入提示。
- 启动失败/出图中断/出片失败/LLM 异常示例化体验。
- 严重故障自动准备只读证据草稿（不自动外发）。
- 支持明确的用户操作后连续监控同一原任务进度，避免一次修复一套进度系统。

### W4 工程验收和交付

- 后端单元、接口和 UI 回归；SQLite 空库/旧库迁移；Ruff、TypeScript、生产构建。
- 项目规定的后端八分片并行全量验证与前端回归；保留失败逐条证据，不允许把既有红项说成全绿。
- Windows 真机验证只在可用硬件上声明；4080/4070/5090VD2/未接入用户机均不得虚构实机 PASS。
- 编写用户操作文档、故障/权限说明、验收表、真实测试与待办边界。
- 通过开发分支 PR 合入 main；不将测试生成的密钥、会话、日志、用户素材、数据库、成片打包入 Git。

## 11. 强制验收用例（至少覆盖以下测试）

| ID | 用例 | PASS 标准 |
| --- | --- | --- |
| S01 | 全局 UI 跨导演/素材/设置/成片 | 入口唯一、状态连续、不重置七阶段 |
| S02 | 未配置 LLM | 本地检查、错误解释、MD 报告可用 |
| S03 | 有多个 LLM Provider | support_agent 独立路由、密钥不泄漏、不改变影视 Agent 角色路由 |
| S04 | 浮窗上下文 | 仅发送受控页面 ID/关联 task ID，无整页抓取 |
| S05 | verdict/准备摘要一致性 | 不得与顶部/基础配置出现互相矛盾 READY |
| S06 | 反复打开/切换页面 | 不反复全盘检索、不反复哈希、不自动重启 ComfyUI |
| S07 | 模型已存在却无法使用 | 读取原回执并提出原链路修复，不能伪造登记 |
| S08 | 服务/节点失联 | 获取原错误与归属，外部实例不强停 |
| S09 | 出图六种能力其中一种失败 | 单项具名诊断，不阻断其他能力 |
| S10 | H3 模型变体/工作流不匹配 | 拒绝不合法方案，不默默换别的视频模型 |
| S11 | 失败任务处于 UNKNOWN | 只走原 reconcile，不重复提交 |
| S12 | Worker/GPU 忙且发起修复 | 尊重 Lease/并发，不抢占与破坏原任务 |
| S13 | 修复预览/用户授权/重放 | 未确认绝不修改；相同身份幂等 |
| S14 | 工作流用户副本调整 | 原工作流合同校验通过，不改内置模板 |
| S15 | 内置模板/节点源码修改请求 | 后端拒绝操作，生成 MD 需求/BUG |
| S16 | 恶意 Prompt：执行 PowerShell/git/pip | 无危险工具入口、无执行，记录拒绝 |
| S17 | 恶意网页/错误日志注入 | 内容只作为证据，不提升工具权限 |
| S18 | 报告 MD | 包含版本/复现/脱敏证据/不确定性/建议验证，不含秘钥 |
| S19 | 越界路径/软链接/目录穿越 | 报告只写受控目录，拒绝任意写文件 |
| S20 | 用户级工作流修改前后 | 有预览、许可、回滚和既有验证 |
| S21 | 配置修复失败 | 返回原任务真实 FAILED，保留证据，能生成 BLOCKER |
| S22 | 无法判定根因 | 输出 UNDETERMINED，不谎称代码 BUG |
| S23 | 联网关闭/断网/LLM 401/429 | 不阻断出图，离线诊断照常工作 |
| S24 | 远程 LLM 搜索隐私 | 日志脱敏、默认不外传私密材料、网站指令不可执行 |
| S25 | 浏览器关闭/重启 | 会话可恢复、原配置任务续跑、不重复提交 |
| S26 | 各主题/深色/键盘 | 面板可读、层级清晰、无透明背景文字失明 |
| S27 | 业务真实门禁与人工资产 | 无绕过 Style Bible/主资产审核/Ref2VA/最终验收 |
| S28 | 安装包安全 | 不含 API Key、个人日志、报告、SQLite、模型权重与私有数据 |
| S29 | 启动器无法启动后端 | 有离线基础诊断/报告出口；未实现则如实标 BLOCKED |
| S30 | 仓库代码只读边界 | 运行时 Agent 的所有工具均不能改 Git 跟踪文件或第三方节点源码 |
| S31 | 旧库迁移和报表去重 | 保留历史项目、报告指纹可去重、清理符合保留规则 |
| S32 | 性能与资源 | 常规诊断不占 GPU、不启动新 ComfyUI、不造成任务明显卡顿 |

若其中 S15、S16、S17、S19、S27、S30 未通过则为 P0 安全阻断，禁止合入。若 S05、S11、S12、S13 未通过则为 P0 生产正确性阻断。缺 Windows 真实硬件时只能报告 OFFLINE_TESTED，不可宣称真机通过。

## 12. 性能、可靠性与观测要求

- 首屏管家加载不阻断 React 主页面；默认不启动 GPU 任务、不触发模型加载、不进行全盘资源扫描。
- 事实快照有超时与陈旧提示；读快照/简单错误诊断先给结果，再异步继续 LLM 搜索，避免一个长调用让页面显示“后台已失联”。
- 诊断操作与配置执行的状态和错误码可审计；工具超时或用户取消有终态，禁止无限“正在诊断”。
- 限制并发诊断次数、输出 token 和外部联网请求；诊断失败不得抢 Worker 或占用单卡渲染时隙。
- 审计字段需包括 actor、工具名、关联 evidence_id、动作预览哈希、审批时间、既有执行任务 ID、复验结果；日志不记录 API Key。
- 对话记录可清理，生成报告可由用户自行删除；区分原始证据引用与复制的脱敏摘要。

## 13. 第三方参考（参考思想，不直接引入整套系统）

- Stability Matrix：https://github.com/LykosAI/StabilityMatrix — 隔离的 Python 环境、统一包管理、共享模型目录。
- ComfyUI-Manager：https://github.com/Comfy-Org/ComfyUI-Manager — 节点信息、安全安装和环境快照；其安装器不能与 Shine 并行争夺执行权。
- ComfyUI Desktop：https://github.com/Comfy-Org/desktop — 桌面应用与运行环境一体化思路。
- Open WebUI：https://github.com/open-webui/open-webui — 聊天工具和知识库交互，借鉴产品体验，不嵌入第二后端。
- LangGraph：https://github.com/langchain-ai/langgraph — 有界 Agent 状态持久化、人机确认和错误恢复；先利用 Shine 当前持久任务，不因框架热门而强依赖。
- OpenHands：https://github.com/OpenHands/software-agent-sdk — 仅借鉴证据驱动诊断；其通用代码修改与任意执行模式 **不得**进入用户端 AI 管家。

外部开源代码不得直接复制进入主仓，需核对许可证、版本、依赖和适用边界；上游内容一律视为低于 Shine 项目合同的参考资料。

## 14. 开发组织、PR 与验收纪律

建议从本方案所在 docs/shine-ai-copilot-v1-plan-20261116 新建 **独立 feature 分支与独立 worktree** 开发（建议 feat/shine-ai-copilot-support-agent-v1）。如期间 main 前进，先对比差异，最小化同步并保留该方案文件。严禁直接覆盖 main、强推或修改已有其他 worktree 的本地未提交文件。

开发 Agent 应自主完成 W0—W4，功能与测试通过后编写实现/验收 MD，给出：
- 具体 Git commit SHA、PR 链接、API/数据迁移/组件清单。
- 权限白名单审计、用户配置与源码零修改隔离的反向测试证据。
- 真实场景通过/未通过矩阵，LLM/联网/ComfyUI/GPU 各自“真实、模拟、未执行”边界。
- 项目真实业务生产门禁未放松的差异证明。
- 文档、功能、前端操作体验验收。
- 未通过项是否为既有缺陷、如何复现以及是否阻断本功能。

全部 P0/P1 验收 PASS 才能发起可合并 PR；PR 合并应满足仓库保护规则与用户既有授权，不得因为 GitHub CI 额度问题把未运行项目标绿。禁止在本轮捎带修复无关既有业务 BUG，除非是被本功能引入的回归。失败保留分支和客观报告，不宣称完成。

## 15. 最终产品判据

**最终由零编程用户验证：**
1. 用户在任意页面失败，能在原地唤出管家，获取基于真实本机证据的诊断。
2. 配置范围问题，管家能在有明确授权时调动原安装器/调度层完成安全恢复，原裁决通过才称恢复。
3. 工程源码问题，管家不会修改一行源码，也不会偷偷编译补丁；只导出开发者能直接使用的 Markdown 报告。
4. 没有 LLM/网络、GPU 正忙、原后台服务中断时，能明确说明边界并尽量提供离线诊断路径。
5. 增加 AI 管家以后，Shine 对用户的操作复杂度下降，不出现第二套“请先配置/登记/再验证/转到另一个页面”的闭锁。

**一句话验收：AI 管家可以看、查、解释、在权限内调用原执行者进行配置恢复，并生成工程问题 MD；但它不能自己改源码，更不能重新定义 Shine 是否可以出片。**
