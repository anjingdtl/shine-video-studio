"""Director-mode streamline endpoints: the convergent ①–④ path.

These routes compose the existing versioned services into the single-click
path a non-expert walks.  They persist intent and read ledgers; the resident
Worker performs every model call and render.  Expert mode keeps its own
entrances unchanged.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field
from sqlalchemy import select

from shine.api_common import DBSession
from shine.api_operator import Operator
from shine.director import streamline
from shine.models import AgentArtifact, utcnow
from shine.schemas import APIModel
from shine.services import add_audit, require_project

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
        # Keep the typed model: a raw dict here would round-trip through the
        # node's serializer as an unexpected value and lose its field order.
        from shine.schemas import ContinuityMode

        update["continuity"] = spec.continuity.model_copy(
            update={"mode": ContinuityMode(body.continuity_mode)})
    if not update:
        return {"shot_id": shot.id, "revision": shot.revision, "changed": False}
    result = revise_shot_record(db, project, shot, spec=spec.model_copy(update=update), actor=actor)
    db.commit()
    return {"shot_id": shot.id, "revision": result["revision"], "changed": True}


class ShotPromptBody(APIModel):
    """The prompt a person wrote for this shot.

    ``freeze`` makes it the submitted text.  The official H3 guidance is
    reviewed as warnings; nothing here refuses a save because of style.
    """

    prompt: str = Field(min_length=1, max_length=12000)
    mode: str = Field(min_length=1, max_length=16)
    freeze: bool = False
    expected_revision: int | None = Field(default=None, ge=1)


@router.get("/projects/{project_id}/director/storyboard/shots/{shot_id}/prompt")
def shot_prompt(project_id: str, shot_id: str, db: DBSession,
                compiler: int = 0) -> dict:
    """The compiled preview, the current frozen revision, and advisory findings."""
    from shine.director.prompt_revisions import shot_prompt_view
    from shine.models import Shot

    project = require_project(db, project_id)
    shot = db.get(Shot, shot_id)
    if shot is None or shot.project_id != project_id:
        raise HTTPException(404, "SHOT_NOT_FOUND")
    payload = shot_prompt_view(db, project, shot, compiler_preview=bool(compiler))
    db.rollback()
    return payload


@router.post("/projects/{project_id}/director/storyboard/shots/{shot_id}/prompt", status_code=201)
def save_shot_prompt(project_id: str, shot_id: str, body: ShotPromptBody, actor: Operator,
                     db: DBSession) -> dict:
    from shine.director.prompt_revisions import save_shot_prompt as save_prompt
    from shine.models import Shot

    project = require_project(db, project_id)
    shot = db.get(Shot, shot_id)
    if shot is None or shot.project_id != project_id:
        raise HTTPException(404, "SHOT_NOT_FOUND")
    result = save_prompt(
        db, project, shot, prompt=body.prompt, mode=body.mode, freeze=body.freeze,
        expected_revision=body.expected_revision, actor=actor,
    )
    db.commit()
    db.refresh(shot)
    return {**result, "shot_id": shot.id, "shot_revision": shot.revision}


@router.post("/projects/{project_id}/director/storyboard/shots/{shot_id}/prompt/unfreeze")
def unfreeze_shot_prompt(project_id: str, shot_id: str, actor: Operator, db: DBSession) -> dict:
    from shine.director.prompt_revisions import unfreeze_shot_prompt as unfreeze
    from shine.models import Shot

    project = require_project(db, project_id)
    shot = db.get(Shot, shot_id)
    if shot is None or shot.project_id != project_id:
        raise HTTPException(404, "SHOT_NOT_FOUND")
    result = unfreeze(db, project, shot, actor=actor)
    db.commit()
    return result


class RequestedAssetBody(APIModel):
    """A material the person says this shot must contain."""

    family: Literal["character", "scene", "prop"]
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=600)
    carrier: Literal["object", "paper", "screen"] | None = None


@router.post("/projects/{project_id}/director/storyboard/shots/{shot_id}/requested-assets", status_code=201)
def add_requested_asset(project_id: str, shot_id: str, body: RequestedAssetBody,
                        actor: Operator, db: DBSession) -> dict:
    """Record the requirement, then queue one bounded rewrite of this shot."""
    from shine.director.shot_revision import add_requested_asset as add_asset
    from shine.models import Shot

    project = require_project(db, project_id)
    shot = db.get(Shot, shot_id)
    if shot is None or shot.project_id != project_id:
        raise HTTPException(404, "SHOT_NOT_FOUND")
    result = add_asset(db, project, shot, body.model_dump(mode="json"), actor=actor)
    db.commit()
    return result


class SubtitleLineBody(APIModel):
    start_s: float = Field(ge=0, allow_inf_nan=False)
    end_s: float = Field(gt=0, allow_inf_nan=False)
    text: str = Field(min_length=1, max_length=200)


class ShotSubtitleBody(APIModel):
    """Lines relative to this shot; they compile into the external subtitle track."""

    lines: list[SubtitleLineBody] = Field(default_factory=list, max_length=12)
    freeze: bool = False


@router.get("/projects/{project_id}/director/storyboard/episodes/{episode_id}/subtitles")
def episode_shot_subtitles(project_id: str, episode_id: str, db: DBSession) -> dict:
    from shine.director.subtitles import episode_shot_subtitles as view

    project = require_project(db, project_id)
    return view(db, project, episode_id)


@router.put("/projects/{project_id}/director/storyboard/shots/{shot_id}/subtitles")
def save_shot_subtitles(project_id: str, shot_id: str, body: ShotSubtitleBody,
                        actor: Operator, db: DBSession) -> dict:
    from shine.director.subtitles import save_shot_subtitle_lines
    from shine.models import Shot

    project = require_project(db, project_id)
    shot = db.get(Shot, shot_id)
    if shot is None or shot.project_id != project_id:
        raise HTTPException(404, "SHOT_NOT_FOUND")
    result = save_shot_subtitle_lines(
        db, project, shot, [line.model_dump(mode="json") for line in body.lines],
        freeze=body.freeze, actor=actor,
    )
    db.commit()
    return result


@router.post("/projects/{project_id}/director/storyboard/episodes/{episode_id}/subtitles/compile", status_code=201)
def compile_episode_subtitles(project_id: str, episode_id: str, actor: Operator,
                              db: DBSession) -> dict:
    from shine.director.subtitles import compile_episode_subtitle_track

    project = require_project(db, project_id)
    result = compile_episode_subtitle_track(db, project, episode_id, actor=actor)
    db.commit()
    return result


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


# ---------------------------------------------------------------------------
# Unified read-only overview (V2 §5): one projection, project and episode grain
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/director/overview")
def director_overview(project_id: str, db: DBSession, package_id: str | None = None,
                      run_id: str | None = None) -> dict:
    """The only progress aggregate the director page reads.

    Read-only and derived from the real ledgers: a reload returns the same
    answer, and one pending episode never becomes a global stop.
    """
    from shine.director.overview import build_overview

    project = require_project(db, project_id)
    return build_overview(db, project, package_id=package_id, run_id=run_id)


@router.get("/projects/{project_id}/director/episodes/{episode_id}/overview")
def director_episode_overview(project_id: str, episode_id: str, db: DBSession,
                              package_id: str | None = None, run_id: str | None = None) -> dict:
    from shine.director.overview import build_episode_overview

    project = require_project(db, project_id)
    return build_episode_overview(db, project, episode_id, package_id=package_id, run_id=run_id)

# ---------------------------------------------------------------------------
# ⑤⑥ One explicit shoot action (V2 §8): the only main CTA of production
# ---------------------------------------------------------------------------


class ShootBody(APIModel):
    episode_ids: list[str] = Field(min_length=1, max_length=200)
    workflow_profile_id: str | None = Field(default=None, max_length=36)


@router.post("/projects/{project_id}/director/production/start", status_code=201)
def start_director_production(project_id: str, body: ShootBody, request: Request,
                              actor: Operator, db: DBSession) -> dict:
    """Confirm the selected episodes' package and start the real run, once.

    This is the only main production action in director mode.  It reuses the
    package and Run engines: the same selection replays the same run, an
    unfinished episode is a named per-episode refusal, and nothing is silently
    switched to a mock renderer.
    """
    project = require_project(db, project_id)
    result = streamline.start_director_production(
        db, project,
        settings=request.app.state.settings,
        episode_ids=body.episode_ids,
        workflow_profile_id=body.workflow_profile_id,
        actor=actor,
    )
    db.commit()
    return result


# ---------------------------------------------------------------------------
# ④ One material hand-off (V2 §7.3): confirm the decisions and queue the
#    selected candidates under a deterministic request identity
# ---------------------------------------------------------------------------


class MaterialDecisionBody(APIModel):
    requirement_id: str = Field(min_length=1, max_length=36)
    strategy: str = Field(pattern="^(reuse_master|generate|upload|skip|omit_prop)$")
    entity_id: str | None = Field(default=None, max_length=100)
    asset_id: str | None = Field(default=None, max_length=36)


class MaterialCommitBody(APIModel):
    plan_revision_id: str = Field(min_length=1, max_length=36)
    expected_snapshot: str = Field(min_length=64, max_length=64)
    decisions: list[MaterialDecisionBody] = Field(min_length=1, max_length=200)
    queue_generation: bool = True
    notes: str = Field(default="", max_length=600)


@router.post("/projects/{project_id}/director/materials/commit", status_code=201)
def commit_director_materials(project_id: str, body: MaterialCommitBody, actor: Operator,
                              db: DBSession) -> dict:
    """Record the decisions and queue exactly the selected candidates, once.

    The request identity is derived from what the person confirmed (plan
    revision + the exact decision set), never from a clock.  A dropped response
    therefore retries into the same receipt: the same batch, the same
    confirmation, no second round of images.  The confirmation and the batch are
    two steps of one intent; if the batch cannot be queued the confirmation is
    still reported as committed, because it really is, and the response names the
    step that failed.
    """
    from shine.comfy.workflow import graph_hash
    from shine.materialprep import service
    from shine.materialprep.contracts import MaterialConfirmation
    from shine.services import add_audit

    require_project(db, project_id)
    decision_rows = sorted(
        ({"requirement_id": row.requirement_id, "strategy": row.strategy,
          "entity_id": row.entity_id, "asset_id": row.asset_id} for row in body.decisions),
        key=lambda row: row["requirement_id"],
    )
    request_key = "director-materials:" + graph_hash({
        "project": project_id,
        "plan_revision_id": body.plan_revision_id,
        "expected_snapshot": body.expected_snapshot,
        "decisions": decision_rows,
    })
    receipt = _material_receipt(db, project_id, request_key)
    if receipt is not None:
        # 回执命中：确认与批次都已存在，但“回绑镜头”是幂等收尾，补做一次，
        # 让早期只写了需求、没绑镜头的确认也能被这次重放修好。
        _bind_adopted_masters(db, project_id, receipt["confirmation"]["plan_revision_id"], actor)
        db.commit()
        return {**receipt, "replayed": True}

    confirmation_body = MaterialConfirmation.model_validate({
        "plan_revision_id": body.plan_revision_id,
        "expected_snapshot": body.expected_snapshot,
        "decisions": [
            {"requirement_id": row["requirement_id"], "strategy": row["strategy"],
             "entity_id": row["entity_id"], "asset_id": row["asset_id"]}
            for row in decision_rows
        ],
        "notes": body.notes,
    })
    confirmation = service.confirm(db, actor, project_id, confirmation_body)
    generate_ids = [row["requirement_id"] for row in confirmation["decisions"]
                    if row["strategy"] == "generate"]
    batch = None
    batch_error = None
    if body.queue_generation and generate_ids:
        try:
            batch = service.create_batch(
                db,
                project_id,
                confirmation_id=confirmation["confirmation_id"],
                confirmation_hash=confirmation["confirmation_hash"],
                requirement_ids=generate_ids,
                idempotency_key=request_key + ":" + graph_hash(sorted(generate_ids)),
                rights_id=None,
            )
        except HTTPException as exc:
            batch_error = {
                "code": "MATERIAL_BATCH_REFUSED",
                "message": "决策已确认，但候选生成没有排队；按下面的原因处理后重试",
                "detail": exc.detail,
            }
    result = {
        "confirmation": confirmation,
        "generate_count": len(generate_ids),
        "batch": batch,
        "batch_error": batch_error,
        "request_key": request_key,
        "replayed": False,
    }
    bound_shots = _bind_adopted_masters(db, project_id, confirmation["plan_revision_id"], actor)
    add_audit(db, actor, "DIRECTOR_MATERIALS_COMMITTED", "preproduction_plan",
              confirmation["plan_revision_id"],
              {"request_key": request_key,
               "bound_shot_count": bound_shots,
               "confirmation_id": confirmation["confirmation_id"],
               "confirmation_hash": confirmation["confirmation_hash"],
               "generate_count": len(generate_ids),
               "batch": ({k: batch.get(k) for k in ("run_id", "item_count", "status") if k in batch}
                         if batch else None),
               **({"batch_error": batch_error} if batch_error else {})},
              project_id=project_id)
    db.commit()
    return result


def _bind_adopted_masters(db, project_id: str, plan_id: str, actor: str) -> int:
    """Bind every confirmed "reuse this master" onto the shots that asked for it.

    A confirmed requirement is not yet a shot binding: the shot table and the
    production readiness read ``shot.bindings``, not the material list.  Without
    this step a person who just confirmed "reuse this master" still sees the
    shot as missing its reference and cannot start.  The step is idempotent, so
    a replayed confirmation repairs a half-finished hand-off.  Shots that
    already own a current passed take are never touched: adopting a master for a
    new episode can never invalidate an episode that already shipped.
    """
    from sqlalchemy import select as sa_select

    from shine.comfyops.models import AssetRequirement
    from shine.models import Asset, Job, Shot
    from shine.schemas import ReferenceUse, ShotBindingInput
    from shine.services import require_project, set_shot_bindings

    project = require_project(db, project_id)
    bound = 0
    rows = db.scalars(sa_select(AssetRequirement).where(
        AssetRequirement.plan_id == plan_id,
        AssetRequirement.project_id == project_id,
    )).all()
    for requirement in rows:
        payload = dict(requirement.payload or {})
        if payload.get("strategy") != "reuse_master" or payload.get("family") != "character":
            continue
        asset_id = payload.get("bound_asset_id")
        entity = payload.get("entity_id") or ""
        if not asset_id or not entity:
            continue
        asset = db.get(Asset, asset_id)
        if asset is None or not asset.approved or not asset.locked:
            continue
        for shot_id in payload.get("used_shot_ids") or []:
            shot = db.get(Shot, shot_id)
            if shot is None or shot.project_id != project_id:
                continue
            if entity not in list((shot.spec or {}).get("characters") or []):
                continue
            if any(link.asset_id == asset.id for link in shot.bindings):
                continue
            passed = db.scalar(sa_select(Job).where(
                Job.shot_id == shot.id, Job.status == "PASSED", Job.stale.is_(False),
                Job.shot_revision == shot.revision,
            ))
            if passed is not None:
                continue
            inputs = [
                ShotBindingInput(asset_id=link.asset_id, use_as=ReferenceUse(link.use_as),
                                 role=link.role, ordinal=index)
                for index, link in enumerate(shot.bindings)
            ]
            inputs.append(ShotBindingInput(asset_id=asset.id, use_as=ReferenceUse.SUBJECT,
                                           role=entity, ordinal=len(inputs)))
            set_shot_bindings(db, project, shot, inputs, actor=actor)
            bound += 1
    return bound


def _material_receipt(db, project_id: str, request_key: str) -> dict | None:
    """The receipt of this exact commit, when it already happened.

    A lost HTTP answer must not turn into a second confirmation or a second
    image batch.  The audit row is the existing ledger for that intent; reading
    it needs no new table and no second state machine.
    """
    from shine.models import AuditEvent

    rows = db.scalars(select(AuditEvent).where(
        AuditEvent.project_id == project_id,
        AuditEvent.event_type == "DIRECTOR_MATERIALS_COMMITTED",
    ).order_by(AuditEvent.created_at.desc()).limit(40)).all()
    for row in rows:
        payload = dict(row.payload or {})
        if payload.get("request_key") == request_key:
            return {
                "confirmation": {
                    "plan_revision_id": row.entity_id,
                    "confirmation_id": payload.get("confirmation_id"),
                    "confirmation_hash": payload.get("confirmation_hash"),
                    "decisions": [],
                    "status": "CONFIRMED",
                },
                "generate_count": payload.get("generate_count") or 0,
                "batch": payload.get("batch"),
                "batch_error": payload.get("batch_error"),
                "request_key": request_key,
            }
    return None


class MaterialPromptBody(APIModel):
    prompt: str = Field(min_length=1, max_length=4000)
    negative_prompt: str = Field(default="", max_length=1200)


def _qwen_prompt_warnings(prompt: str, negative_prompt: str) -> list[str]:
    """Advisory findings for a hand-written image prompt (the standard is guidance)."""
    import re

    from shine.materialprep import qwen_image_standard as standard
    from shine.materialprep.image_prompts import (
        MAX_COMPOSED_NEGATIVE_CHARS,
        MAX_COMPOSED_PROMPT_CHARS,
    )

    warnings: list[str] = []
    slogan_hit = [
        slogan for slogan in standard.QUALITY_SLOGANS
        if re.search(r"(?i)\b" + re.escape(slogan) + r"\b", prompt)
    ]
    if slogan_hit:
        warnings.append("提示词包含质量口号（无画面信息）：" + "、".join(slogan_hit[:6]))
    if not standard.is_prompt_language(prompt):
        warnings.append("提示词含中文；Qwen-Image-2.1 按英文自然句读图，中文通常会被削弱")
    if len(prompt) > MAX_COMPOSED_PROMPT_CHARS:
        warnings.append(f"提示词 {len(prompt)} 字符，超过建议上限 {MAX_COMPOSED_PROMPT_CHARS}")
    if len(negative_prompt) > MAX_COMPOSED_NEGATIVE_CHARS:
        warnings.append(f"负向提示词 {len(negative_prompt)} 字符，超过建议上限 {MAX_COMPOSED_NEGATIVE_CHARS}")
    if not prompt.strip().endswith((".", "!", "?")):
        warnings.append("建议用完整句子收尾；Qwen-Image-2.1 按自然句理解画面")
    return warnings


def _requirement_or_404(db, project_id: str, requirement_id: str):
    from shine.comfyops.models import AssetRequirement

    row = db.get(AssetRequirement, requirement_id)
    if row is None or row.project_id != project_id:
        raise HTTPException(404, "MATERIAL_REQUIREMENT_NOT_FOUND")
    return row


def _composed_requirement_prompt(db, requirement) -> tuple[str, str, str | None]:
    """The prompt the renderer would use now, plus the reason it is unavailable.

    The preview and the render share one composer, so what the person edits is
    what the renderer sends.
    """
    from shine.comfyops.models import PreProductionPlan
    from shine.materialprep.contracts import MaterialImageBrief, MaterialVisualDirection
    from shine.materialprep.models import MaterialItem
    from shine.materialprep.prompt_source import (
        PromptSourceError,
        compose_requirement_prompt,
        film_style_for,
        frozen_prompt,
    )
    from shine.models import StyleVersion

    payload = dict(requirement.payload or {})
    item = db.scalars(select(MaterialItem).where(
        MaterialItem.project_id == requirement.project_id,
        MaterialItem.requirement_id == requirement.id,
    ).order_by(MaterialItem.created_at.desc())).first()
    brief_raw = ((item.payload or {}).get("brief") if item is not None else None) or payload.get("brief")
    plan = db.get(PreProductionPlan, requirement.plan_id) if requirement.plan_id else None
    direction = MaterialVisualDirection.model_validate(
        (plan.payload or {}).get("visual_direction") or {} if plan is not None else {})
    style = db.scalars(select(StyleVersion).where(
        StyleVersion.project_id == requirement.project_id,
        StyleVersion.frozen_at.is_not(None),
    ).order_by(StyleVersion.version.desc())).first()
    try:
        composed = compose_requirement_prompt(
            family=str(payload.get("family") or ""),
            brief=MaterialImageBrief.model_validate(brief_raw) if brief_raw else None,
            visual_direction=direction,
            requirement_name=str(payload.get("entity_id") or ""),
            carrier=payload.get("carrier"),
            film_style=film_style_for((style.snapshot if style is not None else {}) or {}),
            description=str(payload.get("description") or ""),
            manual=frozen_prompt(payload),
        )
    except PromptSourceError as exc:
        code = "MATERIAL_BRIEF_NOT_GENERATED" if exc.code == "MATERIAL_BRIEF_MISSING" else exc.code
        return "", "", code
    except ValueError:
        return "", "", "MATERIAL_BRIEF_INVALID"
    return composed.prompt, composed.negative_prompt, None


@router.get("/projects/{project_id}/director/materials/requirements/{requirement_id}/prompt")
def material_requirement_prompt(project_id: str, requirement_id: str, db: DBSession) -> dict:
    """The frozen prompt, or the prompt the current brief would compose."""
    from shine.materialprep import qwen_image_standard as standard

    require_project(db, project_id)
    requirement = _requirement_or_404(db, project_id, requirement_id)
    payload = dict(requirement.payload or {})
    frozen = dict(payload.get("manual_prompt") or {})
    prompt, negative, blocked = _composed_requirement_prompt(db, requirement)
    if frozen.get("prompt"):
        prompt, negative = frozen["prompt"], frozen.get("negative_prompt") or ""
        source = "frozen"
    elif prompt:
        source = "composed"
    else:
        source = "empty"
    return {
        "requirement_id": requirement.id,
        "family": payload.get("family"),
        "entity_id": payload.get("entity_id"),
        "description": payload.get("description") or "",
        "prompt": prompt,
        "negative_prompt": negative,
        "source": source,
        "blocked": blocked,
        "frozen": ({
            "sha256": frozen.get("sha256"),
            "frozen_at": frozen.get("frozen_at"),
            "frozen_by": frozen.get("frozen_by"),
            "chars": len(frozen.get("prompt") or ""),
        } if frozen.get("prompt") else None),
        "warnings": _qwen_prompt_warnings(prompt, negative) if prompt else [],
        "standard": {
            "standard_id": standard.STANDARD_ID,
            "standard_version": standard.STANDARD_VERSION,
            "layer_order": list(standard.LAYER_ORDER),
            "quality_slogans": list(standard.QUALITY_SLOGANS),
        },
    }


@router.put("/projects/{project_id}/director/materials/requirements/{requirement_id}/prompt")
def freeze_material_requirement_prompt(project_id: str, requirement_id: str,
                                       body: MaterialPromptBody, actor: Operator,
                                       db: DBSession) -> dict:
    """Freeze a hand-written prompt; the worker then skips the brief and uses it."""
    import hashlib

    from shine.comfy.workflow import graph_hash
    from shine.materialprep import qwen_image_standard as standard

    require_project(db, project_id)
    requirement = _requirement_or_404(db, project_id, requirement_id)
    prompt = body.prompt.strip()
    if not prompt:
        raise HTTPException(422, {"code": "MATERIAL_PROMPT_EMPTY", "message": "提示词不能为空"})
    warnings = _qwen_prompt_warnings(prompt, body.negative_prompt)
    payload = dict(requirement.payload or {})
    payload["manual_prompt"] = {
        "prompt": prompt,
        "negative_prompt": body.negative_prompt.strip(),
        "sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "standard_version": standard.STANDARD_VERSION,
        "frozen_at": utcnow().isoformat() + "Z",
        "frozen_by": actor,
        "warnings": warnings,
    }
    requirement.payload = payload
    requirement.sha256 = graph_hash(payload)
    add_audit(db, actor, "MATERIAL_PROMPT_FROZEN", "asset_requirement", requirement.id,
              {"family": payload.get("family"), "entity_id": payload.get("entity_id"),
               "prompt_sha256": payload["manual_prompt"]["sha256"],
               "warnings": len(warnings)},
              project_id=project_id)
    db.commit()
    return {
        "requirement_id": requirement.id,
        "prompt": prompt,
        "negative_prompt": payload["manual_prompt"]["negative_prompt"],
        "frozen": payload["manual_prompt"],
        "warnings": warnings,
    }


@router.delete("/projects/{project_id}/director/materials/requirements/{requirement_id}/prompt")
def unfreeze_material_requirement_prompt(project_id: str, requirement_id: str,
                                         actor: Operator, db: DBSession) -> dict:
    """Return this requirement to the producer's brief."""
    from shine.comfy.workflow import graph_hash

    require_project(db, project_id)
    requirement = _requirement_or_404(db, project_id, requirement_id)
    payload = dict(requirement.payload or {})
    removed = payload.pop("manual_prompt", None)
    if removed is not None:
        requirement.payload = payload
        requirement.sha256 = graph_hash(payload)
        add_audit(db, actor, "MATERIAL_PROMPT_UNFROZEN", "asset_requirement", requirement.id,
                  {"prompt_sha256": removed.get("sha256")}, project_id=project_id)
    db.commit()
    return {"requirement_id": requirement.id, "unfrozen": removed is not None}


@router.get("/projects/{project_id}/director/materials/requirements")
def director_material_requirements(project_id: str, db: DBSession,
                                   episode_ids: str | None = None) -> dict:
    """The current requirement list with its real use, without re-deriving it.

    Reading never buys a model call: it answers with the stored ledger rows and
    their shot usage.  The plan is only created by the explicit derive action.
    """
    from shine.comfyops.models import AssetRequirement
    from shine.comfyops.preproduction import current_material_plan
    from shine.materialprep.service import confirmation_snapshot_hash

    require_project(db, project_id)
    plan = current_material_plan(db, project_id)
    if plan is None:
        return {"plan_revision_id": None, "expected_snapshot": None, "requirements": []}
    rows = db.scalars(select(AssetRequirement).where(
        AssetRequirement.plan_id == plan.id,
        AssetRequirement.project_id == project_id,
    )).all()
    wanted = {item for item in (episode_ids or "").split(",") if item}
    requirements = []
    for row in rows:
        payload = dict(row.payload or {})
        used_episodes = list(payload.get("used_episodes") or [])
        if wanted and not wanted.intersection(used_episodes):
            continue
        # ``bound_asset_id`` is what the person actually confirmed (including a
        # freshly uploaded file); ``matched_master_id`` is only the derivation's
        # suggestion.  The confirmed binding wins, so the row's thumbnail and
        # status update the moment an upload is adopted.
        matched_id = payload.get("bound_asset_id") or payload.get("matched_master_id")
        manual_prompt = dict(payload.get("manual_prompt") or {})
        requirements.append({
            "requirement_id": row.id,
            "family": payload.get("family"),
            "entity_id": payload.get("entity_id"),
            "name": payload.get("entity_id"),
            "description": payload.get("description") or "",
            "critical": bool(payload.get("critical", True)),
            "optional": bool(payload.get("optional", False)),
            "user_specified": bool(payload.get("user_specified")),
            "strategy": payload.get("strategy"),
            "manual_prompt": ({
                "sha256": manual_prompt.get("sha256"),
                "chars": len(str(manual_prompt.get("prompt") or "")),
                "frozen_at": manual_prompt.get("frozen_at"),
            } if manual_prompt.get("prompt") else None),
            "variants": list(payload.get("variants") or []),
            "used_shot_ids": list(payload.get("used_shot_ids") or []),
            "used_shot_count": len(payload.get("used_shot_ids") or []),
            "used_episodes": used_episodes,
            "matched_master": ({"asset_id": matched_id,
                                "sha256": payload.get("bound_asset_sha256")}
                               if matched_id else None),
            "status": "matched" if matched_id else "missing",
        })
    return {
        "plan_revision_id": plan.id,
        "expected_snapshot": confirmation_snapshot_hash(plan),
        "requirements": requirements,
    }
