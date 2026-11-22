"""监督进程生命周期：自动退出决策、presence 安全、整栈/Worker 控制。"""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
import time
import urllib.request
from datetime import UTC
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location("shine_supervisor", SCRIPTS / "shine_supervisor.py")
supervisor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(supervisor)


def test_auto_stop_decision_matrix():
    now = 1_000_000.0
    base = dict(now=now, last_seen=now - 400, ever_seen=True, minutes=5,
                stack_running=True, blockers=[])
    assert supervisor.auto_stop_decision(**base)[0] is True
    assert supervisor.auto_stop_decision(**{**base, "minutes": 0})[0] is False
    assert supervisor.auto_stop_decision(**{**base, "ever_seen": False})[0] is False
    assert supervisor.auto_stop_decision(**{**base, "last_seen": None})[0] is False
    assert supervisor.auto_stop_decision(**{**base, "stack_running": False})[0] is False
    assert supervisor.auto_stop_decision(**{**base, "blockers": ["1 个镜头任务"]})[0] is False
    assert supervisor.auto_stop_decision(**{**base, "last_seen": now - 60})[0] is False


def test_worker_start_reads_the_record_after_taking_the_stack_guard(tmp_path, monkeypatch):
    import stop_shine

    run = tmp_path / "run"
    run.mkdir()
    record = run / "processes.json"
    record.write_text(json.dumps({"processes": []}))

    def acquire_after_stack_stop(data_dir):
        assert data_dir == tmp_path
        record.unlink()
        return None

    monkeypatch.setattr(stop_shine, "_acquire_startup_guard", acquire_after_stack_stop)
    monkeypatch.setattr(
        supervisor.subprocess, "Popen",
        lambda *args, **kwargs: pytest.fail("A stopped stack cannot start a new Worker"),
    )
    assert supervisor._start_worker(tmp_path) == (409, {"code": "STACK_NOT_RUNNING"})
    assert not record.exists()


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(supervisor, "_PRESENCE", {"last_seen": None, "ever_seen": False})
    monkeypatch.setattr(supervisor, "_LAST_STOP", None)
    handler = supervisor.Handler
    handler.data_dir = tmp_path
    handler.api_port = 8000
    handler.web_port = 5173
    handler.idle_minutes = 5
    from http.server import ThreadingHTTPServer
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", tmp_path
    httpd.shutdown()


def _post(base, path, origin=None, marker=True):
    request = urllib.request.Request(base + path, data=b"{}", method="POST")
    request.add_header("Content-Type", "application/json")
    if origin:
        request.add_header("Origin", origin)
    if marker:
        request.add_header("x-shine-local-action", "1")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=5) as response:
        return json.loads(response.read())


def test_health_withdraws_the_port_claim_when_the_stack_is_dead(server):
    """残栈 supervisor 不得继续声明机器入口端口（验收接管事故的根因之一）。"""
    base, _ = server
    body = _get(base, "/health")
    assert body["stack"] == "down"
    assert body["api_port"] is None and body["web_port"] is None
    assert _get(base, "/lifecycle")["stack_running"] is False


def test_health_claims_ports_only_while_the_recorded_listener_lives(server):
    import os

    from shine.comfyops.installer import process_identity
    base, data_dir = server
    run = data_dir / "run"
    run.mkdir(parents=True, exist_ok=True)
    birth = process_identity(os.getpid())
    manifest = {"api_port": 8123, "web_port": 8124,
                "processes": [{"name": "api", "listener_pid": os.getpid(),
                               "owners": [{"pid": os.getpid(), "birth": birth}]}]}
    (run / "processes.json").write_text(json.dumps(manifest), encoding="utf-8")
    body = _get(base, "/health")
    assert body["stack"] == "up"
    assert (body["api_port"], body["web_port"]) == (8123, 8124)
    assert _get(base, "/lifecycle")["stack_running"] is True
    manifest["processes"][0]["listener_pid"] = 4_000_000
    (run / "processes.json").write_text(json.dumps(manifest), encoding="utf-8")
    body = _get(base, "/health")
    assert body["stack"] == "down"
    assert body["api_port"] is None


def test_presence_requires_marker_and_loopback_origin(server):
    base, _ = server
    assert _post(base, "/presence", marker=False)[0] == 403
    assert _post(base, "/presence", origin="https://evil.example")[0] == 403
    code, body = _post(base, "/presence", origin="http://127.0.0.1:5173")
    assert code == 200
    with supervisor._LOCK:
        assert supervisor._PRESENCE["ever_seen"] is True


def test_stop_stack_refused_while_busy(server, monkeypatch):
    base, _ = server
    monkeypatch.setattr(supervisor, "_stack_blockers", lambda data_dir: ["1 个镜头任务待执行或正在生成"])
    code, body = _post(base, "/stop-stack")
    assert code == 409
    assert body["code"] == "STACK_BUSY"
    assert body["blockers"]


def test_stop_stack_idle_queues_stop(server, monkeypatch):
    base, _ = server
    calls = []
    monkeypatch.setattr(supervisor, "_stack_blockers", lambda data_dir: [])
    monkeypatch.setattr(supervisor, "_run_stop", lambda data_dir, kind: calls.append(kind))
    code, _ = _post(base, "/stop-stack")
    assert code == 202
    deadline = time.monotonic() + 5
    while not calls and time.monotonic() < deadline:
        time.sleep(0.05)
    assert calls == ["manual"]


def test_stop_worker_uses_temp_manifest_and_keeps_real_record(server, monkeypatch, tmp_path):
    base, data_dir = server
    run = data_dir / "run"
    run.mkdir(parents=True, exist_ok=True)
    manifest = {"processes": [{"name": "worker", "pid": 1, "birth": "x",
                               "owners": [{"pid": 1, "birth": "x"}]}]}
    (run / "processes.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(supervisor, "_stack_blockers", lambda data_dir: [])
    seen = {}

    import stop_shine

    def fake_stop_manifest(path):
        seen["temp"] = Path(path)
        seen["content"] = json.loads(Path(path).read_text(encoding="utf-8"))
        return 0

    monkeypatch.setattr(stop_shine, "stop_manifest", fake_stop_manifest)
    code, body = _post(base, "/stop-worker")
    assert code == 200
    assert seen["content"]["processes"][0]["name"] == "worker"
    assert not seen["temp"].exists()
    # 真实 manifest 不被改写：整栈停止与下次启动对账仍走既有安全路径
    assert json.loads((run / "processes.json").read_text(encoding="utf-8")) == manifest


def test_start_worker_refuses_when_running(server, monkeypatch):
    base, data_dir = server
    import os

    from shine.comfyops.installer import process_identity
    run = data_dir / "run"
    run.mkdir(parents=True, exist_ok=True)
    manifest = {"processes": [{"name": "worker", "pid": os.getpid(),
                               "birth": process_identity(os.getpid()),
                               "owners": [{"pid": os.getpid(),
                                           "birth": process_identity(os.getpid())}]}]}
    (run / "processes.json").write_text(json.dumps(manifest), encoding="utf-8")
    code, body = _post(base, "/start-worker")
    assert code == 409
    assert body["code"] == "WORKER_ALREADY_RUNNING"


def test_a_stopped_worker_is_recorded_offline_at_once(tmp_path, monkeypatch):
    """A stopped Worker must stop counting as online right away.

    Its last heartbeat stays inside the offline window, so a service
    validation retried in that window is refused with "stop the Worker
    first" although it already is - the person clicks the same button again
    and gets the same answer.
    """
    from datetime import datetime

    from shine.db import Base, make_engine, make_session_factory
    from shine.models import WorkerInstance

    database_url = f"sqlite:///{(tmp_path / 'shine.db').as_posix()}"
    monkeypatch.setenv("SHINE_DATABASE_URL", database_url)
    engine = make_engine(database_url)
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    with factory.begin() as db:
        db.add(WorkerInstance(worker_id="worker-x", version="1", status="online",
                              last_heartbeat_at=datetime.now(UTC)))
    engine.dispose()

    supervisor._mark_worker_stopped(tmp_path)

    engine = make_engine(database_url)
    factory = make_session_factory(engine)
    with factory() as db:
        row = db.get(WorkerInstance, "worker-x")
        assert row.status == "stopped"
    engine.dispose()


def test_late_supervisor_validation_cannot_affect_newer_task_or_lease(tmp_path, monkeypatch):
    """R5 CE-17: Stale supervisor validation (both fail and success) leaves newer task/lease untouched."""
    from shine.comfyops import runtime_configuration, setup_preparation
    from shine.db import Base, make_engine, make_session_factory
    from shine.models import AppSetting

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    database_url = f"sqlite:///{(data_dir / 'shine.db').as_posix()}"
    monkeypatch.setenv("SHINE_DATABASE_URL", database_url)
    engine = make_engine(database_url)
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    with factory.begin() as db:
        db.add(AppSetting(key=setup_preparation.TASK_KEY, value={
            "task_id": "new-task",
            "claim_token": "new-claim-token",
            "status": "RUNNING",
            "current": "VALIDATE",
            "lease_owner": "worker-new",
            "steps": [{"id": "VALIDATE", "status": "PENDING"}],
        }))
        db.add(AppSetting(key=runtime_configuration.CANDIDATE_KEY, value={
            "recipe_sha256": "b" * 64,
            "identity": {"recipe_sha256": "b" * 64},
            "project_id": None,
        }))
    engine.dispose()

    # 1. Late failure from an older task/claim must not mark new-task FAILED.
    monkeypatch.setattr(supervisor, "_stop_worker", lambda _d: (200, {}))
    monkeypatch.setattr(supervisor, "_start_worker", lambda _d: (200, {}))
    monkeypatch.setattr(
        "shine.comfyops.service_validation.validate_services",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("LATE_BOOM")),
    )
    supervisor._validate_services_background(
        data_dir,
        delegation={
            "task_id": "old-task",
            "claim_token": "old-claim",
            "recipe_sha256": "a" * 64,
        },
    )

    # 2. Late success from an older task/claim/recipe must not mutate new-task or activate.
    monkeypatch.setattr(
        "shine.comfyops.service_validation.validate_services",
        lambda *a, **k: {"status": "READY", "workflow_profiles": []},
    )
    monkeypatch.setattr(
        "shine.comfyops.runtime_configuration.activate_runtime",
        lambda *a, **k: pytest.fail("stale delegation must not activate newer recipe"),
    )
    supervisor._validate_services_background(
        data_dir,
        delegation={
            "task_id": "new-task",
            "claim_token": "stale-claim-token",
            "recipe_sha256": "b" * 64,
        },
    )

    engine = make_engine(database_url)
    factory = make_session_factory(engine)
    with factory() as db:
        task = db.get(AppSetting, setup_preparation.TASK_KEY).value
        assert task["task_id"] == "new-task"
        assert task["claim_token"] == "new-claim-token"
        assert task["status"] == "RUNNING"
        assert task["lease_owner"] == "worker-new"
    engine.dispose()


def test_accepted_validation_delegation_survives_normal_worker_wait(tmp_path, monkeypatch):
    """R5 closeout: Worker releasing lease after HTTP 202 VALIDATING keeps same delegation valid for Supervisor."""
    from sqlalchemy import select

    from shine import worker
    from shine.comfyops import runtime_configuration, service_validation, setup_preparation
    from shine.config import Settings
    from shine.db import Base, make_engine, make_session_factory
    from shine.models import AppSetting, ProductionSeries, Project

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    database_url = f"sqlite:///{(data_dir / 'shine.db').as_posix()}"
    monkeypatch.setenv("SHINE_DATABASE_URL", database_url)
    settings = Settings(data_dir=data_dir, database_url=database_url)
    engine = make_engine(database_url)
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    recipe_sha = "c" * 64
    with factory.begin() as db:
        db.add(AppSetting(key=setup_preparation.TASK_KEY, value={
            "task_id": "setup-del-1",
            "project_id": None,
            "status": "QUEUED",
            "current": "VALIDATE",
            "lease_owner": None,
            "lease_expires_at": None,
            "request": {"capabilities": ["h3"], "verify_runtime": True},
            "result": {"recipe_sha256": recipe_sha},
            "steps": [
                {"id": "RECIPE", "label": "生成运行配方", "status": "DONE", "detail": "ok"},
                {"id": "VALIDATE", "label": "核对运行合同", "status": "PENDING", "detail": None},
            ],
        }))
        db.add(AppSetting(key=runtime_configuration.CANDIDATE_KEY, value={
            "version": 1,
            "project_id": None,
            "identity": {"recipe_sha256": recipe_sha},
        }))
        project = Project(title="等待续拍的项目")
        db.add(project)
        db.flush()
        db.add(ProductionSeries(project_id=project.id, status="PAUSED"))

    monkeypatch.setattr(
        runtime_configuration,
        "runtime_report",
        lambda db, _s: {
            "status": "CANDIDATE_READY",
            "services_verified": False,
            "candidate": {"recipe_sha256": recipe_sha, "project_id": None},
        },
    )
    delegations = []

    def accept_http(_settings, delegation):
        delegations.append(delegation)
        return {"status": "VALIDATING"}

    monkeypatch.setattr(setup_preparation, "_delegate_supervisor_validation", accept_http)
    assert setup_preparation.advance(factory, settings, "setup-worker") == "setup:VALIDATE:delegated"
    assert len(delegations) == 1
    delegation = delegations[0]
    assert delegation["task_id"] == "setup-del-1"
    assert delegation["claim_token"]
    # Another worker tick while delegation is active must not steal/clobber the pending delegation.
    assert setup_preparation.advance(factory, settings, "setup-worker") is None
    monkeypatch.setattr(worker, "_reconcile_pre_runtime_control_and_leases",
                        lambda *_a: pytest.fail("new work during the validation handoff"))
    assert worker.run_configured_once(factory, None, "setup-worker", settings=settings) == "setup:VALIDATE:delegated"

    called = []
    monkeypatch.setattr(supervisor, "_settings", lambda _d: settings)
    monkeypatch.setattr(supervisor, "_stack_blockers",
                        lambda _d: pytest.fail("delegated validation is not a whole-stack stop"))
    monkeypatch.setattr(supervisor, "_stop_worker", lambda _d: (200, {}))
    monkeypatch.setattr(supervisor, "_start_worker", lambda _d: (200, {}))
    monkeypatch.setattr(
        service_validation,
        "validate_services",
        lambda *a, **k: (called.append("validated") or {"status": "READY", "workflow_profiles": []}),
    )
    monkeypatch.setattr(
        runtime_configuration,
        "activate_runtime",
        lambda *a, **k: called.append("activated"),
    )
    result = supervisor._validate_services_background(data_dir, delegation)
    with factory() as db:
        current = setup_preparation.read_task(db)
        assert db.scalar(select(ProductionSeries)).status == "PAUSED"
    engine.dispose()
    assert result["status"] == "READY"
    assert called == ["validated", "activated"]
    assert current["task_id"] == "setup-del-1"
    assert current["status"] == "DONE"
    assert next(s["status"] for s in current["steps"] if s["id"] == "VALIDATE") == "DONE"


def test_lifecycle_and_heal_cover_worker_m4(server, monkeypatch):
    """Supervisor reports worker_m4, marks m4_workers offline on stop, and heals dead worker-m4."""
    import os

    from shine.comfyops.installer import process_identity
    from shine.manual4.storage.db import make_m4_engine, make_m4_session_factory
    from shine.manual4.storage.migrations import ensure_m4_schema
    from shine.manual4.storage.models import (
        M4ExecutionSnapshot,
        M4ProductionRun,
        M4Project,
        M4StoryboardRevision,
        M4WorkerInstance,
    )

    base, data_dir = server
    run = data_dir / "run"
    run.mkdir(parents=True, exist_ok=True)
    my_pid = os.getpid()
    my_birth = process_identity(my_pid)

    # Create manual4.sqlite with an online M4WorkerInstance and a PENDING run
    m4_db_url = f"sqlite:///{(data_dir / 'manual4.sqlite').as_posix()}"
    m4_engine = make_m4_engine(m4_db_url)
    ensure_m4_schema(m4_engine)
    m4_factory = make_m4_session_factory(m4_engine)
    with m4_factory.begin() as db:
        db.add(M4Project(id="proj-1", title="M4 测试作品"))
        db.flush()
        db.add(
            M4StoryboardRevision(
                id="rev-1",
                project_id="proj-1",
                revision=1,
                status="CONFIRMED",
                payload={},
            )
        )
        db.flush()
        db.add(
            M4ExecutionSnapshot(
                id="snap-1",
                project_id="proj-1",
                storyboard_revision_id="rev-1",
                snapshot_sha256="a" * 64,
                config_json={},
                items_json=[],
            )
        )
        db.flush()
        db.add(M4WorkerInstance(worker_id="w-m4-1", pid=999999, version="m4", status="IDLE"))
        db.add(
            M4ProductionRun(
                project_id="proj-1",
                storyboard_revision_id="rev-1",
                execution_snapshot_id="snap-1",
                idempotency_key="k1",
                scope_kind="project",
                provider="comfyui",
                status="PENDING",
            )
        )
    m4_engine.dispose()

    # _stack_blockers must recognize pending M4 runs so watchdog does not auto-stop idle stack
    shine_db_url = f"sqlite:///{(data_dir / 'shine.db').as_posix()}"
    monkeypatch.setenv("SHINE_DATABASE_URL", shine_db_url)
    from shine.db import Base, make_engine
    s_engine = make_engine(shine_db_url)
    Base.metadata.create_all(s_engine)
    s_engine.dispose()
    blockers = supervisor._stack_blockers(data_dir)
    assert any("四阶排片批次" in b for b in blockers)

    # Manifest with live API + live worker + dead worker-m4
    manifest = {
        "api_port": 8000,
        "web_port": 5173,
        "processes": [
            {"name": "api", "listener_pid": my_pid, "owners": [{"pid": my_pid, "birth": my_birth}]},
            {"name": "worker", "pid": my_pid, "birth": my_birth, "owners": [{"pid": my_pid, "birth": my_birth}]},
            {"name": "worker-m4", "pid": 4_000_000, "birth": "dead", "owners": [{"pid": 4_000_000, "birth": "dead"}]},
        ],
    }
    (run / "processes.json").write_text(json.dumps(manifest), encoding="utf-8")

    lc = _get(base, "/lifecycle")
    assert lc["worker"] == "running"
    assert lc["worker_m4"] == "stopped"

    # Heal triggers _start_worker when stack is live and worker-m4 stopped
    healed_calls: list[Path] = []
    monkeypatch.setattr(supervisor, "_WORKER_HELD_STOP", False)
    monkeypatch.setattr(supervisor, "_start_worker", lambda d: (healed_calls.append(d) or (200, {})))
    assert supervisor.heal_stopped_workers(data_dir) is True
    assert healed_calls == [data_dir]

    # When operator stopped workers intentionally, heal must stay hands-off
    monkeypatch.setattr(supervisor, "_WORKER_HELD_STOP", True)
    assert supervisor.heal_stopped_workers(data_dir) is False
    monkeypatch.setattr(supervisor, "_WORKER_HELD_STOP", False)

    # _mark_worker_stopped updates M4WorkerInstance too
    supervisor._mark_worker_stopped(data_dir)
    m4_engine = make_m4_engine(m4_db_url)
    with make_m4_session_factory(m4_engine)() as db:
        assert db.get(M4WorkerInstance, "w-m4-1").status == "OFFLINE"
    m4_engine.dispose()
