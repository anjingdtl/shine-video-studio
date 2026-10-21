from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import Field
from sqlalchemy import literal_column, select

from shine.agents.film import FilmAgentDispatcher, load_inputs
from shine.agents.film_contracts import CameraPlan, FilmAgentRegistry, FilmRunRequest, StyleProposal
from shine.api_common import DBSession, llm_gateway
from shine.h3.rulebook import PACK_ID, H3RulebookRegistry, RuleValidationReport, digest
from shine.llm.base import LLMError
from shine.llm.gateway import LLMGateway
from shine.models import (
    AgentArtifact,
    AgentRoute,
    AgentTaskRecord,
    AppSetting,
    Asset,
    ProviderProfile,
    RulePack,
    WorkflowProfile,
    utcnow,
)
from shine.schemas import APIModel, CreateJob, RenderRequest, ShotBindingInput, ShotSpec
from shine.services import add_audit, create_shot_record, make_render_request, require_project

router = APIRouter(prefix="/api/v1", tags=["film-crew", "rulebook"])


def _registry(request: Request):
    return H3RulebookRegistry(request.app.state.settings.data_dir / "h3-source-cache")


class RuleLockRequest(APIModel):
    rulepack_id: str = PACK_ID


class CandidatePack(APIModel):
    manifest: dict


class SourceImport(APIModel):
    source_path: str


class ApplyCamera(APIModel):
    continuity_artifact_id: str
    name_prefix: str = Field(default="专业分镜", max_length=80)


def _artifact(row: AgentArtifact):
    return {
        "id": row.id,
        "kind": row.kind,
        "payload_json": row.payload_json,
        "sha256": row.sha256,
        "producer_role": row.producer_role,
        "source_task_id": row.source_task_id,
        "input_revision_ids": row.input_revision_ids,
        "stale": row.stale,
        "approved_by": row.approved_by,
        "created_at": row.created_at,
    }


@router.get("/film-agents")
def film_agents(db: DBSession):
    rows = []
    for role, (label, contract, required, sections) in FilmAgentRegistry.roles.items():
        route = db.scalar(select(AgentRoute).where(AgentRoute.role == role))
        profile = db.get(ProviderProfile, route.provider_profile_id) if route else None
        rows.append(
            {
                "role": role,
                "label": label,
                "version": "1.1",
                "output_kind": contract.__name__,
                "output_schema": contract.model_json_schema(),
                "required_inputs": required,
                "required_rule_sections": sections,
                "tool_allowlist": ["propose"]
                + (["style_proposal"] if role in {"scriptwriter", "director", "cinematographer", "editor"} else []),
                "profile_id": profile.id if profile else None,
                "model": profile.model if profile else None,
                "permissions": "仅结构化建议；无主资产/风格审批、数据库写入或渲染提交权限",
            }
        )
    return rows


@router.post("/projects/{project_id}/film-agents/{role}/run", status_code=201)
def run_role(
    project_id: str,
    role: str,
    body: FilmRunRequest,
    request: Request,
    db: DBSession,
    gateway: Annotated[LLMGateway, Depends(llm_gateway)],
):
    try:
        return _artifact(
            FilmAgentDispatcher(gateway, request.app.state.settings.data_dir).run(db, project_id, role, body)
        )
    except LLMError as exc:
        raise HTTPException(status_code=422, detail=exc.code) from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.get("/projects/{project_id}/film-runs")
def runs(project_id: str, db: DBSession):
    require_project(db, project_id)
    return [
        {
            "id": r.id,
            "role_id": r.role_id,
            "input_hash": r.input_hash,
            "style_version_id": r.style_version_id,
            "asset_manifest_hash": r.asset_manifest_hash,
            "rulepack_id": r.rulepack_id,
            "rulepack_hash": r.rulepack_hash,
            "model_profile_id": r.model_profile_id,
            "model_id": r.model_id,
            "status": r.status,
            "error_code": r.error_code,
            "trace": r.trace,
            "created_at": r.created_at,
        }
        for r in db.scalars(
            select(AgentTaskRecord)
            .where(AgentTaskRecord.project_id == project_id)
            .order_by(AgentTaskRecord.created_at.desc(), literal_column("agent_task_records.rowid").desc())
            .limit(200)
        )
    ]


@router.get("/projects/{project_id}/film-artifacts")
def artifacts(project_id: str, db: DBSession):
    require_project(db, project_id)
    return [
        _artifact(a)
        for a in db.scalars(
            select(AgentArtifact)
            .where(AgentArtifact.project_id == project_id)
            .order_by(AgentArtifact.created_at.desc(), literal_column("agent_artifacts.rowid").desc())
            .limit(200)
        )
    ]


@router.post("/projects/{project_id}/film-artifacts/{artifact_id}/apply-camera")
def apply_camera(project_id: str, artifact_id: str, body: ApplyCamera, db: DBSession):
    try:
        project = require_project(db, project_id)
        artifact, continuity = load_inputs(db, project_id, [artifact_id, body.continuity_artifact_id])
        if artifact.kind != "CameraPlan" or continuity.kind != "ContinuityReport":
            raise ValueError("WRONG_HANDOFF_KIND")
        if (
            artifact_id not in continuity.input_revision_ids
            or continuity.payload_json["issues"]
            or continuity.payload_json["recommendation"] != "proceed_to_preflight"
        ):
            raise ValueError("CONTINUITY_REVIEW_REQUIRED")
        key = f"film-apply:{artifact_id}"
        applied = db.get(AppSetting, key)
        if applied:
            return applied.value
        parents = load_inputs(db, project_id, artifact.input_revision_ids)
        direction = next(a for a in parents if a.kind == "DirectorialPlan")
        scripts = load_inputs(db, project_id, direction.input_revision_ids)
        script = next(a for a in scripts if a.kind == "ScriptBreakdown")
        beats = {b["id"]: b for b in script.payload_json["beats"]}
        intents = {s["id"]: s for s in direction.payload_json["shots"]}
        created = []
        for camera in CameraPlan.model_validate(artifact.payload_json).shots:
            intent = intents[camera.intent_id]
            beat = beats[intent["beat_id"]]
            spec = ShotSpec(
                story_beat=intent["visual_description"],
                duration_target_s=intent["duration_s"],
                style_version_id=project.frozen_style_version_id,
                required_asset_ids=intent["asset_ids"],
                characters=beat["characters"],
                dialogue=beat["dialogue"],
                locked_story_facts=script.payload_json["locked_story_facts"],
                camera={
                    "composition": camera.composition,
                    "movement": camera.movement,
                    "lighting": camera.lighting,
                    "axis_rule": camera.axis,
                },
            )
            bindings = []
            for index, identity in enumerate(intent["asset_ids"]):
                asset = db.get(Asset, identity)
                if not asset or asset.project_id != project_id or not asset.approved or not asset.locked:
                    raise ValueError("ASSET_APPROVAL_CHANGED")
                use_as = {"image": "subject", "audio": "audio", "video": "video"}[asset.kind]
                bindings.append(ShotBindingInput(asset_id=identity, use_as=use_as, ordinal=index))
            shot = create_shot_record(
                db, project, name=f"{body.name_prefix}-{camera.intent_id}", spec=spec, bindings=bindings
            )
            db.flush()
            # Preflight before committing any new shot; rollback the entire application on error.
            make_render_request(db, project, shot, CreateJob(shot_id=shot.id))
            created.append(shot.id)
        artifact.approved_by = "user"
        artifact.approved_at = utcnow()
        result = {"shot_ids": created, "source_artifact_id": artifact.id}
        db.add(AppSetting(key=key, value=result))
        add_audit(db, "user", "FILM_CAMERA_PLAN_APPLIED", "agent_artifact", artifact.id, result, project_id=project_id)
        db.commit()
        return result
    except HTTPException:
        db.rollback()
        raise
    except (ValueError, StopIteration, KeyError):
        db.rollback()
        raise HTTPException(status_code=422, detail="FILM_HANDOFF_REJECTED") from None


@router.post("/projects/{project_id}/film-artifacts/{artifact_id}/style-draft")
def apply_style_suggestion(project_id: str, artifact_id: str, db: DBSession):
    from shine.services import update_style_draft

    require_project(db, project_id)
    try:
        artifact = load_inputs(db, project_id, [artifact_id])[0]
        if artifact.kind != "StyleProposal":
            raise ValueError("NOT_A_STYLE_PROPOSAL")
        proposal = StyleProposal.model_validate(artifact.payload_json)
        update_style_draft(db, require_project(db, project_id), proposal.suggestion)
        add_audit(db, "user", "FILM_STYLE_SUGGESTION_APPLIED", "agent_artifact", artifact.id, project_id=project_id)
        db.commit()
        return {"status": "DRAFT_ONLY", "requires_user_freeze": True}
    except ValueError:
        db.rollback()
        raise HTTPException(status_code=422, detail="STYLE_SUGGESTION_REJECTED") from None


@router.get("/h3-rulebook")
def rulebook(request: Request, db: DBSession, q: str = ""):
    registry = _registry(request)
    registry.ensure_builtin(db)
    packs = list(db.scalars(select(RulePack).order_by(RulePack.created_at)))
    db.commit()
    workflows = list(db.scalars(select(WorkflowProfile)))
    return {
        "packs": [
            {"id": p.id, "sha256": p.sha256, "approved_at": p.approved_at, "manifest": p.manifest} for p in packs
        ],
        "sources": registry.cache_status(),
        "rules": registry.search(query=q),
        "modes": ["T2VA", "I2VA", "FL2VA", "L2VA", "Ref2VA"],
        "raw_source_distribution": "DISALLOWED_PENDING_LICENSE_REVIEW",
        "machine_rules_status": "AVAILABLE_SHINE_AUTHORED",
        "local_workflow_capabilities": [
            {
                "id": w.id,
                "name": w.name,
                "mode": w.mode,
                "graph_sha256": w.graph_sha256,
                "status": "PENDING_LOCAL_H3",
                "validated_against": w.validated_against,
            }
            for w in workflows
        ],
    }


@router.post("/h3-rulebook/recheck")
def recheck(request: Request, db: DBSession):
    registry = _registry(request)
    for pack in db.scalars(select(RulePack)):
        try:
            registry.verify_pack(pack)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
    return {"sources": registry.cache_status(), "raw_text_required_for_machine_rules": False}


@router.post("/h3-rulebook/cache")
async def import_cache(request: Request, source_path: str, file: Annotated[UploadFile, File()]):
    content = await file.read(1024 * 1024 + 1)
    if len(content) > 1024 * 1024:
        raise HTTPException(status_code=413, detail="SOURCE_TOO_LARGE")
    try:
        _registry(request).import_source(source_path, content)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return {"status": "VERIFIED_LOCAL_CACHE_ONLY"}


@router.post("/h3-rulebook/candidates", status_code=201)
def propose_pack(body: CandidatePack, db: DBSession):
    registry = H3RulebookRegistry()
    original = registry.ensure_builtin(db)
    identity = body.manifest.get("id", "")
    if not isinstance(identity, str) or not 1 <= len(identity) <= 80 or db.get(RulePack, identity):
        raise HTTPException(status_code=422, detail="RULEPACK_ID_INVALID_OR_EXISTS")
    pack = RulePack(id=identity, manifest=body.manifest, sha256=digest(body.manifest))
    try:
        registry.verify_pack(pack)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    differences = {
        k: {"before": original.manifest.get(k), "after": v}
        for k, v in body.manifest.items()
        if original.manifest.get(k) != v
    }
    db.add(pack)
    add_audit(db, "user", "RULEPACK_CANDIDATE_CREATED", "rulepack", pack.id, {"diff": differences})
    db.commit()
    return {"id": pack.id, "sha256": pack.sha256, "diff": differences, "status": "REQUIRES_USER_APPROVAL"}


@router.post("/h3-rulebook/packs/{pack_id:path}/approve")
def approve_pack(pack_id: str, db: DBSession):
    pack = db.get(RulePack, pack_id)
    if not pack:
        raise HTTPException(status_code=404, detail="RULEPACK_NOT_FOUND")
    try:
        H3RulebookRegistry().verify_pack(pack)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if pack.approved_at is None:
        pack.approved_at = utcnow()
        add_audit(db, "user", "RULEPACK_APPROVED", "rulepack", pack.id, {"hash": pack.sha256})
    db.commit()
    return {"id": pack.id, "approved_at": pack.approved_at}


@router.post("/projects/{project_id}/rulepack/freeze")
def freeze_rulepack(project_id: str, body: RuleLockRequest, db: DBSession):
    require_project(db, project_id)
    try:
        lock = H3RulebookRegistry().lock(db, project_id, body.rulepack_id)
        add_audit(
            db,
            "user",
            "PROJECT_RULEPACK_FROZEN",
            "project",
            project_id,
            {"rulepack_id": lock.rulepack_id, "hash": lock.rulepack_hash},
            project_id=project_id,
        )
        db.commit()
        return {"rulepack_id": lock.rulepack_id, "rulepack_hash": lock.rulepack_hash}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.get("/projects/{project_id}/rulepack")
def project_rulepack(project_id: str, db: DBSession):
    require_project(db, project_id)
    pack = H3RulebookRegistry().for_project(db, project_id)
    return {
        "rulepack_id": pack.id if pack else None,
        "rulepack_hash": pack.sha256 if pack else None,
        "status": "FROZEN" if pack else "LEGACY_COMPILER_PIN_ONLY",
    }


@router.post("/h3-rulebook/validate", response_model=RuleValidationReport)
def validate_request(body: RenderRequest, db: DBSession):
    pack = db.get(RulePack, body.rulepack_id)
    if not pack or not pack.approved_at:
        raise HTTPException(status_code=409, detail="RULEPACK_NOT_APPROVED")
    try:
        return H3RulebookRegistry().validate(body, pack)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
