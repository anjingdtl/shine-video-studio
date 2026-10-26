"""Unknown remote agent outcomes cannot become rejections or automatic retries."""

import copy
from datetime import timedelta

import pytest
from sqlalchemy import select

from shine.llm.base import LLMTimeout
from shine.models import AgentTaskRecord, utcnow
from tests.test_style_input_assessment import setup_inputs
from tests.test_v13_preparation import HEADERS, post, prep  # noqa: F401


def uncertain(prep, monkeypatch, *, local=False):  # noqa: F811
    client, factory, _ = prep
    project, _, spec, _ = setup_inputs(client)
    client.app.state.settings = prep[2]  # Asset fixture temporarily installs its own operator.
    profile = client.post("/api/v1/provider-profiles", json={
        "name": "synthetic-unknown-text", "kind": "openai_compatible", "model": "fixture-only",
        "base_url": "http://127.0.0.1:19999/v1" if local else "https://text.example/v1", "max_retries": 0,
    }).json()
    client.put("/api/v1/agent-routes", json={"role": "qc_reviewer", "provider_profile_id": profile["id"]})
    calls = []

    def timeout(*a, **kw):
        calls.append(1)
        raise LLMTimeout("sk-secret-response-must-not-be-persisted")

    monkeypatch.setattr("shine.llm.gateway.LLMGateway.complete_for_role", timeout)
    body = {"shot_spec": spec, "allow_remote_text": not local}
    path = f"/api/v1/projects/{project}/film-agents/qc_reviewer/run"
    assert client.post(path, json=body).status_code == 422
    with factory() as db:
        task = db.scalar(select(AgentTaskRecord).where(AgentTaskRecord.project_id == project))
        task_id = task.id
    return client, factory, project, task_id, path, body, calls


def test_nonmock_timeout_is_unknown_and_same_request_does_not_call_again(prep, monkeypatch):  # noqa: F811
    client, factory, _, task_id, path, body, calls = uncertain(prep, monkeypatch)
    with factory() as db:
        task = db.get(AgentTaskRecord, task_id)
        assert task.status == "NEEDS_REVIEW"
        assert task.trace["external_outcome"] == "UNKNOWN"
        assert task.trace["automatic_replay"] is False
        assert "sk-secret" not in str(task.trace)
    result = client.post(path, json=body)
    assert result.status_code == 422
    assert result.json()["detail"] == "AGENT_REQUEST_RECONCILIATION_REQUIRED"
    assert len(calls) == 1


@pytest.mark.parametrize("damage", [None, "legacy-timeout", "local", "active-lease", "wrong-project", "no-auth",
                                  "expired-grant", "changed-snapshot", "changed-provider", "media-call-role"])
def test_explicit_remote_abandon_is_authenticated_bounded_and_never_infers(prep, monkeypatch, damage):  # noqa: F811
    from shine.comfyops.models import ApprovalGrant, ResourceLease
    from tests.conftest import create_project

    client, factory, project, task_id, path, body, calls = uncertain(prep, monkeypatch, local=damage == "local")
    if damage == "legacy-timeout":
        with factory.begin() as db:
            task = db.get(AgentTaskRecord, task_id)
            task.status = "REJECTED"
            task.trace = {k: v for k, v in task.trace.items() if k != "review_contract_sha256"}
    grant = post(client, "/preparation/approval-grants",
                 {"action": "model_reconcile", "target_id": task_id}, operator=True)
    with factory.begin() as db:
        task = db.get(AgentTaskRecord, task_id)
        if damage == "expired-grant":
            db.get(ApprovalGrant, grant["grant_id"]).expires_at = utcnow() - timedelta(hours=1)
        elif damage == "changed-snapshot":
            task.trace = {**task.trace, "synthetic_snapshot_changed": True}
        elif damage == "changed-provider":
            from shine.models import ProviderProfile

            db.get(ProviderProfile, task.model_profile_id).model = "another-checkpoint"
        elif damage == "media-call-role":
            task.role_id = "model_call:VLM:review"
    if damage == "active-lease":
        with factory.begin() as db:
            db.add(ResourceLease(owner="synthetic-expired-but-unreconciled", budget_mb=1, exclusive=True,
                                 priority=1, status="ACTIVE", expires_at=utcnow() - timedelta(hours=1)))
    target = create_project(client) if damage == "wrong-project" else project
    result = client.post(f"/api/v1/projects/{target}/film-runs/{task_id}/abandon-remote-response", json={
        "grant_id": grant["grant_id"], "snapshot": grant["snapshot"],
        "evidence": "Synthetic remote response lost; no quality approval and no replay",
    }, headers={} if damage == "no-auth" else HEADERS)
    if damage not in {None, "legacy-timeout"}:
        assert result.status_code in {401, 403, 404, 409}
        with factory() as db:
            assert not db.get(ApprovalGrant, grant["grant_id"]).used
            assert db.get(AgentTaskRecord, task_id).status == "NEEDS_REVIEW"
    else:
        assert result.status_code == 200, result.text
        with factory() as db:
            task = db.get(AgentTaskRecord, task_id)
            assert task.status == "ABANDONED"
            assert task.trace["external_outcome"] == "UNKNOWN"
            assert task.trace["local_model_unload_claimed"] is False
            assert db.get(ApprovalGrant, grant["grant_id"]).used
        repeat = client.post(path, json=body)
        assert repeat.status_code == 422
        assert repeat.json()["detail"] == "AGENT_REQUEST_REPLAY_BLOCKED"
        alias = client.post("/api/v1/provider-profiles", json={
            "name": "same-wire-new-profile-id", "kind": "openai_compatible", "model": "fixture-only",
            "base_url": "https://text.example/v1",
        }).json()
        client.put("/api/v1/agent-routes", json={"role": "qc_reviewer", "provider_profile_id": alias["id"]})
        assert client.post(path, json=body).json()["detail"] == "AGENT_REQUEST_REPLAY_BLOCKED"
    assert len(calls) == 1


def test_schema_change_cannot_evade_unreconciled_outcome_but_is_new_after_abandon(prep, monkeypatch):  # noqa: F811
    from shine.agents.execution import guard_request
    from shine.models import ProviderProfile

    client, factory, project, task_id, _, _, _ = uncertain(prep, monkeypatch)
    with factory() as db:
        task = db.get(AgentTaskRecord, task_id)
        profile = db.get(ProviderProfile, task.model_profile_id)
        with pytest.raises(ValueError, match="RECONCILIATION_REQUIRED"):
            guard_request(db, project, task.role_id, task.input_hash, profile,
                          text_hash=task.trace["text_input_sha256"], contract_hash="f" * 64)
    grant = post(client, "/preparation/approval-grants",
                 {"action": "model_reconcile", "target_id": task_id}, operator=True)
    result = client.post(f"/api/v1/projects/{project}/film-runs/{task_id}/abandon-remote-response", json={
        "grant_id": grant["grant_id"], "snapshot": grant["snapshot"], "evidence": "Synthetic lost remote response",
    }, headers=HEADERS)
    assert result.status_code == 200
    with factory() as db:
        task = db.get(AgentTaskRecord, task_id)
        guard_request(db, project, task.role_id, task.input_hash, db.get(ProviderProfile, task.model_profile_id),
                      text_hash=task.trace["text_input_sha256"], contract_hash="f" * 64)


def test_concurrent_identical_requests_create_only_one_model_intent(prep, monkeypatch):  # noqa: F811
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from shine.llm.base import LLMResponse

    client, factory, _ = prep
    project, _, spec, response = setup_inputs(client)
    remote = client.post("/api/v1/provider-profiles", json={
        "name": "concurrent-test-only", "kind": "openai_compatible",
        "base_url": "https://text.example/v1", "model": "fixture-only",
    }).json()
    client.put("/api/v1/agent-routes", json={"role": "qc_reviewer", "provider_profile_id": remote["id"]})
    entered, release, calls = Event(), Event(), []

    def model(*a, **kw):
        calls.append(1)
        entered.set()
        assert release.wait(10)
        return LLMResponse(text="synthetic", parsed=response, model="fixture-only", latency_ms=1)

    monkeypatch.setattr("shine.llm.gateway.LLMGateway.complete_for_role", model)
    path = f"/api/v1/projects/{project}/film-agents/qc_reviewer/run"
    body = {"shot_spec": spec, "allow_remote_text": True}
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(client.post, path, json=body)
        try:
            assert entered.wait(5)
            second = client.post(path, json=body)
            assert second.status_code == 422
            assert second.json()["detail"] == "AGENT_REQUEST_RECONCILIATION_REQUIRED"
        finally:
            release.set()
        assert first.result(timeout=10).status_code == 201
    assert len(calls) == 1
    with factory() as db:
        assert len(list(db.scalars(select(AgentTaskRecord).where(AgentTaskRecord.project_id == project)))) == 1


def test_untrusted_response_model_label_is_hashed_not_persisted(prep, monkeypatch):  # noqa: F811
    from shine.llm.base import LLMResponse

    client, factory, _ = prep
    project, _, spec, response = setup_inputs(client)
    monkeypatch.setattr("shine.llm.gateway.LLMGateway.complete_for_role", lambda *a, **kw: LLMResponse(
        text="synthetic", parsed=response, model="sk-secret-transport-label", latency_ms=1,
    ))
    result = client.post(f"/api/v1/projects/{project}/film-agents/qc_reviewer/run", json={"shot_spec": spec})
    assert result.status_code == 201
    assert "sk-secret" not in client.get(f"/api/v1/projects/{project}/film-runs").text
    with factory() as db:
        assert len(db.get(AgentTaskRecord, result.json()["source_task_id"]).trace["response_model_sha256"]) == 64


def test_newer_unresolved_text_task_cannot_fall_back_to_older_usable_artifact(prep, monkeypatch):  # noqa: F811
    from fastapi import HTTPException

    from shine.comfyops.preproduction import enforce
    from shine.llm.base import LLMResponse
    from shine.quality.input_assessment import assessment_for_request
    from shine.schemas import RenderRequest
    from tests.test_film_crew import ENGLISH_STYLE

    client, factory, _ = prep
    project, frozen, spec, response = setup_inputs(client)
    spec["story_beat"] = "The character sits centre-left."
    response["checks"][1].update(status="consistent", shot_text=spec["story_beat"])
    response["recommendation"] = "proceed_to_preflight"
    remote = client.post("/api/v1/provider-profiles", json={
        "name": "synthetic-real-route", "kind": "openai_compatible",
        "base_url": "https://text.example/v1", "model": "fixture-only",
    }).json()
    client.put("/api/v1/agent-routes", json={"role": "qc_reviewer", "provider_profile_id": remote["id"]})
    monkeypatch.setattr("shine.llm.gateway.LLMGateway.complete_for_role", lambda *a, **kw: LLMResponse(
        text="synthetic", parsed=copy.deepcopy(response), model="fixture-only", latency_ms=1,
    ))
    path = f"/api/v1/projects/{project}/film-agents/qc_reviewer/run"
    body = {"shot_spec": spec, "allow_remote_text": True}
    artifact = client.post(path, json=body).json()
    render = RenderRequest(idempotency_key="fixture", shot_id="fixture", shot_revision=1,
                           style_version_id=frozen["id"], style_hash=frozen["sha256"], shot_spec=spec,
                           style_snapshot={**ENGLISH_STYLE, "shot_blocking": "The character sits centre-left."},
                           expected_duration_s=6)
    monkeypatch.setattr("shine.llm.gateway.LLMGateway.complete_for_role",
                        lambda *a, **kw: (_ for _ in ()).throw(LLMTimeout("synthetic timeout")))
    assert client.post(path, json=body).status_code == 422
    with factory() as db, pytest.raises(HTTPException):
        assessment_for_request(db, project, render, select_latest=True)
    render.style_input_assessment_id = artifact["id"]
    render.style_input_assessment_sha256 = artifact["sha256"]
    with factory() as db, pytest.raises(HTTPException):
        enforce(db, project, render_request=render)
