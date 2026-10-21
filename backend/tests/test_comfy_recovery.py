from __future__ import annotations

from datetime import timedelta

import pytest

from shine.comfy.client import ComfyUIClient
from shine.config import Settings
from shine.models import Job, JobAttempt, MediaArtifact, utcnow
from shine.worker import recover_expired_jobs, run_one
from tests.conftest import (
    create_comfy_job,
    create_project,
    create_shot,
    freeze_style,
    make_test_video_bytes,
    shot_payload,
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


def crash_after_ack(factory, job_id: str, *, attempt_no: int = 1, prompt_id: str = "prompt-1") -> None:
    with factory.begin() as db:
        row = db.get(Job, job_id)
        row.status = "RUNNING"
        row.provider_job_id = prompt_id
        row.lease_owner = "crashed-worker"
        row.lease_expires_at = utcnow() - timedelta(seconds=1)
        db.add(
            JobAttempt(
                job_id=row.id,
                attempt_no=attempt_no,
                request_fingerprint="f" * 64,
                status="ACKNOWLEDGED",
                provider_job_id=prompt_id,
                lease_owner="crashed-worker",
                lease_expires_at=row.lease_expires_at,
            )
        )


def seed_completed_prompt(fake_comfy, prompt_id: str = "prompt-1") -> None:
    fake_comfy.prompts[prompt_id] = {"prompt": {"1": {"class_type": "H3BaseSampler", "inputs": {}}}}


def test_completed_external_task_is_collected_after_a_crash(api, fake_comfy):
    client, factory, settings = api
    fake_comfy.output_bytes = make_test_video_bytes(duration_s=6)
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    shot = create_shot(client, project_id, style["id"])
    job = create_comfy_job(client, project_id, shot["id"])
    seed_completed_prompt(fake_comfy)
    crash_after_ack(factory, job["id"])
    offline = worker_settings(settings)
    assert (
        recover_expired_jobs(
            factory,
            "replacement",
            settings=offline,
            comfy_client=ComfyUIClient(fake_comfy.base_url, timeout_s=2),
        )
        == 1
    )
    assert fake_comfy.prompt_count == 0
    result = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert result["artifact_id"]
    assert result["attempts"][0]["status"] == "COMPLETED"
    assert result["attempts"][0]["output_sha256"]
    artifacts = client.get(f"/api/v1/jobs/{job['id']}/artifacts").json()
    assert len(artifacts) == 1
    assert artifacts[0]["is_mock"] is False


def test_existing_media_is_reused_and_never_downloaded_twice(api, fake_comfy):
    client, factory, settings = api
    fake_comfy.output_bytes = make_test_video_bytes(duration_s=6)
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    shot = create_shot(client, project_id, style["id"])
    job = create_comfy_job(client, project_id, shot["id"])
    seed_completed_prompt(fake_comfy)
    crash_after_ack(factory, job["id"])
    offline = worker_settings(settings)
    recover_expired_jobs(
        factory,
        "replacement",
        settings=offline,
        comfy_client=ComfyUIClient(fake_comfy.base_url, timeout_s=2),
    )
    first = client.get(f"/api/v1/jobs/{job['id']}").json()
    artifact_id = first["artifact_id"]
    assert artifact_id

    # A second recovery pass must not create a second artifact.
    with factory.begin() as db:
        row = db.get(Job, job["id"])
        row.status = "QC_PENDING"
        row.lease_owner = "crashed-worker"
        row.lease_expires_at = utcnow() - timedelta(seconds=1)
    recover_expired_jobs(
        factory,
        "replacement-2",
        settings=offline,
        comfy_client=ComfyUIClient(fake_comfy.base_url, timeout_s=2),
    )
    second = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert second["artifact_id"] == artifact_id
    artifacts = client.get(f"/api/v1/jobs/{job['id']}/artifacts").json()
    assert len(artifacts) == 1
    with factory.begin() as db:
        assert db.query(MediaArtifact).filter(MediaArtifact.job_id == job["id"]).count() == 1


def test_stale_upstream_revision_is_discarded_instead_of_ingested(api, fake_comfy):
    client, factory, settings = api
    fake_comfy.output_bytes = make_test_video_bytes(duration_s=6)
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    shot = create_shot(client, project_id, style["id"])
    job = create_comfy_job(client, project_id, shot["id"])
    seed_completed_prompt(fake_comfy)
    crash_after_ack(factory, job["id"])
    revised = client.post(
        f"/api/v1/shots/{shot['id']}/revise",
        json=shot_payload(style["id"], title=shot["name"], beat="被修改的剧情")[ "spec"],
    )
    assert revised.status_code == 200, revised.text
    offline = worker_settings(settings)
    recover_expired_jobs(
        factory,
        "replacement",
        settings=offline,
        comfy_client=ComfyUIClient(fake_comfy.base_url, timeout_s=2),
    )
    result = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert result["status"] == "NEEDS_REVIEW"
    assert result["stale"] is True
    assert result["artifact_id"] is None
    assert client.get(f"/api/v1/jobs/{job['id']}/artifacts").json() == []


def test_only_one_worker_can_recover_the_same_task(api, fake_comfy):
    client, factory, settings = api
    fake_comfy.output_bytes = make_test_video_bytes(duration_s=6)
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    shot = create_shot(client, project_id, style["id"])
    job = create_comfy_job(client, project_id, shot["id"])
    seed_completed_prompt(fake_comfy)
    crash_after_ack(factory, job["id"])
    offline = worker_settings(settings)
    client_a = ComfyUIClient(fake_comfy.base_url, timeout_s=2)
    first = recover_expired_jobs(factory, "worker-a", settings=offline, comfy_client=client_a)
    second = recover_expired_jobs(factory, "worker-b", settings=offline, comfy_client=client_a)
    assert first == 1
    assert second == 0
    with factory.begin() as db:
        assert db.query(MediaArtifact).filter(MediaArtifact.job_id == job["id"]).count() == 1


def test_recovery_after_comfyui_returns_from_offline(api, fake_comfy):
    client, factory, settings = api
    fake_comfy.output_bytes = make_test_video_bytes(duration_s=6)
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    shot = create_shot(client, project_id, style["id"])
    job = create_comfy_job(client, project_id, shot["id"])
    seed_completed_prompt(fake_comfy)
    crash_after_ack(factory, job["id"])
    offline = worker_settings(settings)
    fake_comfy.stop()
    recover_expired_jobs(
        factory,
        "replacement",
        settings=offline,
        comfy_client=ComfyUIClient(fake_comfy.base_url, timeout_s=1),
    )
    blocked = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert blocked["failure_code"] == "RECONCILIATION_UNAVAILABLE"


def test_quality_gates_are_recorded_for_the_recovered_attempt(api, fake_comfy):
    client, factory, settings = api
    fake_comfy.output_bytes = make_test_video_bytes(duration_s=6)
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    shot = create_shot(client, project_id, style["id"])
    job = create_comfy_job(client, project_id, shot["id"])
    seed_completed_prompt(fake_comfy)
    crash_after_ack(factory, job["id"])
    offline = worker_settings(settings)
    recover_expired_jobs(
        factory,
        "replacement",
        settings=offline,
        comfy_client=ComfyUIClient(fake_comfy.base_url, timeout_s=2),
    )
    gates = client.get(f"/api/v1/jobs/{job['id']}/quality-gates").json()
    names = {gate["gate_name"] for gate in gates}
    assert {"technical", "semantic", "audio", "style", "final"}.issubset(names)
    final = next(gate for gate in gates if gate["gate_name"] == "final")
    assert final["status"] in {"passed", "needs_review", "failed"}
    assert final["evidence"]["gates"]


def test_worker_restart_does_not_render_again(api, fake_comfy):
    client, factory, settings = api
    fake_comfy.output_bytes = make_test_video_bytes(duration_s=6)
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    shot = create_shot(client, project_id, style["id"])
    job = create_comfy_job(client, project_id, shot["id"])
    offline = worker_settings(settings)
    assert (
        run_one(
            factory,
            settings=offline,
            worker_id="comfy-worker",
            comfy_client=ComfyUIClient(fake_comfy.base_url, timeout_s=2),
        )
        == job["id"]
    )
    assert fake_comfy.prompt_count == 1
    recover_expired_jobs(
        factory,
        "replacement",
        settings=offline,
        comfy_client=ComfyUIClient(fake_comfy.base_url, timeout_s=2),
    )
    assert fake_comfy.prompt_count == 1
