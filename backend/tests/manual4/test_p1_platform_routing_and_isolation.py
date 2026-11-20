"""P1 acceptance tests: platform line selection, DB isolation, cross-line guards & GPU mutex."""

from __future__ import annotations

import sqlite3
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from shine.comfyops.models import ResourceLease
from shine.comfyops.scheduler import ResourceScheduler
from shine.config import Settings
from shine.db import Base, make_engine, make_session_factory
from shine.main import create_app
from shine.models import Project, utcnow
from shine.platform_gpu import acquire_m4_gpu_lease, inspect_gpu_status, release_m4_gpu_lease

pytestmark = pytest.mark.integration

LOCAL_ACTION_HEADERS = {"x-shine-local-action": "1"}


def _build_isolated_app(tmp_path):
    db_path = tmp_path / "shine.db"
    database_url = f"sqlite:///{db_path.as_posix()}"
    engine = make_engine(database_url)
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    settings = Settings(
        data_dir=tmp_path,
        database_url=database_url,
        node_pack_gate="diagnose",
    )
    app = create_app(settings=settings, database_url=database_url, asset_root=tmp_path)
    app.state.engine.dispose()
    app.state.engine = engine
    app.state.session_factory = factory
    return app, factory, settings


def test_p1_project_line_selection_persistence_and_cross_line_guards(tmp_path):
    app, factory, settings = _build_isolated_app(tmp_path)
    with TestClient(app) as client:
        # 1. Insert a legacy project without a project_workflows row -> implicitly director7
        with factory.begin() as db:
            legacy = Project(title="历史七阶作品", description="", language="Chinese")
            db.add(legacy)
            db.flush()
            legacy_id = legacy.id

        wf_legacy = client.get(f"/api/v1/platform/projects/{legacy_id}/workflow")
        assert wf_legacy.status_code == 200
        assert wf_legacy.json()["workflow_kind"] == "director7"
        assert wf_legacy.json()["implicit"] is True

        # 2. Normal 7-stage create stays untouched
        d7_resp = client.post("/api/v1/projects", json={"title": "七阶新作品"})
        assert d7_resp.status_code == 201
        d7_id = d7_resp.json()["id"]
        assert client.get(f"/api/v1/platform/projects/{d7_id}/workflow").json()["workflow_kind"] == "director7"

        # 7-stage project cannot enter manual4 endpoints
        m4_blocked = client.get(f"/api/v1/manual4/projects/{d7_id}/overview")
        assert m4_blocked.status_code == 409
        assert m4_blocked.json()["detail"]["code"] == "WORKFLOW_LINE_MISMATCH"

        # 3. Create manual4 project atomically
        m4_resp = client.post(
            "/api/v1/platform/manual4-projects",
            headers=LOCAL_ACTION_HEADERS,
            json={"title": "四阶自主作品", "max_shot_seconds": 12.0},
        )
        assert m4_resp.status_code == 201
        m4_id = m4_resp.json()["id"]
        assert m4_resp.json()["workflow_kind"] == "manual4"

        # Verify manual4.sqlite has its own m4_projects row and shine.db has project_workflows
        m4_sqlite = tmp_path / "manual4.sqlite"
        assert m4_sqlite.is_file()
        with sqlite3.connect(m4_sqlite) as conn:
            row = conn.execute("SELECT title, max_shot_seconds FROM m4_projects WHERE id = ?", (m4_id,)).fetchone()
            assert row == ("四阶自主作品", 12.0)

        # Manual4 overview & readiness work without 7-stage calls
        ov = client.get(f"/api/v1/manual4/projects/{m4_id}/overview")
        assert ov.status_code == 200
        ov_body = ov.json()
        assert ov_body["project"]["workflow_kind"] == "manual4"
        assert ov_body["readiness"]["next_stage"] == "storyboard"
        assert len(ov_body["readiness"]["stages"]) == 4

        # Cross-line guard: manual4 project is refused on 7-stage subroutes
        for forbidden_path in (
            f"/api/v1/projects/{m4_id}/journey",
            f"/api/v1/projects/{m4_id}/assets",
            f"/api/v1/projects/{m4_id}/styles",
            f"/api/v1/projects/{m4_id}/shots",
            f"/api/v1/projects/{m4_id}/jobs",
            f"/api/v1/projects/{m4_id}/deliveries",
        ):
            r = client.get(forbidden_path)
            assert r.status_code == 409, f"{forbidden_path} returned {r.status_code}"
            assert r.json()["detail"]["code"] == "PROJECT_IS_MANUAL4_NOT_DIRECTOR7"

        # Switching workflow kind on an existing project is refused
        sw1 = client.post(
            f"/api/v1/platform/projects/{m4_id}/workflow",
            json={"workflow_kind": "director7"},
        )
        assert sw1.status_code == 409
        assert sw1.json()["detail"]["code"] == "WORKFLOW_SELECTION_IMMUTABLE"

        sw2 = client.post(
            f"/api/v1/platform/projects/{d7_id}/workflow",
            json={"workflow_kind": "manual4"},
        )
        assert sw2.status_code == 409

    # 4. Restart simulation: recreate app on same tmp_path, confirm state restores cleanly
    app2 = create_app(settings=settings, database_url=settings.database_url, asset_root=tmp_path)
    with TestClient(app2) as client2:
        listed = {row["id"]: row for row in client2.get("/api/v1/platform/projects").json()}
        assert listed[legacy_id]["workflow_kind"] == "director7"
        assert listed[d7_id]["workflow_kind"] == "director7"
        assert listed[m4_id]["workflow_kind"] == "manual4"
        assert client2.get(f"/api/v1/manual4/projects/{m4_id}/overview").status_code == 200
        assert client2.get(f"/api/v1/projects/{m4_id}/journey").status_code == 409


def test_p1_atomic_rollback_if_manual4_db_fails(tmp_path):
    app, factory, _ = _build_isolated_app(tmp_path)
    # Break m4_session_factory deliberately to verify platform DB rollback
    app.state.m4_session_factory = lambda: (_ for _ in ()).throw(RuntimeError("simulated m4 db failure"))
    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/platform/manual4-projects",
            headers=LOCAL_ACTION_HEADERS,
            json={"title": "不应残留半成品"},
        )
        assert resp.status_code == 500
        projects = client.get("/api/v1/platform/projects").json()
        assert projects == []


def test_p1_platform_gpu_mutex_between_director7_and_manual4(tmp_path):
    app, factory, settings = _build_isolated_app(tmp_path)
    app.state.m4_engine.dispose()
    app.state.engine.dispose()

    # Re-open cleanly for scheduler test
    engine = make_engine(settings.database_url)
    factory = make_session_factory(engine)
    scheduler = ResourceScheduler()

    # Case A: director7 holds lease -> manual4 refused
    with factory.begin() as db:
        d7_lease = scheduler.enqueue(db, "h3:job-701", 16384, exclusive=True, priority=10)
        d7_lease_id = d7_lease.id
    with factory() as db:
        assert scheduler.admit(db, d7_lease_id) is True

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        acquire_m4_gpu_lease(factory, tmp_path, owner="m4:run-1")
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "GPU_BUSY_OTHER_LINE"
    assert exc_info.value.detail["blocking_line"] == "director7"

    with factory.begin() as db:
        scheduler.release(db, d7_lease_id, "h3:job-701")

    # Case B: manual4 holds lease -> director7 ResourceScheduler.admit refuses
    m4_lease = acquire_m4_gpu_lease(factory, tmp_path, owner="m4:run-2")
    with factory.begin() as db:
        d7_waiting = scheduler.enqueue(db, "h3:job-702", 16384, exclusive=True, priority=10)
        d7_waiting_id = d7_waiting.id
    with factory() as db:
        assert scheduler.admit(db, d7_waiting_id) is False

    # Case C: expired ACTIVE lease becomes RECONCILE_REQUIRED and blocks blind re-acquire
    with factory.begin() as db:
        row = db.get(ResourceLease, m4_lease["lease_id"])
        row.expires_at = utcnow() - timedelta(seconds=5)
    status = inspect_gpu_status(factory, tmp_path)
    assert status["busy"] is True
    assert len(status["reconcile_required"]) == 1

    with pytest.raises(HTTPException) as exc2:
        acquire_m4_gpu_lease(factory, tmp_path, owner="m4:run-3")
    assert exc2.value.detail["code"] == "GPU_RECONCILE_REQUIRED"

    assert release_m4_gpu_lease(factory, tmp_path, lease_id=m4_lease["lease_id"], owner="m4:run-2", reconciled=True)
    with factory() as db:
        assert scheduler.admit(db, d7_waiting_id) is True

    engine.dispose()
