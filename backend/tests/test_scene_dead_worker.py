"""A worker that dies with a scene request in flight must not look alive.

The scene task used to carry no lease at all, so a row left RUNNING by a dead
worker was indistinguishable from a live one: the outline showed "running",
the scene could never be asked again, and the whole journey stalled.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from shine.models import AgentTaskRecord, utcnow
from tests.test_material_scene_safety import setup_scene
from tests.test_material_scenes import _prepare, _responder


def _dead_scene_task(factory, project, doc, *, phase, external_outcome="UNKNOWN",
                     age_s=4000, status="RUNNING", identity="dead-scene-hash"):
    """A scene task left behind by a worker that died mid-request."""
    scene_key = "S1"
    with factory.begin() as db:
        task = AgentTaskRecord(
            project_id=project, role_id="scriptwriter", input_hash=identity,
            # A retired task keeps its input hash but releases the unique claim.
            request_identity=None if status == "SUPERSEDED" else identity,
            asset_manifest_hash=identity,
            rulepack_id="material-script-prepare", rulepack_hash="x",
            model_profile_id="x", model_id="x", schema_version="1.0", status=status,
            trace={"tool": "script_prepare", "scene_key": scene_key, "document_id": doc["id"],
                   "document_sha256": doc["sha256"], "phase": phase,
                   "external_outcome": external_outcome, "automatic_replay": False},
        )
        db.add(task)
        db.flush()
        # Age the row past the provider timeout: no live worker can be behind it.
        task.created_at = utcnow() - timedelta(seconds=age_s)
        task_id = task.id
    return task_id


def test_a_dead_worker_before_the_request_is_retired_and_the_scene_is_asked_again(api, monkeypatch):
    """The request provably never left: the identity is released and re-asked."""
    client, factory, project, doc = setup_scene(api, monkeypatch)
    calls = []

    def counted(messages, schema):
        calls.append(1)
        return _responder(messages, schema)

    client.app.state.mock_llm_responder = counted
    dead_id = _dead_scene_task(factory, project, doc, phase="WAITING_RESOURCE")
    result = _prepare(client, project, doc).json()["scenes"][0]
    assert result["status"] == "PREPARED", result
    assert len(calls) == 1, "the scene is asked again with a new request"
    with factory() as db:
        dead = db.get(AgentTaskRecord, dead_id)
        assert dead.status == "SUPERSEDED"
        assert dead.request_identity is None, "the finished identity is released"
        assert dead.trace["reconciliation"]["outcome"] == "NOT_SENT_PROVEN"
        assert dead.trace["reconciliation"]["last_phase"] == "WAITING_RESOURCE"
        fresh = [row for row in db.scalars(select(AgentTaskRecord).where(
            AgentTaskRecord.project_id == project, AgentTaskRecord.role_id == "scriptwriter"))
            if row.id != dead_id]
        assert len(fresh) == 1 and fresh[0].status == "SUCCEEDED"


def test_a_dead_worker_after_the_request_keeps_its_identity_and_is_never_replayed(api, monkeypatch):
    """The answer may exist upstream: the task stays unknown and waits for a person."""
    client, factory, project, doc = setup_scene(api, monkeypatch)
    calls = []

    def forbidden(messages, schema):
        calls.append(1)
        raise AssertionError("an unknown outcome must never be replayed")

    client.app.state.mock_llm_responder = forbidden
    dead_id = _dead_scene_task(factory, project, doc, phase="WAITING_PROVIDER")
    result = _prepare(client, project, doc).json()["scenes"][0]
    assert result["dead_worker"] is True, result
    assert result["outcome_unknown"] is True, result
    assert result["external_outcome"] == "UNKNOWN"
    assert calls == [], "the provider must not be called again"
    with factory() as db:
        dead = db.get(AgentTaskRecord, dead_id)
        assert dead.status == "RUNNING"
        assert dead.request_identity is not None, "an unknown outcome keeps its identity"


def test_the_dead_worker_budget_stops_a_scene_that_never_settles(api, monkeypatch):
    """A scene that dies before every request is bounded, not retried forever."""
    client, factory, project, doc = setup_scene(api, monkeypatch)
    calls = []

    def counted(messages, schema):
        calls.append(1)
        return _responder(messages, schema)

    client.app.state.mock_llm_responder = counted
    for _ in range(2):
        _dead_scene_task(factory, project, doc, phase="PREPARING", status="SUPERSEDED",
                         external_outcome="NOT_SENT")
    dead_id = _dead_scene_task(factory, project, doc, phase="PREPARING")
    result = _prepare(client, project, doc).json()["scenes"][0]
    assert result["dead_worker"] is True, result
    assert calls == [], "the budget stops the automatic re-ask"
    with factory() as db:
        dead = db.get(AgentTaskRecord, dead_id)
        assert dead.status == "RUNNING"
        assert dead.request_identity is not None
