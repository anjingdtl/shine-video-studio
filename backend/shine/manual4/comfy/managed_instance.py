"""Dedicated managed ComfyUI renderer lifecycle for Manual4 via unified foundation.

Isolation & Single-Authority guarantees:
* Environment, Python, custom_nodes & weights are resolved through the single
  authoritative ``shine.foundation.environment.resolve_shared_comfy_launch``.
* GPU arbitration goes through the single ``platform_gpu`` / ``ResourceScheduler``
  ledger; idle director7 image renderers are cleanly retired under the shared
  ``renderer_lifecycle_guard`` before Manual4 launches ComfyUI.
* Dedicated loopback port (default 8198+, never touching 8188), dedicated runtime
  directories (``<m4_root>/comfy-runtime/``), and Windows Job Object
  (``Local\\ShineM4Handoff-*``) via shared ``OwnedProcessGroup``.
* Background 15s heartbeat renews both the platform GPU lease and caller worker
  heartbeat across long renders (>60s).
"""

from __future__ import annotations

import logging
import os
import secrets
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from shine.foundation.environment import resolve_shared_comfy_launch
from shine.foundation.runtime import (
    OwnedProcessGroup,
    acquire_m4_gpu_lease,
    heartbeat_m4_gpu_lease,
    launch_owned_comfyui_group,
    named_group_active_processes,
    process_identity,
    release_m4_gpu_lease,
    retire_idle_director7_image_renderer,
    terminate_named_group,
    verify_loopback_listener_ownership,
    windows_port_listeners,
)
from shine.manual4.comfy.client import M4ComfyClient
from shine.manual4.storage.files import sha256_bytes
from shine.manual4.storage.models import M4RendererState, m4_utcnow

logger = logging.getLogger("shine.manual4.comfy")
HEARTBEAT_INTERVAL_S = 15.0


def resolve_m4_comfy_spec(settings, m4_root: Path, *, platform_factory=None) -> dict[str, Any]:
    """Resolve shared ComfyUI launch config from the single authoritative machine ledger."""
    if platform_factory is not None:
        with platform_factory() as db:
            return resolve_shared_comfy_launch(db, settings, m4_root)
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
    try:
        with sessionmaker(bind=engine)() as db:
            return resolve_shared_comfy_launch(db, settings, m4_root)
    finally:
        engine.dispose()


def _renderer_is_gone(state: M4RendererState) -> bool:
    if state.pid and state.birth:
        try:
            if process_identity(int(state.pid)) == str(state.birth):
                return False
        except Exception:
            return False
    if state.job_name:
        try:
            if named_group_active_processes(str(state.job_name)) > 0:
                return False
        except Exception:
            return False
    if state.port and os.name == "nt":
        try:
            if windows_port_listeners(int(state.port)):
                return False
        except Exception:
            return False
    return True


def reconcile_m4_renderer(platform_factory, m4_factory, data_dir: Path) -> dict[str, Any]:
    """Reconcile the Manual4 renderer and its GPU lease as the single executor.

    A renderer proven gone has its lease released.  A renderer that lingers alive
    while **no run is active** is an idle orphan this installation owns (a post-run
    terminate that did not take): stop it through its named Job Object and release
    the lease, so an idle worker never keeps holding the GPU against a stop request
    (remote report 2026-11-21: ``runtime-orchestrator/stop`` refused 409
    ``RUNTIME_WORK_IN_FLIGHT`` on an idle ``m4:`` lease).  A renderer serving an
    active run is never touched — that is real in-flight work.
    """
    from sqlalchemy import select

    from shine.manual4.storage.models import M4ProductionRun

    with m4_factory.begin() as db:
        state = db.get(M4RendererState, "comfyui-m4")
        if state is None or state.status == "STOPPED":
            return {"reconciled": False, "status": "STOPPED"}
        active_run = db.scalar(select(M4ProductionRun).where(
            M4ProductionRun.status.in_(("CLAIMED", "RUNNING", "PAUSE_REQUESTED"))))
        job_name = state.job_name
        lease_id, lease_owner = state.lease_id, state.lease_owner
        gone = _renderer_is_gone(state)

    if not gone:
        if active_run is not None:
            raise HTTPException(
                409,
                {
                    "code": "M4_RENDERER_STILL_RUNNING",
                    "message": f"四阶 ComfyUI 渲染器仍在运行（PID {state.pid}），请先停止或等待其结束。",
                },
            )
        # Idle orphan: stop the renderer this installation owns, then re-prove gone.
        if job_name:
            try:
                terminate_named_group(str(job_name), timeout=15)
            except Exception:  # noqa: BLE001 - re-proven below; never blind-release
                logger.warning("m4_idle_renderer_terminate_failed job_name=%s", job_name)
        with m4_factory() as db:
            state = db.get(M4RendererState, "comfyui-m4")
            if state is None or not _renderer_is_gone(state):
                raise HTTPException(
                    409,
                    {
                        "code": "M4_RENDERER_STILL_RUNNING",
                        "message": "四阶 ComfyUI 渲染器仍在运行，无法证明已停止；租约保留待核对。",
                    },
                )

    if lease_id and lease_owner:
        release_m4_gpu_lease(
            platform_factory, data_dir, lease_id=lease_id, owner=lease_owner, reconciled=True
        )
    with m4_factory.begin() as db:
        state = db.get(M4RendererState, "comfyui-m4")
        if state is not None:
            state.status = "STOPPED"
            state.pid = None
            state.birth = None
            state.lease_id = None
            state.lease_owner = None
            state.updated_at = m4_utcnow()
    return {"reconciled": True, "status": "STOPPED"}


@contextmanager
def managed_m4_comfyui(
    platform_factory,
    m4_factory,
    settings,
    m4_root: Path,
    *,
    owner: str,
    startup_timeout_s: float = 300.0,
    on_lease_acquired: Callable[[str, str], None] | None = None,
    on_heartbeat: Callable[[], None] | None = None,
):
    """Acquire unified GPU lease, launch isolated ComfyUI on shared install, renew heartbeat, then stop."""
    owner = owner if owner.startswith("m4:") else f"m4:{owner}"
    spec = resolve_m4_comfy_spec(settings, m4_root, platform_factory=platform_factory)

    # Guard against a leftover un-reconciled live renderer before taking a new lease
    with m4_factory() as db:
        prev_state = db.get(M4RendererState, "comfyui-m4")
        if prev_state is not None and prev_state.status != "STOPPED":
            if not _renderer_is_gone(prev_state):
                raise HTTPException(
                    409,
                    {
                        "code": "M4_RENDERER_STILL_RUNNING",
                        "message": f"四阶 ComfyUI 渲染器仍在运行（PID {prev_state.pid}）。",
                    },
                )

    retire_idle_director7_image_renderer(platform_factory, settings, spec.get("instance_id"))
    lease = acquire_m4_gpu_lease(platform_factory, settings.data_dir, owner=owner, budget_mb=16384)
    lease_id = lease["lease_id"]
    if on_lease_acquired is not None:
        on_lease_acquired(lease_id, owner)

    port = int(spec["port"])
    url = f"http://127.0.0.1:{port}"
    token = secrets.token_urlsafe(32)
    job_name = f"Local\\ShineM4Handoff-{uuid4().hex}"
    env = {
        **spec["env_base"],
        "PYTHONDONTWRITEBYTECODE": "1",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "PIP_NO_INDEX": "1",
        "UV_NO_INDEX": "1",
        "SHINE_COMFY_HANDOFF_TOKEN": token,
        "SHINE_COMFY_START_DRAINED": "1",
        "SHINE_COMFY_BOOTSTRAP_ROOT": spec["root"],
        "SHINE_COMFY_ADAPTER_FILE": str(Path(spec["adapter_dir"]) / "__init__.py"),
        "SHINE_COMFY_EXPECTED_ADAPTER_SHA256": spec["adapter_sha256"],
        "SHINE_COMFY_LOG_FILE": spec["log_file"],
    }

    with m4_factory.begin() as db:
        row = db.get(M4RendererState, "comfyui-m4")
        if row is None:
            row = M4RendererState(id="comfyui-m4")
            db.add(row)
        row.status = "STARTING"
        row.port = port
        row.job_name = job_name
        row.url = url
        row.handoff_token_sha256 = sha256_bytes(token.encode("utf-8"))
        row.runtime_root = spec["runtime_root"]
        row.log_file = spec["log_file"]
        row.lease_id = lease_id
        row.lease_owner = owner
        row.updated_at = m4_utcnow()

    stop_hb = threading.Event()
    hb_errors: list[str] = []

    def _pulse() -> None:
        while not stop_hb.wait(HEARTBEAT_INTERVAL_S):
            try:
                heartbeat_m4_gpu_lease(
                    platform_factory, settings.data_dir, lease_id=lease_id, owner=owner
                )
                if on_heartbeat is not None:
                    on_heartbeat()
            except Exception as exc:  # noqa: BLE001 - defer transient lock, record fatal loss
                hb_errors.append(type(exc).__name__)
                logger.warning("m4_gpu_lease_heartbeat_error lease_id=%s err=%s", lease_id, exc)
                if "LOST" in str(getattr(exc, "detail", "")):
                    stop_hb.set()

    hb_thread = threading.Thread(target=_pulse, name="shine-m4-lease-heartbeat", daemon=True)
    hb_thread.start()

    group: OwnedProcessGroup | None = None
    birth = ""
    client = M4ComfyClient(url, timeout_s=30.0)
    try:
        group, birth = launch_owned_comfyui_group(
            spec["argv"],
            cwd=Path(spec["root"]),
            env=env,
            pinned_files=spec["pinned_files"],
            job_name=job_name,
        )
        with m4_factory.begin() as db:
            row = db.get(M4RendererState, "comfyui-m4")
            row.pid = group.pid
            row.birth = birth
            row.updated_at = m4_utcnow()

        deadline = time.monotonic() + startup_timeout_s
        while time.monotonic() < deadline:
            try:
                client.system_stats(timeout_s=3.0)
                verify_loopback_listener_ownership(port, group, birth)
                client.handoff_resume(token)
                break
            except Exception as exc:
                if group.exit_code_if_stopped() is not None:
                    raise HTTPException(
                        500,
                        {
                            "code": "M4_COMFY_EXITED_DURING_STARTUP",
                            "message": f"四阶 ComfyUI 启动过程中退出，请查看日志：{spec['log_file']}",
                        },
                    ) from exc
                time.sleep(0.75)
        else:
            raise HTTPException(504, {"code": "M4_COMFY_STARTUP_TIMEOUT", "message": "四阶 ComfyUI 启动超时"})

        with m4_factory.begin() as db:
            row = db.get(M4RendererState, "comfyui-m4")
            row.status = "RUNNING"
            row.updated_at = m4_utcnow()

        yield client, spec
    finally:
        stop_hb.set()
        hb_thread.join(timeout=5.0)
        stop_ok = False
        try:
            try:
                client.handoff_drain(token)
            except Exception:
                pass
            if group is not None:
                res = group.terminate(timeout=15)
                stop_ok = res.get("active_processes", 1) == 0
                group.close()
            else:
                res = terminate_named_group(job_name, timeout=10)
                stop_ok = res.get("active_processes", 1) == 0
        except Exception:
            try:
                stop_ok = named_group_active_processes(job_name) == 0
            except Exception:
                stop_ok = False
        if stop_ok:
            release_m4_gpu_lease(
                platform_factory, settings.data_dir, lease_id=lease_id, owner=owner, reconciled=True
            )
            with m4_factory.begin() as db:
                row = db.get(M4RendererState, "comfyui-m4")
                if row is not None:
                    row.status = "STOPPED"
                    row.pid = None
                    row.birth = None
                    row.lease_id = None
                    row.lease_owner = None
                    row.updated_at = m4_utcnow()
        else:
            with m4_factory.begin() as db:
                row = db.get(M4RendererState, "comfyui-m4")
                if row is not None:
                    row.status = "RECONCILE_REQUIRED"
                    row.updated_at = m4_utcnow()
