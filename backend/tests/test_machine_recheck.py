"""Machine recheck preserves the immutable render and cannot approve hard failures."""

import pytest
from sqlalchemy import select

from shine.models import AppSetting, Job, JobAttempt, MediaArtifact, QCReport, QualityGate
from shine.schemas import FusedQCResult
from shine.worker import _record_gates, recover_expired_jobs
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


@pytest.mark.parametrize("changed", [False, True])
def test_recheck_uses_collected_artifact_after_comfy_history_is_gone(reviewable, monkeypatch, changed):  # noqa: F811
    client, factory, settings, job_id, _, path = reviewable
    version, artifact_id = prepare(factory, job_id)
    response = client.post(f"/api/v1/jobs/{job_id}/review/recheck",
                           json={"state_version": version, "request_id": "physical-restart-recheck-1"})
    assert response.status_code == 202, response.text
    if changed:
        path.write_bytes(b"artifact modified after review request")

    class NoHistoryClient:
        def history(self, *args, **kwargs):
            raise AssertionError("recheck must not query vanished ComfyUI history")

    finalized = []
    monkeypatch.setattr("shine.worker._finalize_comfy_attempt", lambda _factory, **kw: finalized.append(kw))
    assert recover_expired_jobs(factory, "test-recheck-worker", settings=settings,
                                comfy_client=NoHistoryClient()) == 1
    with factory() as db:
        job = db.get(Job, job_id)
        attempts = list(db.scalars(select(JobAttempt).where(JobAttempt.job_id == job_id)))
        assert len(attempts) == 1 and attempts[0].status == "COMPLETED"
        assert job.artifact_id == artifact_id
        if changed:
            assert not finalized and job.status == "NEEDS_REVIEW"
            assert job.failure_code == "QC_RECHECK_INPUT_CHANGED"
        else:
            assert len(finalized) == 1 and finalized[0]["attempt_id"] == attempts[0].id
            assert finalized[0]["prompt_id"] == "fake-prompt" and job.status == "RUNNING"


def test_recheck_revalidates_artifact_after_worker_claim(reviewable, monkeypatch):  # noqa: F811
    from shine import worker

    client, factory, settings, job_id, _, path = reviewable
    version, artifact_id = prepare(factory, job_id)
    assert client.post(f"/api/v1/jobs/{job_id}/review/recheck",
                       json={"state_version": version, "request_id": "artifact-race-recheck-1"}).status_code == 202
    real_finalize = worker._finalize_comfy_attempt
    monkeypatch.setattr(worker, "_load_comfy_context", lambda *_: (None, None, None, None, None, None))

    def change_before_finalize(factory, **kwargs):
        path.unlink()
        return real_finalize(factory, **kwargs)

    monkeypatch.setattr(worker, "_finalize_comfy_attempt", change_before_finalize)
    assert recover_expired_jobs(factory, "test-race-worker", settings=settings, comfy_client=object()) == 1
    with factory() as db:
        job = db.get(Job, job_id)
        attempt = db.scalar(select(JobAttempt).where(JobAttempt.job_id == job_id))
        assert job.status == "NEEDS_REVIEW" and job.failure_code == "ARTIFACT_HASH_MISMATCH"
        assert job.artifact_id == artifact_id and attempt.status == "COMPLETED"
