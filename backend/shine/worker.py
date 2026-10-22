from __future__ import annotations

import argparse
import logging
import os
import time
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from shine.comfy.client import ComfyError, ComfySubmissionUncertain
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
    ReviewContext,
    Transcript,
    dialogue_findings,
)
from shine.quality.technical import TechnicalExpectations, TechnicalQC
from shine.schemas import (
    FusedQCResult,
    HardFailure,
    ReferenceManifestItem,
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
) -> None:
    try:
        with session_factory.begin() as db:
            _register_worker(db, worker_id)
            instance = db.get(WorkerInstance, worker_id)
            instance.status = status
            instance.current_job_id = current_job_id
            instance.current_phase = phase
            if current_job_id:
                from shine.comfyops.models import ResourceLease

                resource = db.scalar(select(ResourceLease).where(ResourceLease.owner == "h3:" + current_job_id))
                if resource and resource.status == "ACTIVE":
                    resource.heartbeat_at = utcnow()
                    resource.expires_at = utcnow() + timedelta(seconds=60)
            instance.last_heartbeat_at = utcnow()
            instance.processed_count += processed_delta
            if error:
                instance.last_error = error[:2000]
    except Exception:  # pragma: no cover - the heartbeat must not kill the worker
        logger.exception("worker_heartbeat_failed")


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
    existing = db.scalar(select(QCReport).where(QCReport.job_id == job.id, QCReport.attempt_id == attempt.id))
    if existing is not None:
        # The attempt was already checked. Recovery must not duplicate the report.
        existing.result = fused.result
        existing.artifact_id = artifact.id if artifact else existing.artifact_id
        existing.is_mock = fused.is_mock
        existing.report = fused.model_dump(mode="json")
        return
    db.add(
        QCReport(
            job_id=job.id,
            attempt_id=attempt.id,
            artifact_id=artifact.id if artifact else None,
            result=fused.result,
            is_mock=fused.is_mock,
            reviewer="quality-gates",
            report=fused.model_dump(mode="json"),
        )
    )


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
) -> tuple[list[Any], str | None]:
    """Run the semantic reviewer, classifying every failure mode separately."""
    if reviewer is None:
        return [], None
    frames: list[Path] = []
    if getattr(reviewer, "is_mock", False) is False:
        frames = extract_frames(
            artifact_path,
            settings.data_dir / "evidence" / project_id / job_id,
            ffmpeg_path=settings.ffmpeg_path,
        )
    context = ReviewContext(
        job_id=job_id,
        shot_spec=spec,
        style=style,
        artifact_path=artifact_path,
        artifact_sha256=artifact_sha256,
        frame_paths=tuple(frames),
        transcript=transcript,
        remote_consent=remote_consent,
    )
    try:
        result = reviewer.review(context)
    except PermissionError as exc:
        return [], f"SEMANTIC_REVIEW_FAILED: {exc}"
    except TimeoutError as exc:
        return [], f"SEMANTIC_REVIEW_TIMEOUT: {exc}"
    except ValueError as exc:
        return [], f"SEMANTIC_REVIEW_INVALID_JSON: {exc}"
    except Exception as exc:  # pragma: no cover - defensive
        return [], f"SEMANTIC_REVIEW_FAILED: {type(exc).__name__}"
    if result is None:
        return [], "SEMANTIC_REVIEW_INVALID_JSON: the reviewer returned no result"
    return [result], None


def _transcript_review(
    *,
    asr,
    artifact_path: Path,
    spec: ShotSpec,
) -> tuple[Transcript | None, list[Any] | None, str | None]:
    if asr is None:
        return None, None, None
    try:
        transcript = asr.transcribe(artifact_path)
    except TimeoutError as exc:
        return None, None, f"ASR_FAILED: {exc}"
    except Exception as exc:  # pragma: no cover - defensive
        return None, None, f"ASR_FAILED: {type(exc).__name__}"
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
    from shine.comfy.client import ComfyUIClient
    from shine.comfyops.models import ResourceLease
    from shine.comfyops.scheduler import ResourceScheduler

    worker_id = worker_id or f"worker-{uuid4().hex[:12]}"
    heartbeat(session_factory, worker_id, phase="claiming")
    scheduler = ResourceScheduler()
    resource_id, owner = None, None
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
        )
    finally:
        if resource_id:
            with session_factory.begin() as resource_db:
                job = resource_db.get(Job, job_id)
                lease = resource_db.get(ResourceLease, resource_id)
                if job and (
                    job.status in {"SUBMITTING", "RUNNING"} or job.failure_code == "COMFY_SUBMISSION_OUTCOME_UNKNOWN"
                ):
                    lease.status = "RECONCILE_REQUIRED"
                else:
                    release_client = comfy_client or (comfy_service.client if comfy_service else None)
                    if release_client is None and settings:
                        release_client = ComfyUIClient(settings.comfyui_base_url, max_retries=0)
                    try:
                        if release_client is None or not hasattr(release_client, "free_models"):
                            raise ValueError("MODEL_RELEASE_REQUIRES_LOCAL_RECONCILIATION")
                        release_client.free_models()
                    except Exception:
                        lease.status = "RECONCILE_REQUIRED"
                    else:
                        scheduler.release(resource_db, resource_id, owner, reconciled=True)


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
        )
    except Exception as exc:
        logger.exception("job_execution_failed job_id=%s", job_id)
        try:
            with session_factory.begin() as db:
                job = db.get(Job, job_id)
                attempt = db.get(JobAttempt, attempt_id)
                if job:
                    job.failure_code = "WORKER_EXCEPTION"
                    job.lease_owner = None
                    job.lease_expires_at = None
                    if job.status not in {"NEEDS_REVIEW", "FAILED"}:
                        try_transition(job, "NEEDS_REVIEW")
                    if attempt:
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
) -> str:
    heartbeat(session_factory, worker_id, current_job_id=job_id, phase="submitting")
    try:
        submission = provider.submit(idempotency_key, scenario)
    except SubmissionUncertain as exc:
        with session_factory.begin() as db:
            guarded = _guard_claimed_job(
                db, job_id=job_id, expected_state_version=expected_version, worker_id=worker_id, attempt_id=attempt_id
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
                db, job_id=job_id, expected_state_version=expected_version, worker_id=worker_id, attempt_id=attempt_id
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
            db, job_id=job_id, expected_state_version=expected_version, worker_id=worker_id, attempt_id=attempt_id
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
        if existing and existing.attempt_id != attempt_id:
            existing = None
        artifact_path: Path | None = None
        if existing:
            candidate = settings.data_dir / existing.storage_path
            if candidate.is_file():
                artifact_path = candidate

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

    technical = TechnicalQC(ffprobe_path=settings.ffprobe_path, ffmpeg_path=settings.ffmpeg_path).check(
        artifact_path,
        expectations=TechnicalExpectations(
            duration_s=request.expected_duration_s,
            expect_audio=bool(request.shot_spec.dialogue),
        ),
    )
    transcript, transcript_findings, asr_error = _transcript_review(
        asr=asr_reviewer,
        artifact_path=artifact_path,
        spec=request.shot_spec,
    )
    semantic_results, semantic_error = _semantic_review(
        reviewer=semantic_reviewer,
        job_id=job_id,
        spec=request.shot_spec,
        style=request.style_snapshot,
        artifact_path=artifact_path,
        artifact_sha256=digest,
        settings=settings,
        remote_consent=remote_consent,
        project_id=project_id,
        transcript=transcript,
    )
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
        job.quality_verification = VERIFICATION_MOCK if any(gate.is_mock for gate in gates) else VERIFICATION_REAL
        if fused.result == STATUS_PASSED:
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

    return hashlib.sha256(path.read_bytes()).hexdigest()


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
) -> str:
    settings = settings or Settings.from_env()
    client = comfy_client
    if client is None:
        from shine.comfy.client import ComfyUIClient

        client = ComfyUIClient(settings.comfyui_base_url, timeout_s=10)
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
    heartbeat(session_factory, worker_id, current_job_id=job_id, phase="submitting")
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
                db, job_id=job_id, expected_state_version=expected_version, worker_id=worker_id, attempt_id=attempt_id
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
                db, job_id=job_id, expected_state_version=expected_version, worker_id=worker_id, attempt_id=attempt_id
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

    try:
        submission: ComfySubmission = service.submit_prepared(prepared, request)
    except ComfySubmissionUncertain as exc:
        with session_factory.begin() as db:
            guarded = _guard_claimed_job(
                db, job_id=job_id, expected_state_version=expected_version, worker_id=worker_id, attempt_id=attempt_id
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
                db, job_id=job_id, expected_state_version=expected_version, worker_id=worker_id, attempt_id=attempt_id
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
            db, job_id=job_id, expected_state_version=expected_version, worker_id=worker_id, attempt_id=attempt_id
        )
        if guarded is None:
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
    heartbeat(session_factory, worker_id, current_job_id=job_id, phase="rendering")
    deadline = time.monotonic() + 600
    while True:
        result = service.poll(submission.prompt_id)
        if result.status not in {"running", "pending"}:
            break
        heartbeat(session_factory, worker_id, current_job_id=job_id, phase="rendering")
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
                )
            ).all()
        )
    recovered = 0
    for job_id in job_ids:
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
        for job_id in job_ids:
            job = db.get(Job, job_id)
            attempt = _latest_attempt(db, job_id) if job else None
            lease = db.scalar(select(ResourceLease).where(ResourceLease.owner == "h3:" + job_id))
            if lease and attempt and attempt.status == "COMPLETED" and job.status not in ACTIVE_STATUSES:
                lease.status = "RELEASED"
    return recovered


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
    finalize: dict[str, Any] | None = None
    with session_factory.begin() as db:
        job = db.get(Job, job_id)
        if job is None:
            return 0
        attempt = _latest_attempt(db, job.id)
        if not _shot_is_current(db, job):
            _discard_stale_result(db, job, attempt, reason="SHOT_REVISION_SUPERSEDED", worker_id=worker_id)
            return 1
        if job.provider == "comfyui" and (
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
    from shine.comfy.client import ComfyUIClient

    client = comfy_client or ComfyUIClient(settings.comfyui_base_url, timeout_s=10)
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
            if _take_recovery_lease(db, job, worker_id, settings) is None:
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


def serve(poll_seconds: float = 0.5) -> None:
    settings = Settings.from_env()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    worker_id = f"worker-{uuid4().hex[:12]}"
    provider = MockH3Provider()
    comfy_client = None
    if settings.render_provider == "comfyui":
        from shine.comfy.client import ComfyUIClient

        comfy_client = ComfyUIClient(settings.comfyui_base_url, timeout_s=10)
    heartbeat(session_factory, worker_id, status="online", phase="starting")
    recover_expired_jobs(session_factory, worker_id, settings=settings, comfy_client=comfy_client)
    try:
        while True:
            claimed = run_one(
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
        from shine.comfy.client import ComfyUIClient

        comfy_client = ComfyUIClient(settings.comfyui_base_url, timeout_s=10)
    heartbeat(session_factory, worker_id, status="online", phase="starting")
    recover_expired_jobs(session_factory, worker_id, settings=settings, comfy_client=comfy_client)
    if args.once:
        run_one(session_factory, provider, worker_id, settings=settings, comfy_client=comfy_client)
        heartbeat(session_factory, worker_id, status="stopped", phase="stopped")
    else:
        serve()


if __name__ == "__main__":
    main()
