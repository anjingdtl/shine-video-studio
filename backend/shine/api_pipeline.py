"""The four-stage pipeline API: import, outline, script, storyboard."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from shine.api_common import DBSession
from shine.api_common import llm_gateway as llm_gateway_dep
from shine.api_operator import Operator
from shine.director import pipeline
from shine.h3.rulebook import digest
from shine.llm.gateway import LLMGateway
from shine.models import AgentArtifact, ProjectDocument
from shine.services import require_project

router = APIRouter(prefix="/api/v1", tags=["pipeline"])

_LOCAL = {"x-shine-local-action": "1"}


def _local_actor(operator: Operator) -> str:
    return operator if isinstance(operator, str) else "local-user"


def _http_from_value_error(exc: ValueError) -> HTTPException:
    code = str(exc)
    return HTTPException(422, {"code": code, "message": code})


@router.get("/projects/{project_id}/pipeline")
def pipeline_overview(project_id: str, db: DBSession) -> dict[str, Any]:
    """The pipeline's real state: master, plan, bible, per-volume progress."""
    require_project(db, project_id)
    plan = pipeline.current_volume_plan(db, project_id)
    bible = pipeline.current_bible(db, project_id)
    # One query for the whole project: the panel shows one Chinese line per
    # section, never the internal check list.
    qa_states = pipeline.qa_states_by_section(db, project_id)
    volumes = []
    if plan:
        for volume in plan.payload_json["volumes"]:
            volume_id = volume["volume_id"]
            outline = pipeline._volume_outline(db, project_id, volume_id)
            sections = (outline.payload_json.get("production_sections") or []) if outline else []
            scripts = [
                {
                    "section_id": s["production_section_id"],
                    "scene_key": s.get("scene_key", s["production_section_id"]),
                    "title": s.get("title"),
                    "confirmed": pipeline.current_scene_script(
                        db, project_id, volume_id, s["production_section_id"]) is not None,
                    "qa": qa_states.get(s["production_section_id"]),
                }
                for s in sections
            ]
            latest_outline_row = None
            volume_doc = pipeline._volume_document(db, project_id, volume_id)
            if volume_doc is not None:
                latest_outline_row = db.scalar(select(AgentArtifact).where(
                    AgentArtifact.project_id == project_id,
                    AgentArtifact.kind == "StoryOutline",
                    AgentArtifact.stale.is_(False),
                    AgentArtifact.payload_json["document_id"].as_string() == volume_doc.id,
                    AgentArtifact.payload_json["document_sha256"].as_string() == volume_doc.sha256,
                ).order_by(AgentArtifact.created_at.desc(), AgentArtifact.id.desc()).limit(1))
                questions = (list(latest_outline_row.payload_json.get("questions") or [])
                             if latest_outline_row else [])
            volumes.append({
                "volume_id": volume_id, "order": volume["order"], "title": volume["title"],
                "char_count": volume.get("char_count"),
                "outline_confirmed": outline is not None,
                "latest_outline": ({"artifact_id": latest_outline_row.id,
                                    "sha256": latest_outline_row.sha256,
                                    "questions": questions,
                                    "questions_sha256": (digest({"artifact": latest_outline_row.id,
                                                                 "questions": questions})
                                                         if questions else None)}
                                   if latest_outline_row else None),
                "scripts": scripts,
                "scripts_confirmed": sum(1 for s in scripts if s["confirmed"]),
                "scripts_total": len(scripts),
                # The authoritative per-unit state of this volume.  The panel
                # renders from it, so a refresh restores every pending apply,
                # every failed unit and the delivery receipt.
                "storyboard": pipeline.volume_storyboard_state(db, project_id, volume_id),
                "episode_contracts": pipeline.volume_episode_contracts(db, project_id, volume_id),
                "handoff": pipeline.volume_handoff_receipt(db, project_id, volume_id),
            })
    master_ref = plan.payload_json["source_master"] if plan else {"document_id": None, "sha256": None}
    pending_bible = pipeline.latest_unconfirmed(db, project_id, pipeline.BIBLE_KIND)
    pending_scripts = []
    for volume in volumes:
        for entry in volume.get("scripts", []):
            script = pipeline.latest_unconfirmed_by_section(
                db, project_id, volume["volume_id"], entry["section_id"])
            if script is not None:
                pending_scripts.append({
                    "volume_id": volume["volume_id"],
                    "section_id": entry["section_id"],
                    "artifact_id": script.id, "sha256": script.sha256,
                    "scene_key": entry["scene_key"], "title": entry["title"],
                    "text": (script.payload_json or {}).get("text", ""),
                    "qa": qa_states.get(entry["section_id"]),
                })
    return {
        "master": master_ref,
        "volume_plan_confirmed": plan is not None,
        # The confirmed plan revision, so the boundary editor knows what it
        # is changing.
        "volume_plan": ({"artifact_id": plan.id, "sha256": plan.sha256,
                         "volumes": plan.payload_json.get("volumes") or []}
                        if plan is not None else None),
        "bible_confirmed": bible is not None,
        # Whether the confirmed blueprint still describes the current work.
        # The read path, the confirmation and the production path read the
        # same check, so they can never disagree.
        "bible_status": ({"artifact_id": bible.id, "sha256": bible.sha256,
                          "blockers": pipeline.bible_basis_blockers(db, project_id, bible)}
                         if bible is not None else None),
        "pending_bible": ({"artifact_id": pending_bible.id, "sha256": pending_bible.sha256}
                          if pending_bible else None),
        "pending_scripts": pending_scripts,
        "active_tasks": pipeline.pipeline_task_summary(db, project_id),
        "volumes": volumes,
    }


@router.post("/projects/{project_id}/pipeline/sources", status_code=201)
def import_source(project_id: str, body: dict, db: DBSession) -> dict[str, Any]:
    """Set the master document and produce the draft volume plan."""
    require_project(db, project_id)
    document_id = body.get("document_id")
    expected = body.get("expected_document_hash")
    document = db.get(ProjectDocument, document_id)
    if document is None or document.project_id != project_id:
        raise HTTPException(404, "DOCUMENT_NOT_FOUND")
    if expected and expected != document.sha256:
        raise HTTPException(409, "DOCUMENT_HASH_MISMATCH")
    try:
        plan = pipeline.plan_volumes(db, project_id, document)
        db.commit()
    except ValueError as exc:
        raise _http_from_value_error(exc) from None
    return {"volume_plan_id": plan.id, "sha256": plan.sha256,
            "volumes": plan.payload_json["volumes"], "coverage": plan.payload_json["coverage"]}


@router.post("/projects/{project_id}/pipeline/volume-plans/{artifact_id}/confirm", status_code=200)
def confirm_plan_route(project_id: str, artifact_id: str, body: dict, db: DBSession, operator: Operator) -> dict:
    """A person confirms the volume plan; volume documents are materialized."""
    from shine.api_preparation import locked

    locked(db)
    require_project(db, project_id)
    try:
        artifact = pipeline.confirm_volume_plan(db, project_id, artifact_id, body.get("expected_sha256"))
        docs = pipeline.materialize_volume_documents(db, project_id, artifact_id)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _http_from_value_error(exc) from None
    return {"confirmed": True, "sha256": artifact.sha256, "volume_documents": [d.id for d in docs]}


@router.post("/projects/{project_id}/pipeline/volumes/{volume_id}/outline", status_code=202)
def queue_volume_outline(
        project_id: str, volume_id: str, db: DBSession,
        gateway: Annotated[LLMGateway, Depends(llm_gateway_dep)]) -> dict:
    """Queue the outline extraction run on one volume's derived document."""
    from shine.materialprep.story import queue_outline

    require_project(db, project_id)
    plan = pipeline.current_volume_plan(db, project_id)
    if plan is None:
        raise HTTPException(409, "PIPELINE_VOLUME_PLAN_NOT_CONFIRMED")
    volume = next((v for v in plan.payload_json["volumes"] if v["volume_id"] == volume_id), None)
    if volume is None:
        raise HTTPException(404, "VOLUME_NOT_IN_PLAN")
    doc = pipeline._volume_document(db, project_id, volume_id)
    if doc is None:
        raise HTTPException(409, "PIPELINE_VOLUME_DOCUMENT_MISSING")
    task = queue_outline(db, project_id, doc, gateway, facts=[])
    db.commit()
    return {"task_id": task.id, "document_id": doc.id, "status": task.status}


@router.post("/projects/{project_id}/pipeline/bible", status_code=202)
def queue_bible_route(project_id: str, db: DBSession, gateway: Annotated[LLMGateway, Depends(llm_gateway_dep)]) -> dict:
    """Queue the global story bible synthesis task."""
    require_project(db, project_id)
    try:
        task = pipeline.queue_bible(db, project_id, gateway)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _http_from_value_error(exc) from None
    return JSONResponse(status_code=202, content={"task_id": task.id, "status": task.status})


@router.post("/projects/{project_id}/pipeline/bibles/{artifact_id}/confirm", status_code=200)
def confirm_bible_route(project_id: str, artifact_id: str, body: dict, db: DBSession, operator: Operator) -> dict:
    require_project(db, project_id)
    try:
        artifact = pipeline.confirm_bible(db, project_id, artifact_id, body.get("expected_sha256"))
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _http_from_value_error(exc) from None
    return {"confirmed": True, "sha256": artifact.sha256}


@router.post("/projects/{project_id}/pipeline/volumes/{volume_id}/scripts", status_code=202)
def queue_scripts_route(
        project_id: str, volume_id: str, db: DBSession,
        gateway: Annotated[LLMGateway, Depends(llm_gateway_dep)]) -> dict:
    """Queue one script task per section that still lacks a confirmed script."""
    require_project(db, project_id)
    try:
        tasks = pipeline.queue_scene_scripts(db, project_id, volume_id, gateway)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _http_from_value_error(exc) from None
    return JSONResponse(status_code=202, content={"queued": len(tasks),
        "task_ids": [t.id for t in tasks]})


@router.get("/projects/{project_id}/pipeline/volumes/{volume_id}/sections/{section_id}/qa")
def section_qa_route(project_id: str, volume_id: str, section_id: str, db: DBSession) -> dict:
    """The latest literary review of one section, or an honest NOT_RUN.

    A review is evidence, not state: it never changes whether a script exists.
    """
    require_project(db, project_id)
    report = pipeline.latest_section_qa_report(db, project_id, volume_id, section_id)
    if report is None:
        return {"state": "NOT_RUN", "checks": []}
    return {"artifact_id": report.id, "sha256": report.sha256,
            **(report.payload_json or {})}


@router.post("/projects/{project_id}/pipeline/scene-scripts/{artifact_id}/confirm", status_code=200)
def confirm_script_route(project_id: str, artifact_id: str, body: dict, db: DBSession, operator: Operator) -> dict:
    require_project(db, project_id)
    try:
        artifact = pipeline.confirm_scene_script(db, project_id, artifact_id, body.get("expected_sha256"))
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _http_from_value_error(exc) from None
    return {"confirmed": True, "sha256": artifact.sha256}


@router.post("/projects/{project_id}/pipeline/scene-scripts/{artifact_id}/edit", status_code=201)
def edit_script_route(project_id: str, artifact_id: str, body: dict, db: DBSession, operator: Operator) -> dict:
    """A person's edit creates a new unconfirmed version; the old stays history."""
    require_project(db, project_id)
    text = body.get("text")
    if not text or not isinstance(text, str) or not text.strip():
        raise HTTPException(422, "PIPELINE_SCENE_SCRIPT_TEXT_REQUIRED")
    try:
        artifact = pipeline.edit_scene_script(db, project_id, artifact_id, text, body.get("note", ""))
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _http_from_value_error(exc) from None
    return {"artifact_id": artifact.id, "sha256": artifact.sha256, "confirmed": False}


@router.post("/projects/{project_id}/pipeline/volumes/{volume_id}/storyboard-draft", status_code=201)
def storyboard_draft_route(
        project_id: str, volume_id: str,
        body: dict | None = None, db: DBSession = None,
        gateway: Annotated[LLMGateway, Depends(llm_gateway_dep)] = None,
        request: Request = None) -> dict:
    """Run the crew chain in draft mode over the volume's confirmed scripts.

    The chain runs once per scene, so a project-length volume is written in
    bounded batches instead of being refused by an inline limit.  The response
    carries every unit's apply handle: the panel applies each scene's camera
    plan, and the handoff verifies the resulting scene -> shots mapping.
    """
    from shine.director.pipeline import record_storyboard_draft, volume_storyboard_draft

    require_project(db, project_id)
    only = (body or {}).get("section_ids")
    only_section_ids = [str(item) for item in only] if isinstance(only, list) and only else None
    try:
        script_text = pipeline.serialize_volume_script(db, project_id, volume_id)
        from shine.media.output_layout import output_root_for_request

        result = volume_storyboard_draft(
            db, project_id, volume_id, gateway,
            data_dir=output_root_for_request(request, db),
            only_section_ids=only_section_ids)
        record = record_storyboard_draft(db, project_id, volume_id, result, script_text)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _http_from_value_error(exc) from None
    # The response carries the persistent state, not just this run: a unit
    # that was produced earlier stays listed with its apply handle, so the
    # panel can restore the whole volume after a refresh.
    state = pipeline.volume_storyboard_state(db, project_id, volume_id)
    return {"status": result.get("status"), "record": record,
            "units": state["units"],
            "state": state,
            "steps": {k: v for k, v in (result.get("steps") or {}).items()},
            "next": result.get("next"), "rejections": result.get("rejections")}


@router.get("/projects/{project_id}/pipeline/volumes/{volume_id}")
def volume_pipeline_state(project_id: str, volume_id: str, db: DBSession) -> dict[str, Any]:
    """The authoritative state of one volume.

    It is the read-back point of the production chain: the draft, the
    per-unit status, the pending applies, the failures with their
    reconciliation handles, the episode contracts and the delivery receipt
    with the exact package id.
    """
    require_project(db, project_id)
    plan = pipeline.current_volume_plan(db, project_id)
    if plan is None:
        raise HTTPException(409, "PIPELINE_VOLUME_PLAN_NOT_CONFIRMED")
    volume = next((v for v in plan.payload_json["volumes"] if v["volume_id"] == volume_id), None)
    if volume is None:
        raise HTTPException(404, "VOLUME_NOT_IN_PLAN")
    state = pipeline.volume_production_state(db, project_id, volume_id)
    return {"volume_id": volume_id, "title": volume.get("title"),
            "order": volume.get("order"), **state}


@router.get("/projects/{project_id}/pipeline/volumes/{volume_id}/handoff")
def volume_handoff_receipt_route(project_id: str, volume_id: str, db: DBSession) -> dict[str, Any]:
    """The delivery receipt of one volume, with the exact package id."""
    require_project(db, project_id)
    receipt = pipeline.volume_handoff_receipt(db, project_id, volume_id)
    if receipt is None:
        raise HTTPException(404, "PIPELINE_VOLUME_NOT_HANDED_OFF")
    return receipt


def _applied_shots(db, camera_artifact_id: str | None) -> dict:
    """Whether this scene's camera plan was applied, and how many shots it made."""
    return pipeline.applied_shots_for(db, camera_artifact_id)


@router.post("/projects/{project_id}/pipeline/volumes/{volume_id}/handoff", status_code=201)
def handoff_route(project_id: str, volume_id: str, body: dict, request: Request,
                  db: DBSession, operator: Operator) -> dict:
    """Publish the volume to the existing production machinery (plan+package)."""
    from shine.director.pipeline import build_volume_handoff

    require_project(db, project_id)
    workflow_profile_id = (body or {}).get("workflow_profile_id")
    if not workflow_profile_id:
        raise HTTPException(422, "PIPELINE_WORKFLOW_PROFILE_REQUIRED")
    try:
        result = build_volume_handoff(
            db, project_id, volume_id, workflow_profile_id,
            acknowledge_scene_coverage=bool((body or {}).get("acknowledge_scene_coverage")),
            settings=request.app.state.settings)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _http_from_value_error(exc) from None
    return result


@router.post("/projects/{project_id}/pipeline/volume-plans/{artifact_id}/boundaries", status_code=201)
def adjust_boundaries_route(project_id: str, artifact_id: str, body: dict, db: DBSession, operator: Operator) -> dict:
    """A person's manual volume boundaries become a new draft plan revision."""
    from shine.director.pipeline import adjust_volume_boundaries

    require_project(db, project_id)
    volumes = (body or {}).get("volumes")
    if not isinstance(volumes, list) or not volumes:
        raise HTTPException(422, "PIPELINE_VOLUME_EDITS_EMPTY")
    try:
        artifact = adjust_volume_boundaries(db, project_id, artifact_id, volumes)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _http_from_value_error(exc) from None
    return {"volume_plan_id": artifact.id, "sha256": artifact.sha256,
            "volumes": artifact.payload_json["volumes"]}


@router.post("/projects/{project_id}/pipeline/volumes/{volume_id}/episode-scripts", status_code=201)
def build_episode_script_route(project_id: str, volume_id: str, body: dict, db: DBSession, operator: Operator) -> dict:
    """Draft one episode's script contract from confirmed scene scripts."""
    from shine.director.pipeline import build_episode_script

    require_project(db, project_id)
    title = str((body or {}).get("title") or "").strip()
    section_ids = (body or {}).get("section_ids")
    if not title or not isinstance(section_ids, list) or not section_ids:
        raise HTTPException(422, "PIPELINE_EPISODE_SECTIONS_INVALID")
    try:
        artifact = build_episode_script(db, project_id, volume_id, title, section_ids)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _http_from_value_error(exc) from None
    return {"artifact_id": artifact.id, "sha256": artifact.sha256,
            "continuity_issues": artifact.payload_json.get("continuity_issues", []),
            "planned_seconds": artifact.payload_json.get("planned_seconds")}


@router.post("/projects/{project_id}/pipeline/episode-scripts/{artifact_id}/confirm", status_code=200)
def confirm_episode_script_route(
        project_id: str, artifact_id: str, body: dict, db: DBSession,
        operator: Operator) -> dict:
    require_project(db, project_id)
    try:
        artifact = pipeline.confirm_episode_script(db, project_id, artifact_id, body.get("expected_sha256"))
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _http_from_value_error(exc) from None
    return {"confirmed": True, "sha256": artifact.sha256,
            "continuity_status": artifact.payload_json.get("continuity_status")}


@router.post("/projects/{project_id}/pipeline/volumes/{volume_id}/outline-confirm")
def confirm_volume_outline_route(
        project_id: str, volume_id: str, body: dict, db: DBSession, operator: Operator) -> dict:
    """Confirm one volume's outline from the four-stage panel.

    Same gates as the expert confirmation (sha, questions acknowledgement,
    section invalidation); the volume document must own the outline.
    """
    from shine.materialprep.story import confirm_outline_artifact

    require_project(db, project_id)
    plan = pipeline.current_volume_plan(db, project_id)
    if plan is None:
        raise HTTPException(409, "PIPELINE_VOLUME_PLAN_NOT_CONFIRMED")
    doc = pipeline._volume_document(db, project_id, volume_id)
    if doc is None:
        raise HTTPException(409, "PIPELINE_VOLUME_DOCUMENT_MISSING")
    artifact_id = (body or {}).get("outline_artifact_id")
    expected = (body or {}).get("expected_sha256")
    row = db.get(AgentArtifact, artifact_id)
    if (row is None or row.project_id != project_id or row.kind != "StoryOutline" or row.stale
            or row.sha256 != expected or row.sha256 != digest(row.payload_json)
            or row.payload_json.get("document_id") != doc.id):
        raise HTTPException(409, "STORY_OUTLINE_CHANGED")
    result = confirm_outline_artifact(
        db, project_id, row, expected_sha256=expected,
        acknowledge_questions_sha256=(body or {}).get("acknowledge_questions_sha256"),
        actor=operator)
    db.commit()
    return result


@router.post("/projects/{project_id}/pipeline/tasks/{task_id}/resume")
def resume_pipeline_task_route(project_id: str, task_id: str, body: dict, db: DBSession, operator: Operator) -> dict:
    """Re-queue a blocked pipeline task after the person fixed its cause."""
    require_project(db, project_id)
    try:
        result = pipeline.resume_pipeline_task(db, project_id, task_id,
                                               (body or {}).get("expected_state_version"))
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _http_from_value_error(exc) from None
    return result


@router.post("/projects/{project_id}/pipeline/tasks/{task_id}/reconcile")
def reconcile_pipeline_task_route(project_id: str, task_id: str, body: dict, db: DBSession,
                                   operator: Operator) -> dict:
    """Reconcile one four-stage task without contacting the provider.

    The conclusion is the server's: only a proven "not sent" may be
    continued as an ordinary resume, and an unknown outcome stays unknown
    until the person authorises a separate new attempt.
    """
    require_project(db, project_id)
    try:
        result = pipeline.reconcile_pipeline_task(
            db, project_id, task_id, (body or {}).get("expected_state_version"),
            actor=_local_actor(operator))
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _http_from_value_error(exc) from None
    return result


@router.post("/projects/{project_id}/pipeline/tasks/{task_id}/new-attempt", status_code=202)
def new_pipeline_attempt_route(project_id: str, task_id: str, body: dict, db: DBSession,
                               operator: Operator) -> dict:
    """Authorise a physically separate retry after an unknown send outcome.

    This is a different decision from "continue": the person accepts a second
    provider call while the first one may still have been answered.  The
    original attempt keeps its UNKNOWN outcome as history.
    """
    require_project(db, project_id)
    try:
        result = pipeline.start_pipeline_new_attempt(
            db, project_id, task_id, (body or {}).get("expected_state_version"),
            actor=_local_actor(operator))
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _http_from_value_error(exc) from None
    return result
