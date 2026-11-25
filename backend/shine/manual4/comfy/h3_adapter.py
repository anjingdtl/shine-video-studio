"""Manual4 H3 Ref2VA adapter delegating to the unified ``shine.foundation.h3`` engine."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from shine.foundation.h3 import (
    ACCELERATORS,
    MAX_AUDIO_SLOTS,
    MAX_IMAGE_SLOTS,
    MAX_STYLE_LORAS,
    MAX_TOTAL_SLOTS,
    MAX_VIDEO_SLOTS,
    CompiledWorkflow,
    GraphCompileError,
    H3ExecutionError,
    SecondPassSpec,
    StyleLoraConfig,
    StyleLoraError,
    StyleLoraSpec,
    apply_style_lora_triggers,
    compile_h3_ref2va_graph,
    evaluate_style_lora_selection,
    extract_last_frame_png,
    extract_video_output,
    verified_variants_catalog,
)


@dataclass
class M4GraphCompileError(Exception):
    code: str
    message: str
    details: dict[str, Any] | None = None

    def __str__(self) -> str:
        return self.message


M4SecondPassSpec = SecondPassSpec
M4CompiledWorkflow = CompiledWorkflow

IMAGE_REFERENCE_LIMIT = MAX_IMAGE_SLOTS
AUDIO_REFERENCE_LIMIT = MAX_AUDIO_SLOTS
VIDEO_REFERENCE_LIMIT = MAX_VIDEO_SLOTS
TOTAL_REFERENCE_LIMIT = MAX_TOTAL_SLOTS


def compile_m4_ref2va_workflow(
    *,
    variant_id: str = "h3-ref2va-int8-convrot",
    accelerator_id: str = "pdd8",
    second_pass: M4SecondPassSpec | None = None,
    aspect_ratio: str = "16:9",
    resolution_scale: float = 1.0,
    image_count: int,
    audio_count: int = 0,
    video_count: int = 0,
    object_info: dict[str, Any] | None = None,
    low_vram_attention: bool | None = None,
    style_loras: Sequence[StyleLoraSpec] = (),
) -> CompiledWorkflow:
    _ = (aspect_ratio, resolution_scale)
    try:
        return compile_h3_ref2va_graph(
            variant_id=variant_id,
            accelerator_id=accelerator_id,
            second_pass=second_pass,
            image_count=image_count,
            audio_count=audio_count,
            video_count=video_count,
            object_info=object_info,
            low_vram_attention=low_vram_attention,
            style_loras=style_loras,
        )
    except (GraphCompileError, H3ExecutionError) as exc:
        code_map = {
            "ACCELERATOR_UNKNOWN": "M4_ACCELERATOR_UNKNOWN",
            "ACCELERATOR_UNVERIFIED": "M4_ACCELERATOR_UNVERIFIED",
        }
        raise M4GraphCompileError(code_map.get(exc.code, exc.code), str(exc), getattr(exc, "details", None)) from exc


__all__ = [
    "ACCELERATORS",
    "AUDIO_REFERENCE_LIMIT",
    "IMAGE_REFERENCE_LIMIT",
    "M4CompiledWorkflow",
    "M4GraphCompileError",
    "M4SecondPassSpec",
    "MAX_STYLE_LORAS",
    "StyleLoraConfig",
    "StyleLoraError",
    "StyleLoraSpec",
    "TOTAL_REFERENCE_LIMIT",
    "VIDEO_REFERENCE_LIMIT",
    "apply_style_lora_triggers",
    "compile_m4_ref2va_workflow",
    "evaluate_style_lora_selection",
    "extract_last_frame_png",
    "extract_video_output",
    "verified_variants_catalog",
]
