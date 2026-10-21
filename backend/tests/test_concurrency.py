from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from shine.models import Job, JobAttempt, utcnow
from shine.providers import MockH3Provider
from shine.worker import recover_expired_jobs, run_one
from tests.conftest import create_job, create_project, create_shot, freeze_style, shot_payload


def test_revising_shot_while_worker_renders_discards_the_stale_result(studio):
    client, factory, _root, _settings = studio
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    shot = create_shot(client, project_id, style["id"])
    job = create_job(client, project_id, shot["id"])
    claimed = threading.Event()
    release = threading.Event()

    class SlowProvider(MockH3Provider):
        def submit(self, idempotency_key, scenario):
            claimed.set()
            assert release.wait(timeout=5)
            return super().submit(idempotency_key, scenario)

    provider = SlowProvider()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run_one, factory, provider, "worker-stale")
        assert claimed.wait(timeout=5)
        revised = client.post(
            f"/api/v1/shots/{shot['id']}/revise",
            json=shot_payload(style["id"], title=shot["name"], beat="渲染期间被修改")["spec"],
        )
        assert revised.status_code == 200, revised.text
        assert revised.json()["revision"] == 2
        release.set()
        assert future.result(timeout=5) == job["id"]

    result = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert result["status"] == "NEEDS_REVIEW"
    assert result["stale"] is True
    assert result["shot_revision"] == 1
    assert result["attempts"][0]["status"] != "ACKNOWLEDGED"
    assert client.get(f"/api/v1/jobs/{job['id']}/qc").json() == []


def test_recovery_after_acknowledged_submission_resumes_without_resubmission(studio):
    client, factory, _root, _settings = studio
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    shot = create_shot(client, project_id, style["id"])
    job = create_job(client, project_id, shot["id"])
    with factory.begin() as db:
        row = db.get(Job, job["id"])
        row.status = "RUNNING"
        row.provider_job_id = "mock-acknowledged"
        row.lease_owner = "crashed-worker"
        row.lease_expires_at = utcnow() - timedelta(seconds=1)
        attempt = JobAttempt(
            job_id=row.id,
            attempt_no=1,
            request_fingerprint="b" * 64,
            status="ACKNOWLEDGED",
            provider_job_id="mock-acknowledged",
            lease_owner="crashed-worker",
            lease_expires_at=row.lease_expires_at,
        )
        db.add(attempt)
    provider = MockH3Provider()
    assert recover_expired_jobs(factory, "replacement") == 1
    assert provider.submission_count == 0
    result = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert result["status"] == "PASSED"
    assert result["attempts"][0]["status"] == "ACKNOWLEDGED"
    qc = client.get(f"/api/v1/jobs/{job['id']}/qc").json()
    assert len(qc) == 1 and qc[0]["is_mock"] is True
    assert qc[0]["report"]["recovered_after_acknowledged_submission"] is True
    assert run_one(factory, provider, "replacement") is None


def test_expired_submission_intent_requires_reconciliation_and_is_not_retried(studio):
    client, factory, _root, _settings = studio
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    shot = create_shot(client, project_id, style["id"])
    job = create_job(client, project_id, shot["id"])
    with factory.begin() as db:
        row = db.get(Job, job["id"])
        row.status = "SUBMITTING"
        row.lease_owner = "crashed"
        row.lease_expires_at = utcnow() - timedelta(seconds=1)
        db.add(
            JobAttempt(
                job_id=row.id,
                attempt_no=1,
                request_fingerprint="c" * 64,
                status="SUBMIT_INTENT",
                lease_owner="crashed",
                lease_expires_at=row.lease_expires_at,
            )
        )
    provider = MockH3Provider()
    assert recover_expired_jobs(factory, "replacement") == 1
    result = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert result["status"] == "NEEDS_REVIEW"
    assert result["failure_code"] == "SUBMISSION_OUTCOME_UNKNOWN"
    assert provider.submission_count == 0
    assert run_one(factory, provider, "replacement") is None
    assert len(client.get(f"/api/v1/jobs/{job['id']}").json()["attempts"]) == 1


def test_duplicate_acknowledgement_does_not_create_a_second_mock_job(studio):
    client, factory, _root, _settings = studio
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    shot = create_shot(client, project_id, style["id"])
    job = create_job(client, project_id, shot["id"], mock_scenario="duplicate")
    provider = MockH3Provider()
    assert run_one(factory, provider, "worker-dup") == job["id"]
    assert provider.submission_count == 1
    qc = client.get(f"/api/v1/jobs/{job['id']}/qc").json()[0]
    assert qc["report"]["duplicate_acknowledgement"] is True


def test_two_workers_only_create_one_attempt_for_one_job(studio):
    client, factory, _root, _settings = studio
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    shot = create_shot(client, project_id, style["id"])
    job = create_job(client, project_id, shot["id"])
    claimed = threading.Event()
    release = threading.Event()

    class SlowProvider(MockH3Provider):
        def submit(self, idempotency_key, scenario):
            claimed.set()
            assert release.wait(timeout=5)
            return super().submit(idempotency_key, scenario)

    provider = SlowProvider()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run_one, factory, provider, "worker-one")
        assert claimed.wait(timeout=5)
        second_result = run_one(factory, provider, "worker-two")
        release.set()
        first_result = first.result(timeout=5)
    assert first_result == job["id"] and second_result is None
    attempts = client.get(f"/api/v1/jobs/{job['id']}").json()["attempts"]
    assert len(attempts) == 1
    assert attempts[0]["attempt_no"] == 1
    assert provider.submission_count == 1
