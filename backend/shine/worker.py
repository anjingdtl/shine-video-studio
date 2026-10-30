from __future__ import annotations

import argparse
import logging
import os
import time
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from shine.comfy.client import ComfyError, ComfySubmissionUncertain, ComfyUIClient, ComfyUIError
from shine.comfy.media import ComfyPendingWorkflowMapping
from shine.comfy.provider import ComfyResult, ComfySubmission, ComfyUIRenderService
from shine.config import Settings
from shine.db import make_engine, make_session_factory
from shine.media.artifacts import store_artifact
from shine.media.frames import extract_frames
from shine.models import (
    Asset,
    ExternalJobReconciliation,
    Job,
    JobAttempt,
    JobDependency,
    MediaArtifact,
    Project,
    QCReport,
    QualityGate,
    RepairRecord,
    Shot,
    WorkerInstance,
    WorkflowProfile,
    utcnow,
)
from shine.providers import MockH3Provider, MockSubmission, ProviderFailure, SubmissionUncertain
from shine.quality.gates import (
    STATUS_FAILED,
    STATUS_PASSED,
    VERIFICATION_MOCK,
    VERIFICATION_PARTIAL,
    VERIFICATION_REAL,
    GateOutcome,
    QualityPolicy,
    evaluate_gates,
)
from shine.quality.repair import RepairEngine
from shine.quality.revisions import (
    RequestRevision,
    derive_seed,
    manifest_fingerprint,
    parameter_fingerprint,
    revision_for_parameters,
    revision_for_prompt,
    revision_for_references,
)
from shine.quality.semantic import (
    ReceivedReviewError,
    ReviewContext,
    Transcript,
    dialogue_findings,
)
from shine.quality.technical import TechnicalExpectations, TechnicalQC
from shine.schemas import (
    FusedQCResult,
    HardFailure,
    ReferenceManifestItem,
    ReferenceUse,
    RenderRequest,
    RepairAction,
    ShotSpec,
    StyleBible,
)
from shine.services import (
    add_audit,
    job_fingerprint,
    manifest_from_shot_bindings,
    mark_stale_descendants,
    transition,
    try_transition,
)

WORKER_VERSION = "shine-worker/3.0"
logger = logging.getLogger("shine.worker")

ACTIVE_STATUSES = ("SUBMITTING", "RUNNING", "QC_PENDING")

# ComfyUI removes a prompt from its queue before the matching history record is
# readable. A worker that samples exactly in that window sees neither the
# queue entry nor the history entry and would otherwise discard a render that
# actually succeeded. The worker therefore treats a missing external record as
# provisional for this long before it declares the submission lost.
EXTERNAL_MISSING_GRACE_SECONDS = 30

# Failure codes that mean "the external outcome is not known". Such a job may
# still be reconcilable against ComfyUI history and must never be resubmitted.
RECONCILABLE_FAILURE_CODES = ("COMFY_JOB_NOT_FOUND", "QC_RECHECK_REQUESTED", "COMFY_WORKER_EXCEPTION_WITH_EXTERNAL")


def _register_worker(db: Session, worker_id: str) -> None:
    now = utcnow()
    instance = db.get(WorkerInstance, worker_id)
    if instance is None:
        db.add(
            WorkerInstance(
                worker_id=worker_id,
                version=WORKER_VERSION,
                pid=os.getpid(),
                status="online",
                started_at=now,
                last_heartbeat_at=now,
            )
        )
        db.flush()
    else:
        instance.version = WORKER_VERSION
        instance.pid = os.getpid()
        instance.status = "online"
        instance.last_heartbeat_at = now


def heartbeat(
    session_factory,
    worker_id: str,
    *,
    status: str = "online",
    current_job_id: str | None = None,
    phase: str | None = None,
    error: str | None = None,
    processed_delta: int = 0,
    expected_state_version: int | None = None,
    attempt_id: str | None = None,
    lease_seconds: int = 30,
    allow_ack_transition: bool = False,
) -> bool:
    try:
        with session_factory.begin() as db:
            from sqlalchemy import text

            db.execute(text("BEGIN IMMEDIATE"))
            _register_worker(db, worker_id)
            instance = db.get(WorkerInstance, worker_id)
            instance.status = status
            instance.current_job_id = current_job_id
            instance.current_phase = phase
            resource_ok = True
            if current_job_id and expected_state_version is not None and attempt_id:
                from shine.comfyops.models import ResourceLease

                resource = db.scalar(select(ResourceLease).where(ResourceLease.owner == "h3:" + current_job_id))
                job, attempt, now = db.get(Job, current_job_id), db.get(JobAttempt, attempt_id), utcnow()
                version_matches = bool(job and (job.state_version == expected_state_version or (
                    allow_ack_transition and job.state_version == expected_state_version + 1
                    and job.status == "RUNNING" and attempt and attempt.provider_job_id
                    and job.provider_job_id == attempt.provider_job_id)))
                owned = (job and attempt and attempt.job_id == job.id and job.lease_owner == worker_id
                         and attempt.lease_owner == worker_id and version_matches
                         and job.status in {"SUBMITTING", "RUNNING", "QC_PENDING"})
                if not owned:
                    resource_ok = False
                elif (not resource or resource.status != "ACTIVE" or resource.expires_at <= now
                      or not job.lease_expires_at or job.lease_expires_at <= now
                      or not attempt.lease_expires_at or attempt.lease_expires_at <= now
                      or job.stale or not _shot_is_current(db, job)):
                    if not resource:
                        from shine.comfyops.scheduler import ResourceScheduler

                        resource = ResourceScheduler().enqueue(db, "h3:" + current_job_id, 16384, True, 100)
                    resource.status = "RECONCILE_REQUIRED"
                    if try_transition(job, "NEEDS_REVIEW"):
                        job.failure_code = "COMFY_HEARTBEAT_RECONCILIATION_REQUIRED"
                        job.lease_owner, job.lease_expires_at = None, None
                        add_audit(db, "comfy-worker", "COMFY_HEARTBEAT_RECONCILIATION_REQUIRED", "job", job.id,
                                  {"attempt_id": attempt_id, "resubmitted": False}, project_id=job.project_id)
                    resource_ok = False
                else:
                    resource.heartbeat_at, resource.expires_at = now, now + timedelta(seconds=60)
                    job.heartbeat_at = attempt.heartbeat_at = now
                    job.lease_expires_at = attempt.lease_expires_at = now + timedelta(seconds=lease_seconds)
            instance.last_heartbeat_at = utcnow()
            instance.processed_count += processed_delta
            if error:
                instance.last_error = error[:2000]
        return resource_ok
    except Exception as exc:  # pragma: no cover - the heartbeat must not kill the worker
        logger.error("worker_heartbeat_failed error_type=%s", type(exc).__name__)
        return False


def _promote_dependencies(db: Session) -> None:
    planned = db.scalars(select(Job).where(Job.status.in_(["PLANNED", "WAITING_ASSET"]))).all()
    for job in planned:
        dependencies = db.scalars(select(JobDependency).where(JobDependency.child_job_id == job.id)).all()
        parents = [db.get(Job, dep.parent_job_id) for dep in dependencies]
        if any(parent and (parent.stale or parent.status in {"FAILED", "NEEDS_REVIEW"}) for parent in parents):
            job.failure_code = "DEPENDENCY_BLOCKED"
            if try_transition(job, "NEEDS_REVIEW"):
                add_audit(
                    db,
                    "orchestrator",
                    "JOB_BLOCKED_BY_DEPENDENCY",
                    "job",
                    job.id,
                    project_id=job.project_id,
                )
        elif all(parent and parent.status == "PASSED" for parent in parents):
            if try_transition(job, "QUEUED"):
                add_audit(
                    db,
                    "orchestrator",
                    "JOB_DEPENDENCIES_READY",
                    "job",
                    job.id,
                    project_id=job.project_id,
                )


def _latest_attempt(db: Session, job_id: str) -> JobAttempt | None:
    return db.scalar(select(JobAttempt).where(JobAttempt.job_id == job_id).order_by(JobAttempt.attempt_no.desc()))


def _shot_is_current(db: Session, job: Job) -> bool:
    shot = db.get(Shot, job.shot_id)
    return bool(shot and not job.stale and shot.revision == job.shot_revision)


def _discard_stale_result(
    db: Session,
    job: Job,
    attempt: JobAttempt | None,
    *,
    reason: str,
    worker_id: str,
) -> None:
    job.lease_owner = None
    job.lease_expires_at = None
    job.stale = True
    job.failure_code = reason
    if job.status not in {"NEEDS_REVIEW", "FAILED"} and try_transition(job, "NEEDS_REVIEW"):
        pass
    if attempt:
        attempt.status = "STALE"
        attempt.failure_code = reason
        attempt.reconciliation_state = "discarded"
    add_audit(
        db,
        "orchestrator",
        "STALE_RESULT_DISCARDED",
        "job",
        job.id,
        {"reason": reason, "worker_id": worker_id, "shot_revision": job.shot_revision},
        project_id=job.project_id,
    )


def _guard_claimed_job(
    db: Session,
    *,
    job_id: str,
    expected_state_version: int,
    worker_id: str,
    attempt_id: str,
    allowed_status: tuple[str, ...] = ("SUBMITTING",),
    settings: Settings | None = None,
) -> tuple[Job, JobAttempt] | None:
    job = db.get(Job, job_id)
    attempt = db.get(JobAttempt, attempt_id)
    if not job or not attempt:
        return None
    if job.state_version != expected_state_version or job.lease_owner != worker_id:
        return None
    if job.status not in allowed_status:
        return None
    if not _shot_is_current(db, job):
        _discard_stale_result(db, job, attempt, reason="SHOT_REVISION_SUPERSEDED", worker_id=worker_id)
        return None
    from shine.comfyops.preproduction import enforce_job

    if job.provider == "comfyui" and job.status == "SUBMITTING":
        from shine.comfyops.models import ResourceLease

        resource = db.scalar(select(ResourceLease).where(ResourceLease.owner == "h3:" + job.id))
        if not resource or resource.status != "ACTIVE" or resource.expires_at <= utcnow():
            job.failure_code = "GPU_RESOURCE_LEASE_LOST"
            job.lease_owner = None
            job.lease_expires_at = None
            attempt.status = "NEEDS_REVIEW"
            transition(job, "NEEDS_REVIEW")
            return None
    try:
        enforce_job(db, job, settings)
    except HTTPException as exc:
        job.failure_code = str(exc.detail)
        job.lease_owner = None
        job.lease_expires_at = None
        attempt.status = "NEEDS_REVIEW"
        transition(job, "NEEDS_REVIEW")
        return None
    return job, attempt


def _record_mock_qc(db: Session, job: Job, attempt: JobAttempt, report: dict[str, Any]) -> None:
    db.add(
        QCReport(
            job_id=job.id,
            attempt_id=attempt.id,
            result="passed",
            is_mock=True,
            reviewer="mock",
            report=report,
        )
    )


def _project_policy(db: Session, project_id: str) -> QualityPolicy:
    project = db.get(Project, project_id)
    return QualityPolicy.from_dict(project.quality_policy if project else None)


def _record_gates(
    db: Session,
    job: Job,
    attempt: JobAttempt,
    artifact: MediaArtifact | None,
    gates: list[GateOutcome],
    fused: FusedQCResult,
) -> None:
    payload = fused.model_dump(mode="json")
    existing = db.scalars(select(QCReport).where(QCReport.job_id == job.id, QCReport.attempt_id == attempt.id)).all()
    if any(row.report == payload and row.artifact_id == (artifact.id if artifact else None) for row in existing):
        # Idempotent crash recovery; changed reviews are new immutable reports.
        return
    for gate in gates:
        db.add(
            QualityGate(
                job_id=job.id,
                attempt_id=attempt.id,
                artifact_id=artifact.id if artifact else None,
                gate_name=gate.gate_name,
                status=gate.status,
                required=gate.required,
                is_mock=gate.is_mock,
                model=gate.model,
                confidence=gate.confidence,
                error_code=gate.error_code,
                coverage=gate.coverage,
                evidence=gate.evidence,
            )
        )
    db.add(
        QCReport(
            job_id=job.id,
            attempt_id=attempt.id,
            artifact_id=artifact.id if artifact else None,
            result=fused.result,
            is_mock=fused.is_mock,
            reviewer="quality-gates",
            report=payload,
        )
    )


def _raise_if_managed_review(reviewer):
    """An uncertain managed stage must reach its handoff context unchanged."""
    from shine.comfyops.handoff_preparation import HandoffReviewProvider

    if isinstance(reviewer, HandoffReviewProvider):
        raise


def _semantic_review(
    *,
    reviewer,
    job_id: str,
    spec: ShotSpec,
    style: StyleBible,
    artifact_path: Path,
    artifact_sha256: str,
    settings: Settings,
    remote_consent: bool,
    project_id: str,
    transcript: Transcript | None,
    local_reference_frames=(),
    reference_manifest=(),
    review_revision=None,
    independent_input=None,
) -> tuple[list[Any], str | None]:
    """Run the semantic reviewer, classifying every failure mode separately."""
    if reviewer is None:
        return [], None
    if getattr(reviewer, "accepts_local_references", False) and reference_manifest:
        from shine.media.frames import reference_thumbnails

        try:
            local_reference_frames = reference_thumbnails(
                settings.data_dir,
                reference_manifest,
                settings.data_dir / "evidence" / project_id / job_id / "references",
                ffmpeg_path=settings.ffmpeg_path,
            )
        except Exception as exc:
            return [], f"SEMANTIC_REFERENCE_EVIDENCE_FAILED: {type(exc).__name__}"
    frames: list[Path] = []
    if getattr(reviewer, "is_mock", False) is False:
        frames = extract_frames(
            artifact_path,
            settings.data_dir / "evidence" / project_id / job_id,
            ffmpeg_path=settings.ffmpeg_path,
            duration_s=spec.duration_target_s,
        )
    identity_labels = {
        f"{entry.label} / {entry.entity_id or entry.role or 'reference'}"
        for entry in reference_manifest
        if entry.content_type.startswith("image/") and entry.use_as in {ReferenceUse.SUBJECT, ReferenceUse.PICTURE}
        and (entry.role or "").casefold() not in {"scene", "location", "prop", "costume", "voice"}
        and (entry.entity_id in spec.characters or entry.role in spec.characters)
    }
    context = ReviewContext(
        job_id=job_id,
        shot_spec=spec,
        style=style,
        artifact_path=artifact_path,
        artifact_sha256=artifact_sha256,
        frame_paths=tuple(frames),
        transcript=transcript,
        remote_consent=remote_consent,
        local_reference_frames=local_reference_frames,
        review_revision=review_revision,
        independent_input=independent_input,
        identity_reference_labels=tuple(label for label, _ in local_reference_frames if label in identity_labels),
    )
    try:
        result = reviewer.review(context)
    except PermissionError as exc:
        _raise_if_managed_review(reviewer)
        return [], f"SEMANTIC_REVIEW_FAILED: {exc}"
    except (TimeoutError, httpx.TimeoutException):
        _raise_if_managed_review(reviewer)
        return [], "SEMANTIC_REVIEW_TIMEOUT: response unavailable"
    except ReceivedReviewError as exc:
        if str(exc) == "VLM_RESPONSE_INCOMPLETE":
            finish = exc.finish_reason if exc.finish_reason in {"length", "content_filter"} else "UNSPECIFIED"
            return [], f"SEMANTIC_REVIEW_INCOMPLETE: {finish}"
        reason = (exc.validation or {}).get("code")
        if reason == "JSON_DECODE_FAILED":
            return [], "SEMANTIC_REVIEW_INVALID_JSON: JSON_DECODE_FAILED"
        allowed = {"STYLE_INPUT_COVERAGE_MISSING", "STYLE_INPUT_DECISION_CONTRADICTION",
                   "SCHEMA_VALIDATION_FAILED", "HTTP_REQUEST_REJECTED", "VLM_DECISION_MISSING",
                   "VLM_VERDICT_INVALID", "VLM_DIALOGUE_EVIDENCE_MISSING", "VLM_DIALOGUE_HARD_UNSUPPORTED",
                   "VLM_IDENTITY_REFERENCE_MISSING", "VLM_FINDING_DETAIL_TOO_LONG", "VLM_SUMMARY_TOO_LONG",
                   "RESPONSE_SHAPE_INVALID"}
        # The review finished and its answer breaks the contract.  That is a
        # failed review, not an uncertain external stage: the isolation receipt
        # proves the call completed, the artifact stays valid and the shot
        # needs a human.  Escalating it would hold the GPU lease for a
        # probabilistic answer and stop the whole run.
        return [], f"SEMANTIC_REVIEW_CONTRACT_INVALID: {reason if reason in allowed else 'RESPONSE_REJECTED'}"
    except ValueError as exc:
        return [], f"SEMANTIC_REVIEW_INVALID_JSON: {exc}"
    except Exception as exc:  # pragma: no cover - defensive
        _raise_if_managed_review(reviewer)
        return [], f"SEMANTIC_REVIEW_FAILED: {type(exc).__name__}"
    if result is None:
        return [], "SEMANTIC_REVIEW_INVALID_JSON: the reviewer returned no result"
    if getattr(reviewer, "is_mock", False) and not result.is_mock:
        result = result.model_copy(update={"is_mock": True})
    return [result], None


def _transcript_review(
    *,
    asr,
    artifact_path: Path,
    spec: ShotSpec,
    remote_consent: bool = False,
) -> tuple[Transcript | None, list[Any] | None, str | None]:
    if asr is None:
        return None, None, None
    if getattr(asr, "requires_remote_consent", False) and not remote_consent:
        return None, None, "ASR_REMOTE_CONSENT_REQUIRED"
    try:
        transcript = asr.transcribe(artifact_path)
    except TimeoutError as exc:
        _raise_if_managed_review(asr)
        return None, None, f"ASR_FAILED: {exc}"
    except Exception as exc:  # pragma: no cover - defensive
        _raise_if_managed_review(asr)
        return None, None, f"ASR_FAILED: {type(exc).__name__}"
    if getattr(asr, "is_mock", False) and not transcript.is_mock:
        transcript = replace(transcript, is_mock=True)
    findings = dialogue_findings(spec, transcript)
    return transcript, findings, None


def _repair_root_cause_repeated(db: Session, job: Job, category: str) -> bool:
    records = db.scalars(select(RepairRecord).where(RepairRecord.job_id == job.id)).all()
    return sum(1 for record in records if record.action and category and category in record.root_cause) > 0


def _build_repair_revision(
    db: Session,
    job: Job,
    attempt: JobAttempt,
    *,
    action: RepairAction,
    root_cause: str,
    project_language: str,
) -> RequestRevision | None:
    """Produce a materially different request for the repair, or ``None``."""
    request = dict(job.request or {})
    if not request:
        return None
    try:
        spec = ShotSpec.model_validate(request.get("shot_spec", {}))
        style = StyleBible.model_validate(request.get("style_snapshot", {}))
    except Exception:
        return None
    manifest = [ReferenceManifestItem.model_validate(item) for item in request.get("reference_manifest", [])]
    if action == RepairAction.ADJUST_PROMPT_STRATEGY:
        return revision_for_prompt(
            request=request,
            spec=spec,
            style=style,
            manifest=manifest,
            root_cause=root_cause,
            attempt_no=attempt.attempt_no,
            project_language=project_language,
        )
    if action == RepairAction.REBUILD_REFERENCE_BINDINGS:
        shot = db.get(Shot, job.shot_id)
        if shot is None:
            return None
        rebuilt = manifest_from_shot_bindings(db, shot)
        if not rebuilt:
            return None
        return revision_for_references(
            request=request,
            manifest=rebuilt,
            root_cause=root_cause,
            attempt_no=attempt.attempt_no,
        )
    if action in {RepairAction.ADJUST_WORKFLOW_PARAMETERS, RepairAction.RERENDER_AFTER_TRANSIENT}:
        seed = derive_seed(request, attempt.attempt_no)
        return revision_for_parameters(
            request=request,
            root_cause=root_cause,
            attempt_no=attempt.attempt_no,
            new_seed=seed,
            reason=(
                "Re-validated the workflow profile and changed the generation parameters"
                if action == RepairAction.ADJUST_WORKFLOW_PARAMETERS
                else "Reconciled the previous submission and changed the generation parameters"
            ),
        )
    return None


def _auto_repair(
    db: Session,
    job: Job,
    attempt: JobAttempt,
    *,
    report: FusedQCResult,
    failure_code: str,
    project_auto_enabled: bool,
    worker_id: str,
    project_language: str = "Chinese",
) -> bool:
    """Turn a QC failure into a *different* request, or escalate to a human."""
    if not RepairEngine.guard_passed(job):
        return False
    repeated = _repair_root_cause_repeated(db, job, report.hard_failures[0].code if report.hard_failures else "")
    decision = RepairEngine().plan(
        report,
        retries_left=max(0, job.max_semantic_retries - job.semantic_retries),
        auto_repair_enabled=project_auto_enabled,
        repeated_root_cause=repeated,
    )
    job.last_repair_plan = decision.plan.model_dump(mode="json")
    revision: RequestRevision | None = None
    if decision.auto_apply:
        revision = _build_repair_revision(
            db,
            job,
            attempt,
            action=decision.plan.action,
            root_cause=decision.plan.root_cause,
            project_language=project_language,
        )
        if revision is None:
            decision = RepairEngine().plan(
                report,
                retries_left=max(0, job.max_semantic_retries - job.semantic_retries),
                auto_repair_enabled=False,
                repeated_root_cause=True,
            )
            job.last_repair_plan = decision.plan.model_dump(mode="json")
    if revision is None or not decision.auto_apply:
        job.failure_code = failure_code
        db.add(
            RepairRecord(
                job_id=job.id,
                attempt_id=attempt.id,
                action=decision.plan.action.value,
                root_cause=decision.plan.root_cause,
                changes=list(decision.plan.changes),
                outcome="escalated",
            )
        )
        add_audit(
            db,
            "orchestrator",
            "REPAIR_ESCALATED",
            "job",
            job.id,
            {
                "action": decision.plan.action.value,
                "category": decision.error_category,
                "reason": "no safe automatic change was possible",
            },
            project_id=job.project_id,
        )
        return False

    job.semantic_retries += 1
    job.failure_code = None
    job.mock_scenario = "success" if job.provider == "mock" else job.mock_scenario
    new_request = dict(revision.request)
    new_request["idempotency_key"] = uuid4().hex
    job.request = new_request
    job.request_revision = (job.request_revision or 1) + 1
    job.pending_repair_context = {
        "attempt_kind": "repair",
        "parent_attempt_id": attempt.id,
        "reason": revision.reason,
        "changes": list(revision.changes),
        "action": revision.action,
        "prompt_sha256": revision.prompt_sha256,
        "parameter_sha256": revision.parameter_sha256,
        "manifest_sha256": revision.manifest_sha256,
    }
    if job.status == "REPAIR_PENDING":
        transition(job, "READY")
        transition(job, "QUEUED")
    db.add(
        RepairRecord(
            job_id=job.id,
            attempt_id=attempt.id,
            action=decision.plan.action.value,
            root_cause=decision.plan.root_cause,
            changes=list(revision.changes),
            outcome="auto_queued",
        )
    )
    add_audit(
        db,
        "orchestrator",
        "AUTO_REPAIR_QUEUED",
        "job",
        job.id,
        {
            "action": decision.plan.action.value,
            "retry": job.semantic_retries,
            "changes": list(revision.changes),
            "prompt_sha256": revision.prompt_sha256,
            "parameter_sha256": revision.parameter_sha256,
            "manifest_sha256": revision.manifest_sha256,
            "request_revision": job.request_revision,
        },
        project_id=job.project_id,
    )
    mark_stale_descendants(db, [job.id], project_id=job.project_id)
    logger.info(
        "auto_repair_queued job_id=%s retry=%s action=%s",
        job.id,
        job.semantic_retries,
        decision.plan.action.value,
    )
    return True


def _mock_report(submission: MockSubmission) -> dict[str, Any]:
    return {
        "simulated": True,
        "video_created": False,
        "artifact_kind": "none",
        "provider_job_id": submission.provider_job_id,
        "duplicate_acknowledgement": submission.duplicate,
        "model_checkpoint_id": "MiniMax-H3-Base",
        "technical_qc": "not_applicable",
        "semantic_qc": "not_applicable",
        "verification": VERIFICATION_MOCK,
    }


def run_one(
    session_factory,
    provider=None,
    worker_id=None,
    *,
    settings=None,
    comfy_service=None,
    comfy_client=None,
    semantic_reviewer=None,
    asr_reviewer=None,
):
    from shine.comfyops.models import ResourceLease
    from shine.comfyops.scheduler import ResourceScheduler

    worker_id = worker_id or f"worker-{uuid4().hex[:12]}"
    heartbeat(session_factory, worker_id, phase="claiming")
    scheduler = ResourceScheduler()
    resource_id, owner = None, None
    managed_first = None
    with session_factory.begin() as resource_db:
        _promote_dependencies(resource_db)
        resource_db.flush()
        candidate = resource_db.scalar(
            select(Job)
            .where(Job.status == "QUEUED", Job.paused.is_(False), Job.stale.is_(False))
            .order_by(Job.created_at, Job.id)
            .limit(1)
        )
        if not candidate:
            return None
        selected_job_id = candidate.id
        if candidate.provider == "comfyui":
            owner = "h3:" + candidate.id
            lease = scheduler.enqueue(resource_db, owner, 16384, True, 100)
            if lease.status == "RELEASED":
                lease.status = "WAITING"  # A bounded repair attempt may reserve the same logical owner again.
            resource_db.commit()
            resource_id = lease.id
            job_id = candidate.id
    if resource_id:
        with session_factory() as resource_db:
            if not scheduler.admit(resource_db, resource_id):
                return None
        effective_settings = settings or Settings.from_env()
        managed_recipe = None
        managed_error = None
        try:
            managed_recipe = _managed_recipe(effective_settings)
        except HTTPException as exc:
            managed_error = str(exc.detail)
        if managed_error is None and managed_recipe is not None and not effective_settings.offline_simulation:
            # A managed renderer is started behind its own durable intent, so the
            # job no longer needs the conservative unowned-renderer block.
            try:
                managed_first = _FirstManagedContinuation(
                    session_factory, effective_settings, job_id=job_id, lease_id=resource_id,
                    recipe=managed_recipe["renderer"],
                    review_configs=_managed_review_specs(managed_recipe, semantic_reviewer, asr_reviewer),
                    history_dir=Path(effective_settings.data_dir).resolve() / "comfyops" / "handoff-history",
                )
            except HTTPException as exc:
                managed_error = str(exc.detail)
        if managed_error is not None:
            with session_factory.begin() as resource_db:
                resource_db.get(ResourceLease, resource_id).status = "RECONCILE_REQUIRED"
                blocked = resource_db.get(Job, job_id)
                if blocked and blocked.status == "QUEUED":
                    try_transition(blocked, "NEEDS_REVIEW")
                    blocked.failure_code = managed_error
                    add_audit(resource_db, "orchestrator", "COMFY_MANAGED_STARTUP_BLOCKED", "job", job_id,
                              {"submitted": False, "resource_id": resource_id, "reason": managed_error},
                              project_id=blocked.project_id)
            return job_id
        if managed_first is None:
            if not _handoff_preflight_allowed(effective_settings, (semantic_reviewer, asr_reviewer)):
                with session_factory.begin() as resource_db:
                    resource_db.get(ResourceLease, resource_id).status = "RECONCILE_REQUIRED"
                    blocked = resource_db.get(Job, job_id)
                    if blocked and blocked.status == "QUEUED":
                        try_transition(blocked, "NEEDS_REVIEW")
                        blocked.failure_code = "COMFY_HANDOFF_OWNERSHIP_UNVERIFIED"
                        add_audit(resource_db, "orchestrator", "COMFY_HANDOFF_PREFLIGHT_BLOCKED", "job", job_id,
                                  {"submitted": False, "resource_id": resource_id}, project_id=blocked.project_id)
                return job_id
            release_client = comfy_client or (comfy_service.client if comfy_service else None)
            if release_client is None and settings:
                release_client = _renderer_client(settings, max_retries=0)
            try:
                if release_client is None or not hasattr(release_client, "free_models"):
                    raise ValueError("MODEL_RELEASE_REQUIRES_LOCAL_RECONCILIATION")
                release_client.free_models()
            except Exception:
                with session_factory.begin() as resource_db:
                    resource_db.get(ResourceLease, resource_id).status = "RECONCILE_REQUIRED"
                    blocked = resource_db.get(Job, job_id)
                    if blocked and blocked.status == "QUEUED":
                        try_transition(blocked, "NEEDS_REVIEW")
                        blocked.failure_code = "MODEL_RELEASE_RECONCILIATION_REQUIRED"
                        add_audit(
                            resource_db,
                            "orchestrator",
                            "H3_PREFLIGHT_RELEASE_UNCONFIRMED",
                            "job",
                            job_id,
                            {"submitted": False, "resource_id": resource_id},
                            project_id=blocked.project_id,
                        )
                return job_id
    from shine.comfyops.scheduler import CURRENT_RESOURCE

    resource_token = CURRENT_RESOURCE.set(resource_id)
    try:
        return _run_one(
            session_factory,
            provider,
            worker_id,
            settings=settings,
            comfy_service=comfy_service,
            comfy_client=comfy_client,
            semantic_reviewer=semantic_reviewer,
            asr_reviewer=asr_reviewer,
            expected_job_id=selected_job_id,
            managed_continuation=managed_first,
        )
    finally:
        CURRENT_RESOURCE.reset(resource_token)
        if resource_id:
            with session_factory.begin() as resource_db:
                job = resource_db.get(Job, job_id)
                lease = resource_db.get(ResourceLease, resource_id)
                if job and job.lease_owner and job.lease_owner != worker_id:
                    # A newer claim owns the job; this worker must not alter its lease.
                    pass
                elif lease.status == "RECONCILE_REQUIRED":
                    # Never turn an uncertain handoff into a release by trying
                    # the renderer's unload endpoint a second time.
                    pass
                elif managed_first is not None and _managed_handoff_holds_lease(managed_first):
                    # The completed managed handoff keeps its own exclusive lease
                    # alive through the idle keeper; releasing it here would let
                    # another model enter between two serial stages.
                    pass
                elif not _isolated_mock_handoff(
                    settings or Settings.from_env(), (semantic_reviewer, asr_reviewer)
                ):
                    # Even without a reviewer, another model cannot be admitted
                    # solely on the renderer's self-reported unload.
                    lease.status = "RECONCILE_REQUIRED"
                elif job and (
                    job.status in {"SUBMITTING", "RUNNING"}
                    or job.failure_code in {"COMFY_SUBMISSION_OUTCOME_UNKNOWN", "COMFY_WORKER_EXCEPTION_WITH_EXTERNAL"}
                ):
                    lease.status = "RECONCILE_REQUIRED"
                else:
                    release_client = comfy_client or (comfy_service.client if comfy_service else None)
                    if release_client is None and settings:
                        release_client = _renderer_client(settings, max_retries=0)
                    try:
                        if release_client is None or not hasattr(release_client, "free_models"):
                            raise ValueError("MODEL_RELEASE_REQUIRES_LOCAL_RECONCILIATION")
                        release_client.free_models()
                    except Exception:
                        lease.status = "RECONCILE_REQUIRED"
                    else:
                        scheduler.release(resource_db, resource_id, owner, reconciled=True)


class _ManagedRenderHeartbeat:
    """Extend the claimed job, attempt and resource during slow owned operations."""

    def __init__(self, factory, worker_id, job_id, attempt_id, state_version, lease_seconds):
        from threading import Event

        self.factory, self.worker_id, self.job_id = factory, worker_id, job_id
        self.attempt_id, self.state_version, self.lease_seconds = attempt_id, state_version, lease_seconds
        self.stopped, self.error, self.thread = Event(), False, None

    def _pulse(self):
        return heartbeat(self.factory, self.worker_id, current_job_id=self.job_id,
                         expected_state_version=self.state_version, attempt_id=self.attempt_id,
                         lease_seconds=self.lease_seconds, allow_ack_transition=True)

    def _run(self):
        interval = min(10.0, max(0.1, self.lease_seconds / 3))
        while not self.stopped.wait(interval):
            if not self._pulse():
                self.error = True
                self.stopped.set()
                return

    def __enter__(self):
        from threading import Thread

        if not self._pulse():
            raise HTTPException(409, "COMFY_HEARTBEAT_RECONCILIATION_REQUIRED")
        self.thread = Thread(target=self._run, name="shine-managed-render-heartbeat", daemon=True)
        self.thread.start()
        return self

    def check(self):
        if self.error:
            raise HTTPException(409, "COMFY_HEARTBEAT_RECONCILIATION_REQUIRED")

    def stop(self):
        self.stopped.set()
        if self.thread:
            self.thread.join(timeout=12)
            if self.thread.is_alive():
                raise HTTPException(409, "COMFY_HEARTBEAT_EXIT_UNCONFIRMED")
        self.check()

    def __exit__(self, exc_type, exc, traceback):
        self.stop()


class _ManagedAttemptContext:
    """What a managed attempt context must provide to the render path.

    Two kinds exist: the first attempt of a fresh managed startup, and the
    continuation of a previous handoff. Both own the renderer, prepare the
    references and open the renderer for one claimed attempt.
    """

    job_id: str
    next: Any

    @property
    def instance_id(self) -> str:
        """The managed ComfyUI instance this context owns."""
        raise NotImplementedError

    @property
    def supervisor(self):
        """The supervisor bound to that instance."""
        raise NotImplementedError

    def before_prepare(self, service, attempt_id, worker_id, state_version):
        raise NotImplementedError

    def activate(self, attempt_id, worker_id, state_version):
        raise NotImplementedError


class _ManagedContinuation(_ManagedAttemptContext):
    """In-process authority for one previously reserved managed continuation."""

    @property
    def instance_id(self) -> str:
        return self.previous.instance_id

    @property
    def supervisor(self):
        return self.previous.replacement

    def __init__(self, factory, settings, previous, boundary, job_id, lease_id):
        from shine.comfyops.handoff_preparation import ManagedReviewCoordinator

        self.factory, self.settings, self.previous, self.boundary = factory, settings, previous, boundary
        self.job_id, self.lease_id = job_id, lease_id
        self.next = ManagedReviewCoordinator(
            previous.replacement, previous.instance_id, previous.history_dir, previous.gpu_uuid,
            min_ram_mb=previous.min_ram_mb, min_vram_mb=previous.min_vram_mb,
            review_configs=previous._review_configs,
        )
        self.next._previous_releases = dict(previous._previous_releases)
        self.next._cold_fences = dict(previous._cold_fences)

    def before_prepare(self, service, attempt_id, worker_id, state_version):
        from shine.comfy.provider import ComfyUIRenderService
        from shine.comfyops.models import ComfyInstance, ResourceLease
        from shine.comfyops.resource_epoch import read_epoch
        from shine.models import AppSetting

        client = getattr(service, "client", None)
        simulated = bool(self.settings.offline_simulation and getattr(service, "is_mock", False))
        if (not client or getattr(client, "base_url", "").rstrip("/") != self.previous.replacement._url
                or (not simulated and (type(service) is not ComfyUIRenderService
                    or type(client) is not ComfyUIClient or client._transport is not None
                    or client.trust_env is not False))):
            raise HTTPException(409, "HANDOFF_RENDER_CLIENT_UNVERIFIED")
        with self.factory.begin() as db:
            guarded = _guard_claimed_job(db, job_id=self.job_id, expected_state_version=state_version,
                                         worker_id=worker_id, attempt_id=attempt_id, settings=self.settings)
            row = db.get(AppSetting, "handoff-reservation:" + self.job_id)
            lease, instance = db.get(ResourceLease, self.lease_id), db.get(ComfyInstance, self.previous.instance_id)
            if (not guarded or not guarded[0].lease_expires_at or guarded[0].lease_expires_at <= utcnow()
                    or guarded[1].lease_owner != worker_id or guarded[1].lease_expires_at <= utcnow()
                    or not row or row.value.get("stage") != "RESERVED"
                    or row.value.get("lease_id") != self.lease_id or not lease or lease.status != "ACTIVE"
                    or not instance or read_epoch(db) != row.value.get("resource_epoch")):
                raise HTTPException(409, "HANDOFF_RESERVATION_INVALID")
            payload, instance_hash = dict(instance.payload), instance.sha256
        self.boundary.verify(self.factory, self.settings, self.previous.instance_id, attempt_id, self.lease_id)
        self.previous.replacement.verify_drained_binding(payload.get("url", ""), payload.get("process"))
        with self.factory() as db:
            instance = db.get(ComfyInstance, self.previous.instance_id)
            row = db.get(AppSetting, "handoff-reservation:" + self.job_id)
            if (not instance or instance.sha256 != instance_hash or instance.payload != payload or not row
                    or read_epoch(db) != row.value.get("resource_epoch")):
                raise HTTPException(409, "HANDOFF_RESERVATION_INVALID")

    def activate(self, attempt_id, worker_id, state_version):
        from shine.comfyops.handoff_activation import activate_renderer_for_attempt

        return activate_renderer_for_attempt(
            self.factory, self.settings, self.previous.replacement, self.job_id, attempt_id, self.lease_id,
            self.previous.instance_id, worker_id, state_version, physical_boundary=self.boundary,
        )


class _FirstManagedContinuation(_ManagedAttemptContext):
    """In-process authority for the first managed attempt of a fresh startup.

    Unlike the continuation of a previous handoff, this one owns the very first
    renderer: it starts the closed renderer behind a durable intent, proves the
    ownership and the physical baseline, and opens it only for a claimed attempt.
    """

    def __init__(self, factory, settings, *, job_id, lease_id, recipe, review_configs, history_dir):
        self.factory, self.settings = factory, settings
        self.job_id, self.lease_id = job_id, lease_id
        self.recipe = recipe
        self.review_configs = review_configs
        self.history_dir = Path(history_dir)
        self.renderer = None
        self.review_services = {}
        self.coordinator = None
        self.next = None

    @property
    def instance_id(self) -> str:
        return self.renderer.instance_id if self.renderer is not None else ""

    @property
    def supervisor(self):
        return self.renderer.supervisor if self.renderer is not None else None

    def before_prepare(self, service, attempt_id, worker_id, state_version):
        from shine.comfyops.handoff_preparation import ManagedReviewCoordinator
        from shine.comfyops.handoff_startup import start_managed_renderer, start_managed_review_service
        from shine.comfyops.managed_recipe import build_renderer_launch, build_review_launch

        if self.renderer is not None:
            raise HTTPException(409, "COMFY_STARTUP_ALREADY_RECORDED")
        recipe = self.recipe
        argv, cwd, env, pins, token, port = build_renderer_launch(recipe)
        self.renderer = start_managed_renderer(
            self.factory, self.settings,
            instance_id=recipe["instance_id"], lease_id=self.lease_id, job_id=self.job_id,
            worker_id=worker_id, argv=argv, cwd=cwd, env=env, pinned_files=pins,
            port=port, handoff_token=token, adapter_sha256=recipe["adapter_sha256"],
            gpu_uuid=recipe["gpu_uuid"], min_ram_mb=recipe["min_ram_mb"],
            min_vram_mb=recipe["min_vram_mb"],
        )
        client = getattr(service, "client", None)
        if (not client or getattr(client, "base_url", "").rstrip("/") != self.renderer.url
                or type(client) is not ComfyUIClient or client._transport is not None
                or client.trust_env is not False):
            raise HTTPException(409, "HANDOFF_RENDER_CLIENT_UNVERIFIED")
        configs = {}
        fences = {}
        for kind, spec in self.review_configs.items():
            review_argv, review_cwd, review_env, review_pins, review_token = build_review_launch(spec)
            started = start_managed_review_service(
                self.factory, self.settings,
                service_id=spec["service_id"], lease_id=self.lease_id, job_id=self.job_id,
                worker_id=worker_id, argv=review_argv, cwd=review_cwd, env=review_env,
                pinned_files=review_pins, port=spec["port"], handoff_token=review_token,
                runtime_sha256=spec.get("runtime_sha256"), gpu_uuid=recipe["gpu_uuid"],
                min_ram_mb=spec.get("min_ram_mb", 8000), min_vram_mb=spec.get("min_vram_mb", 8000),
                base_url=spec.get("base_url"),
            )
            self.review_services[kind] = started
            configs[kind] = started["config"]
            # The fence belongs to the coordinator, not to the release config:
            # the release config is what the fence verification receives.
            fences[kind] = started["fence"]["fence_id"]
        self.coordinator = ManagedReviewCoordinator(
            self.renderer.supervisor, self.renderer.instance_id, self.history_dir,
            self.renderer.gpu_uuid, min_ram_mb=self.renderer.min_ram_mb,
            min_vram_mb=self.renderer.min_vram_mb, review_configs=configs,
        )
        self.coordinator._cold_fences.update(fences)
        self.next = self.coordinator

    def activate(self, attempt_id, worker_id, state_version):
        from shine.comfyops.handoff_activation import activate_renderer_for_first_attempt
        from shine.comfyops.handoff_startup import StartupBoundary

        if self.renderer is None:
            raise HTTPException(409, "COMFY_STARTUP_BOUNDARY_INVALID")
        return activate_renderer_for_first_attempt(
            self.factory, self.settings, self.renderer.supervisor, self.job_id, attempt_id,
            self.lease_id, self.renderer.instance_id, worker_id, state_version,
            startup_boundary=StartupBoundary(self.renderer),
        )


def run_managed_first(session_factory, worker_id, *, settings, recipe, review_configs,
                      comfy_service=None, comfy_client=None, semantic_reviewer=None, asr_reviewer=None,
                      expected_job_id: str | None = None):
    """Run the first managed job behind a freshly started, closed renderer.

    The renderer is created only after an exclusive lease exists, and every
    external action is preceded by its own durable intent.

    ``expected_job_id`` pins the shot this startup belongs to.  A production
    series owns the order of its shots, so the series scheduler names the job
    instead of letting the global queue decide.
    """
    from shine.comfyops.scheduler import CURRENT_RESOURCE, ResourceScheduler

    if (not isinstance(recipe, dict) or not recipe.get("instance_id")
            or not isinstance(review_configs, dict) or not review_configs
            or not (semantic_reviewer or asr_reviewer)):
        raise HTTPException(409, "COMFY_STARTUP_ARGUMENTS_INVALID")
    scheduler = ResourceScheduler()
    with session_factory.begin() as db:
        if expected_job_id:
            candidate = db.get(Job, expected_job_id)
            if (candidate is None or candidate.status != "QUEUED" or candidate.paused
                    or candidate.stale or candidate.provider != "comfyui"):
                return None
        else:
            candidate = db.scalar(
                select(Job)
                .where(Job.status == "QUEUED", Job.paused.is_(False), Job.stale.is_(False),
                       Job.provider == "comfyui")
                .order_by(Job.created_at, Job.id)
                .limit(1)
            )
            if not candidate:
                return None
        owner = "h3:" + candidate.id
        lease = scheduler.enqueue(db, owner, 16384, True, 100)
        if lease.status == "RELEASED":
            lease.status = "WAITING"
        db.commit()
        lease_id, job_id = lease.id, candidate.id
    with session_factory() as db:
        if not scheduler.admit(db, lease_id):
            return None
    history_dir = Path(settings.data_dir).resolve() / "comfyops" / "handoff-history"
    continuation = _FirstManagedContinuation(
        session_factory, settings, job_id=job_id, lease_id=lease_id, recipe=recipe,
        review_configs=review_configs, history_dir=history_dir,
    )
    token = CURRENT_RESOURCE.set(lease_id)
    try:
        return _run_one(session_factory, worker_id=worker_id, settings=settings,
                        comfy_service=comfy_service, comfy_client=comfy_client,
                        semantic_reviewer=semantic_reviewer, asr_reviewer=asr_reviewer,
                        expected_job_id=job_id, managed_continuation=continuation)
    finally:
        CURRENT_RESOURCE.reset(token)


def run_managed_once(session_factory, worker_id, previous, *, settings, comfy_service=None, comfy_client=None,
                     semantic_reviewer=None, asr_reviewer=None, expected_job_id: str | None = None,
                     exclude_series_jobs: bool = False):
    """Continue one queued H3 job from a live prior handoff; no first-start authority is implied.

    ``expected_job_id`` pins the shot this continuation belongs to, so a
    production series keeps its own order instead of inheriting the global
    queue order.
    """
    from shine.comfyops.handoff_activation import ReviewReleaseBoundary
    from shine.comfyops.handoff_idle import IdleHandoffKeeper
    from shine.comfyops.handoff_preparation import ManagedReviewCoordinator
    from shine.comfyops.models import ResourceLease
    from shine.comfyops.scheduler import CURRENT_RESOURCE
    from shine.models import AppSetting

    if (not isinstance(previous, ManagedReviewCoordinator) or not previous.session or not previous.session.finished
            or not isinstance(previous.keeper, IdleHandoffKeeper) or not previous.replacement
            or previous.keeper.factory is not session_factory
            or previous.keeper.attempt_id != previous.session.attempt_id
            or previous.keeper.supervisor is not previous.replacement or previous.session.factory is not session_factory
            or not (semantic_reviewer or asr_reviewer)):
        raise HTTPException(409, "HANDOFF_COORDINATOR_INVALID")
    if (Path(settings.data_dir).resolve() != Path(previous.session.settings.data_dir).resolve()
            or settings.database_url != previous.session.settings.database_url
            or bool(settings.offline_simulation) != bool(previous.session.settings.offline_simulation)):
        raise HTTPException(409, "HANDOFF_SETTINGS_MISMATCH")
    if not settings.offline_simulation and any(
        getattr(provider, "is_mock", None) is not False for provider in (semantic_reviewer, asr_reviewer) if provider
    ):
        raise HTTPException(409, "HANDOFF_MOCK_REVIEW_FORBIDDEN")
    with session_factory.begin() as db:
        _promote_dependencies(db)
        db.flush()
        if expected_job_id:
            job = db.get(Job, expected_job_id)
            if (job is None or job.status != "QUEUED" or job.paused or job.stale
                    or job.provider != "comfyui"):
                return None, previous
        else:
            statement = select(Job).where(Job.status == "QUEUED", Job.paused.is_(False), Job.stale.is_(False))
            if exclude_series_jobs:
                statement = statement.where(Job.id.not_in(_series_job_ids()))
            job = db.scalar(statement.order_by(Job.created_at, Job.id).limit(1))
            if not job or job.provider != "comfyui":
                return None, previous
        request = RenderRequest.model_validate(job.request)
        policy = _project_policy(db, job.project_id)
        if request.self_review_scope == "basic":
            required = ({"VLM"} if request.shot_spec.characters else set()) | (
                {"ASR"} if request.shot_spec.dialogue else set())
        else:
            required = ({"VLM"} if policy.require_semantic_review or policy.require_style_review else set()) | (
                {"ASR"} if policy.require_audio_review and request.shot_spec.dialogue else set())
        available = {kind for kind, provider in (("VLM", semantic_reviewer), ("ASR", asr_reviewer)) if provider}
        if not required.issubset(available) or not available.issubset(previous._review_configs):
            raise HTTPException(409, "HANDOFF_REQUIRED_REVIEW_MISSING")
        if (not settings.offline_simulation
                and not available.issubset(set(previous._previous_releases) | set(previous._cold_fences))):
            raise HTTPException(409, "HANDOFF_REVIEW_COLD_FENCE_REQUIRED")
        from urllib.parse import urlsplit

        ports = [urlsplit(previous._review_configs[kind]["base_url"]).port for kind in available]
        if len(ports) != len(set(ports)):
            raise HTTPException(409, "HANDOFF_REVIEW_SERVICE_SHARED")
        job_id = job.id
    boundary = ReviewReleaseBoundary(previous.session, min_ram_mb=previous.min_ram_mb,
                                     min_vram_mb=previous.min_vram_mb, cold_fences=previous._cold_fences,
                                     review_configs=previous._review_configs,
                                     prior_releases=previous._previous_releases)
    reserved = previous.keeper.reserve(settings, boundary, job_id, previous.instance_id)
    lease_id = reserved["lease_id"]
    continuation, token = None, None
    result = None
    try:
        continuation = _ManagedContinuation(session_factory, settings, previous, boundary, job_id, lease_id)
        token = CURRENT_RESOURCE.set(lease_id)
        result = _run_one(session_factory, worker_id=worker_id, settings=settings, comfy_service=comfy_service,
                          comfy_client=comfy_client, semantic_reviewer=semantic_reviewer, asr_reviewer=asr_reviewer,
                          expected_job_id=job_id, managed_continuation=continuation)
    finally:
        if token is not None:
            CURRENT_RESOURCE.reset(token)
        if not (continuation and continuation.next.session and continuation.next.session.finished
                and continuation.next.keeper):
            with session_factory.begin() as db:
                lease, job = db.get(ResourceLease, lease_id), db.get(Job, job_id)
                row = db.get(AppSetting, "handoff-reservation:" + job_id)
                newer_claim = bool(job and job.lease_owner and job.lease_owner != worker_id)
                if lease and lease.owner == "h3:" + job_id and not newer_claim:
                    lease.status = "RECONCILE_REQUIRED"
                if row and row.value.get("stage") == "RESERVED" and not newer_claim:
                    row.value = {**row.value, "stage": "RECONCILE_REQUIRED", "reason": "MANAGED_RUN_INCOMPLETE"}
                if job and job.status == "QUEUED" and not newer_claim:
                    try_transition(job, "NEEDS_REVIEW")
                    job.failure_code = "COMFY_HANDOFF_RECONCILIATION_REQUIRED"
    return result, continuation.next


def release_managed_series(session_factory, settings, coordinator, *, series_id=None, worker_id=None):
    """End a managed series: stop the owned renderer, then release its lease.

    The renderer is stopped and proved empty first, the exclusive lease is
    released afterwards, and only then does the idle keeper stop renewing it.
    A failure at any step keeps the lease for reconciliation instead of
    pretending the hardware was released.

    When a production series owns the run, the intent to release is recorded
    before the first external action, and the lease release and the series
    state transition commit in ONE transaction.  A crash or a failed commit can
    therefore never leave a released lease behind a series that still claims
    to hold it, and a release that already committed is idempotent instead of
    being reported as a failure.
    """
    from shine.comfyops.models import ResourceLease
    from shine.comfyops.scheduler import ResourceScheduler

    if coordinator is None or coordinator.keeper is None or coordinator.replacement is None:
        raise HTTPException(409, "HANDOFF_COORDINATOR_INVALID")
    supervisor = coordinator.replacement
    lease_id = coordinator.session.lease_id
    if series_id and worker_id:
        from shine.comfyops.production_series import record_finalization_intent

        record_finalization_intent(session_factory, series_id, worker_id, lease_id, coordinator)
    stopped = supervisor.stop_closed()
    supervisor.check_memory(coordinator.gpu_uuid, min_ram_mb=coordinator.min_ram_mb,
                            min_vram_mb=coordinator.min_vram_mb)
    coordinator.keeper.finalize()
    with session_factory.begin() as db:
        lease = db.get(ResourceLease, lease_id)
        if lease is not None and lease.status == "RELEASED":
            # The previous attempt committed the release but the caller never
            # saw the answer.  Releasing twice is not a second release.
            if series_id and worker_id:
                from shine.comfyops.production_series import mark_series_released

                mark_series_released(db, series_id, worker_id)
            return {"lease_id": lease_id, "stopped": stopped, "released": True, "idempotent": True}
        if not lease or lease.status != "ACTIVE":
            raise HTTPException(409, "HANDOFF_RESOURCE_LEASE_LOST")
        ResourceScheduler().release(db, lease.id, lease.owner, reconciled=True)
        if series_id and worker_id:
            from shine.comfyops.production_series import mark_series_released

            mark_series_released(db, series_id, worker_id)
    return {"lease_id": lease_id, "stopped": stopped, "released": True}


_QUEUE_CHOICE = object()
"""Sentinel: let the global job queue pick the next shot of a sequence."""


def _series_job_ids():
    """The job ids a production series owns; the ordinary queue leaves them alone."""
    from shine.models import ProductionSeriesItem

    return select(ProductionSeriesItem.job_id).where(ProductionSeriesItem.job_id.is_not(None))


def _run_managed_loop(
    session_factory,
    worker_id,
    *,
    settings,
    recipe,
    review_configs,
    max_shots=8,
    comfy_service=None,
    comfy_client=None,
    semantic_reviewer=None,
    asr_reviewer=None,
    first_job_id=None,
    next_job_id=None,
    on_shot_complete=None,
    series_id=None,
    exclude_series_jobs=False,
):
    """The one managed production loop.

    ``first_job_id`` names the shot of the fresh managed startup.  ``next_job_id``
    is consulted after every finished shot and returns the job the run continues
    with, or ``None`` to end the run.  The exclusive lease is held across the
    whole run and released exactly once, at the end.

    ``exclude_series_jobs`` keeps the ordinary job queue away from the jobs a
    production series owns: a series drives its own shots in its own order.
    """
    from shine.comfyops.scheduler import CURRENT_RESOURCE, ResourceScheduler

    renderer_recipe = recipe.get("renderer") if isinstance(recipe, dict) else None
    if (not isinstance(renderer_recipe, dict) or not renderer_recipe.get("instance_id")
            or not isinstance(review_configs, dict) or not review_configs
            or not (semantic_reviewer or asr_reviewer)):
        raise HTTPException(409, "COMFY_STARTUP_ARGUMENTS_INVALID")
    if type(max_shots) is not int or not 1 <= max_shots <= 64:
        raise HTTPException(409, "COMFY_SERIES_LENGTH_INVALID")
    if on_shot_complete is not None and not callable(on_shot_complete):
        raise HTTPException(409, "COMFY_SERIES_CALLBACK_INVALID")
    if next_job_id is not None and not callable(next_job_id):
        raise HTTPException(409, "COMFY_SERIES_CALLBACK_INVALID")
    scheduler = ResourceScheduler()
    with session_factory.begin() as db:
        if first_job_id:
            candidate = db.get(Job, first_job_id)
            if (candidate is None or candidate.status != "QUEUED" or candidate.paused
                    or candidate.stale or candidate.provider != "comfyui"):
                return []
        else:
            statement = (
                select(Job)
                .where(Job.status == "QUEUED", Job.paused.is_(False), Job.stale.is_(False),
                       Job.provider == "comfyui")
            )
            if exclude_series_jobs:
                statement = statement.where(Job.id.not_in(_series_job_ids()))
            candidate = db.scalar(statement.order_by(Job.created_at, Job.id).limit(1))
            if not candidate:
                return []
        owner = "h3:" + candidate.id
        lease = scheduler.enqueue(db, owner, 16384, True, 100)
        if lease.status == "RELEASED":
            lease.status = "WAITING"
        db.commit()
        lease_id, job_id = lease.id, candidate.id
    with session_factory() as db:
        if not scheduler.admit(db, lease_id):
            return []
    history_dir = Path(settings.data_dir).resolve() / "comfyops" / "handoff-history"
    continuation = _FirstManagedContinuation(
        session_factory, settings, job_id=job_id, lease_id=lease_id, recipe=renderer_recipe,
        review_configs=review_configs, history_dir=history_dir)
    token = CURRENT_RESOURCE.set(lease_id)
    try:
        result = _run_one(session_factory, worker_id=worker_id, settings=settings,
                          comfy_service=comfy_service, comfy_client=comfy_client,
                          semantic_reviewer=semantic_reviewer, asr_reviewer=asr_reviewer,
                          expected_job_id=job_id, managed_continuation=continuation)
    finally:
        CURRENT_RESOURCE.reset(token)
    shots = [result]
    coordinator = continuation.coordinator
    while coordinator is not None and len(shots) < max_shots:
        if on_shot_complete is not None:
            on_shot_complete(shots[-1], coordinator)
        target = next_job_id(shots[-1]) if next_job_id is not None else _QUEUE_CHOICE
        if target is _QUEUE_CHOICE:
            result, coordinator = run_managed_once(session_factory, worker_id, coordinator, settings=settings,
                                                    comfy_service=comfy_service, comfy_client=comfy_client,
                                                    semantic_reviewer=semantic_reviewer,
                                                    asr_reviewer=asr_reviewer,
                                                    exclude_series_jobs=exclude_series_jobs)
        elif target is None:
            break
        else:
            result, coordinator = run_managed_once(session_factory, worker_id, coordinator, settings=settings,
                                                    comfy_service=comfy_service, comfy_client=comfy_client,
                                                    semantic_reviewer=semantic_reviewer,
                                                    asr_reviewer=asr_reviewer,
                                                    expected_job_id=target)
        if result is None:
            break
        shots.append(result)
    if coordinator is not None and coordinator.keeper is not None:
        release_managed_series(session_factory, settings, coordinator, series_id=series_id,
                                worker_id=worker_id if series_id else None)
    return shots


def run_managed_series(session_factory, worker_id, *, settings, recipe, review_configs, max_shots=8,
                       comfy_service=None, comfy_client=None, semantic_reviewer=None, asr_reviewer=None,
                       on_shot_complete=None):
    """Drive a queue of H3 shots behind one managed renderer.

    The first shot owns a fresh managed startup.  Every following shot
    continues the same fenced renderer through the idle keeper's lease
    transfer, so no second renderer is started and no exclusive resource is
    released between two shots: the serial model stages can never overlap.
    This is the unattended production path — the caller queues the shots and
    the worker drives them to completion.

    ``on_shot_complete`` is called with the finished job id before the next
    shot is reserved.  A dependent shot can only be created after its source
    job passed, so the hook is where the caller queues it; the fenced
    renderer keeps holding the exclusive admission across that gap.
    """
    return _run_managed_loop(
        session_factory, worker_id,
        settings=settings, recipe=recipe, review_configs=review_configs, max_shots=max_shots,
        comfy_service=comfy_service, comfy_client=comfy_client,
        semantic_reviewer=semantic_reviewer, asr_reviewer=asr_reviewer,
        on_shot_complete=on_shot_complete,
    )


def _run_one(
    session_factory,
    provider: MockH3Provider | None = None,
    worker_id: str | None = None,
    *,
    settings: Settings | None = None,
    comfy_service: ComfyUIRenderService | None = None,
    comfy_client=None,
    semantic_reviewer=None,
    asr_reviewer=None,
    expected_job_id=None,
    managed_continuation=None,
) -> str | None:
    worker_id = worker_id or f"worker-{uuid4().hex[:12]}"
    if provider is None:
        provider = MockH3Provider()
    heartbeat(session_factory, worker_id, status="online", phase="claiming")
    claimed: tuple[str, str, str, int, str, str, str] | None = None
    try:
        with session_factory.begin() as db:
            _register_worker(db, worker_id)
            _promote_dependencies(db)
            db.flush()
            candidate = db.scalar(
                select(Job)
                .where(Job.status == "QUEUED", Job.paused.is_(False), Job.stale.is_(False), Job.id == expected_job_id)
                .order_by(Job.created_at, Job.id)
                .limit(1)
            )
            if not candidate:
                return None
            from shine.comfyops.preproduction import enforce

            try:
                current_preproduction_hash = enforce(
                    db,
                    candidate.project_id,
                    offline=candidate.provider == "mock" or bool(settings and settings.offline_simulation),
                    data_dir=settings.data_dir if settings else None,
                    workflow_profile_id=candidate.workflow_profile_id if candidate.provider == "comfyui" else None,
                    render_request=candidate.request,
                )
                if candidate.preproduction_manifest_hash != current_preproduction_hash:
                    raise HTTPException(409, "PREPRODUCTION_OUTDATED")
            except HTTPException as exc:
                candidate.status = "NEEDS_REVIEW"
                candidate.state_version += 1
                candidate.failure_code = str(exc.detail)
                add_audit(
                    db,
                    "orchestrator",
                    "PREPRODUCTION_BLOCKED",
                    "job",
                    candidate.id,
                    {"code": str(exc.detail)},
                    project_id=candidate.project_id,
                )
                return candidate.id
            # Recheck immutable rules at the last boundary, including requests
            # rebuilt by the existing repair engine. A failed rule never submits.
            from shine.models import ProjectRuleLock

            if (candidate.request or {}).get("rulepack_id") or db.get(ProjectRuleLock, candidate.project_id):
                from shine.h3.rulebook import H3RulebookRegistry

                registry = H3RulebookRegistry(settings.data_dir / "h3-source-cache" if settings else None)
                try:
                    if settings and any(s["local_cache_status"] == "TAMPERED" for s in registry.cache_status()):
                        raise ValueError("OFFICIAL_CACHE_TAMPERED")
                    pack = registry.for_project(db, candidate.project_id)
                    request = RenderRequest.model_validate(candidate.request)
                    if not pack or not registry.validate(request, pack).passed:
                        raise ValueError("RULE_PREFLIGHT_FAILED")
                except ValueError:
                    candidate.status = "NEEDS_REVIEW"
                    candidate.state_version += 1
                    candidate.failure_code = "RULE_PREFLIGHT_FAILED"
                    add_audit(
                        db,
                        "orchestrator",
                        "RULE_PREFLIGHT_FAILED",
                        "job",
                        candidate.id,
                        project_id=candidate.project_id,
                    )
                    return candidate.id
            now = utcnow()
            lease_seconds = settings.worker_lease_seconds if settings else 30
            result = db.execute(
                update(Job)
                .where(
                    Job.id == candidate.id,
                    Job.status == "QUEUED",
                    Job.paused.is_(False),
                    Job.stale.is_(False),
                )
                .values(
                    status="SUBMITTING",
                    state_version=candidate.state_version + 1,
                    lease_owner=worker_id,
                    lease_expires_at=now + timedelta(seconds=lease_seconds),
                    heartbeat_at=now,
                )
            )
            if result.rowcount != 1:
                return None
            job = db.get(Job, candidate.id)
            attempt_no = len(job.attempts) + 1
            repair_context = job.pending_repair_context or {}
            manifest = [
                ReferenceManifestItem.model_validate(item) for item in (job.request or {}).get("reference_manifest", [])
            ]
            attempt = JobAttempt(
                job_id=job.id,
                attempt_no=attempt_no,
                request_fingerprint=job_fingerprint(job.request),
                status="SUBMIT_INTENT",
                lease_owner=worker_id,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                heartbeat_at=now,
                attempt_kind=str(repair_context.get("attempt_kind") or "initial"),
                parent_attempt_id=repair_context.get("parent_attempt_id"),
                request_snapshot=job.request,
                prompt_sha256=(job.request or {}).get("prompt_sha256"),
                parameter_sha256=parameter_fingerprint(job.request or {}),
                manifest_sha256=manifest_fingerprint(manifest),
                repair_reason=repair_context.get("reason"),
                repair_root_cause=repair_context.get("reason"),
            )
            db.add(attempt)
            db.flush()
            job.pending_repair_context = None
            add_audit(
                db,
                "orchestrator",
                "SUBMIT_INTENT",
                "job",
                job.id,
                {
                    "attempt_no": attempt_no,
                    "provider": job.provider,
                    "attempt_kind": attempt.attempt_kind,
                    "request_revision": job.request_revision,
                    "prompt_sha256": attempt.prompt_sha256,
                    "parameter_sha256": attempt.parameter_sha256,
                },
                project_id=job.project_id,
            )
            claimed = (
                job.id,
                job.idempotency_key,
                attempt.id,
                job.state_version,
                job.provider,
                job.mock_scenario,
                job.project_id,
            )
    except Exception as exc:
        heartbeat(session_factory, worker_id, status="error", error=f"claim: {type(exc).__name__}")
        logger.exception("claim_failed")
        return None

    job_id, idempotency_key, attempt_id, expected_version, job_provider, scenario, project_id = claimed
    try:
        if job_provider == "comfyui":
            from contextlib import nullcontext

            guard_scope = (_ManagedRenderHeartbeat(session_factory, worker_id, job_id, attempt_id,
                                                    expected_version, settings.worker_lease_seconds)
                           if managed_continuation is not None else nullcontext(None))
            with guard_scope as managed_guard:
                return _run_comfy_job(
                    session_factory,
                    job_id=job_id,
                    attempt_id=attempt_id,
                    expected_version=expected_version,
                    worker_id=worker_id,
                    settings=settings,
                    comfy_service=comfy_service,
                    comfy_client=comfy_client,
                    semantic_reviewer=semantic_reviewer,
                    asr_reviewer=asr_reviewer,
                    managed_continuation=managed_continuation,
                    managed_guard=managed_guard,
                )
        return _run_mock_job(
            session_factory,
            provider,
            job_id=job_id,
            idempotency_key=idempotency_key,
            attempt_id=attempt_id,
            expected_version=expected_version,
            scenario=scenario,
            worker_id=worker_id,
            settings=settings,
        )
    except Exception as exc:
        # Provider exception text may contain URLs, credentials, or private input.
        logger.error("job_execution_failed job_id=%s error_type=%s", job_id, type(exc).__name__)
        detail = getattr(exc, "detail", None)
        if isinstance(detail, str) and detail:
            # The exception's own code names the blocked step without carrying
            # provider text, URLs or payloads into the log.
            logger.error("job_execution_failed_code job_id=%s code=%s", job_id, detail)
        try:
            with session_factory.begin() as db:
                job = db.get(Job, job_id)
                attempt = db.get(JobAttempt, attempt_id)
                expected_failure_version = expected_version
                if (job and attempt and job.status == "RUNNING" and job.provider_job_id
                        and attempt.provider_job_id == job.provider_job_id):
                    expected_failure_version += 1  # Acknowledgement is one guarded transition.
                owned = (job and attempt and job.lease_owner == worker_id and attempt.lease_owner == worker_id
                         and job.state_version == expected_failure_version and job.status in {"SUBMITTING", "RUNNING"}
                         and job.lease_expires_at and job.lease_expires_at > utcnow()
                         and attempt.lease_expires_at > utcnow() and not job.stale and _shot_is_current(db, job))
                if owned:
                    if job.provider == "comfyui" and attempt.provider_job_id:
                        job.failure_code = "COMFY_WORKER_EXCEPTION_WITH_EXTERNAL"
                        attempt.reconciliation_state = "worker_exception_known_id"
                    elif job.provider == "comfyui" and attempt.prompt_graph_sha256:
                        job.failure_code = "COMFY_SUBMISSION_OUTCOME_UNKNOWN"
                        attempt.status = "ACK_UNCERTAIN"
                        attempt.reconciliation_state = "submission_unknown"
                    else:
                        job.failure_code = "WORKER_EXCEPTION"
                    job.lease_owner = None
                    job.lease_expires_at = None
                    try_transition(job, "NEEDS_REVIEW")
                    if job.provider != "comfyui" or not (attempt.provider_job_id or attempt.prompt_graph_sha256):
                        attempt.status = "FAILED"
                        attempt.failure_code = "WORKER_EXCEPTION"
                    add_audit(
                        db,
                        "orchestrator",
                        "WORKER_EXCEPTION",
                        "job",
                        job.id,
                        {"error": type(exc).__name__},
                        project_id=job.project_id,
                    )
        except Exception:  # pragma: no cover
            logger.exception("worker_error_state_failed")
        heartbeat(session_factory, worker_id, status="error", error=f"job: {type(exc).__name__}")
        return job_id


def _run_mock_job(
    session_factory,
    provider: MockH3Provider,
    *,
    job_id: str,
    idempotency_key: str,
    attempt_id: str,
    expected_version: int,
    scenario: str,
    worker_id: str,
    settings: Settings | None = None,
) -> str:
    heartbeat(session_factory, worker_id, current_job_id=job_id, phase="submitting")
    try:
        submission = provider.submit(idempotency_key, scenario)
    except SubmissionUncertain as exc:
        with session_factory.begin() as db:
            guarded = _guard_claimed_job(
                db,
                job_id=job_id,
                expected_state_version=expected_version,
                worker_id=worker_id,
                attempt_id=attempt_id,
                settings=settings,
            )
            if guarded is None:
                return job_id
            job, attempt = guarded
            job.failure_code = str(exc)
            job.lease_owner = None
            job.lease_expires_at = None
            attempt.status = "ACK_UNCERTAIN"
            attempt.failure_code = str(exc)
            transition(job, "NEEDS_REVIEW")
            add_audit(
                db,
                "orchestrator",
                "SUBMISSION_ACK_UNCERTAIN",
                "job",
                job.id,
                {"resubmitted": False},
                project_id=job.project_id,
            )
        heartbeat(session_factory, worker_id, phase="review")
        return job_id
    except ProviderFailure as exc:
        with session_factory.begin() as db:
            guarded = _guard_claimed_job(
                db,
                job_id=job_id,
                expected_state_version=expected_version,
                worker_id=worker_id,
                attempt_id=attempt_id,
                settings=settings,
            )
            if guarded is None:
                return job_id
            job, attempt = guarded
            job.failure_code = exc.code
            job.lease_owner = None
            job.lease_expires_at = None
            attempt.status = "FAILED"
            attempt.failure_code = exc.code
            transition(job, "REPAIR_PENDING")
            add_audit(
                db,
                "mock-worker",
                "MOCK_RENDER_FAILED",
                "job",
                job.id,
                {"failure_code": exc.code},
                project_id=job.project_id,
            )
            project = db.get(Project, job.project_id)
            report = FusedQCResult(
                result="failed",
                hard_failures=[HardFailure(code=exc.code, evidence="The mock renderer reported a scripted failure")],
                review_confidence=1.0,
                human_review_required=True,
                is_mock=True,
                coverage={"technical": "not_run", "semantic": "not_run", "asr": "not_run"},
            )
            _auto_repair(
                db,
                job,
                attempt,
                report=report,
                failure_code=exc.code,
                project_auto_enabled=bool(project and project.auto_repair_enabled),
                worker_id=worker_id,
            )
        return job_id

    with session_factory.begin() as db:
        guarded = _guard_claimed_job(
            db,
            job_id=job_id,
            expected_state_version=expected_version,
            worker_id=worker_id,
            attempt_id=attempt_id,
            settings=settings,
        )
        if guarded is None:
            return job_id
        job, attempt = guarded
        job.provider_job_id = submission.provider_job_id
        job.heartbeat_at = utcnow()
        attempt.provider_job_id = submission.provider_job_id
        attempt.submit_ack_at = utcnow()
        attempt.status = "ACKNOWLEDGED"
        transition(job, "RUNNING")
        transition(job, "QC_PENDING")
        report = _mock_report(submission)
        _record_mock_qc(db, job, attempt, report)
        transition(job, "PASSED")
        job.last_qc_result = "passed"
        job.quality_verification = VERIFICATION_MOCK
        job.lease_owner = None
        job.lease_expires_at = None
        add_audit(
            db,
            "mock-worker",
            "MOCK_WORKFLOW_PASSED",
            "job",
            job.id,
            report,
            project_id=job.project_id,
        )
    heartbeat(session_factory, worker_id, phase="idle", processed_delta=1)
    return job_id


def _load_comfy_context(
    db: Session, job: Job
) -> tuple[WorkflowProfile | None, RenderRequest, list[ReferenceManifestItem], dict[str, Asset], QualityPolicy, str]:
    profile = db.get(WorkflowProfile, job.workflow_profile_id) if job.workflow_profile_id else None
    request = RenderRequest.model_validate(job.request)
    manifest = [ReferenceManifestItem.model_validate(item) for item in request.reference_manifest]
    assets = {
        asset.id: asset
        for asset in db.scalars(select(Asset).where(Asset.id.in_([item.asset_id for item in manifest]))).all()
    }
    project = db.get(Project, job.project_id)
    policy = QualityPolicy.from_dict(project.quality_policy if project else None)
    language = project.language if project else "Chinese"
    return profile, request, manifest, assets, policy, language


def _renderer_client(settings: Settings, *, max_retries: int = 2):
    """The renderer client for this worker.

    A local renderer must never be reached through an environment proxy: the
    managed path refuses a client that could be redirected, and reference
    bytes travel over that connection. Only an explicitly allowed remote
    renderer keeps the environment proxy behaviour.
    """

    return ComfyUIClient(
        settings.comfyui_base_url, timeout_s=10, max_retries=max_retries,
        trust_env=bool(settings.allow_remote_comfyui),
    )


def _isolated_mock_handoff(settings, reviewers):
    """Only isolated offline simulation may use the synthetic handoff."""
    return settings.offline_simulation and all(
        reviewer is None or getattr(reviewer, "is_mock", False) for reviewer in reviewers
    )


def _managed_recipe(settings):
    """Read the operator's managed renderer recipe, if one is configured."""
    path = os.getenv("SHINE_MANAGED_RECIPE", "").strip()
    if not path:
        return None
    from shine.comfyops.managed_recipe import load_managed_recipe

    return load_managed_recipe(path)


def _managed_review_specs(recipe, semantic_reviewer, asr_reviewer):
    """Bind each configured reviewer to its own owned review service."""
    from urllib.parse import urlsplit

    specs = {}
    for kind, provider in (("VLM", semantic_reviewer), ("ASR", asr_reviewer)):
        if provider is None:
            continue
        config = getattr(provider, "config", None)
        base_url = getattr(config, "base_url", None)
        port = urlsplit(base_url).port if base_url else None
        entry = (recipe.get("reviews") or {}).get(kind)
        if entry is None or not port or entry["port"] != port:
            raise HTTPException(409, "COMFY_REVIEW_SERVICE_UNCONFIGURED")
        specs[kind] = {**entry, "base_url": base_url}
    if not specs:
        raise HTTPException(409, "COMFY_REVIEW_SERVICE_UNCONFIGURED")
    return specs


def _managed_handoff_holds_lease(managed_first):
    """True when a completed managed handoff keeps its own exclusive lease."""
    coordinator = getattr(managed_first, "coordinator", None)
    return bool(coordinator is not None and getattr(coordinator, "keeper", None) is not None)


def _handoff_preflight_allowed(settings, reviewers):
    return _isolated_mock_handoff(settings, reviewers)


def _collect_comfy_reviews(*, request, settings, artifact_path, digest, job_id, project_id, remote_consent,
                           semantic_reviewer, asr_reviewer, review_revision):
    technical = TechnicalQC(ffprobe_path=settings.ffprobe_path, ffmpeg_path=settings.ffmpeg_path).check(
        artifact_path,
        expectations=TechnicalExpectations(
            duration_s=request.expected_duration_s, expect_audio=bool(request.shot_spec.dialogue),
        ),
    )
    transcript, transcript_findings, asr_error = _transcript_review(
        asr=asr_reviewer, artifact_path=artifact_path, spec=request.shot_spec, remote_consent=remote_consent,
    )
    semantic_results, semantic_error = _semantic_review(
        reviewer=semantic_reviewer, job_id=job_id, spec=request.shot_spec, style=request.style_snapshot,
        artifact_path=artifact_path, artifact_sha256=digest, settings=settings, remote_consent=remote_consent,
        project_id=project_id, transcript=transcript, reference_manifest=request.reference_manifest,
        review_revision=review_revision,
        independent_input=({"artifact_id": request.style_input_assessment_id,
                            "sha256": request.style_input_assessment_sha256}
                           if request.style_input_assessment_id else None),
    )
    return technical, transcript, transcript_findings, asr_error, semantic_results, semantic_error


def _finalize_comfy_attempt(
    session_factory,
    *,
    job_id: str,
    attempt_id: str,
    expected_state_version: int | None,
    worker_id: str,
    settings: Settings,
    service: ComfyUIRenderService,
    prompt_id: str,
    semantic_reviewer=None,
    asr_reviewer=None,
    managed_handoff=None,
) -> str:
    """Download the external output, run every quality gate and decide the outcome.

    This is shared by the normal render path and by crash recovery, so a task
    that ComfyUI already finished is always collected, checked and advanced
    without a second render.
    """
    with session_factory.begin() as db:
        job = db.get(Job, job_id)
        attempt = db.get(JobAttempt, attempt_id)
        if not job or not attempt:
            return job_id
        profile, request, manifest, assets, policy, language = _load_comfy_context(db, job)
        project = db.get(Project, job.project_id)
        remote_consent = bool(project and project.allow_remote_review)
        project_id = job.project_id
        existing = db.get(MediaArtifact, job.artifact_id) if job.artifact_id else None
        from shine.models import AppSetting

        recheck = db.get(AppSetting, "qc-recheck:" + job.id)
        review_revision = recheck.value.get("request_id") if recheck else None
        if existing and existing.attempt_id != attempt_id:
            existing = None
        artifact_path: Path | None = None
        if existing:
            # Preserve the distinction between an absent stored artifact and
            # a render that has not yet been collected. Never redownload an
            # existing attempt's missing or changed artifact during recheck.
            artifact_path = settings.data_dir / existing.storage_path

    relative: str
    digest: str
    byte_size: int
    content_type: str
    if artifact_path is None:
        result = service.poll(prompt_id)
        if result.status != "completed" or result.content is None or not result.content_type:
            with session_factory.begin() as db:
                guarded = _guard_claimed_job(
                    db,
                    job_id=job_id,
                    expected_state_version=expected_state_version if expected_state_version is not None else -1,
                    worker_id=worker_id,
                    attempt_id=attempt_id,
                    allowed_status=("RUNNING", "QC_PENDING", "SUBMITTING"),
                    settings=settings,
                )
                if guarded is None:
                    return job_id
                job, attempt = guarded
                job.failure_code = result.error_code or "COMFY_OUTPUT_MISSING"
                job.lease_owner = None
                job.lease_expires_at = None
                attempt.status = "FAILED"
                attempt.failure_code = job.failure_code
                attempt.reconciliation_state = result.status
                if job.status in {"SUBMITTING", "RUNNING"}:
                    try_transition(job, "QC_PENDING")
                try_transition(job, "NEEDS_REVIEW")
                db.add(
                    ExternalJobReconciliation(
                        job_id=job.id,
                        provider_job_id=prompt_id,
                        action="collect_output",
                        result=result.status,
                        evidence={"error_code": result.error_code},
                    )
                )
                add_audit(
                    db,
                    "comfy-worker",
                    "COMFY_OUTPUT_RECONCILIATION_REQUIRED",
                    "job",
                    job.id,
                    {"result": result.status, "error_code": result.error_code},
                    project_id=job.project_id,
                )
            return job_id
        relative, digest, byte_size = store_artifact(
            settings.data_dir, project_id, job_id, result.content, result.content_type
        )
        content_type = result.content_type
        artifact_path = settings.data_dir / relative
        stored_digest = _file_digest(artifact_path)
        if stored_digest != digest:
            with session_factory.begin() as db:
                job = db.get(Job, job_id)
                attempt = db.get(JobAttempt, attempt_id)
                if job:
                    job.failure_code = "ARTIFACT_HASH_MISMATCH"
                    job.lease_owner = None
                    job.lease_expires_at = None
                    if job.status not in {"NEEDS_REVIEW", "FAILED"}:
                        try_transition(job, "NEEDS_REVIEW")
                    if attempt:
                        attempt.status = "FAILED"
                        attempt.failure_code = "ARTIFACT_HASH_MISMATCH"
            return job_id
    else:
        relative = str(existing.storage_path)  # type: ignore[union-attr]
        digest = str(existing.sha256)  # type: ignore[union-attr]
        byte_size = int(existing.byte_size)  # type: ignore[union-attr]
        content_type = str(existing.content_type)  # type: ignore[union-attr]
        # A previously collected artifact may have changed since recheck was
        # requested. Revalidate it before giving any bytes to a reviewer.
        from shine.media.artifacts import safe_asset_path

        try:
            artifact_path = safe_asset_path(settings.data_dir, relative)
            if artifact_path.stat().st_size != byte_size or _file_digest(artifact_path) != digest:
                raise ValueError("artifact changed")
        except (OSError, ValueError, HTTPException):
            with session_factory.begin() as db:
                guarded = _guard_claimed_job(
                    db, job_id=job_id,
                    expected_state_version=expected_state_version if expected_state_version is not None else -1,
                    worker_id=worker_id, attempt_id=attempt_id,
                    allowed_status=("RUNNING", "QC_PENDING", "SUBMITTING"), settings=settings,
                )
                if guarded:
                    job, _attempt = guarded
                    job.failure_code = "ARTIFACT_HASH_MISMATCH"
                    job.lease_owner = None
                    job.lease_expires_at = None
                    try_transition(job, "NEEDS_REVIEW")
            return job_id

    # Record the downloaded bytes before any process lifecycle operation. A
    # ComfyUI restart may erase in-memory history; recovery must retain the
    # exact output and must never submit the render again to recover it.
    with session_factory.begin() as db:
        guarded = _guard_claimed_job(
            db, job_id=job_id,
            expected_state_version=expected_state_version if expected_state_version is not None else -1,
            worker_id=worker_id, attempt_id=attempt_id,
            allowed_status=("RUNNING", "QC_PENDING", "SUBMITTING"), settings=settings,
        )
        if guarded is None:
            return job_id
        collected_job, collected_attempt = guarded
        if not collected_job.artifact_id:
            artifact = MediaArtifact(
                project_id=project_id, job_id=job_id, attempt_id=attempt_id,
                artifact_kind="video", storage_path=relative, sha256=digest,
                byte_size=byte_size, content_type=content_type, is_mock=False,
                provenance={"prompt_id": prompt_id},
            )
            db.add(artifact)
            db.flush()
            collected_job.artifact_id = artifact.id
            collected_attempt.output_path = relative
            collected_attempt.output_sha256 = digest
            collected_attempt.output_bytes = byte_size
            add_audit(db, "comfy-worker", "COMFY_OUTPUT_PERSISTED", "job", job_id,
                      {"attempt_id": attempt_id, "artifact_id": artifact.id, "sha256": digest},
                      project_id=project_id)

    if request.self_review_scope == "basic" and not request.shot_spec.characters:
        semantic_reviewer = None
    review_scope = None
    if semantic_reviewer or asr_reviewer:
        from shine.comfyops.model_calls import TrackedProvider
        from shine.comfyops.models import ResourceLease
        from shine.comfyops.scheduler import CURRENT_RESOURCE, ResourceScheduler

        # Offline simulation is restricted to isolated mock reviewers. A real
        # renderer's HTTP unload is not evidence of process ownership or host
        # memory release, even if its own CUDA counters report idle.
        simulated = _isolated_mock_handoff(settings, (semantic_reviewer, asr_reviewer))
        if not simulated and managed_handoff is None:
            with session_factory.begin() as release_db:
                current_job = release_db.get(Job, job_id)
                lease = release_db.scalar(select(ResourceLease).where(ResourceLease.owner == "h3:" + job_id))
                if lease is None:
                    lease = ResourceScheduler().enqueue(release_db, "h3:" + job_id, 16384, True, 100)
                lease.status = "RECONCILE_REQUIRED"
                current_job.failure_code = "COMFY_HANDOFF_OWNERSHIP_UNVERIFIED"
                current_job.lease_owner = None
                current_job.lease_expires_at = None
                try_transition(current_job, "NEEDS_REVIEW")
                add_audit(release_db, "comfy-worker", "COMFY_HANDOFF_BLOCKED", "job", job_id,
                          {"attempt_id": attempt_id, "reason": current_job.failure_code,
                           "render_resubmission": False}, project_id=project_id)
            return job_id

        if managed_handoff is None:
            # This path is limited to isolated offline mock simulation.
            try:
                service.client.free_models()
            except Exception:
                with session_factory.begin() as release_db:
                    current_job = release_db.get(Job, job_id)
                    current_job.failure_code = "MODEL_RELEASE_RECONCILIATION_REQUIRED"
                    current_job.lease_owner = None
                    current_job.lease_expires_at = None
                    try_transition(current_job, "NEEDS_REVIEW")
                return job_id
            with session_factory.begin() as release_db:
                lease = release_db.scalar(select(ResourceLease).where(ResourceLease.owner == "h3:" + job_id))
                if lease:
                    ResourceScheduler().release(release_db, lease.id, lease.owner, reconciled=True)
            CURRENT_RESOURCE.set(None)
        if semantic_reviewer and request.self_review_scope == "basic":
            from shine.quality.basic_character_reviewer import BasicCharacterReviewer
            from shine.quality.semantic import LocalVisionReviewer

            if isinstance(semantic_reviewer, LocalVisionReviewer):
                semantic_reviewer = BasicCharacterReviewer(
                    semantic_reviewer.config, resource_mb=semantic_reviewer.resource_budget_mb("review")
                )
        if managed_handoff is not None:
            from shine.comfyops.handoff_preparation import ManagedReviewCoordinator

            if not isinstance(managed_handoff, ManagedReviewCoordinator):
                raise HTTPException(409, "HANDOFF_COORDINATOR_INVALID")
            with session_factory() as db:
                lease = db.scalar(select(ResourceLease).where(ResourceLease.owner == "h3:" + job_id))
                if not lease:
                    raise HTTPException(409, "HANDOFF_RESOURCE_LEASE_LOST")
                lease_id = lease.id
            review_scope = managed_handoff.review(
                session_factory, settings, job_id, attempt_id, lease_id, worker_id, expected_state_version,
                project_id, semantic_reviewer, asr_reviewer,
            )
        else:
            if semantic_reviewer:
                semantic_reviewer = TrackedProvider(semantic_reviewer, session_factory, project_id,
                                                     reviewer_kind="VLM", production_job_id=job_id)
            if asr_reviewer:
                asr_reviewer = TrackedProvider(asr_reviewer, session_factory, project_id, reviewer_kind="ASR",
                                               production_job_id=job_id)

    from contextlib import nullcontext

    with review_scope or nullcontext((semantic_reviewer, asr_reviewer)) as (semantic, asr):
        reviewed = _collect_comfy_reviews(
            request=request, settings=settings, artifact_path=artifact_path, digest=digest, job_id=job_id,
            project_id=project_id, remote_consent=remote_consent, semantic_reviewer=semantic, asr_reviewer=asr,
            review_revision=review_revision,
        )
        technical, transcript, transcript_findings, asr_error, semantic_results, semantic_error = reviewed
    if request.self_review_scope == "basic":
        from shine.quality.basic_character_reviewer import CharacterReview
        from shine.quality.basic_review import (
            CharacterReviewResponse,
            character_evidence_from_response,
            dialogue_evidence_from_transcript,
            evaluate_basic_gates,
        )

        characters = ()
        character_response = None
        if not semantic_error and len(semantic_results) == 1 and isinstance(semantic_results[0], CharacterReview):
            result = semantic_results[0]
            character_response = result.response
            bound = {item.entity_id or item.role for item in request.reference_manifest
                     if item.content_type.startswith("image/")
                     and item.use_as in {ReferenceUse.SUBJECT, ReferenceUse.PICTURE}
                     and (item.role or "").casefold() not in {"scene", "location", "prop", "costume", "voice"}
                     and (item.entity_id in request.shot_spec.characters or item.role in request.shot_spec.characters)}
            try:
                characters = character_evidence_from_response(
                    CharacterReviewResponse.model_validate(result.response),
                    expected_characters=request.shot_spec.characters, reference_characters=bound,
                    artifact_sha256=result.input_sha256, reviewer=result.model, is_mock=result.is_mock,
                )
            except ValueError:
                characters = ()
                character_response = None
                semantic_error = "BASIC_CHARACTER_RESPONSE_INVALID"
        dialogue = (dialogue_evidence_from_transcript(request.shot_spec, transcript, digest)
                    if transcript and not asr_error and request.shot_spec.dialogue else None)
        size = request.basic_review_resolution
        gates, fused = evaluate_basic_gates(
            shot=request.shot_spec, technical=technical, expected_resolution=(size.width, size.height),
            artifact_sha256=digest, characters=characters, dialogue=dialogue, character_response=character_response,
            character_error=semantic_error, audio_error=asr_error,
            offline_simulation=bool(settings.offline_simulation),
        )
    else:
        gates, fused = evaluate_gates(
            policy=policy,
            shot_spec=request.shot_spec,
            technical=technical,
            semantic_results=semantic_results,
            semantic_error=semantic_error,
            transcript_findings=transcript_findings,
            asr_error=asr_error,
            transcript_model=transcript.model if transcript else None,
            transcript_is_mock=bool(transcript.is_mock) if transcript else False,
            style_version_id=request.style_version_id,
            style_hash=request.style_hash,
            reference_count=len(manifest),
        )

    with session_factory.begin() as db:
        guarded = _guard_claimed_job(
            db,
            job_id=job_id,
            expected_state_version=expected_state_version if expected_state_version is not None else -1,
            worker_id=worker_id,
            attempt_id=attempt_id,
            allowed_status=("RUNNING", "QC_PENDING", "SUBMITTING"),
            settings=settings,
        )
        if guarded is None:
            return job_id
        job, attempt = guarded
        job.provider_job_id = job.provider_job_id or prompt_id
        artifact = db.get(MediaArtifact, job.artifact_id) if job.artifact_id else None
        if artifact is None or artifact.attempt_id != attempt.id:
            artifact = MediaArtifact(
                project_id=job.project_id,
                job_id=job.id,
                attempt_id=attempt.id,
                artifact_kind="video",
                storage_path=relative,
                sha256=digest,
                byte_size=byte_size,
                content_type=content_type,
                is_mock=False,
                provenance={"prompt_id": prompt_id},
            )
            db.add(artifact)
            db.flush()
            job.artifact_id = artifact.id
        attempt.output_path = relative
        attempt.output_sha256 = digest
        attempt.output_bytes = byte_size
        attempt.progress = 100
        attempt.status = "COMPLETED"
        attempt.reconciliation_state = "collected"
        attempt.reconciled_at = utcnow()
        job.last_qc_result = fused.result
        _record_gates(db, job, attempt, artifact, gates, fused)
        if job.status in {"SUBMITTING", "RUNNING"}:
            transition(job, "QC_PENDING")
        job.lease_owner = None
        job.lease_expires_at = None
        job.quality_verification = (
            VERIFICATION_MOCK if any(gate.is_mock for gate in gates)
            else VERIFICATION_PARTIAL if request.self_review_scope == "basic" and fused.result == "needs_review"
            else VERIFICATION_REAL
        )
        if fused.result == STATUS_PASSED:
            job.failure_code = None
            transition(job, "PASSED")
        elif fused.result == STATUS_FAILED:
            transition(job, "REPAIR_PENDING")
            job.failure_code = "QC_FAILED"
            project = db.get(Project, job.project_id)
            _auto_repair(
                db,
                job,
                attempt,
                report=fused,
                failure_code="QC_FAILED",
                project_auto_enabled=bool(project and project.auto_repair_enabled),
                worker_id=worker_id,
                project_language=language,
            )
        else:
            try_transition(job, "NEEDS_REVIEW")
            job.failure_code = "QC_NEEDS_REVIEW"
            decision = RepairEngine().plan(
                fused,
                retries_left=max(0, job.max_semantic_retries - job.semantic_retries),
                auto_repair_enabled=False,
            )
            job.last_repair_plan = decision.plan.model_dump(mode="json")
        add_audit(
            db,
            "comfy-worker",
            "COMFY_ARTIFACT_QC",
            "job",
            job.id,
            {
                "qc_result": fused.result,
                "sha256": digest,
                "coverage": fused.coverage,
                "gates": {gate.gate_name: gate.status for gate in gates},
                "verification": job.quality_verification,
            },
            project_id=job.project_id,
        )
    heartbeat(session_factory, worker_id, phase="idle", processed_delta=1)
    return job_id


def _file_digest(path: Path) -> str:
    import hashlib

    with path.open("rb") as media:
        return hashlib.file_digest(media, "sha256").hexdigest()


def _run_comfy_job(
    session_factory,
    *,
    job_id: str,
    attempt_id: str,
    expected_version: int,
    worker_id: str,
    settings: Settings | None,
    comfy_service: ComfyUIRenderService | None,
    comfy_client,
    semantic_reviewer=None,
    asr_reviewer=None,
    managed_continuation=None,
    managed_guard=None,
) -> str:
    settings = settings or Settings.from_env()
    client = comfy_client or _renderer_client(settings)
    service = comfy_service or ComfyUIRenderService(client, settings.data_dir)
    with session_factory.begin() as db:
        job = db.get(Job, job_id)
        attempt = db.get(JobAttempt, attempt_id)
        if not job or not attempt:
            return job_id
        profile, request, manifest, assets, _policy, _language = _load_comfy_context(db, job)
        project_id = job.project_id
        if profile is None:
            job.failure_code = "COMFY_WORKFLOW_PROFILE_MISSING"
            job.lease_owner = None
            job.lease_expires_at = None
            attempt.status = "FAILED"
            attempt.failure_code = job.failure_code
            try_transition(job, "REPAIR_PENDING")
            add_audit(
                db,
                "orchestrator",
                "COMFY_WORKFLOW_MISSING",
                "job",
                job.id,
                project_id=job.project_id,
            )
            return job_id
    if not heartbeat(session_factory, worker_id, current_job_id=job_id, phase="submitting",
                     expected_state_version=expected_version, attempt_id=attempt_id,
                     lease_seconds=settings.worker_lease_seconds):
        return job_id
    if managed_continuation is not None:
        if not isinstance(managed_continuation, _ManagedAttemptContext) or managed_continuation.job_id != job_id:
            raise HTTPException(409, "HANDOFF_COORDINATOR_INVALID")
        managed_guard.check()
        managed_continuation.before_prepare(service, attempt_id, worker_id, expected_version)
    try:
        prepared = service.prepare(
            request,
            profile,
            assets,
            seed=request.seed,
            project_id=project_id,
        )
    except ComfyPendingWorkflowMapping as exc:
        with session_factory.begin() as db:
            guarded = _guard_claimed_job(
                db,
                job_id=job_id,
                expected_state_version=expected_version,
                worker_id=worker_id,
                attempt_id=attempt_id,
                settings=settings,
            )
            if guarded is None:
                return job_id
            job, attempt = guarded
            job.failure_code = exc.code
            job.lease_owner = None
            job.lease_expires_at = None
            attempt.status = "FAILED"
            attempt.failure_code = exc.code
            transition(job, "NEEDS_REVIEW")
            add_audit(
                db,
                "comfy-worker",
                "COMFY_WORKFLOW_MAPPING_PENDING",
                "job",
                job.id,
                {"details": exc.details},
                project_id=job.project_id,
            )
        return job_id
    except ComfyError as exc:
        with session_factory.begin() as db:
            guarded = _guard_claimed_job(
                db,
                job_id=job_id,
                expected_state_version=expected_version,
                worker_id=worker_id,
                attempt_id=attempt_id,
                settings=settings,
            )
            if guarded is None:
                return job_id
            job, attempt = guarded
            job.failure_code = exc.code
            job.lease_owner = None
            job.lease_expires_at = None
            attempt.status = "FAILED"
            attempt.failure_code = exc.code
            attempt.reconciliation_state = "rejected_before_queue"
            transition(job, "REPAIR_PENDING")
            add_audit(
                db,
                "comfy-worker",
                "COMFY_SUBMIT_FAILED",
                "job",
                job.id,
                {"code": exc.code, "details": exc.details},
                project_id=job.project_id,
            )
        return job_id

    if managed_guard is not None:
        managed_guard.check()
    # The submission intent is durable before the external call. If the call
    # is accepted but the response is lost, the persisted graph hash is enough
    # to find the original task instead of submitting a second one.
    with session_factory.begin() as db:
        attempt = db.get(JobAttempt, attempt_id)
        if attempt is None:
            return job_id
        attempt.prompt_graph_sha256 = prepared.prompt_graph_sha256
        attempt.status = "SUBMIT_INTENT"
        attempt.heartbeat_at = utcnow()
        add_audit(
            db,
            "comfy-worker",
            "COMFY_SUBMIT_INTENT",
            "job",
            job_id,
            {
                "prompt_graph_sha256": prepared.prompt_graph_sha256,
                "uploaded_references": prepared.uploaded_references,
            },
            project_id=project_id,
        )

    if managed_continuation is not None:
        managed_continuation.activate(attempt_id, worker_id, expected_version)
        managed_guard.check()

    try:
        with session_factory() as submit_db:
            from sqlalchemy import text

            from shine.comfyops.preproduction import enforce

            submit_db.execute(text("BEGIN IMMEDIATE"))
            current = submit_db.get(Job, job_id)
            try:
                pin = enforce(
                    submit_db,
                    project_id,
                    offline=settings.offline_simulation,
                    data_dir=settings.data_dir,
                    workflow_profile_id=current.workflow_profile_id,
                    render_request=current.request,
                )
                if pin != current.preproduction_manifest_hash:
                    raise HTTPException(409, "PREPRODUCTION_OUTDATED")
                if not _guard_claimed_job(
                    submit_db,
                    job_id=job_id,
                    expected_state_version=expected_version,
                    worker_id=worker_id,
                    attempt_id=attempt_id,
                    settings=settings,
                ):
                    submit_db.commit()  # persist a guard's rejection before ending this writer transaction
                    return job_id
                if managed_continuation is not None:
                    from copy import deepcopy

                    from shine.comfy.workflow import graph_hash
                    from shine.comfyops.handoff_activation import _check
                    from shine.comfyops.resource_epoch import read_epoch
                    from shine.models import AppSetting

                    _, managed_instance = _check(
                        submit_db, settings, job_id, attempt_id, managed_continuation.lease_id,
                        managed_continuation.instance_id, worker_id, expected_version,
                    )
                    activation = submit_db.get(AppSetting, "renderer-activation:" + attempt_id)
                    activation_value = activation.value if activation else {}
                    if (activation_value.get("stage") != "CONFIRMED" or activation_value.get("job_id") != job_id
                            or activation_value.get("lease_id") != managed_continuation.lease_id
                            or activation_value.get("instance_sha256") != managed_instance.sha256
                            or read_epoch(submit_db) != activation_value.get("physical_release", {}).get(
                                "observed_resource_epoch")):
                        raise HTTPException(409, "COMFY_ACTIVATION_INTENT_CHANGED")
                    prepared = replace(prepared, prompt=deepcopy(prepared.prompt))
                    persisted_graph_hash = submit_db.get(JobAttempt, attempt_id).prompt_graph_sha256
                    if (graph_hash(prepared.prompt) != prepared.prompt_graph_sha256
                            or persisted_graph_hash != prepared.prompt_graph_sha256):
                        raise HTTPException(409, "COMFY_PROMPT_GRAPH_CHANGED")
                    managed_continuation.supervisor.verify_binding(
                        managed_instance.payload.get("url", ""), managed_instance.payload.get("process"),
                    )
                    _check(submit_db, settings, job_id, attempt_id, managed_continuation.lease_id,
                           managed_continuation.instance_id, worker_id, expected_version)
            except HTTPException as exc:
                if managed_continuation is not None:
                    from shine.comfyops.models import ResourceLease
                    from shine.models import AppSetting

                    if (current and current.lease_owner == worker_id and current.state_version == expected_version
                            and current.status == "SUBMITTING"):
                        resource = submit_db.get(ResourceLease, managed_continuation.lease_id)
                        if resource and resource.owner == "h3:" + job_id:
                            resource.status = "RECONCILE_REQUIRED"
                        activation = submit_db.get(AppSetting, "renderer-activation:" + attempt_id)
                        if activation and activation.value.get("stage") == "CONFIRMED":
                            activation.value = {**activation.value, "stage": "RECONCILE_REQUIRED",
                                                "last_confirmed_stage": "CONFIRMED", "reason": str(exc.detail)}
                        current.failure_code = "COMFY_ACTIVATION_RECONCILIATION_REQUIRED"
                        current.lease_owner, current.lease_expires_at = None, None
                        try_transition(current, "NEEDS_REVIEW")
                        add_audit(submit_db, "comfy-worker", "COMFY_SUBMIT_GUARD_BLOCKED", "job", current.id,
                                  {"attempt_id": attempt_id, "reason": str(exc.detail), "resubmitted": False},
                                  project_id=current.project_id)
                    submit_db.commit()
                    return job_id
                current.failure_code = str(exc.detail)
                current.lease_owner = None
                current.lease_expires_at = None
                submit_db.get(JobAttempt, attempt_id).status = "FAILED"
                transition(current, "NEEDS_REVIEW")
                submit_db.commit()
                return job_id
            submission: ComfySubmission = service.submit_prepared(prepared, request)
            submit_db.commit()
    except ComfySubmissionUncertain as exc:
        with session_factory.begin() as db:
            guarded = _guard_claimed_job(
                db,
                job_id=job_id,
                expected_state_version=expected_version,
                worker_id=worker_id,
                attempt_id=attempt_id,
                settings=settings,
            )
            if guarded is None:
                return job_id
            job, attempt = guarded
            job.failure_code = exc.code
            job.lease_owner = None
            job.lease_expires_at = None
            attempt.status = "ACK_UNCERTAIN"
            attempt.failure_code = exc.code
            attempt.reconciliation_state = "submission_unknown"
            transition(job, "NEEDS_REVIEW")
            db.add(
                ExternalJobReconciliation(
                    job_id=job.id,
                    provider_job_id=None,
                    action="submit_uncertain",
                    result="requires_reconciliation",
                    evidence={"code": exc.code},
                )
            )
            add_audit(
                db,
                "orchestrator",
                "SUBMISSION_REQUIRES_RECONCILIATION",
                "job",
                job.id,
                {"resubmitted": False},
                project_id=job.project_id,
            )
        return job_id
    except ComfyError as exc:
        with session_factory.begin() as db:
            guarded = _guard_claimed_job(
                db,
                job_id=job_id,
                expected_state_version=expected_version,
                worker_id=worker_id,
                attempt_id=attempt_id,
                settings=settings,
            )
            if guarded is None:
                return job_id
            job, attempt = guarded
            job.failure_code = exc.code
            job.lease_owner = None
            job.lease_expires_at = None
            attempt.status = "FAILED"
            attempt.failure_code = exc.code
            attempt.reconciliation_state = "rejected_before_queue"
            transition(job, "REPAIR_PENDING")
            add_audit(
                db,
                "comfy-worker",
                "COMFY_SUBMIT_FAILED",
                "job",
                job.id,
                {"code": exc.code, "details": exc.details},
                project_id=job.project_id,
            )
        return job_id

    with session_factory.begin() as db:
        guarded = _guard_claimed_job(
            db,
            job_id=job_id,
            expected_state_version=expected_version,
            worker_id=worker_id,
            attempt_id=attempt_id,
            settings=settings,
        )
        if guarded is None:
            # The external acknowledgement is evidence even if another worker
            # now owns the claim; never overwrite its job/attempt state.
            if db.get(Job, job_id):
                db.add(ExternalJobReconciliation(
                    job_id=job_id, provider_job_id=submission.prompt_id,
                    action="ack_after_claim_lost", result="requires_reconciliation",
                    evidence={"attempt_id": attempt_id, "prompt_graph_sha256": submission.prompt_graph_sha256,
                              "resubmitted": False},
                ))
            return job_id
        job, attempt = guarded
        job.provider_job_id = submission.prompt_id
        job.heartbeat_at = utcnow()
        job.compiled_prompt_sha256 = job.request.get("prompt_sha256")
        attempt.provider_job_id = submission.prompt_id
        attempt.prompt_graph_sha256 = submission.prompt_graph_sha256
        attempt.submit_ack_at = utcnow()
        attempt.status = "ACKNOWLEDGED"
        transition(job, "RUNNING")
        ack_state_version = job.state_version
        add_audit(
            db,
            "comfy-worker",
            "COMFY_ACKNOWLEDGED",
            "job",
            job.id,
            {
                "prompt_id": submission.prompt_id,
                "workflow_hash": submission.workflow_hash,
                "uploaded_references": submission.uploaded_references,
            },
            project_id=job.project_id,
        )
    if managed_guard is not None:
        managed_guard.check()
    if not heartbeat(session_factory, worker_id, current_job_id=job_id, phase="rendering",
                     expected_state_version=ack_state_version, attempt_id=attempt_id,
                     lease_seconds=settings.worker_lease_seconds):
        return job_id
    deadline = time.monotonic() + 600
    missing_since: float | None = None
    while True:
        if managed_guard is not None:
            managed_guard.check()
        result = service.poll(submission.prompt_id)
        if result.status == "not_found":
            # ComfyUI removes a finished prompt from the queue before its history
            # record becomes readable, so a single observation of "no record"
            # is not proof that the external task is gone. Keep reconciling for a
            # short grace window before declaring the submission lost.
            if missing_since is None:
                missing_since = time.monotonic()
            elif time.monotonic() - missing_since >= EXTERNAL_MISSING_GRACE_SECONDS:
                break
        elif result.status not in {"running", "pending"}:
            break
        else:
            missing_since = None
        if not heartbeat(session_factory, worker_id, current_job_id=job_id, phase="rendering",
                         expected_state_version=ack_state_version, attempt_id=attempt_id,
                         lease_seconds=settings.worker_lease_seconds):
            return job_id
        if time.monotonic() >= deadline:
            result = ComfyResult(status="timeout", error_code="COMFY_TIMEOUT")
            break
        time.sleep(1.0)
    if result.status in {"running", "pending", "timeout"}:
        with session_factory.begin() as db:
            job = db.get(Job, job_id)
            if (
                job
                and job.status == "RUNNING"
                and job.lease_owner == worker_id
                and job.state_version == ack_state_version
            ):
                job.heartbeat_at = utcnow()
                job.lease_expires_at = utcnow() + timedelta(seconds=settings.worker_lease_seconds)
                add_audit(
                    db,
                    "comfy-worker",
                    "COMFY_RENDER_STILL_RUNNING",
                    "job",
                    job.id,
                    {"prompt_id": submission.prompt_id},
                    project_id=job.project_id,
                )
        return job_id
    if result.status != "completed":
        with session_factory.begin() as db:
            guarded = _guard_claimed_job(
                db,
                job_id=job_id,
                expected_state_version=ack_state_version,
                worker_id=worker_id,
                attempt_id=attempt_id,
                allowed_status=("RUNNING", "QC_PENDING"),
                settings=settings,
            )
            if guarded is None:
                return job_id
            job, attempt = guarded
            if job.status == "RUNNING":
                transition(job, "QC_PENDING")
            job.failure_code = result.error_code or "COMFY_JOB_NOT_FOUND"
            job.lease_owner = None
            job.lease_expires_at = None
            attempt.status = "FAILED"
            attempt.failure_code = job.failure_code
            attempt.reconciliation_state = result.status
            transition(job, "NEEDS_REVIEW")
            db.add(
                ExternalJobReconciliation(
                    job_id=job.id,
                    provider_job_id=submission.prompt_id,
                    action="poll_after_ack",
                    result=result.status,
                    evidence={"output": result.output_filename, "error_code": result.error_code},
                )
            )
            add_audit(
                db,
                "comfy-worker",
                "COMFY_OUTPUT_RECONCILIATION_REQUIRED",
                "job",
                job.id,
                {"result": result.status, "error_code": result.error_code},
                project_id=job.project_id,
            )
        return job_id

    if managed_guard is not None:
        managed_guard.stop()
    return _finalize_comfy_attempt(
        session_factory,
        job_id=job_id,
        attempt_id=attempt_id,
        expected_state_version=ack_state_version,
        worker_id=worker_id,
        settings=settings,
        service=service,
        prompt_id=submission.prompt_id,
        semantic_reviewer=semantic_reviewer,
        asr_reviewer=asr_reviewer,
        managed_handoff=managed_continuation.next if managed_continuation else None,
    )


def _take_recovery_lease(db: Session, job: Job, worker_id: str, settings: Settings) -> int | None:
    """Only one worker may take over an expired job.

    Returns the new ``state_version`` of the job when the lease was taken.
    """
    now = utcnow()
    expires = now + timedelta(seconds=settings.worker_lease_seconds)
    result = db.execute(
        update(Job)
        .where(
            Job.id == job.id,
            Job.lease_expires_at.is_not(None),
            Job.lease_expires_at < now,
        )
        .values(
            lease_owner=worker_id,
            lease_expires_at=expires,
            heartbeat_at=now,
            state_version=job.state_version + 1,
        ),
        execution_options={"synchronize_session": False},
    )
    if result.rowcount != 1:
        return None
    job.lease_owner = worker_id
    job.lease_expires_at = expires
    job.heartbeat_at = now
    job.state_version = job.state_version + 1
    return job.state_version


def _claim_uncertain_job(db: Session, job: Job, worker_id: str, settings: Settings) -> int | None:
    """Claim a job whose submission outcome is unknown.

    The conditional update is a compare-and-set on the job status, so only one
    worker can ever reconcile a given uncertain submission.
    """
    now = utcnow()
    expires = now + timedelta(seconds=settings.worker_lease_seconds)
    result = db.execute(
        update(Job)
        .where(
            Job.id == job.id,
            Job.status == "NEEDS_REVIEW",
            Job.failure_code == "COMFY_SUBMISSION_OUTCOME_UNKNOWN",
        )
        .values(
            status="RUNNING",
            lease_owner=worker_id,
            lease_expires_at=expires,
            heartbeat_at=now,
            state_version=job.state_version + 1,
        ),
        execution_options={"synchronize_session": False},
    )
    if result.rowcount != 1:
        return None
    job.status = "RUNNING"
    job.lease_owner = worker_id
    job.lease_expires_at = expires
    job.heartbeat_at = now
    job.state_version = job.state_version + 1
    return job.state_version


def _claim_stranded_job(db: Session, job: Job, worker_id: str, settings: Settings) -> int | None:
    """Claim a job whose external outcome became unknown after the worker stopped.

    A job can reach ``NEEDS_REVIEW`` with ``COMFY_JOB_NOT_FOUND`` while its
    ComfyUI task actually completed: the worker observed the queue/history gap
    that ComfyUI opens between finishing a prompt and publishing its history
    record. Such a job must be reconciled against the external history rather
    than discarded or resubmitted. The compare-and-set keeps this exclusive to
    one worker.
    """
    now = utcnow()
    expires = now + timedelta(seconds=settings.worker_lease_seconds)
    result = db.execute(
        update(Job)
        .where(
            Job.id == job.id,
            Job.status == "NEEDS_REVIEW",
            Job.failure_code.in_(list(RECONCILABLE_FAILURE_CODES)),
            Job.provider_job_id.is_not(None),
        )
        .values(
            lease_owner=worker_id,
            lease_expires_at=expires,
            heartbeat_at=now,
            state_version=job.state_version + 1,
        ),
        execution_options={"synchronize_session": False},
    )
    if result.rowcount != 1:
        return None
    job.lease_owner = worker_id
    job.lease_expires_at = expires
    job.heartbeat_at = now
    job.state_version = job.state_version + 1
    return job.state_version


def _live_handoff_reservation(db, job, lease, now):
    from shine.comfyops.installer import process_identity
    from shine.models import AppSetting

    row = db.get(AppSetting, "handoff-reservation:" + job.id) if job else None
    value = row.value if row and isinstance(row.value, dict) else {}
    if (not job or job.status != "QUEUED" or not lease or lease.status != "ACTIVE"
            or lease.owner != "h3:" + job.id or lease.expires_at <= now or value.get("stage") != "RESERVED"
            or value.get("job_id") != job.id or value.get("lease_id") != lease.id
            or type(value.get("worker_pid")) is not int or not value.get("worker_birth")):
        return False
    try:
        return process_identity(value["worker_pid"]) == value["worker_birth"]
    except (OSError, ValueError):
        return False


def recover_expired_jobs(
    session_factory,
    worker_id: str,
    *,
    settings: Settings | None = None,
    comfy_client=None,
    semantic_reviewer=None,
    asr_reviewer=None,
) -> int:
    """Resolve expired external submissions conservatively; never resubmit blindly."""
    settings = settings or Settings.from_env()
    from shine.comfyops.handoff_startup import reconcile_managed_startups

    # An interrupted managed startup is reconciled before any other job may
    # claim the GPU: only a provably owned process may be stopped.
    reconcile_managed_startups(session_factory, settings)
    with session_factory.begin() as db:
        now = utcnow()
        job_ids = list(
            db.scalars(
                select(Job.id).where(
                    (
                        Job.status.in_(list(ACTIVE_STATUSES))
                        & Job.lease_expires_at.is_not(None)
                        & (Job.lease_expires_at < now)
                    )
                    | ((Job.status == "NEEDS_REVIEW") & (Job.failure_code == "COMFY_SUBMISSION_OUTCOME_UNKNOWN"))
                    | (
                        (Job.status == "NEEDS_REVIEW")
                        & (Job.failure_code.in_(list(RECONCILABLE_FAILURE_CODES)))
                        & Job.provider_job_id.is_not(None)
                    )
                )
            ).all()
        )
        from shine.comfyops.models import ResourceLease
        from shine.models import AppSetting

        for row in db.scalars(select(AppSetting).where(AppSetting.key.like("handoff-reservation:%"))):
            value = row.value if isinstance(row.value, dict) else {}
            reservation_job_id = row.key.removeprefix("handoff-reservation:")
            job = db.get(Job, reservation_job_id)
            if not job or job.status != "QUEUED":
                continue
            lease = db.get(ResourceLease, value.get("lease_id")) if value.get("lease_id") else None
            if not _live_handoff_reservation(db, job, lease, now):
                job_ids.append(job.id)
    recovered = 0
    for job_id in job_ids:
        from shine.comfyops.models import ResourceLease

        with session_factory() as check_db:
            candidate = check_db.get(Job, job_id)
            if (
                candidate
                and candidate.failure_code == "QC_RECHECK_REQUESTED"
                and check_db.scalar(
                    select(ResourceLease.id)
                    .where(
                        ResourceLease.status.in_(["ACTIVE", "RECONCILE_REQUIRED"]),
                        ResourceLease.owner != "h3:" + job_id,
                    )
                    .limit(1)
                )
            ):
                continue
        recovered += _recover_one_job(
            session_factory,
            worker_id,
            job_id,
            settings=settings,
            comfy_client=comfy_client,
            semantic_reviewer=semantic_reviewer,
            asr_reviewer=asr_reviewer,
        )
    from shine.comfyops.models import ResourceLease

    with session_factory.begin() as db:
        # An exclusive GPU lease blocks every later job until it is released.
        # A lease whose job already finished must be reconciled against the
        # renderer's real memory state, even when the job itself needs no
        # further recovery, otherwise one stale lease deadlocks the queue.
        release_client = comfy_client or _renderer_client(settings, max_retries=0)
        leases = list(
            db.scalars(select(ResourceLease).where(ResourceLease.status.in_(["ACTIVE", "RECONCILE_REQUIRED"])))
        )
        for lease in leases:
            if lease.status == "RECONCILE_REQUIRED":
                continue  # Requires explicit physical reconciliation, not an HTTP unload retry.
            owner_job_id = lease.owner.removeprefix("h3:")
            job = db.get(Job, owner_job_id)
            if lease.owner.startswith("h3:"):
                if _live_handoff_reservation(db, job, lease, utcnow()):
                    continue
                if not job or job.status not in ACTIVE_STATUSES or lease.expires_at <= utcnow():
                    lease.status = "RECONCILE_REQUIRED"
                continue  # Renderer unload alone cannot establish physical process release.
            if job is not None and job.status in ACTIVE_STATUSES:
                continue
            attempt = _latest_attempt(db, owner_job_id) if job is not None else None
            if attempt is None or attempt.status != "COMPLETED":
                continue
            try:
                release_client.free_models()
            except ComfyUIError as exc:
                if "INSTANCE_BUSY" in str(getattr(exc, "detail", "") or ""):
                    # The renderer is busy with another task; keep the lease
                    # and try again later instead of declaring a release.
                    continue
                lease.status = "RECONCILE_REQUIRED"
            except Exception:
                lease.status = "RECONCILE_REQUIRED"
            else:
                lease.status = "RELEASED"
    return recovered


def _block_interrupted_handoff(session_factory, job_id):
    """Preserve unresolved process side effects before any renderer recovery call."""
    from sqlalchemy import text

    from shine.comfyops.models import ResourceLease
    from shine.comfyops.scheduler import ResourceScheduler
    from shine.models import AppSetting

    with session_factory.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        job = db.get(Job, job_id)
        if not job or (job.lease_expires_at and job.lease_expires_at > utcnow()):
            return False
        attempt = _latest_attempt(db, job_id)
        journal = db.get(AppSetting, "handoff:" + attempt.id) if attempt else None
        activation, reservation = False, False
        if not journal and attempt:
            journal = db.get(AppSetting, "renderer-activation:" + attempt.id)
            activation = journal is not None
        if not journal:
            journal = db.get(AppSetting, "handoff-reservation:" + job_id)
            reservation = journal is not None
        if not journal:
            return False
        raw_value = journal.value
        value = raw_value if isinstance(raw_value, dict) else {"original_value": raw_value}
        binding_mismatch = value.get("job_id") != job_id
        if not reservation and (binding_mismatch or not attempt or value.get("attempt_id") != attempt.id):
            raise HTTPException(409, "HANDOFF_INTENT_BINDING_CHANGED")
        lease = db.scalar(select(ResourceLease).where(ResourceLease.owner == "h3:" + job_id))
        lease_mismatch = reservation and value.get("lease_id") != (lease.id if lease else None)
        if reservation and not lease_mismatch and _live_handoff_reservation(db, job, lease, utcnow()):
            return False
        if lease is None:
            lease = ResourceScheduler().enqueue(db, "h3:" + job_id, 16384, True, 100)
        lease.status = "RECONCILE_REQUIRED"
        if value.get("stage") != "RECONCILE_REQUIRED" or binding_mismatch or lease_mismatch:
            journal.value = {**value, "stage": "RECONCILE_REQUIRED", "last_confirmed_stage": value.get("stage"),
                             "reason": ("RESERVATION_JOB_BINDING_CHANGED" if binding_mismatch else
                                        "RESERVATION_LEASE_BINDING_CHANGED" if lease_mismatch else
                                        "WORKER_LEASE_EXPIRED_DURING_HANDOFF"),
                             **({"recovery_job_id": job_id} if binding_mismatch else {}),
                             **({"recovery_lease_id": lease.id} if lease_mismatch else {})}
        if job.status != "NEEDS_REVIEW":
            try_transition(job, "NEEDS_REVIEW")
        job.failure_code = ("COMFY_ACTIVATION_RECONCILIATION_REQUIRED" if activation
                            else "COMFY_HANDOFF_RECONCILIATION_REQUIRED")
        job.lease_owner = None
        job.lease_expires_at = None
        add_audit(db, "orchestrator", "COMFY_HANDOFF_RECOVERY_BLOCKED", "job", job.id,
                  {"attempt_id": attempt.id if attempt else None, "last_stage": value.get("stage"),
                   "resubmitted": False},
                  project_id=job.project_id)
        return True


def _recover_one_job(
    session_factory,
    worker_id: str,
    job_id: str,
    *,
    settings: Settings,
    comfy_client,
    semantic_reviewer=None,
    asr_reviewer=None,
) -> int:
    if _block_interrupted_handoff(session_factory, job_id):
        return 1
    finalize: dict[str, Any] | None = None
    with session_factory.begin() as db:
        job = db.get(Job, job_id)
        if job is None:
            return 0
        attempt = _latest_attempt(db, job.id)
        from shine.comfyops.preproduction import enforce_job

        try:
            enforce_job(db, job, settings)
        except HTTPException as exc:
            if job.status != "NEEDS_REVIEW":
                try_transition(job, "NEEDS_REVIEW")
            job.failure_code = str(exc.detail)
            job.lease_owner = None
            job.lease_expires_at = None
            if attempt:
                attempt.status = "NEEDS_REVIEW"
            return 1
        if not _shot_is_current(db, job):
            _discard_stale_result(db, job, attempt, reason="SHOT_REVISION_SUPERSEDED", worker_id=worker_id)
            return 1
        if job.failure_code == "QC_RECHECK_REQUESTED":
            # This is an explicitly requested review of a collected artifact,
            # not reconciliation of an uncertain submission. ComfyUI history
            # may have disappeared after an intentional process restart.
            from shine.models import AppSetting
            from shine.operator_review import context

            pin = db.get(AppSetting, "qc-recheck:" + job.id)
            try:
                _, checked_attempt, artifact, _, _, _ = context(db, settings, job.id)
                binding = pin.value if pin else {}
                if (not binding.get("request_id") or binding.get("attempt_id") != checked_attempt.id
                        or binding.get("artifact_id") != artifact.id
                        or binding.get("artifact_sha256") != artifact.sha256):
                    raise ValueError("recheck binding changed")
            except (HTTPException, ValueError):
                job.failure_code = "QC_RECHECK_INPUT_CHANGED"
                add_audit(db, "orchestrator", "QC_RECHECK_INPUT_CHANGED", "job", job.id,
                          {"render_resubmission": False}, project_id=job.project_id)
                return 1
            if _claim_stranded_job(db, job, worker_id, settings) is None:
                return 0
            transition(job, "RUNNING")
            client = comfy_client or _renderer_client(settings)
            finalize = {
                "job_id": job.id,
                "attempt_id": checked_attempt.id,
                "expected_state_version": job.state_version,
                "prompt_id": job.provider_job_id,
                "service": ComfyUIRenderService(client, settings.data_dir),
            }
        elif job.provider == "comfyui" and (
            job.provider_job_id
            or job.failure_code == "COMFY_SUBMISSION_OUTCOME_UNKNOWN"
            or (attempt is not None and attempt.prompt_graph_sha256)
        ):
            handled, finalize = _recover_comfy_job(
                db,
                job,
                attempt,
                worker_id=worker_id,
                settings=settings,
                comfy_client=comfy_client,
            )
            if handled and finalize is None:
                return 1
        elif job.status == "SUBMITTING" or not job.provider_job_id:
            if job.status != "NEEDS_REVIEW":
                transition(job, "NEEDS_REVIEW")
            job.failure_code = "SUBMISSION_OUTCOME_UNKNOWN"
            if attempt:
                attempt.status = "ACK_UNCERTAIN"
                attempt.failure_code = job.failure_code
            add_audit(
                db,
                "orchestrator",
                "SUBMISSION_REQUIRES_RECONCILIATION",
                "job",
                job.id,
                {"resubmitted": False},
                project_id=job.project_id,
            )
            return 1
        elif job.status == "RUNNING":
            transition(job, "QC_PENDING")
            if attempt:
                attempt.status = "ACKNOWLEDGED"
            job.lease_owner = None
            job.lease_expires_at = None
            job.heartbeat_at = utcnow()
            if job.status == "QC_PENDING":
                if not attempt:
                    transition(job, "NEEDS_REVIEW")
                    job.failure_code = "ATTEMPT_RECORD_MISSING"
                else:
                    report = {
                        "simulated": True,
                        "video_created": False,
                        "artifact_kind": "none",
                        "recovered_after_acknowledged_submission": True,
                    }
                    _record_mock_qc(db, job, attempt, report)
                    job.last_qc_result = "passed"
                    job.quality_verification = VERIFICATION_MOCK
                    transition(job, "PASSED")
                    add_audit(
                        db,
                        "mock-worker",
                        "MOCK_QC_PASSED",
                        "job",
                        job.id,
                        report,
                        project_id=job.project_id,
                    )
            return 1
    if finalize:
        service = finalize.pop("service")
        _finalize_comfy_attempt(
            session_factory,
            worker_id=worker_id,
            settings=settings,
            service=service,
            semantic_reviewer=semantic_reviewer,
            asr_reviewer=asr_reviewer,
            **finalize,
        )
        return 1
    return 0


def _recover_comfy_job(
    db: Session,
    job: Job,
    attempt: JobAttempt | None,
    *,
    worker_id: str,
    settings: Settings,
    comfy_client,
) -> tuple[bool, dict[str, Any] | None]:
    """Reconcile an external ComfyUI task and decide what happens next.

    Returns ``(handled, finalisation_descriptor)``. The caller commits this
    transaction before the descriptor is executed, so the finalisation can
    open its own transaction.
    """

    client = comfy_client or _renderer_client(settings)
    service = ComfyUIRenderService(client, settings.data_dir)
    prompt_id = job.provider_job_id or (attempt.provider_job_id if attempt else None)
    graph_hash = attempt.prompt_graph_sha256 if attempt else None
    uncertain = job.failure_code == "COMFY_SUBMISSION_OUTCOME_UNKNOWN"
    if uncertain:
        claimed = _claim_uncertain_job(db, job, worker_id, settings)
        if claimed is None:
            return False, None
        job.failure_code = None
    found_id, status = service.find_submission(
        prompt_id=prompt_id,
        prompt_graph_sha256=graph_hash,
    )
    job.heartbeat_at = utcnow()
    if status in {"running", "pending"}:
        job.lease_expires_at = utcnow() + timedelta(seconds=settings.worker_lease_seconds)
        db.add(
            ExternalJobReconciliation(
                job_id=job.id,
                provider_job_id=found_id or prompt_id,
                action="recover_running",
                result=status,
                evidence={"prompt_id": found_id or prompt_id},
            )
        )
        add_audit(
            db,
            "orchestrator",
            "COMFY_RECONCILED_RUNNING",
            "job",
            job.id,
            {"status": status, "prompt_id": found_id or prompt_id},
            project_id=job.project_id,
        )
        return True, None
    if status == "completed" and found_id:
        if attempt is None:
            job.failure_code = "ATTEMPT_RECORD_MISSING"
            try_transition(job, "NEEDS_REVIEW")
            return True, None
        if not uncertain:
            if job.status == "NEEDS_REVIEW" and job.failure_code in RECONCILABLE_FAILURE_CODES:
                # The job was stranded by the queue/history completion window.
                # Re-open it so the collected media can flow into QC.
                if _claim_stranded_job(db, job, worker_id, settings) is None:
                    return False, None
                if job.status == "NEEDS_REVIEW":
                    transition(job, "RUNNING")
            elif _take_recovery_lease(db, job, worker_id, settings) is None:
                return False, None
        attempt.provider_job_id = found_id
        attempt.reconciliation_state = "history_found"
        attempt.reconciled_at = utcnow()
        if job.status == "SUBMITTING":
            transition(job, "RUNNING")
        if job.status == "RUNNING":
            transition(job, "QC_PENDING")
        state_version = job.state_version
        db.add(
            ExternalJobReconciliation(
                job_id=job.id,
                provider_job_id=found_id,
                action="recover_history",
                result=status,
                evidence={"prompt_id": found_id},
            )
        )
        add_audit(
            db,
            "orchestrator",
            "COMFY_RECONCILED_HISTORY",
            "job",
            job.id,
            {"status": status, "prompt_id": found_id},
            project_id=job.project_id,
        )
        return True, {
            "job_id": job.id,
            "attempt_id": attempt.id,
            "expected_state_version": state_version,
            "prompt_id": found_id,
            "service": service,
        }
    if status == "completed_without_output":
        job.failure_code = "COMFY_OUTPUT_MISSING"
        job.lease_owner = None
        job.lease_expires_at = None
        if job.status in set(ACTIVE_STATUSES):
            transition(job, "NEEDS_REVIEW")
        if attempt:
            attempt.reconciliation_state = "completed_without_output"
            attempt.reconciled_at = utcnow()
        db.add(
            ExternalJobReconciliation(
                job_id=job.id,
                provider_job_id=found_id or prompt_id,
                action="recover_without_output",
                result=status,
                evidence={},
            )
        )
        return True, None
    if status.startswith("unavailable:"):
        job.failure_code = "RECONCILIATION_UNAVAILABLE"
        job.lease_owner = None
        job.lease_expires_at = None
        if job.status in set(ACTIVE_STATUSES):
            transition(job, "NEEDS_REVIEW")
        if attempt:
            attempt.status = "ACK_UNCERTAIN"
            attempt.failure_code = job.failure_code
        db.add(
            ExternalJobReconciliation(
                job_id=job.id,
                provider_job_id=prompt_id,
                action="recover_unavailable",
                result=status,
                evidence={"resubmitted": False},
            )
        )
        return True, None
    # not_found: the external job may never have been created or history was cleared.
    job.failure_code = "EXTERNAL_JOB_NOT_FOUND"
    job.lease_owner = None
    job.lease_expires_at = None
    if job.status in set(ACTIVE_STATUSES):
        transition(job, "NEEDS_REVIEW")
    if attempt:
        attempt.status = "ACK_UNCERTAIN"
        attempt.failure_code = job.failure_code
        attempt.reconciliation_state = "not_found"
        attempt.reconciled_at = utcnow()
    db.add(
        ExternalJobReconciliation(
            job_id=job.id,
            provider_job_id=prompt_id,
            action="recover_not_found",
            result="not_found",
            evidence={"resubmitted": False},
        )
    )
    add_audit(
        db,
        "orchestrator",
        "COMFY_RECONCILIATION_UNCERTAIN",
        "job",
        job.id,
        {"resubmitted": False},
        project_id=job.project_id,
    )
    return True, None


def run_configured_series_once(
    session_factory,
    worker_id,
    *,
    settings,
    comfy_client=None,
    semantic_reviewer=None,
    asr_reviewer=None,
):
    """The resident Worker's production entry for real H3 renders.

    This is the scheduling entry the Web workbench actually reaches: a person
    creates a production series in the browser, and the resident Worker claims
    it, drives every shot of it behind one managed renderer, and releases the
    renderer and the exclusive GPU lease exactly once at the end.

    It returns ``None`` when this worker must not run a managed production
    right now — mock mode, offline simulation, no managed recipe, no reviewer
    route, or nothing claimable — so the caller falls back to the ordinary
    single-shot path instead of silently doing nothing.
    """
    from shine.comfyops.production_series import (
        claim_series,
        reconcile_series,
        run_production_series,
    )

    if settings.render_provider != "comfyui" or settings.offline_simulation:
        return None
    try:
        recipe = _managed_recipe(settings)
    except HTTPException:
        return None
    if recipe is None:
        return None
    if not (semantic_reviewer or asr_reviewer):
        return None
    try:
        review_configs = _managed_review_specs(recipe, semantic_reviewer, asr_reviewer)
    except HTTPException:
        return None
    reconcile_series(session_factory, worker_id)
    series = claim_series(session_factory, worker_id)
    if series is not None:
        logger.info("production_series_claimed series_id=%s worker_id=%s", series.id, worker_id)
        result = run_production_series(
            session_factory, worker_id, series.id,
            settings=settings, recipe=recipe, review_configs=review_configs,
            comfy_client=comfy_client,
            semantic_reviewer=semantic_reviewer, asr_reviewer=asr_reviewer,
        )
        logger.info("production_series_finished series_id=%s result=%s", series.id, result.get("status"))
        return result.get("status")
    # No series is waiting: the ordinary queue still owns real H3 work.  It is
    # driven behind the same managed renderer, and the renderer is released at
    # the end instead of being held forever after the first shot.
    from shine.worker import _run_managed_loop

    shots = _run_managed_loop(
        session_factory, worker_id,
        settings=settings, recipe=recipe, review_configs=review_configs, max_shots=8,
        comfy_service=None, comfy_client=comfy_client,
        semantic_reviewer=semantic_reviewer, asr_reviewer=asr_reviewer,
        first_job_id=None, next_job_id=None, exclude_series_jobs=True,
    )
    if not shots:
        return None
    logger.info("production_queue_finished worker_id=%s shots=%s", worker_id, len(shots))
    return "queue"


def run_configured_once(session_factory, provider, worker_id, *, settings, comfy_client=None):
    from shine.quality.runtime import configured_reviewers

    semantic, asr = configured_reviewers(session_factory, settings)
    recovered = recover_expired_jobs(
        session_factory,
        worker_id,
        settings=settings,
        comfy_client=comfy_client,
        semantic_reviewer=semantic,
        asr_reviewer=asr,
    )
    if recovered:
        return "reconciled"
    series = run_configured_series_once(
        session_factory,
        worker_id,
        settings=settings,
        comfy_client=comfy_client,
        semantic_reviewer=semantic,
        asr_reviewer=asr,
    )
    if series is not None:
        return f"series:{series}"
    return run_one(
        session_factory,
        provider,
        worker_id,
        settings=settings,
        comfy_client=comfy_client,
        semantic_reviewer=semantic,
        asr_reviewer=asr,
    )


def serve(poll_seconds: float = 0.5) -> None:
    settings = Settings.from_env()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    worker_id = f"worker-{uuid4().hex[:12]}"
    provider = MockH3Provider()
    comfy_client = None
    if settings.render_provider == "comfyui":

        comfy_client = _renderer_client(settings)
    heartbeat(session_factory, worker_id, status="online", phase="starting")
    try:
        while True:
            claimed = run_configured_once(
                session_factory,
                provider,
                worker_id,
                settings=settings,
                comfy_client=comfy_client,
            )
            if claimed is None:
                heartbeat(session_factory, worker_id, status="online", phase="idle")
                time.sleep(poll_seconds)
    except KeyboardInterrupt:
        heartbeat(session_factory, worker_id, status="stopped", phase="stopped")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="Run Shine's standalone local worker")
    parser.add_argument("--once", action="store_true", help="Process at most one queued job and exit")
    args = parser.parse_args()
    settings = Settings.from_env()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    worker_id = f"worker-{uuid4().hex[:12]}"
    provider = MockH3Provider()
    comfy_client = None
    if settings.render_provider == "comfyui":

        comfy_client = _renderer_client(settings)
    heartbeat(session_factory, worker_id, status="online", phase="starting")
    if args.once:
        run_configured_once(session_factory, provider, worker_id, settings=settings, comfy_client=comfy_client)
        heartbeat(session_factory, worker_id, status="stopped", phase="stopped")
    else:
        serve()


if __name__ == "__main__":
    main()
