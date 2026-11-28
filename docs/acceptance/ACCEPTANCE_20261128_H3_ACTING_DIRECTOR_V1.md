# 验收报告：H3 演技驱动升级与时长控制简化 V1（2026-11-28）

- 方案文档：[plan_20261127_h3_acting_director_v1.md](../plan_20261127_h3_acting_director_v1.md)
- 基线 commit：`fc936ea774da9618ac0e9ecca612f5894a64dede`（`main`，未提交，未 push）
- 版本顺延说明：基线中 `5070f50` 已发布 `shine-h3-compiler/2.4.0`（接缝色调锁定），依 `AGENTS.md` §3.2 不可变历史版本铁律，保留 `2.4.0` 逐字节不变（冻结为 `LEGACY_CONTINUITY_COLOR_V24_COMPILER_VERSION`），本次演技驱动编译器顺延升级至 **`shine-h3-compiler/2.5.0`**。

## 1. Phase 1 + Phase 2 建设摘要

1. **时长控制简化**
   - 前端 [StreamlinePanels.tsx](../../frontend/src/components/StreamlinePanels.tsx) 移除手动“单镜秒数上限”输入框，仅保留集数与每集目标秒数，展示只读说明并固定提交 `tolerance_seconds: 10`。
   - 后端 [contracts.py](../../backend/shine/director/contracts.py) 与 [api_streamline.py](../../backend/shine/api_streamline.py) 将 `DEFAULT_TOLERANCE_SECONDS` 收敛为 `10`、`duration_target_verdict` 下界夹紧在 `4.0s`、保持 `DEFAULT_MAX_SHOT_SECONDS = 10.0` 向后兼容。
   - [scene_director.py](../../backend/shine/agents/scene_director.py) 软化 `scene_budget_seconds` 为 ±10s 弹性软目标。
   - 同步更新 `storyboard_book_generate` 的 `contract_revision=3` 并经生成器刷新 Copilot 契约与知识清单，`--check` 通过。
2. **四维声学受控词表与容错归一化**
   - [schemas.py](../../backend/shine/schemas.py) 新增 `VocalDelivery`（`volume / pace / voice_quality / pitch`）与 `DialogueLine.delivery`（空值序列化省略，历史快照哈希零漂移）。
   - [crew_salvage.py](../../backend/shine/agents/crew_salvage.py) 新增 `normalize_vocal_delivery()` 别名/同义词折叠与单维软降级，修复 Python 3.11+ `types.UnionType` 嵌套模型归一化，并补充 `DramaBeat` 的 `beat_type` / `beat_energy` 容错折叠（真实付费 LLM 首测中实际命中并修复）。
3. **`H3PromptCompiler` 升级至 `shine-h3-compiler/2.5.0`**
   - [compiler.py](../../backend/shine/h3/compiler.py) 冻结 `2.4.0` 至 `LEGACY_240_VERSIONS`；`2.5.0` 将静态环境（`Space / Light / Air`）与裁剪后的 `Background story facts` 前置，按时间轴交错输出 `timeline` 与 `dialogue`，并在 `<d>` 标签外渲染四维声学短语（无声镜零人声词泄漏）。
   - [spec_review.py](../../backend/shine/h3/spec_review.py)、[manual4/h3/spec_review.py](../../backend/shine/manual4/h3/spec_review.py)、[rulebook.py](../../backend/shine/h3/rulebook.py) 统一共享 `DIALOGUE_SENTENCE_RE`，对 `2.5.0` 声学修饰零 `H3_TIMING` 误报。
4. **整场张力曲线 + 2–4 镜表演组统筹 Acting Pass**
   - [film_contracts.py](../../backend/shine/agents/film_contracts.py) 与 [crew_prompts.py](../../backend/shine/agents/crew_prompts.py) 新增可选 `DramaBeat.beat_energy`（`calm / probe / pressure / turn_pause / erupt / aftermath`）。
   - [continuity_engine.py](../../backend/shine/agents/continuity_engine.py) 在唯一裁决口补充 `detect_bare_emotion_words()` 轻量 `BARE_EMOTION_WORD` 告警。
   - 新增 [acting_director.py](../../backend/shine/agents/acting_director.py)：0-token 表演段落分组 `group_performance_segments()`、自适应空拍与安全台词平移 `compute_pre_speech_budget()`、极简增量 `ActingGroupPatch` 契约、五官解剖拆解提示词、组间 `carry_over_state` 接力、单镜至多 1 次定向重试与非阻塞 `ACTING_POLISH_FALLBACK` 降级。
   - [film.py](../../backend/shine/agents/film.py) 与 [api_film.py](../../backend/shine/api_film.py) 挂载 Acting Pass 至 `SceneShotBoard` 落镜链路并新增单镜重导接口 `POST /api/v1/shots/{shot_id}/repolish-acting`（复用 `revise_shot_record` 唯一写入口，同步登记事件码 `SHOT_ACTING_REPOLISHED`）。

## 2. 离线全量回归证据

- **后端 8 分片全量**：`.\run_backend_shards.ps1 -WorkRoot <测试根> -Shards 8`（`CODEBUDDY_SAFE_DELETE_ENABLED=0`）
  - 首轮 7/8 通过，`chunk-3` 在 `test_audit_event_codes.py` 检出新事件码 `SHOT_ACTING_REPOLISHED` 缺前端登记（已补齐）。
  - 复跑 **8/8 全绿**，`files=326`，`shards_with_failure_markers=0`，耗时 `728.4s`，归档日志：`<测试根>\shards-20261128-085012-101`，basetemp 已自动清理。
- **前端全量**：`npx vitest run --no-file-parallelism` **100 文件 / 524 用例全绿**，`npx tsc --noEmit` 0 错误，`npm run build` 成功。
- **Ruff / 契约 / Alembic / 卫生检查**：改动与新增文件 `ruff check` 全绿；`generate_shine_copilot_contracts.py --check` 与 `generate_copilot_knowledge.py --check` 通过；`alembic heads` 单头 `f180f0010010`；`test_workspace_hygiene.py` 10/10 通过。
- **新增单测**：[test_h3_compiler_v24_acting.py](../../backend/tests/test_h3_compiler_v24_acting.py)（6 例）、[test_acting_director_pass.py](../../backend/tests/test_acting_director_pass.py)（7 例，含 HTTP API 集成测试）全绿。

## 3. 隔离真机全链路 1 集 60 秒样片验收

- **隔离环境**：`DataDir = <测试根>\acceptance\20261128-h3-acting-director\data`（独立 `.env`，`SHINE_RUNTIME_ENV_PATH` 隔离），端口 API `18828` / Web `15188` / Supervisor `18798`，渲染器采用本机规范安装 `<规范渲染器目录>`（未复制权重）。
- **测试剧本与付费 LLM**：单集双人对手戏《雨夜钟表铺》（林晚 × 陈默），使用项目内付费 LLM（`MiniMax-M3.1-flash`）全自动走完大纲 → 分镜书（1 集 / 60s / ±10s）→ `SeriesArchitecture` → `DramaSceneScript` → `SceneShotBoard` → **Acting Pass**。
  - `DramaSceneScript.beat_energy` 完整覆盖：`['calm', 'probe', 'probe', 'pressure', 'turn_pause', 'pressure', 'erupt', 'erupt', 'aftermath']`。
  - **Acting Pass 实测**：9 镜自动分为 3 组 `[4, 2, 3]`，**9/9 全部精修成功（`fallback_shots=0`）**，`salvage_notes_count=1`（软折叠 1 处声学词未阻塞），计划总时长 **66.0s**（落入 `60±10s` 弹性窗），单镜均 6–8s（≤10s 红线）。
  - 9/9 镜经 `shine-h3-compiler/2.5.0` 编译，规范审阅警告数均为 **0**。
- **真实素材**：本地 Qwen-Image 2.1 INT8 实时生成并晋升锁定全部 5 项主素材（林晚、陈默、雨夜钟表铺、停在十二点的黄铜怀表、生锈的银钥匙）。
- **真实 H3 渲染参数**（严格按用户指定）：
  - 分辨率：**0.8 倍横屏 `1088 × 608 @ 24fps`**（`frame_size("landscape", 0.8)`）
  - 加速与开关：**PDD 8 步（`pdd8`）、无二采（`second_pass.enabled=false`）、无 2X 超清化（`upscale.enabled=false`）**，模型 `h3-ref2va-int8-convrot`（Ref2VA only）
- **实测结果**：
  - 项目 `bffa69e1-85ce-46fb-81db-ab520e4d157f`，Run `63621278-34b8-40fc-822a-0f2c4d23e786`，总耗时 `2338s`（~38.9 分钟），状态 `COMPLETED`，交付单 `58cab595-8615-409c-ab8e-178dde9ba6e1` `DELIVERED`，活跃 GPU 租约 `0`。
  - **9/9 镜头全部 `PASSED`（`quality_verification=real`）**，无 OOM，全部分片与成片 `ffmpeg -v error -f null -` 解码通过。
  - **成片实测**：`1088 × 608`，`24fps`，**`67.063s`（1,609 帧）**，`12.53 MiB`，已留存在本机证据目录 `<测试根>\acceptance\20261128-h3-acting-director\evidence\ep01_rainy_clockshop_60s_acting_v1.mp4`。

| 镜头 | 时长 | 衔接 | 台词数 | 前 1s 均值声压 | 对白段均值 / 峰值声压 | 四维声学配置 |
|---|---:|---|---:|---:|---:|---|
| s1 | 7.29s | cut | 0 | -120.0 dB | -120.0 / -120.0 dB | 无声镜（零泄漏） |
| s2 | 6.58s | last_frame | 0 | -120.0 dB | -120.0 / -120.0 dB | 无声镜（零泄漏，接缝 MAE=3.39） |
| s3 | 8.00s | cut | 2 | -54.6 dB | -40.1 / -19.0 dB | `conversational/measured/crisp/steady` + `soft/slow/flat/low_flat` |
| s4 | 8.00s | last_frame | 2 | -54.9 dB | -34.1 / -13.1 dB | `raised/halting/strained/rising` + `conversational/measured/crisp/falling` |
| s5 | 8.00s | cut | 0 | -120.0 dB | -120.0 / -120.0 dB | 无声镜（零泄漏） |
| s6 | 8.00s | last_frame | 0 | -120.0 dB | -120.0 / -120.0 dB | 无声镜（零泄漏） |
| s7 | 7.29s | cut | 1 | -51.8 dB | -34.6 / -18.1 dB | **1.2s 空拍蓄力** → `raised/brisk/strained/rising`（爆发峰值） |
| s8 | 7.29s | last_frame | 1 | -52.4 dB | -45.1 / -14.3 dB | **1.2s 空拍蓄力** → `measured/hoarse/falling`（较 s7 均值低 10.5 dB） |
| s9 | 7.29s | last_frame | 0 | -120.0 dB | -120.0 / -120.0 dB | 无声镜（零泄漏） |

- 高潮帧组证据图：[acting_climax_contact_sheet.png](../evidence/20261128-h3-acting-director/acting_climax_contact_sheet.png)（清晰呈现 `s7` 0.1s 闭口凝视 → 0.6s 蹙眉蓄力 → 1.2s 启唇 → 3.0s 爆发质问 → 6.2s 闭口余韵，以及 `s7 → s8` 无缝 `last_frame` 接拍）。

### 实测中发现并顺手修补的小细节
1. 真实付费 LLM 在 `DramaSceneScript` 中曾把 `beat_energy` 值填入 `beat_type`，已在 [crew_prompts.py](../../backend/shine/agents/crew_prompts.py) 明列 `beat_type` 枚举并在 [crew_salvage.py](../../backend/shine/agents/crew_salvage.py) 加入确定性折叠，复跑即一次通过。
2. 当某镜原始首句台词已晚于 `1.2s`（如 `s8` 的 `1.3s`）时，`compute_pre_speech_budget` 原将 `applied_pause_s` 夹在 `1.2s` 导致 `window[1]` 略早于台词；验收后已在 [acting_director.py](../../backend/shine/agents/acting_director.py) 收敛为 `round(first_start, 3)` 并补单测回归。
3. 未做 `2.3.0 / 2.4.0` 与 `2.5.0` 同种子 A/B 盲测打分，亦未做人工逐句听感评估；上述声压与抽帧为客观物理量与视觉抽检记录。

## 4. 竣工清理与日常工作台交还

- 隔离栈已经 `scripts/stop_shine.py --data-dir` 按身份停止：`18828 / 15188 / 18798 / 8188` 监听数为 0，GPU 租约 0。
- `scripts/clean_test_workspace.py --work-root <测试根> --apply --prune-links`：共清理 **826.0 MiB**（错误 0 条），保留 `acceptance/20261128-h3-acting-director/evidence`（13.9 MiB，含 60s 样片 MP4 与抽帧图）；清理后 `<测试根>` 总占用 **477.7 MiB**（≤ 2 GiB 预算）。
- 仓库根 `.env` SHA-256 复核一致：`931B8BBAAE2E803225ACD56A00E512329509EB00F5F99025B9A98F355C25D5C8`。
- 经日常入口 `scripts/start_dev.ps1 -Update`（与 `启动 Shine 工作台.cmd` 同源）启动并复验：API `:8000` 在线、`worker_online=True`、Web `:5173` HTTP 200、`data_dir=<日常数据目录>`、日常库原 **18** 个作品完整可见。
- 保留既有未跟踪工作区目录 `.trae/` 与 `.vite/`。
