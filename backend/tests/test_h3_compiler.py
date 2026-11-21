from __future__ import annotations

import pytest

from shine.h3.compiler import H3PromptCompiler, PromptCompilationError
from shine.schemas import (
    FilmType,
    FrameRole,
    GenerationMode,
    ReferenceManifestItem,
    ReferenceUse,
    ShotSpec,
    StyleBible,
)
from tests.conftest import create_project, create_shot, freeze_style, shot_payload

pytestmark = pytest.mark.unit

STYLE = StyleBible(
    photography_language="平视中近景，稳定缓推",
    shot_blocking="对话保持视线轴线",
    color_palette="青灰环境与温暖肤色",
    lighting_tone="单侧主光，保留阴影层次",
    lens_texture="自然浅景深，轻颗粒",
    editing_rhythm="对白停顿后切反应镜头",
    sound_performance="普通话自然语速，环境声克制",
)


def item(
    label: str,
    use_as: ReferenceUse,
    *,
    asset_id: str = "asset-1",
    content_type: str | None = None,
    frame_role: FrameRole | None = None,
    duration_s: float | None = None,
    entity_id: str | None = None,
    role: str | None = None,
) -> ReferenceManifestItem:
    default_type = {
        ReferenceUse.SUBJECT: "image/png",
        ReferenceUse.PICTURE: "image/png",
        ReferenceUse.VIDEO: "video/mp4",
        ReferenceUse.AUDIO: "audio/wav",
    }[use_as]
    return ReferenceManifestItem(
        label=label,
        asset_id=asset_id,
        sha256="a" * 64,
        storage_key=f"assets/{asset_id}",
        content_type=content_type or default_type,
        use_as=use_as,
        frame_role=frame_role,
        duration_s=duration_s,
        entity_id=entity_id,
        role=role,
    )


def spec(**overrides) -> ShotSpec:
    payload = {
        "story_beat": "两人交换线索，气氛紧张",
        "duration_target_s": 6,
        "style_version_id": "style-v1",
        "camera": {"shot_size": "medium_two_shot", "movement": "slow_push"},
        "characters": [],
        "dialogue": [],
        "sound": {"environment": "夜雨中的旧码头", "music_policy": "low_or_none"},
    }
    payload.update(overrides)
    return ShotSpec(**payload)


def compile_prompt(manifest, *, shot=None, mode=None, project_language="Chinese"):
    return H3PromptCompiler().compile(
        spec=shot or spec(),
        style=STYLE,
        manifest=manifest,
        project_language=project_language,
        mode_hint=mode,
    )


def test_t2va_uses_the_three_official_fields_in_order():
    compiled = compile_prompt([])
    assert compiled.mode == GenerationMode.T2VA
    assert list(compiled.sections) == [
        "integrated_multimodal_description",
        "overall_soundscape",
        "non_diegetic_music",
    ]
    assert compiled.prompt.startswith("integrated_multimodal_description: [Shot 1]")
    assert "Visual style constraints from the frozen Style Bible (version style-v1)" in compiled.prompt
    assert "夜雨中的旧码头" in compiled.sections["overall_soundscape"]
    assert compiled.sections["non_diegetic_music"].startswith("Sparse low strings")
    assert len(compiled.prompt_sha256) == 64


def test_the_prompt_states_the_spoken_language_contract():
    """The prompt anchors every voice to the project language and describes a
    non-vocal soundtrack for a silent shot.

    The whole prompt is written in English.  Without an explicit instruction the
    model invents English speech, and it invents speech at all in a silent shot.
    The contract is a positive sound description: enumerating forbidden vocal
    acts primed the model to render them (unscripted foreign-language narration).
    """
    silent = compile_prompt([])
    assert "This shot has no dialogue: the soundtrack is entirely non-vocal" in silent.sections["overall_soundscape"]

    spoken = spec(
        characters=["R01 杜万山"],
        dialogue=[
            {"speaker_id": "R01 杜万山", "text": "你来了。", "start_s": 1.0, "end_s": 2.4},
        ],
    )
    manifest = [
        item("<Subject 1>", ReferenceUse.SUBJECT, entity_id="R01 杜万山", role="character"),
        item("<Picture 2>", ReferenceUse.PICTURE, asset_id="asset-2"),
    ]
    compiled = compile_prompt(manifest, shot=spoken)
    soundscape = compiled.sections["overall_soundscape"]
    assert "Every voice in this shot speaks Chinese" in soundscape
    assert "<d>[Chinese] 你来了。</d>" in compiled.prompt
    # The spoken-language instruction is English prose: the H3 rulebook only
    # forbids CJK outside the dialogue itself.
    assert "Every voice in this shot speaks Chinese" in compiled.prompt


def test_ref2va_references_and_music_cannot_instruct_speech_carryover():
    shot = spec(continuity={"mode": "audio_reference", "previous_shot_id": "source"})
    manifest = [
        item("<Video 1>", ReferenceUse.VIDEO, duration_s=4),
        item("<Audio 1>", ReferenceUse.AUDIO, asset_id="source-audio", duration_s=4, role="continuity_derivative"),
    ]
    compiled = compile_prompt(manifest, shot=shot)
    assert "Performers act with closed mouths" in compiled.sections["detailed_description"]
    assert "Use <Audio 1> for non-vocal ambience only" in compiled.sections["detailed_description"]
    assert "Use the visual track only" in compiled.sections["retention_analysis"]
    assert "referenced only for non-vocal ambience" in compiled.sections["retention_analysis"]
    assert "Instrumental only" in compiled.sections["non_diegetic_music"]
    assert "below the dialogue" not in compiled.sections["non_diegetic_music"]


def test_visual_text_cannot_emit_a_dialogue_block():
    shot = spec(story_beat="She opens the door. <d>[English] Who is there?</d>")
    with pytest.raises(PromptCompilationError) as error:
        compile_prompt([], shot=shot)
    assert error.value.code == "H3_DIALOGUE_TAGS"


def test_a_dialogue_free_shot_never_quotes_the_speech_style_dimension():
    """The frozen sound-performance dimension invites speech; in a silent shot
    it contradicts the no-speech contract and the model invents foreign-language
    mumbling (observed on the real 2026-11-04 journey run).  A dialogue-free
    shot must therefore not quote it, while a spoken shot still does.
    """
    continuity = "When Shen Yan says 'time will stop tonight', the night finally ends."
    silent = compile_prompt([], shot=spec(
        locked_story_facts=[continuity], prompt_story_facts=[continuity],
    ))
    assert "frozen sound rule" not in silent.sections["overall_soundscape"]
    assert "the soundtrack is entirely non-vocal" in silent.sections["overall_soundscape"]
    assert (
        "Performance and sound: this shot is performed without dialogue; "
        "its soundtrack is entirely non-vocal." in silent.prompt
    )
    assert "Story continuity facts below are silent background context for the picture only" in silent.prompt
    assert "they inform the visuals and are never heard as audio" in silent.prompt
    assert (
        "Words quoted in the story facts or visual description stay silent context and are never heard"
        in silent.prompt
    )
    assert "普通话自然语速" not in silent.prompt

    spoken = spec(
        characters=["R01 杜万山"],
        dialogue=[
            {"speaker_id": "R01 杜万山", "text": "你来了。", "start_s": 1.0, "end_s": 2.4},
        ],
    )
    manifest = [
        item("<Subject 1>", ReferenceUse.SUBJECT, entity_id="R01 杜万山", role="character"),
        item("<Picture 2>", ReferenceUse.PICTURE, asset_id="asset-2"),
    ]
    voiced = compile_prompt(manifest, shot=spoken)
    # This fixture carries its own environment line, so the soundscape section
    # never quotes the sound rule; the frozen dimension itself stays in the
    # style sentences of the prompt.
    assert "Performance and sound: 普通话自然语速" in voiced.prompt


def test_a_language_written_in_another_script_never_reaches_the_english_prompt():
    """The spoken language is stated in English, whatever the project wrote.

    The official rule pack rejects CJK outside the dialogue tags, so a project
    setting written in Chinese must be normalised instead of copied into the
    prompt.  Otherwise creating the job fails with an unrelated error.
    """
    import re

    spoken = spec(
        characters=["R01 杜万山"],
        dialogue=[
            {"speaker_id": "R01 杜万山", "text": "你来了。", "start_s": 1.0, "end_s": 2.4},
        ],
    )
    manifest = [
        item("<Subject 1>", ReferenceUse.SUBJECT, entity_id="R01 杜万山", role="character"),
        item("<Picture 2>", ReferenceUse.PICTURE, asset_id="asset-2"),
    ]
    compiled = compile_prompt(manifest, shot=spoken, project_language="中文")
    soundscape = compiled.sections["overall_soundscape"]
    contract = (
        "Every voice in this shot speaks Chinese. The only words heard are the explicitly tagged "
        "dialogue lines of this shot, delivered exactly as written at their timed positions; all "
        "other sound stays non-vocal. Any voice or reading described in the story facts "
        "or visual description refers only to these exact lines."
    )
    assert contract in soundscape
    # The contract itself is English prose, and the raw project value never
    # reaches the prompt: the rule pack rejects CJK outside the dialogue tags.
    assert contract.isascii()
    assert "中文" not in compiled.prompt
    assert not re.search(r"[\u3400-\u9fff]", contract)


def test_an_unknown_language_falls_back_to_the_default_voice():
    """A language the build cannot state must not silently disappear."""
    compiled = compile_prompt([], shot=spec(), project_language="火星语")
    assert "All spoken dialogue" not in compiled.sections["overall_soundscape"]
    assert any("cannot be written in the English prompt" in w for w in compiled.warnings)


def test_chinese_locked_facts_require_and_use_explicit_english_prompt_translation():
    original = ["怀表停在十二点"]
    shot = spec(locked_story_facts=original, prompt_story_facts=["The watch is stopped at twelve."])
    compiled = compile_prompt([], shot=shot)
    assert "Story continuity facts below are silent background context" in compiled.prompt
    assert "The watch is stopped at twelve." in compiled.prompt
    assert original == shot.locked_story_facts
    with pytest.raises(PromptCompilationError) as error:
        compile_prompt([], shot=spec(locked_story_facts=original))
    assert error.value.code == "H3_LOCKED_FACT_TRANSLATION_REQUIRED"


def test_prompt_style_and_subject_alias_preserve_original_identity():
    english = StyleBible(
        photography_language="Steady medium shots", shot_blocking="Preserve the axis",
        color_palette="Cool colors", lighting_tone="Soft side light", lens_texture="Subtle grain",
        editing_rhythm="Pause after dialogue", sound_performance="Natural Mandarin speech",
    )
    subject = item("<Subject 1>", ReferenceUse.SUBJECT, entity_id="杜万山", role="杜万山")
    compiled = compile_prompt([subject], shot=spec(prompt_style=english))
    assert "Steady medium shots" in compiled.prompt
    # The H3 prompt is English, so a Chinese name cannot be copied into it;
    # the person is identified by cast position and the asset keeps its name.
    assert "the first listed performer" in compiled.sections["subject_definitions"]
    assert "杜万山" not in compiled.prompt
    assert subject.entity_id == "杜万山"
    assert STYLE.photography_language == "平视中近景，稳定缓推"


def test_i2va_starts_with_the_official_alignment_instruction():
    manifest = [item("<Picture 1>", ReferenceUse.PICTURE, frame_role=FrameRole.FIRST)]
    compiled = compile_prompt(manifest, mode=GenerationMode.I2VA)
    first_line = compiled.prompt.splitlines()[0]
    assert first_line == (
        "For the target video, at 0.00 seconds into the target video, "
        "<Picture 1> (from [Shot 1]) is fully referenced."
    )
    assert compiled.prompt.splitlines()[1] == ""


def test_fl2va_and_l2va_alignment_instructions():
    fl = compile_prompt(
        [
            item("<Picture 1>", ReferenceUse.PICTURE, frame_role=FrameRole.FIRST),
            item("<Picture 2>", ReferenceUse.PICTURE, frame_role=FrameRole.LAST),
        ],
        mode=GenerationMode.FL2VA,
    )
    assert fl.mode == GenerationMode.FL2VA
    assert "Picture 1 (from Shot 1) aligns with the 0.00-second mark" in fl.prompt
    assert "Picture 2 (from Shot 1) aligns with the 6.00-second mark" in fl.prompt
    l2 = compile_prompt(
        [item("<Picture 1>", ReferenceUse.PICTURE, frame_role=FrameRole.LAST)],
        mode=GenerationMode.L2VA,
    )
    assert l2.mode == GenerationMode.L2VA
    assert "<Picture 1> (from [Shot 1]) aligns with the 6.00-second mark" in l2.prompt


def test_ref2va_emits_all_six_official_sections_and_stable_labels():
    manifest = [
        item("<Subject 1>", ReferenceUse.SUBJECT, entity_id="CHAR_A", role="character"),
        item("<Picture 1>", ReferenceUse.PICTURE, frame_role=FrameRole.FIRST),
        item("<Audio 1>", ReferenceUse.AUDIO, duration_s=5),
    ]
    shot = spec(
        characters=["CHAR_A"],
        dialogue=[{"speaker_id": "CHAR_A", "text": "账册在哪儿？", "start_s": 0.4, "end_s": 2.0}],
    )
    compiled = compile_prompt(manifest, shot=shot)
    assert compiled.mode == GenerationMode.REF2VA
    assert list(compiled.sections) == [
        "subject_definitions",
        "summary",
        "retention_analysis",
        "detailed_description",
        "overall_soundscape",
        "non_diegetic_music",
    ]
    assert compiled.prompt.index("subject_definitions:") < compiled.prompt.index("summary:")
    assert (
        "<Subject 1> is the approved, locked reference" in compiled.sections["subject_definitions"]
    )
    # 1.4.0: a first-frame picture is a concrete frame anchor, so the official
    # task type is keyframe completion alongside reference generation.
    assert compiled.sections["summary"].startswith(
        "[reference generation + keyframe completion + audio reference]"
    )
    assert (
        "<Subject 1> (the first listed performer in [Shot 1]): fully_preserved"
        in compiled.sections["retention_analysis"]
    )
    assert "<Picture 1> ([Shot 1] first_frame frame): fully_preserved" in compiled.sections["retention_analysis"]
    assert "<Subject 1> (S1) says: <d>[Chinese] 账册在哪儿？</d>" in compiled.sections["detailed_description"]
    assert compiled.labels["<Subject 1>"] == "asset-1"


def test_ref2va_rejects_video_references_without_measured_duration():
    manifest = [item("<Video 1>", ReferenceUse.VIDEO, duration_s=None)]
    with pytest.raises(PromptCompilationError) as exc:
        compile_prompt(manifest)
    assert exc.value.code == "H3_REFERENCE_DURATION_UNKNOWN"


def test_reference_limits_and_label_mismatch_are_blocked():
    too_many = [
        item(f"<Picture {index + 1}>", ReferenceUse.PICTURE) for index in range(10)
    ]
    with pytest.raises(PromptCompilationError) as exc:
        compile_prompt(too_many)
    assert exc.value.code == "H3_REFERENCE_LIMIT"
    with pytest.raises(PromptCompilationError) as exc:
        compile_prompt([item("<Picture 1>", ReferenceUse.AUDIO, duration_s=4)])
    assert exc.value.code == "H3_LABEL_MISMATCH"


def test_ref2va_speaker_requires_a_matching_subject_reference():
    manifest = [item("<Subject 1>", ReferenceUse.SUBJECT, entity_id="CHAR_B")]
    shot = spec(
        characters=["CHAR_A"],
        dialogue=[{"speaker_id": "CHAR_A", "text": "等等。", "start_s": 0.4, "end_s": 1.5}],
    )
    with pytest.raises(PromptCompilationError) as exc:
        compile_prompt(manifest, shot=shot)
    assert exc.value.code == "H3_SPEAKER_REFERENCE_MISSING"


def test_ref2va_speaker_matches_approved_character_view_suffix():
    manifest = [
        item("<Subject 1>", ReferenceUse.SUBJECT, entity_id="R01 杜万山胸上", role="character")
    ]
    shot = spec(
        characters=["R01 杜万山"],
        dialogue=[{"speaker_id": "R01 杜万山", "text": "还差一分钟。", "start_s": 0.4, "end_s": 1.5}],
    )

    compiled = compile_prompt(manifest, shot=shot)

    assert "<Subject 1> (S1) says:" in compiled.prompt


def test_ref2va_subject_definitions_cite_presentation_picture_slots():
    """Official Ref2VA guidance: every reference image is presented to the text
    encoder as ``<Picture i>`` in request order, and subject definitions bind
    identity by citing that presentation slot ("<Subject 1> ... in <Picture 1>").
    """
    manifest = [
        item("<Subject 1>", ReferenceUse.SUBJECT, entity_id="CHAR_A", role="character"),
        item("<Subject 2>", ReferenceUse.SUBJECT, entity_id="CHAR_B", asset_id="asset-2", role="character"),
        item("<Picture 3>", ReferenceUse.PICTURE, asset_id="asset-3", frame_role=FrameRole.KEYFRAME),
        item("<Audio 1>", ReferenceUse.AUDIO, asset_id="asset-4", duration_s=4),
    ]
    shot = spec(characters=["CHAR_A"])
    compiled = compile_prompt(manifest, shot=shot)
    definitions = compiled.sections["subject_definitions"]
    assert (
        "<Subject 1> is the approved, locked reference for the first listed performer (character), "
        "shown in <Picture 1>;"
    ) in definitions
    assert (
        "<Subject 2> is the approved, locked reference for the second listed performer (character), "
        "shown in <Picture 2>;"
    ) in definitions
    assert "<Picture 3> is the keyframe reference for [Shot 1]." in definitions
    assert "<Audio 1> is a reference for non-vocal ambience only." in definitions


def test_ref2va_presentation_indices_follow_manifest_order_not_family_order():
    # Images enter presentation in manifest order: subject, picture, subject.
    # Standalone picture labels must keep the shared image position (Picture 2),
    # matching the tags the H3 text encoder actually splices in.
    manifest = [
        item("<Subject 1>", ReferenceUse.SUBJECT, entity_id="CHAR_A"),
        item("<Picture 2>", ReferenceUse.PICTURE, asset_id="asset-2"),
        item("<Subject 2>", ReferenceUse.SUBJECT, entity_id="CHAR_B", asset_id="asset-3"),
    ]
    compiled = compile_prompt(manifest)
    definitions = compiled.sections["subject_definitions"]
    assert "shown in <Picture 1>" in definitions.splitlines()[0]
    assert "shown in <Picture 3>" in definitions.splitlines()[2]


def test_dialogue_timing_and_overlap_are_blocked():
    shot = spec(
        characters=["A", "B"],
        dialogue=[
            {"speaker_id": "A", "text": "第一句", "start_s": 0.5, "end_s": 3.0},
            {"speaker_id": "B", "text": "第二句", "start_s": 2.5, "end_s": 4.0},
        ],
    )
    manifest = [
        item("<Subject 1>", ReferenceUse.SUBJECT, entity_id="A"),
        item("<Subject 2>", ReferenceUse.SUBJECT, entity_id="B", asset_id="asset-2"),
    ]
    with pytest.raises(PromptCompilationError) as exc:
        compile_prompt(manifest, shot=shot)
    assert exc.value.code == "H3_DIALOGUE_OVERLAP"


def test_compilation_is_deterministic_and_requires_a_style_version():
    first = compile_prompt([])
    second = compile_prompt([])
    assert first.prompt_sha256 == second.prompt_sha256
    with pytest.raises(PromptCompilationError) as exc:
        compile_prompt([], shot=spec(style_version_id=""))
    assert exc.value.code == "H3_STYLE_MISSING"


def test_mode_hint_conflicting_with_references_is_rejected():
    manifest = [item("<Picture 1>", ReferenceUse.PICTURE, frame_role=FrameRole.FIRST)]
    with pytest.raises(PromptCompilationError) as exc:
        compile_prompt(manifest, mode=GenerationMode.T2VA)
    assert exc.value.code == "H3_MODE_REFERENCE_MISMATCH"


def test_film_type_writes_one_fixed_english_sentence():
    animation = STYLE.model_copy(update={"film_type": FilmType.ANIMATION})
    compiled = H3PromptCompiler().compile(spec=spec(), style=animation, manifest=[])
    assert "Film type: The entire shot is 2D cel-shaded animation" in compiled.prompt
    assert "no photorealistic skin, no live-action footage, and no 3D CGI rendering" in compiled.prompt

    live_action = STYLE.model_copy(update={"film_type": FilmType.LIVE_ACTION})
    compiled = H3PromptCompiler().compile(spec=spec(), style=live_action, manifest=[])
    assert "Film type: The entire shot is photorealistic live-action footage" in compiled.prompt
    assert "no illustration, no cel shading, and no cartoon or anime rendering" in compiled.prompt


def test_an_unset_film_type_does_not_change_the_legacy_style_hash_shape():
    assert "film_type" not in STYLE.model_dump(mode="json")
    selected = STYLE.model_copy(update={"film_type": FilmType.ANIMATION})
    assert selected.model_dump(mode="json")["film_type"] == "animation"


def test_a_per_shot_english_style_inherits_the_frozen_film_type():
    frozen = STYLE.model_copy(update={"film_type": FilmType.ANIMATION})
    shot = spec(prompt_style=STYLE)
    compiled = H3PromptCompiler().compile(spec=shot, style=frozen, manifest=[])
    assert "2D cel-shaded animation" in compiled.prompt


def test_ref2va_uses_frozen_animation_even_when_shot_projection_says_live_action():
    from shine.h3.compiler import LEGACY_STYLE_COMPILER_VERSION

    frozen = STYLE.model_copy(update={"film_type": FilmType.ANIMATION})
    per_shot = STYLE.model_copy(update={"film_type": FilmType.LIVE_ACTION})
    shot = spec(prompt_style=per_shot)
    manifest = [item("<Subject 1>", ReferenceUse.SUBJECT)]
    compiler = H3PromptCompiler()
    compiled = compiler.compile(spec=shot, style=frozen, manifest=manifest)
    assert "2D cel-shaded animation" in compiled.sections["summary"]
    assert "every frame and internal cut" in compiled.sections["summary"]
    assert "frozen medium even when the source uses another medium" in compiled.sections["summary"]
    assert "2D cel-shaded animation" in compiled.sections["detailed_description"]
    assert "The entire shot is photorealistic" not in compiled.prompt
    historical = compiler.compile(
        spec=shot, style=frozen, manifest=manifest, compiler_version=LEGACY_STYLE_COMPILER_VERSION,
    )
    assert historical.compiler_version == LEGACY_STYLE_COMPILER_VERSION
    assert "The entire shot is photorealistic" in historical.sections["detailed_description"]


def test_a_manual_shot_inherits_the_projects_english_style_prompt(api):
    client, _factory, _settings = api
    project_id = create_project(client, "手写镜头继承英文风格")
    style = freeze_style(client, project_id)
    english = {
        "photography_language": "Eye-level medium framing, slow controlled push.",
        "shot_blocking": "Single subject, diagonal composition, stable axis.",
        "color_palette": "Cool low-saturation cyan-blue and gray-green.",
        "lighting_tone": "Low-key side and rim light, readable shadows.",
        "lens_texture": "Clean realistic texture, moderate depth of field.",
        "editing_rhythm": "Restrained suspense rhythm, complete shots, hard cuts.",
        "sound_performance": "Restrained rain ambience, low music bed, no dialogue.",
    }
    template = shot_payload(style["id"], title="AI 英文风格模板")
    template["spec"]["prompt_style"] = english
    assert client.post(f"/api/v1/projects/{project_id}/shots", json=template).status_code == 201
    manual = create_shot(client, project_id, style["id"], title="手写干净镜头")
    assert manual["spec"]["prompt_style"] == english


# --- 1.3.0 positive audio contract -------------------------------------------
#
# The pre-1.3.0 fixed sentences enumerated forbidden vocal acts ("narration,
# voice-over, whispering, mumbling, singing, humming...").  A video model reads
# that enumeration as concepts to render, and real runs produced unscripted
# foreign-language narration from it.  The contract now describes what the
# soundtrack contains and anchors every voice to the project language.

#: Vocal-act vocabulary that must never appear in the fixed sentences of a
#: non-narrated shot.  A narrated shot legitimately names its narrator.
_PRIMING_VOCABULARY = (
    "voice-over", "whispering", "mumbling", "singing", "humming",
    "background chatter", "vocals", "lyrics", "speech",
)


def test_the_fixed_sentences_never_enumerate_vocal_acts():
    silent = compile_prompt([], shot=spec(sound={}))
    assert (
        "Natural room ambience continues throughout the video; the soundtrack stays entirely non-vocal."
        in silent.sections["overall_soundscape"]
    )
    for token in (*_PRIMING_VOCABULARY, "narration"):
        assert token not in silent.prompt.lower(), token

    spoken = spec(
        characters=["R01 杜万山"],
        dialogue=[{"speaker_id": "R01 杜万山", "text": "你来了。", "start_s": 1.0, "end_s": 2.4}],
        sound={},
    )
    manifest = [
        item("<Subject 1>", ReferenceUse.SUBJECT, entity_id="R01 杜万山", role="character"),
        item("<Audio 1>", ReferenceUse.AUDIO, asset_id="voice-1", duration_s=4),
    ]
    voiced = compile_prompt(manifest, shot=spoken)
    for token in (*_PRIMING_VOCABULARY, "narration"):
        assert token not in voiced.prompt.lower(), token
    # The positive anchors are present instead.
    assert "Every voice in this shot speaks Chinese" in voiced.sections["overall_soundscape"]
    assert "the scripted lines' voice timbre" in voiced.sections["subject_definitions"]
    assert "the approved timbre voices only the scripted dialogue lines" in voiced.sections["retention_analysis"]


def test_a_narrated_shot_keeps_its_narrator_and_gains_the_language_anchor():
    shot = spec(
        characters=["旁白"],
        dialogue=[{"speaker_id": "旁白", "text": "夜幕降临。", "start_s": 0.5, "end_s": 2.5}],
        sound={},
    )
    manifest = [item("<Picture 1>", ReferenceUse.PICTURE, asset_id="asset-2", frame_role=FrameRole.KEYFRAME)]
    compiled = compile_prompt(manifest, shot=shot)
    assert "off-screen narrator" in compiled.prompt
    assert "Every voice in this shot speaks Chinese" in compiled.sections["overall_soundscape"]
    assert "<d>[Chinese] 夜幕降临。</d>" in compiled.prompt
    for token in _PRIMING_VOCABULARY:
        assert token not in compiled.prompt.lower(), token


def test_the_music_section_is_instrumental_without_naming_vocals():
    compiled = compile_prompt([])
    music = compiled.sections["non_diegetic_music"]
    assert music.endswith("Instrumental only.")
    assert "vocals" not in music and "lyrics" not in music


#: Prompt hashes captured from the pre-1.3.0 compiler on the fixtures below.
#: Stored requests pin their compiler version; recompiling them must reproduce
#: exactly these bytes or their provenance verification would fail.
_LEGACY_PROMPT_HASHES = {
    "silent_t2va_with_facts": {
        "shine-h3-compiler/1.2.1": "c57e82e84ff9a7167376d0d5cbeaa22e6b631e17aadbc3598e31ce672fea7775",
        "shine-h3-compiler/1.2.2": "37a11b34133832c65f08c25ce355950c549e4bc56bd8ee776f006c16bcd2aea2",
        "shine-h3-compiler/1.2.3": "37a11b34133832c65f08c25ce355950c549e4bc56bd8ee776f006c16bcd2aea2",
    },
    "dialogue_ref2va_voice_master": {
        "shine-h3-compiler/1.2.1": "3cdd5ccae00c0216ac092723db2fc9879694dc98a17e708480b02b6265e22ada",
        "shine-h3-compiler/1.2.2": "bcc60aa8d4f8f4a9d5777e9e1178d7035b225ed30f11428fbf9a5529bfaf36f5",
        "shine-h3-compiler/1.2.3": "bcc60aa8d4f8f4a9d5777e9e1178d7035b225ed30f11428fbf9a5529bfaf36f5",
    },
    "silent_ref2va_continuity_audio": {
        "shine-h3-compiler/1.2.1": "91553d4ea864c4d26189c26ed60df1d27e25eabdde441fa3812de8c8cac218b9",
        "shine-h3-compiler/1.2.2": "c530df41cd3d40ecf2ecceb690ecc7c7aee89c69ae9e157af4583c4482dfe7ff",
        "shine-h3-compiler/1.2.3": "c530df41cd3d40ecf2ecceb690ecc7c7aee89c69ae9e157af4583c4482dfe7ff",
    },
    "narrated_ref2va": {
        "shine-h3-compiler/1.2.1": "5fe1339c3e1236b35cd82a401f2d4ab1042b1a1b74631037b4a34cadc37ce5dc",
        "shine-h3-compiler/1.2.2": "03c0cf1fe7130980a314a59b79ba30aa0c286f46fa8e048969b112789502d825",
        "shine-h3-compiler/1.2.3": "03c0cf1fe7130980a314a59b79ba30aa0c286f46fa8e048969b112789502d825",
    },
    "silent_ref2va_no_sound_fields": {
        "shine-h3-compiler/1.2.1": "d6c2c0308451c00beb92a022ad2b41a3818daa2042d8001e3be7dbee1644e386",
        "shine-h3-compiler/1.2.2": "1fbcb8d5d3bd656e5af7a33a9e600d19454d6c495309f67aaa522b0ba0d0868c",
        "shine-h3-compiler/1.2.3": "1fbcb8d5d3bd656e5af7a33a9e600d19454d6c495309f67aaa522b0ba0d0868c",
    },
}


def _legacy_hash_fixtures():
    fact = "When Shen Yan says 'time will stop tonight', the night finally ends."
    return {
        "silent_t2va_with_facts": (
            spec(locked_story_facts=[fact], prompt_story_facts=[fact]), [],
        ),
        "dialogue_ref2va_voice_master": (
            spec(
                characters=["R01 杜万山"],
                dialogue=[{"speaker_id": "R01 杜万山", "text": "你来了。", "start_s": 1.0, "end_s": 2.4}],
            ),
            [
                item("<Subject 1>", ReferenceUse.SUBJECT, entity_id="R01 杜万山", role="character"),
                item("<Picture 2>", ReferenceUse.PICTURE, asset_id="asset-2"),
                item("<Audio 1>", ReferenceUse.AUDIO, asset_id="voice-1", duration_s=4),
            ],
        ),
        "silent_ref2va_continuity_audio": (
            spec(continuity={"mode": "audio_reference", "previous_shot_id": "source"}),
            [
                item("<Video 1>", ReferenceUse.VIDEO, duration_s=4),
                item("<Audio 1>", ReferenceUse.AUDIO, asset_id="source-audio", duration_s=4,
                     role="continuity_derivative"),
            ],
        ),
        "narrated_ref2va": (
            spec(
                characters=["旁白"],
                dialogue=[{"speaker_id": "旁白", "text": "夜幕降临。", "start_s": 0.5, "end_s": 2.5}],
            ),
            [item("<Picture 1>", ReferenceUse.PICTURE, asset_id="asset-2", frame_role=FrameRole.KEYFRAME)],
        ),
        "silent_ref2va_no_sound_fields": (
            spec(sound={}),
            [item("<Subject 1>", ReferenceUse.SUBJECT, entity_id="CHAR_A", role="character")],
        ),
    }


def test_historical_compiler_versions_keep_their_exact_prompt_bytes():
    from shine.h3.compiler import COMPILER_VERSION

    compiler = H3PromptCompiler()
    for name, (shot, manifest) in _legacy_hash_fixtures().items():
        for version, expected in _LEGACY_PROMPT_HASHES[name].items():
            compiled = compiler.compile(
                spec=shot, style=STYLE, manifest=manifest,
                project_language="Chinese", compiler_version=version,
            )
            assert compiled.prompt_sha256 == expected, (name, version)
            assert compiled.compiler_version == version, (name, version)
        current = compiler.compile(spec=shot, style=STYLE, manifest=manifest, project_language="Chinese")
        assert current.compiler_version == COMPILER_VERSION
        assert current.prompt_sha256 != expected, name


#: The ledger holds TWO byte variants both pinned as ``1.3.0``: the anchored
#: variant (current main) and the pre-anchoring variant (``1.3.0-original``).
#: Both must replay byte-exactly so stored requests stay verifiable; neither is
#: rewritten.  Captured on the same fixtures as the 1.2.x golden masters.
_V13_PROMPT_HASHES = {
    "silent_t2va_with_facts": {
        "shine-h3-compiler/1.3.0": "329cfce89ca06a39318fdd725fb9469170b53e337899ad94d9e1a8b0f9930f6b",
        "shine-h3-compiler/1.3.0-original": "07639f9c475acfeeceeddbf15b1b9f01704cc379ae4b96e653aa7b8709cd75bb",
    },
    "dialogue_ref2va_voice_master": {
        "shine-h3-compiler/1.3.0": "af22fd909d3fff53da7af3718ac8957a4e971413d38c5bf852bfdcb7cd92b471",
        "shine-h3-compiler/1.3.0-original": "ef0184ea99273dd3f4f74145bf6273d7be00fd2b2c9109154c38bdb9d5162c0f",
    },
    "silent_ref2va_continuity_audio": {
        "shine-h3-compiler/1.3.0": "8eb6674db969de3cd47cbbc98a4ac2d67c0c794a4ff7895b1bf4fbc4ec932be4",
        "shine-h3-compiler/1.3.0-original": "42bacd0479d19e72a14578bfc7aee8120de8b8bc894ef37c9a514d297d27edf0",
    },
    "narrated_ref2va": {
        # Drift does not touch a narrated picture-only shot: both variants agree.
        "shine-h3-compiler/1.3.0": "1a387ae785e9c73a18e0f2c05d79e8beb6b1ccde45ae740056694214d283acd6",
        "shine-h3-compiler/1.3.0-original": "1a387ae785e9c73a18e0f2c05d79e8beb6b1ccde45ae740056694214d283acd6",
    },
    "silent_ref2va_no_sound_fields": {
        "shine-h3-compiler/1.3.0": "866ed3680531ea1cb2b9c5b0121fe2f2c38690893fdd36b04afdd2f5090517af",
        "shine-h3-compiler/1.3.0-original": "7380662d13cdb261744eedb1bf9d75a2a46205ada706f8671c6bce8a434431b3",
    },
}


def test_both_historical_130_variants_replay_their_exact_bytes():
    from shine.h3.compiler import (
        LEGACY_ANCHORED_COMPILER_VERSION,
        LEGACY_PRE_ANCHOR_COMPILER_VERSION,
    )

    compiler = H3PromptCompiler()
    for name, (shot, manifest) in _legacy_hash_fixtures().items():
        hashes = {}
        for version, expected in _V13_PROMPT_HASHES[name].items():
            compiled = compiler.compile(
                spec=shot, style=STYLE, manifest=manifest,
                project_language="Chinese", compiler_version=version,
            )
            assert compiled.prompt_sha256 == expected, (name, version)
            assert compiled.compiler_version == version, (name, version)
            hashes[version] = compiled.prompt_sha256
        # The two variants differ exactly where the drift reaches (silent anchor,
        # silent ambience sanitizer, subject appearance) and agree elsewhere.
        if name == "narrated_ref2va":
            assert hashes[LEGACY_ANCHORED_COMPILER_VERSION] == hashes[LEGACY_PRE_ANCHOR_COMPILER_VERSION]
        else:
            assert hashes[LEGACY_ANCHORED_COMPILER_VERSION] != hashes[LEGACY_PRE_ANCHOR_COMPILER_VERSION]


def test_pre_anchor_replay_restores_the_original_silent_and_subject_wording():
    from shine.h3.compiler import LEGACY_PRE_ANCHOR_COMPILER_VERSION

    shot = spec(
        characters=["安娜"],
        sound={"ambience": "Keep diegetic city ambience restrained beneath the original dialogue."},
    )
    manifest = [item("<Subject 1>", ReferenceUse.SUBJECT, entity_id="安娜", role="character")]
    original = H3PromptCompiler().compile(
        spec=shot, style=STYLE, manifest=manifest, project_language="Chinese",
        compiler_version=LEGACY_PRE_ANCHOR_COMPILER_VERSION,
    )
    # Pre-anchoring bytes: no language-world anchor, no ambience sanitizing, and
    # the original "hairstyle and build" subject wording.
    assert "Chinese-language world" not in original.sections["overall_soundscape"]
    assert "beneath the original dialogue" in original.sections["overall_soundscape"]
    assert "hairstyle and build must be kept" in original.sections["subject_definitions"]
    assert "reference appearance" not in original.sections["subject_definitions"]


# --- P0-C / P3: native in-shot timeline, reference semantics, natural camera ----


def test_internal_cuts_compile_to_the_native_shot_timeline():
    shot = spec(
        characters=["CHAR_A"],
        duration_target_s=10,
        internal_cuts=[
            {"start_s": 3.5, "beat_id": "B02",
             "visual_description": "The woman places both feet firmly on the floor and rises from the chair."},
            {"start_s": 7.0, "beat_id": "B03",
             "visual_description": "She crosses to the window and pulls the curtain aside."},
        ],
    )
    manifest = [item("<Subject 1>", ReferenceUse.SUBJECT, entity_id="CHAR_A", role="character")]
    compiled = compile_prompt(manifest, shot=shot)
    desc = compiled.sections["detailed_description"]
    assert "[Shot 1]" in desc
    assert "[Shot 2] At 00:03.500, The woman places both feet firmly on the floor" in desc
    assert "[Shot 3] At 00:07.000, She crosses to the window and pulls the curtain aside." in desc
    assert "hard cut" not in desc
    # The cut timeline must survive the rule pack's increasing-timestamp check.
    from shine.h3.spec_review import review_h3_prompt
    findings = review_h3_prompt(
        compiled.prompt, compiled.sections, mode=compiled.mode.value,
        dialogue=shot.dialogue, duration_s=shot.duration_target_s,
        defined_labels={i.label for i in manifest},
    )
    assert not any(f.get("rule_id") == "H3_TIMING" for f in findings)


def test_legacy_versions_ignore_internal_cuts_and_keep_single_block():
    from shine.h3.compiler import LEGACY_ANCHORED_COMPILER_VERSION
    shot = spec(
        characters=["CHAR_A"], duration_target_s=10,
        internal_cuts=[{"start_s": 3.5, "visual_description": "She rises from the chair."}],
    )
    manifest = [item("<Subject 1>", ReferenceUse.SUBJECT, entity_id="CHAR_A", role="character")]
    legacy = H3PromptCompiler().compile(
        spec=shot, style=STYLE, manifest=manifest, project_language="Chinese",
        compiler_version=LEGACY_ANCHORED_COMPILER_VERSION,
    )
    assert "[Shot 2]" not in legacy.sections["detailed_description"]


def test_empty_internal_cuts_keep_old_spec_bytes():
    plain = spec()
    assert "internal_cuts" not in plain.model_dump(mode="json")
    with_empty = spec(internal_cuts=[])
    assert with_empty.model_dump(mode="json") == plain.model_dump(mode="json")


def test_video_motion_reference_is_not_labelled_continuation():
    shot = spec(continuity={"mode": "cut"})
    manifest = [item("<Video 1>", ReferenceUse.VIDEO, duration_s=4)]
    compiled = compile_prompt(manifest, shot=shot)
    assert "video continuation" not in compiled.sections["summary"]
    assert "reference generation" in compiled.sections["summary"]
    assert "motion and pacing reference" in compiled.sections["retention_analysis"]
    assert "attribute_transfer" in compiled.sections["retention_analysis"]


def test_true_video_continuation_is_labelled_continuation():
    shot = spec(continuity={"mode": "video_reference", "previous_shot_id": "src"})
    manifest = [item("<Video 1>", ReferenceUse.VIDEO, duration_s=4, role="continuity_derivative")]
    compiled = compile_prompt(manifest, shot=shot)
    assert "video continuation" in compiled.sections["summary"]
    assert "(continuation structure): fully_preserved" in compiled.sections["retention_analysis"]


def test_composition_picture_is_partially_preserved_not_a_frame_anchor():
    manifest = [item("<Picture 1>", ReferenceUse.PICTURE)]  # no frame_role
    compiled = compile_prompt(manifest, shot=spec(), mode=GenerationMode.REF2VA)
    assert "keyframe completion" not in compiled.sections["summary"]
    assert "composition reference): partially_preserved" in compiled.sections["retention_analysis"]


def test_camera_is_natural_prose_not_mechanical_enumeration():
    shot = spec(camera={
        "composition": "A medium two-shot of the leads at the desk",
        "movement": "The camera slowly pushes in on the lead's face",
        "lighting": "Low-key side light keeps the shadows readable",
    })
    compiled = compile_prompt([], shot=shot)
    desc = compiled.sections["integrated_multimodal_description"]
    assert "composition:" not in desc and "movement:" not in desc and "lighting:" not in desc
    assert "A medium two-shot of the leads at the desk" in desc
    assert "The camera slowly pushes in on the lead's face" in desc


def test_silent_shot_sanitizes_dialogue_ambience_and_anchors_project_language():
    """无对白镜头剥离环境音里的原对话字样并锚定工程语言背景，防止 H3 编造外语语音。"""
    silent = compile_prompt(
        [item("<Subject 1>", ReferenceUse.SUBJECT, entity_id="安娜", role="character")],
        shot=spec(sound={
            "ambience": (
                "Keep diegetic city ambience restrained beneath the original dialogue "
                "and preserve intentional silence."
            ),
        }),
        project_language="Chinese",
    )
    soundscape = silent.sections["overall_soundscape"]
    assert "dialogue" not in soundscape.lower().replace("has no dialogue", "")
    assert "beneath the original dialogue" not in soundscape.lower()
    assert "Chinese-language world" in soundscape
    assert "the soundtrack is entirely non-vocal" in soundscape
    assert "reference appearance" in silent.sections["subject_definitions"]
    for token in (*_PRIMING_VOCABULARY, "narration"):
        assert token not in silent.prompt.lower(), token
