"""Dialogue and narration settings steer the storyboard without gating it."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from shine.agents.crew_prompts import crew_prompt
from shine.agents.film import validate_output
from shine.agents.film_contracts import ScriptBreakdown
from shine.schemas import StyleBible
from tests.conftest import create_project, style_payload

pytestmark = pytest.mark.integration

BRIEF = "雨夜，巷口。她把信折好放进衣袋。"


def _body():
    return SimpleNamespace(brief=BRIEF, locked_story_facts=[])


def _narrator_proposal() -> ScriptBreakdown:
    return ScriptBreakdown.model_validate({
        "beats": [{
            "id": "B1", "description": "She folds the letter and pockets it.",
            "characters": ["旁白"],
            "dialogue": [{"speaker_id": "旁白", "text": "三年前的那个雨夜。",
                          "start_s": 1, "end_s": 3}],
        }],
    })


def test_crew_prompt_carries_the_narration_choice():
    high = crew_prompt("scriptwriter", tool="storyboard", narration_level="high")
    low = crew_prompt("scriptwriter", tool="storyboard", narration_level="low")
    off = crew_prompt("scriptwriter", tool="storyboard", narration_level="none")
    unset = crew_prompt("scriptwriter", tool="storyboard")
    assert "narrator" in high.lower()
    assert "At most one short narrator line" in low
    assert "switched off" in off
    assert "switched off" not in unset and "narrator line per section" not in unset


def test_narration_off_refuses_a_narrator_line_even_without_a_ratio():
    with pytest.raises(ValueError) as excinfo:
        validate_output("scriptwriter", _narrator_proposal(), _body(), [],
                        {"narration_level": "none", "dialogue_ratio_percent": 0})
    assert str(excinfo.value) == "DIALOGUE_NOT_IN_USER_SCRIPT"


def test_narration_on_allows_a_narrator_line_under_a_zero_dialogue_ratio():
    validate_output("scriptwriter", _narrator_proposal(), _body(), [],
                    {"narration_level": "low", "dialogue_ratio_percent": 0})


def test_narration_setting_survives_a_style_freeze(studio):
    client, _factory, _tmp, _settings = studio
    project_id = create_project(client, "对白旁白")
    draft = client.put(
        f"/api/v1/projects/{project_id}/style/draft",
        json={**style_payload(), "dialogue_ratio_percent": 70, "narration_level": "low"})
    assert draft.status_code == 200, draft.text
    frozen = client.post(f"/api/v1/projects/{project_id}/style/freeze", json={})
    assert frozen.status_code == 201, frozen.text
    assert frozen.json()["snapshot"]["narration_level"] == "low"
    assert frozen.json()["snapshot"]["dialogue_ratio_percent"] == 70


def test_unset_narration_keeps_the_old_style_bytes():
    style = StyleBible(
        photography_language="a", shot_blocking="b", color_palette="c",
        lighting_tone="d", lens_texture="e", editing_rhythm="f", sound_performance="g",
    )
    assert "narration_level" not in style.model_dump(mode="json")
    with_narration = style.model_copy(update={"narration_level": "high"})
    assert with_narration.model_dump(mode="json")["narration_level"] == "high"
