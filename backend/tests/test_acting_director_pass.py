"""Phase 2 regression tests: DramaBeat.beat_energy, deterministic 2-4 shot performance
segment grouping, adaptive pre-speech pause & safe shift, anatomical bare-emotion
detection, cross-group carry_over_state handoff, fault-injection non-blocking
degradation, and end-to-end single-shot repolish API."""

from __future__ import annotations

import json

import pytest

from shine.agents import acting_director as ad
from shine.agents import continuity_engine as engine
from shine.agents.film_contracts import DramaBeat, DramaSceneScript
from shine.h3.compiler import H3PromptCompiler
from shine.h3.spec_review import review_h3_prompt
from shine.schemas import (
    GenerationMode,
    ReferenceManifestItem,
    ReferenceUse,
    ShotSpec,
    StyleBible,
)

pytestmark = pytest.mark.unit


def _two_person_scene(num_beats: int = 8) -> dict:
    energies = ["calm", "probe", "pressure", "pressure", "turn_pause", "erupt", "erupt", "aftermath"]
    beats = []
    for idx in range(num_beats):
        speaker = "林晚" if idx % 2 == 0 else "陈默"
        beats.append({
            "beat_type": "dialogue_exchange" if idx not in {4, 7} else "reaction",
            "visible_action": f"Beat {idx + 1} action at the wooden table.",
            "characters": ["林晚", "陈默"],
            "spoken_lines": [{"speaker": speaker, "emotion": "克制", "text": f"第{idx + 1}句对白。"}],
            "subtext": "testing resolve",
            "beat_energy": energies[idx % len(energies)],
        })
    return {
        "location": "旧茶馆",
        "time_of_day": "night",
        "weather_atmosphere": "雨夜",
        "entry_bridge": "门外雨声未停。",
        "exit_state": "茶杯凉透。",
        "beats": beats,
    }


def _make_shot(
    shot_id: str,
    beat_id: str,
    *,
    speaker: str = "林晚",
    line_text: str = "第1句对白。",
    duration_s: float = 7.0,
    line_start: float = 0.2,
    line_end: float = 2.0,
    shot_size: str = "CU",
    motivation: str = "shot_reverse_shot",
    location: str = "旧茶馆",
    axis: str = "table_axis",
    internal_cuts: list[dict] | None = None,
) -> dict:
    token = "{{C1}}" if speaker == "林晚" else "{{C2}}"
    return {
        "id": shot_id,
        "beat_ids": [beat_id],
        "duration_s": duration_s,
        "visual_description": f"{token} sits at the wooden table under warm lamplight.",
        "asset_ids": [],
        "location": location,
        "dialogue": (
            [{"speaker_id": speaker, "text": line_text, "start_s": line_start, "end_s": line_end}]
            if line_text
            else []
        ),
        "camera": {
            "composition": f"{shot_size} framing on {token}.",
            "shot_size": shot_size,
            "angle": "eye-level",
            "lens": "50mm",
            "move": "static",
            "motive": "",
            "movement": "The camera holds still.",
            "lighting": "Warm table lamp key.",
        },
        "ambience": "Steady rain against wooden shutters.",
        "music": "none",
        "blocking": {
            "axis": axis,
            "placements": [
                {
                    "character": speaker,
                    "zone": "left_fg" if speaker == "林晚" else "right_fg",
                    "facing": "facing_right" if speaker == "林晚" else "facing_left",
                    "posture": "seated upright",
                    "props": [],
                }
            ],
        },
        "environment": {
            "depth": "Table foreground, lattice screen background.",
            "light": "Single warm amber lamp from screen left.",
            "micro_motion": "Thin steam rises from the cup.",
        },
        "timeline": [
            {
                "start_s": 0.0,
                "end_s": round(duration_s / 2, 2),
                "action": f"{token} leans slightly forward",
                "expression": "eyes narrow slightly",
                "camera": "The camera holds still.",
            },
            {
                "start_s": round(duration_s / 2, 2),
                "end_s": duration_s,
                "action": f"{token} holds posture still",
                "expression": "jaw tightens before exhale",
                "camera": "The camera holds still.",
            },
        ],
        "entry_state": f"{token} seated at the wooden table.",
        "exit_state": f"{token} holds gaze across the table.",
        "transition_motivation": motivation,
        "handoff_cue": "eyeline cut",
        "internal_cuts": list(internal_cuts or []),
    }


def _eight_shot_scene_board() -> tuple[dict, dict, dict]:
    scene = _two_person_scene(8)
    sizes = ["MS", "MS", "MS", "MCU", "CU", "CU", "MCU", "CU"]
    motivations = [
        "establishing_reset",
        "shot_reverse_shot",
        "shot_reverse_shot",
        "shot_reverse_shot",
        "eyeline_match",
        "shot_reverse_shot",
        "continuous_action",
        "eyeline_match",
    ]
    shots = []
    for i in range(8):
        speaker = "林晚" if i % 2 == 0 else "陈默"
        shots.append(
            _make_shot(
                f"S{i + 1}",
                f"B{i + 1}",
                speaker=speaker,
                line_text=f"第{i + 1}句对白。",
                duration_s=7.5 if sizes[i] in {"CU", "MCU"} else 6.0,
                line_start=0.2,
                line_end=2.2,
                shot_size=sizes[i],
                motivation=motivations[i],
            )
        )
    board = {
        "schema_version": "1.0",
        "shots": shots,
        "character_tokens": {"{{C1}}": "林晚", "{{C2}}": "陈默"},
    }
    ctx = {
        "max_shot_seconds": 10.0,
        "continuity_predecessor": False,
        "registered_locations": ["旧茶馆"],
        "character_tokens": {"{{C1}}": "林晚", "{{C2}}": "陈默"},
    }
    return board, scene, ctx


def test_drama_beat_energy_optional_and_omitted_when_unset():
    legacy = DramaBeat(visible_action="老周放下杯子。", characters=["老周"])
    assert "beat_energy" not in legacy.model_dump(mode="json")

    energized = DramaBeat(
        beat_type="reaction",
        visible_action="老周停住手指。",
        characters=["老周"],
        beat_energy="turn_pause",
    )
    assert energized.model_dump(mode="json")["beat_energy"] == "turn_pause"
    script = DramaSceneScript(location="旧茶馆", beats=[legacy, energized])
    assert len(script.beats) == 2

    # Salvage recovers LLM confusion between beat_type and beat_energy losslessly.
    from shine.agents.crew_salvage import parse_with_salvage

    salvaged, notes = parse_with_salvage(
        {
            "location": "旧茶馆",
            "beats": [
                {
                    "beat_type": "turn_pause",
                    "visible_action": "林晚盯住银钥匙。",
                    "characters": ["林晚"],
                    "spoken_lines": [],
                },
                {
                    "beat_type": "dialogue",
                    "visible_action": "陈默开口。",
                    "characters": ["陈默"],
                    "spoken_lines": [{"speaker": "陈默", "text": "你早就知道。"}],
                    "beat_energy": "bogus_tier",
                },
            ],
        },
        DramaSceneScript,
    )
    assert isinstance(salvaged, DramaSceneScript)
    assert salvaged.beats[0].beat_type == "reaction"
    assert salvaged.beats[0].beat_energy == "turn_pause"
    assert salvaged.beats[1].beat_type == "dialogue_exchange"
    assert salvaged.beats[1].beat_energy is None
    assert notes


def test_detect_bare_emotion_words_only_flags_abstract_labels_without_anatomy():
    assert engine.detect_bare_emotion_words("looks angry and shocked") == ["angry", "shocked"]
    assert engine.detect_bare_emotion_words("feels deeply sad") == ["sad"]
    # Concrete facial anatomy passes even if an emotional adjective is present.
    assert engine.detect_bare_emotion_words(
        "inner brows draw inward in angry tension as nostrils flare and jaw clenches"
    ) == []
    assert engine.detect_bare_emotion_words(
        "upper eyelids narrow, gaze locks forward, lips press tight as throat swallows"
    ) == []

    board, scene, ctx = _eight_shot_scene_board()
    board["shots"][0]["timeline"][0]["expression"] = "looks furious and nervous"
    verdict = engine.review_scene_board(board, scene, ctx)
    assert verdict.accepted  # warning only, never a blocking refusal
    bare_warnings = [w for w in verdict.warnings if w["code"] == "BARE_EMOTION_WORD"]
    assert len(bare_warnings) == 1
    assert bare_warnings[0]["shot_id"] == "S1"
    assert bare_warnings[0]["words"] == ["furious", "nervous"]


def test_group_performance_segments_splits_by_breaks_and_energy_within_2_to_4_shots():
    board, scene, _ctx = _eight_shot_scene_board()
    groups = ad.group_performance_segments(board["shots"], scene)
    # 8 continuous shots split into 2-4 shot groups, splitting naturally at S5 (turn_pause).
    assert [idx for g in groups for idx in g] == list(range(8))
    assert all(1 <= len(g) <= 4 for g in groups)
    assert groups == [[0, 1, 2, 3], [4, 5, 6, 7]]

    # Location/axis/time jump forces an explicit group boundary.
    board["shots"][2]["transition_motivation"] = "time_or_space_jump"
    board["shots"][2]["location"] = "雨巷"
    groups_jump = ad.group_performance_segments(board["shots"], scene)
    assert groups_jump[0] == [0, 1]
    assert groups_jump[1][0] == 2


def test_compute_pre_speech_budget_adaptive_pause_safe_shift_and_exemptions():
    # 1. Close-up with room -> 1.0s pre-speech pause by safely shifting dialogue (+0.8s).
    cu_shot = _make_shot("S1", "B1", duration_s=7.0, line_start=0.2, line_end=2.2, shot_size="CU")
    cu_plan = ad.compute_pre_speech_budget(cu_shot, ["turn_pause"])
    assert cu_plan.target_pause_s == 1.0
    assert cu_plan.applied_pause_s == 1.0
    assert cu_plan.shift_s == pytest.approx(0.8)
    assert cu_plan.adjusted_dialogue[0]["start_s"] == pytest.approx(1.0)
    assert cu_plan.adjusted_dialogue[0]["end_s"] == pytest.approx(3.0)
    assert cu_plan.window_slots[0] == {"start_s": 0.0, "end_s": 1.0, "role": "pre_speech"}

    # 2. Medium shot on probe -> compact 0.4s pause.
    ms_shot = _make_shot("S2", "B2", duration_s=6.0, line_start=0.1, line_end=2.0, shot_size="MS")
    ms_plan = ad.compute_pre_speech_budget(ms_shot, ["probe"])
    assert ms_plan.target_pause_s == 0.4
    assert ms_plan.applied_pause_s == pytest.approx(0.4)
    assert ms_plan.adjusted_dialogue[0]["start_s"] == pytest.approx(0.4)

    # 3. Continuous action -> exempt (0.0s, never breaks tail-frame motion).
    cont_shot = _make_shot("S3", "B3", duration_s=6.0, line_start=0.2, line_end=2.0, shot_size="CU",
                           motivation="continuous_action")
    cont_plan = ad.compute_pre_speech_budget(cont_shot, ["erupt"])
    assert cont_plan.applied_pause_s == 0.0
    assert cont_plan.exempt_reason == "continuous_action"
    assert cont_plan.adjusted_dialogue[0]["start_s"] == pytest.approx(0.2)

    # 4. Internal cut headroom limit: dialogue ending at 2.65s before cut at 3.0s only has 0.2s headroom.
    tight_shot = _make_shot(
        "S4", "B4", duration_s=7.0, line_start=0.2, line_end=2.65, shot_size="CU",
        internal_cuts=[{"start_s": 3.0, "beat_ids": ["B5"], "visual_description": "Cut to listener."}],
    )
    tight_plan = ad.compute_pre_speech_budget(tight_shot, ["turn_pause"])
    assert tight_plan.adjusted_dialogue[0]["end_s"] <= 3.0 - 0.15 + 1e-6
    assert tight_plan.shift_s == pytest.approx(0.2)

    # 5. Wide shot and silent shot -> exempt; line already at 1.3s keeps exact 1.3s pre-speech boundary.
    wide_shot = _make_shot("S5", "B5", duration_s=6.0, line_start=0.2, line_end=2.0, shot_size="WS")
    assert ad.compute_pre_speech_budget(wide_shot, ["calm"]).exempt_reason == "wide_shot"
    silent_shot = _make_shot("S6", "B6", duration_s=6.0, line_text="", shot_size="CU")
    assert ad.compute_pre_speech_budget(silent_shot, ["turn_pause"]).exempt_reason == "silent_shot"
    late_shot = _make_shot("S7", "B7", duration_s=7.0, line_start=1.3, line_end=4.0, shot_size="CU")
    late_plan = ad.compute_pre_speech_budget(late_shot, ["aftermath"])
    assert late_plan.applied_pause_s == pytest.approx(1.3)
    assert late_plan.window_slots[0]["end_s"] == pytest.approx(1.3)
    assert late_plan.window_slots[1]["start_s"] == pytest.approx(1.3)


def test_polish_scene_board_carries_over_state_and_handles_fault_injection_non_blocking():
    board, scene, ctx = _eight_shot_scene_board()
    seen_payloads: list[tuple[str, dict]] = []

    def _good_shot_patch(shot_row: dict, *, volume: str = "hushed") -> dict:
        tok = next(iter(shot_row["allowed_tokens"]), "{{C1}}")
        return {
            "shot_id": shot_row["shot_id"],
            "windows": [
                {
                    "start_s": slot["start_s"],
                    "end_s": slot["end_s"],
                    "action": f"{tok} holds still over the table edge",
                    "expression": (
                        "inner brows draw inward, upper eyelids narrow, nostrils flare on a slow inhale, "
                        "and jaw clenches before lips part"
                    ),
                }
                for slot in shot_row["window_slots"]
            ],
            "deliveries": [
                {
                    "line_index": 0,
                    "delivery": {
                        "loudness": volume,  # alias + synonym normalized via crew_salvage
                        "pace": "deliberate",
                        "timbre": "raspy",
                        "intonation": "low-flat",
                    },
                }
            ],
            "exit_acting_state": f"{tok}: jaw clenched, eyelids narrow, hushed-hoarse register",
        }

    def fake_llm(_system: str, payload: dict, label: str):
        seen_payloads.append((label, payload))
        # Group 1 (S1..S4):
        # - S1 valid
        # - S2 corrupted / missing windows -> retried once and succeeds on retry
        # - S3 bare emotion word -> retried once and STILL bare -> falls back cleanly (ACTING_POLISH_FALLBACK)
        # - S4 valid
        if label == "acting_group_1":
            rows = payload["shots"]
            return json.dumps({
                "shots": [
                    _good_shot_patch(rows[0], volume="quiet"),
                    {"shot_id": rows[1]["shot_id"], "windows": []},  # broken
                    {
                        "shot_id": rows[2]["shot_id"],
                        "windows": [
                            {
                                "start_s": 0.0,
                                "end_s": 3.0,
                                "action": "{{C1}} leans forward",
                                "expression": "looks very angry and shocked",  # bare emotion
                            }
                        ],
                        "deliveries": [],
                    },
                    _good_shot_patch(rows[3], volume="raised"),
                ]
            })
        if label == "acting_retry_S2":
            return _good_shot_patch(payload["shots"][0], volume="soft")
        if label == "acting_retry_S3":
            raise TimeoutError("simulated provider timeout on single-shot retry")
        # Group 2 (S5..S8): verify carry_over_state arrived from Group 1!
        if label == "acting_group_2":
            assert "jaw clenched" in payload["previous_group_carry_over_state"]
            return {"shots": [_good_shot_patch(row, volume="whisper") for row in payload["shots"]]}
        raise AssertionError(f"unexpected label {label}")

    polished, warnings, meta = ad.polish_scene_board_acting(board, scene, ctx, call_llm=fake_llm)

    # 7 of 8 shots polished cleanly; S3 degraded cleanly with ACTING_POLISH_FALLBACK.
    assert meta["groups"] == 2
    assert meta["polished_shots"] == 7
    assert meta["fallback_shots"] == 1
    assert meta["fallback_shot_ids"] == ["S3"]
    assert any(w["code"] == "ACTING_POLISH_FALLBACK" and w["shot_id"] == "S3" for w in warnings)

    # Whole polished board still passes continuity_engine with zero refusals.
    verdict = engine.review_scene_board(polished, scene, ctx)
    assert verdict.accepted, verdict.refusals

    # S1 received synonym-folded 4D VocalDelivery; S3 kept original scene_director timeline untouched.
    assert polished["shots"][0]["dialogue"][0]["delivery"] == {
        "volume": "hushed",
        "pace": "slow",
        "voice_quality": "hoarse",
        "pitch": "low_flat",
    }
    assert "delivery" not in polished["shots"][2]["dialogue"][0]
    assert polished["shots"][2]["timeline"] == board["shots"][2]["timeline"]

    # S5 (CU + turn_pause) has 1.0s pre-speech ramp compiled right before 4D acoustic dialogue in H3 2.5.0!
    s5 = polished["shots"][4]
    assert s5["dialogue"][0]["start_s"] == pytest.approx(1.0)
    spec5 = ShotSpec(
        story_beat=s5["visual_description"],
        duration_target_s=s5["duration_s"],
        style_version_id="style-1",
        characters=["林晚"],
        character_tokens=board["character_tokens"],
        camera={"composition": s5["camera"]["composition"], "movement": s5["camera"]["movement"]},
        blocking=s5["blocking"],
        environment=s5["environment"],
        timeline=s5["timeline"],
        dialogue=s5["dialogue"],
        entry_state=s5["entry_state"],
        exit_state=s5["exit_state"],
    )
    style = StyleBible(
        photography_language="Close-up natural framing",
        shot_blocking="Consistent 180-degree eyeline",
        color_palette="Muted warm tungsten",
        lighting_tone="Single table lamp",
        lens_texture="Fine natural grain",
        editing_rhythm="Breath before line",
        sound_performance="Natural Mandarin speech",
    )
    manifest = [
        ReferenceManifestItem(
            label="<Subject 1>", asset_id="a1", sha256="1" * 64, storage_key="c1.png",
            content_type="image/png", use_as=ReferenceUse.SUBJECT, entity_id="林晚", role="林晚",
        )
    ]
    compiled = H3PromptCompiler().compile(
        spec=spec5, style=style, manifest=manifest, project_language="Chinese", mode_hint=GenerationMode.REF2VA
    )
    desc = compiled.sections["detailed_description"]
    assert desc.index("Space:") < desc.index("From 0.0s to 1.0s") < desc.index(
        "At 00:01.000, <Subject 1> (S1), in a whispered, slow, hoarse voice with a low, flat pitch, speaks:"
    )
    assert not review_h3_prompt(
        compiled.prompt, compiled.sections, mode="Ref2VA", dialogue=spec5.dialogue, duration_s=spec5.duration_target_s
    )


def test_polish_single_shot_spec_works_and_degrades_gracefully():
    raw_spec = {
        "story_beat": "{{C1}} confronts the guest.",
        "duration_target_s": 6.0,
        "style_version_id": "style-1",
        "characters": ["林晚"],
        "character_tokens": {"{{C1}}": "林晚"},
        "camera": {
            "shot_size": "CU",
            "composition": "Tight close-up on {{C1}}.",
            "movement": "The camera holds still.",
        },
        "dialogue": [{"speaker_id": "林晚", "text": "你终于来了。", "start_s": 0.2, "end_s": 2.0}],
        "timeline": [
            {
                "start_s": 0.0, "end_s": 3.0,
                "action": "{{C1}} sits still", "expression": "steady gaze", "camera": "Static",
            },
            {
                "start_s": 3.0, "end_s": 6.0,
                "action": "{{C1}} breathes out", "expression": "lips close", "camera": "Static",
            },
        ],
    }

    def ok_llm(_sys, payload, _lbl):
        s = payload["shots"][0]
        return {
            "shots": [{
                "shot_id": s["shot_id"],
                "windows": [
                    {
                        "start_s": w["start_s"],
                        "end_s": w["end_s"],
                        "action": "{{C1}} keeps shoulders motionless",
                        "expression": "eyelids lower halfway, jaw tightens, throat swallows once before lips part",
                    }
                    for w in s["window_slots"]
                ],
                "deliveries": [
                    {
                        "line_index": 0,
                        "delivery": {
                            "volume": "hushed", "pace": "slow",
                            "voice_quality": "hoarse", "pitch": "falling",
                        },
                    }
                ],
                "exit_acting_state": "{{C1}}: still gaze, hushed-falling",
            }]
        }

    merged, warnings, meta = ad.polish_single_shot_spec(
        raw_spec, shot_id="shot-42", call_llm=ok_llm, beat_energies=["turn_pause"]
    )
    assert meta["polished"] is True
    assert not warnings
    validated = ShotSpec.model_validate(merged)
    assert validated.dialogue[0].start_s == pytest.approx(1.0)
    assert validated.dialogue[0].delivery is not None
    assert validated.dialogue[0].delivery.pitch == "falling"

    # Faulty LLM falls back cleanly without mutating the shot spec.
    def broken_llm(_sys, _payload, _lbl):
        raise RuntimeError("network_down")

    kept, fb_warnings, fb_meta = ad.polish_single_shot_spec(raw_spec, shot_id="shot-42", call_llm=broken_llm)
    assert fb_meta["polished"] is False and fb_meta["fallback"] is True
    assert fb_warnings[0]["code"] == "ACTING_POLISH_FALLBACK"
    assert kept == raw_spec


@pytest.mark.integration
def test_repolish_shot_acting_api_bumps_revision_and_persists_delivery(api):
    client, _factory, settings = api
    from shine.api_common import llm_gateway
    from shine.llm.gateway import LLMGateway
    from shine.llm.secrets import SecretStore
    from tests.test_film_crew import prepare

    project, style, _profile = prepare(client)
    style_id = style["id"]
    shot_res = client.post(
        f"/api/v1/projects/{project}/shots",
        json={
            "name": "S1",
            "spec": {
                "story_beat": "{{C1}} speaks quietly across the counter.",
                "duration_target_s": 6.0,
                "style_version_id": style_id,
                "characters": ["林晚"],
                "camera": {
                    "shot_size": "CU",
                    "composition": "Tight close-up on {{C1}}.",
                    "movement": "The camera holds still.",
                },
                "dialogue": [{"speaker_id": "林晚", "text": "你终于来了。", "start_s": 0.2, "end_s": 2.0}],
                "character_tokens": {"{{C1}}": "林晚"},
            },
            "bindings": [],
        },
    )
    assert shot_res.status_code == 201, shot_res.text
    shot = shot_res.json()
    shot_id = shot["id"]
    assert shot["revision"] == 1

    def mock_responder(_messages, _schema):
        return {
            "shots": [{
                "shot_id": shot_id,
                "windows": [
                    {
                        "start_s": 0.0,
                        "end_s": 1.0,
                        "action": "{{C1}} keeps shoulders motionless over the wooden counter",
                        "expression": (
                            "inner brows draw slightly inward, eyelids lower, nostrils widen before lips part"
                        ),
                    },
                    {
                        "start_s": 1.0,
                        "end_s": 3.1,
                        "action": "{{C1}} holds gaze steadily forward",
                        "expression": "jaw stays set while mouth corners tighten after the final syllable",
                    },
                    {
                        "start_s": 3.1,
                        "end_s": 6.0,
                        "action": "{{C1}} exhales slowly without blinking",
                        "expression": "lower lip presses flat as throat swallows once",
                    },
                ],
                "deliveries": [
                    {
                        "line_index": 0,
                        "delivery": {
                            "volume": "hushed", "pace": "slow",
                            "voice_quality": "hoarse", "pitch": "low_flat",
                        },
                    }
                ],
                "exit_acting_state": "{{C1}}: still gaze, hushed-hoarse",
            }]
        }

    gw = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=mock_responder)
    client.app.dependency_overrides[llm_gateway] = lambda: gw
    try:
        res = client.post(
            f"/api/v1/shots/{shot_id}/repolish-acting",
            json={"beat_energies": ["turn_pause"]},
            headers={"x-shine-local-action": "1"},
        )
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["polished"] is True
        assert data["revision"] == 2
        assert data["spec"]["dialogue"][0]["start_s"] == pytest.approx(1.0)
        assert data["spec"]["dialogue"][0]["delivery"] == {
            "volume": "hushed",
            "pace": "slow",
            "voice_quality": "hoarse",
            "pitch": "low_flat",
        }
    finally:
        client.app.dependency_overrides.pop(llm_gateway, None)

