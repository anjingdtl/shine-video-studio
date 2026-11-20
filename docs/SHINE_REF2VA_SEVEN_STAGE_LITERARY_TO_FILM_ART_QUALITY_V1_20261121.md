# Shine 七阶段 Ref2VA 文学—镜头—成片艺术质量收束建设方案 V1.0

> 状态：待本地开发机 Agent 实施的**完整建设基线**，非代码已完成声明  
> 编制：2026-11-21（Asia/Shanghai）  
> 远端审查基线：`main@6ca5eeb6ac449cfc7d6ebdd1354733ddaf2c0c76`（2026-11-20 19:07:49 UTC）  
> 方案分支：`docs/ref2va-seven-stage-art-quality-chain-v1-20261121`  
> 适用范围：**七阶段 AI 导演流水线，Ref2VA 为唯一真实生产主模式**；不改四阶段自主制片业务域  
> 核心：保留既有五 Agent、四阶段文学能力、Ref2VA 编译器、视频调度和剪辑器，修复信息交接、导演动作/摄影表达、参考角色语义、音频防回归和真实看镜剪辑  
> 纪律：**一套事实、一个裁决、一个执行者**；零第二套基础检查、零第二个 H3 提示词输出器、零第二套 EDL 执行器

---

## 0. 目标、证据层级与不做事项

### 0.1 目标

从“小说文本 → 分集 → 分镜 → 素材 → 导演包 → H3 Ref2VA → 后期成片”的既有链路中，使每个实际投产的视频段同时具有：

1. 可追溯的文学事实和表演意图（谁、在何处、因何做什么、动作如何开始和结束）；
2. 物理合理的角色站位、道具空间关系与摄影机起止状态；
3. 对 H3 原生 Ref2VA 六字段和图/音/视频引用角色的准确组织；
4. 被用户明确授权的对白与旁白，以及严格的项目语言锚定；
5. 合理的镜内 CUT、镜间 CUT、延续参考和真实视频后的剪辑点；
6. 对人物穿模/突现、乱运镜、身份漂移、莫名人声/外语及衔接破裂的可回溯质检/返工能力。

**验收不是“pytest 全绿就有电影感”**。分别报告工程、Ref2VA 编译、真实 H3 生产、音频与艺术成片四类证据；真实能力未经测试即记 `NOT_RUN`，质检不可用即记 `UNAVAILABLE`。

### 0.2 事实分级

- **R（Repository）**：本方案对固定提交 `6ca5eeb` 的源码/测试/验收记录进行只读核查所得。
- **O（Official）**：MiniMax 官方 `h3-prompt-writing` 的 `SKILL.md`、`ref-en.txt`、`base-en.txt`，规范优先级最高。
- **C（Community）**：公开案例/issue 属风险线索与 A/B 测试素材，不是新的模型官方保证。
- **D（Design）**：本方案建议；不等于现有代码已经支持，也不应声称达到实际成片效果。

### 0.3 范围与不可触碰边界

- **只改七阶段业务链**。`backend/shine/manual4/*` 四阶段 H3 自主制片业务、事实/执行队列/用户流程不得为本次目标被改造；必须保持双线隔离和既有基础配置共用边界。不得借机改动统一 ComfyUI/模型权重/节点管理、安装器、基础设置或资源调度裁决。
- 保留现有 `StyleBible`、`EpisodePlanPayload`、`ScriptBreakdown`、`DirectorialPlan`、`CameraPlan`、`ShotSpec`、`DirectorPackageVersion`、`ProductionRun`、`EditIntentPlan`、`EditDecisionList`、`EpisodePostContract` 的权威归属；**优先扩展现有可选字段或派生视图，不新增平行 JSON 文件链、第二数据库事实表或第二审核开关**。
- 不重写已冻结导演包/人工编辑提示词/旧镜头、旧内容哈希和已交付视频。新行为仅通过新镜头修订、新包/新请求生效。
- 用户设置的单镜上限是唯一时长事实，支持用户现有 4–15 秒有效范围内的配置，不强制固定 5/8/10 秒，不为质量优化增加另一个时长控制器。
- 不因 VLM/ASR 不可用而扩大已有的投产硬阻断；保持原有咨询质检地位、任务级报错和可复核证据。
- LoRA/采样/二采/RelayVSR X2 是执行策略，不承担导演语义纠错；本轮不以提高算力和增加工作流数量替代叙事/拍摄修复。

---

## 1. 基线核查：七阶段的真实工作方式（R）

| 阶段 | 当前事实与代码入口 | 真实改造焦点 |
|---|---|---|
| ① 创意与剧本 | `director/creative.py` 的想法→剧本；`director/pipeline.py` 的长文分卷/大纲/GlobalStoryBible/`FourStageSceneScript`；`literary/memory.py` 的五维记忆与预算视图 | 保留原文证据、已确认场景脚本、对白授权与角色记忆的权威链；不能改写出“新旁白” |
| ② 剧本统筹 | `director/streamline.py` 的分镜书预览、分集计划/时长与风格冻结；`director/journey.py` 根据台账投影阶段 | 锁定分集和艺术意图，不把“文学节奏”误当精确视频切点 |
| ③ 镜头拆解 | `queue_director_storyboard` → `_advance_director_unit` → `api_film.generate_storyboard`；五角色顺序：scriptwriter→director→cinematographer→continuity_supervisor→editor；`apply_camera` 落 `ShotSpec` | 信息交接、可拍摄动作、空间一致性、镜头动机、真实内切点结构 |
| ④ 拍摄素材准备 | `director/streamline.py::derive_shot_materials` 从已确认镜头需求派生素材；`materialprep`/素材中心整备主资产 | 确认素材是否为角色/场景/构图/运动/声音参考，并维持资源 ID、引用顺序 |
| ⑤ 导演包 | `director/package.py` 冻结已确认版本与身份；`director/prompt_revisions.py` 管理人工提示词；`h3/compiler.py` 编译 | 六字段含义、角色映射、镜头时间线、正向声音合同、版本兼容 |
| ⑥ 视频生产 | `production/runner.py`、`comfyops/production_series.py`、现有动态工作流/质量与续接；`quality/advisory_queue.py` 检测意外人声 | 精确重拍与已存在的质检通道；绝不第二执行者 |
| ⑦ 后期成片 | `production/episode_delivery.py::build_episode_edl`；`media/delivery_engine.py::build_auto_edl`；`media/assembly.py` 实际拼片 | 目前默认真实产物整段取用和 `CUT`；让“已看过素材”的入出点建议进入**同一 EDL 合同** |

### 1.1 源码级重点发现（R）

**R1：场景脚本交接不完整。** `streamline.py::_section_beats_view` 会为分镜书预览读取 `FourStageSceneScript`；但 `queue_director_storyboard` 将原始文档按源偏移分段，在 `_advance_director_unit` 内将 `document.text[unit.start_offset:unit.end_offset]` 传给 `StoryboardRequest.text`，附带大纲和 `continuity_facts`。已确认场景脚本不是此自动拆镜请求明确优先读取的权威内容。风险：剧本内的表演、视线、人物意图、修订后的对白没有直接、稳定地传到五 Agent。不能仅因为 `FourStageSceneScript` 存在就盲目取用：必须校验当前 document/outline/section/确认态与源 span。

**R2：镜内 CUT 被压平成自然语言。** `api_film.py::apply_camera` 当前把每个 `EditorialCut` 拼接成 `At 00:xx, hard cut. ...` 放进 `ShotSpec.story_beat`；`h3/compiler.py::_shot_text` 以 `[Shot 1]` 开头再接整段文本。此实现并未可靠保留 Ref2VA 官方的 `[Shot 2] At MM:SS.mmm, ...` 独立镜头时间线语法。

**R3：摄影机字段过于机械。** `h3/compiler.py::_camera_sentence` 直接以 `Camera: key: value` 汇合 `ShotSpec.camera`；`CameraShot` 有 composition/movement/lighting/axis，但没有确定性“何时不动/运动为什么发生”的场景约束。影视表达缺乏镜头选择动机，不能仅靠长文本堆运镜术语。

**R4：素材角色与保留语义被过度泛化。** `compiler._summary` 见 `ReferenceUse.VIDEO` 就追加 `video continuation`，并不区分真正续接 vs. 只参考动作轨迹；`compiler._retention` 对非音频一律偏向 `fully_preserved`。官方允许 `fully_preserved` / `partially_preserved` / `attribute_transfer` / `weak_reference`，且视频参考不必等于续接。

**R5：第七阶段剪辑能力边界明确。** `episode_delivery.build_episode_edl` 无用户确认的剪辑合同时按 `in_s=0, out_s=实片全长`，每镜 `CUT`；`EpisodePostContract` 可确认每镜一个入出点和字幕版本，不支持音频增益混音。故**不能承诺本轮无需扩展合同就实现多段复杂重排、J/L-cut 或新音频混音**。先做真实视频感知的首尾裁剪和衔接建议，再按确有必要评估合同扩展。

**R6：静音镜头修复刚刚更新。** 最新 `main@6ca5eeb` 的 `compiler.py` 新增 `non_vocal_ambience_text()`、无对白的项目语言世界锚点；`api_film.apply_camera` 在无对白时清理导演风格 sound ambience；`crew_prompts.py` 同时增强“仅真实上镜角色/首次视觉出现顺序”约束。此修复是**受保护的既有成果**，不得按照旧 `f62e0b2` 基线重复修改。

**R7：质检能力存在，但并非保证生效。** `quality/advisory_queue.py::unexpected_speech_finding` 已能标记无对白镜头意外人声和有对白镜头额外人声；`quality/semantic.py::dialogue_findings` 能提示台词不符。历史实测 ASR/VLM 有 `UNAVAILABLE` 或超时，不能把缺失评审当作已证实“无旁白”。

---

## 2. 官方 Ref2VA 合同与社区经验（O/C）

### 2.1 优先级与来源

- O1 官方 Skill：<https://github.com/MiniMax-AI/MiniMax-H3/tree/main/skills/h3-prompt-writing>
- O2 Ref2VA 原文：<https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/h3-prompt-writing/references/ref-en.txt>
- O3 基础镜头/运镜/对白时间语法：<https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/h3-prompt-writing/references/base-en.txt>
- C1 Comfy-Org 提示词指导：<https://github.com/Comfy-Org/docs/blob/main/tutorials/video/minimax/minimax-h3-prompt-guide.mdx>
- C2 MiniMax 官方仓 issue #95：<https://github.com/MiniMax-AI/MiniMax-H3/issues/95>（音色引用导致多台词/重复/截断的用户报告）
- C3 MiniMax 官方仓 issue #100：<https://github.com/MiniMax-AI/MiniMax-H3/issues/100>（双音色多轮交替说话人错位的用户报告）

社区报告只形成待复现的**测试用例和风险权重**，不能将猜测写成确定性模型规律；官方格式为最高权威。

### 2.2 唯一目标六字段（按顺序）

1. `subject_definitions`：`<Subject N>`（角色/场景/物体/动作等可复用视觉主体）；`<Picture N>`（具体起帧/关键帧/构图锚，若仅是身份来源则只在 Subject 内引用）；`<Video N>`（直接剪辑/续接/运动或节奏的来源）；`<Audio N>`（音频复用或参考）。
2. `summary`：按**实际用途**标记 `reference generation`、`keyframe completion`、`video editing`、`video continuation`、`audio reuse`、`audio reference`。素材存在≠任务类型必须存在。
3. `retention_analysis`：按引用角色判断具体保留方式，不能一律 `fully_preserved`；声音复制 `fully_copy`/`partially_copy` 与仅音色 `reference` 不得混淆。
4. `detailed_description`：逐镜头**可观察事件**，按播放顺序写构图/主体/位置/环境/动作起点—过程—结果/机位/光影/时间码；`[Shot 1]` 无时间戳，后续 `[Shot N] At MM:SS.mmm, ...`；运镜用运动类型、幅度、速度的自然语言。
5. `overall_soundscape`：描述实质存在的环境声、物理声、合法对白空间；无对白段以正向无声词汇描述，不在面向模型的最终文本中堆“禁止旁白、外语、哼唱”等词。
6. `non_diegetic_music`：只描述配乐或 `N/A`，不要因空白而注入默认有人声音乐。

其他强制合同：原对白仅在 `<d>[Language] ...</d>`，说话人 `(S1)` 等稳定映射；全篇视觉/运镜文字用英文，原对白/画面中可见文字保留原语言；同一引用标签全字段保持对应同一源；只有实际跨切台词才用官方 `<scenetrans>`，不可把此标签当任意过渡词。

### 2.3 参考角色处理准则

| 使用场景 | 正确关系 | 禁忌 |
|---|---|---|
| 角色定妆图 | `Subject` 来自 `Picture`，身份/服装按锁定程度保留 | 把人像定妆照当必须完整复现的开场画面 |
| 场景背景图 | 可定义场景 Subject，或作为构图 Picture | 不分用途一律标称首帧 |
| 动作示范视频 | `Video` 提供运动/节奏，通常 `reference generation` | 只因传入视频就声明 `video continuation` |
| 前镜末帧 PNG | 只有与真实上镜连续动作吻合才作为开场软参考/明确锚定 | 所有邻镜自动复刻前镜最后画面 |
| 音色示范音频 | `Audio` 仅参考 timbre/delivery | 把原素材台词、外语或环境人声拷入目标 |
| 实际复用源音频 | 根据复制范围使用 `fully_copy`/`partially_copy` | 假称仅引用音色且实际复制了人声 |

以 `ReferenceManifestItem`、已绑定的 `ShotBindingInput`、`continuity_derivative` lineage 为唯一参考事实；按物理槽位及官方标签对照编译。若要增加参考角色标记，优先在既有 manifest/绑定元数据中附加**可选、版本化**的用途信息，**不新增“影子素材登记表”**。

---

## 3. 面向用户保持原七阶段；后台唯一链路

```text
① 创意与剧本 [原文/已确认大纲/可选已确认场景脚本/五维记忆]
              │ 锁定事实、剧情、人物、台词、允许的旁白
              ▼
② 剧本统筹 [分集范围/影片类型/风格/目标时长/单镜上限/声音策略]
              │ 唯一分集计划 + 冻结 StyleBible
              ▼
③ 镜头拆解 [既有五 Agent]
  ScriptBreakdown → DirectorialPlan → CameraPlan → ContinuityReport → EditIntentPlan
              │ 可拍摄动作与摄影机决策、精确分段/镜内切点
              ▼
④ 拍摄素材准备 [现有素材中心与素材准备]
              │ 人物/场景/道具/动作/音色引用的角色和来源
              ▼
⑤ 导演包 [ShotSpec + reference manifest + 现有 H3PromptCompiler]
              │ Ref2VA 原生六字段、编译版本与引用 hash
              ▼
⑥ 视频生产 [原统一调度/ComfyUI/Ref2VA/续接/现有 QC]
              │ 真实 passed take、视觉/音频异常证据与精确返工
              ▼
⑦ 后期成片 [视频感知剪辑建议 → 既有 EditDecisionList/episode post → FFmpeg]
              │ 镜头入出点/衔接合理性/字幕，输出新交付版本
              ▼
             完整剧集
```

**单一事实**：原始文稿与确认文本、场景事实、分镜、素材引用、镜头规格、生产实例和剪辑表分别由其现有归属对象负责。其他 Agent 仅通过同一来源的投影读取，不擅自复写。  
**单一裁决**：现有确认、校验和规则包进行唯一裁决，艺术判断是诊断/建议/定向返工输入；绝不增加一条“不通过就无法开拍”的第二质量链。  
**单一执行**：只有现有 Worker/调度层提交 ComfyUI、现有 delivery engine 消费 EDL；Agent 不直接调用 FFmpeg、新渲染队列或写第二份视频。

---

## 4. 七阶段具体建设合同

### P0-A：①→③ 文学/剧本交接收束（第一优先）

**目的**：把文学阶段的有效剧情和表演信息送入真正执行的五 Agent 拆镜，而不是仅供分镜书预览。

设计一项**只读“当前分镜创作输入选择”函数**（落在 `director/streamline.py` 或复用现有读取函数，不建第二套任务和大表）：

- 输入为当前项目/已确认 `outline` /计划 `EpisodePlanPayload` /`production_section_id` /源 `document_sha256`/源 span。
- 若有**同文档、同大纲有效谱系、同 section、已确认且未 stale** 的 `FourStageSceneScript`，从该稿抽取该单元相关的场景文本、beats、对白和表演意图，用其作为“剧本层创作文本”；原文偏移仍作为不可变来源证据，不能混跨 section/分集，不可拿上一版脚本替代。
- 若无匹配已确认场景脚本，照旧使用当前源片段；不可为补全此能力强制用户跑文学四阶段。
- 如果当前 `FourStageSceneScript` 覆盖整 section，而 `queue_director_storyboard` 产生了多个独立 unit：**不能将整场脚本在每个 unit 重复投喂**。先按可证实的 `source_refs`、`span_ids`、`beat` 对齐；无法精确对应时安全回退原文，保留诊断，不用猜测切片。
- 生成新的分镜请求身份时包含被采用的脚本 `artifact_id/sha` 与来源 span；避免重试期间换稿、旧缓存混入。既有 `resume_steps`、未知外部调用对账机制保持。
- `literary/memory.py` 已有有预算的五维事实检索：把角色/关系/线索/伏笔作为**创作前参考**与**创作后校正**，不能把五维长篇原文直接倾倒进每段 H3 prompt。
- 角色关系、剧情主线/伏笔只帮助解释表演原因；**最终 H3 只接收本镜可见的动作和必要连续性事实**。文学暗线不得变为无端旁白。

**工作口径**：场景脚本“确认稿优先，原文为溯源”；未确认/谱系不一致即原文优先。这里的“优先”只指创作输入，不允许覆盖用户明确冻结的大纲和分集事实。

### P0-B：②③ 表演与摄影决策提升（不新增 Agent）

复用现有 `crew_prompts.py` + `film_contracts.py` + `api_film.py`：

- 编剧 ScriptBreakdown：把本段文本转为可视化 beats。每 beat 至少表达**主体、所在位置/姿势、可观察动作、情绪或观众需看到的证据、下一状态**；人物仅列实际上镜/按现有声音绑定规则确有发声关系者，不复读全角色。
- 导演 DirectorialPlan：每一 render segment 的主要目标只有**一个清晰动作/情绪重心**；需决定静止/运动、场景轴线、切镜原因、开场和结尾可以衔接的状态。对白与用户时长继续遵守既有 `editorial_timing_plan` 的唯一精确裁决，禁止重演 11-04 的“文字提示鼓励改切点，服务端却不允许”问题。
- 摄影 CameraPlan：每段表达 `starting framing / subject position / camera relative position / movement type / amplitude / speed / motive / ending composition`，复杂机位变化落在镜内 cuts 的对应镜头而不是笼统写给父段。**默认静机位**，只有动作、视线或信息揭示确实需要时才运动；推镜≠变焦，不能堆“push/pull/pan/tilt/orbit”。
- 场记 ContinuityReport：覆盖上镜角色位置/服装/手持物、地面接触、左右屏幕方向、动作完成状态、视线轴、镜内切点合法性、相邻段的真实接续语义，**不去重新裁决分集数量**。
- 剪辑 EditIntentPlan：保留计划的镜头衔接理由，但不得自称已经看过尚未生成的视频；后期实际看镜另在第⑦阶段。

**低风险实施方式**：优先改善上述 Agent 提示词与其现有结构化字段的写法与验证，不立第二个“影视一致性引擎”。若仅靠字符串不能传递镜内 cut，才按 `P0-C` 扩展一个可选字段。

### P0-C：③→⑤ 保留镜内时间线，不再只靠自然语句拼接

- `EditorialCut(beat_id,start_s,visual_description)` 已在 `ShotIntent.cuts` 存在；它是镜内 cut 的唯一来源。
- 在 `apply_camera` 落镜时，**不要只通过拼入 `story_beat` 的 `At ... hard cut` 文本向编译器传递**；建议给既有 `ShotSpec` 增加一个可选、结构化的 `internal_cuts`（或等效最小承载），包含切点偏移和剪后视觉文本，按照现有 `editorial_timing_plan` 的判定存储；**不额外建表，不生成另一套独立剪辑计划**。
- 必须保持旧镜头序列化/哈希逐字节不变：可选字段为空时省略，兼容历史记录；已确认镜头和旧包不回写。
- `h3/compiler.py::_shot_text` 从现有 `ShotSpec` 编译出 `[Shot 1] ... [Shot 2] At MM:SS.mmm, ...`；各 cut 时间严格递增且 `< segment duration`；摄影描述只附在它所属的当前内部镜头。
- 复杂“同一视频段内转角/换景/多人激烈交互”应被导演识别为高不确定性，优先拆为独立**渲染段**并交由后期剪辑；不得“禁止所有镜内 CUT”或为其额外创造一次最短时长逻辑。无法在既有时长合同下合法拆分时，按当前用户确认的时间表保持并提出可预览的风险，不因艺术建议创建死锁。
- 镜间 `CUT`、`LAST_FRAME`、`HARD_FRAME_ANCHOR`、`VIDEO_REFERENCE` 等只按现有受支持选择，新增**决策理由**不新增执行路径；首帧软参考并非叙事上的硬性连续镜头。

### P0-D：④⑤ H3 Ref2VA 原生六字段语义校准

修改局限于 `h3/compiler.py` 及其调用的现有模型/规则，保持 **H3PromptCompiler 为唯一文本编译器**：

1. `subject_definitions`：以真实 manifest 为主；角色主体与图像来源区分；视频只在真实参与时定义；说话人顺序全镜稳定。维持 `subject→picture→video→audio` 的已验证物理顺序及与实际 H3 文本编码器 `<Picture N>` 的序号映射。
2. `summary`：按 `manifest.use_as` + 用途/continuity.mode 推导真正任务类型；`video as motion reference`≠`video continuation`；`audio as timbre`≠`audio reuse`。
3. `retention_analysis`：按显式用途选择官方枚举，不可只凭素材类型一刀切；人脸/衣装需要强保留时明确，背景氛围可弱参考，运动可迁移，音频 timbre 只作 `reference`。对于**没有证明的用途字段**采用兼容老行为并发出诊断，不要猜用途然后强行改变冻结内容。
4. `detailed_description`：依据已批准 `ShotSpec.story_beat` + 可选镜内切点 + `camera` 组织英语**自然镜头叙事**，起点状态→主要动作→物理结果→镜头结束状态；避免 `Camera: movement: ...` 式的机械枚举和每镜整段重复故事大纲；舞台动作、地面、姿态、道具、视线优先于风格形容词。
5. `overall_soundscape`：严守 `COMPILER_VERSION = shine-h3-compiler/1.3.0` 最新正向声音策略和 `main@6ca5eeb` 新增无对白环境声过滤；如需改变 prompt 字节必须**升新编译器版本**，永久保留 1.2.x/1.3.0 历史重编译输出，旧快照不迁移。
6. `non_diegetic_music`：只使用冻结声音策略明确允许的配乐；无意外人声/歌词暗示，不把小说旁白写成配乐内容。

**官方规则包** `h3/rulebook.py` 原有 `RULE_VERSION`/哈希钉住机制不被替换为“社区规则包”；先对比官方规则版本和本地快照，若官方原文变动，出具差异、许可与版本决议，不能无声修改 frozen pack 以破坏 provenance。

### P0-E：历史旁白/外语回归作为不可协商合同

**历史证据**：`docs/PROMPT_TUNING_20261115_NARRATION_PRIMING.md` 记载编译器早期负面人声词枚举诱导无端旁白；1.3.0 改正向声音合同。最新 6ca5eeb 又针对无对白的 `sound ambience` 增加过滤和语言锚点。`docs/ACCEPTANCE_20261104_VOICE_AND_REF2VA.md` 及 `docs/ACCEPTANCE_20261106_CROSS_SERIES_CONTINUITY.md` 记录过真实 VLM 超时、ASR HTTP 404/运行时不可用，因此**不能把代码里有 QC 当成已通过成片验收**。

执行契约：
- **禁止未经用户明确允许生成旁白**。`StyleBible.narration_level` 未设置时，对**新创建的**分镜任务采用“未授权旁白”有效策略（可通过现有风格决定层明确派生，不另造第二配置项），`none` 完全不转换为 narrator；`low/high` 仍须按剧本与风格限定。旧冻结风格维持旧语义/哈希，不静默重写。
- `dialogue_ratio_percent` 不是强制编造对白的配额。只有已授权改编、原文证据支持且说话人合法时才允许新增；`0` 或无台词场景不得凭紧张感生成额外语言。
- 以 `ShotSpec.dialogue`、说话人 ID、时间戳和 `project.language` 为音频事实。英文提示词与中文对白不冲突；`<d>[Chinese] 原文</d>` 要保持逐字/标签/时间一致。说话人没有合法绑定时按原有编译/素材修复流程处理。
- **不在最终 H3 提示词正文重新写入长篇否定式人声词枚举**；“无对白全程 non-vocal”“有对白只有带标记的原文、全体说项目语言”的正向写法为受保护合同。
- 声音 master、音频续接派生物及视频的音轨**职责不同**：仅音色参考不能偷带参考音频的原句；没有明确复制意图时不能把参考视频原声直接当最终音轨。
- `quality/advisory_queue.py::requested_kinds` / `unexpected_speech_finding` 已涵盖“无对白也跑 ASR”的政策，**只能复用/增量扩展已有检测**；ASR 可信度低时标记 `uncertain`，缺失时标记 `UNAVAILABLE`；不把 ASR 空字符串简单等同安静，也不新建硬阻断。
- 附带检查“字幕来源、音频后期”是否注入新话语。当前 `EpisodePostContract` 无 audio gain 混音功能，禁止文档声称已能后期修音。

### P1-A：⑥ 已有 QC 的动作/物理/衔接反馈

复用 `quality/semantic.py`、`quality/advisory_queue.py`、`quality/repair.py`、既有 quality gate 与 Worker：

- 对每镜采样**首、中、尾与可疑局部时间点**，判定“人物突现/消失、身体穿地面、动作轨迹跳变、道具穿透/身份漂移、摄影机运动与导演意图不符、嘴部/手部明显错误”；跨镜加上一镜末尾和下一镜开始的对照。
- 静态抽帧无法判定所有运动逻辑，建议结合短时间窗帧序列/有条件小段分析；受现有 VLM/显存/超时预算约束，先测试真实性能，不新增必须存在的“全片一致性大模型”。
- 每个发现写进**原有 QC/repair 证据结构**：`code, shot_id, timestamp_range, evidence, confidence, suggested_repair`；不能在几乎没有证据时宣告确定穿模。
- 错因→现有定向处理：文本不清→重新编译/修镜；参考错位→修素材用途/绑定；物理生成事故→只返工单镜；剪辑过长→第⑦阶段 trim；模型本身不可控→明确人工确认/后续留档。不自动扩大到全剧返工。
- 分级：`verified` / `uncertain` / `not_run`，保留既有非阻断咨询门；用户可按原流程决定是否重拍。

### P1-B：⑦ 基于真实生成视频的自动看镜剪辑建议

- 不另写剪辑流水线：获取现有 `episode_current_jobs` 认定的当前 PASSED take 和真实媒体 hash/时长，只对对应 run/episode 生成**剪辑建议**，最终交由现有 `EpisodePostContract`/`EditDecisionList` 和 FFmpeg 执行。
- V1 限现有合同能力：镜头先后仍按已确认 `episode.shot_ids`，基于实际首尾帧、局部运动、ASR 时间戳和画面节奏建议每段合法 `in_s/out_s`（每个 shot 至多一个连续取片范围），裁去明显的无意义开头/尾巴/伪动作，避免在台词正中或尚未完成的动作中硬切。
- **“计划剪辑意图”与“真实素材剪辑决策”分开**：`EditIntentPlan` 是开拍前的导演意图，后期建议才是看了真实片段后的操作，但两者都不获得第二执行权；不能依据预计 5 秒代替实测 5.17 秒。
- 对“应该补一个反打镜头/动作衔接镜”仅生成 `shot_revision` 建议，走既有返工/新增合法镜头确认路径；不擅自改剧本、分集/角色和出片顺序。
- 真实需要多次切分同一源镜、J/L-cut、音轨混音、复杂叠化时，**单列 V2 待决**：先扩展唯一 post/EDL 合同并完成迁移，再进入生产；本轮 V1 不用 hack 或另建第二装配器伪装实现。

---

## 5. 数据传递与最小结构建议

### 5.1 一套内容与修订链

```text
ProjectDocument(document_id, sha, original text, source spans)
  └─ confirmed outline / GlobalStoryBible + five-dimension memory
      └─ optional confirmed FourStageSceneScript (same section + lineage)
          └─ EpisodePlanPayload(confirmed) + StyleVersion(frozen)
              └─ ScriptBreakdown(beats, exact authorized dialogue)
                  └─ DirectorialPlan(shot/cut/continuity)
                      └─ CameraPlan + ContinuityReport + EditIntentPlan
                          └─ ShotSpec + bindings + manifest
                              └─ H3 compiled prompt(version, sha, six sections)
                                  └─ JobAttempt / real MediaArtifact
                                      └─ QC / Repair → EDL / EpisodePostContract → delivery
```

`FourStageSceneScript` 是“可选已确认创作稿”，不是要求用户再额外做四个页面；未使用它的项目沿现有安全路径。每个转换边界带源 artifact 与 hash，只有真正改变镜头内容或用户确认的新版本才使后续快照失效。

### 5.2 一个可选的结构扩展，而不是二次 schema 治理

默认先以原有模型字段和 Agent 输出改善完成 P0-A/B/D/E。确需保留镜内 cut 的结构，优先扩充 `ShotSpec` 一个**可选**的 `internal_cuts`：

```json
{
  "internal_cuts": [
    {
      "start_s": 3.5,
      "beat_id": "B02",
      "visual_description": "The woman places both feet firmly on the floor and rises from the chair."
    }
  ]
}
```

以上仅为可选数据形状草案，字段最终命名/序列化规则必须以本地 Agent 的影响面审计为准，不提前承诺修改所有模型。不得同一 cut 既存两份可独立编辑的数据，`ShotIntent.cuts` 仍是产生落镜数据的权威上游；`ShotSpec` 是已冻结执行快照。空字段不得改旧 hash。

### 5.3 内容预算：只传本镜必要内容

- 文学层：正文、跨段五维记忆、伏笔与关系，按既有 LLM governor 的弹性预算与回溯处理。
- 分镜层：本场剧情、当前人物/位置/物件、分集目标/受控风格/单镜时间表；输出结构化 `beat/shot/camera`，不传整本原文。
- Ref2VA 层：当前视觉动作、真实引用标签、必要空间连续性、受权对白与声音，不再复制全篇故事记忆或抽象叙事解释。
- 上游对“他想起往事、心中恐惧”只保留**可见外化**的表演、动作与镜头选择；不自动把原文字句变成读稿音轨。

---

## 6. A/B 样例、验收矩阵与发布指标

### 6.1 固定样例集合

在现有真实项目自有/获授权素材中取样，最少覆盖下列 10 类（建议每类 2 镜 + 2 组连续镜头；以可执行成本为准），保留旧基线对照：

| ID | 样例 | 主要验收 |
|---|---|---|
| T01 | 女主坐椅听声、扶椅起身 | 脚触地、站位、关节/身体轨迹、动作结束 |
| T02 | 角色步行出入门 | 地面、门框穿模、走位与景别 |
| T03 | 无对白静态惊讶近景 | 只有环境声/约定配乐、静机位、不乱嘴动 |
| T04 | 无对白氛围空镜 | 无意外英文/外语/旁白；字幕不被误读 |
| T05 | 中文单人对白 | `<d>` 原话、开头首字、时间和语言 |
| T06 | 中文双人对白 A→B→A→B | 全局说话人/音色/视觉主体映射，不串音 |
| T07 | 有明确授权旁白而人物不说话 | 旁白只来自授权台词，不把旁白配给在场人物口型 |
| T08 | 连续动作跨渲染段 | last-frame soft anchor 合适性，方向/动作接续 |
| T09 | 同一段内合法多镜头 CUT | `[Shot N]` 时间码正确，H3 实际切换可理解 |
| T10 | 不同地点/时间的镜间 CUT | 不强行续帧、不出现时空混乱 |
| T11 | 同一参考视频只借摄像机运动 | `summary` 为 reference generation 而非误称 continuation |
| T12 | 真实三镜以上自动拼片 | 逐镜 trim，衔接节奏，台词不被切断 |

T06 是社区 issue #100 报告的风险，不能以“代码映射正确”替代真人实际听审；T05 与 issue #95 报告的重复/首音节截断同理。

### 6.2 测试层级

**L0 基线与版本指纹**
- 开工前记录 main SHA、文件 hash、既有风格/规则包/编译器版本、测试基线；绝不可直接覆盖 main 未提交改动；
- 历史 1.2.x、1.3.0 的代表性 `CompiledPrompt.prompt` 字节/哈希回归；新版本对新镜头产生新文本；
- 不改变 `manual4` 业务源码、原 ComfyUI 目录、基础环境设置和调度裁决。

**L1 静态/单元测试**
- ①→③ confirmed scene script vs raw fallback；不同文档/旧 section/stale/未确认/重叠 unit/缺 source span 安全回退；五维记忆预算不引入额外话语；
- Ref2VA 六字段顺序、场景 Subject/Picture 差异、video only motion vs true continuation、保留方式分级、物理槽位/标签对应、多说话人/对话跨切；
- 镜内 cut 真实 `[Shot N]` 和时间码不越界，旧 `ShotSpec` 序列化与哈希不变；
- 新版本静音镜头不会出现历史负面诱导词列举；最新 `non_vocal_ambience_text` 与语言锚点保留；
- `narration_level=none`/未显式授权、low/high、dialogue_ratio=0、中文对话、音色引用、无对话带环境声；
- 精确分组表、cut 总和、不可分组反馈；旧 `EDITORIAL_TIMING_CONFLICT` 和 `LLM_FORMAT` 回归不出现；
- 交付 trim 使用真实媒资/sha、合法入出点、保留镜序/不裁剪有效台词、重复请求幂等。

**L2 后端/前端集成**
- 现有 `backend/tests/test_h3_compiler.py`、`test_h3_narration.py`、`test_crew_prompt_contract.py`、`test_film_crew.py`、`test_dialogue_contract_repair.py`、`test_director_streamline.py`、`test_story_continuity.py`、`test_literary_memory.py`、`test_continuity_media.py`、`test_cross_series_continuity.py`、`test_advisory_queue.py`、`test_advisory_model_reviews.py`、`test_director_entries_and_post.py`、`test_episode_subtitle_cut.py`、`test_output_spec_and_identity.py` 定向复验；
- 被改动模块的周边契约、迁移、API schema、前端构建/类型检查和后端完整测试按项目既有三层测试系统逐级执行，**不把全套无差别长测设为开发每一步的重复阻滞**；
- 拍摄素材缺失、ASR 未配置、VLM 超时、GPU 运行锁与四阶段并行状态的负例不增加第二检查链。

**L3 真实 Ref2VA / ComfyUI**
- 在本地开发机现有 `RTX 5080` 环境完成最少一个 2–3 镜连续片段和 T03/T04/T05/T06 中有代表性的真实音频片段；其他案例可分批。真机测试保留 Model/量化版本/LoRA/采样设置/参考集/工作流哈希/分辨率/时长/提示词 SHA；
- 同素材同设置比较“旧编译器+旧导演指令”和“新方案”，若本地推理不可控，记录变量；不得虚报严格的同 seed 绝对可复现；
- 未接入用户的 `4070`、`5090vd2` 真实设备时，不要求也不得宣称通过这两种真机性能测试；
- 真实人工逐镜听声看画面；ASR/VLM 可用则记录独立证据，不可用保留 `UNAVAILABLE`。

**L4 艺术质量判断**
- 音频硬回归：新增的无授权旁白/外语乱入/错误说话人/重复台词事件必须为 **0 个已确认的回归**；任何疑似案例须复核与给出复现包，不自动宣布通过。
- 基本物理合理性：新增明显脚穿地、人物突生、空间瞬移不得高于旧版本；关键动作应在观感上更连贯。
- 运镜：每个有运动镜头应能说清运动动机/幅度/速度、并在成片真实呈现或明确标记模型未遵守。
- 剪辑：入出点有实际素材依据；不截断台词，若动作镜头未完成则允许退回，而非强剪。
- 给每个真实样例记 1–5 级：动作/空间、摄影动机、镜间连续、声音正确、剪辑节奏；保存两位审看者或一位人工审看者的具体事件记录。**不人为预设“代码通过即艺术分数必须提升”，效果不显著则不推广到全量项目。**

### 6.3 交付验收报告应包含

`BASE_SHA / WORK_BRANCH / HEAD_SHA / changed_files / TEST_SUMMARY / REAL_H3_EVIDENCE / AUDIO_REGRESSIONS / SHOT_CUT_EVIDENCE / QC_UNAVAILABLE / KNOWN_LIMITATIONS / USER_VISIBLE_IMPACT / PR_URL`。提供至少一组“原始文学→分镜 Beat→导演/摄影→ShotSpec→六字段→真实片段→EDL”的逐段追踪例子，明确每个中间产物的 hash。

---

## 7. 分阶段实施与代码责任（按顺序走闸）

| 阶段 | 修改重点 | 必须交付的通过条件 |
|---|---|---|
| P0 基线与样本 | 固定 main SHA，官方 Skill diff、现有 H3 编译器 golden master、T01–T12 原/预期样本 | 有可回滚与防回归基线；不写业务代码也能独立审查 |
| P1 文学交接 | `director/streamline.py`/需要时 `agents/film.py` + 既有 `literary` 检索 | 确认稿优先，原文兜底，来源与版本正确，绝无重复整段投喂 |
| P2 镜头决策 | `agents/crew_prompts.py`、`film_contracts.py`、`api_film.py` | 角色站位/运动理由/静机位/精确 cut 合同经五 Agent 与静态验证 |
| P3 Ref2VA 编译 | `h3/compiler.py`，必要时 `schemas.py` 单个可选字段与现有 hash 保护 | 正确六字段、视频/音频引用语义、原生 cut、正向静音和历史版本哈希 |
| P4 现有质检/返工/剪辑 | `quality/*`、`production/episode_delivery.py`、`media/delivery_engine.py` 的**必要小范围修改** | 异常证据走原有链，真实 EDL trim，未授权音频防回归 |
| P5 真机 A/B 与收尾 | 原有 UI 下可查看来源、提示词及逐镜问题；测试、文档、PR | 工程无回归 + 已有质量事实不伪造 + 真实媒体证据 + PR 可合并 |

每完成一阶段都应产生可审查提交与已通过的最小相关测试。若 P3 的单个可选字段会显著扩大历史哈希/数据迁移风险，必须先出差异评估：可选择不扩展 schema、暂用受控适配编译策略，但**不能**为追求一次性改完而默默变更旧产物。

---

## 8. 历史事故与防复发追踪（必须逐一在 PR 验收中关闭）

| 日期/线索 | 事故与教训 | 关闭标准 |
|---|---|---|
| 2026-11-04 `ACCEPTANCE_20261104_CREW_PROMPT_REPAIR.md` | 导演提示允许重分组/加尾巴，服务端却严格校验原定切点，重复被拒 | 新导演描述不改变既有定时表；无法分组前置一次性说明 |
| 2026-11-04 `ACCEPTANCE_20261104_VOICE_AND_REF2VA.md` | 参考槽位数量/顺序、合唱 Speaker、多声部、ASR 404/VLM 超时 | Ref2VA 真绑定对应标签、音色不串人；质检不可用不假装通过 |
| 2026-11-06 `ACCEPTANCE_20261106_CROSS_SERIES_CONTINUITY.md` | 跨 batch 续接曾仅靠文字；后来补真实媒体 lineage；ASR 环境问题 | 连续段必须沿原真实派生物/sha；不能再建第二条续接 |
| 2026-11-15 `PROMPT_TUNING_20261115_NARRATION_PRIMING.md` | 列举“不要旁白、不要外语、不要哼唱”反而诱发声音 | 1.3.0 正向句式与历史 SHA 黄金样例通过；真机无新增旁白 |
| 2026-11-20 最新 main 6ca5eeb | 静音音轨仍有“original dialogue”等音效风格词诱导；已加入 sanitizer 和语言锚点 | 不回退 `non_vocal_ambience_text`；无对白中英文/外语噪声真机复测 |
| 2026-11-20 双流水线建设 | 两条流水线独立业务但基础底座复用边界清晰 | 本轮不动 `manual4` 业务及公共配置/执行裁决；双线回归照旧 |

---

## 9. 风险、兼容、回退

1. **版本身份**：任何改变 H3 prompt 字节的修复都必须升新 `COMPILER_VERSION` 并保留旧版本 dispatch 分支，不能直接改变 `1.3.0` 的历史输出；`frozen_manual_prompt` 仍由现有 `prompt_revisions` 优先级管理。
2. **一致性不是复制末帧**：要分清镜间剪辑硬切、视觉接续、相同动作换机位。仅当画面起点真实合理时才继续末帧；跨角度更多时候是动作/视线匹配而非“像素一致”。
3. **文学内容不能变旁白**：叙事说明/内心世界属于背景知识，不得默认为对白；文风模板不能转为“对白之外音轨”。
4. **VLM/ASR 风险**：跑不起来时只保留真实状态和人工审核入口，不允许另外启一个本地“检测成功”标志。
5. **耗时与显存**：Ref2VA 结构改善不等于低显存性能改善；生产没有物理真机数据时不能承诺几分钟一镜。
6. **本地 main 可能先于或异于本文基线**：开工先对 `git status`、`git rev-parse HEAD` 和 `origin/main` 做差异分析；如存在本地未提交工作，先保护和备份；禁止强制覆盖、reset、clean、rebase 丢改动。
7. **回退粒度**：保留旧 compiler、旧 ShotSpec 兼容读写、旧提示词 golden master。新镜头可切回旧兼容生成路径，但不可用旧编译器解释新字段而产生错误 SHA。
8. **运行期基础配置不是本轮交付**：不添加新的模型节点“整备成功”硬门、不要求人工重新登记已正确识别资源、不引入第二环境检查链。

---

## 10. 本地 Agent 建设实施契约

1. `git fetch origin`，记录当前 `origin/main` 与本方案基线差异；拉取并**只读**本方案 MD；本地在 **main** 的真实当前状态实施，**不要将 docs 方案分支当作代码开发分支覆盖 main**。
2. 先形成一页 `BASELINE_DIFFERENCE`：当前本地 main、远端 main 与文档写作 SHA 的文件差异，逐项判断本方案中哪些问题已被近期提交修复，**已修的不重复做**。尤其保护 `6ca5eeb` 中无对白环境声修复、阶段导航与素材映射。
3. 严格按 P0→P1→P2→P3→P4→P5 分阶段开发。更动前搜索既有对象/调用链和源头，避免重复查找器、第二审批器、第二工作流注册器。
4. 面向代码的改动集中在当前七阶段业务；测试覆盖新旧哈希与真实 Ref2VA 参考排序。必要时先小型 spike 验证 H3 编译器输出格式和本地 H3 执行，再扩大改造。
5. 全部新增“质量策略”优先是可观察、可撤销、可定向返工的建议，而非前端硬编码或新的投产强制红灯。
6. 实验性质量策略不能伪称模型成功遵守：请保留 H3 原 prompt、manifest、媒体 sha、A/B 视频与人工评审。
7. 完工先在本地 main 验收，留下审计/测试/真机素材目录或可复现说明；按项目约定创建可合并 PR，再按用户既有授权策略合入主分支及推送；如环境限制无法推送则如实报告，禁止声明已合并。
8. 最终输出：“实现点及代码链接、逐阶段测试、真实音频防回归、艺术 A/B 结果、未关闭风险、PR/merge SHA”。只在有证明时使用 `PASS`；未做为 `NOT_RUN`。

---

## 11. 建设完成的定义（DoD）

- [ ] 七阶段 UI/既有生产链不新增额外用户确认页，四阶段 H3 自主制片和共用底座没有行为回归。
- [ ] 确认场景脚本/原文的单一选择规则与来源 hash 可追溯；长文/跨集事实不丢。
- [ ] 导演与摄影的镜头语言包含可观察表演、物理空间和运镜理由，且不突破单镜时长/定时表。
- [ ] Ref2VA 六字段顺序、素材角色、真正续接与动作参考、合法对白、切镜时间线均准确。
- [ ] 旧编译器 1.2.x/1.3.0 与旧镜头/人工提示词的历史字节/哈希兼容。
- [ ] 最新无对白 ambience sanitizer、语言锚点、说话人/音色绑定与 `unexpected_speech_finding` 没有被删、绕过或改坏。
- [ ] ASR/VLM 不可用时保留可见的未完成质检事实，不产生虚假“静音已通过”。
- [ ] 同一真实片段剪辑可产生有理由的裁剪建议，最终经现有 Episode EDL/FFmpeg 交付；不虚称支持音频混音/J-cut。
- [ ] 有至少一条“文学→Ref2VA 六字段→真实 H3→自动剪辑”的可追溯测试链，含原片与优化片及独立音频听审。
- [ ] 实际修改文件、测试日志、风险清单、回滚说明及 PR 信息齐备。

**本方案只定义建设要求。仓库 docs 分支的存在，不意味着功能已开发、真实出片已达标，也不意味着可跳过用户对主分支改动的最终授权。**
