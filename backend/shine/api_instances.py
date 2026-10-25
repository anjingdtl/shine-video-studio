"""Human-controlled instance plans; default executors remain explicitly Fake."""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field

from shine.api_common import DBSession, app_settings
from shine.api_operator import Operator
from shine.comfyops.installer import PackageSpec
from shine.comfyops.instances import (
    FakeCommandRunner,
    InstanceOperation,
    execute_operation,
    health,
    plan_operation,
)
from shine.comfyops.models import ComfyInstance, InstallationAttempt, ModelPackage, NodePackage
from shine.config import Settings
from shine.services import add_audit

router = APIRouter(prefix="/api/v1/comfyops", tags=["controlled-instance-lifecycle"])
SettingsDep = Annotated[Settings, Depends(app_settings)]


def fake_http():
    return httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"queue_running": [], "queue_pending": [], "is_mock": True}
                if request.url.path == "/queue"
                else {"system": {"os": "FAKE"}, "is_mock": True},
            )
        )
    )


@router.post("/instance-plans")
def instance_plan(body: InstanceOperation, actor: Operator, settings: SettingsDep, db: DBSession):
    row = plan_operation(db, settings, body)
    db.commit()
    return jsonable_encoder({c.name: getattr(row, c.name) for c in row.__table__.columns})


class ExecuteInstance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_hash: str


@router.post("/instance-plans/{identity}/execute")
def instance_execute(identity: str, body: ExecuteInstance, actor: Operator, request: Request, settings: SettingsDep):
    from shine.comfyops.models import InstallationPlan

    factory = request.app.state.session_factory
    with factory() as db:
        plan = db.get(InstallationPlan, identity)
        if not plan:
            raise HTTPException(404, "PLAN_NOT_FOUND")
        mode = plan.payload.get("operation", {}).get("mode")
    if mode == "fake":
        if not hasattr(request.app.state, "instance_runner"):
            request.app.state.instance_runner = FakeCommandRunner()
        runner = request.app.state.instance_runner
        http = fake_http()
    else:
        runner, http = None, None
    try:
        attempt = execute_operation(factory, settings, identity, body.plan_hash, actor, runner=runner, http=http)
    except (ValueError, OSError, httpx.HTTPError) as exc:
        raise HTTPException(409, str(exc)) from exc
    with factory() as db:
        row = db.get(InstallationAttempt, attempt)
        return jsonable_encoder({c.name: getattr(row, c.name) for c in row.__table__.columns})


@router.get("/instances/{identity}/health")
def instance_health(identity: str, actor: Operator, db: DBSession):
    row = db.get(ComfyInstance, identity)
    if not row or "port" not in row.payload:
        raise HTTPException(409, "REGISTERED_MANAGED_INSTANCE_REQUIRED")
    try:
        result = health(row.payload["port"], fake_http() if row.payload.get("is_mock") else httpx.Client(timeout=10))
    except httpx.HTTPError as exc:
        return {"status": "OFFLINE", "is_mock": row.payload.get("is_mock"), "error": type(exc).__name__}
    if not row.payload.get("is_mock") and row.payload.get("managed"):
        row.status = "ONLINE"
        db.commit()
    return {"status": "ONLINE", "is_mock": row.payload.get("is_mock"), "evidence": result}


@router.post("/packages/{identity}/offline-files")
async def offline_package_file(
    identity: str,
    actor: Operator,
    settings: SettingsDep,
    db: DBSession,
    relative_path: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
):
    row = db.get(ModelPackage, identity) or db.get(NodePackage, identity)
    if not row:
        raise HTTPException(404, "PACKAGE_MISSING")
    spec = PackageSpec.model_validate(row.payload)
    if spec.license_id.lower() in {"unknown", "unverified"}:
        raise HTTPException(409, "LICENSE_NOT_APPROVED")
    entry = next((f for f in spec.files if f.relative_path == relative_path), None)
    if not entry:
        raise HTTPException(409, "FILE_NOT_IN_PINNED_MANIFEST")
    cache = settings.data_dir / "comfyops" / "isolated-packages" / ".cache"
    cache.mkdir(parents=True, exist_ok=True)
    if entry.size > shutil.disk_usage(cache).free:
        raise HTTPException(409, "INSUFFICIENT_DISK")
    temporary = cache / (uuid4().hex + ".upload")
    digest, size = hashlib.sha256(), 0
    try:
        with temporary.open("xb") as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > entry.size:
                    raise HTTPException(413, "FILE_SIZE_EXCEEDED")
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if size != entry.size or digest.hexdigest() != entry.sha256:
            raise HTTPException(409, "FILE_CHECKSUM_FAILED")
        os.replace(temporary, cache / entry.sha256)
    finally:
        temporary.unlink(missing_ok=True)
    add_audit(
        db,
        actor,
        "OFFLINE_PACKAGE_FILE_CACHED",
        "package",
        identity,
        {"sha256": entry.sha256, "relative_path": relative_path, "license": spec.license_id},
    )
    db.commit()
    return {
        "status": "CACHED_NOT_INSTALLED",
        "sha256": entry.sha256,
        "bytes": size,
        "source": "USER_OFFLINE_IMPORT",
        "risk": "EXECUTABLE_CODE" if spec.kind == "node" else "MODEL_LICENSE",
    }


@router.get("/execution-context")
def execution_context(actor: Operator, settings: SettingsDep, db: DBSession):
    import os

    from shine.comfyops.scope import current_scope

    return {
        **current_scope(db, settings),
        "administrator_roots": settings.comfy_probe_roots,
        "administrator_ports": settings.comfy_probe_ports,
        "local_execution_enabled": os.name == "nt" and os.getenv("SHINE_ENABLE_LOCAL_EXECUTION") == "1",
        "providers": {
            "image": "ComfyImageProvider"
            if os.getenv("SHINE_IMAGE_COMFY_URL")
            else "LocalImageHTTPProvider"
            if settings.local_image_url
            else "FakeImageProvider",
            "voice": ("QwenRoleProcessSDK" if os.getenv("SHINE_VOICE_ROLE_RUNTIMES") else "QwenLocalVoiceSDK")
            if os.getenv("SHINE_VOICE_SDK_PACKAGES")
            else "LocalVoiceHTTPProvider"
            if settings.local_voice_url
            else "FakeVoiceProvider",
        },
    }


class ScopeSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str
    roots: list[str]
    ports: list[int]
    previous_hash: str
    grant_id: str
    snapshot: str


@router.post("/scope")
def select_scope(body: ScopeSelection, actor: Operator, settings: SettingsDep, db: DBSession):
    from sqlalchemy import text

    from shine.comfy.workflow import graph_hash
    from shine.comfyops import approval
    from shine.comfyops.scope import KEY, current_scope
    from shine.models import AppSetting, Project

    db.execute(text("BEGIN IMMEDIATE"))
    current = current_scope(db, settings)
    if current["configuration_hash"] != body.previous_hash:
        raise HTTPException(409, "SCOPE_CONFIGURATION_CHANGED")
    project = db.get(Project, body.project_id)
    if not project:
        raise HTTPException(404, "PROJECT_NOT_FOUND")
    context = body.model_dump(exclude={"grant_id", "snapshot"})
    approval.consume(db, actor, "scope_configure", project, body.grant_id, body.snapshot, context=context)
    roots = [str(Path(r).resolve()) for r in body.roots]
    if any(not any(Path(r).is_relative_to(Path(a).resolve()) for a in settings.comfy_probe_roots) for r in roots):
        raise HTTPException(403, "SCOPE_ESCALATION_FORBIDDEN")
    if not set(body.ports) <= set(settings.comfy_probe_ports):
        raise HTTPException(403, "PORT_SCOPE_ESCALATION_FORBIDDEN")
    row = db.get(AppSetting, KEY)
    value = {"roots": roots, "ports": sorted(set(body.ports))}
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=KEY, value=value))
    add_audit(
        db,
        actor,
        "COMFY_SCOPE_SELECTED",
        "project",
        project.id,
        {"configuration_hash": graph_hash(value)},
        project_id=project.id,
    )
    db.commit()
    return current_scope(db, settings)


@router.get("/instances/{identity}/attempts")
def instance_attempts(identity: str, actor: Operator, db: DBSession):
    from sqlalchemy import select

    return [
        jsonable_encoder({c.name: getattr(r, c.name) for c in r.__table__.columns})
        for r in db.scalars(select(InstallationAttempt))
        if r.payload.get("instance_id") == identity
    ]


class ReconcileInstance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    grant_id: str
    snapshot: str
    evidence: str = Field(min_length=5)


@router.post("/instance-attempts/{identity}/reconcile")
def reconcile_instance(identity: str, body: ReconcileInstance, actor: Operator, settings: SettingsDep, db: DBSession):
    from sqlalchemy import text

    from shine.comfy.workflow import graph_hash
    from shine.comfyops import approval
    from shine.comfyops.installer import process_identity
    from shine.comfyops.instances import tree_manifest
    from shine.comfyops.models import InstallationPlan

    db.execute(text("BEGIN IMMEDIATE"))
    attempt = db.get(InstallationAttempt, identity)
    if not attempt or attempt.status != "SUBMIT_INTENT":
        raise HTTPException(409, "INTERRUPTED_INSTANCE_ATTEMPT_REQUIRED")
    plan = db.get(InstallationPlan, attempt.plan_id)
    op = plan.payload["operation"]
    if plan.payload.get("kind") != "INSTANCE_LIFECYCLE":
        raise HTTPException(409, "INSTANCE_ATTEMPT_REQUIRED")
    owner = attempt.payload.get("owner_pid")
    birth = attempt.payload.get("owner_birth")
    if not owner or not birth or process_identity(owner) == birth:
        raise HTTPException(409, "INSTANCE_OWNER_ALIVE_OR_UNVERIFIABLE")
    command = attempt.payload.get("command_owner")
    if command and process_identity(command["pid"]) == command["birth"]:
        raise HTTPException(409, "COMMAND_OWNER_STILL_ACTIVE")
    approval.consume(db, actor, "instance_reconcile", attempt, body.grant_id, body.snapshot)
    root = Path(plan.payload["root"])
    if not root.resolve().is_relative_to((settings.data_dir / "comfyops").resolve()):
        raise HTTPException(409, "INSTANCE_ROOT_OUTSIDE_SCOPE")
    no_change = (
        op["action"] not in {"CREATE", "START", "STOP"}
        and root.exists()
        and graph_hash(tree_manifest(root)) == plan.payload["tree_hash"]
    )
    attempt.status = "ROLLED_BACK" if no_change else "NEEDS_REVIEW"
    instance = db.get(ComfyInstance, plan.payload["instance_id"])
    if no_change:
        instance.status = "MOCK_VERIFIED" if instance.payload.get("is_mock") else "STOPPED"
    else:
        # A separate hash-approved ROLLBACK plan restores the verified pre-mutation
        # backup. Reconciliation itself never deletes files or retries commands.
        instance.status = "NEEDS_REVIEW"
        instance.payload = {
            **instance.payload,
            "last_attempt": attempt.id,
            **({"process": attempt.payload["started_process"]} if attempt.payload.get("started_process") else {}),
        }
        instance.sha256 = graph_hash(instance.payload)
    plan.status = attempt.status
    add_audit(
        db,
        actor,
        "INSTANCE_INTERRUPTION_RECONCILED",
        "installation_attempt",
        identity,
        {"outcome": attempt.status, "evidence": body.evidence},
        project_id=plan.project_id,
    )
    db.commit()
    return jsonable_encoder({c.name: getattr(attempt, c.name) for c in attempt.__table__.columns})


class ExternalBackup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str
    instance_id: str
    root: str
    mode: str = Field(pattern="^(fake|local)$")
    risk_confirmed: bool


@router.post("/backup-plans")
def external_backup_plan(body: ExternalBackup, actor: Operator, settings: SettingsDep, db: DBSession):
    from shine.comfyops.external_backup import plan_backup

    try:
        row = plan_backup(db, settings, **body.model_dump())
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    return jsonable_encoder({c.name: getattr(row, c.name) for c in row.__table__.columns})


@router.post("/backup-plans/{identity}/execute")
def external_backup_execute(
    identity: str, body: ExecuteInstance, actor: Operator, request: Request, settings: SettingsDep
):
    from shine.comfyops.external_backup import execute_backup
    from shine.comfyops.models import InstallationPlan

    factory = request.app.state.session_factory
    with factory() as db:
        plan = db.get(InstallationPlan, identity)
        if not plan:
            raise HTTPException(404, "PLAN_NOT_FOUND")
        mock = plan.payload.get("is_mock", False)
    if not mock and (os.name != "nt" or os.getenv("SHINE_ENABLE_LOCAL_EXECUTION") != "1"):
        raise HTTPException(409, "LOCAL_EXECUTION_NOT_ENABLED")
    try:
        aid = execute_backup(
            factory,
            settings,
            identity,
            body.plan_hash,
            actor,
            fake_http() if mock else httpx.Client(timeout=10, follow_redirects=False),
        )
    except (ValueError, OSError, httpx.HTTPError) as exc:
        raise HTTPException(409, str(exc)) from exc
    with factory() as db:
        row = db.get(InstallationAttempt, aid)
        return jsonable_encoder({c.name: getattr(row, c.name) for c in row.__table__.columns})
