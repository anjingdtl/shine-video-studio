"""Manual4 Stage 2 HTTP endpoints (material requirements, generation, upload, promotion, shot bindings)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from shine.foundation.materials import get_library_task_status
from shine.manual4.api.deps import (
    M4DB,
    M4Operator,
    PlatformDB,
)
from shine.manual4.api.deps import (
    require_m4_project as _project,
)
from shine.manual4.materials.service import (
    adopt_library_version_for_m4,
    backfill_m4_project_assets,
    decline_m4_requirement,
    get_materials_state,
    promote_m4_asset,
    put_shot_bindings,
    resolve_m4_asset_file,
    search_m4_library_assets,
    select_or_generate_material,
    sync_m4_material_tasks,
    upload_m4_asset,
)

materials_router = APIRouter()


class M4SelectOrGenerateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = "generate"
    asset_version_id: str | None = None
    prompt: str = ""
    width: int = Field(default=1024, ge=256, le=2048)
    height: int = Field(default=1024, ge=256, le=2048)
    seed: int = Field(default=1, ge=0)
    steps: int = Field(default=25, ge=1, le=60)


class M4DeclineBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = ""


class M4PromoteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement_id: str | None = None


class M4ShotBindingItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_version_id: str
    use_as: str | None = None
    role_label: str = ""


class M4ShotBindingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bindings: list[M4ShotBindingItem] = Field(default_factory=list)


class M4AdoptLibraryBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_id: str
    requirement_id: str | None = None
    shot_ids: list[str] = Field(default_factory=list)
    role_label: str = ""
    family: str | None = None


@materials_router.get("/projects/{project_id}/materials/requirements")
def m4_materials_requirements(
    project_id: str,
    request: Request,
    platform_db: PlatformDB,
    m4_db: M4DB,
) -> dict[str, Any]:
    project = _project(project_id, platform_db, m4_db)
    state = get_materials_state(m4_db, project, platform_db=platform_db, settings=request.app.state.settings)
    m4_db.commit()
    return state


@materials_router.post("/projects/{project_id}/materials/{req_id}/select-or-generate")
def m4_select_or_generate(
    project_id: str,
    req_id: str,
    body: M4SelectOrGenerateBody,
    request: Request,
    actor: M4Operator,
    platform_db: PlatformDB,
    m4_db: M4DB,
) -> dict[str, Any]:
    project = _project(project_id, platform_db, m4_db)
    provider = getattr(request.app.state, "m4_image_provider", None)
    result = select_or_generate_material(
        m4_db,
        request.app.state.session_factory,
        request.app.state.m4_session_factory,
        request.app.state.settings,
        request.app.state.m4_root,
        project,
        req_id,
        actor=actor,
        mode=body.mode,
        asset_version_id=body.asset_version_id,
        prompt=body.prompt,
        width=body.width,
        height=body.height,
        seed=body.seed,
        steps=body.steps,
        image_provider=provider,
        platform_db=platform_db,
    )
    platform_db.commit()
    m4_db.commit()
    return {
        **result,
        "state": get_materials_state(m4_db, project, platform_db=platform_db, settings=request.app.state.settings),
    }


@materials_router.post("/projects/{project_id}/materials/{req_id}/decline")
def m4_decline_material(
    project_id: str,
    req_id: str,
    body: M4DeclineBody,
    request: Request,
    actor: M4Operator,
    platform_db: PlatformDB,
    m4_db: M4DB,
) -> dict[str, Any]:
    project = _project(project_id, platform_db, m4_db)
    result = decline_m4_requirement(m4_db, project, req_id, actor=actor, reason=body.reason)
    m4_db.commit()
    return {
        **result,
        "state": get_materials_state(m4_db, project, platform_db=platform_db, settings=request.app.state.settings),
    }


@materials_router.post("/projects/{project_id}/assets/upload")
async def m4_upload_asset(
    project_id: str,
    request: Request,
    actor: M4Operator,
    platform_db: PlatformDB,
    m4_db: M4DB,
    family: str = Form("character"),
    entity_name: str = Form(...),
    variant_name: str = Form(""),
    requirement_id: Annotated[str, Form()] = "",
    kind: Annotated[str, Form()] = "",
    file: UploadFile = File(),  # noqa: B008
) -> dict[str, Any]:
    project = _project(project_id, platform_db, m4_db)
    content = await file.read()
    asset = upload_m4_asset(
        m4_db,
        project,
        request.app.state.m4_root,
        actor=actor,
        family=family,
        entity_name=entity_name,
        variant_name=variant_name,
        requirement_id=requirement_id or None,
        filename=file.filename or "asset.png",
        content=content,
        kind=kind or None,
        platform_db=platform_db,
        settings=request.app.state.settings,
    )
    platform_db.commit()
    m4_db.commit()
    return {
        "asset": asset,
        "state": get_materials_state(m4_db, project, platform_db=platform_db, settings=request.app.state.settings),
    }


@materials_router.post("/projects/{project_id}/assets/{asset_id}/promote")
def m4_promote_asset(
    project_id: str,
    asset_id: str,
    request: Request,
    actor: M4Operator,
    platform_db: PlatformDB,
    m4_db: M4DB,
    body: M4PromoteBody | None = None,
) -> dict[str, Any]:
    project = _project(project_id, platform_db, m4_db)
    result = promote_m4_asset(
        m4_db,
        project,
        asset_id,
        actor=actor,
        requirement_id=body.requirement_id if body else None,
        platform_db=platform_db,
        settings=request.app.state.settings,
    )
    platform_db.commit()
    m4_db.commit()
    return {
        **result,
        "state": get_materials_state(m4_db, project, platform_db=platform_db, settings=request.app.state.settings),
    }


@materials_router.get("/projects/{project_id}/assets/{asset_id}/media")
def m4_asset_media(
    project_id: str,
    asset_id: str,
    request: Request,
    platform_db: PlatformDB,
    m4_db: M4DB,
) -> FileResponse:
    project = _project(project_id, platform_db, m4_db)
    path, content_type = resolve_m4_asset_file(
        m4_db,
        request.app.state.m4_root,
        project,
        asset_id,
        platform_db=platform_db,
        settings=request.app.state.settings,
    )
    return FileResponse(path, media_type=content_type)


@materials_router.get("/projects/{project_id}/materials/library")
def m4_search_library(
    project_id: str,
    request: Request,
    platform_db: PlatformDB,
    m4_db: M4DB,
    kind: str | None = Query(default=None),
    category: str | None = Query(default=None),
    query: str = Query(default=""),
    limit: int = Query(default=60, ge=1, le=200),
) -> dict[str, Any]:
    project = _project(project_id, platform_db, m4_db)
    return search_m4_library_assets(
        platform_db,
        request.app.state.settings,
        project=project,
        kind=kind,
        category=category,
        query=query,
        limit=limit,
    )


@materials_router.post("/projects/{project_id}/materials/library/adopt")
def m4_adopt_library_version(
    project_id: str,
    body: M4AdoptLibraryBody,
    request: Request,
    actor: M4Operator,
    platform_db: PlatformDB,
    m4_db: M4DB,
) -> dict[str, Any]:
    project = _project(project_id, platform_db, m4_db)
    result = adopt_library_version_for_m4(
        m4_db,
        platform_db,
        request.app.state.settings,
        project,
        actor=actor,
        version_id=body.version_id,
        requirement_id=body.requirement_id,
        shot_ids=body.shot_ids or None,
        role_label=body.role_label,
        family=body.family,
    )
    platform_db.commit()
    m4_db.commit()
    return {
        **result,
        "state": get_materials_state(m4_db, project, platform_db=platform_db, settings=request.app.state.settings),
    }


@materials_router.get("/projects/{project_id}/materials/tasks/{task_id}")
def m4_material_task_status(
    project_id: str,
    task_id: str,
    request: Request,
    platform_db: PlatformDB,
    m4_db: M4DB,
) -> dict[str, Any]:
    project = _project(project_id, platform_db, m4_db)
    task = get_library_task_status(platform_db, request.app.state.settings, task_id)
    sync_m4_material_tasks(m4_db, platform_db, request.app.state.settings, project, task_id=task_id)
    m4_db.commit()
    return task


@materials_router.post("/projects/{project_id}/materials/backfill-library")
def m4_backfill_library(
    project_id: str,
    request: Request,
    actor: M4Operator,
    platform_db: PlatformDB,
    m4_db: M4DB,
) -> dict[str, Any]:
    project = _project(project_id, platform_db, m4_db)
    result = backfill_m4_project_assets(
        m4_db,
        platform_db,
        request.app.state.settings,
        request.app.state.m4_root,
        project,
        actor=actor,
    )
    platform_db.commit()
    m4_db.commit()
    return {
        **result,
        "state": get_materials_state(m4_db, project, platform_db=platform_db, settings=request.app.state.settings),
    }


@materials_router.put("/projects/{project_id}/shots/{shot_id}/bindings")
def m4_put_shot_bindings(
    project_id: str,
    shot_id: str,
    body: M4ShotBindingsBody,
    request: Request,
    actor: M4Operator,
    platform_db: PlatformDB,
    m4_db: M4DB,
) -> dict[str, Any]:
    project = _project(project_id, platform_db, m4_db)
    result = put_shot_bindings(
        m4_db,
        project,
        shot_id,
        actor=actor,
        bindings=[item.model_dump() for item in body.bindings],
    )
    m4_db.commit()
    return {
        **result,
        "state": get_materials_state(m4_db, project, platform_db=platform_db, settings=request.app.state.settings),
    }
