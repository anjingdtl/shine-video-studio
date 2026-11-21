# 验收报告 — Ref2VA 七阶段文学—镜头—成片艺术质量收束 V1.0

> 日期：2026-11-21（Asia/Shanghai）
> 方案：`docs/SHINE_REF2VA_SEVEN_STAGE_LITERARY_TO_FILM_ART_QUALITY_V1_20261121.md`
> 基线差异：`docs/BASELINE_DIFFERENCE_REF2VA_ART_QUALITY_V1_20261121.md`
> 纪律：一套事实、一个裁决、一个执行者；只改七阶段业务链，不动四阶段 `manual4` 与共用底座。

## 0. 基线身份

- BASE_SHA / 开工 HEAD / origin/main：`6ca5eeb6ac449cfc7d6ebdd1354733ddaf2c0c76`（三者一致，`rev-list --left-right --count = 0 0`）。
- WORK_BRANCH：本地 `main`（原地实施，未新建 worktree/副本）。
- 工作树开工时仅未跟踪 `.trae/`、`.vite/`（保留不动），无已跟踪未提交改动。
- 官方规范 diff：`SKILL.md`/`base-en.txt`/`ref-en.txt` 三个被钉住文件 blob SHA 与上游 `MiniMax-AI/MiniMax-H3@main` 逐一一致，冻结规则包 `shine-h3-rules/1.1.0` 继续有效，未替换社区规则包。

## 1. P0 — 版本漂移审计与修复（已完成，已验证）

**审计结论（属实）**：`6ca5eeb` 相对 `b8202e5`/`f62e0b2`（两者编译器逐字节相同）在 `COMPILER_VERSION` 仍为 `1.3.0` 时改动了可输出提示词字节，三处漂移点：
1. 无对白镜头 `non_vocal_ambience_text()` 环境声过滤；
2. 无对白镜头语言世界锚点；
3. 主体外观措辞（影响所有带人物主体镜头）。

真实台账 `D:/Shine/data/shine.db` 34 个钉住 `1.3.0` 的请求中，**18 个原始变体 + 16 个漂移变体**。当前 main 的 `1.3.0` dispatch 只重放漂移变体，18 个原始变体重编译 SHA 不一致（实测 `e22ba283`/`f3962c6a` DRIFT-FAIL）。

**修复（不篡改旧 hash、不重写数据库快照）**：
- 新增当前版本 `shine-h3-compiler/1.4.0`，本轮所有新提示词行为仅此版本生效。
- `1.3.0` dispatch 保持当前 main 漂移字节不变（保护 16 个近期请求）。
- 新增只读重放身份 `shine-h3-compiler/1.3.0-original`，精确复现 `b8202e5` 原始字节。
- **唯一校验口** `h3/rulebook.py::recompile_for_provenance`：钉住 `1.3.0` 的请求规范重编译不命中时回退 `1.3.0-original`，仅逐字节命中才通过，warnings 记录命中变体；篡改文本两变体都不命中仍被拒。
- 黄金样：1.2.1/1.2.2/1.2.3 + 1.3.0（漂移）+ 1.3.0-original（原始）双变体哈希钉住。

**验证**：34 个真实冻结请求全部逐字节重放成功（18 original + 16 anchored，0 失败）；`test_h3_compiler.py` 40 例、`test_film_crew.py` 双变体 provenance 反例通过。

## 2. P1 — 文学交接收束（已完成，已验证）

- 新增只读选择函数 `director/streamline.py::select_unit_creative_input` + 单一谱系裁决 `director/pipeline.py::confirmed_scene_script_for_section` + 唯一创作文本渲染 `scene_script_creative_text`。
- 确认稿优先条件：`current_scene_script` 指针命名 + 未 stale + 同主文档（identity+sha）+ 源 span 逐字等于该 section 切片。整 section 单元才取整场脚本；子片段/合并单元安全回退原文，绝不整场重复投喂。
- 请求身份带脚本 `artifact_id/sha`；`materialprep/story.py::verified_production_context` 这一唯一源范围校验口扩展为接受「原文切片或已核验创作文本」，raw 项目身份逐字节不变。
- 五维记忆仍经既有 `_storyboard_memory_view` 预算检索，不倾倒长篇原文。
- 验证：`test_storyboard_creative_input.py` 5 例（确认优先/未确认回退/stale 回退/谱系不符回退/子片段回退）；`test_director_streamline.py`+`test_story_preparation.py` 40 例无回归。

## 3. P2 — 表演与摄影决策提升（已完成，已验证）

`agents/crew_prompts.py` 五角色提示词增强（不新增 Agent、不立第二一致性引擎、不与服务器定时表裁决冲突）：
- 编剧：beat 写可观察动作（主体/位置姿势/动作起止/情绪证据/结束状态），内心外化为可见行为。
- 导演：每段单一动作/情绪重心，静机位默认+运镜动机，开场/结尾可衔接状态，明确不重算 editorial_timing_plan。
- 摄影：默认静机位，运动需动机+类型/幅度/速度，起始构图/主体位置/机位相对位置/结尾构图，推镜≠变焦，物理接触合理。
- 场记：物理连续性（位置/服装/手持物/地面接触/屏幕方向/动作完成/视线轴/镜内切点合法）。
- 剪辑：明确「尚未渲染、未看过真实 footage」，真实入出点后期决定。
- 验证：`test_crew_prompt_contract.py`+`test_h3_narration.py` 23 例通过。

## 4. P0-E — 旁白/外语防回归（已完成，已验证）

- `agents/film.py` 风格决定层：`narration_level` 未设置 → 新分镜任务有效策略派生为 `none`（不另造配置项，冻结快照与 hash 不改写）；显式 low/high 仍按剧本与风格。
- 保护既有 1.3.0 正向声音合同与 `6ca5eeb` 无对白 sanitizer/语言锚点（纳入 1.4.0，不回退）。
- 验证：`test_dialogue_contract_repair.py` 新增「未授权旁白被拒」「剧本自带旁白仍通过」；既有旁白/静音黄金样通过。

## 5. P3 — Ref2VA 编译语义校准 + 镜内时间线（已完成，已验证）

- `schemas.py`：`ShotSpec.internal_cuts`（可选 `InternalCut`，空省略保旧哈希，严格递增且 < duration）；`SemanticFinding.evidence_end_s`（可选时间窗）。
- `api_film.py::apply_camera`：镜内 cut 落结构化 `internal_cuts`，`story_beat` 保留开场段（不再拼 "hard cut" 散文）；`ShotIntent.cuts` 仍是权威上游。
- `h3/compiler.py` 1.4.0：
  - `_shot_text` 原生 `[Shot 1] … [Shot N] At MM:SS.mmm` 时间线；
  - `_summary` 按真实用途推导任务类型（构图锚=keyframe completion；真续接=video continuation；动作参考≠续接；音频=audio reference）；
  - `_retention` 官方枚举分级（主体 fully_preserved；构图参考 partially_preserved；动作参考 attribute_transfer；真续接 fully_preserved；音频 reference）；
  - `_camera_narrative` 自然运镜叙事替代机械 `Camera: key: value`。
  - 1.2.x/1.3.0 双变体字节不变（legacy_semantics 门控）。
- 验证：`test_h3_compiler.py` 40 例（含原生时间线/视频语义/构图分级/自然运镜/旧 spec 字节不变）；`test_storyboard.py` 定时 cut 落镜断言更新为结构化字段。

## 6. P4 — 质检反馈 + 真实看镜剪辑建议（已完成，已验证）

- P1-A：`media/frames.py::extract_frames` 可选首尾帧（默认不变）；`worker.py` 语义审片启用首尾帧；`quality/semantic.py` VLM 提示词扩展物理/动作/衔接评估（CHARACTER_POP_IN_OUT/GROUND_PENETRATION/ACTION_TRAJECTORY_JUMP/PROP_PENETRATION/IDENTITY_DRIFT/CAMERA_MOTION_MISMATCH/ANATOMY_ERROR，均 uncertain 咨询级，不阻断投产）；发现走既有 `SemanticFinding`/advisory 链。
- P1-B：`production/episode_delivery.py::suggest_episode_cuts` 只读建议，读 `episode_current_jobs` 同一当前通过 take + `probe_file` 实测时长（非计划值），每镜一个合法连续 in/out，保全部台词、仅裁大段静默头尾且标 uncertain；无台词不猜剪；不可测记 unavailable/not_passed。新增 `GET /production-runs/{id}/episodes/{eid}/cut-suggestions`；应用仍走既有 `EpisodePostContract`/EDL/FFmpeg，不建第二装配器，不声称音频混音/J-cut。
- 验证：`test_episode_cut_suggestions.py` 3 例；`test_advisory_queue.py`/`test_delivery_orders.py`/`test_episode_subtitle_cut.py`/`test_advisory_model_reviews.py` 39 例无回归。

## 7. 测试结果

- 定向：编译器 40、film_crew provenance、storyboard、streamline+story 40、crew+narration 23、dialogue 10、QC/delivery 39、cut-suggestions 3、creative-input 5 —— 全绿。
- Ruff：`backend/shine` 全量 + 改动测试 All checks passed。
- 前端：`tsc --noEmit` 通过；`vitest run --no-file-parallelism` 90 文件 / 459 例全绿；`npm run build` 通过。
- 后端全量 8 分片（修复后干净复跑，`run_backend_shards.ps1 -WorkRoot F:/ShineRegTest`，`CODEBUDDY_SAFE_DELETE_ENABLED=0`）：**8/8 分片 exit 0，289 个测试文件，`shards_with_failure_markers=0`，日志无 FAILED/ERROR**。日志：`F:/ShineRegTest/shards-20261121-084901-798/chunk-*.log`。首轮 7 项失败全部修复后复跑全绿（见 7.1）。
- 迁移：本轮未改 SQLAlchemy 模型/列，无新增迁移（internal_cuts 为 ShotSpec JSON 内可选字段）。

### 7.1 首轮全量暴露并已修复的 7 项

首轮 8 分片暴露 7 个 FAILED，全部定位并修复、定向复验转绿：
1. `test_support_copilot_v21::test_contract_check_passes_on_the_committed_build`、`test_build_endpoint_reports_real_identity`、`test_support_copilot_v3::test_p1_knowledge_manifest_is_bound_by_the_build_script`：新增 cut-suggestions 路由使 API schema digest 与 discovered 路由集变化，copilot 构建产物过期。按 Copilot V2.1 契约规则重新生成 `support/generated/copilot_contracts.json`（discovered 475），`--check` 通过。**未手工编辑生成物授权能力**。
2. `test_director_styles::test_six_profiles_..._compile_official_sections`：自然运镜叙事丢掉了 `director_style` 标签。修复：冻结导演风格约束块保留 "Director style:" 标签（运镜散文仍自然），断言恢复。
3. `test_structured_review::test_committed_decoder_lock_pins_current_wire_schema`：曾给 `SemanticFinding` 加 `evidence_end_s` 改动 VLM wire schema、触发解码器锁。决议：**撤回该字段**，保持 wire schema 与解码器锁稳定；时间证据用既有 `evidence_time_s`（点时间戳），范围化为 V2 需评审的解码器锁修订。
4. `test_storyboard::test_frozen_dialogue_ratio_controls_script_adaptation[80-Narrator-...]`、`test_agent_retry_and_rate_limit::test_a_refused_contract_is_retried_with_a_named_correction`：P0-E 把未设置 narration_level 派生为 none，使旁白行改走「未授权旁白」拒绝路径。这是**方案有意的行为变更**。调整：storyboard 参数化保留 Narrator 用例并改断言为 `DIALOGUE_NOT_IN_USER_SCRIPT`、另加非旁白说话人 `Stranger` 用例覆盖 `ADAPTED_DIALOGUE_SPEAKER_NOT_IN_SOURCE_BEAT`；agent_retry 用例改用非旁白说话人 `Stranger` 以保留「说话人不在 beat」纠错注记覆盖。未弱化任何安全拒绝。

## 8. 真实 H3 A/B 验收（RTX 5080）

### 8.0 真机环境与方法

- GPU：NVIDIA GeForce RTX 5080 16GB；ComfyUI 安装 `F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base`，H3 INT8 ConvRot DiT + pdd8 + tail-refine 二采（与基线同策略）。
- 方法：同一镜头 `7788147e`（女巫1119，rev 16，单句中文对白）同 spec/同参考集/同策略/同输出 672×384@24fps，分别以 1.3.0（基线，既有通过产物）与 1.4.0（新编译器，本次真实重渲）出片。H3 为概率推理，不承诺同 seed 逐像素复现；记录变量。
- 新渲染经唯一生产执行链：`POST /projects/{id}/jobs`（provider=comfyui + 冻结 strategy）→ 常驻 Worker → 受管渲染器（PID/birth/Job Object 三证）→ 渲染 → 技术 QC → 租约释放。渲染后 GPU 回基线（约 1.7GB），无孤儿渲染器。

### 8.2 真实成片 A/B 证据（已完成）

| 项 | A 基线 1.3.0 | B 新 1.4.0 |
|---|---|---|
| job | `e1a5a876` | `ebc6095b` |
| compiler_version | shine-h3-compiler/1.3.0 | shine-h3-compiler/1.4.0 |
| prompt_sha256 | `19600b726cb352ef…` | `d7f0e45539b64363…` |
| artifact | `41a95ce3` | `45738140` |
| artifact_sha256 | `849cdb34b885aa30…` | `629dd221ce38c449…` |
| 状态 | PASSED / real | PASSED / real |
| 时长/画幅/帧率 | 4.458s / 672×384 / 24fps | 4.458s / 672×384 / 24fps |
| 全文件解码 `ffmpeg -f null -` | exit 0，0 错误 | exit 0，0 错误 |
| has_audio | true | true |
| is_mock | 0 | 0 |

- 抽帧目检（0.5/2.2/4.0s）：A、B 均为正确 2D 赛璐璐动画媒介，主角（绿蓝衣青年）与紫衣女巫身份/发型/服装一致，广场+绞架背景一致；新编译器的自然运镜叙事与保留分级**未破坏身份与媒介**。
- 质量门（B）：technical=passed（真实 ffprobe/解码）；semantic/audio/style 咨询门=not_run/unavailable（本机 VLM/ASR 路由未配置，如实记录，不假装通过）；final=needs_review，acceptance=pending_review。**非人工艺术验收**。
- B 产物写入 `history/`（A 为当前采用版本），新重渲不覆盖已采用成片；选用仍走既有「选为当前装配版本」。
- 证据文件：`.local-acceptance/ref2va-ab-20261121/real_render_evidence.json`、`{A_old_130,B_new_140}_prompt.txt`、`{A,B}_frame_*.jpg`。

### 8.1 确定性编译器 A/B（离线，已完成）

对真实冻结请求同 spec/同参考集重编译 1.3.0 vs 1.4.0：
- dialogue 镜 `5ec39139`：old SHA `96736425022429ba…` → new SHA `1c22b3f2af9947bd…`，17 行差异。
- multi 镜 `e22ba283`：old SHA `5b1227f4492b5322…` → new SHA `3f25b34b92af7b45…`，15 行差异。
- 差异内容：构图参考 fully_preserved→partially_preserved；机械 Camera 枚举→自然运镜叙事。**正向声音合同、`<d>[Chinese]` 台词、说话人 (S1) 映射、主体定义逐字不变**。
- 证据文件：`.local-acceptance/ref2va-ab-20261121/{dialogue,multi}_*_{old_130,new_140,diff}.txt`。

镜内 cut（T09）A/B（同创意：开场 + 3.5s/7.0s 两切点）：
- A（旧 1.3.0，平铺）：单 `[Shot 1]` 块内 `At 00:03.500, hard cut. …` 散文 + 机械 `Camera: composition:`。
- B（新 1.4.0，结构化 internal_cuts）：原生 `[Shot 2] At 00:03.500, …`、`[Shot 3] At 00:07.000, …` 时间线 + 自然运镜。
- 证据文件：`.local-acceptance/ref2va-ab-20261121/cuts_{A_old_flattened,B_new_timeline}.txt`。

## 9. 历史哈希/兼容保护

- 1.2.1/1.2.2/1.2.3 黄金样逐字节不变；1.3.0 双变体精确重放；旧镜头/旧包/人工提示词/已交付视频不回写。
- 旧 `ShotSpec` 序列化字节不变（internal_cuts 空省略）；raw 项目分镜请求身份不变。

## 10. 遗留问题与诚实边界

- **音频硬回归未做真机听审**：本机 VLM/ASR 路由未配置，咨询门 `unavailable`；新渲染 `has_audio=true` 但**未验证 spoken language/无旁白**（ASR 不可用≠已证实安静）。T05/T06 对白语言、T03/T04 无外语旁白的真机听审仍需用户/后续配置 ASR 后完成。本轮以确定性提示词合同（正向声音+语言锚点+`<d>` 标签）与编译级回归保护，不声称真机音频已验收。
- 本轮改了 `worker.py`（首尾帧采样，1 行），按 `client_review_identity` 约定旧 VLM revision 失效；真实正负校准历史上未完成，**不得声称已校准**。真实成片最终验收门为 `needs_review`/`pending_review`，非人工艺术验收。
- 艺术 A/B 为单镜同 spec 对照（概率推理，不承诺同 seed 复现）；目检身份/媒介一致、构图合理，但**未做两位审看者 1–5 级评分**；效果不显著不推广全量。T01/T02/T07/T08/T10/T11/T12 等样例本轮未逐一真机覆盖（离线编译级覆盖+单镜真机），记 NOT_RUN/PARTIAL。
- 未接入用户 `4070`/`5090vd2`，不宣称通过这两种真机性能测试。
- P1-B 剪辑建议为只读咨询，V1 仅单连续 in/out、保台词、裁大段静默头尾；多段重排/J-L-cut/音频混音明确 V2 待决，未实现亦未声称。
- 为做真机 A/B 启动了工作台（API 8000 / Web 5173 / Worker，supervisor 8790），当前仍在运行；用户可经页面「工作栈」停止。未获授权不推送远端 main。
- 全量复跑收尾时个别 basetemp 哈希文件因被占用报 Access-denied（脚本已删其余并保留最近 1 次日志）；`F:/ShineRegTest` 残留 2 个目录，属可清理测试残留，非生产数据。

## 11. 改动文件清单

源（七阶段业务链，14）：`h3/compiler.py`、`h3/rulebook.py`、`schemas.py`、`agents/crew_prompts.py`、`agents/film.py`、`api_film.py`、`api_director.py`、`director/streamline.py`、`director/pipeline.py`、`materialprep/story.py`、`production/episode_delivery.py`、`quality/semantic.py`、`media/frames.py`、`worker.py`（1 行首尾帧）。

构建产物（再生成，非手工授权）：`support/generated/copilot_contracts.json`、`support/knowledge_manifest.json`（新 cut-suggestions 路由仅 DISCOVERED、executable=false）。

测试：修改 `test_h3_compiler.py`、`test_film_crew.py`、`test_storyboard.py`、`test_dialogue_contract_repair.py`、`test_agent_retry_and_rate_limit.py`；新增 `test_episode_cut_suggestions.py`、`test_storyboard_creative_input.py`。

文档：`docs/BASELINE_DIFFERENCE_REF2VA_ART_QUALITY_V1_20261121.md`、`docs/ACCEPTANCE_20261121_REF2VA_ART_QUALITY_V1.md`。

未触及 `manual4/*`、`foundation/*`、统一 ComfyUI/权重/节点管理、安装器、基础设置、资源调度裁决。无 SQLAlchemy 模型/迁移变更。

HEAD_SHA：见本轮本地 main 提交（`git log -1`；未推送远端，待用户授权）。