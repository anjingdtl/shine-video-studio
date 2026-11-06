"""Advisory reviews are durable, independent work items, not a production gate."""

from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta

import pytest

from shine.config import Settings
from shine.models import AgentTaskRecord, Job, JobAttempt, MediaArtifact, QCReport, QualityGate, Shot, utcnow
from shine.quality.advisory_queue import (
    ROLE_ASR,
    ROLE_VLM,
    enqueue_advisory_reviews,
    run_pending_advisory_reviews,
)
from shine.quality.gates import QualityPolicy
from shine.quality.semantic import SemanticReviewResult, Transcript
from shine.schemas import RenderRequest
from tests.conftest import (
    create_project,
    create_shot,
    ffmpeg_executable,
    ffprobe_executable,
    freeze_style,
    make_test_video_bytes,
)

DIALOGUE = [{"speaker_id": "林岚", "text": "你终于来了。", "start_s": 0.4, "end_s": 2.4}]


class FakeVLM:
    is_mock = False
    name = "fake-vlm"
    accepts_local_references = False

    def review(self, context):
        return SemanticReviewResult(
            reviewer=self.name,
            model="fake-vlm-4b",
            is_mock=False,
            input_sha256=context.artifact_sha256,
            findings=[],
            overall_confidence=0.95,
            raw_summary="scripted unit-test review; no production claim",
        )


class FailingVLM:
    is_mock = False
    name = "failing-vlm"
    accepts_local_references = False

    def review(self, context):
        raise TimeoutError("scripted reviewer timeout")


class FakeASR:
    is_mock = False
    name = "fake-asr"
    requires_remote_consent = False

    def transcribe(self, path):
        return Transcript(text="你终于来了。", segments=[], is_mock=False, model="fake-asr-0.6b")


def _settings(settings: Settings) -> Settings:
    ffprobe, ffmpeg = ffprobe_executable(), ffmpeg_executable()
    if not ffprobe or not ffmpeg:
        pytest.skip("advisory queue tests require FFprobe and FFmpeg")
    return Settings(
        data_dir=settings.data_dir,
        database_url=settings.database_url,
        secret_store_dir=settings.secret_store_dir,
        ffprobe_path=ffprobe,
        ffmpeg_path=ffmpeg,
    )


@pytest.fixture
def passed_job(api):
    """A completed real-shaped H3 job whose technical media check really passed."""
    client, factory, settings = api
    settings = _settings(settings)
    project_id = create_project(client, "建议审片队列")
    style = freeze_style(client, project_id)
    shot = create_shot(
        client, project_id, style["id"], characters=["林岚"], dialogue=DIALOGUE,
        bindings=[],
    )
    content = make_test_video_bytes(duration_s=6.0, with_audio=True)
    relative = "advisory-queue-media.mp4"
    (settings.data_dir / relative).write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    with factory.begin() as db:
        shot_row = db.get(Shot, shot["id"])
        request = RenderRequest(
            idempotency_key="advisory-" + uuid.uuid4().hex,
            shot_id=shot_row.id,
            shot_revision=shot_row.revision,
            style_version_id=style["id"],
            style_hash=style["sha256"],
            provider="comfyui",
            shot_spec=shot_row.spec,
            style_snapshot=style["snapshot"],
            expected_duration_s=float(shot_row.spec["duration_target_s"]),
            self_review_scope="full",
        )
        job = Job(
            project_id=project_id,
            shot_id=shot_row.id,
            shot_revision=shot_row.revision,
            style_version_id=style["id"],
            request=request.model_dump(mode="json"),
            idempotency_key=request.idempotency_key,
            status="PASSED",
            provider="comfyui",
            last_qc_result="needs_review",
            quality_verification="real",
        )
        db.add(job)
        db.flush()
        attempt = JobAttempt(
            job_id=job.id,
            attempt_no=1,
            request_fingerprint=hashlib.sha256(b"request").hexdigest(),
            status="COMPLETED",
            lease_owner="test-worker",
            lease_expires_at=utcnow() + timedelta(hours=1),
        )
        db.add(attempt)
        db.flush()
        artifact = MediaArtifact(
            project_id=project_id,
            job_id=job.id,
            attempt_id=attempt.id,
            artifact_kind="video",
            storage_path=relative,
            sha256=digest,
            byte_size=len(content),
            content_type="video/mp4",
            is_mock=False,
        )
        db.add(artifact)
        db.flush()
        job.artifact_id = artifact.id
    return {
        "factory": factory,
        "settings": settings,
        "project_id": project_id,
        "job_id": job.id,
        "attempt_id": attempt.id,
        "artifact_id": artifact.id,
        "shot_id": shot["id"],
        "digest": digest,
    }


def _enqueue(fixture, policy=None):
    with fixture["factory"].begin() as db:
        job = db.get(Job, fixture["job_id"])
        attempt = db.get(JobAttempt, fixture["attempt_id"])
        artifact = db.get(MediaArtifact, fixture["artifact_id"])
        return enqueue_advisory_reviews(db, job, attempt, artifact, policy or QualityPolicy())


def test_enqueue_binds_the_exact_passed_video(passed_job):
    ids = _enqueue(passed_job)
    assert len(ids) == 2
    with passed_job["factory"]() as db:
        tasks = [db.get(AgentTaskRecord, task_id) for task_id in ids]
        assert {task.role_id for task in tasks} == {ROLE_VLM, ROLE_ASR}
        for task in tasks:
            assert task.status == "QUEUED"
            assert task.trace["artifact_sha256"] == passed_job["digest"]
            assert task.trace["job_id"] == passed_job["job_id"]
            assert task.trace["shot_revision"] == 1
            assert task.trace["advisory"] is True
    # The production job is untouched: queuing suggestions is not production.
    with passed_job["factory"]() as db:
        assert db.get(Job, passed_job["job_id"]).status == "PASSED"


def test_the_queue_really_executes_and_records_bound_evidence(passed_job):
    _enqueue(passed_job)
    processed = run_pending_advisory_reviews(
        passed_job["factory"], "test-worker", settings=passed_job["settings"],
        semantic_reviewer=FakeVLM(), asr_reviewer=FakeASR(), limit=4,
    )
    assert processed == 2
    with passed_job["factory"]() as db:
        tasks = db.query(AgentTaskRecord).filter(AgentTaskRecord.role_id.in_([ROLE_VLM, ROLE_ASR])).all()
        assert all(task.status == "COMPLETED" for task in tasks)
        # The durable task records the model that actually executed the review.
        assert {task.model_id for task in tasks} == {"fake-vlm", "fake-asr"}
        gates = db.query(QualityGate).filter(QualityGate.job_id == passed_job["job_id"]).all()
        async_gates = [gate for gate in gates if (gate.evidence or {}).get("async") is True]
        assert {gate.gate_name for gate in async_gates} == {"semantic", "style", "audio"}
        for gate in async_gates:
            assert gate.required is False
            assert gate.evidence["artifact_sha256"] == passed_job["digest"]
        reports = db.query(QCReport).filter(
            QCReport.job_id == passed_job["job_id"],
            QCReport.reviewer == "advisory-review-queue",
        ).all()
        assert len(reports) == 2
        transcript = [report.report.get("transcript") for report in reports if report.report.get("transcript")]
        assert transcript and transcript[0]["text"] == "你终于来了。"
        # Production state is untouched and no render was requested again.
        job = db.get(Job, passed_job["job_id"])
        assert job.status == "PASSED"
        assert db.query(Job).filter(Job.project_id == passed_job["project_id"]).count() == 1


def test_a_changed_shot_supersedes_the_queued_review(passed_job):
    _enqueue(passed_job)
    with passed_job["factory"].begin() as db:
        shot = db.get(Shot, passed_job["shot_id"])
        shot.revision += 1
    processed = run_pending_advisory_reviews(
        passed_job["factory"], "test-worker", settings=passed_job["settings"],
        semantic_reviewer=FakeVLM(), asr_reviewer=FakeASR(), limit=4,
    )
    # Superseded items are retired, not executed; nothing is recorded for them.
    assert processed == 0
    with passed_job["factory"]() as db:
        tasks = db.query(AgentTaskRecord).filter(AgentTaskRecord.role_id.in_([ROLE_VLM, ROLE_ASR])).all()
        assert {task.status for task in tasks} == {"SUPERSEDED"}
        assert not db.query(QualityGate).filter(QualityGate.job_id == passed_job["job_id"]).all()


def test_a_failing_reviewer_leaves_the_passed_video_and_next_shots_alone(passed_job):
    _enqueue(passed_job)
    processed = run_pending_advisory_reviews(
        passed_job["factory"], "test-worker", settings=passed_job["settings"],
        semantic_reviewer=FailingVLM(), asr_reviewer=FakeASR(), limit=4,
    )
    assert processed == 2
    with passed_job["factory"]() as db:
        tasks = db.query(AgentTaskRecord).filter(AgentTaskRecord.role_id.in_([ROLE_VLM, ROLE_ASR])).all()
        statuses = {task.role_id: task.status for task in tasks}
        assert statuses[ROLE_VLM] == "UNAVAILABLE"
        assert statuses[ROLE_ASR] == "COMPLETED"
        job = db.get(Job, passed_job["job_id"])
        assert job.status == "PASSED"
        assert job.artifact_id == passed_job["artifact_id"]
        assert db.query(Job).filter(Job.project_id == passed_job["project_id"]).count() == 1


def test_no_reviewer_route_is_recorded_as_unavailable_not_faked(passed_job):
    _enqueue(passed_job)
    processed = run_pending_advisory_reviews(
        passed_job["factory"], "test-worker", settings=passed_job["settings"],
        semantic_reviewer=None, asr_reviewer=None, limit=4,
    )
    assert processed == 2
    with passed_job["factory"]() as db:
        tasks = db.query(AgentTaskRecord).filter(AgentTaskRecord.role_id.in_([ROLE_VLM, ROLE_ASR])).all()
        assert all(task.status == "UNAVAILABLE" for task in tasks)
        assert all(task.trace.get("outcome") == "unavailable" for task in tasks)
        job = db.get(Job, passed_job["job_id"])
        assert job.status == "PASSED"


class UnreleasableVLM(FakeVLM):
    """A reviewer that answers but whose weights cannot be released."""

    def release_models(self):
        raise ConnectionError("review service is gone")


def test_an_unreleasable_reviewer_is_recorded_and_never_stops_the_worker(passed_job, monkeypatch):
    from shine.quality import advisory_queue

    # The physical probe is not the subject here: the audit write is.
    monkeypatch.setattr(advisory_queue, "_physical_memory_ready", lambda _gpu: True)
    _enqueue(passed_job)
    processed = run_pending_advisory_reviews(
        passed_job["factory"], "test-worker", settings=passed_job["settings"],
        semantic_reviewer=UnreleasableVLM(), asr_reviewer=None, limit=1,
    )
    assert processed >= 1
    from shine.models import AuditEvent

    with passed_job["factory"]() as db:
        rows = db.query(AuditEvent).filter(
            AuditEvent.event_type == "ADVISORY_REVIEW_RELEASE_FALLBACK").all()
        assert rows, "the fallback must be recorded"
        for row in rows:
            # A null entity id used to abort the whole tick with a NOT NULL error.
            assert row.entity_id
            assert row.payload["errors"]
