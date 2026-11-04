"""Closing the workbench closes the backends it started.

ComfyUI and the local reviewers belong to this installation: they are started
by it and they must not outlive it.  A renderer that survives the stack keeps
the model resident in GPU memory and blocks the next run, so the workbench
needs one action that releases it — and that action may only ever touch a
process whose ownership is proven.
"""

from __future__ import annotations

import pytest

from shine.comfyops import renderer_release as release
from shine.comfyops.models import ResourceLease
from shine.models import AppSetting, Job, utcnow

JOB_NAME = "Local\\ShineHandoff-" + "a" * 32


def _record(db, *, stage="RUNNING", instance_id="renderer-1", lease_id=None, pid=4242, port=8188):
    db.add(AppSetting(key=f"renderer-startup:{instance_id}", value={
        "stage": stage,
        "instance_id": instance_id,
        "lease_id": lease_id,
        "port": port,
        "process": {"pid": pid, "birth": "12345", "job_name": JOB_NAME},
        "recorded_at": "2026-11-04T00:00:00",
    }))
    db.flush()


def _lease(db, *, owner="h3:job-1", status="ACTIVE"):
    lease = ResourceLease(
        owner=owner, budget_mb=16384, exclusive=True, priority=100, status=status,
        expires_at=utcnow(),
    )
    db.add(lease)
    db.flush()
    return lease


def _owned(monkeypatch, *, alive=True, owned=True, members=1, port_busy=True):
    monkeypatch.setattr(release, "process_identity", lambda pid: "12345" if alive else None)
    monkeypatch.setattr(release, "inspect_named_group", lambda name, pid, birth: owned)
    monkeypatch.setattr(release, "named_group_active_processes", lambda name: members)
    monkeypatch.setattr(
        release, "windows_port_listeners",
        lambda port: [("127.0.0.1", 4242)] if port_busy else [],
    )


def test_a_renderer_that_is_provably_gone_is_recorded_stopped_and_its_lease_released(api, monkeypatch):
    client, factory, _settings = api
    _owned(monkeypatch, alive=False, owned=False, members=0, port_busy=False)
    with factory.begin() as db:
        lease = _lease(db)
        _record(db, lease_id=lease.id)
        lease_id = lease.id
    with factory.begin() as db:
        report = release.release_managed_processes(db, actor="local-user", reason="test")
    assert report["stopped"][0]["outcome"] == "released"
    with factory() as db:
        assert db.get(ResourceLease, lease_id).status == "RELEASED"
        record = db.get(AppSetting, "renderer-startup:renderer-1")
        assert record.value["stage"] == "STOPPED"


def test_a_renderer_that_serves_a_running_attempt_is_never_stopped(api, monkeypatch):
    from tests.conftest import create_job, create_project, create_shot, freeze_style

    client, factory, _settings = api
    _owned(monkeypatch)
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    shot = create_shot(client, project_id, style["id"])
    job = create_job(client, project_id, shot["id"])
    with factory.begin() as db:
        row = db.get(Job, job["id"])
        row.status = "RUNNING"
        lease = _lease(db, owner="h3:" + job["id"])
        _record(db, lease_id=lease.id)
    with factory.begin() as db:
        report = release.release_managed_processes(db, actor="local-user", reason="test")
    assert report["refused"][0]["outcome"] == "busy"
    assert report["stopped"] == []


def test_an_owned_renderer_is_stopped_and_its_lease_released(api, monkeypatch):
    client, factory, _settings = api
    _owned(monkeypatch)
    terminated: list[str] = []

    def terminate(name, *, timeout=10):
        terminated.append(name)
        return {"job_name": name, "active_processes": 0, "total_processes": 3}

    monkeypatch.setattr(release, "terminate_named_group", terminate)
    with factory.begin() as db:
        lease = _lease(db)
        _record(db, lease_id=lease.id)
        lease_id = lease.id
    with factory.begin() as db:
        report = release.release_managed_processes(db, actor="local-user", reason="test")
    assert terminated == [JOB_NAME]
    assert report["stopped"][0]["outcome"] == "stopped"
    with factory() as db:
        assert db.get(ResourceLease, lease_id).status == "RELEASED"
        assert db.get(AppSetting, "renderer-startup:renderer-1").value["stage"] == "STOPPED"


def test_a_process_without_proven_ownership_is_never_touched(api, monkeypatch):
    client, factory, _settings = api
    _owned(monkeypatch, alive=True, owned=False, members=1)
    monkeypatch.setattr(
        release, "terminate_named_group",
        lambda name, *, timeout=10: pytest.fail("an unproven process must never be terminated"),
    )
    with factory.begin() as db:
        lease = _lease(db)
        _record(db, lease_id=lease.id)
        lease_id = lease.id
    with factory.begin() as db:
        report = release.release_managed_processes(db, actor="local-user", reason="test")
    assert report["refused"][0]["outcome"] == "unverified"
    with factory() as db:
        assert db.get(ResourceLease, lease_id).status == "ACTIVE"


def test_the_workbench_reports_the_managed_processes_it_owns(api, monkeypatch):
    client, factory, _settings = api
    _owned(monkeypatch)
    with factory.begin() as db:
        _record(db)
    response = client.get("/api/v1/setup/managed-processes")
    assert response.status_code == 200, response.text
    rows = response.json()["processes"]
    assert rows[0]["kind"] == "renderer"
    assert rows[0]["state"] == "RUNNING"


def test_the_operator_can_release_the_backends_from_the_workbench(api, monkeypatch):
    client, factory, _settings = api
    _owned(monkeypatch)
    monkeypatch.setattr(
        release, "terminate_named_group",
        lambda name, *, timeout=10: {"job_name": name, "active_processes": 0, "total_processes": 1},
    )
    with factory.begin() as db:
        lease = _lease(db)
        _record(db, lease_id=lease.id)
        lease_id = lease.id
    response = client.post("/api/v1/setup/managed-processes/release", json={},
                           headers={"X-Shine-Local-Action": "1"})
    assert response.status_code == 200, response.text
    assert response.json()["stopped"][0]["outcome"] == "stopped"
    with factory() as db:
        assert db.get(ResourceLease, lease_id).status == "RELEASED"


def test_the_residual_cleanup_refuses_while_the_stack_still_runs(tmp_path, monkeypatch):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import release_residual_backends as cleanup  # noqa: PLC0415

    data_dir = tmp_path / "data"
    (data_dir / "run").mkdir(parents=True)
    monkeypatch.setattr(cleanup, "_stack_is_running", lambda _data_dir: True)
    report = cleanup.release_residual_backends(data_dir)
    assert report["errors"]
    assert report["managed"]["stopped"] == []


def test_an_interrupted_startup_keeps_the_identity_it_needs_to_be_released(api, monkeypatch):
    """A reconciliation that drops the process identity strands the lease.

    The record is the only proof that this installation owns the process.  A
    reconciliation that rewrites it without that proof leaves the renderer
    unstoppable and the lease unreleasable.
    """
    from shine.comfyops import handoff_startup

    client, factory, _settings = api
    with factory.begin() as db:
        lease = _lease(db)
        _record(db, stage="LISTENING", lease_id=lease.id)
    monkeypatch.setattr(handoff_startup, "process_identity", lambda pid: None)
    monkeypatch.setattr(handoff_startup, "inspect_named_group", lambda name, pid, birth: False)
    findings = handoff_startup.reconcile_managed_startups(factory, None, terminate=lambda name: {})
    assert findings and findings[0]["action"] == "already_exited"
    with factory() as db:
        value = db.get(AppSetting, "renderer-startup:renderer-1").value
        assert value["stage"] == "RECONCILE_REQUIRED"
        assert value["process"]["pid"] == 4242
        assert value["process"]["job_name"] == JOB_NAME
        assert value["lease_id"]
        assert value["port"] == 8188


def test_an_uncertain_startup_is_released_once_the_renderer_is_provably_gone(api, monkeypatch):
    """An uncertain event is still judged by the physical facts.

    A lease may only be released when the renderer is provably gone, and a
    record that lost its identity is exactly the case that used to strand it.
    """
    client, factory, _settings = api
    _owned(monkeypatch, alive=False, owned=False, members=0, port_busy=False)
    with factory.begin() as db:
        lease = _lease(db)
        _record(db, stage="RECONCILE_REQUIRED", lease_id=lease.id)
        lease_id = lease.id
    with factory.begin() as db:
        report = release.release_managed_processes(db, actor="local-user", reason="test")
    assert report["stopped"][0]["outcome"] == "released"
    with factory() as db:
        assert db.get(ResourceLease, lease_id).status == "RELEASED"
        assert db.get(AppSetting, "renderer-startup:renderer-1").value["stage"] == "STOPPED"


def test_a_released_startup_is_not_reconciled_again(api, monkeypatch):
    """A released record states that its process is gone; nothing may revive it."""
    from shine.comfyops import handoff_startup

    client, factory, _settings = api
    _owned(monkeypatch)
    with factory.begin() as db:
        _record(db, stage="STOPPED")
    monkeypatch.setattr(handoff_startup, "process_identity", lambda pid: "12345")
    findings = handoff_startup.reconcile_managed_startups(factory, None, terminate=lambda name: {})
    assert findings == []
