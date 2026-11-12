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


def test_normalisation_keeps_content_distinct():
    assert _normalised_dialogue("……好吃。") == _normalised_dialogue("… 好吃。")
    assert _normalised_dialogue("好吃。") != _normalised_dialogue("不好吃。")