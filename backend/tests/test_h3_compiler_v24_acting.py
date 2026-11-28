"""Phase 1 regression tests: 4D vocal delivery, crew_salvage normalization,
H3PromptCompiler 2.5.0 acting interleaving (with 2.3.0 / 2.4.0 byte-identical replay),
and simplified ±10s episode tolerance with 10s per-shot ceiling."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from shine.agents.crew_salvage import normalize_vocal_delivery, parse_with_salvage
from shine.agents.scene_director import scene_director_prompt
from shine.api_streamline import StoryboardBookBody
from shine.director.contracts import DEFAULT_TOLERANCE_SECONDS, duration_target_verdict
from shine.h3.compiler import (
    COMPILER_VERSION,
    LEGACY_ACTING_V25_COMPILER_VERSION,
    LEGACY_CONTINUITY_COLOR_V24_COMPILER_VERSION,
    LEGACY_HANDOFF_V23_COMPILER_VERSION,
    SKILL_SHA256,
    H3PromptCompiler,
)
from shine.h3.rulebook import (
    PACK_ID,
    H3RulebookRegistry,
    RulePack,
    builtin_manifest,
    digest,
    recompile_for_provenance,
)
from shine.h3.spec_review import review_h3_prompt
from shine.manual4.h3.spec_review import review_h3_prompt as manual4_review_h3_prompt
from shine.schemas import (
    DEFAULT_MAX_SHOT_SECONDS,
    DialogueLine,
    GenerationMode,
    ReferenceManifestItem,
    ReferenceUse,
    ShotEnvironment,
    ShotSpec,
    StyleBible,
    TemporalBeat,
    VocalDelivery,
)

pytestmark = pytest.mark.unit

EN_STYLE = StyleBible(
    photography_language="Eye-level close-up, restrained composition",
    shot_blocking="Hold eyeline across the counter",
    color_palette="Muted slate shadows with warm tungsten highlights",
    lighting_tone="Single practical lamp with deep shadow falloff",
    lens_texture="Shallow depth of field with fine natural grain",
    editing_rhythm="Let micro-expressions build before cutting",
    sound_performance="Natural Mandarin dialogue over quiet room tone",
)


def _manifest() -> list[ReferenceManifestItem]:
    return [
        ReferenceManifestItem(
            label="<Subject 1>",
            asset_id="asset-char-1",
            sha256="1" * 64,
            storage_key="assets/char1.png",
            content_type="image/png",
            use_as=ReferenceUse.SUBJECT,
            entity_id="林晚",
            role="林晚",
            asset_role="character",
        ),
        ReferenceManifestItem(
            label="<Picture 2>",
            asset_id="asset-scene-1",
            sha256="2" * 64,
            storage_key="assets/scene1.png",
            content_type="image/png",
            use_as=ReferenceUse.PICTURE,
            entity_id="旧茶馆",
            asset_role="scene",
        ),
    ]


def _acting_shot(*, with_delivery: bool = True, with_dialogue: bool = True) -> ShotSpec:
    delivery = (
        VocalDelivery(volume="hushed", pace="slow", voice_quality="hoarse", pitch="low_flat")
        if with_delivery
        else None
    )
    dialogue = (
        [
            DialogueLine(
                speaker_id="林晚",
                text="你真以为我不敢？",
                start_s=1.0,
                end_s=3.4,
                delivery=delivery,
            )
        ]
        if with_dialogue
        else []
    )
    return ShotSpec(
        story_beat="{{C1}} confronts the visitor across the table.",
        duration_target_s=6.0,
        style_version_id="style-v1",
        characters=["林晚"],
        character_tokens={"{{C1}}": "林晚"},
        camera={"composition": "Tight close-up on {{C1}}.", "movement": "The camera holds still."},
        sound={"environment": "rain-streaked teahouse interior", "music_policy": "none"},
        environment=ShotEnvironment(
            depth="Foreground wooden table edge, midground {{C1}}, shadowed lattice screen behind.",
            light="Warm amber table lamp on left cheek, cool blue rain glare behind.",
            micro_motion="Steam curls gently from a porcelain cup.",
        ),
        locked_story_facts=["Era: late autumn night."],
        prompt_story_facts=["Era: late autumn night."],
        timeline=[
            TemporalBeat(
                start_s=0.0,
                end_s=1.0,
                action="{{C1}} holds still over the table",
                expression=(
                    "inner brows draw tightly inward, upper eyelids narrow, nostrils flare on a short inhale, "
                    "and jaw muscles clench before the lips part slightly"
                ),
                camera="Camera stays locked",
            ),
            TemporalBeat(
                start_s=1.0,
                end_s=3.8,
                action="{{C1}} keeps gaze locked forward",
                expression="corner of the mouth tightens and throat swallows once after the final syllable",
                camera="Camera holds tight on the eyes",
            ),
            TemporalBeat(
                start_s=3.8,
                end_s=6.0,
                action="{{C1}} lowers her chin half an inch without blinking",
                expression="lower lip presses flat against the upper lip as breath stills",
                camera="Camera holds steady",
            ),
        ],
        dialogue=dialogue,
        entry_state="{{C1}} sits rigid across the wooden table.",
        exit_state="{{C1}} holds an unbroken stare in silence.",
    )


def test_dialogue_line_omits_empty_delivery_for_zero_hash_drift():
    plain = DialogueLine(speaker_id="林晚", text="走吧。", start_s=0.5, end_s=2.0)
    empty = DialogueLine(
        speaker_id="林晚", text="走吧。", start_s=0.5, end_s=2.0, delivery=VocalDelivery()
    )
    assert plain.model_dump(mode="json") == {"speaker_id": "林晚", "text": "走吧。", "start_s": 0.5, "end_s": 2.0}
    assert empty.model_dump(mode="json") == plain.model_dump(mode="json")
    h1 = hashlib.sha256(json.dumps(plain.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()
    h2 = hashlib.sha256(json.dumps(empty.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()
    assert h1 == h2

    populated = DialogueLine(
        speaker_id="林晚",
        text="走吧。",
        start_s=0.5,
        end_s=2.0,
        delivery=VocalDelivery(volume="hushed", pitch="low_flat"),
    )
    dumped = populated.model_dump(mode="json")
    assert dumped["delivery"] == {"volume": "hushed", "pitch": "low_flat"}


def test_normalize_vocal_delivery_aliases_synonyms_and_soft_fallback():
    cleaned, notes = normalize_vocal_delivery({
        "Loudness": "Quiet",
        "speed": "hesitant",
        "timbre": "Raspy",
        "intonation": "Low-Flat",
    })
    assert cleaned == {
        "volume": "hushed",
        "pace": "halting",
        "voice_quality": "hoarse",
        "pitch": "low_flat",
    }
    assert len(notes) == 4

    # Single unknown dimension drops only itself without raising or dropping valid dimensions.
    partial, partial_notes = normalize_vocal_delivery({
        "volume": "yelling",
        "pace": "totally_invented_word",
        "pitch": "questioning",
        "unknown_extra": "foo",
    })
    assert partial == {"volume": "shouting", "pitch": "rising"}
    assert any("unknown_value_dropped" in n for n in partial_notes)
    assert any("unknown_dimension_dropped" in n for n in partial_notes)

    # All invalid -> returns None cleanly.
    none_val, none_notes = normalize_vocal_delivery({"volume": "galaxy_brain"})
    assert none_val is None
    assert none_notes

    # Works transparently through parse_with_salvage on DialogueLine.
    line, salvage_notes = parse_with_salvage(
        {
            "speaker": "林晚",
            "line": "别动。",
            "start": "1.0",
            "end_s": 2.5,
            "delivery": {"loudness": "muted", "speed": "deliberate", "timbre": "bogus_word", "tone": "dropping"},
        },
        DialogueLine,
    )
    assert isinstance(line, DialogueLine)
    assert line.delivery is not None
    assert line.delivery.volume == "hushed"
    assert line.delivery.pace == "slow"
    assert line.delivery.voice_quality is None
    assert line.delivery.pitch == "falling"
    assert salvage_notes


def test_v25_interleaves_environment_pre_speech_delivery_and_continuation():
    compiler = H3PromptCompiler()
    shot = _acting_shot(with_delivery=True, with_dialogue=True)
    manifest = _manifest()

    compiled = compiler.compile(
        spec=shot,
        style=EN_STYLE,
        manifest=manifest,
        project_language="Chinese",
        mode_hint=GenerationMode.REF2VA,
    )
    assert compiled.compiler_version == COMPILER_VERSION == "shine-h3-compiler/2.6.0"
    desc = compiled.sections["detailed_description"]

    # 1. Static environment and background story facts are fronted BEFORE timeline & dialogue.
    env_pos = desc.index("The scene is set in rain-streaked teahouse interior.")
    space_pos = desc.index("Space: Foreground wooden table edge")
    facts_pos = desc.index("Background story facts, for the picture only and never heard:")
    pre_speech_pos = desc.index("From 0.0s to 1.0s, <Subject 1> holds still")
    dialogue_pos = desc.index(
        "At 00:01.000, <Subject 1> (S1), in a hushed, slow, hoarse voice with a low, flat pitch, speaks: "
        "<d>[Chinese] 你真以为我不敢？</d>"
    )
    cont_pos = desc.index("From 1.0s to 3.8s, <Subject 1> keeps gaze locked forward")
    tail_pos = desc.index("From 3.8s to 6.0s, <Subject 1> lowers her chin")

    assert env_pos < space_pos < facts_pos < pre_speech_pos < dialogue_pos < cont_pos < tail_pos

    # 2. Both spec_review and manual4 spec_review and H3RulebookRegistry accept 2.5.0 with zero H3_TIMING false alarms.
    findings = review_h3_prompt(
        compiled.prompt,
        compiled.sections,
        mode="Ref2VA",
        dialogue=shot.dialogue,
        duration_s=shot.duration_target_s,
        defined_labels={"<Subject 1>", "<Picture 2>", "<Picture 1>"},
    )
    assert not findings, findings

    m4_findings = manual4_review_h3_prompt(
        compiled.prompt,
        mode="Ref2VA",
        dialogue=[{"speaker": "林晚", "text": "你真以为我不敢？"}],
        duration_s=shot.duration_target_s,
        defined_labels={"<Subject 1>", "<Picture 2>", "<Picture 1>"},
    )
    assert not m4_findings, m4_findings

    pack = RulePack(id=PACK_ID, manifest=builtin_manifest(), sha256=digest(builtin_manifest()))
    req = SimpleNamespace(
        compiler_version=COMPILER_VERSION,
        shot_spec=shot,
        style_snapshot=EN_STYLE,
        reference_manifest=manifest,
        generation_mode=compiled.mode,
        project_language="Chinese",
        compiled_prompt=compiled.prompt,
        prompt_sha256=compiled.prompt_sha256,
        compiled_prompt_sections=compiled.sections,
        manual_prompt=False,
        rulepack_id=PACK_ID,
        rulepack_hash=pack.sha256,
        rule_version=compiled.rule_version,
        prompt_skill_hash=SKILL_SHA256,
        style_version_id=shot.style_version_id,
        style_hash=digest(EN_STYLE.model_dump(mode="json")),
        expected_duration_s=shot.duration_target_s,
    )
    report = H3RulebookRegistry().validate(req, pack)
    assert report.passed, report.violations


def test_v25_partial_delivery_empty_delivery_and_silent_shot_isolation():
    compiler = H3PromptCompiler()
    manifest = _manifest()

    # Pitch-only delivery uses "with a cracking pitch, speaks:".
    pitch_only = _acting_shot(with_delivery=False, with_dialogue=True)
    pitch_only.dialogue[0].delivery = VocalDelivery(pitch="cracking")
    c_pitch = compiler.compile(spec=pitch_only, style=EN_STYLE, manifest=manifest, project_language="Chinese")
    expected_pitch = (
        "At 00:01.000, <Subject 1> (S1), with a cracking pitch, speaks: "
        "<d>[Chinese] 你真以为我不敢？</d>"
    )
    assert expected_pitch in c_pitch.prompt
    assert not review_h3_prompt(
        c_pitch.prompt, c_pitch.sections, mode="Ref2VA", dialogue=pitch_only.dialogue, duration_s=6.0
    )

    # No delivery falls back to clean "says:".
    no_delivery = _acting_shot(with_delivery=False, with_dialogue=True)
    c_plain = compiler.compile(spec=no_delivery, style=EN_STYLE, manifest=manifest, project_language="Chinese")
    assert "At 00:01.000, <Subject 1> (S1) says: <d>[Chinese] 你真以为我不敢？</d>" in c_plain.prompt

    # Silent shot never leaks vocal delivery phrasing or "speaks:".
    silent = _acting_shot(with_delivery=False, with_dialogue=False)
    c_silent = compiler.compile(spec=silent, style=EN_STYLE, manifest=manifest, project_language="Chinese")
    assert "speaks:" not in c_silent.prompt
    assert "voice with" not in c_silent.prompt
    assert "<d>" not in c_silent.prompt


def test_v230_and_v240_replay_byte_identically_under_v250_compiler():
    compiler = H3PromptCompiler()
    shot = _acting_shot(with_delivery=False, with_dialogue=True)
    manifest = _manifest()

    c230 = compiler.compile(
        spec=shot, style=EN_STYLE, manifest=manifest, project_language="Chinese",
        compiler_version=LEGACY_HANDOFF_V23_COMPILER_VERSION,
    )
    c240 = compiler.compile(
        spec=shot, style=EN_STYLE, manifest=manifest, project_language="Chinese",
        compiler_version=LEGACY_CONTINUITY_COLOR_V24_COMPILER_VERSION,
    )
    c250 = compiler.compile(
        spec=shot, style=EN_STYLE, manifest=manifest, project_language="Chinese",
        compiler_version=LEGACY_ACTING_V25_COMPILER_VERSION,
    )
    c260 = compiler.compile(spec=shot, style=EN_STYLE, manifest=manifest, project_language="Chinese")

    # 2.3.0 & 2.4.0 keep timeline BEFORE environment BEFORE dialogue.
    for legacy in (c230, c240):
        d = legacy.sections["detailed_description"]
        assert d.index("From 0.0s to 1.0s") < d.index("Space:") < d.index("At 00:01.000")
    # 2.5.0 moves environment BEFORE timeline & interleaves dialogue; 2.6.0 omits pre-timeline story_beat spoiler.
    assert c250.prompt_sha256 != c240.prompt_sha256
    assert shot.story_beat.replace("{{C1}}", "<Subject 1>") in c250.sections["detailed_description"]
    assert shot.story_beat.replace("{{C1}}", "<Subject 1>") not in c260.sections["detailed_description"]

    for stored in (c230, c240, c250, c260):
        req = SimpleNamespace(
            compiler_version=stored.compiler_version,
            shot_spec=shot,
            style_snapshot=EN_STYLE,
            reference_manifest=manifest,
            generation_mode=stored.mode,
            project_language="Chinese",
            compiled_prompt=stored.prompt,
            prompt_sha256=stored.prompt_sha256,
            compiled_prompt_sections=stored.sections,
        )
        replayed, variant = recompile_for_provenance(req)
        assert variant is None
        assert replayed.prompt == stored.prompt
        assert replayed.prompt_sha256 == stored.prompt_sha256


def test_duration_tolerance_simplified_to_plus_minus_10s_and_10s_shot_ceiling():
    assert DEFAULT_TOLERANCE_SECONDS == 10
    assert DEFAULT_MAX_SHOT_SECONDS == 10.0
    body = StoryboardBookBody(episode_count=1, target_seconds=60)
    assert body.tolerance_seconds == 10
    assert body.max_shot_seconds == 10.0

    # Within [target - 10, target + 10] -> None.
    assert duration_target_verdict(50.0, 60.0, 10.0) is None
    assert duration_target_verdict(70.0, 60.0, 10.0) is None
    # Clamps lower window bound at H3's 4.0s floor.
    assert duration_target_verdict(4.0, 8.0, 10.0) is None
    low = duration_target_verdict(3.5, 8.0, 10.0)
    assert low is not None and low["window_low_s"] == 4.0
    # Outside ±10s -> soft named deviation.
    high = duration_target_verdict(72.0, 60.0, 10.0)
    assert high is not None and high["code"] == "DURATION_TARGET_DEVIATION"

    # Scene director prompt frames scene_budget_seconds as soft ±10s target.
    prompt = scene_director_prompt(
        max_shot_seconds=10.0,
        continuity_predecessor=False,
        translation_supplied=True,
        narration_allowed=False,
        scene_budget_seconds=60.0,
    )
    assert "SOFT target" in prompt
    assert "±10" in prompt
    assert "4 to 10s" in prompt


def test_v26_resolves_blocking_tokens_omits_duplicate_story_beat_and_anchors_last_frame():
    compiler = H3PromptCompiler()
    shot = _acting_shot(with_delivery=False, with_dialogue=False)
    shot = ShotSpec.model_validate({
        **shot.model_dump(mode="json"),
        "story_beat": "{{C2}} withdraws a rusted silver key and slides it beside the watch.",
        "characters": ["林晚", "陈默"],
        "character_tokens": {"{{C1}}": "林晚", "{{C2}}": "陈默"},
        "blocking": {
            "axis": "counter",
            "placements": [
                {
                    "character": "林晚",
                    "zone": "left_mg",
                    "facing": "facing_right",
                    "posture": "standing behind the wooden counter, gaze fixed on {{C2}}",
                    "props": ["velvet cloth"],
                },
                {
                    "character": "陈默",
                    "zone": "right_mg",
                    "facing": "facing_left",
                    "posture": "standing in front of the wooden counter, watching {{C1}}",
                    "props": [],
                },
            ],
        },
        "continuity": {"mode": "last_frame", "previous_shot_id": "prev-s5"},
    })
    manifest = [
        ReferenceManifestItem(
            label="<Subject 1>", asset_id="asset-char-1", use_as=ReferenceUse.SUBJECT,
            role="林晚", entity_id="林晚", asset_role="character",
            sha256="a" * 64, storage_key="char-1.png", content_type="image/png",
        ),
        ReferenceManifestItem(
            label="<Subject 2>", asset_id="asset-char-2", use_as=ReferenceUse.SUBJECT,
            role="陈默", entity_id="陈默", asset_role="character",
            sha256="b" * 64, storage_key="char-2.png", content_type="image/png",
        ),
        ReferenceManifestItem(
            label="<Picture 3>", asset_id="asset-scene-1", use_as=ReferenceUse.PICTURE,
            role="scene", entity_id="雨夜钟表铺", asset_role="scene",
            sha256="c" * 64, storage_key="scene-1.png", content_type="image/png",
        ),
        ReferenceManifestItem(
            label="<Picture 4>", asset_id="asset-prev-last", use_as=ReferenceUse.PICTURE,
            role="continuity_derivative", entity_id="prev-s5", asset_role="continuity_derivative",
            sha256="d" * 64, storage_key="prev-s5.png", content_type="image/png",
        ),
    ]

    c250 = compiler.compile(
        spec=shot, style=EN_STYLE, manifest=manifest, project_language="Chinese",
        compiler_version=LEGACY_ACTING_V25_COMPILER_VERSION,
    )
    c260 = compiler.compile(spec=shot, style=EN_STYLE, manifest=manifest, project_language="Chinese")

    # 2.5.0 kept the historical literal token & story_beat spoiler (byte-identical replay).
    assert "gaze fixed on {{C2}}" in c250.sections["detailed_description"]
    assert "<Subject 2> withdraws a rusted silver key" in c250.sections["detailed_description"]

    # 2.6.0 resolves {{Ck}} inside blocking posture, omits pre-timeline story_beat spoiler,
    # and strengthens last_frame spatial-side & visible-prop-motion anchor.
    desc260 = c260.sections["detailed_description"]
    assert "{{" not in desc260
    assert "gaze fixed on <Subject 2>" in desc260
    assert "watching <Subject 1>" in desc260
    assert "withdraws a rusted silver key" not in desc260
    assert "same physical side of fixed set landmarks" in desc260
    assert "never materialize an unseen prop without a visible continuous motion" in desc260

    # When timeline is empty, 2.6.0 still emits story_beat so timeline-less shots stay intact.
    no_tl = shot.model_copy(update={"timeline": []})
    c260_no_tl = compiler.compile(spec=no_tl, style=EN_STYLE, manifest=manifest, project_language="Chinese")
    assert "<Subject 2> withdraws a rusted silver key" in c260_no_tl.sections["detailed_description"]

