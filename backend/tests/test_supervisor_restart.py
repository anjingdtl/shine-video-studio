"""The resident supervisor: loopback-only, local-action gated, idle-gated restart.

The page cannot restart a dead backend through the API it serves; the supervisor
owns that one action. These tests pin the safety contract: cross-origin refusal,
local-action marker, the product idle gate refusing live work, and the restart
state machine — without ever spawning a real stack restart.
"""

from __future__ import annotations

import importlib.util
import json
import threading
import time
import urllib.error
import urllib.request
from datetime import timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from shine.comfyops.models import ResourceLease
from shine.db import Base, make_engine, make_session_factory
from shine.models import utcnow

_SPEC = importlib.util.spec_from_file_location(
    "shine_supervisor", Path(__file__).resolve().parents[2] / "scripts" / "shine_supervisor.py")
sup = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sup)


def test_loopback_origin_policy():
    assert sup._loopback_origin(None) is True
    assert sup._loopback_origin("http://127.0.0.1:5173") is True
    assert sup._loopback_origin("http://localhost:5173") is True
    assert sup._loopback_origin("https://evil.example") is False
    assert sup._loopback_origin("http://192.168.1.5:5173") is False


@pytest.fixture()
def supervisor(tmp_path, monkeypatch):
    data_dir = tmp_path / "sup"
    data_dir.mkdir()
    db_url = f"sqlite:///{(data_dir / 'shine.db').as_posix()}"
    monkeypatch.setenv("SHINE_DATABASE_URL", db_url)
    engine = make_engine(db_url)
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    engine.dispose()

    sup.Handler.data_dir = data_dir
    sup.Handler.api_port = 8000
    sup.Handler.web_port = 5173
    server = ThreadingHTTPServer(("127.0.0.1", 0), sup.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, factory, data_dir
    server.shutdown()
    server.server_close()


def _url(server, path):
    return f"http://127.0.0.1:{server.server_address[1]}{path}"


def test_health_and_cross_origin_and_marker(supervisor):
    server, _factory, _dir = supervisor
    with urllib.request.urlopen(_url(server, "/health"), timeout=5) as r:
        assert json.loads(r.read())["role"] == "supervisor"

    # Cross-site Origin is refused even with the marker.
    req = urllib.request.Request(_url(server, "/restart-stack"), method="POST",
                                 headers={"Origin": "https://evil.example",
                                          "x-shine-local-action": "1"})
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=5)
    assert exc.value.code == 403

    # Loopback but missing the local-action marker is refused.
    req = urllib.request.Request(_url(server, "/restart-stack"), method="POST",
                                 headers={"Origin": "http://127.0.0.1:5173"})
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=5)
    assert exc.value.code == 403


def test_restart_refused_while_live_work(supervisor):
    server, factory, _dir = supervisor
    with factory() as db:
        db.add(ResourceLease(owner="worker-x", budget_mb=12000, exclusive=True,
                             priority=0, status="ACTIVE",
                             expires_at=utcnow() + timedelta(minutes=5)))
        db.commit()
    req = urllib.request.Request(_url(server, "/restart-stack"), method="POST",
                                 headers={"Origin": "http://127.0.0.1:5173",
                                          "x-shine-local-action": "1"})
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=5)
    assert exc.value.code == 409
    assert json.loads(exc.value.read())["code"] == "STACK_BUSY"


def test_restart_idle_runs_state_machine(supervisor, monkeypatch):
    server, _factory, data_dir = supervisor
    (data_dir / "run").mkdir()
    (data_dir / "run" / "processes.json").write_text(
        json.dumps({"api_port": 8125, "web_port": 5199}), encoding="utf-8",
    )
    called = {}

    def fake_restart(data_dir, api_port, web_port):
        called["args"] = (data_dir, api_port, web_port)
        with sup._LOCK:
            sup._STATE.update(phase="ready", detail="后台已重启。")

    monkeypatch.setattr(sup, "_run_restart", fake_restart)
    req = urllib.request.Request(_url(server, "/restart-stack"), method="POST",
                                 headers={"Origin": "http://127.0.0.1:5173",
                                          "x-shine-local-action": "1"})
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.status == 202
    for _ in range(50):
        with urllib.request.urlopen(_url(server, "/restart-status"), timeout=5) as r:
            status = json.loads(r.read())
        if status["phase"] == "ready":
            break
        threading.Event().wait(0.05)
    assert status["phase"] == "ready"
    assert called["args"][1:] == (8125, 5199)


def test_a_restart_that_is_still_running_is_refused(supervisor, monkeypatch):
    """A restart in flight is a real refusal, not a dead end."""
    server, _factory, _dir = supervisor
    monkeypatch.setattr(sup, "_run_restart", lambda *a, **k: None)
    with sup._LOCK:
        sup._STATE.update(phase="starting", started_at=time.time())
    req = urllib.request.Request(_url(server, "/restart-stack"), method="POST",
                                 headers={"Origin": "http://127.0.0.1:5173",
                                          "x-shine-local-action": "1"})
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=5)
    assert exc.value.code == 409
    assert json.loads(exc.value.read())["code"] == "SUPERVISOR_RESTART_IN_PROGRESS"


def test_a_restart_that_never_reported_back_does_not_lock_the_next_one(supervisor, monkeypatch):
    """An abandoned restart must not refuse every later restart forever.

    The page has no other way to clear it, so a stuck attempt is abandoned
    after the stale window and the operator can try again.
    """
    server, _factory, _dir = supervisor
    started = {}

    def fake_restart(data_dir, api_port, web_port):
        started["called"] = True
        with sup._LOCK:
            sup._STATE.update(phase="ready", detail="后台已重启。")

    monkeypatch.setattr(sup, "_run_restart", fake_restart)
    with sup._LOCK:
        sup._STATE.update(phase="starting",
                          started_at=time.time() - sup.RESTART_STALE_AFTER_SECONDS - 60)
    req = urllib.request.Request(_url(server, "/restart-stack"), method="POST",
                                 headers={"Origin": "http://127.0.0.1:5173",
                                          "x-shine-local-action": "1"})
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.status == 202
    for _ in range(50):
        with urllib.request.urlopen(_url(server, "/restart-status"), timeout=5) as r:
            status = json.loads(r.read())
        if status["phase"] == "ready":
            break
        threading.Event().wait(0.05)
    assert status["phase"] == "ready"
    assert started.get("called") is True


def test_the_launcher_never_writes_into_a_pipe_the_supervisor_reads(supervisor, monkeypatch, tmp_path):
    """The launcher must not hand the stack a pipe it has to wait on.

    The processes it starts inherit the launcher's standard handles.  When
    those handles are pipes, the supervisor waits for the whole stack to exit
    before it can report a result - the restart then never finishes and every
    later restart is refused as "already in progress".
    """
    data_dir = tmp_path / "sup"
    calls = []

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append({"command": command, **kwargs})
        return _Result()

    monkeypatch.setattr(sup.subprocess, "run", fake_run)
    sup._run_restart(data_dir, 8000, 5173)
    assert calls, "the restart must actually launch something"
    for call in calls:
        assert "capture_output" not in call, call["command"]
        assert call.get("stdout") is not None
        # A pipe would be the integer ``subprocess.PIPE``; the launcher must
        # write into a real file instead.
        assert not isinstance(call["stdout"], int), call["command"]
        assert str(getattr(call["stdout"], "name", "")).endswith(".log")
