"""One authorized preparation, advanced by the resident Worker.

The setup page used to drive the whole chain from the browser: download, then
profiles, then recipe, then service validation, then activation.  Closing the
tab stopped it half-way, and the half-way state - a saved image binding with no
recipe, a verified weight with no instance binding - is exactly what produced
``IMAGE_RENDERER_RECIPE_MISSING`` on machines that were, in fact, ready.

This module makes that order durable.  A single task row lives in
``AppSetting``; the Worker advances exactly one step per tick, so:

* the browser is a *view*, never the executor;
* a closed tab, a restarted Worker or a reboot resumes at the first unfinished
  step instead of starting over;
* a step that cannot finish says so by name and stops, instead of silently
  leaving the next phase looking green.

It creates no second installer and no second scheduler: downloads stay with the
existing installation attempts, process control stays with
``runtime_orchestrator``, and every step calls the same function the API does.

No step runs inside an open write transaction: each handler opens the short
sessions it needs, so a slow recipe write or a renderer start never holds the
database lock that the rest of the workbench needs.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from shine.models import AppSetting, utcnow

logger = logging.getLogger(__name__)

TASK_KEY = "setup-preparation-task"
LEASE_SECONDS = 300

STEP_LABELS = {
    "TARGET": "确认本机安装与端口",
    "RECIPE": "生成受管运行配置",
    "IMAGE_BIND": "对齐出图运行时",
    "PROFILES": "登记生成方案",
    "VALIDATE": "核对运行合同",
    "IMAGE_VERIFY": "核对出图节点",
}


def _steps_for(capabilities: list[str], *, verify_runtime: bool) -> list[str]:
    steps = ["TARGET", "RECIPE"]
    if "image" in capabilities:
        steps.append("IMAGE_BIND")
    if "h3" in capabilities:
        steps.append("PROFILES")
        if verify_runtime:
            steps.append("VALIDATE")
    if "image" in capabilities:
        steps.append("IMAGE_VERIFY")
    return steps


def enqueue(db, *, project_id: str, capabilities: list[str], installation_path: str | None = None,
            port: int | None = None, variant_id: str | None = None,
            verify_runtime: bool = True, actor: str = "local-user") -> dict[str, Any]:
    """Freeze one authorization into a resumable task.

    Enqueuing the same request twice returns the running task instead of
    creating a second one: the physical work is the same, so the identity is.
    """
    from shine.services import add_audit

    capabilities = sorted({row for row in capabilities if row in {"h3", "image", "vlm", "asr"}})
    existing = db.get(AppSetting, TASK_KEY)
    current = dict(existing.value) if existing is not None and isinstance(existing.value, dict) else {}
    if current.get("status") in {"QUEUED", "RUNNING"}:
        if (current.get("project_id") == project_id
                and sorted(current.get("request", {}).get("capabilities") or []) == capabilities):
            return current
        # A different request supersedes the old one: the previous task is
        # cancelled by name so nothing later claims to be "in progress".
        current["status"] = "SUPERSEDED"
        current["message"] = "已被新的准备请求取代"
    steps = _steps_for(capabilities, verify_runtime=verify_runtime)
    value = {
        "version": 1,
        "task_id": "setup-" + uuid4().hex[:12],
        "project_id": project_id,
        "status": "QUEUED",
        "created_at": utcnow().isoformat(),
        "updated_at": utcnow().isoformat(),
        "lease_owner": None,
        "lease_expires_at": None,
        "current": steps[0] if steps else None,
        "message": "等待后台开始",
        "error": None,
        "request": {
            "capabilities": capabilities,
            "installation_path": installation_path,
            "port": port,
            "variant_id": variant_id,
            "verify_runtime": verify_runtime,
        },
        "target": {},
        "steps": [{"id": step, "label": STEP_LABELS[step], "status": "PENDING", "detail": None}
                  for step in steps],
        "result": {},
    }
    if existing is None:
        db.add(AppSetting(key=TASK_KEY, value=value))
    else:
        existing.value = value
    add_audit(db, actor, "SETUP_PREPARATION_ENQUEUED", "setup_task", value["task_id"], {
        "capabilities": capabilities, "steps": steps,
        "installation_path": installation_path, "port": port,
    }, project_id=project_id)
    db.commit()
    return value


def cancel(db, *, actor: str = "local-user") -> dict[str, Any]:
    from shine.services import add_audit

    row = db.get(AppSetting, TASK_KEY)
    if row is None or not isinstance(row.value, dict):
        return {"status": "IDLE"}
    value = dict(row.value)
    if value.get("status") in {"QUEUED", "RUNNING"}:
        value["status"] = "CANCELLED"
        value["message"] = "已取消；已经完成的步骤会保留"
        value["updated_at"] = utcnow().isoformat()
        value["lease_owner"] = None
        value["lease_expires_at"] = None
        row.value = value
        add_audit(db, actor, "SETUP_PREPARATION_CANCELLED", "setup_task", value.get("task_id"), {})
        db.commit()
    return value


def read_task(db) -> dict[str, Any]:
    row = db.get(AppSetting, TASK_KEY)
    return dict(row.value) if row is not None and isinstance(row.value, dict) else {}


# --- worker side ------------------------------------------------------------


def _live_installation_work(db, project_id: str) -> list[str]:
    """Downloads that still own the target; convergence must wait for them."""
    from shine.comfyops.models import InstallationAttempt, InstallationPlan

    rows = db.scalars(select(InstallationAttempt).where(
        InstallationAttempt.status.in_(["QUEUED", "SUBMIT_INTENT", "INSTALLING",
                                        "FAILED_RECONCILE_REQUIRED"]))).all()
    live = []
    for attempt in rows:
        plan = db.get(InstallationPlan, attempt.plan_id)
        if plan is None:
            continue
        if (plan.payload or {}).get("project_id") not in (None, project_id):
            continue
        live.append(attempt.id)
    return live


def _claim(factory, worker_id: str) -> dict[str, Any] | None:
    try:
        with factory.begin() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            row = db.get(AppSetting, TASK_KEY)
            if row is None or not isinstance(row.value, dict):
                return None
            value = dict(row.value)
            if value.get("status") not in {"QUEUED", "RUNNING"}:
                return None
            owner = value.get("lease_owner")
            expires = value.get("lease_expires_at")
            if owner and owner != worker_id and isinstance(expires, str):
                try:
                    if datetime.fromisoformat(expires) > utcnow():
                        return None
                except ValueError:
                    pass
            value["status"] = "RUNNING"
            value["lease_owner"] = worker_id
            value["lease_expires_at"] = (utcnow() + timedelta(seconds=LEASE_SECONDS)).isoformat()
            value["updated_at"] = utcnow().isoformat()
            row.value = value
            return value
    except OperationalError:
        return None


def _save(factory, value: dict[str, Any]) -> None:
    with factory.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        row = db.get(AppSetting, TASK_KEY)
        if row is None:
            return
        current = dict(row.value) if isinstance(row.value, dict) else {}
        if current.get("task_id") != value.get("task_id"):
            return  # a newer task superseded this one; never resurrect it
        value["lease_owner"] = None
        value["lease_expires_at"] = None
        value["updated_at"] = utcnow().isoformat()
        row.value = value


def advance(factory, settings, worker_id: str) -> str | None:
    """Advance the authorized preparation by exactly one step."""
    task = _claim(factory, worker_id)
    if task is None:
        return None
    steps = task.get("steps") or []
    pending = next((row for row in steps if row.get("status") == "PENDING"), None)
    if pending is None:
        task["status"] = ("DONE" if all(row.get("status") in {"DONE", "SKIPPED"} for row in steps)
                          else task.get("status"))
        task["message"] = "制作环境准备完成"
        task["current"] = None
        _save(factory, task)
        return "setup:done"
    step_id = str(pending["id"])
    task["current"] = step_id
    try:
        if step_id != "TARGET":
            with factory() as db:
                live = _live_installation_work(db, str(task.get("project_id") or ""))
            if live:
                # The bytes are still arriving; convergence must not judge them yet.
                task["message"] = "正在等待下载完成（关闭页面也会继续）"
                _save(factory, task)
                return "setup:waiting"
        detail = _run_step(factory, settings, task, step_id)
        for row in steps:
            if row["id"] == step_id:
                row["status"] = detail.get("status", "DONE")
                row["detail"] = detail.get("detail")
                row["code"] = detail.get("code")
                break
        task["message"] = detail.get("detail") or ""
        if detail.get("status") == "FAILED":
            task["status"] = "FAILED"
            task["error"] = detail.get("code")
        elif all(row.get("status") in {"DONE", "SKIPPED", "FAILED"} for row in steps):
            failures = [row for row in steps if row.get("status") == "FAILED"]
            task["status"] = "DONE_WITH_WARNINGS" if failures else "DONE"
            if failures:
                task["error"] = failures[0].get("code")
            task["current"] = None
        _save(factory, task)
        return f"setup:{step_id}"
    except Exception as exc:  # noqa: BLE001 - a failed step is evidence, not a crash
        detail_code = _code_of(exc)
        for row in steps:
            if row["id"] == step_id:
                row["status"] = "FAILED"
                row["code"] = detail_code
                row["detail"] = _message_of(exc)
                break
        task["status"] = "FAILED"
        task["error"] = detail_code
        task["message"] = _message_of(exc)
        _save(factory, task)
        logger.warning("setup_step_failed step=%s code=%s", step_id, detail_code)
        return f"setup:{step_id}:failed"


def _code_of(exc: Exception) -> str:
    detail = getattr(exc, "detail", None)
    if isinstance(detail, str):
        return detail[:120]
    if isinstance(detail, dict):
        return str(detail.get("code") or "")[:120] or type(exc).__name__
    return type(exc).__name__


def _message_of(exc: Exception) -> str:
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict) and detail.get("message"):
        return str(detail["message"])[:300]
    if isinstance(detail, str):
        return detail[:300]
    return str(exc)[:300]


def _run_step(factory, settings, task: dict[str, Any], step_id: str) -> dict[str, Any]:
    handler = {
        "TARGET": _step_target,
        "RECIPE": _step_recipe,
        "IMAGE_BIND": _step_image_bind,
        "PROFILES": _step_profiles,
        "VALIDATE": _step_validate,
        "IMAGE_VERIFY": _step_image_verify,
    }[step_id]
    return handler(factory, settings, task)


def _step_target(factory, settings, task) -> dict[str, Any]:
    """Resolve the physical installation once and freeze it for every step."""
    from shine.comfyops.models import ComfyInstance
    from shine.comfyops.setup_summary import resolve_installation

    request = task.get("request") or {}
    with factory() as db:
        resolved = resolve_installation(db, settings, path=request.get("installation_path"))
        selected = resolved.get("selected") or {}
        instance_id = selected.get("instance_id")
        if not instance_id:
            return {"status": "FAILED", "code": "MANAGED_INSTANCE_REQUIRED",
                    "detail": "还没有可以使用的 ComfyUI 实例；先完成环境准备（创建或确认安装）。"}
        instance = db.get(ComfyInstance, instance_id)
        if instance is None:
            return {"status": "FAILED", "code": "COMFY_INSTANCE_NOT_FOUND",
                    "detail": "登记里的这套安装已经不在了，请重新检测。"}
        payload = instance.payload if isinstance(instance.payload, dict) else {}
        port = request.get("port") or payload.get("port")
        task["target"] = {
            "instance_id": instance_id,
            "name": payload.get("name") or "shine-h3",
            "root": payload.get("root"),
            "port": int(port) if port else None,
            "adopted": bool(payload.get("adopted")),
        }
    return {"status": "DONE",
            "detail": f"使用 {task['target']['root']}（端口 {task['target']['port']}）"}


def _step_recipe(factory, settings, task) -> dict[str, Any]:
    """Write the recipe for exactly the capabilities that were authorized."""
    from shine.comfyops import deployment

    target = task.get("target") or {}
    request = task.get("request") or {}
    capabilities = request.get("capabilities") or []
    if not target.get("instance_id"):
        return {"status": "FAILED", "code": "TARGET_NOT_RESOLVED", "detail": "还没有确认本机安装。"}
    results: dict[str, dict] = {}
    with factory() as db:
        for capability in capabilities:
            if capability not in {"h3", "image"}:
                continue
            try:
                result = deployment.generate_configuration(
                    db, settings, str(task.get("project_id")),
                    instance_name=target.get("name") or "shine-h3",
                    port=int(target.get("port") or 8188),
                    variant_id=request.get("variant_id"),
                    capability=capability,
                )
                results[capability] = {"recipe": result.get("recipe"), "sha256": result.get("recipe_sha256")}
            except Exception as exc:  # noqa: BLE001 - the refusal is the answer
                db.rollback()
                results[capability] = {"error": _code_of(exc)}
                if capability == "h3" and "image" in capabilities:
                    continue
                if capability == "h3" and results.get("image", {}).get("recipe"):
                    # The image recipe already exists; an H3 refusal is named
                    # but must not undo it.
                    continue
    if not results:
        return {"status": "SKIPPED", "detail": "这次没有需要生成运行配置的能力。"}
    detail = "；".join(f"{key}:{'已生成' if not row.get('error') else row['error']}"
                      for key, row in results.items())
    if all(row.get("error") for row in results.values()):
        return {"status": "FAILED", "code": "RECIPE_NOT_GENERATED", "detail": detail}
    return {"status": "DONE", "detail": detail}


def _step_image_bind(factory, settings, task) -> dict[str, Any]:
    """Point the image runtime at the recipe this task just wrote."""
    from shine.comfyops.image_renderer import image_recipe_state
    from shine.materialprep import runtime

    target = task.get("target") or {}
    recipe = image_recipe_state(settings)
    if recipe["state"] != "READY":
        return {"status": "FAILED", "code": "IMAGE_RENDERER_RECIPE_MISSING",
                "detail": "运行配置还没有生成，出图绑定暂时不能对齐。"}
    if recipe.get("instance_id") != target.get("instance_id"):
        return {"status": "FAILED", "code": "RUNTIME_INSTANCE_MISMATCH",
                "detail": "运行配置指向另一套安装；不会把出图绑定改到它上面。"}
    port = int(recipe.get("port") or target.get("port") or 0)
    root = Path(str(target.get("root") or ""))
    if not root.is_dir():
        return {"status": "FAILED", "code": "INSTANCE_ROOT_MISSING", "detail": "安装目录读不到。"}
    current = runtime.load_config(settings)
    base_url = f"http://127.0.0.1:{port}"
    model_root = str(root / "models")
    if (current.instance_id == target["instance_id"] and current.base_url == base_url
            and current.model_root == model_root and current.recipe_sha256 == recipe.get("recipe_sha256")):
        return {"status": "DONE", "detail": "出图运行时已经指向这套安装，无需改动。"}
    config = runtime.ImageRuntimeConfig(
        config_revision=current.config_revision + 1,
        instance_id=str(target["instance_id"]),
        base_url=base_url,
        installation_id=str(target["instance_id"]),
        port=port or None,
        recipe_sha256=recipe.get("recipe_sha256") or "",
        model_root=model_root,
        graph_sha256=current.graph_sha256,
        parameter_map=current.parameter_map,
        model_files=current.model_files or [dict(entry) for entry in runtime.qwen_workflow.MODEL_FILES],
        verified_files=current.verified_files,
        bound_at=utcnow().isoformat(),
    )
    runtime.save_config(settings, config)
    return {"status": "DONE", "detail": f"出图运行时已对齐到端口 {port}"}


def _step_profiles(factory, settings, task) -> dict[str, Any]:
    from shine.api_setup import _register_project_profiles

    request = task.get("request") or {}
    with factory() as db:
        registered = _register_project_profiles(db, str(task.get("project_id")),
                                                variant_id=request.get("variant_id"))
    names = [row["name"] for row in registered]
    return {"status": "DONE", "detail": "生成方案：" + ("、".join(names) if names else "已是最新")}


def _step_validate(factory, settings, task) -> dict[str, Any]:
    from shine.comfyops.runtime_configuration import activate_runtime, runtime_report
    from shine.comfyops.service_validation import validate_services

    with factory() as db:
        report = runtime_report(db, settings)
    if report.get("status") == "ACTIVE" and report.get("services_verified"):
        return {"status": "DONE", "detail": "本机运行合同此前已核验并激活，无需重复启动。"}
    candidate = report.get("candidate") or {}
    recipe_sha256 = candidate.get("recipe_sha256")
    if not recipe_sha256:
        return {"status": "FAILED", "code": "GENERATE_RUNTIME_CONFIGURATION_FIRST",
                "detail": "还没有生成这部作品的运行配置。"}
    with factory() as db:
        validated = validate_services(db, settings, str(task.get("project_id")), recipe_sha256,
                                      actor="worker:setup")
    if validated.get("status") != "READY":
        return {"status": "FAILED", "code": str(validated.get("status") or "SERVICE_VALIDATION_FAILED"),
                "detail": "服务核验没有通过：" + str(validated.get("status"))}
    with factory() as db:
        activate_runtime(db, settings, str(task.get("project_id")), recipe_sha256, "worker:setup")
    return {"status": "DONE", "detail": "服务核验通过，本机配置已激活。"}


def _step_image_verify(factory, settings, task) -> dict[str, Any]:
    """Start the managed image renderer once, prove the graph, release the GPU.

    A machine without a usable GPU cannot prove this step; the failure is named
    and kept, never converted into "ready".  A renderer that cannot be stopped
    keeps its lease for reconciliation - this step never claims it stopped.
    """
    from shine.comfyops.image_renderer import (
        image_renderer_state,
        start_image_renderer,
        stop_image_renderer,
    )

    target = task.get("target") or {}
    instance_id = str(target.get("instance_id") or "")
    if not instance_id:
        return {"status": "SKIPPED", "detail": "没有可用的实例。"}
    with factory() as db:
        state = image_renderer_state(db, settings, instance_id)
    if state.get("state") == "RUNNING":
        return {"status": "DONE", "detail": "出图渲染器已在运行（受管且身份可证明）。"}
    try:
        started = start_image_renderer(factory, settings, instance_id=instance_id, actor="worker:setup")
    except Exception as exc:  # noqa: BLE001 - the named code is the answer
        return {"status": "FAILED", "code": _code_of(exc),
                "detail": "出图渲染器没能启动：" + _message_of(exc)}
    from shine.runtime_orchestrator.contracts import IMAGE_STARTUP_PREFIX

    with factory() as db:
        record = db.get(AppSetting, IMAGE_STARTUP_PREFIX + instance_id)
        graph_verified = bool((record.value or {}).get("graph_verified")) if record is not None else None
    try:
        stop_image_renderer(factory, settings, instance_id=instance_id, actor="worker:setup")
    except Exception:  # noqa: BLE001 - an unstopped renderer keeps its lease for reconciliation
        return {"status": "FAILED", "code": "IMAGE_RENDERER_STOP_UNCONFIRMED",
                "detail": "出图节点已核验，但渲染器没有确认停止；GPU 会在下次释放时处理。"}
    if graph_verified is False:
        return {"status": "FAILED", "code": "IMAGE_GRAPH_NOT_VERIFIED",
                "detail": "本机缺少出图工作流需要的节点或输入。"}
    return {"status": "DONE",
            "detail": f"出图节点已核对（端口 {started.get('port')}），渲染器已释放显存。"}
