"""Manual4 Stage 4 HTTP endpoints (episode delivery assembly, playback & acceptance)."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from shine.manual4.api.deps import (
    M4DB,
    M4Operator,
    PlatformDB,
)
from shine.manual4.api.deps import (
    require_m4_project as _project,
)
from shine.manual4.delivery.service import (
    assemble_episode_delivery,
    get_delivery_state,
    record_delivery_acceptance,
)
from shine.manual4.storage.files import safe_m4_path, sha256_file
from shine.manual4.storage.models import M4Delivery

delivery_router = APIRouter()


class M4AssembleDeliveryBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_overrides: dict[str, str] = Field(default_factory=dict)
    include_subtitles: bool = True
    burn_subtitles: bool = False


class M4AcceptDeliveryBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str
    watched: bool = False
    evidence: str = ""
    expected_sha256: str
    request_id: str | None = None


@delivery_router.get("/projects/{project_id}/delivery/state")
def m4_delivery_state(
    project_id: str,
    platform_db: PlatformDB,
    m4_db: M4DB,
) -> dict[str, Any]:
    project = _project(project_id, platform_db, m4_db)
    return get_delivery_state(m4_db, project)


@delivery_router.post("/projects/{project_id}/episodes/{episode_id}/deliveries/assemble", status_code=201)
def m4_assemble_delivery(
    project_id: str,
    episode_id: str,
    request: Request,
    actor: M4Operator,
    platform_db: PlatformDB,
    m4_db: M4DB,
    body: M4AssembleDeliveryBody | None = None,
) -> dict[str, Any]:
    project = _project(project_id, platform_db, m4_db)
    stub = getattr(request.app.state, "m4_delivery_assembler_stub", None)
    deliv = assemble_episode_delivery(
        m4_db,
        request.app.state.m4_root,
        project,
        episode_id,
        actor=actor,
        job_overrides=body.job_overrides if body else None,
        include_subtitles=body.include_subtitles if body else True,
        burn_subtitles=body.burn_subtitles if body else False,
        assembler_stub=stub,
    )
    m4_db.commit()
    return {"delivery": deliv, "state": get_delivery_state(m4_db, project)}


@delivery_router.post("/projects/{project_id}/deliveries/{delivery_id}/accept")
def m4_accept_delivery(
    project_id: str,
    delivery_id: str,
    body: M4AcceptDeliveryBody,
    request: Request,
    actor: M4Operator,
    platform_db: PlatformDB,
    m4_db: M4DB,
) -> dict[str, Any]:
    project = _project(project_id, platform_db, m4_db)
    req_id = (body.request_id or "").strip() or str(uuid4())
    acc = record_delivery_acceptance(
        m4_db,
        request.app.state.m4_root,
        project,
        delivery_id,
        actor=actor,
        decision=body.decision,
        watched=body.watched,
        evidence=body.evidence,
        expected_sha256=body.expected_sha256,
        request_id=req_id,
    )
    m4_db.commit()
    return {"acceptance": acc, "state": get_delivery_state(m4_db, project)}


@delivery_router.get("/projects/{project_id}/deliveries/{delivery_id}/media")
def m4_delivery_media(
    project_id: str,
    delivery_id: str,
    request: Request,
    platform_db: PlatformDB,
    m4_db: M4DB,
) -> FileResponse:
    project = _project(project_id, platform_db, m4_db)
    deliv = m4_db.get(M4Delivery, delivery_id)
    if deliv is None or deliv.project_id != project.id or not deliv.storage_path:
        raise HTTPException(404, {"code": "M4_DELIVERY_NOT_FOUND", "message": "成片文件不存在。"})
    path = safe_m4_path(request.app.state.m4_root, deliv.storage_path)
    if not path.is_file():
        raise HTTPException(404, {"code": "M4_DELIVERY_FILE_MISSING", "message": "磁盘成片文件缺失。"})
    if deliv.sha256 and sha256_file(path) != deliv.sha256:
        raise HTTPException(
            409,
            {"code": "M4_DELIVERY_SHA_MISMATCH", "message": "成片文件校验和与台账不一致，已拒绝播放。"},
        )
    return FileResponse(path, media_type="video/mp4")


@delivery_router.get("/projects/{project_id}/deliveries/{delivery_id}/subtitles.srt")
def m4_delivery_subtitles(
    project_id: str,
    delivery_id: str,
    platform_db: PlatformDB,
    m4_db: M4DB,
) -> PlainTextResponse:
    project = _project(project_id, platform_db, m4_db)
    deliv = m4_db.get(M4Delivery, delivery_id)
    if deliv is None or deliv.project_id != project.id:
        raise HTTPException(404, {"code": "M4_DELIVERY_NOT_FOUND", "message": "成片记录不存在。"})
    return PlainTextResponse(deliv.subtitle_srt or "", media_type="text/plain; charset=utf-8")
