# Shine AI 管家 V2.0：项目百科、全界面代操作、智能诊断与参数调校建设方案

> 文档日期：2026-11-16
> 项目：Shine Video Studio（Windows 本地 Web 工作台）
> 建设源版本：PR #55（feat/shine-ai-copilot-support-agent-v1）@ 82df4b534c12609cced97c6cbaf7a1391d3632bf
> Main 基线：a2ea6af8276e1259ddbcb151f79115d18701dc12
> 文档分支：docs/shine-copilot-v2-knowledge-automation-diagnostics-20261116（仅设计文档，不含功能代码）
> 前置：PR #55 V1.0 工程收束已经完成代码阶段，但 Windows/ComfyUI/H3 真机及启动器离线诊断 S29 仍未通过；不把这些缺口当成 V2 已实现。

## 0. 目标与最终用户体验

让普通用户可直接对 Shine AI 管家说：
- 「为什么这个分镜无法生成？请查出原因。」→ 精准绑定当前项目、集、镜头、真实任务与失败码，追加只读证据，说明结论和下一步。
- 「Shine 的导演包、导演风格、五维故事记忆和 H3 Ref2VA 有什么关系？」→ 联合项目当前版本真实代码/文档、官方 H3 文档和官方 ComfyUI 文档作可溯源回答。
- 「帮我把本项目第 3 集缺失的素材逐项准备好，配置合适的已登记 H3 Ref2VA 模型，再开始生产。」→ 生成可审阅计划，验证现有门禁与资源占用，取得相应授权，调用原有业务 API/Worker，回报原任务进度及真实结果。
- 「把当前可调整的镜头步数和高清处理参数按推荐方案调好。」→ 只对支持的用户级配置或工作流副本执行 schema 验证、变更预览、授权、原 API 提交、回读复核、可能时回滚。
- 「这些方法都不能修好。」→ 生成去隐私的 BLOCKER/BUG/REQUIREMENT Markdown 报告，供开发者处理，不运行源码修改工具。

不是新增一个「会说话的工程控制台」，也不是让 LLM 直接访问原始 API 或浏览器 DOM；目标是把已有操作收束到安全、可懂的自然语言入口。

## 1. 三项架构原则 + 不可突破的第四项安全底线

1. **一套事实**：项目状态、配置、资源、模型、运行环境、GPU 租约、任务与成片来自现有原始数据库/权威接口。百科项目事实随当前构建的 Git SHA 与产品版本绑定，不得以过期说明覆盖真实代码。
2. **一个裁决**：环境就绪只认既有 setup/verdict；任务可提交、冻结风格、人工资产确认、工作流验证、GPU 准入、质检与交付仍由各业务域原有门禁裁决。AI 不得自行声明 READY、PASS、已出片或成功。
3. **一个执行者**：每种动作复用现有正式 API、业务服务、ComfyUI 统一调度器、资源仲裁器、安装器或持久 Worker；AI 只做意图理解、计划、受控委托、跟踪，不另建第二套执行器与状态机。
4. **代码零修改、工程问题只移交**：运行时 Agent 永久禁止项目源码/测试/受 Git 保护模板/ComfyUI 第三方节点源码写入，禁止任意 shell/Python exec、git/PR/patch、用户任意文件路径、绕过门禁。用户级配置及独立工作流副本可以走官方受控 API。开发本功能的 Codex 是工程开发者，不受运行时 Agent 的「源码零修改」约束。

## 2. V1 当前事实与复用点

- 现有 support：backend/shine/support/{actions,agent,context,diagnostics,investigation,knowledge,models,occupancy,privacy,reports,tools}.py。
- 统一 LLM Gateway + support_agent 角色和 ProviderProfile；无需重建 Provider/Key 管理。
- /api/v1/support 的会话、上下文、预览/授权/回读、受控报告端点。
- 唯一环境裁决 /api/v1/setup/verdict；setup/runtime、summary、setup_preparation、setup_reconcile、renderer_release。
- 原任务/原执行器：Job、JobAttempt、ProductionSeries、ProductionRun、LibraryRenderTask、MaterialRun，ComfyUI/资源租约调度、现有资产中心与七阶段导演工作台。
- 原 UI：frontend/src/App.tsx、ShineCopilot.tsx、copilotContext.ts，既有全局错误入口。
- V1 第三轮修复已加入 target_fingerprint、授权失败 REFUSED、释放专用占用判定、对账结果归属，保留并回归验证，不重写。
- 当前 V1 严格白名单只有 setup_repair / setup_reconcile / managed_release / workflow_user_copy；V2 扩招必须通过可枚举的正式用户操作能力目录，而不是给 LLM 一个通用 HTTP 客户端。

## 3. V2 架构结构（不新增平行执行系统）

用户自然语言 / 指定页面故障
 → 同一全局 ShineCopilot 交互
 → IntentRouter（百科回答 / 只读调查 / 参数调校 / 用户操作）
 → EvidenceReader（既有权威数据库和接口 + 页面项目上下文）
 → ProjectKnowledgeIndex（版本匹配的本地文档/代码合同）
 → OfficialDocsIndex（官方来源，版本/许可/来源显式标注）
 → BoundedPlanner（短任务有界步骤 + 可审阅意图计划）
 → CanonicalCapabilityRegistry（业务能力描述、参数 schema、所需授权、原执行器、预期结果）
 → ExistingBusinessAPI / ExistingService / ExistingWorker / ExistingScheduler
 → ReadBack & Audit（原任务 ID / 原能力 verdict / 原资源状态）
 → 回答、进度或经脱敏的 MD 工程移交报告

任何写 API 都不由 LLM 直接发起；LLM 只能选择已注册 action_id 并提出受 schema 限制的参数。后端授权管理器检验 actor、project、scope、resource revision、GPU/UNKNOWN/Style Bible 门禁、审批、幂等、结果归属。

## 4. 能力 A：项目百科与版本化知识

### A1. 本机「Shine 项目百科」
- 从已检入 Git 的 README、AGENTS、docs、正式业务 API 路由合同、正式 workflow/variant 配置和运营说明创建 build-time 文档索引。
- 每条项目知识必须包含 source_path、source_commit、build_version、content_hash、topic、scope、last_verified；与运行中的应用构建 SHA 一致。
- 将「目标功能设计」「已实现」「测试已通过」「真实机器验证」「未实现或阻塞」显式区分；设计方案和 PR 不得当成已上线功能。
- 模块覆盖：导演模式七阶段、文学四阶段与五维记忆、导演风格、素材中心、模型/工作流注册、统一 ComfyUI 调度、下载/校验/登记、生产与质检/返工、LLM 多模型路由、启动器及安装维护。
- 代码语义元数据可在构建阶段使用静态分析/人工整理的合同映射生成，不允许运行时 AI 任意读取工作树、泄漏秘密文件或执行项目代码。
- 每次版本发布自动重建/检查索引；若运行构建 SHA 与知识 SHA 不符，降级回答并显式标注「文档可能过期」。无需在线搜索才能回答产品基础使用问题。
- 检索优先 SQLite FTS5/BM25/小型索引，避免额外 embedding/常驻 GPU；同一 SQLite 工具和存储，不引入第二个重型知识平台。
- 知识结论按 ProductFact > VersionedProjectDocs > OfficialReference > CommunityReference 等证据等级；引用可点击至原文位置。

### A2. ComfyUI 官方知识
- 官方首页：https://docs.comfy.org/
- 官方仓库：https://github.com/comfy-org/docs
- 仅选与 Shine 有关的官方章节：本地启动、workflow JSON、节点与模型路径、server/API、节点安装、运行兼容性、常见错误。
- 为离线使用封装有来源与许可声明的短摘要及经过许可审查的文档片段；授权不明确或会触发分发义务时不整库复制，仅使用自编短摘要与原文链接。记录版本、来源 URL、抓取时间和许可。
- 官方解释与本机的 ComfyUI 安装目录、节点版本、运行时合同核验结果联动；无法观测版本时不可宣称已兼容。

### A3. MiniMax H3 官方知识
- 官方仓库：https://github.com/MiniMax-AI/MiniMax-H3
- 官方中文说明：https://github.com/MiniMax-AI/MiniMax-H3/blob/main/README.zh-CN.md
- 官方提示词 Skill：https://github.com/MiniMax-AI/MiniMax-H3/tree/main/skills/h3-prompt-writing
- 官方模型权重和许可：https://huggingface.co/MiniMaxAI/MiniMax-H3
- 文档分为 H3-Context-IR、H3-Base（Ref2VA/FL2VA 说明）、H3-Regenerate-2K、prompt schema、输入参考约束和 ComfyUI 官方集成；实际功能可用性必须受 Shine 本机已装变体/工作流及硬件约束。
- **关键区分：官方 H3 全部能力 ≠ Shine 当前已支持的能力**。例如官方 FL2VA / 2K 模块不是自动在当前本机 Ref2VA INT8 构建可用；不可静默更换视频核心、下载其他变体或新增付费 API。
- 尊重 MiniMax H3 Community License Agreement 和各资料的再分发条款；没有明确权利时不把完整官方手册/模型权重重新打包进安装包，使用自编许可安全摘要+用户授权的本地缓存+官方跳转。
- 外部资料检索只在用户授权联网后开启；严格官方域名白名单，去敏查询，外部文本只作参考不能变成行动指令。

### A4. 百科应答合同
- Answer = 简短结论 + 项目当前版本状态 + 证据来源（文件/commit/官方链接）+ 对用户适用性 + 必要时的可操作下一步。
- 禁止只凭 LLM 内部记忆回答项目当前已实现状态；知识检索失败应明确 UNKNOWN。
- 允许用户询问术语、功能关系、错误解释、参数意义、工作流建议、H3 提示词规则，尽量不要求用户手动阅读仓库。

## 5. 能力 B：全局用户代操作与 API 控制

### B1. 「所有界面操作」的可兑现定义
- 覆盖每个用户可以在 Shine Web 上合法完成的业务动作，并尽可能增加界面引导（导航、打开对应配置或任务）。
- 不等于授权 LLM 任意请求任意 HTTP 方法、任意内部 API、任意数据库表和任意 DOM。
- 使用一个 CanonicalCapabilityRegistry，描述 action_id、中文名、对应页面、用户意图示例、原前后端端点/服务、参数 schema、前置事实、业务门禁、权限等级、可逆性、作用域、资源成本、预计原任务状态及回读 API。
- 必须先完成「全界面 / 全业务 API 操作盘点矩阵」，标注 COVERED / READ_ONLY / GUARDED / NOT_SUPPORTED；每一个非 COVERED 的项要有原因，不得假称全覆盖。
- React 只新增轻量语义导航/定位桥（例如打开项目、选定集、显示预览），不用 CSS 选择器/模拟鼠标/屏幕坐标；真实写入走现有后端 API。
- 前后端应共享 action_id/参数/返回状态的版本合同，防止前端硬编码与 API 漂移；允许从 OpenAPI、schema 和显式 capability 声明生成部分静态目录，人工复核高风险条目。

### B2. 典型用户级可委托能力（按现有实现核验逐项注册）
- 项目：创建/选择、元数据编辑、导入文本、按现有流程创建项目；不擅自删除用户作品。
- 创作：提纲/剧本/分镜生成、查看与修订、阶段确认、用户确认后冻结风格和主资产；不可代替用户作真实艺术确认。
- 摄制：列举各集分镜、检查缺失素材、触发既有素材准备、选择已验证的模型变体和适配 workflow、设置用户可编辑参数。
- 生产：预估耗时/资源、查看 GPU/Worker、提交已授权任务、暂停/恢复/取消（仅原有安全 API 可支持且能证明效果时）、进度跟踪、按原合同重试失败任务/质检，UNKNOWN 不自动重复提交。
- 素材中心：搜索/复用项目素材、按已授权的能力执行文生图/图生图/局部修改等，严格遵守版权、来源和确认规范。
- 成片：查询进度、既有审核/交付/下载路径，不自动公开分享或批量删除。
- 设置：LLM 可选角色路由、下载偏好、可安全管理的用户级参数、受控模型核验与已注册工作流副本；不得修改内置模板与节点源码。
- 每一动作必须在现有 API/服务中找出「唯一执行者」。找不到就 NOT_SUPPORTED + 生成需求报告，禁止伪造可用功能。

### B3. 授权分级（服务端强制）
- READ：纯查询、百科、导航、读取状态、生成非侵入式计划，允许自动执行。
- PREVIEW：用户级可逆调整、工作流副本、较小范围的参数编辑；先显示实际变更 diff 和校验结果，再明确确认；同一委托下的多步轻量操作可获得「明确的范围化授权」，要求 actor/project/action scope/time_limit，可撤销。
- EXECUTE_GUARDED：真实 GPU 生成、批量修改、大量下载、付费 LLM/API、素材重新生成、任务取消、重要数据覆盖、提交生产；必须展示资源/费用/时长/影响范围及不可逆性并获得项目级明确授权。超出已授权范围必须暂停请求确认，不得自行扩大授权。
- RESTRICTED：原始删除、发布外部、批量清除、秘密更改、跨项目危险写入等，只有原业务合同许可且用户逐次确认才考虑；没有合同则禁止。
- FORBIDDEN：源码/节点源码修改、shell、git、patch、绕开 Style Bible/主资产确认/生产门禁、未经确认重试 UNKNOWN、调用任意内部 API/HTTP URL。
- 所有委托必须有显式取消/停止条件、配额、项目范围和最长有效期；不能默认永久自主运行。

### B4. 一条控制执行链
用户意图 → 只读收集项目/任务事实 → 生成可读计划与预计成本 → 逐个映射已注册能力 → 原门禁预检 → 预览内容与差异 → 按等级确认授权 → 带幂等键/项目身份/资源修订调用原执行器 → 回读原任务 ID 与原状态 → 展示真实进度 → 终态真实核验 → 失败后可重预览/报告。
- 利用 V1 SupportAction 及其原子授权/target_fingerprint 思路；如果需要长链路编排状态，只存「计划描述、授权记录、原任务 ID 引用和执行轨迹」，复用已有 Worker/ProductionRun，绝不新增与生产并行的第二执行状态机。
- 严防 React 重渲染、页面刷新、网络超时产生二次提交，所有变更请求服务器端至少一次接收但原执行器幂等和精确一次语义应明确；外部结果 UNKNOWN 时禁止自动重发。
- 助手不得仅凭前端网络响应成功就声称完成，以原任务完成和业务效果证明为准。

## 6. 能力 C：诊断、参数建议与受控调校

### C1. 诊断闭环
- 自动引用当前页面、项目/集/镜头、job/attempt/task、错误码、失败时间及原服务日志证据，按需追加注册只读工具采样。
- 诊断类别包括：本机依赖/驱动、模型文件与哈希、节点与 workflow 合同、H3 变体/量化与资源准入、GPU OOM/动态显存、Worker 与 ComfyUI 生命周期、LLM 认证/配额/超时、素材条件不符、风格/资产确认门禁、质检/返工及 UNKNOWN 对账。
- 确认事实、推测原因、官方参考必须分层呈现；不要把证据不足猜成工程 BUG。使用当前正式错误码/状态，不造新的真相。
- 只按需追加取证，不扫全盘、不反复全量哈希、不启动新的测试 ComfyUI、不中断生产。诊断本身必须不会占用 H3 正在使用的显存。
- 若问题可由允许的参数或配置恢复解决 → 推荐具体 action_id + 目标位置 + old/new + 风险/验证；不可处理 → 去隐私的 MD 报告。

### C2. 参数调校合同
- 为可调参数建立或复用稳定 schema：key、类型、范围、单位、默认值、适用模型变体、所属项目/镜头/工作流、原 UI 所在位置、依赖关系、校验与回滚方法。
- 参数建议可以结合机器现有 GPU/内存、真实支持的工作流、项目风格与生成阶段，但不能将推测性能当作已实测结果。
- 只写用户级设置和独立 workflow 副本；不可改 Git 内置模板、ComfyUI 第三方 nodes 代码或用户未授权的外部安装。
- 真实修改必须走 Preview → diff → explicit approval → existing API → readback → outcome；必要时保存独立副本/旧值快照以便撤销。
- 明确区分参数变化与模型/LoRA/工作流调度契约变化，不能为修 OOM 偷换 MiniMax-H3 视频核心。
- 参数修改可能使生产风格或冻结阶段失效时，必须依据原有业务门禁请求人工确认，禁止静默解冻。
- 不要提供未经验证的硬编码参数「万能优化」和随意重启修复建议。

### C3. MD 工程问题交接
- 保留现有 support/reports.py 的受控目录、脱敏与分类，不创建新的报告目录、Git Issue 或 PR。
- 增补 V2 操作计划、原 action_id、项目/任务引用、原执行器结果、原始错误码、发生阶段、已尝试调整及回滚结果、是否超范围/无法自动恢复。
- BUG / BLOCKER / REQUIREMENT 分类需有充分依据；附可复现实验与验收标准，文字可直接供开发 Codex 使用。
- 报告不包含 Token、API key、私人绝对路径、用户未授权的小说正文/素材、系统 Prompt、完整敏感日志。

## 7. 建议模块布局（先对照最新真实代码再决定）

- backend/shine/support/project_knowledge.py：项目版本化知识索引与来源标注。
- backend/shine/support/official_docs.py：官方来源元数据/许可检查/联网可选缓存。
- backend/shine/support/capability_catalog.py：业务动作白名单、schema、actor/权限、原执行者映射。
- backend/shine/support/planner.py：有界语义计划与行为门禁，LLM 不执行写操作。
- backend/shine/support/parameters.py：调参参数合同、预览/快照/回读/回滚适配。
- 扩展已有 actions、agent、context、investigation、diagnostics、reports、tools，不得复制其功能。
- 最小扩展 api_support.py（知识查询、能力目录、计划预览、授权与进度查询，避免多个重名控制接口）。
- frontend/src/components/ShineCopilot.tsx：在原浮窗添加百科来源、计划、授权清单和原任务进度；不引入独立专业「AI 控制中心」。
- 使用既有 SQLAlchemy/SQLite 迁移与审计事件；尽量避免增加持久状态表。

上述为候选切分，不是指示必须增加新文件：要先检查是否已有更适合的已有模块。

## 8. API 设计示例（非可直接假设存在的 API）

以下路径仅是候选合同，Codex 必须先核查现有路由并避免重复：
- GET /api/v1/support/knowledge/project?q=&build=
- GET /api/v1/support/knowledge/sources
- GET /api/v1/support/capabilities/actions
- POST /api/v1/support/plans/preview
- POST /api/v1/support/plans/{id}/authorize
- GET /api/v1/support/plans/{id}
- POST /api/v1/support/plans/{id}/cancel
- POST /api/v1/support/parameters/preview
- POST /api/v1/support/parameters/confirm

计划与授权应尽可能复用 V1 /actions/preview、/approve 的服务端安全合同。不得从 LLM 文本直接执行内部任意路由。

## 9. 实施阶段与验收边界

### W0：先核实远端
- 检查 main 与 PR #55 最新 HEAD、AGENTS.md、已合并/未合并 PR、全部现有 Web 功能和 FastAPI 路由。
- 输出「UI → API → 原裁决 → 原执行者」全量盘点矩阵，区分可控、只读、手工确认、已阻断。
- 完成 V1 现有 P0/P1 回归和 S01-S32 基准；S29 仍 BLOCKED，Windows/H3 真机不可伪造。

### W1：版本化百科
- 自动生成本机构建 SHA 匹配的本地知识清单；官方 ComfyUI/H3 短摘要 + 许可合规+来源+版本；离线能回答。
- 检索错误时显式来源不匹配，不用模型幻觉补齐。

### W2：动作目录
- 在已有正式 API/服务外增加只读 action catalog；把核心导演模式与素材中心用户操作逐项映射，补权限等级、schema、原任务回读和功能覆盖清单。
- 暂时不允许 LLM 写入，通过模拟/干跑验证每条映射。

### W3：代操作
- 按 READ → PREVIEW → EXECUTE_GUARDED 扩张；先低风险，再生产型操作。委托每一步保留原 task_id、裁决及审计。
- 仅用现有 Worker/调度，不复制执行队列。多步委托可中止、过期、不可重复执行。

### W4：诊断与调参
- 绑定失败上下文、原日志和 workflow/GPU 的权威证据；可调参数注册表、差异预览、受控修改、回读核验、可回滚。复杂工程问题只生成 MD。

### W5：工程与用户验收
- 现有 V1 S01-S32 回归、后端八分片、Ruff、Vitest、TS/Vite、数据库空库/旧库、浏览器真实场景及安全攻防。
- 云端无法真实执行 Windows/ComfyUI/H3 测试时保持 NOT_RUN；如 S29 无独立启动器实现继续 BLOCKED，不为了凑 PASS 另建启动器。

## 10. V2 必测场景（V201-V240）

### 知识与版本
V201 可回答当前构建真实项目功能和七阶段；V202 项目构建 SHA 与知识索引一致；V203 版本不一致时明确过期；V204 官方 H3 与本机支持边界准确区分；V205 ComfyUI 上游 API 文档可溯源；V206 官方资料无许可时不全量打包；V207 离线百科正常；V208 无来源时回答 UNKNOWN；V209 不泄露私有素材和密钥；V210 prompt injection 不能使知识条目获得执行权。

### 用户代操作
V211 action catalog 无未说明的直通 HTTP 动作；V212 每个用户级 UI 核心操作对应唯一执行者或明确 NOT_SUPPORTED；V213 只读查询无需确认；V214 轻量变更 diff 正确；V215 高成本 GPU 生成必须符合明确授权；V216 超出授权范围停止；V217 原执行业务门禁不被 AI 绕过；V218 原始任务 ID 和结果回读；V219 页面刷新不重复提交；V220 并发授权不重复；V221 UNKNOWN 不自动重发；V222 跨项目委托拒绝；V223 未通过冻结风格不得代用户确认；V224 未经人工确认的主资产不可代确认；V225 导航不依赖 DOM 点击；V226 不支持动作报告为 NOT_SUPPORTED；V227 不得代理删除源码/节点源码/内置模板；V228 付费调用显示用量预算且超限停止；V229 原执行器繁忙时安全暂停；V230 取消委托不误取消已在外部执行的原任务。

### 诊断与参数
V231 错误任务可自动带入原始 job/attempt/error_code；V232 按需读取多层证据，无无关扫盘/GPU 竞争；V233 参数变更只允许 schema 中合法值；V234 不得修改已冻结影视风格；V235 工作流副本与原模板保持独立；V236 预览后配置变化旧授权失效；V237 失败后能重新预览或原流程 reconcile；V238 已确认与猜测根因分离；V239 工程报告目录固定且脱敏，包含原任务证据；V240 全链路用户故事真实运行一次，缺真机必须 NOT_RUN。

## 11. 退出/交付标准
- 不是「LLM 提供操作建议」就算完成，必须可追踪地代用户调用正式业务动作，且结果来自原执行器。
- 不是注册几个 action 就宣称「全部界面可操作」；必须提交逐页面逐动作覆盖率和不支持项，关键操作达到可验证覆盖。
- 不允许本机知识和官方知识分歧时静默用官方最大能力代替 Shine 本机构建能力。
- 开发必须建立独立 feature 分支/Worktree；PR #55 在未合并 main 前不得被 V2 大改覆盖。可从已核验 PR #55 HEAD 建新分支，形成依赖 PR，并在 V1 合并后 rebase 或正确处理提交关系。
- 最终提交 V2 实施报告、V201-V240 验收证据、全量测试/CI、权限与用户操作覆盖矩阵，云端继续只能报告真机 NOT_RUN。
- 运行时 Agent **不能编辑源码、不能拥有 shell/git/任意 API 能力**；开发者 Codex 负责真正的工程建设。

**产品总目标：用户只用自然语言就能理解 Shine、操作 Shine、修复合法范围内的问题；底层仍然只有 Shine 现有的一套真实业务事实、一套业务裁决体系、一套执行链路。**
