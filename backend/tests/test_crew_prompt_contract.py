"""The crew prompts must be executable: the plan a prompt describes is the plan the server accepts.

These tests are the regression net for the two real chain failures of
2026-11-04 (task ``5cb3a8fc``/``170afa21``: ``EDITORIAL_TIMING_CONFLICT``;
task ``b6f2bda9``/``31ead07a``: ``LLM_FORMAT``).  Both came from the same
cause: the director was asked to invent a segmentation the server had already
computed, and two sentences of the prompt invited it to deviate.
"""

from __future__ import annotations

import copy
import json

import httpx
import pytest
from sqlalchemy import select

from shine.agents.crew_prompts import H3_ALIGNMENT, crew_prompt
from shine.agents.editorial_timing import (
    editorial_timing_blocker,
    editorial_timing_plan,
    editorial_timing_preview,
)
from shine.llm.base import ChatMessage, LLMFormatError
from shine.models import AgentTaskRecord
from tests.test_film_crew import BRIEF, FACTS
from tests.test_storyboard import _storyboard_request, _studio_with_chain

# The cut table of a five-shot script: the shape of the real 2026-11-04 run.
CUT_TABLE = (
    "\n\n| # | 切点 | 时长 |\n|---|---|---|\n"
    "| 1 | 0.000 | 2.40s |\n"
    "| 2 | 2.400 | 1.90s |\n"
    "| 3 | 4.300 | 1.70s |\n"
    "| 4 | 6.000 | 1.70s |\n"
    "| 5 | 7.700 | 2.30s |\n"
)
BEAT_IDS = ["B1", "B2", "B3", "B4", "B5"]


def _brief() -> str:
    return BRIEF + CUT_TABLE


def _beats() -> list[dict]:
    """One beat per editorial shot, in table order, with fitting dialogue."""
    return [
        {
            "id": "B1",
            "description": "The woman waits beside the sealed letter as the room lights up.",
            "characters": ["Lin"],
            "dialogue": [{"speaker_id": "Lin", "text": "你来了。", "start_s": 0.5, "end_s": 1.5}],
        },
        {"id": "B2", "description": "She looks up at the door.",
         "characters": ["Lin"], "dialogue": []},
        {"id": "B3", "description": "She steps closer to the table.",
         "characters": ["Lin"], "dialogue": []},
        {"id": "B4", "description": "The camera moves in on her face.",
         "characters": ["Lin"], "dialogue": []},
        {"id": "B5", "description": "She settles back into the chair.",
         "characters": ["Lin"], "dialogue": []},
    ]


def _plan_from_context(context: dict, registry: dict, *, tail: float = 0.0) -> dict:
    """The DirectorialPlan a model builds when it obeys the prompt.

    Every number comes from the plan the server put in the context; only the
    prose is the model's own.  ``tail`` reproduces the one deviation that
    broke the real run: a duration stretched by a natural tail.
    """
    plan = context["editorial_timing_plan"]
    shots = []
    for segment in plan["segments"]:
        shots.append({
            "id": f"I{segment['index']}",
            "beat_id": segment["opening_beat_id"],
            "visual_description": "The lead performer waits beside a sealed letter in soft daylight.",
            "duration_s": segment["duration_s"] + tail,
            "asset_ids": [registry["lin_asset"]],
            "cuts": [
                {
                    "beat_id": cut["beat_id"],
                    "start_s": cut["start_s"],
                    "visual_description": "The lead performer turns her head toward the door.",
                }
                for cut in segment["cuts"]
            ],
        })
    return {"shots": shots}


def _plan_shots(context: dict) -> list[dict]:
    """The director's shots, wherever they sit in the role's input list."""
    for item in context["inputs"]:
        if item["kind"] == "DirectorialPlan":
            return item["payload"]["shots"]
    raise AssertionError("no DirectorialPlan in the context")


def _crew_responder(registry: dict, *, tail: float = 0.0, calls: list | None = None):
    """A crew that obeys the editorial plan it was given."""

    def respond(messages, schema):
        title = schema.get("title")
        if calls is not None:
            calls.append(title)
        if title == "ScriptBreakdown":
            return copy.deepcopy({"locked_story_facts": FACTS, "beats": _beats()})
        if title == "DirectorialPlan":
            context = json.JSONDecoder().raw_decode(messages[1].content)[0]
            return _plan_from_context(context, registry, tail=tail)
        if title == "CameraPlan":
            context = json.JSONDecoder().raw_decode(messages[1].content)[0]
            shots = _plan_shots(context)
            return {"shots": [
                {
                    "intent_id": shot["id"],
                    "composition": "Medium shot, subject on the left third",
                    "movement": "Slow push in with small amplitude at slow speed",
                    "lighting": "Soft key from screen right",
                    "axis": "Preserve the established axis",
                }
                for shot in shots
            ]}
        if title == "ContinuityReport":
            return {"issues": [], "recommendation": "proceed_to_preflight"}
        if title == "EditIntentPlan":
            context = json.JSONDecoder().raw_decode(messages[1].content)[0]
            shots = _plan_shots(context)
            return {
                "schema_version": "1.2",
                "intent_id": "D1",
                "clips": [
                    {
                        "intent_id": shot["id"],
                        "order": index,
                        "planned_duration_s": shot["duration_s"],
                        "transition": "CUT",
                        "continuity_note": "segment",
                    }
                    for index, shot in enumerate(shots, 1)
                ],
                "rhythm": "steady",
                "dialogue_pace": "natural Mandarin pace",
            }
        raise AssertionError(f"unexpected schema {title}")

    return respond


# --- the timing arithmetic itself -------------------------------------------------


def test_five_editorial_shots_get_one_legal_segmentation():
    timing = editorial_timing_preview(_brief())
    assert timing["editorial_shot_count"] == 5
    assert timing["total_s"] == 10.0
    # No three-segment grouping exists for this table, so the fallback groups
    # it into two legal segments instead of leaving the crew with nothing.
    assert timing["segments"] == [
        {"first_shot": 1, "last_shot": 2, "duration_s": 4.3},
        {"first_shot": 3, "last_shot": 5, "duration_s": 5.7},
    ]
    assert editorial_timing_blocker(timing) is None


def test_the_plan_states_the_exact_numbers_the_server_checks():
    timing = editorial_timing_preview(_brief())
    plan = editorial_timing_plan(timing, BEAT_IDS)
    assert [segment["duration_s"] for segment in plan["segments"]] == [4.3, 5.7]
    assert plan["segments"][0]["opening_beat_id"] == "B1"
    assert plan["segments"][0]["cuts"] == [{"editorial_shot": 2, "beat_id": "B2", "start_s": 2.4}]
    assert plan["segments"][1]["opening_beat_id"] == "B3"
    assert plan["segments"][1]["cuts"] == [
        {"editorial_shot": 4, "beat_id": "B4", "start_s": 1.7},
        {"editorial_shot": 5, "beat_id": "B5", "start_s": 3.4},
    ]
    assert sum(segment["duration_s"] for segment in plan["segments"]) == plan["total_s"]


def test_a_cut_table_that_cannot_be_grouped_says_why():
    brief = BRIEF + "\n\n| # | 切点 | 时长 |\n|---|---|---|\n| 1 | 0.000 | 1.00s |\n| 2 | 1.000 | 1.00s |\n"
    timing = editorial_timing_preview(brief)
    assert timing is not None and timing["segments"] == []
    blocker = editorial_timing_blocker(timing)
    assert blocker["code"] == "EDITORIAL_TIMING_UNGROUPABLE"
    assert blocker["editorial_shot_lengths_s"] == [1.0, 1.0]
    assert blocker["min_segment_s"] == 4.0


# --- the prompts ------------------------------------------------------------------


@pytest.mark.parametrize("role", ["scriptwriter", "director", "cinematographer",
                                  "continuity_supervisor", "editor"])
def test_every_crew_prompt_carries_the_official_h3_rules(role):
    prompt = crew_prompt(role, tool="propose")
    for rule in ("H3_ENGLISH", "H3_TIMING", "H3_DIALOGUE", "H3_LIMITS", "H3_LABELS"):
        assert rule in prompt
    assert "subject_definitions" in prompt and "retention_analysis" in prompt
    assert "detailed_description" in prompt
    assert "overall_soundscape" in prompt and "non_diegetic_music" in prompt
    assert H3_ALIGNMENT in prompt


def test_the_director_prompt_states_the_plan_and_forbids_the_two_deviations():
    timing = editorial_timing_preview(_brief())
    plan = editorial_timing_plan(timing, BEAT_IDS)
    prompt = crew_prompt("director", tool="propose", plan=plan)
    # The plan is written out, not merely referenced.
    assert "duration_s is exactly 4.3" in prompt
    assert "start_s exactly 2.4" in prompt
    assert "duration_s is exactly 5.7" in prompt
    assert "start_s exactly 3.4" in prompt
    # The two sentences that made the real run unrepairable are gone.
    assert "suggested groups may be adjusted" not in prompt
    assert "plus a short natural tail" not in prompt
    assert "A shot's duration_s is fixed by the plan" in prompt
    assert "Never extend a shot to give a spoken line more room" in prompt


def test_without_a_cut_table_the_director_may_still_give_a_dialogue_a_tail():
    prompt = crew_prompt("director", tool="propose")
    assert "plus a short natural tail" in prompt
    assert "A shot's duration_s is fixed by the plan" not in prompt
    # Without a computed plan the model previously fragmented one beat into
    # several same-beat cuts, which the dialogue-window check always rejects.
    assert "use exactly one shot per beat, in beat order, and return no cuts at all" in prompt
    assert "never address one beat twice" in prompt


def test_the_director_prompt_states_the_official_reference_rules():
    prompt = crew_prompt("director", tool="propose")
    assert "approved prop or other master materially depicted" in prompt
    assert "shots[].cuts[].visual_description" in prompt
    assert "never list style or last-frame reference images as shot references" in prompt
    # The server treats a dialogue speaker as a required bound character even
    # when it never appears on screen; the prompt must say the same thing.
    assert "master of EVERY speaker whose line a segment contains" in prompt


# --- the chain --------------------------------------------------------------------


def test_a_plan_built_from_the_prompt_is_accepted(studio):
    client, _factory, _tmp, _settings = studio
    project_id, _style, registry = _studio_with_chain(client)
    calls: list[str] = []
    client.app.state.mock_llm_responder = _crew_responder(registry, calls=calls)

    response = _storyboard_request(client, project_id, text=_brief())
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "PROPOSED", body
    assert calls == ["ScriptBreakdown", "DirectorialPlan", "CameraPlan", "ContinuityReport", "EditIntentPlan"]


def test_a_natural_tail_still_breaks_the_chain(studio):
    """The exact deviation of the real run: the server refuses the total."""
    client, _factory, _tmp, _settings = studio
    project_id, _style, registry = _studio_with_chain(client)
    client.app.state.mock_llm_responder = _crew_responder(registry, tail=0.3)

    response = _storyboard_request(client, project_id, text=_brief())
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "REJECTED"
    assert body["failed_role"] == "director"
    assert body["rejections"][0]["rejection_reason_code"] == "EDITORIAL_TIMING_CONFLICT"


def test_an_ungroupable_cut_table_is_refused_without_a_model_call(studio):
    client, factory, _tmp, _settings = studio
    project_id, _style, registry = _studio_with_chain(client)
    calls: list[str] = []
    client.app.state.mock_llm_responder = _crew_responder(registry, calls=calls)
    brief = BRIEF + "\n\n| # | 切点 | 时长 |\n|---|---|---|\n| 1 | 0.000 | 1.00s |\n| 2 | 1.000 | 1.00s |\n"

    response = _storyboard_request(client, project_id, text=brief)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "REJECTED"
    assert body["rejections"][0]["rejection_reason_code"] == "EDITORIAL_TIMING_UNGROUPABLE"
    # Nothing was sent to a model: no answer could have satisfied the request.
    assert calls == []
    with factory() as db:
        task = db.scalar(select(AgentTaskRecord).where(
            AgentTaskRecord.project_id == project_id, AgentTaskRecord.role_id == "scriptwriter"))
        assert task.status == "REJECTED"
        assert task.trace["editorial_refusal"]["code"] == "EDITORIAL_TIMING_UNGROUPABLE"
        assert task.trace["editorial_refusal"]["editorial_shot_lengths_s"] == [1.0, 1.0]


def test_a_beat_count_that_cannot_match_the_table_is_refused_before_the_director(studio):
    client, _factory, _tmp, _settings = studio
    project_id, _style, registry = _studio_with_chain(client)
    calls: list[str] = []

    def respond(messages, schema):
        title = schema.get("title")
        calls.append(title)
        if title == "ScriptBreakdown":
            return copy.deepcopy({"locked_story_facts": FACTS, "beats": _beats()[:3]})
        return _crew_responder(registry)(messages, schema)

    client.app.state.mock_llm_responder = respond
    response = _storyboard_request(client, project_id, text=_brief())
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "REJECTED"
    assert body["rejections"][0]["rejection_reason_code"] == "EDITORIAL_BEAT_COUNT_MISMATCH"
    # The director was never asked: its answer could not have been accepted.
    assert calls == ["ScriptBreakdown"]


# --- the provider format diagnostic ------------------------------------------------


def test_the_provider_keeps_the_field_that_broke_the_contract(monkeypatch):
    from shine.llm.openai_compatible import OpenAICompatibleProvider
    from tests.test_llm_gateway import config

    provider = OpenAICompatibleProvider(config(type("Endpoint", (), {"base_url": "http://127.0.0.1"})()))
    monkeypatch.setattr(provider, "_request", lambda *a, **kw: httpx.Response(200, json={
        "choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"shots": [{"duration_s": 30}]})}}],
    }))
    with pytest.raises(LLMFormatError) as error:
        provider.complete(
            [ChatMessage("user", "x")],
            schema={
                "type": "object",
                "properties": {
                    "shots": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"duration_s": {"type": "number", "maximum": 15}},
                        },
                    },
                },
            },
        )
    assert error.value.details["path"] == "shots/0/duration_s"
    assert error.value.details["validator"] == "maximum"


def test_a_provider_format_failure_keeps_its_diagnostic_in_the_task_record(studio, monkeypatch):
    from shine.llm.gateway import LLMGateway

    client, factory, _tmp, _settings = studio
    project_id, _style, registry = _studio_with_chain(client)
    client.app.state.mock_llm_responder = _crew_responder(registry)
    original = LLMGateway.complete_for_role

    def refuse(self, db, role, **kwargs):
        if role == "director":
            raise LLMFormatError("Model JSON does not match the required schema",
                                 details={"status": 200, "provider_code": 0,
                                          "path": "shots/0/duration_s", "validator": "maximum"})
        return original(self, db, role, **kwargs)

    monkeypatch.setattr(LLMGateway, "complete_for_role", refuse)
    response = _storyboard_request(client, project_id, text=_brief())
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "REJECTED"
    assert body["rejections"][0]["code"] == "LLM_FORMAT"
    with factory() as db:
        task = db.scalar(select(AgentTaskRecord).where(
            AgentTaskRecord.project_id == project_id, AgentTaskRecord.role_id == "director"))
        assert task.error_code == "LLM_FORMAT"
        assert task.trace["provider_schema_path"] == "shots/0/duration_s"
        assert task.trace["provider_schema_validator"] == "maximum"
