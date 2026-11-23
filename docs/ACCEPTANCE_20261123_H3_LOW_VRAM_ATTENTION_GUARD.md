# 验收报告：MiniMax H3 `MiniMaxLowVRAMAttention` 高水位门控与 OOM 自动兜底

- **基准提交**：`main` @ `2254ae1a8634169fc2c5c36c79f0d5d7f3ad9295`（含方案基线 `f5c0c30f047368dbab27ea2c3fa9495147278e3a`、首轮实现 `4e3001f` 与首轮测试 `2254ae1`）
- **任务基准方案**：[PLAN_20261123_H3_LOW_VRAM_ATTENTION_GUARD.md](PLAN_20261123_H3_LOW_VRAM_ATTENTION_GUARD.md)
- **本报告覆盖两轮**：首轮为实现与离线验收；收尾复验轮（本版）对首轮结论逐项独立复核，修复复核中发现的缺陷，并在 RTX 5080 上完成首轮缺失的实机核验。
- **画质底线**：严格零画质损失。节点 `"13"`（`MiniMaxChunkFeedForward`）常驻；节点 `"15"`（`MiniMaxLowVRAMAttention`）只做数学等价的头分组分块。**真机证据：挂载与不挂载节点 `"15"` 的成片 MP4 逐字节相同**（§5.3）。

---

## 0. 结论

| 方案条目 | 结论 | 证据等级与说明 |
| :--- | :--- | :--- |
| §2.1.1 节点 `"13"` 常驻 | **PASS** | 真机：8 次真实出片 ComfyUI 实际执行的图均含 `MiniMaxChunkFeedForward` |
| §2.1.2 阈值 45,000 按需挂载节点 `"15"` | **PASS** | 离线 21 例；真机：低水位省略、高水位挂载，与审计裁决逐次一致（§5.2） |
| §2.1.3 CUDA OOM 单次兜底 | **部分验证** | 离线闭环全部 PASS（Executor、Manual4、主线裸队列、双重 OOM 与非 OOM 不重试）。**真机未能触发 OOM**：7s / 10s / 15s 三档不挂保护全部首次通过，受管渲染器路径上的兜底没有真机证据（§5.5、§7.1） |
| §2.1.4 双链路统一、零破坏兼容 | **PASS（收尾轮修复后）** | 首轮实现有两处兼容回归（存量冻结请求无法渲染、存量镜头无法重拍），收尾轮修复并由 2 条新测试钉住（§4.1、§4.2） |
| §5.1 自动化用例 | **PASS** | 专项、相邻模块、全量 8 分片（§6） |
| §5.2 实机核验 | **PASS** | Token 口径对照真实 `PackedLayout`（§2.2）；低/高水位真实提交图与画质（§5） |

---

## 1. 核心架构与「三原则」落地核查

| 原则维度 | 唯一归口 | 说明 |
| :--- | :--- | :--- |
| **一套事实** | `backend/shine/comfy/vram_guard.py` | 统一提供 `align_h3_frame_count`、`video_latent_frames`、`estimate_h3_attention_tokens`、`extract_comfy_history_error`、`is_cuda_oom_error` 与默认阈值 `45_000` |
| **一个裁决** | `decide_low_vram_attention()` + `low_vram_attention_form_of_graph()` | 请求冻结（`services.py`）、导演包校验（`director/package.py`）、主线 Worker 与统一执行器共用同一个阈值裁决口。一张镜头图自门控起有**两种合法形态**（节点 `"15"` 挂载 / 不挂载），`low_vram_attention_form_of_graph()` 是唯一知道这对形态的函数：Worker 用它认出存量冻结请求点名的是哪一种，重拍用它把这对图判为同一绑定 |
| **一个执行者** | `compile_ref2va_workflow()` + `ComfyH3Executor` / 主线 `Worker` | 主线与 Manual4 均经 `shine.foundation.h3` 透传到 `graph_compiler.py`；OOM 兜底复用既有状态机与租约，不另起调度链路 |

**冻结请求是权威。** `make_render_request` 在建任务时裁决一次并把图哈希冻结进请求；Worker 不重新裁决，只认冻结哈希点名的那种合法形态。只有 OOM 兜底尝试例外：它必须是「冻结图不挂保护」的那一种，才允许改提交挂载保护的图。

---

## 2. Token 口径

### 2.1 本机 ComfyUI 的真实实现（源码位置）

核验对象：`F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base\ComfyUI`。

| 事实 | 位置 | 实现 |
| :--- | :--- | :--- |
| 帧数对齐 | `comfy_extras/nodes_minimax_h3.py::align_frame_count` | `while n % 17 != 5: n += 1`（向上取整到 `17k+5`） |
| 时间压缩 | 同文件 `::video_latent_t` | `2 if n <= 5 else ((n - 5) // 17) * 5 + 2` |
| 空间 patch | `comfy/ldm/minimax/model.py::patchify_video` / `_frame_grid` | `patch_size=(1, 2, 2)`，单潜帧 token 数 = `(lat_h // 2) × (lat_w // 2)` |
| 目标音频 | `nodes_minimax_h3.py::temporal_shape` | `round(帧数 / 24 × 40) × 2`（40 Hz 立体声） |
| 潜空间 H/W 补齐 | KJNodes `MiniMaxH3TokenCounter`（镜像 `model_base.MiniMaxH3.extra_conds`） | `(H + 1) // 2 * 2` |
| 节点签名 | `ComfyUI-KJNodes/nodes/minimax_nodes.py` | `MiniMaxChunkFeedForward(model, chunks=2, seq_threshold=4096)`、`MiniMaxLowVRAMAttention(model, head_chunks=4)` |

### 2.2 实测对照：真实 `PackedLayout.seq_len` 与 Shine 估算

用 ComfyUI **内嵌解释器**（Python 3.13.11 / torch 2.10.0+cu130）直接构造真实的 `PackedLayout`，与后端虚拟环境里的 `estimate_h3_attention_tokens()` 对比。脚本：`.local-acceptance/lowvram-20261123/verify_seq_len.py`，不加载任何权重。

| 配置 | 真实 `seq_len` | Shine `total_tokens` | 差 | 门控（阈值 45,000） |
| :--- | ---: | ---: | ---: | :--- |
| 672×384 · 5s · 2 图 | 11,266 | 11,266 | 0 | 关 |
| 960×544 · 5s · 2 图 | 21,328 | 21,328 | 0 | 关 |
| 1344×768 · 3s · 1 图 | 24,452 | 24,452 | 0 | 关 |
| 1344×768 · 5s · 2 图 | 40,750 | 40,750 | 0 | 关 |
| **1344×768 · 5s · 5 图** | 43,774 | 43,774 | 0 | 关（边界） |
| **1344×768 · 5s · 6 图** | 44,782 | 44,782 | 0 | 关（边界） |
| 1344×768 · 5s · 9 图 | 47,806 | 47,806 | 0 | 开 |
| 1344×768 · 7s · 2 图 | 56,040 | 56,040 | 0 | 开 |
| 1344×768 · 10s · 2 图 | 76,426 | 76,426 | 0 | 开 |
| 1344×768 · 15s · 2 图 | 112,102 | 112,102 | 0 | 开 |
| 1024×576 · 158 帧 · 6 图 | 32,078 | 32,078 | 0 | 关 |
| 1024×576 · 5s · 3 图 | 24,478 | 24,478 | 0 | 关 |
| 1024×576 · 10s · 6 图 | 46,762 | 46,762 | 0 | 开 |
| 1024×576 · 15s · 2 图 | 65,014 | 65,014 | 0 | 开 |
| 1024×576 · 15s · 6 图 | 67,318 | 67,318 | 0 | 开 |
| 1344×768 · 5s · 5 图 + 1 音频 | 44,286 | 44,286 | 0 | 关 |

**16 / 16 零误差**，含首轮未验的 5 图 / 6 图阈值边界、制作档与带音频参考的组合。

### 2.3 适用前提与偏差界（对首轮「零误差对齐」表述的修正）

§2.2 的相等在以下三个前提下成立。首轮报告未写明前提，这里补全：

1. **文本条件按 `text_len = 1024` 构造。** 真实值是 `cond.shape[1]`，不是常数：`comfy/text_encoders/qwen3vl.py` 的分词器是 `pad_to_max_length=False, max_length=99999999`，所以真实长度 = 提示词 token + 每张参考图的视觉 token + 标记 token。真实值与估算的关系为 `真实值 = 估算值 − 1024 + T_prompt + Σ(n_img + 约 6)`，其中 `n_img` 是该参考图的视觉 token 数（等面积匹配时等于目标单帧 token 数）。**按公式推算**（未实测 `T_prompt`）：1344×768 未受保护的最坏情形是 6 图 5s（估算 44,782），真实值约 49,842 + `T_prompt`，仍比方案所述最低撞墙点 56,040 低 ≥ 10%；小画幅（如 672×384）方向相反，估算偏大，属于保守方向。参考视频的视觉 token 未计入，带参考视频的镜头余量会更窄。
2. **参考图按「不小于目标面积且等面积匹配」计。** `ref_image_size="match"` 只缩小不放大，参考图小于目标面积时真实 `ref_img` 行数更少，估算偏大（保守）。
3. **音频参考按固定 512 行计。** 对应 6.4 s 参考音频；2–15 s 的参考音频真实值在 160–1,200 行之间。

结论：45,000 的默认阈值在全部现有参考数量上限内仍保留 ≥ 10% 的余量，**默认值不需要调整**；但「估算等于真实值」只在上述前提下成立。

---

## 3. 变更文件清单

### 3.1 首轮（`4e3001f`、`2254ae1`）

| 文件路径 | 核心改动 |
| :--- | :--- |
| `backend/shine/comfy/vram_guard.py` | 新增：帧对齐、潜帧换算、Token 估算、阈值决策、`/history` 错误解析、CUDA OOM 识别 |
| `backend/shine/comfy/graph_compiler.py` | `_model_stack()` / `_sampling_stack()` / `compile_ref2va_workflow()` 支持条件挂载节点 `"15"`；`recipe_sha256` 与 `low_vram_attention` 隔离；`is_low_vram_guard_variant()` |
| `backend/shine/comfy/provider.py` | `ComfyResult.error_details`；`poll()` 提取 `/history` 的 `execution_error` |
| `backend/shine/config.py` & `.env.example` | `SHINE_H3_LOW_VRAM_ATTN_TOKEN_THRESHOLD=45000`、`SHINE_H3_OOM_LOW_VRAM_RETRY=1` |
| `backend/shine/foundation/h3.py` | 重导出 `vram_guard`；`H3ExecutionRequest` / `H3ExecutionReceipt` 扩展；`ComfyH3Executor.execute()` 的阈值决策与单次 OOM 重编译重试 |
| `backend/shine/services.py` & `backend/shine/director/package.py` | 请求冻结与导演包校验调用 `decide_low_vram_attention()` |
| `backend/shine/worker.py` | 动态编译接入阈值决策；审计字段；CUDA OOM 触发 `H3_OOM_LOW_VRAM_FALLBACK_SCHEDULED` 并重新入队单次保底重试 |
| `backend/shine/manual4/comfy/h3_adapter.py` & `backend/shine/manual4/worker.py` | 透传阈值与重试开关；两次 attempt 落库；`M4_H3_OOM_LOW_VRAM_FALLBACK_SCHEDULED` |
| `frontend/src/auditEventCodes.json` | 注册 `H3_OOM_LOW_VRAM_FALLBACK_SCHEDULED` |
| `backend/tests/test_h3_low_vram_guard.py` | 19 例 |

### 3.2 收尾复验轮（本次）

| 文件路径 | 改动 |
| :--- | :--- |
| `backend/shine/comfy/graph_compiler.py` | 新增唯一裁决 `low_vram_attention_form_of_graph()`；`is_low_vram_guard_variant()` 改为复用它；`model_identity` 的估算键改名 |
| `backend/shine/comfy/vram_guard.py` | 决策原因新增 `frozen_request` |
| `backend/shine/foundation/h3.py` | 导出 `low_vram_attention_form_of_graph` |
| `backend/shine/worker.py` | 编译前识别冻结请求点名的形态并服从；审计键名避开脱敏规则 |
| `backend/shine/api_segments.py` | 重拍的绑定比对把同一镜头的两种合法图判为同一 H3 工作流绑定 |
| `backend/tests/test_h3_low_vram_guard.py` | 新增 2 例（21 例）；审计断言由 `is not None` 加强为真实数字 |
| `frontend/src/styles.css` | `.route-preview-note` 改用已定义的 `--ui-text-muted` |

---

## 4. 收尾复验轮发现并修复的缺陷

四项都是先写出失败测试、在未修改的实现上确认如实失败，再修复。

### 4.1 存量冻结请求无法渲染（兼容回归，首轮报告未提及）

- **现象**：门控让图模板哈希首次依赖于宽 / 高 / 时长。升级前冻结的请求，其 `workflow_profile_hash` 是**带节点 `"15"`** 的图；新 Worker 对低水位镜头编译出不带节点 `"15"` 的图，哈希不等 → `STRATEGY_COMPILE_WORKFLOW_IDENTITY_CHANGED`，**一条都不提交**。升级时所有已排队、已暂停、待恢复的存量任务都会中招，违反方案 §2.1.4「零破坏兼容」。
- **修法**：`low_vram_attention_form_of_graph(frozen_hash, …)` 是唯一知道「两种合法形态」的函数。Worker 编译前先问它冻结哈希是哪一种；命中的就按冻结请求的形态编译（审计原因记 `frozen_request`）。既不是任一合法形态的哈希仍然按漂移图拒绝。
- **测试**：`test_worker_honors_a_request_frozen_before_the_gate`——存量请求照常渲染、提交的图带节点 `"15"`、审计 `frozen_request`；伪造哈希仍被拒绝且不提交。

### 4.2 存量镜头无法重拍（兼容回归，首轮报告未提及）

- **现象**：`POST /projects/{id}/generation-segments/{shot_id}/regenerate` 逐字段比对源任务与新任务的渲染绑定，`workflow_profile_hash` 不等即 `409 GENERATION_RENDER_BINDINGS_CHANGED`。升级后既有作品里所有低水位镜头都无法重拍。
- **修法**：`MiniMaxLowVRAMAttention` 是数学等价的显存开关，不是工作流变更（方案 §3.2 已规定它不进 `recipe_sha256`）。重拍通过同一个裁决函数，把同一镜头的两种合法图判为同一 H3 工作流绑定；其余绑定逐字比对不变；静态方案任务仍按原样比对。
- **测试**：`test_retake_keeps_the_bindings_of_a_shot_frozen_before_the_gate`——存量镜头重拍返回 202，新任务按门控冻结；伪造哈希仍 409。

### 4.3 审计字段被密钥脱敏规则误伤（真机才暴露）

- **现象**：真机审计里 `estimated_attention_tokens` 落库为 `[redacted]`。`security.sanitize_payload` 把任何含 `token` 的键当作凭据脱敏，方案要求的可观测字段在审计里读不到。首轮测试只断言 `is not None`，`"[redacted]"` 也能通过。
- **修法**：不放宽脱敏规则（它是防密钥落盘的唯一闸门），改审计键名：`estimated_attention_seq_len`，兜底事件统一用 `low_vram_attention_threshold`。测试改为断言真实数字（11,014）。真机 R2 之后的审计已能读到真实值（§5.2）。
- **与方案文本的差异**：方案 §3.5 写的是 `estimated_attention_tokens` / `threshold`，此处因上述原因改名。

### 4.4 前端 `--text-muted` 未定义（首轮报告记为「既有、无关」）

- **现象**：`frontend/src/test/themeCss.test.ts` 失败 1 例。追查发现不是无关噪声：`a91206f`（11-22）在 `styles.css` 引入了 `var(--text-muted)`，该变量在六个主题里都没有定义，`.route-preview-note` 实际取不到主题色。测试是对的，代码错了。
- **修法**：改用六主题都定义的 `--ui-text-muted`。

---

## 5. 真机核验（RTX 5080）

### 5.1 环境

| 项 | 值 |
| :--- | :--- |
| 主机 / GPU | Windows 11（10.0.26200），NVIDIA GeForce RTX 5080，16,303 MiB，驱动 621.24 |
| ComfyUI | `F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base\ComfyUI`（`object_info` 1,281 个节点类），受管渲染器端口 8237 |
| 数据目录 | 隔离目录 `.local-acceptance/lowvram-20261123/data`，零拷贝接管、零下载 |
| 工作台 | API `127.0.0.1:8001`、Web `127.0.0.1:5174`；代码身份 `git:2254ae1+改动`（即基准提交加本轮收尾改动） |
| 驱动方式 | 仅经产品 HTTP API（`.local-acceptance/lowvram-20261123/run-lowvram-live.py`）。ComfyUI 由 Worker 启停，未手工拉起 |
| 证据 | `.local-acceptance/lowvram-20261123/evidence/*.json`、`frames/`、`psnr-*.stats`（Git 忽略，不进安装包） |

### 5.2 逐次结果

每个任务都是真实 MiniMax-H3 出片：`PASSED`、`quality_verification=real`、`acceptance_status=pending_review`（技术质检通过，语义验收如实留给人看）。成片均经 `ffprobe` 与 `ffmpeg -f null -` 全片解码 0 错误。启动器以 `SHINE_H3_LOW_VRAM_ATTN_TOKEN_THRESHOLD` 切换三档配置，对应方案 §6 的两个运行期开关。

| # | 阈值 | 镜头 | 估算 seq_len | 审计裁决 | ComfyUI 实际执行的图 | 结果 | 渲染耗时 | 显存峰值 MiB（高于空闲） |
| :--- | ---: | :--- | ---: | :--- | :--- | :--- | ---: | --- |
| R1 | 45,000 | 1024×576 · 5s · 2 图 | 23,902 | `false / below_threshold` | 节点 `"15"` **不存在**（23 节点） | PASSED | 167.5 s（冷启动首渲） | 未采到¹ |
| R4 | 45,000 | 1344×768 · 7s · 2 图 | 56,040 | `true / token_threshold_reached` | 节点 `"15"` **挂载**（24 节点） | PASSED | 327.7 s | 15,545（+13,506） |
| R2 | 0 | 1024×576 · 5s · 2 图² | 23,902 | `true / token_threshold_reached` | 挂载 | PASSED | 139.2 s | 15,528（+13,246） |
| R2b | 0 | 1024×576 · 5s · 2 图 | 23,902 | `true / token_threshold_reached` | 挂载 | PASSED | 129.1 s | 15,446（+13,337） |
| R3 | 200,000 | 1344×768 · 7s · 2 图 | 56,040 | `false / below_threshold` | **不存在** | PASSED | 328.9 s | 15,505（+13,391） |
| R1b | 200,000 | 1024×576 · 5s · 2 图 | 23,902 | `false / below_threshold` | **不存在** | PASSED | 108.4 s | 15,465（+13,331） |
| R5 | 200,000 | 1344×768 · 10s · 2 图 | 76,426 | `false / below_threshold` | **不存在** | PASSED | 457.2 s | 15,558（+13,536） |
| R6 | 200,000 | 1344×768 · 15s · 2 图 | 112,102 | `false / below_threshold` | **不存在** | PASSED | 831.6 s | 15,639（+13,415） |

¹ R1 是首次真机运行，我的采样线程有一个属性重名错误，在渲染完成后收集证据时崩溃；成片、审计与执行图证据完整，仅缺显存采样。R1b 以同一张图重渲补齐了显存与耗时。
² R2 在另一个项目里渲染，提示词只差冻结风格 Bible 的版本 UUID，因此不用于严格 A/B，只作为「阈值 0 强制挂载」的证据。

**结论**：
- 方案 §5.2 要求的「低水位省略 / 高水位挂载」在默认阈值下得到真机确认（R1、R4），执行图取自 ComfyUI 交接历史归档，不是 Shine 自己编译结果的回显。
- 模型链连通：省略时 `13 → 16`，挂载时 `13 → 15 → 16`，调度器 `model` 输入均为 `["16", 0]`，无悬空边。
- 8 次渲染结束后 13 条 GPU 租约全部 `RELEASED`，无 ComfyUI 进程残留，端口释放。

### 5.3 零画质损失：同种子 A/B

同一项目、逐字节相同的提示词（`prompt_sha256` 均为 `0ee651a1…`）、相同参考图、相同种子（图默认值 `77`）。

| 比对 | 条件 | 成片 SHA-256 | 逐帧 PSNR |
| :--- | :--- | :--- | :--- |
| R1 ↔ R1b | 同一张不挂保护的图，渲染两次 | 相同（`64e72a2c…`） | 124 / 124 帧 inf |
| **R1 ↔ R2b** | 不挂 ↔ **挂载**，1024×576 · 5s | **相同（`64e72a2c…`）** | **124 / 124 帧 inf** |
| **R4 ↔ R3** | 挂载 ↔ **不挂**，1344×768 · 7s | **相同（`e338b658…`）** | **175 / 175 帧 inf** |
| R1 ↔ R2（对照） | 提示词只差一个风格版本 UUID | 不同 | 124 / 124 帧有限，最低 22.18 dB，均值 26.2 dB |

- R1 = R1b 说明渲染管线在本机是确定性的，A/B 方法有效。
- R1 = R2b、R4 = R3：提交给 ComfyUI 的图不同（`prompt_graph_sha256` 不同），**输出 MP4 逐字节相同**。这与 KJNodes 上游节点文档一致（`MiniMaxLowVRAMAttention`："without changing the math … Output is identical to the unpatched model"；`MiniMaxChunkFeedForward`："the output matches the unchunked model"）。
- 对照组说明方法足够灵敏：仅 36 个字符的 UUID 差异就让画面显著不同。
- 目检：三次抽帧（首 / 中 / 尾）人物身份一致、无伪影，无对白镜闭口。

### 5.4 耗时与显存（单次观测，n = 1）

| 镜头 | 不挂节点 `"15"` | 挂载节点 `"15"` | 差 |
| :--- | ---: | ---: | ---: |
| 1024×576 · 5s（23,902） | 108.4 s（R1b） | 129.1 s（R2b） | 省约 16% |
| 1344×768 · 7s（56,040） | 328.9 s（R3） | 327.7 s（R4） | 无可测差异 |

低水位镜头省略节点 `"15"` 有约 16% 的真实提速，且成片不变。显存峰值在各次之间没有可辨差异（15.45–15.64 GB）：该数字是每秒采样一次的整卡占用，由常驻的 DiT 权重主导，**不能反映注意力瞬态**，不作为节点 `"15"` 显存收益的证据。

### 5.5 OOM 复现尝试

方案 §2.1 以「`56,000 ~ 78,000 tokens` 实测撞墙区」为阈值依据。我把阈值调到 200,000 强制三档镜头不挂保护：

| 镜头 | seq_len | 结果 |
| :--- | ---: | :--- |
| 1344×768 · 7s · 2 图 | 56,040 | 首次尝试 PASSED（328.9 s） |
| 1344×768 · 10s · 2 图 | 76,426 | 首次尝试 PASSED（457.2 s） |
| 1344×768 · 15s · 2 图 | 112,102 | 首次尝试 PASSED |

**在本机本次条件下（2 张参考图、全新栈、约 2 GB 显存被其他程序占用），从 7s 到 H3 最大时长 15s 都没有复现 OOM。** 因此：

- 没有任何一次渲染被误判为 OOM，兜底从未被排程——「成功渲染绝不重试」得到真机确认。
- **真机 OOM 兜底未获验证**，仍是只有离线证据（§7.1）。我没有人为占满显存去硬凑 OOM。
- 撞墙位置显然依赖本轮未改变的条件（参考图数量、当时的其他显存占用、ComfyUI 启动参数等）。在本机上节点 `"15"` 对 112,102 tokens 以内的 2 图镜头不是显存必需的。

---

## 6. 自动化测试与全量回归

### 6.1 专项（收尾改动后）

| 验证项 | 命令（仓库根） | 结果 |
| :--- | :--- | :--- |
| 门控与兼容专项 | `backend\.venv\Scripts\python.exe -m pytest backend/tests/test_h3_low_vram_guard.py -q -p no:cacheprovider` | 21 / 21 |
| 专项 + 相邻（图编译器、模式能力、审计码表、Manual4 静态隔离、分段重拍） | 同上，加 `test_graph_compiler.py test_mode_capabilities.py test_audit_event_codes.py manual4/test_isolation_static.py test_generation_segments.py` | 103 / 103 |
| 首轮结论独立复核（未改动树上） | 方案 §5.1 §4 指定集合 + `test_audit_event_codes.py` | 106 / 106 |
| Ruff | `backend\.venv\Scripts\ruff.exe check backend/shine/worker.py backend/shine/api_segments.py backend/shine/comfy/graph_compiler.py backend/shine/comfy/vram_guard.py backend/shine/foundation/h3.py backend/tests/test_h3_low_vram_guard.py` | `All checks passed!` |

### 6.2 前端

- `npx vitest run --no-file-parallelism`：**94 个文件 / 488 个用例全部通过**（首轮为 487 / 488）。
- `npm.cmd run build`（`tsc -b && vite build`）：通过。

### 6.3 后端全量 8 分片

- **命令**（仓库根）：`CODEBUDDY_SAFE_DELETE_ENABLED=0 pwsh -NoProfile -File .\run_backend_shards.ps1 -WorkRoot F:\ShineTest -Shards 8`
- **范围**：305 个测试文件按文件均分 8 分片并行；日志目录 `F:\ShineTest\shards-20261123-231702-338`。
- **结果**：**8 / 8 分片退出码 0，`shards_with_failure_markers=0`**。按判定口径另行复核 8 份分片日志，`FAILED` / `ERROR` 行均为 0。门控专项（21 例）与分段重拍（8 例）在其中执行。

| 分片 | 退出码 | 耗时 | 文件数 |
| :--- | ---: | ---: | ---: |
| chunk-0 | 0 | 280.0 s | 39 |
| chunk-1 | 0 | 733.9 s | 37 |
| chunk-2 | 0 | 645.3 s | 36 |
| chunk-3 | 0 | 781.6 s | 36 |
| chunk-4 | 0 | 805.1 s | 38 |
| chunk-5 | 0 | 1005.1 s | 43 |
| chunk-6 | 0 | 795.0 s | 39 |
| chunk-7 | 0 | 798.2 s | 37 |

清理：本轮各分片 basetemp 已删除（仅 `bt-6` 302 KB 残留）。运行器对历史 `shards-*` 目录的清理报「未能完全删除」，占用来源另见 §7.8。

---

## 7. 未完成项与诚实边界

1. **真机 OOM 兜底未验证。** §5.5：本机未能触发真实 OOM。离线测试覆盖了 `ComfyH3Executor`、Manual4 Worker 与主线裸队列（`run_one` 无受管上下文）；**主线受管路径**（受管渲染器 + GPU 租约，兜底后的停渲染器、释放租约、重启、第二次尝试）以及**多镜 production series 中的兜底**（空闲保管者的租约转移之后）没有任何真机或离线用例。这段代码目前只有代码走读。
2. **`worker.py` 被修改，VLM 重校准义务未履行。** `client_review_identity` 钉住 `worker.py`，首轮与本轮都改了它。按 AGENTS.md 既有规则，原有 VLM revision 与被冻结的制片计划按设计失效，真实生产验收前需要重做真实正负校准、发布新 revision、重新分析并重新冻结。本机没有可用的 VLM/ASR，本轮**没有做**，不得声称已校准。本轮 8 次出片全部是 `acceptance_status=pending_review`，是技术交付，不是艺术验收。
3. **Manual4 链路没有真机出片。** 它与主线共用 `ComfyH3Executor`，本轮只有离线证据。
4. **启动器验收只跑了 1、4 两条。** 本轮没有改启动方式、端口或进程记录逻辑，A.2（默认端口被占）与 A.3（半栈）未跑。A.4（停止：退出码 0、`processes.json` 消失、端口释放）在隔离目录上执行了 5 次；A.1 的 API health 200 由驱动脚本在 5 次启动上等待确认，Web 200 在最后一次启动上显式核对。
5. **未构建安装包。** 本轮不是发布，打包相关文件未改。
6. **Token 估算的三个前提**见 §2.3；其中 `T_prompt` 与标记 token 数是按源码推算而非实测。
7. **耗时与显存是单次观测**（§5.4）。
8. **测试根 `F:\ShineTest` 占用 335 GB，本轮未清理。** 这不是本轮产生的：本轮 basetemp 已清理。占用来自此前遗留的手工测试目录——「基础配置终验 20261118」「基础配置回归 20261117」「基础配置复验 20261118」各 72 GB，「真机整备 20261118」40 GB，`r5-focus-7f3b4d35…` 33 GB，`summary-final-targeted` / `summary-final-targeted2` / `setup-repair-targeted-3` / `setup-repair-targeted-4` 各 10 GB——正是 AGENTS.md 记录过的「换一个手工目录跑一次就多一份残留」。另有两处清理缺陷：运行器按 `-KeepRuns 1` 删除 24 个历史 `shards-*` 目录时全部报「未能完全删除」，原因是测试 checkout 残留的 `.git` 对象带只读属性（这些目录各只剩几百 KB，不是占用来源）；本轮自己的 `bt-6` 也残留 302 KB。删除数百 GB 历史目录需要所有者决定，本轮只报告不处置。

---

## 8. 运维注意：隔离验收会改写仓库 `.env`

本轮发生并已处置的事故，如实记录，供后续验收参考：

- 产品「生成运行配方」流程（`POST /setup/configuration`）按设计把 `SHINE_DATA_DIR`、`SHINE_SETUP_RECIPE`、`SHINE_ENABLE_LOCAL_EXECUTION` 写回**仓库根 `.env`**（`comfyops/deployment.py::persist_env`，注释写明不让任何密钥经过这里）。我用隔离目录启动工作台，但驱动了环境接管流程，于是仓库 `.env` 的 `SHINE_DATA_DIR` 被改成隔离目录，日常启动器随后的「日常数据目录」就变成了它（启动器也因此把隔离栈放到了 8000 / 5173）。
- 数据没有串：三次启动的库与产物都在隔离目录，已逐项核对。
- 产品提供了钩子 `SHINE_RUNTIME_ENV_PATH`：指向数据目录内的文件时，改写到那里而不是仓库 `.env`。**隔离验收必须设置 `SHINE_RUNTIME_ENV_PATH=<隔离目录>\.env`**。
- 仓库 `.env` 已还原为改写前的取值（`SHINE_DATA_DIR` → `.local-acceptance\v3-p4-20261122`，405 字节）。**这是基于证据的还原而非原样恢复**：改写前只记录了变量名，没有备份；原值由三条证据推定——原文件 405 字节只与 14 字符的目录名吻合、只有 v3-p1/p2/p3/p4 四个候选有配套配方文件、其中只有 v3-p4 在改写前的当天被日常启动器使用过。**请所有者确认这个取值。** 改写后的内容留在 `.local-acceptance/lowvram-20261123/env-rewritten-by-setup-flow.txt`。
- 是否让启动器在显式 `-DataDir` 时自动设置该钩子，属于启动器行为变更，需走完整的启动器验收矩阵，本轮未改。
