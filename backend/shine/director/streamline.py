"""The director-mode streamline: one editable outline, one storyboard book,
one per-episode shot table, one shot-driven material list.

This layer never invents a second execution engine.  It composes the existing
versioned artifacts, the episode-plan contract, the five-role crew chain, the
apply-camera landing gate, the material centre and the director package into a
path a non-expert can walk: import -> edit outline -> confirm -> four settings ->
storyboard book -> confirm -> generate -> per-episode confirm -> materials ->
package -> produce -> deliver.  Expert mode keeps every original entrance.

Design rules carried over from the build plan:

* the user's confirmed text is the downstream content authority; a model never
  rewrites an already-confirmed outline;
* questions are hints shown beside the body, not a mandatory second gate;
* episode count is the priority, seconds are a target with a visible deviation;
* a bad answer keeps its usable content; only the affected unit pauses;
* an UNKNOWN provider outcome is reconciled, never blindly replayed.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select, text

from shine.h3.rulebook import digest
from shine.models import (
    AgentArtifact,
    AgentTaskRecord,
    ProjectDocument,
    Shot,
    WorkflowProfile,
    utcnow,
)
from shine.services import add_audit, require_project

logger = logging.getLogger(__name__)

DIRECTOR_STORYBOARD_RUN = "director_storyboard_run"
#: The outline body is simple Markdown with these fixed anchors.  The round trip
#: is deterministic: rendering then parsing returns the same structure, so a
#: user who edits nothing produces a byte-identical payload projection.
OUTLINE_SUMMARY_HEADING = "## 故事梗概"
OUTLINE_STORY_LINE_HEADING = "## 故事主线"
OUTLINE_TIMELINE_HEADING = "## 时间线"
OUTLINE_RELATIONSHIPS_HEADING = "## 人物关系"
OUTLINE_QUESTIONS_HEADING = "## 悬念与疑问（提示，可保留）"


# ---------------------------------------------------------------------------
# ① Editable outline: a deterministic Markdown projection of the payload
# ---------------------------------------------------------------------------


def _bullet_list(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items if str(item).strip())


def outline_to_markdown(payload: dict[str, Any]) -> str:
    """Render the confirmed-outline structure as the editable body."""
    parts = [OUTLINE_SUMMARY_HEADING, "", str(payload.get("summary") or "").strip(), ""]
    for heading, key in (
        (OUTLINE_STORY_LINE_HEADING, "story_line"),
        (OUTLINE_TIMELINE_HEADING, "timeline"),
        (OUTLINE_RELATIONSHIPS_HEADING, "relationships"),
        (OUTLINE_QUESTIONS_HEADING, "questions"),
    ):
        parts += [heading, "", _bullet_list(list(payload.get(key) or [])), ""]
    return "\n".join(parts).strip() + "\n"


def _split_bullets(block: str) -> list[str]:
    items = []
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        stripped = re.sub(r"^[-*•]\s+", "", stripped)
        stripped = re.sub(r"^\d+[.)、]\s+", "", stripped)
        if stripped:
            items.append(stripped)
    return items


def markdown_to_outline(markdown: str, base: dict[str, Any]) -> dict[str, Any]:
    """Parse the edited body back into the outline structure.

    Only the five anchored sections are read; everything else in the payload
    (document provenance, production sections, characters, locked facts, leaf
    artifacts) is carried over untouched.  A missing anchor keeps the base
    value, so deleting a heading never silently erases confirmed content.
    """
    text = markdown or ""
    headings = [
        OUTLINE_SUMMARY_HEADING,
        OUTLINE_STORY_LINE_HEADING,
        OUTLINE_TIMELINE_HEADING,
        OUTLINE_RELATIONSHIPS_HEADING,
        OUTLINE_QUESTIONS_HEADING,
    ]
    positions = []
    for heading in headings:
        match = re.search(r"^" + re.escape(heading) + r"\s*$", text, flags=re.MULTILINE)
        positions.append((heading, match.start() if match else None, match.end() if match else None))
    blocks: dict[str, str] = {}
    ordered = [(h, s, e) for h, s, e in positions if s is not None]
    ordered.sort(key=lambda item: item[1])
    for index, (heading, _start, end) in enumerate(ordered):
        next_start = ordered[index + 1][1] if index + 1 < len(ordered) else len(text)
        blocks[heading] = text[end:next_start]
    payload = dict(base)
    if OUTLINE_SUMMARY_HEADING in blocks:
        payload["summary"] = blocks[OUTLINE_SUMMARY_HEADING].strip()
    for heading, key in (
        (OUTLINE_STORY_LINE_HEADING, "story_line"),
        (OUTLINE_TIMELINE_HEADING, "timeline"),
        (OUTLINE_RELATIONSHIPS_HEADING, "relationships"),
        (OUTLINE_QUESTIONS_HEADING, "questions"),
    ):
        if heading in blocks:
            payload[key] = _split_bullets(blocks[heading])
    return payload


def revise_outline(db, project_id: str, artifact: AgentArtifact, markdown: str, *, actor: str) -> AgentArtifact:
    """Create a new outline revision from the user's edited body.

    The new artifact keeps the document provenance and the source-derived
    production sections; only the narrative projection changes.  It is marked
    user-edited and links the revision it came from, so the version chain and
    the downstream invalidation semantics stay exactly as the expert path's.
    """
    base = dict(artifact.payload_json)
    payload = markdown_to_outline(markdown, base)
    if not str(payload.get("summary") or "").strip():
        raise HTTPException(422, "OUTLINE_BODY_EMPTY")
    # Bound the lists to the contract the merge step enforces.
    for key, limit in (("story_line", 12), ("timeline", 16), ("relationships", 24), ("questions", 12)):
        payload[key] = [str(item)[:300] for item in (payload.get(key) or [])][:limit]
    payload["summary"] = str(payload["summary"])[:1200]
    payload["user_edited"] = True
    payload["revision_of"] = artifact.id
    payload["edited_at"] = utcnow().isoformat()
    revision = AgentArtifact(
        project_id=project_id,
        kind="StoryOutline",
        schema_version=artifact.schema_version,
        payload_json=payload,
        sha256=digest(payload),
        producer_role="local-user",
        source_task_id=artifact.source_task_id,
        input_revision_ids=[artifact.id],
    )
    db.add(revision)
    db.flush()
    add_audit(db, actor, "STORY_OUTLINE_EDITED", "agent_artifact", revision.id,
              {"revision_of": artifact.id, "sha256": revision.sha256}, project_id=project_id)
    return revision


def confirm_outline_director(db, project_id: str, artifact_id: str, expected_sha256: str, *, actor: str) -> dict:
    """One-click director confirmation of the (possibly edited) outline.

    Questions are hints here: the person sees them beside the body and the
    single confirmation acknowledges the exact list shown, recorded in the
    audit.  The shared confirmation function still owns the sha gate, the
    document check and the downstream invalidation, so expert and director
    entrances can never diverge on what "confirmed" means.
    """
    from shine.materialprep.story import confirm_outline_artifact

    row = db.get(AgentArtifact, artifact_id)
    if row is None or row.project_id != project_id or row.kind != "StoryOutline":
        raise HTTPException(404, "STORY_OUTLINE_NOT_FOUND")
    questions = list(row.payload_json.get("questions") or [])
    acknowledge = digest({"artifact": row.id, "questions": questions}) if questions else None
    result = confirm_outline_artifact(
        db, project_id, row,
        expected_sha256=expected_sha256,
        acknowledge_questions_sha256=acknowledge,
        actor=actor,
    )
    project = require_project(db, project_id)
    document_id = row.payload_json.get("document_id")
    if document_id and project.current_document_id != document_id:
        project.current_document_id = document_id
    return result


# ---------------------------------------------------------------------------
# ② Storyboard book: a readable view of the episode plan + paired style confirm
# ---------------------------------------------------------------------------


def _section_beats_view(db, project_id: str, section: dict[str, Any]) -> dict[str, Any]:
    """Beats / key dialogue for one section, from a confirmed scene script when
    one exists, otherwise an honest empty view (the outline still names it)."""
    view = {
        "production_section_id": section["production_section_id"],
        "order": section.get("order"),
        "title": section.get("title") or "",
        "estimated_seconds": float(section.get("estimated_duration_s") or 0.0),
        "beats": [],
        "dialogue": [],
        "source": "outline_section",
    }
    # A four-stage scene script, when present, carries the real beats/dialogue.
    scripts = db.scalars(select(AgentArtifact).where(
        AgentArtifact.project_id == project_id,
        AgentArtifact.kind == "FourStageSceneScript",
        AgentArtifact.stale.is_(False),
    )).all()
    for script in scripts:
        payload = script.payload_json or {}
        if payload.get("section_id") != section["production_section_id"]:
            continue
        view["source"] = "scene_script"
        view["beats"] = list(payload.get("beats") or [])[:40]
        dialogue = []
        for beat in payload.get("beats") or []:
            for line in beat.get("dialogue") or []:
                dialogue.append({"speaker": line.get("speaker_id"), "text": line.get("text")})
        view["dialogue"] = dialogue[:60]
        break
    return view


def storyboard_book_preview(db, project_id: str, plan_payload: dict[str, Any]) -> dict[str, Any]:
    """The readable storyboard book: episodes -> sections -> beats/dialogue."""
    from shine.director.contracts import EpisodePlanPayload

    payload = EpisodePlanPayload.model_validate(plan_payload)
    outline = _confirmed_outline_for_plan(db, project_id, payload)
    sections_by_id = {
        str(row["production_section_id"]): row
        for row in ((outline.payload_json or {}).get("production_sections") or [])
    } if outline else {}
    episodes = []
    total = 0.0
    for episode in payload.episodes:
        section_views = [
            _section_beats_view(db, project_id, sections_by_id[sid])
            for sid in episode.section_ids if sid in sections_by_id
        ]
        planned = float(episode.planned_seconds)
        total += planned
        episodes.append({
            "episode_id": episode.episode_id,
            "order": episode.order,
            "title": episode.title,
            "planned_seconds": planned,
            "deviation_seconds": round(planned - payload.target_seconds, 3),
            "boundary_note": episode.boundary_note,
            "section_count": len(episode.section_ids),
            "sections": section_views,
        })
    return {
        "mode": payload.mode,
        "target_seconds": payload.target_seconds,
        "tolerance_seconds": payload.tolerance_seconds,
        "episode_count": len(episodes),
        "total_planned_seconds": round(total, 3),
        "episodes": episodes,
    }


def _confirmed_outline_for_plan(db, project_id: str, payload):
    from shine.api_director import _confirmed_outline

    source = payload.source or {}
    if source.get("outline_artifact_id"):
        row = db.get(AgentArtifact, source["outline_artifact_id"])
        if row is not None and row.project_id == project_id:
            return row
    return _confirmed_outline(db, project_id)


def confirm_storyboard_book(
    db, project_id: str, *, version: int, expected_sha256: str,
    style: dict[str, Any] | None, actor: str,
) -> dict[str, Any]:
    """Confirm the episode plan and freeze the chosen style as one decision.

    Both halves run in the caller's single transaction: a style failure rolls
    the plan confirmation back, so the workbench never lands a confirmed plan
    against an unfrozen style.  The style half reuses the director-style draft
    adoption and the existing freeze service; the plan half reuses the existing
    coverage-checked confirmation.
    """
    from shine.director.contracts import EpisodePlanPayload
    from shine.director.models import DirectorEpisodePlan

    project = require_project(db, project_id)
    row = db.scalars(select(DirectorEpisodePlan).where(
        DirectorEpisodePlan.project_id == project_id, DirectorEpisodePlan.version == version,
    )).first()
    if row is None:
        raise HTTPException(404, "EPISODE_PLAN_NOT_FOUND")
    if expected_sha256 and row.sha256 != expected_sha256:
        raise HTTPException(409, "EPISODE_PLAN_CHANGED")
    style_result = None
    if style:
        style_result = _apply_and_freeze_style(db, project, style, actor=actor)
    # Reuse the expert confirmation logic (coverage + supersede + audit).
    from shine.api_director import _section_plan_problems

    payload = EpisodePlanPayload.model_validate(row.payload)
    if payload.mode == "sections":
        problems = _section_plan_problems(db, project_id, payload)
    else:
        from shine.director.contracts import validate_episode_coverage

        shots = list(db.scalars(select(Shot).where(Shot.project_id == project_id)).all())
        problems = validate_episode_coverage(payload, [shot.id for shot in shots])
    if problems:
        raise HTTPException(409, {"code": "EPISODE_PLAN_COVERAGE_INVALID", "problems": problems})
    superseded = 0
    for other in db.scalars(select(DirectorEpisodePlan).where(
        DirectorEpisodePlan.project_id == project_id,
        DirectorEpisodePlan.status == "CONFIRMED",
        DirectorEpisodePlan.id != row.id,
    )).all():
        other.status = "SUPERSEDED"
        superseded += 1
    row.status = "CONFIRMED"
    row.confirmed_by = actor
    row.confirmed_at = utcnow()
    add_audit(db, actor, "EPISODE_PLAN_CONFIRMED", "director_episode_plan", row.id,
              {"version": row.version, "sha256": row.sha256, "superseded": superseded,
               "streamline": True, "style_frozen": bool(style_result)}, project_id=project_id)
    return {"plan_version": row.version, "plan_sha256": row.sha256,
            "style": style_result, "superseded": superseded}


def _apply_and_freeze_style(db, project, style: dict[str, Any], *, actor: str) -> dict[str, Any]:
    """Adopt a director template + film type into the draft, then freeze it.

    Mirrors the apply-draft endpoint's contract (template selection, film type
    carried as a project-level H3 contract) and then calls the same freeze
    service the expert path uses.  An identical frozen style is a no-op success
    rather than an error, so re-confirming a book never fails on style alone.
    """
    from shine.director_styles.catalog import DirectorStyleCatalog, DirectorStyleCatalogError
    from shine.director_styles.service import make_binding, register_profile_revision
    from shine.models import StyleVersion
    from shine.schemas import FilmType
    from shine.services import freeze_style, update_style_draft

    profile_id = style.get("profile_id")
    strength = style.get("strength") or "balanced"
    film_type = style.get("film_type")
    dialogue_ratio = style.get("dialogue_ratio_percent")
    narration_level = style.get("narration_level")
    if not profile_id:
        raise HTTPException(422, "STYLE_PROFILE_REQUIRED")
    try:
        entry = DirectorStyleCatalog().get(profile_id, style.get("profile_version"))
    except DirectorStyleCatalogError as exc:
        raise HTTPException(404, str(exc)) from exc
    frozen_current = (
        db.get(StyleVersion, project.frozen_style_version_id)
        if project.frozen_style_version_id else None
    )
    current = project.style_draft or (frozen_current.snapshot if frozen_current else {}) or {}
    style_bible = entry.profile.style_bible.model_copy(update={
        "dialogue_ratio_percent": dialogue_ratio if dialogue_ratio is not None
        else current.get("dialogue_ratio_percent"),
        # Narration is a storyboard-style decision: it tells the crew how much
        # voice-over the episode may use.  It is guidance, not a render gate.
        "narration_level": narration_level if narration_level in {"none", "low", "high"}
        else current.get("narration_level"),
        "film_type": FilmType(film_type) if film_type else (
            FilmType(current["film_type"]) if current.get("film_type") else None),
    })
    register_profile_revision(db, entry)
    update_style_draft(db, project, style_bible, preserve_director_style=False)
    project.director_style_draft = make_binding(entry, strength, style_bible)
    add_audit(db, actor, "DIRECTOR_STYLE_DRAFT_APPLIED", "project", project.id,
              {"profile_id": entry.profile.id, "strength": strength,
               "film_type": film_type, "streamline": True}, project_id=project.id)
    try:
        frozen, impact = freeze_style(db, project, approval_note="streamline storyboard book", actor=actor)
    except HTTPException as exc:
        # Identical to the current frozen version: the book confirmation still
        # succeeds; the style is already frozen exactly as chosen.
        if exc.status_code == 409 and "identical" in str(exc.detail):
            frozen = db.get(StyleVersion, project.frozen_style_version_id)
            impact = {"affected_shots": [], "noop": True}
        else:
            raise
    return {"style_version_id": frozen.id, "sha256": frozen.sha256,
            "film_type": (frozen.snapshot or {}).get("film_type"),
            "impact": impact}


# ---------------------------------------------------------------------------
# ③ One background storyboard run, per-episode landing and confirmation
# ---------------------------------------------------------------------------



def ensure_project_rulepack(db, project_id: str) -> str | None:
    """Pin the built-in H3 rule pack on first use in the director path.

    The rule pack is not a creative decision: it is one immutable built-in
    version the compiler always follows, and a project can only ever pin it
    once.  Making a non-expert discover a "freeze the rule pack first" error at
    the storyboard step is exactly the kind of cross-stage stop the V2 plan
    removes, so the director path pins it here, once, and records the pin in the
    project's own ledger.  An existing lock is never replaced.
    """
    from shine.h3.rulebook import H3RulebookRegistry

    registry = H3RulebookRegistry(None)
    try:
        existing = registry.for_project(db, project_id)
    except ValueError:
        # An invalid/tampered pin is not silently replaced: the compiler gate
        # must still refuse it, and the person sees the real diagnosis.
        return None
    if existing is not None:
        return existing.id
    try:
        lock = registry.lock(db, project_id)
    except ValueError:
        return None
    add_audit(db, "local-user", "PROJECT_RULEPACK_PINNED", "project", project_id,
              {"rulepack_id": lock.rulepack_id, "sha256": lock.rulepack_hash,
               "streamline": True}, project_id=project_id)
    return lock.rulepack_id


def queue_director_storyboard(db, project_id: str, *, actor: str, plan_version: int | None = None) -> dict:
    """Queue one durable run that storyboards every section of the confirmed
    episode plan, in script order, landing each unit's shots before the next.

    Idempotent: the same plan + style + asset manifest reuses a live run instead
    of queueing a second one.  The resident Worker advances it one unit per tick,
    so a closed browser never stops it and a crash resumes at the first
    unfinished unit.
    """
    from shine.agents.film import asset_context
    from shine.api_director import _confirmed_outline
    from shine.director.contracts import EpisodePlanPayload
    from shine.materialprep import story
    from shine.materialprep.source_spans import segment_document

    project = require_project(db, project_id)
    if not project.frozen_style_version_id:
        raise HTTPException(409, "STORYBOARD_STYLE_NOT_FROZEN")
    # The built-in rule pack is pinned here rather than left as a surprise
    # refusal inside the five-role chain (FREEZE_PROJECT_RULEPACK_FIRST).
    ensure_project_rulepack(db, project_id)
    outline = _confirmed_outline(db, project_id)
    if outline is None:
        raise HTTPException(409, "SCRIPT_OUTLINE_NOT_CONFIRMED")
    plan = _latest_plan(db, project_id, plan_version)
    if plan is None or plan.status != "CONFIRMED":
        raise HTTPException(409, "EPISODE_PLAN_NOT_CONFIRMED")
    payload = EpisodePlanPayload.model_validate(plan.payload)
    if payload.mode != "sections":
        raise HTTPException(409, "EPISODE_PLAN_NOT_SCRIPT_FIRST")
    document = db.get(ProjectDocument, outline.payload_json["document_id"])
    if document is None or document.sha256 != outline.payload_json["document_sha256"]:
        raise HTTPException(409, "STORY_DOCUMENT_CHANGED")
    sections_by_id = {str(s["production_section_id"]): s
                      for s in outline.payload_json["production_sections"]}
    asset_hash = digest(asset_context(db, project_id))
    units = []
    for episode in payload.episodes:
        for section_id in episode.section_ids:
            section = sections_by_id.get(section_id)
            if section is None:
                raise HTTPException(409, "EPISODE_PLAN_SECTION_UNKNOWN")
            raw = document.text[section["start_offset"]:section["end_offset"]]
            offset = section["start_offset"]
            for fragment in segment_document(raw, max_scene_chars=1200):
                units.append({
                    "episode_id": episode.episode_id,
                    "production_section_id": section_id,
                    "section_order": section.get("order"),
                    "start_offset": offset,
                    "end_offset": offset + len(fragment.text),
                })
                offset += len(fragment.text)
    identity = digest({"tool": DIRECTOR_STORYBOARD_RUN, "outline": outline.sha256,
                       "plan": plan.sha256, "units": units,
                       "style": project.frozen_style_version_id, "assets": asset_hash})
    old = db.scalar(select(AgentTaskRecord).where(AgentTaskRecord.request_identity == identity))
    if old and old.status in story.REUSABLE_TASK_STATUSES:
        return {"task_id": old.id, "status": old.status, "replayed": True}
    row = AgentTaskRecord(
        project_id=project_id, role_id=DIRECTOR_STORYBOARD_RUN, request_identity=identity,
        input_hash=identity, asset_manifest_hash=asset_hash,
        style_version_id=project.frozen_style_version_id,
        rulepack_id=story.VERSION, rulepack_hash=digest(story.VERSION),
        model_profile_id="crew-role-routing", model_id="existing-film-crew",
        schema_version="1.0", status="QUEUED",
        trace={"document_id": document.id, "document_sha256": document.sha256,
               "outline_artifact_id": outline.id, "outline_sha256": outline.sha256,
               "plan_version": plan.version, "plan_sha256": plan.sha256,
               "style_version_id": project.frozen_style_version_id, "asset_manifest_hash": asset_hash,
               "locked_facts": outline.payload_json.get("locked_facts", []),
               "stage": "STORYBOARD", "units": units, "cursor": 0, "results": [],
               "blocked_episodes": {}, "automatic_replay": False},
    )
    db.add(row)
    db.flush()
    add_audit(db, actor, "DIRECTOR_STORYBOARD_QUEUED", "agent_task", row.id,
              {"plan_version": plan.version, "units": len(units),
               "episodes": len(payload.episodes)}, project_id=project_id)
    return {"task_id": row.id, "status": row.status, "replayed": False}


def _latest_plan(db, project_id: str, version: int | None = None):
    from shine.director.models import DirectorEpisodePlan

    query = select(DirectorEpisodePlan).where(DirectorEpisodePlan.project_id == project_id)
    if version is not None:
        return db.scalars(query.where(DirectorEpisodePlan.version == version)).first()
    rows = db.scalars(query.order_by(DirectorEpisodePlan.version.desc())).all()
    confirmed = [row for row in rows if row.status == "CONFIRMED"]
    return confirmed[0] if confirmed else (rows[0] if rows else None)


def _carryover_facts(db, project_id: str, prior_section_id: str | None) -> list[str]:
    """Deterministic carry-over from the prior section's *applied* shots.

    This is storyboard-level continuity (what the last shots show and who is in
    them), derived from real landed shots — never an invented end state and
    never a claim that a prior render happened.  The first section has none.
    """
    if not prior_section_id:
        return []
    from shine.materialprep.story import section_storyboard_shot_ids

    resolved = section_storyboard_shot_ids(db, project_id, prior_section_id)
    shot_ids = resolved.get("shot_ids") or []
    if not shot_ids:
        return []
    facts = []
    last = db.get(Shot, shot_ids[-1])
    if last is not None and last.project_id == project_id:
        spec = last.spec or {}
        beat = str(spec.get("story_beat") or "").strip()
        if beat:
            facts.append("上一场最后一个镜头的画面：" + beat[:400])
        cast = list(spec.get("characters") or [])
        if cast:
            facts.append("上一场结尾在场的人物：" + "、".join(cast))
        dialogue = spec.get("dialogue") or []
        if dialogue:
            last_line = dialogue[-1]
            facts.append(f"上一场最后一句台词（{last_line.get('speaker_id')}）：{last_line.get('text')}")
    return facts


def run_pending_director_storyboard(factory, settings, worker_id, *, gateway=None, heartbeat_fn=None):
    """Advance one director storyboard unit per tick (claim -> crew -> land)."""
    from datetime import timedelta

    from shine.llm.gateway import LLMGateway
    from shine.llm.secrets import SecretStore
    from shine.materialprep.story import _finish_step, _renew_story_claim

    gateway = gateway or LLMGateway(SecretStore(settings.secret_store_dir or settings.data_dir / "secrets"))
    now = utcnow()
    with factory.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        row = db.scalar(select(AgentTaskRecord).where(
            AgentTaskRecord.role_id == DIRECTOR_STORYBOARD_RUN,
            AgentTaskRecord.status.in_(["QUEUED", "RUNNING"]),
        ).order_by(AgentTaskRecord.created_at).limit(1))
        if row is None:
            return False
        if row.status == "RUNNING" and row.lease_expires_at and row.lease_expires_at > now:
            return False
        row.status = "RUNNING"
        row.lease_owner = worker_id
        row.lease_expires_at = now + timedelta(seconds=120)
        row.state_version += 1
        task_id, version, project_id = row.id, row.state_version, row.project_id
        state = dict(row.trace)
    with _renew_story_claim(factory, task_id, version, worker_id, heartbeat_fn):
        try:
            _advance_director_unit(factory, settings, gateway, task_id, version,
                                   worker_id, project_id, state)
        except Exception as exc:  # noqa: BLE001 - a unit fault must not kill the worker
            logger.exception("director_storyboard_fault task=%s", task_id)
            code = exc.detail if isinstance(exc, HTTPException) and isinstance(exc.detail, str) else type(exc).__name__
            _finish_step(factory, task_id, version, worker_id, state, "NEEDS_REVIEW", str(code)[:64])
    return True


def _advance_director_unit(factory, settings, gateway, task_id, version, worker_id, project_id, state):
    from types import SimpleNamespace

    from shine.api_film import ApplyCamera, StoryboardRequest, apply_camera, generate_storyboard
    from shine.materialprep import text_consent
    from shine.materialprep.story import _finish_step
    from shine.models import Project

    units = state["units"]
    cursor = state.get("cursor", 0)
    blocked = state.get("blocked_episodes", {})
    # Skip units whose episode already failed: an independent episode continues.
    while cursor < len(units) and units[cursor]["episode_id"] in blocked:
        cursor += 1
    if cursor >= len(units):
        status = "NEEDS_REVIEW" if blocked else "SUCCEEDED"
        _finish_step(factory, task_id, version, worker_id, {**state, "cursor": cursor}, status,
                     "DIRECTOR_STORYBOARD_EPISODE_BLOCKED" if blocked else None)
        return
    unit = units[cursor]
    request_like = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)))
    with factory() as db:
        project = db.get(Project, project_id)
        if (project.frozen_style_version_id != state["style_version_id"]
                or digest(_asset_context(db, project_id)) != state["asset_manifest_hash"]):
            raise HTTPException(409, "STORY_FROZEN_CONTRACT_CHANGED")
        outline = db.get(AgentArtifact, state["outline_artifact_id"])
        if not outline or not outline.approved_at or outline.stale or outline.sha256 != state["outline_sha256"]:
            raise HTTPException(409, "STORY_OUTLINE_NOT_CONFIRMED")
        document = db.get(ProjectDocument, state["document_id"])
        for role in ("scriptwriter", "director", "cinematographer", "continuity_supervisor", "editor"):
            profile = gateway.profile_for_role(db, role)
            if not text_consent.allowed(db, project_id, document, role, profile):
                raise HTTPException(409, "REMOTE_AGENT_CONSENT_REQUIRED")
        prior_section = _prior_section_id(state, cursor)
        facts = _carryover_facts(db, project_id, prior_section)
        # A retry resumes the failed unit from its failed role: the resume
        # steps and the one-call replay authorisation are scoped to exactly
        # that unit (``failed_unit_index``), never to the whole run — a later
        # unit of another episode must start its own chain from the top.  The
        # authorisation releases exactly one decided, answered refusal; an
        # UNKNOWN outcome never gets this: it has no replacement path but
        # reconciliation.
        retrying = state.get("failed_unit_index") == cursor
        authorize = None
        if retrying and state.get("failed_task_id"):
            failed = db.get(AgentTaskRecord, state["failed_task_id"])
            # A real provider's decided refusal needs the one-call replay
            # authorisation; a mock record never replays through that gate
            # (the API boundary refuses mock targets by design).
            if failed is not None and (failed.trace or {}).get("is_mock") is not True:
                authorize = state["failed_task_id"]
        result = generate_storyboard(project_id, StoryboardRequest(
            text=document.text[unit["start_offset"]:unit["end_offset"]],
            locked_story_facts=state["locked_facts"], allow_remote_text=True,
            start_role=state.get("failed_role") if retrying else None,
            resume_steps=state.get("resume_steps", {}) if retrying else {},
            authorize_replay_task_id=authorize,
            production_context={
                "outline_artifact_id": outline.id, "outline_sha256": outline.sha256,
                "production_section_id": unit["production_section_id"], "source_offsets": unit,
                "story_line": outline.payload_json.get("story_line", []),
                "timeline": outline.payload_json.get("timeline", []),
                "characters": outline.payload_json.get("characters", []),
                "continuity_facts": facts,
                "continuity": {"basis": "user_supplied_unverified"},
                "previous_render_status": "PENDING_SERVER_VERIFICATION"},
            consent_document_id=document.id, consent_document_sha256=document.sha256,
        ), request_like, db, gateway)
    if result["status"] != "PROPOSED":
        # Pause this episode; keep every other episode's landed shots.  The
        # failed role and resume steps travel with the task so a retry continues
        # from the failed role, never re-buying the successful ones.
        rejections = list(result.get("rejections") or [])
        failed_task_id = next((str(item.get("task_id")) for item in rejections
                               if isinstance(item, dict) and item.get("task_id")), None)
        blocked = {**state.get("blocked_episodes", {}),
                   unit["episode_id"]: {"unit_index": cursor, "status": result["status"],
                                        "failed_role": result.get("failed_role"),
                                        "failed_task_id": failed_task_id,
                                        "rejections": rejections}}
        state = {**state, "blocked_episodes": blocked, "cursor": cursor,
                 "failed_unit_index": cursor,
                 "failed_role": result.get("failed_role"),
                 "failed_task_id": failed_task_id,
                 "resume_steps": result.get("resume_steps", {}),
                 "last_blocked_result": result}
        _finish_step(factory, task_id, version, worker_id, state, "QUEUED")
        return
    # Land the shots through the same gate the expert apply uses.
    steps = result["steps"]
    camera_id = steps["cinematographer"]["artifact_id"]
    continuity_id = steps["continuity_supervisor"]["artifact_id"]
    arbitration_id = (steps.get("director_arbitration") or {}).get("artifact_id")
    with factory() as db:
        applied = apply_camera(project_id, camera_id, ApplyCamera(
            continuity_artifact_id=continuity_id,
            arbitration_artifact_id=arbitration_id,
            name_prefix=f"导演分镜-{unit['episode_id']}",
        ), db)
    state = {**state, "cursor": cursor + 1, "failed_unit_index": None,
             "failed_role": None, "failed_task_id": None,
             "resume_steps": {},
             "results": [*state.get("results", []),
                         {"unit": unit, "storyboard": result, "apply": applied}]}
    _finish_step(factory, task_id, version, worker_id, state)


def _asset_context(db, project_id):
    from shine.agents.film import asset_context

    return asset_context(db, project_id)


def _prior_section_id(state, cursor) -> str | None:
    """The production section immediately before this unit, in script order."""
    unit = state["units"][cursor]
    prior = None
    for earlier in state["units"][:cursor]:
        if (earlier.get("section_order") or 0) < (unit.get("section_order") or 0):
            if prior is None or (earlier.get("section_order") or 0) > (prior.get("section_order") or 0):
                prior = earlier
    return prior["production_section_id"] if prior else None


def director_storyboard_state(db, project_id: str) -> dict[str, Any]:
    """Per-episode progress of the newest director storyboard run."""
    row = db.scalars(select(AgentTaskRecord).where(
        AgentTaskRecord.project_id == project_id,
        AgentTaskRecord.role_id == DIRECTOR_STORYBOARD_RUN,
    ).order_by(AgentTaskRecord.created_at.desc())).first()
    if row is None:
        return {"task_id": None, "status": None, "episodes": []}
    trace = row.trace or {}
    units = trace.get("units", [])
    results = {(r.get("unit") or {}).get("start_offset"): r for r in trace.get("results", [])}
    blocked = trace.get("blocked_episodes", {})
    episodes: dict[str, dict] = {}
    for index, unit in enumerate(units):
        ep = episodes.setdefault(unit["episode_id"], {
            "episode_id": unit["episode_id"], "order": len(episodes) + 1,
            "units": [], "shot_ids": [],
            "status": "BLOCKED" if unit["episode_id"] in blocked else "PENDING",
            "blocker": blocked.get(unit["episode_id"]),
        })
        result = results.get(unit["start_offset"])
        applied = (result or {}).get("apply") or {}
        shot_ids = applied.get("shot_ids", [])
        ep["shot_ids"].extend(shot_ids)
        ep["units"].append({
            "index": index, "production_section_id": unit["production_section_id"],
            "start_offset": unit["start_offset"],
            "state": "APPLIED" if result else ("BLOCKED" if unit["episode_id"] in blocked else "PENDING"),
            "shot_count": len(shot_ids),
            "material_notices": applied.get("material_notices", []),
        })
    for ep in episodes.values():
        if ep["status"] == "BLOCKED":
            continue
        if ep["units"] and all(u["state"] == "APPLIED" for u in ep["units"]):
            ep["status"] = "APPLIED"
        elif any(u["state"] == "APPLIED" for u in ep["units"]):
            ep["status"] = "IN_PROGRESS"
    return {"task_id": row.id, "status": row.status, "state_version": row.state_version,
            "error_code": row.error_code, "cursor": trace.get("cursor", 0),
            "total_units": len(units), "episodes": list(episodes.values())}


def retry_director_episode(db, project_id: str, episode_id: str, *, expected_state_version: int,
                           actor: str) -> dict[str, Any]:
    """Re-queue one blocked episode of the director storyboard run.

    A blocked episode paused its units and the run went on with the other
    episodes; the person fixes the cause (re-freezes the style, reconciles an
    UNKNOWN provider outcome, edits the section) and retries exactly that
    episode.  The rewind is fenced on the task's ``state_version`` the caller
    read, so a run that moved on underneath is a named conflict instead of a
    silent overwrite.  Successful episodes' units are never revisited: their
    cursor positions are already past them and landing is idempotent anyway.
    """
    from sqlalchemy import update

    require_project(db, project_id)
    # A retry is the person saying "continue": the same first-use pin applies,
    # so an old refusal cannot repeat forever because of a missing rule pack.
    ensure_project_rulepack(db, project_id)
    row = db.scalars(select(AgentTaskRecord).where(
        AgentTaskRecord.project_id == project_id,
        AgentTaskRecord.role_id == DIRECTOR_STORYBOARD_RUN,
    ).order_by(AgentTaskRecord.created_at.desc())).first()
    if row is None:
        raise HTTPException(404, "DIRECTOR_STORYBOARD_RUN_NOT_FOUND")
    trace = row.trace or {}
    blocked = dict(trace.get("blocked_episodes") or {})
    if episode_id not in blocked:
        raise HTTPException(409, {"code": "EPISODE_NOT_BLOCKED",
                                  "message": "这一集当前没有被阻塞", "episode_id": episode_id})
    if row.status not in {"NEEDS_REVIEW", "QUEUED", "PAUSED"}:
        raise HTTPException(409, {"code": "DIRECTOR_STORYBOARD_NOT_RETRIABLE",
                                  "message": f"任务状态 {row.status} 不能重试", "task_status": row.status})
    if expected_state_version != row.state_version:
        raise HTTPException(409, {"code": "DIRECTOR_STORYBOARD_CHANGED",
                                  "message": "任务已更新，请刷新后重试",
                                  "current_state_version": row.state_version})
    units = trace.get("units") or []
    episode_units = [index for index, unit in enumerate(units)
                     if unit.get("episode_id") == episode_id]
    if not episode_units:
        raise HTTPException(409, "EPISODE_NOT_IN_RUN")
    # Rewind to the unit that actually failed (the blocked entry records it);
    # the units of this episode that already landed keep their shots.
    failed_unit = blocked[episode_id].get("unit_index")
    rewind_to = failed_unit if failed_unit in episode_units else episode_units[0]
    del blocked[episode_id]
    cursor = min(int(trace.get("cursor") or 0), rewind_to)
    new_trace = {**trace, "blocked_episodes": blocked, "cursor": cursor,
                 "retry_episode": episode_id, "retried_at": utcnow().isoformat()}
    changed = db.execute(update(AgentTaskRecord).where(
        AgentTaskRecord.id == row.id,
        AgentTaskRecord.state_version == expected_state_version,
        AgentTaskRecord.status.in_(["NEEDS_REVIEW", "QUEUED", "PAUSED"]),
    ).values(trace=new_trace, status="QUEUED", error_code=None,
             state_version=expected_state_version + 1)).rowcount
    if not changed:
        raise HTTPException(409, {"code": "DIRECTOR_STORYBOARD_CHANGED",
                                  "message": "任务已更新，请刷新后重试"})
    add_audit(db, actor, "DIRECTOR_STORYBOARD_EPISODE_RETRIED", "agent_task", row.id,
              {"episode_id": episode_id, "rewind_to": rewind_to,
               "resume_from_role": trace.get("failed_role")}, project_id=project_id)
    db.commit()
    db.refresh(row)
    return {"task_id": row.id, "status": row.status, "episode_id": episode_id,
            "state_version": row.state_version, "rewound_to_unit": rewind_to}


# ---------------------------------------------------------------------------
# ③ Per-episode confirmation and the editable shot table
# ---------------------------------------------------------------------------


def confirm_episode_shots(db, project_id: str, episode_id: str, *, actor: str) -> dict[str, Any]:
    """Land one episode's storyboarded sections into the confirmed plan.

    Only the target episode's sections must be applied; other episodes keep
    whatever mapping they already had (empty if still pending).  The result is a
    new plan version, confirmed through the same coverage gate, so a ready
    episode can move to materials/package while another episode stays pending.
    Re-confirming an already-landed episode reuses its shots — it never adds
    duplicates.
    """
    from shine.director.contracts import EpisodePlanPayload
    from shine.director.models import DirectorEpisodePlan
    from shine.materialprep.story import section_storyboard_shot_ids

    require_project(db, project_id)
    plan = _latest_plan(db, project_id)
    if plan is None:
        raise HTTPException(409, "EPISODE_PLAN_NOT_CONFIRMED")
    payload = EpisodePlanPayload.model_validate(plan.payload)
    if payload.mode != "sections":
        raise HTTPException(409, "EPISODE_PLAN_NOT_SCRIPT_FIRST")
    target = next((e for e in payload.episodes if e.episode_id == episode_id), None)
    if target is None:
        raise HTTPException(404, "EPISODE_NOT_IN_PLAN")
    pending = []
    shot_ids: list[str] = []
    for section_id in target.section_ids:
        resolved = section_storyboard_shot_ids(db, project_id, section_id)
        if resolved["missing"] or not resolved["shot_ids"]:
            pending.append({"section_id": section_id,
                            "missing": resolved["missing"] or [{"reason": "SECTION_HAS_NO_SHOTS"}]})
        shot_ids.extend(resolved["shot_ids"])
    if pending:
        raise HTTPException(409, {"code": "EPISODE_SECTION_NOT_STORYBOARDED",
                                  "message": "本集还有场次没有拆成镜头", "episode_id": episode_id,
                                  "pending": pending[:50]})
    episodes = []
    for episode in payload.episodes:
        if episode.episode_id == episode_id:
            episodes.append({**episode.model_dump(mode="json"), "shot_ids": shot_ids})
        else:
            episodes.append(episode.model_dump(mode="json"))
    materialized = EpisodePlanPayload.model_validate({**payload.model_dump(mode="json"), "episodes": episodes})
    from shine.api_director import _section_plan_problems

    problems = _section_plan_problems(db, project_id, materialized)
    if problems:
        raise HTTPException(409, {"code": "EPISODE_PLAN_COVERAGE_INVALID", "problems": problems})
    previous = db.scalars(select(DirectorEpisodePlan).where(
        DirectorEpisodePlan.project_id == project_id)).all()
    new_row = DirectorEpisodePlan(
        project_id=project_id,
        version=max((p.version for p in previous), default=0) + 1,
        status="DRAFT",
        target_seconds=materialized.target_seconds,
        tolerance_seconds=materialized.tolerance_seconds,
        payload=materialized.model_dump(mode="json"),
        sha256=digest_of_payload(materialized.model_dump(mode="json")),
        source="script_sections_materialized",
        created_by=actor,
    )
    db.add(new_row)
    db.flush()
    for other in previous:
        if other.status == "CONFIRMED" and other.id != new_row.id:
            other.status = "SUPERSEDED"
    new_row.status = "CONFIRMED"
    new_row.confirmed_by = actor
    new_row.confirmed_at = utcnow()
    add_audit(db, actor, "EPISODE_SHOTS_CONFIRMED", "director_episode_plan", new_row.id,
              {"episode_id": episode_id, "version": new_row.version,
               "shots": len(shot_ids), "streamline": True}, project_id=project_id)
    return {"plan_version": new_row.version, "plan_sha256": new_row.sha256,
            "episode_id": episode_id, "shot_ids": shot_ids}


def digest_of_payload(payload: dict[str, Any]) -> str:
    from shine.director.contracts import digest_of

    return digest_of(payload)


_CUT_MARKER = re.compile(r"At 00:(\d+\.\d+), hard cut\.\s*")


def episode_of_shot(db, project_id: str, shot_id: str) -> str:
    """Which episode owns this shot, read from the confirmed episode plan.

    The shot table and the subtitle ledger both need this answer.  Deriving it
    once from the plan keeps them from disagreeing about which episode a shot
    belongs to.
    """
    from shine.director.contracts import EpisodePlanPayload
    from shine.materialprep.story import section_storyboard_shot_ids

    plan = _latest_plan(db, project_id)
    if plan is None:
        return "unassigned"
    payload = EpisodePlanPayload.model_validate(plan.payload)
    for episode in payload.episodes:
        if shot_id in (episode.shot_ids or []):
            return episode.episode_id
    for episode in payload.episodes:
        for section_id in episode.section_ids:
            if shot_id in section_storyboard_shot_ids(db, project_id, section_id)["shot_ids"]:
                return episode.episode_id
    return "unassigned"


def episode_shot_table(db, project_id: str, episode_id: str) -> dict[str, Any]:
    """The editable shot table of one episode, read from the landed shots.

    Each row separates the editorial facts (成片秒数, 镜间切开/接拍) from the
    intra-shot cut points (镜内切点) and the H3 generation segment length.  In
    the current landing model one shot is one 4–15s H3 generation segment; the
    internal cut points live inside that segment, so the generation length and
    the editorial length are reported separately even when they coincide.
    """
    from shine.materialprep.story import section_storyboard_shot_ids

    plan = _latest_plan(db, project_id)
    shot_ids: list[str] = []
    section_of: dict[str, str] = {}
    if plan is not None:
        from shine.director.contracts import EpisodePlanPayload

        payload = EpisodePlanPayload.model_validate(plan.payload)
        target = next((e for e in payload.episodes if e.episode_id == episode_id), None)
        if target is not None:
            if target.shot_ids:
                shot_ids = list(target.shot_ids)
            else:
                for section_id in target.section_ids:
                    for sid in section_storyboard_shot_ids(db, project_id, section_id)["shot_ids"]:
                        shot_ids.append(sid)
                        section_of[sid] = section_id
    from shine.director.models import ShotPromptRevision, ShotSubtitle
    from shine.director.prompt_revisions import manual_status_for

    frozen_prompts = {
        row.shot_id: row for row in db.scalars(select(ShotPromptRevision).where(
            ShotPromptRevision.shot_id.in_(shot_ids),
            ShotPromptRevision.status == "FROZEN",
        ).order_by(ShotPromptRevision.revision.desc())).all()
    } if shot_ids else {}
    subtitles = {
        row.shot_id: row for row in db.scalars(select(ShotSubtitle).where(
            ShotSubtitle.shot_id.in_(shot_ids))).all()
    } if shot_ids else {}
    rows = []
    total = 0.0
    for position, shot_id in enumerate(shot_ids):
        shot = db.get(Shot, shot_id)
        if shot is None or shot.project_id != project_id:
            continue
        spec = shot.spec or {}
        prompt_row = frozen_prompts.get(shot.id)
        # A frozen prompt is only "in effect" while it still describes this
        # shot.  The table must not claim 人工冻结 over text that will not be
        # submitted.
        prompt_status = "compiled"
        prompt_reason = None
        if prompt_row is not None:
            prompt_reason = manual_status_for(db, shot, prompt_row)
            prompt_status = "frozen" if prompt_reason is None else "inactive"
        subtitle_row = subtitles.get(shot.id)
        requested = list(spec.get("requested_assets") or [])
        beat = str(spec.get("story_beat") or "")
        cuts = [{"start_s": float(m.group(1))} for m in _CUT_MARKER.finditer(beat)]
        duration = float(spec.get("duration_target_s") or 0.0)
        total += duration
        continuity = spec.get("continuity") or {}
        rows.append({
            "shot_id": shot.id,
            "position": position + 1,
            "name": shot.name,
            "revision": shot.revision,
            "section_id": section_of.get(shot.id),
            "story_beat": beat,
            "characters": list(spec.get("characters") or []),
            "dialogue": list(spec.get("dialogue") or []),
            "duration_target_s": duration,
            "editorial_seconds": duration,
            "h3_segment_count": 1,
            "h3_generation_seconds": duration,
            "internal_cuts": cuts,
            "continuity_mode": continuity.get("mode") or "cut",
            "previous_shot_id": continuity.get("previous_shot_id"),
            "required_asset_ids": list(spec.get("required_asset_ids") or []),
            "requested_assets": requested,
            "prompt": {
                "status": prompt_status,
                "reason": prompt_reason,
                "revision": prompt_row.revision if prompt_row is not None else None,
                "mode": prompt_row.mode if prompt_row is not None else None,
                "chars": len(prompt_row.prompt) if prompt_row is not None else None,
                "frozen_at": (prompt_row.frozen_at.isoformat() + "Z"
                              if prompt_row is not None and prompt_row.frozen_at else None),
            },
            "subtitle": {
                "status": subtitle_row.status if subtitle_row is not None else "EMPTY",
                "revision": subtitle_row.revision if subtitle_row is not None else 0,
                "line_count": len(subtitle_row.lines or []) if subtitle_row is not None else 0,
            },
        })
    return {"episode_id": episode_id, "shots": rows,
            "total_editorial_seconds": round(total, 3), "shot_count": len(rows)}


# ---------------------------------------------------------------------------
# ④ Shot-driven, deduplicated material list
# ---------------------------------------------------------------------------


def _frozen_material_prompts(db, project_id: str) -> dict[tuple[str, str], dict[str, Any]]:
    """Every frozen hand-written prompt of this project, keyed by identity.

    A re-derived list opens a new plan revision, and a new revision has new
    requirement rows.  The person's own words must travel with the item they
    wrote them for, or a second derivation would silently reset them to the
    model's description.
    """
    from shine.comfyops.models import AssetRequirement

    carried: dict[tuple[str, str], dict[str, Any]] = {}
    for row in db.scalars(select(AssetRequirement).where(
            AssetRequirement.project_id == project_id).order_by(
            AssetRequirement.created_at.desc())).all():
        payload = dict(row.payload or {})
        frozen = payload.get("manual_prompt")
        if not isinstance(frozen, dict) or not frozen.get("prompt"):
            continue
        key = (str(payload.get("family") or ""), str(payload.get("entity_id") or ""))
        carried.setdefault(key, frozen)
    return carried


def _confirmed_material_bindings(db, project_id: str) -> dict[tuple[str, str], dict[str, Any]]:
    """The asset each confirmed requirement already adopted, keyed by identity.

    A re-derived list is a new revision with new rows.  The upload a person
    made and the choice they confirmed are facts about *them*, not about the
    revision, so they travel with the item instead of being dropped by a fresh
    row.
    """
    from shine.comfyops.models import AssetRequirement

    carried: dict[tuple[str, str], dict[str, Any]] = {}
    for row in db.scalars(select(AssetRequirement).where(
            AssetRequirement.project_id == project_id,
            AssetRequirement.status == "CONFIRMED",
    ).order_by(AssetRequirement.created_at.desc())).all():
        payload = dict(row.payload or {})
        asset_id = payload.get("bound_asset_id")
        if not asset_id:
            continue
        key = (str(payload.get("family") or ""), str(payload.get("entity_id") or ""))
        carried.setdefault(key, {
            "bound_asset_id": asset_id,
            "bound_asset_sha256": payload.get("bound_asset_sha256"),
        })
    return carried


def derive_shot_materials(db, project_id: str, *, episode_ids: list[str] | None = None,
                          actor: str = "local-user") -> dict[str, Any]:
    """Build the material requirement list from the *confirmed shots*.

    The list is a projection of what the shots already carry (their cast, their
    speaker tags, the references they promised by id, the masters they are
    already bound to) plus the entity catalogue when one exists.  It never
    re-reads the novel: the confirmed design is the only authority, and the
    same confirmed shots always produce the same list.

    Required and optional are different gates.  A character subject is required
    whenever the bound renderer declares a subject slot; a scene or prop plate is
    required only when a shot promised that exact asset by id.  Everything else
    is a suggestion the person may skip, and skipping it never blocks a render.
    """
    from shine.api_director import _confirmed_outline
    from shine.director import requirements as requirement_source
    from shine.materialprep import service
    from shine.materialprep.service import confirmation_snapshot_hash

    project = require_project(db, project_id)
    outline = _confirmed_outline(db, project_id)
    if outline is None:
        raise HTTPException(409, "SCRIPT_OUTLINE_NOT_CONFIRMED")
    document = db.get(ProjectDocument, outline.payload_json["document_id"])
    if document is None:
        raise HTTPException(409, "MATERIAL_DOCUMENT_NOT_FOUND")

    derived = requirement_source.derive(db, project, episode_ids=episode_ids)
    if not derived["preparations"]:
        raise HTTPException(409, "MATERIAL_NO_REQUIREMENTS")
    # A hand-written prompt and an adopted upload are the person's own work.
    # A re-derived list opens a new plan revision with new requirement rows, so
    # both travel over by identity instead of being dropped by a fresh row.
    carried_prompts = _frozen_material_prompts(db, project_id)
    carried_bindings = _confirmed_material_bindings(db, project_id)
    plan_row, replayed = service.create_draft_plan(
        db, project, document, derived["preparations"], actor=actor)

    from shine.comfy.workflow import graph_hash
    from shine.comfyops.models import AssetRequirement

    usage = {(row["family"], row["entity_id"]): row for row in derived["requirements"]}
    requirements_view = []
    shot_ids_seen: set[str] = set()
    for row in db.scalars(select(AssetRequirement).where(
            AssetRequirement.plan_id == plan_row.id, AssetRequirement.project_id == project_id)):
        payload = dict(row.payload or {})
        identity = (payload.get("family"), payload.get("entity_id"))
        carried = carried_prompts.get(identity)
        if carried and not payload.get("manual_prompt"):
            payload["manual_prompt"] = carried
        adopted = carried_bindings.get(identity)
        if adopted and not payload.get("bound_asset_id"):
            payload.update(adopted)
        if payload != dict(row.payload or {}):
            row.payload = payload
            row.sha256 = graph_hash(payload)
        evidence = usage.get(identity) or {}
        shot_ids = sorted(set(evidence.get("shot_ids") or []))
        episodes_used = sorted(evidence.get("used_episodes") or [])
        critical = bool(evidence.get("critical", payload.get("critical", True)))
        strategy = evidence.get("strategy") or payload.get("strategy")
        matched_id = evidence.get("matched_master_id")
        user_specified = bool(evidence.get("user_specified") or payload.get("user_specified"))
        shot_ids_seen.update(shot_ids)
        if (payload.get("used_shot_ids") != shot_ids
                or payload.get("used_episodes") != episodes_used
                or payload.get("critical") != critical
                or payload.get("optional") != (not critical)
                or payload.get("strategy") != strategy
                or payload.get("matched_master_id") != matched_id
                or bool(payload.get("user_specified")) != user_specified):
            row.payload = {
                **payload,
                "used_shot_ids": shot_ids,
                "used_episodes": episodes_used,
                "critical": critical,
                "optional": not critical,
                "strategy": strategy,
                "matched_master_id": matched_id,
                "user_specified": user_specified,
                "variants": list(evidence.get("variants") or payload.get("variants") or []),
            }
            row.sha256 = graph_hash(row.payload)
        # The view reads the persisted row, so the answer the derive endpoint
        # gives is the same one the requirements endpoint will give later.
        payload = dict(row.payload or {})
        requirements_view.append({
            "requirement_id": row.id,
            "family": payload.get("family"),
            "entity_id": payload.get("entity_id"),
            "name": payload.get("entity_id"),
            "description": payload.get("description") or "",
            "critical": critical,
            "optional": not critical,
            "cited_by_shot": bool(evidence.get("cited_by_shot")),
            "user_specified": bool(payload.get("user_specified")),
            "manual_prompt": ({
                "sha256": (payload.get("manual_prompt") or {}).get("sha256"),
                "chars": len(str((payload.get("manual_prompt") or {}).get("prompt") or "")),
                "frozen_at": (payload.get("manual_prompt") or {}).get("frozen_at"),
            } if (payload.get("manual_prompt") or {}).get("prompt") else None),
            "variants": list(evidence.get("variants") or []),
            "used_shot_ids": shot_ids,
            "used_shot_count": len(shot_ids),
            "used_episodes": episodes_used,
            "matched_master": ({"asset_id": matched_id,
                                "sha256": evidence.get("matched_master_sha256")}
                               if matched_id else None),
            "status": "matched" if matched_id else "missing",
            "strategy": strategy,
        })
    return {
        "plan_revision_id": plan_row.id,
        "replayed": replayed,
        "expected_snapshot": confirmation_snapshot_hash(plan_row),
        "requirements": requirements_view,
        "shot_count": len(shot_ids_seen),
        "capacity": derived["capacity"],
        "profile": derived["profile"],
        "catalog_present": derived["catalog_present"],
    }


# ---------------------------------------------------------------------------
# ⑤⑥ One shoot action: freeze the selected episodes, then start the real run
# ---------------------------------------------------------------------------


def start_director_production(
    db, project, *, settings, episode_ids: list[str], workflow_profile_id: str | None = None,
    strategy: dict | None = None,
    actor: str = "local-user",
) -> dict[str, Any]:
    """The single main operation of the director page: shoot the chosen episodes.

    It composes the existing services rather than owning a second production
    engine: readiness is read from the real ledger, a package covering exactly
    the selected episodes is built and confirmed when one does not exist, and
    the existing Run engine starts it with a deterministic request key.  A
    replay of the same selection returns the same run instead of starting a
    second one, and an unfinished episode never drags a ready one down.

    ``strategy`` is the dynamic per-shot round's user choice (model variant,
    accelerator, optional second pass / RelayVSR X2).  With a strategy the
    package freezes the strategy contract instead of one static profile and
    every shot compiles its own graph; the legacy ``workflow_profile_id``
    path stays byte-for-byte for existing callers.
    """
    from shine.comfy.workflow import graph_hash
    from shine.director.overview import build_overview
    from shine.director.package import (
        build_package,
        confirm_package,
        package_episodes,
        package_readiness,
    )
    from shine.production import runner as run_engine

    project_id = project.id
    requested = list(dict.fromkeys(str(item) for item in (episode_ids or []) if str(item).strip()))
    if not requested:
        raise HTTPException(422, {"code": "EPISODE_SCOPE_REQUIRED",
                                  "message": "先选择要拍摄的集"})
    overview = build_overview(db, project)
    known = {row["episode_id"]: row for row in overview["episodes"]}
    unknown = [item for item in requested if item not in known]
    if unknown:
        raise HTTPException(404, {"code": "EPISODE_NOT_IN_PLAN",
                                  "message": "选择的集不在当前分集方案里",
                                  "episode_ids": unknown})
    blocked = [
        {"episode_id": item,
         "state": known[item]["state"],
         "blockers": known[item].get("next_action"),
         "episode_blockers": [b for b in (known[item].get("package") or {}).get("blockers", [])][:5]}
        for item in requested
        if known[item]["state"] not in {"READY_TO_SHOOT", "READY_TO_PACKAGE", "SHOOTING",
                                        "REVIEW_READY", "DELIVERED"}
    ]
    if blocked:
        raise HTTPException(409, {
            "code": "EPISODE_NOT_SHOOTABLE",
            "message": "选中的集里还有没准备好的；先处理这些集，其他集不受影响",
            "episodes": blocked,
        })

    ensure_project_rulepack(db, project_id)
    strategy_sha = None
    if strategy is not None:
        from shine.strategy import GenerationStrategy

        try:
            strategy_contract = GenerationStrategy.model_validate(strategy)
        except ValueError as exc:
            raise HTTPException(422, {"code": "STRATEGY_INVALID",
                                      "message": "生产策略合同不合法。",
                                      "details": str(exc)[:400]}) from exc
        strategy = strategy_contract.model_dump(mode="json")
        strategy_sha = strategy_contract.strategy_sha256()
        if workflow_profile_id:
            raise HTTPException(422, {"code": "STRATEGY_PROFILE_EXCLUSIVE",
                                      "message": "生产策略与静态生成方案不能同时指定。"})
    package = _package_for_selection(db, project_id, requested, strategy_sha=strategy_sha)
    built = False
    if package is None:
        if strategy is not None:
            package = build_package(
                db, project,
                strategy=strategy,
                episode_ids=requested,
                generation_mode="Ref2VA",
                policy=None,
                fill_missing_character_bindings=True,
                created_by=actor,
                settings=settings,
            )
        else:
            profile_id = workflow_profile_id
            if not profile_id:
                from shine.director.requirements import bound_profile

                bound = bound_profile(db, project_id)
                if bound is None:
                    raise HTTPException(409, {"code": "WORKFLOW_PROFILE_REQUIRED",
                                              "message": "先登记本机生成方案，才能真实开拍"})
                profile_id = bound.id
            profile = db.get(WorkflowProfile, profile_id)
            # A profile registered for the whole machine has no project owner; a
            # project-owned profile must belong to this project.
            if profile is None or (profile.project_id and profile.project_id != project_id):
                raise HTTPException(404, {"code": "WORKFLOW_PROFILE_NOT_FOUND",
                                          "message": "选择的生成方案不属于这个作品"})
            package = build_package(
                db, project,
                workflow_profile_id=profile.id,
                episode_ids=requested,
                generation_mode=profile.mode,
                policy=None,
                fill_missing_character_bindings=(profile.mode or "").lower() == "ref2va",
                created_by=actor,
                settings=settings,
            )
        built = True
        readiness = package_readiness(db, package)
        # ``ready`` answers "can this content be produced at all"; ``startable``
        # additionally needs the confirmation this action is about to write.
        if not readiness.get("ready"):
            # The incomplete package stays visible so the person can read the
            # exact gap; nothing is started and no run is created.
            raise HTTPException(409, {
                "code": "DIRECTOR_PACKAGE_NOT_READY",
                "message": "这一版拍摄方案还没有通过预检；按下面的缺项处理后重试",
                "package_id": package.id,
                "revision": package.revision,
                "blockers": (readiness.get("blockers") or [])[:20],
            })
        confirm_package(db, package, actor=actor)

    request_key = "director:" + graph_hash({
        "project": project_id,
        "package": package.manifest_sha256,
        "episodes": sorted(requested),
    })
    run, created = run_engine.create_run(
        db, package, episode_ids=requested, idempotency_key=request_key, actor=actor)
    add_audit(db, actor, "DIRECTOR_PRODUCTION_STARTED", "production_run", run.id,
              {"package_id": package.id, "revision": package.revision,
               "episode_ids": requested, "created": created,
               "package_built": built, "streamline": True}, project_id=project_id)
    return {
        "package_id": package.id,
        "package_revision": package.revision,
        "package_built": built,
        "run_id": run.id,
        "run_status": run.status,
        "created": created,
        "episode_ids": requested,
        "episodes": [
            {"episode_id": row.episode_id, "position": row.position,
             "shot_count": row.shot_count}
            for row in package_episodes(db, package.id)
            if row.episode_id in set(requested)
        ],
    }


def _package_for_selection(db, project_id: str, episode_ids: list[str], *, strategy_sha: str | None = None):
    """A confirmed, non-stale package whose scope - and strategy - match.

    A package only replays when it was frozen for the same production
    strategy: reusing a profile package for a strategy shoot (or the
    reverse) would silently render under a different execution contract the
    user never confirmed.
    """
    from shine.director.models import DirectorPackageEpisode, DirectorPackageVersion

    wanted = set(episode_ids)
    rows = db.scalars(select(DirectorPackageVersion).where(
        DirectorPackageVersion.project_id == project_id,
        DirectorPackageVersion.status == "CONFIRMED",
        DirectorPackageVersion.stale.is_(False),
    ).order_by(DirectorPackageVersion.revision.desc())).all()
    for package in rows:
        covered = set(db.scalars(select(DirectorPackageEpisode.episode_id).where(
            DirectorPackageEpisode.package_id == package.id)).all())
        if covered != wanted:
            continue
        frozen_sha = ((package.manifest or {}).get("binding") or {}).get("strategy_sha256")
        if frozen_sha != strategy_sha:
            continue
        return package
    return None
