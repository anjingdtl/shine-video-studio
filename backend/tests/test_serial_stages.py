"""Model admission and cleanup are stage boundaries, regardless of free capacity."""

from dataclasses import dataclass

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from shine.comfyops.model_calls import digest_input, model_step
from shine.comfyops.models import ResourceLease
from shine.comfyops.scheduler import ResourceScheduler, resource_slot


def test_even_small_legacy_nonexclusive_leases_cannot_overlap(api):
    _, factory, _ = api
    scheduler = ResourceScheduler()
    with factory.begin() as db:
        # Windows can assign identical creation timestamps; priority makes this
        # fixture's admission order explicit while testing mutual exclusion.
        first = scheduler.enqueue(db, "agent-a", 1, priority=20)
        first.exclusive = False  # Existing database written before the serial policy.
        second = scheduler.enqueue(db, "agent-b", 1)
        ids = first.id, second.id
    with factory() as db:
        assert scheduler.admit(db, ids[0])
    with factory() as db:
        assert not scheduler.admit(db, ids[1])
    with factory.begin() as db:
        scheduler.release(db, ids[0], "agent-a")
    with factory() as db:
        assert scheduler.admit(db, ids[1])


def test_resource_admission_order_does_not_depend_on_wall_clock(api, monkeypatch):
    from shine.comfyops.resource_epoch import read_epoch
    from shine.models import utcnow

    _, factory, _ = api
    fixed = utcnow()
    monkeypatch.setattr("shine.comfyops.scheduler.utcnow", lambda: fixed)
    scheduler = ResourceScheduler()
    with factory.begin() as db:
        first = scheduler.enqueue(db, "epoch-first", 1, priority=10)
        second = scheduler.enqueue(db, "epoch-second", 1, priority=1)
        first_id, second_id = first.id, second.id
    with factory() as db:
        assert scheduler.admit(db, first_id)
    with factory() as db:
        first_epoch = read_epoch(db)
    with factory.begin() as db:
        scheduler.release(db, first_id, "epoch-first")
    with factory() as db:
        assert scheduler.admit(db, second_id)
    with factory() as db:
        second_epoch = read_epoch(db)
    assert second_epoch["epoch"] == first_epoch["epoch"] + 1
    assert second_epoch["lease_id"] == second_id


def test_expired_waiting_reservation_does_not_starve_a_live_serial_stage(api):
    from datetime import timedelta

    from shine.models import utcnow

    _, factory, _ = api
    scheduler = ResourceScheduler()
    with factory.begin() as db:
        stale = scheduler.enqueue(db, "h3:orphaned-series-job", 16384, True, 100)
        live = scheduler.enqueue(db, "agent:current-request", 1, True, 10)
        stale_id, live_id = stale.id, live.id
        stale.expires_at = utcnow() - timedelta(seconds=1)

    with factory() as db:
        assert scheduler.admit(db, live_id)
    with factory() as db:
        assert db.get(ResourceLease, stale_id).status == "RELEASED"
        assert db.get(ResourceLease, live_id).status == "ACTIVE"


def test_expired_active_lease_still_blocks_until_reconciliation(api):
    from datetime import timedelta

    from shine.models import utcnow

    _, factory, _ = api
    scheduler = ResourceScheduler()
    with factory.begin() as db:
        active = scheduler.enqueue(db, "h3:expired-active-renderer", 16384, True, 100)
        waiting = scheduler.enqueue(db, "agent:waiting-stage", 1, True, 10)
        active_id, waiting_id = active.id, waiting.id
    with factory() as db:
        assert scheduler.admit(db, active_id)
    with factory.begin() as db:
        db.get(ResourceLease, active_id).expires_at = utcnow() - timedelta(seconds=1)
    with factory() as db:
        assert not scheduler.admit(db, waiting_id)
    with factory() as db:
        assert db.get(ResourceLease, active_id).status == "RECONCILE_REQUIRED"
        assert db.get(ResourceLease, waiting_id).status == "WAITING"


def test_preflight_and_postflight_release_hold_the_slot(api):
    _, factory, _ = api
    events = []

    def cleanup():
        with factory() as db:
            assert len(list(db.scalars(select(ResourceLease).where(ResourceLease.status == "ACTIVE")))) == 1
        with pytest.raises(HTTPException, match="GPU_BUSY"):
            with resource_slot(factory, "AGENT", 1):
                raise AssertionError("Stage overlap")
        events.append("released")

    with resource_slot(factory, "VLM", 8000, release_models=cleanup):
        events.append("inference")
    assert events == ["released", "inference", "released"]
    with resource_slot(factory, "ASR", 4000):
        pass


def test_preflight_release_failure_is_not_retried_and_keeps_reconciliation_block(api):
    _, factory, _ = api
    calls = []

    def failed_release():
        calls.append("release")
        raise RuntimeError("MODEL_RELEASE_UNCONFIRMED")

    with pytest.raises(RuntimeError, match="MODEL_RELEASE_UNCONFIRMED"):
        with resource_slot(factory, "VLM", 8000, release_models=failed_release):
            pytest.fail("Inference must not start while preflight release is unconfirmed")

    assert calls == ["release"]
    with factory() as db:
        lease = db.scalar(select(ResourceLease))
        assert lease.status == "RECONCILE_REQUIRED"


def test_expired_stage_lease_requires_reconciliation_even_if_cleanup_succeeds(api):
    from datetime import timedelta

    from shine.models import utcnow

    _, factory, _ = api
    with pytest.raises(HTTPException, match="RECONCILIATION_REQUIRED"):
        with resource_slot(factory, "VLM", 8000, release_models=lambda: None) as identity:
            with factory.begin() as db:
                db.get(ResourceLease, identity).expires_at = utcnow() - timedelta(seconds=1)
    with factory() as db:
        assert db.get(ResourceLease, identity).status == "RECONCILE_REQUIRED"
    with pytest.raises(HTTPException, match="GPU_BUSY"):
        with resource_slot(factory, "ASR", 4000):
            pass


def test_inherited_expired_lease_cannot_start_another_model(api):
    from datetime import timedelta

    from shine.comfyops.model_calls import TrackedProvider
    from shine.comfyops.scheduler import CURRENT_RESOURCE
    from shine.models import utcnow
    from tests.conftest import create_project

    client, factory, _ = api
    project = create_project(client)
    calls = []

    class Provider:
        is_mock = True

        def generate(self, text):
            calls.append(text)

    with factory.begin() as db:
        lease = ResourceLease(owner="h3:expired", budget_mb=16384, exclusive=True, priority=100, status="ACTIVE",
                              expires_at=utcnow() - timedelta(seconds=1))
        db.add(lease)
        db.flush()
        lease_id = lease.id
    token = CURRENT_RESOURCE.set(lease_id)
    try:
        with pytest.raises(HTTPException, match="RESOURCE_LEASE_LOST"):
            TrackedProvider(Provider(), factory, project).generate("synthetic")
    finally:
        CURRENT_RESOURCE.reset(token)
    assert not calls
    with factory() as db:
        assert db.get(ResourceLease, lease_id).status == "RECONCILE_REQUIRED"


def test_inherited_lease_cannot_admit_parallel_model_calls(api):
    from datetime import timedelta
    from threading import Event, Thread

    from shine.comfyops.model_calls import TrackedProvider
    from shine.comfyops.scheduler import CURRENT_RESOURCE
    from shine.models import utcnow
    from tests.conftest import create_project

    client, factory, _ = api
    project = create_project(client)
    started, release = Event(), Event()
    entered, failures = [], []

    class Provider:
        is_mock = True

        def generate(self, text):
            entered.append(text)
            if text == "first":
                started.set()
                assert release.wait(10)
            return {"is_mock": True}

    with factory.begin() as db:
        lease = ResourceLease(owner="h3:serial-test", budget_mb=16384, exclusive=True, priority=100,
                              status="ACTIVE", expires_at=utcnow() + timedelta(seconds=60))
        db.add(lease)
        db.flush()
        lease_id = lease.id

    def invoke(text):
        token = CURRENT_RESOURCE.set(lease_id)
        try:
            TrackedProvider(Provider(), factory, project).generate(text)
        except Exception as exc:
            failures.append(exc)
        finally:
            CURRENT_RESOURCE.reset(token)

    first = Thread(target=invoke, args=("first",))
    first.start()
    try:
        assert started.wait(10)
        second = Thread(target=invoke, args=("second",))
        second.start()
        second.join(10)
        assert not second.is_alive()
        assert entered == ["first"]
        assert len(failures) == 1 and "RESOURCE_STAGE_BUSY" in str(failures[0])
    finally:
        release.set()
        first.join(10)
    assert not first.is_alive()
    invoke("third")
    assert entered == ["first", "third"]
    assert len(failures) == 1


def test_nested_release_failure_cannot_be_cleared_by_parent_cleanup(api):
    _, factory, _ = api

    class Provider:
        calls = 0

        def release_models(self):
            self.calls += 1
            if self.calls == 2:
                raise OSError("Model still resident")

    with pytest.raises(HTTPException, match="RECONCILIATION_REQUIRED"):
        with resource_slot(factory, "PREPARATION", 12000, release_models=lambda: None) as identity:
            with model_step(Provider(), factory, identity):
                pass
    with factory() as db:
        assert db.get(ResourceLease, identity).status == "RECONCILE_REQUIRED"
    with pytest.raises(HTTPException, match="GPU_BUSY"):
        with resource_slot(factory, "AGENT", 1):
            pass


def test_review_intent_tracks_derived_frame_content(tmp_path):
    @dataclass
    class Context:
        frame: object
        review_revision: str

    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"first")
    first = digest_input(Context(frame, "intent-one"))
    frame.write_bytes(b"different")
    assert digest_input(Context(frame, "intent-one")) != first
    assert digest_input(Context(frame, "intent-two")) != digest_input(Context(frame, "intent-one"))


def test_h3_preflight_release_failure_blocks_without_creating_attempt(api, monkeypatch):
    # Isolate the release failure after admission has been established.
    monkeypatch.setattr("shine.worker._handoff_preflight_allowed", lambda *_: True)
    from types import SimpleNamespace

    from shine.models import Job
    from shine.worker import run_one
    from tests.conftest import create_comfy_job, create_project, create_referenced_shot, freeze_style

    client, factory, settings = api
    project = create_project(client)
    style = freeze_style(client, project)
    shot = create_referenced_shot(client, project, style["id"])
    job = create_comfy_job(client, project, shot["id"])

    def unavailable():
        raise OSError("Unload remains unconfirmed")

    run_one(factory, settings=settings, comfy_client=SimpleNamespace(free_models=unavailable))
    result = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert result["status"] == "NEEDS_REVIEW"
    assert result["failure_code"] == "MODEL_RELEASE_RECONCILIATION_REQUIRED"
    assert not result["attempts"] and not result["provider_job_id"]
    with factory() as db:
        assert not db.get(Job, job["id"]).lease_owner
        assert db.scalar(select(ResourceLease)).status == "RECONCILE_REQUIRED"
