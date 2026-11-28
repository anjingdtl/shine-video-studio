"""Scene 4-view sheet generation pipeline, readiness projection, and deterministic compositor.

Three Principles alignment (一套事实、一个裁决、一个执行者):
- One Fact: reads ``qwen-image-2.1-lora-multiple-angles`` strictly from the
  unified ``style_lora_assets`` ledger by SHA-256 and persists the 4 full-size
  single-angle PNG paths + SHA-256 digests atomically inside the single scene
  master asset's ``provenance["scene_sheet"]["views"]`` (never splits 1 scene
  into 4 orphan Asset rows or alters ``shot.bindings``).
- One Adjudicator: validates and freezes the multiple-angles LoRA exclusively
  through ``evaluate_style_lora_selection()``.
- One Executor: supplies deterministic prompts, dimensions, and Pillow 16:9
  quad sheet composition for the single material-preparation image worker.
"""

from __future__ import annotations

import io
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from PIL import Image
from sqlalchemy.orm import Session

from shine.asset_center.character_sheet import (
    CHARACTER_SHEET_BASE_SAMPLING,
    CHARACTER_SHEET_LORA_SAMPLING,
    MULTIPLE_ANGLES_DEFAULT_STRENGTH,
    MULTIPLE_ANGLES_LORA_ID,
    MULTIPLE_ANGLES_LORA_SHA256,
    _find_multiple_angles_asset,
    project_character_sheet_lora,
    project_character_sheet_lora_from_listed,
)
from shine.comfyops.loras import (
    IMAGE_LORA_BASE,
    StyleLoraError,
    evaluate_style_lora_selection,
    sync_discovered_loras,
)
from shine.materialprep.prompt_source import apply_image_lora_prompts

SCENE_SHEET_OPERATION = "SCENE_SHEET_V1"
SCENE_SHEET_SOURCE_MODE = "SCENE_SHEET"
SCENE_SHEET_PIPELINE = "t2i_to_scene_sheet_v1"
SCENE_SHEET_LAYOUT_VERSION = "scene-sheet-16x9-quad-v1"

SCENE_ANGLE_KEYS: tuple[str, ...] = ("front", "left", "right", "reverse")

SCENE_SHEET_SETUP_HINT = (
    "请前往「设置 → 制作环境 → 角色五视图定妆照」勾选下载并完成纳管"
    "（场景四视图复用同一 Qwen 多视角镜头 LoRA）；"
    "场景四视图不会静默降级为无 LoRA 生图。"
)

SCENE_SHEET_LORA_SAMPLING: dict[str, Any] = dict(CHARACTER_SHEET_LORA_SAMPLING)
SCENE_SHEET_BASE_SAMPLING: dict[str, Any] = dict(CHARACTER_SHEET_BASE_SAMPLING)

# Exact 16:9 zero-downscaling 2x2 quad master sheet geometry:
#   Single view: 1344 x 768 (16:9, both sides multiple of 32)
#   Canvas: 2752 x 1600 (both sides multiple of 32)
#   Horizontal: 24 + 1344 + 16 + 1344 + 24 = 2752
#   Vertical:   24 + 768  + 16 + 768  + 24 = 1600
SCENE_VIEW_WIDTH = 1344
SCENE_VIEW_HEIGHT = 768
SCENE_COMPOSITE_WIDTH = 2752
SCENE_COMPOSITE_HEIGHT = 1600
SCENE_SHEET_OUTER_MARGIN = 24
SCENE_SHEET_CELL_GAP = 16
SCENE_SHEET_BG_RGB = (24, 26, 30)
SCENE_SHEET_BG_HEX = "#181A1E"

SCENE_SHEET_NEGATIVE_PROMPT = (
    "people, person, human, man, woman, character, human figure, silhouette, crowd, face, hands, "
    "text, watermark, logo, signature, label, collage, grid, split screen, split panel, frame border, "
    "platform badge, timestamp, user-interface overlay"
)


@dataclass(frozen=True)
class SceneSheetStepSpec:
    key: str
    angle_key: str
    label: str
    uses_lora: bool
    reference_source: str
    target_width: int = SCENE_VIEW_WIDTH
    target_height: int = SCENE_VIEW_HEIGHT


FRONT_STEP_SPEC = SceneSheetStepSpec(
    key="front_wide",
    angle_key="front",
    label="主全景（正打基准）",
    uses_lora=False,
    reference_source="t2i",
    target_width=SCENE_VIEW_WIDTH,
    target_height=SCENE_VIEW_HEIGHT,
)

EDIT_STEP_SPECS: tuple[SceneSheetStepSpec, ...] = (
    SceneSheetStepSpec(
        key="left_quarter",
        angle_key="left",
        label="左 45° 广角",
        uses_lora=True,
        reference_source="front_wide",
        target_width=SCENE_VIEW_WIDTH,
        target_height=SCENE_VIEW_HEIGHT,
    ),
    SceneSheetStepSpec(
        key="right_quarter",
        angle_key="right",
        label="右 45° 广角",
        uses_lora=True,
        reference_source="front_wide",
        target_width=SCENE_VIEW_WIDTH,
        target_height=SCENE_VIEW_HEIGHT,
    ),
    SceneSheetStepSpec(
        key="reverse_wide",
        angle_key="reverse",
        label="180° 反打 / 高位全景",
        uses_lora=True,
        reference_source="front_wide",
        target_width=SCENE_VIEW_WIDTH,
        target_height=SCENE_VIEW_HEIGHT,
    ),
)

ALL_STEP_SPECS: tuple[SceneSheetStepSpec, ...] = (FRONT_STEP_SPEC, *EDIT_STEP_SPECS)
STEP_SPEC_BY_KEY: dict[str, SceneSheetStepSpec] = {spec.key: spec for spec in ALL_STEP_SPECS}
STEP_KEY_BY_ANGLE: dict[str, str] = {spec.angle_key: spec.key for spec in ALL_STEP_SPECS}


def extract_scene_sheet_note_from_material(
    requirement_payload: Mapping[str, Any] | None,
    brief_payload: Mapping[str, Any] | None = None,
    brief_note: str = "",
) -> str:
    """Build a concise English architectural/terrain consistency note from a scene requirement."""
    parts: list[str] = []
    seen: set[str] = set()
    for raw in (
        (brief_payload or {}).get("appearance"),
        (brief_payload or {}).get("era"),
        brief_note,
        (requirement_payload or {}).get("description"),
    ):
        cleaned = " ".join(str(raw or "").split()).strip()
        if not cleaned:
            continue
        # Keep edit prompts strictly English for Qwen-Image <mva> consistency.
        if any("\u3400" <= ch <= "\u9fff" for ch in cleaned):
            continue
        lowered = cleaned.lower()
        if lowered not in seen:
            seen.add(lowered)
            parts.append(cleaned)
    joined = "; ".join(parts)
    if len(joined) > 480:
        joined = joined[:477].rstrip(" ;,.") + "..."
    return joined


def build_scene_step_prompts(
    step_key: str,
    *,
    note: str = "",
    style_loras: Sequence[Mapping[str, Any]] = (),
) -> tuple[str, str]:
    """Build deterministic positive/negative prompts for one <mva> scene-angle edit step."""
    cleaned_note = " ".join(str(note or "").split()).strip()
    note_clause = f", consistent environment details: {cleaned_note}" if cleaned_note else ""
    common_tail = (
        "strictly preserving the exact same architectural style, terrain, landmarks, era materials, "
        "sky, weather, lighting direction, and color temperature from the reference image, "
        "single continuous widescreen photograph, completely empty scene with no people, no human figure, "
        "no text, no collage, no split panel"
    )

    if step_key == "left_quarter":
        base_prompt = (
            "front-left quarter view, wide-angle establishing environment shot, camera rotated 45 degrees "
            "to the left looking across the left flank and spatial depth of the same location, "
            f"{common_tail}{note_clause}"
        )
    elif step_key == "right_quarter":
        base_prompt = (
            "front-right quarter view, wide-angle establishing environment shot, camera rotated 45 degrees "
            "to the right looking across the right flank and spatial depth of the same location, "
            f"{common_tail}{note_clause}"
        )
    elif step_key == "reverse_wide":
        base_prompt = (
            "back view, 180-degree reverse wide-angle establishing environment shot, slightly elevated "
            "camera looking back toward the opposite side and reverse horizon of the exact same location, "
            f"{common_tail}{note_clause}"
        )
    else:
        raise ValueError(f"Unsupported scene sheet edit step: {step_key}")

    positive, negative = apply_image_lora_prompts(
        base_prompt, SCENE_SHEET_NEGATIVE_PROMPT, style_loras, style_preset=None
    )
    if not positive.startswith("<mva>"):
        positive = f"<mva> {positive}"
    return positive, negative


def project_scene_sheet_lora_from_listed(listed: Mapping[str, Any]) -> dict[str, Any]:
    """Derive scene-sheet LoRA readiness from the shared Qwen-Image LoRA listing."""
    base = project_character_sheet_lora_from_listed(listed)
    return {
        **base,
        "setup_hint": "" if base.get("lora_ready") else SCENE_SHEET_SETUP_HINT,
    }


def project_scene_sheet_lora(
    db: Session,
    settings: Any,
    *,
    object_info: dict[str, Any] | None = None,
    probe_comfy: bool = False,
) -> dict[str, Any]:
    """Read readiness of ``qwen-image-2.1-lora-multiple-angles`` for scene sheets."""
    base = project_character_sheet_lora(
        db, settings, object_info=object_info, probe_comfy=probe_comfy
    )
    return {
        **base,
        "setup_hint": "" if base.get("lora_ready") else SCENE_SHEET_SETUP_HINT,
    }


def freeze_scene_sheet_lora(db: Session, settings: Any) -> dict[str, Any]:
    """Validate and freeze the multiple-angles LoRA selection through the single adjudicator."""
    sync_discovered_loras(db, settings, base_model=IMAGE_LORA_BASE)
    asset = _find_multiple_angles_asset(db)
    if asset is None:
        raise StyleLoraError(
            "SCENE_SHEET_LORA_REQUIRED",
            f"缺少「Qwen 多视角镜头」LoRA 权重。{SCENE_SHEET_SETUP_HINT}",
            http_status=409,
            details={
                "lora_id": MULTIPLE_ANGLES_LORA_ID,
                "sha256": MULTIPLE_ANGLES_LORA_SHA256,
                "setup_hint": SCENE_SHEET_SETUP_HINT,
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
        "style_sampling": dict(SCENE_SHEET_LORA_SAMPLING),
    }


def _decode_rgb(raw: bytes) -> Image.Image:
    with Image.open(io.BytesIO(raw)) as img:
        img.load()
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            rgba = img.convert("RGBA")
            bg = Image.new("RGBA", rgba.size, (*SCENE_SHEET_BG_RGB, 255))
            return Image.alpha_composite(bg, rgba).convert("RGB")
        return img.convert("RGB")


def _fit_panel(image: Image.Image, target_w: int, target_h: int) -> Image.Image:
    if image.size == (target_w, target_h):
        return image
    scale = min(target_w / max(1, image.width), target_h / max(1, image.height))
    scaled_w = max(1, min(target_w, round(image.width * scale)))
    scaled_h = max(1, min(target_h, round(image.height * scale)))
    return image.resize((scaled_w, scaled_h), Image.Resampling.LANCZOS)


def _encode_png(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG", compress_level=6)
    return buf.getvalue()


def compose_scene_sheet(
    *,
    front_bytes: bytes,
    left_bytes: bytes,
    right_bytes: bytes,
    reverse_bytes: bytes,
    composite_width: int = SCENE_COMPOSITE_WIDTH,
    composite_height: int = SCENE_COMPOSITE_HEIGHT,
    outer_margin: int = SCENE_SHEET_OUTER_MARGIN,
    cell_gap: int = SCENE_SHEET_CELL_GAP,
) -> dict[str, Any]:
    """Deterministically compose the 4 full-size 16:9 scene views into a 2x2 master preview sheet.

    Layout (zero text/watermark overlays, zero downscaling for 1344x768 panels):
      Top-left:     front   (front_wide)
      Top-right:    left    (left_quarter)
      Bottom-left:  right   (right_quarter)
      Bottom-right: reverse (reverse_wide)
    """
    panels = {
        "front": _decode_rgb(front_bytes),
        "left": _decode_rgb(left_bytes),
        "right": _decode_rgb(right_bytes),
        "reverse": _decode_rgb(reverse_bytes),
    }

    cell_w = (composite_width - 2 * outer_margin - cell_gap) // 2
    cell_h = (composite_height - 2 * outer_margin - cell_gap) // 2

    canvas = Image.new("RGB", (composite_width, composite_height), SCENE_SHEET_BG_RGB)
    grid_positions = (
        ("front", 0, 0),
        ("left", 1, 0),
        ("right", 0, 1),
        ("reverse", 1, 1),
    )
    boxes: dict[str, dict[str, int]] = {}
    for angle_key, col, row in grid_positions:
        fitted = _fit_panel(panels[angle_key], cell_w, cell_h)
        slot_x = outer_margin + col * (cell_w + cell_gap)
        slot_y = outer_margin + row * (cell_h + cell_gap)
        px = slot_x + (cell_w - fitted.width) // 2
        py = slot_y + (cell_h - fitted.height) // 2
        canvas.paste(fitted, (px, py))
        boxes[angle_key] = {"x": px, "y": py, "width": fitted.width, "height": fitted.height}

    return {
        "composite_sheet_bytes": _encode_png(canvas),
        "composite_sheet_size": {"width": composite_width, "height": composite_height},
        "layout": {
            "version": SCENE_SHEET_LAYOUT_VERSION,
            "aspect_ratio": "16:9",
            "background_hex": SCENE_SHEET_BG_HEX,
            "background_rgb": list(SCENE_SHEET_BG_RGB),
            "composite_width": composite_width,
            "composite_height": composite_height,
            "outer_margin": outer_margin,
            "cell_gap": cell_gap,
            "composite_boxes": boxes,
        },
    }


def scene_sheet_views_from_provenance(provenance: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Extract validated single-angle scene view descriptors from an Asset's provenance."""
    if not isinstance(provenance, Mapping):
        return {}
    sheet = provenance.get("scene_sheet")
    if not isinstance(sheet, Mapping):
        return {}
    raw_views = sheet.get("views")
    if not isinstance(raw_views, Mapping):
        return {}
    views: dict[str, dict[str, Any]] = {}
    for angle_key in SCENE_ANGLE_KEYS:
        entry = raw_views.get(angle_key)
        if not isinstance(entry, Mapping):
            continue
        storage_path = str(entry.get("storage_path") or "").strip()
        sha256 = str(entry.get("sha256") or "").strip()
        if not storage_path or not sha256:
            continue
        views[angle_key] = {
            "key": str(entry.get("key") or STEP_KEY_BY_ANGLE[angle_key]),
            "storage_path": storage_path,
            "sha256": sha256,
            "width": int(entry.get("width") or SCENE_VIEW_WIDTH),
            "height": int(entry.get("height") or SCENE_VIEW_HEIGHT),
        }
    return views


def find_scene_sheet_view_by_sha(
    provenance: Mapping[str, Any] | None,
    sha256: str,
) -> tuple[str, dict[str, Any]] | None:
    """Locate one scene-sheet single-angle view entry by its pinned content SHA-256."""
    if not sha256:
        return None
    for angle_key, view in scene_sheet_views_from_provenance(provenance).items():
        if view["sha256"] == sha256:
            return angle_key, view
    return None
