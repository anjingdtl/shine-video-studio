"""A worker that dies between /prompt and harvest must not deadlock the GPU.

Two facts of the 2026-11-12 live run: the image startup journal carried no
lease_id, so the dead-renderer proof never matched the image lease; and the
acknowledged attempt had no recovery path, leaving its item RUNNING forever
while the exclusive lease blocked every later render.
"""
from __future__ import annotations

import os
from datetime import timedelta

import pytest
from sqlalchemy import select

from shine.comfyops.handoff_startup import reconcile_dead_renderer_leases
from shine.comfyops.models import ResourceLease
from shine.materialprep import execution as material_execution
from shine.materialprep.models import MaterialImageAttempt, MaterialItem
from shine.materialprep.states import ATTEMPT_SUBMITTED, OUTCOME_ACKNOWLEDGED
from shine.models import AppSetting, WorkerInstance, utcnow
from tests.test_material_image_jobs import _batch, _setup


def _dead_image_journal(db, port: int) -> None:
    db.add(AppSetting(key="renderer-startup:image:inst-1", value={
        "stage": "RUNNING", "instance_id": "inst-1", "port": port, "purpose": "image",
        "process": {"pid": 999999, "birth": "134389570970542265"},
    }))


@pytest.mark.windows_only
@pytest.mark.skipif(os.name != "nt", reason="proving an empty Windows TCP listener table is Windows-only")
def test_image_lease_releases_when_the_unlinked_journal_proves_death(api):
    client, factory, _ = api
    with factory.begin() as db:
        lease = ResourceLease(owner="image:item-1:worker-old", gpu="gpu-0", budget_mb=12000,
                              exclusive=True, priority=10, status="RECONCILE_REQUIRED",
                              expires_at=utcnow() - timedelta(minutes=5))
        db.add(lease)
        db.flush()
        lease_id = lease.id
        _dead_image_journal(db, port=59999)
    findings = reconcile_dead_renderer_leases(factory)
    assert any(f["lease_id"] == lease_id for f in findings), findings
    with factory() as db:
        assert db.get(ResourceLease, lease_id).status == "RELEASED"


@pytest.mark.windows_only
@pytest.mark.skipif(os.name != "nt", reason="renderer listener ownership uses the Windows TCP table")
def test_image_lease_stays_blocked_while_a_recorded_renderer_lives(api):
    import os

    from shine.comfyops.installer import process_identity
    client, factory, _ = api
    birth = process_identity(os.getpid())
    with factory.begin() as db:
        lease = ResourceLease(owner="image:item-1:worker-old", gpu="gpu-0", budget_mb=12000,
                              exclusive=True, priority=10, status="RECONCILE_REQUIRED",
                              expires_at=utcnow() - timedelta(minutes=5))
        db.add(lease)
        db.flush()
        lease_id = lease.id
        db.add(AppSetting(key="renderer-startup:image:inst-1", value={
            "stage": "RUNNING", "instance_id": "inst-1", "port": 59999, "purpose": "image",
            "process": {"pid": os.getpid(), "birth": birth},
        }))
    reconcile_dead_renderer_leases(factory)
    with factory() as db:
        assert db.get(ResourceLease, lease_id).status == "RECONCILE_REQUIRED"


def _lost_item(db, project_id, *, owner_heartbeat):
    _, requirement = _setup(db, project_id)
    db.commit()
    batch = _batch(db, project_id, confirmation_id="c" * 64, confirmation_hash="d" * 64,
                   requirement_ids=[requirement.id], idempotency_key="loss")
    run_id = batch["run_id"]
    item = db.scalar(select(MaterialItem).where(MaterialItem.run_id == run_id))
    item.status = "RUNNING"
    item.worker_id = "worker-old"
    item.heartbeat_at = utcnow() - timedelta(minutes=30)
    db.add(WorkerInstance(worker_id="worker-old", version="test", pid=1,
                          last_heartbeat_at=owner_heartbeat))
    payload = {"prompt_id": "p-1"}
    db.add(MaterialImageAttempt(item_id=item.id, run_id=run_id, project_id=project_id,
                                attempt_no=1, status=ATTEMPT_SUBMITTED,
                                external_outcome=OUTCOME_ACKNOWLEDGED, prompt_id="p-1",
                                payload=payload, sha256="e" * 64))
    db.commit()
    return item.id


def test_a_lost_workers_acknowledged_render_becomes_review_not_resubmission(api):
    client, factory, _ = api
    project_id = client.post("/api/v1/projects", json={"title": "素材"}).json()["id"]
    with factory() as db:
        item_id = _lost_item(db, project_id, owner_heartbeat=utcnow() - timedelta(minutes=30))
    recovered = material_execution.recover_lost_image_items(factory)
    assert recovered == 1
    with factory() as db:
        item = db.get(MaterialItem, item_id)
        assert item.status == "NEEDS_REVIEW"
        assert item.error_code == "IMAGE_WORKER_LOST"
        attempt = db.scalar(select(MaterialImageAttempt).where(MaterialImageAttempt.item_id == item_id))
        assert attempt.external_outcome == "UNKNOWN"
        assert attempt.reconciliation_state == "worker_lost_before_harvest"
        assert attempt.resubmitted in (0, False)
    assert material_execution.recover_lost_image_items(factory) == 0


def test_a_live_owners_render_is_never_touched(api):
    client, factory, _ = api
    project_id = client.post("/api/v1/projects", json={"title": "素材"}).json()["id"]
    with factory() as db:
        item_id = _lost_item(db, project_id, owner_heartbeat=utcnow())
    assert material_execution.recover_lost_image_items(factory) == 0
    with factory() as db:
        assert db.get(MaterialItem, item_id).status == "RUNNING"
