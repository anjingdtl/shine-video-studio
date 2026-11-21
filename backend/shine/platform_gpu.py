"""Unified platform GPU arbitration (single OS mutex + ResourceScheduler ledger).

Both seven-stage (``director7``) and four-stage (``manual4``) share the same
physical GPU and the single authoritative ``ResourceScheduler`` /
``resource_leases`` ledger in ``shine.db``, guarded by one OS-level named mutex
so cross-process admission is atomic.

* Neither line may steal an active or reconcilable lease held by the other line.
* Releasing an expired ``m4:`` lease requires proving the Manual4 renderer
  process recorded in ``manual4.sqlite`` is gone (or never existed).
* Manual4 never terminates or touches an active ``director7`` ComfyUI process.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from shine.comfyops.models import ResourceLease
from shine.models import utcnow

_INPROC_LOCKS: dict[str, threading.RLock] = {}
_INPROC_GUARD = threading.Lock()
_HELD_MUTEXES = threading.local()

DEFAULT_LEASE_TTL_SECONDS = 60
WAITING_LEASE_TTL_SECONDS = 60


def data_dir_for_session(db) -> Path | str:
    """Derive the canonical arbitration scope key from a SQLAlchemy session."""
    try:
        bind = db.get_bind()
        database = getattr(getattr(bind, "url", None), "database", None)
        if database and database != ":memory:":
            return Path(database).resolve().parent
        return f"memory:{id(bind)}"
    except Exception:
        return "memory:default"


def _arbitrator_name(scope: Path | str) -> str:
    if isinstance(scope, str) and scope.startswith("memory:"):
        canonical = scope.upper()
    else:
        canonical = os.path.abspath(scope).rstrip("\\").upper()
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:40]
    return f"Global\\ShinePlatformGpuArbitrator-{digest}"


def _inproc_lock(name: str) -> threading.RLock:
    with _INPROC_GUARD:
        lock = _INPROC_LOCKS.get(name)
        if lock is None:
            lock = threading.RLock()
            _INPROC_LOCKS[name] = lock
        return lock


@contextmanager
def gpu_arbitrator_lock(data_dir: Path | str, timeout_s: float = 15.0):
    """Cross-process atomic critical section for GPU lease admission/mutation."""
    name = _arbitrator_name(data_dir)
    held: dict[str, int] = getattr(_HELD_MUTEXES, "counts", None) or {}
    _HELD_MUTEXES.counts = held
    if held.get(name, 0) > 0:
        held[name] += 1
        try:
            yield
        finally:
            held[name] -= 1
        return

    lock = _inproc_lock(name)
    if not lock.acquire(timeout=timeout_s):
        raise HTTPException(409, {"code": "PLATFORM_GPU_ARBITRATOR_BUSY", "message": "GPU 仲裁锁正忙，请稍后重试。"})

    win_handle = None
    try:
        if os.name == "nt":
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
            kernel32.CreateMutexW.restype = ctypes.c_void_p
            kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            kernel32.WaitForSingleObject.restype = ctypes.c_uint32
            kernel32.ReleaseMutex.argtypes = [ctypes.c_void_p]
            kernel32.ReleaseMutex.restype = ctypes.c_int
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle.restype = ctypes.c_int
            win_handle = kernel32.CreateMutexW(None, 0, name)
            if not win_handle:
                raise HTTPException(409, {"code": "PLATFORM_GPU_ARBITRATOR_UNAVAILABLE"})
            wait = kernel32.WaitForSingleObject(win_handle, int(max(timeout_s, 0.1) * 1000))
            if wait not in (0x0, 0x80):  # WAIT_OBJECT_0 / WAIT_ABANDONED
                kernel32.CloseHandle(win_handle)
                win_handle = None
                raise HTTPException(
                    409,
                    {"code": "PLATFORM_GPU_ARBITRATOR_BUSY", "message": "GPU 仲裁锁正忙，请稍后重试。"},
                )
        held[name] = 1
        try:
            yield
        finally:
            held[name] -= 1
    finally:
        if win_handle is not None:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.ReleaseMutex(win_handle)
            kernel32.CloseHandle(win_handle)
        lock.release()


def _line_of_owner(owner: str) -> str:
    return "manual4" if str(owner or "").startswith("m4:") else "director7"


def inspect_gpu_status(platform_factory: sessionmaker, data_dir: Path | None = None) -> dict[str, Any]:
    """Read current GPU lease state across both production lines."""
    _ = data_dir
    with platform_factory() as db:
        now = utcnow()
        rows = db.scalars(select(ResourceLease).order_by(ResourceLease.created_at)).all()
        active = []
        reconcile = []
        for row in rows:
            status = row.status
            if status == "ACTIVE" and row.expires_at <= now:
                status = "RECONCILE_REQUIRED"
            info = {
                "lease_id": row.id,
                "owner": row.owner,
                "line": _line_of_owner(row.owner),
                "status": status,
                "budget_mb": row.budget_mb,
                "expires_at": row.expires_at.isoformat() + "Z",
            }
            if status == "ACTIVE":
                active.append(info)
            elif status == "RECONCILE_REQUIRED":
                reconcile.append(info)
        return {
            "busy": bool(active or reconcile),
            "active_leases": active,
            "reconcile_required": reconcile,
        }


def is_m4_renderer_gone_for_lease(data_dir: Path | str, lease_id: str) -> bool:
    """Prove whether a Manual4 renderer owning ``lease_id`` has genuinely exited.

    Reads ``manual4.sqlite`` read-only; uncertainty (corrupt/unreadable DB,
    live process, active Job Object member, or occupied loopback port) always
    returns ``False``.
    """
    if isinstance(data_dir, str) and data_dir.startswith("memory:"):
        return False
    m4_db_path = Path(data_dir) / "manual4.sqlite"
    if not m4_db_path.is_file():
        return True
    try:
        conn = sqlite3.connect(f"file:{m4_db_path.as_posix()}?mode=ro", uri=True, timeout=3.0)
        try:
            row = conn.execute(
                "SELECT status, pid, birth, job_name, port, lease_id FROM m4_renderer_state WHERE id = 'comfyui-m4'"
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return False

    if row is None:
        return True
    status, pid, birth, job_name, port, rec_lease_id = row
    if rec_lease_id and rec_lease_id != lease_id:
        # Renderer record already moved on to another lease; the old lease has no live renderer.
        return True
    if status == "STOPPED":
        return True

    from shine.comfyops.installer import process_identity
    from shine.comfyops.owned_process import named_group_active_processes
    from shine.comfyops.renderer_supervisor import windows_port_listeners

    if pid and birth:
        try:
            if process_identity(int(pid)) == str(birth):
                return False
        except Exception:
            return False
    if job_name:
        try:
            if named_group_active_processes(str(job_name)) > 0:
                return False
        except Exception:
            return False
    if port and os.name == "nt":
        try:
            if windows_port_listeners(int(port)):
                return False
        except Exception:
            return False
    return True


def read_m4_renderer_snapshot(data_dir: Path | str) -> dict[str, Any] | None:
    """Read-only state of the Manual4 managed renderer, if active or uncertain.

    Shares the exact physical process/Job Object/port proof with
    ``is_m4_renderer_gone_for_lease`` so status classification and lease
    reconciliation read one set of facts.
    """
    if isinstance(data_dir, str) and data_dir.startswith("memory:"):
        return None
    m4_db_path = Path(data_dir) / "manual4.sqlite"
    if not m4_db_path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{m4_db_path.as_posix()}?mode=ro", uri=True, timeout=3.0)
        try:
            row = conn.execute(
                "SELECT status, pid, birth, job_name, port, lease_id, lease_owner "
                "FROM m4_renderer_state WHERE id = 'comfyui-m4'"
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None

    if row is None:
        return None
    status, pid, birth, job_name, port, lease_id, lease_owner = row
    if status == "STOPPED":
        return None
    if status == "RECONCILE_REQUIRED":
        state = "UNCERTAIN"
    else:
        alive = False
        if pid and birth:
            try:
                from shine.comfyops.installer import process_identity

                alive = process_identity(int(pid)) == str(birth)
            except Exception:
                alive = False
        if not alive and job_name:
            try:
                from shine.comfyops.owned_process import named_group_active_processes

                alive = named_group_active_processes(str(job_name)) > 0
            except Exception:
                alive = False
        if alive:
            state = "STARTING" if status == "STARTING" else "ACTIVE"
        elif status == "STARTING" and not pid:
            state = "STARTING"
        else:
            state = "GONE"
    return {
        "status": status,
        "state": state,
        "pid": pid,
        "port": port,
        "lease_id": lease_id,
        "lease_owner": lease_owner,
        "purpose": "m4",
    }


def acquire_m4_gpu_lease(
    platform_factory: sessionmaker,
    data_dir: Path,
    *,
    owner: str,
    budget_mb: int = 16384,
    priority: int = 10,
    wait_seconds: float = 0.0,
) -> dict[str, Any]:
    """Atomically acquire an exclusive platform GPU lease via the unified ResourceScheduler."""
    from shine.comfyops.scheduler import ResourceScheduler

    if not owner.startswith("m4:"):
        raise ValueError("Manual4 lease owner must start with 'm4:'")

    scheduler = ResourceScheduler()
    deadline = time.monotonic() + max(0.0, wait_seconds)
    while True:
        blocker_status = "ACTIVE"
        blocker_line = "director7"
        blocker_owner = ""
        with gpu_arbitrator_lock(data_dir):
            with platform_factory() as db:
                row = scheduler.enqueue(db, owner, budget_mb, True, priority)
                lease_id = row.id
                should_release_waiting = row.status == "WAITING"
                db.commit()

            with platform_factory() as db:
                admitted = scheduler.admit(db, lease_id)

            if admitted:
                with platform_factory() as db:
                    lease = db.get(ResourceLease, lease_id)
                    expires_at = lease.expires_at if lease is not None else utcnow()
                return {
                    "lease_id": lease_id,
                    "owner": owner,
                    "expires_at": expires_at.isoformat() + "Z",
                }

            with platform_factory() as db:
                rows = db.scalars(
                    select(ResourceLease).order_by(ResourceLease.created_at, ResourceLease.id)
                ).all()
                blocking = [r for r in rows if r.status in {"ACTIVE", "RECONCILE_REQUIRED"}]
                if blocking:
                    blocker = blocking[0]
                    blocker_status = blocker.status
                    blocker_line = _line_of_owner(blocker.owner)
                    blocker_owner = blocker.owner
                else:
                    waiting = [r for r in rows if r.status == "WAITING" and r.id != lease_id]
                    if waiting:
                        blocker_status = "WAITING"
                        blocker_line = _line_of_owner(waiting[0].owner)
                        blocker_owner = waiting[0].owner

                if should_release_waiting:
                    self_row = db.get(ResourceLease, lease_id)
                    if self_row is not None and self_row.status == "WAITING":
                        scheduler.release(db, lease_id, owner)
                        db.commit()

        if time.monotonic() >= deadline or blocker_status == "RECONCILE_REQUIRED":
            code = "GPU_RECONCILE_REQUIRED" if blocker_status == "RECONCILE_REQUIRED" else "GPU_BUSY_OTHER_LINE"
            line_label = "七阶 AI 导演线" if blocker_line == "director7" else "四阶 H3 自主制片线"
            action_hint = (
                "请先完成待核对任务后再启动 GPU 生成。"
                if blocker_status == "RECONCILE_REQUIRED"
                else (
                    f"当前显卡正被{line_label}任务（{blocker_owner}）占用；"
                    "请等待其释放后再继续，系统不会强制中断另一条生产线的作业。"
                )
            )
            raise HTTPException(
                409,
                {
                    "code": code,
                    "message": action_hint,
                    "blocking_line": blocker_line,
                    "blocking_owner": blocker_owner,
                    "blocking_status": blocker_status,
                },
            )
        time.sleep(min(0.25, max(0.05, deadline - time.monotonic())))


def heartbeat_m4_gpu_lease(
    platform_factory: sessionmaker,
    data_dir: Path,
    *,
    lease_id: str,
    owner: str,
    ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS,
) -> None:
    from shine.comfyops.scheduler import ResourceScheduler

    _ = ttl_seconds
    with gpu_arbitrator_lock(data_dir):
        with platform_factory.begin() as db:
            try:
                ResourceScheduler().heartbeat(db, lease_id, owner)
            except HTTPException as exc:
                raise HTTPException(409, {"code": "M4_RESOURCE_LEASE_LOST"}) from exc


def release_m4_gpu_lease(
    platform_factory: sessionmaker,
    data_dir: Path,
    *,
    lease_id: str,
    owner: str,
    reconciled: bool = False,
) -> bool:
    from shine.comfyops.scheduler import ResourceScheduler

    if not owner.startswith("m4:"):
        raise HTTPException(403, {"code": "M4_LEASE_OWNER_FORBIDDEN"})
    with gpu_arbitrator_lock(data_dir):
        with platform_factory.begin() as db:
            lease = db.get(ResourceLease, lease_id)
            if lease is None:
                return False
            if lease.owner != owner:
                raise HTTPException(403, {"code": "M4_LEASE_OWNER_MISMATCH"})
            if lease.status == "RELEASED":
                return True
            if lease.status == "RECONCILE_REQUIRED" and not reconciled:
                raise HTTPException(409, {"code": "M4_LEASE_RECONCILE_REQUIRED"})
            ResourceScheduler().release(db, lease_id, owner, reconciled=reconciled)
            return True


def release_orphaned_m4_leases(platform_factory: sessionmaker, data_dir: Path) -> int:
    """Release any dangling ``m4:`` GPU leases when no Manual4 renderer process exists."""
    with platform_factory() as db:
        rows = db.scalars(
            select(ResourceLease).where(
                ResourceLease.owner.like("m4:%"),
                ResourceLease.status.in_(("ACTIVE", "RECONCILE_REQUIRED")),
            )
        ).all()
        pairs = [(row.id, row.owner) for row in rows]
    released = 0
    for lease_id, owner in pairs:
        if is_m4_renderer_gone_for_lease(data_dir, lease_id):
            if release_m4_gpu_lease(platform_factory, data_dir, lease_id=lease_id, owner=owner, reconciled=True):
                released += 1
    return released
