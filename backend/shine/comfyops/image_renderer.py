"""The managed renderer that serves the local image runtime.

ComfyUI belongs to the workbench.  A renderer that a script started keeps the
weights resident in GPU memory and cannot be released afterwards, because the
workbench cannot prove that it owns it.  The image runtime therefore never
binds an arbitrary URL: it binds an instance this installation manages, and the
renderer that serves it is started here.

The startup follows the discipline the H3 renderer already uses:

* the intent is written before the process exists, so an interrupted start is
  reconciled instead of relaunched;
* ownership is proved by PID, OS birth identity and named Job Object
  membership;
* the queue starts closed and is opened only after the adapter answered;
* the memory admission is measured on the host, so a renderer that cannot be
  measured is never registered;
* the record is written under the ``renderer-startup:`` prefix, so the
  existing release path stops this renderer and gives the GPU memory back.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import text

from shine.comfy.workflow import graph_hash
from shine.comfyops.handoff_memory import evaluate_readiness, probe_memory
from shine.comfyops.installer import process_identity
from shine.comfyops.managed_recipe import build_renderer_launch, load_managed_recipe
from shine.comfyops.models import ComfyInstance
from shine.comfyops.owned_process import OwnedProcessGroup, terminate_named_group
from shine.comfyops.renderer_release import live_process_facts, managed_process_state
from shine.comfyops.renderer_supervisor import RendererSupervisor, windows_port_listeners
from shine.models import AppSetting, utcnow
from shine.services import add_audit

IMAGE_STARTUP_PREFIX = "renderer-startup:image:"
"""Same prefix as the H3 renderer, so one release path stops both."""

RECIPE_RELATIVE_PATH = ("setup", "managed_recipe.json")
"""The recipe ``generate_configuration`` wrote for this installation."""


def image_startup_key(instance_id: str) -> str:
    if not isinstance(instance_id, str) or not instance_id or len(instance_id) > 96:
        raise HTTPException(409, "IMAGE_RENDERER_IDENTITY_INVALID")
    return IMAGE_STARTUP_PREFIX + instance_id


def recipe_path(settings) -> Path:
    return Path(settings.comfyops_dir).joinpath(*RECIPE_RELATIVE_PATH)


def load_image_recipe(settings) -> dict[str, Any]:
    """The pinned recipe of the managed renderer, or a named blocker."""
    path = recipe_path(settings)
    if not path.is_file():
        raise HTTPException(409, {
            "code": "IMAGE_RENDERER_RECIPE_MISSING",
            "detail": "还没有为本机生成受管运行配置。请先在基础配置里完成本机环境登记。",
        })
    return load_managed_recipe(str(path))


def _record(factory, key: str, value: dict[str, Any]) -> None:
    with factory.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        row = db.get(AppSetting, key)
        if row is None:
            db.add(AppSetting(key=key, value=value))
        else:
            row.value = value


def _wait_for_owned_listener(group, port: int, *, timeout: float = 600.0) -> dict[str, Any]:
    """Wait until the only loopback listener belongs to the owned group.

    A real renderer loads thousands of node types before it answers, so the
    wait is generous: the startup intent is already durable.
    """
    deadline = time.monotonic() + timeout
    while True:
        listeners = windows_port_listeners(port)
        if listeners:
            if len(listeners) != 1 or listeners[0][0] != "127.0.0.1":
                raise HTTPException(409, "IMAGE_RENDERER_LISTENER_UNVERIFIED")
            pid = listeners[0][1]
            birth = process_identity(pid)
            if not birth or not group.owns(pid, birth):
                raise HTTPException(409, "IMAGE_RENDERER_OWNERSHIP_UNVERIFIED")
            return {"pid": pid, "birth": birth}
        if time.monotonic() >= deadline:
            raise HTTPException(409, "IMAGE_RENDERER_LISTENER_TIMEOUT")
        time.sleep(0.05)


def image_renderer_state(db, settings, instance_id: str) -> dict[str, Any]:
    """What can be proven about the image renderer right now."""
    if not instance_id:
        return {"state": "NOT_BOUND", "instance_id": "", "port": None, "pid": None}
    key = image_startup_key(instance_id)
    row = db.get(AppSetting, key)
    record = dict(row.value) if row is not None and isinstance(row.value, dict) else {}
    if not record:
        return {"state": "NOT_STARTED", "instance_id": instance_id, "port": None, "pid": None}
    facts = live_process_facts(record)
    return {
        "state": managed_process_state(record),
        "instance_id": instance_id,
        "port": record.get("port"),
        "pid": facts.get("pid"),
        "stage": record.get("stage"),
        "recorded_at": record.get("recorded_at"),
        "recipe_path": str(recipe_path(settings)),
    }


def start_image_renderer(factory, settings, *, instance_id: str, actor: str) -> dict[str, Any]:
    """Start the managed renderer that serves the image runtime.

    The renderer is started closed and is opened for the image queue only after
    the adapter proved its own identity, so a renderer that does not belong to
    this installation never accepts a prompt.
    """
    recipe = load_image_recipe(settings)
    renderer = recipe["renderer"]
    if renderer.get("instance_id") != instance_id:
        raise HTTPException(409, {
            "code": "IMAGE_RENDERER_RECIPE_MISMATCH",
            "detail": "出图运行时绑定的实例与本机受管运行配置不是同一个实例。",
        })
    if os.name != "nt":
        raise HTTPException(409, "IMAGE_RENDERER_WINDOWS_ONLY")
    port = int(renderer["port"])
    key = image_startup_key(instance_id)
    recipe_sha256 = graph_hash(renderer)
    with factory.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        instance = db.get(ComfyInstance, instance_id)
        if instance is None or not instance.payload.get("managed") or instance.payload.get("is_mock"):
            raise HTTPException(409, "IMAGE_RENDERER_MANAGED_INSTANCE_REQUIRED")
        if instance.payload.get("process"):
            raise HTTPException(409, "IMAGE_RENDERER_ALREADY_STARTED")
        if instance.status not in {"STOPPED", "CREATED", "MOCK_VERIFIED"}:
            raise HTTPException(409, "IMAGE_RENDERER_INSTANCE_NOT_STOPPED")
        existing = db.get(AppSetting, key)
        if existing is not None and isinstance(existing.value, dict):
            if managed_process_state(existing.value) == "RUNNING":
                raise HTTPException(409, "IMAGE_RENDERER_ALREADY_RUNNING")
        intent = {
            "stage": "INTENT",
            "instance_id": instance_id,
            "startup_id": str(uuid4()),
            "port": port,
            "owner_pid": os.getpid(),
            "owner_birth": process_identity(os.getpid()),
            "recipe_sha256": recipe_sha256,
            "recorded_at": utcnow().isoformat(),
            "actor": actor,
            "purpose": "image",
        }
        if existing is None:
            db.add(AppSetting(key=key, value=intent))
        else:
            existing.value = intent
    argv, cwd, env, pins, token, port = build_renderer_launch(renderer)
    group = None
    try:
        group = OwnedProcessGroup.launch(
            argv, cwd=cwd, env=env, pinned_files=pins,
            job_name="Local\\ShineHandoff-" + uuid4().hex,
        )
        birth = process_identity(group.pid)
        if not birth or not group.owns(group.pid, birth):
            raise HTTPException(409, "IMAGE_RENDERER_OWNERSHIP_UNVERIFIED")
        process = {"pid": group.pid, "birth": birth, "job_name": group.job_name}
        _record(factory, key, {**intent, "stage": "LAUNCHED", "process": process})
        listener = _wait_for_owned_listener(group, port)
        supervisor = RendererSupervisor(group, f"http://127.0.0.1:{port}", token, renderer["adapter_sha256"])
        supervisor.require_closed_restart()
        # The queue opens only after the adapter proved that it is the pinned
        # one; the image submission is a real prompt, not a rehearsal.
        supervisor.resume()
        measured = probe_memory(
            renderer["gpu_uuid"],
            min_ram_mb=int(renderer.get("min_ram_mb", 8192)),
            min_vram_mb=int(renderer.get("min_vram_mb", 12000)),
        )
        verified = evaluate_readiness(
            available_ram_bytes=measured.get("available_ram_bytes"),
            gpu=measured.get("gpu"),
            min_ram_mb=int(renderer.get("min_ram_mb", 8192)),
            min_vram_mb=int(renderer.get("min_vram_mb", 12000)),
            expected_gpu=renderer["gpu_uuid"],
        )
        if measured.get("ready") is not True or not verified["ready"]:
            raise HTTPException(409, {
                "code": "IMAGE_RENDERER_MEMORY_INSUFFICIENT",
                "detail": "本机显存或内存不够运行出图渲染器。",
            })
        with factory.begin() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            instance = db.get(ComfyInstance, instance_id)
            # The image renderer and the H3 renderer share ONE adopted instance
            # row, and the active runtime identity records that row's digest.
            # Writing the image process into it changed the digest, so the
            # runtime identity no longer matched and the Worker reported
            # ACTIVE_RUNTIME_CONFIGURATION_CHANGED after every image render.
            # The image runtime keeps its process, port and listener in its own
            # durable startup record; the shared row is left as the installer
            # left it.
            instance.status = "RUNNING_PENDING_HEALTH"
            row = db.get(AppSetting, key)
            row.value = {**row.value, "stage": "RUNNING", "process": process, "listener": listener}
            add_audit(db, actor, "IMAGE_RENDERER_STARTED", "comfy_instance", instance_id, {
                "port": port, "pid": process["pid"], "job_name": process["job_name"],
                "recipe_sha256": recipe_sha256,
            })
        return {
            "status": "RUNNING",
            "instance_id": instance_id,
            "base_url": f"http://127.0.0.1:{port}",
            "port": port,
            "process": process,
            "listener": listener,
        }
    except Exception as exc:
        _record(factory, key, {
            **intent, "stage": "FAILED", "error": type(exc).__name__,
            "failed_at": utcnow().isoformat(),
        })
        if group is not None:
            try:
                group.terminate(timeout=15)
            except Exception:  # noqa: BLE001 - the failure is already recorded
                pass
        raise


def ensure_image_renderer(factory, settings, *, instance_id: str) -> dict[str, Any]:
    """Make sure the image runtime's renderer is up, and that it is ours.

    A renderer that is already running and provably owned is reused; a renderer
    that cannot be proven is never adopted and never restarted blindly.
    """
    with factory() as db:
        row = db.get(AppSetting, image_startup_key(instance_id))
        record = dict(row.value) if row is not None and isinstance(row.value, dict) else {}
    if record and managed_process_state(record) == "RUNNING":
        return {"status": "RUNNING", "reused": True, "instance_id": instance_id,
                "base_url": f"http://127.0.0.1:{record.get('port')}"}
    result = start_image_renderer(factory, settings, instance_id=instance_id, actor="worker")
    return {**result, "reused": False}


def _clear_image_marks(factory, instance_id: str, job_name: str | None = None) -> bool:
    """Restore the instance row to the shape the installation evidence states.

    An earlier revision of the image renderer wrote its own marks onto the
    shared H3 instance row.  The one-time repair takes them back, including a
    process entry this runtime started, so the row matches the installation
    attempt again and the H3 activation can compare it.
    """
    with factory.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        row = db.get(ComfyInstance, instance_id)
        if row is None or not isinstance(row.payload, dict):
            return False
        cleaned = _without_image_marks(row.payload)
        process = cleaned.get("process") or {}
        if job_name and process.get("job_name") == job_name:
            cleaned = {**cleaned, "process": None}
        if cleaned == row.payload:
            return False
        row.payload = cleaned
        row.sha256 = graph_hash(cleaned)
        return True


def stop_image_renderer(factory, settings, *, instance_id: str, actor: str) -> dict[str, Any]:
    """Stop the managed image renderer and give the GPU memory back.

    The recorded identity and the named group must both agree; a process this
    installation cannot prove it owns is reported and left alone.
    """
    key = image_startup_key(instance_id)
    with factory() as db:
        row = db.get(AppSetting, key)
        record = dict(row.value) if row is not None and isinstance(row.value, dict) else {}
    if not record:
        return {"status": "NOT_STARTED", "instance_id": instance_id}
    state = managed_process_state(record)
    job_name = (record.get("process") or {}).get("job_name")
    if state == "STOPPED":
        _clear_image_marks(factory, instance_id, job_name)
        return {"status": "STOPPED", "instance_id": instance_id, "outcome": "already_stopped"}
    if state == "GONE":
        _mark_stopped(factory, instance_id, record)
        _clear_image_marks(factory, instance_id, job_name)
        return {"status": "STOPPED", "instance_id": instance_id, "outcome": "recorded_stopped"}
    if state != "RUNNING":
        raise HTTPException(409, {
            "code": "IMAGE_RENDERER_OWNERSHIP_UNVERIFIED",
            "detail": "记录里的进程身份无法证明属于本工作台，因此没有停止它。",
            "state": state,
        })
    evidence = terminate_named_group(job_name, timeout=20)
    if evidence.get("active_processes") != 0:
        raise HTTPException(409, {
            "code": "IMAGE_RENDERER_STOP_UNCONFIRMED",
            "detail": "受管进程组里仍有成员，没有停止。",
        })
    _mark_stopped(factory, instance_id, record)
    return {"status": "STOPPED", "instance_id": instance_id, "outcome": "stopped", "evidence": evidence}


def _without_image_marks(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop the image-only marks from the shared instance row.

    The image runtime and the H3 runtime share one adopted instance.  While it
    runs, the image renderer marks the row; those marks are not part of the
    installation evidence.  Leaving them behind would make the H3 activation
    compare the row against the installation attempt and refuse forever, with
    a blocker that names neither cause.
    """
    if payload.get("purpose") != "image":
        return payload
    cleaned = {key: value for key, value in payload.items() if key not in {"purpose", "url"}}
    return cleaned


def _mark_stopped(factory, instance_id: str, record: dict[str, Any], actor: str = "worker") -> None:
    job_name = (record.get("process") or {}).get("job_name")
    with factory.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        # Only the status changes.  The shared row's payload belongs to the
        # installation evidence and the active runtime identity: the image
        # runtime keeps its own process in its startup record, so the row keeps
        # the digest the installer recorded.
        row = db.get(ComfyInstance, instance_id)
        if row is not None:
            row.status = "STOPPED"
        row = db.get(AppSetting, image_startup_key(instance_id))
        if row is not None:
            value = dict(row.value) if isinstance(row.value, dict) else {}
            value.update({"stage": "STOPPED", "stopped_at": utcnow().isoformat(),
                          "stopped_reason": "operator_release"})
            row.value = value
        add_audit(db, actor, "IMAGE_RENDERER_STOPPED", "comfy_instance", instance_id,
                  {"job_name": job_name})
