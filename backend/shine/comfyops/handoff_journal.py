"""Durable intent journal for a future owned ComfyUI process handoff.

The journal is not physical evidence and does not itself stop a process. A
controller must independently verify every recorded observation. The same
exclusive lease remains held across all stages and any uncertain outcome.
"""

import os
from contextvars import ContextVar
from datetime import timedelta
from threading import Event, Thread
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import text

from shine.comfy.workflow import graph_hash
from shine.comfyops.installer import process_identity
from shine.comfyops.models import ComfyInstance, ResourceLease
from shine.comfyops.scheduler import CURRENT_RESOURCE
from shine.models import AgentTaskRecord, AppSetting, Job, JobAttempt, MediaArtifact, Shot, utcnow
from shine.security import is_loopback_url
from shine.services import add_audit, try_transition

NEXT_STAGE = {
    "INTENT": "DRAIN_INTENT",
    "DRAIN_INTENT": "DRAINED",
    "DRAINED": "STOP_INTENT",
    "STOP_INTENT": "STOPPED",
    "STOPPED": "PHYSICAL_READY",
    "PHYSICAL_READY": "REVIEW_INTENT",
    "REVIEW_INTENT": "REVIEW_FINISHED",
    "REVIEW_FINISHED": "RESTART_INTENT",
    "RESTART_INTENT": "RESTARTED",
    "RESTARTED": "COMPLETE",
}

CURRENT_HANDOFF_REVIEW = ContextVar("shine_handoff_review", default=None)


def _mark_claim_for_reconciliation(job, value):
    """Do not overwrite a newer worker claim or a terminal decision."""
    if (not job or job.id != value.get("job_id") or job.lease_owner != value.get("worker_id")
            or job.state_version != value.get("state_version") or job.status not in {"RUNNING", "QC_PENDING"}):
        return
    if try_transition(job, "NEEDS_REVIEW"):
        job.failure_code = "COMFY_HANDOFF_RECONCILIATION_REQUIRED"
        job.lease_owner = None
        job.lease_expires_at = None


def reserve_handoff_review(factory, attempt_id, kind, *, settings=None):
    """Persist a single review intent before any model request can be made."""
    if kind not in {"ASR", "VLM"}:
        raise HTTPException(409, "HANDOFF_REVIEW_KIND_INVALID")
    pulse_handoff(factory, attempt_id)
    with factory.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        row = db.get(AppSetting, "handoff:" + attempt_id)
        value = row.value if row else {}
        if value.get("stage") != "REVIEW_INTENT":
            raise HTTPException(409, "HANDOFF_STAGE_CHANGED")
        lease = db.get(ResourceLease, value["resource_id"])
        job = db.get(Job, value["job_id"])
        shot = db.get(Shot, job.shot_id) if job else None
        attempt = db.get(JobAttempt, attempt_id)
        artifact = db.get(MediaArtifact, value["artifact_id"])
        if (not lease or lease.owner != "h3:" + value["job_id"] or lease.status != "ACTIVE"
                or not lease.exclusive or lease.expires_at <= utcnow() or not job
                or job.status not in {"RUNNING", "QC_PENDING"} or job.stale
                or job.lease_owner != value["worker_id"] or job.state_version != value["state_version"]
                or not job.lease_expires_at or job.lease_expires_at <= utcnow()
                or not shot or shot.revision != job.shot_revision or job.artifact_id != value["artifact_id"]
                or not artifact or artifact.sha256 != value["artifact_sha256"] or artifact.attempt_id != attempt_id
                or not attempt or attempt.job_id != job.id or attempt.provider_job_id != value["provider_job_id"]
                or attempt.prompt_graph_sha256 != value["prompt_graph_sha256"]
                or os.getpid() != value["worker_pid"] or process_identity(os.getpid()) != value["worker_birth"]):
            raise HTTPException(409, "HANDOFF_JOB_CLAIM_LOST")
        from shine.comfyops.preproduction import enforce_job

        enforce_job(db, job, settings)
        steps = list(value.get("review_steps", []))
        if any(step.get("status") != "VERIFIED" for step in steps):
            raise HTTPException(409, "HANDOFF_REVIEW_IN_FLIGHT")
        if any(step.get("kind") == kind for step in steps):
            raise HTTPException(409, "HANDOFF_REVIEW_DUPLICATE")
        identity = str(uuid4())
        row.value = {**value, "review_steps": [*steps, {"id": identity, "kind": kind, "status": "INTENT"}]}
        add_audit(db, "comfy-worker", "HANDOFF_REVIEW_INTENT", "job", job.id,
                  {"attempt_id": attempt_id, "review_id": identity, "kind": kind}, project_id=job.project_id)
    return identity


def bind_handoff_review_task(factory, attempt_id, review_id, task_id, *, settings=None):
    """Bind a still-unsubmitted task to the persisted intent in one transaction."""
    pulse_handoff(factory, attempt_id)
    with factory.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        row = db.get(AppSetting, "handoff:" + attempt_id)
        value = row.value if row else {}
        steps = list(value.get("review_steps", []))
        task = db.get(AgentTaskRecord, task_id)
        job = db.get(Job, value.get("job_id")) if value.get("job_id") else None
        lease = db.get(ResourceLease, value.get("resource_id")) if value.get("resource_id") else None
        shot = db.get(Shot, job.shot_id) if job else None
        if (value.get("stage") != "REVIEW_INTENT" or not steps or steps[-1].get("id") != review_id
                or steps[-1].get("status") != "INTENT" or steps[-1].get("task_id") is not None
                or not task or not job or task.project_id != job.project_id or task.status != "RUNNING"
                or not lease or lease.status != "ACTIVE" or not lease.exclusive or lease.expires_at <= utcnow()
                or lease.owner != "h3:" + job.id or job.lease_owner != value.get("worker_id")
                or job.state_version != value.get("state_version") or not job.lease_expires_at
                or job.lease_expires_at <= utcnow() or job.stale or not shot or shot.revision != job.shot_revision
                or (task.trace or {}).get("submitted") or (task.trace or {}).get("handoff_review_id")
                or (task.trace or {}).get("resource_id") != value.get("resource_id")
                or (task.trace or {}).get("kind") != steps[-1].get("kind")):
            raise HTTPException(409, "HANDOFF_REVIEW_TASK_BINDING_INVALID")
        from shine.comfyops.preproduction import enforce_job

        enforce_job(db, job, settings)
        task.trace = {**task.trace, "handoff_review_id": review_id}
        steps[-1] = {**steps[-1], "task_id": task_id}
        row.value = {**value, "review_steps": steps}


def begin_handoff(factory, job_id, attempt_id, lease_id, instance_id, worker_id, state_version, comfy_url,
                  *, settings=None):
    """Commit the exact output, instance and resource intent before side effects."""
    with factory.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        key = "handoff:" + attempt_id
        if db.get(AppSetting, key):
            raise HTTPException(409, "HANDOFF_ALREADY_EXISTS")
        job, attempt = db.get(Job, job_id), db.get(JobAttempt, attempt_id)
        lease, instance = db.get(ResourceLease, lease_id), db.get(ComfyInstance, instance_id)
        shot = db.get(Shot, job.shot_id) if job else None
        now = utcnow()
        if (not job or job.provider != "comfyui" or job.status not in {"RUNNING", "QC_PENDING"}
                or job.stale or not shot or shot.revision != job.shot_revision
                or job.lease_owner != worker_id or job.state_version != state_version
                or not job.lease_expires_at or job.lease_expires_at <= now
                or not attempt or attempt.job_id != job.id or not attempt.provider_job_id
                or not attempt.prompt_graph_sha256):
            raise HTTPException(409, "HANDOFF_JOB_CLAIM_LOST")
        if (not lease or lease.owner != "h3:" + job_id or lease.status != "ACTIVE"
                or lease.expires_at <= now or not lease.exclusive):
            raise HTTPException(409, "HANDOFF_RESOURCE_LEASE_LOST")
        from shine.comfyops.preproduction import enforce_job

        enforce_job(db, job, settings)
        artifact = db.get(MediaArtifact, job.artifact_id) if job.artifact_id else None
        if (not artifact or artifact.job_id != job_id or artifact.attempt_id != attempt_id
                or artifact.sha256 != attempt.output_sha256 or not artifact.sha256
                or artifact.byte_size != attempt.output_bytes or artifact.storage_path != attempt.output_path):
            raise HTTPException(409, "HANDOFF_OUTPUT_NOT_PERSISTED")
        payload = instance.payload if instance else {}
        process = payload.get("process") or {}
        if (not instance or instance.status != "RUNNING_PENDING_HEALTH" or instance.sha256 != graph_hash(payload)
                or payload.get("managed") is not True or payload.get("is_mock") is not False
                or payload.get("url", "").rstrip("/") != comfy_url.rstrip("/")
                or not is_loopback_url(payload.get("url", ""))
                or type(process.get("pid")) is not int or not isinstance(process.get("birth"), str)
                or not process["birth"]):
            raise HTTPException(409, "COMFY_HANDOFF_OWNERSHIP_UNVERIFIED")
        owner_birth = process_identity(os.getpid())
        if not owner_birth:
            raise HTTPException(409, "HANDOFF_WORKER_IDENTITY_UNVERIFIED")
        value = {
            "stage": "INTENT", "job_id": job_id, "attempt_id": attempt_id, "resource_id": lease_id,
            "instance_id": instance_id, "instance_sha256": instance.sha256, "process": dict(process),
            "instance_url": payload["url"], "worker_id": worker_id, "worker_pid": os.getpid(),
            "worker_birth": owner_birth, "state_version": state_version, "artifact_id": artifact.id,
            "artifact_sha256": artifact.sha256, "provider_job_id": attempt.provider_job_id,
            "prompt_graph_sha256": attempt.prompt_graph_sha256,
            "evidence": {},
        }
        db.add(AppSetting(key=key, value=value))
        add_audit(db, "comfy-worker", "COMFY_HANDOFF_INTENT", "job", job.id,
                  {"attempt_id": attempt.id, "resource_id": lease_id, "instance_id": instance_id},
                  project_id=job.project_id)
    return value


def record_handoff_stage(factory, attempt_id, expected, stage, evidence, *, settings=None,
                         expected_resource_epoch=None):
    """Record one ordered transition; no transition releases the resource."""
    if NEXT_STAGE.get(expected) != stage:
        raise HTTPException(409, "HANDOFF_STAGE_INVALID")
    with factory.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        row = db.get(AppSetting, "handoff:" + attempt_id)
        if not row or row.value.get("stage") != expected:
            raise HTTPException(409, "HANDOFF_STAGE_CHANGED")
        value = row.value
        if stage == "REVIEW_FINISHED" and any(
            step.get("status") != "VERIFIED" for step in value.get("review_steps", [])
        ):
            raise HTTPException(409, "HANDOFF_REVIEW_IN_FLIGHT")
        if os.getpid() != value["worker_pid"] or process_identity(os.getpid()) != value["worker_birth"]:
            raise HTTPException(409, "HANDOFF_WORKER_IDENTITY_CHANGED")
        lease = db.get(ResourceLease, value["resource_id"])
        job = db.get(Job, value["job_id"])
        artifact = db.get(MediaArtifact, value["artifact_id"])
        attempt = db.get(JobAttempt, attempt_id)
        shot = db.get(Shot, job.shot_id) if job else None
        if (not lease or lease.owner != "h3:" + value["job_id"] or lease.status != "ACTIVE"
                or lease.expires_at <= utcnow()):
            raise HTTPException(409, "HANDOFF_RESOURCE_LEASE_LOST")
        if (not job or job.status not in {"RUNNING", "QC_PENDING"} or job.stale
                or not shot or shot.revision != job.shot_revision
                or job.lease_owner != value["worker_id"] or job.state_version != value["state_version"]
                or job.artifact_id != value["artifact_id"] or not job.lease_expires_at
                or job.lease_expires_at <= utcnow() or not artifact
                or artifact.sha256 != value["artifact_sha256"] or artifact.attempt_id != attempt_id
                or not attempt or attempt.job_id != job.id or attempt.provider_job_id != value["provider_job_id"]
                or attempt.prompt_graph_sha256 != value["prompt_graph_sha256"]):
            raise HTTPException(409, "HANDOFF_JOB_CLAIM_LOST")
        from shine.comfyops.preproduction import enforce_job

        enforce_job(db, job, settings)
        if stage == "RESTARTED":
            from shine.comfyops.resource_epoch import read_epoch

            current_epoch = read_epoch(db)
            if not isinstance(expected_resource_epoch, dict) or current_epoch != expected_resource_epoch:
                raise HTTPException(409, "HANDOFF_RESOURCE_ACTIVITY_CHANGED")
            instance = db.get(ComfyInstance, value["instance_id"])
            process = evidence.get("process") if isinstance(evidence, dict) else None
            if (not instance or instance.sha256 != value["instance_sha256"]
                    or graph_hash(instance.payload) != instance.sha256
                    or instance.payload.get("process") != value["process"] or not isinstance(process, dict)
                    or type(process.get("pid")) is not int or process["pid"] <= 0
                    or not isinstance(process.get("birth"), str) or not process["birth"].isdigit()):
                raise HTTPException(409, "HANDOFF_RESTART_IDENTITY_CHANGED")
            instance.payload = {**instance.payload, "process": dict(process)}
            instance.sha256 = graph_hash(instance.payload)
            value = {**value, "restarted_instance_sha256": instance.sha256, "resource_epoch": current_epoch}
        row.value = {**value, "stage": stage, "stage_recorded_at": utcnow().isoformat(),
                     "evidence": {**value["evidence"], stage: evidence}}
        add_audit(db, "comfy-worker", "COMFY_HANDOFF_STAGE", "job", job.id,
                  {"attempt_id": attempt_id, "stage": stage}, project_id=job.project_id)


def pulse_handoff(factory, attempt_id, *, lease_seconds=60):
    """Extend the job and resource together, never reviving an expired claim."""
    if type(lease_seconds) is not int or not 1 <= lease_seconds <= 300:
        raise ValueError("HANDOFF_HEARTBEAT_INTERVAL_INVALID")
    error = None
    with factory.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        row = db.get(AppSetting, "handoff:" + attempt_id)
        if not row or row.value.get("stage") not in NEXT_STAGE:
            raise HTTPException(409, "HANDOFF_STAGE_CHANGED")
        value = row.value
        lease = db.get(ResourceLease, value["resource_id"])
        job = db.get(Job, value["job_id"])
        attempt = db.get(JobAttempt, attempt_id)
        artifact = db.get(MediaArtifact, value["artifact_id"])
        shot = db.get(Shot, job.shot_id) if job else None
        now = utcnow()
        try:
            owner_valid = os.getpid() == value["worker_pid"] and process_identity(os.getpid()) == value["worker_birth"]
        except (OSError, ValueError):
            owner_valid = False
        if not owner_valid:
            error = "HANDOFF_WORKER_IDENTITY_CHANGED"
        elif (not lease or lease.owner != "h3:" + value["job_id"] or lease.status != "ACTIVE"
              or lease.expires_at <= now):
            error = "HANDOFF_RESOURCE_LEASE_LOST"
        elif (not job or job.status not in {"RUNNING", "QC_PENDING"} or job.lease_owner != value["worker_id"]
              or job.state_version != value["state_version"] or not job.lease_expires_at
              or job.lease_expires_at <= now or job.stale or not shot or shot.revision != job.shot_revision
              or job.artifact_id != value["artifact_id"] or not artifact
              or artifact.sha256 != value["artifact_sha256"] or artifact.attempt_id != attempt_id
              or not attempt or attempt.job_id != job.id or attempt.provider_job_id != value["provider_job_id"]
              or attempt.prompt_graph_sha256 != value["prompt_graph_sha256"]):
            error = "HANDOFF_JOB_CLAIM_LOST"
        if error:
            if lease and lease.owner == "h3:" + value["job_id"]:
                lease.status = "RECONCILE_REQUIRED"
            row.value = {**value, "stage": "RECONCILE_REQUIRED", "last_confirmed_stage": value["stage"],
                         "reason": error}
            if job:
                _mark_claim_for_reconciliation(job, value)
                add_audit(db, "comfy-worker", "COMFY_HANDOFF_RECONCILE_REQUIRED", "job", job.id,
                          {"attempt_id": attempt_id, "reason": error}, project_id=job.project_id)
        else:
            expires = now + timedelta(seconds=lease_seconds)
            lease.heartbeat_at, lease.expires_at = now, expires
            job.heartbeat_at, job.lease_expires_at = now, expires
            attempt.heartbeat_at, attempt.lease_expires_at = now, expires
    if error:
        raise HTTPException(409, error)


class HandoffHeartbeat:
    """Maintain the same exclusive lease while owned stages execute serially."""

    def __init__(self, factory, attempt_id, *, lease_seconds=60, interval=10):
        if interval <= 0 or interval >= lease_seconds:
            raise ValueError("HANDOFF_HEARTBEAT_INTERVAL_INVALID")
        self.factory, self.attempt_id = factory, attempt_id
        self.lease_seconds, self.interval = lease_seconds, interval
        self.stopped = Event()
        self.error = None
        self.thread = None
        self.token = None

    def _pulse(self):
        while not self.stopped.wait(self.interval):
            try:
                pulse_handoff(self.factory, self.attempt_id, lease_seconds=self.lease_seconds)
            except Exception as exc:
                self.error = str(exc.detail) if isinstance(exc, HTTPException) else "HANDOFF_HEARTBEAT_FAILED"
                self.stopped.set()

    def __enter__(self):
        pulse_handoff(self.factory, self.attempt_id, lease_seconds=self.lease_seconds)
        with self.factory() as db:
            resource_id = db.get(AppSetting, "handoff:" + self.attempt_id).value["resource_id"]
        self.token = CURRENT_RESOURCE.set(resource_id)
        self.thread = Thread(target=self._pulse, name="shine-handoff-heartbeat", daemon=True)
        self.thread.start()
        return self

    def check(self):
        if self.error:
            raise HTTPException(409, self.error)
        pulse_handoff(self.factory, self.attempt_id, lease_seconds=self.lease_seconds)
        if self.error:
            raise HTTPException(409, self.error)

    def __exit__(self, exc_type, exc, traceback):
        self.stopped.set()
        self.thread.join(timeout=2)
        try:
            if self.thread.is_alive():
                fail_handoff(self.factory, self.attempt_id, "HANDOFF_HEARTBEAT_EXIT_UNCONFIRMED")
                raise HTTPException(409, "HANDOFF_HEARTBEAT_EXIT_UNCONFIRMED")
            if exc is not None:
                fail_handoff(self.factory, self.attempt_id, "HANDOFF_STAGE_FAILED")
            elif self.error:
                fail_handoff(self.factory, self.attempt_id, self.error)
                raise HTTPException(409, self.error)
            else:
                self.check()
        finally:
            CURRENT_RESOURCE.reset(self.token)


def fail_handoff(factory, attempt_id, reason):
    """Preserve an uncertain side effect and block every later resource user."""
    with factory.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        row = db.get(AppSetting, "handoff:" + attempt_id)
        if not row:
            raise HTTPException(409, "HANDOFF_INTENT_MISSING")
        value = row.value
        if value["stage"] == "RECONCILE_REQUIRED":
            _mark_claim_for_reconciliation(db.get(Job, value["job_id"]), value)
            return
        if value["stage"] == "COMPLETE":
            raise HTTPException(409, "HANDOFF_ALREADY_COMPLETE")
        lease = db.get(ResourceLease, value["resource_id"])
        if not lease or lease.owner != "h3:" + value["job_id"]:
            raise HTTPException(409, "HANDOFF_RESOURCE_OWNER_CHANGED")
        lease.status = "RECONCILE_REQUIRED"
        row.value = {**value, "stage": "RECONCILE_REQUIRED", "last_confirmed_stage": value["stage"],
                     "reason": reason}
        job = db.get(Job, value["job_id"])
        if job:
            _mark_claim_for_reconciliation(job, value)
            add_audit(db, "comfy-worker", "COMFY_HANDOFF_RECONCILE_REQUIRED", "job", job.id,
                      {"attempt_id": attempt_id, "reason": reason}, project_id=job.project_id)
