"""Token-based gating for MiniMaxLowVRAMAttention and CUDA OOM error detection.

This module provides the single source of truth for:
1. Calibrated H3 DiT sequence length (``seq_len``) estimation matching ComfyUI's
   ``PackedLayout`` (VAE 16x spatial + DiT 2x2 patch = 32px patch grid,
   17k+5 -> 5k+2 temporal latent compression, plus 40 Hz stereo audio).
2. The 45,000-token high-watermark threshold decision for mounting
   ``MiniMaxLowVRAMAttention`` without degrading image/video quality.
3. Structured extraction of ComfyUI ``/history`` ``execution_error`` payloads
   and deterministic CUDA OOM classification for single-shot fallback retries.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal

DEFAULT_LOW_VRAM_ATTENTION_TOKEN_THRESHOLD = 45_000
DEFAULT_TEXT_CONDITION_TOKENS = 1_024
DEFAULT_TOKENS_PER_AUDIO_REF = 512
DEFAULT_VIDEO_REF_DURATION_S = 3.0
DEFAULT_WIDTH = 1344
DEFAULT_HEIGHT = 768
DEFAULT_DURATION_S = 5.0


@dataclass(frozen=True)
class H3TokenEstimate:
    """Calibrated breakdown of MiniMax H3 packed-DiT sequence tokens."""

    aligned_frames: int
    latent_frames: int
    tokens_per_latent_frame: int
    video_tokens: int
    image_ref_tokens: int
    video_ref_tokens: int
    audio_and_text_tokens: int
    total_tokens: int


@dataclass(frozen=True)
class LowVramAttentionDecision:
    """Single authoritative gating decision for ``MiniMaxLowVRAMAttention``.

    ``frozen_request`` marks the one case the threshold does not decide: a
    request already frozen names one of the shot's two legal graphs (every
    request frozen before the gate names the mounted one), and the Worker
    submits the graph the request demands.
    """

    enabled: bool
    reason: Literal[
        "below_threshold",
        "token_threshold_reached",
        "oom_fallback",
        "forced_disabled",
        "legacy_default",
        "frozen_request",
    ]
    threshold: int
    estimate: H3TokenEstimate | None = None


def low_vram_threshold_from_env(
    default: int = DEFAULT_LOW_VRAM_ATTENTION_TOKEN_THRESHOLD,
) -> int:
    """Read ``SHINE_H3_LOW_VRAM_ATTN_TOKEN_THRESHOLD`` with validation."""
    raw = os.getenv("SHINE_H3_LOW_VRAM_ATTN_TOKEN_THRESHOLD")
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("SHINE_H3_LOW_VRAM_ATTN_TOKEN_THRESHOLD must be an integer") from exc
    if value < 0:
        raise ValueError("SHINE_H3_LOW_VRAM_ATTN_TOKEN_THRESHOLD must be >= 0")
    return value


def align_h3_frame_count(duration_s: float, *, fps: float = 24.0) -> int:
    """Mirror ``graph_compiler.LENGTH_EXPRESSION`` on the 17k+5 frame grid at 24 fps."""
    raw = max(5, round(float(duration_s) * fps))
    return raw + (5 - (raw % 17)) % 17


def video_latent_frames(frame_count: int) -> int:
    """Mirror ``comfy_extras.nodes_minimax_h3.video_latent_t`` (17k+5 -> 5k+2)."""
    if frame_count <= 5:
        return 2
    return ((frame_count - 5) // 17) * 5 + 2


def estimate_h3_attention_tokens(
    *,
    width: int,
    height: int,
    duration_s: float,
    image_count: int = 1,
    audio_count: int = 0,
    video_count: int = 0,
    video_ref_duration_s: float = DEFAULT_VIDEO_REF_DURATION_S,
    text_tokens: int = DEFAULT_TEXT_CONDITION_TOKENS,
    tokens_per_audio_ref: int = DEFAULT_TOKENS_PER_AUDIO_REF,
    fps: float = 24.0,
) -> H3TokenEstimate:
    """Estimate the packed DiT ``seq_len`` matching ComfyUI's ``PackedLayout``.

    Calibrated against local ComfyUI ``comfy/ldm/minimax/model.py`` and
    ``comfy_extras/nodes_minimax_h3.py``:
    - VAE encodes spatial axes at /16 and DiT applies a 2x2 spatial patch
      (rounding latent H/W up to even before halving).
    - Temporal compression maps ``17k + 5`` pixel frames to ``5k + 2`` latent
      frames.
    - Target stereo audio contributes ``round(aligned_frames / 24 * 40) * 2``
      tokens alongside text conditioning and reference blocks.
    """
    eff_w = int(width) if width and int(width) > 0 else DEFAULT_WIDTH
    eff_h = int(height) if height and int(height) > 0 else DEFAULT_HEIGHT
    eff_dur = float(duration_s) if duration_s and float(duration_s) > 0 else DEFAULT_DURATION_S

    aligned_frames = align_h3_frame_count(eff_dur, fps=fps)
    latent_frames = video_latent_frames(aligned_frames)

    lat_w = (max(1, eff_w // 16) + 1) // 2 * 2
    lat_h = (max(1, eff_h // 16) + 1) // 2 * 2
    tokens_per_latent_frame = max(1, (lat_w // 2) * (lat_h // 2))

    video_tokens = latent_frames * tokens_per_latent_frame
    image_ref_tokens = max(0, int(image_count)) * tokens_per_latent_frame

    if int(video_count) > 0:
        ref_frames = align_h3_frame_count(video_ref_duration_s, fps=fps)
        ref_latent_frames = video_latent_frames(ref_frames)
        video_ref_tokens = max(0, int(video_count)) * ref_latent_frames * tokens_per_latent_frame
    else:
        video_ref_tokens = 0

    target_audio_tokens = round((aligned_frames / fps) * 40.0) * 2
    audio_and_text_tokens = (
        max(0, int(text_tokens))
        + target_audio_tokens
        + max(0, int(audio_count)) * max(0, int(tokens_per_audio_ref))
    )
    total_tokens = video_tokens + image_ref_tokens + video_ref_tokens + audio_and_text_tokens
    return H3TokenEstimate(
        aligned_frames=aligned_frames,
        latent_frames=latent_frames,
        tokens_per_latent_frame=tokens_per_latent_frame,
        video_tokens=video_tokens,
        image_ref_tokens=image_ref_tokens,
        video_ref_tokens=video_ref_tokens,
        audio_and_text_tokens=audio_and_text_tokens,
        total_tokens=total_tokens,
    )


def decide_low_vram_attention(
    *,
    width: int | None = None,
    height: int | None = None,
    duration_s: float | None = None,
    image_count: int = 1,
    audio_count: int = 0,
    video_count: int = 0,
    threshold: int = DEFAULT_LOW_VRAM_ATTENTION_TOKEN_THRESHOLD,
    force_low_vram_attention: bool | None = None,
    video_ref_duration_s: float = DEFAULT_VIDEO_REF_DURATION_S,
) -> LowVramAttentionDecision:
    """Compute whether ``MiniMaxLowVRAMAttention`` should be mounted."""
    eff_threshold = max(0, int(threshold))
    has_geometry = width is not None and height is not None and int(width) > 0 and int(height) > 0
    estimate = (
        estimate_h3_attention_tokens(
            width=int(width),
            height=int(height),
            duration_s=float(duration_s) if duration_s and float(duration_s) > 0 else DEFAULT_DURATION_S,
            image_count=image_count,
            audio_count=audio_count,
            video_count=video_count,
            video_ref_duration_s=video_ref_duration_s,
        )
        if has_geometry
        else None
    )

    if force_low_vram_attention is True:
        return LowVramAttentionDecision(
            enabled=True,
            reason="oom_fallback",
            threshold=eff_threshold,
            estimate=estimate,
        )
    if force_low_vram_attention is False:
        return LowVramAttentionDecision(
            enabled=False,
            reason="forced_disabled",
            threshold=eff_threshold,
            estimate=estimate,
        )
    if eff_threshold <= 0:
        return LowVramAttentionDecision(
            enabled=True,
            reason="token_threshold_reached",
            threshold=eff_threshold,
            estimate=estimate,
        )
    if estimate is None:
        # Preserve legacy graph identities when canvas dimensions are absent.
        return LowVramAttentionDecision(
            enabled=True,
            reason="legacy_default",
            threshold=eff_threshold,
            estimate=None,
        )
    if estimate.total_tokens >= eff_threshold:
        return LowVramAttentionDecision(
            enabled=True,
            reason="token_threshold_reached",
            threshold=eff_threshold,
            estimate=estimate,
        )
    return LowVramAttentionDecision(
        enabled=False,
        reason="below_threshold",
        threshold=eff_threshold,
        estimate=estimate,
    )


def extract_comfy_history_error(record: dict[str, Any] | None) -> dict[str, Any] | None:
    """Extract structured ``execution_error`` details from a ComfyUI ``/history`` record."""
    if not isinstance(record, dict):
        return None
    status = record.get("status")
    if not isinstance(status, dict):
        return None
    messages = status.get("messages")
    if not isinstance(messages, list):
        return None

    payload: dict[str, Any] | None = None
    for entry in messages:
        if (
            isinstance(entry, list | tuple)
            and len(entry) >= 2
            and entry[0] == "execution_error"
            and isinstance(entry[1], dict)
        ):
            payload = entry[1]

    if payload is None:
        return None

    raw_tb = payload.get("traceback")
    tb_summary: str | None = None
    if isinstance(raw_tb, list):
        lines = [str(item).strip() for item in raw_tb if str(item).strip()]
        if lines:
            tb_summary = "\n".join(lines[-4:])[:600]
    elif isinstance(raw_tb, str) and raw_tb.strip():
        tb_summary = raw_tb.strip()[-600:]

    return {
        "node_id": str(payload["node_id"]) if payload.get("node_id") is not None else None,
        "node_type": str(payload["node_type"]) if payload.get("node_type") is not None else None,
        "exception_type": (
            str(payload["exception_type"]) if payload.get("exception_type") is not None else None
        ),
        "exception_message": (
            str(payload["exception_message"])[:600]
            if payload.get("exception_message") is not None
            else None
        ),
        "traceback_summary": tb_summary,
    }


def format_comfy_history_error(
    error_info: dict[str, Any] | None,
    *,
    fallback: str = "ComfyUI 执行失败。",
) -> str:
    """Build a concise human-readable error string from extracted ComfyUI error details."""
    if not isinstance(error_info, dict):
        return fallback
    parts: list[str] = []
    node_type = error_info.get("node_type")
    exc_type = error_info.get("exception_type")
    exc_msg = error_info.get("exception_message")
    if node_type:
        parts.append(str(node_type))
    if exc_type and exc_msg:
        parts.append(f"{exc_type}: {exc_msg}")
    elif exc_type:
        parts.append(str(exc_type))
    elif exc_msg:
        parts.append(str(exc_msg))
    return " | ".join(parts)[:500] if parts else fallback


_OOM_PHRASES = (
    "cuda out of memory",
    "hip out of memory",
    "cublas_status_alloc_failed",
    "allocation on device",
    "cuda error: out of memory",
    "mps backend out of memory",
)


def is_cuda_oom_error(
    error_info: dict[str, Any] | None = None,
    message: str | None = None,
) -> bool:
    """Return True only when the error clearly indicates GPU/CUDA out-of-memory."""
    exc_type = ""
    fields: list[str] = []
    if isinstance(error_info, dict):
        exc_type = str(error_info.get("exception_type") or "")
        for key in ("exception_type", "exception_message", "traceback_summary"):
            val = error_info.get(key)
            if val:
                fields.append(str(val))
    if message:
        fields.append(str(message))

    if "outofmemoryerror" in exc_type.lower():
        return True

    combined = " ".join(fields).lower()
    if not combined:
        return False
    if any(phrase in combined for phrase in _OOM_PHRASES):
        return True
    if "out of memory" in combined and "host memory" not in combined and "disk" not in combined:
        return True
    return False


__all__ = [
    "DEFAULT_DURATION_S",
    "DEFAULT_HEIGHT",
    "DEFAULT_LOW_VRAM_ATTENTION_TOKEN_THRESHOLD",
    "DEFAULT_TEXT_CONDITION_TOKENS",
    "DEFAULT_TOKENS_PER_AUDIO_REF",
    "DEFAULT_VIDEO_REF_DURATION_S",
    "DEFAULT_WIDTH",
    "H3TokenEstimate",
    "LowVramAttentionDecision",
    "align_h3_frame_count",
    "decide_low_vram_attention",
    "estimate_h3_attention_tokens",
    "extract_comfy_history_error",
    "format_comfy_history_error",
    "is_cuda_oom_error",
    "low_vram_threshold_from_env",
    "video_latent_frames",
]
