"""监督进程生命周期：自动退出决策、presence 安全、整栈/Worker 控制。"""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
import time
import urllib.request
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
    from datetime import datetime, timezone

    from shine.db import Base, make_engine, make_session_factory
    from shine.models import WorkerInstance

    database_url = f"sqlite:///{(tmp_path / 'shine.db').as_posix()}"
    monkeypatch.setenv("SHINE_DATABASE_URL", database_url)
    engine = make_engine(database_url)
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    with factory.begin() as db:
        db.add(WorkerInstance(worker_id="worker-x", version="1", status="online",
                              last_heartbeat_at=datetime.now(timezone.utc)))
    engine.dispose()

    supervisor._mark_worker_stopped(tmp_path)

    engine = make_engine(database_url)
    factory = make_session_factory(engine)
    with factory() as db:
        row = db.get(WorkerInstance, "worker-x")
        assert row.status == "stopped"
    engine.dispose()