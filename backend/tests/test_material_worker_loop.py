"""The Worker must not spin on a finished item, and a busy Worker is online.

Two defects met on the real machine and are pinned here:

* a failed item stayed claimable, so the Worker re-claimed it in a tight loop:
  the CPU burned, the heartbeat stopped, the workbench reported an offline
  Worker, and every other queued item never ran;
* the heartbeat was only written while the Worker was idle, so a Worker that
  was busy producing something looked absent.
"""
from __future__ import annotations

from sqlalchemy import select

from shine.materialprep.execution import claim_next_item
from shine.materialprep.states import ITEM_CLAIMABLE, ITEM_FAILED, ITEM_QUEUED
from shine.models import WorkerInstance, utcnow
from tests.test_material_brief import _item


def test_a_failed_item_is_not_claimed_again(api):
    client, factory, settings = api
    project_id = client.post("/api/v1/projects", json={"title": "loop"}).json()["id"]
    with factory() as db:
        failed = _item(db, project_id)
        failed.status = ITEM_FAILED
        failed.error_code = "MATERIAL_BRIEF_NEEDS_CLARIFICATION"
        failed_id = failed.id
        version = failed.state_version
        db.commit()
    assert ITEM_FAILED not in ITEM_CLAIMABLE
    # The Worker must not pick the finished item up again.
    assert claim_next_item(factory, "worker-test") is None
    with factory() as db:
        row = db.get(type(failed), failed_id)
        assert row.status == ITEM_FAILED
        assert row.state_version == version

    # A queued item is still claimable.
    other_project = client.post("/api/v1/projects", json={"title": "loop-2"}).json()["id"]
    with factory() as db:
        queued = _item(db, other_project)
        queued.status = ITEM_QUEUED
        queued_id = queued.id
        db.commit()
    assert claim_next_item(factory, "worker-test") == queued_id


def test_a_busy_worker_still_writes_its_heartbeat(api, monkeypatch):
    from shine import worker

    client, factory, settings = api
    engine = client.app.state.engine
    monkeypatch.setattr(worker.Settings, "from_env", classmethod(lambda cls: settings))
    monkeypatch.setattr(worker, "make_engine", lambda url: engine)
    monkeypatch.setattr(worker, "make_session_factory", lambda eng: factory)

    phases: list[str | None] = []
    real_heartbeat = worker.heartbeat

    def record(session_factory, worker_id, **kwargs):
        phases.append(kwargs.get("phase"))
        return real_heartbeat(session_factory, worker_id, **kwargs)

    monkeypatch.setattr(worker, "heartbeat", record)
    ticks = {"n": 0}

    def busy_then_stop(*args, **kwargs):
        ticks["n"] += 1
        if ticks["n"] >= 2:
            raise KeyboardInterrupt
        return "material-image:1"

    monkeypatch.setattr(worker, "run_configured_once", busy_then_stop)
    worker.serve()

    # The heartbeat was written while the Worker was busy, not only when idle.
    assert "material-image:1" in phases
    with factory() as db:
        instance = db.scalars(select(WorkerInstance).order_by(WorkerInstance.started_at.desc())).first()
        assert instance is not None
        assert instance.status in {"online", "stopped"}
        assert (utcnow() - instance.last_heartbeat_at).total_seconds() < 120
