from __future__ import annotations

import argparse
import time
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from shine.config import Settings
from shine.db import make_engine, make_session_factory
from shine.models import Job, JobAttempt, JobDependency, QCReport, utcnow
from shine.providers import MockH3Provider, ProviderFailure, SubmissionUncertain
from shine.services import add_audit, job_fingerprint, transition

LEASE_SECONDS = 30


def _promote_dependencies(db: Session) -> None:
    planned = db.scalars(select(Job).where(Job.status == "PLANNED")).all()
    for job in planned:
        dependencies = db.scalars(select(JobDependency).where(JobDependency.child_job_id == job.id)).all()
        parents = [db.get(Job, dep.parent_job_id) for dep in dependencies]
        if any(parent and (parent.stale or parent.status in {"FAILED", "NEEDS_REVIEW"}) for parent in parents):
            job.failure_code = "DEPENDENCY_BLOCKED"
            transition(job, "NEEDS_REVIEW")
            add_audit(db, "orchestrator", "JOB_BLOCKED_BY_DEPENDENCY", "job", job.id)
        elif all(parent and parent.status == "PASSED" for parent in parents):
            transition(job, "QUEUED")
            add_audit(db, "orchestrator", "JOB_DEPENDENCIES_READY", "job", job.id)


def recover_expired_jobs(session_factory, worker_id: str) -> int:
    """Resolve expired external submissions conservatively; never resubmit an ambiguous task."""
    recovered = 0
    with session_factory.begin() as db:
        now = utcnow()
        jobs = db.scalars(
            select(Job).where(
                Job.status.in_(["SUBMITTING", "RUNNING", "QC_PENDING"]),
                Job.lease_expires_at < now,
            )
        ).all()
        for job in jobs:
            attempt = db.scalar(
                select(JobAttempt).where(JobAttempt.job_id == job.id).order_by(JobAttempt.attempt_no.desc())
            )
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
                db.add(
                    QCReport(
                        job_id=job.id,
                        attempt_id=attempt.id,
                        result="passed",
                        is_mock=True,
                        report=report,
                    )
                )
                transition(job, "PASSED")
                add_audit(db, "mock-worker", "MOCK_QC_PASSED", "job", job.id, report)
    return recovered


def run_one(session_factory, provider: MockH3Provider, worker_id: str | None = None) -> str | None:
    worker_id = worker_id or f"worker-{uuid4().hex[:12]}"
    claimed: tuple[str, str, int, str, str] | None = None
    with session_factory.begin() as db:
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
        result = db.execute(
            update(Job)
            .where(Job.id == candidate.id, Job.status == "QUEUED", Job.paused.is_(False))
            .values(
                status="SUBMITTING",
                lease_owner=worker_id,
                lease_expires_at=now + timedelta(seconds=LEASE_SECONDS),
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
            lease_expires_at=now + timedelta(seconds=LEASE_SECONDS),
            heartbeat_at=now,
        )
        db.add(attempt)
        db.flush()
        add_audit(db, "orchestrator", "SUBMIT_INTENT", "job", job.id, {"attempt_no": attempt_no})
        claimed = (job.id, job.idempotency_key, attempt_no, job.mock_scenario, attempt.id)

    job_id, idempotency_key, _attempt_no, scenario, attempt_id = claimed
    try:
        submission = provider.submit(idempotency_key, scenario)
    except SubmissionUncertain as exc:
        with session_factory.begin() as db:
            job = db.get(Job, job_id)
            attempt = db.get(JobAttempt, attempt_id)
            transition(job, "NEEDS_REVIEW")
            job.failure_code = str(exc)
            job.lease_owner = None
            job.lease_expires_at = None
            attempt.status = "ACK_UNCERTAIN"
            attempt.failure_code = str(exc)
            add_audit(db, "mock-worker", "SUBMISSION_ACK_UNCERTAIN", "job", job.id, {"resubmitted": False})
        return job_id
    except ProviderFailure as exc:
        with session_factory.begin() as db:
            job = db.get(Job, job_id)
            attempt = db.get(JobAttempt, attempt_id)
            transition(job, "REPAIR_PENDING")
            job.failure_code = exc.code
            job.lease_owner = None
            job.lease_expires_at = None
            attempt.status = "FAILED"
            attempt.failure_code = exc.code
            add_audit(db, "mock-worker", "MOCK_RENDER_FAILED", "job", job.id, {"failure_code": exc.code})
        return job_id

    with session_factory.begin() as db:
        job = db.get(Job, job_id)
        attempt = db.get(JobAttempt, attempt_id)
        job.provider_job_id = submission.provider_job_id
        job.heartbeat_at = utcnow()
        attempt.provider_job_id = submission.provider_job_id
        attempt.submit_ack_at = utcnow()
        attempt.status = "ACKNOWLEDGED"
        transition(job, "RUNNING")
        transition(job, "QC_PENDING")
        job.heartbeat_at = utcnow()
        report = {
            "simulated": True,
            "video_created": False,
            "artifact_kind": "none",
            "provider_job_id": submission.provider_job_id,
            "duplicate_acknowledgement": submission.duplicate,
            "model_checkpoint_id": "MiniMax-H3-Base",
        }
        db.add(
            QCReport(
                job_id=job.id,
                attempt_id=attempt.id,
                result="passed",
                is_mock=True,
                report=report,
            )
        )
        transition(job, "PASSED")
        job.lease_owner = None
        job.lease_expires_at = None
        add_audit(db, "mock-worker", "MOCK_WORKFLOW_PASSED", "job", job.id, report)
    return job_id


def serve(poll_seconds: float = 0.5) -> None:
    settings = Settings.from_env()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    worker_id = f"worker-{uuid4().hex[:12]}"
    provider = MockH3Provider()
    recover_expired_jobs(session_factory, worker_id)
    while True:
        claimed = run_one(session_factory, provider, worker_id)
        if claimed is None:
            time.sleep(poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Shine's standalone local mock worker")
    parser.add_argument("--once", action="store_true", help="Process at most one queued job and exit")
    args = parser.parse_args()
    settings = Settings.from_env()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    worker_id = f"worker-{uuid4().hex[:12]}"
    provider = MockH3Provider()
    recover_expired_jobs(session_factory, worker_id)
    if args.once:
        run_one(session_factory, provider, worker_id)
    else:
        while True:
            if run_one(session_factory, provider, worker_id) is None:
                time.sleep(0.5)


if __name__ == "__main__":
    main()
