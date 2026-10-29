"""An accepted handoff must not expose an unleased interval between jobs."""

from datetime import timedelta
from types import SimpleNamespace

import pytest

from shine.comfyops.handoff_activation import ReviewReleaseBoundary
from shine.comfyops.models import ComfyInstance, ResourceLease
from shine.models import AppSetting, Job, JobAttempt, utcnow
from tests.conftest import create_comfy_job
from tests.test_comfy_handoff_journal import handoff  # noqa: F401


@pytest.mark.parametrize("live", [False, True])
def test_recovery_blocks_expired_reservation_before_any_renderer_call(api, live):
    import os

    from sqlalchemy import select

    from shine.comfyops.installer import process_identity
    from shine.worker import recover_expired_jobs
    from tests.conftest import create_project, create_shot, freeze_style

    client, factory, settings = api
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    shot = create_shot(client, project_id, style["id"])
    job_id = create_comfy_job(client, project_id, shot["id"])["id"]
    with factory.begin() as db:
        lease = ResourceLease(owner="h3:" + job_id, budget_mb=16384, exclusive=True, priority=100,
                              status="ACTIVE", expires_at=utcnow() + timedelta(seconds=30 if live else -1))
        db.add(lease)
        db.flush()
        db.add(AppSetting(key="handoff-reservation:" + job_id, value={"stage": "RESERVED", "job_id": job_id,
                          "lease_id": lease.id, "worker_pid": os.getpid() if live else 99999999,
                          "worker_birth": process_identity(os.getpid()) if live else "1"}))

    class NoRendererCalls:
        def __getattr__(self, _name):
            raise AssertionError("recovery must not contact an unverified renderer")

    assert recover_expired_jobs(factory, "restart-worker", settings=settings, comfy_client=NoRendererCalls()) == (
        0 if live else 1
    )
    with factory() as db:
        job = db.get(Job, job_id)
        lease = db.scalar(select(ResourceLease).where(ResourceLease.owner == "h3:" + job_id))
        if live:
            assert job.status == "QUEUED" and lease.status == "ACTIVE"
            assert db.get(AppSetting, "handoff-reservation:" + job_id).value["stage"] == "RESERVED"
        else:
            assert job.status == "NEEDS_REVIEW" and job.failure_code == "COMFY_HANDOFF_RECONCILIATION_REQUIRED"
            assert lease.status == "RECONCILE_REQUIRED"
            assert db.get(AppSetting, "handoff-reservation:" + job_id).value["stage"] == "RECONCILE_REQUIRED"
        assert not db.scalar(select(JobAttempt).where(JobAttempt.job_id == job_id))


def test_missing_reserved_lease_creates_a_blocking_reconciliation_record(api):
    from uuid import uuid4

    from sqlalchemy import select

    from shine.worker import recover_expired_jobs
    from tests.conftest import create_project, create_shot, freeze_style

    client, factory, settings = api
    project = create_project(client)
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"])
    job_id = create_comfy_job(client, project, shot["id"])["id"]
    lost_lease_id = str(uuid4())
    with factory.begin() as db:
        db.add(AppSetting(key="handoff-reservation:" + job_id, value={"stage": "RESERVED", "job_id": job_id,
                    "lease_id": lost_lease_id, "worker_pid": 99999999, "worker_birth": "1"}))

    class NoRendererCalls:
        def __getattr__(self, _name):
            raise AssertionError("recovery must not contact an unverified renderer")

    assert recover_expired_jobs(factory, "restart-worker", settings=settings, comfy_client=NoRendererCalls()) == 1
    with factory() as db:
        job = db.get(Job, job_id)
        lease = db.scalar(select(ResourceLease).where(ResourceLease.owner == "h3:" + job_id))
        reservation = db.get(AppSetting, "handoff-reservation:" + job_id).value
        assert job.status == "NEEDS_REVIEW" and lease.status == "RECONCILE_REQUIRED"
        assert reservation["lease_id"] == lost_lease_id and reservation["recovery_lease_id"] == lease.id


def test_corrupt_reservation_binding_blocks_the_job_named_by_the_key(api):
    from sqlalchemy import select

    from shine.worker import recover_expired_jobs
    from tests.conftest import create_project, create_shot, freeze_style

    client, factory, settings = api
    project = create_project(client)
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"])
    job_id = create_comfy_job(client, project, shot["id"])["id"]
    with factory.begin() as db:
        db.add(AppSetting(key="handoff-reservation:" + job_id,
                          value={"stage": "RESERVED", "job_id": "different-job", "lease_id": "missing"}))

    class NoRendererCalls:
        def __getattr__(self, _name):
            raise AssertionError("recovery must not contact an unverified renderer")

    assert recover_expired_jobs(factory, "restart-worker", settings=settings, comfy_client=NoRendererCalls()) == 1
    with factory() as db:
        job = db.get(Job, job_id)
        lease = db.scalar(select(ResourceLease).where(ResourceLease.owner == "h3:" + job_id))
        reservation = db.get(AppSetting, "handoff-reservation:" + job_id).value
        assert job.status == "NEEDS_REVIEW" and lease.status == "RECONCILE_REQUIRED"
        assert reservation["job_id"] == "different-job" and reservation["recovery_job_id"] == job_id


def test_claimed_reservation_recovery_preserves_graph_without_renderer_contact(api):
    from sqlalchemy import select

    from shine.worker import recover_expired_jobs
    from tests.conftest import create_project, create_shot, freeze_style

    client, factory, settings = api
    project = create_project(client)
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"])
    job_id = create_comfy_job(client, project, shot["id"])["id"]
    with factory.begin() as db:
        job = db.get(Job, job_id)
        job.status, job.lease_owner = "SUBMITTING", "dead-worker"
        job.lease_expires_at = utcnow() - timedelta(seconds=1)
        attempt = JobAttempt(job_id=job_id, attempt_no=1, request_fingerprint="a" * 64,
                             status="SUBMIT_INTENT", prompt_graph_sha256="c" * 64, lease_owner="dead-worker",
                             lease_expires_at=utcnow() - timedelta(seconds=1))
        lease = ResourceLease(owner="h3:" + job_id, budget_mb=16384, exclusive=True, priority=100,
                              status="ACTIVE", expires_at=utcnow() - timedelta(seconds=1))
        db.add_all([attempt, lease])
        db.flush()
        db.add(AppSetting(key="handoff-reservation:" + job_id, value={"stage": "RESERVED", "job_id": job_id,
                          "lease_id": lease.id, "worker_pid": 99999999, "worker_birth": "1"}))

    class NoRendererCalls:
        def __getattr__(self, _name):
            raise AssertionError("recovery must not contact an unverified renderer")

    assert recover_expired_jobs(factory, "restart-worker", settings=settings, comfy_client=NoRendererCalls()) == 1
    with factory() as db:
        job = db.get(Job, job_id)
        attempt = db.scalar(select(JobAttempt).where(JobAttempt.job_id == job_id))
        lease = db.scalar(select(ResourceLease).where(ResourceLease.owner == "h3:" + job_id))
        assert job.status == "NEEDS_REVIEW" and job.failure_code == "COMFY_HANDOFF_RECONCILIATION_REQUIRED"
        assert attempt.status == "SUBMIT_INTENT" and attempt.prompt_graph_sha256 == "c" * 64
        assert lease.status == "RECONCILE_REQUIRED"


@pytest.mark.parametrize("scenario", ["valid", "not_queued", "stale", "existing_lease", "instance_changed"])
def test_reserve_queued_job_without_an_unleased_gap(api, handoff, monkeypatch, scenario):  # noqa: F811
    import os

    from fastapi import HTTPException
    from sqlalchemy import select

    from shine.comfyops.handoff_transfer import reserve_next_handoff_job
    from shine.comfyops.installer import process_identity
    from shine.comfyops.scheduler import ResourceScheduler

    client, factory, settings = api
    _, old_job_id, old_attempt_id, old_lease_id, instance_id, _ = handoff
    with factory() as db:
        old = db.get(Job, old_job_id)
        project_id, shot_id = old.project_id, old.shot_id
    new_job_id = create_comfy_job(client, project_id, shot_id)["id"]
    with factory.begin() as db:
        old = db.get(Job, old_job_id)
        old.status, old.lease_owner, old.lease_expires_at = "NEEDS_REVIEW", None, None
        job = db.get(Job, new_job_id)
        if scenario == "not_queued":
            job.status = "NEEDS_REVIEW"
        if scenario == "stale":
            job.stale = True
        if scenario == "existing_lease":
            ResourceScheduler().enqueue(db, "h3:" + new_job_id, 1)
        if scenario == "instance_changed":
            instance = db.get(ComfyInstance, instance_id)
            instance.payload = {**instance.payload, "unexpected": True}
        db.add(AppSetting(key="handoff:" + old_attempt_id, value={"stage": "RESTARTED", "job_id": old_job_id,
                   "attempt_id": old_attempt_id, "resource_id": old_lease_id, "instance_id": instance_id,
                   "restarted_instance_sha256": db.get(ComfyInstance, instance_id).sha256,
                   "worker_pid": os.getpid(), "worker_birth": process_identity(os.getpid()),
                   "stage_recorded_at": utcnow().isoformat(),
                   "resource_epoch": {"epoch": 0, "kind": None, "lease_id": None}}))
    boundary = object.__new__(ReviewReleaseBoundary)
    boundary._session = SimpleNamespace(attempt_id=old_attempt_id, lease_id=old_lease_id, job_id=old_job_id)
    monkeypatch.setattr(ReviewReleaseBoundary, "verify", lambda *args: {
        "observed_resource_epoch": {"epoch": 0, "kind": None, "lease_id": None}, "reviews": ["synthetic"],
    })
    if scenario != "valid":
        with pytest.raises(HTTPException):
            reserve_next_handoff_job(factory, settings, boundary, new_job_id, instance_id)
        with factory() as db:
            assert db.get(ResourceLease, old_lease_id).status == "ACTIVE"
            assert not db.get(AppSetting, "handoff-reservation:" + new_job_id)
        return
    result = reserve_next_handoff_job(factory, settings, boundary, new_job_id, instance_id)
    with factory.begin() as db:
        assert db.get(ResourceLease, old_lease_id).status == "RELEASED"
        assert db.get(ResourceLease, result["lease_id"]).status == "ACTIVE"
        assert db.get(Job, new_job_id).status == "QUEUED"
        assert not db.scalar(select(JobAttempt).where(JobAttempt.job_id == new_job_id))
        assert db.get(AppSetting, "handoff-reservation:" + new_job_id).value["lease_id"] == result["lease_id"]
        waiting = ResourceScheduler().enqueue(db, "other-model", 1)
        waiting_id = waiting.id
    with factory() as db:
        assert not ResourceScheduler().admit(db, waiting_id)


@pytest.mark.parametrize("scenario", ["valid", "race", "epoch_changed", "expired", "missing_graph"])
def test_next_job_takes_lease_atomically_and_preserves_previous_record(api, handoff, monkeypatch, scenario):  # noqa: F811
    from sqlalchemy import select, text

    from shine.comfyops.handoff_transfer import transfer_handoff_lease
    from shine.comfyops.resource_epoch import advance_epoch
    from shine.comfyops.scheduler import ResourceScheduler

    client, factory, settings = api
    _, old_job_id, old_attempt_id, old_lease_id, instance_id, _ = handoff
    with factory() as db:
        old = db.get(Job, old_job_id)
        project_id, shot_id = old.project_id, old.shot_id
    new_job_id = create_comfy_job(client, project_id, shot_id)["id"]
    with factory.begin() as db:
        old = db.get(Job, old_job_id)
        old.status, old.lease_owner, old.lease_expires_at = "NEEDS_REVIEW", None, None
        next_job = db.get(Job, new_job_id)
        next_job.status, next_job.lease_owner = "SUBMITTING", "worker-next"
        next_job.lease_expires_at = utcnow() + timedelta(seconds=60)
        attempt = JobAttempt(job_id=new_job_id, attempt_no=1, request_fingerprint="a" * 64,
                             status="SUBMIT_INTENT", lease_owner="worker-next",
                             lease_expires_at=utcnow() + timedelta(seconds=60), prompt_graph_sha256="c" * 64)
        db.add(attempt)
        db.flush()
        next_attempt_id, version = attempt.id, next_job.state_version
        if scenario == "missing_graph":
            attempt.prompt_graph_sha256 = None
        if scenario == "expired":
            db.get(ResourceLease, old_lease_id).expires_at = utcnow() - timedelta(seconds=1)
        db.add(AppSetting(key="handoff:" + old_attempt_id, value={"stage": "RESTARTED", "job_id": old_job_id,
                   "attempt_id": old_attempt_id, "resource_id": old_lease_id, "instance_id": instance_id,
                   "restarted_instance_sha256": db.get(ComfyInstance, instance_id).sha256}))
    boundary = object.__new__(ReviewReleaseBoundary)
    boundary._session = SimpleNamespace(attempt_id=old_attempt_id, lease_id=old_lease_id, job_id=old_job_id)
    if scenario == "epoch_changed":
        with factory.begin() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            advance_epoch(db, old_lease_id, "model")
    monkeypatch.setattr(ReviewReleaseBoundary, "verify", lambda *args: {
        "observed_resource_epoch": {"epoch": 0, "kind": None, "lease_id": None}, "reviews": ["synthetic"],
    })
    if scenario not in {"valid", "race"}:
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            transfer_handoff_lease(factory, settings, boundary, new_job_id, next_attempt_id, instance_id,
                                    "worker-next", version)
        with factory() as db:
            assert db.get(ResourceLease, old_lease_id).status == "ACTIVE"
            assert not db.scalar(select(ResourceLease).where(ResourceLease.owner == "h3:" + new_job_id))
            assert not db.get(AppSetting, "handoff-transfer:" + next_attempt_id)
        return
    if scenario == "race":
        from threading import Event, Thread

        import shine.comfyops.handoff_transfer as transfer_module

        with factory.begin() as db:
            waiting = ResourceScheduler().enqueue(db, "racing-model", 1)
            waiting_id = waiting.id
        locked, release, contender_started, finished = Event(), Event(), Event(), Event()
        result_box, errors, admission = [], [], []
        original_check = transfer_module._check

        def paused_check(*args, **kwargs):
            locked.set()
            assert release.wait(5)
            return original_check(*args, **kwargs)

        def perform_transfer():
            try:
                result_box.append(transfer_handoff_lease(factory, settings, boundary, new_job_id, next_attempt_id,
                                                          instance_id, "worker-next", version))
            except Exception as exc:
                errors.append(exc)

        def contend():
            try:
                with factory() as db:
                    contender_started.set()
                    admission.append(ResourceScheduler().admit(db, waiting_id))
            except Exception as exc:
                errors.append(exc)
            finally:
                finished.set()

        monkeypatch.setattr(transfer_module, "_check", paused_check)
        transfer_thread = Thread(target=perform_transfer)
        contender = Thread(target=contend)
        transfer_thread.start()
        try:
            assert locked.wait(5)
            contender.start()
            assert contender_started.wait(5)
            assert not finished.wait(0.2)  # SQLite writer remains held during transfer.
        finally:
            release.set()
            transfer_thread.join(10)
            if contender.ident:
                contender.join(10)
        assert not transfer_thread.is_alive() and not contender.is_alive()
        assert not errors and admission == [False] and len(result_box) == 1
        result = result_box[0]
    else:
        result = transfer_handoff_lease(factory, settings, boundary, new_job_id, next_attempt_id, instance_id,
                                        "worker-next", version)
    with factory.begin() as db:
        old_lease = db.get(ResourceLease, old_lease_id)
        new_lease = db.get(ResourceLease, result["lease_id"])
        assert old_lease.status == "RELEASED" and old_lease.owner == "h3:" + old_job_id
        assert new_lease.status == "ACTIVE" and new_lease.owner == "h3:" + new_job_id
        assert db.get(AppSetting, "handoff-transfer:" + next_attempt_id).value["old_lease_id"] == old_lease_id
        waiting = ResourceScheduler().enqueue(db, "unrelated-model", 1)
        waiting_id = waiting.id
    with factory() as db:
        assert not ResourceScheduler().admit(db, waiting_id)
    from fastapi import HTTPException

    with pytest.raises(HTTPException, match="HANDOFF_TRANSFER_ALREADY_RECORDED"):
        transfer_handoff_lease(factory, settings, boundary, new_job_id, next_attempt_id, instance_id,
                               "worker-next", version)
    with factory() as db:
        assert db.get(ResourceLease, old_lease_id).status == "RELEASED"
        assert db.get(ResourceLease, result["lease_id"]).status == "ACTIVE"
