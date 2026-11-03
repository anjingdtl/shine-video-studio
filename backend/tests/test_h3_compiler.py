from __future__ import annotations

import pytest

from shine.h3.compiler import H3PromptCompiler, PromptCompilationError
from shine.schemas import (
    FrameRole,
    GenerationMode,
    ReferenceManifestItem,
    ReferenceUse,
    ShotSpec,
    StyleBible,
)
from tests.conftest import create_project, create_shot, freeze_style, shot_payload

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


def compile_prompt(manifest, *, shot=None, mode=None):
    return H3PromptCompiler().compile(
        spec=shot or spec(),
        style=STYLE,
        manifest=manifest,
        project_language="Chinese",
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


def test_chinese_locked_facts_require_and_use_explicit_english_prompt_translation():
    original = ["怀表停在十二点"]
    shot = spec(locked_story_facts=original, prompt_story_facts=["The watch is stopped at twelve."])
    compiled = compile_prompt([], shot=shot)
    assert "Locked story fact: The watch is stopped at twelve." in compiled.prompt
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
    assert "approved subject" in compiled.sections["subject_definitions"]
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
    assert "<Subject 1> is the approved reference" in compiled.sections["subject_definitions"]
    assert compiled.sections["summary"].startswith("[reference generation + audio reference]")
    assert "<Subject 1> (appears in [Shot 1]): fully_preserved" in compiled.sections["retention_analysis"]
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
    assert "<Subject 1> is the approved reference for character, shown in <Picture 1>," in definitions
    assert "<Subject 2> is the approved reference for character, shown in <Picture 2>," in definitions
    assert "<Picture 3> is the keyframe reference for [Shot 1]." in definitions
    assert "<Audio 1> is an approved audio reference." in definitions


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
