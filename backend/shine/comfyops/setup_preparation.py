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
    "SCAN": "深搜本机已有资源",
    "RECONCILE": "核验并登记已有字节",
    "DOWNLOAD": "下载并安装缺失文件",
    "INSTANCE": "创建/补全本机 ComfyUI 实例",
    "TARGET": "确认本机安装与端口",
    "RECIPE": "生成受管运行配置",
    "IMAGE_BIND": "对齐出图运行时",
    "PROFILES": "登记生成方案",
    "VALIDATE": "核对运行合同",
    "IMAGE_VERIFY": "核对出图节点",
    "IMAGE_SAMPLES": "生成三类真实出图小样",
}


def _steps_for(capabilities: list[str], *, verify_runtime: bool,
               deep_scan: bool = False) -> list[str]:
    steps = []
    if deep_scan:
        steps.append("SCAN")
    steps.extend(["RECONCILE", "DOWNLOAD"])
    # The instance/recipe/profile/validation chain belongs to the H3 and image
    # runtimes. RelayVSR is post-production on the existing ComfyUI core: it
    # only needs its weights reconciled and downloaded, not a managed recipe.
    if "h3" in capabilities or "image" in capabilities:
        steps.extend(["INSTANCE", "TARGET", "RECIPE"])
        if "image" in capabilities:
            steps.append("IMAGE_BIND")
        if "h3" in capabilities:
            steps.append("PROFILES")
            if verify_runtime:
                steps.append("VALIDATE")
        if "image" in capabilities:
            steps.append("IMAGE_VERIFY")
            if verify_runtime:
                steps.append("IMAGE_SAMPLES")
    return steps


def enqueue(db, *, project_id: str | None, capabilities: list[str], installation_path: str | None = None,
            port: int | None = None, variant_id: str | None = None,
            verify_runtime: bool = True, actor: str = "local-user",
            settings=None, deep_scan: bool = False) -> dict[str, Any]:
    """Freeze one authorization into a resumable task.

    Enqueuing the same request twice returns the running task instead of
    creating a second one: the physical work is the same, so the identity is.
    ``project_id`` may be None: the machine can be prepared before any work
    exists; project-scoped steps then skip themselves by name.
    """
    from shine.services import add_audit

    capabilities = sorted({row for row in capabilities if row in {"h3", "image", "vlm", "asr", "relayvsr"}})
    existing = db.get(AppSetting, TASK_KEY)
    current = dict(existing.value) if existing is not None and isinstance(existing.value, dict) else {}
    if current.get("status") in {"QUEUED", "RUNNING"}:
        if (current.get("project_id") == project_id
                and sorted(current.get("request", {}).get("capabilities") or []) == capabilities
                and current.get("request", {}).get("variant_id") == variant_id
                and current.get("request", {}).get("port") == port):
            return current
        # A different request supersedes the old one: the previous task is
        # cancelled by name so nothing later claims to be "in progress".
        current["status"] = "SUPERSEDED"
        current["message"] = "已被新的准备请求取代"
    steps = _steps_for(capabilities, verify_runtime=verify_runtime, deep_scan=deep_scan)
    if settings is not None:
        # The instance ladder only belongs in the chain when the machine has no
        # usable installation yet; an existing (possibly adopted) installation
        # must not be mutated by the preparation task.
        from shine.comfyops.setup_summary import resolve_installation
        selected = (resolve_installation(db, settings, path=installation_path).get("selected") or {})
        if selected.get("instance_id"):
            steps = [step for step in steps if step != "INSTANCE"]
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
            "deep_scan": deep_scan,
        },
        "target": {},
        "downloads": {},
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
    """Downloads that still own the target; convergence must wait for them.

    Only work that is *still running* may hold the chain, and only work that
    the chain actually needs.  Two rules, both about not letting an
    unrelated download freeze the whole machine:

    * An attempt that already failed (``FAILED_RECONCILE_REQUIRED``) will never
      finish by itself: it waits for a person to reconcile it.  Treating it as
      live work made the whole preparation wait forever on a download that had
      already failed - the page showed "waiting for the download" while
      nothing was downloading at all.
    * The review models (画面审片 / 语音核对) are advisory.  They give
      suggestions and never gate a production, so a review download that is
      still running must not hold the H3 chain either.
    """
    from shine.comfyops.deployment import REVIEW_COMPONENTS
    from shine.comfyops.models import InstallationAttempt, InstallationPlan

    review_only = set(REVIEW_COMPONENTS.values())
    rows = db.scalars(select(InstallationAttempt).where(
        InstallationAttempt.status.in_(["QUEUED", "SUBMIT_INTENT", "INSTALLING"]))).all()
    live = []
    for attempt in rows:
        plan = db.get(InstallationPlan, attempt.plan_id)
        if plan is None:
            continue
        payload = plan.payload or {}
        if payload.get("project_id") not in (None, project_id):
            continue
        packages = [str(row.get("id") or "") for row in (payload.get("packages") or [])]
        if packages and set(packages).issubset(review_only):
            # Advisory review models never gate the production chain.
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
        if step_id not in {"TARGET", "SCAN", "RECONCILE", "DOWNLOAD"}:
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
        "SCAN": _step_scan,
        "RECONCILE": _step_reconcile,
        "DOWNLOAD": _step_download,
        "INSTANCE": _step_instance,
        "TARGET": _step_target,
        "RECIPE": _step_recipe,
        "IMAGE_BIND": _step_image_bind,
        "PROFILES": _step_profiles,
        "VALIDATE": _step_validate,
        "IMAGE_VERIFY": _step_image_verify,
        "IMAGE_SAMPLES": _step_image_samples,
    }[step_id]
    return handler(factory, settings, task)


def _step_scan(factory, settings, task) -> dict[str, Any]:
    """Advance the bounded deep scan; the page closing never stops it."""
    from shine.comfyops import resource_discovery

    with factory() as db:
        status = resource_discovery.read_state(db)["scan"].get("status")
        if status not in {"QUEUED", "RUNNING"}:
            resource_discovery.start_scan(db, actor="worker:setup")
    with factory() as db:
        result = resource_discovery.advance_scan(db, budget_s=2.0)
    if result.get("status") in {"QUEUED", "RUNNING"}:
        return {"status": "PENDING",
                "detail": f"正在深搜本机资源（剩余 {result.get('remaining', 0)} 批，关闭页面也会继续）。"}
    return {"status": "DONE", "detail": "本机资源深搜完成，已有文件进入候选台账。"}


def _step_reconcile(factory, settings, task) -> dict[str, Any]:
    """Hash-verify and register the bytes this machine already has.

    Read-only against the person's own installation; adoption is the single
    authorization this preparation already carries.  A capability whose
    reconcile fails is named, but the download step may still fetch its bytes.
    """
    from shine.comfyops.setup_reconcile import reconcile_installation
    from shine.comfyops.setup_summary import resolve_installation

    request = task.get("request") or {}
    capabilities = [row for row in request.get("capabilities") or [] if row in {"h3", "image", "relayvsr"}]
    if not capabilities:
        return {"status": "SKIPPED", "detail": "这次没有需要核验已有字节的能力。"}
    with factory() as db:
        resolved = resolve_installation(db, settings, path=request.get("installation_path"))
        selected = resolved.get("selected") or {}
    root = selected.get("root")
    if not root:
        return {"status": "DONE", "detail": "还没有已发现的安装，跳过核验，交给实例阶梯。"}
    notes: list[str] = []
    failures = 0
    for capability in capabilities:
        with factory() as db:
            db.rollback()  # hashing and adoption own their transaction boundaries
            try:
                result = reconcile_installation(
                    db, settings, path=root, project_id=task.get("project_id"),
                    capability=capability, adopt=True, port=request.get("port"),
                    actor="worker:setup",
                    variant_id=request.get("variant_id") if capability == "h3" else None)
                notes.append(f"{capability}:{result.get('status')}")
            except Exception as exc:  # noqa: BLE001 - the named refusal is evidence
                failures += 1
                notes.append(f"{capability}:{_code_of(exc)}")
    if failures == len(capabilities):
        return {"status": "FAILED", "code": "RECONCILE_FAILED", "detail": "；".join(notes)}
    return {"status": "DONE", "detail": "已有字节核验登记：" + "；".join(notes)}


_DOWNLOAD_ACTIVE = {"QUEUED", "SUBMIT_INTENT", "INSTALLING", "DOWNLOADING", "VERIFYING",
                    "WAITING_FOR_TARGET", "RETRY_WAIT", "VALIDATING_RUNTIME_CONTRACT", "PAUSED"}
_DOWNLOAD_SUCCESS = {"FILES_VERIFIED_PENDING_RUNTIME", "RUNTIME_CONTRACT_READY"}


def _catalog_file_for(capability: str, variant_id: str | None, report: dict) -> str:
    """The catalog file stem the installer loads (not the shine-… identity)."""
    from shine.comfyops.setup_summary import CATALOG_FILE_BY_CAPABILITY

    if capability == "h3" and variant_id:
        from shine.comfyops import variants as variant_manifests
        try:
            manifest = variant_manifests.load_variant_manifest(str(variant_id))
            stem = str(manifest.get("catalog_id") or "")
            if stem:
                return stem
        except (OSError, ValueError, FileNotFoundError):
            pass
    return (report.get("catalog_file")
            or CATALOG_FILE_BY_CAPABILITY.get(capability)
            or "h3-ref2va-windows-x64")


def _step_download(factory, settings, task) -> dict[str, Any]:
    """Fetch exactly the missing pinned files, through the existing installer.

    The plan is created by the same admission endpoint the UI uses, approved
    with this task as evidence, and executed by the resident installation
    worker.  Each tick re-reads the attempt's real stage; a finished download
    is never re-queued and a failed one is named, not silently retried forever.
    """
    from shine.comfyops.models import InstallationAttempt
    from shine.comfyops.setup_summary import capability_report, resolve_installation

    request = task.get("request") or {}
    capabilities = request.get("capabilities") or []
    downloads = task.setdefault("downloads", {})

    pending: list[str] = []
    for capability, info in list(downloads.items()):
        with factory() as db:
            attempt = db.get(InstallationAttempt, info.get("attempt_id"))
        if attempt is None:
            return {"status": "FAILED", "code": "DOWNLOAD_ATTEMPT_MISSING",
                    "detail": f"{capability} 的安装尝试记录不在了，请重新发起整备。"}
        stage = attempt.stage or attempt.status
        if attempt.status in {"FAILED", "FAILED_RECONCILE_REQUIRED"} or stage in {
                "NEEDS_REVIEW", "CANCELLED", "FAILED_RETRYABLE"}:
            return {"status": "FAILED", "code": attempt.error_code or "DOWNLOAD_FAILED",
                    "detail": f"{capability} 下载未完成：{attempt.error_message or stage}。"
                              "已下载的字节会保留，核对后可继续。"}
        if stage in _DOWNLOAD_SUCCESS:
            info["done"] = True
            continue
        if stage in _DOWNLOAD_ACTIVE or attempt.status in _DOWNLOAD_ACTIVE:
            pending.append(capability)
        else:
            return {"status": "FAILED", "code": "DOWNLOAD_STATE_UNKNOWN",
                    "detail": f"{capability} 的安装停在未知状态 {stage}，需要核对。"}
    if pending:
        return {"status": "PENDING",
                "detail": f"正在下载并安装：{'、'.join(pending)}（关闭页面也会继续）。"}

    with factory() as db:
        resolved = resolve_installation(db, settings, path=request.get("installation_path"))
        selected = resolved.get("selected") or {}
    plans_needed: list[tuple[str, list[str], str]] = []
    for capability in capabilities:
        if downloads.get(capability, {}).get("done"):
            continue  # this authorization already fetched and verified the bytes
        with factory() as db:
            report = capability_report(db, settings, capability, installation=selected,
                                       project_id=task.get("project_id"),
                                       variant_id=request.get("variant_id") if capability == "h3" else None)
        missing = report.get("missing_files") or []
        components = sorted({row["component_id"] for row in missing})
        if capability == "h3":
            from shine.comfyops.setup_summary import runtime_closure_gap
            with factory() as db:
                gap = runtime_closure_gap(db, settings, selected)
            components = sorted(set(components) | set(gap["missing_components"]))
        if components:
            catalog_file = _catalog_file_for(capability, request.get("variant_id"), report)
            plans_needed.append((capability, components, catalog_file))
    if not plans_needed:
        return {"status": "DONE", "detail": "固定清单的文件都已在本机，无需下载。"}

    from shine.api_setup import InstallPlanRequest, install_plan
    from shine.comfyops.instances import approve_lifecycle_plan
    from shine.comfyops.local import queue_install_attempt
    from shine.comfyops.models import InstallationPlan
    from shine.comfyops.storage import disk_info
    from shine.services import add_audit

    for capability, components, catalog_file in plans_needed:
        with factory() as db:
            free = disk_info(settings.data_dir).get("free_bytes", 0)
            body = InstallPlanRequest(
                catalog=catalog_file,
                components=components[:14], project_id=task.get("project_id"),
                disk_free=int(free),
                variant_id=request.get("variant_id") if capability == "h3" else None,
                candidate_download_acknowledged=capability == "h3",
            )
            try:
                plan_view = install_plan(body=body, settings=settings, db=db)
            except Exception as exc:  # noqa: BLE001 - admission refusal is the answer
                db.rollback()
                return {"status": "FAILED", "code": _code_of(exc),
                        "detail": f"{capability} 的安装计划被拒绝：{_message_of(exc)}"}
            plan_id = plan_view["plan_id"]
            row = db.get(InstallationPlan, plan_id)
            approve_lifecycle_plan(db, row, actor="worker:setup",
                                   evidence=f"setup-preparation:{task.get('task_id')}")
            add_audit(db, "worker:setup", "SETUP_PREPARATION_DOWNLOAD_APPROVED",
                      "installation_plan", plan_id,
                      {"task_id": task.get("task_id"), "capability": capability},
                      project_id=task.get("project_id"))
            digest = row.sha256
        attempt_id, _reused = queue_install_attempt(
            factory, settings, plan_id, digest, "worker:setup", "LOCAL_ISOLATED")
        downloads[capability] = {"attempt_id": attempt_id, "plan_id": plan_id}
    return {"status": "PENDING",
            "detail": f"已排队下载：{'、'.join(row[0] for row in plans_needed)}（关闭页面也会继续）。"}


def _step_image_samples(factory, settings, task) -> dict[str, Any]:
    """Queue the three real sample families once, then wait for their result.

    The samples are rendered by the ordinary image queue under the same GPU
    lease rules as production; this step only holds the run id and reports the
    outcome.  Without a project there is nothing to attach the samples to, so
    the step skips itself by name instead of inventing evidence.
    """
    from shine.materialprep import service
    from shine.materialprep.models import MaterialRun

    project_id = task.get("project_id")
    if not project_id:
        return {"status": "SKIPPED",
                "detail": "机器级整备：创建作品后会自动补三类真实小样。"}
    result = task.get("result") or {}
    run_id = result.get("image_sample_run_id")
    if not run_id:
        with factory.begin() as db:
            batch = service.create_validation_batch(
                db, str(project_id), families=["character", "scene", "prop"],
                idempotency_key=f"setup-samples-{task.get('task_id')}", rights_id=None)
            run_id = batch.get("run_id")
            result["image_sample_run_id"] = run_id
            task["result"] = result
        return {"status": "PENDING", "detail": f"三类真实小样已排队（批次 {str(run_id)[:8]}…）。"}
    with factory() as db:
        run = db.get(MaterialRun, run_id)
        status = run.status if run is not None else None
    if status in {"COMPLETED", "NEEDS_REVIEW"}:
        return {"status": "DONE", "detail": "三类真实小样已生成，等待人工评审。"}
    if status in {"FAILED", "PARTIAL_FAILED", "CANCELLED"}:
        return {"status": "FAILED", "code": "IMAGE_SAMPLES_FAILED",
                "detail": f"出图小样未完成：{status}。渲染器或节点合同需要在基础配置里核对。"}
    return {"status": "PENDING", "detail": f"出图小样进行中：{status or '排队中'}。"}


def _step_instance(factory, settings, task) -> dict[str, Any]:
    """Drive the instance ladder under the single frozen authorization.

    A machine that already has a usable installation skips the ladder entirely
    (adopted installations are never mutated here). A bare machine gets
    CREATE -> INSTALL_NODE -> BIND_MODELS, one sub-action per tick, each plan
    approved with the preparation task as evidence and executed by the same
    identity-verified executor the API uses. Bytes must be in the verified
    cache before approval, so the 30-minute execute window never burns while
    a download is still running.
    """
    from datetime import datetime as _dt

    from shine.comfyops import deployment, instances
    from shine.comfyops.installer import ControlledDownloader, PackageFile
    from shine.comfyops.models import InstallationPlan
    from shine.comfyops.setup_summary import resolve_installation
    from shine.services import add_audit

    request = task.get("request") or {}
    project_id = str(task.get("project_id"))
    with factory() as db:
        resolved = resolve_installation(db, settings, path=request.get("installation_path"))
        selected = resolved.get("selected") or {}
    if selected.get("instance_id"):
        return {"status": "DONE", "detail": "已有可用安装，无需创建实例。"}
    port = int(request.get("port") or 8188)
    with factory() as db:
        plan, action = deployment.next_instance_plan(
            db, settings, deployment.load_default_catalog(), project_id,
            instance_name="shine-h3", port=port, mode="local")
    if plan is None:
        return {"status": "DONE", "detail": "实例阶梯已完成。"}
    with factory.begin() as db:
        row = db.get(InstallationPlan, plan.id)
        if row.status in {"INSTALLING", "NEEDS_REVIEW"}:
            return {"status": "FAILED", "code": "INSTANCE_REQUIRES_RECONCILIATION",
                    "detail": "实例操作中断待对账；请在制作环境里核对后继续，不会盲目重试。"}
        cache = Path(settings.comfyops_dir) / "isolated-packages" / ".cache"
        missing = [f["sha256"] for p in row.payload["packages"]
                   for f in p["spec"]["files"]
                   if not (cache / f["sha256"]).is_file()
                   or not ControlledDownloader.valid(cache / f["sha256"], PackageFile.model_validate(f))]
        if missing:
            return {"status": "PENDING",
                    "detail": f"等待 {len(missing)} 个文件下载完成（关闭页面也会继续）。"}
        try:
            expired = _dt.fromisoformat(row.payload.get("execute_before", "")) <= utcnow()
        except ValueError:
            expired = True
        if row.status == "AWAITING_APPROVAL" or expired:
            instances.approve_lifecycle_plan(
                db, row, actor="worker:setup",
                evidence=f"setup-preparation:{task.get('task_id')}")
            add_audit(db, "worker:setup", "SETUP_PREPARATION_INSTANCE_APPROVED",
                      "installation_plan", row.id,
                      {"task_id": task.get("task_id"), "action": action},
                      project_id=project_id)
        digest = row.sha256
    instances.execute_operation(factory, settings, plan.id, digest, "worker:setup")
    return {"status": "PENDING", "detail": f"实例操作 {action} 已执行，继续下一步阶梯。"}


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

    if not task.get("project_id"):
        return {"status": "SKIPPED",
                "detail": "机器级整备：创建作品后会幂等绑定标准生成方案。"}
    request = task.get("request") or {}
    with factory() as db:
        registered = _register_project_profiles(db, str(task.get("project_id")),
                                                variant_id=request.get("variant_id"))
    names = [row["name"] for row in registered]
    return {"status": "DONE", "detail": "生成方案：" + ("、".join(names) if names else "已是最新")}


def _step_validate(factory, settings, task) -> dict[str, Any]:
    from shine.comfyops.runtime_configuration import runtime_report

    if not task.get("project_id"):
        return {"status": "SKIPPED",
                "detail": "机器级整备：运行合同核验在作品绑定后执行。"}
    with factory() as db:
        report = runtime_report(db, settings)
    if report.get("status") == "ACTIVE" and report.get("services_verified"):
        return {"status": "DONE", "detail": "本机运行合同此前已核验并激活，无需重复启动。"}
    candidate = report.get("candidate") or {}
    if not candidate.get("recipe_sha256"):
        return {"status": "FAILED", "code": "GENERATE_RUNTIME_CONFIGURATION_FIRST",
                "detail": "还没有生成这部作品的运行配置。"}
    # Service validation needs the Worker offline, but this task runs *inside*
    # the Worker. Delegate to the supervisor: it stops the Worker, validates and
    # activates, records this step, then restarts the Worker, which resumes here.
    delegated = _delegate_supervisor_validation(settings)
    if delegated.get("status") == "VALIDATING":
        return {"status": "PENDING",
                "detail": "已委托后台核验：会短暂停止 Worker、核对真实节点并激活，然后自动恢复 Worker。"}
    if delegated.get("status") == "IN_PROGRESS":
        return {"status": "PENDING", "detail": "后台核验正在进行，等待其完成。"}
    return {"status": "FAILED", "code": delegated.get("code") or "SUPERVISOR_UNAVAILABLE",
            "detail": delegated.get("detail") or "无法联系监督进程执行服务核验。"}


def _delegate_supervisor_validation(settings) -> dict[str, Any]:
    """Ask the resident supervisor to validate while the Worker is stopped."""
    import json as _json

    import httpx

    record = Path(settings.data_dir) / "run" / "supervisor.json"
    try:
        info = _json.loads(record.read_text(encoding="utf-8"))
        port = int(info["port"])
    except (OSError, ValueError, KeyError):
        return {"status": "FAILED", "code": "SUPERVISOR_NOT_RECORDED",
                "detail": "没有监督进程记录；请用启动器启动一次工作台后再整备。"}
    try:
        # Loopback only; never inherit the host proxy for a local call.
        with httpx.Client(trust_env=False, timeout=10) as client:
            response = client.post(
                f"http://127.0.0.1:{port}/validate-services",
                headers={"x-shine-local-action": "1"})
        if response.status_code == 202:
            return {"status": "VALIDATING"}
        if response.status_code == 409:
            body = response.json()
            if body.get("code") == "VALIDATION_IN_PROGRESS":
                return {"status": "IN_PROGRESS"}
            return {"status": "FAILED", "code": body.get("code") or "SUPERVISOR_REFUSED",
                    "detail": str(body.get("detail") or body)}
        return {"status": "FAILED", "code": "SUPERVISOR_REFUSED",
                "detail": f"监督进程返回 {response.status_code}。"}
    except Exception as exc:  # noqa: BLE001 - an unreachable supervisor is named
        return {"status": "FAILED", "code": "SUPERVISOR_UNREACHABLE",
                "detail": f"无法联系监督进程：{type(exc).__name__}。"}


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
