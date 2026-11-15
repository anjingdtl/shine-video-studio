# 拍摄提示词调优：扫清"不明语言声音旁白"（编译器 1.3.0）

日期：2026-11-15
分支：`tune/h3-narration-priming`（独立 worktree，不影响 main 与其他分支）
范围：仅 `backend/shine/h3/compiler.py` 的固定句式与对应回归；不改规则包钉住、
不改 `worker.py` / `preproduction.py`（不触发 VLM 重校准义务）。

## 1. 报障与根因

用户报障：正常出片仍会出现"不明语言的声音旁白"。怀疑与项目为硬性符合
MiniMax-H3 标准而采用的固定句式有关。

排查确认诱导源就在编译器的固定句式里。1.2.3 及以前的禁声/语言合同用**否定式
枚举**列出禁止的人声行为：

- 无对白镜头："do not produce speech, narration, voice-over, whispering,
  mumbling, singing, humming or vocal sounds of any kind"
- 有对白镜头："no other language, unscripted speech, narration, voice-over,
  background chatter, whispering or extra words may appear"
- 锁定事实句："must never be spoken, narrated or performed as voice-over"
- 配乐句："with no vocals or lyrics"；参考保留句："never carry over any words
  or vocals"

视频生成模型对否定词不敏感，枚举的人声概念（旁白、画外音、低语、哼唱……）
反而成为要渲染的语义内容；这些概念没有语言锚点，模型就随机选一种语言发声——
表现正是"不明语言的声音旁白"。2026-11-04 的两处修复（冻结风格声音维度裁剪、
音色参考豁免）解决了另外两条诱导路径，但固定句式自身的枚举一直保留。

## 2. 修订原则：正向音频合同

不再枚举"禁止哪些人声行为"，改为描述"声带里有什么"，并把所有人声锚定到项目
语言。编译器版本升至 `shine-h3-compiler/1.3.0`；官方章节顺序、`<d>` 对白标签、
规则包版本与哈希钉住全部不变。

| 位置 | 旧（≤1.2.3） | 新（1.3.0） |
| --- | --- | --- |
| 风格句（无对白） | Performance and sound: no spoken dialogue in this shot. | Performance and sound: this shot is performed without dialogue; its soundtrack is entirely non-vocal. |
| 表演句（无对白） | …with no speaking or mouthing words. Use only non-vocal scene sounds. | Performers act with closed mouths, expressing reactions through gaze, gestures and movement; the soundtrack stays non-vocal. |
| 表演句（有对白） | Perform speech only for the timed dialogue blocks below… | Deliver only the timed dialogue blocks below, exactly as written; between those lines the performers react silently and the scene sound stays non-vocal. |
| 锁定事实引导句 | …must never be spoken, narrated or performed as voice-over: | …are silent background context for the picture only; they inform the visuals and are never heard as audio: |
| 语言合同（无对白） | do not produce speech, narration, voice-over, whispering, mumbling, singing, humming… | This shot has no dialogue: the soundtrack is entirely non-vocal. Words quoted in the story facts or visual description stay silent context and are never heard. |
| 语言合同（有对白） | Only the explicitly tagged dialogue lines…no other language, unscripted speech, narration, voice-over, background chatter, whispering… | Every voice in this shot speaks {language}. The only words heard are the explicitly tagged dialogue lines of this shot, delivered exactly as written at their timed positions; all other sound stays non-vocal. Any voice or reading described in the story facts or visual description refers only to these exact lines. |
| 配乐句后缀 | Instrumental only, with no vocals or lyrics. | Instrumental only. |
| 音频参考保留句 | never carry over any words or vocals. | 无对白/续接派生物：referenced only for non-vocal ambience. 有对白音色主素材：the approved timbre voices only the scripted dialogue lines of this shot. |
| 音频参考定义句（有对白） | is a reference for voice timbre for scripted lines and non-vocal sound only. | is a reference for the scripted lines' voice timbre and non-vocal sound only. |

关键设计：

- **有对白镜头的语言锚点是"Every voice in this shot speaks {language}"**：任何
  声音（包括模型想加的旁白）都必须说项目语言，而"唯一能听到的词"是被标记的
  台词原文。旁白既没有语言空间，也没有文本空间。
- **无对白镜头只陈述事实**（没有对白、声带全非人声），不再点名任何人声行为。
- 旁白功能（`narrator.py`，用户主动开启的画外音）不变：旁白台词照常走 `<d>`
  语言标签，同样被新的语言锚点覆盖。
- 冻结风格的声音表演维度仍只在有对白镜头引用（2026-11-04 规则不变）。

## 3. 历史兼容（硬约束）

- 1.2.1 / 1.2.2 / 1.2.3 三个历史版本的输出**逐字节不变**：新增
  `LEGACY_SOUND_COMPILER_VERSION` 与 `legacy_sound` 门控，所有旧句式原样保留。
  已落库请求按各自钉住的版本重编译仍能得到原哈希，provenance 校验不受影响。
- `KNOWN_COMPILER_VERSIONS` 保证显式历史版本编译回执如实报告自己的版本号。
- 旧成片、旧冻结人工提示词、旧风格快照一律不重写。

## 4. 验证

- `backend/tests/test_h3_compiler.py` 30 例全绿，其中新增：
  - 固定句式不得出现诱导词表（voice-over/whispering/mumbling/singing/humming/
    background chatter/vocals/lyrics/speech/narration）；
  - 有对白语言锚点与音色参考正向句；旁白镜头保留旁白句并获得语言锚点；
  - 配乐句以 "Instrumental only." 结尾且不点名 vocals/lyrics；
  - **5 组代表性镜头 × 3 个历史版本的 prompt SHA-256 基线**（修订前采集），
    证明历史版本逐字节不变、新版本确实产生新字节。
- 定向回归 4 批并行（35 个受影响模块：编译器/旁白/剧组/分镜/风格/冻结解锁、
  绑定/参考/槽位、离线 E2E/生产序列/一键开拍/审片队列、交接/制片准备/素材
  提示词/变体门禁）：结果见提交说明与本文 §6 回填。
- Ruff 通过。前端无契约引用，不需要改动。

## 5. 真机验证边界（如实声明）

- 本轮是**提示词层修复**，离线测试只能证明合同文本与哈希契约正确，**不能证明
  H3 不再发出不明语言旁白**。最终确认需要在用户机用 1.3.0 重新出片并人耳核对
  （无对白镜头应只有环境声/配乐；有对白镜头应只有项目语言的原台词）。
- 历史成片不重写；旧镜头要享受新合同，需按既有重拍/重编译路径产生新请求。
- 本轮未改 `worker.py` / `preproduction.py` / handoff / quality 客户端，不触发
  `client_review_identity` 重校准；历史 VLM 正负校准仍未完成，不得声称已校准。

## 6. 回归结果回填

2026-11-15，worktree `shine-video-studio-prompt-tuning`，主检出 venv +
`PYTHONPATH` 指向本 worktree，`env -u NO_PROXY -u no_proxy`、
`CODEBUDDY_SAFE_DELETE_ENABLED=0`，4 批并行、按文件分批：

| 批次 | 模块 | 用例 | 退出码 |
| --- | --- | --- | --- |
| A | h3_compiler / h3_narration / crew_prompt_contract / film_crew / storyboard / storyboard_tone / shot_prompt_revision / director_styles / freeze_unlock | 152 | 0 |
| B | bindings / existing_binding / character_binding / output_spec_and_identity / reference_transfer / reference_slots / reference_slot_pruning / film_reference_budget / validation_template_binding / managed_run_binding | 78 | 0 |
| C | offline_e2e / production_series / generation_segments / one_click_batch_admission / one_click_start_closeout / director_execution_contract / advisory_queue / advisory_model_reviews | 68 | 0 |
| D | comfy_completion_window / comfy_handoff_journal / comfy_handoff_barrier / handoff_lease_transfer / v13_preparation / material_manual_prompt / material_prompt_carryover / variant_binding_and_gate | 175 | 0 |

合计 **35 模块 473 例，全部通过，日志无 FAILED/ERROR 行**（日志留存于
worktree `.local-tmp/logs/`，不入库）。Ruff 对改动文件通过。前端无契约引用，
本轮未改前端，未跑前端套件。未跑全量后端套件（本轮为编译器句式的定向影响面）。