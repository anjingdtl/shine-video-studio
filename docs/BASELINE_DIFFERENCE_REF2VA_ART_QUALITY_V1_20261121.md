# BASELINE_DIFFERENCE — Ref2VA 七阶段艺术质量收束 V1.0（P0 基线核查）

> 编制：2026-11-21（Asia/Shanghai）
> 方案：`docs/SHINE_REF2VA_SEVEN_STAGE_LITERARY_TO_FILM_ART_QUALITY_V1_20261121.md`
> 方案分支：`docs/ref2va-seven-stage-art-quality-chain-v1-20261121`（只读，不作开发分支）
> 事实分级：R=仓库只读核查，O=官方规范，D=本方案设计。

## 0. 基线身份

| 项 | 值 |
|---|---|
| BASE_SHA（方案远端审查基线） | `6ca5eeb6ac449cfc7d6ebdd1354733ddaf2c0c76` |
| 本地 `main` HEAD（开工时） | `6ca5eeb6ac449cfc7d6ebdd1354733ddaf2c0c76` |
| `origin/main`（fetch 后） | `6ca5eeb6ac449cfc7d6ebdd1354733ddaf2c0c76` |
| 本地 vs 远端 main | `git rev-list --left-right --count main...origin/main` = `0 0`（完全一致） |
| 工作树 | 仅未跟踪 `.trae/`、`.vite/`（临时目录，保留不动）；**无已跟踪文件未提交改动**，无需备份 |
| 开发分支 | 本地 `main`（按用户指令原地实施，不新建 worktree/副本） |

结论：本地 main 与方案基线、远端 main 三者一致，方案中基于 `6ca5eeb` 的 R 级核查全部适用。

## 1. 官方规范 diff（O）——无漂移

规则包钉住官方 commit `d21241f0a4b3acbb34c97dae47fa417b7065e438`（`h3/rulebook.py:30`）。
对 `MiniMax-AI/MiniMax-H3@main` 只读核对三个被钉住文件的 blob SHA：

| 文件 | 钉住 blob_sha | 上游当前 blob_sha | 结论 |
|---|---|---|---|
| `skills/h3-prompt-writing/SKILL.md` | `b6d9b283…` | `b6d9b283…` | 一致 |
| `references/base-en.txt` | `40cf586a…` | `40cf586a…` | 一致 |
| `references/ref-en.txt` | `7ae1b2d0…` | `7ae1b2d0…` | 一致 |

**官方原文未变动**，冻结规则包 `shine-h3-rules/1.1.0` 继续有效；本轮不替换为社区规则包，不改 `RULE_VERSION`/哈希钉住。

## 2. 编译器版本漂移审计（R6b）——**属实，需修复**

对比三个提交的 `backend/shine/h3/compiler.py`：

- `b8202e5`（11-15，1.3.0 正向音频合同首次引入）与 `f62e0b2`（V2 双线收尾）**逐字节相同**。
- `f62e0b2 → 6ca5eeb` 改动 40 行，`COMPILER_VERSION` 仍为 `shine-h3-compiler/1.3.0`，但**可输出提示词字节已变**。三处漂移点：
  1. `_soundscape`：无对白镜头对 environment/ambience 应用 `non_vocal_ambience_text()` 过滤（剥离 "beneath the original dialogue" 等）。
  2. `_spoken_language_sentence`：无对白镜头新增语言世界锚点 "The scene takes place in a {language}-language world, and every sound stays wordless."。
  3. `_subject_definitions`：主体外观措辞由 "this exact face, hairstyle and build" 变为 "this exact face, hairstyle, build, and reference appearance"（**影响所有带人物主体的镜头，不止静音镜头**）。

**真实台账证据**（`D:/Shine/data/shine.db`，只读）：67 个 job 中 34 个钉住 `1.3.0`，其中
- **18 个原始变体**（11-19～11-20 16:12，`hairstyle and build`）；
- **16 个漂移变体**（11-20 16:51 之后，`build, and reference appearance`，静音镜头带语言锚点）。

用当前编译器对真实冻结请求重编译（`compiler_version=1.3.0`）：
- 原始变体 `e22ba283`/`f3962c6a`：stored SHA ≠ recompiled SHA → **DRIFT-FAIL**（provenance 会拒）。
- 漂移变体 `8bcb33c1`/`7a62301b`：MATCH。

即：**同一版本号 `1.3.0` 在台账里对应两种不同字节**。当前 main 的 `1.3.0` dispatch 只重放漂移变体，18 个原始变体请求在重校验/重拍时会以 `SHINE_PROVENANCE` 被拒。这是 main 上已存在的潜在缺陷。

### 修复决议（不篡改旧 hash、不重写数据库快照）

- 新增当前版本 `shine-h3-compiler/1.4.0`：本轮所有新提示词行为（镜内时间线、六字段语义、自然运镜叙事）只在此版本生效。
- `1.3.0` dispatch **保持当前 main 的漂移字节不变**（保护 16 个近期真实请求与既有黄金行为）。
- 新增**只读重放版本** `shine-h3-compiler/1.3.0-original`：精确复现 `b8202e5` 的原始 1.3.0 字节（关闭三处漂移点）。
- **唯一校验口** `h3/rulebook.py::validate`：对钉住 `1.3.0` 的请求，若规范重编译与冻结字节不符，回退用 `1.3.0-original` 重放；**仅当某一历史变体逐字节命中才通过**，并在报告 warnings 记录命中的变体。这是对既有单一裁决口的补全，不是第二套检查；任意篡改文本两个变体都不命中，仍被拒。
- 黄金样：钉住 1.2.1/1.2.2/1.2.3 + 1.3.0（漂移）+ 1.3.0-original（原始）双变体哈希；1.4.0 对新镜头产生新文本。
- 旧冻结导演包/人工提示词/旧镜头/旧内容哈希/已交付视频一律不回写；新行为仅经新镜头修订、新包/新请求生效。

## 3. 已被近期提交修复、本轮不重复做的项（R6 保护）

- `6ca5eeb` 的无对白 `non_vocal_ambience_text` 过滤、语言世界锚点、`apply_camera` 无对白清理 sound ambience、`crew_prompts` "仅真实上镜角色/首次视觉出现顺序" 约束：**受保护既有成果**，本轮保留并纳入 1.4.0，不按旧 `f62e0b2` 基线重复修改或回退。
- 1.3.0 正向声音合同（`PROMPT_TUNING_20261115_NARRATION_PRIMING.md`）：禁止在最终提示词重新堆否定式人声词枚举；本轮保留。
- 阶段导航、素材映射、`episode_current_jobs`、字幕跟随真实 EDL（`_selected_cut_timing`）等近期收敛：不改。

## 4. 本轮确认的真实改造焦点（R，未被近期提交覆盖）

| 编号 | 现状 | 改造 |
|---|---|---|
| R1 | `queue_director_storyboard` 用原始文档偏移分段，`_advance_director_unit` 传 `document.text[start:end]`；已确认 `FourStageSceneScript` 仅供 `_section_beats_view` 预览（且该预览读任意非 stale 草稿、从 beats 读不存在的 dialogue） | P1：确认稿优先、原文兜底的只读创作输入选择，谱系/section/确认态校验，请求身份带脚本 artifact_id/sha |
| R2 | `apply_camera` 把 `EditorialCut` 拼成 `At 00:xx, hard cut.` 自然语言进 `story_beat`；`_shot_text` 以 `[Shot 1]` 接整段 | P0-C/P3：`ShotSpec.internal_cuts` 可选结构化字段 + 编译器 `[Shot N] At MM:SS.mmm` 原生时间线 |
| R3 | `_camera_sentence` 机械 `Camera: key: value` 枚举 | P3：自然镜头叙事（运动类型/幅度/速度/动机），默认静机位 |
| R4 | `_summary` 见 VIDEO 一律加 `video continuation`；`_retention` 非音频一律 `fully_preserved` | P3：按 manifest 用途推导任务类型与官方保留枚举；无用途证明时兼容老行为并发诊断 |
| R5 | `build_episode_edl` 无合同默认整段取用 `in_s=0,out_s=全长` 每镜 CUT | P4：基于真实 PASSED take 的看镜剪辑**建议**，经既有 `EpisodePostContract`/EDL/FFmpeg 执行 |
| R7 | `unexpected_speech_finding`/`dialogue_findings` 存在但 ASR/VLM 实测 UNAVAILABLE/超时 | P4：复用既有咨询链扩展动作/物理/衔接发现；不可用记 UNAVAILABLE，不假装通过 |

## 5. 不可触碰边界（本轮承诺）

- 不改 `backend/shine/manual4/*` 四阶段业务、事实/执行队列/用户流程；保持双线隔离与共用底座边界。
- 不改统一 ComfyUI/模型权重/节点管理、安装器、基础设置、资源调度裁决。
- 不新增平行 JSON 文件链、第二数据库事实表、第二审核开关、第二 H3 提示词输出器、第二 EDL 执行器。
- 用户单镜上限是唯一时长事实（4–15s），不新增时长控制器。
- 不因 VLM/ASR 不可用扩大投产硬阻断；保持咨询质检地位。
- LoRA/采样/二采/RelayVSR X2 是执行策略，不承担导演语义纠错。

## 6. 真机环境（P5 预备）

- GPU：NVIDIA GeForce RTX 5080，16303 MiB 总 / 开工时空闲约 14279 MiB。
- 开工时 8000/5173/8188/8790 无监听（无运行栈）。
- 日常数据目录 `D:/Shine/data`（`SHINE_DATA_DIR`）；测试用隔离临时目录，不写日常库。
- 未接入用户 `4070`/`5090vd2`，不宣称通过这两种真机性能测试。