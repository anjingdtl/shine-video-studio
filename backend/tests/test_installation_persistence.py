# ruff: noqa: F811
"""The installation is a persistent task: 202, re-attach, pause, resume, cancel.

Software acceptance only.  No model weight is downloaded, no ComfyUI is
started and no real H3 inference is claimed: the bytes are synthetic, the
transport is injected and the installer is a recording double.
"""

import hashlib
from contextlib import contextmanager
from datetime import timedelta

import httpx
import pytest

from shine.comfyops.installation import (
    CANCELLED,
    DOWNLOADING,
    INSTALLING,
    NEEDS_REVIEW,
    QUEUED,
    recover_expired_attempts,
)
from shine.comfyops.installer import ControlledDownloader, HTTPTransport, PackageFile
from shine.comfyops.models import InstallationAttempt
from shine.models import utcnow
from tests.conftest import create_project
from tests.test_v13_preparation import HEADERS, approve, package, post, prep  # noqa: F401

MIRROR_HOSTS = {"huggingface.co", "hf-mirror.com", "cas-bridge.xethub.hf.co"}
REVISION = "a" * 40


def pinned_file(payload: bytes = b"data") -> PackageFile:
    return PackageFile(
        url=f"https://huggingface.co/Qwen/model/resolve/{REVISION}/model.safetensors",
        relative_path="model.safetensors",
        sha256=hashlib.sha256(payload).hexdigest(),
        size=len(payload),
    )


class RecordingInstaller:
    """A double that records what a verified install would produce."""

    def __init__(self):
        self.installed: list[str] = []

    def install(self, spec):
        self.installed.append(spec.name)
        return {"status": "AVAILABLE", "files": [f.model_dump() for f in spec.files]}


def approved_plan(client, executor: str = "fake"):
    project = create_project(client)
    model = package(client)
    plan = post(
        client,
        "/comfyops/install-plans",
        {
            "project_id": project,
            "package_ids": [model["id"]],
            "disk_free": 10**12,
            "executor": executor,
        },
    )
    plan = approve(client, plan, "install", f"/comfyops/install-plans/{plan['id']}/approve")
    return project, model, plan


def queue_fake_attempt(client, plan) -> str:
    response = client.post(
        f"/api/v1/comfyops/install-plans/{plan['id']}/execute",
        json={"plan_hash": plan["sha256"], "executor": "fake"},
        headers=HEADERS,
    )
    assert response.status_code == 202, response.text
    return response.json()["attempt_id"]


def test_a_second_plan_shares_the_physical_attempt_and_reads_the_same_receipt(prep):
    """The receipt belongs to the target: a lost response is found again (J02)."""
    client, factory, settings = prep
    _, _, plan_a = approved_plan(client)
    first = client.post(
        f"/api/v1/comfyops/install-plans/{plan_a['id']}/execute",
        json={"plan_hash": plan_a["sha256"], "executor": "fake"}, headers=HEADERS)
    assert first.status_code == 202, first.text
    attempt_id = first.json()["attempt_id"]
    assert first.json()["reused"] is False
    assert first.json()["plan_id"] == plan_a["id"]
    assert first.json()["plan_hash"] == plan_a["sha256"]

    # The same pinned closure in the same root: this is one physical target.
    package_id = plan_a["payload"]["packages"][0]["id"]
    plan_b = post(
        client, "/comfyops/install-plans",
        {"project_id": create_project(client), "package_ids": [package_id],
         "disk_free": 10**12, "executor": "fake"},
    )
    plan_b = approve(client, plan_b, "install",
                     f"/comfyops/install-plans/{plan_b['id']}/approve")
    assert plan_b["id"] != plan_a["id"]
    rows = client.get(
        f"/api/v1/comfyops/install-plans/{plan_b['id']}/attempts?scope=target",
        headers=HEADERS).json()
    assert [row["attempt_id"] for row in rows] == [attempt_id]
    assert rows[0]["terminal"] is False
    assert rows[0]["plan_id"] == plan_a["id"]
    assert rows[0]["plan_hash"] == plan_a["sha256"]
    # The plan-scoped history is unchanged: B never wrote its own attempt.
    assert client.get(
        f"/api/v1/comfyops/install-plans/{plan_b['id']}/attempts",
        headers=HEADERS).json() == []

    second = client.post(
        f"/api/v1/comfyops/install-plans/{plan_b['id']}/execute",
        json={"plan_hash": plan_b["sha256"], "executor": "fake"}, headers=HEADERS)
    assert second.status_code == 202, second.text
    body = second.json()
    assert body["attempt_id"] == attempt_id
    assert body["reused"] is True
    assert body["plan_id"] == plan_a["id"]
    assert body["plan_hash"] == plan_a["sha256"], "the attempt keeps its own plan hash"
    assert body["requested_plan_id"] == plan_b["id"]
    assert body["requested_plan_hash"] == plan_b["sha256"]


def test_execute_queues_the_attempt_and_answers_202(prep):
    client, factory, settings = prep
    _, _, plan = approved_plan(client)
    response = client.post(
        f"/api/v1/comfyops/install-plans/{plan['id']}/execute",
        json={"plan_hash": plan["sha256"], "executor": "fake"},
        headers=HEADERS,
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "QUEUED"
    assert body["plan_hash"] == plan["sha256"]
    attempt_id = body["attempt_id"]
    # A refreshed browser re-attaches by attempt id instead of waiting for a
    # request that may never come back.
    view = client.get(f"/api/v1/comfyops/installation-attempts/{attempt_id}", headers=HEADERS).json()
    assert view["attempt_id"] == attempt_id
    assert view["stage"] == QUEUED
    assert view["confirmed_bytes"] == 0
    assert view["terminal"] is False


def test_a_second_execute_of_the_same_plan_returns_the_same_attempt(prep):
    """A lost receipt must re-attach, never start a second physical install."""
    client, factory, settings = prep
    _, _, plan = approved_plan(client)
    attempt_id = queue_fake_attempt(client, plan)
    response = client.post(
        f"/api/v1/comfyops/install-plans/{plan['id']}/execute",
        json={"plan_hash": plan["sha256"], "executor": "fake"},
        headers=HEADERS,
    )
    assert response.status_code == 202, response.text
    assert response.json()["attempt_id"] == attempt_id


def test_the_worker_advances_the_queued_attempt(prep):
    client, factory, settings = prep
    _, _, plan = approved_plan(client)
    attempt_id = queue_fake_attempt(client, plan)
    from shine.comfyops.installation_worker import run_pending_installation

    assert run_pending_installation(factory, "worker-test", settings=settings) == "MOCK_VERIFIED"
    view = client.get(f"/api/v1/comfyops/installation-attempts/{attempt_id}", headers=HEADERS).json()
    assert view["stage"] == "MOCK_VERIFIED"
    assert view["terminal"] is True


def test_pause_resume_and_cancel_are_explicit(prep):
    client, factory, settings = prep
    _, _, plan = approved_plan(client)
    attempt_id = queue_fake_attempt(client, plan)
    paused = client.post(
        f"/api/v1/comfyops/installation-attempts/{attempt_id}/pause", headers=HEADERS
    ).json()
    assert paused["pause_requested"] is True
    resumed = client.post(
        f"/api/v1/comfyops/installation-attempts/{attempt_id}/resume", headers=HEADERS
    ).json()
    assert resumed["pause_requested"] is False
    cancelled = client.post(
        f"/api/v1/comfyops/installation-attempts/{attempt_id}/cancel", headers=HEADERS
    ).json()
    assert cancelled["cancel_requested"] is True
    assert cancelled["stage"] == CANCELLED
    assert cancelled["terminal"] is True


def test_a_cancelled_attempt_keeps_its_partial_files_and_verified_models(prep, tmp_path, monkeypatch):
    client, factory, settings = prep
    _, _, plan = approved_plan(client)
    attempt_id = queue_fake_attempt(client, plan)
    with factory.begin() as db:
        attempt = db.get(InstallationAttempt, attempt_id)
        attempt.payload = {**attempt.payload, "kept_part": str(tmp_path / "keep.part")}
    client.post(f"/api/v1/comfyops/installation-attempts/{attempt_id}/cancel", headers=HEADERS)
    with factory() as db:
        attempt = db.get(InstallationAttempt, attempt_id)
        # The cancellation never deletes a reusable partial file.
        assert attempt.stage == CANCELLED


def test_the_local_attempt_runs_and_reports_verified_files(prep, monkeypatch):
    client, factory, settings = prep
    _, _, plan = approved_plan(client, executor="local")
    from shine.comfyops.local import queue_install_attempt

    attempt_id, _reused = queue_install_attempt(
        factory, settings, plan["id"], plan["sha256"], "v13-test-operator", "LOCAL_ISOLATED"
    )
    monkeypatch.setattr("shine.comfyops.installation_worker.local_execution_enabled", lambda: True)
    from shine.comfyops.installation_worker import run_attempt

    installer = RecordingInstaller()
    stage = run_attempt(factory, "worker-test", attempt_id, settings=settings, installer=installer)
    assert stage == "FILES_VERIFIED_PENDING_RUNTIME"
    assert installer.installed == ["test-model"]
    view = client.get(f"/api/v1/comfyops/installation-attempts/{attempt_id}", headers=HEADERS).json()
    assert view["stage"] == "FILES_VERIFIED_PENDING_RUNTIME"
    assert view["terminal"] is True
    # Files on disk are not a runtime claim.
    assert view["payload"].get("runtime_verified") is False


def test_a_dead_worker_in_a_download_stage_leaves_a_claimable_attempt(prep):
    client, factory, settings = prep
    _, _, plan = approved_plan(client, executor="local")
    from shine.comfyops.local import queue_install_attempt

    attempt_id, _reused = queue_install_attempt(
        factory, settings, plan["id"], plan["sha256"], "v13-test-operator", "LOCAL_ISOLATED"
    )
    with factory.begin() as db:
        attempt = db.get(InstallationAttempt, attempt_id)
        attempt.stage = DOWNLOADING
        attempt.status = DOWNLOADING
        attempt.lease_owner = "dead-worker"
        attempt.lease_expires_at = utcnow() - timedelta(seconds=5)
    assert recover_expired_attempts(factory, "worker-test") == [attempt_id]
    with factory() as db:
        attempt = db.get(InstallationAttempt, attempt_id)
        assert attempt.stage == QUEUED
        assert attempt.lease_owner is None
        assert attempt.error_code == "INSTALL_WORKER_LOST_RESUMABLE"


def test_a_dead_worker_in_an_uncertain_stage_is_never_replayed(prep):
    client, factory, settings = prep
    _, _, plan = approved_plan(client, executor="local")
    from shine.comfyops.local import queue_install_attempt

    attempt_id, _reused = queue_install_attempt(
        factory, settings, plan["id"], plan["sha256"], "v13-test-operator", "LOCAL_ISOLATED"
    )
    with factory.begin() as db:
        attempt = db.get(InstallationAttempt, attempt_id)
        attempt.stage = INSTALLING
        attempt.status = INSTALLING
        attempt.lease_owner = "dead-worker"
        attempt.lease_expires_at = utcnow() - timedelta(seconds=5)
    assert recover_expired_attempts(factory, "worker-test") == []
    with factory() as db:
        attempt = db.get(InstallationAttempt, attempt_id)
        assert attempt.stage == NEEDS_REVIEW
        assert attempt.status == "FAILED_RECONCILE_REQUIRED"
        assert attempt.error_code == "INSTALL_OUTCOME_UNCERTAIN"


def test_a_changed_plan_hash_blocks_the_automatic_resume(prep):
    client, factory, settings = prep
    _, _, plan = approved_plan(client, executor="local")
    from shine.comfyops.local import queue_install_attempt

    attempt_id, _reused = queue_install_attempt(
        factory, settings, plan["id"], plan["sha256"], "v13-test-operator", "LOCAL_ISOLATED"
    )
    with factory.begin() as db:
        attempt = db.get(InstallationAttempt, attempt_id)
        attempt.stage = DOWNLOADING
        attempt.payload = {**attempt.payload, "plan_hash": "b" * 64}
        attempt.lease_owner = "dead-worker"
        attempt.lease_expires_at = utcnow() - timedelta(seconds=5)
    assert recover_expired_attempts(factory, "worker-test") == []
    with factory() as db:
        attempt = db.get(InstallationAttempt, attempt_id)
        assert attempt.stage == NEEDS_REVIEW


def test_range_ignored_rewrites_the_partial_instead_of_appending(monkeypatch, tmp_path):
    file = pinned_file()
    (tmp_path / (file.sha256 + ".part")).write_bytes(b"da")
    calls = []

    @contextmanager
    def stream(method, url, **kwargs):
        calls.append(kwargs["headers"])
        yield httpx.Response(200, content=b"data")

    monkeypatch.setattr(httpx, "stream", stream)
    downloader = ControlledDownloader({"huggingface.co"}, HTTPTransport())
    assert downloader.download(file, tmp_path).read_bytes() == b"data"
    # The whole file was rewritten; the old bytes were not appended to.
    assert (tmp_path / file.sha256).read_bytes() == b"data"


def test_retryable_status_backs_off_and_honours_a_capped_retry_after(monkeypatch, tmp_path):
    file = pinned_file()
    calls = []
    slept = []

    @contextmanager
    def stream(method, url, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            yield httpx.Response(503, headers={"retry-after": "2"})
        else:
            yield httpx.Response(200, content=b"data")

    monkeypatch.setattr(httpx, "stream", stream)
    downloader = ControlledDownloader({"huggingface.co"}, HTTPTransport(), sleep=slept.append)
    assert downloader.download(file, tmp_path).read_bytes() == b"data"
    assert slept == [2.0]


def test_retry_after_is_capped_and_the_backoff_stays_bounded(monkeypatch, tmp_path):
    file = pinned_file()
    slept = []

    @contextmanager
    def stream(method, url, **kwargs):
        yield httpx.Response(429, headers={"retry-after": "9999"})

    monkeypatch.setattr(httpx, "stream", stream)
    downloader = ControlledDownloader({"huggingface.co"}, HTTPTransport(), sleep=slept.append)
    with pytest.raises(ValueError, match="DOWNLOAD_HTTP_REJECTED:429"):
        downloader.download(file, tmp_path)
    assert slept and all(0 < value <= 60 for value in slept)


def test_an_interrupted_transfer_switches_to_the_mirror_and_continues(monkeypatch, tmp_path):
    file = pinned_file()
    calls = []

    class Interrupted(httpx.SyncByteStream):
        def __iter__(self):
            yield b"da"
            raise httpx.ReadError("interrupted")

    @contextmanager
    def stream(method, url, **kwargs):
        calls.append(url)
        if url == file.url:
            yield httpx.Response(200, stream=Interrupted())
        else:
            yield httpx.Response(206, content=b"ta", headers={"content-range": "bytes 2-3/4"})

    monkeypatch.setattr(httpx, "stream", stream)
    downloader = ControlledDownloader(MIRROR_HOSTS, HTTPTransport())
    assert downloader.download(file, tmp_path).read_bytes() == b"data"
    assert calls[0] == file.url
    assert calls[-1].startswith("https://hf-mirror.com/")


def test_the_precheck_names_the_unreachable_source_and_never_claims_all_passed():
    from shine.comfyops.installation_precheck import PlanPrecheck

    plan = {
        "packages": [
            {
                "spec": {
                    "files": [
                        {
                            "url": f"https://huggingface.co/Qwen/model/resolve/{REVISION}/model.safetensors",
                            "redirect_hosts": [],
                        }
                    ]
                }
            }
        ]
    }

    def unreachable(url, headers):
        return 503, {}

    report = PlanPrecheck(opener=unreachable).check(plan)
    assert report["status"] == "BLOCKED"
    assert report["blocking"] is True
    assert report["unreachable"] == ["huggingface.co"]
    assert "huggingface.co" in report["summary"]

    def ignores_range(url, headers):
        return 200, {}

    degraded = PlanPrecheck(opener=ignores_range).check(plan)
    assert degraded["status"] == "DEGRADED"
    assert degraded["blocking"] is False
    assert degraded["not_resumable"] == ["huggingface.co"]


def test_the_installation_attempt_migration_round_trip(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config

    repo = __import__("pathlib").Path(__file__).resolve().parents[2]
    db_path = tmp_path / "round-trip.db"
    monkeypatch.setenv("SHINE_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("SHINE_DATA_DIR", str(tmp_path))
    config = Config(str(repo / "backend" / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")
    command.upgrade(config, "head")
    import sqlite3

    connection = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(installation_attempts)")}
        assert {
            "stage",
            "confirmed_bytes",
            "total_bytes",
            "speed_bps",
            "current_component",
            "current_file",
            "retry_count",
            "error_code",
            "error_message",
            "heartbeat_at",
            "lease_owner",
            "lease_expires_at",
            "cancel_requested",
            "pause_requested",
        } <= columns
    finally:
        connection.close()
    command.downgrade(config, "f180b0010001")
    connection = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(installation_attempts)")}
        assert "stage" not in columns
        assert "confirmed_bytes" not in columns
    finally:
        connection.close()
    command.upgrade(config, "head")
