"""Resident loopback supervisor: the work stack's lifecycle owner.

The web page is served by the API it would restart, so a dead backend cannot be
revived through any API call. This small resident process, started by the
launcher and recorded OUTSIDE ``run/processes.json`` (so ``stop_shine.py`` never
kills it mid-restart), owns the stack's lifecycle actions:

* ``POST /restart-stack`` — identity-verified stop + idempotent start (unchanged);
* ``POST /presence`` — the page reports it is open; a closed tab stops pinging;
* ``GET /lifecycle`` — presence, policy and last stop, for the page to display;
* ``POST /stop-stack`` — explicit exit: stop the stack and release the backends;
* ``POST /stop-worker`` / ``POST /start-worker`` — worker-level control so service
  re-validation can reach its "API alive, Worker stopped" precondition;
* a watchdog that auto-stops the stack once the page has been closed and the
  machine is idle past a grace period. Live production is never interrupted:
  while work exists the watchdog waits, and a stack that never saw a page
  (headless / script usage) is never auto-stopped.

Safety contract:
  * binds 127.0.0.1 only; a cross-site ``Origin`` is refused, so a remote page
    cannot trigger any action (the local-action marker alone is not enough);
  * every mutating endpoint requires the same-origin ``x-shine-local-action``
    marker used by the rest of the personal-desktop workbench;
  * stop/restart/worker-stop are refused while live work exists (active jobs /
    series / runs / GPU leases / installing attempts / deliveries) via the
    product idle gates — never a silent kill of an in-flight render;
  * stop and start reuse the identity-verified ``stop_shine.py`` and the
    idempotent ``start_shine.ps1``; no PID-only kill is introduced here.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PORT = 8790
DEFAULT_IDLE_SHUTDOWN_MINUTES = 5
WATCHDOG_TICK_SECONDS = 20
RESTART_STALE_AFTER_SECONDS = 900.0
"""A restart that has not reported back for this long is treated as dead.

The page has no other way to clear a stuck restart, so an abandoned attempt
must not keep refusing every later one.
"""

_STATE = {
    "phase": "idle",  # idle | stopping | starting | ready | failed | refused
    "detail": "",
    "started_at": None,
    "finished_at": None,
}
_LOCK = threading.Lock()
_PRESENCE = {"last_seen": None, "ever_seen": False}
_VALIDATE_BUSY = threading.Lock()
_LAST_STOP: dict | None = None


def _loopback_origin(origin: str | None) -> bool:
    if not origin:
        return True  # non-browser caller (curl / tests)
    parsed = urlparse(origin)
    return parsed.hostname in ("127.0.0.1", "localhost", "::1")


def _settings(data_dir: Path):
    from shine.config import Settings
    return Settings(
        data_dir=data_dir,
        database_url=os.getenv("SHINE_DATABASE_URL")
        or f"sqlite:///{(data_dir / 'shine.db').as_posix()}",
    )


def _idle_blocker(data_dir: Path) -> str | None:
    """The product idle gate; returns a named code when live work exists."""
    from shine.comfyops.runtime_configuration import require_idle_configuration
    from shine.db import make_engine, make_session_factory
    from fastapi import HTTPException

    engine = make_engine(_settings(data_dir).database_url)
    factory = make_session_factory(engine)
    try:
        with factory() as db:
            require_idle_configuration(db)
        return None
    except HTTPException as exc:
        return str(exc.detail)
    finally:
        engine.dispose()


def _stack_blockers(data_dir: Path) -> list[str]:
    """Everything that means the stack still has work to do.

    The union of the configuration idle gate (leases / active jobs / live
    series / installing attempts), the media work blockers (jobs / series /
    runs / library tasks), creative script tasks and pending deliveries.
    """
    from sqlalchemy import func, select
    from shine.comfyops.runtime_configuration import require_idle_configuration
    from shine.db import make_engine, make_session_factory
    from shine.media.output_layout import active_work_blockers
    from shine.models import AgentTaskRecord
    from shine.media.delivery_models import DeliveryOrder
    from fastapi import HTTPException

    blockers: list[str] = []
    engine = make_engine(_settings(data_dir).database_url)
    factory = make_session_factory(engine)
    try:
        with factory() as db:
            try:
                require_idle_configuration(db)
            except HTTPException as exc:
                blockers.append(str(exc.detail))
            blockers.extend(active_work_blockers(db))
            from shine.director.creative import SCRIPT_TASK_ROLE_ID
            creative = db.scalar(
                select(func.count(AgentTaskRecord.id)).where(
                    AgentTaskRecord.status.in_(["QUEUED", "RUNNING"]),
                    AgentTaskRecord.role_id.in_([SCRIPT_TASK_ROLE_ID, "creative_brief_authoring"]),
                )) or 0
            if creative:
                blockers.append(f"{creative} 个创意剧本任务待执行或正在生成")
            deliveries = db.scalar(
                select(func.count(DeliveryOrder.id)).where(
                    DeliveryOrder.status.in_(["PENDING", "ASSEMBLING"]))) or 0
            if deliveries:
                blockers.append(f"{deliveries} 个成片交付待装配")
    finally:
        engine.dispose()
    return blockers


def auto_stop_decision(*, now: float, last_seen: float | None, ever_seen: bool,
                       minutes: float, stack_running: bool,
                       blockers: list[str]) -> tuple[bool, str]:
    """Pure watchdog decision; kept separate so it is unit-testable."""
    if minutes <= 0:
        return False, "自动退出已关闭"
    if not ever_seen or last_seen is None:
        return False, "页面从未打开（无头/脚本用法不自动退出）"
    if not stack_running:
        return False, "工作栈不在运行"
    if blockers:
        return False, "有活工作：" + "；".join(blockers)
    if now - last_seen < minutes * 60:
        return False, "关页宽限期内"
    return True, "页面已关闭且空闲超过宽限期"


def _venv_python() -> Path:
    python = REPO_ROOT / "backend" / ".venv" / "Scripts" / "python.exe"
    if not python.is_file():  # posix dev checkout
        python = REPO_ROOT / "backend" / ".venv" / "bin" / "python"
    return python


def _run_stop(data_dir: Path, kind: str) -> None:
    global _LAST_STOP
    try:
        stop = subprocess.run(
            [str(_venv_python()), str(REPO_ROOT / "scripts" / "stop_shine.py"),
             "--data-dir", str(data_dir)],
            capture_output=True, text=True, timeout=180,
            env={**os.environ, "SHINE_STARTUP_GUARD_HELD": "0"},
        )
        if stop.returncode not in (0, 1):
            with _LOCK:
                _LAST_STOP = {"at": time.time(), "kind": kind,
                              "detail": f"停止未完全收尾（exit {stop.returncode}）",
                              "ok": False}
            return
        release = subprocess.run(
            [str(_venv_python()), str(REPO_ROOT / "scripts" / "release_residual_backends.py"),
             "--data-dir", str(data_dir)],
            capture_output=True, text=True, timeout=300,
        )
        with _LOCK:
            _LAST_STOP = {
                "at": time.time(), "kind": kind, "ok": True,
                "detail": "后台已停止，显存已释放。"
                if release.returncode == 0 else
                f"后台已停止；显存释放未完全确认（exit {release.returncode}）。",
            }
    except Exception as exc:  # noqa: BLE001 - surfaced, never silent
        with _LOCK:
            _LAST_STOP = {"at": time.time(), "kind": kind, "ok": False,
                          "detail": f"停止异常：{exc}"}


def _watchdog(data_dir: Path, minutes: float) -> None:
    while True:
        time.sleep(WATCHDOG_TICK_SECONDS)
        try:
            with _LOCK:
                busy_restart = _STATE["phase"] in ("stopping", "starting")
                presence = dict(_PRESENCE)
            if busy_restart:
                continue
            stack_running = (data_dir / "run" / "processes.json").is_file()
            blockers = _stack_blockers(data_dir) if stack_running else []
            stop, reason = auto_stop_decision(
                now=time.time(), last_seen=presence["last_seen"],
                ever_seen=presence["ever_seen"], minutes=minutes,
                stack_running=stack_running, blockers=blockers)
            if stop:
                _run_stop(data_dir, "auto")
        except Exception:  # noqa: BLE001 - the watchdog must never die
            continue


def _run_restart(data_dir: Path, api_port: int, web_port: int) -> None:
    python = _venv_python()
    log_dir = data_dir / "run" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    try:
        with _LOCK:
            _STATE.update(phase="stopping", detail="身份核验停止旧栈…",
                          started_at=time.time(), finished_at=None)
        # The launcher and the stopper must never write into a pipe this
        # process reads: the stack they start inherits the pipe handle and
        # keeps it open for as long as it runs, so reading the pipe would wait
        # for the whole stack - the restart then never reports a result and
        # every later restart is refused as "already in progress".  Their
        # output goes to files, where an inherited handle costs nothing.
        with (log_dir / "restart.stop.log").open("w", encoding="utf-8") as stop_out, \
                (log_dir / "restart.start.log").open("w", encoding="utf-8") as start_out:
            stop = subprocess.run(
                [str(python), str(REPO_ROOT / "scripts" / "stop_shine.py"),
                 "--data-dir", str(data_dir)],
                stdout=stop_out, stderr=subprocess.STDOUT, timeout=180,
                env={**os.environ, "SHINE_STARTUP_GUARD_HELD": "0"},
            )
            if stop.returncode not in (0, 1):
                # 1 = unresolved identities retained for reconciliation; still
                # proceed only when nothing is left running, otherwise surface
                # the refusal.
                with _LOCK:
                    _STATE.update(phase="failed",
                                  detail=f"停止未完全收尾（exit {stop.returncode}），"
                                         f"详见 {log_dir / 'restart.stop.log'}")
                return
            with _LOCK:
                _STATE.update(phase="starting", detail="幂等启动新栈…")
            launcher = REPO_ROOT / "scripts" / "start_shine.ps1"
            start = subprocess.run(
                ["pwsh", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(launcher),
                 "-ApiPort", str(api_port), "-WebPort", str(web_port)],
                stdout=start_out, stderr=subprocess.STDOUT, timeout=600,
                env={**os.environ, "SHINE_DATA_DIR": str(data_dir)},
            )
        ok = start.returncode == 0
        with _LOCK:
            _STATE.update(
                phase="ready" if ok else "failed",
                detail=("后台已重启。" if ok else
                        f"启动失败（exit {start.returncode}），"
                        f"详见 {log_dir / 'restart.start.log'}"),
                finished_at=time.time(),
            )
    except subprocess.TimeoutExpired:
        with _LOCK:
            _STATE.update(
                phase="failed",
                detail=f"重启超时，详见 {log_dir / 'restart.start.log'}",
                finished_at=time.time(),
            )
    except Exception as exc:  # noqa: BLE001 - surfaced to the page, never silent
        with _LOCK:
            _STATE.update(phase="failed", detail=f"重启异常：{exc}", finished_at=time.time())


def _read_manifest(data_dir: Path) -> dict | None:
    path = data_dir / "run" / "processes.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def _worker_state(manifest: dict | None) -> str:
    from shine.comfyops.installer import process_identity
    if not manifest:
        return "absent"
    entry = next((row for row in manifest.get("processes", [])
                  if row.get("name") == "worker"), None)
    if not entry:
        return "absent"
    state = "stopped"
    for owner in entry.get("owners", []):
        try:
            current = process_identity(owner.get("pid"))
        except (OSError, ValueError):
            return "unknown"
        if current is not None and current == owner.get("birth"):
            return "running"
        if current is not None:
            state = "unknown"
    return state


def _stop_worker(data_dir: Path) -> tuple[int, dict]:
    import stop_shine
    manifest = _read_manifest(data_dir)
    if not manifest:
        return 409, {"code": "STACK_NOT_RUNNING"}
    entry = next((row for row in manifest.get("processes", [])
                  if row.get("name") == "worker"), None)
    if not entry:
        return 409, {"code": "WORKER_NOT_RECORDED"}
    # A temporary single-entry manifest: the real record stays untouched, so a
    # later whole-stack stop skips the gone worker and the next launcher start
    # reconciles the half stack through its existing safe path.
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                     encoding="utf-8") as handle:
        json.dump({"processes": [entry]}, handle)
        temp_path = Path(handle.name)
    try:
        result = stop_shine.stop_manifest(temp_path)
    finally:
        temp_path.unlink(missing_ok=True)
    if result != 0:
        return 409, {"code": "WORKER_STOP_UNRESOLVED",
                     "detail": "Worker 进程身份无法核验，未强行终止；请走对账。"}
    _mark_worker_stopped(data_dir)
    return 200, {"status": "STOPPED",
                 "detail": "Worker 已停止，后台记录已同步为离线，可以继续服务核验。"}


def _mark_worker_stopped(data_dir: Path) -> None:
    """Record the stopped Worker as offline right away.

    The worker process is gone, but its last heartbeat stays inside the
    offline window, so the backend keeps counting it as online for another
    half minute: a service validation retried in that window is refused with
    "stop the Worker first" although it already is.  The record is the
    worker's own state, so the one that stopped it writes it down.
    """
    from sqlalchemy import select

    from shine.db import make_engine, make_session_factory
    from shine.models import WorkerInstance

    engine = make_engine(_settings(data_dir).database_url)
    factory = make_session_factory(engine)
    try:
        with factory.begin() as db:
            for row in db.scalars(select(WorkerInstance)).all():
                if row.status != "stopped":
                    row.status = "stopped"
    except Exception:  # noqa: BLE001 - the heartbeat still expires on its own
        pass
    finally:
        engine.dispose()


def _start_worker(data_dir: Path) -> tuple[int, dict]:
    import stop_shine
    import shine_processes
    from shine.comfyops.installer import process_identity
    manifest = _read_manifest(data_dir)
    if not manifest:
        return 409, {"code": "STACK_NOT_RUNNING"}
    if _worker_state(manifest) == "running":
        return 409, {"code": "WORKER_ALREADY_RUNNING"}
    if _worker_state(manifest) == "unknown":
        return 409, {"code": "WORKER_STATE_UNVERIFIED"}
    python = _venv_python()
    log_dir = data_dir / "run" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "SHINE_DATA_DIR": str(data_dir), "PYTHONUNBUFFERED": "1"}
    if manifest.get("database_url"):
        env["SHINE_DATABASE_URL"] = manifest["database_url"]
    if manifest.get("render_provider"):
        env["SHINE_RENDER_PROVIDER"] = manifest["render_provider"]
    out = open(log_dir / "worker.out.log", "ab")
    err = open(log_dir / "worker.err.log", "ab")
    try:
        process = subprocess.Popen(
            [str(python), "-m", "shine.worker"], cwd=str(REPO_ROOT), env=env,
            stdout=out, stderr=err,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    finally:
        out.close()
        err.close()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and process.poll() is None:
        time.sleep(0.1)
    if process.poll() is not None:
        return 409, {"code": "WORKER_START_FAILED",
                     "detail": "Worker 启动后立即退出，见 run/logs/worker.err.log。"}
    birth = process_identity(process.pid)
    owners = shine_processes.snapshot(process.pid, birth)
    guard = stop_shine._acquire_startup_guard(data_dir)
    try:
        manifest = _read_manifest(data_dir) or manifest
        entry = next((row for row in manifest.get("processes", [])
                      if row.get("name") == "worker"), None)
        if entry is None:
            entry = {"name": "worker"}
            manifest.setdefault("processes", []).append(entry)
        entry.update(pid=process.pid, birth=birth, owners=owners,
                     command=f"{python} -m shine.worker")
        path = data_dir / "run" / "processes.json"
        tmp = path.with_suffix(f".new-{os.getpid()}")
        tmp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        stop_shine._release_startup_guard(guard)
    return 200, {"status": "RUNNING", "pid": process.pid}


def _validate_services_background(data_dir: Path) -> dict:
    """Validate and activate the runtime while the Worker is stopped.

    The preparation task runs *inside* the Worker, but service validation
    requires the Worker offline (so nothing else drives the GPU).  The Worker
    therefore cannot stop itself: the supervisor does it.  Order: stop Worker →
    clear the task lease → validate + activate → record the VALIDATE step →
    restart Worker.  The restarted Worker resumes the task at the next step.
    """
    from shine.comfyops.runtime_configuration import activate_runtime, runtime_report
    from shine.comfyops.service_validation import validate_services
    from shine.comfyops.setup_preparation import TASK_KEY
    from shine.db import make_engine, make_session_factory
    from shine.models import AppSetting, utcnow

    blockers = _stack_blockers(data_dir)
    if blockers:
        return {"status": "FAILED", "code": "STACK_BUSY", "blockers": blockers}
    settings = _settings(data_dir)
    engine = make_engine(settings.database_url)
    factory = make_session_factory(engine)

    def mark_task(step_status: str, code: str | None, detail: str) -> None:
        with factory.begin() as db:
            row = db.get(AppSetting, TASK_KEY)
            if row is None or not isinstance(row.value, dict):
                return
            task = dict(row.value)
            task["lease_owner"] = None
            task["lease_expires_at"] = None
            for step in task.get("steps", []):
                if step.get("id") == "VALIDATE":
                    step["status"] = step_status
                    step["code"] = code
                    step["detail"] = detail
            if step_status == "FAILED":
                task["status"] = "FAILED"
                task["error"] = code
                task["message"] = detail
            task["updated_at"] = utcnow().isoformat()
            row.value = task

    try:
        code, payload = _stop_worker(data_dir)
        if code != 200:
            return {"status": "FAILED", "code": payload.get("code", "WORKER_STOP_FAILED"),
                    "detail": payload.get("detail", "Worker 未能停止，未进行核验。")}
        with factory() as db:
            report = runtime_report(db, settings)
        candidate = report.get("candidate") or {}
        recipe_sha256 = candidate.get("recipe_sha256")
        project_id = candidate.get("project_id")
        if not recipe_sha256 or not project_id:
            mark_task("FAILED", "GENERATE_RUNTIME_CONFIGURATION_FIRST", "还没有可核验的运行配置。")
            _start_worker(data_dir)
            return {"status": "FAILED", "code": "GENERATE_RUNTIME_CONFIGURATION_FIRST"}
        try:
            with factory() as db:
                validated = validate_services(db, settings, str(project_id), recipe_sha256,
                                              actor="supervisor:setup")
            if validated.get("status") != "READY":
                detail = "服务核验没有通过：" + str(validated.get("status"))
                mark_task("FAILED", str(validated.get("status") or "SERVICE_VALIDATION_FAILED"), detail)
                return {"status": "FAILED", "code": validated.get("status"), "detail": detail}
            with factory() as db:
                activate_runtime(db, settings, str(project_id), recipe_sha256, "supervisor:setup")
            mark_task("DONE", None, "服务核验通过，本机配置已激活（Worker 已自动恢复）。")
            return {"status": "READY"}
        except Exception as exc:  # noqa: BLE001 - the named refusal is the answer
            detail = str(getattr(exc, "detail", exc))[:300]
            mark_task("FAILED", "SERVICE_VALIDATION_FAILED", detail)
            return {"status": "FAILED", "code": "SERVICE_VALIDATION_FAILED", "detail": detail}
        finally:
            _start_worker(data_dir)
    finally:
        engine.dispose()


class Handler(BaseHTTPRequestHandler):
    server_version = "ShineSupervisor/1.1"
    data_dir: Path
    api_port: int
    web_port: int
    idle_minutes: float

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

    def _origin(self) -> str | None:
        return self.headers.get("Origin")

    def _refused_origin(self, origin: str | None) -> bool:
        if not _loopback_origin(origin):
            self._send(403, {"code": "SUPERVISOR_CROSS_ORIGIN_REFUSED"})
            return True
        return False

    def _refused_marker(self, origin: str | None) -> bool:
        if self.headers.get("x-shine-local-action") != "1":
            self._send(403, {"code": "SUPERVISOR_LOCAL_ACTION_REQUIRED"}, origin)
            return True
        return False

    def do_OPTIONS(self):
        origin = self._origin()
        if self._refused_origin(origin):
            return
        self.send_response(204)
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "content-type, x-shine-local-action")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def do_GET(self):
        origin = self._origin()
        if self._refused_origin(origin):
            return
        path = urlparse(self.path).path
        if path == "/health":
            self._send(200, {"status": "ok", "role": "supervisor",
                             "data_dir": str(self.data_dir),
                             "api_port": self.api_port, "web_port": self.web_port}, origin)
        elif path == "/restart-status":
            with _LOCK:
                self._send(200, dict(_STATE), origin)
        elif path == "/lifecycle":
            with _LOCK:
                presence = dict(_PRESENCE)
                last_stop = dict(_LAST_STOP) if _LAST_STOP else None
            manifest = _read_manifest(self.data_dir)
            self._send(200, {
                "idle_shutdown_minutes": self.idle_minutes,
                "ever_seen": presence["ever_seen"],
                "last_seen_age_s": (time.time() - presence["last_seen"])
                if presence["last_seen"] else None,
                "stack_running": manifest is not None,
                "worker": _worker_state(manifest),
                "last_stop": last_stop,
            }, origin)
        else:
            self._send(404, {"code": "SUPERVISOR_UNKNOWN_PATH"}, origin)

    def do_POST(self):
        origin = self._origin()
        if self._refused_origin(origin):
            return
        path = urlparse(self.path).path
        if path == "/presence":
            if self._refused_marker(origin):
                return
            with _LOCK:
                _PRESENCE.update(last_seen=time.time(), ever_seen=True)
            self._send(200, {"status": "ok"}, origin)
            return
        if path == "/stop-stack":
            if self._refused_marker(origin):
                return
            blockers = _stack_blockers(self.data_dir)
            if blockers:
                self._send(409, {"code": "STACK_BUSY", "blockers": blockers}, origin)
                return
            threading.Thread(target=_run_stop, args=(self.data_dir, "manual"),
                             daemon=True).start()
            self._send(202, {"status": "STOPPING"}, origin)
            return
        if path == "/stop-worker":
            if self._refused_marker(origin):
                return
            blockers = _stack_blockers(self.data_dir)
            if blockers:
                self._send(409, {"code": "STACK_BUSY", "blockers": blockers}, origin)
                return
            code, payload = _stop_worker(self.data_dir)
            self._send(code, payload, origin)
            return
        if path == "/start-worker":
            if self._refused_marker(origin):
                return
            code, payload = _start_worker(self.data_dir)
            self._send(code, payload, origin)
            return
        if path == "/validate-services":
            if self._refused_marker(origin):
                return
            # Long operation (stops Worker, starts renderer, reads nodes,
            # restarts Worker). Run off the request thread; the caller is the
            # Worker itself, which is about to be stopped, so it only needs the
            # acceptance receipt. A second call while one is running is refused.
            if not _VALIDATE_BUSY.acquire(blocking=False):
                self._send(409, {"code": "VALIDATION_IN_PROGRESS"}, origin)
                return

            def _run() -> None:
                try:
                    _validate_services_background(self.data_dir)
                finally:
                    _VALIDATE_BUSY.release()

            threading.Thread(target=_run, daemon=True).start()
            self._send(202, {"status": "VALIDATING",
                             "detail": "已委托监督进程：短暂停止 Worker、核验并激活、自动恢复 Worker。"},
                       origin)
            return
        if path != "/restart-stack":
            self._send(404, {"code": "SUPERVISOR_UNKNOWN_PATH"}, origin)
            return
        if self._refused_marker(origin):
            return
        with _LOCK:
            if _STATE["phase"] in ("stopping", "starting"):
                started_at = _STATE.get("started_at")
                age = time.time() - float(started_at) if started_at else None
                if age is not None and age > RESTART_STALE_AFTER_SECONDS:
                    # A restart that never reported back must not lock the
                    # button forever: the operator has no way to clear it, so
                    # the stale attempt is abandoned and a new one is allowed.
                    _STATE.update(phase="failed",
                                  detail=f"上一次重启已停滞 {int(age)} 秒，已作废并重新开始",
                                  finished_at=time.time())
                else:
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
    parser.add_argument("--idle-shutdown-minutes", type=float,
                        default=float(os.environ.get("SHINE_IDLE_SHUTDOWN_MINUTES",
                                                     DEFAULT_IDLE_SHUTDOWN_MINUTES)))
    args = parser.parse_args()
    data_dir = Path(args.data_dir)
    (data_dir / "run").mkdir(parents=True, exist_ok=True)

    Handler.data_dir = data_dir
    Handler.api_port = args.api_port
    Handler.web_port = args.web_port
    Handler.idle_minutes = args.idle_shutdown_minutes
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    # Recorded outside processes.json so stop_shine.py never kills the supervisor.
    (data_dir / "run" / "supervisor.json").write_text(
        json.dumps({"pid": os.getpid(), "port": args.port, "data_dir": str(data_dir)}, indent=2),
        encoding="utf-8",
    )
    threading.Thread(target=_watchdog, args=(data_dir, args.idle_shutdown_minutes),
                     daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())