"""Versioned long-script preparation; API queues, the resident Worker executes."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select, text

from shine.agents.film import asset_context
from shine.api_common import DBSession, llm_gateway
from shine.api_operator import Operator
from shine.h3.rulebook import digest
from shine.llm.base import LLMError
from shine.llm.gateway import LLMGateway
from shine.materialprep import story
from shine.materialprep.source_spans import segment_document
from shine.models import AgentArtifact, AgentTaskRecord, ProjectDocument, utcnow
from shine.schemas import APIModel
from shine.services import add_audit, require_project

router = APIRouter(prefix="/api/v1", tags=["long script preparation"])


class OutlineRequest(APIModel):
    document_id: str
    expected_document_hash: str = Field(min_length=64, max_length=64)
    locked_facts: list[str] = Field(default_factory=list, max_length=100)


def _document(db, project_id, document_id, expected_hash=None):
    document = db.get(ProjectDocument, document_id)
    if document is None or document.project_id != project_id:
        raise HTTPException(404, "MATERIAL_DOCUMENT_NOT_FOUND")
    if expected_hash and document.sha256 != expected_hash:
        raise HTTPException(409, "STORY_DOCUMENT_CHANGED")
    return document


class ScriptBasisRequest(APIModel):
    document_id: str = Field(min_length=1, max_length=36)


def _basis_view(db, project) -> dict:
    from shine.director.package import _outline_for_document, current_script_basis

    basis = current_script_basis(db, project)
    document = basis["document"]
    outline = _outline_for_document(db, project.id, document) if document is not None else None
    return {
        "project_id": project.id,
        "document_id": document.id if document else None,
        "document_sha256": document.sha256 if document else None,
        "document_filename": document.filename if document else None,
        "explicit": basis["explicit"],
        "outline_artifact_id": outline.id if outline else None,
        "outline_confirmed": bool(outline and outline.approved_at),
    }


@router.get("/projects/{project_id}/script-basis")
def read_script_basis(project_id: str, db: DBSession) -> dict:
    """Which script the project will produce from, and what is still a candidate."""
    project = require_project(db, project_id)
    documents = db.scalars(
        select(ProjectDocument)
        .where(ProjectDocument.project_id == project_id)
        .order_by(ProjectDocument.created_at.desc())
    ).all()
    return _basis_view(db, project) | {
        "candidates": [
            {"document_id": row.id, "filename": row.filename, "sha256": row.sha256,
             "created_at": row.created_at.isoformat() + "Z"}
            for row in documents
        ],
    }


@router.post("/projects/{project_id}/script-basis")
def choose_script_basis(project_id: str, body: ScriptBasisRequest, actor: Operator, db: DBSession) -> dict:
    """Switch the production basis to one already-imported document.

    This is the explicit action a second upload never performs by itself.
    Packages confirmed against the previous basis stay readable by id; their
    readiness reports ``SCRIPT_BASIS_CHANGED`` so they cannot silently produce
    a superseded script.
    """
    project = require_project(db, project_id)
    document = _document(db, project_id, body.document_id)
    changed = project.current_document_id != document.id
    if changed:
        previous = project.current_document_id
        project.current_document_id = document.id
        add_audit(db, actor, "SCRIPT_BASIS_SELECTED", "project_document", document.id,
                  {"previous_document_id": previous, "sha256": document.sha256}, project_id=project_id)
    db.commit()
    db.refresh(project)
    return _basis_view(db, project) | {"changed": changed}


@router.post("/projects/{project_id}/story-preparation/outlines", status_code=202)
def queue_outline(project_id: str, body: OutlineRequest, actor: Operator, db: DBSession,
                  gateway: Annotated[LLMGateway, Depends(llm_gateway)]):
    db.execute(text("BEGIN IMMEDIATE"))
    require_project(db, project_id)
    document = _document(db, project_id, body.document_id, body.expected_document_hash)
    if not document.text.strip():
        raise HTTPException(422, "STORY_DOCUMENT_EMPTY")
    try:
        row = story.queue_outline(db, project_id, document, gateway, body.locked_facts)
    except LLMError as exc:
        # The script is stored already; only this role's model is missing.  The
        # refusal names the role and the page that fixes it, instead of failing
        # the whole preparation or asking for every text role at once (J03).
        raise HTTPException(409, {
            "code": "TEXT_ROLE_NOT_READY",
            "role": "scriptwriter",
            "role_label": "故事大纲",
            "reason": exc.code,
            "action": "CONFIGURE_TEXT_ROLE",
            "message": "整理大纲需要「故事大纲」这一个角色已配置并测试；其它文本工种不影响这一步。",
        }) from None
    db.commit()
    return {"task_id": row.id, "status": row.status, "state_version": row.state_version}


def _task_summary(row):
    """What the person is actually waiting for, in numbers they can read.

    The trace keeps the raw cursor; the person needs "done of total" and the
    stage in words.  Nothing here guesses: the totals come from the units the
    task actually owns.
    """
    trace = row.trace or {}
    stage = trace.get("stage")
    if row.role_id == story.SECTION_RUN:
        label = "逐段生成分镜"
        total = len(trace.get("units") or [])
        done = len(trace.get("results") or [])
    elif stage == "EXTRACT":
        label = "逐场提取剧情要点"
        total = int(trace.get("segment_total") or 0)
        done = int(trace.get("cursor") or 0)
    elif stage == "MERGE":
        label = f"归并大纲（第 {int(trace.get('level') or 0) + 1} 层）"
        total = len(trace.get("level_inputs") or [])
        done = int(trace.get("cursor") or 0)
    elif stage == "AWAITING_OUTLINE_CONFIRMATION":
        label = "等待你确认大纲"
        total = done = 1
    else:
        label = "准备中"
        total = done = 0
    percent = 100 if total and done >= total else (int(done * 100 / total) if total else 0)
    expired = bool(row.lease_expires_at) and row.lease_expires_at <= utcnow()
    remaining = None
    if row.lease_expires_at:
        remaining = max(0, int((row.lease_expires_at - utcnow()).total_seconds()))
    return {"stage": stage or "", "label": label, "done": done, "total": total, "percent": percent,
            "lease_expires_at": row.lease_expires_at.isoformat() if row.lease_expires_at else None,
            "lease_remaining_s": remaining,
            "awaiting_reclaim": row.status == "RUNNING" and expired}


def public_task(db, row):
    trace = row.trace
    outline = db.get(AgentArtifact, trace.get("outline_artifact_id")) if trace.get("outline_artifact_id") else None
    questions = list(outline.payload_json.get("questions") or []) if outline else []
    return {"task_id": row.id, "role": row.role_id, "status": row.status, "state_version": row.state_version,
            "error_code": row.error_code, "progress": trace, "summary": _task_summary(row),
            "retry": _outline_retry_view(db, row) if row.role_id == story.OUTLINE_RUN else None,
            "outline": {"artifact_id": outline.id, "sha256": outline.sha256, "payload": outline.payload_json,
                        "approved": bool(outline.approved_at), "stale": outline.stale,
                        # What the person is acknowledging, so the workbench can
                        # offer an explicit confirmation instead of an endless
                        # "add more facts" loop on a script that is deliberately
                        # ambiguous.
                        "questions_sha256": digest({"artifact": outline.id, "questions": questions})
                        if questions else None} if outline else None}


def _outline_retry_view(db, row):
    blocked = (row.trace or {}).get("blocked_subtasks") or []
    entry = blocked[0] if blocked else {}
    sub = db.get(AgentTaskRecord, entry["task_id"]) if entry.get("task_id") else None
    if sub is not None and sub.project_id != row.project_id:
        sub = None
    trace = sub.trace if sub is not None else {}
    code = (sub.error_code if sub else None) or entry.get("error_code") or row.error_code
    outcome = trace.get("external_outcome") or entry.get("external_outcome")
    provider_code = trace.get("provider_code")
    failure = ({"code": code, "scene_key": entry.get("scene_key"), "outcome": outcome,
                "http_status": trace.get("response_status"),
                "provider_code": provider_code if type(provider_code) is int and provider_code else None}
               if code else row.trace.get("last_retry_failure"))
    eligible = row.status in {"NEEDS_REVIEW", "FAILED"} and (
        bool(blocked) or code == "REMOTE_AGENT_CONSENT_REQUIRED"
    )
    unknown = outcome == "UNKNOWN" and entry.get("reconciliation") not in {
        "NOT_SENT_PROVEN", "RESULT_ADOPTED", "USER_AUTHORIZED_NEW_REQUEST",
    }
    exhausted = (entry.get("reconciliation") == "RETRIES_EXHAUSTED"
                 or bool(eligible and unknown and sub and story.unknown_resends_used(db, sub)
                         >= story.MAX_UNKNOWN_RESENDS))
    if unknown and (sub is None or sub.status != "NEEDS_REVIEW"):
        eligible = False
    return {"failure": failure, "can_retry": eligible and not exhausted,
            "unknown_subtask_id": sub.id if eligible and not exhausted and unknown and sub else None,
            "exhausted": exhausted}


@router.get("/projects/{project_id}/story-preparation")
def preparation_state(project_id: str, db: DBSession, document_id: str | None = None):
    require_project(db, project_id)
    tasks = db.scalars(select(AgentTaskRecord).where(
        AgentTaskRecord.project_id == project_id, AgentTaskRecord.role_id.in_([story.OUTLINE_RUN, story.SECTION_RUN])
    ).order_by(AgentTaskRecord.created_at.desc()))
    return [public_task(db, row) for row in tasks if not document_id or row.trace.get("document_id") == document_id]


class OutlineApproval(APIModel):
    expected_sha256: str = Field(min_length=64, max_length=64)
    #: A script may legitimately keep an ambiguity (a mystery whose answer is
    #: the point).  The gate still refuses a silent confirmation, but the person
    #: who wrote the script can state, once, which open questions are intended:
    #: the acknowledgement names the exact question list it was given for, and
    #: the confirmed artifact records it.
    acknowledge_questions_sha256: str | None = Field(default=None, min_length=64, max_length=64)


@router.post("/projects/{project_id}/story-preparation/outlines/{artifact_id}/confirm")
def confirm_outline(project_id: str, artifact_id: str, body: OutlineApproval, actor: Operator, db: DBSession):
    row = db.get(AgentArtifact, artifact_id)
    if (row is None or row.project_id != project_id or row.kind != "StoryOutline" or row.stale
            or row.sha256 != body.expected_sha256 or row.sha256 != digest(row.payload_json)):
        raise HTTPException(409, "STORY_OUTLINE_CHANGED")
    _document(db, project_id, row.payload_json["document_id"], row.payload_json["document_sha256"])
    # Confirming an outline is an explicit choice of the script it was built
    # from: that document becomes the project's production basis.  An upload
    # that was never confirmed stays a candidate.
    from shine.models import Project

    project = db.get(Project, project_id)
    if project is not None and project.current_document_id != row.payload_json["document_id"]:
        project.current_document_id = row.payload_json["document_id"]
    questions = list(row.payload_json.get("questions") or [])
    if questions:
        expected = digest({"artifact": row.id, "questions": questions})
        if body.acknowledge_questions_sha256 != expected:
            raise HTTPException(409, "STORY_OUTLINE_QUESTIONS_REQUIRE_FACTS")
    was_approved = bool(row.approved_at)
    if not was_approved:
        row.approved_by, row.approved_at = actor, utcnow()
    changed = story.select_outline_revision(db, project_id, row)
    if changed or not was_approved:
        add_audit(db, actor, "STORY_OUTLINE_CONFIRMED", "agent_artifact", row.id,
                  {"sha256": row.sha256, "invalidated_section_ids": changed,
                   # The artifact payload is immutable and stays checked against
                   # its own digest, so the accepted ambiguity is recorded here.
                   "questions_acknowledged": questions or None}, project_id=project_id)
    db.commit()
    return {"artifact_id": row.id, "approved": True}


class SectionRequest(APIModel):
    outline_artifact_id: str
    expected_outline_hash: str = Field(min_length=64, max_length=64)
    production_section_ids: list[str] = Field(min_length=1, max_length=200)
    continuity_facts: dict[str, list[str]] = Field(default_factory=dict)


@router.get("/projects/{project_id}/story-preparation/outlines/{artifact_id}/continuity")
def outline_continuity(project_id: str, artifact_id: str, db: DBSession):
    """Per-section carry-over availability, derived from the real ledger.

    Read-only: a section reports a verified prior end state only when the
    previous section's shots really are storyboarded, applied and accepted
    right now.  Freezing happens when a section run is actually queued.
    """
    require_project(db, project_id)
    outline = db.get(AgentArtifact, artifact_id)
    if outline is None or outline.project_id != project_id or outline.kind != "StoryOutline":
        raise HTTPException(404, "STORY_OUTLINE_NOT_FOUND")
    rows = []
    for section in outline.payload_json["production_sections"]:
        prior = story.prior_section_of(section, outline.payload_json["production_sections"])
        live = (story.compute_section_continuity(db, project_id, prior["production_section_id"])
                if prior else {"status": "NOT_PRODUCED", "missing": ["NO_PRIOR_SECTION"]})
        frozen = None
        if prior:
            candidates = [row for row in db.scalars(select(AgentArtifact).where(
                AgentArtifact.project_id == project_id, AgentArtifact.kind == story.CONTINUITY_KIND))
                if row.payload_json.get("production_section_id") == prior["production_section_id"]
                and not row.stale and row.sha256 == story.digest(live)]
            frozen = candidates[-1] if candidates else None
        own = story.compute_section_continuity(db, project_id, section["production_section_id"])
        rows.append({"production_section_id": section["production_section_id"], "order": section["order"],
                     "prior_production_section_id": prior["production_section_id"] if prior else None,
                     "verified_prior_available": live.get("status") == "VERIFIED",
                     "prior_production_status": live.get("status"),
                     "prior_continuity": ({"artifact_id": frozen.id, "sha256": frozen.sha256,
                                            "end_state": frozen.payload_json.get("end_state")}
                                           if frozen else None),
                     "own_production": {"status": own["status"], "missing": own["missing"]}})
    return {"outline_artifact_id": outline.id, "outline_sha256": outline.sha256, "sections": rows}


@router.post("/projects/{project_id}/story-preparation/materials", status_code=202)
def section_materials(project_id: str, body: SectionRequest, actor: Operator, db: DBSession):
    from shine.materialprep import service

    outline = db.get(AgentArtifact, body.outline_artifact_id)
    if (not outline or outline.project_id != project_id or not outline.approved_at or outline.stale
            or outline.kind != "StoryOutline" or outline.sha256 != body.expected_outline_hash):
        raise HTTPException(409, "STORY_OUTLINE_NOT_CONFIRMED")
    document = _document(db, project_id, outline.payload_json["document_id"], outline.payload_json["document_sha256"])
    sections = [s for s in outline.payload_json["production_sections"]
                if s["production_section_id"] in body.production_section_ids]
    if len(sections) != len(set(body.production_section_ids)):
        raise HTTPException(422, "STORY_SECTION_UNKNOWN")
    span_ids = {span.id for scene in segment_document(document.text) for span in scene.spans
                if any(span.start_offset < s["end_offset"] and span.end_offset > s["start_offset"] for s in sections)}
    preparations = []
    for artifact_id in outline.payload_json["leaf_artifact_ids"]:
        leaf = db.get(AgentArtifact, artifact_id)
        if leaf is None or leaf.project_id != project_id or leaf.sha256 != digest(leaf.payload_json):
            raise HTTPException(409, "STORY_SOURCE_ARTIFACT_CHANGED")
        requirements = [r for r in leaf.payload_json.get("requirements", [])
                        if any(key in span_ids for key in r["source_span_ids"])]
        if requirements:
            preparations.append({**leaf.payload_json, "requirements": requirements})
    plan, replayed = service.create_draft_plan(db, require_project(db, project_id), document, preparations, actor=actor)
    add_audit(db, actor, "STORY_SECTION_MATERIALS_PREPARED", "preproduction_plan", plan.id,
              {"outline_artifact_id": outline.id, "production_section_ids": body.production_section_ids},
              project_id=project_id)
    db.commit()
    return {"plan_revision_id": plan.id, "replayed": replayed}


@router.post("/projects/{project_id}/story-preparation/sections", status_code=202)
def queue_sections(project_id: str, body: SectionRequest, actor: Operator, db: DBSession):
    db.execute(text("BEGIN IMMEDIATE"))
    project = require_project(db, project_id)
    outline = db.get(AgentArtifact, body.outline_artifact_id)
    if (not outline or outline.project_id != project_id or outline.kind != "StoryOutline" or outline.stale
            or not outline.approved_at or outline.sha256 != body.expected_outline_hash
            or outline.sha256 != digest(outline.payload_json)):
        raise HTTPException(409, "STORY_OUTLINE_NOT_CONFIRMED")
    if not project.frozen_style_version_id:
        raise HTTPException(409, "STORYBOARD_STYLE_NOT_FROZEN")
    document = _document(db, project_id, outline.payload_json["document_id"], outline.payload_json["document_sha256"])
    selected = set(body.production_section_ids)
    sections = [s for s in outline.payload_json["production_sections"] if s["production_section_id"] in selected]
    if len(sections) != len(selected):
        raise HTTPException(422, "STORY_SECTION_UNKNOWN")
    units = []
    for section in sections:
        facts = body.continuity_facts.get(section["production_section_id"], [])
        basis = None
        if section["order"] > 1:
            prior = story.prior_section_of(section, outline.payload_json["production_sections"])
            artifact, _live = story.freeze_section_continuity(db, project_id, prior["production_section_id"]) \
                if prior else (None, None)
            if artifact:
                basis = {"basis": "verified_section_state", "continuity_artifact_id": artifact.id,
                         "continuity_sha256": artifact.sha256,
                         "prior_production_section_id": prior["production_section_id"]}
            elif not facts:
                # Without a verified prior end state the carry-over must come
                # from the user; inventing the previous section's end state is
                # never allowed.
                raise HTTPException(409, {"code": "STORY_CONTINUITY_FACTS_REQUIRED",
                                          "production_section_id": section["production_section_id"],
                                          "prior_production_section_id":
                                              prior["production_section_id"] if prior else None})
        raw = document.text[section["start_offset"]:section["end_offset"]]
        offset = section["start_offset"]
        # Long sections are split into thirds so one storyboard unit cannot
        # spend the whole provider timeout; the per-unit budget is still the
        # shared context controller.
        for fragment in segment_document(raw, max_scene_chars=1200):
            units.append({"production_section_id": section["production_section_id"], "start_offset": offset,
                          "end_offset": offset + len(fragment.text), "continuity_facts": facts,
                          "continuity": basis or {"basis": "user_supplied_unverified"}})
            offset += len(fragment.text)
    asset_hash = digest(asset_context(db, project_id))
    identity = digest({"tool": story.SECTION_RUN, "outline": outline.sha256, "units": units,
                       "style": project.frozen_style_version_id, "assets": asset_hash})
    old = db.scalar(select(AgentTaskRecord).where(AgentTaskRecord.request_identity == identity))
    if old:
        return {"task_id": old.id, "status": old.status, "replayed": True}
    row = AgentTaskRecord(project_id=project_id, role_id=story.SECTION_RUN, request_identity=identity,
        input_hash=identity, asset_manifest_hash=asset_hash, style_version_id=project.frozen_style_version_id,
        rulepack_id=story.VERSION, rulepack_hash=digest(story.VERSION), model_profile_id="crew-role-routing",
        model_id="existing-film-crew", schema_version="1.0", status="QUEUED",
        trace={"document_id": document.id, "document_sha256": document.sha256, "stage": "STORYBOARD",
               "outline_artifact_id": outline.id, "outline_sha256": outline.sha256,
               "style_version_id": project.frozen_style_version_id, "asset_manifest_hash": asset_hash,
               "locked_facts": outline.payload_json["locked_facts"], "units": units, "cursor": 0, "results": []})
    db.add(row)
    db.flush()
    add_audit(db, actor, "STORY_SECTIONS_SELECTED", "agent_task", row.id,
              {"production_section_ids": body.production_section_ids}, project_id=project_id)
    db.commit()
    return {"task_id": row.id, "status": row.status, "replayed": False}


class TaskControl(APIModel):
    expected_state_version: int = Field(ge=0)
    subtask_id: str | None = Field(default=None, max_length=36)
    evidence: str | None = Field(default=None, min_length=1, max_length=500)


class ResendUnknownBody(APIModel):
    """One person's explicit decision to ask an unknown scene again."""

    expected_state_version: int = Field(ge=0)
    subtask_id: str = Field(min_length=1, max_length=36)
    evidence: str = Field(min_length=1, max_length=500)


@router.post("/projects/{project_id}/story-preparation/tasks/{task_id}/resend-unknown")
def resend_unknown_subtask(project_id: str, task_id: str, body: ResendUnknownBody,
                           actor: Operator, db: DBSession):
    """Ask one unknown scene again, because a person decided to.

    The old request may still own an answer upstream, so this never happens
    by itself: the person confirms, the decision is audited with the evidence
    they gave, and the scene is asked exactly once more.  The old task is
    never recorded as "not sent" - it stays in the ledger as an unknown result
    that a person overrode.
    """
    db.execute(text("BEGIN IMMEDIATE"))
    row = db.get(AgentTaskRecord, task_id)
    if not row or row.project_id != project_id or row.role_id not in {story.OUTLINE_RUN, story.SECTION_RUN}:
        raise HTTPException(404, "STORY_TASK_NOT_FOUND")
    if row.state_version != body.expected_state_version:
        raise HTTPException(409, "STORY_TASK_STATE_CHANGED")
    decision = story.authorize_unknown_resend(db, row, body.subtask_id, actor, body.evidence)
    db.commit()
    return {**public_task(db, row), "resubmitted": False, "decision": decision,
            "note": "已登记你的确认：这一场会以一次新的模型请求重问；旧请求的结果仍然未知，不会被自动采用。"}


@router.post("/projects/{project_id}/story-preparation/tasks/{task_id}/{action}")
def control(project_id: str, task_id: str, action: str, body: TaskControl, actor: Operator, db: DBSession):
    db.execute(text("BEGIN IMMEDIATE"))
    row = db.get(AgentTaskRecord, task_id)
    from shine.director.streamline import DIRECTOR_STORYBOARD_RUN

    if not row or row.project_id != project_id or row.role_id not in {
            story.OUTLINE_RUN, story.SECTION_RUN, DIRECTOR_STORYBOARD_RUN}:
        raise HTTPException(404, "STORY_TASK_NOT_FOUND")
    if action not in {"pause", "cancel", "resume", "reconcile", "replace-unknown", "retry"}:
        raise HTTPException(422, "STORY_ACTION_UNKNOWN")
    if row.role_id == DIRECTOR_STORYBOARD_RUN and action not in {"pause", "resume", "cancel"}:
        raise HTTPException(422, "STORY_ACTION_UNKNOWN")
    if action == "replace-unknown":
        raise HTTPException(409, "AGENT_UNKNOWN_OUTCOME_REQUIRES_RECONCILIATION")
    if row.state_version != body.expected_state_version:
        raise HTTPException(409, "STORY_TASK_STATE_CHANGED")
    if action == "retry":
        if row.role_id != story.OUTLINE_RUN:
            raise HTTPException(409, "STORY_TASK_RETRY_UNAVAILABLE")
        retry = _outline_retry_view(db, row)
        if not retry["can_retry"]:
            raise HTTPException(409, "STORY_TASK_RETRY_UNAVAILABLE")
        checked = story.reconcile_blocked_subtasks(db, row, actor)
        if not checked["resumable"]:
            unknown_id = retry["unknown_subtask_id"]
            if not unknown_id or body.subtask_id != unknown_id or not body.evidence:
                raise HTTPException(409, "STORY_UNKNOWN_SUBTASK_RECONCILIATION_REQUIRED")
            story.authorize_unknown_resend(db, row, unknown_id, actor, body.evidence)
        row.trace = {**row.trace, "last_retry_failure": retry["failure"]}
        action = "resume"
    blocked = row.trace.get("blocked_subtasks", [])
    if action == "reconcile":
        if row.status == "RUNNING" and row.lease_expires_at and row.lease_expires_at > utcnow():
            raise HTTPException(409, "STORY_TASK_STILL_RUNNING")
        result = story.reconcile_blocked_subtasks(db, row, actor)
        db.commit()
        return {**public_task(db, row), "resubmitted": False, "reconciliation": result,
                "note": "按证据核对每个阻塞子任务：已落库的产物直接采用；能证明请求从未发出的子任务"
                        "标记为未发送并注销；可能已发出的保持待核对，绝不自动重发。"}
    if action == "resume" and any(
        s.get("status") in {"RUNNING", "NEEDS_REVIEW", "FAILED"}
        or s.get("reconciliation") in {"UNCONFIRMABLE", "RETRIES_EXHAUSTED"}
        for s in blocked
    ):
        raise HTTPException(409, "STORY_UNKNOWN_SUBTASK_RECONCILIATION_REQUIRED")
    if row.status in {"SUCCEEDED", "CANCELLED"}:
        return public_task(db, row)
    row.status = {"pause": "PAUSED", "cancel": "CANCELLED", "resume": "QUEUED"}[action]
    if action in {"resume", "pause"}:
        # The last failure belongs to the run that ended, not to this one; a
        # resumed task that still shows "需要处理：IntegrityError" reads like it
        # is broken while it is queued again.
        row.error_code = None
    if action == "resume":
        # The blocks were decided by the reconciliation above; keeping them
        # would make the next successful step report the old reason again.
        state = dict(row.trace)
        if state.get("blocked_result"):
            state["last_blocked_result"] = state["blocked_result"]
            state.pop("blocked_result", None)
        row.trace = {**state, "blocked_subtasks": []}
    row.state_version += 1
    row.lease_owner, row.lease_expires_at = None, None
    add_audit(db, actor, f"STORY_TASK_{action.upper()}", "agent_task", row.id, project_id=project_id)
    db.commit()
    return public_task(db, row)
