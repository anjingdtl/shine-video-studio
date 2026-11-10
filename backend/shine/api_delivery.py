"""Delivery endpoints: turn an edit decision list into a real, playable reel.

The editor agent proposes an ``EditDecisionList``. These endpoints apply that
proposal to the artifacts it names and store the resulting reel as a content
addressed artifact, so a delivery can never overwrite a passed shot artifact
and every segment stays traceable to the artifact it came from.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import Field
from sqlalchemy import select

from shine.agents.film_contracts import EditDecisionList
from shine.api_common import DBSession, app_settings
from shine.api_operator import Operator
from shine.config import Settings
from shine.media.artifacts import safe_asset_path
from shine.media.assembly import DeliveryAssembler, DeliveryClip, DeliveryError
from shine.models import MediaArtifact, Project, SubtitleTrack
from shine.security import sha256_bytes
from shine.services import add_audit, require_project

router = APIRouter(prefix="/api/v1", tags=["delivery"])

SettingsDep = Annotated[Settings, Depends(app_settings)]


class DeliveryRequest(EditDecisionList):
    """The editor's proposal plus the assembly parameters."""

    fps: float = Field(default=24.0, gt=0, le=120, allow_inf_nan=False)
    width: int | None = Field(default=None, ge=2, le=8192, multiple_of=2)
    height: int | None = Field(default=None, ge=2, le=8192, multiple_of=2)
    crf: int = Field(default=18, ge=0, le=51)
    subtitle_revision: int | None = Field(default=None, ge=1, le=100000)
    """The approved subtitle revision to composite into the reel.

    The subtitles are composited at the very end, so the picture and the audio
    of the shots are never touched by them.
    """


@router.post("/projects/{project_id}/deliveries", status_code=201)
def create_delivery(
    project_id: str,
    body: DeliveryRequest,
    request: Request,
    db: DBSession,
    settings: SettingsDep,
) -> dict[str, Any]:
    require_project(db, project_id)
    clips = [
        DeliveryClip(
            job_id=clip.job_id,
            attempt_id=clip.attempt_id,
            artifact_id=clip.artifact_id,
            in_s=clip.in_s,
            out_s=clip.out_s,
        )
        for clip in body.clips
    ]
    assembler = DeliveryAssembler(
        asset_root=request.app.state.asset_root,
        ffmpeg_path=settings.ffmpeg_path,
        ffprobe_path=settings.ffprobe_path,
    )
    subtitle_ass = None
    subtitle_sha256 = None
    if body.subtitle_revision is not None:
        track = db.scalar(
            select(SubtitleTrack).where(
                SubtitleTrack.project_id == project_id,
                SubtitleTrack.revision == body.subtitle_revision,
            )
        )
        if track is None:
            raise HTTPException(status_code=404, detail="Subtitle revision not found")
        if track.status != "APPROVED":
            # Only an approved revision is composited; a draft is never burned in.
            raise HTTPException(status_code=409, detail="SUBTITLE_REVISION_NOT_APPROVED")
        subtitle_ass = track.ass
        subtitle_sha256 = track.sha256
    try:
        result = assembler.assemble(
            db,
            project_id,
            clips,
            fps=body.fps,
            width=body.width,
            height=body.height,
            crf=body.crf,
            subtitle_ass=subtitle_ass,
            subtitle_sha256=subtitle_sha256,
        )
    except DeliveryError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    add_audit(
        db,
        "user",
        "DELIVERY_ASSEMBLED",
        "artifact",
        result.artifact.id,
        {
            "sha256": result.artifact.sha256,
            "byte_size": result.artifact.byte_size,
            "duration_s": result.duration_s,
            "width": result.width,
            "height": result.height,
            "fps": result.fps,
            "clip_count": len(result.clips),
            "review_state": result.review_state,
            "sources": [
                {
                    "artifact_id": clip["artifact_id"],
                    "job_id": clip["job_id"],
                    "attempt_id": clip["attempt_id"],
                    "in_s": clip["in_s"],
                    "out_s": clip["out_s"],
                    "source_job_status": clip["source_job_status"],
                }
                for clip in result.clips
            ],
        },
        project_id=project_id,
    )
    db.commit()
    db.refresh(result.artifact)
    return {
        "artifact_id": result.artifact.id,
        "sha256": result.artifact.sha256,
        "byte_size": result.artifact.byte_size,
        "storage_path": result.artifact.storage_path,
        "duration_s": result.duration_s,
        "width": result.width,
        "height": result.height,
        "fps": result.fps,
        "review_state": result.review_state,
        "clips": result.clips,
    }


@router.get("/projects/{project_id}/deliveries")
def list_deliveries(project_id: str, db: DBSession) -> list[dict[str, Any]]:
    require_project(db, project_id)
    rows = (
        db.query(MediaArtifact)
        .filter(
            MediaArtifact.project_id == project_id,
            MediaArtifact.artifact_kind == "delivery",
        )
        .order_by(MediaArtifact.created_at.desc())
        .all()
    )
    return [
        {
            "artifact_id": row.id,
            "sha256": row.sha256,
            "byte_size": row.byte_size,
            "storage_path": row.storage_path,
            "created_at": row.created_at,
            "provenance": row.provenance,
        }
        for row in rows
    ]


@router.get("/projects/{project_id}/delivery-orders")
def list_delivery_orders(project_id: str, db: DBSession) -> list[dict[str, Any]]:
    """The durable auto-delivery orders of a project, newest version first."""
    from shine.media.delivery_engine import _order_public
    from shine.media.delivery_models import DeliveryOrder

    require_project(db, project_id)
    rows = (
        db.query(DeliveryOrder)
        .filter(DeliveryOrder.project_id == project_id)
        .order_by(DeliveryOrder.created_at.desc(), DeliveryOrder.version.desc())
        .all()
    )
    return [_order_public(order) for order in rows]


@router.post("/delivery-orders/{order_id}/retry", status_code=202)
def retry_delivery_order(order_id: str, db: DBSession) -> dict[str, Any]:
    """Requeue a failed auto-delivery order (bounded again by max_attempts)."""
    from shine.media.delivery_models import DeliveryOrder
    from shine.services import add_audit

    order = db.get(DeliveryOrder, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Delivery order not found")
    if order.status not in {"FAILED", "BLOCKED"}:
        raise HTTPException(status_code=409, detail="DELIVERY_ORDER_NOT_RETRYABLE")
    order.status = "PENDING"
    order.attempt_count = 0
    order.failure_code = None
    order.failure_note = None
    order.state_version += 1
    add_audit(db, "user", "AUTO_DELIVERY_RETRY_QUEUED", "delivery_order", order.id,
              {"series_id": order.series_id, "version": order.version},
              project_id=order.project_id)
    db.commit()
    return {"id": order.id, "status": order.status}


@router.post("/projects/{project_id}/production-series/{series_id}/assemble", status_code=202)
def assemble_completed_series(project_id: str, series_id: str, actor: Operator, db: DBSession):
    from shine.api_preparation import locked
    from shine.media.delivery_engine import DeliveryBlock, _order_public, enqueue_auto_delivery
    from shine.models import ProductionSeries

    locked(db)
    series = db.get(ProductionSeries, series_id)
    if series is None or series.project_id != project_id:
        raise HTTPException(404, "PRODUCTION_SERIES_NOT_FOUND")
    if series.status != "COMPLETED":
        raise HTTPException(409, "PRODUCTION_SERIES_NOT_COMPLETED")
    try:
        order = enqueue_auto_delivery(db, series)
    except DeliveryBlock as exc:
        raise HTTPException(409, exc.code) from exc
    if order is None:
        raise HTTPException(409, "DELIVERY_MANAGED_BY_PRODUCTION_RUN_OR_RETAKE")
    add_audit(db, actor, "AUTO_DELIVERY_REQUESTED", "delivery_order", order.id,
              {"series_id": series_id, "version": order.version}, project_id=project_id)
    db.commit()
    return _order_public(order)


@router.get("/deliveries/{artifact_id}/media")
def delivery_media(artifact_id: str, request: Request, db: DBSession) -> FileResponse:
    artifact = db.get(MediaArtifact, artifact_id)
    if not artifact or artifact.artifact_kind != "delivery":
        raise HTTPException(status_code=404, detail="Delivery artifact not found")
    path: Path = safe_asset_path(request.app.state.asset_root, artifact.storage_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Delivery file is missing on disk")
    if path.stat().st_size != artifact.byte_size or sha256_bytes(path.read_bytes()) != artifact.sha256:
        raise HTTPException(status_code=409, detail="DELIVERY_FILE_HASH_MISMATCH")
    return FileResponse(
        path,
        media_type=artifact.content_type,
        filename=_delivery_download_filename(db, artifact),
    )


def _delivery_download_filename(db: DBSession, artifact: MediaArtifact) -> str:
    """Give downloads a project/episode name while keeping storage content-addressed."""
    from shine.media.delivery_models import DeliveryOrder
    from shine.production.models import ProductionRunEpisode

    project = db.get(Project, artifact.project_id)
    title = project.title if project else "项目"
    title = re.sub(r'[\x00-\x1f<>:"/\\|?*]+', "-", title)
    title = re.sub(r"\s+", " ", title).strip(" .-")[:140].rstrip(" .-") or "项目"

    order = db.scalar(
        select(DeliveryOrder)
        .where(
            DeliveryOrder.project_id == artifact.project_id,
            DeliveryOrder.artifact_id == artifact.id,
        )
        .order_by(DeliveryOrder.version.desc())
        .limit(1)
    )
    episode = None
    version = 1
    if order is not None:
        version = order.version
        if order.scope_kind == "episode" and order.scope_id:
            episode = db.get(ProductionRunEpisode, order.scope_id)
    if episode is None:
        episode = db.scalar(
            select(ProductionRunEpisode).where(
                ProductionRunEpisode.project_id == artifact.project_id,
                ProductionRunEpisode.delivered_artifact_id == artifact.id,
            )
        )

    if episode is not None:
        sibling_ids = db.scalars(
            select(ProductionRunEpisode.id)
            .where(ProductionRunEpisode.run_id == episode.run_id)
            .order_by(ProductionRunEpisode.position, ProductionRunEpisode.id)
        ).all()
        episode_number = sibling_ids.index(episode.id) + 1
        suffix = f"-第{episode_number}集"
    else:
        suffix = "-成片"
    if version > 1:
        suffix += f"-v{version}"
    return f"{title}{suffix}.mp4"
