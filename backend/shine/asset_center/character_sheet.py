"""Character sheet generation pipeline, readiness projection, and deterministic compositor.

Three Principles alignment (一套事实、一个裁决、一个执行者):
- One Fact: reads ``qwen-image-2.1-lora-multiple-angles`` strictly from the
  unified ``style_lora_assets`` ledger by SHA-256 and never creates a second
  LoRA registry or task table.
- One Adjudicator: freezes and validates the LoRA selection exclusively
  through ``evaluate_style_lora_selection()``.
- One Executor: supplies deterministic prompts, dimensions, and Pillow layout
  composition for the single asset-center image worker.
"""

from __future__ import annotations

import io
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from shine.comfyops.loras import (
    IMAGE_LORA_BASE,
    StyleLoraError,
    evaluate_style_lora_selection,
    list_style_loras,
    sync_discovered_loras,
)
from shine.comfyops.models import StyleLoraAsset
from shine.materialprep.prompt_source import apply_image_lora_prompts

CHARACTER_SHEET_OPERATION = "CHARACTER_SHEET_V1"
CHARACTER_SHEET_SOURCE_MODE = "CHARACTER_SHEET"
CHARACTER_SHEET_LAYOUT_VERSION = "character-sheet-16x9-dual-closeup-triview-v2"

MULTIPLE_ANGLES_LORA_ID = "qwen-image-2.1-lora-multiple-angles"
MULTIPLE_ANGLES_LORA_SHA256 = "548c57977c3f1c09c2c9e5f4e83e930cbc6f719c878a576ab8666ce29fef39b6"
MULTIPLE_ANGLES_DEFAULT_STRENGTH = 0.8

CHARACTER_SHEET_SETUP_HINT = (
    "请前往「设置 → 制作环境」勾选「生图风格 LoRA 包」下载并完成纳管；"
    "角色定妆照不会静默降级为无 LoRA 生图。"
)

# Multiple-Angles v2 upstream baseline uses cfg=1.0 with euler/simple;
# local RTX 5080 ablation confirms reliable 3/4 turn & identity at strength=0.8, steps=36, cfg=1.0.
CHARACTER_SHEET_LORA_SAMPLING: dict[str, Any] = {
    "steps": 36,
    "cfg": 1.0,
    "sampler_name": "euler",
    "scheduler": "simple",
}
CHARACTER_SHEET_BASE_SAMPLING: dict[str, Any] = {
    "steps": 28,
    "cfg": 1.0,
    "sampler_name": "euler",
    "scheduler": "simple",
}

# Exact 16:9 zero-downscaling master sheet geometry (aligned with H3 ref_image_size="match"):
#   Canvas: 3584 x 2016 (16:9, both sides multiple of 32), outer margin = 64 -> inner height 1888
#   Left column (x=64..800): stacked 736x928 closeup_front + 32px gap + 736x928 closeup_3_4 = 1888h
#   Section gap: 64px (x=800..864)
#   Right block (x=864..3520): 3 slots x 864w separated by 32px gaps; each 576x1280 full-body
#   scales proportionally to height 1888 (850x1888, centered inside 864w slot -> 0 cropping/downscale).
CHARACTER_SHEET_BG_RGB = (232, 234, 237)
CHARACTER_SHEET_BG_HEX = "#E8EAED"
COMPOSITE_WIDTH = 3584
COMPOSITE_HEIGHT = 2016
CHARACTER_SHEET_OUTER_MARGIN = 64
CHARACTER_SHEET_CLOSEUP_GAP = 32
CHARACTER_SHEET_SECTION_GAP = 64
CHARACTER_SHEET_VIEW_GAP = 32
CHARACTER_SHEET_PANEL_HEIGHT = COMPOSITE_HEIGHT - 2 * CHARACTER_SHEET_OUTER_MARGIN

TRIVIEW_OUTER_MARGIN = 48
TRIVIEW_VIEW_GAP = 24
TRIVIEW_PANEL_HEIGHT = 1280

CLOSEUP_TARGET_WIDTH = 736
CLOSEUP_TARGET_HEIGHT = 928
FULLBODY_TARGET_WIDTH = 576
FULLBODY_TARGET_HEIGHT = 1280
STEP_TARGET_HEIGHT = FULLBODY_TARGET_HEIGHT

CHARACTER_SHEET_NEGATIVE_PROMPT = (
    "text, watermark, label, collage, grid, split screen, multiple people, "
    "extra limbs, cropped head, cropped feet, cluttered background, busy scenery, frame border"
)
CHARACTER_SHEET_CLOSEUP_NEGATIVE_PROMPT = (
    "straight front view, direct frontal camera, " + CHARACTER_SHEET_NEGATIVE_PROMPT
)


@dataclass(frozen=True)
class CharacterSheetStepSpec:
    key: str
    label: str
    uses_lora: bool
    reference_source: str
    target_width: int
    target_height: int


STEP_SPECS: tuple[CharacterSheetStepSpec, ...] = (
    CharacterSheetStepSpec(
        key="closeup_front",
        label="肩上正面照",
        uses_lora=False,
        reference_source="input",
        target_width=CLOSEUP_TARGET_WIDTH,
        target_height=CLOSEUP_TARGET_HEIGHT,
    ),
    CharacterSheetStepSpec(
        key="closeup_3_4",
        label="3/4 侧面肩上照",
        uses_lora=True,
        reference_source="input",
        target_width=CLOSEUP_TARGET_WIDTH,
        target_height=CLOSEUP_TARGET_HEIGHT,
    ),
    CharacterSheetStepSpec(
        key="fullbody_front",
        label="全身正面照",
        uses_lora=False,
        reference_source="input",
        target_width=FULLBODY_TARGET_WIDTH,
        target_height=FULLBODY_TARGET_HEIGHT,
    ),
    CharacterSheetStepSpec(
        key="fullbody_side",
        label="全身侧面照",
        uses_lora=True,
        reference_source="fullbody_front",
        target_width=FULLBODY_TARGET_WIDTH,
        target_height=FULLBODY_TARGET_HEIGHT,
    ),
    CharacterSheetStepSpec(
        key="fullbody_back",
        label="全身背面照",
        uses_lora=True,
        reference_source="fullbody_front",
        target_width=FULLBODY_TARGET_WIDTH,
        target_height=FULLBODY_TARGET_HEIGHT,
    ),
)

STEP_SPEC_BY_KEY: dict[str, CharacterSheetStepSpec] = {spec.key: spec for spec in STEP_SPECS}


def step_dimensions(
    source_width: int | None,
    source_height: int | None,
    *,
    target_width: int,
    target_height: int = STEP_TARGET_HEIGHT,
) -> tuple[int, int]:
    """Prevent center-cropping head/feet when the source reference is taller than the target aspect."""
    if (
        isinstance(source_width, int)
        and isinstance(source_height, int)
        and source_width > 0
        and source_height > 0
        and (source_width / source_height) < (target_width / target_height)
    ):
        narrowed = round(target_height * (source_width / source_height) / 32) * 32
        return max(512, min(target_width, narrowed)), target_height
    return target_width, target_height


def build_step_prompts(
    step_key: str,
    *,
    note: str = "",
    style_loras: Sequence[Mapping[str, Any]] = (),
) -> tuple[str, str]:
    """Build deterministic positive/negative prompts for one character-sheet step.

    Never injects T2I scene presets so reference subject identity is never overpowered.
    """
    cleaned_note = " ".join(str(note or "").split()).strip()
    if step_key == "closeup_front":
        base_prompt = (
            "front view head-and-shoulders bust portrait facing directly toward the camera, "
            "exact same character from the reference image, strictly preserving facial features, "
            "hairstyle, headwear, accessories, and upper garment details, clean solid neutral light "
            "gray studio backdrop (#E8EAED), even soft studio lighting, single subject centered, no text"
        )
        return base_prompt, CHARACTER_SHEET_NEGATIVE_PROMPT

    if step_key == "closeup_3_4":
        base_prompt = (
            "front-right quarter view, eye-level shot close-up, three-quarter side angle head-and-shoulders "
            "bust portrait, head and shoulders turned 45 degrees to the side, showing three-quarter profile of "
            "face and cheek while preserving exact facial identity, hairstyle, hairline, and garment, clean solid "
            "neutral light gray studio backdrop (#E8EAED), even soft studio lighting, no text"
        )
        positive, negative = apply_image_lora_prompts(
            base_prompt, CHARACTER_SHEET_CLOSEUP_NEGATIVE_PROMPT, style_loras, style_preset=None
        )
        if not positive.startswith("<mva>"):
            positive = f"<mva> {positive}"
        return positive, negative

    if step_key == "fullbody_front":
        base_prompt = (
            "full-body front standing portrait from head to toe with generous headroom and foot clearance, "
            "facing directly forward, exact same character from the reference image, strictly preserving "
            "facial features, hairstyle, headwear, accessories, and upper garment, naturally completing "
            "matching lower-body clothing and footwear consistent with the character style, arms relaxed "
            "naturally at sides, clean solid neutral light gray studio backdrop (#E8EAED), even soft "
            "studio lighting, single subject centered, no text"
        )
        if cleaned_note:
            base_prompt = f"{base_prompt}, additional lower-body clothing and footwear details: {cleaned_note}"
        return base_prompt, CHARACTER_SHEET_NEGATIVE_PROMPT

    if step_key == "fullbody_side":
        base_prompt = (
            "right side view, eye-level shot, full-body standing profile portrait from head to toe "
            "with generous headroom and foot clearance, exact same character, hairstyle, outfit, "
            "lower-body clothing, and footwear as the front reference image, clean solid neutral light "
            "gray studio backdrop (#E8EAED), even soft studio lighting, single subject centered, no text"
        )
        positive, negative = apply_image_lora_prompts(
            base_prompt, CHARACTER_SHEET_NEGATIVE_PROMPT, style_loras, style_preset=None
        )
        if not positive.startswith("<mva>"):
            positive = f"<mva> {positive}"
        return positive, negative

    if step_key == "fullbody_back":
        base_prompt = (
            "back view, eye-level shot, full-body rear standing portrait from head to toe "
            "with generous headroom and foot clearance, exact same character, hairstyle, outfit, "
            "lower-body clothing, and footwear seen from behind matching the front reference image, "
            "clean solid neutral light gray studio backdrop (#E8EAED), even soft studio lighting, "
            "single subject centered, no text"
        )
        positive, negative = apply_image_lora_prompts(
            base_prompt, CHARACTER_SHEET_NEGATIVE_PROMPT, style_loras, style_preset=None
        )
        if not positive.startswith("<mva>"):
            positive = f"<mva> {positive}"
        return positive, negative

    raise ValueError(f"Unsupported character sheet step: {step_key}")


def project_character_sheet_lora(
    db: Session,
    settings: Any,
    *,
    object_info: dict[str, Any] | None = None,
    probe_comfy: bool = False,
) -> dict[str, Any]:
    """Read readiness of ``qwen-image-2.1-lora-multiple-angles`` from ``style_lora_assets``."""
    listed = list_style_loras(
        db, settings, base_model=IMAGE_LORA_BASE, object_info=object_info, probe_comfy=probe_comfy
    )
    items = listed.get("items") or []
    match = next(
        (
            row
            for row in items
            if row.get("sha256") == MULTIPLE_ANGLES_LORA_SHA256
            and row.get("id") == MULTIPLE_ANGLES_LORA_ID
        ),
        None,
    ) or next((row for row in items if row.get("sha256") == MULTIPLE_ANGLES_LORA_SHA256), None)
    if match is None:
        return {
            "lora_id": MULTIPLE_ANGLES_LORA_ID,
            "lora_sha256": MULTIPLE_ANGLES_LORA_SHA256,
            "lora_status": "NOT_INSTALLED",
            "lora_ready": False,
            "comfy_reachable": bool(listed.get("comfy_reachable")),
            "display_name": "Qwen 多视角镜头",
            "setup_hint": CHARACTER_SHEET_SETUP_HINT,
        }
    lora_status = str(match.get("status") or "MISSING_FILE")
    lora_ready = lora_status in {"READY", "COMFY_UNREACHABLE"}
    return {
        "lora_id": str(match.get("id") or MULTIPLE_ANGLES_LORA_ID),
        "lora_sha256": MULTIPLE_ANGLES_LORA_SHA256,
        "lora_status": lora_status,
        "lora_ready": lora_ready,
        "comfy_reachable": bool(listed.get("comfy_reachable")),
        "display_name": str(match.get("display_name") or "Qwen 多视角镜头"),
        "setup_hint": "" if lora_ready else CHARACTER_SHEET_SETUP_HINT,
    }


def _find_multiple_angles_asset(db: Session) -> StyleLoraAsset | None:
    preferred = db.get(StyleLoraAsset, MULTIPLE_ANGLES_LORA_ID)
    if (
        preferred is not None
        and preferred.base_model == IMAGE_LORA_BASE
        and preferred.sha256 == MULTIPLE_ANGLES_LORA_SHA256
    ):
        return preferred
    return db.scalar(
        select(StyleLoraAsset)
        .where(
            StyleLoraAsset.base_model == IMAGE_LORA_BASE,
            StyleLoraAsset.sha256 == MULTIPLE_ANGLES_LORA_SHA256,
        )
        .order_by(StyleLoraAsset.created_at.asc())
        .limit(1)
    )


def freeze_character_sheet_lora(db: Session, settings: Any) -> dict[str, Any]:
    """Validate and freeze the multiple-angles LoRA selection through the single adjudicator."""
    sync_discovered_loras(db, settings, base_model=IMAGE_LORA_BASE)
    asset = _find_multiple_angles_asset(db)
    if asset is None:
        raise StyleLoraError(
            "CHARACTER_SHEET_LORA_REQUIRED",
            f"缺少「Qwen 多视角镜头」LoRA 权重。{CHARACTER_SHEET_SETUP_HINT}",
            http_status=409,
            details={
                "lora_id": MULTIPLE_ANGLES_LORA_ID,
                "sha256": MULTIPLE_ANGLES_LORA_SHA256,
                "setup_hint": CHARACTER_SHEET_SETUP_HINT,
            },
        )
    (spec,) = evaluate_style_lora_selection(
        db,
        settings,
        [
            {
                "lora_id": asset.id,
                "sha256": MULTIPLE_ANGLES_LORA_SHA256,
                "strength_model": MULTIPLE_ANGLES_DEFAULT_STRENGTH,
            }
        ],
        base_model=IMAGE_LORA_BASE,
    )
    return {
        "style_loras": [spec.canonical_dict()],
        "style_sampling": dict(CHARACTER_SHEET_LORA_SAMPLING),
    }


def _decode_rgb_panel(raw: bytes) -> Image.Image:
    with Image.open(io.BytesIO(raw)) as img:
        img.load()
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            rgba = img.convert("RGBA")
            bg = Image.new("RGBA", rgba.size, (*CHARACTER_SHEET_BG_RGB, 255))
            return Image.alpha_composite(bg, rgba).convert("RGB")
        return img.convert("RGB")


def extract_character_sheet_note_from_material(
    requirement_payload: Mapping[str, Any] | None,
    brief_payload: Mapping[str, Any] | None = None,
    brief_note: str = "",
) -> str:
    """Build a concise lower-body/wardrobe consistency note from a material requirement."""
    parts: list[str] = []
    seen: set[str] = set()
    for raw in (
        brief_note,
        (brief_payload or {}).get("appearance"),
        (requirement_payload or {}).get("description"),
    ):
        cleaned = " ".join(str(raw or "").split()).strip()
        if cleaned and cleaned.lower() not in seen:
            seen.add(cleaned.lower())
            parts.append(cleaned)
    joined = "; ".join(parts)
    if len(joined) > 480:
        joined = joined[:477].rstrip(" ;,.") + "..."
    return joined


def _scale_to_height(image: Image.Image, target_height: int) -> Image.Image:
    width, height = image.size
    if height == target_height:
        return image
    scaled_width = max(1, round(width * target_height / height))
    return image.resize((scaled_width, target_height), Image.Resampling.LANCZOS)


def _scale_to_fit(image: Image.Image, max_width: int, max_height: int) -> Image.Image:
    width, height = image.size
    if width <= 0 or height <= 0:
        return image
    scale = min(max_width / width, max_height / height)
    if abs(scale - 1.0) < 1e-6:
        return image
    scaled_w = max(1, min(max_width, round(width * scale)))
    scaled_h = max(1, min(max_height, round(height * scale)))
    return image.resize((scaled_w, scaled_h), Image.Resampling.LANCZOS)


def _encode_png(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG", compress_level=6)
    return buf.getvalue()


def compose_character_sheet(
    *,
    closeup_front_bytes: bytes,
    closeup_3_4_bytes: bytes,
    front_bytes: bytes,
    side_bytes: bytes,
    back_bytes: bytes,
    composite_width: int = COMPOSITE_WIDTH,
    composite_height: int = COMPOSITE_HEIGHT,
    outer_margin: int = CHARACTER_SHEET_OUTER_MARGIN,
    closeup_gap: int = CHARACTER_SHEET_CLOSEUP_GAP,
    view_gap: int = CHARACTER_SHEET_VIEW_GAP,
    section_gap: int = CHARACTER_SHEET_SECTION_GAP,
    triview_panel_height: int = TRIVIEW_PANEL_HEIGHT,
    triview_outer_margin: int = TRIVIEW_OUTER_MARGIN,
    triview_view_gap: int = TRIVIEW_VIEW_GAP,
) -> dict[str, Any]:
    """Deterministically compose standalone full-body tri-view and exact 16:9 5-view master sheet.

    Left column stacks ``closeup_front`` above ``closeup_3_4`` at native resolution;
    right block places ``fullbody_front`` | ``fullbody_side`` | ``fullbody_back`` with equal height
    proportional scaling inside an exact 16:9 canvas (3584x2016 default), zero downscaling of
    generated panels, zero cropping, and zero text/watermark overlays.
    """
    raw_front_cu = _decode_rgb_panel(closeup_front_bytes)
    raw_34_cu = _decode_rgb_panel(closeup_3_4_bytes)
    raw_front = _decode_rgb_panel(front_bytes)
    raw_side = _decode_rgb_panel(side_bytes)
    raw_back = _decode_rgb_panel(back_bytes)

    # 1. Standalone full-body tri-view at native full-body height (no downscaling).
    tri_front = _scale_to_height(raw_front, triview_panel_height)
    tri_side = _scale_to_height(raw_side, triview_panel_height)
    tri_back = _scale_to_height(raw_back, triview_panel_height)
    triview_w = (
        triview_outer_margin * 2
        + tri_front.width
        + triview_view_gap
        + tri_side.width
        + triview_view_gap
        + tri_back.width
    )
    triview_h = triview_outer_margin * 2 + triview_panel_height
    triview_canvas = Image.new("RGB", (triview_w, triview_h), CHARACTER_SHEET_BG_RGB)
    triview_x_front = triview_outer_margin
    triview_x_side = triview_x_front + tri_front.width + triview_view_gap
    triview_x_back = triview_x_side + tri_side.width + triview_view_gap
    triview_canvas.paste(tri_front, (triview_x_front, triview_outer_margin))
    triview_canvas.paste(tri_side, (triview_x_side, triview_outer_margin))
    triview_canvas.paste(tri_back, (triview_x_back, triview_outer_margin))

    # 2. Exact 16:9 master sheet (default 3584 x 2016).
    inner_h = composite_height - 2 * outer_margin
    closeup_slot_h = (inner_h - closeup_gap) // 2
    left_col_w = min(CLOSEUP_TARGET_WIDTH, max(raw_front_cu.width, raw_34_cu.width))
    # Never downscale standard 736x928 closeups; only adapt if larger custom fixture passed.
    if raw_front_cu.width == CLOSEUP_TARGET_WIDTH and raw_front_cu.height == closeup_slot_h:
        cu_front_img = raw_front_cu
    else:
        cu_front_img = _scale_to_fit(raw_front_cu, CLOSEUP_TARGET_WIDTH, closeup_slot_h)
    if raw_34_cu.width == CLOSEUP_TARGET_WIDTH and raw_34_cu.height == closeup_slot_h:
        cu_34_img = raw_34_cu
    else:
        cu_34_img = _scale_to_fit(raw_34_cu, CLOSEUP_TARGET_WIDTH, closeup_slot_h)
    left_col_w = max(cu_front_img.width, cu_34_img.width)

    right_start_x = outer_margin + left_col_w + section_gap
    right_total_w = composite_width - outer_margin - right_start_x
    fb_slot_w = (right_total_w - 2 * view_gap) // 3

    fb_front_img = _scale_to_fit(raw_front, fb_slot_w, inner_h)
    fb_side_img = _scale_to_fit(raw_side, fb_slot_w, inner_h)
    fb_back_img = _scale_to_fit(raw_back, fb_slot_w, inner_h)

    composite_canvas = Image.new("RGB", (composite_width, composite_height), CHARACTER_SHEET_BG_RGB)

    cu_front_x = outer_margin + (left_col_w - cu_front_img.width) // 2
    cu_front_y = outer_margin + (closeup_slot_h - cu_front_img.height) // 2
    cu_34_x = outer_margin + (left_col_w - cu_34_img.width) // 2
    cu_34_y = outer_margin + closeup_slot_h + closeup_gap + (closeup_slot_h - cu_34_img.height) // 2

    composite_canvas.paste(cu_front_img, (cu_front_x, cu_front_y))
    composite_canvas.paste(cu_34_img, (cu_34_x, cu_34_y))

    fb_boxes: dict[str, dict[str, int]] = {}
    for idx, (fb_key, fb_im) in enumerate((("front", fb_front_img), ("side", fb_side_img), ("back", fb_back_img))):
        slot_x = right_start_x + idx * (fb_slot_w + view_gap)
        px = slot_x + (fb_slot_w - fb_im.width) // 2
        py = outer_margin + (inner_h - fb_im.height) // 2
        composite_canvas.paste(fb_im, (px, py))
        fb_boxes[fb_key] = {"x": px, "y": py, "width": fb_im.width, "height": fb_im.height}

    return {
        "fullbody_triview_bytes": _encode_png(triview_canvas),
        "composite_sheet_bytes": _encode_png(composite_canvas),
        "fullbody_triview_size": {"width": triview_w, "height": triview_h},
        "composite_sheet_size": {"width": composite_width, "height": composite_height},
        "layout": {
            "version": CHARACTER_SHEET_LAYOUT_VERSION,
            "aspect_ratio": "16:9",
            "background_hex": CHARACTER_SHEET_BG_HEX,
            "background_rgb": list(CHARACTER_SHEET_BG_RGB),
            "composite_width": composite_width,
            "composite_height": composite_height,
            "outer_margin": outer_margin,
            "closeup_gap": closeup_gap,
            "section_gap": section_gap,
            "view_gap": view_gap,
            "triview_boxes": {
                "front": {
                    "x": triview_x_front, "y": triview_outer_margin,
                    "width": tri_front.width, "height": triview_panel_height,
                },
                "side": {
                    "x": triview_x_side, "y": triview_outer_margin,
                    "width": tri_side.width, "height": triview_panel_height,
                },
                "back": {
                    "x": triview_x_back, "y": triview_outer_margin,
                    "width": tri_back.width, "height": triview_panel_height,
                },
            },
            "composite_boxes": {
                "closeup_front": {
                    "x": cu_front_x, "y": cu_front_y,
                    "width": cu_front_img.width, "height": cu_front_img.height,
                },
                "closeup_3_4": {"x": cu_34_x, "y": cu_34_y, "width": cu_34_img.width, "height": cu_34_img.height},
                **fb_boxes,
            },
        },
    }
