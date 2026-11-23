"""Unified Public H3 Ref2VA production foundation shared across pipelines.

This facade owns the single authoritative Ref2VA graph compilation, reference
slot policy, ComfyUI execution, ``/history`` & ``/queue`` reconciliation, frame
continuity extraction, and deterministic technical QC wrapper.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import time
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypedDict

from shine.comfy.client import (
    ComfyError,
    ComfyPromptRejected,
    ComfySubmissionUncertain,
    ComfyUIClient,
    ComfyUnavailable,
    ComfyWorkflowError,
)
from shine.comfy.graph_compiler import (
    ACCELERATORS,
    AUDIO_REFERENCE_LIMIT,
    IMAGE_REFERENCE_LIMIT,
    INT8_VARIANT_ID,
    TOTAL_REFERENCE_LIMIT,
    VARIANT_ACCELERATORS,
    VIDEO_REFERENCE_LIMIT,
    CompiledWorkflow,
    GraphCompileError,
    SecondPassSpec,
    compile_ref2va_workflow,
    is_low_vram_guard_variant,
)
from shine.comfy.provider import (
    find_output,
    prepare_workflow_submission,
    reconcile_submission,
    submit_prepared_prompt,
)
from shine.comfy.vram_guard import (
    DEFAULT_LOW_VRAM_ATTENTION_TOKEN_THRESHOLD,
    H3TokenEstimate,
    LowVramAttentionDecision,
    align_h3_frame_count,
    decide_low_vram_attention,
    estimate_h3_attention_tokens,
    extract_comfy_history_error,
    format_comfy_history_error,
    is_cuda_oom_error,
    low_vram_threshold_from_env,
)
from shine.comfy.workflow import apply_parameters, graph_hash
from shine.foundation.h3_audio import (
    canonical_h3_language,
    enforce_h3_render_audio_contract,
    ensure_h3_execution_prompt_contract,
    extract_comfy_prompt_texts,
    has_authorized_h3_speech,
    sanitize_h3_dialogue_text,
)
from shine.quality.technical import TechnicalExpectations, TechnicalQC

MAX_IMAGE_SLOTS = IMAGE_REFERENCE_LIMIT
MAX_AUDIO_SLOTS = AUDIO_REFERENCE_LIMIT
MAX_VIDEO_SLOTS = VIDEO_REFERENCE_LIMIT
MAX_TOTAL_SLOTS = TOTAL_REFERENCE_LIMIT

DEFAULT_AUDIO_DURATION_MIN_S = 1.0
DEFAULT_AUDIO_DURATION_MAX_S = 30.0


class H3ExecutionError(RuntimeError):
    """Domain error raised by the unified H3 foundation executor."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass(frozen=True)
class H3ReferenceInput:
    family: str  # "image" | "audio" | "video"
    path: Path
    filename: str
    content_type: str
    sha256: str
    role_label: str = ""
    duration_s: float | None = None


@dataclass(frozen=True)
class H3ExecutionRequest:
    prompt_text: str
    prompt_sha256: str
    duration_s: float
    width: int
    height: int
    seed: int
    references: list[H3ReferenceInput] = field(default_factory=list)
    variant_id: str = "h3-ref2va-int8-convrot"
    accelerator_id: str = "pdd8"
    second_pass: SecondPassSpec | None = None
    extra_data: dict[str, Any] | None = None
    idempotency_key: str = ""
    recipe_sha256: str | None = None
    dialogue: list[dict[str, Any]] = field(default_factory=list)
    reference_manifest: list[dict[str, Any]] = field(default_factory=list)
    normalize_audio_prompt: bool = False
    project_language: str = "Chinese"
    ffmpeg_path: str = "ffmpeg"
    low_vram_attention_token_threshold: int = DEFAULT_LOW_VRAM_ATTENTION_TOKEN_THRESHOLD
    force_low_vram_attention: bool | None = None
    retry_on_oom_with_low_vram: bool = True


@dataclass(frozen=True)
class H3ExecutionReceipt:
    status: str  # "completed" | "failed" | "timeout"
    prompt_id: str | None
    prompt_graph_sha256: str
    output_filename: str | None = None
    output_bytes: bytes | None = None
    output_sha256: str | None = None
    model_identity: dict[str, Any] | None = None
    error_code: str | None = None
    error: str | None = None
    error_details: dict[str, Any] | None = None
    low_vram_attention_used: bool | None = None
    oom_fallback_triggered: bool = False


def verified_variants_catalog() -> list[dict[str, Any]]:
    """Return production variants with an audited dynamic graph recipe."""
    from shine.strategy import model_variant_options

    return [
        {
            "variant_id": row["variant_id"],
            "label": row["label"],
            "verified": True,
            "accelerators": list(VARIANT_ACCELERATORS[row["variant_id"]]),
        }
        for row in model_variant_options()
        if row["verified"]
    ]


class ReferenceCounts(TypedDict):
    image_count: int
    audio_count: int
    video_count: int


def render_reference_counts(manifest: Iterable) -> ReferenceCounts:
    """H3 numbers subject and still-image references in one shared sequence."""
    counts = Counter(item.use_as.value for item in manifest)
    return {
        "image_count": counts["subject"] + counts["picture"],
        "audio_count": counts["audio"],
        "video_count": counts["video"],
    }


def compile_h3_ref2va_graph(
    *,
    image_count: int,
    audio_count: int = 0,
    video_count: int = 0,
    variant_id: str = INT8_VARIANT_ID,
    accelerator_id: str = "pdd8",
    second_pass: SecondPassSpec | None = None,
    object_info: dict[str, Any] | None = None,
    low_vram_attention: bool | None = None,
    low_vram_decision: LowVramAttentionDecision | None = None,
) -> CompiledWorkflow:
    """Compile the single Ref2VA definition for the frozen reference layout."""
    try:
        return compile_ref2va_workflow(
            variant_id=variant_id,
            accelerator_id=accelerator_id,
            second_pass=second_pass,
            image_count=image_count,
            audio_count=audio_count,
            video_count=video_count,
            object_info=object_info,
            low_vram_attention=low_vram_attention,
            low_vram_decision=low_vram_decision,
        )
    except GraphCompileError as exc:
        if exc.code == "REF2VA_IMAGE_REFERENCE_REQUIRED":
            raise H3ExecutionError(
                "PROJECT_REQUIRES_REF2VA",
                "Ref2VA 生产要求每个镜头至少绑定 1 张有效图片参考。",
                {"image_count": image_count},
            ) from exc
        raise


def extract_video_output(history_record: dict[str, Any]) -> tuple[str, str, str]:
    """Extract ``(filename, subfolder, file_type)`` from a ComfyUI history record."""
    output = find_output(history_record, ("videos", "gifs", "images"))
    if output is None:
        raise H3ExecutionError("COMFY_VIDEO_OUTPUT_MISSING", "ComfyUI 已完成执行但未返回视频输出。")
    return str(output["filename"]), str(output.get("subfolder") or ""), str(output.get("type") or "output")


def extract_last_frame_png(video_path: Path, out_png_path: Path, *, ffmpeg_path: str = "ffmpeg") -> Path:
    """Extract the tail PNG frame from a completed shot video using FFmpeg."""
    executable = shutil.which(ffmpeg_path)
    if not executable:
        raise H3ExecutionError("FFMPEG_MISSING", "未检测到 FFmpeg，无法提取上一镜尾帧。")
    out_png_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        executable,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-sseof",
        "-0.08",
        "-i",
        str(video_path),
        "-vframes",
        "1",
        str(out_png_path),
    ]
    res = subprocess.run(cmd, capture_output=True, timeout=60, check=False)
    if res.returncode != 0 or not out_png_path.is_file() or out_png_path.stat().st_size == 0:
        stderr = res.stderr.decode("utf-8", errors="replace").strip()
        raise H3ExecutionError("CONTINUITY_FRAME_FAILED", f"提取上一镜尾帧失败：{stderr[:200]}")
    return out_png_path


def evaluate_h3_technical_qc(
    video_path: Path,
    *,
    duration_s: float,
    expect_audio: bool,
    duration_tolerance_s: float = 1.0,
    ffprobe_path: str = "ffprobe",
    ffmpeg_path: str = "ffmpeg",
) -> dict[str, Any]:
    """Single deterministic TechnicalQC entrypoint for H3 renders."""
    qc = TechnicalQC(ffprobe_path=ffprobe_path, ffmpeg_path=ffmpeg_path)
    result = qc.check(
        video_path,
        expectations=TechnicalExpectations(
            duration_s=duration_s,
            duration_tolerance_s=duration_tolerance_s,
            expect_audio=expect_audio,
            require_decodable=True,
        ),
    )
    return result.model_dump(mode="json")


class ComfyH3Executor:
    """Prepare a frozen prompt with the shared Ref2VA graph and ComfyUI submitter."""

    def execute(
        self,
        client: ComfyUIClient,
        request: H3ExecutionRequest,
        *,
        on_before_submit: Callable[[str, dict[str, Any]], None] | None = None,
        on_submitted: Callable[[str], None] | None = None,
        object_info: dict[str, Any] | None = None,
        poll_timeout_s: float = 900.0,
        poll_interval_s: float = 0.25,
    ) -> H3ExecutionReceipt:
        actual_prompt_sha = hashlib.sha256(request.prompt_text.encode()).hexdigest()
        if actual_prompt_sha != request.prompt_sha256:
            raise H3ExecutionError(
                "PROMPT_HASH_MISMATCH",
                "提交前校验发现提示词文本哈希与冻结快照不一致。",
                {"expected": request.prompt_sha256, "actual": actual_prompt_sha},
            )

        images = [r for r in request.references if r.family == "image"]
        audios = [r for r in request.references if r.family == "audio"]
        videos = [r for r in request.references if r.family == "video"]

        for aud in audios:
            if aud.duration_s is not None and aud.duration_s > DEFAULT_AUDIO_DURATION_MAX_S:
                raise H3ExecutionError(
                    "AUDIO_DURATION_EXCEEDED",
                    f"参考音频「{aud.filename}」时长 {aud.duration_s:.1f}s "
                    f"超过上限 {DEFAULT_AUDIO_DURATION_MAX_S:.0f}s。",
                )

        # Read and verify all reference bytes BEFORE touching ComfyUI or transactions.
        loaded_refs: list[tuple[H3ReferenceInput, bytes]] = []
        for ref in [*images, *audios, *videos]:
            if not ref.path.is_file():
                raise H3ExecutionError("REFERENCE_FILE_MISSING", f"参考素材文件缺失：{ref.filename}")
            raw = ref.path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            if digest != ref.sha256:
                raise H3ExecutionError(
                    "REFERENCE_SHA_MISMATCH",
                    f"参考素材校验和变化，拒绝执行：{ref.filename}",
                )
            loaded_refs.append((ref, raw))

        if object_info is None:
            object_info = client.object_info()
        video_ref_durations = [v.duration_s for v in videos if v.duration_s is not None and v.duration_s > 0]
        avg_video_ref_duration = (
            sum(video_ref_durations) / len(video_ref_durations) if video_ref_durations else 3.0
        )
        decision = decide_low_vram_attention(
            width=request.width,
            height=request.height,
            duration_s=request.duration_s,
            image_count=len(images),
            audio_count=len(audios),
            video_count=len(videos),
            threshold=request.low_vram_attention_token_threshold,
            force_low_vram_attention=request.force_low_vram_attention,
            video_ref_duration_s=avg_video_ref_duration,
        )
        compiled = compile_h3_ref2va_graph(
            image_count=len(images),
            audio_count=len(audios),
            video_count=len(videos),
            variant_id=request.variant_id,
            accelerator_id=request.accelerator_id,
            second_pass=request.second_pass,
            object_info=object_info,
            low_vram_attention=decision.enabled,
            low_vram_decision=decision,
        )
        if request.recipe_sha256 and compiled.recipe_sha256 != request.recipe_sha256:
            raise H3ExecutionError(
                "WORKFLOW_IDENTITY_CHANGED", "逐镜执行图与冻结请求的配方哈希不一致。",
                {"expected": request.recipe_sha256, "actual": compiled.recipe_sha256},
            )
        validation = compiled.validated_against or {}
        if validation["missing_nodes"] or validation["missing_inputs"]:
            raise H3ExecutionError(
                "H3_WORKFLOW_NODES_UNAVAILABLE",
                f"本机 ComfyUI 缺少 H3 执行节点或输入：{validation}",
                {"validation": validation},
            )

        uploaded_names: dict[str, str] = {}
        img_idx = 0
        aud_idx = 0
        vid_idx = 0
        for ref, raw in loaded_refs:
            transfer = compiled.media_transfers.get(ref.family)
            if not isinstance(transfer, dict):
                raise H3ExecutionError("PENDING_WORKFLOW_MAPPING", f"缺少 {ref.family} 上传适配器配置。")
            if hasattr(client, "upload_file"):
                res = client.upload_file(
                    ref.filename,
                    raw,
                    upload_path=str(transfer.get("upload_path") or "/upload/image"),
                    field_name=str(transfer.get("field_name") or "image"),
                    subfolder=str(transfer.get("subfolder") or "shine"),
                    content_type=ref.content_type or str(transfer.get("content_type") or "application/octet-stream"),
                    style=str(transfer.get("style") or "multipart"),
                )
            else:
                res = client.upload_image(
                    ref.filename,
                    raw,
                    upload_path=str(transfer.get("upload_path") or "/upload/image"),
                    field_name=str(transfer.get("field_name") or "image"),
                    subfolder=str(transfer.get("subfolder") or "shine"),
                    content_type=ref.content_type or str(transfer.get("content_type") or "application/octet-stream"),
                )
            remote_name = str(res.get("name") or ref.filename)
            if res.get("subfolder"):
                remote_name = f"{res['subfolder']}/{remote_name}"
            if ref.family == "image":
                img_idx += 1
                uploaded_names[f"reference_image_{img_idx}"] = remote_name
            elif ref.family == "audio":
                aud_idx += 1
                uploaded_names[f"reference_audio_{aud_idx}"] = remote_name
            elif ref.family == "video":
                vid_idx += 1
                uploaded_names[f"reference_video_{vid_idx}"] = remote_name

        effective_prompt = request.prompt_text
        if request.normalize_audio_prompt:
            ref_dicts = request.reference_manifest or [
                {
                    "use_as": "subject" if r.family == "image" else r.family,
                    "role_label": r.role_label,
                    "entity_name": r.role_label,
                }
                for r in request.references
            ]
            effective_prompt = ensure_h3_execution_prompt_contract(
                request.prompt_text,
                references=ref_dicts,
                dialogue=request.dialogue,
                project_language=request.project_language,
            )

        current_compiled = compiled
        current_decision = decision
        current_idempotency_key = request.idempotency_key
        fallback_triggered = False
        history_rec: dict[str, Any] | None = None
        final_graph_sha = ""
        prompt_id = ""

        while True:
            try:
                prepared = prepare_workflow_submission(
                    current_compiled,
                    prompt=effective_prompt,
                    duration_s=float(request.duration_s),
                    seed=int(request.seed),
                    width=int(request.width),
                    height=int(request.height),
                    references=uploaded_names,
                )
            except ComfyWorkflowError as exc:
                raise H3ExecutionError("WORKFLOW_PARAMETER_ERROR", str(exc), details=exc.details) from exc

            if on_before_submit is not None:
                on_before_submit(prepared.prompt_graph_sha256, prepared.prompt)

            final_graph_sha = prepared.prompt_graph_sha256
            try:
                submission = submit_prepared_prompt(
                    client,
                    prepared,
                    idempotency_key=current_idempotency_key,
                    prompt_sha256=request.prompt_sha256,
                    extra_data=request.extra_data,
                )
                prompt_id = submission.prompt_id
            except ComfySubmissionUncertain:
                # The owned renderer is still alive. Recover its ACK before releasing it.
                if not current_idempotency_key:
                    raise
                recovered_id, _, _ = reconcile_submission(
                    client, prompt_id=None, prompt_graph_sha256=final_graph_sha,
                    idempotency_key=current_idempotency_key,
                )
                if recovered_id is None:
                    raise
                prompt_id = recovered_id

            if on_submitted is not None:
                try:
                    on_submitted(prompt_id)
                except Exception as exc:
                    raise ComfySubmissionUncertain("ComfyUI 已回执，但未能持久化 prompt_id；需要对账。") from exc

            deadline = time.monotonic() + max(1.0, poll_timeout_s)
            interval = max(0.05, poll_interval_s)
            retry_with_low_vram = False
            while True:
                matched_id, state, rec = reconcile_submission(
                    client,
                    prompt_id=prompt_id,
                )
                if state == "failed":
                    error_info = extract_comfy_history_error(rec)
                    error_text = format_comfy_history_error(error_info, fallback="ComfyUI 执行失败。")
                    if (
                        not fallback_triggered
                        and request.retry_on_oom_with_low_vram
                        and not current_decision.enabled
                        and is_cuda_oom_error(error_info, error_text)
                    ):
                        fallback_triggered = True
                        current_decision = decide_low_vram_attention(
                            width=request.width,
                            height=request.height,
                            duration_s=request.duration_s,
                            image_count=len(images),
                            audio_count=len(audios),
                            video_count=len(videos),
                            threshold=request.low_vram_attention_token_threshold,
                            force_low_vram_attention=True,
                            video_ref_duration_s=avg_video_ref_duration,
                        )
                        current_compiled = compile_h3_ref2va_graph(
                            image_count=len(images),
                            audio_count=len(audios),
                            video_count=len(videos),
                            variant_id=request.variant_id,
                            accelerator_id=request.accelerator_id,
                            second_pass=request.second_pass,
                            object_info=object_info,
                            low_vram_attention=True,
                            low_vram_decision=current_decision,
                        )
                        fb_validation = current_compiled.validated_against or {}
                        if fb_validation["missing_nodes"] or fb_validation["missing_inputs"]:
                            raise H3ExecutionError(
                                "H3_WORKFLOW_NODES_UNAVAILABLE",
                                f"本机 ComfyUI 缺少 H3 LowVRAM 兜底节点或输入：{fb_validation}",
                                {"validation": fb_validation},
                            )
                        current_idempotency_key = (
                            f"{request.idempotency_key}:oom-lowvram" if request.idempotency_key else ""
                        )
                        retry_with_low_vram = True
                        break
                    return H3ExecutionReceipt(
                        status="failed",
                        prompt_id=matched_id or prompt_id,
                        prompt_graph_sha256=final_graph_sha,
                        model_identity=current_compiled.model_identity,
                        error_code="COMFY_EXECUTION_ERROR",
                        error=error_text,
                        error_details=error_info,
                        low_vram_attention_used=current_decision.enabled,
                        oom_fallback_triggered=fallback_triggered,
                    )
                if state == "completed" and rec is not None:
                    history_rec = rec
                    break
                if state == "completed_without_output":
                    return H3ExecutionReceipt(
                        status="failed",
                        prompt_id=matched_id or prompt_id,
                        prompt_graph_sha256=final_graph_sha,
                        model_identity=current_compiled.model_identity,
                        error_code="COMFY_VIDEO_OUTPUT_MISSING",
                        error="ComfyUI 完成执行但未产生任何视频输出。",
                        low_vram_attention_used=current_decision.enabled,
                        oom_fallback_triggered=fallback_triggered,
                    )
                if time.monotonic() >= deadline:
                    return H3ExecutionReceipt(
                        status="timeout",
                        prompt_id=prompt_id,
                        prompt_graph_sha256=final_graph_sha,
                        model_identity=current_compiled.model_identity,
                        error_code="COMFY_TIMEOUT",
                        error="ComfyUI 视频生成超时。",
                        low_vram_attention_used=current_decision.enabled,
                        oom_fallback_triggered=fallback_triggered,
                    )
                time.sleep(interval)

            if not retry_with_low_vram:
                break

        filename, subfolder, file_type = extract_video_output(history_rec)
        out_bytes = client.view(filename, subfolder=subfolder, file_type=file_type)
        out_bytes = enforce_h3_render_audio_contract(
            out_bytes,
            prompt_text=effective_prompt,
            dialogue=request.dialogue,
            references=request.reference_manifest or request.references,
            ffmpeg_path=request.ffmpeg_path,
        )
        out_sha = hashlib.sha256(out_bytes).hexdigest()
        return H3ExecutionReceipt(
            status="completed",
            prompt_id=prompt_id,
            prompt_graph_sha256=final_graph_sha,
            output_filename=filename,
            output_bytes=out_bytes,
            output_sha256=out_sha,
            model_identity=current_compiled.model_identity,
            low_vram_attention_used=current_decision.enabled,
            oom_fallback_triggered=fallback_triggered,
        )


__all__ = [
    "ACCELERATORS",
    "ComfyError",
    "ComfyPromptRejected",
    "ComfySubmissionUncertain",
    "ComfyUIClient",
    "ComfyUnavailable",
    "CompiledWorkflow",
    "ComfyH3Executor",
    "DEFAULT_LOW_VRAM_ATTENTION_TOKEN_THRESHOLD",
    "GraphCompileError",
    "H3ExecutionError",
    "H3ExecutionReceipt",
    "H3ExecutionRequest",
    "H3ReferenceInput",
    "H3TokenEstimate",
    "LowVramAttentionDecision",
    "MAX_AUDIO_SLOTS",
    "MAX_IMAGE_SLOTS",
    "MAX_TOTAL_SLOTS",
    "MAX_VIDEO_SLOTS",
    "SecondPassSpec",
    "align_h3_frame_count",
    "apply_parameters",
    "canonical_h3_language",
    "compile_h3_ref2va_graph",
    "decide_low_vram_attention",
    "enforce_h3_render_audio_contract",
    "ensure_h3_execution_prompt_contract",
    "estimate_h3_attention_tokens",
    "evaluate_h3_technical_qc",
    "extract_comfy_history_error",
    "extract_comfy_prompt_texts",
    "extract_last_frame_png",
    "extract_video_output",
    "format_comfy_history_error",
    "graph_hash",
    "has_authorized_h3_speech",
    "is_cuda_oom_error",
    "is_low_vram_guard_variant",
    "low_vram_threshold_from_env",
    "reconcile_submission",
    "sanitize_h3_dialogue_text",
    "verified_variants_catalog",
]
