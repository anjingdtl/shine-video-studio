"""Manual4 Stage 3 HTTP endpoints (manual H3 prompts, snapshot preview, runs & worker controls)."""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from shine.manual4.api.deps import (
    M4DB,
    M4Operator,
    PlatformDB,
)
from shine.manual4.api.deps import (
    require_m4_project as _project,
)
from shine.manual4.production.service import (
    control_production_run,
    create_production_run,
    create_retake_job_run,
    get_production_state,
    preview_execution_snapshot,
    reconcile_m4_job,
)
from shine.manual4.prompts.service import (
    batch_freeze_prompts,
    freeze_prompt,
    save_prompt_draft,
    update_shot_render_options,
)
from shine.manual4.storage.files import safe_m4_path
from shine.manual4.storage.models import M4Job

production_router = APIRouter()


class M4ShotOptionsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    aspect_ratio: str | None = None
    continuity_mode: str | None = None


class M4PromptDraftBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_text: str


class M4PromptFreezeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_text: str | None = None


class M4BatchPromptFreezeItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shot_id: str
    prompt_text: str | None = None


class M4BatchPromptFreezeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[M4BatchPromptFreezeItem] = Field(default_factory=list)


class M4SecondPassBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    denoise: float = Field(default=0.25, gt=0.0, le=1.0)
    latent_scale: float = Field(default=1.0, ge=1.0, le=2.0)
    nfe: str = "8"


class M4SnapshotConfigBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope_kind: str = "project"
    scope_target_ids: list[str] = Field(default_factory=list)
    variant_id: str = "h3-ref2va-int8-convrot"
    accelerator_id: str = "pdd8"
    resolution_scale: float = Field(default=1.0, ge=0.3, le=1.0)
    second_pass: M4SecondPassBody | None = None
    seed: int = Field(default=77, ge=0)
    provider: str = "comfyui"
    idempotency_key: str | None = None


class M4ReconcileBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str


@production_router.get("/projects/{project_id}/production/state")
def m4_production_state(
    project_id: str,
    request: Request,
    platform_db: PlatformDB,
    m4_db: M4DB,
) -> dict[str, Any]:
    project = _project(project_id, platform_db, m4_db)
    settings = getattr(request.app.state, "settings", None)
    return get_production_state(m4_db, project, platform_db=platform_db, settings=settings)


@production_router.put("/projects/{project_id}/shots/{shot_id}/render-options")
def m4_update_shot_options(
    project_id: str,
    shot_id: str,
    body: M4ShotOptionsBody,
    request: Request,
    actor: M4Operator,
    platform_db: PlatformDB,
    m4_db: M4DB,
) -> dict[str, Any]:
    project = _project(project_id, platform_db, m4_db)
    res = update_shot_render_options(
        m4_db,
        project,
        shot_id,
        actor=actor,
        aspect_ratio=body.aspect_ratio,
        continuity_mode=body.continuity_mode,
    )
    m4_db.commit()
    settings = getattr(request.app.state, "settings", None)
    return {**res, "state": get_production_state(m4_db, project, platform_db=platform_db, settings=settings)}


@production_router.put("/projects/{project_id}/shots/{shot_id}/prompt/draft")
def m4_save_prompt_draft(
    project_id: str,
    shot_id: str,
    body: M4PromptDraftBody,
    request: Request,
    actor: M4Operator,
    platform_db: PlatformDB,
    m4_db: M4DB,
) -> dict[str, Any]:
    project = _project(project_id, platform_db, m4_db)
    prompt = save_prompt_draft(m4_db, project, shot_id, actor=actor, prompt_text=body.prompt_text)
    m4_db.commit()
    settings = getattr(request.app.state, "settings", None)
    return {"prompt": prompt, "state": get_production_state(m4_db, project, platform_db=platform_db, settings=settings)}


@production_router.post("/projects/{project_id}/shots/{shot_id}/prompt/freeze")
def m4_freeze_prompt(
    project_id: str,
    shot_id: str,
    request: Request,
    actor: M4Operator,
    platform_db: PlatformDB,
    m4_db: M4DB,
    body: M4PromptFreezeBody | None = None,
) -> dict[str, Any]:
    project = _project(project_id, platform_db, m4_db)
    prompt = freeze_prompt(
        m4_db,
        project,
        shot_id,
        actor=actor,
        prompt_text=body.prompt_text if body else None,
    )
    m4_db.commit()
    settings = getattr(request.app.state, "settings", None)
    return {"prompt": prompt, "state": get_production_state(m4_db, project, platform_db=platform_db, settings=settings)}


@production_router.post("/projects/{project_id}/prompts/batch-freeze")
def m4_batch_freeze_prompts(
    project_id: str,
    body: M4BatchPromptFreezeBody,
    request: Request,
    actor: M4Operator,
    platform_db: PlatformDB,
    m4_db: M4DB,
) -> dict[str, Any]:
    project = _project(project_id, platform_db, m4_db)
    result = batch_freeze_prompts(
        m4_db,
        project,
        actor=actor,
        items=[item.model_dump() for item in body.items],
    )
    m4_db.commit()
    settings = getattr(request.app.state, "settings", None)
    return {**result, "state": get_production_state(m4_db, project, platform_db=platform_db, settings=settings)}


@production_router.post("/projects/{project_id}/production/snapshot/preview")
def m4_preview_snapshot(
    project_id: str,
    body: M4SnapshotConfigBody,
    request: Request,
    platform_db: PlatformDB,
    m4_db: M4DB,
) -> dict[str, Any]:
    project = _project(project_id, platform_db, m4_db)
    settings = getattr(request.app.state, "settings", None)
    return preview_execution_snapshot(
        m4_db,
        project,
        scope_kind=body.scope_kind,
        scope_target_ids=body.scope_target_ids,
        variant_id=body.variant_id,
        accelerator_id=body.accelerator_id,
        resolution_scale=body.resolution_scale,
        second_pass=body.second_pass.model_dump() if body.second_pass else None,
        seed=body.seed,
        provider=body.provider,
        platform_db=platform_db,
        settings=settings,
    )


@production_router.post("/projects/{project_id}/production/runs", status_code=201)
def m4_create_run(
    project_id: str,
    body: M4SnapshotConfigBody,
    request: Request,
    actor: M4Operator,
    platform_db: PlatformDB,
    m4_db: M4DB,
) -> dict[str, Any]:
    project = _project(project_id, platform_db, m4_db)
    settings = getattr(request.app.state, "settings", None)
    run = create_production_run(
        m4_db,
        project,
        actor=actor,
        scope_kind=body.scope_kind,
        scope_target_ids=body.scope_target_ids,
        variant_id=body.variant_id,
        accelerator_id=body.accelerator_id,
        resolution_scale=body.resolution_scale,
        second_pass=body.second_pass.model_dump() if body.second_pass else None,
        seed=body.seed,
        provider=body.provider,
        idempotency_key=body.idempotency_key,
        platform_db=platform_db,
        settings=settings,
    )
    m4_db.commit()
    return {"run": run, "state": get_production_state(m4_db, project, platform_db=platform_db, settings=settings)}


@production_router.post("/projects/{project_id}/production/runs/{run_id}/{action}")
def m4_control_run(
    project_id: str,
    run_id: str,
    action: str,
    request: Request,
    actor: M4Operator,
    platform_db: PlatformDB,
    m4_db: M4DB,
) -> dict[str, Any]:
    project = _project(project_id, platform_db, m4_db)
    run = control_production_run(m4_db, project, run_id, actor=actor, action=action)
    m4_db.commit()
    settings = getattr(request.app.state, "settings", None)
    return {"run": run, "state": get_production_state(m4_db, project, platform_db=platform_db, settings=settings)}


@production_router.post("/projects/{project_id}/shots/{shot_id}/retake", status_code=201)
def m4_retake_shot(
    project_id: str,
    shot_id: str,
    body: M4SnapshotConfigBody,
    request: Request,
    actor: M4Operator,
    platform_db: PlatformDB,
    m4_db: M4DB,
) -> dict[str, Any]:
    project = _project(project_id, platform_db, m4_db)
    settings = getattr(request.app.state, "settings", None)
    run = create_retake_job_run(
        m4_db,
        project,
        shot_id,
        actor=actor,
        variant_id=body.variant_id,
        accelerator_id=body.accelerator_id,
        resolution_scale=body.resolution_scale,
        second_pass=body.second_pass.model_dump() if body.second_pass else None,
        seed=body.seed,
        provider=body.provider,
        platform_db=platform_db,
        settings=settings,
    )
    m4_db.commit()
    return {"run": run, "state": get_production_state(m4_db, project, platform_db=platform_db, settings=settings)}


@production_router.post("/projects/{project_id}/jobs/{job_id}/reconcile")
def m4_reconcile_job(
    project_id: str,
    job_id: str,
    body: M4ReconcileBody,
    request: Request,
    actor: M4Operator,
    platform_db: PlatformDB,
    m4_db: M4DB,
) -> dict[str, Any]:
    project = _project(project_id, platform_db, m4_db)
    job = reconcile_m4_job(m4_db, project, job_id, actor=actor, decision=body.decision)
    m4_db.commit()
    settings = getattr(request.app.state, "settings", None)
    return {"job": job, "state": get_production_state(m4_db, project, platform_db=platform_db, settings=settings)}


@production_router.get("/projects/{project_id}/jobs/{job_id}/media")
def m4_job_media(
    project_id: str,
    job_id: str,
    request: Request,
    platform_db: PlatformDB,
    m4_db: M4DB,
) -> FileResponse:
    project = _project(project_id, platform_db, m4_db)
    job = m4_db.get(M4Job, job_id)
    if job is None or job.project_id != project.id or not job.output_storage_path:
        raise HTTPException(404, {"code": "M4_JOB_MEDIA_MISSING", "message": "该镜头尚无视频输出。"})
    path = safe_m4_path(request.app.state.m4_root, job.output_storage_path)
    if not path.is_file():
        raise HTTPException(404, {"code": "M4_JOB_FILE_MISSING", "message": "视频文件缺失。"})
    return FileResponse(path, media_type="video/mp4")


@production_router.post("/projects/{project_id}/jobs/{job_id}/open-folder")
def m4_open_job_folder(
    project_id: str,
    job_id: str,
    request: Request,
    actor: M4Operator,
    platform_db: PlatformDB,
    m4_db: M4DB,
) -> dict[str, str]:
    """Open the stored shot video's directory on the local Windows desktop."""
    _ = actor
    project = _project(project_id, platform_db, m4_db)
    job = m4_db.get(M4Job, job_id)
    if job is None or job.project_id != project.id or not job.output_storage_path:
        raise HTTPException(404, {"code": "M4_JOB_MEDIA_MISSING", "message": "该镜头尚无视频输出。"})
    path = safe_m4_path(request.app.state.m4_root, job.output_storage_path)
    if not path.is_file():
        raise HTTPException(404, {"code": "M4_JOB_FILE_MISSING", "message": "视频文件已移动或缺失。"})
    if not hasattr(os, "startfile"):
        raise HTTPException(
            501,
            {"code": "M4_OPEN_FOLDER_UNSUPPORTED", "message": "打开文件夹需要在 Windows 本机运行工作台。"},
        )
    try:
        os.startfile(str(path.parent), "explore")
    except OSError as exc:
        raise HTTPException(
            409,
            {"code": "M4_OPEN_FOLDER_FAILED", "message": "Windows 无法打开视频所在文件夹。"},
        ) from exc
    return {"folder": str(path.parent), "file": str(path)}
