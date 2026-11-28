# Shine Video Studio — H3 演技驱动升级与时长控制简化建设方案（V1）

> **文档编号**：`PLAN_20261127_H3_ACTING_DIRECTOR_V1`
> **基线日期**：2026-11-27（基于当前 `main` 分支与远端仓 `anjingdtl/shine-video-studio` 基线）
> **架构遵循**：`AGENTS.md` 核心原则「一套事实、一个裁决、一个执行者」及三条禁令
> **并发开发约束**：本方案实施期间**严格保留开工前工作区与远端/本地已更新的项目进程（尤其是并发开发中的 AI 管家 / `copilot` 能力）**，严禁覆盖、回退或触碰非重叠模块。

---

## 一、背景与现状诊断

当前 Shine 在真实 MiniMax-H3 出片中最大的短板是**人物演技粗糙、表演割裂、念台词感强**。经核查仓库核心代码，根因集中在 4 个结构性瓶颈：

1. **上游表演描述被字数预算“饿死”**
   - `backend/shine/agents/scene_director.py` 为了在单次 JSON 中输出整场 6–12 个镜头，强制将每镜压缩在 `1200–1600` 字符、仅 `2` 个 `timeline` 窗口、每个 `expression` 仅 `6–10 words`。LLM 只能输出 `looks angry` 这类抽象情绪标签，无法展开五官肌肉变化与递进层次。
   - 既有表演技能 `config/cinema_skills/observable-action-performance/SKILL.md` 仅挂载在旧 5-role 链路 `crew_prompts.py`，V4 主链 `scene_director` 并未接入。
2. **台词缺少声学表演维度**
   - `backend/shine/schemas.py` 中的 `DialogueLine` 仅有 `speaker_id / text / start_s / end_s`。
   - `backend/shine/h3/compiler.py` 的 `_dialogue_sentence()` 将所有台词编译为平铺的 `At MM:SS.mmm, <Subject 1> (S1) says: <d>[Chinese] ...</d>`，缺乏音量、语速、音质、音调驱动，导致音画联合模型退回均值化朗读。
3. **编译器将“微表情蓄力”与“台词发声”物理拆散**
   - `shine-h3-compiler/2.3.0` 的 `_shot_text_thin()` 先集中输出全部 `timeline`，中间插入大段 `environment`（`Space / Light / Air`）和 `Background story facts`，最后才集中输出全部 `dialogue`。台词前 1 秒的微表情阶梯与紧接的台词在文本序列中被隔开上百个 token，削弱了 H3 的时序对齐。
4. **单镜手动时长与过紧的整集时长控制反向挤压演技空间**
   - 前端 `StreamlinePanels.tsx` 暴露了“每镜最大秒数（`maxShotSeconds`）”手动输入框，一旦用户设小（如 5–6 秒），1 秒空拍与台词根本放不下；同时 `scene_director` 紧盯 `scene_budget_seconds` 凑整秒，容易在情绪高潮处拦腰切断或压扁空拍。
   - 此外，单镜 10 秒是本地 VRAM 防 OOM 与出片速度的硬件红线，但 1 分钟以上的同场景对手戏需要**跨镜头的大尺度情绪统筹**，否则每一镜都从零起跳，表演支离破碎。

---

## 二、核心目标与 `AGENTS.md` 合规审计

### 2.1 六项核心目标

1. **情绪词解剖化**：禁止孤立抽象情绪词，强制拆解为可拍摄的五官肌肉动作（眉弓/眉心、上下睑/瞳孔视线、鼻翼/颧肌、唇线/嘴角/咬肌与吞咽呼吸）。
2. **四维声学受控词表**：每句台词前标明受控英文枚举的 **音量（volume）、语速（pace）、音质（voice_quality）、音调（pitch）**。
3. **自适应 1 秒空拍情绪阶梯 + 交错编织**：按镜头类型自适应预留空拍（特写/近景/情绪转折镜 0.8–1.2s，紧凑对切 0.3–0.5s，动作承接/无余量镜 0s），在 `shine-h3-compiler/2.4.0` 中将静态环境前置，按时间轴交错输出 `[空拍五官肌肉阶梯] → [四维声学 + <d>台词</d>] → [句后余韵]`。
4. **大尺度表演统筹（2–4 镜表演组 + 组间情绪接力）**：在 `DramaSceneScript` 标记整场轻量张力曲线，确定性聚合 2–4 镜连续表演段落组，由方案 B 的轻量 Acting Pass 同窗统筹并传递 `carry_over_state`，让 1 分钟长对手戏呈现连贯情绪弧线。
5. **宽容、非阻塞的 JSON 控制链**：LLM 只输出极简 `ActingPatch` 增量（不复述台词与骨架），时间轴由服务器确定性计算，受控词表经 `crew_salvage.py` 同义词折叠与单维软降级，单镜失败最多重试 1 次后自动降级回 `scene_director` 原稿，**绝不阻滞拍摄流程**。
6. **时长控制简化**：前端取消用户手动“每镜 N 秒”控制，固化 `10.0s` 为系统硬件红线（由导演在 4–10s 内按表演自由分配）；仅保留“拍几集”和“每集目标秒数”，并将每集时长收敛为 **±10 秒软弹性窗口**。

### 2.2 `AGENTS.md`「一套事实、一个裁决、一个执行者」审计表

| 架构维度 | 唯一落点 | 合规说明（严守三条禁令） |
| :--- | :--- | :--- |
| **一套事实** | `ShotSpec`（`timeline` + `dialogue[].delivery`）与 `DramaSceneScript`（`beats[].beat_energy`） | Acting Pass 只产出增量补丁并合入同一份 `ShotSpec`，不新建平行演技表或第二份提示词副本。 |
| **一个裁决** | `backend/shine/agents/continuity_engine.py` + `backend/shine/director/contracts.py` | 不新增平行门禁；抽象情绪词提示作为轻量 `warning` 挂入现有裁决，每集 ±10s 容差复用 `duration_target_verdict()`。 |
| **一个执行者** | `H3PromptCompiler`（升级至 `shine-h3-compiler/2.4.0`） | 唯一 H3 提示词出口；`2.3.0` 及以前版本归入 `LEGACY_230_VERSIONS` 保持字节不变，确保历史请求可精确回放。 |

---

## 三、并发开发隔离与开工前保护规则（硬约束）

用户正在同一仓库同步开发 **AI 管家（`copilot`）能力**，与本方案无模块重叠。Antigravity 在执行任何阶段前必须严格遵守：

1. **禁止任何破坏性 Git 操作**：严禁执行 `git reset --hard`、`git checkout -- .`、`git clean -fd` 或强制覆盖分支。
2. **开工前先盘点并发基线**：运行 `git status --short` 与 `git log -n 5 --oneline`，记录已存在的修改/未跟踪文件（尤其是 `backend/shine/copilot/`、`frontend/src/copilot/`、`backend/scripts/generate_copilot_*` 等），全程**不得修改、格式化或回退这些文件**。
3. **增量精准修改**：只修改本方案第六章列出的受影响文件；若遇到与最新远端/本地提交的上下文行号偏移，按语义精准合并，保留既有新增进程。
4. **日常工作台启动配置交还（遵循 `AGENTS.md` 2026-11-27 裁定）**：
   - 开工前留存日常数据目录、SQLite 路径、`.env` 原始字节与 SHA-256；
   - 隔离验收统一使用 `start_shine.ps1 -DataDir <隔离目录>` 与非日常端口；
   - 收尾按 `stop_shine.py --data-dir` 停机并恢复 `.env` 至开工快照，再用 `启动 Shine 工作台.cmd` 复验原日常作品可见。

---

## 四、Phase 1 详细设计：时长控制简化 + 四维声学契约 + `H3PromptCompiler 2.4.0`

Phase 1 可完全独立落地并通过全量离线回归，不引入新增 LLM 调用，先打通数据契约、时长弹性与 2.4.0 编译器底座。

### 4.1 时长控制简化（前端去每镜控制 + 10s 红线 + 每集 ±10s 软弹性）

| 改造点 | 精确文件 | 变更内容 |
| :--- | :--- | :--- |
| **前端分镜书面板** | `frontend/src/components/StreamlinePanels.tsx` | 移除用户手动“每镜最大秒数（`maxShotSeconds`）”输入控件；仅保留 **拍几集（`episode_count`）** 与 **每集目标秒数（`target_seconds`）**。展示一行只读说明：“单镜由导演按表演在 4–10 秒内自动分配（10 秒为显存安全红线），每集允许 ±10 秒自然弹性。”提交 `generateStoryboardBook` 时固定传 `tolerance_seconds: 10`（移除原 `Math.round(targetSeconds * 0.25)` 动态缩放）且不再要求用户传 `max_shot_seconds`（默认后端 `10.0`）。 |
| **后端默认容差与红线** | `backend/shine/director/contracts.py`<br>`backend/shine/api_streamline.py`<br>`backend/shine/schemas.py` | 1. 将 `DEFAULT_TOLERANCE_SECONDS` 由 `15` 收敛为 `10`（`api_streamline.py` 中 `StoryboardBookBody.tolerance_seconds` 默认值同步为 `10`）。<br>2. 保持 `DEFAULT_MAX_SHOT_SECONDS = 10.0` 为系统唯一单镜上限；`StoryboardBookBody.max_shot_seconds` 保留为可选兼容字段（默认 `10.0`，`ge=4.0, le=10.0`），确保旧客户端与历史冻结项目完全兼容。<br>3. `duration_target_verdict()` 在 `[max(4.0, target - 10), target + 10]` 内返回 `None`；超出窗口仅作软提醒（可一键确认），绝不截断台词或硬阻滞。 |
| **导演提示词预算软化** | `backend/shine/agents/scene_director.py` | 在 `scene_director_prompt()` 中将 `scene_budget_seconds` 明确表述为**软目标（含 ±10 秒弹性）**：允许导演在 4–10 秒单镜红线内按戏剧张力自由分配镜头长度（快对切 4–6s，重头戏/空拍情绪转折 7–10s），优先保护表演完整与空拍，不为了凑整秒而切碎表演。 |

### 4.2 数据契约扩展：四维声学受控词表（`backend/shine/schemas.py`）

新增 `VocalDelivery(APIModel)` 作为 `DialogueLine` 的可选字段，**所有字段默认 `None`，为空时在序列化中完全省略**，确保历史 `ShotSpec` 字节和 SHA-256 零漂移：

```python
VolumeLevel = Literal["whispered", "hushed", "soft", "conversational", "raised", "shouting"]
SpeechPace = Literal["halting", "slow", "measured", "brisk", "rushed"]
VoiceQuality = Literal["breathy", "hoarse", "trembling", "crisp", "strained", "warm", "flat"]
PitchContour = Literal["low_flat", "steady", "rising", "falling", "cracking"]

class VocalDelivery(APIModel):
    volume: VolumeLevel | None = None
    pace: SpeechPace | None = None
    voice_quality: VoiceQuality | None = None
    pitch: PitchContour | None = None
```

| 维度 | 受控枚举值 | 编译输出短语映射（H3 英文自然描述） |
| :--- | :--- | :--- |
| **音量 `volume`** | `whispered` / `hushed` / `soft` / `conversational` / `raised` / `shouting` | `whispered` / `hushed` / `soft` / `conversational` / `raised` / `shouting` |
| **语速 `pace`** | `halting` / `slow` / `measured` / `brisk` / `rushed` | `halting` / `slow` / `measured` / `brisk` / `rushed` |
| **音质 `voice_quality`** | `breathy` / `hoarse` / `trembling` / `crisp` / `strained` / `warm` / `flat` | `breathy` / `hoarse` / `trembling` / `crisp` / `strained` / `warm` / `flat` |
| **音调 `pitch`** | `low_flat` / `steady` / `rising` / `falling` / `cracking` | `a low, flat pitch` / `a steady pitch` / `a rising pitch` / `a falling pitch` / `a cracking pitch` |

`DialogueLine` 扩展方式：
* 新增 `delivery: VocalDelivery | None = None`。
* 增加 `@model_serializer(mode="wrap")`：当 `delivery` 为 `None` 或其 4 个子字段全为 `None` 时，`data.pop("delivery", None)`。
* 现有 `DialogueLine` 所有历史单测和已冻结快照保持 **100% 字节一致**。

### 4.3 `crew_salvage.py` 宽松接收与同义词折叠表

在 `backend/shine/agents/crew_salvage.py` 新增确定性归一化函数 `normalize_vocal_delivery(raw: dict[str, Any]) -> tuple[dict[str, str] | None, list[str]]`：

1. **格式规范化**：去首尾空格、转小写、将连字符/空格统一替换为下划线（例如 `"Low-Flat"` / `"low flat"` → `"low_flat"`）。
2. **字段别名映射**：支持 `speed / rate / tempo → pace`、`timbre / quality / tone_quality → voice_quality`、`intonation / tone → pitch`、`loudness → volume`。
3. **确定性同义词折叠（示例）**：
   - `volume`：`quiet / low / muted / murmured → hushed`；`gentle → soft`；`normal / medium → conversational`；`loud / sharp → raised`；`yelling / screaming → shouting`。
   - `pace`：`hesitant / stammering / broken → halting`；`deliberate / unhurried → slow`；`steady / even / moderate → measured`；`fast / quick / urgent → brisk`；`frantic / breathless → rushed`。
   - `voice_quality`：`airy / sighing → breathy`；`raspy / gravelly / rough / husky → hoarse`；`shaky / quivering / wavering → trembling`；`clear / sharp / clipped → crisp`；`tight / tense / choked → strained`；`soft_warm / gentle → warm`；`dry / deadpan / numb → flat`。
   - `pitch`：`low / monotone / flat / deadpan → low_flat`；`level / even → steady`；`high / upward / questioning → rising`；`dropping / downward / sinking → falling`；`breaking / strained_high → cracking`。
4. **单维软降级**：无法映射的单个维度仅置为 `None` 并记录 `salvage_note`，**绝不触发 `ValidationError` 拒绝整句或整镜**。

### 4.4 `H3PromptCompiler` 升级至 `shine-h3-compiler/2.4.0`

修改 `backend/shine/h3/compiler.py`、`backend/shine/h3/spec_review.py`、`backend/shine/h3/rulebook.py`：

1. **版本冻结与回放零漂移**：
   - 新增 `LEGACY_HANDOFF_V23_COMPILER_VERSION = "shine-h3-compiler/2.3.0"` 与 `LEGACY_230_VERSIONS = (*LEGACY_220_VERSIONS, LEGACY_HANDOFF_V23_COMPILER_VERSION)`。
   - 更新 `COMPILER_VERSION = "shine-h3-compiler/2.4.0"`，并在 `_Context` 增加 `acting_v24: bool = False`（`compiler_version not in LEGACY_230_VERSIONS`）。
   - `2.3.0` 及以前版本输出**逐字节不变**，`recompile_for_provenance()` 全部通过。
2. **`_shot_text_thin()` 2.4.0 结构重排（静态环境前置 + 表演与台词交错编织）**：
   - 每个 `[Shot N]` 块内，先输出固定上下文：`style`、`cast_order`、`performance_sentence`、`top_last_frame_continuity`、`placement`、`entry_state`、`story_beat`、`camera_narrative`、`environment`（`The scene is set in ...` + `Space / Light / Air`）、`scoped_story_facts`、`narrator_sentence`。
   - 随后，将归属当前 `[Shot N]` 的所有 `timeline` 窗口和 `dialogue` 台词按 `(start_s, kind_order)` 排序交错输出（相同 `start_s` 时，若存在紧邻在台词前的空拍窗，空拍窗结束于 `line.start_s` 自然排在前面；同一 `start_s` 下 `dialogue` 排在延续窗之前，确保“空拍肌肉阶梯 → 声学台词 → 句中/句后延续”的线性语序）：
     > **2.4.0 编译示例**：
     > `From 0.0s to 1.0s, <Subject 1> holds still as inner brows draw tightly inward, upper eyelids narrow, nostrils flare on a short inhale, and jaw muscles clench before the lips part slightly. At 00:01.000, <Subject 1> (S1), in a hushed, slow, hoarse voice with a low, flat pitch, speaks: <d>[Chinese] 你真以为我不敢？</d> From 1.0s to 3.8s, <Subject 1> keeps gaze locked forward while the corner of the mouth tightens and throat swallows once after the final syllable.`
3. **`_dialogue_sentence()` 声学短语渲染**：
   - 在 `ctx.acting_v24` 下，若 `line.delivery` 存在非空维度，确定性组装短语：
     - `volume / pace / voice_quality` 依次以逗号拼接为 `in a {adj_list} voice`；
     - 若存在 `pitch`，追加 `with {pitch_phrase}`；
     - 完整句式：`At MM:SS.mmm, {subject} ({speaker}), in a hushed, slow, hoarse voice with a low, flat pitch, speaks: <d>[{language}] {spoken_line}</d>`。
   - 若 `line.delivery` 为空（或历史/降级镜头），退回干净的 `At MM:SS.mmm, {subject} ({speaker}) says: <d>[{language}] {spoken_line}</d>`。
   - 无声镜头（`not spec.dialogue`）绝不进入 `_dialogue_sentence()`，继续保持零人声词汇污染。
4. **同步更新 `h3/spec_review.py`**：
   - 更新第 174 行对白时间戳剥离正则，从写死 `says: <d>.*?</d>` 放宽为兼容声学修饰与 `says|speaks`：
     `r"At \d{2}:\d{2}\.\d{3}, [^\n]*?\([^)]*\)(?:,\s*in [^\n]*?)?\s+(?:says|speaks):\s*<d>.*?</d>"`
   - 确保 2.4.0 提示词在 `review_h3_prompt()` 与 `manual4/h3/spec_review.py` 中不会误报 `H3_TIMING` 警告。

---

## 五、Phase 2 详细设计：整场情绪弧线 + 2–4 镜表演组统筹 Acting Pass

Phase 2 建立在 Phase 1 稳定底座之上，解决“单镜 10 秒上限切碎 1 分钟对手戏”以及“抽象情绪词转五官肌肉 + 自适应 1 秒空拍”的生成质量问题。

### 5.1 Layer 2 轻量整场张力曲线（`DramaBeat.beat_energy`）

* 在 `backend/shine/agents/film_contracts.py` 的 `DramaBeat` 增加可选字段：
  ```python
  BeatEnergy = Literal["calm", "probe", "pressure", "turn_pause", "erupt", "aftermath"]
  beat_energy: BeatEnergy | None = None
  ```
* 在 `crew_prompts.py` 的 `DramaSceneScript` 提示词中引导 `scriptwriter` 为每个 beat 标注 1 个 `beat_energy`（旧数据或未输出时为 `None`，零破坏性）。
* `scene_director.py` 接收 `beats[].beat_energy`：
  - 遇到 `turn_pause`（情绪转折/压迫停顿）优先切特写/近景并留出足够时长余量（7–10s）；
  - 遇到 `probe / pressure`（快对白交锋）可按紧凑 5–6s 推进，避免整场节奏平均化。

### 5.2 确定性表演段落分组器与空拍预算计算器（0 Token，服务器拥有唯一裁决）

新增核心模块 `backend/shine/agents/acting_director.py`，包含两个纯确定性前置函数：

#### 1. `group_performance_segments(shots: list[dict]) -> list[list[int]]`
按 `SceneShotBoard` 已有字段将整场 1 分钟对手戏聚合成若干连续的**表演段落组**（每组 **2–4 镜**，约 20–35 秒）：
* 遇到 `transition_motivation in {"time_or_space_jump", "establishing_reset"}` 或 `location` 切换或 `blocking.axis` 切换时，切开新组；
* 同场景同轴线连续镜头超过 4 镜时，在 `beat_energy` 阶跃点（如 `pressure → turn_pause` 或 `erupt → aftermath`）自然分组，上限 4 镜/组；
* 单镜独立场景允许形成 1 镜组。

#### 2. `compute_pre_speech_budget(shot: dict, beat_energies: list[str]) -> PreSpeechPlan`
服务器在调用 LLM 前**确定性算出每镜的空拍窗口与安全台词时间**，不让 LLM 做时间算术：

| 镜头类型判定条件 | 目标空拍时长 | 规则说明 |
| :--- | :--- | :--- |
| **特写 / 近景 / 中近景**（`shot_size` 含 `close` / `cu` / `mcu`）或包含 `turn_pause` / `erupt` / `aftermath`，且非 `continuous_action` 动作承接 | **0.8s – 1.2s**（默认 `1.0s`） | 完整情绪阶梯空拍：写清五官肌肉 2–3 步递进后再接台词。 |
| **中景 / 双人正反打 / `probe` 或 `pressure` 快交锋** | **0.3s – 0.5s** | 紧凑起势空拍：1 个核心眼/口/呼吸起势动作即接台词，不拖慢对白节奏。 |
| **`continuous_action` 连续承接尾帧**、全景/远景、无台词镜，或余量不足 | **0s（豁免）** | 不硬塞空拍，避免破坏动作连贯或挤压台词。 |

* **安全平移校验（永不触发时间轴硬拒）**：若首句台词原 `start_s < target_pause`，服务器计算所需平移量 `delta = target_pause - first_line.start_s`；仅当平移该子镜内所有台词后仍满足 `last_line.end_s <= sub_shot.end_s - 0.15` 且不与任何 `internal_cuts` 冲突时才执行平移，否则自动将空拍收缩为最大安全余量 `max(0.0, available_headroom)`。

### 5.3 极简 `ActingPatch` 契约与组间情绪状态接力

Acting Pass 按“表演段落组（2–4 镜）”调用现有 LLM Gateway（复用 `director` 角色路由，无需新增外部配置）：

* **输入上下文（只读）**：
  1. 本组 2–4 镜的精简摘要（`shot_id`、机位景别、角色 `{{Ck}}` 映射、服务器算好的 `timeline` 空白时间槽、编号台词列表 `line_index: 0..k` 及 `beat_energy`）；
  2. 上一组最后一镜提取的 **`carry_over_state`**（各角色结尾面部肌肉余韵 + 声学档位，如 `{"{{C1}}": "jaw clenched, eyes narrowed, hushed-hoarse"}`），确保 1 分钟跨组对手戏情绪不断层；
  3. 强制规范：**五官肌肉解剖拆解清单**（禁止孤立使用 `angry / sad / shocked / happy / nervous / scared / furious / depressed` 等抽象情绪词，必须写清上庭眉弓、眼睑瞳孔、鼻翼颧肌、唇颌咬肌与呼吸吞咽的递进变化）。
* **输出契约 `ActingGroupPatch`（极扁平，每镜 ~250–350 tokens，整组 ≤ 1,100 tokens）**：
  ```python
  class ActingWindowPatch(APIModel):
      start_s: float
      end_s: float
      action: str = Field(min_length=1, max_length=400)
      expression: str = Field(min_length=1, max_length=400)  # 放宽至支持完整五官肌肉阶梯

  class ActingDialoguePatch(APIModel):
      line_index: int = Field(ge=0)
      delivery: VocalDelivery

  class ActingShotPatch(APIModel):
      shot_id: str
      windows: list[ActingWindowPatch] = Field(min_length=1, max_length=6)
      deliveries: list[ActingDialoguePatch] = Field(default_factory=list)
      exit_acting_state: str = Field(default="", max_length=200)  # 供下一组接力

  class ActingGroupPatch(APIModel):
      shots: list[ActingShotPatch]
  ```
  > **关键防漂移设计**：LLM **永远不输出 `dialogue[].text`、`speaker_id`、`camera`、`blocking`、`internal_cuts` 或 `asset_ids`**；所有台词声学仅通过 `line_index` 挂接。

### 5.4 逐镜隔离合入 + 非阻塞降级控制链

合入点位于 `SceneShotBoard` 通过 `continuity_engine.review_scene_board()` 之后、最终落库 `ShotSpec` 之前（`backend/shine/agents/film.py` / `api_film.py` 唯一落镜链路）：

1. **逐镜容错解析**：整组 JSON 经 `crew_salvage.py` 清洗（去 markdown 围栏、修复常见括号、执行 `normalize_vocal_delivery()` 同义词折叠）。若某组中 3 镜有 2 镜完整、1 镜字段残缺，**2 镜立即合入生效**。
2. **抽象情绪词软检与单镜限时重试**：
   - 在 `continuity_engine.py`（唯一裁决口）新增轻量工具函数 `detect_bare_emotion_words(expression: str) -> list[str]`：当 `expression` 命中抽象情绪词（如 `angry / sad / shocked / fearful / happy`）且不含任何五官部位词（`brow / eyelid / eye / pupil / gaze / nostril / cheek / lip / mouth / jaw / chin / throat / breath`）时，标出告警。
   - 若某镜 JSON 损坏、超时或命中纯抽象情绪词，**仅对该单镜触发最多 1 次轻量定向重试**。
3. **非阻塞降级（绝不卡死拍摄）**：
   - 若单镜重试仍失败，保留 `scene_director` 原有的合法 `timeline` 与 `dialogue` 原样落库，仅在该镜记录一条 `warning`（`ACTING_POLISH_FALLBACK`），整场继续流转至素材准备、导演包与 H3 渲染。
   - 同步提供单镜重导接口（如 `POST /api/v1/shots/{shot_id}/repolish-acting`）及前端工作台 4 维声学枚举下拉微调（轻量增强），让导演随时可对重点戏单镜精修。

---

## 六、受影响模块清单与净代码量预估

### 6.1 模块清单与预估行数

| 阶段 | 文件路径 | 变更类型 | 预估净增/改行数 | 说明 |
| :--- | :--- | :--- | :---: | :--- |
| **Phase 1** | `backend/shine/schemas.py` | 修改 | ~95 | 新增 `VocalDelivery` 受控四维枚举、`DialogueLine.delivery` 省略空值序列化 |
| **Phase 1** | `backend/shine/agents/crew_salvage.py` | 修改 | ~110 | 新增 `normalize_vocal_delivery()` 同义词折叠表与单维软降级 |
| **Phase 1** | `backend/shine/h3/compiler.py` | 修改 | ~140 | 升级 `COMPILER_VERSION = "shine-h3-compiler/2.4.0"`，`LEGACY_230_VERSIONS` 冻结，环境前置 + 时间线/台词交错编织 + 四维声学渲染 |
| **Phase 1** | `backend/shine/h3/spec_review.py` & `backend/shine/h3/rulebook.py` | 修改 | ~35 | 适配 2.4.0 声学台词正则，登记 `2.3.0` 历史版本常量 |
| **Phase 1** | `backend/shine/director/contracts.py` & `backend/shine/api_streamline.py` | 修改 | ~30 | `DEFAULT_TOLERANCE_SECONDS = 10`，保留 `max_shot_seconds` 10s 红线与向后兼容 |
| **Phase 1** | `backend/shine/agents/scene_director.py` | 修改 | ~45 | 预算软化为 ±10s 弹性提示，放宽 4–10s 表演自主权 |
| **Phase 1** | `frontend/src/components/StreamlinePanels.tsx`（及对应单测） | 修改 | ~65 | 移除手动“每镜最大秒数”控件，固定 ±10s 容差，保留集数与每集秒数 |
| **Phase 1** | `backend/tests/test_h3_compiler_v24_acting.py` 等单测 | 新增/修改 | ~280 | 2.3.0 字节回放、2.4.0 交错编织、声学枚举归一化、±10s 容差回归 |
| **Phase 2** | `backend/shine/agents/film_contracts.py` & `crew_prompts.py` | 修改 | ~40 | `DramaBeat.beat_energy` 可选张力标签 |
| **Phase 2** | `backend/shine/agents/acting_director.py` | **新增** | ~420 | 表演段落分组器、空拍预算与安全平移、五官解剖提示词、`ActingGroupPatch` 契约、组间 `carry_over_state` 接力与逐镜非阻塞合入 |
| **Phase 2** | `backend/shine/agents/continuity_engine.py` | 修改 | ~60 | 唯一裁决口补充 `detect_bare_emotion_words()` 轻量 `warning` |
| **Phase 2** | `backend/shine/agents/film.py` & `backend/shine/api_film.py` | 修改 | ~95 | 将 Acting Pass 挂载到 `SceneShotBoard` 落镜链路 + 单镜重导入口 |
| **Phase 2** | `backend/tests/test_acting_director_pass.py` 等单测 | 新增 | ~340 | 分组逻辑、空拍安全平移、同义词归一化、单镜降级不阻塞、跨组接力端到端模拟 |

* **业务代码净增量预估**：Phase 1 约 **520 行**，Phase 2 约 **615 行**，合计约 **1,135 行**（含前端简化与新增模块）。
* **测试代码净增量预估**：约 **620 行**，总计约 **1,755 行**，规模克制、边界清晰、与 `copilot`（AI 管家）零重叠。

---

## 七、分阶段执行与验收计划（离线回归与真机 GPU 验收分离）

### Phase 1 验收（纯离线，无需 GPU）
1. **单测与定向回归**：
   - `2.3.0` 及所有更早版本 `H3PromptCompiler` 字节回放测试 100% 通过；
   - `2.4.0` 交错编织顺序验证（静态环境在前，空拍窗 → 四维声学 `<d>` 台词 → 延续窗紧邻在后）；
   - `review_h3_prompt()` 对 2.4.0 带声学台词零误报，无声镜头零人声词泄漏；
   - `normalize_vocal_delivery()` 覆盖大小写/连字符/同义词/未知词单维降级；
   - `StoryboardBook` 前端构建（`npm run build`）与 Vitest 回归通过，确认界面仅保留集数与每集目标秒数、±10s 内无偏差警告、超过 10s 单镜仍硬拒。
2. **全量后端 8 分片回归**：按 `AGENTS.md` 固定规则执行 `.\run_backend_shards.ps1 -WorkRoot <固定非系统盘目录>`，确认 8 分片全绿且完成临时目录清理。

### Phase 2 验收（离线模拟 + 本地 ComfyUI / 真机 H3 对比）
1. **离线回归**：
   - 模拟 1 分钟（8–10 镜）双人同场景对手戏：验证确定性分组（2–3 组）、组间 `carry_over_state` 传递、特写镜 1.0s 空拍安全平移、紧凑对切 0.4s 压缩、`continuous_action` 镜豁免；
   - 故障注入测试：模拟 1 镜 JSON 截断/乱码、1 镜超时、1 镜输出纯抽象情绪词，验证**同组其余镜头正常合入、故障单镜重试 1 次后平滑降级 `ACTING_POLISH_FALLBACK`，整场零阻塞**；
   - 运行 `backend/tests/test_film_crew.py`、`backend/tests/test_offline_e2e.py` 及 8 分片全量回归。
2. **本机 ComfyUI / 真机 H3 演技对比验收（独立硬件阶段）**：
   - 使用隔离 `-DataDir` 启动本地真机栈，挑选同一段 40–60 秒双人对手戏（含情绪转折与沉默压迫），分别用 `2.3.0` 基线提示词与 `2.4.0 + Acting Pass` 提示词出片对比：
     - 核查单镜均 ≤ 10s 且无 OOM；
     - 核查特写情绪转折镜是否出现清晰的“1 秒空拍五官微表情递进 → 开口发声”；
     - 核查台词音量、语速、音质、音调是否有可感知的起伏对比，且跨镜头情绪衔接自然；
   - 验收完成后执行收尾恢复 `.env` 并用 `启动 Shine 工作台.cmd` 复验日常作品。

---

## 八、可直接发给 Antigravity 的执行提示词

### 8.1 Phase 1 执行提示词（复制即用）

```text
请严格按照仓库根目录 `AGENTS.md`（特别是「一套事实、一个裁决、一个执行者」、8分片并行回归、以及 2026-11-27 日常工作台启动配置交还裁定）执行 `PLAN_20261127_H3_ACTING_DIRECTOR_V1.md` 的 **Phase 1**。

【并发进度保护红线】
1. 仓库中正在同步推进 AI 管家（`copilot`）相关开发。开工前先运行 `git status --short` 和 `git log -n 5 --oneline` 盘点当前基线，**严格保留所有既有已提交和未提交的非重叠改动**，严禁执行 `git reset --hard`、`git checkout -- .` 或修改 `backend/shine/copilot/`、`frontend/src/copilot/` 等无关文件。
2. 涉及 `.env` 或隔离验收时，严格执行开工留存快照与收尾恢复，并在结束前通过 `启动 Shine 工作台.cmd` 复验日常作品。

【Phase 1 交付范围】
1. **时长控制简化**：
   - 前端 `frontend/src/components/StreamlinePanels.tsx`：移除用户手动“每镜最大秒数”输入框，仅保留“拍几集（episode_count）”和“每集目标秒数（target_seconds）”，展示“单镜 4–10 秒由导演按表演自动分配（10 秒防爆显存红线），每集 ±10 秒自然弹性”说明；提交分镜书时容差固定为 10 秒。
   - 后端 `backend/shine/director/contracts.py` 与 `backend/shine/api_streamline.py`：将 `DEFAULT_TOLERANCE_SECONDS` 收敛为 `10`；保持 `DEFAULT_MAX_SHOT_SECONDS = 10.0` 为系统硬红线，`StoryboardBookBody.max_shot_seconds` 保留为可选兼容字段（默认 `10.0`）不破坏旧项目与测试。
   - `backend/shine/agents/scene_director.py`：将 `scene_budget_seconds` 提示词表述软化为 ±10 秒弹性目标，鼓励在 4–10 秒红线内优先保证表演节奏与空拍完整。
2. **四维声学受控词表与容错归一化**：
   - `backend/shine/schemas.py`：新增 `VocalDelivery`（`volume / pace / voice_quality / pitch` 四个受控英文 `Literal` 枚举，均默认 `None`），挂为 `DialogueLine.delivery: VocalDelivery | None = None`，为空时在序列化中省略，确保历史 `ShotSpec` 哈希零漂移。
   - `backend/shine/agents/crew_salvage.py`：新增 `normalize_vocal_delivery()`，实现大小写/连字符规范化、字段别名映射、同义词折叠表及未知词单维软降级（绝不因单个声学词拼写拒收整句）。
3. **升级 `H3PromptCompiler` 至 `shine-h3-compiler/2.4.0`**：
   - `backend/shine/h3/compiler.py`：冻结 `2.3.0` 至 `LEGACY_230_VERSIONS` 保持字节不变；在 `2.4.0` 中实现（a）静态环境（`environment / Space / Light / Air`）与 `scoped_story_facts` 前置；（b）`timeline` 窗口与 `dialogue` 台词按 `start_s` 交错编织输出；（c）`_dialogue_sentence()` 将非空 `delivery` 编译为 `<d>` 标签外的纯英文声学短语（无 `delivery` 时退回原中性 `says:`，无声镜头零人声词泄漏）。
   - `backend/shine/h3/spec_review.py` 与 `backend/shine/h3/rulebook.py`：更新对白时间戳剥离正则兼容 2.4.0 声学修饰，登记 `2.3.0` 历史版本常量。
4. **测试与回归**：
   - 新增 `backend/tests/test_h3_compiler_v24_acting.py` 覆盖 2.3.0 字节回放、2.4.0 交错编织、四维声学渲染、无声镜头隔离、同义词归一化与 ±10s 容差；
   - 更新受影响的前端测试并跑通 `frontend` build + vitest；
   - 使用 `.\run_backend_shards.ps1` 完成 8 分片全量后端回归，汇报实际命令、通过结果及日常工作台复验状态（明确区分已完成离线验证与待 Phase 2/真机验证项）。
```

### 8.2 Phase 2 执行提示词（待 Phase 1 全绿后发出）

```text
请继续按照 `AGENTS.md` 与 `PLAN_20261127_H3_ACTING_DIRECTOR_V1.md` 执行 **Phase 2**（整场情绪弧线 + 2–4 镜表演组统筹 Acting Pass）。

【并发进度保护红线】
开工前先检查 `git status --short` 与 `git log -n 5 --oneline`，严禁触碰或回退 AI 管家（`copilot`）及其他非重叠模块的最新进程；保持日常工作台 `.env` 快照留存与收尾复验流程。

【Phase 2 交付范围】
1. **整场张力曲线（Layer 2 → Layer 3）**：
   - `backend/shine/agents/film_contracts.py` & `crew_prompts.py`：为 `DramaBeat` 新增可选字段 `beat_energy: Literal["calm", "probe", "pressure", "turn_pause", "erupt", "aftermath"] | None = None`（空值兼容旧剧本）。
   - `backend/shine/agents/scene_director.py`：读取 `beat_energy`，在 `turn_pause / erupt` 等节点优先安排特写/近景并留出空拍时长余量。
2. **新增 `backend/shine/agents/acting_director.py`（方案 B：非阻塞表演组统筹）**：
   - 实现 0-token 确定性分组器 `group_performance_segments()`：按 `location + blocking.axis + transition_motivation` 将一场长戏聚合成 2–4 镜连续表演段落组。
   - 实现 0-token 空拍预算与安全平移器 `compute_pre_speech_budget()`：特写/近景/情绪转折镜 0.8–1.2s，紧凑对切 0.3–0.5s，`continuous_action`/无余量镜 0s；仅在确保 `end_s <= sub_shot.end_s - 0.15` 且不撞切点时安全平移台词。
   - 定义极简增量契约 `ActingGroupPatch`（只输出 `shot_id`、`windows(action/expression)`、`deliveries(line_index + VocalDelivery)`、`exit_acting_state`，绝不复述台词原文与镜头骨架），并实现情绪词 → 五官肌肉解剖拆解提示词与跨组 `carry_over_state` 情绪接力。
   - 接入 `crew_salvage.normalize_vocal_delivery()` 做逐镜清洗与合入：单镜 JSON 损坏、超时或命中纯抽象情绪词最多定向重试 1 次，仍失败则平滑降级保留 `scene_director` 原稿并记录 `ACTING_POLISH_FALLBACK` warning，**绝不阻塞拍摄流程**。
3. **唯一裁决与落镜接入**：
   - `backend/shine/agents/continuity_engine.py`：补充轻量 `detect_bare_emotion_words()` 作为 warning（不新增平行门禁）。
   - `backend/shine/agents/film.py` & `backend/shine/api_film.py`：在 `SceneShotBoard` 通过裁决后挂载 Acting Pass 写回同一份 `ShotSpec`，并提供单镜重导演技接口。
4. **验证与汇报**：
   - 新增 `backend/tests/test_acting_director_pass.py`，覆盖 1 分钟多镜分组、组间情绪接力、空拍安全平移、故障注入（单镜 JSON 损坏/超时/抽象情绪词）非阻塞降级；
   - 跑通 `backend/tests/test_film_crew.py`、`backend/tests/test_offline_e2e.py` 及 8 分片全量回归；
   - 单独列出待本机 ComfyUI/GPU 真机 H3 出片对比验收的清单与步骤，切勿用离线测试冒充真机出片验证。
```
