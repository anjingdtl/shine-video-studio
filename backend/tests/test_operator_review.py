from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from shine.comfy.workflow import graph_hash
from shine.models import AuditEvent, Job, JobAttempt, MediaArtifact, Project, QualityGate, Shot, utcnow
from shine.operator_review import ITEMS, OperatorDecision, authenticate, review_context, submit_review
from shine.schemas import TechnicalQCResult
from shine.services import job_fingerprint
from tests.conftest import create_job, create_project, create_shot, freeze_style


@pytest.fixture
def reviewable(api, monkeypatch):
    client, factory, settings = api
    project = create_project(client)
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"])
    job_data = create_job(client, project, shot["id"])
    content = b"synthetic media; technical QC stub is explicitly isolated"
    path = settings.data_dir / "review.mp4"
    path.write_bytes(content)
    graph = {"1": {"class_type": "Fake", "inputs": {}}}
    with factory.begin() as db:
        job = db.get(Job, job_data["id"])
        job.status = "NEEDS_REVIEW"
        job.provider = "comfyui"
        job.provider_job_id = "fake-prompt"
        job.request = {**job.request, "provider": "comfyui"}
        attempt = JobAttempt(
            job_id=job.id,
            attempt_no=1,
            request_fingerprint=job_fingerprint(job.request),
            request_snapshot=job.request,
            status="COMPLETED",
            provider_job_id="fake-prompt",
            lease_owner="old-worker",
            lease_expires_at=utcnow() - timedelta(seconds=1),
            output_sha256=hashlib.sha256(content).hexdigest(),
            prompt_graph_sha256=graph_hash(graph),
        )
        db.add(attempt)
        db.flush()
        artifact = MediaArtifact(
            project_id=project,
            job_id=job.id,
            attempt_id=attempt.id,
            artifact_kind="video",
            storage_path="review.mp4",
            sha256=attempt.output_sha256,
            byte_size=len(content),
            content_type="video/mp4",
            is_mock=False,
            provenance={"prompt_id": "fake-prompt"},
        )
        db.add(artifact)
        db.flush()
        job.artifact_id = artifact.id
        db.add(
            QualityGate(
                job_id=job.id,
                attempt_id=attempt.id,
                artifact_id=artifact.id,
                gate_name="technical",
                status="passed",
                required=True,
                is_mock=False,
            )
        )
    fake = SimpleNamespace(
        history=lambda _: {
            "fake-prompt": {"status": {"status_str": "success", "completed": True}, "prompt": [0, "fake-prompt", graph]}
        }
    )
    monkeypatch.setattr(
        "shine.operator_review.TechnicalQC.check",
        lambda *a, **k: TechnicalQCResult(
            result="passed",
            checks=[],
            tool="fake-test-only",
            artifact_sha256=hashlib.sha256(content).hexdigest(),
            byte_size=len(content),
        ),
    )
    return client, factory, settings, job_data["id"], fake, path


def decision(db, settings, job_id, **changes):
    data = {
        "snapshot": review_context(db, settings, job_id)["snapshot"],
        "request_id": "request-000000001",
        "items": {k: {"status": "PASS", "evidence": f"specific human observation: {k}"} for k in ITEMS},
        "watched": True,
        "listened": True,
    }
    data.update(changes)
    return OperatorDecision.model_validate(data)


@pytest.mark.parametrize("token", ["", "agent", "x" * 40])
def test_unauthorized_operator_and_agent_claims(api, token):
    client, _, settings = api
    with pytest.raises(HTTPException) as exc:
        authenticate(settings, token)
    assert exc.value.status_code == 403
    assert client.post("/api/v1/operator/jobs/unknown/review", json={"username": "operator"}).status_code == 403


def test_server_provisioned_identity_and_no_client_username(api):
    client, _, settings = api
    configured = replace(
        settings, operator_id="local-human", operator_token_sha256=hashlib.sha256(b"t" * 40).hexdigest()
    )
    assert authenticate(configured, "t" * 40) == "local-human"
    client.app.state.settings = configured
    headers = {"Authorization": "Bearer " + "t" * 40}
    assert (
        client.post("/api/v1/operator/jobs/missing/review", headers=headers, json={"username": "agent"}).status_code
        == 422
    )
    response = client.get("/api/v1/operator/jobs/missing", headers={**headers, "Origin": "https://evil.test"})
    assert response.status_code == 403


@pytest.mark.parametrize("change", ["hash", "missing", "stale", "revision", "lease", "attempt", "prompt", "technical"])
def test_invalid_current_objects_refuse_review(reviewable, change):
    _, factory, settings, job_id, _, path = reviewable
    with factory.begin() as db:
        job = db.get(Job, job_id)
        if change == "hash":
            path.write_bytes(b"replaced")
        elif change == "missing":
            path.unlink()
        elif change == "stale":
            job.stale = True
        elif change == "revision":
            db.get(Shot, job.shot_id).revision += 1
        elif change == "lease":
            job.lease_owner = "worker"
        elif change == "attempt":
            db.get(MediaArtifact, job.artifact_id).attempt_id = "wrong-attempt"
        elif change == "prompt":
            job.provider_job_id = "changed"
        else:
            db.scalar(select(QualityGate)).status = "failed"
    with factory() as db, pytest.raises(HTTPException) as exc:
        review_context(db, settings, job_id)
    assert exc.value.status_code == 409


def test_manual_pass_is_once_and_audited(reviewable):
    _, factory, settings, job_id, fake, _ = reviewable
    with factory() as db:
        body = decision(db, settings, job_id)
        assert submit_review(db, settings, job_id, "test-authorized-human", body, client=fake)["status"] == "PASSED"
    with factory() as db:
        with pytest.raises(HTTPException):
            submit_review(db, settings, job_id, "test-authorized-human", body, client=fake)
        assert len(db.scalars(select(AuditEvent).where(AuditEvent.event_type == "OPERATOR_REVIEW_RECORDED")).all()) == 1
        assert db.scalar(select(QualityGate).where(QualityGate.gate_name == "style")).evidence["operator_id"]


@pytest.mark.parametrize("change", ["policy", "request", "history", "fail", "not_reviewed", "no_watch"])
def test_snapshot_history_and_incomplete_decisions(reviewable, change):
    _, factory, settings, job_id, fake, _ = reviewable
    with factory() as db:
        body = decision(db, settings, job_id)
    with factory.begin() as db:
        job = db.get(Job, job_id)
        if change == "policy":
            db.get(Project, job.project_id).quality_policy = {"require_style_review": False}
        elif change == "request":
            job.request_revision += 1
    if change == "history":
        fake = SimpleNamespace(history=lambda _: {})
    if change in {"fail", "not_reviewed"}:
        body.items["identity"].status = "FAIL" if change == "fail" else "NOT_REVIEWED"
    if change == "no_watch":
        body.watched = False
    with factory() as db:
        if change in {"policy", "request", "history"}:
            with pytest.raises(HTTPException):
                submit_review(db, settings, job_id, "test-human", body, client=fake)
        else:
            assert submit_review(db, settings, job_id, "test-human", body, client=fake)["status"] == "NEEDS_REVIEW"


def test_two_repairs_select_only_latest_attempt(reviewable):
    _, factory, settings, job_id, fake, _ = reviewable
    with factory.begin() as db:
        job = db.get(Job, job_id)
        old = db.scalar(select(JobAttempt).where(JobAttempt.job_id == job_id))
        for number in (2, 3):
            current = JobAttempt(
                job_id=job_id,
                attempt_no=number,
                request_fingerprint=old.request_fingerprint,
                request_snapshot=job.request,
                status="COMPLETED",
                provider_job_id="fake-prompt",
                lease_owner="worker",
                lease_expires_at=utcnow(),
                output_sha256=old.output_sha256,
                prompt_graph_sha256=old.prompt_graph_sha256,
            )
            db.add(current)
            db.flush()
        artifact = db.get(MediaArtifact, job.artifact_id)
        artifact.attempt_id = current.id
        db.add(
            QualityGate(
                job_id=job_id,
                attempt_id=current.id,
                artifact_id=artifact.id,
                gate_name="technical",
                status="passed",
                required=True,
                is_mock=False,
            )
        )
        latest_id = current.id
    with factory() as db:
        body = decision(db, settings, job_id)
        assert review_context(db, settings, job_id)["binding"]["attempt_id"] == latest_id
        submit_review(db, settings, job_id, "test-human", body, client=fake)
    with factory() as db:
        assert db.scalar(select(QualityGate).where(QualityGate.gate_name == "final")).attempt_id == latest_id


@pytest.mark.parametrize("listened", [False, True])
def test_dialogue_requires_explicit_listening(reviewable, listened):
    _, factory, settings, job_id, fake, _ = reviewable
    with factory.begin() as db:
        job = db.get(Job, job_id)
        request = dict(job.request)
        spec = dict(request["shot_spec"])
        spec["characters"] = ["a"]
        spec["dialogue"] = [{"speaker_id": "a", "text": "你好", "start_s": 0, "end_s": 1}]
        request["shot_spec"] = spec
        job.request = request
        attempt = db.scalar(select(JobAttempt).where(JobAttempt.job_id == job_id))
        attempt.request_snapshot = request
        attempt.request_fingerprint = job_fingerprint(request)
    with factory() as db:
        body = decision(db, settings, job_id, listened=listened)
        expected = "passed" if listened else "needs_review"
        assert submit_review(db, settings, job_id, "test-human", body, client=fake)["result"] == expected


def test_reused_request_id_after_failed_review_is_refused(reviewable):
    _, factory, settings, job_id, fake, _ = reviewable
    with factory() as db:
        body = decision(db, settings, job_id)
        body.items["identity"].status = "FAIL"
        submit_review(db, settings, job_id, "test-human", body, client=fake)
    with factory() as db:
        body = decision(db, settings, job_id)
        with pytest.raises(HTTPException) as exc:
            submit_review(db, settings, job_id, "test-human", body, client=fake)
        assert exc.value.status_code == 409


@pytest.mark.parametrize("item", ITEMS)
def test_any_failed_item_blocks_even_on_a_shot_without_scripted_dialogue(reviewable, item):
    _, factory, settings, job_id, fake, _ = reviewable
    with factory() as db:
        body = decision(db, settings, job_id)
        body.items[item].status = "FAIL"
        result = submit_review(db, settings, job_id, "test-human", body, client=fake)
        assert result["status"] == "NEEDS_REVIEW"
        assert result["result"] == "failed"


def test_policy_change_during_external_checks_invalidates_decision(reviewable):
    _, factory, settings, job_id, fake, _ = reviewable
    original = fake.history

    def history(prompt_id):
        with factory.begin() as other:
            job = other.get(Job, job_id)
            other.get(Project, job.project_id).quality_policy = {"min_confidence": 0.99}
        return original(prompt_id)

    fake.history = history
    with factory() as db:
        body = decision(db, settings, job_id)
        with pytest.raises(HTTPException) as exc:
            submit_review(db, settings, job_id, "test-human", body, client=fake)
        assert exc.value.status_code == 409
