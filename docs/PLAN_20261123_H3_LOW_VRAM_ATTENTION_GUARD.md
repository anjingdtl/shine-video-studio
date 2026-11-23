# 技术方案：MiniMax H3 `MiniMaxLowVRAMAttention` 高水位门控与 OOM 兜底策略

- **基准提交**：`main` @ `f5c0c30f047368dbab27ea2c3fa9495147278e3a`
- **目标分支**：`feat/h3-low-vram-attention-guard`（从 `origin/main` 切出，遵循 `docs/BRANCH_PROTECTION.md` 严禁直推 `main`）
- **画质底线**：严格零画质损失（仅调度数学等价的分块计算节点，不引入 Turbo LoRA、VSA、ClipProj 或任何步数/语义有损近似）

---

## 1. 背景与现状审计（基于 `main` 实测）

对当前仓库 `main` 分支源码审计确认以下基线事实：

1. **图编译现状（`backend/shine/comfy/graph_compiler.py`）**：
   - `_model_stack()` 当前已无条件挂载节点 `"13"`（`MiniMaxChunkFeedForward`，`chunks=2, seq_threshold=4096`）和节点 `"15"`（`MiniMaxLowVRAMAttention`，`head_chunks=4`）。
   - 当 `accelerator_id == "pdd8"` 时跳过节点 `"14"`（`MiniMaxH3MemoryEfficientSageAttentionPatch`），节点 `"15"` 直连 `["13", 0]`；当 `accelerator_id == "native20"` 时节点 `"15"` 连 `["14", 0]`。
   - 当 `lowend=True`（`ref2va-lowend-candidate`）时移除节点 `"11"`（Realism LoRA）与 `"16"`（`SolAttnPatch`），并将 `"15"` 上移占据 `"16"` 作为模型链出口。
2. **双链路统一编译现状**：
   - 主线七阶段：`backend/shine/director/package.py`、`backend/shine/services.py`、`backend/shine/worker.py` 均通过 `shine.foundation.h3.compile_h3_ref2va_graph()` 编译执行图。
   - Manual4 独立链路：`backend/shine/manual4/comfy/h3_adapter.py` 与 `backend/shine/manual4/worker.py` 通过 `shine.foundation.h3.compile_h3_ref2va_graph()` 和 `ComfyH3Executor` 执行。
   - 静态隔离约束（`backend/tests/manual4/test_isolation_static.py`）：`shine.manual4` 仅允许导入 `shine.manual4`、`shine.config`、`shine.platform_projects`、`shine.platform_gpu`、`shine.foundation`；`shine.foundation` 禁止反向依赖 `shine.director`、`shine.services`、`shine.worker`、`shine.manual4` 等业务模块。
3. **身份哈希与门禁约束**：
   - `backend/shine/comfyops/mode_capabilities.py` 通过默认调用 `compile_h3_ref2va_graph(variant_id=..., accelerator_id=..., image_count=1)` 推导 `core_mode_node_types()` 及 `ComfyUI-KJNodes` 必需节点包。
   - `backend/tests/test_graph_compiler.py` 固定校验 3 组历史归档 `(graph_sha256, recipe_sha256)`。
   - `backend/shine/worker.py`（L2801）校验 `profile.graph_sha256 == request.workflow_profile_hash`；`backend/shine/foundation/h3.py`（L303）校验 `compiled.recipe_sha256 == request.recipe_sha256`。
4. **OOM 故障识别缺口**：
   - `backend/shine/comfy/provider.py` 中 `_history_outcome()` 对 `status_str == "error"` 仅返回 `("failed", None)`，未解析 `status.messages` 中的 `execution_error` 明细；`ComfyH3Executor.execute()` 亦仅返回通用错误文案 `"ComfyUI 执行失败。"`，当前无法判定是否为 CUDA OOM。

---

## 2. 目标与范围边界

### 2.1 本次目标

1. **常驻 `MiniMaxChunkFeedForward`**：保持节点 `"13"` 在所有 H3 Ref2VA 图中常开，作为零画质损失的底层显存削峰基线。
2. **`MiniMaxLowVRAMAttention` 改为高水位按需挂载**：
   - 默认阈值 **`45,000 tokens`**（距 `56,000 ~ 78,000 tokens` 实测撞墙区预留约 $20\%$ 安全缓冲），支持通过环境变量 `SHINE_H3_LOW_VRAM_ATTN_TOKEN_THRESHOLD` 覆盖。
   - **估算总 token $< 45,000$**：不挂载节点 `"15"`（`MiniMaxLowVRAMAttention`），消除常规/低中分辨率镜头的分块调度开销。
   - **估算总 token $\ge 45,000$**：自动挂载节点 `"15"`（`MiniMaxLowVRAMAttention`, `head_chunks=4`），将显存墙推高至约 $220,000$ token，画质输出不变。
3. **被动 CUDA OOM 单次兜底重试**：
   - 当某镜头因估算低于阈值而未挂载 `MiniMaxLowVRAMAttention`，但 ComfyUI 执行失败且 `/history` 中捕获到 CUDA OOM 异常时，系统自动记录诊断事件，以 `force_low_vram_attention=True` 重新编译图并执行**最多 1 次**保底重试。
   - 若该次提交已挂载 `MiniMaxLowVRAMAttention` 仍发生 OOM，或属于非 OOM 错误，则**绝不盲目重试**，按原失败链路处理。
4. **双链路统一、零破坏兼容**：
   - 主线与 `manual4` 共享同一套估算函数、阈值配置与 OOM 识别逻辑。
   - 保持 `mode_capabilities.py` 的 `ComfyUI-KJNodes` 安装门禁、`test_isolation_static.py` 隔离红线，以及既有未传分辨率参数的归档哈希测试 $100\%$ 兼容。

### 2.2 明确非目标（Out of Scope）

- 不引入 `Turbo LoRA`、`SelfLift-Fish`、`VSA`、`ClipProj` 或 NVFP4 融合 MLP。
- 不修改现有 `MiniMaxH3MemoryEfficientSageAttentionPatch` 在 `native20` / `pdd8` 的开关逻辑。
- **本次不修改节点 `"16"`（`SolAttnPatch`）**：现行 `h3-ref2va-int8-convrot` 依赖 `ComfyUI-SolAttn_triton` 节点包门禁；是否去除或开关化 `SolAttnPatch` 以追求更严苛的无近似注意力，作为独立议题后续专项评估。

---

## 3. 详细技术设计

### 3.1 新增统一策略模块：`backend/shine/comfy/vram_guard.py`

放置在 `shine.comfy` 层并通过 `shine.foundation.h3` 重新导出，确保 `manual4` 与主线均可合法访问且满足静态隔离测试。

#### (1) H3 帧数对齐与 Token 估算公式

复刻 `graph_compiler.py` 的 `LENGTH_EXPRESSION` 帧网格（$24\text{ fps}$，满足 $L \equiv 5 \pmod{17}$）：

```python
def align_h3_frame_count(duration_s: float, *, fps: float = 24.0) -> int:
    raw = max(5, round(float(duration_s) * fps))
    return raw + (5 - (raw % 17)) % 17
```

Token 估算模型 `estimate_h3_attention_tokens(...)`：

```python
DEFAULT_LOW_VRAM_ATTENTION_TOKEN_THRESHOLD = 45_000
DEFAULT_TEXT_CONDITION_TOKENS = 1_024
DEFAULT_TOKENS_PER_AUDIO_REF = 512
DEFAULT_VIDEO_REF_DURATION_S = 3.0

@dataclass(frozen=True)
class H3TokenEstimate:
    aligned_frames: int
    latent_frames: int
    tokens_per_latent_frame: int
    video_tokens: int
    image_ref_tokens: int
    video_ref_tokens: int
    audio_and_text_tokens: int
    total_tokens: int
```

估算规则（开发阶段需先对照本机 ComfyUI H3 / KJNodes 节点源码核验时空下采样步长，若本机实现与下式存在常数倍数偏差，以实测 `seq_len` 校准并记录在验收报告中）：

- 单潜帧空间 token：$\text{tokens\_per\_latent\_frame} = \max(1, \lfloor W / 16 \rfloor) \times \max(1, \lfloor H / 16 \rfloor)$
- 主视频潜帧数：$\text{latent\_frames} = 1 + \lfloor \max(0, L - 1) / 4 \rfloor$
- 主视频 token：$\text{video\_tokens} = \text{latent\_frames} \times \text{tokens\_per\_latent\_frame}$
- 参考图附加 token（因 `ref_image_size="match"`）：$\text{image\_ref\_tokens} = N_{\text{img}} \times \text{tokens\_per\_latent\_frame}$
- 参考视频附加 token：$\text{video\_ref\_tokens} = N_{\text{vid}} \times \text{ref\_latent\_frames} \times \text{tokens\_per\_latent\_frame}$（$\text{ref\_latent\_frames}$ 默认按 `DEFAULT_VIDEO_REF_DURATION_S` 折算，若调用方提供实际参考视频时长则按实际值）
- 音频与文本条件余量：$\text{audio\_and\_text\_tokens} = 1024 + N_{\text{aud}} \times 512$
- 总估算：$\text{total\_tokens} = \text{video\_tokens} + \text{image\_ref\_tokens} + \text{video\_ref\_tokens} + \text{audio\_and\_text\_tokens}$

**典型档位自检（按 $5\text{ s}$、2 张参考图、0 音频为例，$L = 124, \text{latent\_frames} = 31$）**：

- `0.5` 档（$672 \times 384$）：单帧 $42 \times 24 = 1,008$，总计 $31 \times 1,008 + 2 \times 1,008 + 1,024 = 34,288 < 45,000$，**关闭（保速）**
- `0.7` 档（$960 \times 544$）：单帧 $60 \times 34 = 2,040$，总计 $31 \times 2,040 + 2 \times 2,040 + 1,024 = 68,344 \ge 45,000$，**自动开启（防爆）**
- `1.0` 档（$1344 \times 768$）：单帧 $84 \times 48 = 4,032$，总计 $31 \times 4,032 + 2 \times 4,032 + 1,024 = 134,080 \ge 45,000$，**自动开启（防爆）**

#### (2) 决策契约 `LowVramAttentionDecision`

```python
@dataclass(frozen=True)
class LowVramAttentionDecision:
    enabled: bool
    reason: Literal[
        "below_threshold",
        "token_threshold_reached",
        "oom_fallback",
        "forced_disabled",
        "legacy_default",
    ]
    threshold: int
    estimate: H3TokenEstimate | None = None
```

决策函数 `decide_low_vram_attention(...)`：

1. 若 `force_low_vram_attention is True`：返回 `enabled=True, reason="oom_fallback"`。
2. 若 `force_low_vram_attention is False`：返回 `enabled=False, reason="forced_disabled"`。
3. 若 `threshold <= 0`：返回 `enabled=True, reason="token_threshold_reached"`（允许通过环境变量设为 `0` 一键回退到全量常开）。
4. 否则计算 `estimate`，令 `enabled = (estimate.total_tokens >= threshold)`，返回对应 `reason`。

#### (3) ComfyUI 历史错误明细提取与 CUDA OOM 判定

新增纯函数，供 `provider.py`、`foundation/h3.py` 与 `worker.py` 共用：

- `extract_comfy_history_error(record: dict[str, Any] | None) -> dict[str, Any] | None`：
  解析 ComfyUI `/history` 中 `record["status"]["messages"]` 里的 `["execution_error", {...}]` 条目，提取 `node_id`、`node_type`、`exception_type`、`exception_message`（以及截断后的关键 traceback 摘要）。
- `is_cuda_oom_error(error_info: dict[str, Any] | None = None, message: str | None = None) -> bool`：
  大小写不敏感匹配典型 PyTorch / CUDA OOM 特征：
  - `exception_type` 包含 `OutOfMemoryError`
  - 或错误文本包含 `cuda out of memory`、`out of memory`、`cublas_status_alloc_failed`、`allocation on device`、`hip out of memory`（注意排除非显存类普通报错）。

---

### 3.2 图编译器改造：`backend/shine/comfy/graph_compiler.py`

1. **扩展 `_model_stack(...)` 签名**：
   ```python
   def _model_stack(
       unet: str,
       *,
       sage_attention: bool,
       lowend: bool,
       low_vram_attention: bool = True,
   ) -> dict[str, Any]:
   ```
   拓扑重连规则必须覆盖全部组合且**保证下游采样栈连通无悬空**：
   - **先处理 `sage_attention`（节点 `14`）**：
     - `sage_attention=True`：上游出口 `prev = ["14", 0]`
     - `sage_attention=False`：删除 `"14"`，上游出口 `prev = ["13", 0]`
   - **再处理 `low_vram_attention`（节点 `15`）**：
     - `low_vram_attention=True`：`nodes["15"]["inputs"]["model"] = prev`，出口更新为 `prev = ["15", 0]`
     - `low_vram_attention=False`：删除 `"15"`，出口保持 `prev` 不变
   - **最后处理 `lowend` 与节点 `"16"`**：
     - `lowend=False`（INT8 主力变体）：`nodes["16"]["inputs"]["model"] = prev`（`SolAttnPatch` 接 `prev`，下游 `17`/`19`/`50`/`62` 全部照旧连 `["16", 0]`）。
     - `lowend=True`（W6A8 候选变体）：移除 `"11"`、`12` 接 `"10"`、移除 `SolAttnPatch`；若 `low_vram_attention=True`，保持原有行为 `nodes["16"] = nodes.pop("15")`；若 `low_vram_attention=False`，需将当前模型链末端节点（`"14"` 若存在否则 `"13"`）重编号为 `"16"`，或显式返回 `model_out_ref` 供 `_sampling_stack()` 和二采节点 `62` 连接。**推荐使用显式返回 `model_out_ref` 的方式彻底消除硬编码 `"16"` 的脆弱性**，同时确保 `low_vram_attention=True` 时的节点 ID 与原版 $100\%$ 字节级一致（保障历史 `graph_sha256` 完全不变）。

2. **扩展 `compile_ref2va_workflow(...)` 签名与向后兼容策略**：
   - 新增参数：`low_vram_attention: bool | None = None`（及可选透传诊断对象 `low_vram_decision: LowVramAttentionDecision | None = None`）。
   - **当 `low_vram_attention is None`**：默认按 `True` 编译（与历史行为完全等价）。由此保证：
     - `mode_capabilities.core_mode_node_types()` 仍包含 `MiniMaxLowVRAMAttention`，`ComfyUI-KJNodes` 仍是启动/安装必备节点包，避免未装节点包在运行时才暴露。
     - `test_p1_preserves_the_archived_recipe_and_graph_identities` 的 3 组历史 `(graph_sha256, recipe_sha256)` 原封不动通过。
   - **身份与指纹隔离（极其重要）**：
     - **`recipe_sha256` 绝不纳入 `low_vram_attention`**：因为 `MiniMaxLowVRAMAttention` 是数学等价的无损运行时显存保护开关，不是模型或创作配方变更。
     - `model_identity` 中记录可观测元信息（不进入 `recipe_payload`）：`"low_vram_attention": effective_low_vram`（及可选 `"low_vram_attention_reason"`、`"estimated_attention_tokens"`）。
     - `graph_sha256` 仍由 `graph_hash(graph)` 真实计算，如实反映物理提交给 ComfyUI 的图拓扑。

3. **新增安全图等价判定辅助函数 `is_low_vram_guard_variant(...)`**：
   - 给定同一 `variant_id / accelerator_id / second_pass / reference_counts`，可验证新编译出的 `graph_sha256` 恰好等于开启 `low_vram_attention=True` 时的合法编译哈希。供主线 `worker.py` 在 OOM 兜底重试时放行 `workflow_profile_hash` 校验，防止伪造或漂移图绕过校验。

---

### 3.3 配置项：`backend/shine/config.py` 与 `.env.example`

- 在 `Settings` 中新增字段：
  - `h3_low_vram_attention_token_threshold: int = 45000`
    - 环境变量：`SHINE_H3_LOW_VRAM_ATTN_TOKEN_THRESHOLD`（通过 `_env_int` 读取；允许 $\ge 0$，负数抛 `ValueError`）。
  - `h3_oom_low_vram_retry_enabled: bool = True`
    - 环境变量：`SHINE_H3_OOM_LOW_VRAM_RETRY`（默认开启，设为 `0`/`false` 可关闭被动重试以便排障）。
- 同步在 `.env.example` 中补充配置说明注释。

---

### 3.4 统一执行底座：`backend/shine/foundation/h3.py`

1. **扩展 `compile_h3_ref2va_graph(...)`**：透传 `low_vram_attention: bool | None = None`，并在模块 `__all__` 导出 `align_h3_frame_count`、`estimate_h3_attention_tokens`、`decide_low_vram_attention`、`extract_comfy_history_error`、`is_cuda_oom_error`、`DEFAULT_LOW_VRAM_ATTENTION_TOKEN_THRESHOLD`。
2. **扩展 `H3ExecutionRequest` 与 `H3ExecutionReceipt`**：
   - `H3ExecutionRequest` 新增：
     - `low_vram_attention_token_threshold: int = DEFAULT_LOW_VRAM_ATTENTION_TOKEN_THRESHOLD`
     - `force_low_vram_attention: bool | None = None`
     - `retry_on_oom_with_low_vram: bool = True`
   - `H3ExecutionReceipt` 新增：
     - `error_details: dict[str, Any] | None = None`
     - `low_vram_attention_used: bool | None = None`
     - `oom_fallback_triggered: bool = False`
3. **`ComfyH3Executor.execute()` 完整闭环**：
   - 编译前调用 `decide_low_vram_attention(...)`（结合 `request.width`, `request.height`, `request.duration_s`, `len(images)`, `len(audios)`, `len(videos)`, `request.low_vram_attention_token_threshold`, `request.force_low_vram_attention`）。
   - 编译图时传入 `low_vram_attention=decision.enabled`。
   - 当轮询 `reconcile_submission()` 返回 `state == "failed"` 时：
     - 调用 `extract_comfy_history_error(rec)` 拿到真实异常详情并写入 `error_details` 与 `error` 文本。
     - **OOM 兜底判定**：若 `request.retry_on_oom_with_low_vram` 为 `True`、本次 `decision.enabled is False`、且 `is_cuda_oom_error(error_info)` 为 `True`：
       - 自动以 `low_vram_attention=True` 重编译图（**仍复用同一套已上传参考素材与同一 `recipe_sha256` 校验**），使用派生幂等键（如 `f"{request.idempotency_key}:oom-lowvram"`）重新调用 `on_before_submit` / `submit_prepared_prompt` / `on_submitted` 并轮询至终态。
       - 最多重试 **1 次**；返回结果标注 `oom_fallback_triggered=True`、`low_vram_attention_used=True`。
       - 注意对 `manual4/worker.py` 的回调兼容：`_persist_submit_intent` 需递增 `attempt_no`（不能复用 `attempt_no_base + 1` 导致重复 `attempt_no`），确保数据库里清晰留下 Attempt #1（FAILED / CUDA OOM）与 Attempt #2（OOM LowVRAM Fallback）两笔审计轨迹。

---

### 3.5 主线与 Manual4 双链路对齐

#### A. 主线链路（`services.py` + `director/package.py` + `worker.py` + `comfy/provider.py`）

1. **`backend/shine/comfy/provider.py`**：
   - `poll()` 在 `status_str == "error"` 时调用 `extract_comfy_history_error(record)`，把 `exception_type` / `exception_message` / `node_type` 写入 `ComfyResult.error`（必要时增加 `error_details` 字段），不再丢失 ComfyUI 的 OOM 原因。
2. **`backend/shine/services.py`**（构建 `RenderRequest` 预编译阶段，约 L1228）：
   - 结合 `spec.output.width`、`spec.output.height`、`spec.output.duration_s`、`counts` 与 `settings.h3_low_vram_attention_token_threshold` 调用 `decide_low_vram_attention(...)`。
   - 调用 `compile_h3_ref2va_graph(..., low_vram_attention=decision.enabled)`，将对应的 `graph_sha256` 写入 `workflow_profile_hash`。
3. **`backend/shine/director/package.py`**（约 L665）：
   - 若已确定镜头输出规格，同样通过 `decide_low_vram_attention(...)` 保持与 `services.py` 一致的编译检验；同时确认当未启用 `MiniMaxLowVRAMAttention` 时也不漏检能力。
4. **`backend/shine/worker.py`**（约 L2794 及轮询/失败处理区）：
   - 正常提交：根据 `request` 中冻结的 `output.width / height / duration_s`、`render_reference_counts(manifest)` 与 `settings.h3_low_vram_attention_token_threshold` 计算 `decision`，调用 `compile_h3_ref2va_graph(..., low_vram_attention=decision.enabled)`；其 `graph_sha256` 与 `request.workflow_profile_hash` 精确匹配。
   - **OOM 兜底闭环（主线 Worker）**：
     - 当某次 attempt 因 ComfyUI 执行失败返回 CUDA OOM、本次未开启 `MiniMaxLowVRAMAttention`、且 `settings.h3_oom_low_vram_retry_enabled` 为真时：
       - 记录审计事件 `H3_OOM_LOW_VRAM_FALLBACK_SCHEDULED`（含 `estimated_tokens`, `threshold`, `oom_error`）。
       - 将本次 OOM 标记持久化到 Job/Attempt 元数据或重试上下文（如基于前一次失败 attempt 的 `COMFY_CUDA_OOM` 触发下一轮自动重排，或在单次执行流程受控发起下一次严格落库的新 `JobAttempt`——优先复用主线现有的状态机与租约保护机制，避免在无锁状态下绕过 `managed_continuation` 激活门禁）。
       - 当检测到上一次尝试为 `COMFY_CUDA_OOM` 且尚未启用 `low_vram_attention` 时，强制传 `low_vram_attention=True` 重编译；校验哈希时通过 `is_low_vram_guard_variant(...)` 验证其恰好是原冻结请求对应的 LowVRAM 保底图且 `checkpoint_id` 与 `recipe_sha256` 一致，然后正常走完 `managed_continuation` / `submit_prepared` 流程。
   - 审计事件 `STRATEGY_GRAPH_COMPILED` 中补充：`low_vram_attention`、`low_vram_attention_reason`、`estimated_attention_tokens`、`low_vram_attention_threshold`。

#### B. Manual4 链路（`manual4/comfy/h3_adapter.py` + `manual4/worker.py`）

1. **`backend/shine/manual4/comfy/h3_adapter.py`**：
   - `compile_m4_ref2va_workflow()` 新增可选参数 `low_vram_attention: bool | None = None` 并透传给 `compile_h3_ref2va_graph()`。
2. **`backend/shine/manual4/worker.py`**：
   - 将 `settings.h3_low_vram_attention_token_threshold` 和 `settings.h3_oom_low_vram_retry_enabled` 透传进 `H3ExecutionRequest`（以及 `video_stub` 分支编译校验）。
   - 修正 `_persist_submit_intent` 回调在同一次 `ComfyH3Executor.execute()` 触发 OOM 重试时的 `attempt_no` 自增逻辑（每次回调取最新 `attempt_no`，并把上一次 OOM 失败 attempt 标记为 `FAILED` / `M4_CUDA_OOM_RETRYING`），确保两条 attempt 审计链完整可追溯。
   - 若 `receipt.oom_fallback_triggered` 为真，在运行日志或 job 元信息中记录触发事实。

---

## 4. 变更文件清单

| 文件路径 | 变更性质 | 核心改动 |
| :--- | :--- | :--- |
| `backend/shine/comfy/vram_guard.py` | 新增 | H3 帧对齐、Token 估算、45,000 阈值决策、ComfyUI `/history` 错误解析与 CUDA OOM 识别 |
| `backend/shine/comfy/graph_compiler.py` | 修改 | `_model_stack()` / `compile_ref2va_workflow()` 支持条件挂载 `MiniMaxLowVRAMAttention`（默认 `None` 兼容旧签名）、`recipe_sha256` 隔离、`is_low_vram_guard_variant()` |
| `backend/shine/comfy/provider.py` | 修改 | `poll()` 在执行失败时提取真实 `execution_error` 文本与结构化详情 |
| `backend/shine/config.py` 与 `.env.example` | 修改 | 新增 `SHINE_H3_LOW_VRAM_ATTN_TOKEN_THRESHOLD`（默认 45000）与 `SHINE_H3_OOM_LOW_VRAM_RETRY`（默认开启） |
| `backend/shine/foundation/h3.py` | 修改 | 导出 `vram_guard` 接口；`ComfyH3Executor` 集成主动阈值判定、错误明细提取与单次 OOM LowVRAM 自动重试 |
| `backend/shine/services.py` 与 `backend/shine/director/package.py` | 修改 | 预编译阶段按镜头尺寸/时长/参考数计算 `low_vram_attention` 决策并冻结对应图哈希 |
| `backend/shine/worker.py` | 修改 | 执行阶段按统一策略编译、记录可观测审计字段、支持 CUDA OOM 触发 LowVRAM 保底重编译并放行合法保底图哈希校验 |
| `backend/shine/manual4/comfy/h3_adapter.py` 与 `backend/shine/manual4/worker.py` | 修改 | 透传阈值与 OOM 重试配置，适配多 Attempt 落库回调 |
| `backend/tests/test_h3_low_vram_guard.py` | 新增 | 覆盖公式、阈值边界、图拓扑重连、哈希隔离、OOM 识别、主线+Manual4 双链路 OOM 单次兜底与非 OOM 不重试 |
| `docs/PLAN_20261123_H3_LOW_VRAM_ATTENTION_GUARD.md` | 新增 | 本设计方案文档 |
| `docs/ACCEPTANCE_20261123_H3_LOW_VRAM_ATTENTION_GUARD.md` | 新增 | 开发完成后由验收流程产出的真实测试与实机校准验收报告 |

---

## 5. 测试与验收计划

### 5.1 必须覆盖的自动化测试用例

1. **Token 估算与阈值边界单测**：
   - 验证 `align_h3_frame_count()` 与 `LENGTH_EXPRESSION` 在 `1s / 3s / 4s / 5s / 7s / 10s / 15s` 完全一致。
   - 验证 `0.5` 档（$672 \times 384$, 5s, 2 图）$< 45,000$ 判定 `enabled=False`；`0.7` 档（$960 \times 544$, 5s, 2 图）$\ge 45,000$ 判定 `enabled=True`；`1.0` 档（$1344 \times 768$, 3s+, 1 图）$\ge 45,000$ 判定 `enabled=True`。
   - 验证多参考图（如 9 图）与参考视频能显著抬升估算值并正确越过阈值。
   - 验证环境变量 `SHINE_H3_LOW_VRAM_ATTN_TOKEN_THRESHOLD=0` 强制全开、调高阈值可延迟触发。
2. **图拓扑与哈希契约测试**：
   - `low_vram_attention=False` 时：`MiniMaxChunkFeedForward` 仍在图中，`MiniMaxLowVRAMAttention` 不在图中，且所有组合（`int8+pdd8`、`int8+native20`、`int8+pdd8+second_pass`、`lowend+native20`）均通过 `validate_graph()` 且所有上游/下游边连通无悬空。
   - 同一镜头开启/关闭 `low_vram_attention` 时：**`recipe_sha256` 必须完全相等**，`graph_sha256` 必须不同。
   - 既有 `test_p1_preserves_the_archived_recipe_and_graph_identities`、`test_mode_capabilities.py` 全部保持绿色。
3. **OOM 识别与单次兜底闭环测试（离线 Mock ComfyUI）**：
   - `extract_comfy_history_error` 能准确解析 `execution_error`；`is_cuda_oom_error` 对 `torch.OutOfMemoryError` / `CUDA out of memory` 返回 `True`，对普通 ValueError/缺失模型返回 `False`。
   - **Manual4 链路（`ComfyH3Executor` + `manual4/worker.py`）**：首次提交（未挂 LowVRAM）模拟返回 CUDA OOM 时，自动以 `low_vram_attention=True` 重编译提交第 2 次并成功，数据库留下 2 条 `M4JobAttempt`；若第 2 次依然 OOM 则终止失败，不陷入死循环；若首次为非 OOM 错误则不重试。
   - **主线链路（`services.py` + `worker.py`）**：首次提交（未挂 LowVRAM）模拟返回 CUDA OOM 时，触发 LowVRAM 保底重试并通过图哈希校验（`is_low_vram_guard_variant`）成功交付，并记录审计事件。
4. **静态隔离与全量回归**：
   - `pytest backend/tests/manual4/test_isolation_static.py` 必须 $100\%$ 通过。
   - `pytest backend/tests/test_graph_compiler.py backend/tests/test_mode_capabilities.py backend/tests/test_h3_low_vram_guard.py backend/tests/manual4/test_p3_unified_h3.py backend/tests/manual4/test_p4_production.py` 全部通过。
   - 运行仓库全量后端分片测试（`./run_backend_shards.ps1` 或 CI 等价命令）确认零回归。

### 5.2 本地实机核验项（若本机 ComfyUI 可用）

- 核对本机安装的 `ComfyUI-KJNodes` 中 `MiniMaxChunkFeedForward` 与 `MiniMaxLowVRAMAttention` 的输入签名与 `seq_len` 计算口径，如时空下采样比例与 $1 + \lfloor (L - 1) / 4 \rfloor$ 或 $\lfloor W / 16 \rfloor \times \lfloor H / 16 \rfloor$ 有差异，同步校准公式并在验收文档中写明实测证据。
- 分别用 1 个低水位短镜（$< 45,000$ tokens）和 1 个高水位镜头（$\ge 45,000$ tokens）实测提交图 JSON，确认节点 `15` 按预期省略/挂载且画质无异常。

---

## 6. 回滚与 Git 交付纪律

- **运行期一键回滚**：无需回退代码，只需在 `.env` 中设置 `SHINE_H3_LOW_VRAM_ATTN_TOKEN_THRESHOLD=0` 即可立即恢复到所有镜头常开 `MiniMaxLowVRAMAttention` 的旧行为；设置 `SHINE_H3_OOM_LOW_VRAM_RETRY=0` 可单独关闭 OOM 自动重试。
- **分支保护纪律**：严格遵守 `docs/BRANCH_PROTECTION.md`，从 `origin/main` 切出新分支 `feat/h3-low-vram-attention-guard`，绝不尝试直推 `main` 或修改 Ruleset。
- **提交节奏**：拆分为清晰提交（方案文档、核心实现、测试与验收报告），全部检查通过后推送 `origin feat/h3-low-vram-attention-guard` 供所有者审阅合并。