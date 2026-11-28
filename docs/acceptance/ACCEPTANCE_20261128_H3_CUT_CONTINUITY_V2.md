# 验收报告：切镜时机与镜间衔接调优 V2（DeepSeek + H3PromptCompiler 2.6.0，2026-11-28）

- 前序基线：[ACCEPTANCE_20261128_H3_ACTING_DIRECTOR_V1.md](./ACCEPTANCE_20261128_H3_ACTING_DIRECTOR_V1.md)（首轮 MiniMax 60s 样片实测）
- 本轮目标：针对首轮样片人工观片暴露的 **4 项切镜时机与镜间衔接问题**，在唯一裁决口、唯一导演提示词与唯一编译器中做根因级修复，通过全量回归后切换 DeepSeek LLM（`deepseek-flash`）重跑同剧本 60s 真实 MiniMax-H3 样片并逐帧复核。

## 1. 首轮 4 项实测问题的精准根因与修复

| 编号 | 首轮样片现象 | 精确代码根因 | 根治措施（收敛到唯一位置） |
|---|---|---|---|
| 1 | 12–14s 男主从柜台里瞬移到柜台外 | `s1` 单人 `medium close-up` → `s2` 双人 `medium two-shot` 进门却标了 `continuous_action` 走 `last_frame`（硬锁单人近景把男主塞进柜台内侧）；随后 `s2 → s3` 同双人中景又标 `shot_reverse_shot` 硬切 `cut` 重置位置 | [continuity_engine.py](../../backend/shine/agents/continuity_engine.py) `_decide_boundaries()` 新增跨景别静态 `last_frame` 转 `cut`（`CONTINUATION_IGNORED_ON_SCALE_CHANGE`）、紧景新人入场静态 `last_frame` 转 `cut`（`CONTINUATION_IGNORED_ON_CAST_ENTRY`）；[scene_director.py](../../backend/shine/agents/scene_director.py) 要求进门/跨区移动必须写出完整位移演出并持续锚定人物位于柜台哪一侧 |
| 2 | 31–33s 男主掏两把钥匙，第二把覆盖第一把 | `s5` 在同一 H3 提示词里把“掏钥匙并推过柜台”写了 5 遍：`blocking.posture` + `props` + `story_beat` + `Air(micro_motion)` + `timeline[0..1]`，导致 H3 在 0–2s 先放一把、3.5s 再放第二把覆盖 | [compiler.py](../../backend/shine/h3/compiler.py) 冻结 `2.5.0` 逐字节不变并升级至 **`shine-h3-compiler/2.6.0`**：有 `timeline` 时不再前置重复输出 `story_beat` 剧透；[scene_director.py](../../backend/shine/agents/scene_director.py) 与 [acting_director.py](../../backend/shine/agents/acting_director.py) 明确 `blocking` 仅写 0.0s 起幅静态姿态与已手持物件、`micro_motion` 仅写持续环境微动、单次道具动作只执行一次；[continuity_engine.py](../../backend/shine/agents/continuity_engine.py) 新增 `BLOCKING_POSTURE_PROCESS_ACTION` 告警 |
| 3 | 44–45s 女主从柜台里瞬移到柜台外和男主对视 | `s6` 与 `s7` 同为 85mm `close two-shot` 同双人站位，却标 `shot_reverse_shot` 做同构图硬切（Jump Cut），丢弃 `last_frame` 导致 H3 重绘柜台到背景、女主穿柜台而出 | [continuity_engine.py](../../backend/shine/agents/continuity_engine.py) `_decide_boundaries()` 新增伪正反打同构图硬切保护（`SAME_FRAMING_CUT_PROMOTED_TO_LAST_FRAME`）；[scene_director.py](../../backend/shine/agents/scene_director.py) 要求高潮对峙改用真正的单人近景/特写正反打，并在每镜显式声明人物相对柜台的内外侧 |
| 4 | 59–60s 男主手忽然瞬间出现举着钥匙 | `s8`（85mm `close two-shot`，画框内无桌面）→ `s9`（50mm `medium two-shot`，要求展示桌面钥匙并绑定银钥匙 `<Picture 4>`）错误走了 `last_frame`，首帧锁死特写导致 H3 强行在画面底端弹出举钥匙的手 | 跨景别静态 `last_frame` 自动转 `cut`（`CONTINUATION_IGNORED_ON_SCALE_CHANGE`）+ [compiler.py](../../backend/shine/h3/compiler.py) `2.6.0` 强化 `last_frame` 界标侧与禁止无连续动作凭空出现道具指令 |
| 附带发现 | `s6/s7/s8/s9` 编译提示词 `In frame:` 裸漏字面量 `{{C2}}` | `_placement_sentence()` 未对 `blocking.placements[].posture/props` 调用 `_resolve_character_tokens()` | [compiler.py](../../backend/shine/h3/compiler.py) `2.6.0` 在 `_placement_sentence()` 完整解析 `{{Ck}}` 占位符 |

## 2. 离线全量回归证据

- **新增单测**：
  - [test_continuity_engine.py](../../backend/tests/test_continuity_engine.py)：覆盖跨景别静态 `last_frame` 转 `cut`（含 `pull_back` 运镜反例）、紧景新人入场转 `cut`、同构图双人伪正反打升格 `last_frame`（含真单人正反打反例）、`blocking.posture` 过程动作告警。
  - [test_h3_compiler_v24_acting.py](../../backend/tests/test_h3_compiler_v24_acting.py) 与 [test_h3_compiler.py](../../backend/tests/test_h3_compiler.py)：覆盖 `2.6.0` token 解析、去重 `story_beat`（无 `timeline` 时保留）、`last_frame` 界标侧锚定，以及 `2.1.0–2.5.0` 逐字节精确重放。
- **后端 8 分片全量**：`.\run_backend_shards.ps1 -WorkRoot <测试根> -Shards 8`（`CODEBUDDY_SAFE_DELETE_ENABLED=0`）
  - **8/8 全绿**，`files=325`，`shards_with_failure_markers=0`，耗时 `739.8s`，归档日志：`<测试根>\shards-20261128-114459-479`，basetemp 已自动清理。
- **Ruff / Copilot 契约 / 卫生 / 离线 E2E**：全部通过（`contracts ok: app=2.2.0 actions=40 knowledge=22 discovered=462`，`test_workspace_hygiene.py` + `test_offline_e2e.py` 12/12 全绿）。

## 3. 第 2 轮真机全链路 60 秒样片验收（DeepSeek + H3 2.6.0）

- **隔离环境**：`DataDir = <测试根>\acceptance\20261128-h3-cut-continuity-v2\data`（独立 `data/runtime.env` 隔离，零触碰 `<日常数据目录>`），端口 `18838 / 15198 / 18808`，复用规范 ComfyUI 安装 `<规范渲染器目录>`（零权重拷贝）。
- **LLM 与分镜实测**：使用用户授权接入的 DeepSeek（`https://api.deepseek.com`，`deepseek-flash`，密钥仅内存读取、不落仓不回显）一次走完大纲 → 分镜书（1 集 / 60s / ±10s）→ 导演分镜 + Acting Pass：
  - `DramaSceneScript.beat_energy`：`['calm', 'probe', 'probe', 'pressure', 'pressure', 'turn_pause', 'erupt', 'erupt', 'aftermath', 'aftermath']`。
  - 生成 **10 镜 / 70.0s**（落入 `60±10s` 窗，单镜 5–9s ≤ 10s 红线），景别切换丰富自然（`wide → medium wide → medium two-shot → close-up → OTS medium close → medium two-shot → MCU → CU → CU → wide two-shot`）。
  - 10/10 镜经 **`shine-h3-compiler/2.6.0`** 编译，规范警告 **0**，token 泄漏 **0**。
  - 注：`s7/s8` 在 Acting Pass 阶段因单人特写安全卫士（`SINGLE_CAST_MULTI_PERSON_PROSE`）非阻塞回退保留了干净单人镜原稿，未阻塞成片且保证高潮单人特写零角色串入。
- **真实素材与 H3 渲染参数**：本地 Qwen-Image 2.1 INT8 实时生成并晋升全部 5 项主素材；MiniMax-H3 Ref2VA INT8（**`1088 × 608 @ 24fps`、PDD 8 步、无二采、无 2X 超清化**），项目 `bfb942ed-8be0-4069-837e-2eec9f734a61`，Run `cbbdf2e0-d9ae-4d92-b42a-59659dbaa348`，总耗时 `2374s`（~39.6 分钟），状态 `COMPLETED / DELIVERED`，活跃 GPU 租约 `0`。
- **成片实测**：`1088 × 608 @ 24fps`，**`73.646s`**，`14.94 MiB`，全部分片与成片 `ffmpeg -v error -f null -` 解码通过，保留路径：`<测试根>\acceptance\20261128-h3-cut-continuity-v2\evidence\ep01_rainy_clockshop_60s_deepseek_v26.mp4`。

| 镜头 | 成片区间 | 实测时长 | 衔接 | 景别 | 台词数 | 前 1s 声压 | 对白段均值 / 峰值声压 |
|---|---|---:|---|---|---:|---:|---:|
| s01 | 0.00–6.58s | 6.58s | cut | wide（林晚单人） | 0 | -91.0 dB | -91.0 / -91.0 dB（无声零泄漏） |
| s02 | 6.58–13.87s | 7.29s | cut | medium wide（推门收伞走至柜台外） | 0 | -91.0 dB | -91.0 / -91.0 dB |
| s03 | 13.87–23.29s | 9.42s | cut | medium two-shot（慢推） | 2 | -43.1 dB | -22.3 / -5.2 dB（+20.8 dB 蓄力差） |
| s04 | 23.29–28.45s | 5.17s | cut | close-up（林晚叠绒布） | 0 | -91.0 dB | -91.0 / -91.0 dB |
| s05 | 28.45–37.87s | 9.42s | cut | OTS medium close（过肩反打陈默） | 2 | -45.9 dB | -26.4 / -8.4 dB（+19.5 dB 蓄力差） |
| s06 | 37.87–45.87s | 8.00s | cut | medium two-shot（单次掏钥匙放台面） | 0 | -91.0 dB | -91.0 / -91.0 dB |
| s07 | 45.87–53.16s | 7.29s | cut | medium close-up（林晚单人僵住抬眼） | 0 | -91.0 dB | -91.0 / -91.0 dB |
| s08 | 53.16–59.74s | 6.58s | cut | close-up（林晚单人爆发质问） | 1 | -54.2 dB | -20.4 / -4.9 dB（+33.8 dB 爆发差） |
| s09 | 59.74–67.03s | 7.29s | cut | close-up（陈默单人反打沙哑回应） | 1 | -52.0 dB | -21.1 / -3.5 dB（+30.9 dB 蓄力差） |
| s10 | 67.03–73.61s | 6.58s | cut | wide two-shot（远中景对峙收尾） | 0 | -91.0 dB | -91.0 / -91.0 dB |

### 逐帧视觉复核结果（全部 4 项问题确认消除）
1. **进门走位与空间位置**（[verify_issue1_entry_s1_s2_s3.png](../evidence/20261128-h3-cut-continuity-v2/verify_issue1_entry_s1_s2_s3.png)）：`s2` 完整演出推门 → 收伞 → 走至木柜台外侧站定，`s3` 衔接中陈默始终位于柜台外侧（左）、林晚始终位于柜台内侧（右），**无瞬移**。
2. **单把钥匙放置动作**（[verify_issue2_single_key_s6.png](../evidence/20261128-h3-cut-continuity-v2/verify_issue2_single_key_s6.png)）：`s6` 0.3s 双手空按台面 → 1.3–3.3s 伸手探口袋 → 4.2–5.0s 掏出单把银钥匙轻推至怀表旁 → 5.8–7.6s 收手站定，台面仅存 1 把钥匙与 1 只怀表，**无重复掏出覆盖**。
3. **高潮对峙正反打与柜台两侧锁定**（[verify_issue3_srs_confrontation_s6_s9.png](../evidence/20261128-h3-cut-continuity-v2/verify_issue3_srs_confrontation_s6_s9.png)）：`s6 → s7（林晚 MCU）→ s8（林晚 CU 向左质问）→ s9（陈默 CU 向右回应）` 全程 180° 视线轴线精准对齐，林晚始终保持在柜台内侧，**无穿柜台瞬移**。
4. **结尾远中景道具落幅**（[verify_issue4_ending_payoff_s9_s10.png](../evidence/20261128-h3-cut-continuity-v2/verify_issue4_ending_payoff_s9_s10.png)）：`s10` 干净切回远中景双人对峙，黄铜怀表与生锈银钥匙静卧在暖黄台灯光下的木柜台上，双手自然摆放，**无男主手凭空举钥匙冒出**。

## 4. 竣工清理与日常工作台交还

- 隔离栈已通过 `scripts/stop_shine.py --data-dir <测试根>\acceptance\20261128-h3-cut-continuity-v2\data` 按身份停止：隔离端口 `18838 / 15198 / 18808 / 8188` 监听数为 **0**，活跃 GPU 租约 **0**。
- 隔离运行目录（含临时 `data/secrets` 与分片渲染目录）及临时诊断目录 `<测试根>\diag_20261128_cut_continuity` 已移除，先卸载 junction 再删隔离数据目录，零触碰 `<日常数据目录>\comfyops\isolated-packages`；保留最终样片证据目录 `<测试根>\acceptance\20261128-h3-cut-continuity-v2\evidence`（`18.4 MiB`，含 `ep01_rainy_clockshop_60s_deepseek_v26.mp4` `14.94 MiB`）。
- 执行 `scripts/clean_test_workspace.py --work-root <测试根> --apply --prune-links`（错误 0 条），清理后 `<测试根>` 总占用 **`496.2 MiB`**（≤ 2 GiB 预算）。
- 仓库根 `.env` SHA-256 逐字节一致：`931B8BBAAE2E803225ACD56A00E512329509EB00F5F99025B9A98F355C25D5C8`。
- 经日常入口 `scripts/start_dev.ps1 -Update` 启动并复验：API `:8000` `status=ok`、`worker_online=True`、Web `:5173` HTTP 200、`data_dir=<日常数据目录>`、原 **18** 个日常作品完整可见。
