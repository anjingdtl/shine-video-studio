"""Explicit human decisions, authenticated separately from the Agent gateway.

The local administrator provisions a named operator and a SHA-256 token digest
in the process environment. No API creates credentials or accepts an actor name.
Possession of an OS/admin credential is the trust boundary, not a model's claim.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import replace
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import literal_column, select, text, update

from shine.comfy.client import ComfyUIClient
from shine.comfy.workflow import graph_hash
from shine.media.artifacts import safe_asset_path
from shine.models import (
    AuditEvent,
    Job,
    JobAttempt,
    MediaArtifact,
    Project,
    ProjectRuleLock,
    QCReport,
    QualityGate,
    RulePack,
    Shot,
    StyleVersion,
)
from shine.quality.gates import GateOutcome, _final_gate
from shine.quality.technical import TechnicalExpectations, TechnicalQC
from shine.schemas import RenderRequest
from shine.services import add_audit, job_fingerprint, transition

ITEMS = (
    "identity",
    "costume",
    "scene_props",
    "camera_action",
    "style",
    "dialogue",
    "voice",
    "lip_sync",
    "continuity",
    "other",
)


class ReviewItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["PASS", "FAIL", "NOT_REVIEWED", "NOT_APPLICABLE"] = "NOT_REVIEWED"
    evidence: str = Field(default="", max_length=2000)


class OperatorDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot: str
    request_id: str = Field(min_length=16, max_length=64, pattern=r"^[a-zA-Z0-9-]+$")
    items: dict[str, ReviewItem]
    watched: bool = False
    listened: bool = False


def authenticate(settings, token: str) -> str:
    digest = hashlib.sha256(token.encode()).hexdigest()
    if (
        len(token) < 32
        or not settings.operator_id
        or not settings.operator_token_sha256
        or not hmac.compare_digest(digest, settings.operator_token_sha256)
    ):
        raise HTTPException(403, "Local operator credential required; Agent identities are not accepted")
    return settings.operator_id


def context(db, settings, job_id: str):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    attempt = db.scalar(
        select(JobAttempt).where(JobAttempt.job_id == job.id).order_by(JobAttempt.attempt_no.desc()).limit(1)
    )
    artifact = db.get(MediaArtifact, job.artifact_id) if job.artifact_id else None
    shot = db.get(Shot, job.shot_id)
    style = db.get(StyleVersion, job.style_version_id)
    project = db.get(Project, job.project_id)
    from shine.comfyops.preproduction import enforce

    current_preproduction_hash = enforce(
        db,
        job.project_id,
        offline=bool(settings.offline_simulation),
        data_dir=settings.data_dir,
        workflow_profile_id=job.workflow_profile_id if job.provider == "comfyui" else None,
        render_request=job.request,
    )
    if job.preproduction_manifest_hash != current_preproduction_hash:
        raise HTTPException(409, "PREPRODUCTION_OUTDATED")
    request = RenderRequest.model_validate(job.request)
    lock = db.get(ProjectRuleLock, job.project_id)
    pack = db.get(RulePack, request.rulepack_id) if request.rulepack_id else None
    if (
        job.status != "NEEDS_REVIEW"
        or job.stale
        or job.paused
        or job.lease_owner
        or job.lease_expires_at
        or not shot
        or shot.revision != job.shot_revision
        or not style
        or style.sha256 != request.style_hash
        or graph_hash(style.snapshot) != style.sha256
        or style.id != request.style_version_id
        or not project
        or project.frozen_style_version_id != style.id
        or request.shot_revision != job.shot_revision
        or request.shot_id != shot.id
    ):
        raise HTTPException(409, "Job/revision/style/lease is no longer eligible for review")
    if (
        not attempt
        or attempt.status != "COMPLETED"
        or not artifact
        or artifact.attempt_id != attempt.id
        or artifact.job_id != job.id
        or artifact.project_id != job.project_id
        or attempt.request_fingerprint != job_fingerprint(job.request)
        or (attempt.request_snapshot and attempt.request_snapshot != job.request)
        or artifact.is_mock
        or job.provider != "comfyui"
        or not job.provider_job_id
        or attempt.provider_job_id != job.provider_job_id
        or (artifact.provenance or {}).get("prompt_id") != job.provider_job_id
        or attempt.output_sha256 != artifact.sha256
    ):
        raise HTTPException(409, "Current Attempt/Artifact/prompt/request identity mismatch")
    if request.rulepack_id and (
        not pack
        or not pack.approved_at
        or pack.sha256 != request.rulepack_hash
        or graph_hash(pack.manifest) != pack.sha256
        or not lock
        or lock.rulepack_id != pack.id
        or lock.rulepack_hash != pack.sha256
    ):
        raise HTTPException(409, "RulePack lock changed")
    if not request.rulepack_id and lock:
        raise HTTPException(409, "Legacy request predates the current RulePack lock")
    try:
        path = safe_asset_path(settings.data_dir, artifact.storage_path)
        with path.open("rb") as media:
            digest = hashlib.file_digest(media, "sha256").hexdigest()
        if digest != artifact.sha256 or path.stat().st_size != artifact.byte_size:
            raise ValueError("hash mismatch")
    except (OSError, ValueError) as exc:
        raise HTTPException(409, "Media integrity failed; human handling required") from exc
    gates = db.scalars(
        select(QualityGate)
        .where(QualityGate.job_id == job.id)
        .order_by(QualityGate.created_at, literal_column("quality_gates.rowid"))
    ).all()
    technical = next(
        (
            g
            for g in reversed(gates)
            if g.gate_name == "technical" and g.attempt_id == attempt.id and g.artifact_id == artifact.id
        ),
        None,
    )
    if not technical or technical.status != "passed" or technical.is_mock:
        raise HTTPException(409, "Current real technical QC must pass before manual review")
    binding = {
        "job_id": job.id,
        "state_version": job.state_version,
        "preproduction_manifest_hash": job.preproduction_manifest_hash,
        "request": job.request,
        "request_revision": job.request_revision,
        "attempt_id": attempt.id,
        "attempt": {c.name: str(getattr(attempt, c.name)) for c in attempt.__table__.columns},
        "shot_revision": shot.revision,
        "shot_spec": shot.spec,
        "style_id": style.id,
        "style_hash": style.sha256,
        "rulepack_id": request.rulepack_id,
        "rulepack_hash": request.rulepack_hash,
        "artifact_id": artifact.id,
        "artifact": {c.name: str(getattr(artifact, c.name)) for c in artifact.__table__.columns},
        "artifact_sha256": digest,
        "storage_path": artifact.storage_path,
        "provenance": artifact.provenance,
        "prompt_id": job.provider_job_id,
        "quality_policy": project.quality_policy,
        "gates": [{c.name: str(getattr(g, c.name)) for c in g.__table__.columns} for g in gates],
    }
    return job, attempt, artifact, request, path, binding


def review_context(db, settings, job_id: str):
    *_, binding = context(db, settings, job_id)
    return {
        "binding": binding,
        "snapshot": graph_hash(binding),
        "items": ITEMS,
        "source": "manual_visual_and_listening",
        "gpu_submission": False,
    }


def abandon_unreconciled_job(db, settings, job_id, actor, evidence: str = ""):
    """Record a local user's decision to stop waiting for an uncertain render.

    The produced artifact stays as evidence, the attempt is never resubmitted,
    and the job ends FAILED. Only a provably stopped renderer may be abandoned.
    """
    from urllib.parse import urlsplit

    from shine.comfyops.installer import process_identity
    from shine.comfyops.models import ResourceLease
    from shine.comfyops.renderer_supervisor import windows_port_listeners
    from shine.comfyops.scheduler import ResourceScheduler
    from shine.models import AppSetting, ExternalJobReconciliation, utcnow

    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    if job.status != "NEEDS_REVIEW":
        raise HTTPException(409, "JOB_NOT_UNCERTAIN")
    if job.lease_owner or job.lease_expires_at:
        raise HTTPException(409, "H3_WORKER_STILL_OWNS_JOB")
    lease = db.scalar(select(ResourceLease).where(ResourceLease.owner == "h3:" + job.id))
    if lease is not None and lease.status == "ACTIVE":
        raise HTTPException(409, "H3_LEASE_STILL_ACTIVE")
    attempt = db.scalar(
        select(JobAttempt).where(JobAttempt.job_id == job.id).order_by(JobAttempt.attempt_no.desc()).limit(1)
    )
    journal = db.get(AppSetting, "handoff:" + attempt.id) if attempt else None
    value = dict(journal.value) if journal is not None and isinstance(journal.value, dict) else {}
    process = value.get("process") or {}
    pid, birth = process.get("pid"), process.get("birth")
    if type(pid) is int and isinstance(birth, str) and birth:
        try:
            if process_identity(pid) == birth:
                raise HTTPException(409, "OWNED_RENDERER_STILL_RUNNING")
        except (OSError, ValueError):
            pass
    port = urlsplit(value.get("instance_url") or "").port
    if port and windows_port_listeners(port):
        raise HTTPException(409, "OWNED_RENDERER_STILL_RUNNING")
    artifact_id = job.artifact_id
    if lease is not None and lease.status == "RECONCILE_REQUIRED":
        ResourceScheduler().release(db, lease.id, lease.owner, reconciled=True)
    if journal is not None and value.get("stage") == "RECONCILE_REQUIRED":
        journal.value = {**value, "stage": "RECONCILE_COMPLETE",
                         "last_confirmed_stage": "RECONCILE_REQUIRED",
                         "reason": "USER_ABANDONED_UNRECONCILED",
                         "reconciled_at": utcnow().isoformat()}
    transition(job, "FAILED")
    job.failure_code = "USER_ABANDONED_UNRECONCILED"
    job.lease_owner = None
    job.lease_expires_at = None
    db.add(ExternalJobReconciliation(
        job_id=job.id, provider_job_id=job.provider_job_id, action="user_abandon", result="abandoned",
        evidence={"artifact_kept": artifact_id, "resubmitted": False, "note": evidence[:200]},
    ))
    add_audit(db, actor, "JOB_ABANDONED_UNCERTAIN", "job", job.id,
              {"artifact_kept": artifact_id, "resubmitted": False, "note": evidence[:200]},
              project_id=job.project_id)
    return {"status": job.status, "artifact_kept": artifact_id, "resubmitted": False}


def submit_review(db, settings, job_id, actor, decision: OperatorDecision, *, client=None):
    # External reads and decode happen before the short database write lock.
    job, attempt, artifact, request, path, binding = context(db, settings, job_id)
    if graph_hash(binding) != decision.snapshot:
        raise HTTPException(409, "Review snapshot expired; reload and review again")
    if set(decision.items) != set(ITEMS):
        raise HTTPException(422, "Every named review item is required")
    for item in decision.items.values():
        if item.status != "NOT_REVIEWED" and not item.evidence.strip():
            raise HTTPException(422, "Each reviewed or inapplicable item needs specific evidence")
    history = (client or ComfyUIClient(settings.comfyui_base_url, timeout_s=15)).history(job.provider_job_id)
    record = history.get(job.provider_job_id, {})
    prompt = record.get("prompt") or []
    if (
        (record.get("status") or {}).get("status_str") != "success"
        or not (record.get("status") or {}).get("completed")
        or len(prompt) < 3
        or prompt[1] != job.provider_job_id
        or graph_hash(prompt[2]) != attempt.prompt_graph_sha256
    ):
        raise HTTPException(409, "ComfyUI history does not match completed submission")
    technical = TechnicalQC(ffprobe_path=settings.ffprobe_path, ffmpeg_path=settings.ffmpeg_path).check(
        path,
        expectations=TechnicalExpectations(
            duration_s=request.expected_duration_s, expect_audio=bool(request.shot_spec.dialogue)
        ),
    )
    if (
        technical.result != "passed"
        or technical.artifact_sha256 != artifact.sha256
        or technical.byte_size != artifact.byte_size
    ):
        raise HTTPException(409, "Fresh technical QC failed or unavailable; approval blocked")
    db.rollback()
    db.execute(text("BEGIN IMMEDIATE"))
    db.expire_all()
    job, attempt, artifact, request, _, current = context(db, settings, job_id)
    prior = db.scalars(
        select(AuditEvent).where(AuditEvent.entity_id == job_id, AuditEvent.event_type == "OPERATOR_REVIEW_RECORDED")
    ).all()
    if any(event.payload.get("request_id") == decision.request_id for event in prior):
        raise HTTPException(409, "This operator request was already recorded")
    if graph_hash(current) != decision.snapshot:
        raise HTTPException(409, "Concurrent change invalidated this review")
    claimed = db.execute(
        update(Job)
        .where(
            Job.id == job.id,
            Job.state_version == job.state_version,
            Job.status == "NEEDS_REVIEW",
            Job.lease_owner.is_(None),
        )
        .values(state_version=job.state_version + 1),
        execution_options={"synchronize_session": False},
    )
    if claimed.rowcount != 1:
        raise HTTPException(409, "Review already consumed")
    job.state_version += 1
    evidence = {
        "source": "manual_visual_and_listening",
        "operator_id": actor,
        "request_id": decision.request_id,
        "snapshot": decision.snapshot,
        "binding": current,
        **decision.model_dump(exclude={"snapshot", "request_id"}),
    }

    def outcome(name, names, *, listening=False):
        selected = [decision.items[n] for n in names]
        covered = decision.listened if listening else decision.watched
        status = "needs_review"
        if any(i.status == "FAIL" for i in selected):
            status = "failed"
        elif covered and all(i.status == "PASS" for i in selected):
            status = "passed"
        return GateOutcome(
            gate_name=name,
            status=status,
            required=True,
            is_mock=False,
            coverage="covered" if status != "needs_review" else "not_covered",
            evidence=evidence,
        )

    gates = [
        GateOutcome(
            gate_name="technical",
            status="passed",
            required=True,
            is_mock=False,
            coverage="covered",
            evidence=technical.model_dump(mode="json"),
        ),
        outcome("semantic", ["identity", "costume", "scene_props", "camera_action", "continuity", "other"]),
        outcome("style", ["style"]),
    ]
    if request.shot_spec.dialogue or any(
        decision.items[name].status == "FAIL" for name in ("dialogue", "voice", "lip_sync")
    ):
        audio = outcome("audio", ["dialogue", "voice", "lip_sync"], listening=True)
        if not decision.watched and audio.status == "passed":
            audio = replace(audio, status="needs_review", coverage="not_covered")
        gates.append(audio)
    else:
        gates.append(
            GateOutcome(
                gate_name="audio",
                status="not_applicable",
                required=False,
                is_mock=False,
                coverage="not_covered",
                evidence={"reason": "No scripted dialogue"},
            )
        )
    final = _final_gate(gates)
    gates.append(final)
    for gate in gates:
        db.add(QualityGate(job_id=job.id, attempt_id=attempt.id, artifact_id=artifact.id, **gate.as_dict()))
    db.add(
        QCReport(
            job_id=job.id,
            attempt_id=attempt.id,
            artifact_id=artifact.id,
            result=final.status,
            is_mock=False,
            reviewer="operator-manual",
            report={
                "manual": evidence,
                "technical": technical.model_dump(mode="json"),
                "gates": {g.gate_name: g.status for g in gates},
            },
        )
    )
    if final.status == "passed":
        transition(job, "RUNNING")
        transition(job, "QC_PENDING")
        transition(job, "PASSED")
        job.failure_code = None
        job.quality_verification = "manual_verified"
    else:
        job.failure_code = "MANUAL_REVIEW_INCOMPLETE" if final.status == "needs_review" else "MANUAL_REVIEW_FAILED"
    job.last_qc_result = final.status
    add_audit(db, actor, "OPERATOR_REVIEW_RECORDED", "job", job.id, evidence, project_id=job.project_id)
    db.commit()
    return {"status": job.status, "result": final.status, "source": "manual", "request_id": decision.request_id}
