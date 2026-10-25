"""Machine recheck preserves the immutable render and cannot approve hard failures."""

import pytest
from sqlalchemy import select

from shine.models import AppSetting, Job, JobAttempt, MediaArtifact, QCReport, QualityGate
from shine.schemas import FusedQCResult
from shine.worker import _record_gates
from tests.test_operator_review import reviewable  # noqa: F401


def prepare(factory, job_id):
    with factory.begin() as db:
        job = db.get(Job, job_id)
        job.failure_code = "QC_NEEDS_REVIEW"
        return job.state_version, job.artifact_id


def test_machine_recheck_preserves_attempt_artifact_and_history(reviewable):  # noqa: F811
    client, factory, _, job_id, _, _ = reviewable
    version, artifact = prepare(factory, job_id)
    payload = {"state_version": version, "request_id": "machine-intent-0001"}
    response = client.post(f"/api/v1/jobs/{job_id}/review/recheck", json=payload)
    assert response.status_code == 202, response.text
    assert response.json()["render_resubmission"] is False
    assert response.json()["artifact_id"] == artifact
    with factory() as db:
        assert len(list(db.scalars(select(JobAttempt).where(JobAttempt.job_id == job_id)))) == 1
        assert len(list(db.scalars(select(QualityGate).where(QualityGate.job_id == job_id)))) == 1
        assert db.get(Job, job_id).status == "NEEDS_REVIEW"
        assert db.get(AppSetting, "qc-recheck:" + job_id).value["count"] == 1
    assert client.post(f"/api/v1/jobs/{job_id}/review/recheck", json=payload).status_code == 409


@pytest.mark.parametrize("change", ["hard_failure", "stale_version", "budget", "replay"])
def test_recheck_rejects_ineligible_requests(reviewable, change):  # noqa: F811
    client, factory, _, job_id, _, _ = reviewable
    version, artifact = prepare(factory, job_id)
    payload = {"state_version": version, "request_id": "machine-intent-0001"}
    with factory.begin() as db:
        if change == "hard_failure":
            attempt = db.scalar(select(JobAttempt).where(JobAttempt.job_id == job_id))
            db.add(
                QualityGate(
                    job_id=job_id,
                    attempt_id=attempt.id,
                    artifact_id=artifact,
                    gate_name="semantic",
                    status="failed",
                    required=True,
                    is_mock=False,
                )
            )
        elif change == "stale_version":
            payload["state_version"] += 1
        else:
            db.add(
                AppSetting(
                    key="qc-recheck:" + job_id,
                    value={
                        "count": 2 if change == "budget" else 1,
                        "request_id": payload["request_id"] if change == "replay" else "previous",
                    },
                )
            )
    response = client.post(f"/api/v1/jobs/{job_id}/review/recheck", json=payload)
    assert response.status_code == 409, response.text
    with factory() as db:
        assert db.get(Job, job_id).failure_code == "QC_NEEDS_REVIEW"


def test_changed_machine_review_appends_and_exact_recovery_is_idempotent(reviewable):  # noqa: F811
    _, factory, _, job_id, _, _ = reviewable
    first = FusedQCResult(result="needs_review", review_confidence=0, is_mock=False, human_review_required=True)
    second = FusedQCResult(result="passed", review_confidence=0.97, is_mock=False)
    with factory.begin() as db:
        job = db.get(Job, job_id)
        attempt = db.scalar(select(JobAttempt).where(JobAttempt.job_id == job_id))
        artifact = db.get(MediaArtifact, job.artifact_id)
        _record_gates(db, job, attempt, artifact, [], first)
        db.flush()
        _record_gates(db, job, attempt, artifact, [], second)
        db.flush()
        _record_gates(db, job, attempt, artifact, [], second)
    with factory() as db:
        reports = db.scalars(select(QCReport).where(QCReport.job_id == job_id).order_by(QCReport.created_at)).all()
        assert len(reports) == 2
        assert reports[0].report == first.model_dump(mode="json")
        assert reports[1].report == second.model_dump(mode="json")
