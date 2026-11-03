from __future__ import annotations

from datetime import timedelta

import pytest

from shine.models import WorkerInstance, utcnow
from shine.providers import MockH3Provider
from shine.worker import run_one
from tests.conftest import create_job, create_project, create_shot, freeze_style
from tests.fake_http import FakeComfyServer


@pytest.fixture
def fake_comfy():
    server = FakeComfyServer().start()
    try:
        yield server
    finally:
        server.stop()


class ExplodingProvider(MockH3Provider):
    def submit(self, idempotency_key, scenario):
        raise RuntimeError("simulated provider crash")


def test_system_status_distinguishes_api_worker_mock_and_real_h3(studio):
    client, factory, _root, _settings = studio
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    shot = create_shot(client, project_id, style["id"])
    job = create_job(client, project_id, shot["id"])
    assert run_one(factory, MockH3Provider(), "worker-health") == job["id"]

    status = client.get("/api/v1/system/status").json()
    assert status["worker_online"] is True
    assert status["mock_provider_available"] is True
    assert status["h3_connected"] is False
    assert status["render_mode"] == "mock"
    assert status["pending_jobs"] == 0
    assert status["failed_jobs"] == 0
    assert status["blocked_jobs"] == 0
    worker = status["workers"][0]
    assert worker["worker_id"] == "worker-health"
    assert worker["processed_count"] == 1
    assert worker["last_heartbeat_at"]
    assert client.get("/health").json()["render_provider"] == "mock"


def test_system_status_reports_h3_connected_only_with_a_live_renderer(fake_comfy, monkeypatch, tmp_path):
    """h3_connected must reflect a live ComfyUI answer in comfyui mode, and
    must never claim a real renderer from mock mode or a dead endpoint."""
    import dataclasses

    from fastapi.testclient import TestClient

    from shine.config import Settings
    from shine.db import Base, make_engine
    from shine.main import create_app

    db_path = tmp_path / "probe.sqlite3"
    engine = make_engine(f"sqlite:///{db_path.as_posix()}")
    Base.metadata.create_all(engine)
    engine.dispose()

    settings = dataclasses.replace(
        Settings.from_env(), render_provider="comfyui", comfyui_base_url=fake_comfy.base_url
    )
    monkeypatch.setattr(Settings, "from_env", staticmethod(lambda: settings))
    with TestClient(create_app(database_url=f"sqlite:///{db_path.as_posix()}")) as client:
        assert client.get("/api/v1/system/status").json()["h3_connected"] is True
        assert client.get("/health").json()["render_provider"] == "comfyui"
        dead = dataclasses.replace(settings, comfyui_base_url="http://127.0.0.1:9")
        monkeypatch.setattr(Settings, "from_env", staticmethod(lambda: dead))
        with TestClient(create_app(database_url=f"sqlite:///{db_path.as_posix()}")) as dead_client:
            assert dead_client.get("/api/v1/system/status").json()["h3_connected"] is False


def test_offline_worker_is_reported_from_heartbeat_age(studio):
    client, factory, _root, _settings = studio
    create_project(client)
    with factory.begin() as db:
        db.add(
            WorkerInstance(
                worker_id="stale-worker",
                version="test",
                status="online",
                started_at=utcnow() - timedelta(minutes=5),
                last_heartbeat_at=utcnow() - timedelta(minutes=5),
            )
        )
    status = client.get("/api/v1/system/status").json()
    assert status["worker_online"] is False
    assert status["workers"][0]["online"] is False


def test_dead_worker_history_does_not_speak_for_the_system(studio):
    client, factory, _root, _settings = studio
    create_project(client)
    with factory.begin() as db:
        db.add(
            WorkerInstance(
                worker_id="fresh-worker",
                version="test",
                status="online",
                started_at=utcnow(),
                last_heartbeat_at=utcnow(),
            )
        )
        db.add(
            WorkerInstance(
                worker_id="dead-worker",
                version="test",
                status="online",
                started_at=utcnow() - timedelta(hours=30),
                last_heartbeat_at=utcnow() - timedelta(hours=30),
                last_error="job: HTTPException",
            )
        )
    status = client.get("/api/v1/system/status").json()
    assert status["last_error"] is None
    by_id = {w["worker_id"]: w for w in status["workers"]}
    assert by_id["fresh-worker"]["online"] is True
    assert by_id["dead-worker"]["online"] is False


def test_worker_exception_moves_job_to_review_without_claiming_success(studio):
    client, factory, _root, _settings = studio
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    shot = create_shot(client, project_id, style["id"])
    job = create_job(client, project_id, shot["id"])
    assert run_one(factory, ExplodingProvider(), "worker-error") == job["id"]
    result = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert result["status"] == "NEEDS_REVIEW"
    assert result["failure_code"] == "WORKER_EXCEPTION"
    assert result["attempts"][0]["status"] == "FAILED"
    assert client.get(f"/api/v1/jobs/{job['id']}/qc").json() == []
    status = client.get("/api/v1/system/status").json()
    assert status["workers"][0]["status"] == "error"
    assert "RuntimeError" in (status["workers"][0]["last_error"] or "")


def test_worker_counts_pending_running_and_blocked_jobs(studio):
    client, factory, _root, _settings = studio
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    queued_shot = create_shot(client, project_id, style["id"], title="排队")
    create_job(client, project_id, queued_shot["id"])
    blocked_shot = create_shot(client, project_id, style["id"], title="阻塞")
    blocked_job = create_job(client, project_id, blocked_shot["id"])
    with factory.begin() as db:
        from shine.models import Job

        row = db.get(Job, blocked_job["id"])
        row.status = "NEEDS_REVIEW"
        row.failure_code = "MANUAL_BLOCK"
    status = client.get("/api/v1/system/status").json()
    assert status["pending_jobs"] == 1
    assert status["blocked_jobs"] == 1
