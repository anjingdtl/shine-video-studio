"""Platform-level cross-line GPU arbitration (OS mutex + shared resource_leases).

Both seven-stage (``director7``) and four-stage (``manual4``) share the same
physical GPU and the same ``resource_leases`` ledger in ``shine.db``, guarded by
an OS-level mutual exclusion primitive so two processes never race on "check
then insert".

* Neither line may steal an active or reconcilable lease held by the other line.
* Releasing an expired ``m4:`` lease requires proving the Manual4 renderer
  process recorded in ``manual4.sqlite`` is gone (or never existed).
* Manual4 never terminates or touches a ``director7`` ComfyUI process.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import threading
import time
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from shine.comfyops.models import ResourceLease
from shine.models import utcnow

_INPROC_LOCKS: dict[str, threading.RLock] = {}
_INPROC_GUARD = threading.Lock()
_HELD_MUTEXES = threading.local()

DEFAULT_LEASE_TTL_SECONDS = 60
WAITING_LEASE_TTL_SECONDS = 60


def _arbitrator_name(data_dir: Path) -> str:
    canonical = os.path.abspath(data_dir).rstrip("\\").upper()
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
def gpu_arbitrator_lock(data_dir: Path, timeout_s: float = 15.0):
    """Cross-process atomic critical section for GPU lease inspection/mutation."""
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


def inspect_gpu_status(platform_factory: sessionmaker, data_dir: Path) -> dict[str, Any]:
    """Read current GPU lease state across both production lines."""
    with gpu_arbitrator_lock(data_dir):
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


def acquire_m4_gpu_lease(
    platform_factory: sessionmaker,
    data_dir: Path,
    *,
    owner: str,
    budget_mb: int = 16384,
    priority: int = 10,
    wait_seconds: float = 0.0,
) -> dict[str, Any]:
    """Atomically acquire an exclusive platform GPU lease for Manual4."""
    if not owner.startswith("m4:"):
        raise ValueError("Manual4 lease owner must start with 'm4:'")

    deadline = time.monotonic() + max(0.0, wait_seconds)
    while True:
        with gpu_arbitrator_lock(data_dir):
            with platform_factory.begin() as db:
                db.execute(text("BEGIN IMMEDIATE"))
                now = utcnow()
                rows = db.scalars(select(ResourceLease).order_by(ResourceLease.created_at, ResourceLease.id)).all()
                for row in rows:
                    if row.status == "ACTIVE" and row.expires_at <= now:
                        row.status = "RECONCILE_REQUIRED"
                    elif row.status == "WAITING" and row.expires_at <= now:
                        row.status = "RELEASED"

                blocking = [row for row in rows if row.status in {"ACTIVE", "RECONCILE_REQUIRED"}]
                if not blocking:
                    existing = next((row for row in rows if row.owner == owner), None)
                    expires = now + timedelta(seconds=DEFAULT_LEASE_TTL_SECONDS)
                    if existing is not None:
                        existing.status = "ACTIVE"
                        existing.budget_mb = budget_mb
                        existing.exclusive = True
                        existing.priority = priority
                        existing.heartbeat_at = now
                        existing.expires_at = expires
                        lease_id = existing.id
                    else:
                        lease = ResourceLease(
                            id=str(uuid4()),
                            owner=owner,
                            gpu="gpu-0",
                            budget_mb=budget_mb,
                            exclusive=True,
                            priority=priority,
                            status="ACTIVE",
                            heartbeat_at=now,
                            expires_at=expires,
                        )
                        db.add(lease)
                        db.flush()
                        lease_id = lease.id
                    return {"lease_id": lease_id, "owner": owner, "expires_at": expires.isoformat() + "Z"}

                blocker = blocking[0]
                blocker_line = _line_of_owner(blocker.owner)
                blocker_status = blocker.status
                blocker_owner = blocker.owner

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
    with gpu_arbitrator_lock(data_dir):
        with platform_factory.begin() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            now = utcnow()
            lease = db.get(ResourceLease, lease_id)
            if lease is None or lease.owner != owner or lease.status != "ACTIVE" or lease.expires_at <= now:
                raise HTTPException(409, {"code": "M4_RESOURCE_LEASE_LOST"})
            lease.heartbeat_at = now
            lease.expires_at = now + timedelta(seconds=ttl_seconds)


def release_m4_gpu_lease(
    platform_factory: sessionmaker,
    data_dir: Path,
    *,
    lease_id: str,
    owner: str,
    reconciled: bool = False,
) -> bool:
    if not owner.startswith("m4:"):
        raise HTTPException(403, {"code": "M4_LEASE_OWNER_FORBIDDEN"})
    with gpu_arbitrator_lock(data_dir):
        with platform_factory.begin() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            lease = db.get(ResourceLease, lease_id)
            if lease is None:
                return False
            if lease.owner != owner:
                raise HTTPException(403, {"code": "M4_LEASE_OWNER_MISMATCH"})
            if lease.status == "RELEASED":
                return True
            if lease.status == "RECONCILE_REQUIRED" and not reconciled:
                raise HTTPException(409, {"code": "M4_LEASE_RECONCILE_REQUIRED"})
            lease.status = "RELEASED"
            return True
