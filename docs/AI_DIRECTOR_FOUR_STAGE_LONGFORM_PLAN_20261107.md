# Shine 视频工作台：AI 编导四阶段流水线与超长文本分卷交付建设方案

> 文档版本：V1.0 · 2026-11-07  
> 适用仓库：`anjingdtl/shine-video-studio`；编制时核对的 `main` 基线：`549fb3350885abfb966b77c45a80d54589949165`。  
> 状态：**经产品讨论确认的目标设计；本文为实施方案，不等于代码已实现或真实 H3 已验收。**  
> 既有依据：`AGENTS.md`、`docs/DIRECTOR_WORKFLOW_AND_LONG_FORM_PRODUCTION_PLAN_20261105.md`、`docs/LLM_CONTEXT_BUDGET_POLICY.md`、`docs/adr/0007-director-package-and-long-form-run.md`。  
> 本文件仅扩展/明确「文本导入→大纲→剧本→分镜」创作子流程与按卷交付政策；其余既有安全门禁、导演包、H3 运行、素材授权和交付合同继续有效。

## 0. 产品决定与不可变原则

1. **对用户永远是四步**：①文本导入，②故事大纲提炼，③剧本编写，④分镜编写。四步是导演模式的「AI 编导流水线」子流程，不替换现有导演旅程的六个顶层阶段，不新增第二套视频生产引擎。
2. **双入口统一**：用户可输入一句创意、粘贴文本、上传已写好的剧本/小说；最终都进入同一套原文、总纲、剧本、分镜的版本化合同。创意入口可生成「原始创作母本/创意档案」，不需要先完成整部剧本才提炼大纲；已有剧本可走「忠实整理」而非强制重写。
3. **模型能力目标档**：以用户常用模型约 `1,000,000` 上下文、约 `128,000` 最大输出为目标设计，但每次请求以当前 Profile、服务商元数据、经实测可用上限与 API 协议为准；不得无证据宣称所有模型都能真实输出 128K。
4. **单轮输出总预算上限为当前模型可用最大输出的 60%**；以十进制 128K 为例是 `76,800` Token。这里的总预算**包含模型可能计入 completion 上限的 reasoning/thinking Token、可见正文、结构化 JSON 以及其它计入 completion 的 Token**，不是「76,800 个剧本文字」。
5. **全局统一统筹、智能分卷、逐卷创作、逐卷交付，按集与按镜执行**；分卷按叙事边界和可用 Token 预算而非机械固定字数。分卷不是单独新建作品，也不是 H3 ProductionSeries。
6. **先全局确认，再局部开拍**：全局故事蓝图（主线、人物、时间线、结局方向、跨卷伏笔与锁定项）经用户确认后，已完成的卷可以逐卷进入生产准入检查；未确认后续卷不能伪装成全片已完成。凡涉及后续卷可能推翻前卷的重要悬而未决事实，必须具名阻断受影响卷的正式交付。
7. **艺术生产不等于文字生成成功**：分镜草稿可在无素材、未冻结风格时产出；只有正式分镜经审阅、人物/场景/声音主资产确认、Style Bible 冻结、H3 工作流验证及导演包/生产准入通过后，才能交给真实拍摄。
8. **原始作品永远不可被覆盖**。用户对 AI 改编的授权、原著出处、每段文本的来源范围、版本差异和确认动作必须能审计。AI 不得自动批准资产、冻结风格、越过用户确认或者重发 UNKNOWN 的远程请求。

## 1. 远端仓现状与必须修的断层（静态核对）

| 范围 | 现有代码/合同 | 已实现基础与新增需求 |
| --- | --- | --- |
| 原文导入 | `backend/shine/agents/script.py`、项目文档 API | 支持 TXT/MD/JSON/DOCX/PDF；当前文件上限为 20 MiB；需保留原文哈希、提取完整性报告和分卷范围，不能简单删上限 |
| 创意双入口 | `backend/shine/director/creative.py`、`ScriptEntryPanel.tsx` | 已有版本化 CreativeBrief、远程文本同意及 Worker 恢复；当前 `MAX_SCRIPT_CHARS=60_000` 是**单次生成限制**，不得当作整部作品长度上限 |
| 长文本与大纲 | `backend/shine/materialprep/source_spans.py`、`story.py`、`api_story.py` | 来源 span 与约 4,000 字符工作片、层级大纲合并、`StoryOutline`、确认、续租与 UNKNOWN 对账已存在；要把细粒度跨度作为**索引/恢复单位**，而非强制模型只看几千字 |
| 创作输出 | `backend/shine/llm/gateway.py`、`context_window.py`、`agent_call.py` | 可读模型 context/output 元数据；当前 `agent_output_config` 对 MiniMax M3.1 Flash 设置 16,384、其它模型默认 100,000 保守顶帽；部分素材 Agent 的上下文预估和 output reserve 仅 800~2000。需改为能力感知的**创作任务专属**预算，不得让小型结构化 Agent 被不必要地升级 |
| Provider 适配 | `openai_compatible.py`、`anthropic.py` | OpenAI-compatible 接受生成选项，Anthropic 当前 `max_tokens` 至多 4096；必须尊重各 API 的真实能力、推理预算语义、finish_reason、usage 与安全降级 |
| 剧本与分镜 | `backend/shine/api_film.py`、`agents/film.py`、`StoryPreparationPanel.tsx`、`StoryboardPanel.tsx` | 已有五工种分镜链、逐段正式分镜、用户应用机制；**缺独立的分卷/分集/分场「已确认剧本文档」中间合同**，当前从长文总体大纲直达部分分镜 |
| 分集、导演包、生产 | `director/contracts.py`、`api_director.py`、`director/journey.py`、`production/runner.py` | 已有 EpisodePlan、DirectorPackage、ProductionRun、Producer/Comfy/H3 与 FFmpeg 交付；现有篇章/分集的完整性检查主要按全片范围，需要增加「卷范围」读模型与范围确认，不降低旧全片验证 |
| Web | `frontend/src/App.tsx`、`ScriptEntryPanel`、`StoryPreparationPanel`、`StoryboardPanel` | 能力分散在「剧本与素材」「镜头拆解」面板；应聚合为**一个四步骤页面**，专业日志/JSON/任务内部角色折叠显示 |
| 真实运行证据 | `docs/PROGRESS_20261107_DIRECTOR_JOURNEY_R7.md` | 报告了真实长篇分镜与 H3 串行生产尝试，也记录部分远端 LLM `UNKNOWN` 与整片未完、人工艺术验收未完成；不能将测试通过写成真实全片通过，不能碰正在使用的 live 数据/租约 |

> 现有 `docs/DIRECTOR_WORKFLOW_AND_LONG_FORM_PRODUCTION_PLAN_20261105.md` 提倡「全片先规划确认后生产」。本方案的**受控例外**仅为：全书总纲/跨卷约束先锁定，再对已完整确认、已具备真实准入证据的特定卷独立发布导演包及 Run。不能只凭某卷文本已写好就绕过资产、风格或 H3 门禁。

## 2. 目标业务旅程：四步统一、分卷循环

`输入想法/导入文本 → ①原始档案与分卷建议 → ②全局故事蓝图 + 分卷/分集大纲 → ③逐卷逐集剧本 → ④逐卷逐场分镜草案 → 用户确认正式分镜 → 素材/导演风格/生产准入 → 卷级导演包 → 现有 ProductionRun / ProductionSeries / H3 → 逐集交付`

- **第一次进入**：只有「导入或写下创意」主操作；系统自动判断是小说、剧本、分场文本还是梗概，给出完整性、预计规模与分卷建议，不要求用户理解 token、lease、hash。
- **每个阶段**：固定主操作「AI 生成/继续」「编辑」「确认并进入下一步」，并提供「重新生成所选范围」「查看来源差异」「查看待确认问题」；支持手动模式与按事先批准规则自动推进**草稿**模式。
- **任何规模都一套 UI**：短篇为 1 卷，长篇 N 卷；界面外层始终四步，内部提供卷/集/场筛选、待处理数、已确认数、可生产数。
- **原文保留**：创意入口将原始想法与锁定事实持久化为带 provenance 的创作档案，之后再写总纲与分集剧本；非用户明确授权，不擅自补写「原著已发生」的事实。
- **用户确认**：全局蓝图、分卷边界、剧本版、分镜版分别有可追溯的确认快照。自动创作可连续推进草稿，但不能自动批准有实质剧情修改的内容或真实拍摄。

## 3. 四阶段的输入、输出与状态门禁

### 3.1 第一阶段：文本导入（SourceMaster）

输入：粘贴、TXT/MD/JSON/DOCX/PDF 上传、已有 CreativeBrief；未来其它格式须单独声明适配，不默许扫描 PDF OCR 成功。

实施：
1. 原始文件与规范化文本分开留存；记录 format、文件 SHA256、提取文本 SHA256、字符数、页/章统计及任何解析失败/空白页警告。
2. 建立稳定 `SourceSpan` 及 byte/Unicode offset 映射，完整覆盖原始**规范化文本**的字符区间；UTF-8 字节索引与 Python 字符索引不得混用。
3. 文档超出当前 20 MiB 上限时，支持**用户明确选择**的多文件母本/增量导入与合并 manifest；若开发分片式上传，必须做流式校验、合并去重与资源配额。不可直接删文件大小保护。
4. 进行故事/章节边界探测；输出初步 `VolumePlan` 候选及覆盖率、容量评估，允许调整边界、卷名与顺序。
5. 已有剧本识别为「忠实整理」，已有创意识别为「原创创作」；两者最终都有唯一 `source_master_id`。

完成：源文件已持久化，任何导入损失/乱码有清晰提示；卷范围互不重叠、无空洞、顺序正确且可回到源文件。

### 3.2 第二阶段：故事大纲提炼（StoryBible + VolumeOutline）

- 尽可能把**完整原文**连同系统指令、schema、历史事实一并纳入实际可用的模型窗口；如果完整文本不适配，则按自然章节/篇章进行局部提炼、层级合并，并额外做跨卷联合审查。
- 输出：全书总纲（主线、人物/关系、世界观、重大节点、跨卷伏笔、关键物品、时间线、结局与锁定事实）、逐卷大纲、分集规划、章节映射、不确定事项及来源引用。
- 建立 `GlobalStoryBible` / `ContinuityLedger`：事实分为「原文明确」「改编提案」「用户锁定」「待确认」，跨人物身份变化按剧情时间点快照存储，不把模型猜测提升为权威。
- 全局故事蓝图与跨卷约束由用户确认后生成锁定版本；未解决的重大剧情矛盾不得静默跳过。局部大纲更新触发影响分析。
- 大纲与分集的物理限额独立：已有 `MAX_EPISODES=200` 属于现有单计划能力；超过后按卷或受控子计划扩展，不直接放开整数校验。
- 大纲内容必须满足来源覆盖：每个可用 SourceSpan 至少落入某个已分析区间；未处理、重复、断裂可在 UI 查看。

完成：全局蓝图确认、卷范围确认、当前目标卷大纲确认、跨卷重要事实无阻塞。

### 3.3 第三阶段：剧本编写（VolumeScript / EpisodeScript / SceneScript）

- 根据已确认故事蓝图与分集计划逐卷、逐集、逐场写作；每轮给 LLM 全局相关约束、对应原文、前后剧情与上下文记忆；能放入全书的则允许全书作为理解上下文，但**不强制一次写完全部**。
- 正式新增独立的剧本中间成果：`VolumeScript`（卷）、`EpisodeScript`（集）、`SceneScript`（场），记录人物对白、表演动作、场景、时间/空间、节拍、预计时长、来源 span、承接与下一幕状态。
- 已有完整剧本可优先「原文规范化」，保留对白原句；小说改编采用「忠实/适度/自由」明确授权级别，人物关系、结局等实质性改变给出 diff。
- 按集预估短剧时长、对白可读时间、角色数量与 H3 镜头承载能力。估算不是实测；不擅自删除不能在单镜说完的对白，应拆镜或建议用户修订。
- AI 每轮写出的文本须持久保存为已校验草案；局部场次重写不覆盖历史确认版，更新相关卷的下游失效范围。
- 生成时长过长或输出被截断：明确标为 `INCOMPLETE`、保留已验证的段落，接续必须用已保存的场/段标识和输出版本，避免重复对白和漏场次。

完成：目标卷所有要求的集、场均有已确认的独立剧本版本；剧本文本覆盖该卷大纲要求的叙事事件，未解决差异明确列出。

### 3.4 第四阶段：分镜编写（StoryboardDraft / ApprovedStoryboard）

- 以已确认 `SceneScript` 为唯一权威叙事输入；复用现有 `scriptwriter → director → cinematographer → continuity_supervisor → editor` 的专业角色合同。不得创建另一套 H3 自由提示词输出路径。
- 输出逐镜结构化 `ShotSpec` 草案：景别/构图、机位/运镜、灯光、人物动作、对白/音色、时长、镜头内切换、声音、剪辑意图、来源场次和素材需求。
- **草案**不要求资产已晋升或风格冻结；用户可在四阶段内完成整体拆镜。由项目已选择的导演风格模板建议镜头语言，但正式拍摄仍必须读取已冻结 Style Bible。
- **正式应用**沿用 `apply-camera` / ShotSpec 校验与现有 H3 规则编译；只有角色/主素材、场记冲突裁定、工作流参考槽位、冻结风格与授权全都就绪，才可发布「正式生产分镜」。
- 用户修改剧本场次时，精准失效该场和有依赖的下游镜头；历史 PASS 产物不可覆盖。镜头不能越卷引用未知的前镜资源；跨卷硬连续性若现有跨 series 工作流无法支持，要具名阻塞或经用户批准改成 CUT，不能静默削弱为软连续性。

完成：目标卷所有确认剧本场次均有结构化分镜；**「草稿完成」**与**「卷生产就绪」**是两个不同的完成态。

## 4. 1M 上下文与 128K 输出的正确预算规则（P0）

### 4.1 输出封顶公式

首先计算本次 Agent/Provider 的**已验证有效总输出上限**：
`effective_output_max = min(provider_reported_max, probed_or_documented_cap_if_any, configured_user_cap_if_any, adapter_cap_if_any)`。

对已确认支持约 128,000 输出的创作模型：  
`per_call_completion_cap = floor(effective_output_max × 0.60)`；若 `effective_output_max = 128000`，则 = `76800`；若模型声明 131072，则 = `78643`。

- 60% 是**单次请求 completion 上限**，推理/思维过程（在服务商把它计入 completion 的情况下）、可见正文、格式与协议开销都必须在**同一**总额度内；不能把 60% 当作 `visible_text_token_budget`。
- 模型可用输出上限未知、未通过参数验证或小于用户假设时，保留较低安全值并显式显示「服务商未证实 128K」；**不得直接发 76,800**。不同 Profile、角色、调用任务应分别决策。
- 默认适用于新的「长篇创作任务」类别；旧的微型素材提取、场记、质检、简短结构化任务保留更小的合理预算，避免高成本、长等待及服务商速率瓶颈。
- 仅当模型适配器真正支持时设置 `max_completion_tokens`/`max_tokens`/thinking 相关选项，不能向不兼容服务商盲目发统一参数。给 Provider 配置、模型能力缓存、测试与管理 UI 增加相应验证。

### 4.2 输入窗口与思维链估算

每轮请求至少预算：`系统提示词 + 用户目标 + 全局约束 + 相关原文 + JSON schema + 工具/上下文开销 + 预期 completion + 安全 margin`。

- 如果模型窗口是**共享**上下文，completion 预算必须从可用总窗口中预留；如果输入窗口与输出独立，按服务商真实定义分别校验。不能在不清楚协议时乐观计算可塞入的原文。
- 优先用服务商模型元数据与官方能力声明；发送前用可用 tokenizer 算精确 token，若无则保守估算且明确「estimated」；响应中原始 usage 才是实测。不可把 UTF-8 字节数当作真实 token。
- reasoning/thinking Token 的可观测性因 Provider 不同：如 usage 返回 `reasoning_tokens`、`completion_tokens_details`，记录数字但**不存储/暴露私有思维链文本**；未返回时标记 `unavailable`，从总 completion 预算扣除预留区间，动态缩小可见正文目标。
- 输出可见正文目标应自适应：参考过去若干次同 Profile/任务类型的推理比例、JSON 开销、场景长度与 rate-limit；可设置 50–70% 的**内部软目标**但必须受 60% 硬上限约束，不能把猜测当成服务商能力。
- 当前 `agent_output_config` 的 MiniMax 16,384 默认顶帽，以及 Anthropic 4096，必须有**明确的兼容性审计/逐 Profile 验证**之后才允许调高；不能笼统把旧逻辑全部删掉。`agent_call.budget_report` 的 800–2000 输出预留也不能继续代表长篇剧本写作。
- 响应 `finish_reason` 为长度截断、内容不完整、schema 不通过、覆盖不完整、引用失效，均不算完成。若 provider 返回部分可验证场次，可保存为独立草案快照，后续新任务按新的边界继续；不拼接未经校验的半截 JSON。

### 4.3 任务预算记录（审计但不泄漏秘密）

单轮任务最少保留：profile/model/provider identity，能力来源与版本，输入 token 估算、实际 prompt usage、配置 completion cap、实际 completion usage、可观察 reasoning tokens、可见输出 token（有来源才计数）、finish reason、输出内容 hash、来源范围、错误码、分轮编号/段落编号、request identity、外部结果状态。不得保存密钥或私有 chain-of-thought。

## 5. 智能分卷：不仅按字数，更按叙事完整性

### 5.1 分卷计划生成

1. 优先识别章、卷、幕、事件链、人物线与自然高潮/悬念边界，生成用户可编辑的 `VolumePlan`。
2. 同时估算每卷「全局大纲调用 + 剧本生成调用 + 分镜调用」Token 和成本；预计单轮内容不能在硬预算内完成则缩小创作**工作单元**，而不强拆故事原始章段。
3. 卷区间必须连续、有序、无重叠且完整覆盖本次选择的原文范围；人为删节只通过显式 `excluded_ranges` + 用户确认记录。
4. 同一卷可有多集、每集可有多场，每场多镜；卷与 H3 batch 解耦。支持短篇 = 1 卷。
5. 原文超过单个模型窗口，先分篇章提炼事实再树状合并，全局审查使用凝练索引 + 可检索原文片段，不作「整部原文已一次完整进入模型」的虚假声明。

### 5.2 连续性与跨卷状态

- 所有卷共享**一个** GlobalStoryBible，按剧情位置生成权威快照（人物生死、伤势、服装、道具持有者、时空位置、未揭示信息）。
- 前卷推进到「准备交付 H3」时，固定 `global_bible_version`、`volume_revision`、`episode_script_revisions`、`storyboard_revisions`、`asset/style/rule hashes` 与跨卷依赖摘要。
- 后卷改稿会执行影响分析；若改变已拍前卷的锁定事实，不能覆盖前卷已通过镜头，必须产生显式冲突报告和需要用户决策的新版本。
- 不能让「下卷还在写」成为前卷默认阻塞；但未解决的重大结局/人物设定冲突确实影响前卷时，应按引用关系准确阻塞。

## 6. 数据模型与 API（建议合同，实施时可调命名）

沿用现有 SQLite、Alembic、`ProjectDocument`、`AgentArtifact`、`AgentTaskRecord`、`DirectorEpisodePlan`、`DirectorPackage`、`ProductionRun`、`Shot`、`Asset`；在正式编码前先盘点是否能扩展既有对象，避免一套功能两张权威表。

建议扩展/新增对象：

| 对象 | 最少字段/约束 |
| --- | --- |
| `SourceMaster` | project_id、source document ids、原始/规范化 SHA、索引版本、导入警告、locked revisions |
| `VolumePlan` / `Volume` | source_master_id、order、start/end、source hash、narrative boundary、status、approved revision、coverage report |
| `GlobalStoryBible` / `ContinuityLedger` | 角色/世界观/时间线/伏笔/用户锁定、provenance、剧情时间点、version/hash、decision state |
| `VolumeOutline` | volume_id、global_bible_rev、episode boundaries、source refs、unknown questions、approved revision |
| `VolumeScript` / `EpisodeScript` / `SceneScript` | parent id、stable scene key、text、角色、对白、动作、预估时长、source refs、version/hash、approval |
| `StoryboardDraft` | scene script id/rev、ShotSpec 草案、风格建议、需求、审批/应用 receipt、失效关系 |
| `CreativeBatch` / `CreativeCallCheckpoint` | stage、volume/episode/scene 范围、call sequence、input/output hashes、provider request identity、external state、token budget/usage、resume cursor |
| `VolumeHandoff` | approved volume revision、coverage digest、bible/style/asset/rule/workflow IDs、EpisodePlan scope、package/run IDs、readiness、交付/返工状态 |

- 所有父子关系要有外键/版本或等价唯一约束；重要事实不能只靠 JSON 名称字符串串联。分页查询，避免单个 API 返回百万字全文或数千镜头。
- 原文/剧本/分镜皆不做「原地覆盖」，新的修改创建新版本，历史保留以审计与回退；按照影响图精确置 stale。
- 范围 hash 应绑定 project_id、source_master revision、global_bible revision、volume ids/revisions、各分集/场次/ShotSpec revisions、所需资产/规则/风格等。仅上传一份文档、存在一镜头或单集成功，均不能标记整卷/全片完成。
- 建议路由命名：`/projects/{project_id}/creative-pipeline`（总览）、`.../sources`、`.../volumes`、`.../story-bibles`、`.../scripts`、`.../storyboards`、`.../handoffs`。不得擅自破坏现有 `/story-preparation`、`/episode-plans`、`/journey`、`/storyboard/generate` 调用合同；旧接口可以封装/代理到同一权威服务。
- 支持状态：`DRAFT / QUEUED / RUNNING / NEEDS_REVIEW / INCOMPLETE / CONFIRMED / STALE / HANDED_OFF / BLOCKED`（状态取值需根据不同对象的合法生命周期拆分，非一张表全部生搬）。
- 独立确认与权限：同现有本地用户动作、版本 hash、一次性授权/snapshot、远程文本传输授权一致；外部 LLM 同意要与 provider fingerprint 绑定，不允许 UI checkbox 自我授予后台权限。

## 7. 创作编排、续写与故障恢复

- **一个常驻 Worker 与原有任务编排体系**；增加创作任务类型，不另起孤立后台守护进程。任务图从总纲→分卷→分集→分场→分镜自底向上依赖；每个单元带稳定 ID、输入版本、预期输出 schema、token 预算与状态。
- 调用前落库 request identity 与 `SENDING` 意图，调用结果先持久化产物再推进 cursor，满足 lease/state_version/owner fencing。借鉴现有 `director/creative.py` 的 `DRAFT→SENDING→RESPONSE→DOCUMENT` 阶段机制。
- **结果 UNKNOWN/超时**：禁止自动重发。先查服务商可提供的权威结果证据；查不到时停留 NEEDS_REVIEW，显示明确操作。「用户明确放弃后建立新的独立请求」必须保留前次 UNKNOWN 事实和代价警告，不能声称前次未发送。
- 每个卷/集/场支持暂停、恢复、局部修订、重新生成、跳过**被授权的非必需**条目。Worker 重启后从数据库检查点恢复，已完成有效产物不会重新付费生成。
- 任务公平性：创作任务与 H3 生产、交付共用已有调度，避免长篇 LLM 请求或扫描式「检查」饿死 series/交付。不能在数据库写锁内做网络推理，也不应为追求并发绕开当前 GPU/资源调度语义。
- 自动草稿模式需要用户提前确认目标卷集合/改编强度/不可变事实；推理/格式/语义错误触发有限修订预算，超限停止并等待用户决定，不隐式改故事核心事实。

## 8. 单卷正式交付到现有 H3 生产线

将**「卷范围」**作为新的被批准范围，映射成现有 EpisodePlan / DirectorPackage / ProductionRun；一个 Volume 可有多集、多个 Series，每集由本卷适用的场次和 ShotSpec 严格覆盖。

卷发布 Gate：
1. `SourceMaster/VolumePlan` 原文范围覆盖完整、来源未变。
2. `GlobalStoryBible` 与相关卷大纲已确认，跨卷重要矛盾无未决。
3. 当前卷所需 EpisodeScript/SceneScript 及分镜均有有效确认版本，所有要求的场与镜覆盖完整，无重复或遗漏。
4. 必需 Asset master 已人工晋升/锁定；Style Bible 已人工冻结；H3 编译器规则包、workflow profile/parameter_map、参考预算/转场依赖及现实 Comfy 可用性已按原有标准通过。
5. DirectorPackage 的 scope digest 与当前批准的卷、分集/镜头、资产、风格及规则一致，真实生产意图明确为 real H3 或显式 Mock。
6. 缺项只阻塞依赖该缺项的卷/批次；**但禁止**把部分卷交付写成「全片完成」，UI 分别展示「已交付卷 x/y」「已完成集 x/y」「待审核」。
7. 上游剧本或分镜变更时，仅未提交的相应下游任务可重新生成/失效；已 PASSED 的实拍成果必须留档，必要重拍通过新版本/新批次，不在历史上涂改。
8. 不改变既有 H3 唯一视频模型约束、Comfy 独占租约、真实媒体续接限制、五门 QC、审片真实性和分集 FFmpeg 装配判定。

## 9. UI 与用户体验

在导演模式「剧本与分镜」建立独立聚合面板，保持四步横向进度条与当前一步单一主操作：

| 页面/步骤 | 主视图 | 用户主要动作 |
| --- | --- | --- |
| ① 文本导入 | 文件/创意输入、文档完整性、AI 建议分卷列表 | 上传/粘贴、调整卷界、确认母本 |
| ② 故事大纲 | 全书总纲、人物/时间线、分卷分集目录、来源对照 | 编辑、确认总纲与当前卷 |
| ③ 剧本编写 | 左侧卷/集/场目录、中央可编辑剧本、右侧改动与伏笔提示 | 按卷/集生成、局部修订、确认剧本 |
| ④ 分镜编写 | 按集/场展开镜头卡片、景别/时长/对白/镜头运动、草稿/正式标识 | 生成分镜、改镜头、确认草案/交付拍摄 |

- 保留「分步确认」和「按预设规则自动生成草稿」两种方式，运行期展示如「第二卷·第 3 集·剧本草稿 8/12 场」及「准备生产/待修改」；详细 token、schema、task id、租约、provider usage 只放高级/诊断面板。
- 不给前端造第二套完成条件：阶段状态与卷状态只读取服务端权威 ledgers；切换页面/浏览器刷新不丢任务进度。
- 在「当前卷」提供「交付 H3 制作」按钮，仅在服务端 Gate PASS 时可用；否则显示普通用户能理解的缺项和直达修复操作。
- 原已有素材中心和导演风格库若尚未独立落地，采用**可选链接/适配层**衔接，不能把其未来开发视为本次 P0 的硬依赖。

## 10. 实施顺序、每段交付与可回滚性

### P0-A：基线审计与预算基础（先完成）

- 读 `AGENTS.md`、旧导演方案、当前接口/迁移/测试；提交冲突清单与兼容策略。
- 设计创作 Agent 的模型能力/总输出上限计算、Provider 适配、token usage 与 thinking 统计字段，保留低能力回退、旧短回复路径。
- 编写预算测试：128000×60%=76800，131072×60%=78643、未知上限、已配置更小 cap、Anthropic/OpenAI-compatible 差异、reasoning usage 有/无、finish_reason 截断、共享窗口预留。

### P0-B：母本、分卷、总纲

- 落地迁移/数据合同/API、导入覆盖检查、VolumePlan、GlobalStoryBible 与版本确认。
- 测试短篇 1 卷、长篇 N 卷、跨文件边界、章节/页混合、原文空洞/重复、改编事实来源、合并大纲及回滚。

### P0-C：分集剧本与可恢复创作调用

- 新增 Volume/Episode/SceneScript 实际版本化产物、确认与局部改稿；逐轮 checkpoint + UNKNOWN 守卫。
- 将旧创意入口与长篇大纲入口统一到同一合同（数据迁移需要兼容已有项目，不能重写其源文档/旧成果）。
- 长输出按语义切片与结构化 schema 工作，续写不得拼接不完整 JSON；完成覆盖检查与全局故事校验。

### P1-A：分镜草案、正式应用与卷级交付

- 复用五工种团队，分镜草案与正式 ShotSpec 区分；资产、风格、H3 规则在真实生产前判定。
- 添加 VolumeHandoff 与卷级 package/run scope，保持原全片 Runner 兼容；按卷交付后可以继续编写下卷。
- 编写「改动第三卷，不触发第一卷已 PASS 任务重发」与「跨卷依赖变更只使相关范围 stale」回归。

### P1-B：小白 UI 与真用户流程

- 合并入口为一个四步页，卷/集/场树与逐项进度；整合原 ScriptEntry、StoryPreparation、Storyboard 的真实 API，删除重复主操作但保留专家模式和历史项目访问。
- 用真实浏览器流程验证：导入→提炼→确认→剧本→分镜草稿→补素材/风格→批准卷→生产准备；不是只断言按钮存在。

### P2：全剧审校、性能优化、可观测性

- 每卷/全书一致性审查、动态推理预算学习、token/cost 报告、批量分页、可复用上下文缓存（仅在服务商支持且符合隐私授权时）。
- 用真实可重复样本核验不同模型行为；未经验证不宣称 1M 一次处理成功或真实 H3 一次跑完。

每阶段至少生成：迁移+测试、API 合同说明、UI 交互说明、变更与风险报告；尽量小提交、可重入迁移、feature branch/PR，不覆盖用户数据。不得为了赶进度篡改测试或虚构 PASS。

## 11. 对抗性验收矩阵（P0/P1 必测）

1. **预算安全**：模型最大输出 128000 且已验证 → creative call completion cap ≤76800；更小服务商上限/更低用户设置优先；thinking token 计入总额；usage 缺失时不得报「实测 reasoning=0」；输出提早截断时绝不落「已完成」。
2. **来源覆盖**：短篇/长篇、文档末尾、标题/空行、中文/emoji、跨文件拼接、重复标题及超大单段都能复核跨度和 hash；明确跳过的源文必须有审批。
3. **分卷质量**：分卷叙事边界有依据；人工修改边界后关联 outline/script/shot 精准 stale；卷范围完整不交叉。
4. **真实剧本合同**：全局总纲→分集大纲→分场剧本→分镜，每个环节可独立浏览、编辑、确认与回退；不是用旧大纲冒充已确认分集剧本。
5. **续写/恢复**：关闭页面、Worker 重启、发送前崩溃、响应落库后崩溃、超时 UNKNOWN、旧 owner 迟到写回、重复点击、重试耗尽，均不丢有效内容、不双重扣费提交、不误判已完成。
6. **局部失效**：改某集对白不重写无关卷；已 PASSED 的 H3 任务、历史原始媒体和审计记录不被覆盖。
7. **生产准入**：当前卷文本全齐但缺人物主资产/未冻结风格/参考槽未映射时只显示「创作草案完成」，不允许真实开拍；修复后可发布该卷，后续卷不被误标完成。
8. **回归兼容**：旧项目、旧 StoryOutline、原 EpisodePlan/Package/Run、手工分镜与 Mock 路径不退化；生产资源租约、Review、交付与 Worker 不因大量 LLM 任务被饿死。
9. **UI**：新用户不必理解 Agent 角色名、token、JSON、lease；定位到四步+单一主操作；刷新恢复显示服务端进度，明确把网络错误/未知结果与成功分开。
10. **真实性分级**：OFFLINE/MOCK、真实文本 LLM、真实 H3、全片/分卷交付、人工艺术验收分别给证据，绝不互相代替。

验收命令至少覆盖：`backend/tests` 全量、Ruff、Alembic fresh+upgrade+check、`frontend` 的 Vitest + TypeScript/Vite build；涉及 API/Worker/影视团队/分镜/H3 时增加现有针对性回归。重验 `docs/PROGRESS_20261107_DIRECTOR_JOURNEY_R7.md` 所述 UNKNOWN/长篇恢复问题，避免用更改请求身份绕过原阻断。

## 12. 完成定义与排除范围

### 完成定义（软件）

用户从一个入口创建作品，完成四阶段的实际生成、编辑、确认；短篇 1 卷和长篇多卷都覆盖完整原文且支持分轮续写；已确认卷能通过独立生产准入并交给原 H3 工作流；各阶段任务可恢复、失败可定位、历史可追溯；旧项目不丢数据，相关自动测试 PASS。

### 不在本次强制范围

- 不建设第二个 ComfyUI/H3 引擎、不新增云端视频模型、不自动批准主资产。
- 不为证明功能而强制下载大模型或执行真实 8h/24h H3 任务；需要明确本机授权、资源条件和真实验证样本。
- 不将「任意大文本」解释为无限磁盘、无限网络配额或单次调用无限 token。
- 不擅自改动当前 live 接受真实 H3 任务的本地数据目录、运行进程、被钉审片源码身份或外部 UNKNOWN 任务台账。
- 不预先宣称素材中心/导演风格库尚未落地的功能已经实现；本轮只设计可复用接口。

## 13. 交付清单

1. 具体代码与 Alembic 迁移、后端/API/Worker 测试、前端四阶段 UI、对应单元与端到端用例。
2. `docs/AI_DIRECTOR_FOUR_STAGE_IMPLEMENTATION_REPORT.md`：完成项/差异、变更文件、提交、测试精确命令与退出码、模型与硬件证据等级、阻塞与剩余事项。
3. 新的 API/数据合同文档、Token 预算公式与模型 Profile 行为说明；用户操作文档。
4. 至少一个短篇完整创作样例与一个多卷长篇带中断/恢复的离线验收样例；若执行真实 LLM/H3，另附独立真实证据。
5. Git 历史完整、可回滚，保持单一权威数据库、任务编排器、H3 提示词编译器以及既有安全门禁。

**总原则：**大上下文用于完整理解，分轮写作用于可控产出，分卷交付用于持续生产；四步用户体验不因后台规模变得复杂。