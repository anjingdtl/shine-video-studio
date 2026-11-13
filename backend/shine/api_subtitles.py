"""The external subtitle layer: generate, revise, export and composite.

The subtitles of a project are kept outside the rendered shots.  A human can
revise a line or a timing, every revision is kept, and the delivery step
composites the revision that was approved into the reel.

The subtitles never change the picture and never change the audio.  They are
produced from the real alignment of the real artifact, so a line that does not
match the audio is a visible defect rather than a silent one.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field, StrictStr
from sqlalchemy import select

from shine.api_common import DBSession, app_settings
from shine.config import Settings
from shine.media.subtitles import (
    SubtitleCue,
    SubtitleError,
    parse_srt,
    render_ass,
    render_srt,
    sha256_text,
)
from shine.models import SubtitleTrack
from shine.services import add_audit, require_project

router = APIRouter(prefix="/api/v1", tags=["subtitles"])

SettingsDep = Annotated[Settings, Depends(app_settings)]


class SubtitleCueInput(BaseModel):
    """One subtitle line."""

    index: int = Field(ge=1, le=2000)
    start_s: float = Field(ge=0, allow_inf_nan=False)
    end_s: float = Field(gt=0, allow_inf_nan=False)
    text: StrictStr = Field(min_length=1, max_length=200)


class SubtitleRevisionBody(BaseModel):
    """A new revision of the subtitle layer.

    The cues may be given directly, or the human may upload a revised SRT.
    A revised SRT wins, because it is the file the human actually edited.
    """

    cues: list[SubtitleCueInput] = Field(default_factory=list, max_length=2000)
    srt: str = Field(default="", max_length=200000)
    created_by: str = Field(default="operator", max_length=64)
    source: str = Field(default="human", max_length=24)
    episode_id: str | None = Field(default=None, max_length=80)


def _subtitle_data(row: SubtitleTrack) -> dict[str, Any]:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "revision": row.revision,
        "status": row.status,
        "episode_id": row.episode_id,
        "cues": row.cues,
        "sha256": row.sha256,
        "source": row.source,
        "source_artifact_sha256": row.source_artifact_sha256,
        "created_by": row.created_by,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _latest(db: DBSession, project_id: str) -> SubtitleTrack | None:
    return db.scalar(
        select(SubtitleTrack)
        .where(SubtitleTrack.project_id == project_id)
        .order_by(SubtitleTrack.revision.desc())
        .limit(1)
    )


@router.get("/projects/{project_id}/subtitles")
def list_subtitles(project_id: str, db: DBSession) -> list[dict[str, Any]]:
    """Every revision of the subtitle layer, newest first."""
    require_project(db, project_id)
    rows = db.scalars(
        select(SubtitleTrack)
        .where(SubtitleTrack.project_id == project_id)
        .order_by(SubtitleTrack.revision.desc())
    ).all()
    return [_subtitle_data(row) for row in rows]


@router.post("/projects/{project_id}/subtitles", status_code=201)
def create_subtitle_revision(
    project_id: str,
    body: SubtitleRevisionBody,
    db: DBSession,
) -> dict[str, Any]:
    """Store a new revision of the subtitle layer.

    A revised SRT wins over a cue list, because the file is what the human
    edited.  An earlier revision is never changed.
    """
    require_project(db, project_id)
    try:
        if body.srt.strip():
            cues = parse_srt(body.srt)
        else:
            cues = [
                SubtitleCue(index=item.index, start_s=item.start_s, end_s=item.end_s, text=item.text)
                for item in body.cues
            ]
            if not cues:
                raise SubtitleError(422, "SUBTITLE_EMPTY")
        for cue in cues:
            cue.validate()
    except SubtitleError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    srt = render_srt(cues)
    ass = render_ass(cues)
    current = _latest(db, project_id)
    revision = (current.revision + 1) if current else 1
    row = SubtitleTrack(
        project_id=project_id,
        revision=revision,
        status="DRAFT",
        episode_id=body.episode_id,
        cues=[{"index": c.index, "start_s": c.start_s, "end_s": c.end_s, "text": c.text} for c in cues],
        srt=srt,
        ass=ass,
        sha256=sha256_text(srt),
        source=body.source,
        source_artifact_sha256=current.source_artifact_sha256 if current else None,
        created_by=body.created_by,
    )
    db.add(row)
    db.flush()
    add_audit(db, body.created_by, "SUBTITLE_REVISION_SAVED", "subtitle_track", row.id,
              {"revision": row.revision, "cue_count": len(cues), "sha256": row.sha256,
               "episode_id": row.episode_id, "source": row.source},
              project_id=project_id)
    db.commit()
    db.refresh(row)
    return _subtitle_data(row)


@router.get("/projects/{project_id}/subtitles/{revision}/export")
def export_subtitles(
    project_id: str,
    revision: int,
    db: DBSession,
    format: str = "srt",
) -> Response:
    """The subtitle file of one revision, for a human to edit."""
    require_project(db, project_id)
    row = db.scalar(
        select(SubtitleTrack).where(
            SubtitleTrack.project_id == project_id, SubtitleTrack.revision == revision
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Subtitle revision not found")
    if format not in {"srt", "ass"}:
        raise HTTPException(status_code=422, detail="Unsupported subtitle format")
    content = row.srt if format == "srt" else row.ass
    return Response(
        content,
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{row.id}.{format}"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/projects/{project_id}/subtitles/{revision}/approve")
def approve_subtitle_revision(project_id: str, revision: int, db: DBSession) -> dict[str, Any]:
    """Approve one revision; the delivery composites the approved one."""
    require_project(db, project_id)
    row = db.scalar(
        select(SubtitleTrack).where(
            SubtitleTrack.project_id == project_id, SubtitleTrack.revision == revision
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Subtitle revision not found")
    if row.status == "APPROVED":
        return _subtitle_data(row)
    row.status = "APPROVED"
    add_audit(db, "operator", "SUBTITLE_REVISION_APPROVED", "subtitle_track", row.id,
              {"revision": row.revision, "sha256": row.sha256}, project_id=project_id)
    db.commit()
    db.refresh(row)
    return _subtitle_data(row)


__all__ = ["router"]
