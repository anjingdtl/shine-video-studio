"""WP7 decomposition and one-shot recovery against the real task ledger."""

import json

import pytest
from sqlalchemy import select

from shine.agents.crew_decompose import CrewCalls, decompose_scriptwriter, source_fragments
from shine.agents.film_contracts import FilmRunRequest, ScriptBreakdown
from shine.h3.rulebook import digest
from shine.literary.models import LLMCallAttempt
from shine.llm.base import LLMResponse, LLMTimeout, text_provider_snapshot
from shine.llm.gateway import LLMGateway
from shine.llm.secrets import SecretStore
from shine.models import AgentTaskRecord
from tests.conftest import create_project

SCENE = "Clockmaker opens the shop.\r\n\r\nA guest enters.\nThe clock stops."
LOCAL = {"x-shine-local-action": "1"}


def test_source_fragments_preserve_original_text():
    assert "".join(source_fragments(SCENE)) == SCENE


def test_timeout_cascades_to_parent_and_retries_only_the_unknown_child(api, monkeypatch):
    client, factory, settings = api
    project = create_project(client, "Clock shop")
    parent_path = f"/api/v1/projects/{project}/film-runs"
    profile_id = client.post("/api/v1/provider-profiles", json={
        "name": "crew", "kind": "openai_compatible", "model": "crew",
        "base_url": "https://example.invalid/v1",
    }).json()["id"]
    assert client.put("/api/v1/agent-routes", json={
        "role": "scriptwriter", "provider_profile_id": profile_id,
    }).status_code == 200
    gateway = LLMGateway(SecretStore(settings.secret_store_dir))
    sent = []
    fail = True

    def complete(db, role, *, precompiled, on_progress, budget):
        assert role == "scriptwriter"
        on_progress("WAITING_PROVIDER")
        data, _ = json.JSONDecoder().raw_decode(precompiled.user)
        title = precompiled.schema["title"]
        if title == "ScriptStructure":
            sent.append("structure")
            answer = {"character_names": {}, "prompt_story_facts": [],
                      "ranges": [{"id": "b1", "last_fragment": 2},
                                 {"id": "b2", "last_fragment": len(data["fragments"])}]}
        else:
            sent.append(data["beat_id"])
            if data["beat_id"] == "b2" and fail:
                raise LLMTimeout("provider connection ended")
            if data["beat_id"] == "b1":
                assert "A guest enters." in data["beat_text"]
                assert "The clock stops." not in data["beat_text"]
            answer = {"id": data["beat_id"], "description": "An action in the clock shop."}
        on_progress("VALIDATING_RESULT")
        return LLMResponse("", answer, "crew", 1800, {
            "completion_tokens_details": {"reasoning_tokens": 8},
        })

    monkeypatch.setattr(gateway, "complete_for_role", complete)
    monkeypatch.setattr("shine.agents.crew_decompose.crew_call_delay", lambda: 0)

    def parent(db):
        profile = gateway.profile_for_role(db, "scriptwriter")
        row = AgentTaskRecord(
            project_id=project, role_id="scriptwriter", input_hash=digest(SCENE),
            asset_manifest_hash=digest([]), rulepack_id="test", rulepack_hash=digest("test"),
            model_profile_id=profile.id, model_id=profile.model, status="RUNNING",
            trace={"phase": "PREPARING", "is_mock": False, "review_provider": text_provider_snapshot(profile)},
        )
        db.add(row)
        db.commit()
        return row

    def execute(db, row):
        calls = CrewCalls(db, gateway, row, 8192, before_send=lambda: None, escalate=False)
        body = FilmRunRequest(brief=SCENE)
        return calls.response(decompose_scriptwriter(calls, body, {"assets": []}, "Write one beat.", ""))

    with factory() as db:
        row = parent(db)
        with pytest.raises(LLMTimeout) as err:
            execute(db, row)
        assert err.value.details["external_outcome"] == "UNKNOWN"
        child_id = row.trace["blocked_child_task_id"]
        child = db.get(AgentTaskRecord, child_id)
        assert child.status == "NEEDS_REVIEW"
        assert child.trace["parent_task_id"] == row.id
        assert child.trace["external_outcome"] == "UNKNOWN"
        row.status = "NEEDS_REVIEW"
        row.trace = {**row.trace, "external_outcome": err.value.details["external_outcome"]}
        db.commit()
        row_id = row.id
        version = row.state_version

    denied = client.post(f"{parent_path}/{row_id}/resend-unknown", headers=LOCAL,
                         json={"expected_state_version": version})
    assert denied.status_code == 409
    checked = client.post(f"{parent_path}/{row_id}/reconcile", headers=LOCAL,
                          json={"expected_state_version": version})
    assert checked.status_code == 200, checked.text
    assert checked.json()["child_task_id"] == child_id
    allowed = client.post(f"{parent_path}/{row_id}/resend-unknown", headers=LOCAL,
                          json={"expected_state_version": checked.json()["state_version"]})
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["child_task_id"] == child_id
    assert client.post(f"{parent_path}/{row_id}/resend-unknown", headers=LOCAL,
                       json={"expected_state_version": allowed.json()["state_version"]}).status_code == 409

    fail = False
    with factory() as db:
        row = parent(db)
        response = execute(db, row)
        assert [beat.id for beat in ScriptBreakdown.model_validate(response.parsed).beats] == ["b1", "b2"]
        assert sent == ["structure", "b1", "b2", "b2"]
        assert len(row.trace["child_task_ids"]) == 3
        assert response.usage["reasoning_tokens"] == 8
        assert db.get(AgentTaskRecord, child_id).trace["external_outcome"] == "UNKNOWN"
        attempts = db.scalars(select(LLMCallAttempt).where(LLMCallAttempt.project_id == project)).all()
        assert len(attempts) == 4
        assert sum(attempt.external_outcome == "UNKNOWN" for attempt in attempts) == 1
    runs = client.get(parent_path).json()
    assert all("checkpoint" not in run["trace"] for run in runs)
