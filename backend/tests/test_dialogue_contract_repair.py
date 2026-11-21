"""Dialogue contract: faithful typography variants pass; invention is named.

The live run blocked a scene on DIALOGUE_NOT_IN_USER_SCRIPT although the model
had copied the source line with different ellipsis/whitespace typography, and
the refusal carried no evidence for the repair retry.  The contract stays
about content: normalised comparison accepts faithful copies, and a real
invention is refused with the exact offending line so the bounded retry can
quote it.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from shine.agents.film import _normalised_dialogue, validate_output
from shine.agents.film_contracts import ScriptBreakdown

BRIEF = "罗兰说：「……好吃。」\n安娜没有回答。"


def _proposal(line_text: str) -> ScriptBreakdown:
    return ScriptBreakdown.model_validate({
        "beats": [{
            "id": "B1", "description": "Anna tastes the cake.",
            "characters": ["安娜"],
            "dialogue": [{"speaker_id": "安娜", "text": line_text, "start_s": 1, "end_s": 2}],
        }],
    })


def _body() -> SimpleNamespace:
    return SimpleNamespace(brief=BRIEF, locked_story_facts=[])


def test_exact_source_line_passes():
    validate_output("scriptwriter", _proposal("……好吃。"), _body(), [], {})


def test_typography_variant_of_a_source_line_passes():
    validate_output("scriptwriter", _proposal("…好吃。"), _body(), [], {})
    validate_output("scriptwriter", _proposal("…… 好吃。"), _body(), [], {})


def test_invented_line_is_refused_with_the_offending_line():
    with pytest.raises(ValueError) as excinfo:
        validate_output("scriptwriter", _proposal("这蛋糕真甜。"), _body(), [],
                        {"dialogue_ratio_percent": 0})
    assert str(excinfo.value) == "DIALOGUE_NOT_IN_USER_SCRIPT"
    assert excinfo.value.details["offending_line"] == "这蛋糕真甜。"
    assert excinfo.value.details["speaker"] == "安娜"


def test_unset_ratio_allows_adapted_dialogue_by_a_source_speaker():
    # No ratio stated: an adapted line by a character the beat and the source
    # both contain, inside the beat window, is allowed (throughput relaxation).
    validate_output("scriptwriter", _proposal("这蛋糕真甜。"), _body(), [], {})


def test_unset_ratio_still_refuses_a_speaker_outside_the_beat():
    proposal = ScriptBreakdown.model_validate({
        "beats": [{
            "id": "B1", "description": "Anna tastes the cake.",
            "characters": ["安娜"],
            "dialogue": [{"speaker_id": "旁白", "text": "这蛋糕真甜。", "start_s": 1, "end_s": 2}],
        }],
    })
    with pytest.raises(ValueError) as excinfo:
        validate_output("scriptwriter", proposal, _body(), [], {})
    assert str(excinfo.value) == "ADAPTED_DIALOGUE_SPEAKER_NOT_IN_SOURCE_BEAT"


def _narrator_proposal(text: str) -> ScriptBreakdown:
    return ScriptBreakdown.model_validate({
        "beats": [{
            "id": "B1", "description": "Anna tastes the cake.",
            "characters": ["安娜", "旁白"],
            "dialogue": [{"speaker_id": "旁白", "text": text, "start_s": 1, "end_s": 2}],
        }],
    })


def test_unauthorized_narration_is_refused_when_narration_none():
    # P0-E: an unset narration_level derives to "none" for new storyboard tasks;
    # an invented narrator line is an invention like any other and is named.
    with pytest.raises(ValueError) as excinfo:
        validate_output("scriptwriter", _narrator_proposal("夜幕降临了。"), _body(), [],
                        {"narration_level": "none"})
    assert str(excinfo.value) == "DIALOGUE_NOT_IN_USER_SCRIPT"
    assert excinfo.value.details["offending_line"] == "夜幕降临了。"


def test_script_authored_narration_still_passes_when_none():
    # A narrator line that is verbatim in the user's script is authorized by the
    # script itself, even with narration switched off — the contract forbids
    # invention, not the user's own narrated source.
    body = SimpleNamespace(brief="旁白：「夜幕降临了。」\n安娜没有回答。", locked_story_facts=[])
    validate_output("scriptwriter", _narrator_proposal("夜幕降临了。"), body, [],
                    {"narration_level": "none"})


def test_normalisation_keeps_content_distinct():
    assert _normalised_dialogue("……好吃。") == _normalised_dialogue("… 好吃。")
    assert _normalised_dialogue("好吃。") != _normalised_dialogue("不好吃。")


def test_cast_character_named_only_by_pronoun_in_the_brief_passes():
    # The live run refused a real cast member's adapted line because the source
    # segment referred to him by pronoun throughout, so his name was not a literal
    # substring of the brief. The resolved production_context cast is the one
    # character fact source: a named cast member is not an invention.
    body = SimpleNamespace(brief="他站在雨里，没有说话。", locked_story_facts=[])
    proposal = ScriptBreakdown.model_validate({
        "beats": [{
            "id": "B1", "description": "A-Yuan waits in the rain.",
            "characters": ["阿远"],
            "dialogue": [{"speaker_id": "阿远", "text": "这雨一时停不了。", "start_s": 1, "end_s": 2}],
        }],
    })
    context = {"production_context": {"characters": [{"name": "阿远", "entity_id": "阿远"}]}}
    validate_output("scriptwriter", proposal, body, [], context)


def test_speaker_outside_both_cast_and_brief_is_still_refused():
    # Relaxing the substring check must not open the door to invention: a speaker
    # the beat lists but neither the resolved cast nor the brief names is refused.
    body = SimpleNamespace(brief="他站在雨里。", locked_story_facts=[])
    proposal = ScriptBreakdown.model_validate({
        "beats": [{
            "id": "B1", "description": "A stranger speaks.",
            "characters": ["阿远", "陌生人"],
            "dialogue": [{"speaker_id": "陌生人", "text": "借个火。", "start_s": 1, "end_s": 2}],
        }],
    })
    context = {"production_context": {"characters": [{"name": "阿远", "entity_id": "阿远"}]}}
    with pytest.raises(ValueError) as excinfo:
        validate_output("scriptwriter", proposal, body, [], context)
    assert str(excinfo.value) == "ADAPTED_DIALOGUE_SPEAKER_NOT_IN_SOURCE_BEAT"
    assert excinfo.value.details["speaker"] == "陌生人"