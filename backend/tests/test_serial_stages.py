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


def test_h3_preflight_release_failure_blocks_without_creating_attempt(api):
    from types import SimpleNamespace

    from shine.models import Job
    from shine.worker import run_one
    from tests.conftest import create_comfy_job, create_project, create_shot, freeze_style

    client, factory, settings = api
    project = create_project(client)
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"])
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
