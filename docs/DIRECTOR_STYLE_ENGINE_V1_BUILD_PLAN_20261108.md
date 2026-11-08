# Shine视频工作台｜导演风格引擎 V1 建设方案

> 文档状态：APPROVED_FOR_IMPLEMENTATION_PLAN（待开发）；日期：2026-11-08  
> 仓库：`anjingdtl/shine-video-studio`；核对基线：`main@1f4ce34cbd68bdaf5e12841bfa92f647363e9eca`（2026-11-08）。云端 Codex 开发前必须重新拉取最新 `origin/main`，以最新代码和 `AGENTS.md` 为权威；本文中的路径是接入点，不是对将来代码行号的承诺。  
> 文档落点：`docs/DIRECTOR_STYLE_ENGINE_V1_BUILD_PLAN_20261108.md`  
> 研发策略：**本文仅提交方案至 main；实现由云端 Codex 从最新 main 新建独立 feature 分支，提交 PR，不直接把实现提交 main。**  
> 主题：`Director Style Engine`（DSE）；定位：**一套官方 H3 编译标准 + 一套版本化风格模板协议 + 无限扩展的导演风格库**。

## 0. 本轮用户决策（最高级产品约束）

1. **先做方法论，后做导演数量**：首先以当前仓库锁定的 MiniMax-H3 `h3-prompt-writing` 官方规则定义唯一的结构化编译与示例；然后制定统一的导演风格模板协议；最后任意添加模板，增加导演原则上仅增加数据文件，不复制业务代码。
2. 摄影构图、机位/镜头运动、光线/影调、色彩/镜头质感、剪辑与声音都是同一模板的不同维度。模板只表达**可组合的电影语言规则**，不产生供 H3 直接提交的自由文本。
3. 既有 `StyleBible` 七维人工冻结、`H3PromptCompiler` 唯一编译出口、官方 RulePack 锁定、镜头/素材版本、Agent 权限与生产任务身份全部保留。
4. 普通用户在“导演模式”里只需“选导演风格 → 预览效果 → 可选调整 → 确认冻结”；专业级字段默认折叠。用户确认不可由 Agent 代替。
5. **不以 4 个导演作为架构上限**。首批四套代表性模板仅承担契约与可观察差异测试，未来按同一协议扩展目录、导入用户模板或自定义模板。
6. 不生成/下载新的图片、人物、场景、音色或 H3 模型，不修改 ComfyUI 节点/模型选择；不改变正在运行的任务、租约及历史产物。

## 1. 远端仓实情与硬边界

### 1.1 已有实现（2026-11-08 核查）

| 模块 | 当前代码事实 | 本方案使用方式 |
| --- | --- | --- |
| 风格预设 | `frontend/src/styleCatalog.ts`：6 套整套预设，7 维字符串，前端 FNV 目录摘要 | 保留全部现有预设；新增**服务端权威**导演风格目录，旧目录兼容保留，不把模板 ID 冒充提示词 |
| 风格编辑与冻结 | `frontend/src/components/StylePanel.tsx`；`backend/shine/schemas.py:StyleBible` 七个必填字段（每项 ≤2000 字）；`StyleVersion` 冻结及哈希 | 复用草稿与人工冻结动作，附加不可变“模板来源快照”；风格覆盖先预览再草稿 |
| 摄影/导演/剪辑 Agent | `backend/shine/agents/film_contracts.py`：`DirectorialPlan`、`CameraPlan`（composition/movement/lighting/axis）、`EditIntentPlan`；`backend/shine/agents/crew_prompts.py` | 给角色输入**经过服务端校验并冻结的结构化风格约束**；镜头时间/对白/素材仍由原契约管理 |
| 分镜应用 | `backend/shine/api_film.py:apply_camera` 把导演与摄影方案装配为 `ShotSpec` | 不复制装配逻辑；保持 `intent_id`、拍摄段落、镜头轴线、内部 cut 时间合同 |
| H3 标准输出 | `backend/shine/h3/compiler.py:H3PromptCompiler`；`backend/shine/h3/rulebook.py` 的内置 `shine-h3-rules/1.1.0` | **唯一提示词生成器**；优先从结构化 `ShotSpec` 与已冻结风格编译 Ref2VA |
| 版本依赖与导演包 | `backend/shine/director/package.py` 绑定 StyleVersion ID/sha、Shot revision、Workflow/RulePack 等 | 新增模板来源身份的锁定/哈希一致性；任何模板更新不得静默更改已确认包 |
| 导演模式七阶段 | `frontend/src/App.tsx` 与 `backend/shine/director/journey.py`：script → plan → storyboard → assets → package → production → post | 导演风格入口嵌入 `plan` 的风格确认部分，**不增加第八个主流程阶段** |
| 素材策略 R09 | `docs/FOUR_STAGE_REACCEPTANCE_REPORT_20261108.md` 第9节：缺图是 notice，未批准素材绝不绑定；已绑参考图绝不可丢 | 风格模板不能重新引入缺图强制门禁，亦不得规避 Ref2VA 的真实工作流/参考条件 |
| 云端与本机验收边界 | `AGENTS.md`、现有 tests 与报告 | 云端仅声明离线/模拟验证；真实 H3 视觉质量、GPU 推理必须另行本地验证 |

**注意实际约束**：现有 `ShotSpec.prompt_style` 是七维**英文转写**，不能把中文模板文本直接拼进 H3 提示词；H3 的说话语言由项目设置和对白合同决定；无对白镜头有禁声规则。`CameraPlan` 的剪辑行为不等价于真实后期 EDL。参考图片可缺失的“允许继续分镜/冻结”与“实际 Ref2VA 可投递且满足参考条件”是两个不同断言，不可混淆。

### 1.2 必须先阅读

`AGENTS.md`、`docs/architecture.md`、`docs/UI_SIMPLIFICATION_PLAN_20261102.md`、`docs/FOUR_STAGE_REACCEPTANCE_REPORT_20261108.md`、`backend/shine/h3/{compiler.py,rulebook.py}`、`backend/shine/agents/{film_contracts.py,crew_prompts.py,film.py}`、`backend/shine/api_film.py`、`backend/shine/director/{journey.py,package.py}`、`frontend/src/{styleCatalog.ts,components/StylePanel.tsx,App.tsx}`，以及相关 Alembic 迁移和现有测试。发现本文与**较新的用户裁定/主干代码合同**冲突时先保持最新合同并在 PR 中列出差异，不擅自回退。

## 2. 目标、范围、非目标

### 2.1 交付目标

- **官方格式适配层**：为 Ref2VA 建立可回归的标准提示词映射与 golden fixtures，现有 `H3PromptCompiler` 保持唯一出口。
- **导演模板协议 DSE/1**：JSON Schema/Pydantic 严格校验、稳定版本、确定性哈希、镜头技法规则（含适用条件/禁止项/优先级/能力要求）、中英分离。
- **导演风格仓库**：从仓库内置配置文件加载，按版本自动发现/列举/预览；增加合法模板不需要改核心编译器、Agent 注册或前端选择列表。
- **服务端风格解析器**：把“剧本需求 + 选定模板 + 用户覆盖 + 当前风格冻结 + 能力/剪辑约束”解析成可审计的风格选择与结构化摄影/剪辑意图。
- **安全版本冻结**：模板 ID、版本、内容哈希、强度、用户覆盖和解析快照必须与七维 StyleVersion 及导演包绑定，不影响历史已冻结版本。
- **简洁 UI**：现有“作品风格”支持“通用预设 / 导演风格 / 自定义”，导演卡片选择、说明、预览、风格强度可选、差异预览、人工冻结。
- **可验收**：新增一套导演模板可经 schema → list → preview → freeze → 分镜 Agent → CameraPlan → H3 编译 → 导演包一致性 → Mock/Fake E2E 全链路验证。

### 2.2 非目标与红线

- 不创建第二个 Prompt Compiler、第二套 StyleVersion、第二套 Agent Runtime 或平行视频生产管线。
- 不要求模型“照搬某导演作品画面”，不复制特定电影镜头/台词/配乐；只借鉴一般性、可描述的电影拍摄方法。
- 不把导演署名、作品名、`in the style of X` 之类的标签作为“可执行风格”的替代。
- 不宣称精确慢动作、时空交叉剪辑、J/L cut 必定由 H3 单镜推理完成；这类效果交剪辑 EDL/受控后期实现，并且需要实际能力检查。
- 不新增审批绕过、云端秘密传输、无授权外部素材下载、作业重试或自动 H3 提交。
- 不因风格模板而更改既有“缺图片只提示”、已确认素材安全、中文对白逐字、片段时长 4–15 秒、Ref2VA 参考槽数等合同。
- 不改变已有生产资源串行调度、外部 `UNKNOWN` 不得自动重发及原有 Worker/ComfyUI 所有权保护。

## 3. 模块架构：标准骨架 / 模板 / 解析 / 编译

`剧本事实、台词、镜头任务`  
`+ 项目 StyleBible 草稿/冻结版本 + 导演风格选择（模板 ID@version#sha256 + strength + overrides）`  
`+ 当前已批准/锁定素材及已注册 H3 工作流能力`  
→ `DirectorStyleCatalog`（验证模板、版本、来源）  
→ `DirectorStyleResolver`（受限匹配：场景、情绪、镜头条件、冲突与优先级）  
→ `StyleBible 七维草稿建议 + camera/edit 结构化风格约束`  
→ **用户预览、确认、冻结**  
→ 原 `FilmAgentDispatcher`（scriptwriter/director/cinematographer/editor/continuity）  
→ 原 `CameraPlan / ShotSpec / EditIntentPlan`  
→ **唯一 `H3PromptCompiler`**（Ref2VA 六段格式）+ 现有 RulePack validator  
→ 原 ComfyUI / QC / EDL / FFmpeg / 导演包/ProductionRun。

**权限与事实优先级**（从高到低）：官方 H3 规则及真实工作流能力、锁定剧情/对白/角色身份与素材和版本安全、用户冻结的全片 StyleBible 与显式覆盖、当前场景实际约束、导演模板中的候选技法。发生冲突必须拒绝不可执行技法、给出可见 warning 或由人确认新 StyleVersion；不得悄悄覆盖上层约束。

## 4. 官方 H3 标准模板：必须先落地

当前 `H3PromptCompiler` 的 Ref2VA 六段 `REF_SECTION_ORDER` 为：

1. `subject_definitions`：当前请求真实存在的角色/参考图标签及身份描述；
2. `summary`：生成任务摘要、已冻结剧情与参考关系；
3. `retention_analysis`：参考素材保真及身份锁定；
4. `detailed_description`：`[Shot 1]` 内的可观察摄影行为、灯光、构图、人物走位、动作，以及受真实时序限制的内部 cut；
5. `overall_soundscape`：项目语言/禁声合同、对白与环境声；
6. `non_diegetic_music`：配乐指导，不提供未经授权的已知音乐旋律/录音。

**映射规则**：

| DSE 语义 | H3/后期落点 | 合同 |
| --- | --- | --- |
| 构图、视线、位置、景别、镜头运动及速度幅度 | `ShotSpec.camera` + `detailed_description` | 可拍、英文、与 CameraPlan 对齐；不靠空泛导演名 |
| 布光、反差、画面色调、焦段语义、质感 | 冻结 StyleBible 七维 + `camera.lighting` + `detailed_description` | 不改变锁定时间、场景天气、参考身份 |
| 镜头内可表述的变焦/推拉/连续运动 | `detailed_description` | 仅声明工作流已验证的能力；不把后期变速虚报为原生能力 |
| 场景间 CUT、叙事节奏、平行剪辑、精确慢动作/变速 | `EditIntentPlan`/EDL/FFmpeg | 不是在单镜 H3 提示词里强行实现 |
| 环境声、对白、无声要求 | `overall_soundscape` + 现有对白 schema | 原文普通话/项目语言、准确时间轴、无对白禁声门禁 |
| 音乐情绪 | `non_diegetic_music` | 只描述音乐情绪/动态，不模仿具体受版权保护的旋律 |
| 主体/参考一致性 | `subject_definitions` / `retention_analysis` | 只能引用本镜有且经许可的素材；禁止凭风格模板发明标签 |

**硬性编译纪律**：

- 引擎输出的风格文本为英文 H3 执行语句；中文只用于 UI 和用户草稿。多语言翻译必须有类型/来源标记，不能把中文静默传给 `H3PromptCompiler`。
- 模板不可直接写 `[Shot N]`、`<Picture N>`、`<Subject N>`、六段标题、对白引用、时长/切点；这些由受控编译器与请求参考清单决定。
- 风格解析器不能发明角色/场景/道具 `asset_id`、`speaker_id`、参考槽、帧/时间点或视频模型能力。
- “同一段内多切镜”必须复用已验证的 `EditorialCut` 时间计划；无合法剪辑表时遵守当前无 `same-beat cuts` 的服务端门禁。
- 每一条标准示例必须以**完整结构化输入+唯一编译输出+RulePack 校验结果**成对落入 `tests/fixtures`；禁止仅保存一条拼接好的提示词当作标准。
- RulePack 作为官方规范的锁定快照，DSE 模板不拥有修改 RulePack 的权限；未来官方规则变更必须走现有官方规则升级流程。

## 5. 统一导演模板协议 DSE/1

**推荐内置文件格式：UTF-8 JSON**（Python `json` 原生解析、无需新增 YAML 安装依赖），固定目录建议 `config/director_styles/builtin/*.json`，契约模块建议 `backend/shine/director_styles/{contracts.py,catalog.py,resolver.py,service.py}`。文件名仅用于发现，模板真实身份来自 `id`；目录结构和 API 路由须与现有布局审查后定稿。

### 5.1 规范对象（示例：结构示意，不是直接提交 H3 的 prompt）

```json
{
  "schema_version": "director-style/1",
  "id": "wong-kar-wai",
  "name_zh": "王家卫参考风格",
  "version": "1.0.0",
  "summary_zh": "城市情绪、遮挡构图、实景混合色光与情绪留白",
  "categories": ["urban", "emotion", "drama"],
  "style_bible": {
    "photography_language": "以克制的缓慢横移与局部近景表达情绪。",
    "shot_blocking": "优先使用前景遮挡与分层构图，保持人物方位一致。",
    "color_palette": "以暖琥珀、暗绿与有限红色点缀表达城市夜景。",
    "lighting_tone": "采用有来源的暖色实景灯和克制的彩色环境光。",
    "lens_texture": "适度长焦压缩与柔和景深，保留真实皮肤纹理。",
    "editing_rhythm": "允许情绪停顿，切点优先由故事动作和台词决定。",
    "sound_performance": "遵循项目原定语言，突出克制的城市环境声音。"
  },
  "techniques": [
    {
      "id": "foreground-layering",
      "target": "camera",
      "priority": 50,
      "when": {"scene_tags_any": ["urban", "interior", "night"]},
      "instruction_en": "Frame the protagonist through a foreground doorway while maintaining clear screen direction.",
      "requires_capabilities": [],
      "conflicts_with": ["unobstructed-product-closeup"]
    },
    {
      "id": "practical-warm-key",
      "target": "lighting",
      "priority": 40,
      "when": {"scene_tags_any": ["night", "interior"]},
      "instruction_en": "Use a motivated warm practical key light with controlled colored ambient separation.",
      "requires_capabilities": [],
      "conflicts_with": ["neutral-daylight-only"]
    },
    {
      "id": "emotional-pause",
      "target": "editing",
      "priority": 30,
      "when": {"scene_tags_any": ["emotion", "dialogue"]},
      "instruction_en": "Preserve a deliberate reaction pause without truncating spoken dialogue.",
      "requires_capabilities": [],
      "conflicts_with": ["strict-no-pause"]
    }
  ],
  "source_notes": ["General cinematography traits; not a reproduction of a named film shot."]
}
```

**协议硬规则**：

- `schema_version` 为 `director-style/1`；`id` 稳定、长度受限、仅英文小写/数字/连接符；`version` 遵循版本号规则；内置/用户自定义目录隔离。
- 全部七维 `style_bible` 必填，执行原有 `StyleBible` 字数/非空校验；不能把新的专业字段当成第八项必填风格维度。
- `techniques[].target` 使用受限枚举（`camera`、`lighting`、`color`、`texture`、`editing`、`sound`）；`when` 只使用预定义、有限、可核验的场景事实字段和 tag，不运行任意表达式/脚本。
- 每个 `instruction_en` 描述**可观察行为**，不得含标题占位符、引用标签、模型指令、系统角色越权、外部 URL/路径或剧情替换要求。风格不能指挥执行工具。
- `requires_capabilities` 从**现有本机可验证工作流/后期能力集合**引用；未知能力 fail-closed 或显式降级，并把原因反馈给用户；严禁默认标记支持。
- `conflicts_with` 仅引用合法的内部技术/故事约束 ID；同层冲突由稳定优先级+用户覆盖确定性裁决，不能让 LLM 临场改规则。
- `source_notes` 只作来源说明，不含未授权素材、不引用导演名当作 H3 指令。
- 导入文件须有尺寸/数量/UTF-8、重复 ID/版本、非法 key、嵌套深度和 prompt injection 安全限制（`extra=forbid`）。不执行模板中的代码、shell、URL、路径或 JS。
- 规范化 JSON 用单一 canonical 算法计算 SHA-256（sorted keys、固定 Unicode 与数值编码规则），并将 `source=builtin|custom`、`id`、`version`、`hash` 持久化；同 id/version 内容不同 = 拒绝，不能在原地覆盖已发布版本。

### 5.2 双层输出与强度

- **UI 七维草稿**：模板给出 `StyleBible` 的建议（用户可改），按当前 `save draft → freeze` 流程执行。
- **镜头级规则**：解析器依据已确认剧本/场景/镜头事实、`strength`（`subtle|balanced|strong`）、适用条件筛选技法，输出类型化 `StyleResolution`（`selected_technique_ids`、camera/lighting/edit 结构化约束、rejected_reason、warnings、input_hash）。
- `strength` 是有限规则的应用强弱/优先级，不允许提高 H3 未验证能力或覆盖人工锁定剧情；输出必须可复算、可测试，禁止仅靠“strong cinematic style”自由文本加强。
- 默认 `balanced`；同输入同模板同覆盖同能力快照，解析器结果完全一致。
- 模板可以含电影美学方法，但不能把人物/素材/台词和已冻结七维的高优先级约束改写。

## 6. 版本与数据持久化（不可破坏现有 StyleVersion）

**推荐新增轻量记录表，而非直接改造现有七维 schema 结构**：

- `director_style_profile_revisions`（如需自定义导入可持久化）：`id`、`version`、`source`、`canonical_sha256`、`payload_json`、`created_at`；内置模板也要具备可复现快照/哈希，不能只读一个可变化的磁盘文件。
- `director_style_bindings`：`project_id`、`style_version_id`（冻结前可为空，仅草稿绑定时不得冒充正式冻结）、`profile_id`、`profile_version`、`profile_sha256`、`strength`、`overrides_hash`、`resolved_snapshot_json`、`resolved_sha256`、`created_at`。必须有唯一/一致性约束。
- **人工冻结事务原子性**：已有 `StyleVersion` 的原始七维内容和 SHA 不变；在冻结成功同一事务绑定本次已审核的风格来源快照，且只有此冻结版本可作为新镜头的当前权威。若服务端现有事务边界不支持，先重构为正确的原子路径后再添加新表。
- **旧项目兼容**：无 `director_style_bindings` 的 `StyleVersion` 仍可合法冻结、编译、生产；其来源标记为 `legacy/custom`，不能被迫迁移成某个导演。
- **历史快照**：冻结后编辑/升级/删除磁盘模板不会改旧版本/已应用镜头/已确认导演包/历史生成记录。用户想采用新版风格，必须先预览、写草稿、显式冻结新 StyleVersion，并按原有受影响对象的 invalidation/impact-analysis 流程走。
- **依赖身份**：AGENT task `input_hash`、ShotSpec/Shot revision 关联、DirectorPackage manifest/readiness `digest` 至少在需要应用新风格的位置纳入 `style_version_id + style_sha256 + style_resolution_sha256 + profile_id/version/hash`，禁止遗漏导致“模板变了但旧包仍被视为最新”。
- **禁止自动重跑**：风格冻结或模板导入只改变草稿/未来任务与门禁，不触发 Comfy/H3 请求、不夺取 GPU、不重试 UNKNOWN 外部调用。

如项目当前已有等价扩展表或元数据字段，优先复用，但必须在 PR 解释为何冻结身份可追踪、原子且可回放；禁止随意扩张 Alembic 脆弱的 SQLite 父表删除/重建迁移。

## 7. 服务端 API 草案

以下路由只表达产品合同，必须按现有 `api` 路由结构、Pydantic `extra=forbid` 和审批权限实现；不要无理由创建镜像 API。

| 方法 | 路由建议 | 语义 |
| --- | --- | --- |
| GET | `/api/v1/director-styles` | 列表：id/name/summary/categories/version/hash/source/availability；读取服务端目录，不调用 LLM/H3 |
| GET | `/api/v1/director-styles/{style_id}` | 结构化模板信息、七维建议、能力风险、revision |
| POST | `/api/v1/projects/{id}/director-style/preview` | 基于固定模板版本/哈希+强度+覆盖+项目约束，**只读**返回七维 diff、解析规则、冲突、H3 可用性和示例概要 |
| POST | `/api/v1/projects/{id}/director-style/apply-draft` | 通过 `expected_draft_hash` CAS 将所选风格映射为现有 StyleBible 草稿；返回 draft-only，绝不冻结 |
| GET | `/api/v1/projects/{id}/director-style` | 查看当前草稿来源及冻结版本来源；清楚区分两种状态 |
| POST | `/api/v1/director-styles/import` | P1：用户明确授权的自定义 JSON 模板上传、格式验证与冲突预览；默认不联网 |
| existing | 当前 style freeze / film agents / preview / package APIs | 通过兼容式扩展接入，不复制风格冻结/镜头编译/导演包权限 |

服务端对过期风格哈希、旧草稿 CAS、未知模板、禁用能力、语言不合规、违反冻结版本、恶意字段分别返回可操作的稳定错误码（建议 `STYLE_PROFILE_STALE`、`STYLE_PROFILE_INVALID`、`STYLE_DRAFT_CONFLICT`、`STYLE_TECHNIQUE_UNAVAILABLE`、`STYLE_RESOLUTION_CONFLICT`）；前端给出中文错误说明，并提供重新预览操作。导入/预览不等于 user approval。

## 8. Agent 交接、H3 编译与后期处理

1. **剧本/分集阶段**：允许用户在七阶段 `plan` 冻结风格，且镜头阶段可以读取本次选定模板及冻结 StyleBible；没有必要的 LLM 配置时通用预设和导演模板仍可离线套用。
2. **导演 Agent**：输入包含已冻结的 `resolved_snapshot` 和可执行约束；在合法的剧情/时间/素材范围内选择镜头叙事与连续性，而不是输出导演姓名标签。
3. **摄影 Agent**：将 camera/lighting 类型约束落入已有 `CameraShot`（composition/movement/lighting/axis），若需新增字段先判断 `ShotSpec.camera: dict[str,str]` 和现有校验能否安全承载；不得发明新的 H3 参考标签。
4. **剪辑 Agent**：将 editing 类型约束落入现有 `EditIntentPlan`/EDL；所有精确切点依旧遵守 `editorial_timing_plan` 和已有时序合同。不能要求 H3 通过“模仿诺兰”实现复杂后期效果。
5. **场记/QC**：检查风格矛盾和分镜结构，但不得把美学判断当成确定性合格证明。已定义 `style` 质量门禁沿用“技术检查 vs VLM 审美建议”的证据分离。
6. **H3 编译器**：保持 `H3PromptCompiler.compile()` 为唯一官方出口；若需要扩展 `ShotSpec`，优先在生成 `prompt_style`/`camera` 前解析结构化风格；引擎只给**可核验语句**，compiler负责固定六段顺序、参考标签、时间、对白和语言。**不得新增直接提交自由 prompt 的通道**。
7. **原有演示/生产状态**：风格预览只展示提案/提示词，不宣称出片。未真正调用本地 H3 的 E2E 只记 `TESTED_OFFLINE` / `MOCK_VERIFIED`，不能写 `REAL_H3_ACCEPTED`。

## 9. 导演模式 UI 设计（小白优先）

在当前“剧本统筹/作品风格”区域增加一个**导演风格库**选择面板，不加主导航、不要在同一屏堆叠复杂参数：

- 风格来源切换：`通用预设` / `导演风格` / `我的风格`（我的风格可 P1）。
- 风格卡片：中文姓名或类型名称、2 行方法论摘要、适用类型、当前版本、核心镜头特点；不强制抓取电影剧照与导演肖像，不将版权作品作为默认资产。
- 选择后：显示“七维总览 + 代表镜头语言 + 风格不可执行警告”，强度默认“标准”，可选“轻度/标准/强烈”，高级七维选项折叠。
- 操作：`查看预览` → `采用到草稿` → 使用**既有** `确认并冻结`；按钮状态严格来自后端，不能在预览时静默覆盖草稿；冻结历史与版本可查。
- 不展示 “prompt graph SHA / rulepack ids / H3 engine internal labels” 等工程细节；专业模式可展开真实规则摘要、模板版本/hash、当前工作流能力与预览编译结果。
- 兼容 `styleCatalog.ts` 的 6 套旧预设及既有测试；不改掉 legacy 自定义历史文本。服务端为新目录唯一权威，前端不得自己再复制一套导演配置。

## 10. 建议开发计划（云端 Codex feature 分支）

**分支**：从实现时最新 `origin/main` 创建 `feature/director-style-engine-v1-20261108`（冲突时取唯一变体名）；禁止在 main 直接开发。建议分 5 个可检验里程碑，最后由一个 PR 汇总。

| 阶段 | 工作内容 | 可验收交付 |
| --- | --- | --- |
| M0 — 基线与影响分析 | 仔细读本文与 `AGENTS.md`；列出真正 API/表/Agent 注入点；现有测试基线；确保不影响 R09 与七阶段 | `docs/DIRECTOR_STYLE_ENGINE_M0_BASELINE.md`、实际文件清单与风险表 |
| M1 — 模板标准/目录 | Pydantic DSE/1、JSON 加载、canonical SHA、内置模板发现、四份示例、目录查询与校验 | 加合法 JSON 即自动列出；恶意/非法/重复/旧版模板 fail-closed |
| M2 — 解析/版本/冻结 | `DirectorStyleResolver`、七维草稿预览/应用、事务绑定来源、数据库迁移、审计、CAS/冲突 | 旧七维风格继续工作，冻结模板版本不可漂移 |
| M3 — Agent/H3/导演包 | 风格上下文进入导演/摄影/剪辑链、结构化镜头结果、H3 官方六段 golden fixtures、导演包/依赖摘要及 stale 边界 | 四导演同一输入获得有证据的不同 CameraPlan/EDL/H3 提示词；原 RulePack 校验通过 |
| M4 — 前端与全链验收 | 简洁风格库 UI、导入/自定义 P1（如有余力）、Playwright/组件测试、Mock/Fake E2E、中文操作文案、迁移兼容、PR 总报告 | 普通用户可选/预览/冻结/分镜；测试、限制、证据与复现命令齐全 |

**MVP 首批模板**：`wong-kar-wai`、`alfred-hitchcock`、`john-woo`、`christopher-nolan`。它们是**内容验收样本**，不是引擎支持名单上限。可先只实现 4 个正确模板再用第 5 个由测试临时构造的模板证明“零代码增量”；风格差异应以构图/运动/光线/剪辑输出区别证明，不能只比模板人名或文案字符串。

**分支纪律**：云端 Codex 可在自己 feature 分支自主实施代码、单测、文档、必要的非破坏性迁移与修复；不执行真实 GPU 下载/推理，不碰用户本地数据，不能绕过审批/资产业务规则。针对需要依赖的 API/Schema 变更，列出向后兼容方案与回滚方式。完成后推送 feature 分支并创建 PR，**不要自动合并**。

## 11. 必须覆盖的回归与对抗验收

### 11.1 确定性单元测试

1. 内置四模板加载、重复 ID/version、非法 JSON、未知字段、过大文件、非 UTF-8、恶意 prompt、非法 capability 均按稳定错误拒绝。
2. 注入第 5 个合法 JSON 模板无需改解析器、编译器、前端目录静态配置即可发现、预览、应用和回归。
3. 同一输入、同一 profile SHA/version/strength/能力快照，解析结果字节级一致；模板改变一字节导致新 SHA；同 id/version 但改内容禁止覆盖历史。
4. 所有模板七维原始文本合法；英文 `prompt_style` 可被官方规则接受；中文非对白不得直接混入 H3 prompt。
5. 精确黄金输出：Ref2VA 六段顺序、`[Shot N]`、图像标签合法、时间有效、中文普通话原对白保留、无对白禁声、内部 cut 不破坏 `editorial_timing_plan`。
6. 四个模板的摄影/剪辑意图存在**结构层面差异**（具体字段与 technique id 的预期断言）；但剧情、对白、角色 ID、素材绑定与时长完全相同。
7. 未验证镜头运动、精确慢动作等技术被降级/交给 EDL 或提示不支持；不能编造 node 支持证明。
8. 草稿应用 CAS 冲突、冻结事务回滚与原子成功、模板更新对老 StyleVersion 不变、无绑定旧样式仍可编译。
9. 导演包 style/profile/hash 过期阻断旧任务的误复用；已运行/已通过/已交付对象不被静默覆盖；合法新版本走原本 invalidation 流程。
10. R09 缺图片只 notice、已绑定素材必须批准锁定、绑定图片不丢、Ref2VA/工作流能力不匹配仍按现有规则拒绝真实提交；无音色不凭模板创建音色。
11. UNKNOWN 不因风格应用/预览/冻结被重试；资源租约、Worker 和既有生产任务的行为不变。

### 11.2 全链验收/人工校验范围

- 测试链：创建项目→导入剧本→选导演模板→预览七维差异→采用草稿→人工冻结→分镜角色链→`apply-camera`→H3 prompt preview + 官方规则 validate→导演包 snapshot/readiness→Mock/Fake ComfyUI→EDL 输出；使用隔离数据库和临时素材/fixture，不碰实际项目。
- UI：新项目、已有中文自定义风格、老 StyleVersion、无 LLM 配置、模板被删除/升级、浏览器刷新恢复、模板卡片加载失败/空目录以及简体中文错误，都有行为验证。
- 真实 H3/审美对比：另列 `NOT_RUN` 清单，必须由用户授权的本机测试执行；不能从 Mock `PASSED` 推断导演风格的真实影视质量。
- 现有仓库后端 pytest、Ruff、前端 TypeScript/build 和现有 Vitest 均要过；运行策略按 `AGENTS.md` 记载的仓库根目录及分段回归约定，CI 若额度不足应如实注明，不伪造绿灯。
- 反例测试要求必须证明：模板被禁用/改版、镜头无素材、中文模板语句误入 H3、冲突风格强制覆盖对白/时长、导演包 SHA 不一致等“会真实失败”；不要为通过测试而放宽校验。

### 11.3 完成判据（全部满足才可标记 CODE_PASS）

- 新增第 5 套模板 **0 处核心代码变更** 即可使用；H3 Compiler 仍唯一且官方六段固定。
- 风格建议能影响类型化 CameraPlan/EDL/H3 镜头语句，不是“导演名+通用标签”。
- StyleBible 原七维、人工冻结、旧 StyleVersion、旧项目、既有导演包和素材/镜头审批全部兼容。
- 前端两步或少量步骤可选导演并预览、采纳草稿、冻结，且不增加第八阶段、不暴露多余工程项。
- 全部新老测试有实证且故障边界写明；本地未执行的真实 GPU/声画质量不得申报 PASS。

## 12. 文档与 PR 交付物

- 修改/新增源码、Alembic、4 份内置模板、Schema/fixtures、文档、所有配套测试。
- `docs/DIRECTOR_STYLE_ENGINE_IMPLEMENTATION_REPORT.md`：变更清单、原理、重要权衡、接口/迁移、依赖哈希、示例输入输出、风险、尚需本机验证项。
- `docs/DIRECTOR_STYLE_ENGINE_TEST_REPORT.md`：具体测试命令、通过/跳过/失败数量、红队反例、Mock/真实验收边界、对 main 兼容性证明。
- PR 标题建议：`feat(director): modular H3 director style engine and extensible catalog`。
- PR 描述明确：base/head SHA、变更文件、截图或文字 UI 证据、`CODE_PASS / TESTED_OFFLINE / REAL_H3_NOT_RUN` 分离、未解决阻断与回滚方案；**等待用户审查再决定合并 main**。

## 13. 云端 Codex 启动要点

1. 从最新 main 拉取和独立建分支，读本 MD 与 `AGENTS.md`，先验证与现有主干代码没有冲突，再分阶段实装；**不只写方案、不只做假数据演示**。
2. 优先完成可扩充的模板引擎/版本治理，四套内置风格是第一个用例；避免把四个名字和内容硬编码到 `StylePanel.tsx` 或 `H3PromptCompiler`。
3. 先复用当前 `StyleBible` / `StyleVersion` / `CameraPlan` / `FilmAgentDispatcher` / `H3PromptCompiler`，只在证明必要时做最小、可回滚的 API/Schema 扩展。
4. 不得修改真实 H3 工作流、模型栈、渲染器生命周期、UNKNOWN 规则、素材确认规则或已有视频产物。
5. 每阶段保留可运行测试与证据；完成时推送 feature 分支、创建 PR，等待审查，不合并。

---

**方案结论**：将“导演风格”建设为**从官方 H3 规则反推结构化风格模板协议、再把每位导演的摄影/灯光/剪辑方法映射到同一编译标准**的数据驱动功能。导演模板的数量与核心代码解耦，所有生产安全与版本冻结规则仍由 Shine 现有工程管控。
