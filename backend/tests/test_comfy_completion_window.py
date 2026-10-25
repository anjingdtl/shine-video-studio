"""Regression tests for the ComfyUI completion window.

ComfyUI removes a finished prompt from its queue before the matching history
record becomes readable. A worker that samples exactly inside that window sees
neither the queue entry nor the history entry. Treating that single
observation as proof that the render is gone discards a render that actually
succeeded, which is exactly what happened to the first real local H3 shot on
this machine.
"""

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
    create_shot,
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


def worker_settings(settings: Settings) -> Settings:
    return Settings(
        data_dir=settings.data_dir,
        database_url=settings.database_url,
        secret_store_dir=settings.secret_store_dir,
        ffprobe_path="missing-ffprobe",
        ffmpeg_path="missing-ffmpeg",
        render_provider="comfyui",
    )


def test_worker_waits_out_the_completion_window_instead_of_failing(api, fake_comfy):
    """The history record appears a moment after the prompt leaves the queue."""
    client, factory, settings = api
    fake_comfy.output_bytes = make_test_video_bytes(duration_s=6)
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    shot = create_shot(client, project_id, style["id"])
    job = create_comfy_job(client, project_id, shot["id"])

    # The first three history reads behave like the ComfyUI completion window.
    fake_comfy.hide_history_for = 3

    run_one(
        factory,
        None,
        "window-worker",
        settings=worker_settings(settings),
        comfy_client=ComfyUIClient(fake_comfy.base_url, timeout_s=2),
    )

    result = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert result["failure_code"] != "COMFY_JOB_NOT_FOUND", result
    assert result["artifact_id"], result
    artifacts = client.get(f"/api/v1/jobs/{job['id']}/artifacts").json()
    assert len(artifacts) == 1
    assert artifacts[0]["is_mock"] is False
    # The external task was submitted exactly once.
    assert fake_comfy.prompt_count == 1


def test_stranded_job_is_reconciled_against_external_history(api, fake_comfy):
    """A job stranded by the completion window is collected, never resubmitted."""
    client, factory, settings = api
    fake_comfy.output_bytes = make_test_video_bytes(duration_s=6)
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    shot = create_shot(client, project_id, style["id"])
    job = create_comfy_job(client, project_id, shot["id"])

    # Simulate the stranded state: the worker gave up while the render was
    # actually finishing, and the external history now holds the result.
    fake_comfy.prompts["prompt-1"] = {"prompt": {"1": {"class_type": "H3BaseSampler", "inputs": {}}}}
    with factory.begin() as db:
        row = db.get(Job, job["id"])
        row.status = "NEEDS_REVIEW"
        row.failure_code = "COMFY_JOB_NOT_FOUND"
        row.provider_job_id = "prompt-1"
        row.lease_owner = None
        row.lease_expires_at = None
        db.add(
            JobAttempt(
                job_id=row.id,
                attempt_no=1,
                request_fingerprint="f" * 64,
                status="FAILED",
                failure_code="COMFY_JOB_NOT_FOUND",
                provider_job_id="prompt-1",
                lease_owner="stopped-worker",
                lease_expires_at=utcnow() - timedelta(seconds=1),
            )
        )

    assert (
        recover_expired_jobs(
            factory,
            "reconciler",
            settings=worker_settings(settings),
            comfy_client=ComfyUIClient(fake_comfy.base_url, timeout_s=2),
        )
        == 1
    )
    # The media was collected from the existing external task, not re-rendered.
    assert fake_comfy.prompt_count == 0
    result = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert result["artifact_id"], result
    artifacts = client.get(f"/api/v1/jobs/{job['id']}/artifacts").json()
    assert len(artifacts) == 1
    assert artifacts[0]["is_mock"] is False

    events = client.get(f"/api/v1/projects/{project_id}/events?limit=200").json()["items"]
    assert any(event["event_type"] == "COMFY_RECONCILED_HISTORY" for event in events)
