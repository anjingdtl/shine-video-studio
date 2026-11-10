"""Stage C: the producer writes the appearance brief for one requirement.

The material path calls the producer itself instead of borrowing the film
dispatcher: a material brief is written before the Style Bible is frozen and
it has nothing to do with the H3 rule pack.  These tests drive that narrow
call, its single permitted correction and the failures that must be named
rather than retried.
"""

from __future__ import annotations

import copy
import json

import pytest
from sqlalchemy import select

from shine.comfy.workflow import graph_hash
from shine.comfyops.models import (
    PLAN_PURPOSE_ASSET_PREPARATION,
    AssetRequirement,
    PreProductionPlan,
)
from shine.llm.base import LLMFormatError, LLMTimeout
from shine.llm.gateway import LLMGateway
from shine.materialprep import brief, image_prompts
from shine.materialprep.models import MaterialItem, MaterialRun
from shine.models import AgentArtifact, AgentTaskRecord, ProjectDocument
from tests.conftest import PNG

BRIEF_ANSWER = {
    "schema_version": "1.0",
    "appearance": "a man in his thirties with short black hair, wearing a long dark wool coat",
    "must_keep": ["1930s cut"],
    "avoid": ["modern sports watch"],
    "questions": [],
}


def _project(client) -> str:
    return client.post("/api/v1/projects", json={"title": "雾港"}).json()["id"]


def _route(client, *, kind: str = "mock", base_url: str = "") -> str:
    body = {"name": f"producer-{kind}", "kind": kind, "model": "producer"}
    if base_url:
        body["base_url"] = base_url
    profile = client.post("/api/v1/provider-profiles", json=body)
    assert profile.status_code == 201, profile.text
    route = client.put(
        "/api/v1/agent-routes",
        json={"role": "producer", "provider_profile_id": profile.json()["id"]},
    )
    assert route.status_code == 200, route.text
    return profile.json()["id"]


def _responder(monkeypatch, handler) -> list:
    """Route the Worker gateway to a test responder and record the calls."""
    calls: list = []

    def responder(messages, schema):
        calls.append(messages)
        return handler(len(calls))

    def factory(secrets):
        return LLMGateway(secrets, mock_responder=responder)

    monkeypatch.setattr(brief, "LLMGateway", factory)
    return calls


def _item(db, project_id: str, *, description: str = "三十岁上下，长风衣。"):
    document = ProjectDocument(project_id=project_id, filename="script.txt", content_type="text/plain",
                               sha256=graph_hash({"text": description}), source_format="txt", text=description)
    db.add(document)
    db.flush()
    plan_payload = {
        "purpose": PLAN_PURPOSE_ASSET_PREPARATION,
        "document_id": document.id,
        "script_hash": document.sha256,
        "request_hash": "rq",
        "scene_preparations": [
            {
                "schema_version": "1.0",
                "scene_key": "S1",
                "summary": "林知远在雨夜的码头等待。",
                "locked_facts": ["时代：1930年代"],
                "requirements": [],
                "questions": [],
            }
        ],
        "requirements": [],
        "visual_direction": None,
        "confirmation": {"confirmation_id": "c" * 64, "confirmation_hash": "d" * 64},
        "manifest": None,
    }
    plan = PreProductionPlan(
        project_id=project_id,
        payload=plan_payload,
        sha256=graph_hash(plan_payload),
        status="CONFIRMED",
        purpose=PLAN_PURPOSE_ASSET_PREPARATION,
    )
    db.add(plan)
    db.flush()
    requirement_payload = {
        "family": "character",
        "entity_id": "林知远",
        "description": description,
        "critical": True,
        "strategy": "generate",
        "scene_keys": ["S1"],
        "brief_note": "风衣要旧",
    }
    requirement = AssetRequirement(
        project_id=project_id,
        plan_id=plan.id,
        payload=requirement_payload,
        sha256=graph_hash(requirement_payload),
        status="CONFIRMED",
    )
    db.add(requirement)
    db.flush()
    run_payload = {"kind": "production", "rights_id": "r", "requirement_ids": [requirement.id]}
    run = MaterialRun(
        project_id=project_id,
        plan_id=plan.id,
        confirmation_id="c" * 64,
        confirmation_hash="d" * 64,
        idempotency_key="k",
        request_hash="rq",
        mode="real",
        status="QUEUED",
        payload=run_payload,
        sha256=graph_hash(run_payload),
    )
    db.add(run)
    db.flush()
    item_payload = {
        "family": "character",
        "entity_id": "林知远",
        "visual_direction": {"era": "1930s"},
        "seed": 1,
        "kind": "production",
    }
    item = MaterialItem(
        run_id=run.id,
        project_id=project_id,
        requirement_id=requirement.id,
        generation_revision=1,
        status="QUEUED",
        payload=item_payload,
        sha256=graph_hash(item_payload),
    )
    db.add(item)
    db.flush()
    db.commit()
    return item
def test_the_producer_writes_the_brief_and_the_item_carries_it(api, monkeypatch):
    client, factory, settings = api
    project_id = _project(client)
    _route(client)
    calls = _responder(monkeypatch, lambda _n: copy.deepcopy(BRIEF_ANSWER))
    with factory() as db:
        item = _item(db, project_id)
        item_id, version = item.id, item.state_version

    result = brief.ensure_brief(factory, settings, item_id, "worker-1", version=version)

    assert result["ready"] is True
    assert result["brief"]["appearance"] == BRIEF_ANSWER["appearance"]
    assert len(calls) == 1
    with factory() as db:
        row = db.get(MaterialItem, item_id)
        assert row.status == "BRIEF_READY"
        assert row.payload["brief"]["appearance"] == BRIEF_ANSWER["appearance"]
        assert row.brief_artifact_id
        artifacts = db.scalars(
            select(AgentArtifact).where(AgentArtifact.kind == "MaterialImageBrief")
        ).all()
        assert len(artifacts) == 1
        assert artifacts[0].producer_role == "producer"
        tasks = db.scalars(
            select(AgentTaskRecord).where(AgentTaskRecord.role_id == "producer")
        ).all()
        assert len(tasks) == 1
        assert tasks[0].status == "SUCCEEDED"
        assert tasks[0].trace["tool"] == "material_image_brief"


def test_requirement_upload_binds_master_and_validates_format_before_reuse(api):
    client, factory, _ = api
    project_id = _project(client)
    with factory() as db:
        item = _item(db, project_id)
        requirement = db.get(AssetRequirement, item.requirement_id)
        requirement_id, requirement_hash = requirement.id, requirement.sha256
    path = f"/api/v1/projects/{project_id}/material-preparation/requirements/{requirement_id}/upload"
    action = {"x-shine-local-action": "1"}
    data = {"expected_requirement_hash": requirement_hash}
    first = client.post(path, headers=action, data=data, files={"file": ("test.png", PNG, "image/png")})
    assert first.status_code == 201, first.text
    asset = first.json()
    assert asset["approved"] and asset["locked"]
    replay = client.post(path, headers=action, data=data, files={"file": ("test.png", PNG, "image/png")})
    assert replay.status_code == 201 and replay.json()["id"] == asset["id"]
    wrong = client.post(path, headers=action, data=data, files={"file": ("test.jpg", PNG, "image/jpeg")})
    assert wrong.status_code == 415
    assert wrong.json()["detail"] == "File extension does not match the image signature"
    with factory() as db:
        requirement = db.get(AssetRequirement, requirement_id)
        assert requirement.payload["bound_asset_id"] == asset["id"]
        assert requirement.payload["bound_asset_sha256"] == asset["sha256"]


@pytest.mark.parametrize("transport_validates", [False, True])
def test_a_schema_error_earns_exactly_one_correction(api, monkeypatch, transport_validates):
    """One targeted correction, never a loop."""
    client, factory, settings = api
    project_id = _project(client)
    _route(client)

    def handler(call_number):
        if call_number == 1:
            if transport_validates:
                raise LLMFormatError("Schema mismatch", details={
                    "status": 200, "path": "appearance", "validator": "minLength",
                })
            return {"schema_version": "1.0", "appearance": ""}
        return copy.deepcopy(BRIEF_ANSWER)

    calls = _responder(monkeypatch, handler)
    with factory() as db:
        item = _item(db, project_id)
        item_id, version = item.id, item.state_version

    result = brief.ensure_brief(factory, settings, item_id, "worker-1", version=version)

    assert result["ready"] is True
    assert len(calls) == 2
    # The correction carries the broken field, the validator and the task.
    correction = calls[1][1].content
    assert "schema_errors" in correction
    assert "appearance" in correction
    assert "validator" in correction
    with factory() as db:
        tasks = db.scalars(
            select(AgentTaskRecord).where(AgentTaskRecord.role_id == "producer")
        ).all()
        assert tasks[0].trace["correction_count"] == 1


def test_overlong_appearance_gets_one_length_correction_then_keeps_complete_sentences(api, monkeypatch):
    client, factory, settings = api
    project_id = _project(client)
    _route(client)
    sentence = "He wears a dark wool coat with a weathered brass clasp."
    appearance = " ".join([sentence] * 40)
    calls = _responder(monkeypatch, lambda _n: {**BRIEF_ANSWER, "appearance": appearance})
    with factory() as db:
        item = _item(db, project_id)
        item_id, version = item.id, item.state_version

    result = brief.ensure_brief(factory, settings, item_id, "worker-1", version=version)

    assert result["ready"]
    assert len(calls) == 2
    correction, _ = json.JSONDecoder().raw_decode(calls[1][1].content)
    assert correction["schema_errors"] == [{
        "field": "appearance", "type": "string_too_long",
        "actual_length": len(appearance), "max_length": 1600,
    }]
    shortened = result["brief"]["appearance"]
    assert len(shortened) <= 1600 < len(shortened) + len(sentence) + 1
    assert shortened.endswith(sentence)
    with factory() as db:
        task = db.scalar(select(AgentTaskRecord).where(AgentTaskRecord.role_id == "producer"))
        assert task.trace["appearance_truncated"] is True
        assert task.trace["appearance_original_length"] == len(appearance)


def test_a_second_schema_failure_is_refused_by_name(api, monkeypatch):
    client, factory, settings = api
    project_id = _project(client)
    _route(client)
    calls = _responder(monkeypatch, lambda _n: {"schema_version": "1.0"})
    with factory() as db:
        item = _item(db, project_id)
        item_id, version = item.id, item.state_version

    result = brief.ensure_brief(factory, settings, item_id, "worker-1", version=version)

    assert result["ready"] is False
    assert result["code"] == "SCHEMA_VALIDATION_FAILED"
    assert len(calls) == 2, "the correction is attempted exactly once"
    with factory() as db:
        row = db.get(MaterialItem, item_id)
        assert row.status == "FAILED"
        assert row.error_code == "SCHEMA_VALIDATION_FAILED"


def test_an_over_budget_request_is_refused_before_any_model_call(api, monkeypatch):
    client, factory, settings = api
    project_id = _project(client)
    _route(client)
    calls = _responder(monkeypatch, lambda _n: copy.deepcopy(BRIEF_ANSWER))
    with factory() as db:
        # The budget baseline is a 1M-context reasoning model, so "over budget"
        # now means genuinely more than a million tokens rather than a few
        # thousand characters.
        item = _item(db, project_id, description="字" * 1_200_000)
        item_id, version = item.id, item.state_version

    result = brief.ensure_brief(factory, settings, item_id, "worker-1", version=version)

    assert result["ready"] is False
    assert result["code"] == "LLM_CONTEXT_BUDGET_EXCEEDED"
    assert calls == [], "an over-budget request never reaches the provider"


def test_a_remote_provider_is_refused_without_consent(api, monkeypatch):
    client, factory, settings = api
    project_id = _project(client)
    _route(client, kind="openai_compatible", base_url="https://api.example.com/v1")
    calls = _responder(monkeypatch, lambda _n: copy.deepcopy(BRIEF_ANSWER))
    with factory() as db:
        item = _item(db, project_id)
        item_id, version = item.id, item.state_version

    result = brief.ensure_brief(factory, settings, item_id, "worker-1", version=version)

    assert result["ready"] is False
    assert result["code"] == "REMOTE_AGENT_CONSENT_REQUIRED"
    assert calls == [], "script text is never sent to a remote provider"


def test_an_unknown_external_outcome_is_never_replayed(api, monkeypatch):
    client, factory, settings = api
    project_id = _project(client)
    _route(client)

    def handler(_n):
        raise LLMTimeout("no answer", details={"external_outcome": "UNKNOWN"})

    calls = _responder(monkeypatch, handler)
    with factory() as db:
        item = _item(db, project_id)
        item_id, version = item.id, item.state_version

    result = brief.ensure_brief(factory, settings, item_id, "worker-1", version=version)

    assert result["ready"] is False
    assert result["status"] == "NEEDS_REVIEW"
    assert len(calls) == 1, "an unknown outcome is never answered a second time"
    with factory() as db:
        row = db.get(MaterialItem, item_id)
        assert row.status == "NEEDS_REVIEW"
        tasks = db.scalars(
            select(AgentTaskRecord).where(AgentTaskRecord.role_id == "producer")
        ).all()
        assert tasks[0].status == "NEEDS_REVIEW"
        assert tasks[0].trace["external_outcome"] == "UNKNOWN"
        assert tasks[0].trace["automatic_replay"] is False


def test_the_chinese_script_name_does_not_break_the_prompt():
    """A Chinese name is a source identity, not prompt prose."""
    from shine.materialprep.contracts import MaterialImageBrief

    answer = MaterialImageBrief.model_validate(BRIEF_ANSWER)
    composed = image_prompts.compose("character", answer, requirement_name="林知远")
    assert "林知远" not in composed.prompt
    assert composed.subject_omitted == "not_prompt_language"
    assert composed.as_evidence()["subject_omitted"] == "not_prompt_language"
    latin = image_prompts.compose("character", answer, requirement_name="Lin Zhiyuan")
    # v3 carries the Latin name as the opening of the subject sentence instead
    # of a "subject:" label, which the provider reads as literal text.
    assert "Lin Zhiyuan," in latin.prompt
    assert latin.subject_omitted is None
