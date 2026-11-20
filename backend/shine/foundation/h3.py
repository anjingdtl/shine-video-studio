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
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
    TOTAL_REFERENCE_LIMIT,
    VIDEO_REFERENCE_LIMIT,
    CompiledWorkflow,
    GraphCompileError,
    SecondPassSpec,
    compile_ref2va_workflow,
)
from shine.comfy.provider import PreparedSubmission, find_output, reconcile_submission, submit_prepared_prompt
from shine.comfy.workflow import apply_parameters, graph_hash, validate_against_object_info
from shine.h3.mode_policy import REQUIRED_GENERATION_MODE, ModePolicyError, require_project_mode
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


def verified_variants_catalog() -> list[dict[str, Any]]:
    """Return H3 stack variants registered by the machine catalog."""
    return [
        {
            "variant_id": "h3-ref2va-int8-convrot",
            "label": "H3 Ref2VA INT8 ConvRot（本机真机基线）",
            "verified": True,
            "accelerators": ["pdd8", "native20"],
        }
    ]


def compile_h3_ref2va_graph(
    *,
    image_count: int,
    audio_count: int = 0,
    video_count: int = 0,
    variant_id: str = "h3-ref2va-int8-convrot",
    accelerator_id: str = "pdd8",
    second_pass: SecondPassSpec | None = None,
    object_info: dict[str, Any] | None = None,
) -> CompiledWorkflow:
    """Single Ref2VA compile entry enforcing Ref2VA-only policy & slot limits."""
    try:
        require_project_mode(REQUIRED_GENERATION_MODE)
    except ModePolicyError as exc:
        raise H3ExecutionError("PROJECT_REQUIRES_REF2VA", str(exc)) from exc

    if image_count <= 0:
        raise H3ExecutionError(
            "PROJECT_REQUIRES_REF2VA",
            "Ref2VA 生产要求每个镜头至少绑定 1 张有效图片参考。",
            {"image_count": image_count},
        )
    if image_count + audio_count + video_count > MAX_TOTAL_SLOTS:
        raise H3ExecutionError(
            "TOTAL_REFERENCE_LIMIT_EXCEEDED",
            f"参考素材总数不能超过 {MAX_TOTAL_SLOTS}。",
            {"image_count": image_count, "audio_count": audio_count, "video_count": video_count},
        )
    return compile_ref2va_workflow(
        variant_id=variant_id,
        accelerator_id=accelerator_id,
        second_pass=second_pass,
        image_count=image_count,
        audio_count=audio_count,
        video_count=video_count,
        object_info=object_info,
    )


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
        compiled = compile_h3_ref2va_graph(
            image_count=len(images),
            audio_count=len(audios),
            video_count=len(videos),
            variant_id=request.variant_id,
            accelerator_id=request.accelerator_id,
            second_pass=request.second_pass,
            object_info=object_info,
        )
        validation = compiled.validated_against or validate_against_object_info(
            compiled.graph, compiled.parameter_map, object_info
        )
        if validation["missing_nodes"] or validation["missing_inputs"]:
            raise H3ExecutionError(
                "H3_WORKFLOW_NODES_UNAVAILABLE",
                f"本机 ComfyUI 缺少 H3 执行节点或输入：{validation}",
                {"validation": validation},
            )

        uploaded_names: dict[str, str] = {}
        img_idx = 0
        aud_idx = 0
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

        param_values: dict[str, Any] = {
            "positive_prompt": request.prompt_text,
            "duration": float(request.duration_s),
            "seed": int(request.seed),
            "width": int(request.width),
            "height": int(request.height),
            **uploaded_names,
        }
        try:
            final_graph = apply_parameters(compiled.graph, compiled.parameter_map, param_values)
        except ComfyWorkflowError as exc:
            raise H3ExecutionError("WORKFLOW_PARAMETER_ERROR", str(exc), details=exc.details) from exc
        prepared = PreparedSubmission(
            prompt=final_graph,
            prompt_graph_sha256=graph_hash(final_graph),
            workflow_hash=compiled.graph_sha256,
            uploaded_references=len(uploaded_names),
        )

        if on_before_submit is not None:
            on_before_submit(prepared.prompt_graph_sha256, prepared.prompt)

        final_graph_sha = prepared.prompt_graph_sha256
        try:
            submission = submit_prepared_prompt(
                client,
                prepared,
                idempotency_key=request.idempotency_key,
                prompt_sha256=request.prompt_sha256,
                extra_data=request.extra_data,
            )
            prompt_id = submission.prompt_id
        except ComfySubmissionUncertain:
            # The owned renderer is still alive. Recover its ACK before releasing it.
            if not request.idempotency_key:
                raise
            prompt_id, _, _ = reconcile_submission(
                client, prompt_id=None, prompt_graph_sha256=final_graph_sha,
                idempotency_key=request.idempotency_key,
            )
            if prompt_id is None:
                raise

        if on_submitted is not None:
            try:
                on_submitted(prompt_id)
            except Exception as exc:
                raise ComfySubmissionUncertain("ComfyUI 已回执，但未能持久化 prompt_id；需要对账。") from exc

        deadline = time.monotonic() + max(1.0, poll_timeout_s)
        interval = max(0.05, poll_interval_s)
        history_rec: dict[str, Any] | None = None
        while True:
            matched_id, state, rec = reconcile_submission(
                client,
                prompt_id=prompt_id,
            )
            if state == "failed":
                return H3ExecutionReceipt(
                    status="failed",
                    prompt_id=matched_id or prompt_id,
                    prompt_graph_sha256=final_graph_sha,
                    model_identity=compiled.model_identity,
                    error_code="COMFY_EXECUTION_ERROR",
                    error="ComfyUI 执行失败。",
                )
            if state == "completed" and rec is not None:
                history_rec = rec
                break
            if state == "completed_without_output":
                return H3ExecutionReceipt(
                    status="failed",
                    prompt_id=matched_id or prompt_id,
                    prompt_graph_sha256=final_graph_sha,
                    model_identity=compiled.model_identity,
                    error_code="COMFY_VIDEO_OUTPUT_MISSING",
                    error="ComfyUI 完成执行但未产生任何视频输出。",
                )
            if time.monotonic() >= deadline:
                return H3ExecutionReceipt(
                    status="timeout",
                    prompt_id=prompt_id,
                    prompt_graph_sha256=final_graph_sha,
                    model_identity=compiled.model_identity,
                    error_code="COMFY_TIMEOUT",
                    error="ComfyUI 视频生成超时。",
                )
            time.sleep(interval)

        filename, subfolder, file_type = extract_video_output(history_rec)
        out_bytes = client.view(filename, subfolder=subfolder, file_type=file_type)
        out_sha = hashlib.sha256(out_bytes).hexdigest()
        return H3ExecutionReceipt(
            status="completed",
            prompt_id=prompt_id,
            prompt_graph_sha256=final_graph_sha,
            output_filename=filename,
            output_bytes=out_bytes,
            output_sha256=out_sha,
            model_identity=compiled.model_identity,
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
    "GraphCompileError",
    "H3ExecutionError",
    "H3ExecutionReceipt",
    "H3ExecutionRequest",
    "H3ReferenceInput",
    "MAX_AUDIO_SLOTS",
    "MAX_IMAGE_SLOTS",
    "MAX_TOTAL_SLOTS",
    "MAX_VIDEO_SLOTS",
    "SecondPassSpec",
    "apply_parameters",
    "compile_h3_ref2va_graph",
    "evaluate_h3_technical_qc",
    "extract_last_frame_png",
    "extract_video_output",
    "graph_hash",
    "reconcile_submission",
    "verified_variants_catalog",
]
