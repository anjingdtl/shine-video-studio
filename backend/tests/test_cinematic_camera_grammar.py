"""Phase 2 tests: beat_energy-driven cinematic camera grammar, crew_salvage
scene_angle / camera_height synonym folding, acting_director camera_beat sync
with safe non-blocking fallback, continuity_engine rhythm warnings, and full
landing_rows -> build_reference_manifest -> H3PromptCompiler 2.7.0 integration."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from shine.agents.acting_director import (
    ActingShotPatch,
    ActingWindowPatch,
    apply_shot_patch,
    build_acting_group_payload,
    compute_pre_speech_budget,
    parse_acting_group_patch,
)
from shine.agents.continuity_engine import review_scene_board
from shine.agents.crew_salvage import parse_with_salvage
from shine.agents.scene_director import (
    DirectedCamera,
    SceneShotBoard,
    landing_rows,
    scene_director_prompt,
)
from shine.h3.compiler import H3PromptCompiler
from shine.schemas import GenerationMode, ReferenceUse, ShotBindingInput, ShotSpec, StyleBible
from shine.services import build_reference_manifest

pytestmark = pytest.mark.unit


def _make_shot(
    shot_id: str,
    beat_id: str,
    *,
    move: str = "static",
    motive: str = "",
    movement: str = "The camera holds still.",
    scene_angle: str | None = "front",
    scene_angle_end: str | None = None,
    camera_height: str | None = "eye_level",
    foreground_parallax: str = "",
    motivation: str = "establishing_reset",
    dialogue: list[dict] | None = None,
) -> dict:
    return {
        "id": shot_id,
        "beat_ids": [beat_id],
        "duration_s": 6.0,
        "visual_description": "{{C1}} surveys the dust-swept plain before Xiangji Temple.",
        "asset_ids": [],
        "location": "香积寺外旷野",
        "dialogue": dialogue or [],
        "camera": {
            "composition": "Wide frame placing {{C1}} left of center against the temple ridge",
            "shot_size": "Wide shot",
            "angle": "Three-quarter front",
            "lens": "35mm anamorphic",
            "move": move,
            "motive": motive,
            "movement": movement,
            "lighting": "Overcast autumn daylight across armor plates",
            **({"scene_angle": scene_angle} if scene_angle is not None else {}),
            **({"scene_angle_end": scene_angle_end} if scene_angle_end is not None else {}),
            **({"camera_height": camera_height} if camera_height is not None else {}),
            **({"foreground_parallax": foreground_parallax} if foreground_parallax else {}),
        },
        "ambience": "Autumn wind rattling iron armor and distant war drums",
        "music": "Low percussion pulse",
        "blocking": {
            "axis": "plain_axis_1",
            "placements": [
                {
                    "character": "李嗣业",
                    "zone": "left_mg",
                    "facing": "facing_right",
                    "posture": "Standing astride the front banner line",
                    "props": ["陌刀"],
                }
            ],
        },
        "environment": {
            "depth": "Foreground iron spears, midground vanguard infantry, distant pagoda silhouette",
            "light": "Diffused grey autumn sky casting cool metallic glints",
            "micro_motion": "Dust clouds and crimson banners whipping in crosswind",
        },
        "timeline": [
            {
                "start_s": 0.0,
                "end_s": 3.0,
                "action": "{{C1}} plants his boots firmly and grips the long blade handle.",
                "expression": "Brows knit downward, eyes narrowed against blowing grit, jaw clenched.",
                "camera": movement,
            },
            {
                "start_s": 3.0,
                "end_s": 6.0,
                "action": "{{C1}} shifts his weight forward as the front line braces.",
                "expression": "Nostrils flare with a slow inhale, gaze locked on the approaching dust.",
                "camera": movement,
            },
        ],
        "entry_state": "{{C1}} stands beside the war banner facing right.",
        "exit_state": "{{C1}} leans into a combat stance facing right.",
        "transition_motivation": motivation,
        "handoff_cue": "Cuts on the rising drumbeat",
        "internal_cuts": [],
    }


def _make_scene(n_beats: int) -> dict:
    return {
        "location": "香积寺外旷野",
        "beats": [
            {
                "description": f"Beat {i}: vanguard holds the plain.",
                "characters": ["李嗣业"],
                "spoken_lines": [],
                "beat_energy": "erupt" if i % 2 == 0 else "pressure",
            }
            for i in range(1, n_beats + 1)
        ],
    }


def test_scene_director_prompt_removes_static_by_default_and_teaches_energy_grammar():
    prompt = scene_director_prompt(
        max_shot_seconds=10,
        continuity_predecessor=False,
        translation_supplied=True,
        narration_allowed=False,
        scene_budget_seconds=60,
    )
    assert "static by default" not in prompt.lower()
    assert "scene_angle" in prompt
    assert "scene_angle_end" in prompt
    assert "camera_height" in prompt
    assert "foreground_parallax" in prompt
    for energy in ("calm", "probe", "pressure", "turn_pause", "erupt", "aftermath"):
        assert energy in prompt


def test_crew_salvage_normalizes_scene_angle_and_camera_height_synonyms():
    raw_cam = {
        "composition": "Hero low-angle shot of the vanguard general",
        "shot_size": "Medium close-up",
        "move": "orbit",
        "motive": "Build kinetic energy as the charge begins",
        "movement": "A brisk 3/4 orbit sweeps around the general",
        "angle_start": "front_wide",
        "angle_end": "back_view",
        "height": "low",
        "parallax": "passing crimson war banners",
    }
    parsed, notes = parse_with_salvage(raw_cam, DirectedCamera)
    assert isinstance(parsed, DirectedCamera)
    assert parsed.scene_angle == "front"
    assert parsed.scene_angle_end == "reverse"
    assert parsed.camera_height == "low_angle"
    assert parsed.foreground_parallax == "passing crimson war banners"
    assert any("synonym_folded" in n for n in notes)

    # Unknown garbage enum values drop cleanly to None instead of raising ValidationError
    raw_garbage = {
        "composition": "Wide shot of the plain",
        "move": "static",
        "movement": "The camera holds still.",
        "scene_angle": "diagonal_mars_orbit",
        "camera_height": "space_station",
    }
    parsed2, notes2 = parse_with_salvage(raw_garbage, DirectedCamera)
    assert parsed2.scene_angle is None
    assert parsed2.camera_height is None
    assert any("unknown_value_dropped" in n for n in notes2)


def test_continuity_engine_emits_non_blocking_camera_rhythm_warnings():
    # 5-shot scene with 4 consecutive static shots -> STATIC_CAMERA_MONOTONY warning (still accepted)
    scene5 = _make_scene(5)
    board_static = {
        "schema_version": "1.0",
        "shots": [
            _make_shot(f"S{i}", f"B{i}", move="static", movement="The camera holds still.")
            for i in range(1, 6)
        ],
    }
    verdict_static = review_scene_board(
        board_static, scene5, {"max_shot_seconds": 10, "character_tokens": {"{{C1}}": "李嗣业"}}
    )
    assert verdict_static.accepted
    codes = {w["code"] for w in verdict_static.warnings}
    assert "STATIC_CAMERA_MONOTONY" in codes
    assert "EXCESSIVE_CAMERA_MOTION" not in codes

    # 4-shot scene with 3 consecutive high-amplitude moves -> EXCESSIVE_CAMERA_MOTION warning (still accepted)
    scene4 = _make_scene(4)
    board_wild = {
        "schema_version": "1.0",
        "shots": [
            _make_shot(
                "S1", "B1", move="crane",
                motive="Reveal the battlefield", movement="High crane rises over the plain",
            ),
            _make_shot(
                "S2", "B2", move="orbit",
                motive="Track the vanguard clash", movement="Fast orbit circles the general",
            ),
            _make_shot(
                "S3", "B3", move="whip_pan",
                motive="Snap to flanking cavalry", movement="Sharp whip pan across the flank",
            ),
            _make_shot(
                "S4", "B4", move="pull_back",
                motive="Show the aftermath", movement="Slow pull back over the field",
            ),
        ],
    }
    verdict_wild = review_scene_board(
        board_wild, scene4, {"max_shot_seconds": 10, "character_tokens": {"{{C1}}": "李嗣业"}}
    )
    assert verdict_wild.accepted
    wild_codes = {w["code"] for w in verdict_wild.warnings}
    assert "EXCESSIVE_CAMERA_MOTION" in wild_codes
    assert "STATIC_CAMERA_MONOTONY" not in wild_codes


def test_acting_director_camera_beat_sync_and_safe_fallback():
    shot = _make_shot(
        "S1",
        "B1",
        move="push_in",
        motive="Compress tension before the battle cry",
        movement="Steady slow push in toward {{C1}}",
        dialogue=[{"speaker_id": "李嗣业", "text": "今日不破此阵，绝不生还！", "start_s": 0.2, "end_s": 3.0}],
    )
    shot["camera"]["shot_size"] = "Close-up"
    plan = compute_pre_speech_budget(shot, ["turn_pause"])
    assert plan.applied_pause_s >= 0.8

    token_map = {"{{C1}}": "李嗣业", "{{C2}}": "安守忠"}
    payload = build_acting_group_payload([(shot, plan, ["turn_pause"])], token_map=token_map)
    assert payload["shots"][0]["camera_move"] == "push_in"
    assert "push in" in payload["shots"][0]["camera_movement"].lower()

    # Valid camera_beat adopted per window
    raw_response = {
        "shots": [
            {
                "shot_id": "S1",
                "windows": [
                    {
                        "start_s": plan.window_slots[0]["start_s"],
                        "end_s": plan.window_slots[0]["end_s"],
                        "action": "{{C1}} tightens his grip on the long blade hilt.",
                        "expression": "Inner brows draw tight, lower eyelids narrow, deep breath swells chest.",
                        "camera_beat": "Slow deliberate push-in tightening onto {{C1}}'s eyes before speech.",
                    },
                    {
                        "start_s": plan.window_slots[1]["start_s"],
                        "end_s": plan.window_slots[1]["end_s"],
                        "action": "{{C1}} thrusts his chin forward and bellows the command.",
                        "expression": "Jaw opens wide, neck tendons strain taut, pupils stay locked forward.",
                        "camera_beat": "Camera settles and holds rock-steady on the close-up through delivery.",
                    },
                    *(
                        [
                            {
                                "start_s": plan.window_slots[2]["start_s"],
                                "end_s": plan.window_slots[2]["end_s"],
                                "action": "{{C1}} lowers his shoulders as dust blows across his cheek.",
                                "expression": "Lips press shut, nostrils flare on a sharp exhale, unblinking stare.",
                                "camera_beat": "Subtle breathing pull-back holds the lingering tension.",
                            }
                        ]
                        if len(plan.window_slots) >= 3
                        else []
                    ),
                ],
                "deliveries": [
                    {
                        "line_index": 0,
                        "delivery": {
                            "volume": "shouting",
                            "pace": "measured",
                            "voice_quality": "hoarse",
                            "pitch": "rising",
                        },
                    }
                ],
                "exit_acting_state": "Jaw locked, eyes burning forward, hoarse battle command hanging in air.",
            }
        ]
    }
    patches, errors, _notes = parse_acting_group_patch(raw_response, ["S1"])
    assert not errors
    merged = apply_shot_patch(shot, patches["S1"], plan, token_map=token_map)
    assert merged["timeline"][0]["camera"].startswith("Slow deliberate push-in")
    assert merged["timeline"][1]["camera"].startswith("Camera settles and holds")

    # Unsafe camera_beat (contains CJK or off-cast {{C2}}) safely falls back without blocking
    bad_cam_patch = ActingShotPatch(
        shot_id="S1",
        windows=[
            ActingWindowPatch(
                start_s=0.0,
                end_s=1.0,
                action="{{C1}} braces.",
                expression="Brows tighten, jaw sets.",
                camera_beat="缓缓推向 {{C1}}",  # CJK -> must fallback
            ),
            ActingWindowPatch(
                start_s=1.0,
                end_s=6.0,
                action="{{C1}} speaks.",
                expression="Lips part, eyes steady.",
                camera_beat="Pan toward {{C2}} off-screen",  # off-cast token -> must fallback
            ),
        ],
    )
    fallback_merged = apply_shot_patch(shot, bad_cam_patch, plan, token_map=token_map)
    assert fallback_merged["timeline"][0]["camera"] == "Steady slow push in toward {{C1}}"
    assert fallback_merged["timeline"][1]["camera"] == "Steady slow push in toward {{C1}}"


def test_end_to_end_landing_rows_to_dual_angle_manifest_and_h3_compile(tmp_path: Path):
    scene = _make_scene(2)
    board = {
        "schema_version": "1.0",
        "shots": [
            _make_shot(
                "S1",
                "B1",
                move="pan",
                motive="Sweep across the battle array from temple gate to eastern flank",
                movement="A sweeping wide pan glides from the front gate to the right flank",
                scene_angle="front",
                scene_angle_end="right",
                camera_height="high_angle",
                foreground_parallax="fluttering Tang war banners",
                motivation="establishing_reset",
            ),
            _make_shot(
                "S2",
                "B2",
                move="track",
                motive="Follow the vanguard general charging forward",
                movement="Dynamic low tracking shot moves beside {{C1}}",
                scene_angle="right",
                scene_angle_end="reverse",
                camera_height="low_angle",
                foreground_parallax="sweeping spear tips and swirling dust",
                motivation="match_on_action",
            ),
        ],
    }
    parsed_board, _ = parse_with_salvage(board, SceneShotBoard)
    board_dict = parsed_board.model_dump(mode="json")
    rows, verdict = landing_rows(
        board_dict,
        scene,
        {"max_shot_seconds": 10, "character_tokens": {"{{C1}}": "李嗣业"}},
    )
    assert verdict.accepted
    assert rows[0]["camera"]["move"] == "pan"
    assert rows[0]["camera"]["scene_angle"] == "front"
    assert rows[0]["camera"]["scene_angle_end"] == "right"
    assert rows[0]["camera"]["camera_height"] == "high_angle"
    assert rows[0]["camera"]["foreground_parallax"] == "fluttering Tang war banners"

    # Build a 4-view scene asset and compile S1 through build_reference_manifest + H3PromptCompiler 2.7.0
    (tmp_path / "assets").mkdir(parents=True, exist_ok=True)
    views = {}
    angle_pairs = (
        ("front", "front_wide"),
        ("left", "left_quarter"),
        ("right", "right_quarter"),
        ("reverse", "reverse_wide"),
    )
    for angle, key in angle_pairs:
        buf = io.BytesIO()
        Image.new("RGB", (64, 36), (80, 90, 100)).save(buf, format="PNG")
        raw = buf.getvalue()
        rel = f"assets/xiangji_{key}.png"
        (tmp_path / rel).write_bytes(raw)
        views[angle] = {
            "key": key,
            "storage_path": rel,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "width": 1344,
            "height": 768,
        }
    master_sha = hashlib.sha256(b"master").hexdigest()
    scene_asset = SimpleNamespace(
        id="asset-scene-xj",
        content_type="image/png",
        sha256=master_sha,
        storage_path="assets/xiangji_master.png",
        role="scene",
        entity_id="香积寺外旷野",
        duration_s=None,
        provenance={"pipeline": "t2i_to_scene_sheet_v1", "scene_sheet": {"views": views}},
    )
    char_asset = SimpleNamespace(
        id="asset-char-lsy",
        content_type="image/png",
        sha256=hashlib.sha256(b"char").hexdigest(),
        storage_path="assets/char_lsy.png",
        role="character",
        entity_id="李嗣业",
        duration_s=None,
        provenance={},
    )
    spec = ShotSpec(
        story_beat=rows[0]["visual"],
        characters=rows[0]["cast"],
        location_id=rows[0]["location"],
        duration_target_s=rows[0]["duration_s"],
        style_version_id="style-xj-v1",
        camera=rows[0]["camera"],
        character_tokens={"{{C1}}": "李嗣业"},
    )
    bindings = [
        ShotBindingInput(asset_id=char_asset.id, use_as=ReferenceUse.SUBJECT, ordinal=0),
        ShotBindingInput(asset_id=scene_asset.id, use_as=ReferenceUse.PICTURE, ordinal=1),
    ]
    manifest = build_reference_manifest(
        bindings, {char_asset.id: char_asset, scene_asset.id: scene_asset}, shot_spec=spec, asset_root=tmp_path
    )
    assert [m.role for m in manifest] == [None, "scene_angle_opening:front", "scene_angle_ending:right"]

    style = StyleBible(
        photography_language="Epic Tang dynasty war cinema",
        shot_blocking="Sweeping battlefield depth",
        color_palette="Ochre dust, iron grey, crimson silk",
        lighting_tone="Dramatic overcast daylight",
        lens_texture="Anamorphic cinema lens",
        editing_rhythm="Kinetic momentum",
        sound_performance="Natural Mandarin commands and battle drums",
    )
    compiled = H3PromptCompiler().compile(
        spec=spec, style=style, manifest=manifest, project_language="Chinese", mode_hint=GenerationMode.REF2VA
    )
    assert "opening camera-angle architectural and terrain reference" in compiled.sections["subject_definitions"]
    assert "destination camera-angle architectural and terrain reference" in compiled.sections["subject_definitions"]
    assert "Foreground parallax moves past fluttering Tang war banners" in compiled.sections["detailed_description"]
    assert "scene_angle_end" not in compiled.prompt
    assert "high_angle" not in compiled.prompt
