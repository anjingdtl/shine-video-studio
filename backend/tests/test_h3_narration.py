"""The off-screen narrator speaks without owning a picture reference."""

from __future__ import annotations

import pytest

from shine.director.requirements import _character_names
from shine.h3.compiler import H3PromptCompiler
from shine.h3.narrator import is_narrator_speaker
from shine.schemas import (
    DialogueLine,
    GenerationMode,
    ReferenceManifestItem,
    ReferenceUse,
    ShotSpec,
    StyleBible,
)

pytestmark = pytest.mark.unit


def _style() -> StyleBible:
    return StyleBible(
        photography_language="naturalistic handheld coverage",
        shot_blocking="performers stay in their own third",
        color_palette="muted teal and warm amber",
        lighting_tone="soft overcast key",
        lens_texture="35mm film grain",
        editing_rhythm="long takes with sparse cuts",
        sound_performance="restrained natural delivery",
    )


def _manifest() -> list[ReferenceManifestItem]:
    return [ReferenceManifestItem(
        label="<Subject 1>", asset_id="asset-lin", sha256="a" * 64,
        storage_key="assets/lin.png", content_type="image/png",
        use_as=ReferenceUse.SUBJECT, role="Lin", entity_id="Lin", ordinal=0,
    )]


def _spec(*, narrator: bool) -> ShotSpec:
    characters = ["Lin"] + (["旁白"] if narrator else [])
    dialogue = [DialogueLine(speaker_id="Lin", text="你来了。", start_s=0.5, end_s=2.0)]
    if narrator:
        dialogue.append(DialogueLine(speaker_id="旁白", text="三年前的那个雨夜。", start_s=3.0, end_s=5.0))
    return ShotSpec(
        story_beat="Lin stands by the window and turns when the door opens.",
        duration_target_s=8,
        style_version_id="style-1",
        characters=characters,
        dialogue=dialogue,
    )


def test_narrator_names_are_recognised_in_both_scripts():
    assert is_narrator_speaker("旁白")
    assert is_narrator_speaker("Voice-Over")
    assert is_narrator_speaker("narrator")
    assert not is_narrator_speaker("Lin")


def test_narrator_line_compiles_without_a_narrator_reference():
    compiled = H3PromptCompiler().compile(
        spec=_spec(narrator=True), style=_style(), manifest=_manifest(),
        mode_hint=GenerationMode.REF2VA, project_language="Chinese",
    )
    assert "off-screen narrator" in compiled.prompt
    assert "三年前的那个雨夜。" in compiled.prompt


def test_narrator_is_not_a_character_material():
    spec = _spec(narrator=True)
    assert _character_names({"characters": spec.characters,
                             "dialogue": [line.model_dump(mode="json") for line in spec.dialogue]}) == ["Lin"]


def test_visible_character_still_requires_its_reference():
    from shine.h3.compiler import PromptCompilationError

    spec = _spec(narrator=True)
    spec.characters = ["Lin", "旁白", "Zhou"]
    with pytest.raises(PromptCompilationError) as excinfo:
        H3PromptCompiler().compile(
            spec=spec, style=_style(), manifest=_manifest(),
            mode_hint=GenerationMode.REF2VA, project_language="Chinese",
        )
    assert excinfo.value.code == "H3_CHARACTER_REFERENCE_MISSING"
