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
    server, _factory, _dir = supervisor
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
    assert called["args"][1:] == (8000, 5173)