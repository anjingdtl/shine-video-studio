"""Regression tests for cross-shot physical state continuity, dialogue tail protection,
parallax hygiene, core prop auto-binding, and H3PromptCompiler 2.8.0."""

from __future__ import annotations

import pytest

from shine.agents import acting_director as ad
from shine.agents import continuity_engine as engine
from shine.agents.crew_prompts import drama_screenwriter_prompt
from shine.agents.scene_director import scene_director_prompt
from shine.h3.compiler import (
    COMPILER_VERSION,
    LEGACY_SCENE_ANGLE_V27_COMPILER_VERSION,
    H3PromptCompiler,
)
from shine.h3.spec_review import review_h3_prompt
from shine.models import Asset, Project
from shine.schemas import (
    DialogueLine,
    GenerationMode,
    ReferenceManifestItem,
    ReferenceUse,
    ShotSpec,
    StyleBible,
)
from shine.services import complete_prop_bindings

pytestmark = pytest.mark.unit


def _battle_scene(with_dialogue: bool = False) -> dict:
    return {
        "location": "香积寺外旷野",
        "time_of_day": "day",
        "weather_atmosphere": "秋风黄尘",
        "entry_bridge": "两军阵前对峙。",
        "exit_state": "李嗣业率唐军列阵迎敌。",
        "beats": [
            {
                "beat_type": "establishing",
                "visible_action": "李嗣业与安守忠立于两军阵前。",
                "characters": ["李嗣业", "安守忠"],
                "spoken_lines": [],
                "beat_energy": "calm",
            },
            {
                "beat_type": "action",
                "visible_action": "李嗣业将陌刀插进黄土。",
                "characters": ["李嗣业"],
                "spoken_lines": (
                    [{"speaker": "李嗣业", "emotion": "决绝", "text": "今日之战，不死不休！"}]
                    if with_dialogue
                    else []
                ),
                "beat_energy": "pressure",
            },
            {
                "beat_type": "action",
                "visible_action": "李嗣业从土中拔出陌刀迎敌。",
                "characters": ["李嗣业"],
                "spoken_lines": [],
                "beat_energy": "erupt",
            },
        ],
    }


def _shot(
    sid: str,
    beat_ids: list[str],
    *,
    duration_s: float = 6.0,
    placements: list[dict],
    entry_state: str,
    exit_state: str,
    timeline_action: str,
    motivation: str = "match_on_action",
    dialogue: list[dict] | None = None,
    fg_parallax: str = "",
) -> dict:
    return {
        "id": sid,
        "beat_ids": beat_ids,
        "duration_s": duration_s,
        "visual_description": f"{entry_state} {timeline_action}",
        "asset_ids": [],
        "location": "香积寺外旷野",
        "dialogue": list(dialogue or []),
        "camera": {
            "composition": "Medium shot across dusty plain.",
            "shot_size": "MS",
            "angle": "eye-level",
            "lens": "50mm",
            "move": "push_in",
            "motive": "intensify the standoff",
            "movement": "Slow push in toward the vanguard.",
            "lighting": "Overcast autumn daylight.",
            "scene_angle": "front",
            "camera_height": "eye_level",
            "foreground_parallax": fg_parallax,
        },
        "ambience": "Autumn wind across dry grass and distant banners.",
        "music": "none",
        "blocking": {"axis": "battle_axis", "placements": placements},
        "environment": {
            "depth": "Dusty plain with opposing Tang and rebel army ranks arrayed in the background.",
            "light": "Diffused overcast autumn daylight.",
            "micro_motion": "Yellow dust and banners flutter in wind.",
        },
        "timeline": [
            {
                "start_s": 0.0,
                "end_s": duration_s,
                "action": timeline_action,
                "expression": "brows draw tight and jaw sets",
                "camera": "Slow push in.",
            }
        ],
        "entry_state": entry_state,
        "exit_state": exit_state,
        "transition_motivation": motivation,
        "handoff_cue": "continuous action across battlefield",
        "internal_cuts": [],
    }


def _ctx() -> dict:
    return {
        "max_shot_seconds": 10.0,
        "continuity_predecessor": False,
        "registered_locations": [{"name": "香积寺外旷野"}],
        "character_tokens": {"{{C1}}": "李嗣业", "{{C2}}": "安守忠"},
    }


def test_cross_shot_planted_blade_contradictions_are_refused_even_on_cut():
    scene = _battle_scene()
    s1 = _shot(
        "S1",
        ["B1"],
        placements=[
            {"character": "李嗣业", "zone": "left_fg", "facing": "facing_right",
             "posture": "standing on foot in open vanguard", "props": ["modao"]},
            {"character": "安守忠", "zone": "right_fg", "facing": "facing_left",
             "posture": "standing on foot in open vanguard", "props": []},
        ],
        entry_state="{{C1}} and {{C2}} stand on foot facing each other.",
        exit_state="{{C1}} stands on foot facing {{C2}} across open ground.",
        timeline_action="{{C1}} faces {{C2}} steadily on foot.",
        motivation="establishing_reset",
    )
    # S2 ends with modao planted blade-down in the earth.
    s2 = _shot(
        "S2",
        ["B2"],
        placements=[
            {"character": "李嗣业", "zone": "left_fg", "facing": "facing_right",
             "posture": "standing on foot gripping modao", "props": ["modao"]},
        ],
        entry_state="{{C1}} grips the modao on foot.",
        exit_state="The heavy modao stands planted into the earth blade down.",
        timeline_action="{{C1}} drives the modao blade into the earth.",
        motivation="match_on_action",
    )
    # S3 (cut!) illegally opens with the modao raised blade-up without pulling it out.
    s3_bad = _shot(
        "S3",
        ["B3"],
        placements=[
            {"character": "李嗣业", "zone": "left_fg", "facing": "facing_right",
             "posture": "standing on foot holding modao raised upright", "props": ["modao"]},
        ],
        entry_state="{{C1}} holds the modao blade upward over his shoulder.",
        exit_state="{{C1}} holds the modao raised.",
        timeline_action="{{C1}} steadies the raised modao.",
        motivation="match_on_action",
    )
    verdict_bad = engine.review_scene_board({"shots": [s1, s2, s3_bad]}, scene, _ctx())
    assert not verdict_bad.accepted
    assert any(
        r["code"] == "CROSS_SHOT_PROP_STATE_CONFLICT" and r["detail"] == "planted_to_raised_without_pull"
        for r in verdict_bad.refusals
    )

    # S3 properly opens planted and visibly pulls the modao out of the earth -> ACCEPTED.
    s3_good = _shot(
        "S3",
        ["B3"],
        placements=[
            {"character": "李嗣业", "zone": "left_fg", "facing": "facing_right",
             "posture": "standing on foot gripping modao planted into the earth", "props": ["modao"]},
        ],
        entry_state="{{C1}} grips the modao planted in the earth.",
        exit_state="{{C1}} holds the pulled modao blade upward ready to charge.",
        timeline_action="{{C1}} pulls the modao out of the earth and lifts it high.",
        motivation="match_on_action",
    )
    verdict_good = engine.review_scene_board({"shots": [s1, s2, s3_good]}, scene, _ctx())
    assert verdict_good.accepted, verdict_good.refusals

    # Reverse contradiction: S2 ends with modao raised overhead, S3 pulls it out of the ground -> REFUSED.
    s2_raised = {
        **s2,
        "exit_state": "{{C1}} holds the modao raised high across his shoulder.",
        "timeline": [{
            "start_s": 0.0,
            "end_s": 6.0,
            "action": "{{C1}} lifts the modao high over his shoulder.",
            "expression": "jaw clenches",
            "camera": "Slow push in.",
        }],
    }
    s3_phantom_pull = _shot(
        "S3",
        ["B3"],
        placements=[
            {"character": "李嗣业", "zone": "left_fg", "facing": "facing_right",
             "posture": "standing on foot gripping modao", "props": ["modao"]},
        ],
        entry_state="{{C1}} grips the modao shaft.",
        exit_state="{{C1}} holds the modao raised.",
        timeline_action="{{C1}} wrenches the modao out of the ground.",
        motivation="match_on_action",
    )
    verdict_phantom = engine.review_scene_board({"shots": [s1, s2_raised, s3_phantom_pull]}, scene, _ctx())
    assert not verdict_phantom.accepted
    assert any(
        r["code"] == "CROSS_SHOT_PROP_STATE_CONFLICT" and r["detail"] == "pull_from_ground_when_not_planted"
        for r in verdict_phantom.refusals
    )


def test_cross_shot_mount_teleport_refused_unless_transition_staged():
    scene = _battle_scene()
    s1 = _shot(
        "S1",
        ["B1"],
        placements=[
            {"character": "李嗣业", "zone": "left_fg", "facing": "facing_right",
             "posture": "standing on foot in open dust", "props": []},
            {"character": "安守忠", "zone": "right_fg", "facing": "facing_left",
             "posture": "standing on foot in open dust", "props": []},
        ],
        entry_state="{{C1}} and {{C2}} stand on foot facing each other.",
        exit_state="{{C1}} and {{C2}} remain standing on foot.",
        timeline_action="{{C1}} and {{C2}} lock eyes on foot.",
        motivation="establishing_reset",
    )
    s2_teleport = _shot(
        "S2",
        ["B2"],
        placements=[
            {"character": "李嗣业", "zone": "left_fg", "facing": "facing_right",
             "posture": "mounted on horseback gripping reins", "props": []},
        ],
        entry_state="{{C1}} sits mounted on a black warhorse.",
        exit_state="{{C1}} sits mounted on horseback.",
        timeline_action="{{C1}} glares forward from the saddle.",
        motivation="shot_reverse_shot",
    )
    s3 = _shot(
        "S3",
        ["B3"],
        placements=[
            {"character": "李嗣业", "zone": "left_fg", "facing": "facing_right",
             "posture": "mounted on horseback gripping modao", "props": ["modao"]},
        ],
        entry_state="{{C1}} remains mounted on horseback.",
        exit_state="{{C1}} rides forward.",
        timeline_action="{{C1}} spurs forward.",
        motivation="continuous_action",
    )
    verdict_bad = engine.review_scene_board({"shots": [s1, s2_teleport, s3]}, scene, _ctx())
    assert not verdict_bad.accepted
    assert any(r["code"] == "CROSS_SHOT_MOUNT_CONFLICT" for r in verdict_bad.refusals)

    # Explicit mounting action in S1 makes S2 legal.
    s1_mounts = {
        **s1,
        "timeline": [{
            "start_s": 0.0,
            "end_s": 6.0,
            "action": "{{C1}} mounts a warhorse and swings into the saddle.",
            "expression": "brows tighten",
            "camera": "Slow push in.",
        }],
    }
    verdict_ok = engine.review_scene_board({"shots": [s1_mounts, s2_teleport, s3]}, scene, _ctx())
    assert verdict_ok.accepted, verdict_ok.refusals


def test_dialogue_tail_protection_and_parallax_hygiene():
    scene = _battle_scene(with_dialogue=True)
    s1 = _shot(
        "S1",
        ["B1"],
        placements=[
            {"character": "李嗣业", "zone": "left_fg", "facing": "facing_right",
             "posture": "standing on foot", "props": []},
            {"character": "安守忠", "zone": "right_fg", "facing": "facing_left",
             "posture": "standing on foot", "props": []},
        ],
        entry_state="{{C1}} and {{C2}} face each other.",
        exit_state="{{C1}} steps forward.",
        timeline_action="{{C1}} faces {{C2}}.",
        motivation="establishing_reset",
        fg_parallax="edge of a black horse's neck and mane low in frame",
    )
    # Dialogue ends at 5.8s in a 6.0s shot -> 0.2s tail -> REFUSED by DIALOGUE_TAIL_TOO_SHORT.
    s2_cutoff = _shot(
        "S2",
        ["B2"],
        duration_s=6.0,
        placements=[
            {"character": "李嗣业", "zone": "left_fg", "facing": "facing_right",
             "posture": "standing on foot", "props": []},
        ],
        entry_state="{{C1}} inhales sharply.",
        exit_state="{{C1}} finishes shouting.",
        timeline_action="{{C1}} lifts his chin.",
        dialogue=[{"speaker_id": "李嗣业", "text": "今日之战，不死不休！", "start_s": 3.0, "end_s": 5.8}],
    )
    s3 = _shot(
        "S3",
        ["B3"],
        placements=[
            {"character": "李嗣业", "zone": "left_fg", "facing": "facing_right",
             "posture": "standing on foot", "props": []},
        ],
        entry_state="{{C1}} holds firm.",
        exit_state="{{C1}} charges.",
        timeline_action="{{C1}} steps ahead.",
    )
    verdict = engine.review_scene_board({"shots": [s1, s2_cutoff, s3]}, scene, _ctx())
    assert not verdict.accepted
    assert any(r["code"] == "DIALOGUE_TAIL_TOO_SHORT" for r in verdict.refusals)
    assert any(
        w["code"] == "FOREGROUND_PARALLAX_CONTAMINATION" and w["cue"] == "horse"
        for w in verdict.warnings
    )

    # Acting director preserves 0.7s tail at shot end (never shoves dialogue to clip end).
    tight_end_shot = {
        **s2_cutoff,
        "duration_s": 6.0,
        "dialogue": [{"speaker_id": "李嗣业", "text": "今日之战，不死不休！", "start_s": 0.3, "end_s": 5.2}],
    }
    plan = ad.compute_pre_speech_budget(tight_end_shot, ["erupt"])
    assert plan.adjusted_dialogue[0]["end_s"] <= 6.0 - ad._SHOT_END_TAIL_S + 1e-6


def test_complete_prop_bindings_auto_binds_locked_modao_from_english_prose(api):
    _client, factory, _settings = api
    with factory() as db:
        project = Project(title="Xiangji Prop Auto-Bind Test")
        db.add(project)
        db.flush()

        modao = Asset(
            project_id=project.id,
            filename="modao.png",
            kind="image",
            role="prop",
            entity_id="陌刀",
            storage_path="assets/modao.png",
            content_type="image/png",
            sha256="a" * 64,
            byte_size=1024,
            approved=True,
            locked=True,
            provenance={},
        )
        db.add(modao)
        db.commit()

        # Shot prose mentions "modao" or space-separated "mo dao" in English without passing modao.id.
        bindings = complete_prop_bindings(
            db,
            project.id,
            ["standing gripping heavy Tang modao", "drives the modao blade into the earth"],
            [],
        )
        assert len(bindings) == 1
        assert bindings[0].asset_id == modao.id
        assert bindings[0].use_as.value == "picture"
        assert bindings[0].role == "陌刀"

        spaced_bindings = complete_prop_bindings(
            db,
            project.id,
            ["both hands gripping the long-hafted mo dao"],
            [],
        )
        assert len(spaced_bindings) == 1
        assert spaced_bindings[0].asset_id == modao.id

        # Unrelated shot prose without modao does not falsely bind the prop.
        empty_bindings = complete_prop_bindings(
            db,
            project.id,
            ["wide establishing shot of empty wooden teahouse interior"],
            [],
        )
        assert empty_bindings == []


def test_h3_compiler_280_ensemble_guard_speech_completion_and_270_replay():
    style = StyleBible(
        photography_language="Grounded historical epic cinematography",
        shot_blocking="Clear spatial axis",
        color_palette="Muted ochre and iron grey",
        lighting_tone="Overcast autumn daylight",
        lens_texture="35mm film grain",
        editing_rhythm="Deliberate escalating rhythm",
        sound_performance="Clear Mandarin dialogue with wind ambience",
    )
    spec = ShotSpec(
        story_beat="{{C1}} speaks before the opposing army ranks.",
        duration_target_s=7.0,
        style_version_id="style-v1",
        characters=["李嗣业"],
        character_tokens={"{{C1}}": "李嗣业"},
        dialogue=[
            DialogueLine(speaker_id="李嗣业", text="今日之战，不死不休！", start_s=1.0, end_s=3.8),
        ],
        camera={"shot_size": "MS", "movement": "Slow push in.", "camera_height": "low_angle"},
        sound={"ambience": "Autumn wind and fluttering war banners.", "music": "none"},
    )
    manifest = [
        ReferenceManifestItem(
            label="<Subject 1>",
            asset_id="char-1",
            sha256="1" * 64,
            storage_key="assets/char1.png",
            use_as=ReferenceUse.SUBJECT,
            content_type="image/png",
            role="李嗣业",
            entity_id="李嗣业",
            asset_role="character",
        ),
        ReferenceManifestItem(
            label="<Picture 2>",
            asset_id="scene-1",
            sha256="2" * 64,
            storage_key="assets/scene_front.png",
            use_as=ReferenceUse.PICTURE,
            content_type="image/png",
            role="scene_angle:front",
            entity_id="香积寺外旷野",
            asset_role="scene",
        ),
    ]
    compiler = H3PromptCompiler()
    c280 = compiler.compile(spec=spec, style=style, manifest=manifest, mode_hint=GenerationMode.REF2VA)
    assert c280.compiler_version == COMPILER_VERSION == "shine-h3-compiler/2.8.0"
    assert "Keep any background crowds, formations, or secondary figures" in c280.prompt
    assert "finishing every final syllable cleanly before the shot ends" in c280.prompt
    findings = review_h3_prompt(
        c280.prompt,
        c280.sections,
        mode=GenerationMode.REF2VA.value,
        dialogue=spec.dialogue,
        duration_s=7.0,
        defined_labels={"<Subject 1>", "<Picture 1>", "<Picture 2>"},
    )
    assert findings == []

    # 2.7.0 replay stays byte-identical to its pre-2.8.0 output.
    c270 = compiler.compile(
        spec=spec,
        style=style,
        manifest=manifest,
        mode_hint=GenerationMode.REF2VA,
        compiler_version=LEGACY_SCENE_ANGLE_V27_COMPILER_VERSION,
    )
    assert c270.compiler_version == "shine-h3-compiler/2.7.0"
    assert "Keep any background crowds" not in c270.prompt
    assert "finishing every final syllable cleanly" not in c270.prompt

    # Prompt contracts include the new continuity safeguards.
    dp = drama_screenwriter_prompt(scene_budget_seconds=60)
    assert "mount/foot state" in dp and "planted blade-down" in dp
    sp = scene_director_prompt(
        max_shot_seconds=10.0,
        continuity_predecessor=False,
        translation_supplied=False,
        narration_allowed=False,
        scene_budget_seconds=60,
    )
    assert "MANDATORY CROSS-SHOT PHYSICAL STATE LEDGER" in sp
    assert "FOREGROUND PARALLAX HYGIENE" in sp
