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
from shine.runtime_orchestrator.contracts import IMAGE_STARTUP_PREFIX
from shine.services import add_audit

__all__ = ["IMAGE_STARTUP_PREFIX"]

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


def _owner_alive(record: dict[str, Any]) -> bool:
    """Whether the process that wrote this startup record still exists.

    An interrupted start leaves an INTENT/LAUNCHED record behind.  If its
    owner process is gone the record is stale history; if the owner is alive
    another start is genuinely in progress and must not be doubled.
    An unreadable identity counts as alive: uncertainty refuses, never races.
    """
    owner_pid = record.get("owner_pid")
    owner_birth = record.get("owner_birth")
    if not isinstance(owner_pid, int) or not isinstance(owner_birth, str):
        return False
    try:
        return process_identity(owner_pid) == owner_birth
    except (OSError, ValueError):
        return True


def _refuse_installation_conflicts(db, instance_id: str, *, held_lease_id: str | None = None) -> None:
    """One physical installation, one managed renderer, one controller.

    The image runtime and H3 share the adopted instance row and the recipe
    port.  A start that ignores a live H3 run, an in-flight image job or an
    active GPU lease is exactly how two renderers used to end up fighting
    over one port (audit R3/R4).
    """
    from shine.runtime_orchestrator.identity import (
        h3_records_for_installation,
        image_record_for_installation,
        lease_facts,
    )

    for record in h3_records_for_installation(db, instance_id):
        if record["state"] in ("ACTIVE", "STARTING"):
            raise HTTPException(409, {
                "code": "IMAGE_RENDERER_INSTALLATION_BUSY_H3",
                "detail": "H3 受管渲染器正在此安装上运行；按现有租约串行，等待它完成。",
                "run_id": record["run_id"],
                "job_id": record.get("job_id"),
            })
        if record["state"] == "UNCERTAIN":
            raise HTTPException(409, {
                "code": "IMAGE_RENDERER_INSTALLATION_UNCERTAIN",
                "detail": "此安装上有结果未核对的 H3 启动记录；先对账再启动。",
                "run_id": record["run_id"],
            })
    image = image_record_for_installation(db, instance_id)
    if image and image["state"] in ("STARTING", "UNCERTAIN", "ACTIVE"):
        # A live STARTING intent means another executor is mid-launch right
        # now; doubling it is the duplicate-process bug this guard exists for.
        if image["state"] == "STARTING" and image.get("stage") in ("INTENT", "LAUNCHED", "LISTENING"):
            record_row = image
            raw = db.get(AppSetting, record_row["key"])
            raw_value = raw.value if raw is not None and isinstance(raw.value, dict) else {}
            if _owner_alive(raw_value):
                raise HTTPException(409, {
                    "code": "IMAGE_RENDERER_START_IN_PROGRESS",
                    "detail": "另一个启动者正在启动此渲染器；本次不再重复启动。",
                })
        elif image["state"] == "UNCERTAIN" or image["live_state"] == "UNVERIFIED":
            raise HTTPException(409, {
                "code": "IMAGE_RENDERER_OWNERSHIP_UNVERIFIED",
                "detail": "既有渲染器记录无法证明归属；先核对或释放，再启动。",
            })
    for lease in lease_facts(db, exclude_lease_id=held_lease_id):
        # A lease the caller itself holds (the Worker renders inside its own
        # slot) is the reason this start is allowed; anyone else's lease means
        # the GPU is mid-model-work and a second renderer is not admitted.
        raise HTTPException(409, {
            "code": "IMAGE_RENDERER_GPU_LEASE_ACTIVE",
            "detail": "GPU 租约被占用；渲染器启动会与在途模型阶段冲突。",
            "lease_id": lease["lease_id"],
            "lease_owner": lease["owner"],
            "lease_status": lease["status"],
        })


def _provably_gone(record: dict[str, Any]) -> bool:
    """Whether every physical fact says the recorded renderer no longer runs.

    Two shapes qualify: the recorded PID/birth is readable and no longer
    matches while its named group is empty and the port is free, or a record
    that reached RUNNING carries no process at all and its port is provably
    free.  An unreadable identity, a busy port or a non-empty owned group is
    never "gone", and a start still in flight (INTENT/LAUNCHED/LISTENING) is
    never "gone" either — that is another executor's live section.
    """
    if not record:
        return False
    if record.get("stage") in ("INTENT", "LAUNCHED", "LISTENING") and _owner_alive(record):
        return False
    if managed_process_state(record) == "GONE":
        return True
    if record.get("stage") != "RUNNING" or record.get("process"):
        return False
    port = record.get("port")
    if not isinstance(port, int) or port <= 0:
        return False
    try:
        return not windows_port_listeners(port)
    except (OSError, ValueError):
        return False


def _normalize_provably_dead_record(factory, instance_id: str, *, actor: str) -> bool:
    """Clear a record whose renderer is proven gone, so a start is not blocked.

    A renderer that died while its record said RUNNING left the shared instance
    row in RUNNING_PENDING_HEALTH; every later start was refused with
    IMAGE_RENDERER_INSTANCE_NOT_STOPPED and nothing could clear it (R8).  The
    only thing cleared here is a *proven* death — uncertainty keeps refusing.
    """
    with factory() as db:
        row = db.get(AppSetting, image_startup_key(instance_id))
        record = dict(row.value) if row is not None and isinstance(row.value, dict) else {}
    if not record or record.get("stage") in ("STOPPED", None):
        return False
    if not _provably_gone(record):
        return False
    _mark_stopped(factory, instance_id, record, actor=actor)
    _clear_image_marks(factory, instance_id, (record.get("process") or {}).get("job_name"))
    return True


def reconcile_dead_image_renderer(factory, instance_id: str) -> bool:
    """Clear only a dead renderer record, using fresh proof under its installation lock."""
    from shine.runtime_orchestrator.guard import renderer_lifecycle_guard

    with renderer_lifecycle_guard(instance_id):
        return _normalize_provably_dead_record(factory, instance_id, actor="controller:reconcile")


def start_image_renderer(factory, settings, *, instance_id: str, actor: str,
                         held_lease_id: str | None = None) -> dict[str, Any]:
    """Start the managed renderer that serves the image runtime.

    The renderer is started closed and is opened for the image queue only after
    the adapter proved its own identity, so a renderer that does not belong to
    this installation never accepts a prompt.

    The conflict checks and the durable intent write happen inside the
    installation lifecycle guard; the process launch itself stays outside it
    (nothing long is ever held under the OS lock), protected by the intent
    record the guard section wrote.
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
    from shine.runtime_orchestrator.guard import renderer_lifecycle_guard

    with renderer_lifecycle_guard(instance_id):
        # A record whose renderer is proven gone is history, not a lock: clear
        # it before the conflict checks so a dead process never blocks the
        # installation forever.  Uncertainty is untouched and still refuses.
        _normalize_provably_dead_record(factory, instance_id, actor="controller:recorded-gone")
        with factory() as db:
            _refuse_installation_conflicts(db, instance_id, held_lease_id=held_lease_id)
        try:
            listeners = windows_port_listeners(port)
        except (OSError, ValueError) as exc:
            raise HTTPException(409, "COMFY_LISTENER_UNVERIFIED") from exc
        if listeners:
            raise HTTPException(409, {"code": "EXTERNAL_INSTANCE_ON_PORT", "port": port})
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
                if (existing.value.get("stage") in ("INTENT", "LAUNCHED", "LISTENING")
                        and _owner_alive(existing.value)):
                    raise HTTPException(409, "IMAGE_RENDERER_START_IN_PROGRESS")
            intent = {
                "stage": "INTENT",
                "instance_id": instance_id,
                "startup_id": str(uuid4()),
                "port": port,
                "owner_pid": os.getpid(),
                "owner_birth": process_identity(os.getpid()),
                "recipe_sha256": recipe_sha256,
                # Pinned with the intent so a later read can tell whether the
                # renderer that is listening still runs the adapter the recipe
                # names, without hashing the installation on every status poll.
                "adapter_sha256": renderer.get("adapter_sha256"),
                "recorded_at": utcnow().isoformat(),
                "actor": actor,
                "purpose": "image",
            }
            if existing is None:
                db.add(AppSetting(key=key, value=intent))
            else:
                existing.value = intent
    group = None
    process = None
    try:
        argv, cwd, env, pins, token, port = build_renderer_launch(renderer)
        group = OwnedProcessGroup.launch(
            argv, cwd=cwd, env=env, pinned_files=pins,
            job_name="Local\\ShineHandoff-" + uuid4().hex,
        )
        birth = process_identity(group.pid)
        process = {"pid": group.pid, "birth": birth, "job_name": group.job_name}
        if not birth or not group.owns(group.pid, birth):
            raise HTTPException(409, "IMAGE_RENDERER_OWNERSHIP_UNVERIFIED")
        _record(factory, key, {**intent, "stage": "LAUNCHED", "process": process})
        listener = _wait_for_owned_listener(group, port)
        base_url = f"http://127.0.0.1:{port}"
        supervisor = RendererSupervisor(group, base_url, token, renderer["adapter_sha256"])
        supervisor.require_closed_restart()
        from shine.comfy.client import ComfyError, ComfyUIClient
        from shine.materialprep.runtime import verify_graph_against_object_info

        try:
            object_info = ComfyUIClient(base_url, max_retries=0, trust_env=False).object_info()
            graph_verified = verify_graph_against_object_info(object_info)["complete"]
        except (ComfyError, ValueError):
            graph_verified = False
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
            row.value = {
                **row.value, "stage": "RUNNING", "process": process, "listener": listener,
                "graph_verified": graph_verified,
            }
            add_audit(db, actor, "IMAGE_RENDERER_STARTED", "comfy_instance", instance_id, {
                "port": port, "pid": process["pid"], "job_name": process["job_name"],
                "recipe_sha256": recipe_sha256,
            })
        return {
            "status": "RUNNING",
            "instance_id": instance_id,
            "base_url": base_url,
            "port": port,
            "process": process,
            "listener": listener,
        }
    except Exception as exc:
        # The exception class alone says nothing about what to fix: a missing
        # model file, a busy port and a refused recipe all arrive here as
        # HTTPException.  The named reason (a stable code, never a message that
        # could carry a path or a secret) is what the panel shows and what a
        # later diagnosis reads.
        detail = getattr(exc, "detail", None)
        reason = (detail if isinstance(detail, str)
                  else (detail or {}).get("code") if isinstance(detail, dict) else None)
        failed = {**intent, "stage": "FAILED", "error": type(exc).__name__,
                  "failed_at": utcnow().isoformat()}
        if isinstance(reason, str) and reason:
            failed["error_code"] = reason
        if group is not None:
            try:
                group.terminate(timeout=15)
            except Exception:  # noqa: BLE001 - preserve the unresolved process proof
                failed.update({
                    "stage": "RECONCILE_REQUIRED",
                    "process": process or {"pid": group.pid, "job_name": group.job_name},
                })
        _record(factory, key, failed)
        raise


def ensure_image_renderer(factory, settings, *, instance_id: str) -> dict[str, Any]:
    """Make sure the image runtime's renderer is up, and that it is ours.

    A renderer that is already running and provably owned is reused; a renderer
    that cannot be proven is never adopted and never restarted blindly.  The
    Worker calls this inside its own GPU lease; that lease is the caller's own
    admission and must not read as someone else's conflict.
    """
    from shine.comfyops.scheduler import CURRENT_RESOURCE
    from shine.runtime_orchestrator.guard import renderer_lifecycle_guard

    with renderer_lifecycle_guard(instance_id), factory() as db:
        row = db.get(AppSetting, image_startup_key(instance_id))
        record = row.value if row is not None and isinstance(row.value, dict) else {}
        if record.get("stage") == "RUNNING" and managed_process_state(record) == "RUNNING":
            return {"status": "RUNNING", "reused": True, "instance_id": instance_id,
                    "base_url": f"http://127.0.0.1:{record.get('port')}"}
    result = start_image_renderer(
        factory, settings, instance_id=instance_id, actor="worker",
        held_lease_id=CURRENT_RESOURCE.get(),
    )
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
    installation cannot prove it owns is reported and left alone.  The whole
    decision and the termination run inside the installation lifecycle guard:
    a stop that races a start on the same physical install is how a "stopped"
    renderer used to come back to life under a newer PID.
    """
    from shine.runtime_orchestrator.guard import renderer_lifecycle_guard

    with renderer_lifecycle_guard(instance_id):
        return _stop_image_renderer_locked(factory, settings, instance_id=instance_id, actor=actor)


def _stop_image_renderer_locked(factory, settings, *, instance_id: str, actor: str) -> dict[str, Any]:
    key = image_startup_key(instance_id)
    with factory() as db:
        row = db.get(AppSetting, key)
        record = dict(row.value) if row is not None and isinstance(row.value, dict) else {}
    if not record:
        return {"status": "NOT_STARTED", "instance_id": instance_id}
    state = managed_process_state(record)
    job_name = (record.get("process") or {}).get("job_name")
    if record.get("stage") in ("INTENT", "LAUNCHED", "LISTENING") and _owner_alive(record):
        raise HTTPException(409, "IMAGE_RENDERER_START_IN_PROGRESS")
    if state == "STOPPED":
        _clear_image_marks(factory, instance_id, job_name)
        # The startup record already proves the renderer is gone; the shared
        # instance row must follow it, or the next start is refused forever
        # with IMAGE_RENDERER_INSTANCE_NOT_STOPPED.
        with factory.begin() as db:
            row = db.get(ComfyInstance, instance_id)
            if row is not None and row.status != "STOPPED":
                row.status = "STOPPED"
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
    _refuse_stop_under_in_flight_work(factory, instance_id)
    evidence = terminate_named_group(job_name, timeout=20)
    if evidence.get("active_processes") != 0:
        raise HTTPException(409, {
            "code": "IMAGE_RENDERER_STOP_UNCONFIRMED",
            "detail": "受管进程组里仍有成员，没有停止。",
        })
    _mark_stopped(factory, instance_id, record)
    return {"status": "STOPPED", "instance_id": instance_id, "outcome": "stopped", "evidence": evidence}


def retire_idle_image_renderer(factory, settings, installation_id: str, *,
                               actor: str = "worker:h3-coordination") -> dict[str, Any] | None:
    """An idle image renderer yields its GPU to an admitted H3 lease (R4).

    Shared by the Worker's H3 startup and by the H3 startup section itself, so
    the "make way" step and the "start H3" decision cannot be separated by a
    competing image start.  Only a *proven running* renderer is stopped; a
    stale or unverifiable record is left for reconciliation, never killed.
    """
    from shine.runtime_orchestrator.identity import image_record_for_installation

    with factory() as db:
        record = image_record_for_installation(db, installation_id)
    if not record or record.get("live_state") != "RUNNING":
        return None
    return stop_image_renderer(factory, settings, instance_id=installation_id, actor=actor)


def _refuse_stop_under_in_flight_work(factory, instance_id: str) -> None:
    """A stop must never kill a renderer that is serving real work (R6).

    In-flight image items render inside ``image:*`` leases; a library render
    task holds a submit intent or an external prompt id.  Stopping under them
    turns live external submissions into unknown outcomes.  An H3 lease does
    NOT block this stop: stopping an idle image renderer while H3 waits is
    exactly the coordination the unified controller is for.
    """
    from shine.runtime_orchestrator.identity import in_flight_image_work, lease_facts

    with factory() as db:
        work = in_flight_image_work(db)
        image_leases = [
            lease for lease in lease_facts(db) if str(lease.get("owner") or "").startswith("image:")
        ]
    if work:
        raise HTTPException(409, {
            "code": "IMAGE_RENDERER_BUSY_TASK_IN_FLIGHT",
            "detail": "有正在执行的出图任务使用此渲染器；等它完成或先取消任务。",
            "work": work[:5],
        })
    if image_leases:
        raise HTTPException(409, {
            "code": "IMAGE_RENDERER_BUSY_LEASE_ACTIVE",
            "detail": "出图租约仍在执行中；渲染器此刻不能停止。",
            "leases": image_leases[:3],
        })


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
