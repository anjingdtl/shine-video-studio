from __future__ import annotations

from datetime import timedelta

import pytest

from shine.comfy.client import ComfyUIClient
from shine.config import Settings
from shine.models import Job, JobAttempt, utcnow
from shine.worker import recover_expired_jobs, run_one
from tests.conftest import (
    create_comfy_job,
    create_project,
    create_referenced_shot,
    freeze_style,
    make_test_video_bytes,
)
from tests.fake_http import FakeComfyServer


@pytest.fixture
def fake_comfy():
    server = FakeComfyServer().start()
    try:
        yield server
    finally:
        server.stop()


def comfy_settings(settings: Settings) -> Settings:
    return Settings(
        data_dir=settings.data_dir,
        database_url=settings.database_url,
        secret_store_dir=settings.secret_store_dir,
        ffprobe_path="missing-ffprobe",
        ffmpeg_path="missing-ffmpeg",
        render_provider="comfyui",
    )


def test_accepted_but_unacknowledged_submission_is_never_resubmitted(api, fake_comfy, monkeypatch):
    monkeypatch.setattr("shine.worker._handoff_preflight_allowed", lambda *_: True)
    client, factory, settings = api
    fake_comfy.mode = "accept_then_drop"
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    shot = create_referenced_shot(client, project_id, style["id"])
    job = create_comfy_job(client, project_id, shot["id"])
    offline = comfy_settings(settings)
    assert (
        run_one(
            factory,
            settings=offline,
            worker_id="comfy-worker",
            comfy_client=ComfyUIClient(fake_comfy.base_url, timeout_s=2),
        )
        == job["id"]
    )
    result = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert result["status"] == "NEEDS_REVIEW"
    assert result["failure_code"] == "COMFY_SUBMISSION_OUTCOME_UNKNOWN"
    # ComfyUI really accepted the prompt, so exactly one external task exists.
    assert fake_comfy.prompt_count == 1
    assert len(fake_comfy.prompts) == 1


def test_uncertain_submission_is_recovered_from_history_without_a_second_render(api, fake_comfy, monkeypatch):
    monkeypatch.setattr("shine.worker._handoff_preflight_allowed", lambda *_: True)
    client, factory, settings = api
    fake_comfy.output_bytes = make_test_video_bytes(duration_s=6)
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    shot = create_referenced_shot(client, project_id, style["id"])
    job = create_comfy_job(client, project_id, shot["id"])
    fake_comfy.mode = "accept_then_drop"
    offline = comfy_settings(settings)
    run_one(
        factory,
        settings=offline,
        worker_id="comfy-worker",
        comfy_client=ComfyUIClient(fake_comfy.base_url, timeout_s=2),
    )
    assert fake_comfy.prompt_count == 1
    blocked = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert blocked["status"] == "NEEDS_REVIEW"

    # The worker restarts and reconciles the lost acknowledgement.
    recovered = recover_expired_jobs(
        factory,
        "replacement-worker",
        settings=offline,
        comfy_client=ComfyUIClient(fake_comfy.base_url, timeout_s=2),
    )
    assert recovered == 1
    assert fake_comfy.prompt_count == 1
    result = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert result["status"] in {"QC_PENDING", "NEEDS_REVIEW", "PASSED"}
    assert result["provider_job_id"]
    assert result["artifact_id"]
    attempts = result["attempts"]
    assert len(attempts) == 1
    assert attempts[0]["prompt_graph_sha256"]


def test_lost_history_is_reported_and_never_resubmitted(api, fake_comfy):
    client, factory, settings = api
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    shot = create_referenced_shot(client, project_id, style["id"])
    job = create_comfy_job(client, project_id, shot["id"])
    with factory.begin() as db:
        row = db.get(Job, job["id"])
        row.status = "RUNNING"
        row.provider_job_id = "prompt-vanished"
        row.lease_owner = "crashed"
        row.lease_expires_at = utcnow() - timedelta(seconds=1)
        db.add(
            JobAttempt(
                job_id=row.id,
                attempt_no=1,
                request_fingerprint="d" * 64,
                status="ACKNOWLEDGED",
                provider_job_id="prompt-vanished",
                lease_owner="crashed",
                lease_expires_at=row.lease_expires_at,
            )
        )
    offline = comfy_settings(settings)
    assert (
        recover_expired_jobs(
            factory,
            "replacement",
            settings=offline,
            comfy_client=ComfyUIClient(fake_comfy.base_url, timeout_s=2),
        )
        == 1
    )
    result = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert result["status"] == "NEEDS_REVIEW"
    assert result["failure_code"] == "EXTERNAL_JOB_NOT_FOUND"
    assert fake_comfy.prompt_count == 0


def test_offline_comfyui_is_reported_as_unavailable(api, fake_comfy):
    client, factory, settings = api
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    shot = create_referenced_shot(client, project_id, style["id"])
    job = create_comfy_job(client, project_id, shot["id"])
    with factory.begin() as db:
        row = db.get(Job, job["id"])
        row.status = "RUNNING"
        row.provider_job_id = "prompt-offline"
        row.lease_owner = "crashed"
        row.lease_expires_at = utcnow() - timedelta(seconds=1)
        db.add(
            JobAttempt(
                job_id=row.id,
                attempt_no=1,
                request_fingerprint="e" * 64,
                status="ACKNOWLEDGED",
                provider_job_id="prompt-offline",
                lease_owner="crashed",
                lease_expires_at=row.lease_expires_at,
            )
        )
    fake_comfy.stop()
    offline = comfy_settings(settings)
    assert (
        recover_expired_jobs(
            factory,
            "replacement",
            settings=offline,
            comfy_client=ComfyUIClient(fake_comfy.base_url, timeout_s=1),
        )
        == 1
    )
    result = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert result["status"] == "NEEDS_REVIEW"
    assert result["failure_code"] == "RECONCILIATION_UNAVAILABLE"


def test_explicit_rejection_is_a_safe_failure_not_an_uncertain_submission(api, fake_comfy, monkeypatch):
    monkeypatch.setattr("shine.worker._handoff_preflight_allowed", lambda *_: True)
    client, factory, settings = api
    fake_comfy.mode = "reject"
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    shot = create_referenced_shot(client, project_id, style["id"])
    job = create_comfy_job(client, project_id, shot["id"])
    offline = comfy_settings(settings)
    run_one(
        factory,
        settings=offline,
        worker_id="comfy-worker",
        comfy_client=ComfyUIClient(fake_comfy.base_url, timeout_s=2),
    )
    result = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert result["status"] == "REPAIR_PENDING"
    assert result["failure_code"] == "COMFY_PROMPT_REJECTED"
    assert fake_comfy.prompt_count == 0


def test_execution_error_is_reported_distinctly(api, fake_comfy, monkeypatch):
    monkeypatch.setattr("shine.worker._handoff_preflight_allowed", lambda *_: True)
    client, factory, settings = api
    fake_comfy.mode = "error"
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    shot = create_referenced_shot(client, project_id, style["id"])
    job = create_comfy_job(client, project_id, shot["id"])
    offline = comfy_settings(settings)
    run_one(
        factory,
        settings=offline,
        worker_id="comfy-worker",
        comfy_client=ComfyUIClient(fake_comfy.base_url, timeout_s=2),
    )
    assert fake_comfy.prompt_count == 1
    result = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert result["status"] == "NEEDS_REVIEW"
    assert result["failure_code"] == "COMFY_EXECUTION_ERROR"
