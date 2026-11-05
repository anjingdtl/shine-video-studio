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


def test_an_orphan_h3_lease_without_any_startup_record_is_released_by_conservative_proof(api, monkeypatch):
    """A worker that died before writing its startup journal must not deadlock the GPU.

    The lease has no record to match, so the orphan proof applies: a dead job,
    no live recorded renderer process or owned group anywhere, and a free
    managed port release the ledger entry.  Nothing is resubmitted.
    """
    from shine.comfyops import handoff_startup
    from shine.comfyops.models import ComfyInstance

    client, factory, _settings = api
    monkeypatch.setattr(handoff_startup, "process_identity", lambda pid: None)
    monkeypatch.setattr(handoff_startup, "named_group_active_processes", lambda name: 0)
    monkeypatch.setattr(handoff_startup, "windows_port_listeners", lambda port: [])
    with factory.begin() as db:
        db.add(ComfyInstance(payload={"name": "shine-h3", "port": 8188, "managed": True},
                             sha256="x" * 64, status="STOPPED"))
        lease = ResourceLease(owner="h3:missing-job", budget_mb=16384, exclusive=True,
                              priority=100, status="RECONCILE_REQUIRED", expires_at=utcnow())
        db.add(lease)
        db.flush()
        lease_id = lease.id
    findings = handoff_startup.reconcile_dead_renderer_leases(factory)
    assert findings and findings[0]["action"] == "released"
    with factory() as db:
        assert db.get(ResourceLease, lease_id).status == "RELEASED"


def test_an_orphan_lease_is_kept_when_any_recorded_renderer_is_still_alive(api, monkeypatch):
    """One live recorded renderer — even for another lease — blocks the orphan release."""
    from shine.comfyops import handoff_startup

    client, factory, _settings = api
    monkeypatch.setattr(handoff_startup, "process_identity", lambda pid: "12345")
    monkeypatch.setattr(handoff_startup, "named_group_active_processes", lambda name: 0)
    monkeypatch.setattr(handoff_startup, "windows_port_listeners", lambda port: [])
    with factory.begin() as db:
        # A startup record for an unrelated instance whose process is alive.
        db.add(AppSetting(key="renderer-startup:other", value={
            "stage": "RUNNING", "instance_id": "other", "lease_id": None,
            "port": 8199, "process": {"pid": 999, "birth": "12345", "job_name": JOB_NAME},
            "recorded_at": "2026-11-04T00:00:00",
        }))
        lease = ResourceLease(owner="h3:missing-job", budget_mb=16384, exclusive=True,
                              priority=100, status="RECONCILE_REQUIRED", expires_at=utcnow())
        db.add(lease)
        db.flush()
        lease_id = lease.id
    findings = handoff_startup.reconcile_dead_renderer_leases(factory)
    assert findings == []
    with factory() as db:
        assert db.get(ResourceLease, lease_id).status == "RECONCILE_REQUIRED"


def test_an_already_stopped_renderer_still_repairs_its_instance_row(api, monkeypatch):
    """The image runtime keeps its process in the startup record, not on the
    shared row, so a stopped renderer leaves no mark to match.  The release
    path must still write the instance row back to STOPPED -- otherwise the
    row stays RUNNING_PENDING_HEALTH forever and every later start is refused
    with IMAGE_RENDERER_INSTANCE_NOT_STOPPED (the live stack hit exactly this
    after a restart while the image renderer was idle-stopped)."""
    from shine.comfy.workflow import graph_hash
    from shine.comfyops.models import ComfyInstance

    client, factory, _settings = api
    _owned(monkeypatch, alive=False, port_busy=False)
    with factory.begin() as db:
        payload = {"managed": True, "adopted": True, "port": 8188, "process": None}
        db.add(ComfyInstance(id="image-inst", status="RUNNING_PENDING_HEALTH",
                             payload=payload, sha256=graph_hash(payload)))
        db.add(AppSetting(key="renderer-startup:image:image-inst", value={
            "stage": "STOPPED", "instance_id": "image-inst", "lease_id": None,
            "port": 8188, "process": {"pid": 4242, "birth": "12345", "job_name": JOB_NAME},
            "recorded_at": "2026-11-04T00:00:00",
        }))
    response = client.post("/api/v1/setup/managed-processes/release", json={},
                           headers={"X-Shine-Local-Action": "1"})
    assert response.status_code == 200, response.text
    assert response.json()["stopped"][0]["outcome"] == "already_stopped"
    with factory() as db:
        assert db.get(ComfyInstance, "image-inst").status == "STOPPED"


def test_stop_image_renderer_on_an_already_stopped_record_repairs_the_row(api):
    from shine.comfy.workflow import graph_hash
    from shine.comfyops.image_renderer import stop_image_renderer
    from shine.comfyops.models import ComfyInstance

    client, factory, settings = api
    with factory.begin() as db:
        payload = {"managed": True, "port": 8188, "process": None}
        db.add(ComfyInstance(id="image-inst", status="RUNNING_PENDING_HEALTH",
                             payload=payload, sha256=graph_hash(payload)))
        db.add(AppSetting(key="renderer-startup:image:image-inst", value={
            "stage": "STOPPED", "instance_id": "image-inst", "lease_id": None,
            "port": 8188, "process": {"pid": 4242, "birth": "12345", "job_name": JOB_NAME},
            "recorded_at": "2026-11-04T00:00:00",
        }))
    result = stop_image_renderer(factory, settings, instance_id="image-inst", actor="local-user")
    assert result["outcome"] == "already_stopped"
    with factory() as db:
        assert db.get(ComfyInstance, "image-inst").status == "STOPPED"
