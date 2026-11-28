"""Phase 1 tests: 4-view scene sheet compositor, LoRA prompt builder,
single/dual angle routing (resolve_scene_angle_views + MAX_IMAGES protection),
provider cryptographic verification for angle views, and H3PromptCompiler 2.7.0
with byte-identical 2.6.0 replay."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from shine.asset_center.scene_sheet import (
    EDIT_STEP_SPECS,
    SCENE_COMPOSITE_HEIGHT,
    SCENE_COMPOSITE_WIDTH,
    SCENE_SHEET_LAYOUT_VERSION,
    SCENE_VIEW_HEIGHT,
    SCENE_VIEW_WIDTH,
    build_scene_step_prompts,
    compose_scene_sheet,
    extract_scene_sheet_note_from_material,
    find_scene_sheet_view_by_sha,
    scene_sheet_views_from_provenance,
)
from shine.comfy.provider import ComfyReferenceError, ComfyUIRenderService
from shine.h3.compiler import (
    COMPILER_VERSION,
    LEGACY_CONTINUITY_CLEAN_V26_COMPILER_VERSION,
    H3PromptCompiler,
)
from shine.schemas import (
    CharacterPlacement,
    ContinuitySettings,
    Facing,
    FrameZone,
    GenerationMode,
    ReferenceManifestItem,
    ReferenceUse,
    ShotBindingInput,
    ShotBlocking,
    ShotSpec,
    StyleBible,
    TransitionMotivation,
)
from shine.services import build_reference_manifest, resolve_scene_angle_views

pytestmark = pytest.mark.unit

EN_STYLE = StyleBible(
    photography_language="Epic widescreen battlefield cinematography",
    shot_blocking="Clear spatial separation across the plains outside Xiangji Temple",
    color_palette="Muted bronze dust, crimson banners, weathered stone slate",
    lighting_tone="Overcast autumn daylight cutting through battle smoke",
    lens_texture="Anamorphic cinema lens with subtle organic film grain",
    editing_rhythm="Build tension from wide formations into kinetic clashes",
    sound_performance="Natural Mandarin battlefield commands over wind and distant war drums",
)


def _make_png(width: int, height: int, color: tuple[int, int, int]) -> bytes:
    img = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_scene_asset(
    tmp_path: Path | None = None,
    *,
    with_scene_sheet: bool = True,
    missing_angles: set[str] | None = None,
) -> SimpleNamespace:
    missing = missing_angles or set()
    master_bytes = _make_png(64, 36, (40, 40, 40))
    master_sha = hashlib.sha256(master_bytes).hexdigest()
    master_rel = "assets/scene_master.png"
    if tmp_path is not None:
        (tmp_path / "assets").mkdir(parents=True, exist_ok=True)
        (tmp_path / master_rel).write_bytes(master_bytes)

    provenance: dict = {}
    if with_scene_sheet:
        views = {}
        colors = {
            "front": (200, 40, 40),
            "left": (40, 200, 40),
            "right": (40, 40, 200),
            "reverse": (200, 200, 40),
        }
        keys = {
            "front": "front_wide",
            "left": "left_quarter",
            "right": "right_quarter",
            "reverse": "reverse_wide",
        }
        for angle, rgb in colors.items():
            raw = _make_png(128, 72, rgb)
            sha = hashlib.sha256(raw).hexdigest()
            rel = f"assets/scene_{keys[angle]}.png"
            if tmp_path is not None and angle not in missing:
                (tmp_path / rel).write_bytes(raw)
            views[angle] = {
                "key": keys[angle],
                "storage_path": rel,
                "sha256": sha,
                "width": SCENE_VIEW_WIDTH,
                "height": SCENE_VIEW_HEIGHT,
            }
        provenance = {
            "pipeline": "t2i_to_scene_sheet_v1",
            "scene_sheet": {
                "layout_version": SCENE_SHEET_LAYOUT_VERSION,
                "artifact_role": "composite_sheet",
                "views": views,
            },
        }
    return SimpleNamespace(
        id="asset-scene-1",
        project_id="proj-1",
        filename="scene_xiangji.png",
        kind="image",
        content_type="image/png",
        sha256=master_sha,
        storage_path=master_rel,
        role="scene",
        entity_id="香积寺外旷野",
        duration_s=None,
        approved=True,
        locked=True,
        provenance=provenance,
    )


def _make_asset(asset_id: str, role: str, entity_id: str) -> SimpleNamespace:
    raw = _make_png(32, 32, (100, 120, 140))
    sha = hashlib.sha256(raw).hexdigest()
    return SimpleNamespace(
        id=asset_id,
        project_id="proj-1",
        filename=f"{asset_id}.png",
        kind="image",
        content_type="image/png",
        sha256=sha,
        storage_path=f"assets/{asset_id}.png",
        role=role,
        entity_id=entity_id,
        duration_s=None,
        approved=True,
        locked=True,
        provenance={},
    )


def _make_shot_spec(
    *,
    camera: dict | None = None,
    transition_motivation: TransitionMotivation | None = None,
    blocking: ShotBlocking | None = None,
) -> ShotSpec:
    return ShotSpec(
        story_beat="Tang vanguard stands in battle formation on the dusty plain before Xiangji Temple.",
        characters=["李嗣业"],
        location_id="香积寺外旷野",
        duration_target_s=6.0,
        style_version_id="style-v1",
        camera=camera or {"composition": "Wide establishing frame of the plain", "movement": "The camera holds still."},
        continuity=ContinuitySettings(),
        transition_motivation=transition_motivation,
        blocking=blocking,
    )


def test_compose_scene_sheet_exact_quad_16x9_zero_downscale():
    front = _make_png(SCENE_VIEW_WIDTH, SCENE_VIEW_HEIGHT, (220, 30, 30))
    left = _make_png(SCENE_VIEW_WIDTH, SCENE_VIEW_HEIGHT, (30, 220, 30))
    right = _make_png(SCENE_VIEW_WIDTH, SCENE_VIEW_HEIGHT, (30, 30, 220))
    reverse = _make_png(SCENE_VIEW_WIDTH, SCENE_VIEW_HEIGHT, (220, 220, 30))

    composed = compose_scene_sheet(
        front_bytes=front,
        left_bytes=left,
        right_bytes=right,
        reverse_bytes=reverse,
    )
    assert composed["composite_sheet_size"] == {
        "width": SCENE_COMPOSITE_WIDTH,
        "height": SCENE_COMPOSITE_HEIGHT,
    }
    assert composed["layout"]["version"] == SCENE_SHEET_LAYOUT_VERSION
    boxes = composed["layout"]["composite_boxes"]
    for angle in ("front", "left", "right", "reverse"):
        assert boxes[angle]["width"] == SCENE_VIEW_WIDTH
        assert boxes[angle]["height"] == SCENE_VIEW_HEIGHT

    with Image.open(io.BytesIO(composed["composite_sheet_bytes"])) as img:
        assert img.size == (SCENE_COMPOSITE_WIDTH, SCENE_COMPOSITE_HEIGHT)
        assert img.getpixel((boxes["front"]["x"] + 10, boxes["front"]["y"] + 10)) == (220, 30, 30)
        assert img.getpixel((boxes["left"]["x"] + 10, boxes["left"]["y"] + 10)) == (30, 220, 30)
        assert img.getpixel((boxes["right"]["x"] + 10, boxes["right"]["y"] + 10)) == (30, 30, 220)
        assert img.getpixel((boxes["reverse"]["x"] + 10, boxes["reverse"]["y"] + 10)) == (220, 220, 30)


def test_build_scene_step_prompts_enforces_mva_and_no_people():
    for spec in EDIT_STEP_SPECS:
        pos, neg = build_scene_step_prompts(
            spec.key,
            note="Tang dynasty pagoda and wide autumn plain",
            style_loras=[{"trigger_words": ["<mva>"]}],
        )
        assert pos.startswith("<mva>")
        assert "no people" in pos.lower()
        assert "no collage" in pos.lower()
        assert "no split panel" in pos.lower()
        assert "people" in neg.lower()
        assert "split screen" in neg.lower()

    note = extract_scene_sheet_note_from_material(
        {"description": "香积寺外旷野"},
        {"appearance": "Ancient Tang pagoda overlooking dry autumn battlefield plain", "era": "Tang Dynasty"},
    )
    assert "Ancient Tang pagoda" in note
    assert "香积寺" not in note


def test_legacy_single_image_scene_passes_through_unchanged():
    scene = _make_scene_asset(with_scene_sheet=False)
    char = _make_asset("asset-char-1", "character", "李嗣业")
    bindings = [
        ShotBindingInput(asset_id=char.id, use_as=ReferenceUse.SUBJECT, ordinal=0),
        ShotBindingInput(asset_id=scene.id, use_as=ReferenceUse.PICTURE, ordinal=1),
    ]
    spec = _make_shot_spec(camera={
        "move": "pan",
        "scene_angle": "front",
        "scene_angle_end": "left",
        "movement": "Slow pan from front to left.",
    })
    manifest = build_reference_manifest(bindings, {char.id: char, scene.id: scene}, shot_spec=spec)
    assert len(manifest) == 2
    assert manifest[1].label == "<Picture 2>"
    assert manifest[1].sha256 == scene.sha256
    assert manifest[1].storage_key == scene.storage_path
    assert manifest[1].role is None


def test_scene_sheet_single_angle_routing_and_reverse_shot_default(tmp_path: Path):
    scene = _make_scene_asset(tmp_path, with_scene_sheet=True)
    char = _make_asset("asset-char-1", "character", "李嗣业")
    views = scene_sheet_views_from_provenance(scene.provenance)
    bindings = [
        ShotBindingInput(asset_id=char.id, use_as=ReferenceUse.SUBJECT, ordinal=0),
        ShotBindingInput(asset_id=scene.id, use_as=ReferenceUse.PICTURE, ordinal=1),
    ]

    # Explicit single angle (push_in with scene_angle="left" -> 1 single-angle view)
    spec_left = _make_shot_spec(camera={
        "move": "push_in",
        "scene_angle": "left",
        "scene_angle_end": "right",  # push_in is not cross-angle -> must stay 1 view
        "movement": "Slow push in on the left flank.",
    })
    manifest = build_reference_manifest(
        bindings, {char.id: char, scene.id: scene}, shot_spec=spec_left, asset_root=tmp_path
    )
    assert len(manifest) == 2
    assert manifest[1].role == "scene_angle:left"
    assert manifest[1].sha256 == views["left"]["sha256"]
    assert manifest[1].storage_key == views["left"]["storage_path"]

    # Automatic reverse inference on shot_reverse_shot with character facing left
    spec_srs = _make_shot_spec(
        camera={"move": "static", "movement": "The camera holds still."},
        transition_motivation=TransitionMotivation.SHOT_REVERSE_SHOT,
        blocking=ShotBlocking(
            axis="battle_line_1",
            placements=[CharacterPlacement(character="李嗣业", zone=FrameZone.RIGHT_MG, facing=Facing.FACING_LEFT)],
        ),
    )
    manifest_srs = build_reference_manifest(
        bindings, {char.id: char, scene.id: scene}, shot_spec=spec_srs, asset_root=tmp_path
    )
    assert len(manifest_srs) == 2
    assert manifest_srs[1].role == "scene_angle:reverse"
    assert manifest_srs[1].sha256 == views["reverse"]["sha256"]


def test_scene_sheet_dual_angle_expansion_and_slot_budget_downgrade(tmp_path: Path):
    scene = _make_scene_asset(tmp_path, with_scene_sheet=True)
    views = scene_sheet_views_from_provenance(scene.provenance)
    char = _make_asset("asset-char-1", "character", "李嗣业")

    spec_pan = _make_shot_spec(camera={
        "move": "pan",
        "scene_angle": "front",
        "scene_angle_end": "right",
        "movement": "Slow wide pan sweeping from the main gate to the eastern ridge.",
    })
    bindings = [
        ShotBindingInput(asset_id=char.id, use_as=ReferenceUse.SUBJECT, ordinal=0),
        ShotBindingInput(asset_id=scene.id, use_as=ReferenceUse.PICTURE, ordinal=1),
    ]
    manifest = build_reference_manifest(
        bindings, {char.id: char, scene.id: scene}, shot_spec=spec_pan, asset_root=tmp_path
    )
    assert len(manifest) == 3
    assert manifest[0].label == "<Subject 1>"
    assert manifest[1].label == "<Picture 2>"
    assert manifest[1].role == "scene_angle_opening:front"
    assert manifest[1].sha256 == views["front"]["sha256"]
    assert manifest[2].label == "<Picture 3>"
    assert manifest[2].role == "scene_angle_ending:right"
    assert manifest[2].sha256 == views["right"]["sha256"]

    # When 9 images are already occupied (8 subjects/props + 1 scene = 9), must NOT exceed MAX_IMAGES (9);
    # safely downgrades to 1 opening view.
    crowded_assets = {scene.id: scene}
    crowded_bindings = []
    for idx in range(8):
        a = _make_asset(f"asset-fill-{idx}", "character" if idx < 3 else "prop", f"item-{idx}")
        crowded_assets[a.id] = a
        crowded_bindings.append(
            ShotBindingInput(
                asset_id=a.id,
                use_as=ReferenceUse.SUBJECT if idx < 3 else ReferenceUse.PICTURE,
                ordinal=idx,
            )
        )
    crowded_bindings.append(ShotBindingInput(asset_id=scene.id, use_as=ReferenceUse.PICTURE, ordinal=8))
    crowded_manifest = build_reference_manifest(
        crowded_bindings, crowded_assets, shot_spec=spec_pan, asset_root=tmp_path
    )
    assert len(crowded_manifest) == 9
    scene_items = [m for m in crowded_manifest if m.asset_id == scene.id]
    assert len(scene_items) == 1
    assert scene_items[0].role == "scene_angle:front"


def test_scene_sheet_missing_disk_file_falls_back_gracefully(tmp_path: Path):
    # Ending missing -> downgrades cleanly to opening view
    case1 = tmp_path / "case1"
    scene_partial = _make_scene_asset(case1, with_scene_sheet=True, missing_angles={"right"})
    spec_pan = _make_shot_spec(camera={
        "move": "orbit",
        "scene_angle": "front",
        "scene_angle_end": "right",
        "movement": "Dynamic 3/4 orbit around the vanguard.",
    })
    resolved_partial = resolve_scene_angle_views(
        scene_partial, spec_pan, extra_image_slots=2, extra_total_slots=2, asset_root=case1
    )
    assert len(resolved_partial) == 1
    assert resolved_partial[0]["role"] == "scene_angle:front"

    # Opening missing -> falls back to master asset without crashing
    case2 = tmp_path / "case2"
    scene_missing_open = _make_scene_asset(case2, with_scene_sheet=True, missing_angles={"front"})
    resolved_empty = resolve_scene_angle_views(
        scene_missing_open, spec_pan, extra_image_slots=2, extra_total_slots=2, asset_root=case2
    )
    assert resolved_empty == []


def test_comfy_provider_verifies_scene_angle_view_by_provenance_sha(tmp_path: Path):
    scene = _make_scene_asset(tmp_path, with_scene_sheet=True)
    views = scene_sheet_views_from_provenance(scene.provenance)
    assert find_scene_sheet_view_by_sha(scene.provenance, views["left"]["sha256"]) is not None

    provider = ComfyUIRenderService(client=None, asset_root=tmp_path)  # type: ignore[arg-type]
    item = ReferenceManifestItem(
        label="<Picture 1>",
        asset_id=scene.id,
        sha256=views["left"]["sha256"],
        storage_key=views["left"]["storage_path"],
        content_type="image/png",
        use_as=ReferenceUse.PICTURE,
        role="scene_angle_opening:left",
        asset_role="scene",
    )
    raw = provider._verify_asset_for_upload(item, scene, project_id="proj-1")
    assert hashlib.sha256(raw).hexdigest() == views["left"]["sha256"]

    # Tampering the angle file on disk must be caught cryptographically
    (tmp_path / views["left"]["storage_path"]).write_bytes(b"corrupted")
    with pytest.raises(ComfyReferenceError):
        provider._verify_asset_for_upload(item, scene, project_id="proj-1")


def test_preproduction_enforce_live_references_accepts_scene_angle_views(tmp_path: Path):
    from fastapi import HTTPException

    from shine.comfyops.preproduction import enforce_live_references

    scene = _make_scene_asset(tmp_path, with_scene_sheet=True)
    views = scene_sheet_views_from_provenance(scene.provenance)

    class _FakeDB:
        def get(self, _model, asset_id):
            return scene if asset_id == scene.id else None

    spec = _make_shot_spec(camera={
        "move": "pan",
        "scene_angle": "front",
        "scene_angle_end": "right",
        "movement": "Slow pan across the battlefield.",
    })
    request_payload = {
        "idempotency_key": "idem-scene-angle-1",
        "shot_id": "shot-1",
        "shot_revision": 1,
        "shot_spec": spec.model_dump(mode="json"),
        "style_version_id": "style-v1",
        "style_snapshot": EN_STYLE.model_dump(mode="json"),
        "style_hash": "a" * 64,
        "compiled_prompt": "subject_definitions:\n<Picture 1> opening",
        "expected_duration_s": 6.0,
        "reference_manifest": [
            {
                "label": "<Picture 1>",
                "asset_id": scene.id,
                "sha256": views["front"]["sha256"],
                "storage_key": views["front"]["storage_path"],
                "content_type": "image/png",
                "use_as": "picture",
                "role": "scene_angle_opening:front",
                "asset_role": "scene",
            },
            {
                "label": "<Picture 2>",
                "asset_id": scene.id,
                "sha256": views["right"]["sha256"],
                "storage_key": views["right"]["storage_path"],
                "content_type": "image/png",
                "use_as": "picture",
                "role": "scene_angle_ending:right",
                "asset_role": "scene",
            },
        ],
    }
    enforce_live_references(_FakeDB(), "proj-1", request_payload, data_dir=tmp_path)

    # Corrupting one angle view on disk must fail closed
    (tmp_path / views["right"]["storage_path"]).write_bytes(b"tampered")
    with pytest.raises(HTTPException) as exc_info:
        enforce_live_references(_FakeDB(), "proj-1", request_payload, data_dir=tmp_path)
    assert exc_info.value.status_code == 409


def test_compiler_270_dual_angle_anchoring_and_260_byte_identical_replay(tmp_path: Path):
    scene = _make_scene_asset(tmp_path, with_scene_sheet=True)
    char = _make_asset("asset-char-1", "character", "李嗣业")
    bindings = [
        ShotBindingInput(asset_id=char.id, use_as=ReferenceUse.SUBJECT, ordinal=0),
        ShotBindingInput(asset_id=scene.id, use_as=ReferenceUse.PICTURE, ordinal=1),
    ]
    spec = _make_shot_spec(camera={
        "composition": "Wide battle line sweeping across the dusty plain",
        "shot_size": "Wide shot",
        "move": "pan",
        "movement": "A sweeping pan moves from the temple gate toward the eastern ridge",
        "scene_angle": "front",
        "scene_angle_end": "right",
        "camera_height": "high_angle",
        "foreground_parallax": "billowing crimson war banners",
    })
    manifest = build_reference_manifest(
        bindings, {char.id: char, scene.id: scene}, shot_spec=spec, asset_root=tmp_path
    )
    compiler = H3PromptCompiler()
    compiled_270 = compiler.compile(
        spec=spec,
        style=EN_STYLE,
        manifest=manifest,
        project_language="Chinese",
        mode_hint=GenerationMode.REF2VA,
    )
    assert compiled_270.compiler_version == COMPILER_VERSION == "shine-h3-compiler/2.7.0"
    defs = compiled_270.sections["subject_definitions"]
    ret = compiled_270.sections["retention_analysis"]
    desc = compiled_270.sections["detailed_description"]

    assert "<Picture 2> is the opening camera-angle architectural and terrain reference for [Shot 1]" in defs
    assert "<Picture 3> is the destination camera-angle architectural and terrain reference for [Shot 1]" in defs
    assert "without warping or repeating structures" in defs
    assert "opening camera-angle environment reference" in ret
    assert "destination camera-angle architectural reference" in ret
    # Natural English camera height & parallax woven into prose; internal enum keys never leak
    assert "elevated high angle looking down" in desc
    assert "Foreground parallax moves past billowing crimson war banners" in desc
    assert "scene_angle" not in desc
    assert "high_angle" not in desc
    assert "push_in" not in desc

    # Legacy 2.6.0 byte-identical replay for single-plate historical shots
    legacy_spec = _make_shot_spec(camera={
        "composition": "Medium shot on the plain",
        "shot_size": "Medium shot",
        "movement": "The camera holds still.",
    })
    legacy_manifest = [
        ReferenceManifestItem(
            label="<Subject 1>",
            asset_id=char.id,
            sha256=char.sha256,
            storage_key=char.storage_path,
            content_type="image/png",
            use_as=ReferenceUse.SUBJECT,
            entity_id=char.entity_id,
            asset_role="character",
        ),
        ReferenceManifestItem(
            label="<Picture 2>",
            asset_id=scene.id,
            sha256=scene.sha256,
            storage_key=scene.storage_path,
            content_type="image/png",
            use_as=ReferenceUse.PICTURE,
            entity_id=scene.entity_id,
            asset_role="scene",
        ),
    ]
    v26_a = compiler.compile(
        spec=legacy_spec,
        style=EN_STYLE,
        manifest=legacy_manifest,
        project_language="Chinese",
        mode_hint=GenerationMode.REF2VA,
        compiler_version=LEGACY_CONTINUITY_CLEAN_V26_COMPILER_VERSION,
    )
    v27_single = compiler.compile(
        spec=legacy_spec,
        style=EN_STYLE,
        manifest=legacy_manifest,
        project_language="Chinese",
        mode_hint=GenerationMode.REF2VA,
    )
    assert v26_a.compiler_version == LEGACY_CONTINUITY_CLEAN_V26_COMPILER_VERSION == "shine-h3-compiler/2.6.0"
    assert v26_a.prompt_sha256 == v27_single.prompt_sha256

    # Under 2.6.0 replay, dual-angle roles keep pre-2.7.0 single-scene wording (frozen semantics)
    v26_dual = compiler.compile(
        spec=spec,
        style=EN_STYLE,
        manifest=manifest,
        project_language="Chinese",
        mode_hint=GenerationMode.REF2VA,
        compiler_version=LEGACY_CONTINUITY_CLEAN_V26_COMPILER_VERSION,
    )
    assert "destination camera-angle" not in v26_dual.sections["subject_definitions"]
    assert "elevated high angle looking down" not in v26_dual.sections["detailed_description"]
    assert v26_dual.prompt_sha256 != compiled_270.prompt_sha256
