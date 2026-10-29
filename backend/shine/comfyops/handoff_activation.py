"""Open a fenced renderer only for an exclusively leased, claimed attempt."""

import hashlib
import re
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import select, text

from shine.comfy.workflow import graph_hash
from shine.comfyops.models import ComfyInstance, ResourceLease
from shine.comfyops.preproduction import enforce_job
from shine.comfyops.renderer_supervisor import RendererSupervisor
from shine.models import AgentTaskRecord, AppSetting, Job, JobAttempt, Shot, utcnow
from shine.services import add_audit, try_transition


class ReviewReleaseBoundary:
    """Live proofs for the immediately preceding managed review, never a stored pass flag."""

    def __init__(self, session, *, min_ram_mb, min_vram_mb, cold_fences=None, review_configs=None,
                 prior_releases=None):
        from shine.comfyops.handoff_preparation import HandoffSession

        if (not isinstance(session, HandoffSession) or not session.finished or not session.accepted
                or type(min_ram_mb) is not int or min_ram_mb < session.min_ram_mb
                or type(min_vram_mb) is not int or min_vram_mb < session.min_vram_mb):
            raise HTTPException(409, "COMFY_PHYSICAL_RELEASE_UNVERIFIED")
        self._session = session
        self._min_ram_mb, self._min_vram_mb = min_ram_mb, min_vram_mb
        self._cold_fences = dict(cold_fences or {})
        self._review_configs = dict(review_configs or {})
        self._prior_releases = dict(prior_releases or {})
        if (not set(self._cold_fences).issubset(self._review_configs)
                or set(self._cold_fences) & (set(session.accepted) | set(self._prior_releases))
                or not set(self._prior_releases).issubset(self._review_configs)):
            raise HTTPException(409, "COMFY_PHYSICAL_RELEASE_UNVERIFIED")

    def verify(self, factory, settings, instance_id, current_attempt_id, current_lease_id):
        from shine.comfyops.resource_epoch import read_epoch
        from shine.quality.review_release import verify_cold_review_fence, verify_review_release

        session = self._session
        if (session.factory is not factory or session.attempt_id == current_attempt_id
                or set(session.accepted) != set(session.review_requests)):
            raise HTTPException(409, "COMFY_PHYSICAL_RELEASE_UNVERIFIED")
        with factory() as db:
            row = db.get(AppSetting, "handoff:" + session.attempt_id)
            value = row.value if row else {}
            instance = db.get(ComfyInstance, instance_id)
            previous_job = db.get(Job, value.get("job_id")) if value.get("job_id") else None
            steps = value.get("review_steps", [])
            if (value.get("stage") != "RESTARTED" or value.get("attempt_id") != session.attempt_id
                    or value.get("instance_id") != instance_id or not previous_job
                    or not instance or value.get("restarted_instance_sha256") != instance.sha256
                    or graph_hash(instance.payload) != instance.sha256 or len(steps) != len(session.accepted)):
                raise HTTPException(409, "COMFY_PHYSICAL_RELEASE_UNVERIFIED")
            try:
                recorded_at = datetime.fromisoformat(value["stage_recorded_at"])
                if recorded_at.tzinfo is not None:
                    raise ValueError
            except (KeyError, TypeError, ValueError) as exc:
                raise HTTPException(409, "COMFY_PHYSICAL_RELEASE_UNVERIFIED") from exc
            previous_lease_id = value.get("resource_id")
            recorded_epoch = value.get("resource_epoch")
            current_epoch = read_epoch(db)
            if (not isinstance(recorded_epoch, dict) or type(recorded_epoch.get("epoch")) is not int
                    or recorded_epoch["epoch"] < 0):
                raise HTTPException(409, "COMFY_PHYSICAL_RELEASE_UNVERIFIED")
            same_lease = current_lease_id == previous_lease_id and current_epoch == recorded_epoch
            newly_admitted = (current_epoch["epoch"] == recorded_epoch["epoch"] + 1
                              and current_epoch["kind"] == "lease" and current_epoch["lease_id"] == current_lease_id)
            if not (same_lease or newly_admitted):
                raise HTTPException(409, "COMFY_PHYSICAL_RELEASE_UNVERIFIED")
            leases = db.scalars(select(ResourceLease).where(ResourceLease.id != current_lease_id))
            for other in leases:
                if other.status in {"ACTIVE", "RECONCILE_REQUIRED"}:
                    raise HTTPException(409, "COMFY_PHYSICAL_RELEASE_UNVERIFIED")
                if (other.id != previous_lease_id and
                        (other.created_at > recorded_at or other.heartbeat_at > recorded_at)):
                    raise HTTPException(409, "COMFY_PHYSICAL_RELEASE_UNVERIFIED")
            later_calls = db.scalars(select(AgentTaskRecord).where(
                AgentTaskRecord.role_id.like("model_call:%"), AgentTaskRecord.created_at > recorded_at,
            ))
            if any(True for _ in later_calls):
                raise HTTPException(409, "COMFY_PHYSICAL_RELEASE_UNVERIFIED")
            expected = {}
            last_review_epoch = -1
            for step in steps:
                kind = step.get("kind")
                task = db.get(AgentTaskRecord, step.get("task_id")) if step.get("task_id") else None
                proof = step.get("proof") or {}
                step_epoch = step.get("resource_epoch")
                mock = (task.trace or {}).get("is_mock") is not False if task else True
                if (kind not in session.accepted or kind in expected or step.get("status") != "VERIFIED"
                        or not task or task.project_id != previous_job.project_id or task.status != "COMPLETED"
                        or not isinstance(step_epoch, dict) or type(step_epoch.get("epoch")) is not int
                        or step_epoch["epoch"] <= last_review_epoch or step_epoch.get("kind") != "model"
                        or step_epoch.get("lease_id") != previous_lease_id
                        or (task.trace or {}).get("resource_epoch") != step_epoch
                        or (task.trace or {}).get("production_review", {}).get("job_id") != previous_job.id
                        or (mock and not (settings.offline_simulation and session.settings.offline_simulation))
                        or (not mock and (task.trace or {}).get("result_is_mock") is not False)
                        or (task.trace or {}).get("handoff_review_id") != step.get("id")
                        or (task.trace or {}).get("handoff_release", {}).get("proof") != proof
                        or session.accepted[kind].get("task_id") != task.id):
                    raise HTTPException(409, "COMFY_PHYSICAL_RELEASE_UNVERIFIED")
                last_review_epoch = step_epoch["epoch"]
                config = session.review_requests[kind]
                if ((task.trace or {}).get("isolation_receipt") != config.get("receipt")
                        or proof.get("review_stage_id") != config.get("receipt", {}).get("stage_id")
                        or (task.trace or {}).get("review_provider", {}).get("base_url_sha256") != hashlib.sha256(
                            str(config.get("base_url", "")).encode()).hexdigest()
                        or config.get("gpu_uuid") != session.gpu_uuid):
                    raise HTTPException(409, "COMFY_PHYSICAL_RELEASE_UNVERIFIED")
                expected[kind] = config
            if not steps or steps[-1].get("resource_epoch") != recorded_epoch:
                raise HTTPException(409, "COMFY_PHYSICAL_RELEASE_UNVERIFIED")
        verified = []
        for kind, config in expected.items():
            proof = verify_review_release(**{**config, "min_ram_mb": self._min_ram_mb,
                                             "min_vram_mb": self._min_vram_mb})
            if proof.get("review_stage_id") != config["receipt"]["stage_id"]:
                raise HTTPException(409, "COMFY_PHYSICAL_RELEASE_UNVERIFIED")
            verified.append({"kind": kind, "review_stage_id": proof["review_stage_id"], "memory": proof["memory"]})
        for kind, config in self._prior_releases.items():
            if kind in expected:
                if config != expected[kind]:
                    raise HTTPException(409, "COMFY_PHYSICAL_RELEASE_UNVERIFIED")
                continue
            if {key: value for key, value in config.items() if key != "receipt"} != self._review_configs[kind]:
                raise HTTPException(409, "COMFY_PHYSICAL_RELEASE_UNVERIFIED")
            proof = verify_review_release(**{**config, "min_ram_mb": self._min_ram_mb,
                                             "min_vram_mb": self._min_vram_mb})
            if proof.get("review_stage_id") != config.get("receipt", {}).get("stage_id"):
                raise HTTPException(409, "COMFY_PHYSICAL_RELEASE_UNVERIFIED")
            verified.append({"kind": kind, "review_stage_id": proof["review_stage_id"], "memory": proof["memory"]})
        for kind, fence_id in self._cold_fences.items():
            proof = verify_cold_review_fence(**{**self._review_configs[kind], "fence_id": fence_id,
                                                "min_ram_mb": self._min_ram_mb, "min_vram_mb": self._min_vram_mb})
            verified.append({"kind": kind, "cold_fence_id": fence_id, "memory": proof["memory"]})
        with factory() as db:
            if read_epoch(db) != current_epoch:
                raise HTTPException(409, "COMFY_PHYSICAL_RELEASE_UNVERIFIED")
        return {"prior_attempt_id": session.attempt_id, "gpu_uuid": session.gpu_uuid, "reviews": verified,
                "observed_resource_epoch": current_epoch}


def _check(db, settings, job_id, attempt_id, lease_id, instance_id, worker_id, state_version):
    job, attempt = db.get(Job, job_id), db.get(JobAttempt, attempt_id)
    lease, instance = db.get(ResourceLease, lease_id), db.get(ComfyInstance, instance_id)
    shot = db.get(Shot, job.shot_id) if job else None
    if (not job or job.provider != "comfyui" or job.status not in {"SUBMITTING", "RUNNING"} or job.stale
            or job.lease_owner != worker_id or job.state_version != state_version
            or not job.lease_expires_at or job.lease_expires_at <= utcnow() or not shot
            or shot.revision != job.shot_revision or not attempt or attempt.job_id != job_id
            or attempt.status != "SUBMIT_INTENT"
            or not isinstance(attempt.prompt_graph_sha256, str)
            or not re.fullmatch(r"[a-f0-9]{64}", attempt.prompt_graph_sha256)
            or attempt.lease_owner != worker_id or attempt.lease_expires_at <= utcnow()
            or attempt.provider_job_id or job.provider_job_id or not lease or lease.owner != "h3:" + job_id
            or lease.status != "ACTIVE" or not lease.exclusive or lease.expires_at <= utcnow()):
        raise HTTPException(409, "COMFY_ACTIVATION_CLAIM_INVALID")
    payload = instance.payload if instance else {}
    if (not instance or instance.status != "RUNNING_PENDING_HEALTH" or graph_hash(payload) != instance.sha256
            or payload.get("managed") is not True or payload.get("is_mock") is not False):
        raise HTTPException(409, "COMFY_ACTIVATION_INSTANCE_INVALID")
    enforce_job(db, job, settings)
    return job, instance


def activate_renderer_for_attempt(factory, settings, supervisor, job_id, attempt_id, lease_id, instance_id,
                                  worker_id, state_version, *, physical_boundary=None):
    """Persist intent before opening admission, retaining the lease on uncertainty."""
    if not isinstance(supervisor, RendererSupervisor):
        raise HTTPException(409, "COMFY_ACTIVATION_SUPERVISOR_INVALID")
    if not isinstance(physical_boundary, ReviewReleaseBoundary):
        raise HTTPException(409, "COMFY_PHYSICAL_RELEASE_UNVERIFIED")
    supervisor.require_closed_restart()
    key = "renderer-activation:" + attempt_id
    with factory.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        if db.get(AppSetting, key):
            raise HTTPException(409, "COMFY_ACTIVATION_ALREADY_RECORDED")
        job, instance = _check(db, settings, job_id, attempt_id, lease_id, instance_id, worker_id, state_version)
        payload, instance_hash = dict(instance.payload), instance.sha256
        db.add(AppSetting(key=key, value={"stage": "INTENT", "job_id": job_id, "attempt_id": attempt_id,
                                         "lease_id": lease_id, "instance_id": instance_id,
                                         "instance_sha256": instance_hash, "worker_id": worker_id,
                                         "state_version": state_version}))
        add_audit(db, "comfy-worker", "COMFY_ACTIVATION_INTENT", "job", job_id,
                  {"attempt_id": attempt_id, "instance_id": instance_id}, project_id=job.project_id)
    try:
        from shine.comfyops.resource_epoch import read_epoch

        release_evidence = physical_boundary.verify(factory, settings, instance_id, attempt_id, lease_id)
        with factory.begin() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            journal = db.get(AppSetting, key)
            if not journal or journal.value.get("stage") != "INTENT":
                raise HTTPException(409, "COMFY_ACTIVATION_INTENT_CHANGED")
            job, instance = _check(db, settings, job_id, attempt_id, lease_id, instance_id, worker_id, state_version)
            if instance.sha256 != instance_hash or read_epoch(db) != release_evidence.get("observed_resource_epoch"):
                raise HTTPException(409, "COMFY_ACTIVATION_INSTANCE_INVALID")
            owner = supervisor.verify_drained_binding(payload.get("url", ""), payload.get("process"))
            supervisor._control("POST", "/shine-handoff/resume", "accepting", owner)
            _check(db, settings, job_id, attempt_id, lease_id, instance_id, worker_id, state_version)
            if read_epoch(db) != release_evidence["observed_resource_epoch"]:
                raise HTTPException(409, "COMFY_PHYSICAL_RELEASE_UNVERIFIED")
            journal.value = {**journal.value, "stage": "CONFIRMED", "listener": owner,
                             "physical_release": release_evidence}
            add_audit(db, "comfy-worker", "COMFY_ACTIVATION_CONFIRMED", "job", job_id,
                      {"attempt_id": attempt_id}, project_id=job.project_id)
        return owner
    except BaseException:
        with factory.begin() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            journal, lease, job = db.get(AppSetting, key), db.get(ResourceLease, lease_id), db.get(Job, job_id)
            if journal and journal.value.get("stage") == "INTENT":
                journal.value = {**journal.value, "stage": "RECONCILE_REQUIRED"}
            if lease and lease.owner == "h3:" + job_id:
                lease.status = "RECONCILE_REQUIRED"
            if job:
                try_transition(job, "NEEDS_REVIEW")
                job.failure_code = "COMFY_ACTIVATION_RECONCILIATION_REQUIRED"
                job.lease_owner = None
                job.lease_expires_at = None
                add_audit(db, "comfy-worker", "COMFY_ACTIVATION_UNCERTAIN", "job", job_id,
                          {"attempt_id": attempt_id, "resubmitted": False}, project_id=job.project_id)
        raise
