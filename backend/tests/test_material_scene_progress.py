"""The material scene analysis must be visible live and unblockable by a person.

The prepare call is one long synchronous request.  Before this round the page
only learned the outcome from that response, so a refresh lost every scene
result, and an UNKNOWN scene (connection broke after the request was sent) had
no user-authorized resend entry at all: it stayed blocked forever.
"""
from __future__ import annotations

from sqlalchemy import select

from shine.models import AgentArtifact, AgentTaskRecord, AuditEvent
from tests.test_material_scene_safety import setup_scene
from tests.test_material_scenes import _prepare, _responder


def _status(client, project, doc):
    return client.get(
        f"/api/v1/projects/{project}/material-preparation/scenes/status",
        params={"document_id": doc["id"]},
    )


def test_status_passes_live_scene_progress_through_without_calling_the_provider(api, monkeypatch):
    client, factory, project, doc = setup_scene(api, monkeypatch)
    calls = []

    def counted(messages, schema):
        calls.append(1)
        return _responder(messages, schema)

    client.app.state.mock_llm_responder = counted
    assert _prepare(client, project, doc).status_code == 200
    before = len(calls)
    response = _status(client, project, doc)
    assert response.status_code == 200, response.text
    scene = response.json()["scenes"][0]
    assert scene["status"] == "SUCCEEDED"
    assert scene["task_id"] and scene["state_version"] is not None
    assert len(calls) == before, "the read path never buys a model call"

    # A scene in flight must read as running, with its phase, after a refresh.
    with factory.begin() as db:
        row = db.get(AgentTaskRecord, scene["task_id"])
        db.query(AgentArtifact).filter(AgentArtifact.source_task_id == row.id).delete()
        row.status = "RUNNING"
        row.trace = {**row.trace, "phase": "WAITING_PROVIDER"}
    live = _status(client, project, doc).json()["scenes"][0]
    assert live["status"] == "RUNNING"
    assert live["phase"] == "WAITING_PROVIDER"
    with factory() as db:
        assert db.scalar(select(AgentTaskRecord).where(
            AgentTaskRecord.project_id == project)) is not None
        rows = db.scalars(select(AgentTaskRecord).where(
            AgentTaskRecord.project_id == project)).all()
        assert len(rows) == 1, "reading status never creates a task"


def test_an_unknown_scene_is_resent_once_by_explicit_user_authorization(api, monkeypatch):
    client, factory, project, doc = setup_scene(api, monkeypatch)

    def lost(*_args):
        raise TimeoutError("response lost")

    client.app.state.mock_llm_responder = lost
    unknown = _prepare(client, project, doc).json()["scenes"][0]
    assert unknown["status"] == "NEEDS_REVIEW"
    assert unknown["external_outcome"] == "UNKNOWN"

    scene = _status(client, project, doc).json()["scenes"][0]
    assert scene["external_outcome"] == "UNKNOWN"
    resend = client.post(
        f"/api/v1/projects/{project}/material-preparation/scenes/{unknown['task_id']}/resend-unknown",
        json={"expected_state_version": unknown["state_version"],
              "evidence": "用户确认对结果未知的素材分场发起一次新请求"},
        headers={"x-shine-local-action": "1"},
    )
    assert resend.status_code == 200, resend.text
    assert resend.json()["outcome"] == "USER_AUTHORIZED_NEW_REQUEST"
    with factory() as db:
        old = db.get(AgentTaskRecord, unknown["task_id"])
        assert old.status == "SUPERSEDED"
        assert old.request_identity is None
        assert old.trace["external_outcome"] == "UNKNOWN", "never dressed up as not sent"
        assert old.trace["reconciliation"]["outcome"] == "USER_AUTHORIZED_NEW_REQUEST"
        assert db.scalar(select(AuditEvent).where(
            AuditEvent.event_type == "MATERIAL_SCENE_UNKNOWN_RESEND_AUTHORIZED",
            AuditEvent.entity_id == old.id)) is not None

    again = client.post(
        f"/api/v1/projects/{project}/material-preparation/scenes/{unknown['task_id']}/resend-unknown",
        json={"expected_state_version": resend.json()["state_version"], "evidence": "再来一次"},
        headers={"x-shine-local-action": "1"},
    )
    assert again.status_code == 409
    assert again.json()["detail"] == "MATERIAL_SCENE_NOT_UNKNOWN", "a retired row is history"

    calls = []

    def counted(messages, schema):
        calls.append(1)
        return _responder(messages, schema)

    client.app.state.mock_llm_responder = counted
    result = _prepare(client, project, doc).json()["scenes"][0]
    assert result["status"] == "PREPARED", result
    assert len(calls) == 1, "the authorized scene is asked exactly once more"
    assert result["task_id"] != unknown["task_id"]

    # If that new request itself ends unknown, the single authorized resend is
    # spent: a second override on the new row is refused and no call is bought.
    with factory.begin() as db:
        fresh = db.get(AgentTaskRecord, result["task_id"])
        db.query(AgentArtifact).filter(AgentArtifact.source_task_id == fresh.id).delete()
        fresh.status = "NEEDS_REVIEW"
        fresh.error_code = "LLM_CONNECTION"
        fresh.trace = {**fresh.trace, "external_outcome": "UNKNOWN", "phase": "FAILED"}
        fresh_version = fresh.state_version
    budget = client.post(
        f"/api/v1/projects/{project}/material-preparation/scenes/{result['task_id']}/resend-unknown",
        json={"expected_state_version": fresh_version, "evidence": "再来一次"},
        headers={"x-shine-local-action": "1"},
    )
    assert budget.status_code == 409
    assert budget.json()["detail"] == "MATERIAL_SCENE_UNKNOWN_RESEND_ALREADY_USED"
    blocked = _prepare(client, project, doc).json()["scenes"][0]
    assert blocked["status"] == "NEEDS_REVIEW"
    assert len(calls) == 1, "a spent budget never buys another call"


def test_resend_refuses_a_stale_version_and_a_scene_that_is_not_unknown(api, monkeypatch):
    client, factory, project, doc = setup_scene(api, monkeypatch)
    client.app.state.mock_llm_responder = _responder
    prepared = _prepare(client, project, doc).json()["scenes"][0]
    assert prepared["status"] == "PREPARED"
    refused = client.post(
        f"/api/v1/projects/{project}/material-preparation/scenes/{prepared['task_id']}/resend-unknown",
        json={"expected_state_version": prepared["state_version"], "evidence": "x"},
        headers={"x-shine-local-action": "1"},
    )
    assert refused.status_code == 409
    assert refused.json()["detail"] == "MATERIAL_SCENE_NOT_UNKNOWN"
    stale = client.post(
        f"/api/v1/projects/{project}/material-preparation/scenes/{prepared['task_id']}/resend-unknown",
        json={"expected_state_version": prepared["state_version"] + 5, "evidence": "x"},
        headers={"x-shine-local-action": "1"},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"] == "MATERIAL_SCENE_TASK_STATE_CHANGED"