from __future__ import annotations

import argparse
import logging
import os
import time
from datetime import timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from shine.comfy.client import ComfyError, ComfySubmissionUncertain
from shine.comfy.provider import ComfyResult, ComfySubmission, ComfyUIRenderService
from shine.config import Settings
from shine.db import make_engine, make_session_factory
from shine.media.artifacts import store_artifact
from shine.models import (
    Asset,
    ExternalJobReconciliation,
    Job,
    JobAttempt,
    JobDependency,
    MediaArtifact,
    Project,
    QCReport,
    RepairRecord,
    Shot,
    WorkerInstance,
    WorkflowProfile,
    utcnow,
)
from shine.providers import MockH3Provider, MockSubmission, ProviderFailure, SubmissionUncertain
from shine.quality.repair import RepairEngine
from shine.quality.semantic import fuse
from shine.quality.technical import TechnicalExpectations, TechnicalQC
from shine.schemas import (
    FusedQCResult,
    HardFailure,
    ReferenceManifestItem,
    RenderRequest,
)
from shine.services import add_audit, job_fingerprint, transition, try_transition

WORKER_VERSION = "shine-worker/2.0"
logger = logging.getLogger("shine.worker")


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


def _auto_repair(
    db: Session,
    job: Job,
    attempt: JobAttempt,
    *,
    report: FusedQCResult,
    failure_code: str,
    project_auto_enabled: bool,
    worker_id: str,
) -> bool:
    if not RepairEngine.guard_passed(job):
        return False
    decision = RepairEngine().plan(
        report,
        retries_left=max(0, job.max_semantic_retries - job.semantic_retries),
        auto_repair_enabled=project_auto_enabled,
    )
    job.last_repair_plan = decision.plan.model_dump(mode="json")
    if not decision.auto_apply:
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
            {"action": decision.plan.action.value, "category": decision.error_category},
            project_id=job.project_id,
        )
        return False
    job.semantic_retries += 1
    job.failure_code = None
    job.mock_scenario = "success" if job.provider == "mock" else job.mock_scenario
    if job.status == "REPAIR_PENDING":
        transition(job, "READY")
        transition(job, "QUEUED")
    db.add(
        RepairRecord(
            job_id=job.id,
            attempt_id=attempt.id,
            action=decision.plan.action.value,
            root_cause=decision.plan.root_cause,
            changes=list(decision.plan.changes),
            outcome="auto_queued",
        )
    )
    add_audit(
        db,
        "orchestrator",
        "AUTO_REPAIR_QUEUED",
        "job",
        job.id,
        {"action": decision.plan.action.value, "retry": job.semantic_retries},
        project_id=job.project_id,
    )
    logger.info("auto_repair_queued job_id=%s retry=%s", job.id, job.semantic_retries)
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
    }


def run_one(
    session_factory,
    provider: MockH3Provider | None = None,
    worker_id: str | None = None,
    *,
    settings: Settings | None = None,
    comfy_service: ComfyUIRenderService | None = None,
    comfy_client=None,
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
                .where(Job.status == "QUEUED", Job.paused.is_(False), Job.stale.is_(False))
                .order_by(Job.created_at, Job.id)
                .limit(1)
            )
            if not candidate:
                return None
            now = utcnow()
            lease_seconds = (settings.worker_lease_seconds if settings else 30)
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
            attempt = JobAttempt(
                job_id=job.id,
                attempt_no=attempt_no,
                request_fingerprint=job_fingerprint(job.request),
                status="SUBMIT_INTENT",
                lease_owner=worker_id,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                heartbeat_at=now,
            )
            db.add(attempt)
            db.flush()
            add_audit(
                db,
                "orchestrator",
                "SUBMIT_INTENT",
                "job",
                job.id,
                {"attempt_no": attempt_no, "provider": job.provider},
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
        profile = db.get(WorkflowProfile, job.workflow_profile_id) if job.workflow_profile_id else None
        if profile is None:
            job.failure_code = "COMFY_WORKFLOW_PROFILE_MISSING"
            job.lease_owner = None
            job.lease_expires_at = None
            attempt.status = "FAILED"
            attempt.failure_code = job.failure_code
            transition(job, "REPAIR_PENDING")
            add_audit(
                db,
                "orchestrator",
                "COMFY_WORKFLOW_MISSING",
                "job",
                job.id,
                project_id=job.project_id,
            )
            return job_id
        manifest = [ReferenceManifestItem.model_validate(item) for item in job.request.get("reference_manifest", [])]
        assets = {
            asset.id: asset
            for asset in db.scalars(
                select(Asset).where(Asset.id.in_([item.asset_id for item in manifest]))
            ).all()
        }
        request = RenderRequest.model_validate(job.request)
        project_id = job.project_id
    heartbeat(session_factory, worker_id, current_job_id=job_id, phase="submitting")
    try:
        submission: ComfySubmission = service.submit(request, profile, assets, seed=request.seed)
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
            transition(job, "REPAIR_PENDING")
            add_audit(
                db,
                "comfy-worker",
                "COMFY_SUBMIT_FAILED",
                "job",
                job.id,
                {"code": exc.code},
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
            {"prompt_id": submission.prompt_id, "workflow_hash": submission.workflow_hash},
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
            result = ComfyResult(status="timeout")
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
            job.failure_code = (
                "COMFY_OUTPUT_MISSING" if result.status == "completed_without_output" else "COMFY_JOB_NOT_FOUND"
            )
            job.lease_owner = None
            job.lease_expires_at = None
            attempt.status = "FAILED"
            attempt.failure_code = job.failure_code
            transition(job, "NEEDS_REVIEW")
            db.add(
                ExternalJobReconciliation(
                    job_id=job.id,
                    provider_job_id=submission.prompt_id,
                    action="poll_after_ack",
                    result=result.status,
                    evidence={"output": result.output_filename},
                )
            )
            add_audit(
                db,
                "comfy-worker",
                "COMFY_OUTPUT_RECONCILIATION_REQUIRED",
                "job",
                job.id,
                {"result": result.status},
                project_id=job.project_id,
            )
        return job_id

    assert result.content is not None and result.content_type
    relative, digest, byte_size = store_artifact(
        settings.data_dir, project_id, job_id, result.content, result.content_type
    )
    technical = TechnicalQC(ffprobe_path=settings.ffprobe_path, ffmpeg_path=settings.ffmpeg_path).check(
        settings.data_dir / relative,
        expectations=TechnicalExpectations(
            duration_s=request.expected_duration_s,
            expect_audio=bool(request.shot_spec.dialogue),
        ),
    )
    fused = fuse(technical=technical, semantic_results=[])
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
        artifact = MediaArtifact(
            project_id=job.project_id,
            job_id=job.id,
            attempt_id=attempt.id,
            artifact_kind="video",
            storage_path=relative,
            sha256=digest,
            byte_size=byte_size,
            content_type=result.content_type,
            is_mock=False,
            provenance={"prompt_id": submission.prompt_id, "workflow_hash": submission.workflow_hash},
        )
        db.add(artifact)
        db.flush()
        job.artifact_id = artifact.id
        job.last_qc_result = fused.result
        attempt.output_path = relative
        attempt.output_sha256 = digest
        attempt.output_bytes = byte_size
        attempt.progress = 100
        attempt.status = "COMPLETED"
        db.add(
            QCReport(
                job_id=job.id,
                attempt_id=attempt.id,
                artifact_id=artifact.id,
                result="passed" if fused.result == "passed" else fused.result,
                is_mock=False,
                reviewer="technical-qc",
                report=fused.model_dump(mode="json"),
            )
        )
        transition(job, "QC_PENDING") if job.status == "RUNNING" else None
        job.lease_owner = None
        job.lease_expires_at = None
        if fused.result == "passed":
            transition(job, "PASSED")
        elif fused.result == "failed":
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
            )
        else:
            transition(job, "NEEDS_REVIEW")
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
            {"qc_result": fused.result, "sha256": digest, "coverage": fused.coverage},
            project_id=job.project_id,
        )
    heartbeat(session_factory, worker_id, phase="idle", processed_delta=1)
    return job_id


def recover_expired_jobs(
    session_factory,
    worker_id: str,
    *,
    settings: Settings | None = None,
    comfy_client=None,
) -> int:
    """Resolve expired external submissions conservatively; never resubmit an ambiguous task."""
    recovered = 0
    settings = settings or Settings.from_env()
    with session_factory.begin() as db:
        now = utcnow()
        jobs = db.scalars(
            select(Job).where(
                Job.status.in_(["SUBMITTING", "RUNNING", "QC_PENDING"]),
                Job.lease_expires_at.is_not(None),
                Job.lease_expires_at < now,
            )
        ).all()
        for job in jobs:
            attempt = _latest_attempt(db, job.id)
            if not _shot_is_current(db, job):
                _discard_stale_result(db, job, attempt, reason="SHOT_REVISION_SUPERSEDED", worker_id=worker_id)
                recovered += 1
                continue
            if job.provider == "comfyui" and job.provider_job_id:
                recovered += _recover_comfy_job(db, job, attempt, settings=settings, comfy_client=comfy_client)
                continue
            if job.status == "SUBMITTING" or not job.provider_job_id:
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
                recovered += 1
                continue
            if job.status == "RUNNING":
                transition(job, "QC_PENDING")
                if attempt:
                    attempt.status = "ACKNOWLEDGED"
                recovered += 1
            job.lease_owner = None
            job.lease_expires_at = None
            job.heartbeat_at = now
            if job.status == "QC_PENDING":
                if not attempt:
                    transition(job, "NEEDS_REVIEW")
                    job.failure_code = "ATTEMPT_RECORD_MISSING"
                    continue
                report = {
                    "simulated": True,
                    "video_created": False,
                    "artifact_kind": "none",
                    "recovered_after_acknowledged_submission": True,
                }
                _record_mock_qc(db, job, attempt, report)
                job.last_qc_result = "passed"
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
    return recovered


def _recover_comfy_job(
    db: Session,
    job: Job,
    attempt: JobAttempt | None,
    *,
    settings: Settings,
    comfy_client,
) -> int:
    from shine.comfy.client import ComfyUIClient

    client = comfy_client or ComfyUIClient(settings.comfyui_base_url, timeout_s=10)
    service = ComfyUIRenderService(client, settings.data_dir)
    assert job.provider_job_id
    status = service.reconcile(job.provider_job_id)
    job.heartbeat_at = utcnow()
    if status in {"running", "pending", "timeout"}:
        job.lease_expires_at = utcnow() + timedelta(seconds=settings.worker_lease_seconds)
        db.add(
            ExternalJobReconciliation(
                job_id=job.id,
                provider_job_id=job.provider_job_id,
                action="recover_running",
                result=status,
                evidence={},
            )
        )
        add_audit(
            db,
            "orchestrator",
            "COMFY_RECONCILED_RUNNING",
            "job",
            job.id,
            {"status": status},
            project_id=job.project_id,
        )
        return 1
    if status in {"completed", "completed_without_output"}:
        job.failure_code = None if status == "completed" else "COMFY_OUTPUT_MISSING"
        job.lease_owner = None
        job.lease_expires_at = None
        if status == "completed":
            # The artifact download is intentionally left to a dedicated QC pass.
            if job.status == "SUBMITTING":
                transition(job, "RUNNING")
            if job.status == "RUNNING":
                transition(job, "QC_PENDING")
            if job.status == "QC_PENDING":
                transition(job, "NEEDS_REVIEW")
                job.failure_code = "RECOVERED_OUTPUT_REQUIRES_QC"
            if attempt:
                attempt.reconciliation_state = "history_found"
                attempt.reconciled_at = utcnow()
        else:
            if job.status in {"RUNNING", "QC_PENDING"}:
                transition(job, "NEEDS_REVIEW")
            if attempt:
                attempt.reconciliation_state = "completed_without_output"
                attempt.reconciled_at = utcnow()
        db.add(
            ExternalJobReconciliation(
                job_id=job.id,
                provider_job_id=job.provider_job_id,
                action="recover_history",
                result=status,
                evidence={},
            )
        )
        add_audit(
            db,
            "orchestrator",
            "COMFY_RECONCILED_HISTORY",
            "job",
            job.id,
            {"status": status},
            project_id=job.project_id,
        )
        return 1
    if status.startswith("unavailable:"):
        job.failure_code = "RECONCILIATION_UNAVAILABLE"
        job.lease_owner = None
        job.lease_expires_at = None
        if job.status in {"RUNNING", "QC_PENDING", "SUBMITTING"}:
            transition(job, "NEEDS_REVIEW")
        if attempt:
            attempt.status = "ACK_UNCERTAIN"
            attempt.failure_code = job.failure_code
        db.add(
            ExternalJobReconciliation(
                job_id=job.id,
                provider_job_id=job.provider_job_id,
                action="recover_unavailable",
                result=status,
                evidence={"resubmitted": False},
            )
        )
        return 1
    # not_found: the external job may never have been created or history was cleared.
    job.failure_code = "EXTERNAL_JOB_NOT_FOUND"
    job.lease_owner = None
    job.lease_expires_at = None
    if job.status in {"RUNNING", "QC_PENDING", "SUBMITTING"}:
        transition(job, "NEEDS_REVIEW")
    if attempt:
        attempt.status = "ACK_UNCERTAIN"
        attempt.failure_code = job.failure_code
        attempt.reconciliation_state = "not_found"
        attempt.reconciled_at = utcnow()
    db.add(
        ExternalJobReconciliation(
            job_id=job.id,
            provider_job_id=job.provider_job_id,
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
    return 1


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
