"""Atomically pass exclusive admission between two managed H3 attempts.

This is an internal primitive; the normal worker does not yet call it. It
performs no renderer submission and never reuses a prior attempt's lease row.
"""

import os
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select, text

from shine.comfy.workflow import graph_hash
from shine.comfyops.handoff_activation import ReviewReleaseBoundary, _check
from shine.comfyops.installer import process_identity
from shine.comfyops.models import ComfyInstance, ResourceLease
from shine.comfyops.preproduction import enforce_job
from shine.comfyops.resource_epoch import advance_epoch, read_epoch
from shine.models import AppSetting, Job, Shot, utcnow
from shine.services import add_audit


def reserve_next_handoff_job(factory, settings, boundary, job_id, instance_id):
    """Reserve an already-queued job without exposing a gap in GPU ownership.

    This does not claim the job, open the renderer, or submit a prompt. Those
    operations require their own durable intents and guards.
    """
    if not isinstance(boundary, ReviewReleaseBoundary):
        raise HTTPException(409, "COMFY_PHYSICAL_RELEASE_UNVERIFIED")
    prior = boundary._session
    if prior.job_id == job_id:
        raise HTTPException(409, "HANDOFF_TRANSFER_TARGET_INVALID")
    evidence = boundary.verify(factory, settings, instance_id, None, prior.lease_id)
    with factory.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        key = "handoff-reservation:" + job_id
        if db.get(AppSetting, key):
            raise HTTPException(409, "HANDOFF_RESERVATION_ALREADY_RECORDED")
        row = db.get(AppSetting, "handoff:" + prior.attempt_id)
        value = row.value if row else {}
        old_lease = db.get(ResourceLease, prior.lease_id)
        old_job, job = db.get(Job, prior.job_id), db.get(Job, job_id)
        instance = db.get(ComfyInstance, instance_id)
        shot = db.get(Shot, job.shot_id) if job else None
        try:
            recorded_at = datetime.fromisoformat(value["stage_recorded_at"])
            if recorded_at.tzinfo is not None:
                raise ValueError
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(409, "HANDOFF_TRANSFER_CLAIM_INVALID") from exc
        if (value.get("stage") != "RESTARTED" or value.get("job_id") != prior.job_id
                or value.get("attempt_id") != prior.attempt_id or value.get("resource_id") != prior.lease_id
                or value.get("instance_id") != instance_id or not old_job
                or old_job.status not in {"PASSED", "NEEDS_REVIEW", "REPAIR_PENDING", "FAILED"}
                or not old_lease or old_lease.owner != "h3:" + prior.job_id or old_lease.status != "ACTIVE"
                or not old_lease.exclusive or old_lease.expires_at <= utcnow() or not instance
                or instance.sha256 != value.get("restarted_instance_sha256")
                or graph_hash(instance.payload) != instance.sha256
                or value.get("worker_pid") != os.getpid() or not value.get("worker_birth")
                or process_identity(os.getpid()) != value.get("worker_birth")
                or read_epoch(db) != evidence.get("observed_resource_epoch")
                or read_epoch(db) != value.get("resource_epoch") or not job or job.provider != "comfyui"
                or job.status != "QUEUED" or job.paused or job.stale or job.lease_owner is not None
                or not shot or shot.revision != job.shot_revision):
            raise HTTPException(409, "HANDOFF_TRANSFER_CLAIM_INVALID")
        others = db.scalars(select(ResourceLease).where(ResourceLease.id != prior.lease_id))
        for other in others:
            if (other.status in {"ACTIVE", "RECONCILE_REQUIRED"} or other.owner == "h3:" + job_id
                    or other.created_at > recorded_at or other.heartbeat_at > recorded_at):
                raise HTTPException(409, "HANDOFF_TRANSFER_RESOURCE_CONFLICT")
        enforce_job(db, job, settings)
        now = utcnow()
        lease = ResourceLease(owner="h3:" + job_id, gpu=old_lease.gpu, budget_mb=old_lease.budget_mb,
                              exclusive=True, priority=old_lease.priority, status="ACTIVE",
                              heartbeat_at=now, expires_at=now + timedelta(seconds=60))
        db.add(lease)
        db.flush()
        old_lease.status = "RELEASED"
        epoch = advance_epoch(db, lease.id, "lease")
        db.add(AppSetting(key=key, value={"stage": "RESERVED", "old_job_id": prior.job_id,
            "old_attempt_id": prior.attempt_id, "old_lease_id": prior.lease_id, "job_id": job_id,
            "lease_id": lease.id, "instance_id": instance_id, "resource_epoch": epoch,
            "worker_pid": value["worker_pid"], "worker_birth": value["worker_birth"]}))
        add_audit(db, "comfy-worker", "COMFY_HANDOFF_JOB_RESERVED", "job", job_id,
                  {"old_lease_id": prior.lease_id, "lease_id": lease.id}, project_id=job.project_id)
        result = {"lease_id": lease.id, "resource_epoch": epoch}
    return result


def transfer_handoff_lease(factory, settings, boundary, job_id, attempt_id, instance_id, worker_id, state_version):
    """Commit old release and next admission together, after a live physical recheck."""
    if not isinstance(boundary, ReviewReleaseBoundary):
        raise HTTPException(409, "COMFY_PHYSICAL_RELEASE_UNVERIFIED")
    prior = boundary._session
    if prior.job_id == job_id or prior.attempt_id == attempt_id:
        raise HTTPException(409, "HANDOFF_TRANSFER_TARGET_INVALID")
    evidence = boundary.verify(factory, settings, instance_id, attempt_id, prior.lease_id)
    with factory.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        key = "handoff-transfer:" + attempt_id
        if db.get(AppSetting, key):
            raise HTTPException(409, "HANDOFF_TRANSFER_ALREADY_RECORDED")
        journal = db.get(AppSetting, "handoff:" + prior.attempt_id)
        old_lease = db.get(ResourceLease, prior.lease_id)
        previous_job = db.get(Job, prior.job_id)
        instance = db.get(ComfyInstance, instance_id)
        value = journal.value if journal else {}
        if (value.get("stage") != "RESTARTED" or value.get("job_id") != prior.job_id
                or value.get("attempt_id") != prior.attempt_id or value.get("resource_id") != prior.lease_id
                or value.get("instance_id") != instance_id or not previous_job
                or previous_job.status not in {"PASSED", "NEEDS_REVIEW", "REPAIR_PENDING", "FAILED"}
                or not old_lease or old_lease.owner != "h3:" + prior.job_id or old_lease.status != "ACTIVE"
                or not old_lease.exclusive or old_lease.expires_at <= utcnow() or not instance
                or value.get("restarted_instance_sha256") != instance.sha256
                or read_epoch(db) != evidence.get("observed_resource_epoch")):
            raise HTTPException(409, "HANDOFF_TRANSFER_CLAIM_INVALID")
        others = db.scalars(select(ResourceLease).where(ResourceLease.id != prior.lease_id,
                                                       ResourceLease.status.in_(["ACTIVE", "RECONCILE_REQUIRED"])))
        existing_next = db.scalar(select(ResourceLease.id).where(ResourceLease.owner == "h3:" + job_id))
        if any(True for _ in others) or existing_next:
            raise HTTPException(409, "HANDOFF_TRANSFER_RESOURCE_CONFLICT")
        now = utcnow()
        new_lease = ResourceLease(owner="h3:" + job_id, gpu=old_lease.gpu, budget_mb=old_lease.budget_mb,
                                  exclusive=True, priority=old_lease.priority, status="ACTIVE",
                                  heartbeat_at=now, expires_at=now + timedelta(seconds=60))
        db.add(new_lease)
        db.flush()
        job, _instance = _check(db, settings, job_id, attempt_id, new_lease.id, instance_id, worker_id, state_version)
        old_lease.status = "RELEASED"
        epoch = advance_epoch(db, new_lease.id, "lease")
        db.add(AppSetting(key=key, value={"old_job_id": prior.job_id, "old_attempt_id": prior.attempt_id,
                                         "old_lease_id": prior.lease_id, "job_id": job_id,
                                         "attempt_id": attempt_id, "lease_id": new_lease.id,
                                         "instance_id": instance_id, "resource_epoch": epoch}))
        add_audit(db, "comfy-worker", "COMFY_HANDOFF_LEASE_TRANSFERRED", "job", job_id,
                  {"attempt_id": attempt_id, "old_lease_id": prior.lease_id, "lease_id": new_lease.id},
                  project_id=job.project_id)
        result = {"lease_id": new_lease.id, "resource_epoch": epoch}
    return result
