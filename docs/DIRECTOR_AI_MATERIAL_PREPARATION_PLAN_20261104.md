# 导演模式「出品人 Agent 与 AI 素材准备」完整建设方案

> 文档版本：1.0；整理日期：2026-11-05（北京时间）。文件名沿用需求稿的 20261104，便于交接引用。
> 状态：待实施的产品与工程规范，不是功能完成或硬件验收报告。
> 已核对代码基线：`main / bff25ad`，应用版本 `0.2.0`。实施前必须重新核对 HEAD、工作区与运行状态。
> 配套文件：[本地建设 Agent 开工提示词](LOCAL_AGENT_MATERIAL_PREPARATION_PROMPT_20261104.md)。

## 1. 目标与交付边界

让用户从一份剧本开始，在导演模式完成「理解剧本 → 确认素材清单 → 补齐候选 → 人工选用 → 冻结影视风格 → 分镜 → H3 制作」。解决当前用户必须事先在外部准备所有图片、素材与剧本身份难以对齐的问题，同时保持现有简化界面、唯一资产库和生产门禁。

本轮建设的是工作台内部的准备能力：编剧和出品人通过现有 LLM Gateway 工作；本机受管 ComfyUI 执行图片生成；唯一常驻 Worker 调度持久任务。Codex 等开发 Agent 仅用于建设，不能成为产品运行依赖。

### 1.1 已确定的产品决定

| 项目 | 本轮决定 |
| --- | --- |
| 图片模型 | **Qwen-Image-2.1**；原需求中的 Z-image2.1 已修正，不得静默替换 |
| 生成范围 | 主要角色、场景、核心道具 |
| 角色素材 | 灰白中性背景、单人半身定妆照、完整头部、清晰面部 |
| 场景素材 | 无人物环境图，保留剧本规定的空间、时代、天气和光线 |
| 道具素材 | 灰白中性背景、单件、完整轮廓，无持物人物 |
| 生成触发 | 用户确认需求与视觉方向后，一键生成选中的缺失项 |
| 默认数量 | 每项一张；用户可修改描述后再生成一版，保留历史 |
| 主资产权限 | AI 只创建候选；用户明确确认后，经现有授权流程晋升 |
| 风格顺序 | 先确认素材视觉方向，再冻结完整 Style Bible |
| 视频生产 | 继续由本地 MiniMax-H3 / ComfyUI 执行，默认多参考 Ref2VA |
| 用途 | 本地研究与评估，不宣称商业授权 |

首版不包含图生图、换脸、扩图、音色生成、多视图拼图、镜头画面生成、自动批准或替换主资产。服装与材质可作为人物或道具描述，不扩张为独立生成类别。用户仍可完全通过上传素材完成制作。

### 1.2 规范优先级与继承

先读取当前 `AGENTS.md`、V1.0 建设方案、首轮开发提示词、V1.1 修订方案，再结合现有代码。旧文档中的「只允许人工提供主素材」按 V1.3 修订解释：允许 AI 候选，禁止 AI 自行晋升。旧提示词中的创建仓库、重建项目等历史指令不适用于现有仓库。

本方案只新增图片准备能力，不放宽 H3 参考槽位、角色身份、风格冻结、预制作准入、授权、GPU 租约及未知提交对账规则。冻结前允许素材准备，不等于冻结前允许视频生产。

### 1.3 应交付的成果

1. 版本化剧本准备、素材需求、视觉方向及用户确认契约。
2. 持久化素材批次、单项任务、图片执行尝试、恢复与取消能力。
3. Qwen-Image-2.1 安装登记、受管运行时、工作流审计与三类真实小样验证。
4. 编剧 `script_prepare` 与出品人单项 `material_image_brief` 工具。
5. 导演模式四阶段素材流程、基础配置能力卡与既有 H3 制作衔接。
6. 兼容迁移、自动化回归、Windows 入口和安装包验证、分层验收报告。

## 2. 已核对的现状与工程落点

以下是本次读取 `bff25ad` 源码得到的现状；「拟新增」不代表现有接口。

| 现有入口 | 当前可复用内容 | 本轮增量 |
| --- | --- | --- |
| `backend/shine/comfyops/models.py` | `CandidateAsset`、`AssetRequirement`、`PreProductionPlan`、`ApprovalGrant`、`ResourceLease` | 计划用途隔离、不可变版本、批次与单项关联 |
| `backend/shine/models.py` | `AgentTaskRecord`、项目、文档、主资产 | 复用任务账本，补充必要的 claim/attempt 关联 |
| `backend/shine/comfyops/tasks.py` | 准备任务意图与审计、未知状态阻止重放 | 扩展为可由 Worker 认领、续租、恢复的后台执行 |
| `backend/shine/comfyops/images.py` | `ComfyImageProvider` 提交、轮询与取图 | 提交前完整图持久化、明确输出节点、尺寸映射、分阶段恢复 |
| `backend/shine/api_preparation.py` | 候选生成/导入/查看/QC/晋升与预制作 API | 新导演入口；候选生成收敛到统一执行服务 |
| `backend/shine/comfyops/preproduction.py` | 生产分析、`current_plan`、`readiness`、`enforce` | 所有生产读取显式过滤 `purpose=production` |
| `backend/shine/agents/film_contracts.py` | `producer` 路由和 `ProductionPlan` | 保留旧契约，新工具单独选择输出 Schema |
| `backend/shine/asset_identity.py` | `entity_id` 权威关联 | 复用身份解析；推荐匹配不成为正式绑定 |
| `frontend/src/components/DirectorSetupPanel.tsx`、`SetupPanel.tsx` | 导演基础配置与通用安装 UI | AI 素材能力卡与就绪证据 |
| `backend/shine/comfyops/renderer_release.py`、现有 Worker | 受管所有权与 GPU 释放 | 图片与 H3 共用资源治理 |

当前图片 provider 的 `generate` 是同步提交/轮询调用，并从历史输出中遍历取图；候选生成入口在缺配置时存在 Fake provider 分支。新正式入口必须显式要求真实模式，并拒绝 Fake。不能仅给现有同步调用包一个 HTTP 202 就声称已实现持久化后台恢复。

`current_plan` 当前按项目取最新计划，尚无用途过滤。新增素材准备计划前必须先隔离生产选择器，否则素材计划可能抢占冻结生产合同。

允许增加小型素材领域模块、批次关系表或执行尝试表，但它们必须复用同一个 SQLite、Worker、任务认领与审计机制；不增加第二个常驻调度服务或第二套主资产库。具体物理表名与已有字段复用在阶段 A 的 ADR 中固定。

## 3. 导演模式用户流程

### 3.1 页面与旅程

沿用现有路由和设计语言，将五步旅程呈现为：

**建作品 → 读剧本、备素材 → 定风格 → 出分镜 → 制成片**

「素材与风格」页面按以下四个阶段展开。当前阶段只有一个醒目的主操作；完成阶段折叠为可展开摘要。

| 阶段 | 内容 | 主操作与完成判定 |
| --- | --- | --- |
| 读剧本 | 上传/选择现有文档、当前版本、编剧进度、分场概要 | 「分析剧本」；所有分场处理完成且关键歧义已处理 |
| 确认需求 | 角色/场景/道具清单、原文依据、已有素材推荐、视觉方向 | 「确认清单并补齐」；保存版本化确认，有生成项才创建批次 |
| 选择素材 | 上传素材、候选、逐项进度、历史版本、可恢复错误 | 「确认使用所选素材」；每个必需项绑定有效主资产 |
| 定风格 | 已选素材、视觉方向摘要、现有七项影视风格约定 | 「冻结风格并继续分镜」；用户完成既有冻结操作 |

「确认清单并补齐」可以在一次 UI 点击中顺序调用确认和生成两个 API，但确认成功不意味着批次一定创建成功。界面必须分别显示结果；刷新后用幂等键查询，不自动重新提交。已全部有素材时文案改为「确认清单并继续」。

制作页引用同一文档 ID/hash 与已确认身份映射，不要求再次导入。已有项目不强制回退或重做；旧风格、镜头与成片保留。当用户主动启用新准备流程时，再建立素材计划并显示与旧合同的差异。

### 3.2 清单、匹配与人工确认

- 已确认且匹配当前需求的有效主资产优先复用。
- 已上传但待批准/锁定的资产属于「待确认」，不得自动当作缺失项生成。
- 名称、标签、文件名、上传顺序仅用于推荐，展示匹配理由；最终关联以用户确认后的 `entity_id` 为准。
- 同名、别名、跨场重复人物需要服务端归并或用户消歧，不允许 LLM 编造正式资产 UUID。
- 每个必需项选择「已有素材」「上传」「生成候选」之一；生成仅覆盖用户选择的缺失项。
- 必需项变为非必需项须用户明确修改并保留理由，不能由 Agent 为绕过门禁自动取消。
- 生成授权与结果晋升必须分开；不能在生成按钮中预授权未来候选。
- 点击选中一张图片只是 UI 选择。执行「确认使用」才申请并消费绑定候选 hash 的一次性晋升授权。
- 批量确认在界面允许一次操作，但每项使用各自快照/授权；部分成功须逐项展示，不宣称整个批次原子成功。
- 晋升创建可追溯的新主资产；覆盖或替换已确认绑定必须是明确用户操作，产生新版本并走依赖失效流程。

### 3.3 完成判定与版本变化

素材完成条件：当前确认计划的所有必需需求均绑定本项目、正确类型与身份、当前有效、已批准且已锁定、文件哈希校验通过的正式主资产；真实项目不能绑定 `offline_only` Fake 主资产。零必需项只在完整分析和用户确认确实无需这些素材后成立，不能因分析未完成而空集通过。

新剧本来源、需求、视觉方向或绑定变化均创建新版本。旧候选保留来源但标为过期/待重新选用；旧生成授权失效，不对旧图片自动晋升或重新生成。正在执行的旧版本任务可以保存结果，不能自动满足新版本需求。

冻结后的 Style Bible 若与素材视觉方向冲突，展示差异，让用户明确选择调整素材或建立新风格版本；不修改旧冻结合同。明确重新选用历史候选时重新验证来源、字节与当前需求，并获取新的晋升/绑定授权。

### 3.4 进度与错误呈现

服务器返回持久化阶段、已完成/失败/待确认数、UTC 时间戳、当前项与下一步。前端重新挂载、刷新或切换作品只重新查询任务；旧请求响应不能写入新作品状态。保留现有秒级计时及无时区时间补 `Z` 的约定。

错误显示中文说明、稳定 reason code 与具体下一步，例如「生成结果还未核对，暂不能重试」。等待 GPU 与执行中分开显示，不用虚构百分比掩盖等待。所有必要用户动作沿用 `.required-action`、`.must-button` 与 `.module-guide`。

## 4. Agent 职责、契约与上下文

### 4.1 职责分工

| 参与方 | 可以做 | 不可以做 |
| --- | --- | --- |
| 前期编剧 `script_prepare` | 提取原文事实、按场拆解、提出角色/场景/核心道具需求和歧义 | 创建镜头、冻结风格、生成资产、提交 H3 |
| 出品人 `material_image_brief` | 把已确认的单项需求转为短小外观描述 | 新增剧情事实、选工作流、调 GPU、批准资产 |
| 服务端 | 分场、归并身份、预算预检、匹配建议、任务/工作流/参数/种子和确定性提示词 | 将推荐当成人工批准，伪造真实验证 |
| 用户 | 确认清单与视觉方向、选用主资产、冻结影视风格 | 不要求用户手写 JSON、节点映射或审批证据文本 |

复用角色路由 `producer`，导演层显示「出品人」。旧 `ProductionPlan` 和旧调用语义保持不变；按工具/产物种类选择新 Schema，禁止直接把 producer 的默认输出全局改成图片描述。

### 4.2 `ScriptPreparation`：前期剧本准备

LLM 每次处理一个有编号的场或受控片段，输出浅层对象。建议初版边界如下，实施时固化为 Pydantic/JSON Schema 并测试；这些是产品 Schema 上限，不是模型能力宣称。

| 字段 | 类型与上限 | 规则 |
| --- | --- | --- |
| `schema_version` | 固定字符串 `1.0` | 不由模型自选版本 |
| `scene_key` | 字符串，≤80 字符 | 必须等于输入的场编号 |
| `summary` | 字符串，≤800 字符 | 不新增剧情 |
| `locked_facts` | 字符串数组，≤24 项，每项≤300 字符 | 与输入锁定事实核对 |
| `requirements` | 固定结构数组，≤32 项 | 单项只有 family、name、description、source_span_ids、questions |
| `questions` | 字符串数组，≤8 项，每项≤200 字符 | 影响身份/剧情的缺失信息必须提出 |

`requirements` 内 `family` 仅允许 `character/scene/prop`；`name` ≤100 字符，`description` ≤600 字符，引用片段 ≤8 个，问题 ≤4 个。所有对象拒绝额外字段。超上限不得静默删减，继续拆分或拒绝并显示可操作原因。

服务端外层封装项目、文档 ID/hash、源片段偏移、输入 hash、任务和产物 ID、路由与模型版本。原文定位由服务端验证，模型输出不存在的片段编号立即拒绝。归并结果分配稳定 `requirement_id/entity_id`，歧义待用户处理；模型不分配数据库 ID。

前期 `ScriptPreparation` 与冻结风格下的正式 `ScriptBreakdown` 分离。后续分镜链读取已确认准备结果、身份映射与原文，重新生成正式契约，继续现有时长/对白/规则门禁。

### 4.3 `MaterialImageBrief`：单项图片描述

模型只返回以下对象。`requirement_id`、类别、项目与视觉方向等在服务端 envelope 中固定，不信任模型回填身份。

```json
{
  "schema_version": "1.0",
  "appearance": "已确认人物的年龄段、发型、服装轮廓与材质描述",
  "must_keep": ["已确认时代", "剧情要求的服装特征"],
  "avoid": ["与剧本冲突的现代饰品"],
  "questions": []
}
```

限制：`appearance` 1–1200 字符；`must_keep/avoid` 各≤12 项，每项≤160 字符；`questions` ≤6 项，每项≤200 字符。字段固定、拒绝额外属性；无 Markdown、递归对象或动态嵌套。存在影响执行的澄清项时进入待确认，不提交图片生成。

服务端通过独立的图片提示词模板追加构图规则和已确认视觉方向；固定模板版本/hash。它不属于 `H3PromptCompiler`，不得输出 H3 标签。禁止项是否映射独立 negative prompt，必须以已验证工作流能力为准；不支持时只使用经审计的文本组装规则，不虚构节点参数。

### 4.4 上下文预算与恢复

输入仅包含当前项、相关原文片段、锁定事实、视觉方向、必要角色边界和 Schema。不得传全图 JSON、Base64、完整历史、全量资产库。图片首版纯文生图，不读取用户素材字节发送远程 LLM。

每次调用预检 `输入估算 + 输出预留 + 安全余量 ≤ 已配置上下文窗口`，保留估算方式和预算证据。窗口未知时采用明确配置的保守上限或显示待配置，不能无限放大。预算不足优先按场拆分；单段仍超限则拒绝并提示拆分，绝不截断锁定事实。场景很多时先分段，再服务端归并；别名冲突不能只靠字符串相等自动合并。

保留 `SHINE_AGENT_STEP_DELAY_MS` 的实际调用间隔与显式角色路由。已知收到响应但 Schema 错误，最多一次定向修正，只带错误字段、校验器、原任务和必要片段。仍失败则保存受控产物与错误位置，允许单项修复。日志不输出完整 provider 文本或密钥。

超时、断线且外部结果未知时保留 `UNKNOWN/NEEDS_REVIEW`，不计入可自动格式重试。若远程 provider 无查询能力，明确报告不可对账；只有用户明确发起独立新请求时才建立替代任务，旧请求未知事实永远保留。

## 5. 数据、版本与审批设计

### 5.1 唯一账本与用途隔离

给 `PreProductionPlan` 增加持久化 `purpose`，允许 `asset_preparation/production`，历史记录回填 `production`。增加按项目/用途查询的索引。所有生产查询、冻结、重新绑定、readiness、enforce、Worker 读取与按 ID 操作均校验用途；不仅修改一个 `current_plan` 调用点。

素材计划的「用户确认」和生产计划的「冻结」使用不同状态/动作，不能用素材计划通过生产 `enforce`。既有正式计划 ID/hash/批准证据保持原样；新增列不应顺便重算历史 payload hash 或作废授权。

### 5.2 持久化信息分配

| 对象 | 至少保存 |
| --- | --- |
| 素材计划修订 | purpose、项目、文档 hash、准备产物、需求集合、视觉方向快照/hash、确认版本、状态 |
| 素材需求 | 计划修订、稳定身份、family、必需标记、原文依据、确认描述、选择策略、绑定资产版本/hash |
| 批次 | run_id、项目、确认快照、幂等键/请求 hash、选中项、执行模式、状态、取消标记、时间 |
| 单项任务 | AgentTaskRecord 关联、需求修订、brief 版本、状态/state_version、claim owner/expiry/heartbeat、当前 attempt |
| 图片执行尝试 | attempt_no、输入/图/模板 hash、固定 seed/宽高、工作流/模型/运行时指纹、输出节点、提交意图、prompt_id、external outcome、lease、错误与时间 |
| 候选 | 复用 CandidateAsset；内容 hash/尺寸/字节数、任务/尝试/需求/批次、来源与技术 QC、is_mock/offline_only、formal_asset_id |
| 审计 | 人工确认、执行、恢复、取消、晋升、绑定变化、资源释放；保留 actor 与快照 |

复用已有字段时记录映射，缺失的并发控制字段使用可索引列，不把全部状态塞进无法原子条件更新的 JSON。批次是任务集合的领域视图，不是第二个调度器。

### 5.3 幂等与不可变约束

- API 幂等键按项目和操作作用域存储；相同 key + 相同请求 hash 返回原结果，相同 key + 不同 payload 返回 409。
- 另设业务唯一约束：同一需求修订/生成版本最多一个未结束执行意图；两个浏览器用不同 key 也不能重复提交同一版本。
- 「再生成一版」明确创建新生成修订、brief/seed 和授权快照，保留旧候选；不是清空旧 attempt 重跑。
- 所有 Worker 写入校验任务状态、state_version、claim owner 与需求修订。过期 claim 可重新认领，但外部提交不得因此重发。
- 更新需求或视觉方向后，旧 confirm/grant 不可再用；旧批次结果只挂在旧修订。
- 晋升 grant 绑定候选 ID、内容 hash、目标身份、动作与当前确认版本，原子消费；跨项目、过期、重放或快照变化均拒绝。
- 继续同源 loopback、CSRF 与 `local-user` 约定，不要求操作者密码或手打权利证明。
- 生成来源与研究用途如实记录，不把模型许可误写成用户已拥有独占版权。已有 rights/grant 结构应作兼容适配，不绕过。

### 5.4 迁移策略

在全新测试库与脱敏旧库副本验证 Alembic upgrade。测试旧生产合同仍选中、新素材计划可并存、同一时间戳下选择稳定、外键与唯一约束生效。只有数据库结构确实需要变化才添加迁移；不得以 `create_all` 代替。

开发与真实验收使用独立数据目录，禁止在用户正式库执行破坏性升级/降级实验。已有新数据时回退优先关闭新功能、保留数据和兼容读路径；不要为回退直接删除候选、批次或新表。

## 6. API 契约

以下为拟新增路径，统一位于 `/api/v1`。详细请求/响应由阶段 A 生成 OpenAPI 与前端类型，保持本文语义。

| 方法与路径 | 请求关键字段 | 返回与要求 |
| --- | --- | --- |
| POST `/projects/{id}/material-preparation/analyze` | document_id、expected_document_hash、idempotency_key | 202 + task_id；相同输入复用现有分析 |
| GET `/projects/{id}/material-preparation` | 可选 plan_revision_id | 当前计划、分析进度、需求/建议/绑定、能力与阻塞 |
| POST `/projects/{id}/material-preparation/confirm` | plan_revision_id、expected_snapshot、逐项选择、visual_direction | 确认记录/hash；消歧完成后才能确认 |
| POST `/projects/{id}/material-preparation/generate` | confirmation_id/hash、requirement_ids、idempotency_key、mode=real | 202 + run_id；只接受当前已确认生成项 |
| GET `/projects/{id}/material-preparation/runs/{run_id}` | 无 | 批次/逐项状态、候选、错误、可执行恢复动作 |
| POST `/projects/{id}/material-preparation/runs/{run_id}/cancel` | expected_state_version、idempotency_key | 202 + cancel 状态；请求取消≠已停止 |
| POST `/projects/{id}/material-preparation/runs/{run_id}/reconcile` | expected_state_version、选定项、idempotency_key | 202；只核对与采用，不重新提交 |
| POST `/projects/{id}/material-preparation/runs/{run_id}/resume` | 已确定失败/未开始项、expected_snapshot、idempotency_key | 202；拒绝 UNKNOWN；可复用原 intent 或创建明确新尝试 |
| POST `/projects/{id}/material-preparation/requirements/{requirement_id}/regenerate` | 当前确认版本、修改后的 brief/描述确认、idempotency_key | 202；新生成修订，不覆盖旧候选 |
| GET `/setup/image-readiness` | project_id | 按类别返回四级就绪状态、证据、失效原因与下一步 |
| POST `/setup/image-runtime` | instance_id、workflow_revision_id、expected_config_revision | 绑定已登记受管实例与固定配置；不触发下载 |
| POST `/setup/image-runtime/validate` | project_id、配置指纹、idempotency_key | 202 + 验证任务；真实三类小样，共用队列 |

`resume/reconcile/regenerate` 若能用现有同语义工具承载，可以复用，但必须在实现报告固定最终路由、前端操作及测试，不能遗漏恢复入口。API 不接受任意服务器路径、任意工作流 JSON 或不受控 URL。

复用现有候选路径：`GET /projects/{project_id}/candidate-assets`、`GET /candidate-assets/{identity}/media`、`POST /candidate-assets/{identity}/qc`、`POST /candidate-assets/{identity}/promote`。晋升授权使用现有审批流程；上传主素材继续已有入口。所有读取和操作都检查项目归属。

错误响应至少包含 `code`、安全的 `message`、`task_id/run_id`（适用时）、`recoverable`、`allowed_actions`；兼容既有错误 envelope，不能破坏旧客户端。预期版本冲突/未就绪用 409，输入合同不合法用 422，资源不存在用 404。

建议错误码：`MATERIAL_PLAN_STALE`、`MATERIAL_CONFIRMATION_REQUIRED`、`MATERIAL_IDENTITY_AMBIGUOUS`、`CROSS_PROJECT_REFERENCE`、`IMAGE_RUNTIME_NOT_READY`、`IMAGE_REAL_PROVIDER_REQUIRED`、`IMAGE_OUTPUT_NODE_MISMATCH`、`IMAGE_SUBMISSION_OUTCOME_UNKNOWN`、`IMAGE_OUTCOME_RECONCILIATION_REQUIRED`、`ARTIFACT_HASH_MISMATCH`、`GPU_RECONCILIATION_REQUIRED`、`LLM_CONTEXT_BUDGET_EXCEEDED`。已有等义错误码优先复用。

## 7. 持久化执行、取消与恢复

### 7.1 状态机

下列为领域状态语义；实现时映射到既有账本枚举，禁止只是前端状态。

```text
计划：ANALYZING → DRAFT → CONFIRMED → PREPARING → MATERIALS_READY
                      ↘ NEEDS_CLARIFICATION
修订变化：旧计划/结果保留，当前视图标记 STALE

单项：QUEUED → BRIEF_RUNNING → BRIEF_READY → WAITING_GPU
      → SUBMIT_INTENT → RUNNING → OUTPUT_STORED → QC_PENDING → CANDIDATE_READY
      → FAILED / NEEDS_REVIEW / CANCELLED（按真实执行结论）

批次：QUEUED → RUNNING → COMPLETED / PARTIAL_FAILED / NEEDS_REVIEW
                   ↘ CANCEL_REQUESTED → CANCELLED / NEEDS_REVIEW
```

`CANDIDATE_READY` 只表示可供人工选择，不表示晋升或主资产批准。批次完成也不等于素材准备完成；后者仍等用户选用。混合结果保留逐项结论；存在未知外部状态时批次不能标成完全完成或完全取消。

### 7.2 提交事务边界

1. API 在短事务中验证确认快照，建立批次/任务与幂等记录，提交后返回。
2. 常驻 Worker 原子认领任务，准备单项 brief；已存在合法产物则复用，不重新调用模型。
3. 获取共享 GPU 租约，验证受管实例、节点、模型、工作流指纹与用户确认仍有效。
4. 服务端确定参数并构建最终 API 图，将完整图 hash、seed、输出节点和 attempt/submit intent 持久化，提交事务。
5. 在 SQLite 写事务外调用 `/prompt`；禁止 HTTP 客户端自动重试这个非幂等提交。
6. 收到 prompt_id 后立即短事务保存，随后可恢复轮询/下载。心跳遇锁忙只延后，不虚报失租。
7. 只读取固定输出节点与明确输出槽的最终图片；多个输出按固定规则校验，歧义拒绝。
8. 安全写入临时文件，限制字节/像素，检查真实格式、完整解码、尺寸及 SHA-256，原子归档后登记候选和审计。
9. 完成资源释放确认，再更新批次状态。卸载失败或所有权未知保留租约并阻止后续 GPU 任务。

ComfyUI 返回的 filename/subfolder 都是外部数据，不能直接拼接为任意本机目标路径；通过受控取图接口和工作台存储路径写入，拒绝路径穿越、重定向和超限数据。

### 7.3 恢复决策表

| 中断位置/证据 | 恢复动作 |
| --- | --- |
| 尚未建立外部 submit intent | 重认领并继续未开始步骤 |
| 有 intent，无 prompt_id，不能证明未提交 | UNKNOWN，查队列/历史及可用关联元数据；查无结果不等于未提交 |
| 有已确认 prompt_id | 查询该任务，不建立第二次提交 |
| 已有 output 文件与匹配身份链 | 先验证字节/尺寸/hash，再登记采用并继续技术 QC |
| 文件已归档但数据库未提交 | 通过暂存 journal/确定路径核对 attempt 身份，幂等补登记；不按文件名猜测 |
| 文件损坏/篡改 | 拒绝采用，保留原因，不留下成功 adoption 记录 |
| 明确执行失败且无在途提交 | 允许用户恢复或新建受限尝试；失败项不拖累独立项 |
| GPU 所有权不明或停止未确认 | 暂停后续 GPU 工作并对账，不能按超时释放 |

身份链至少包含项目、run/item/attempt、已知外部 prompt_id、最终图 hash、工作流/模型指纹及输出内容 hash。文件真实存在并通过验证是采用前提，ComfyUI 历史只作辅助证据。未知 prompt_id 的产物不能仅凭文件名归属。

### 7.4 调度与所有权

图片、H3、局部审片共用现有资源租约。H3 series 持有租约期间图片任务等待，不抢占；图片批次逐项执行，单项结束确认释放后让现有调度器决定下一任务，不因长批次饿死视频队列。

进程停止严格复用 PID/出生时间/命名 Job Object 证明；不接管外部 ComfyUI。端口空闲不能单独证明 GPU 已释放。宿主注入 provider 的生命周期由宿主控制；仅重建工作台自建 provider。绑定配置变更不改变已提交尝试使用的运行时指纹。

取消首先阻止未开始项。当前任务若可定向取消且所有权已证明，则取消并核对；不得用全局 interrupt 影响外部任务。不能可靠定向取消时显示「已停止后续任务，当前项正在收尾/待核对」。已经成功生成的候选保留，不自动删除、不晋升。

## 8. Qwen-Image-2.1 就绪与上游审计

### 8.1 来源与许可

审计起点：[ComfyUI 官方文生图模板](https://github.com/Comfy-Org/workflow_templates/blob/main/templates/image_qwen_image_2_1_t2i.json)。2026-11-05 已核对该文件页面存在；这不是本机兼容或生成成功的证据。

[Qwen-Image-2.1 官方许可证](https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/LICENSE)第 1.i 与第 2 条将非商业用途限定为研究或评估，商业使用需另行取得许可。本轮保持这一用途边界；部署页展示来源和限制，资产晋升不能产生商业授权证明。实际安装前复核所固定版本的条款及各配套权重许可。

阶段 A 更新 `docs/upstream-audit.md`，记录模板 commit/blob/hash、模型仓库 revision、每个下载文件的 SHA-256/大小/用途/许可、ComfyUI commit、节点依赖与源码复用边界。未取得的数值填 `PENDING_VERIFICATION`，禁止猜测模型体积、显存或版本。漂移的 main URL 只作发现入口，执行使用固定版本。

### 8.2 工作流与新机安装

- 将 UI 模板导出/转换为真实 API 图，展开子图并校验；UI JSON 不直接提交 `/prompt`。
- 明确 prompt、seed、width、height 及实际支持参数的 parameter_map，固定最终输出节点。
- 采样器、步数、精度、分辨率档位沿用经审计且本机可运行的基线，不能让出品人选择。图片尺寸不擅自沿用 H3 的 24 fps/尺寸合同。
- 检测现有安装可复用文件并校验 hash；需要新依赖且与 H3 不兼容时，创建隔离受管实例，保留原 H3 环境。
- 安装 UI 展示实际下载量、最终占用、临时空间需求、许可证、目标盘和后续小样；已有下载确认可覆盖展示的有序步骤，配置变化重新确认。
- 不直接安装未经审计节点、不把 GPL 源码复制进主仓库、不将权重打入发布包。

### 8.3 能力状态与失效

| 层级 | 必要证据 | 可对用户声明 |
| --- | --- | --- |
| 未配置 | 无有效绑定/缺依赖 | 尚不能 AI 补素材 |
| 文件已校验 | 包 revision、逐文件 hash、运行时路径 | 文件齐备，尚未出图验证 |
| 节点与图已校验 | 实际 object_info、节点输入输出、参数图与依赖验证 | 工作流校验通过，尚未实测 |
| 实际出图验证通过 | 真实生成、解码、hash、尺寸、输出节点与当前配置指纹 | 对应类别技术出图能力已验证 |

角色、场景、道具分别有验证结论，三类都完成才声明三类可用。构图和美术质量仍需人工评审；技术出图成功不能自动证明「场景无人」或「人物正确」。可选 VLM 只提供 advisory，不进入硬执行关键路径。

验证任务本身允许在「节点与图已校验」后执行，解决首次验证的循环依赖；普通素材批次要求对应类别已有当前有效实测证据。证据固定运行时、模型文件、节点、API 图、执行适配器/提示词模板及参数档指纹；相关变化后降级重验，不无限沿用旧 READY。

图片能力未就绪只阻止 AI 生成；用户可继续上传、选用和既有 H3 生产。禁止自动切换 Fake、云图片 API 或另一模型。

## 9. 与正式 H3 制作的衔接

人工晋升后，按当前确认身份进行主资产绑定；分镜使用正式 `ScriptBreakdown` 与现有角色绑定流程。`entity_id` 是权威键，tags 不参与最终身份判定。

正式生产重新运行现有预制作分析和冻结。素材计划不充当生产计划；图片 workflow 不充当 H3 workflow。H3 继续校验 Style/Rule hash、每镜主资产、请求局部标签、实际 reference slots 和 media_transfers。

人物参考顺序继续服从 `spec.characters`，引用不够必须显示 `PENDING_WORKFLOW_MAPPING` 或现有精确原因；不能丢失角色、把生成图偷偷替换成其他角色图，或伪造宽槽位 profile。中性背景是本产品素材规范，不是 H3 官方要求。

音色仍是可选增强，首版素材生成不创建 voice 需求依赖。无对白镜头不补人声参考，继续现有禁声合同。新候选不会自动替换历史镜头、成片或通过版本；用户确认替换后才按既有规则使相关依赖失效。

若修改触及 `client_review_identity` 绑定文件，既有 VLM 能力证据按规则失效。真实 H3 验收前重新正/负校准、发布新 revision、弃用旧 revision、重新分析并冻结正式计划；不要把文生图验证当成 VLM 校准。

## 10. 实施阶段与交付门槛

| 阶段 | 工作包 | 退出条件 |
| --- | --- | --- |
| A 契约与迁移 | 基线检查、上游审计、ADR、Schema、purpose 隔离、幂等/状态设计 | 旧生产计划选择不变；新计划不能通过生产门禁；Schema 与迁移测试通过 |
| B 运行时与恢复 | setup、受管图、Worker 任务、意图/取图/QC、取消/对账 | 有持久化任务入口；崩溃窗口测试通过；真实配置缺失明确阻塞 |
| C Agent | 编剧前期准备、出品人单项描述、预算/修正/路由 | 未冻结可准备；旧 ProductionPlan 可用；长剧本及 UNKNOWN 测试通过 |
| D 导演界面 | 四阶段、能力卡、素材确认、历史/进度、分镜交接 | 刷新/切换/重复点击不重提；用户动作和候选晋升可从 UI 完成 |
| E 验收与交付 | 完整回归、三类真实出图、人工晋升、真实 H3 引用、Windows 两链 | 按证据分层交付，记录阻塞与入口/安装包结果 |

A→B→C→D→E 是接续顺序，每阶段读上一阶段报告后继续。真实硬件阻塞时仍完成可独立开展的代码与离线验证，不把阻塞伪装成通过，也不反复重试未知任务。

每阶段更新 `docs/PROGRESS_DIRECTOR_AI_MATERIAL_PREPARATION.md`：当前 HEAD/分支、改动、迁移、实际命令与结果、未决调用/租约、下一步。最终输出 `docs/ACCEPTANCE_DIRECTOR_AI_MATERIAL_PREPARATION.md`。这两个文件由实施 Agent 创建，不是本文已完成的交付。

## 11. 测试矩阵与验收命令

### 11.1 必须覆盖的自动化情形

| 领域 | 测试情形 | 期望 |
| --- | --- | --- |
| 清单与身份 | 无素材/部分/全部/待确认上传、同名/别名、跨项目、分析空结果 | 不误判齐备、不重复生成待确认素材、不越权绑定 |
| 计划隔离 | 旧冻结生产计划 + 新素材计划、同时间戳、多修订、按 ID 冻结 | 所有生产路径只读 production；历史 hash 不变 |
| 前期与正式契约 | 未冻结准备、未冻结 H3、旧 producer、前期产物冒充分镜 | 准备允许，生产拒绝；旧契约兼容 |
| Agent | 长剧本、单段超限、错误片段编号、提示词注入、Schema 错误、UNKNOWN | 不丢事实、一次格式修正、未知不重放、不执行剧本指令 |
| 并发 | 双 Worker、双浏览器不同幂等键、过期版本、claim 过期 | 单次外部提交，旧 worker 无权写入 |
| 恢复 | 提交前/响应丢失/prompt_id 后/下载中/归档后/QC 前崩溃 | 对账与字节验证后恢复；不重复生图 |
| 输出 | 预览节点、非声明节点、多个歧义输出、坏图、哈希篡改、路径穿越 | 硬拒绝；无错误 adoption 记录 |
| 资源 | H3 series 正在运行、图片未释放、外部实例、PID 重用、心跳锁忙 | 互斥、所有权保守、锁忙不失租 |
| 授权 | 过期/重放 grant、改 hash、Agent 晋升、Fake、部分批量成功 | 只允许当前人工授权，真实入口不回退 Fake |
| UI | 刷新/切作品/断线/取消/单项恢复/再生成/上传继续 | 任务持久、状态隔离、操作原因可见 |
| 安装兼容 | 新机、已有 H3、文件复用、指纹变更、缺运行时 | H3 环境保留，就绪准确降级 |

拟新增专项测试可按实际模块拆分为 `test_material_preparation.py`、`test_material_image_jobs.py`、`test_image_runtime.py`、`test_material_agent_contracts.py` 与前端素材旅程测试。文件名为建议，验收情形必须保留。

### 11.2 仓库回归

以下命令在仓库根目录执行；先使用独立临时 `SHINE_DATA_DIR` 进行迁移验证，记录并在结束后恢复原环境变量。不要对用户正式库跑验收迁移。

```powershell
uv run --project backend --locked pytest -c backend/pyproject.toml backend/tests/test_offline_e2e.py backend/tests/test_film_crew.py backend/tests/test_v13_preparation.py backend/tests/test_v13_migrations.py backend/tests/test_v13_e2e.py backend/tests/test_comfy_completion_window.py backend/tests/test_production_series.py backend/tests/test_advisory_queue.py backend/tests/test_comfy_handoff_journal.py backend/tests/test_h3_compiler.py backend/tests/test_managed_process_release.py
uv run --project backend --locked pytest -c backend/pyproject.toml backend/tests
uv run --project backend --locked ruff check backend/shine backend/alembic backend/tests
uv run --project backend alembic -c backend/alembic.ini heads
uv run --project backend alembic -c backend/alembic.ini upgrade head
uv run --project backend alembic -c backend/alembic.ini check
```

前端目录执行：

```powershell
npm.cmd test
npx.cmd tsc --noEmit
npm.cmd run build
```

新增运行依赖时更新锁文件；命令前确认使用仓库规定的 Python 3.12/uv 环境。若独立 `tsc --noEmit` 因项目引用布局不能覆盖应用，以现有 `tsc -b` 构建验证为准并说明。保留 Windows CI，不能只报告 Linux 结果。检查失败应修复或记录精确阻塞，不删除测试/降级门禁。

### 11.3 真实本机端到端

使用独立测试项目，从导演 UI 完成以下操作，保留任务证据：

1. 导入含主要人物、场景、核心道具的短剧本，真实编剧拆解并确认清单。
2. 确认视觉方向，通过 setup 注册并验证三类真实 Qwen 小样。
3. 从正式素材入口生成三类候选，验证刷新后可恢复进度；人工评审角色、场景与道具构图。
4. 用户明确晋升与绑定，证明 Candidate → Asset 的授权记录及身份链。
5. 用户冻结风格，生成正式分镜，验证对应图片确实出现在每镜 manifest 和映射节点。
6. 通过既有 H3 真实生产生成短片，检查实际上传引用、角色顺序、产物与结果页面。
7. 在受控测试任务演练已知响应丢失/Worker 重启恢复；不故意破坏正式项目运行。

证据记录：提交 SHA、文档与计划 hash、真实 LLM 路由/任务、Qwen runtime/model/workflow 指纹、prompt_id、图片 SHA-256、晋升审计、H3 attempt/manifest/产物。测试证据留本地或已授权位置，不提交私有素材、密钥、数据库或生成媒体。

程序验证与艺术验收分开：真实出图/出片不代表外观满意、人物一致或对白质量已被用户接受。最终报告逐类记录人工结论；无人评审记为待评审。

## 12. Windows 入口、安装包与最终完成标准

实际建设改变依赖、启动配置或前端产物时，按当前 `AGENTS.md` 完成开发机根目录启动入口与项目安装包两条验收链。至少核验干净重启、默认端口被占用与停止；涉及端口/进程记录时补半启动残栈。活跃用户生产期间先避免打断，记录可执行窗口。

确认快捷方式、`启动 Shine 工作台.cmd`、`scripts/start_dev.ps1`、`scripts/start_shine.ps1` 一致；需要时通过 `scripts/update_dev_shortcut.ps1` 重建快捷方式。安装卡、新依赖、下载/占用量和已知限制同步到安装包说明，不能沿用过期固定数值。

从最终已提交的树构建：

```powershell
pwsh -File scripts/build_test_package.ps1 -OutDir releases -Verify
```

校验 ZIP、SHA-256、BUILD_INFO 与逐文件清单，保留上一版。安装包不包含权重、生成媒体、用户素材、SQLite、密钥、环境、日志和 `docs/evidence`。`.cmd` 保持 ASCII；含非 ASCII 的 `.ps1` 使用 UTF-8 BOM。仓库当前未跟踪个人文件不应被顺手纳入提交或包。

最终报告必须分别列出：

- `IMPLEMENTED`：实际代码、API、迁移、UI 已完成项。
- `OFFLINE_VERIFIED`：合成/模拟/契约与回归结果。
- `REAL_QWEN_VERIFIED`：三类分别记录当前配置真实出图证据。
- `REAL_H3_VERIFIED`：新晋升素材确实进入 H3 引用并生成的证据。
- `HUMAN_REVIEW`：构图、美术与最终视频是否人工接受。
- `WINDOWS_AND_PACKAGE`：入口测试、包校验与实际交付路径。
- `BLOCKED/NOT_RUN`：阻塞、未知外部结果、未执行测试及恢复方法。

只有导演 UI 全链路、任务持久恢复、真实图片/H3 引用、人工选用门禁和要求的交付验证均有证据，才可声明本轮建设完成。若硬件、配置或人工选择未完成，交付可运行代码与精确待办，整体标记 `PARTIAL / NOT ACCEPTED`，不能以 Mock 替代。

## 13. 主要风险与应对

| 风险 | 应对 |
| --- | --- |
| 模板/模型存在但本机不兼容 | 固定版本、受管隔离、三类小样；不伪造 READY |
| 素材计划抢占生产计划 | purpose 显式过滤全部路径、迁移与旧库回归 |
| 长剧本导致 JSON/上下文失败 | 按场、浅 Schema、预算预检、单项恢复 |
| 自动生成侵蚀人工资产权限 | 候选与主资产分离、独立晋升 grant、哈希快照 |
| 重复点击/未知提交重复耗 GPU | 数据库幂等与业务唯一约束、submit intent、UNKNOWN 对账 |
| Qwen/H3 模型同驻或外部进程被停止 | 同一资源账本、受管所有权证明、释放不确定则保留租约 |
| 新素材与冻结风格冲突 | 独立视觉快照、差异展示、显式新版本 |
| 技术成功被误当作品合格 | 技术 QC、advisory 与人工质量结论分别记录 |
| 许可或组件版本漂移 | 固定 revision/hash、保留研究用途限制、安装前复核 |

本文的交付仅为建设规范与实施输入；不代表已下载、安装或运行任何新增模型，也不代表已完成新增 Qwen/H3 验收。
