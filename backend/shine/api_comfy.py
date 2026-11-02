from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from shine.api_common import DBSession, app_settings
from shine.comfy.capabilities import profile_metadata
from shine.comfy.client import ComfyError, ComfyUIClient
from shine.comfy.workflow import graph_hash, validate_against_object_info, validate_graph
from shine.config import Settings
from shine.models import WorkflowProfile
from shine.schemas import (
    ComfyConnectionConfig,
    ComfyTestResult,
    WorkflowProfileCreate,
    WorkflowProfilePublic,
)
from shine.security import require_loopback_url
from shine.services import add_audit, require_project

router = APIRouter(prefix="/api/v1", tags=["comfyui"])
SettingsDep = Annotated[Settings, Depends(app_settings)]


def _profile_data(profile: WorkflowProfile, *, include_graph: bool = False) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": profile.id,
        "project_id": profile.project_id,
        "name": profile.name,
        "mode": profile.mode,
        "graph_sha256": profile.graph_sha256,
        "parameter_map": profile.parameter_map,
        "media_transfers": profile.media_transfers,
        "required_nodes": profile.required_nodes,
        "checkpoint_id": profile.checkpoint_id,
        "model_identity": profile_metadata(profile.graph, profile.mode, profile.parameter_map, profile.media_transfers),
        "validated_against": profile.validated_against,
        "created_at": profile.created_at,
    }
    if include_graph:
        data["graph"] = profile.graph
    return data


@router.get("/comfy/config")
def comfy_config(settings: SettingsDep, db: DBSession) -> dict[str, Any]:
    from shine.comfyops.runtime_configuration import resolve_runtime
    settings = resolve_runtime(db, settings)
    profiles = db.scalars(select(WorkflowProfile).order_by(WorkflowProfile.created_at)).all()
    return {
        "base_url": settings.comfyui_base_url,
        "render_mode": settings.render_provider,
        "workflow_profile_count": len(profiles),
        "h3_checkpoint_family": "MiniMax-H3",
        "real_gpu_validation": "pending_local_h3",
    }


@router.post("/comfy/test", response_model=ComfyTestResult)
def test_comfy(body: ComfyConnectionConfig, settings: SettingsDep, db: DBSession) -> ComfyTestResult:
    base_url = require_loopback_url(body.base_url, allow_remote=settings.allow_remote_comfyui)
    client = ComfyUIClient(base_url, timeout_s=body.timeout_s)
    try:
        stats = client.system_stats()
        object_info = client.object_info()
    except ComfyError as exc:
        return ComfyTestResult(ok=False, message=str(exc))
    h3_nodes = sorted(name for name in object_info if "h3" in name.lower() or "minimax" in name.lower())
    add_audit(
        db,
        "user",
        "COMFY_CONNECTION_TESTED",
        "system",
        "comfyui",
        {"base_url": body.base_url, "node_count": len(object_info), "h3_node_count": len(h3_nodes)},
    )
    db.commit()
    return ComfyTestResult(
        ok=True,
        message="ComfyUI responded",
        system_stats=stats if isinstance(stats, dict) else {},
        node_count=len(object_info),
        h3_nodes=h3_nodes,
    )


@router.get("/comfy/capabilities")
def comfy_capabilities(settings: SettingsDep, db: DBSession) -> dict[str, Any]:
    client = ComfyUIClient(settings.comfyui_base_url, timeout_s=10)
    try:
        stats = client.system_stats()
        object_info = client.object_info()
    except ComfyError as exc:
        return {"ok": False, "code": exc.code, "message": str(exc)}
    validation = {}
    profile = db.scalar(select(WorkflowProfile).order_by(WorkflowProfile.created_at))
    if profile:
        try:
            validation = validate_against_object_info(profile.graph, profile.parameter_map, object_info)
            profile.validated_against = validation
            db.commit()
        except ComfyError as exc:
            validation = {"error": str(exc)}
    return {
        "ok": True,
        "system_stats": stats,
        "node_count": len(object_info),
        "h3_nodes": sorted(name for name in object_info if "h3" in name.lower() or "minimax" in name.lower()),
        "validation": validation,
    }


@router.get("/workflow-profiles", response_model=list[WorkflowProfilePublic])
def list_workflow_profiles(db: DBSession, project_id: str | None = Query(default=None)) -> list[WorkflowProfilePublic]:
    query = select(WorkflowProfile).order_by(WorkflowProfile.created_at)
    if project_id:
        require_project(db, project_id)
        query = query.where(
            (WorkflowProfile.project_id == project_id) | (WorkflowProfile.project_id.is_(None))
        )
    return [WorkflowProfilePublic.model_validate(_profile_data(row)) for row in db.scalars(query).all()]


@router.post("/workflow-profiles", response_model=WorkflowProfilePublic, status_code=201)
def create_workflow_profile(body: WorkflowProfileCreate, db: DBSession) -> WorkflowProfilePublic:
    if body.project_id:
        require_project(db, body.project_id)
    # Same visibility rule as the list endpoint: a name is unique within one
    # project and among global profiles. Database constraints protect both
    # scopes; this precheck only provides an early friendly conflict.
    scoped = select(WorkflowProfile).where(WorkflowProfile.name == body.name)
    scoped = (
        scoped.where(WorkflowProfile.project_id == body.project_id)
        if body.project_id
        else scoped.where(WorkflowProfile.project_id.is_(None))
    )
    if db.scalar(scoped):
        raise HTTPException(status_code=409, detail="A workflow profile with this name already exists")
    try:
        validate_graph(body.graph, required_nodes=body.required_nodes)
    except ComfyError as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)}) from exc
    if "positive_prompt" not in body.parameter_map:
        raise HTTPException(
            status_code=422,
            detail="A workflow profile must map the compiled H3 prompt to a node input",
        )
    if "duration" not in body.parameter_map:
        raise HTTPException(
            status_code=422,
            detail="A workflow profile must map the target duration to a node input",
        )
    for family, transfer in (body.media_transfers or {}).items():
        if family not in {"image", "video", "audio"}:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown media transfer family: {family}",
            )
        if not isinstance(transfer, dict):
            raise HTTPException(
                status_code=422,
                detail=f"The media transfer for {family} must be an object",
            )
        upload_path = str(transfer.get("upload_path") or "")
        if not upload_path.startswith("/"):
            raise HTTPException(
                status_code=422,
                detail=f"The media transfer for {family} needs an absolute upload_path",
            )
    identity = profile_metadata(body.graph, body.mode.value, body.parameter_map, body.media_transfers)
    checkpoint_id = body.checkpoint_id
    if identity["variant"] == "Ref2VA INT8 ConvRot":
        actual_weight = identity["weight_files"][0]
        if checkpoint_id not in {"MiniMax-H3-Base", actual_weight}:
            raise HTTPException(422, "Checkpoint metadata does not match the registered UNETLoader weight")
        checkpoint_id = actual_weight
    profile = WorkflowProfile(
        project_id=body.project_id,
        name=body.name,
        mode=body.mode.value,
        graph=body.graph,
        parameter_map=body.parameter_map,
        media_transfers=body.media_transfers,
        required_nodes=body.required_nodes,
        checkpoint_id=checkpoint_id,
        graph_sha256=graph_hash(body.graph),
        model_identity=identity,
    )
    db.add(profile)
    try:
        db.flush()
    except IntegrityError as exc:
        # Private or global registrations raced past the name precheck;
        # their database uniqueness constraint rejects the copy.
        db.rollback()
        raise HTTPException(
            status_code=409, detail="A workflow profile with this name already exists"
        ) from exc
    add_audit(
        db,
        "user",
        "WORKFLOW_PROFILE_REGISTERED",
        "workflow_profile",
        profile.id,
        {
            "mode": profile.mode,
            "graph_sha256": profile.graph_sha256,
            "required_nodes": profile.required_nodes,
            "parameter_map": sorted(profile.parameter_map),
            "media_transfers": sorted(profile.media_transfers or {}),
        },
        project_id=profile.project_id,
    )
    db.commit()
    db.refresh(profile)
    return WorkflowProfilePublic.model_validate(_profile_data(profile))


@router.post("/workflow-profiles/{profile_id}/validate", response_model=WorkflowProfilePublic)
def validate_workflow_profile(profile_id: str, settings: SettingsDep, db: DBSession) -> WorkflowProfilePublic:
    """Check the registered profile against the live ComfyUI node inventory."""
    profile = db.get(WorkflowProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Workflow profile not found")
    client = ComfyUIClient(settings.comfyui_base_url, timeout_s=30)
    try:
        object_info = client.object_info()
    except ComfyError as exc:
        raise HTTPException(status_code=503, detail={"code": exc.code, "message": str(exc)}) from exc
    validation = validate_against_object_info(profile.graph or {}, profile.parameter_map, object_info)
    profile.validated_against = {
        **validation, "validated_at": datetime.now(UTC).isoformat(),
        "source": "live_object_info", "generation_verified": False,
    }
    add_audit(
        db,
        "user",
        "WORKFLOW_PROFILE_VALIDATED",
        "workflow_profile",
        profile.id,
        {
            "missing_nodes": validation["missing_nodes"],
            "missing_input_count": len(validation["missing_inputs"]),
        },
        project_id=profile.project_id,
    )
    db.commit()
    db.refresh(profile)
    return WorkflowProfilePublic.model_validate(_profile_data(profile))


@router.post("/workflow-profiles/{profile_id}/validate-managed", response_model=WorkflowProfilePublic)
def validate_managed_workflow_profile(profile_id: str, settings: SettingsDep, db: DBSession) -> WorkflowProfilePublic:
    from shine.comfyops.service_validation import validate_registered_workflow

    validate_registered_workflow(db, settings, profile_id)
    profile = db.get(WorkflowProfile, profile_id)
    return WorkflowProfilePublic.model_validate(_profile_data(profile))


@router.get("/workflow-profiles/{profile_id}")
def get_workflow_profile(profile_id: str, db: DBSession) -> dict[str, Any]:
    profile = db.get(WorkflowProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Workflow profile not found")
    return _profile_data(profile, include_graph=True)


@router.delete("/workflow-profiles/{profile_id}", status_code=204, response_class=Response)
def delete_workflow_profile(profile_id: str, db: DBSession) -> Response:
    profile = db.get(WorkflowProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Workflow profile not found")
    add_audit(
        db,
        "user",
        "WORKFLOW_PROFILE_DELETED",
        "workflow_profile",
        profile.id,
        {"name": profile.name},
        project_id=profile.project_id,
    )
    db.delete(profile)
    db.commit()
    return Response(status_code=204)
