# Shine Video Studio V4 重构方案：从小说导入到 H3 拍摄提示词的端到端重构

> **重构目标**：在严格遵守 `AGENTS.md` **「一套事实、一个裁决、一个执行者」**与三条禁令的前提下，结合本地显卡（含 RTX 5090D v2）**单镜 >10s 易爆显存**的真实物理硬约束，彻底打破当前“小说原文切片 → 字符平摊时长 → 五角色串行传话 → 机械模板注水提示词”的结构性死结，建立**“戏剧化短剧改编 + 显式空间/剪辑状态机 + 确定性连续性引擎 + 瘦壳富时序 H3 2.0 编译器”**的四层全自动流水线，无缝接续既有素材中心、导演包、整备门禁与 H3 生产控制链。

## 一、大破大立：必须直接砍掉的 7 个历史包袱

| 历史机制 | 现有代码位置 | 为什么必须砍掉 | V4 替代方案 |
| :--- | :--- | :--- | :--- |
| **1. 小说原文 1200 字机械硬切直通分镜** | `director/streamline.py:748` (`segment_document(..., max_scene_chars=1200)`) & `select_unit_creative_input` | 一旦超过 1200 字就绕过场景剧本，把充满心理描写与概括叙述的小说散文直接喂给分镜编剧，天然无法成戏 | **强制前置短剧改编层**：分镜层只接收结构化 `DramaSceneScript`，彻底禁止小说原文直通分镜 |
| **2. 跨 Unit 落镜强制重置 `created = []`** | `api_film.py:720-730` & `crew_prompts.py:370` | 每换一个 1200 字切片或相邻同场景 section，首镜的 `previous_shot_id` 必然为空，强制退化为 `cut`，同场戏被切得支离破碎 | **集内全连续镜头链**：`apply_camera` 接收同集上一镜 `previous_shot_id` 与 `ShotState`；跨集首镜仍强制重置为 `cut` |
| **3. 达标即砍尾的时长闸门** | `director/streamline.py:671` (`_allocate_unit_targets`) & `:994` (`_update_fit_closed_episodes`) | 按原文字符数平摊秒数，且一旦本集已落地秒数 `>= target_seconds` 就直接跳过剩余 unit，导致剧情在半路被腰斩 | **上游控制体量、下游尊重戏剧完整**：在集纲与剧本层通过场次和字数预算控时，绝不在拆镜中途砍掉后半集 |
| **4. 对白占比 KPI 与小说模式“逐字照抄”校验** | `crew_prompts.py:302` (`dialogue_ratio_percent`) & `agents/film.py:498` (`DIALOGUE_NOT_IN_USER_SCRIPT`) | 开占比会倒逼 AI 在无台词段落硬编废话；不开占比又禁止改写书面语，导致小说独白生硬塞进镜头 | **区分双入口**：仅“导入现成标准剧本”保留逐字锁；“导入小说”走**事实守恒 + 口语戏剧化改写** |
| **5. 五角色串行传话筒（含空转的 `editor`）** | `api_film.py:generate_storyboard` & `apply_camera` | `scriptwriter → director → cinematographer → continuity → editor` 串行 5–10 次调用，彼此割裂；`EditIntentPlan` 落库时甚至一个字都没被读取 | **收敛为一体化 `SceneDirector` + 确定性规则引擎**：拆镜、站位、运镜、镜间接续、镜内切点一次联合求解 |
| **6. 无人工切点表时禁止镜内切（`cuts`）** | `crew_prompts.py:510` | 小说模式无切点表，被迫 `1 beat = 1 shot` 且禁用 `internal_cuts`，浪费了 H3 原生支持单段 8–10s 内 `[Shot 1]/[Shot 2]` 镜内切的核心能力 | **受控释放 H3 原生镜内切**：快节奏正反打优先打包为 **8–10s 内 1 次镜内切**，超过 9s 的长动作拆两段走 `last_frame` 接续 |
| **7. 机械六段套话注水（法务式提示词）** | `h3/compiler.py` (`_style_sentences` / `_cast_order` / `_shot_text`) | 提示词头部塞满 150+ 词的 Style Bible 版本号与法务免责声明，严重稀释 Qwen 文本编码器注意力，真正的**秒级动作时序、微表情变化、环境光影质感**却只有干瘪一两句 | **瘦壳富时序电影散文（Thin-Shell, Rich-Timeline Prose）**：保留唯一官方六段契约（零破坏规则包），将壳子瘦身 70%，把 90% 文本算力倾注在秒级动作链、微表情演化、物理机位与实景光影 |

## 二、V4 端到端新架构总览

新流水线将原有的“文学四阶段 + 导演七阶段拆镜”收敛为**四层强契约管道**，全部落入既有 `AgentArtifact` / `Shot` / `ShotBinding` 权威台账，全链路统一锚定 **4–10s 本地显存安全单镜上限（黄金区间 5–8s）**：

| 层级 | 模块名称 | 执行粒度 | 核心职责 | 关键产物（均落既有 `AgentArtifact` / `Shot`） |
| :--- | :--- | :--- | :--- | :--- |
| **Layer 1** | **全书总控与集纲架构师** (`SeriesArchitect`) | 全书 / 分卷 → 集 | 提取全局视觉圣经（角色/场景/道具唯一 ID 注册表），按**戏剧冲突与钩子**划分剧集与场次槽位 | `SeriesBibleV2`、`EpisodeBlueprint` |
| **Layer 2** | **短剧戏剧编剧** (`DramaScreenwriter`) | 单集 / 完整单场 | 将小说散文改编为**可拍摄的短剧剧本**：去心理独白、写潜台词口语对白（单句 ≤24 字/≤5.8s）、明确场头时空与场间转场桥接 | `DramaSceneScript` |
| **Layer 3** | **一体化视听导演 + 确定性连续性引擎** (`SceneDirector` + `ContinuityEngine`) | 完整单场（携带集内前场 `exit_state`） | 联合规划本场所有 **4–10s** 渲染段：人物站位、180° 轴线、**2–3 窗秒级动作与微表情时序链**、**物理环境与实景光影**、物理机位运镜、镜间接续与镜内切点（≤1 次） | `SceneShotBoard` → 落库为唯一 `Shot` / `ShotSpec` |
| **Layer 4** | **瘦壳富时序 H3 编译器 2.0** (`shine-h3-compiler/2.0.0`) | 单渲染段 (`Shot`) | 保持唯一 `H3PromptCompiler` 与唯一 `REF_SECTION_ORDER` 六段契约，剥离模板噪音，输出高艺术质量电影级英文提示词 | `CompiledPrompt (v2.0.0)` |

## 三、逐层详细设计与强契约 Schema

### Layer 1：全书总控与集纲架构师（`SeriesArchitect`）

**解决的核心问题**：为整部短剧建立**全局唯一的实体注册表（角色、场景、道具）**和**按戏剧节奏而非字数划分的分集蓝图**，从源头保证跨场、跨集的场景和人物统一。

```python
class LocationEntry(APIModel):
    location_id: str           # 唯一稳定 ID，如 "LOC_LIN_STUDY"
    name: str                  # 场景名，如 "林氏老宅书房"
    visual_anchor_en: str      # 英文核心空间锚点（家具布局、门窗方位、材质质感）
    practical_lighting_en: str # 实景光源与色温（如"cool blue rainlight from tall window, warm amber desk lamp"）
    atmosphere_details_en: str # 空气感与环境微动态（尘埃、雨滴、阴影、反光）
    time_variants: list[str]   # ["day", "night", "rainy_night"]

class CharacterEntry(APIModel):
    character_id: str          # 如 "CHAR_LIN_MO"
    name: str                  # 原文姓名
    english_tag: str           # 极简角色识别标签（不在正文重复重写外貌，避免与参考图打架）
    costume_lock_en: str       # 本卷/本集服装锁定（供素材生成与场记核对用）
    voice_style: str           # 声线与说话节奏特征（供编剧控句长与音效参考）
    speech_habit: str          # 口癖/语气特征

class SceneSlot(APIModel):
    scene_id: str              # 如 "EP01_SC02"
    location_id: str           # 必须引用 LocationEntry.location_id
    time_of_day: Literal["dawn", "day", "dusk", "night", "late_night"]
    source_start: int          # 原文起止偏移（用于溯源，不再硬切）
    source_end: int
    dramatic_goal: str         # 本场唯一戏剧目标（如"揭穿伪造遗嘱"）
    characters_present: list[str]
    transition_from_prev: Literal[
        "continuous_time",     # 紧接上一场同一时空（可接续或短切）
        "same_day_later",      # 同日稍后（需明确时间流逝视觉锚点）
        "time_jump",           # 跨日/跨阶段（必须设计过场镜或台词交代）
        "location_switch",     # 同时间切地点（可用声音先导或建立镜）
        "flashback_in",        # 切入回忆
        "flashback_out",       # 切回现实
    ]
    transition_hook: str       # 具体怎么从上一场过渡过来
    target_duration_s: float   # 本场建议时长（如 18–30s，按单镜 5–8s 约 3–4 镜）

class EpisodeBlueprint(APIModel):
    episode_id: str            # "EP01"
    title: str
    target_duration_s: float   # 如 75.0（按单镜均长 6–7.5s，约 10–12 镜）
    opening_hook: str          # 前 3 秒抓人画面/冲突
    climax_beat: str           # 本集核心高潮
    cliffhanger: str           # 集尾悬念钩子
    scenes: list[SceneSlot]    # 每集 3–5 场，严禁碎片化
```

* **节奏优先于字数**：一集 60–90 秒只承载 **1 个核心矛盾 + 1 次反转/升级 + 1 个结尾钩子**，对应 3–5 个场次（约 10–12 个 5–8s 镜头）；小说里拖沓的过渡章节压缩为 1 个过场建立镜（4–5 秒）或并入对白潜台词。
* **场景收敛**：单集场景数控制在 3–4 个，复用 `LocationEntry`，直接降低素材准备成本并提升视觉连贯性。

### Layer 2：短剧戏剧编剧（`DramaScreenwriter`）

```python
class ScriptActionLine(APIModel):
    beat_id: str              # 如 "EP01_SC02_B01"
    beat_type: Literal["establishing", "action", "dialogue_exchange", "reaction", "reveal", "transition_out"]
    visible_action: str       # 纯外部可见动作与微表情（禁止"他心想/她感到愤怒"）
    characters_on_screen: list[str]
    spoken_lines: list[dict]  # [{"line_id": "L1", "speaker": "林墨", "emotion": "冷笑压低声音", "text": "遗嘱上的墨迹，还没干呢。", "est_duration_s": 2.4}]
    subtext_note: str         # 潜台词与表演意图（指导导演设计微表情/眼神）

class DramaSceneScript(APIModel):
    scene_id: str
    location_id: str
    time_of_day: str
    weather_atmosphere: str
    entry_bridge: str         # 显式承接上一场 exit_state 的开场动作/声音桥梁
    beats: list[ScriptActionLine]
    exit_state: dict          # {"location_id", "characters_positions", "held_props", "emotional_tail", "unresolved_action"}
```

1. **心理描写 100% 视觉化翻译**：严禁出现“他想起”“她内心崩溃”等不可拍动词；必须改写为具体身体反应或潜台词对白。
2. **适配 ≤10s 硬上限的口语与语速约束**：
   * 单镜净台词安全窗口最多 **8.0–8.5 秒**（需留出 `0.4s` 入镜起手 + `0.6s` 表演尾韵）；
   * 每句台词严格控制在 **4–24 个汉字**（约 1.2–5.8 秒），单个 `ScriptActionLine` 总台词估算时长 **≤ 7.5 秒**；长独白/连珠炮争吵必须在剧本层拆分为带动作/反应穿插的多个短 beat；
   * 严禁说明书式台词和凑数台词。
3. **强制场间缝合**：每场第 1 个 beat 必须消化 `entry_bridge`；跨时空跳转必须安排建立镜或动作/声音承接。
4. **L0 确定性剧本门禁（零 Token）**：自然语速估算（`est_s = max(1.2, char_count / 4.2)`，单句 `> 6.5s` 或单 beat 总台词 `> 7.5s` 直接退回拆句）、心理描写黑名单词告警、角色与场景 ID 100% 命中 `SeriesBibleV2`、`production_only` 伏笔防泄密。

### Layer 3：一体化视听导演 + 显式空间/剪辑状态机（`SceneDirector`）

将原有的 `scriptwriter(拆beat) + director + cinematographer + editor` 4 次串行调用收敛为 **1 次高密度 `SceneDirector` 调用** + **唯一确定性 `ContinuityEngine` 裁决口**。

```python
class CharacterPlacement(APIModel):
    character_id: str
    frame_zone: Literal["left_fg", "center_fg", "right_fg", "left_mg", "center_mg", "right_mg", "left_bg", "center_bg", "right_bg"]
    facing: Literal["facing_camera", "facing_left", "facing_right", "three_quarter_left", "three_quarter_right", "back_to_camera"]
    posture: str
    props_in_hand: list[str]

class SpatialBlocking(APIModel):
    location_id: str          # 同场强约束与 SceneScript.location_id 一致
    axis_line: str            # 180° 轴线锁定标签
    placements: list[CharacterPlacement]

class TemporalActionBeat(APIModel):
    """每镜 2–3 个物理动作 + 微表情演进节点（每窗 2.0–3.5s），驱动 H3 2.0 电影级时序正文。"""
    start_s: float
    end_s: float
    physical_action_en: str   # 起手 -> 受力/接触 -> 惯性回收，禁止空洞形容词
    facial_expression_en: str # 眉眼、瞳孔、嘴角、下颌、呼吸停顿的逐秒演化
    camera_behavior_en: str   # 物理机位 + 动作触发式微动/稳持
    dialogue_line_ids: list[str] = Field(default_factory=list)

class EnvironmentAtmosphere(APIModel):
    spatial_depth_en: str     # 前景/中景/背景层次与材质质感
    practical_light_en: str   # 实景光源位置、色温对比、受光面与投影方向
    micro_motion_en: str      # 雨滴、尘埃、光影轻晃、衣摆微动等环境动态

class BoundaryDecision(APIModel):
    mode: Literal["cut", "last_frame", "video_reference"]
    motivation: Literal[
        "continuous_action",  # 同场景连续动作 -> 强制 last_frame（仅在 VHS_LoadVideo 就绪时可选 video_reference）
        "emotional_hold",     # 同一景别/缓慢推进情绪延续 -> 强制 last_frame
        "match_on_action",    # 动作匹配切景别 -> cut + 明确交接动作
        "shot_reverse_shot",  # 同场正反打 -> cut + 强制锁同 location_id 与 180° 轴线
        "eyeline_match",      # 视线匹配切特写/插入镜 -> cut + 保持视线方向一致
        "establishing_reset", # 新场次/新集首镜建立镜 -> 强制 cut
        "time_or_space_jump", # 时空跳转 -> 强制 cut
    ]
    handoff_cue_en: str       # 直接写入 ShotSpec.continuity.notes 并编译进 H3

class InternalSubShot(APIModel):
    start_s: float
    beat_id: str
    shot_size: str
    physical_camera_en: str
    blocking_delta_en: str
    timeline: list[TemporalActionBeat]

class DirectedShot(APIModel):
    shot_key: str
    beat_ids: list[str]
    duration_s: float = Field(ge=4.0, le=10.0)  # V4 默认安全硬上限：4–10s（黄金区间 5–8s）
    boundary: BoundaryDecision
    blocking: SpatialBlocking
    environment: EnvironmentAtmosphere
    entry_state_en: str
    exit_state_en: str
    timeline: list[TemporalActionBeat]          # 2–3 个时间窗节点，避免单镜过载
    sound_ambience_en: str
    music_cue_en: str
    dialogue: list[DialogueLine]
    internal_cuts: list[InternalSubShot] = Field(default_factory=list, max_length=1)  # ≤10s 内最多 1 次镜内切
    character_ids: list[str]  # 按画面首次出现严格排序
    prop_ids: list[str]       # 仅核心道具
```

#### 剪辑决策铁律（适配 ≤10s 显存安全上限）

| 戏剧场景 | 正确拍法 | 原因与效果 |
| :--- | :--- | :--- |
| **单主动作 / 单人情绪台词** | **1 个 5–8 秒渲染段**（`internal_cuts = []`），拆 2–3 个时间窗描写动作与微表情 | 显存余量最安全、H3 画面稳定性最高、天然契合短剧快节奏 |
| **同场快节奏双人正反打**（A 说 4s → B 反应/回怼 4–5s） | 合并为 **1 个 8–10 秒渲染段（严格 ≤ 10.0s）**，仅做 **1 次镜内切**（`[Shot 1]` + `[Shot 2]`） | 同次采样共享潜空间，轴线/光影/服装高度一致，且严禁 10 秒内塞 3 个子镜造成走马灯 |
| **连续动作或单场对峙超过 8–9 秒** | **主动拆成 2 个 5–7 秒渲染段**，第 2 段设 `boundary.mode = "last_frame"` | 把单次显存峰值切半，同时靠 2.0 编译器的 `exit_state_en` 定格 + 末帧图接续实现丝滑长镜头效果 |
| **同场大景别切换 / 道具特写插入** | 外部 `boundary.mode = "cut"` + 动作/视线匹配 + **强制绑定同一 `location_id` 场景主图** | 防止强行用 `last_frame` 导致画面变形，同时锁死空间不穿帮 |
| **跨场次 / 跨时间 / 换地点 / 新集第 1 镜** | 外部 `boundary.mode = "cut"` + `establishing_reset`，严禁 `last_frame` | 杜绝背景残影污染新场景，并严格满足跨集独立调度与交付边界 |

### Layer 4：瘦壳富时序 H3 编译器 2.0（`shine-h3-compiler/2.0.0`）

严格遵守 **`H3PromptCompiler` 唯一输出口**与 **`h3/rulebook.py` 唯一规则裁决口**（不另造第二套无壳编译器，不破坏 `H3_SECTIONS` 官方六段契约与历史版本 `1.2.1`–`1.4.0` 字节重放）：

1. **极简壳瘦身 70%（零法务免责声明噪音）**：`subject_definitions` / `summary` / `retention_analysis` 每条精简至 1 句明确分权绑定；彻底删除 `Visual style constraints from the frozen Style Bible (version ...)`、`Story continuity facts below are silent background context...` 等污染前部注意力的元指令套话。
2. **参考图“零外貌重写”纪律**：人物参考图已锁脸与服装，正文中严禁反复重写长相/衣着细节，把 90% 文本算力倾注在**秒级动作链、微表情演化、实景光影环境与物理机位**。
3. **显式秒级时序动作链 + 微表情演化**：将 2–3 个 `TemporalActionBeat` 织成流畅的英文电影散文（`From 0.0s to 2.2s, ... From 2.2s to 4.8s, ...`），并将 `<Subject N>` 空间站位自然锚定在首句、将 `exit_state_en` 锚定在尾句（为下一镜 `last_frame` 提供稳定定格）。
4. **修复原生多镜段 `[Shot N]` 对白归位 Bug**：按 `internal_cuts` 时间区间将每句 `<d>[Chinese] ...</d>` 精准归属到对应 `[Shot N]` 块内，不再全部堆在 `[Shot 1]`。
5. **正向声音合同受保护继承**：完整保留 1.3.0/1.4.0 已验证的静音镜头正向非人声合同（`non_vocal_ambience_text` 与语言世界锚点），防止幻觉旁白回归。

---

## 五、三原则合规审计与全链路接续架构

本节严格对照 `AGENTS.md` 最高原则（**一套事实、一个裁决、一个执行者**及三条禁令）、`SHINE_SETUP_R5_THREE_PRINCIPLES_CONVERGENCE_PLAN.md`、`SHINE_FOUNDATION_BOUNDARIES_AND_CALL_GRAPH.md` 与 `SHINE_MAINLINE_V3_MODULE_OWNERSHIP.md`，逐一落实 V4 重构与所有关联模块的链接点、整备边界、≤10s 显存安全门禁和 H3 控制链接续。

### 5.1 三原则合规映射表（零平行表、零双裁决、零双执行者）

| 原则 / 禁令 | V4 重构必须守住的红线 | 具体落位方式 |
| :--- | :--- | :--- |
| **一套事实**（禁止禁令 2：不新增同步状态/平行表） | 不新建 `drama_scripts`、`shot_states` 等平行数据库表；不维护第二套镜头完成态、素材库或时长上限字段 | 文学与分镜工件全部作为版本化 `AgentArtifact` 写入既有账本；镜头唯一权威事实仍是 `Shot` + `ShotBinding`；单镜时长上限唯一事实仍是冻结在 `StyleVersion.snapshot` 中的 `max_shot_seconds`；七阶段进度唯一读投影仍是 `director/journey.py:compute_journey` |
| **一个裁决**（禁止禁令 1：不用新增检查修复检查过多） | 不在 `continuity_supervisor` 之上叠第二层检查；不另造绕过 `h3/rulebook.py` 的提示词模式 | `DeterministicContinuityEngine` **替换收敛**为唯一的分镜连续性裁决口；`h3/mode_policy.py`（Ref2VA only）、`h3/rulebook.py`（`H3_SECTIONS` 等官方规则）、`package.py:package_readiness`、`production_mode.machine_verdict` 保持全仓唯一 |
| **一个执行者**（禁止禁令 3：不另开新入口绕过旧入口） | 不新建第二套文学/分镜 Worker 队列；不在前端开第二个“V4 旁路工作台” | V4 文学任务复用既有 `worker.py` 中的 `run_pending_pipeline_task`，V4 分镜任务复用既有 `run_pending_director_storyboard`；保留既有 `state_version` 乐观锁、`lease_owner`、`DRAFT → SENDING → RESPONSE` 不可盲目重放守卫与 `text_consent` 远端文本授权指纹 |

### 5.2 六大关联模块链接点与接续清单

| 关联模块 | 现有权威入口 | V4 链接点与接续改造 | 明确不触碰的边界 |
| :--- | :--- | :--- | :--- |
| **1. 公共素材中心 & 自动备料** | `director/streamline.py:derive_shot_materials`、`asset_center/`、`api_film.py:apply_camera` | `SeriesBibleV2` 的 `CharacterEntry` / `LocationEntry` / `PropEntry` 在落镜时直接写入既有的 `ShotSpec.characters` 与 `ShotSpec.requested_assets(family="character"|"scene"|"prop")`；已锁定的场景主图通过 `complete_location_binding` 自动写入 `ShotBinding(use_as="picture", role="location_master")`；**AI 绝不静默晋升或锁定主素材**，仍由用户在阶段 4 确认晋升 | 不改 `asset_center` 公共表结构与 `foundation/materials.py` 跨线门面；不绕过候选资产人工晋升门禁 |
| **2. 导演包 & 谱系过期裁决** | `director/package.py:build_package`、`package_readiness`、`director/journey.py` | 将 `SeriesBibleV2`、`EpisodeBlueprint`、`DramaSceneScript` 的 `(artifact_id, sha256)` 纳入 `creative_lineage` 哈希计算，上游剧本修订后自动触发既有 `mark_package_stale`；`package_readiness` 复用同一单镜上限事实拦截 `> 10.0s` 爆显存风险镜，并校验 `ShotBinding` 完整度与场景主图缺口 | 不建第二套包冻结表；不改 `journey.py` 七阶段定义与 `package_id / run_id` scope 语义 |
| **3. 整备要求 & 可选能力门禁** | `strategy.py:optional_capabilities_overview`、`shot_reference_capability_blockers`、`comfyops/setup_preparation.py` | **零新增整备要求**：`SceneDirector` 默认只生成 `cut`、`last_frame`（静态末帧图，零节点增量）与 `internal_cuts`；**仅当** `optional_capabilities_overview(db, settings)["video_reference"]["available"] is True` 时才允许提案 `video_reference`，避免在未装 `VHS_LoadVideo` 的机器上触发 `VIDEO_REFERENCE_BACKEND_UNAVAILABLE` 阻塞 | 不改 `setup_preparation` 唯一整备链、不新增下载项、不改 `180 GiB` 容量下限与 7 级证据阶梯 |
| **4. H3 渲染控制链 & GPU 仲裁** | `h3/mode_policy.py` → `services.py:make_render_request` → `comfy/graph_compiler.py` → `comfyops/production_series.py` → `platform_gpu.py` | ① **Ref2VA Only 守恒**：空镜/纯环境建立镜强制绑定对应 `LocationEntry` 的 `location_master` 场景图，确保每镜 `len(manifest) >= 1`，绝不触发 `PROJECT_REQUIRES_REF2VA`；<br>② **槽位上限与确定性排序**：单镜 `<= 9` 图、`<= 12` 总参考，`order_bindings_for_cast` 保持 `Subject(1..N)` → `location_master` → `prop` → `continuity_derivative(last_frame)` 固定序；<br>③ **集内串行接续 + 跨集重置**：`apply_camera` 打通同集跨 unit 的 `previous_shot_id`，每集第 1 镜强制 `mode="cut"`、`previous_shot_id=None`；下游 `production_series.py:725-894` 原生解析 `previous_shot_id → previous_job_id` 并抽取末帧图，单租约串行续拍无缝接住；<br>④ **版本防漂移**：`h3/compiler.py` 升版至 `2.0.0`，`rulebook.py:recompile_for_provenance` 按 pinned version 字节级重放历史 `1.2.1`–`1.4.0` | 不改 `graph_compiler.py` 动态图编译契约、不改 `ResourceScheduler` 与 `Global\ShineGpuArbitrationMutex` GPU 仲裁、不改事务外提交与崩溃对账逻辑 |
| **5. 质检、返工与后期交付** | `quality/gates.py`、`quality/repair.py`、`media/delivery_engine.py` | 保持 5 道质检门与“咨询审片永不阻塞主链”不变；集内 `previous_shot_id` 链天然通过 `delivery_engine.py:77` 连续性核验；上游重拍仍沿用既有依赖失效（downstream `stale`）机制，绝不覆盖已 `PASSED` 版本。**注意 `client_review_identity` 边界**：重构尽量不改动 `worker.py` / `preproduction.py` / `quality/` 哈希钉住文件，避免无谓触发既有 VLM 校准失效 | 不改 `delivery_engine` 唯一 EDL/FFmpeg 装配器；不改 `acceptance_status` 人工终审语义 |
| **6. `manual4` 双线隔离边界** | `backend/shine/manual4/*`、`tests/manual4/test_isolation_static.py` | V4 新增模块严格限定在 `director7` 业务线内，不改动 `manual4/*`；若 `manual4` 也需要 ≤10s 安全上限，通过公共常量 `SAFE_LOCAL_MAX_SHOT_SECONDS` 单源读取 | 确保 `test_isolation_static.py` AST 隔离检查与 `manual4Routing.test.tsx` 100% 通过 |

### 5.3 单镜 ≤10s 显存安全硬门槛：唯一事实与全链路收紧策略

针对本地所有显卡（含 RTX 5090D v2）渲染 >10s 单镜易触发 CUDA OOM 的现实物理瓶颈，按**「区分协议上限与本地安全上限、不新增第二套时长控制器」**的原则统一收紧：

1. **严格区分两个常量，保护历史数据零破坏**：
   * `H3_PROTOCOL_MAX_SHOT_SECONDS = 15.0`：保留在 `ShotSpec`（`le=15`）、`h3/compiler.py`（`MAX_MEDIA_DURATION_S = 15.0`）与 `h3/rulebook.py`（`H3_LIMITS`）中，确保历史已存的 11–15s 镜头、老导演包和黄金重放测试 100% 不破坏；
   * `LOCAL_VRAM_SAFE_MAX_SHOT_SECONDS = 10.0`：作为本地 ComfyUI Ref2VA 生产的**唯一安全硬上限常量**（定义在 `foundation/h3.py` 或 `h3/mode_policy.py` 一处，全仓导入引用，禁止散落硬编码）。
2. **收紧唯一权威事实 `StyleBible.max_shot_seconds`**：
   * 新建项目/新冻结风格（`api_streamline.py`、`director/streamline.py`）的 `max_shot_seconds` **默认值从 `15.0` 改为 `10.0`，输入上限校验从 `le=15.0` 收紧为 `le=10.0`**（前端滑块/选择器同步读后端上限，不另写死 15）；
   * 人工编辑单镜时长接口（`api_streamline.py:288`）与 `editorial_timing_preview(..., max_segment_s=...)` 自动继承 `min(style.max_shot_seconds or 10.0, 10.0)`，切点分组算法自动把所有渲染段压在 `[4.0, 10.0]` 秒内。
3. **在唯一渲染准入口设置 OOM 事前拦截（`package_readiness` + `make_render_request`）**：
   * 对于老项目里历史遗留的 `10.0 < duration_target_s <= 15.0` 镜头，在进入本地 ComfyUI 渲染前由既有唯一裁决口（`package.py:package_readiness` 与 `services.py:make_render_request`）具名拦截：`SHOT_DURATION_EXCEEDS_LOCAL_VRAM_LIMIT`（提示用户将其拆镜或调至 ≤10s），绝不等送进 ComfyUI 跑几分钟后爆显存崩进程。
4. **上下游节奏联动重算**：
   * 编剧层：单句 ≤24 字（≤5.8s）、单 beat 总台词 ≤7.5s；
   * 导演层：常规单镜 5–8s，双人正反打镜内切 8–10s（最多 1 次镜内切），超过 8–9s 的长动作拆两段走 `last_frame` 接续；
   * 表演时序层：每镜 2–3 个 `TemporalActionBeat`（每窗 2.0–3.5s），兼顾画面稳定与电影级表演密度。

### 5.4 关键风险与防回归门禁

1. **`ShotSpec` 序列化零漂移**：新增可选字段在 `ShotSpec._omit_empty_optional_lists` 中若为空则完全省略，确保历史镜头快照与 `manifest_sha256` 逐字节不变。
2. **LLM 角色路由无缝承接**：`SeriesArchitect` / `DramaScreenwriter` 复用既有 `scriptwriter` 路由，`SceneDirector` 复用既有 `director` 路由，升级后无需用户重配任何模型路由或重新授权。
3. **回归验收命令矩阵**（遵守 `AGENTS.md` 固定规则）：
   * 专项回归：`test_film_crew.py`、`test_h3_compiler.py`、`test_shot_continuity.py`、`test_duration_target_chain.py`、`test_shot_ceiling_and_frame.py`、`test_director_streamline.py`、`test_production_series.py`、`test_offline_e2e.py`、`tests/manual4/test_isolation_static.py`；
   * 全量后端回归：8 分片并行（`CODEBUDDY_SAFE_DELETE_ENABLED=0`，按文件 `split -n r/8`，8 分片全部 exit 0 且无 `FAILED/ERROR`）；
   * 前端回归：`npx vitest run --no-file-parallelism` + `npm run build`。

---

## 六、代码库精确改动清单与分阶段落地路线

### Phase 1：止血、≤10s 显存硬上限收紧、打通集内断链与上线 H3 2.0 编译器

1. **`backend/shine/h3/mode_policy.py`（或 `foundation/h3.py`）**：定义唯一常量 `LOCAL_VRAM_SAFE_MAX_SHOT_SECONDS = 10.0`；在 `api_streamline.py`、`director/streamline.py`、`editorial_timing.py`、`package.py:package_readiness`、`services.py:make_render_request` 统一引用，保留 `ShotSpec` 历史 `le=15` 重放兼容。
2. **`backend/shine/schemas.py`**：为 `ShotSpec` 增加可选字段 `blocking`、`environment`、`timeline`、`entry_state`、`exit_state`、`location_id`、`transition_motivation`（空值省略，历史 hash 零漂移）。
3. **`backend/shine/api_film.py:apply_camera`**：
   * 增加 `previous_shot_id: str | None = None` 入参（仅同集内传递，跨集首镜置 `None`）；
   * 将 `intent["continuity"]["note"]` 写入 `ShotSpec.continuity.notes`；
   * 新增 `complete_location_binding` 补齐已锁定场景主图（未锁定则写入 `requested_assets(family="scene")`）；
   * 移除无提示的台词时间戳等比压缩逻辑。
4. **`backend/shine/h3/compiler.py` & `rulebook.py`**：
   * 新增 `COMPILER_VERSION = "shine-h3-compiler/2.0.0"`（瘦壳六段 + 2–3 窗富时序动作/微表情/实景光影 + `[Shot N]` 对白精准归位）；
   * `rulebook.py` 保持唯一 `REF_SECTION_ORDER` 裁决与 `1.2.1`–`1.4.0` 完整历史重放。

### Phase 2：重构文学改编层（复用 `run_pending_pipeline_task` 唯一执行链）

1. **新增 `backend/shine/literary/drama_engine.py`**：实现 `SeriesArchitect`（复用 `scriptwriter` 路由）与 `DramaScreenwriter`（落实单句 ≤24 字、单 beat ≤7.5s 硬约束），产物写入既有 `AgentArtifact`。
2. **升级 `backend/shine/literary/qa.py`**：新增 L0 戏剧质检（自然语速与 ≤7.5s 节拍预算、心理独白黑名单、角色/场景注册表覆盖、场间衔接）。
3. **改造 `backend/shine/director/streamline.py` & `package.py`**：移除 1200 字硬切与 `_update_fit_closed_episodes` 砍尾；将 `SeriesBibleV2` / `EpisodeBlueprint` / `DramaSceneScript` 接入 `creative_lineage`。

### Phase 3：收敛为一体化 `SceneDirector` + 唯一确定性 `ContinuityEngine`

1. **新增 `backend/shine/agents/scene_director.py` & `continuity_engine.py`**：复用 `director` 路由与 `run_pending_director_storyboard` 唯一执行链；默认 `duration_s` 锁死 `[4.0, 10.0]`、镜内切最多 1 次、>8–9s 连续动作自动拆两段走 `last_frame`；按 `optional_capabilities_overview` 自动约束 `video_reference`。
2. **精简 `backend/shine/agents/crew_prompts.py` & `film_contracts.py`**：废弃 `dialogue_ratio_percent` 硬凑指令与无切点表禁用 `cuts` 规则。

### Phase 4：端到端对照验收

用同一段 3000–5000 字小说章节在本地 H3 上做 V3（`1.4.0`）vs V4（`2.0.0`）实机对照：0 次 >10s 爆显存失败、同场 `last_frame` / 镜内切命中率、台词自然语速与留白达标率、表演/微表情/实景光影密度、同场场景主图锁定率、零集尾腰斩，以及每场 LLM 调用从 5–10 次降至 1–2 次。