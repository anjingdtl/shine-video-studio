"""Resident loopback supervisor that can restart the Shine stack from the page.

The web page is served by the API it would restart, so a dead backend cannot be
revived through any API call. This small resident process, started by the
launcher and recorded OUTSIDE ``run/processes.json`` (so ``stop_shine.py`` never
kills it mid-restart), owns that one action.

Safety contract:
  * binds 127.0.0.1 only; a cross-site ``Origin`` is refused, so a remote page
    cannot trigger a restart (the local-action marker alone is not enough);
  * the mutating endpoint requires the same-origin ``x-shine-local-action``
    marker used by the rest of the personal-desktop workbench;
  * a restart is refused while live work exists (active jobs / series / GPU
    leases / installing attempts) via the product idle gate — never a silent
    kill of an in-flight render;
  * stop and start reuse the identity-verified ``stop_shine.py`` and the
    idempotent ``start_shine.ps1``; no PID-only kill is introduced here.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PORT = 8790

_STATE = {
    "phase": "idle",  # idle | stopping | starting | ready | failed | refused
    "detail": "",
    "started_at": None,
    "finished_at": None,
}
_LOCK = threading.Lock()


def _loopback_origin(origin: str | None) -> bool:
    if not origin:
        return True  # non-browser caller (curl / tests)
    parsed = urlparse(origin)
    return parsed.hostname in ("127.0.0.1", "localhost", "::1")


def _idle_blocker(data_dir: Path) -> str | None:
    """The product idle gate; returns a named code when live work exists."""
    from shine.config import Settings
    from shine.comfyops.runtime_configuration import require_idle_configuration
    from shine.db import make_engine, make_session_factory
    from fastapi import HTTPException

    settings = Settings(
        data_dir=data_dir,
        database_url=os.getenv("SHINE_DATABASE_URL")
        or f"sqlite:///{(data_dir / 'shine.db').as_posix()}",
    )
    engine = make_engine(settings.database_url)
    factory = make_session_factory(engine)
    try:
        with factory() as db:
            require_idle_configuration(db)
        return None
    except HTTPException as exc:
        return str(exc.detail)
    finally:
        engine.dispose()


def _run_restart(data_dir: Path, api_port: int, web_port: int) -> None:
    python = REPO_ROOT / "backend" / ".venv" / "Scripts" / "python.exe"
    if not python.is_file():  # posix dev checkout
        python = REPO_ROOT / "backend" / ".venv" / "bin" / "python"
    try:
        with _LOCK:
            _STATE.update(phase="stopping", detail="身份核验停止旧栈…",
                          started_at=time.time(), finished_at=None)
        stop = subprocess.run(
            [str(python), str(REPO_ROOT / "scripts" / "stop_shine.py"),
             "--data-dir", str(data_dir)],
            capture_output=True, text=True, timeout=180,
            env={**os.environ, "SHINE_STARTUP_GUARD_HELD": "0"},
        )
        if stop.returncode not in (0, 1):
            # 1 = unresolved identities retained for reconciliation; still proceed
            # only when nothing is left running, otherwise surface the refusal.
            with _LOCK:
                _STATE.update(phase="failed",
                              detail=f"停止未完全收尾（exit {stop.returncode}）：{(stop.stdout or stop.stderr)[-300:]}")
            return
        with _LOCK:
            _STATE.update(phase="starting", detail="幂等启动新栈…")
        launcher = REPO_ROOT / "scripts" / "start_shine.ps1"
        start = subprocess.run(
            ["pwsh", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(launcher),
             "-ApiPort", str(api_port), "-WebPort", str(web_port)],
            capture_output=True, text=True, timeout=600,
            env={**os.environ, "SHINE_DATA_DIR": str(data_dir)},
        )
        ok = start.returncode == 0
        with _LOCK:
            _STATE.update(
                phase="ready" if ok else "failed",
                detail=("后台已重启。" if ok else
                        f"启动失败（exit {start.returncode}）：{(start.stderr or start.stdout)[-400:]}"),
                finished_at=time.time(),
            )
    except Exception as exc:  # noqa: BLE001 - surfaced to the page, never silent
        with _LOCK:
            _STATE.update(phase="failed", detail=f"重启异常：{exc}", finished_at=time.time())


class Handler(BaseHTTPRequestHandler):
    server_version = "ShineSupervisor/1.0"
    data_dir: Path
    api_port: int
    web_port: int

    def log_message(self, *args):  # quiet by default
        pass

    def _send(self, code: int, payload: dict, origin: str | None = None):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if origin and _loopback_origin(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        origin = self.headers.get("Origin")
        if not _loopback_origin(origin):
            self._send(403, {"code": "SUPERVISOR_CROSS_ORIGIN_REFUSED"})
            return
        self.send_response(204)
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "content-type, x-shine-local-action")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def do_GET(self):
        origin = self.headers.get("Origin")
        if not _loopback_origin(origin):
            self._send(403, {"code": "SUPERVISOR_CROSS_ORIGIN_REFUSED"})
            return
        path = urlparse(self.path).path
        if path == "/health":
            self._send(200, {"status": "ok", "role": "supervisor",
                             "data_dir": str(self.data_dir),
                             "api_port": self.api_port, "web_port": self.web_port}, origin)
        elif path == "/restart-status":
            with _LOCK:
                self._send(200, dict(_STATE), origin)
        else:
            self._send(404, {"code": "SUPERVISOR_UNKNOWN_PATH"}, origin)

    def do_POST(self):
        origin = self.headers.get("Origin")
        if not _loopback_origin(origin):
            self._send(403, {"code": "SUPERVISOR_CROSS_ORIGIN_REFUSED"})
            return
        if urlparse(self.path).path != "/restart-stack":
            self._send(404, {"code": "SUPERVISOR_UNKNOWN_PATH"}, origin)
            return
        if self.headers.get("x-shine-local-action") != "1":
            self._send(403, {"code": "SUPERVISOR_LOCAL_ACTION_REQUIRED"}, origin)
            return
        with _LOCK:
            if _STATE["phase"] in ("stopping", "starting"):
                self._send(409, {"code": "SUPERVISOR_RESTART_IN_PROGRESS"}, origin)
                return
        blocker = _idle_blocker(self.data_dir)
        if blocker:
            with _LOCK:
                _STATE.update(phase="refused", detail=blocker, finished_at=time.time())
            self._send(409, {"code": "STACK_BUSY", "detail": blocker}, origin)
            return
        threading.Thread(
            target=_run_restart, args=(self.data_dir, self.api_port, self.web_port),
            daemon=True,
        ).start()
        self._send(202, {"status": "RESTARTING"}, origin)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default=os.environ.get("SHINE_DATA_DIR", str(REPO_ROOT / ".local-data")))
    parser.add_argument("--port", type=int, default=int(os.environ.get("SHINE_SUPERVISOR_PORT", DEFAULT_PORT)))
    parser.add_argument("--api-port", type=int, default=8000)
    parser.add_argument("--web-port", type=int, default=5173)
    args = parser.parse_args()
    data_dir = Path(args.data_dir)
    (data_dir / "run").mkdir(parents=True, exist_ok=True)

    Handler.data_dir = data_dir
    Handler.api_port = args.api_port
    Handler.web_port = args.web_port
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    # Recorded outside processes.json so stop_shine.py never kills the supervisor.
    (data_dir / "run" / "supervisor.json").write_text(
        json.dumps({"pid": os.getpid(), "port": args.port, "data_dir": str(data_dir)}, indent=2),
        encoding="utf-8",
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())