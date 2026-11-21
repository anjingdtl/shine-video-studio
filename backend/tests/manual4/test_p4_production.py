"""P4 acceptance tests: manual H3 prompts, execution snapshots, worker state machine & GPU coordination."""

from __future__ import annotations

import os
import sqlite3
import struct
import zlib
from contextlib import contextmanager
from datetime import timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from shine.comfyops.models import ResourceLease
from shine.config import Settings
from shine.db import Base, make_engine, make_session_factory
from shine.main import create_app
from shine.manual4.materials.probe import make_mp4_fixture
from shine.manual4.storage.models import M4Job, M4JobAttempt, M4ProductionRun, M4WorkerInstance, m4_utcnow
from shine.manual4.worker import execute_next_m4_run, reconcile_stalled_m4_runs
from shine.platform_gpu import acquire_m4_gpu_lease

pytestmark = pytest.mark.integration

LOCAL_ACTION_HEADERS = {"x-shine-local-action": "1"}


def _minimal_png(width: int = 16, height: int = 16) -> bytes:
    def _chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw_scanlines = b"".join(b"\x00" + (b"\x20\x60\xd0" * width) for _ in range(height))
    idat = zlib.compress(raw_scanlines)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")


def _setup_m4_client(tmp_path):
    db_path = tmp_path / "shine.db"
    database_url = f"sqlite:///{db_path.as_posix()}"
    engine = make_engine(database_url)
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    settings = Settings(data_dir=tmp_path, database_url=database_url, node_pack_gate="diagnose")
    app = create_app(settings=settings, database_url=database_url, asset_root=tmp_path)
    app.state.engine.dispose()
    app.state.engine = engine
    app.state.session_factory = factory
    return app, tmp_path


def _prepare_two_shots_materials_ready(client: TestClient) -> tuple[str, list[dict]]:
    pid = client.post(
        "/api/v1/platform/manual4-projects",
        headers=LOCAL_ACTION_HEADERS,
        json={"title": "四阶排片与 Worker 测试", "max_shot_seconds": 15.0},
    ).json()["id"]
    raw_text = (
        "第1集：雨夜重逢（目标总时长：12s）\n"
        "第1镜（6s）：雨夜窄巷中林晚撑伞回头。\n"
        "人物：林晚\n"
        "第2镜（6s）：林晚独自走近路灯。\n"
        "人物：林晚\n"
    )
    client.post(
        f"/api/v1/manual4/projects/{pid}/storyboard/import",
        headers=LOCAL_ACTION_HEADERS,
        data={"text": raw_text, "filename": "plan.txt"},
    )
    client.post(
        f"/api/v1/manual4/projects/{pid}/storyboard/confirm",
        headers=LOCAL_ACTION_HEADERS,
        json={"acknowledge_duration_mismatch": False},
    )
    mat = client.get(f"/api/v1/manual4/projects/{pid}/materials/requirements").json()
    req_id = mat["requirements"][0]["id"]
    png_bytes = _minimal_png()
    up = client.post(
        f"/api/v1/manual4/projects/{pid}/assets/upload",
        headers=LOCAL_ACTION_HEADERS,
        data={"family": "character", "entity_name": "林晚", "requirement_id": req_id},
        files={"file": ("linwan.png", png_bytes, "image/png")},
    ).json()["asset"]
    prom = client.post(
        f"/api/v1/manual4/projects/{pid}/assets/{up['id']}/promote",
        headers=LOCAL_ACTION_HEADERS,
        json={},
    ).json()
    shots = prom["state"]["shots"]
    return pid, shots


def test_p4_manual_prompt_invalidation_refuses_fallback_and_worker_executes_and_retakes(tmp_path):
    app, data_dir = _setup_m4_client(tmp_path)
    with TestClient(app) as client:
        pid, shots = _prepare_two_shots_materials_ready(client)
        s1_id, s2_id = shots[0]["id"], shots[1]["id"]

        # Freeze manual prompts for both shots (even with Chinese non-blocking lint warning)
        p1_text = "subject_definitions:\n<Subject 1>: 林晚\ndetailed_description:\n林晚在雨夜撑伞回头看镜头。"
        p2_text = "subject_definitions:\n<Subject 1>: 林晚\ndetailed_description:\nClose-up under warm streetlamp."
        f1 = client.post(
            f"/api/v1/manual4/projects/{pid}/shots/{s1_id}/prompt/freeze",
            headers=LOCAL_ACTION_HEADERS,
            json={"prompt_text": p1_text},
        )
        assert f1.status_code == 200
        assert f1.json()["prompt"]["status"] == "FROZEN"
        assert any(w["code"] == "H3_ENGLISH" for w in f1.json()["prompt"]["warnings"])

        f2 = client.post(
            f"/api/v1/manual4/projects/{pid}/shots/{s2_id}/prompt/freeze",
            headers=LOCAL_ACTION_HEADERS,
            json={"prompt_text": p2_text},
        )
        assert f2.status_code == 200

        # Invalid accelerator turbo4 must be refused explicitly
        bad_acc = client.post(
            f"/api/v1/manual4/projects/{pid}/production/snapshot/preview",
            headers=LOCAL_ACTION_HEADERS,
            json={"scope_kind": "project", "accelerator_id": "turbo4"},
        )
        assert bad_acc.status_code == 422
        assert bad_acc.json()["detail"]["code"] == "M4_ACCELERATOR_UNVERIFIED"

        # Duration change invalidates shot1's frozen prompt -> run creation REFUSES (no compiler fallback!)
        sb = client.get(f"/api/v1/manual4/projects/{pid}/storyboard/draft").json()["draft"]
        edited = dict(sb["payload"])
        edited["episodes"][0]["shots"][0]["target_seconds"] = 7.0
        client.put(
            f"/api/v1/manual4/projects/{pid}/storyboard/draft",
            headers=LOCAL_ACTION_HEADERS,
            json={"expected_revision": sb["revision"], "payload": edited},
        )
        conf2 = client.post(
            f"/api/v1/manual4/projects/{pid}/storyboard/confirm",
            headers=LOCAL_ACTION_HEADERS,
            json={"acknowledge_duration_mismatch": True},
        )
        assert conf2.status_code == 200
        assert "ep01-s01" in conf2.json()["invalidated_prompts"]

        # Note: after storyboard confirm, new M4Shot IDs were materialized for rev 2
        prod_state = client.get(f"/api/v1/manual4/projects/{pid}/production/state").json()
        rev2_s1 = prod_state["episodes"][0]["shots"][0]
        rev2_s2 = prod_state["episodes"][0]["shots"][1]
        assert rev2_s1["prompt_valid_for_run"] is False
        assert rev2_s2["prompt_valid_for_run"] is True

        blocked_run = client.post(
            f"/api/v1/manual4/projects/{pid}/production/runs",
            headers=LOCAL_ACTION_HEADERS,
            json={"scope_kind": "project", "provider": "mock"},
        )
        assert blocked_run.status_code == 409
        assert blocked_run.json()["detail"]["code"] == "M4_PROMPT_NOT_FROZEN"

        # Re-freeze rev2_s1 manually with updated text -> now snapshot & run creation succeed
        p1_v2_text = "subject_definitions:\n<Subject 1>: 林晚\ndetailed_description:\nEdited 7s manual H3 prompt."
        assert (
            client.post(
                f"/api/v1/manual4/projects/{pid}/shots/{rev2_s1['id']}/prompt/freeze",
                headers=LOCAL_ACTION_HEADERS,
                json={"prompt_text": p1_v2_text},
            ).status_code
            == 200
        )

        created = client.post(
            f"/api/v1/manual4/projects/{pid}/production/runs",
            headers=LOCAL_ACTION_HEADERS,
            json={
                "scope_kind": "project",
                "accelerator_id": "pdd8",
                "resolution_scale": 0.5,
                "provider": "mock",
                "idempotency_key": "run-fixed-key-1",
            },
        )
        assert created.status_code == 201
        run1 = created.json()["run"]
        assert len(run1["jobs"]) == 2

        # Idempotent re-post returns the SAME run id
        dup = client.post(
            f"/api/v1/manual4/projects/{pid}/production/runs",
            headers=LOCAL_ACTION_HEADERS,
            json={
                "scope_kind": "project",
                "accelerator_id": "pdd8",
                "resolution_scale": 0.5,
                "provider": "mock",
                "idempotency_key": "run-fixed-key-1",
            },
        )
        assert dup.status_code == 201
        assert dup.json()["run"]["id"] == run1["id"]

        # Execute with worker + video_stub producing duration-matched MP4 fixtures
        seen_prompts: list[str] = []

        def _stub_video(*, shot_code, prompt_text, duration_s, width, height, **_kwargs):
            seen_prompts.append(prompt_text)
            return {
                "bytes": make_mp4_fixture(duration_s=duration_s, width=width, height=height, fps=24),
                "filename": f"{shot_code}.mp4",
                "is_mock": True,
            }

        ran_id = execute_next_m4_run(
            app.state.session_factory,
            app.state.m4_session_factory,
            app.state.settings,
            app.state.m4_root,
            worker_id="test-w1",
            video_stub=_stub_video,
        )
        assert ran_id == run1["id"]
        assert seen_prompts == [p1_v2_text, p2_text]

        after = client.get(f"/api/v1/manual4/projects/{pid}/production/state").json()
        assert after["readiness"]["stages"][2]["done"] is True
        old_s2_job = after["episodes"][0]["shots"][1]["latest_job"]
        old_s2_sha = old_s2_job["output_sha256"]

        # Single-shot retake preserves historical artifact while creating a new PASSED job
        retake = client.post(
            f"/api/v1/manual4/projects/{pid}/shots/{rev2_s2['id']}/retake",
            headers=LOCAL_ACTION_HEADERS,
            json={"seed": 123, "resolution_scale": 0.5, "provider": "mock"},
        )
        assert retake.status_code == 201
        retake_run = retake.json()["run"]
        execute_next_m4_run(
            app.state.session_factory,
            app.state.m4_session_factory,
            app.state.settings,
            app.state.m4_root,
            worker_id="test-w1",
            video_stub=_stub_video,
        )
        after_retake = client.get(f"/api/v1/manual4/projects/{pid}/production/state").json()
        assert after_retake["readiness"]["stages"][2]["done"] is True
        assert any(r["id"] == retake_run["id"] and r["status"] == "COMPLETED" for r in after_retake["runs"])
        assert (
            client.get(f"/api/v1/manual4/projects/{pid}/jobs/{old_s2_job['id']}/media").status_code == 200
        )
        assert old_s2_sha != ""

    with sqlite3.connect(data_dir / "shine.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM job_attempts").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM shots").fetchone()[0] == 0


def test_p4_worker_crash_reconciliation_and_cross_line_gpu_lease_exclusion(tmp_path):
    app, data_dir = _setup_m4_client(tmp_path)

    # 1. Cross-line GPU mutex: when director7 holds resource_leases, manual4 must refuse acquire
    with app.state.session_factory.begin() as pdb:
        pdb.add(
            ResourceLease(
                id="d7-lease-1",
                owner="d7:worker-1",
                gpu="gpu-0",
                budget_mb=16384,
                exclusive=True,
                priority=10,
                status="ACTIVE",
                heartbeat_at=m4_utcnow(),
                expires_at=m4_utcnow() + timedelta(minutes=10),
            )
        )
    with pytest.raises(HTTPException) as exc_info:
        acquire_m4_gpu_lease(app.state.session_factory, data_dir, owner="m4:worker-1")
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "GPU_BUSY_OTHER_LINE"

    # Release d7 lease cleanly
    with app.state.session_factory.begin() as pdb:
        lease = pdb.get(ResourceLease, "d7-lease-1")
        lease.status = "RELEASED"

    # 2. Simulate crashed worker with 1 RUNNING job + 1 QUEUED job -> reconcile marks RUNNING as NEEDS_REVIEW
    with TestClient(app) as client:
        pid, shots = _prepare_two_shots_materials_ready(client)
        for idx, sh in enumerate(shots):
            client.post(
                f"/api/v1/manual4/projects/{pid}/shots/{sh['id']}/prompt/freeze",
                headers=LOCAL_ACTION_HEADERS,
                json={"prompt_text": f"detailed_description:\nShot {idx + 1} crash test prompt."},
            )
        run = client.post(
            f"/api/v1/manual4/projects/{pid}/production/runs",
            headers=LOCAL_ACTION_HEADERS,
            json={"scope_kind": "project", "provider": "mock"},
        ).json()["run"]

        with app.state.m4_session_factory.begin() as mdb:
            mdb.add(
                M4WorkerInstance(
                    worker_id="dead-w",
                    pid=999999,
                    version="4.0.0",
                    status="RUNNING",
                    last_heartbeat_at=m4_utcnow() - timedelta(minutes=10),
                )
            )
            r_row = mdb.get(M4ProductionRun, run["id"])
            r_row.status = "RUNNING"
            r_row.worker_id = "dead-w"
            j0 = mdb.get(M4Job, run["jobs"][0]["id"])
            j0.status = "RUNNING"
            mdb.add(
                M4JobAttempt(
                    job_id=j0.id,
                    attempt_no=1,
                    status="RUNNING",
                    prompt_id="external-comfy-pid-99",
                    prompt_graph_sha256="abc",
                )
            )

        recovered = reconcile_stalled_m4_runs(app.state.m4_session_factory, stale_seconds=60)
        assert recovered == 1

        st = client.get(f"/api/v1/manual4/projects/{pid}/production/state").json()
        assert st["runs"][0]["status"] == "NEEDS_REVIEW"
        assert st["runs"][0]["jobs"][0]["status"] == "NEEDS_REVIEW"
        assert st["runs"][0]["jobs"][1]["status"] == "QUEUED"

        # Operator reconciles job0 -> requeue, then worker runs both cleanly
        rec = client.post(
            f"/api/v1/manual4/projects/{pid}/jobs/{run['jobs'][0]['id']}/reconcile",
            headers=LOCAL_ACTION_HEADERS,
            json={"decision": "requeue"},
        )
        assert rec.status_code == 200
        assert rec.json()["job"]["status"] == "QUEUED"


def _m4_renderer_state(mf, lease_id, owner, *, status="RECONCILE_REQUIRED"):
    from shine.manual4.storage.models import M4RendererState
    with mf.begin() as db:
        db.add(M4RendererState(
            id="comfyui-m4", status=status, port=8198,
            job_name=r"Local\ShineM4Handoff-test", pid=999999, birth="birth-x",
            lease_id=lease_id, lease_owner=owner,
        ))


def test_idle_m4_renderer_orphan_is_stopped_and_lease_released(tmp_path, monkeypatch):
    """远端报障（2026-11-21）：四阶工人空闲无任务时仍持 ACTIVE GPU 租约（一次运行后
    渲染器 terminate 未生效而 linger），runtime-orchestrator/stop 被 409
    RUNTIME_WORK_IN_FLIGHT 拒。reconcile_m4_renderer 作为唯一执行者，须停止无活跃
    运行的归属渲染器并释放租约。"""
    from shine.manual4.comfy import managed_instance
    from shine.manual4.storage.models import M4RendererState

    app, data_dir = _setup_m4_client(tmp_path)
    pf, mf = app.state.session_factory, app.state.m4_session_factory
    lease = acquire_m4_gpu_lease(pf, data_dir, owner="m4:worker-1")
    lease_id = lease["lease_id"]
    _m4_renderer_state(mf, lease_id, "m4:worker-1")

    alive = {"value": True}
    monkeypatch.setattr(managed_instance, "_renderer_is_gone", lambda state: not alive["value"])
    terminated = []

    def fake_terminate(job_name, timeout=15):
        terminated.append(job_name)
        alive["value"] = False
        return {"active_processes": 0}

    monkeypatch.setattr(managed_instance, "terminate_named_group", fake_terminate)
    result = managed_instance.reconcile_m4_renderer(pf, mf, data_dir)
    assert result["reconciled"] is True
    assert terminated == [r"Local\ShineM4Handoff-test"]
    with mf() as db:
        state = db.get(M4RendererState, "comfyui-m4")
        assert state.status == "STOPPED" and state.lease_id is None
    with pf() as db:
        assert db.get(ResourceLease, lease_id).status == "RELEASED"


def test_reconcile_never_stops_a_renderer_serving_an_active_run(tmp_path, monkeypatch):
    """有活跃运行时渲染器是真在途工作，reconcile 不得停止它，租约保留。"""
    from shine.manual4.comfy import managed_instance
    from shine.manual4.storage.models import (
        M4ExecutionSnapshot,
        M4ProductionRun,
        M4Project,
        M4RendererState,
        M4StoryboardRevision,
    )

    app, data_dir = _setup_m4_client(tmp_path)
    pf, mf = app.state.session_factory, app.state.m4_session_factory
    lease = acquire_m4_gpu_lease(pf, data_dir, owner="m4:worker-1")
    lease_id = lease["lease_id"]
    _m4_renderer_state(mf, lease_id, "m4:worker-1")
    with mf.begin() as db:
        project = M4Project(title="活跃运行保护")
        db.add(project)
        db.flush()
        revision = M4StoryboardRevision(project_id=project.id, revision=1)
        db.add(revision)
        db.flush()
        snapshot = M4ExecutionSnapshot(
            project_id=project.id, storyboard_revision_id=revision.id, snapshot_sha256="0" * 64)
        db.add(snapshot)
        db.flush()
        db.add(M4ProductionRun(
            project_id=project.id, execution_snapshot_id=snapshot.id,
            idempotency_key="active-run-1", scope_kind="project", status="RUNNING"))

    monkeypatch.setattr(managed_instance, "_renderer_is_gone", lambda state: False)

    def forbidden_terminate(job_name, timeout=15):
        raise AssertionError("an active run's renderer must never be stopped")

    monkeypatch.setattr(managed_instance, "terminate_named_group", forbidden_terminate)
    with pytest.raises(HTTPException) as exc:
        managed_instance.reconcile_m4_renderer(pf, mf, data_dir)
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "M4_RENDERER_STILL_RUNNING"
    with pf() as db:
        assert db.get(ResourceLease, lease_id).status == "ACTIVE"
    with mf() as db:
        assert db.get(M4RendererState, "comfyui-m4").status == "RECONCILE_REQUIRED"


def test_live_m4_renderer_reports_busy_not_reconciling_and_failed_run_closes_queued_jobs(tmp_path, monkeypatch):
    """BUG-2/BUG-3：活体四阶渲染器持租约时快照应为 BUSY 而非 RECONCILING；失败批次不得遗留 QUEUED 孤儿任务。"""
    from shine.manual4.storage.models import (
        M4ExecutionSnapshot,
        M4Job,
        M4ProductionRun,
        M4Project,
        M4RendererState,
        M4StoryboardRevision,
    )
    from shine.manual4.worker import execute_next_m4_run
    from shine.runtime_orchestrator.status import runtime_snapshot

    app, data_dir = _setup_m4_client(tmp_path)
    pf, mf = app.state.session_factory, app.state.m4_session_factory
    settings = app.state.settings

    lease = acquire_m4_gpu_lease(pf, data_dir, owner="m4:worker-live")
    with mf.begin() as db:
        db.add(M4RendererState(
            id="comfyui-m4", status="RUNNING", port=8198,
            job_name=r"Local\ShineM4Handoff-live", pid=os.getpid(), birth="birth-live",
            lease_id=lease["lease_id"], lease_owner="m4:worker-live",
        ))
    import shine.comfyops.installer as _inst
    monkeypatch.setattr(_inst, "process_identity", lambda pid: "birth-live" if pid == os.getpid() else "")
    with pf() as db:
        snap = runtime_snapshot(db, settings)
    assert snap["state"] == "BUSY"
    assert snap["recovery"]["required"] is False

    # BUG-3：启动异常导致运行 FAILED 时，未提交的 QUEUED 任务同步收口为 FAILED。
    with mf.begin() as db:
        project = M4Project(title="失败收口")
        db.add(project)
        db.flush()
        rev = M4StoryboardRevision(project_id=project.id, revision=1)
        db.add(rev)
        db.flush()
        ex_snap = M4ExecutionSnapshot(project_id=project.id, storyboard_revision_id=rev.id, snapshot_sha256="1" * 64)
        db.add(ex_snap)
        db.flush()
        run = M4ProductionRun(
            project_id=project.id, execution_snapshot_id=ex_snap.id,
            idempotency_key="fail-run-1", scope_kind="project", provider="comfyui", status="PENDING")
        db.add(run)
        db.flush()
        qjob = M4Job(
            project_id=project.id, run_id=run.id, episode_id="ep-1", shot_id="shot-1",
            shot_revision=1, ordinal=1, idempotency_key="fail-job-1",
            prompt_revision_id="pr-1", prompt_sha256="2" * 64,
            reference_manifest_sha256="3" * 64, target_seconds=5.0, status="QUEUED",
        )
        db.add(qjob)
        db.flush()
        run_id, job_id = run.id, qjob.id

    import shine.manual4.worker as m4_worker

    @contextmanager
    def _boom(*_a, **_k):
        raise HTTPException(504, {"code": "M4_COMFY_STARTUP_TIMEOUT", "message": "四阶 ComfyUI 启动超时"})
        yield

    from dataclasses import replace

    monkeypatch.setattr(m4_worker, "managed_m4_comfyui", _boom)
    settings = replace(settings, render_provider="comfyui")
    execute_next_m4_run(pf, mf, settings, data_dir / "manual4", worker_id="w-m4")
    with mf() as db:
        r = db.get(M4ProductionRun, run_id)
        j = db.get(M4Job, job_id)
        assert r.status == "FAILED"
        assert r.error_code == "M4_COMFY_STARTUP_TIMEOUT"
        assert j.status == "FAILED"
        assert j.failure_code == "M4_COMFY_STARTUP_TIMEOUT"
