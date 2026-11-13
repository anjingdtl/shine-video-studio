"""Director-mode streamline endpoints: the convergent ①–④ path.

These routes compose the existing versioned services into the single-click
path a non-expert walks.  They persist intent and read ledgers; the resident
Worker performs every model call and render.  Expert mode keeps its own
entrances unchanged.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import Field
from sqlalchemy import select

from shine.api_common import DBSession
from shine.api_operator import Operator
from shine.director import streamline
from shine.models import AgentArtifact
from shine.schemas import APIModel
from shine.services import require_project

router = APIRouter(prefix="/api/v1", tags=["director streamline"])


# ---------------------------------------------------------------------------
# ① Editable outline
# ---------------------------------------------------------------------------


class OutlineRevisionBody(APIModel):
    expected_sha256: str = Field(min_length=64, max_length=64)
    markdown: str = Field(min_length=1, max_length=60000)


@router.get("/projects/{project_id}/director/outline")
def director_outline(project_id: str, db: DBSession) -> dict[str, Any]:
    """The current outline as an editable body, with questions as hints."""
    from shine.api_director import _confirmed_outline

    require_project(db, project_id)
    # Prefer the newest non-stale outline (a fresh edit before confirmation),
    # falling back to the confirmed one.
    row = db.scalars(select(AgentArtifact).where(
        AgentArtifact.project_id == project_id, AgentArtifact.kind == "StoryOutline",
        AgentArtifact.stale.is_(False),
    ).order_by(AgentArtifact.created_at.desc())).first()
    confirmed = _confirmed_outline(db, project_id)
    if row is None:
        return {"artifact_id": None, "markdown": "", "questions": [], "confirmed": False}
    return {
        "artifact_id": row.id,
        "sha256": row.sha256,
        "markdown": streamline.outline_to_markdown(row.payload_json),
        "payload": row.payload_json,
        "questions": list(row.payload_json.get("questions") or []),
        "user_edited": bool(row.payload_json.get("user_edited")),
        "approved": bool(row.approved_at),
        "confirmed": bool(confirmed and confirmed.id == row.id),
        "confirmed_artifact_id": confirmed.id if confirmed else None,
    }


@router.post("/projects/{project_id}/director/outline/revision", status_code=201)
def revise_outline(project_id: str, body: OutlineRevisionBody, actor: Operator, db: DBSession) -> dict:
    require_project(db, project_id)
    artifact_id = _outline_by_sha(db, project_id, body.expected_sha256)
    row = db.get(AgentArtifact, artifact_id) if artifact_id else None
    if row is None:
        raise HTTPException(409, "STORY_OUTLINE_CHANGED")
    revision = streamline.revise_outline(db, project_id, row, body.markdown, actor=actor)
    db.commit()
    db.refresh(revision)
    return {"artifact_id": revision.id, "sha256": revision.sha256,
            "markdown": streamline.outline_to_markdown(revision.payload_json),
            "questions": list(revision.payload_json.get("questions") or [])}


def _outline_by_sha(db, project_id: str, sha256: str):
    rows = db.scalars(select(AgentArtifact).where(
        AgentArtifact.project_id == project_id, AgentArtifact.kind == "StoryOutline",
        AgentArtifact.sha256 == sha256, AgentArtifact.stale.is_(False),
    ).order_by(AgentArtifact.created_at.desc())).all()
    return rows[0].id if rows else None


class OutlineConfirmBody(APIModel):
    artifact_id: str = Field(min_length=1, max_length=36)
    expected_sha256: str = Field(min_length=64, max_length=64)


@router.post("/projects/{project_id}/director/outline/confirm")
def confirm_outline(project_id: str, body: OutlineConfirmBody, actor: Operator, db: DBSession) -> dict:
    require_project(db, project_id)
    result = streamline.confirm_outline_director(
        db, project_id, body.artifact_id, body.expected_sha256, actor=actor)
    db.commit()
    return result


# ---------------------------------------------------------------------------
# ② Storyboard book: four settings -> preview -> one paired confirmation
# ---------------------------------------------------------------------------


class StoryboardBookBody(APIModel):
    episode_count: int = Field(ge=1, le=200)
    target_seconds: int = Field(default=60, ge=8, le=3600)
    tolerance_seconds: int = Field(default=15, ge=0, le=900)


@router.post("/projects/{project_id}/director/storyboard-book", status_code=201)
def generate_storyboard_book(project_id: str, body: StoryboardBookBody, actor: Operator,
                             db: DBSession) -> dict:
    from shine.api_director import _confirmed_outline, _outline_sections, _section_plan_problems
    from shine.director.contracts import derive_episode_plan_by_count
    from shine.director.models import DirectorEpisodePlan

    require_project(db, project_id)
    outline = _confirmed_outline(db, project_id)
    if outline is None:
        raise HTTPException(422, {"code": "SCRIPT_OUTLINE_NOT_CONFIRMED",
                                  "message": "先确认大纲，再生成分镜书"})
    sections = _outline_sections(outline)
    if not sections:
        raise HTTPException(422, "SCRIPT_OUTLINE_HAS_NO_SECTIONS")
    try:
        payload = derive_episode_plan_by_count(
            sections, episode_count=body.episode_count,
            target_seconds=body.target_seconds, tolerance_seconds=body.tolerance_seconds,
            source={"outline_artifact_id": outline.id, "outline_sha256": outline.sha256},
        )
    except ValueError as exc:
        raise HTTPException(422, {"code": "EPISODE_PLAN_INVALID", "problems": [str(exc)[:400]]}) from exc
    problems = _section_plan_problems(db, project_id, payload)
    if problems:
        raise HTTPException(422, {"code": "EPISODE_PLAN_COVERAGE_INVALID", "problems": problems})
    previous = db.scalars(select(DirectorEpisodePlan).where(
        DirectorEpisodePlan.project_id == project_id)).all()
    from shine.director.contracts import digest_of

    row = DirectorEpisodePlan(
        project_id=project_id, version=max((p.version for p in previous), default=0) + 1,
        status="DRAFT", target_seconds=payload.target_seconds,
        tolerance_seconds=payload.tolerance_seconds,
        payload=payload.model_dump(mode="json"),
        sha256=digest_of(payload.model_dump(mode="json")),
        source="script_sections_by_count", created_by=actor,
    )
    db.add(row)
    db.flush()
    from shine.services import add_audit

    add_audit(db, actor, "STORYBOARD_BOOK_GENERATED", "director_episode_plan", row.id,
              {"version": row.version, "episode_count": body.episode_count,
               "target_seconds": body.target_seconds}, project_id=project_id)
    db.commit()
    db.refresh(row)
    return {"plan_version": row.version, "plan_sha256": row.sha256,
            "preview": streamline.storyboard_book_preview(db, project_id, row.payload)}


class StoryboardBookConfirmBody(APIModel):
    plan_version: int = Field(ge=1)
    expected_sha256: str = Field(min_length=64, max_length=64)
    style: dict[str, Any] | None = None


@router.post("/projects/{project_id}/director/storyboard-book/confirm")
def confirm_storyboard_book(project_id: str, body: StoryboardBookConfirmBody, actor: Operator,
                            db: DBSession) -> dict:
    require_project(db, project_id)
    result = streamline.confirm_storyboard_book(
        db, project_id, version=body.plan_version, expected_sha256=body.expected_sha256,
        style=body.style, actor=actor)
    db.commit()
    return result


# ---------------------------------------------------------------------------
# ③ One generation, per-episode table and confirmation
# ---------------------------------------------------------------------------


class DirectorStoryboardBody(APIModel):
    plan_version: int | None = None


@router.post("/projects/{project_id}/director/storyboard/generate", status_code=202)
def generate_director_storyboard(project_id: str, body: DirectorStoryboardBody, actor: Operator,
                                 db: DBSession) -> dict:
    require_project(db, project_id)
    result = streamline.queue_director_storyboard(db, project_id, actor=actor,
                                                  plan_version=body.plan_version)
    db.commit()
    return result


@router.get("/projects/{project_id}/director/storyboard/state")
def director_storyboard_state(project_id: str, db: DBSession) -> dict:
    require_project(db, project_id)
    return streamline.director_storyboard_state(db, project_id)


@router.get("/projects/{project_id}/director/storyboard/episodes/{episode_id}/shots")
def episode_shots(project_id: str, episode_id: str, db: DBSession) -> dict:
    require_project(db, project_id)
    return streamline.episode_shot_table(db, project_id, episode_id)


class EpisodeRetryBody(APIModel):
    expected_state_version: int = Field(ge=0)


@router.post("/projects/{project_id}/director/storyboard/episodes/{episode_id}/retry", status_code=202)
def retry_episode(project_id: str, episode_id: str, body: EpisodeRetryBody, actor: Operator,
                  db: DBSession) -> dict:
    """Re-queue one blocked episode of the director storyboard run."""
    require_project(db, project_id)
    return streamline.retry_director_episode(
        db, project_id, episode_id, expected_state_version=body.expected_state_version, actor=actor)


class ShotRowEditBody(APIModel):
    """One editable fact of a landed shot row (streamline plan §6.1).

    The editorial length and the cut/continue relation are the director's
    own decisions; everything structural (cast, bindings, style) stays with
    the landing gates.
    """

    expected_revision: int = Field(ge=1)
    duration_target_s: float | None = Field(default=None, ge=4, le=15)
    continuity_mode: Literal["cut", "last_frame"] | None = None


@router.patch("/projects/{project_id}/director/storyboard/shots/{shot_id}")
def edit_shot_row(project_id: str, shot_id: str, body: ShotRowEditBody, actor: Operator,
                  db: DBSession) -> dict:
    from shine.models import Shot
    from shine.schemas import ShotSpec
    from shine.services import revise_shot_record

    project = require_project(db, project_id)
    shot = db.get(Shot, shot_id)
    if shot is None or shot.project_id != project_id:
        raise HTTPException(404, "SHOT_NOT_FOUND")
    if shot.revision != body.expected_revision:
        raise HTTPException(409, {"code": "SHOT_REVISION_CHANGED",
                                  "message": "镜头已更新，请刷新",
                                  "current_revision": shot.revision})
    spec = ShotSpec.model_validate(shot.spec)
    update: dict[str, Any] = {}
    if body.duration_target_s is not None:
        # Every dialogue line must still fit the new editorial length.
        if any(line.end_s > body.duration_target_s for line in spec.dialogue):
            raise HTTPException(422, {"code": "SHOT_DURATION_BELOW_DIALOGUE",
                                      "message": "成片秒数装不下这一镜的台词"})
        update["duration_target_s"] = body.duration_target_s
    if body.continuity_mode is not None:
        continuity = (spec.continuity.model_dump(mode="json")
                      if spec.continuity else {})
        continuity["mode"] = body.continuity_mode
        update["continuity"] = continuity
    if not update:
        return {"shot_id": shot.id, "revision": shot.revision, "changed": False}
    result = revise_shot_record(db, project, shot, spec=spec.model_copy(update=update), actor=actor)
    db.commit()
    return {"shot_id": shot.id, "revision": result["revision"], "changed": True}


class EpisodeConfirmBody(APIModel):
    expected_state_version: int | None = None


@router.post("/projects/{project_id}/director/storyboard/episodes/{episode_id}/confirm")
def confirm_episode(project_id: str, episode_id: str, actor: Operator, db: DBSession,
                    body: EpisodeConfirmBody | None = None) -> dict:
    require_project(db, project_id)
    result = streamline.confirm_episode_shots(db, project_id, episode_id, actor=actor)
    db.commit()
    return result


# ---------------------------------------------------------------------------
# ④ Shot-driven material list (selection/generation reuse the material centre)
# ---------------------------------------------------------------------------


class ShotMaterialsBody(APIModel):
    episode_ids: list[str] | None = Field(default=None, max_length=200)


@router.post("/projects/{project_id}/director/materials/derive")
def derive_materials(project_id: str, body: ShotMaterialsBody, actor: Operator, db: DBSession) -> dict:
    require_project(db, project_id)
    result = streamline.derive_shot_materials(db, project_id, episode_ids=body.episode_ids, actor=actor)
    db.commit()
    return result