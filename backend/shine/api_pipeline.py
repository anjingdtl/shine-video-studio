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
                }
                for s in sections
            ]
            latest_outline_row = None
            volume_doc = pipeline._volume_document(db, project_id, volume_id)
            if volume_doc is not None:
                rows = db.scalars(select(AgentArtifact).where(
                    AgentArtifact.project_id == project_id,
                    AgentArtifact.kind == "StoryOutline").order_by(
                    AgentArtifact.created_at.desc())).all()
                latest_outline_row = next(
                    (r for r in rows if r.payload_json.get("document_id") == volume_doc.id), None)
            volumes.append({
                "volume_id": volume_id, "order": volume["order"], "title": volume["title"],
                "char_count": volume.get("char_count"),
                "outline_confirmed": outline is not None,
                "latest_outline": ({"artifact_id": latest_outline_row.id,
                                    "sha256": latest_outline_row.sha256,
                                    "questions": list(latest_outline_row.payload_json.get("questions") or [])}
                                   if latest_outline_row else None),
                "scripts": scripts,
                "scripts_confirmed": sum(1 for s in scripts if s["confirmed"]),
                "scripts_total": len(scripts),
            })
    master_ref = plan.payload_json["source_master"] if plan else {"document_id": None, "sha256": None}
    pending_bible = pipeline.latest_unconfirmed(db, project_id, pipeline.BIBLE_KIND)
    pending_scripts = []
    for volume in volumes:
        for entry in volume.get("scripts", []):
            script = pipeline.latest_unconfirmed_by_section(db, project_id, entry["section_id"])
            if script is not None:
                pending_scripts.append({
                    "volume_id": volume["volume_id"],
                    "section_id": entry["section_id"],
                    "artifact_id": script.id, "sha256": script.sha256,
                    "scene_key": entry["scene_key"], "title": entry["title"],
                    "text": (script.payload_json or {}).get("text", ""),
                })
    return {
        "master": master_ref,
        "volume_plan_confirmed": plan is not None,
        "bible_confirmed": bible is not None,
        "pending_bible": ({"artifact_id": pending_bible.id, "sha256": pending_bible.sha256}
                          if pending_bible else None),
        "pending_scripts": pending_scripts,
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
    """Run the crew chain in draft mode over the volume's confirmed scripts."""
    from shine.director.pipeline import record_storyboard_draft, volume_storyboard_draft

    require_project(db, project_id)
    try:
        script_text = pipeline.serialize_volume_script(db, project_id, volume_id)
        result = volume_storyboard_draft(db, project_id, volume_id, gateway,
                              data_dir=request.app.state.settings.data_dir)
        record = record_storyboard_draft(db, project_id, volume_id, result, script_text)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _http_from_value_error(exc) from None
    return {"status": result.get("status"), "record": record,
            "steps": {k: v for k, v in (result.get("steps") or {}).items()},
            "next": result.get("next"), "rejections": result.get("rejections")}


@router.post("/projects/{project_id}/pipeline/volumes/{volume_id}/handoff", status_code=201)
def handoff_route(project_id: str, volume_id: str, body: dict, db: DBSession, operator: Operator) -> dict:
    """Publish the volume to the existing production machinery (plan+package)."""
    from shine.director.pipeline import build_volume_handoff

    require_project(db, project_id)
    workflow_profile_id = (body or {}).get("workflow_profile_id")
    if not workflow_profile_id:
        raise HTTPException(422, "PIPELINE_WORKFLOW_PROFILE_REQUIRED")
    try:
        result = build_volume_handoff(
            db, project_id, volume_id, workflow_profile_id,
            acknowledge_scene_coverage=bool((body or {}).get("acknowledge_scene_coverage")))
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
