from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import ValidationError
from sqlalchemy import select

from shine.agents.planner import HeuristicScriptPlanner, LLMScriptPlanner, ShotPlanner, build_context
from shine.agents.runtime import ProducerAgentRuntime
from shine.agents.script import ScriptParseError, parse_document
from shine.agents.tools import AssetView
from shine.api_common import DBSession, llm_gateway, secret_store
from shine.llm.base import LLMError, LLMNotConfigured
from shine.llm.gateway import GatewayStructuredModel, LLMGateway
from shine.llm.secrets import SecretStore
from shine.models import Asset, ProjectDocument, Shot, StyleVersion
from shine.schemas import (
    AgentPlanRequest,
    AgentPlanResult,
    AgentShotRequest,
    ScriptBeat,
    ScriptEpisode,
    ScriptPlan,
    ScriptScene,
    ShotProposal,
    StyleBible,
)
from shine.security import safe_filename, sha256_bytes
from shine.services import add_audit, create_shot_record, require_project

router = APIRouter(prefix="/api/v1", tags=["agent"])

MAX_SCRIPT_BYTES = 20 * 1024 * 1024
SecretDependency = Annotated[SecretStore, Depends(secret_store)]


def _document_data(document: ProjectDocument) -> dict[str, Any]:
    return {
        "id": document.id,
        "project_id": document.project_id,
        "filename": document.filename,
        "content_type": document.content_type,
        "sha256": document.sha256,
        "source_format": document.source_format,
        "created_at": document.created_at,
        "text_preview": document.text[:400],
        "text_length": len(document.text),
    }


def _asset_views(db, project_id: str) -> list[AssetView]:
    rows = db.scalars(select(Asset).where(Asset.project_id == project_id).order_by(Asset.created_at)).all()
    return [
        AssetView(
            id=asset.id,
            filename=asset.filename,
            kind=asset.kind,
            role=asset.role or "",
            entity_id=asset.entity_id or "",
            tags=tuple(asset.tags or []),
            duration_s=asset.duration_s,
            approved=asset.approved,
            locked=asset.locked,
        )
        for asset in rows
    ]


def _project_context(db, project) -> Any:
    style = None
    if project.frozen_style_version_id:
        version = db.get(StyleVersion, project.frozen_style_version_id)
        if version:
            style = StyleBible.model_validate(version.snapshot)
    shots = db.scalars(select(Shot).where(Shot.project_id == project.id).order_by(Shot.created_at)).all()
    return build_context(
        project_id=project.id,
        title=project.title,
        language=project.language,
        style_version_id=project.frozen_style_version_id or "",
        style=style,
        assets=_asset_views(db, project.id),
        shots=[
            {
                "id": shot.id,
                "name": shot.name,
                "revision": shot.revision,
                "story_beat": str(shot.spec.get("story_beat", ""))[:300],
            }
            for shot in shots
        ],
    )


@router.post("/projects/{project_id}/documents", status_code=201)
async def upload_document(project_id: str, db: DBSession, file: Annotated[UploadFile, File()]) -> dict[str, Any]:
    project = require_project(db, project_id)
    filename = safe_filename(file.filename)
    content = await file.read(MAX_SCRIPT_BYTES + 1)
    if len(content) > MAX_SCRIPT_BYTES:
        raise HTTPException(status_code=413, detail="Script documents are limited to 20 MiB")
    try:
        parsed = parse_document(filename, content)
    except ScriptParseError as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)}) from exc
    digest = sha256_bytes(content)
    duplicate = db.scalar(
        select(ProjectDocument).where(ProjectDocument.project_id == project.id, ProjectDocument.sha256 == digest)
    )
    if duplicate:
        return _document_data(duplicate)
    document = ProjectDocument(
        project_id=project.id,
        filename=filename,
        content_type=file.content_type or "application/octet-stream",
        sha256=digest,
        source_format=parsed.source_format,
        text=parsed.text,
        parsed=parsed.parsed or None,
    )
    db.add(document)
    db.flush()
    add_audit(
        db,
        "user",
        "SCRIPT_DOCUMENT_IMPORTED",
        "project_document",
        document.id,
        {"source_format": parsed.source_format, "sha256": digest, "text_length": len(parsed.text)},
        project_id=project.id,
    )
    db.commit()
    db.refresh(document)
    return _document_data(document)


@router.get("/projects/{project_id}/documents")
def list_documents(project_id: str, db: DBSession) -> list[dict[str, Any]]:
    require_project(db, project_id)
    rows = db.scalars(
        select(ProjectDocument)
        .where(ProjectDocument.project_id == project_id)
        .order_by(ProjectDocument.created_at.desc())
    ).all()
    return [_document_data(document) for document in rows]


def _resolve_script_text(db, project_id: str, body: AgentPlanRequest) -> tuple[str, str]:
    if body.document_id:
        document = db.get(ProjectDocument, body.document_id)
        if not document or document.project_id != project_id:
            raise HTTPException(status_code=404, detail="Script document not found in this project")
        return document.text, document.filename
    if body.text and body.text.strip():
        return body.text.strip(), "inline"
    raise HTTPException(status_code=422, detail="Provide script text or an imported document")


def _structured_model(
    db, secrets: SecretStore, role: str, gateway: LLMGateway
) -> tuple[GatewayStructuredModel | None, Any]:
    try:
        profile = gateway.profile_for_role(db, role)
    except LLMNotConfigured:
        return None, None
    return GatewayStructuredModel(gateway, db, role), profile


def _reject_agent_output(exc: Exception) -> HTTPException:
    if isinstance(exc, LLMError):
        return HTTPException(status_code=502, detail={"code": exc.code, "message": str(exc)})
    return HTTPException(status_code=422, detail={"code": "AGENT_OUTPUT_REJECTED", "message": str(exc)})


def _validate_proposals(
    proposals: list[ShotProposal],
    *,
    project,
    approved_assets: dict[str, Asset],
) -> list[ShotProposal]:
    cleaned: list[ShotProposal] = []
    for proposal in proposals:
        spec = proposal.spec
        if not spec.style_version_id:
            spec = spec.model_copy(update={"style_version_id": project.frozen_style_version_id})
        if spec.style_version_id != project.frozen_style_version_id:
            raise ValueError("Agent proposal selected a style version other than the current frozen style")
        unknown = [asset_id for asset_id in spec.required_asset_ids if asset_id not in approved_assets]
        if unknown:
            raise ValueError("Agent proposal referenced assets that are not user-approved and locked")
        if len({asset_id for asset_id in spec.required_asset_ids}) != len(spec.required_asset_ids):
            raise ValueError("Agent proposal repeated an asset reference")
        cleaned.append(ShotProposal(name=proposal.name, spec=spec))
    return cleaned


@router.post("/projects/{project_id}/agent/plan", response_model=AgentPlanResult)
def agent_plan(
    project_id: str,
    body: AgentPlanRequest,
    db: DBSession,
    secrets: SecretDependency,
    gateway: Annotated[LLMGateway, Depends(llm_gateway)],
) -> AgentPlanResult:
    project = require_project(db, project_id)
    if not project.frozen_style_version_id:
        raise HTTPException(status_code=409, detail="Freeze the Style Bible before agent shot planning")
    text, title = _resolve_script_text(db, project_id, body)
    context = _project_context(db, project)

    script_model, script_profile = _structured_model(db, secrets, "script_planner", gateway)
    warnings: list[str] = []
    if script_model is not None:
        try:
            plan_result = LLMScriptPlanner(script_model).plan(text, title=title)
        except (LLMError, ValidationError, ValueError) as exc:
            raise _reject_agent_output(exc) from exc
        planner = "llm"
    else:
        plan_result = HeuristicScriptPlanner().plan(text, title=title)
        planner = "heuristic"
        warnings.append("No script_planner LLM route is configured; the deterministic local planner was used")

    shot_model, shot_profile = _structured_model(db, secrets, "shot_planner", gateway)
    try:
        proposals, shot_planner_name, shot_warnings = ShotPlanner(model=shot_model).propose(
            plan_result.plan,
            style_version_id=project.frozen_style_version_id,
            style=context.style,
            context=context,
            max_shots=body.max_shots,
        )
    except (LLMError, ValidationError, ValueError) as exc:
        raise _reject_agent_output(exc) from exc
    warnings.extend(shot_warnings)
    approved_assets = {
        asset.id: asset
        for asset in db.scalars(
            select(Asset).where(
                Asset.project_id == project.id,
                Asset.approved.is_(True),
                Asset.locked.is_(True),
            )
        ).all()
    }
    try:
        proposals = _validate_proposals(proposals, project=project, approved_assets=approved_assets)
    except ValueError as exc:
        raise _reject_agent_output(exc) from exc

    applied: list[str] = []
    if body.apply:
        for proposal in proposals:
            shot = create_shot_record(
                db,
                project,
                name=proposal.name,
                spec=proposal.spec,
                actor="agent",
            )
            applied.append(shot.id)
    add_audit(
        db,
        "agent",
        "AGENT_PLAN_CREATED",
        "project",
        project.id,
        {
            "planner": planner,
            "shot_planner": shot_planner_name,
            "proposal_count": len(proposals),
            "applied_count": len(applied),
            "script_chars": len(text),
        },
        project_id=project.id,
    )
    db.commit()
    return AgentPlanResult(
        planner=planner,
        provider_profile_id=(shot_profile or script_profile).id if (shot_profile or script_profile) else None,
        model=(shot_profile or script_profile).model if (shot_profile or script_profile) else None,
        plan=plan_result.plan,
        shots=proposals,
        warnings=warnings,
        applied_shot_ids=applied,
    )


@router.post("/projects/{project_id}/agent/shots", response_model=AgentPlanResult)
def agent_shots(
    project_id: str,
    body: AgentShotRequest,
    db: DBSession,
    secrets: SecretDependency,
    gateway: Annotated[LLMGateway, Depends(llm_gateway)],
) -> AgentPlanResult:
    project = require_project(db, project_id)
    if not project.frozen_style_version_id:
        raise HTTPException(status_code=409, detail="Freeze the Style Bible before agent shot planning")
    model, profile = _structured_model(db, secrets, "shot_planner", gateway)
    if model is None:
        raise HTTPException(
            status_code=409,
            detail="No shot_planner LLM route is configured; configure a provider profile and route first",
        )
    runtime = ProducerAgentRuntime(model)
    try:
        spec = runtime.propose_shot(body.brief, style_version_id=project.frozen_style_version_id)
    except (LLMError, ValidationError, ValueError) as exc:
        raise _reject_agent_output(exc) from exc
    approved_assets = {
        asset.id: asset
        for asset in db.scalars(
            select(Asset).where(
                Asset.project_id == project.id,
                Asset.approved.is_(True),
                Asset.locked.is_(True),
            )
        ).all()
    }
    proposals = _validate_proposals(
        [ShotProposal(name=f"Agent Shot {len(spec.story_beat) % 97}", spec=spec)],
        project=project,
        approved_assets=approved_assets,
    )
    applied: list[str] = []
    if body.apply:
        shot = create_shot_record(db, project, name=proposals[0].name, spec=proposals[0].spec, actor="agent")
        applied.append(shot.id)
    plan = ScriptPlan(
        title=body.brief[:120],
        episodes=[
            ScriptEpisode(
                name="第一集",
                scenes=[
                    ScriptScene(
                        name="Agent brief",
                        beats=[ScriptBeat(summary=body.brief[:1000], characters=spec.characters)],
                    )
                ],
            )
        ],
    )
    add_audit(
        db,
        "agent",
        "AGENT_SHOT_PROPOSED",
        "project",
        project.id,
        {"applied_count": len(applied)},
        project_id=project.id,
    )
    db.commit()
    return AgentPlanResult(
        planner="llm",
        provider_profile_id=profile.id if profile else None,
        model=profile.model if profile else None,
        plan=plan,
        shots=proposals,
        warnings=[],
        applied_shot_ids=applied,
    )
