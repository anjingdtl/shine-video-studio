from __future__ import annotations

import pytest

from shine.h3.compiler import (
    LEGACY_PRE_SANITIZE_COMPILER_VERSION,
    LEGACY_TIMELINE_COMPILER_VERSION,
    H3PromptCompiler,
    PromptCompilationError,
)
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


def compile_prompt(manifest, *, shot=None, mode=None, project_language="Chinese", style=STYLE):
    return H3PromptCompiler().compile(
        spec=shot or spec(),
        style=style,
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
    # 2.0.0 keeps the seven frozen dimensions and drops the header that only
    # named the Style Bible version: the request already pins that version.
    assert "Photography language: 平视中近景，稳定缓推." in compiled.prompt
    assert "Visual style constraints from the frozen Style Bible" not in compiled.prompt
    assert "style-v1" not in compiled.prompt
    # The released 1.4.0 bytes still carry the header they were frozen with.
    legacy = H3PromptCompiler().compile(spec=spec(), style=STYLE, manifest=[],
                                        compiler_version=LEGACY_TIMELINE_COMPILER_VERSION)
    assert "Visual style constraints from the frozen Style Bible (version style-v1)" in legacy.prompt
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
    # 2.0.0 states the same rule in one short lead instead of a meta paragraph.
    assert "Background story facts, for the picture only and never heard:" in silent.prompt
    assert "Story continuity facts below" not in silent.prompt
    # 1.4.0 keeps the paragraph its frozen requests were compiled with.
    legacy = H3PromptCompiler().compile(
        spec=spec(locked_story_facts=[continuity], prompt_story_facts=[continuity]),
        style=STYLE, manifest=[], compiler_version=LEGACY_TIMELINE_COMPILER_VERSION,
    )
    assert "Story continuity facts below are silent background context for the picture only" in legacy.prompt
    assert "they inform the visuals and are never heard as audio" in legacy.prompt
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
    assert "Background story facts, for the picture only and never heard:" in compiled.prompt
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
    # 2.1.0 binds the label to its presentation slot in one sentence and
    # explicitly anchors face, hairstyle and outfit to the reference image.
    assert (
        "<Subject 1> is the first listed performer (character), shown in <Picture 1>; "
        "keep that exact reference appearance, face, hairstyle and outfit throughout."
    ) in compiled.sections["subject_definitions"]
    assert "approved, locked" not in compiled.sections["subject_definitions"]
    assert "this exact face, hairstyle" not in compiled.sections["subject_definitions"]
    # 1.4.0: a first-frame picture is a concrete frame anchor, so the official
    # task type is keyframe completion alongside reference generation.
    assert compiled.sections["summary"].startswith(
        "[reference generation + keyframe completion + audio reference]"
    )
    assert (
        "<Subject 1>: fully_preserved - the same person in the same outfit in every frame."
        in compiled.sections["retention_analysis"]
    )
    # Released 2.0.0 replays its exact wording unchanged.
    from shine.h3.compiler import LEGACY_THIN_SHELL_COMPILER_VERSION
    legacy_200 = H3PromptCompiler().compile(spec=shot, style=STYLE, manifest=manifest,
                                            compiler_version=LEGACY_THIN_SHELL_COMPILER_VERSION)
    assert (
        "<Subject 1> is the first listed performer (character), shown in <Picture 1>; "
        "keep that exact reference appearance throughout."
    ) in legacy_200.sections["subject_definitions"]
    assert "<Subject 1>: fully_preserved - the same person in every frame." in legacy_200.sections["retention_analysis"]
    assert "<Picture 1> ([Shot 1] first_frame frame): fully_preserved" in compiled.sections["retention_analysis"]
    # The released 1.4.0 retention line replays unchanged.
    legacy = H3PromptCompiler().compile(spec=shot, style=STYLE, manifest=manifest,
                                        compiler_version=LEGACY_TIMELINE_COMPILER_VERSION)
    assert ("<Subject 1> (the first listed performer in [Shot 1]): fully_preserved"
            in legacy.sections["retention_analysis"])
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
        "<Subject 1> is the first listed performer (character), shown in <Picture 1>;"
    ) in definitions
    assert (
        "<Subject 2> is the second listed performer (character), shown in <Picture 2>;"
    ) in definitions
    assert "<Picture 3> is the keyframe reference for [Shot 1]." in definitions
    assert "<Audio 1> is a reference for non-vocal ambience only." in definitions
    # The released 1.4.0 binding sentence replays unchanged.
    legacy = H3PromptCompiler().compile(spec=shot, style=STYLE, manifest=manifest,
                                        compiler_version=LEGACY_TIMELINE_COMPILER_VERSION)
    assert (
        "<Subject 1> is the approved, locked reference for the first listed performer (character), "
        "shown in <Picture 1>;"
    ) in legacy.sections["subject_definitions"]


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


def test_compiler_140_sanitizes_dialogue_quotes_and_silent_style_priming():
    english_style = StyleBible(
        photography_language="Eye-level medium shot.",
        shot_blocking="Medium two-shot follows the 180-degree dialogue axis.",
        color_palette="Neutral desert amber.",
        lighting_tone="Hard noon sunlight.",
        lens_texture="Clean spherical image.",
        editing_rhythm="Natural dialogue breaths between cuts.",
        sound_performance="Natural Mandarin Chinese speech.",
    )
    silent = H3PromptCompiler().compile(
        spec=spec(sound={"environment": "desert wind beneath the original dialogue"}),
        style=english_style,
        manifest=[item("<Subject 1>", ReferenceUse.SUBJECT, entity_id="林风", role="character")],
        project_language="Chinese",
    )
    desc = silent.sections["detailed_description"].lower()
    assert "dialogue" not in desc.replace("performed without dialogue", "")
    assert "180-degree interaction axis" in desc
    assert "natural silent reaction beats" in desc
    assert "beneath the original" not in desc

    spoken = H3PromptCompiler().compile(
        spec=spec(
            characters=["林风"],
            dialogue=[{"speaker_id": "林风", "text": "“这就是……古地图上的遗迹，”", "start_s": 0.5, "end_s": 3.0}],
        ),
        style=english_style,
        manifest=[item("<Subject 1>", ReferenceUse.SUBJECT, entity_id="林风", role="character")],
        project_language="Chinese",
    )
    assert "<d>[Chinese] 这就是，古地图上的遗迹</d>" in spoken.sections["detailed_description"]


# --- 1.4.0 golden masters ---------------------------------------------------------
#
# 1.4.0 is a historical version since 2.0.0, so its bytes are pinned here the
# same way 1.2.x and 1.3.0 are.  The ``1.4.0`` column was sampled on the
# unmodified tree before 2.0.0 existed.  The ``1.4.0-original`` column was
# sampled on the code as it was before commit 40042d2, which added three
# sanitizers under 1.4.0 without a version bump.

_V14_PROMPT_HASHES = {
    "silent_t2va_with_facts": {
        "shine-h3-compiler/1.4.0": "44cdd444e359f0227b491c229dca83ee0b81e1b844adeea0671b7ea563ed1b79",
    },
    "dialogue_ref2va_voice_master": {
        "shine-h3-compiler/1.4.0": "f6099fb0a14109569fcfe4354bbb0f39c09c9201c7f35f58d82ca86dbb1cca6e",
    },
    "silent_ref2va_continuity_audio": {
        "shine-h3-compiler/1.4.0": "13c81a0cd852c7ce0a4058ed002345b5702ea207378619b638198e0b156e4bfe",
    },
    "narrated_ref2va": {
        "shine-h3-compiler/1.4.0": "8df5763d0384d77c049a309f41f2e2b06d82753110d948499424422609884298",
    },
    "silent_ref2va_no_sound_fields": {
        "shine-h3-compiler/1.4.0": "879a4213154c4f32d53f555dd02a52e1814d05513ce944f21c4e20026f9f926c",
    },
    "internal_cuts_with_dialogue": {
        "shine-h3-compiler/1.4.0": "5be07dd1383c038fd23934e228425e59fd27e1494131e052cfd795ae04cb999d",
    },
    "camera_narrative_t2va": {
        "shine-h3-compiler/1.4.0": "4721c8dd2434b0d1fe20a5fde3fc581ddfc93b66ad064e1b0f6882fb57622290",
    },
    "video_motion_reference": {
        "shine-h3-compiler/1.4.0": "c9c4597c4e731085d7e0b000038d1debb3655fdcafec14d1ffc486c15b4149f4",
    },
    "true_video_continuation": {
        "shine-h3-compiler/1.4.0": "b75205ee60f13e75efd2af86b0d6bcd687d800ab7462bee9833422052a0c5e4a",
    },
    "composition_picture": {
        "shine-h3-compiler/1.4.0": "758c27bdf32be401d869b8e78f89dc837557e69789de1ef811bf9814370fa741",
    },
    "animation_frozen_with_live_action_projection": {
        "shine-h3-compiler/1.4.0": "29a2436c24300bef51a18654886f00a913b240e562d95266d186c1a884721d7b",
    },
    "silent_english_style_dialogue_priming": {
        "shine-h3-compiler/1.4.0": "85935b83a43cf76625fea4c94f2e45c9475af0f6c13070a3869e5c987c1d7328",
        "shine-h3-compiler/1.4.0-original": "f8f70f388c2a54578c52d783f58f262e73345145b19ec82bfa64d53c6fda6f65",
    },
    "silent_ambience_priming": {
        "shine-h3-compiler/1.4.0": "ee37b37a28edc248c71bb55ec0b9d0524b09bb392e91094503a30d9fef7365ca",
    },
    "spoken_ellipsis_and_trailing_comma": {
        "shine-h3-compiler/1.4.0": "7d2709efdedc1fea950554e37dfef07f70986208f26377ede06ab3cd5797df8e",
        "shine-h3-compiler/1.4.0-original": "809e032b728d21d69607cc686048ca43d48d08a2d708068ecb0f77ae3522e74b",
    },
    "last_frame_continuation": {
        "shine-h3-compiler/1.4.0": "3c04b8931f8531fb3a87e4439d5a85e29e990b5d6370f8e5504d5a942dc94f70",
    },
}

_V14 = "shine-h3-compiler/1.4.0"
_V14_ORIGINAL = "shine-h3-compiler/1.4.0-original"

_ENGLISH_STYLE = StyleBible(
    photography_language="Eye-level medium shot.",
    shot_blocking="Medium two-shot follows the 180-degree dialogue axis.",
    color_palette="Neutral desert amber.",
    lighting_tone="Hard noon sunlight.",
    lens_texture="Clean spherical image.",
    editing_rhythm="Natural dialogue breaths between cuts.",
    sound_performance="Natural Mandarin Chinese speech.",
)


def _v14_hash_fixtures() -> dict[str, tuple[ShotSpec, list, StyleBible]]:
    """The 1.2.x/1.3.0 masters plus the shots that exercise 1.4.0 itself."""
    return {
        **{name: (shot, manifest, STYLE) for name, (shot, manifest) in _legacy_hash_fixtures().items()},
        # 1.4.0 features: the native in-shot timeline, natural camera prose, the
        # real use of every reference, and a frozen medium.
        "internal_cuts_with_dialogue": (
            spec(
                characters=["CHAR_A"], duration_target_s=10,
                dialogue=[{"speaker_id": "CHAR_A", "text": "你先走。", "start_s": 0.4, "end_s": 1.8},
                          {"speaker_id": "CHAR_A", "text": "我随后就到。", "start_s": 4.2, "end_s": 6.2}],
                internal_cuts=[
                    {"start_s": 3.5, "beat_id": "B02",
                     "visual_description": "The woman places both feet firmly on the floor and rises from the chair."},
                    {"start_s": 7.0, "beat_id": "B03",
                     "visual_description": "She crosses to the window and pulls the curtain aside."},
                ],
            ),
            [item("<Subject 1>", ReferenceUse.SUBJECT, entity_id="CHAR_A", role="character")],
            STYLE,
        ),
        "camera_narrative_t2va": (
            spec(camera={
                "composition": "A medium two-shot of the leads at the desk",
                "movement": "The camera slowly pushes in on the lead's face",
                "lighting": "Low-key side light keeps the shadows readable",
            }),
            [],
            STYLE,
        ),
        "video_motion_reference": (
            spec(continuity={"mode": "cut"}),
            [item("<Video 1>", ReferenceUse.VIDEO, duration_s=4)],
            STYLE,
        ),
        "true_video_continuation": (
            spec(continuity={"mode": "video_reference", "previous_shot_id": "src"}),
            [item("<Video 1>", ReferenceUse.VIDEO, duration_s=4, role="continuity_derivative")],
            STYLE,
        ),
        "composition_picture": (spec(), [item("<Picture 1>", ReferenceUse.PICTURE)], STYLE),
        "animation_frozen_with_live_action_projection": (
            spec(prompt_style=STYLE.model_copy(update={"film_type": FilmType.LIVE_ACTION})),
            [item("<Subject 1>", ReferenceUse.SUBJECT)],
            STYLE.model_copy(update={"film_type": FilmType.ANIMATION}),
        ),
        # The content the 40042d2 sanitizers reach: a silent shot's style
        # dimensions and environment, and a line with an ellipsis and a trailing comma.
        "silent_english_style_dialogue_priming": (
            spec(sound={"environment": "desert wind beneath the original dialogue"}),
            [item("<Subject 1>", ReferenceUse.SUBJECT, entity_id="林风", role="character")],
            _ENGLISH_STYLE,
        ),
        "silent_ambience_priming": (
            spec(sound={"ambience": "Keep diegetic city ambience restrained beneath the original dialogue "
                                    "and preserve intentional silence."}),
            [item("<Subject 1>", ReferenceUse.SUBJECT, entity_id="安娜", role="character")],
            STYLE,
        ),
        "spoken_ellipsis_and_trailing_comma": (
            spec(
                characters=["林风"],
                dialogue=[{"speaker_id": "林风", "text": "“这就是……古地图上的遗迹，”", "start_s": 0.5, "end_s": 3.0}],
            ),
            [item("<Subject 1>", ReferenceUse.SUBJECT, entity_id="林风", role="character")],
            _ENGLISH_STYLE,
        ),
        "last_frame_continuation": (
            spec(continuity={"mode": "last_frame", "previous_shot_id": "shot-prev"}),
            [
                item("<Subject 1>", ReferenceUse.SUBJECT, entity_id="CHAR_A", role="character"),
                item("<Picture 2>", ReferenceUse.PICTURE, asset_id="last", role="continuity_derivative",
                     frame_role=FrameRole.LAST),
            ],
            STYLE,
        ),
    }


def _respec(shot: ShotSpec, **changes) -> ShotSpec:
    """A copy of a shot with fields replaced, validated like a stored spec."""
    return ShotSpec.model_validate({**shot.model_dump(mode="json"), **changes})


def test_the_released_140_compiler_keeps_its_exact_prompt_bytes():
    from shine.h3.compiler import COMPILER_VERSION

    compiler = H3PromptCompiler()
    # 当前版本的哨兵：提升版本号必须是有意为之，并同时保证每个已发布版本的字节不变
    # （见 test_fact_scoping_lands_in_220_and_stored_210_requests_still_replay）。
    assert COMPILER_VERSION == "shine-h3-compiler/2.2.0"
    for name, (shot, manifest, style) in _v14_hash_fixtures().items():
        compiled = compiler.compile(
            spec=shot, style=style, manifest=manifest,
            project_language="Chinese", compiler_version=LEGACY_TIMELINE_COMPILER_VERSION,
        )
        assert compiled.prompt_sha256 == _V14_PROMPT_HASHES[name][_V14], name
        assert compiled.compiler_version == LEGACY_TIMELINE_COMPILER_VERSION
        current = compiler.compile(spec=shot, style=style, manifest=manifest, project_language="Chinese")
        assert current.compiler_version == COMPILER_VERSION
        # 2.0.0 restyles every prompt: the style header alone is gone.
        assert current.prompt_sha256 != _V14_PROMPT_HASHES[name][_V14], name


def test_the_pre_sanitizing_140_variant_replays_its_exact_bytes():
    """Commit 40042d2 added three sanitizers under 1.4.0 without a version bump.

    Requests frozen before it (6 of the 16 real 1.4.0 requests in the ledger)
    stopped replaying.  ``1.4.0-original`` is their replay-only identity; the
    hashes below were sampled on the code as it was before that commit.
    """
    compiler = H3PromptCompiler()
    for name, (shot, manifest, style) in _v14_hash_fixtures().items():
        expected = _V14_PROMPT_HASHES[name].get(_V14_ORIGINAL, _V14_PROMPT_HASHES[name][_V14])
        replay = compiler.compile(
            spec=shot, style=style, manifest=manifest,
            project_language="Chinese", compiler_version=LEGACY_PRE_SANITIZE_COMPILER_VERSION,
        )
        assert replay.prompt_sha256 == expected, name
        assert replay.compiler_version == LEGACY_PRE_SANITIZE_COMPILER_VERSION
        canonical = compiler.compile(
            spec=shot, style=style, manifest=manifest,
            project_language="Chinese", compiler_version=LEGACY_TIMELINE_COMPILER_VERSION,
        )
        drifted = name in {"silent_english_style_dialogue_priming", "spoken_ellipsis_and_trailing_comma"}
        # The two variants differ exactly where the sanitizers reach.
        assert (replay.prompt_sha256 != canonical.prompt_sha256) is drifted, name


def test_pre_sanitize_replay_restores_the_unsanitized_wording():
    compiler = H3PromptCompiler()
    shot, manifest, style = _v14_hash_fixtures()["spoken_ellipsis_and_trailing_comma"]
    original = compiler.compile(spec=shot, style=style, manifest=manifest, project_language="Chinese",
                                compiler_version=LEGACY_PRE_SANITIZE_COMPILER_VERSION)
    assert "<d>[Chinese] “这就是……古地图上的遗迹，”</d>" in original.prompt
    assert "<d>[Chinese] 这就是，古地图上的遗迹</d>" in compile_prompt(manifest, shot=shot).prompt

    shot, manifest, style = _v14_hash_fixtures()["silent_english_style_dialogue_priming"]
    original_silent = compiler.compile(spec=shot, style=style, manifest=manifest, project_language="Chinese",
                                       compiler_version=LEGACY_PRE_SANITIZE_COMPILER_VERSION)
    desc = original_silent.sections["detailed_description"]
    assert "Natural dialogue breaths between cuts." in desc
    assert "desert wind beneath the original dialogue" in desc
    # The current compiler still sanitizes both, as 1.4.0 has since 40042d2.
    current = compile_prompt(manifest, shot=shot, style=_ENGLISH_STYLE).sections["detailed_description"]
    assert "Natural silent reaction beats between cuts." in current
    assert "beneath the original dialogue" not in current


def test_provenance_replay_falls_back_to_the_earlier_140_variant_only_on_exact_bytes():
    from types import SimpleNamespace

    from shine.h3.rulebook import recompile_for_provenance

    compiler = H3PromptCompiler()
    shot, manifest, style = _v14_hash_fixtures()["spoken_ellipsis_and_trailing_comma"]
    original = compiler.compile(spec=shot, style=style, manifest=manifest, project_language="Chinese",
                                compiler_version=LEGACY_PRE_SANITIZE_COMPILER_VERSION)
    canonical = compiler.compile(spec=shot, style=style, manifest=manifest, project_language="Chinese",
                                 compiler_version=LEGACY_TIMELINE_COMPILER_VERSION)

    def frozen(compiled, version) -> SimpleNamespace:
        return SimpleNamespace(
            compiler_version=version, shot_spec=shot, style_snapshot=style, reference_manifest=manifest,
            generation_mode=compiled.mode, project_language="Chinese", compiled_prompt=compiled.prompt,
            prompt_sha256=compiled.prompt_sha256, compiled_prompt_sections=compiled.sections,
        )

    # A request frozen before 40042d2 is replayed byte-exactly by the earlier variant.
    replayed, variant = recompile_for_provenance(frozen(original, LEGACY_TIMELINE_COMPILER_VERSION))
    assert variant == LEGACY_PRE_SANITIZE_COMPILER_VERSION
    assert replayed.prompt == original.prompt
    # The canonical bytes of the same version need no fallback.
    replayed, variant = recompile_for_provenance(frozen(canonical, LEGACY_TIMELINE_COMPILER_VERSION))
    assert variant is None and replayed.prompt == canonical.prompt
    # A tampered prompt matches neither variant: nothing is accepted for it.
    tampered = frozen(original, LEGACY_TIMELINE_COMPILER_VERSION)
    tampered.compiled_prompt = original.prompt.replace("古地图上的遗迹", "被改写的台词")
    replayed, variant = recompile_for_provenance(tampered)
    assert variant is None and replayed.prompt != tampered.compiled_prompt
    # A version that was never changed under itself has no variant to fall back to.
    anchored = compiler.compile(spec=shot, style=style, manifest=manifest, project_language="Chinese",
                                compiler_version="shine-h3-compiler/1.2.3")
    tampered = frozen(anchored, "shine-h3-compiler/1.2.3")
    tampered.compiled_prompt = anchored.prompt + " "
    replayed, variant = recompile_for_provenance(tampered)
    assert variant is None and replayed.prompt != tampered.compiled_prompt


# --- 2.0.0: thin shell, rich timeline, every line in its own block ---------------


def _shell_words(compiled) -> int:
    """The words of the three reference sections and of the two meta paragraphs."""
    count = sum(len(compiled.sections[name].split())
                for name in ("subject_definitions", "summary", "retention_analysis"))
    first = compiled.sections["detailed_description"].splitlines()[0]
    for marker, end in (
        ("Visual style constraints from the frozen Style Bible", "Photography language:"),
        ("Reference cast order (mandatory)", ". The first described person"),
    ):
        if marker in first:
            start = first.index(marker)
            stop = first.index(end, start) if end in first[start:] else start + len(marker)
            count += len(first[start:stop].split())
    return count


def test_the_thin_shell_keeps_the_contract_and_drops_the_meta_instructions():
    manifest = [
        item("<Subject 1>", ReferenceUse.SUBJECT, entity_id="CHAR_A", role="character"),
        item("<Subject 2>", ReferenceUse.SUBJECT, entity_id="CHAR_B", asset_id="asset-2", role="character"),
        item("<Subject 3>", ReferenceUse.SUBJECT, entity_id="CHAR_C", asset_id="asset-3", role="character"),
        item("<Picture 4>", ReferenceUse.PICTURE, asset_id="asset-4"),
        item("<Audio 1>", ReferenceUse.AUDIO, asset_id="voice-1", duration_s=4),
    ]
    shot = spec(
        characters=["CHAR_A", "CHAR_B", "CHAR_C"],
        dialogue=[{"speaker_id": "CHAR_A", "text": "你来了。", "start_s": 1.0, "end_s": 2.4}],
    )
    compiled = compile_prompt(manifest, shot=shot)
    legacy = H3PromptCompiler().compile(spec=shot, style=STYLE, manifest=manifest,
                                        compiler_version=LEGACY_TIMELINE_COMPILER_VERSION)
    # The official six-section contract and its order are untouched.
    assert list(compiled.sections) == [
        "subject_definitions", "summary", "retention_analysis",
        "detailed_description", "overall_soundscape", "non_diegetic_music",
    ]
    # So are the task types, the retention levels, the slots and the labels.
    assert compiled.sections["summary"].startswith("[reference generation + audio reference]")
    assert ("Reference labels used: <Subject 1>, <Subject 2>, <Subject 3>, <Picture 4>, <Audio 1>."
            in compiled.sections["summary"])
    assert (
        "<Subject 1>: fully_preserved - the same person in the same outfit in every frame."
        in compiled.sections["retention_analysis"]
    )
    assert "([Shot 1] composition reference): partially_preserved" in compiled.sections["retention_analysis"]
    assert "the approved timbre voices only the scripted dialogue lines" in compiled.sections["retention_analysis"]
    assert "shown in <Picture 1>" in compiled.sections["subject_definitions"].splitlines()[0]
    assert "shown in <Picture 3>" in compiled.sections["subject_definitions"].splitlines()[2]
    # And the validated positive sound contract is inherited as it was.
    assert "Every voice in this shot speaks Chinese" in compiled.sections["overall_soundscape"]
    assert "the scripted lines' voice timbre" in compiled.sections["subject_definitions"]
    assert ("Deliver only the timed dialogue blocks below, exactly as written"
            in compiled.sections["detailed_description"])
    assert "<d>[Chinese] 你来了。</d>" in compiled.sections["detailed_description"]
    # The meta instructions are gone.
    prompt = compiled.prompt
    for boilerplate in (
        "Visual style constraints from the frozen Style Bible", "style-v1",
        "Reference cast order (mandatory)",
        "The target video adapts the frozen story beat within the locked Style Bible",
        "approved, locked reference", "this exact face, hairstyle",
    ):
        assert boilerplate not in prompt, boilerplate
    for boilerplate in ("Visual style constraints from the frozen Style Bible", "Reference cast order (mandatory)",
                        "approved, locked reference"):
        assert boilerplate in legacy.prompt, boilerplate
    assert ("Cast order: <Subject 1> (first), <Subject 2> (second), <Subject 3> (third); "
            "each person keeps exactly their own reference identity."
            in compiled.sections["detailed_description"])
    # No doubled full stop where a frozen dimension already ends with one.
    assert ".. " not in prompt and not prompt.endswith("..")
    # The same shot's shell is a fraction of what 1.4.0 spent on it.
    assert _shell_words(compiled) < 0.6 * _shell_words(legacy), (_shell_words(compiled), _shell_words(legacy))


_RICH_SHOT = dict(
    characters=["CHAR_A", "CHAR_B"],
    duration_target_s=6,
    dialogue=[{"speaker_id": "CHAR_A", "text": "你听见了吗？", "start_s": 1.0, "end_s": 2.4}],
    timeline=[
        {"start_s": 0.0, "end_s": 2.4, "action": "She sets the brass cup down without a sound",
         "expression": "her eyes stay on the door", "camera": "The camera holds still"},
        {"start_s": 2.4, "end_s": 5.0, "action": "He turns his head a fraction toward the window",
         "expression": "his jaw tightens, then releases"},
    ],
    blocking={
        "axis": "the two sit on one eyeline, camera on the same side",
        "placements": [
            {"character": "CHAR_A", "zone": "left_fg", "facing": "facing_right",
             "posture": "seated, back straight", "props": ["the brass cup"]},
            {"character": "CHAR_B", "zone": "right_mg", "facing": "facing_left", "posture": "standing"},
        ],
    },
    environment={
        "depth": "Desk in the foreground, rain-streaked window behind",
        "light": "A single desk lamp rims their profiles; the window stays cold",
        "micro_motion": "Rain crawls down the glass; the lamp filament flickers",
    },
    entry_state="the cup halfway to the table",
    exit_state="her hand flat on the desk, his reflection in the window",
)
_RICH_MANIFEST = [
    item("<Subject 1>", ReferenceUse.SUBJECT, entity_id="CHAR_A", role="character"),
    item("<Subject 2>", ReferenceUse.SUBJECT, entity_id="CHAR_B", asset_id="asset-2", role="character"),
    item("<Picture 3>", ReferenceUse.PICTURE, asset_id="asset-3"),
]


def test_the_rich_timeline_becomes_second_by_second_prose():
    compiled = compile_prompt(_RICH_MANIFEST, shot=spec(**_RICH_SHOT))
    desc = compiled.sections["detailed_description"]
    # Placement is anchored before the first performance window...
    assert (
        "In frame: <Subject 1> is in the left foreground, facing screen-right, seated, back straight, "
        "holding the brass cup; <Subject 2> is in the right midground, facing screen-left, standing."
    ) in desc
    assert "It opens on the cup halfway to the table." in desc
    assert desc.index("In frame:") < desc.index("From 0.0s")
    # ...the performance is a timeline of action, face and camera...
    assert (
        "From 0.0s to 2.4s, She sets the brass cup down without a sound; her eyes stay on the door. "
        "The camera holds still. From 2.4s to 5.0s, He turns his head a fraction toward the window; "
        "his jaw tightens, then releases."
    ) in desc
    # ...the physical set is stated once, in its own three facts...
    assert (
        "Space: Desk in the foreground, rain-streaked window behind. "
        "Light: A single desk lamp rims their profiles; the window stays cold. "
        "Air: Rain crawls down the glass; the lamp filament flickers."
    ) in desc
    # ...and the exit state is the last sentence: the frame the next shot continues from.
    assert desc.endswith("The shot settles on her hand flat on the desk, his reflection in the window.")
    assert "<d>[Chinese] 你听见了吗？</d>" in desc
    # The timeline prose is not a cut marker: the rule pack reads only
    # "At MM:SS.mmm" as a timestamp, and the only one is the spoken line.
    assert desc.count("At 00:") == 1
    from shine.h3.spec_review import review_h3_prompt

    findings = review_h3_prompt(
        compiled.prompt, compiled.sections, mode=compiled.mode.value,
        dialogue=_RICH_SHOT["dialogue"], duration_s=6,
        defined_labels={reference.label for reference in _RICH_MANIFEST},
    )
    assert not any(finding.get("rule_id") == "H3_TIMING" for finding in findings)


def test_a_silent_rich_timeline_never_primes_speech():
    compiled = compile_prompt(_RICH_MANIFEST, shot=spec(**{**_RICH_SHOT, "dialogue": []}))
    desc = compiled.sections["detailed_description"]
    assert "From 0.0s to 2.4s" in desc and "The shot settles on" in desc
    assert "Performers act with closed mouths" in desc
    for token in (*_PRIMING_VOCABULARY, "narration"):
        assert token not in compiled.prompt.lower(), token
    assert "dialogue" not in desc.replace("performed without dialogue", "")


def test_every_spoken_line_sits_in_the_shot_block_that_performs_it():
    shot, manifest, _ = _v14_hash_fixtures()["internal_cuts_with_dialogue"]
    shot = _respec(shot, dialogue=[
        *shot.dialogue, {"speaker_id": "CHAR_A", "text": "带上灯。", "start_s": 7.0, "end_s": 8.6},
    ])
    lines = compile_prompt(manifest, shot=shot).sections["detailed_description"].splitlines()
    assert len(lines) == 3
    assert lines[0].startswith("[Shot 1] ")
    assert "<d>[Chinese] 你先走。</d>" in lines[0]
    assert "我随后就到" not in lines[0] and "带上灯" not in lines[0]
    # A line that starts after the first cut belongs to the second sub-shot.
    assert lines[1].startswith("[Shot 2] At 00:03.500, The woman places both feet firmly on the floor")
    assert "<d>[Chinese] 我随后就到。</d>" in lines[1]
    # A line that starts exactly at a cut belongs to the sub-shot that opens there.
    assert lines[2].startswith("[Shot 3] At 00:07.000, She crosses to the window and pulls the curtain aside.")
    assert "<d>[Chinese] 带上灯。</d>" in lines[2]
    # Up to 1.4.0 every line sat in the [Shot 1] block whatever its timestamp said.
    legacy = H3PromptCompiler().compile(spec=shot, style=STYLE, manifest=manifest,
                                        compiler_version=LEGACY_TIMELINE_COMPILER_VERSION)
    legacy_lines = legacy.sections["detailed_description"].splitlines()
    for line in ("<d>[Chinese] 你先走。</d>", "<d>[Chinese] 我随后就到。</d>", "<d>[Chinese] 带上灯。</d>"):
        assert line in legacy_lines[0]
    assert not any("<d>" in line for line in legacy_lines[1:])
    # No line was lost, and the cut timestamps still increase inside the duration.
    prompt = "\n".join(lines)
    assert prompt.count("<d>") == len(shot.dialogue) == 3


def test_a_timeline_window_lands_in_the_sub_shot_where_it_starts():
    shot, manifest, _ = _v14_hash_fixtures()["internal_cuts_with_dialogue"]
    shot = _respec(
        shot,
        timeline=[
            {"start_s": 0.2, "end_s": 2.0, "action": "She grips the chair arm"},
            {"start_s": 3.8, "end_s": 5.4, "action": "She rises to her full height"},
            {"start_s": 7.2, "end_s": 9.0, "action": "She leans into the window glass"},
        ],
        exit_state="her breath fogging the glass",
    )
    lines = compile_prompt(manifest, shot=shot).sections["detailed_description"].splitlines()
    assert "From 0.2s to 2.0s, She grips the chair arm." in lines[0]
    assert "From 3.8s to 5.4s, She rises to her full height." in lines[1]
    assert ("From 7.2s to 9.0s, She leans into the window glass. "
            "The shot settles on her breath fogging the glass." in lines[2])
    assert "The shot settles on" not in lines[0] + lines[1]


def test_rich_timeline_fields_are_omitted_while_empty_and_keep_old_spec_bytes():
    from pydantic import ValidationError

    plain = spec().model_dump(mode="json")
    for key in ("location_id", "transition_motivation", "blocking", "environment",
                "timeline", "entry_state", "exit_state"):
        assert key not in plain
    empty = spec(location_id=None, transition_motivation=None, blocking=None, environment=None,
                 timeline=[], entry_state="", exit_state="   ")
    assert empty.model_dump(mode="json") == plain
    # A shot that carries them keeps every field it was given.
    kept = spec(**_RICH_SHOT).model_dump(mode="json")
    assert set(kept) - set(plain) == {"blocking", "environment", "timeline", "entry_state", "exit_state"}
    tagged = spec(location_id="LOC_STUDY", transition_motivation="shot_reverse_shot").model_dump(mode="json")
    assert tagged["location_id"] == "LOC_STUDY" and tagged["transition_motivation"] == "shot_reverse_shot"

    with pytest.raises(ValidationError):
        # Two windows at the same moment would tell H3 two different actions.
        spec(timeline=[{"start_s": 2.0, "end_s": 4.0, "action": "She waits"},
                       {"start_s": 3.0, "end_s": 5.0, "action": "She turns"}])
    with pytest.raises(ValidationError):
        spec(timeline=[{"start_s": 0.0, "end_s": 6.5, "action": "She waits"}])
    with pytest.raises(ValidationError):
        spec(timeline=[{"start_s": 0.0, "end_s": 0.0, "action": "She waits"}])
    with pytest.raises(ValidationError):
        spec(characters=["CHAR_A"], blocking={"placements": [
            {"character": "CHAR_B", "zone": "left_fg", "facing": "facing_right"},
        ]})
    with pytest.raises(ValidationError):
        spec(blocking={"placements": [{"character": "CHAR_A", "zone": "middle", "facing": "sideways"}]})


def test_a_released_version_ignores_the_rich_timeline_fields():
    compiled = H3PromptCompiler().compile(
        spec=spec(**_RICH_SHOT), style=STYLE, manifest=_RICH_MANIFEST,
        compiler_version=LEGACY_TIMELINE_COMPILER_VERSION,
    )
    desc = compiled.sections["detailed_description"]
    for prose in ("From 0.0s", "In frame:", "The shot settles on", "Space:", "It opens on"):
        assert prose not in desc, prose


def test_rulebook_accepts_sanitized_dialogue_with_trailing_comma():
    from shine.h3.compiler import SKILL_SHA256
    from shine.h3.rulebook import PACK_ID, H3RulebookRegistry, builtin_manifest, digest
    from shine.models import RulePack
    from shine.schemas import RenderRequest

    english_style = StyleBible(
        photography_language="Eye-level medium shot.",
        shot_blocking="Two performers hold screen axis.",
        color_palette="Warm tungsten interior.",
        lighting_tone="Soft desk lamp key.",
        lens_texture="Clean 50mm realistic texture.",
        editing_rhythm="Balanced narrative pacing.",
        sound_performance="Natural Mandarin Chinese speech.",
    )
    shot = spec(
        characters=["老周"],
        duration_target_s=8.0,
        story_beat="The old repairman holds out a dry towel across the counter.",
        camera={"composition": "Eye-level medium shot.", "movement": "The camera holds still.",
                "lighting": "Warm lamp light.", "axis_rule": "Keep screen axis."},
        sound={"environment": "Quiet watch shop.", "ambience": "Faint clock ticking."},
        dialogue=[{"speaker_id": "老周", "text": "擦擦，", "start_s": 0.4, "end_s": 2.0}],
    )
    manifest = [item("<Subject 1>", ReferenceUse.SUBJECT, entity_id="老周", role="character")]
    compiled = H3PromptCompiler().compile(
        spec=shot, style=english_style, manifest=manifest, project_language="Chinese",
    )
    assert "<d>[Chinese] 擦擦</d>" in compiled.prompt
    manifest_payload = builtin_manifest()
    pack = RulePack(id=PACK_ID, manifest=manifest_payload, sha256=digest(manifest_payload))
    req = RenderRequest(
        idempotency_key="k",
        shot_id="s",
        shot_revision=1,
        shot_spec=shot,
        style_version_id=shot.style_version_id,
        style_snapshot=english_style,
        style_hash=digest(english_style.model_dump(mode="json")),
        generation_mode=compiled.mode,
        expected_duration_s=shot.duration_target_s,
        reference_manifest=manifest,
        compiled_prompt=compiled.prompt,
        compiled_prompt_sections=compiled.sections,
        prompt_sha256=compiled.prompt_sha256,
        compiler_version=compiled.compiler_version,
        rule_version=compiled.rule_version,
        prompt_skill_hash=SKILL_SHA256,
        rulepack_id=pack.id,
        rulepack_hash=pack.sha256,
    )
    report = H3RulebookRegistry().validate(req, pack)
    assert report.passed, report.violations


def test_scoped_story_facts_omit_off_cast_bios_and_off_shot_props():
    """Off-cast character bios and off-shot props in project-wide locked_story_facts
    must never leak into a single-character shot's H3 detailed_description."""
    zh_facts = [
        "欧洲中世纪西方暗黑奇幻世界观，禁止现代物件",
        "艾德里安：35岁男性骑士，左颧骨旧剑疤，深墨绿斗篷",
        "莫瑞甘：28岁女性守望者，银白色高盘长编发，暗紫色短斗篷",
        "道具：符文长剑（冰蓝魔纹）、黄铜提灯（琥珀圣火）",
        "全片共两场，精准拆分为8个镜头",
    ]
    en_facts = [
        "European medieval dark-fantasy world; no modern objects.",
        "Adrian: 35-year-old male knight, left-cheekbone scar, dark-green cloak.",
        "Morrigan: 28-year-old female watcher, silver-white braided updo, dark-purple short cloak.",
        "Props: rune sword (ice-blue sigils), brass lantern (amber sacred flame).",
        "Two scenes in total, divided into 8 shots.",
    ]
    tokens = {"{{C1}}": "艾德里安", "{{C2}}": "莫瑞甘"}

    # Shot 1: Adrian alone, no sword, no lantern -> Morrigan bio + both props + meta omitted.
    shot1 = spec(
        characters=["艾德里安"],
        character_tokens=tokens,
        story_beat="{{C1}} strides into the rain-lashed courtyard.",
        locked_story_facts=zh_facts,
        prompt_story_facts=en_facts,
    )
    m1 = [
        item("<Subject 1>", ReferenceUse.SUBJECT, entity_id="艾德里安", role="艾德里安").model_copy(
            update={"asset_role": "character"}
        ),
        item("<Picture 2>", ReferenceUse.PICTURE, entity_id="古堡庭院").model_copy(
            update={"asset_role": "scene"}
        ),
    ]
    c1 = compile_prompt(m1, shot=shot1)
    desc1 = c1.sections["detailed_description"]
    assert "European medieval dark-fantasy world" in desc1
    assert "Adrian: 35-year-old male knight" in desc1
    assert "Morrigan" not in desc1
    assert "rune sword" not in desc1
    assert "brass lantern" not in desc1
    assert "divided into 8 shots" not in desc1

    # Shot 3: Adrian draws rune sword (no Morrigan, no brass lantern) -> keeps rune sword only.
    shot3 = spec(
        characters=["艾德里安"],
        character_tokens=tokens,
        story_beat="{{C1}} draws the rune sword.",
        locked_story_facts=zh_facts,
        prompt_story_facts=en_facts,
    )
    m3 = [
        *m1,
        item("<Picture 3>", ReferenceUse.PICTURE, entity_id="符文长剑", role="符文长剑").model_copy(
            update={"asset_role": "prop"}
        ),
    ]
    desc3 = compile_prompt(m3, shot=shot3).sections["detailed_description"]
    assert "Props: rune sword (ice-blue sigils)." in desc3
    assert "brass lantern" not in desc3
    assert "Morrigan" not in desc3

    # Shot 5: Morrigan alone with brass lantern -> omits Adrian and rune sword.
    shot5 = spec(
        characters=["莫瑞甘"],
        character_tokens=tokens,
        story_beat="{{C2}} walks out of the mist holding a lantern.",
        locked_story_facts=zh_facts,
        prompt_story_facts=en_facts,
    )
    m5 = [
        item("<Subject 1>", ReferenceUse.SUBJECT, entity_id="莫瑞甘", role="莫瑞甘").model_copy(
            update={"asset_role": "character"}
        ),
        item("<Picture 2>", ReferenceUse.PICTURE, entity_id="圣殿祭坛").model_copy(
            update={"asset_role": "scene"}
        ),
        item("<Picture 3>", ReferenceUse.PICTURE, entity_id="黄铜提灯", role="黄铜提灯").model_copy(
            update={"asset_role": "prop"}
        ),
    ]
    desc5 = compile_prompt(m5, shot=shot5).sections["detailed_description"]
    assert "Morrigan: 28-year-old female watcher" in desc5
    assert "Props: brass lantern (amber sacred flame)." in desc5
    assert "Adrian" not in desc5
    assert "rune sword" not in desc5


def test_scoped_story_facts_keep_labelled_world_facts_with_unfamiliar_prefixes():
    """Scoping may drop a person's bio, never a world fact whose label it does not know.

    The scene here tokenises only its own cast, so an off-scene character's bio is
    recognised by its *content* (an age or sex description), not by "the prefix is
    not in a whitelist": "Era:" and "Tone:" are not whitelisted either, and a rule
    that dropped every unfamiliar label would silently erase the world of another
    script.
    """
    zh_facts = [
        "时代：欧洲中世纪晚期，战火初歇",
        "基调：阴郁压抑的暗黑奇幻",
        "维伦：35岁男性牧师，灰色粗布法袍",
        "艾德里安：35岁男性骑士，左颧骨旧剑疤",
    ]
    en_facts = [
        "Era: late medieval Europe, the war freshly ended.",
        "Tone: grim, oppressive dark fantasy.",
        "Velen: 35-year-old male priest in a grey rough-cloth robe.",
        "Adrian: 35-year-old male knight, left-cheekbone scar.",
    ]
    shot = spec(
        characters=["艾德里安"],
        character_tokens={"{{C1}}": "艾德里安"},
        story_beat="{{C1}} stands alone in the rain.",
        locked_story_facts=zh_facts,
        prompt_story_facts=en_facts,
    )
    manifest = [
        item("<Subject 1>", ReferenceUse.SUBJECT, entity_id="艾德里安", role="艾德里安").model_copy(
            update={"asset_role": "character"}
        ),
        item("<Picture 2>", ReferenceUse.PICTURE, entity_id="古堡庭院").model_copy(
            update={"asset_role": "scene"}
        ),
    ]
    desc = compile_prompt(manifest, shot=shot).sections["detailed_description"]
    assert "Era: late medieval Europe, the war freshly ended." in desc
    assert "Tone: grim, oppressive dark fantasy." in desc
    assert "Adrian: 35-year-old male knight" in desc
    assert "Velen" not in desc, "a bio of a character this scene never tokenised still leaks"


def test_fact_scoping_lands_in_220_and_stored_210_requests_still_replay():
    """New prompt behaviour goes into the next compiler version; stored requests replay.

    The project's rule after two version-drift incidents (1.3.0, 1.4.0): behaviour
    never changes under a released version.  Scoping the locked story facts is new
    behaviour, so it belongs to 2.2.0; a request frozen under the released 2.1.0
    must still replay byte-exactly through the single provenance verifier, or an
    upgrade would refuse every queued job that was compiled before it.
    """
    from types import SimpleNamespace

    from shine.h3.compiler import COMPILER_VERSION, LEGACY_ANCHORED_TOKENS_COMPILER_VERSION
    from shine.h3.rulebook import recompile_for_provenance

    compiler = H3PromptCompiler()
    shot = spec(
        characters=["艾德里安"],
        character_tokens={"{{C1}}": "艾德里安", "{{C2}}": "莫瑞甘"},
        story_beat="{{C1}} strides into the rain-lashed courtyard.",
        locked_story_facts=[
            "欧洲中世纪西方暗黑奇幻世界观，禁止现代物件",
            "艾德里安：35岁男性骑士，左颧骨旧剑疤，深墨绿斗篷",
            "莫瑞甘：28岁女性守望者，银白色高盘长编发，暗紫色短斗篷",
            "道具：符文长剑（冰蓝魔纹）、黄铜提灯（琥珀圣火）",
            "全片共两场，精准拆分为8个镜头",
        ],
        prompt_story_facts=[
            "European medieval dark-fantasy world; no modern objects.",
            "Adrian: 35-year-old male knight, left-cheekbone scar, dark-green cloak.",
            "Morrigan: 28-year-old female watcher, silver-white braided updo, dark-purple short cloak.",
            "Props: rune sword (ice-blue sigils), brass lantern (amber sacred flame).",
            "Two scenes in total, divided into 8 shots.",
        ],
    )
    manifest = [
        item("<Subject 1>", ReferenceUse.SUBJECT, entity_id="艾德里安", role="艾德里安").model_copy(
            update={"asset_role": "character"}
        ),
        item("<Picture 2>", ReferenceUse.PICTURE, entity_id="古堡庭院").model_copy(
            update={"asset_role": "scene"}
        ),
    ]
    released = compiler.compile(
        spec=shot, style=STYLE, manifest=manifest, project_language="Chinese",
        compiler_version=LEGACY_ANCHORED_TOKENS_COMPILER_VERSION,
    )
    scoped = compile_prompt(manifest, shot=shot)

    # The current version is 2.2.0 and it is the one that scopes.
    assert COMPILER_VERSION == "shine-h3-compiler/2.2.0"
    assert scoped.compiler_version == COMPILER_VERSION
    released_desc = released.sections["detailed_description"]
    assert "Morrigan: 28-year-old female watcher" in released_desc
    assert "brass lantern" in released_desc
    assert "divided into 8 shots" in released_desc
    assert "Morrigan" not in scoped.sections["detailed_description"]
    assert "brass lantern" not in scoped.sections["detailed_description"]
    assert released.prompt_sha256 != scoped.prompt_sha256

    def frozen(compiled, version) -> SimpleNamespace:
        return SimpleNamespace(
            compiler_version=version, shot_spec=shot, style_snapshot=STYLE, reference_manifest=manifest,
            generation_mode=compiled.mode, project_language="Chinese", compiled_prompt=compiled.prompt,
            prompt_sha256=compiled.prompt_sha256, compiled_prompt_sections=compiled.sections,
        )

    # A request stored under either version replays byte-exactly, with no fallback variant.
    for stored, version in ((released, LEGACY_ANCHORED_TOKENS_COMPILER_VERSION), (scoped, COMPILER_VERSION)):
        replayed, variant = recompile_for_provenance(frozen(stored, version))
        assert variant is None
        assert replayed.prompt == stored.prompt
        assert replayed.prompt_sha256 == stored.prompt_sha256
    # The scoped bytes never existed under the released 2.1.0: labelled as 2.1.0 they do not replay.
    mislabelled = frozen(scoped, LEGACY_ANCHORED_TOKENS_COMPILER_VERSION)
    replayed, _variant = recompile_for_provenance(mislabelled)
    assert replayed.prompt != mislabelled.compiled_prompt
