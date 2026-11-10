from __future__ import annotations

import errno
import sqlite3
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

from shine.db import make_engine, make_session_factory
from shine.models import WorkerInstance
from shine.worker import heartbeat
from tests.conftest import PNG, create_project, make_studio


def test_long_and_windows_invalid_filenames_are_rejected(api):
    client, _factory, _settings = api
    project_id = create_project(client)
    long_name = "a" * 201 + ".png"
    response = client.post(
        f"/api/v1/projects/{project_id}/assets",
        data={"asset_kind": "image"},
        files={"file": (long_name, PNG, "image/png")},
    )
    assert response.status_code == 400
    invalid = client.post(
        f"/api/v1/projects/{project_id}/assets",
        data={"asset_kind": "image"},
        files={"file": ("bad<name>.png", PNG, "image/png")},
    )
    assert invalid.status_code == 400


def test_configured_upload_limit_and_chinese_storage_path(tmp_path):
    app, _factory, settings = make_studio(tmp_path, max_upload_bytes=16)
    with TestClient(app) as client:
        project_id = create_project(client)
        response = client.post(
            f"/api/v1/projects/{project_id}/assets",
            data={"asset_kind": "image"},
            files={"file": ("角色.png", PNG, "image/png")},
        )
        assert response.status_code == 413
    assert str(settings.data_dir).endswith("本地素材")
    app.state.engine.dispose()


def test_disk_full_is_reported_and_partial_files_are_cleaned(api, monkeypatch):
    client, _factory, settings = api
    project_id = create_project(client)
    original = Path.write_bytes

    def failing_write(self: Path, data: bytes) -> int:
        if self.name.endswith(".partial"):
            original(self, data[:1])
            raise OSError(errno.ENOSPC, "disk full")
        return original(self, data)

    monkeypatch.setattr(Path, "write_bytes", failing_write)
    response = client.post(
        f"/api/v1/projects/{project_id}/assets",
        data={"asset_kind": "image"},
        files={"file": ("角色.png", PNG, "image/png")},
    )
    assert response.status_code == 507
    assert not list(settings.data_dir.rglob("*.partial"))


def test_sqlite_wal_and_foreign_key_pragmas_are_enabled(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'pragma.db').as_posix()}")
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar() == 10000
    engine.dispose()


def test_duplicate_worker_start_does_not_create_multiple_instances(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'workers.db').as_posix()}")
    from shine.db import Base

    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    heartbeat(factory, "worker-one", status="online")
    heartbeat(factory, "worker-one", status="online")
    with factory() as db:
        rows = db.query(WorkerInstance).all()
    assert len(rows) == 1
    assert rows[0].worker_id == "worker-one"
    engine.dispose()


def test_sqlite_busy_timeout_waits_for_a_short_writer_lock(tmp_path):
    app, _factory, settings = make_studio(tmp_path)
    db_path = Path(settings.database_url.removeprefix("sqlite:///"))
    client = TestClient(app)
    connection = sqlite3.connect(db_path, timeout=15)
    connection.execute("BEGIN IMMEDIATE")
    result: list[int] = []
    started = time.monotonic()

    def create_project_while_locked() -> None:
        response = client.post("/api/v1/projects", json={"title": "等待锁"})
        result.append(response.status_code)

    worker = threading.Thread(target=create_project_while_locked)
    worker.start()
    time.sleep(0.4)
    connection.commit()
    connection.close()
    worker.join(timeout=10)
    elapsed = time.monotonic() - started
    assert result == [201]
    assert elapsed >= 0.35
    client.close()
    app.state.engine.dispose()


def test_path_traversal_filename_is_reduced_to_a_safe_leaf(studio):
    client, factory, root, settings = studio
    project_id = create_project(client)
    response = client.post(
        f"/api/v1/projects/{project_id}/assets",
        data={"asset_kind": "image"},
        files={"file": (r"..\..\..\Windows\evil.png", PNG, "image/png")},
    )
    assert response.status_code == 201
    assert response.json()["filename"] == "evil.png"
    with factory() as db:
        from shine.models import Asset

        asset = db.get(Asset, response.json()["id"])
        stored = settings.data_dir / asset.storage_path
        assert stored.parent.name == "素材"
    assert stored.is_file()
    assert "Windows" not in str(stored)
    _ = root
