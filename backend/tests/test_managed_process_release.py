"""Closing the workbench closes the backends it started.

ComfyUI and the local reviewers belong to this installation: they are started
by it and they must not outlive it.  A renderer that survives the stack keeps
the model resident in GPU memory and blocks the next run, so the workbench
needs one action that releases it — and that action may only ever touch a
process whose ownership is proven.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

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
    physical = {"alive": alive, "owned": owned, "members": members, "port_busy": port_busy}
    monkeypatch.setattr(release, "process_identity", lambda pid: "12345" if physical["alive"] else None)
    monkeypatch.setattr(release, "inspect_named_group", lambda name, pid, birth: physical["owned"])
    monkeypatch.setattr(release, "named_group_active_processes", lambda name: physical["members"])
    monkeypatch.setattr(
        release, "windows_port_listeners",
        lambda port: [("127.0.0.1", 4242)] if physical["port_busy"] else [],
    )
    return physical


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
    physical = _owned(monkeypatch)
    terminated: list[str] = []

    def terminate(name, *, timeout=10):
        terminated.append(name)
        physical.update(alive=False, members=0, port_busy=False)
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
    physical = _owned(monkeypatch)

    def terminate(name, *, timeout=10):
        physical.update(alive=False, members=0, port_busy=False)
        return {"job_name": name, "active_processes": 0, "total_processes": 1}

    monkeypatch.setattr(release, "terminate_named_group", terminate)
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


def _agent_orphan(factory, *, expires_at=None):
    from datetime import timedelta

    with factory.begin() as db:
        lease = ResourceLease(owner="agent:" + "d" * 32, budget_mb=1, exclusive=True,
                              priority=10, status="RECONCILE_REQUIRED",
                              expires_at=expires_at or (utcnow() - timedelta(seconds=60)))
        db.add(lease)
        db.flush()
        return lease.id


def test_an_orphan_agent_text_lease_no_longer_deadlocks_the_gpu_ledger(api):
    """The production failure: a workbench killed during a remote text call.

    An ``agent:`` slot owns no renderer, so no startup journal ever matches it
    and the renderer proof can never run.  Its lease stayed
    ``RECONCILE_REQUIRED`` forever and one such row blocks every later
    admission, so the whole ledger refused all work with ``GPU_BUSY_RETRY_OR_QUEUE``.
    A remote-only text configuration has nothing resident to unload, so the
    orphan is released and a fresh admission succeeds again.
    """
    from shine.comfyops import handoff_startup
    from shine.comfyops.scheduler import ResourceScheduler
    from shine.models import AuditEvent, ProviderProfile

    client, factory, _settings = api
    with factory.begin() as db:
        db.add(ProviderProfile(name="remote-text", kind="openai_compatible",
                               base_url="https://api.minimax.cn/v1", model="MiniMax-M3.1-flash",
                               enabled=True))
    lease_id = _agent_orphan(factory)

    findings = handoff_startup.reconcile_dead_renderer_leases(factory)

    assert [f["lease_id"] for f in findings] == [lease_id]
    with factory() as db:
        assert db.get(ResourceLease, lease_id).status == "RELEASED"
        audit = db.scalars(select(AuditEvent).where(
            AuditEvent.event_type == "RESOURCE_ORPHAN_AGENT_LEASE_RELEASED")).one()
        assert audit.entity_type == "resource_lease" and audit.entity_id == lease_id
        assert audit.payload["resubmitted"] is False
    # The deadlock is gone: a later serial model stage is admitted again.
    with factory.begin() as db:
        fresh = ResourceScheduler().enqueue(db, "agent:" + "e" * 32, 1, True, 10).id
    with factory() as db:
        assert ResourceScheduler().admit(db, fresh) is True


def test_an_orphan_agent_lease_stays_blocked_when_a_local_text_model_cannot_confirm_unload(api, monkeypatch):
    """A loopback text runtime that will not confirm its unload is uncertainty."""
    from shine.comfyops import handoff_startup
    from shine.llm import lifecycle
    from shine.llm.gateway import LLMGateway
    from shine.models import ProviderProfile

    client, factory, _settings = api
    with factory.begin() as db:
        db.add(ProviderProfile(name="local-text", kind="openai_compatible",
                               base_url="http://127.0.0.1:11434/v1", model="local", enabled=True))
    lease_id = _agent_orphan(factory)
    monkeypatch.setattr(LLMGateway, "config_for", lambda self, profile, **kw: object())
    monkeypatch.setattr(lifecycle, "release_text_model",
                        lambda config: (_ for _ in ()).throw(ValueError("LOCAL_LLM_RELEASE_UNCONFIRMED")))

    assert handoff_startup.reconcile_dead_renderer_leases(factory) == []
    with factory() as db:
        assert db.get(ResourceLease, lease_id).status == "RECONCILE_REQUIRED"


def test_a_local_text_model_that_confirms_its_unload_releases_the_orphan_agent_lease(api, monkeypatch):
    """The proof is the slot's own declared protocol, so a confirmed unload releases."""
    from shine.comfyops import handoff_startup
    from shine.llm import lifecycle
    from shine.llm.gateway import LLMGateway
    from shine.models import ProviderProfile

    client, factory, _settings = api
    with factory.begin() as db:
        db.add(ProviderProfile(name="local-text", kind="openai_compatible",
                               base_url="http://127.0.0.1:11434/v1", model="local", enabled=True))
    lease_id = _agent_orphan(factory)
    calls = []
    monkeypatch.setattr(LLMGateway, "config_for", lambda self, profile, **kw: object())
    monkeypatch.setattr(lifecycle, "release_text_model", lambda config: calls.append(config))

    findings = handoff_startup.reconcile_dead_renderer_leases(factory)

    assert [f["lease_id"] for f in findings] == [lease_id] and len(calls) == 1
    with factory() as db:
        assert db.get(ResourceLease, lease_id).status == "RELEASED"


def test_a_live_agent_lease_is_never_released_by_the_orphan_proof(api):
    """An unexpired lease may still have its owner inside the call."""
    from datetime import timedelta

    from shine.comfyops import handoff_startup
    from shine.models import ProviderProfile

    client, factory, _settings = api
    with factory.begin() as db:
        db.add(ProviderProfile(name="remote-text", kind="openai_compatible",
                               base_url="https://api.minimax.cn/v1", model="m", enabled=True))
    lease_id = _agent_orphan(factory, expires_at=utcnow() + timedelta(seconds=60))

    assert handoff_startup.reconcile_dead_renderer_leases(factory) == []
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


def _restarted_handoff_fixture(api, monkeypatch, *, submit_intent=False):
    from uuid import uuid4

    from shine.comfy.workflow import graph_hash
    from shine.comfyops.models import ComfyInstance
    from shine.models import AuditEvent, JobAttempt
    from tests.conftest import create_job, create_project, create_shot, freeze_style

    client, factory, _settings = api
    project_id = create_project(client, "handoff restart release")
    style = freeze_style(client, project_id)
    shot = create_shot(client, project_id, style["id"])
    prior_job = create_job(client, project_id, shot["id"])
    # The previous job is finished before the next one starts: a shot that is
    # still being produced belongs to that production (R05).
    with factory.begin() as db:
        db.get(Job, prior_job["id"]).status = "PASSED"
    blocked_job = create_job(client, project_id, shot["id"])
    old_attempt_id = str(uuid4())
    instance_id = "renderer-restarted"
    old_lease_id = None
    new_lease_id = None
    old_process = {"pid": 4242, "birth": "12345", "job_name": JOB_NAME}
    restarted_process = {"pid": 4343, "birth": "67890", "job_name": JOB_NAME.replace("a" * 32, "b" * 32)}
    installation = {"id": "installation-1", "sha256": "c" * 64, "attempt_id": "install-attempt-1"}
    startup_id = "startup-id-1"
    current_payload = {
        "managed": True,
        "is_mock": False,
        "url": "http://127.0.0.1:8188",
        "port": 8188,
        "process": restarted_process,
        "startup_id": startup_id,
        "installation": installation,
    }
    restarted_sha256 = graph_hash(current_payload)
    worker_pid = 7000
    worker_birth = "worker-birth-1"
    physical = {"alive": True, "members": 1, "port_busy": True}

    def identity(pid):
        if pid == restarted_process["pid"]:
            return restarted_process["birth"] if physical["alive"] else None
        if pid == old_process["pid"]:
            return None
        return None

    monkeypatch.setattr(release, "process_identity", identity)
    monkeypatch.setattr(release, "inspect_named_group", lambda name, pid, birth: name == restarted_process["job_name"])
    monkeypatch.setattr(release, "named_group_active_processes", lambda _name: physical["members"])
    monkeypatch.setattr(release, "windows_port_listeners",
                        lambda _port: [("127.0.0.1", restarted_process["pid"])] if physical["port_busy"] else [])

    with factory.begin() as db:
        prior = db.get(Job, prior_job["id"])
        prior.provider = "comfyui"
        prior.status = "PASSED"
        target = db.get(Job, blocked_job["id"])
        target.provider = "comfyui"
        target.status = "NEEDS_REVIEW"
        target.failure_code = "PENDING_WORKFLOW_MAPPING"
        target.lease_owner = None
        target.lease_expires_at = None
        old_lease = _lease(db, owner="h3:" + prior.id, status="RELEASED")
        new_lease = _lease(db, owner="h3:" + target.id, status="RECONCILE_REQUIRED")
        old_lease_id, new_lease_id = old_lease.id, new_lease.id
        db.add(JobAttempt(
            job_id=target.id,
            attempt_no=1,
            request_fingerprint="f" * 64,
            status="FAILED",
            failure_code="PENDING_WORKFLOW_MAPPING",
            lease_owner="worker-1",
            lease_expires_at=utcnow(),
        ))
        db.add(AppSetting(key="renderer-startup:" + instance_id, value={
            "stage": "RUNNING",
            "instance_id": instance_id,
            "startup_id": startup_id,
            "lease_id": old_lease.id,
            "instance_sha256": "d" * 64,
            "installation": installation,
            "port": 8188,
            "process": old_process,
            "recorded_at": "2026-11-07T00:00:00",
        }))
        db.add(ComfyInstance(id=instance_id, payload=current_payload, sha256=restarted_sha256,
                             status="RUNNING_PENDING_HEALTH"))
        db.add(AppSetting(key="handoff:" + old_attempt_id, value={
            "stage": "RESTARTED",
            "job_id": prior.id,
            "attempt_id": old_attempt_id,
            "resource_id": old_lease.id,
            "instance_id": instance_id,
            "instance_sha256": "d" * 64,
            "restarted_instance_sha256": restarted_sha256,
            "process": old_process,
            "worker_pid": worker_pid,
            "worker_birth": worker_birth,
            "evidence": {
                "STOPPED": {"pid": old_process["pid"], "active_processes": 0},
                "RESTARTED": {"process": restarted_process},
            },
        }))
        db.add(AppSetting(key="handoff-reservation:" + target.id, value={
            "stage": "RECONCILE_REQUIRED",
            "reason": "MANAGED_RUN_INCOMPLETE",
            "old_job_id": prior.id,
            "old_attempt_id": old_attempt_id,
            "old_lease_id": old_lease.id,
            "job_id": target.id,
            "lease_id": new_lease.id,
            "instance_id": instance_id,
            "worker_pid": worker_pid,
            "worker_birth": worker_birth,
        }))
        db.add(AuditEvent(project_id=project_id, actor="comfy-worker",
                          event_type="COMFY_WORKFLOW_MAPPING_PENDING", entity_type="job",
                          entity_id=target.id, payload={"details": {"reason": "PENDING_WORKFLOW_MAPPING"}}))
        if submit_intent:
            db.add(AuditEvent(project_id=project_id, actor="comfy-worker",
                              event_type="COMFY_SUBMIT_INTENT", entity_type="job",
                              entity_id=target.id, payload={"prompt_graph_sha256": "a" * 64}))
    return factory, physical, restarted_process, blocked_job["id"], old_lease_id, new_lease_id


def test_handoff_restart_proof_releases_only_the_proven_pre_submit_reservation(api, monkeypatch):
    from sqlalchemy import select

    from shine.models import AuditEvent

    factory, physical, restarted_process, blocked_job_id, old_lease_id, new_lease_id = (
        _restarted_handoff_fixture(api, monkeypatch)
    )
    terminated = []

    def terminate(name, *, timeout=10):
        terminated.append(name)
        physical.update(alive=False, members=0, port_busy=False)
        return {"job_name": name, "active_processes": 0, "total_processes": 2}

    monkeypatch.setattr(release, "terminate_named_group", terminate)
    with factory.begin() as db:
        report = release.release_managed_processes(db, actor="local-user", reason="test")
    assert terminated == [restarted_process["job_name"]]
    assert report["refused"] == []
    assert any(item.get("lease_id") == new_lease_id
               and item.get("external_submission") == "NOT_SENT_PROVEN" for item in report["released"])
    with factory() as db:
        assert db.get(ResourceLease, old_lease_id).status == "RELEASED"
        assert db.get(ResourceLease, new_lease_id).status == "RELEASED"
        reservation = db.get(AppSetting, "handoff-reservation:" + blocked_job_id)
        assert reservation.value["stage"] == "RECONCILE_COMPLETE"
        assert reservation.value["external_submission"] == "NOT_SENT_PROVEN"
        assert reservation.value["reconciled_by"] == "local-user"
        event = db.scalar(select(AuditEvent).where(
            AuditEvent.entity_id == blocked_job_id,
            AuditEvent.event_type == "COMFY_HANDOFF_PRE_SUBMIT_LEASE_RELEASED",
        ))
        assert event.actor == "local-user"


def test_handoff_restart_does_not_release_a_reservation_with_submit_intent(api, monkeypatch):
    factory, physical, restarted_process, _blocked_job_id, _old_lease_id, new_lease_id = (
        _restarted_handoff_fixture(api, monkeypatch, submit_intent=True)
    )

    def terminate(_name, *, timeout=10):
        physical.update(alive=False, members=0, port_busy=False)
        return {"job_name": restarted_process["job_name"], "active_processes": 0, "total_processes": 2}

    monkeypatch.setattr(release, "terminate_named_group", terminate)
    with factory.begin() as db:
        report = release.release_managed_processes(db, actor="local-user", reason="test")
    assert report["refused"] == []
    assert all(item.get("lease_id") != new_lease_id for item in report["released"])
    with factory() as db:
        assert db.get(ResourceLease, new_lease_id).status == "RECONCILE_REQUIRED"


def test_handoff_restart_rebind_refuses_a_changed_installation_pin(api, monkeypatch):
    from shine.comfy.workflow import graph_hash
    from shine.comfyops.models import ComfyInstance

    factory, physical, _restarted_process, _job_id, _old_lease_id, _new_lease_id = (
        _restarted_handoff_fixture(api, monkeypatch)
    )
    with factory.begin() as db:
        instance = db.get(ComfyInstance, "renderer-restarted")
        instance.payload = {**instance.payload,
                            "installation": {**instance.payload["installation"], "sha256": "e" * 64}}
        instance.sha256 = graph_hash(instance.payload)
    monkeypatch.setattr(release, "terminate_named_group",
                        lambda *_args, **_kwargs: pytest.fail("changed installation pin must not be stopped"))
    with factory.begin() as db:
        processes = release.describe_managed_processes(db)
    assert processes[0]["ownership_evidence"] == "startup_record"
    assert processes[0]["state"] == "UNVERIFIED"
    assert physical["alive"] is True
