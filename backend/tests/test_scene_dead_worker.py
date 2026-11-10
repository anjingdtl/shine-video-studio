"""A worker that dies with a scene request in flight must not look alive.

The scene task used to carry no lease at all, so a row left RUNNING by a dead
worker was indistinguishable from a live one: the outline showed "running",
the scene could never be asked again, and the whole journey stalled.

A dead scene task is seeded from the task the product really created: the same
input hash, so the scene lookup finds it exactly as it would in production.
A row with a different input belongs to a different request and must never
silence this one.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from shine.models import AgentArtifact, AgentTaskRecord, utcnow
from tests.test_material_scene_safety import setup_scene
from tests.test_material_scenes import _prepare, _responder


def _dead_scene_task(factory, project, client, doc, *, phase, external_outcome="UNKNOWN",
                     status="RUNNING", retired_before=0):
    """Turn the task the product really created into one a dead worker left.

    The row keeps its real input hash, so the scene lookup matches it, and it
    is aged past the provider timeout, so no live worker can be behind it.
    ``retired_before`` adds rows a person already retired, which is what the
    per-scene re-ask budget counts.
    """
    client.app.state.mock_llm_responder = _responder
    _prepare(client, project, doc)
    with factory.begin() as db:
        real = db.scalars(
            select(AgentTaskRecord)
            .where(AgentTaskRecord.project_id == project, AgentTaskRecord.role_id == "scriptwriter")
            .order_by(AgentTaskRecord.created_at.desc())
        ).first()
        # The artifact of the first run would let the scene be reused instead
        # of being asked again, so the dead task stands on its own.
        db.query(AgentArtifact).filter(AgentArtifact.source_task_id == real.id).delete()
        for _ in range(retired_before):
            db.add(AgentTaskRecord(
                project_id=project, role_id="scriptwriter", input_hash=real.input_hash,
                request_identity=None, asset_manifest_hash=real.asset_manifest_hash,
                rulepack_id="material-script-prepare", rulepack_hash="x",
                model_profile_id="x", model_id="x", schema_version="1.0", status="SUPERSEDED",
                trace={**real.trace, "phase": phase, "external_outcome": "NOT_SENT",
                       "reconciliation": {"outcome": "NOT_SENT_PROVEN"}},
            ))
        real.status = status
        real.created_at = utcnow() - timedelta(seconds=4000)
        real.trace = {**real.trace, "phase": phase, "external_outcome": external_outcome}
        dead_id = real.id
    return dead_id


def test_a_dead_worker_before_the_request_is_retired_and_the_scene_is_asked_again(api, monkeypatch):
    """The request provably never left: the identity is released and re-asked."""
    client, factory, project, doc = setup_scene(api, monkeypatch)
    calls = []

    def counted(messages, schema):
        calls.append(1)
        return _responder(messages, schema)

    dead_id = _dead_scene_task(factory, project, client, doc, phase="WAITING_RESOURCE")
    client.app.state.mock_llm_responder = counted
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

    dead_id = _dead_scene_task(factory, project, client, doc, phase="WAITING_PROVIDER")
    client.app.state.mock_llm_responder = forbidden
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

    dead_id = _dead_scene_task(factory, project, client, doc, phase="PREPARING",
                               retired_before=2)
    client.app.state.mock_llm_responder = counted
    result = _prepare(client, project, doc).json()["scenes"][0]
    assert result["dead_worker"] is True, result
    assert calls == [], "the budget stops the automatic re-ask"
    with factory() as db:
        dead = db.get(AgentTaskRecord, dead_id)
        assert dead.status == "RUNNING"
        assert dead.request_identity is not None
