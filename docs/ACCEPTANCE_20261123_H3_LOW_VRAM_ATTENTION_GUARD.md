# 验收报告：MiniMax H3 `MiniMaxLowVRAMAttention` 高水位门控与 OOM 自动兜底

- **基准提交**：`main` @ `41f8be93afd1a0c005abdde0a2103caab05e58fd`（包含方案基线 `f5c0c30f047368dbab27ea2c3fa9495147278e3a` 及最新主线修复）
- **任务基准方案**：[PLAN_20261123_H3_LOW_VRAM_ATTENTION_GUARD.md](file:///f:/ClaudeWorkSpace/projects/shine-video-studio/docs/PLAN_20261123_H3_LOW_VRAM_ATTENTION_GUARD.md)（取自 `origin/feat/h3-low-vram-attention-guard`）
- **画质底线**：严格零画质损失（节点 `"13"` `MiniMaxChunkFeedForward` 常驻；节点 `"15"` `MiniMaxLowVRAMAttention` 仅做数学等价的头分组分块开关，不引入 Turbo LoRA / VSA / ClipProj / 步数缩减等任何有损近似）

---

## 1. 核心架构与“三原则”落地核查

| 原则维度 | 唯一归口 | 说明 |
| :--- | :--- | :--- |
| **一套事实** | `backend/shine/comfy/vram_guard.py` | 统一提供 `align_h3_frame_count`、`video_latent_frames`、`estimate_h3_attention_tokens`、`extract_comfy_history_error`、`is_cuda_oom_error` 与默认阈值 `45_000` |
| **一个裁决** | `decide_low_vram_attention()` + `is_low_vram_guard_variant()` | 请求冻结（`services.py`）、导演包校验（`director/package.py`）、主线 Worker（`worker.py`）与统一执行器（`foundation/h3.py`）共用同一个阈值裁决口；未传画幅的兼容路径返回 `legacy_default`（`enabled=True`），不破坏历史哈希 |
| **一个执行者** | `compile_ref2va_workflow()` + `ComfyH3Executor` / 主线 `Worker` | 双流水线均经 `shine.foundation.h3` 透传至 `graph_compiler.py`；OOM 兜底仅复用既有状态机/租约与 `ComfyH3Executor` 单次重试，无第二套调度链路、无新增环境整备或生产门禁 |

---

## 2. 本机 ComfyUI / KJNodes 源码核验与 Token 口径校准

对本机安装路径 `F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base\ComfyUI` 完成源码与 Python 实测校准：

1. **节点输入签名实测**（`ComfyUI-KJNodes/nodes/minimax_nodes.py`）：
   - `MiniMaxChunkFeedForward`: `[('model', None), ('chunks', 2), ('seq_threshold', 4096)]` —— 与 `graph_compiler.py` 节点 `"13"` 一致。
   - `MiniMaxLowVRAMAttention`: `[('model', None), ('head_chunks', 4)]` —— 与 `graph_compiler.py` 节点 `"15"` 一致；启用时向 `transformer_options` 注入 `minimax_head_chunks=4` 与 `sol_take_forward=minimax_attn_lowmem_forward`，并与前置 SageAttention Patch 组合。
   - `MiniMaxH3TokenCounter`: 直接调用 `comfy.ldm.minimax.model.PackedLayout` 统计 `seq_len`。

2. **时空下采样与 `PackedLayout.seq_len` 口径校准**（`comfy_extras/nodes_minimax_h3.py` & `comfy/ldm/minimax/model.py`）：
   - **帧数对齐**：`align_frame_count` 与 `graph_compiler.py` 的 `LENGTH_EXPRESSION` 完全一致（24 fps 下对齐到 `17k + 5`）。
   - **时间压缩（关键校准项 1）**：`video_latent_t(L) = 2 if L <= 5 else ((L - 5) // 17) * 5 + 2`（例如 `5s / L=124` 时 `latent_t = 37`，而非草稿式的 `1 + (124 - 1) // 4 = 31`）。
   - **空间 Patch（关键校准项 2）**：VAE 下采样 `/16` 后，DiT `patchify_video` 执行 **`2x2` 空间 patch**（`lat_w = (W // 16 + 1) // 2 * 2`，每潜帧 token 数为 `(lat_w // 2) * (lat_h // 2)`，即约 `⌊W/32⌋ × ⌊H/32⌋`），而非草稿式未切 patch 的 `⌊W/16⌋ × ⌊H/16⌋`（后者偏大 4 倍）。
   - **目标双声道音频**：`audio_t = round(L / 24 * 40)`，`PackedLayout` 占用 `audio_t * 2` 个 token。

3. **实机 `PackedLayout.seq_len` 对照实测（零误差对齐）**：

| 档位 / 场景 | 分辨率 × 时长 × 参考 | ComfyUI `PackedLayout.seq_len`（实测） | `estimate_h3_attention_tokens().total_tokens` | 门控裁决（阈值 45,000） |
| :--- | :--- | :---: | :---: | :--- |
| `0.5` 档 5s | `672×384`, 5s (`L=124`), 2 图 | `11,266` | `11,266` | `False` (`below_threshold`) |
| `0.7` 档 5s | `960×544`, 5s (`L=124`), 2 图 | `21,328` | `21,328` | `False` (`below_threshold`) |
| `1.0` 档 3s | `1344×768`, 3s (`L=73`), 1 图 | `24,452` | `24,452` | `False` (`below_threshold`) |
| `1.0` 档 5s | `1344×768`, 5s (`L=124`), 2 图 | `40,750` | `40,750` | `False` (`below_threshold`) |
| `1.0` 档 7s | `1344×768`, 7s (`L=175`), 2 图 | `56,040` | `56,040` | **`True`** (`token_threshold_reached`) |
| `1.0` 档 10s | `1344×768`, 10s (`L=243`), 2 图 | `76,426` | `76,426` | **`True`** (`token_threshold_reached`) |
| `1.0` 档 15s | `1344×768`, 15s (`L=362`), 2 图 | `112,102` | `112,102` | **`True`** (`token_threshold_reached`) |
| `1.0` 档 5s 满图 | `1344×768`, 5s (`L=124`), 9 图 | `47,806` | `47,806` | **`True`** (`token_threshold_reached`) |

> **校准结论**：方案第 2.1 节所述的 `56,000 ~ 78,000 tokens` 显存撞墙区（`1344×768` 7s~10s）与 `45,000 tokens` 默认阈值（距 `56,040` 预留 ~20% 安全缓冲）完全基于 `PackedLayout.seq_len` 真值口径。公式按 `PackedLayout` 校准后，常规 5s 镜头（≤40,750 tokens）免除 `MiniMaxLowVRAMAttention` 分块调度开销，7s+ / 多参考图 / 视频参考高水位镜头（≥45,000 tokens）自动开启；若个别小显存卡在低水位场景偶发 OOM，由被动 CUDA OOM 单次兜底接管。

---

## 3. 变更文件清单

| 文件路径 | 变更性质 | 核心改动 |
| :--- | :--- | :--- |
| `backend/shine/comfy/vram_guard.py` | 新增 | `align_h3_frame_count`、`video_latent_frames`、`estimate_h3_attention_tokens`、`decide_low_vram_attention`、`extract_comfy_history_error`、`format_comfy_history_error`、`is_cuda_oom_error` |
| `backend/shine/comfy/graph_compiler.py` | 修改 | `_model_stack()` / `_sampling_stack()` / `compile_ref2va_workflow()` 支持条件挂载节点 `"15"`（默认 `None` 等价 `True`）；`recipe_sha256` 与 `low_vram_attention` 隔离；新增 `is_low_vram_guard_variant()` |
| `backend/shine/comfy/provider.py` | 修改 | `ComfyResult` 增加 `error_details`；`poll()` 失败时提取 `/history` `execution_error` 文本与详情 |
| `backend/shine/config.py` & `.env.example` | 修改 | 新增 `h3_low_vram_attention_token_threshold`（`SHINE_H3_LOW_VRAM_ATTN_TOKEN_THRESHOLD=45000`，允许 `>= 0`）与 `h3_oom_low_vram_retry_enabled`（`SHINE_H3_OOM_LOW_VRAM_RETRY=1`） |
| `backend/shine/foundation/h3.py` | 修改 | 重新导出 `vram_guard` 与 `is_low_vram_guard_variant`；`H3ExecutionRequest` / `H3ExecutionReceipt` 扩展；`ComfyH3Executor.execute()` 实现阈值决策、真实错误提取与 CUDA OOM 单次重编译重试 |
| `backend/shine/services.py` & `backend/shine/director/package.py` | 修改 | 预编译阶段统一调用 `decide_low_vram_attention()` 并冻结对应 `workflow_profile_hash` |
| `backend/shine/worker.py` | 修改 | 动态编译接入阈值决策；`STRATEGY_GRAPH_COMPILED` 记录可观测字段；CUDA OOM 触发 `H3_OOM_LOW_VRAM_FALLBACK_SCHEDULED` 并重新入队单次保底重试（含 `is_low_vram_guard_variant` 哈希校验与 `RELEASED` 租约保护） |
| `backend/shine/manual4/comfy/h3_adapter.py` & `backend/shine/manual4/worker.py` | 修改 | 透传 `low_vram_attention`、阈值与重试开关；`_persist_submit_intent` 递增 `attempt_no`、将前一次 OOM 失败记为 `FAILED / M4_CUDA_OOM_RETRYING` 并写 `M4_H3_OOM_LOW_VRAM_FALLBACK_SCHEDULED` 审计事件 |
| `frontend/src/auditEventCodes.json` | 修改 | 注册 `H3_OOM_LOW_VRAM_FALLBACK_SCHEDULED`，保持前后端审计码表一致 |
| `backend/tests/test_h3_low_vram_guard.py` | 新增 | 19 个完整自动化用例覆盖公式、边界、拓扑重连、哈希隔离、OOM 解析、Manual4/Executor 与主线双链路单次兜底及防死循环 |
| `docs/PLAN_20261123_H3_LOW_VRAM_ATTENTION_GUARD.md` | 新增 | 从 `origin/feat/h3-low-vram-attention-guard` 同步的设计方案全文 |
| `docs/ACCEPTANCE_20261123_H3_LOW_VRAM_ATTENTION_GUARD.md` | 新增 | 本验收报告 |

---

## 4. 测试与验证结果

### 4.1 专项与主线回归

| 验证项 | 命令 | 结果 |
| :--- | :--- | :--- |
| LowVRAM 门控与 OOM 闭环专项 | `backend\.venv\Scripts\python.exe -m pytest backend/tests/test_h3_low_vram_guard.py -v` | 19/19 通过 |
| 审计码表一致性 | `backend\.venv\Scripts\python.exe -m pytest backend/tests/test_audit_event_codes.py -v` | 3/3 通过 |
| 方案第 5.1 节指定回归集合（含离线 E2E、Production Series、Manual4 隔离/统一执行/生产） | `backend\.venv\Scripts\python.exe -m pytest backend/tests/test_graph_compiler.py backend/tests/test_mode_capabilities.py backend/tests/test_h3_low_vram_guard.py backend/tests/manual4/test_isolation_static.py backend/tests/manual4/test_p3_unified_h3.py backend/tests/manual4/test_p4_production.py backend/tests/test_offline_e2e.py backend/tests/test_production_series.py backend/tests/test_dynamic_strategy_pipeline.py backend/tests/test_strategy_worker_submission.py backend/tests/test_submission_reconciliation.py -q` | 164/164 通过（0 失败） |
| Ruff 代码规范检查 | `backend\.venv\Scripts\ruff.exe check <所有新增/修改后端文件>` | 0 错误（`All checks passed!`） |

### 4.2 后端全量 8 分片并行回归（最终修改后执行）

- **命令**：`pwsh -NoProfile -File .\run_backend_shards.ps1 -WorkRoot F:\ShineTest -Shards 8`
- **范围**：全仓库 **305** 个测试文件均分 8 分片并行，总耗时 **852.5s**
- **日志目录**：`F:\ShineTest\shards-20261123-200949-909`
- **结果**：**8/8 分片全绿**（`shards_with_failure_markers=0`，全部退出码 `0`）：
  - `chunk-0`: clean (exit 0, 422.7s, 40 files)
  - `chunk-1`: clean (exit 0, 643.3s, 37 files)
  - `chunk-2`: clean (exit 0, 807.8s, 36 files)
  - `chunk-3`: clean (exit 0, 851.9s, 36 files)
  - `chunk-4`: clean (exit 0, 722.1s, 37 files)
  - `chunk-5`: clean (exit 0, 766.6s, 42 files)
  - `chunk-6`: clean (exit 0, 835.3s, 39 files)
  - `chunk-7`: clean (exit 0, 828.7s, 38 files)

### 4.3 前端构建与测试

- **构建**：`Set-Location frontend; npm.cmd run build` (`tsc -b && vite build`) —— 通过（1.42s）。
- **单测**：`npx vitest run --no-file-parallelism` —— 93/94 文件（487/488 用例）通过；仅既有的 `src/test/themeCss.test.ts`（`--text-muted` 缺失）失败，与本次后端及 `auditEventCodes.json` 改动无关。

---

## 5. 实机 GPU 核验状态与遗留风险

- **已完成本机实测**：
  - 本机 ComfyUI（`F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base\ComfyUI`）`ComfyUI-KJNodes` 节点输入签名核验通过。
  - `PackedLayout.seq_len` 与 Shine `estimate_h3_attention_tokens()` 跨 `0.5` / `0.7` / `1.0`（3s~15s、1~9 图）实测 100% 一致。
  - 离线编译图拓扑（含与不含节点 `15`）及所有 `int8+pdd8`、`int8+native20`、`int8+pdd8+second_pass`、`lowend+native20` 组合校验通过。
- **未能在本次会话实测的项目（待真机 GPU 环节补充）**：
  - 本机 `http://127.0.0.1:8188/system_stats` 在本次验收期间处于未启动状态（`ComfyUI DOWN`），因此 **低水位（`< 45,000`）与高水位（`>= 45,000`）真实 GPU 端到端出片耗时对比、峰值显存实测与真机 CUDA OOM 重试** 尚未执行，需在启动受管 ComfyUI 渲染器后按需补测。
- **遗留风险与回滚手段**：
  - 若某张较小显存 GPU 在 `21,328 ~ 40,750 tokens` 区间触发 OOM，被动单次重试会自动挂载 `MiniMaxLowVRAMAttention` 兜底；若需要全部镜头直接常开，在 `.env` 设置 `SHINE_H3_LOW_VRAM_ATTN_TOKEN_THRESHOLD=0` 即可瞬时无损回退。
  - 设置 `SHINE_H3_OOM_LOW_VRAM_RETRY=0` 可单独关闭被动 OOM 重试以排查显存问题。
