from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import Field, ValidationError
from sqlalchemy import literal_column, select

from shine.agents.film import FilmAgentDispatcher, asset_context, load_inputs
from shine.agents.film_contracts import CameraPlan, FilmAgentRegistry, FilmRunRequest, StyleInputWire, StyleProposal
from shine.api_common import DBSession, llm_gateway
from shine.api_operator import Operator
from shine.asset_identity import resolve_character_asset
from shine.h3.rulebook import PACK_ID, H3RulebookRegistry, RuleValidationReport, digest
from shine.llm.base import LLMError, text_provider_snapshot
from shine.llm.gateway import LLMGateway
from shine.models import (
    AgentArtifact,
    AgentRoute,
    AgentTaskRecord,
    AppSetting,
    Asset,
    ProjectDocument,
    ProviderProfile,
    RulePack,
    WorkflowProfile,
    utcnow,
)
from shine.schemas import APIModel, CreateJob, RenderRequest, ShotBindingInput, ShotSpec, StyleBible
from shine.security import is_loopback_url
from shine.services import add_audit, create_shot_record, make_render_request, require_project

router = APIRouter(prefix="/api/v1", tags=["film-crew", "rulebook"])

logger = logging.getLogger(__name__)


class AbandonRemoteResponse(APIModel):
    grant_id: str
    snapshot: str
    evidence: str = Field(min_length=1, max_length=2000)


@router.post("/projects/{project_id}/film-runs/{task_id}/abandon-remote-response")
def abandon_remote(project_id: str, task_id: str, body: AbandonRemoteResponse, actor: Operator, db: DBSession):
    from shine.agents.execution import abandon_remote_response
    from shine.api_preparation import locked

    locked(db)
    row = db.get(AgentTaskRecord, task_id)
    if not row or row.project_id != project_id:
        raise HTTPException(404, "Agent task not found for project")
    return abandon_remote_response(db, row, actor, body.grant_id, body.snapshot, body.evidence)


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
    arbitration_artifact_id: str | None = None
    name_prefix: str = Field(default="专业分镜", max_length=80)


def _arbitration_releases(arbitration: dict | None, issues: list[str]) -> bool:
    """Whether a crew arbitration releases every raised issue.

    A ruling is only usable when it answers every issue by name, cites rules
    that exist in the locked rule pack, and leaves no conflict standing.  The
    arbitration is a proposal: a human still applies the storyboard.
    """
    if not arbitration or not issues:
        return False
    rulings = arbitration.get("rulings") or []
    ruled = [ruling.get("issue") for ruling in rulings]
    if sorted(ruled) != sorted(issues) or len(ruled) != len(issues):
        return False
    known = {rule["id"] for rule in H3RulebookRegistry().search(())}
    if any(ruling.get("rule_id") not in known for ruling in rulings):
        return False
    if any(ruling.get("verdict") != "not_a_conflict" for ruling in rulings):
        return False
    return arbitration.get("recommendation") == "proceed_to_preflight"


def _load_arbitration(db, project_id: str, arbitration_id: str | None,
                      continuity_id: str, camera_id: str) -> dict | None:
    """The arbitration artifact of this project, when it really arbitrates this pair."""
    row = db.get(AgentArtifact, arbitration_id) if arbitration_id else None
    if (
        not row
        or row.project_id != project_id
        or row.stale
        or row.kind != "ContinuityArbitration"
    ):
        return None
    if continuity_id not in row.input_revision_ids or camera_id not in row.input_revision_ids:
        return None
    return row.payload_json


def _artifact(row: AgentArtifact):
    return {
        "id": row.id,
        "kind": row.kind,
        "schema_version": row.schema_version,
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
                "version": "1.3" if role == "comfyops" else "1.1",
                "output_kind": contract.__name__,
                "output_schema": contract.model_json_schema(),
                **({"model_response_protocol": "independent-text-v2",
                    "model_response_schema": StyleInputWire.model_json_schema()} if role == "qc_reviewer" else {}),
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
        issues = list(continuity.payload_json.get("issues") or [])
        arbitration = None
        if artifact_id not in continuity.input_revision_ids:
            raise ValueError("CONTINUITY_REVIEW_REQUIRED")
        if issues or continuity.payload_json.get("recommendation") != "proceed_to_preflight":
            # A conflict between the crew roles is released only by a valid
            # arbitration of the role that owns the shots, on the official
            # criteria of the locked rule pack.
            arbitration = _load_arbitration(
                db, project_id, body.arbitration_artifact_id, continuity.id, artifact_id
            )
            if not _arbitration_releases(arbitration, issues):
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
            # The scriptwriter and the director choose timings independently;
            # at this materialisation boundary the spoken beats are fitted
            # proportionally into the shot the director actually planned, so
            # the spec stays renderable without changing a single word.
            duration = float(intent["duration_s"])
            dialogue = []
            for line in beat["dialogue"]:
                line = dict(line)
                start, end = float(line["start_s"]), float(line["end_s"])
                if end > start:
                    latest = max(0.5, duration - 0.2)
                    if end > latest:
                        scale = (latest - 0.2) / (end - start) if end - start > 0 else 1.0
                        start = 0.2 + (start - min(start, 0.2)) * scale
                        start, end = min(start, latest - 0.1), latest
                        if end - start < 0.3:
                            start = max(0.2, end - 0.3)
                    line["start_s"], line["end_s"] = round(start, 3), round(end, 3)
                dialogue.append(line)
            beat = {**beat, "dialogue": dialogue}
            spec = ShotSpec(
                story_beat=intent["visual_description"],
                duration_target_s=intent["duration_s"],
                style_version_id=project.frozen_style_version_id,
                required_asset_ids=intent["asset_ids"],
                characters=beat["characters"],
                dialogue=beat["dialogue"],
                locked_story_facts=script.payload_json["locked_story_facts"],
                prompt_story_facts=script.payload_json.get("prompt_story_facts", []),
                prompt_style=script.payload_json.get("prompt_style"),
                camera={
                    "composition": camera.composition,
                    "movement": camera.movement,
                    "lighting": camera.lighting,
                    "axis_rule": camera.axis,
                },
                **(
                    {
                        "continuity": {
                            "mode": intent["continuity"]["mode"],
                            "previous_shot_id": created[-1],
                        }
                    }
                    if intent.get("continuity") and intent["continuity"].get("mode") not in (None, "cut")
                    and created
                    else {}
                ),
            )
            bindings = []
            for index, identity in enumerate(intent["asset_ids"]):
                asset = db.get(Asset, identity)
                if not asset or asset.project_id != project_id or not asset.approved or not asset.locked:
                    raise ValueError("ASSET_APPROVAL_CHANGED")
                # The approved master's role decides the reference family: a
                # character master is a subject reference, any other picture is
                # a picture reference; audio and video map to themselves.
                use_as = {
                    "image": "subject" if asset.role == "character" else "picture",
                    "audio": "audio",
                    "video": "video",
                }[asset.kind]
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
    except (ValueError, StopIteration, KeyError, ValidationError) as exc:
        db.rollback()
        logger.error("apply_camera_rejected artifact=%s reason=%s", artifact_id, exc)
        raise HTTPException(status_code=422, detail="FILM_HANDOFF_REJECTED") from None


class StyleDraftApply(APIModel):
    """Adopt an AI style proposal into the draft only when the server draft is
    exactly what the user was looking at (`base_style`); `None` means the
    server draft must still be empty.  Any other writer's change refuses the
    adoption so an in-flight proposal can never silently overwrite newer
    content (docs/UI_SIMPLIFICATION_PLAN_20261102.md §7.2)."""

    base_style: StyleBible | None


@router.post("/projects/{project_id}/film-artifacts/{artifact_id}/style-draft")
def apply_style_suggestion(project_id: str, artifact_id: str, body: StyleDraftApply, db: DBSession):
    from shine.services import update_style_draft

    project = require_project(db, project_id)
    try:
        artifact = load_inputs(db, project_id, [artifact_id])[0]
        if artifact.kind != "StyleProposal":
            raise ValueError("NOT_A_STYLE_PROPOSAL")
        expected = body.base_style.model_dump(mode="json") if body.base_style is not None else None
        if project.style_draft != expected:
            raise HTTPException(status_code=409, detail="STYLE_DRAFT_CHANGED")
        proposal = StyleProposal.model_validate(artifact.payload_json)
        update_style_draft(db, project, proposal.suggestion)
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


class StoryboardRequest(APIModel):
    """One complete-script storyboard generation request (five crew roles)."""

    document_id: str | None = None
    text: str | None = Field(default=None, max_length=60000)
    allow_remote_text: bool = False
    locked_story_facts: list[str] = Field(default_factory=list, max_length=100)


def _character_coverage(assets: list[dict], breakdown: dict) -> list[str]:
    """Characters without one uniquely matching approved character master.

    The rule mirrors ``validate_output`` for DirectorialPlan: every beat
    character must be covered by an approved, locked image/character asset,
    or the shots that need them cannot enter real production.
    """
    character_assets = [a for a in assets if a["kind"] == "image" and a["role"] == "character"]
    named: list[str] = []
    for beat in breakdown.get("beats", []):
        for name in beat.get("characters", []):
            if name and name not in named:
                named.append(name)
        for line in beat.get("dialogue", []):
            speaker = line.get("speaker_id") if isinstance(line, dict) else getattr(line, "speaker_id", None)
            if speaker and speaker not in named:
                named.append(speaker)
    return [name for name in named if resolve_character_asset(name, character_assets) is None]


@router.post("/projects/{project_id}/storyboard/generate", status_code=201)
def generate_storyboard(
    project_id: str,
    body: StoryboardRequest,
    request: Request,
    db: DBSession,
    gateway: Annotated[LLMGateway, Depends(llm_gateway)],
) -> dict:
    """Run the real crew chain on one complete script.

    scriptwriter -> director -> cinematographer -> continuity supervisor ->
    editor (frozen edit intent).  Every step is a real routed LLM call with
    its own task record and trace.  Characters without approved masters block
    the chain with an explicit missing list; nothing is auto-created.
    """
    import hashlib

    project = require_project(db, project_id)
    if not project.frozen_style_version_id:
        raise HTTPException(409, "STORYBOARD_STYLE_NOT_FROZEN")
    if body.document_id:
        document = db.get(ProjectDocument, body.document_id)
        if not document or document.project_id != project_id:
            raise HTTPException(404, "Script document not found in this project")
        script_text, source = document.text, f"document:{document.id}"
    elif body.text and body.text.strip():
        script_text, source = body.text.strip(), "inline"
    else:
        raise HTTPException(422, "STORYBOARD_SCRIPT_REQUIRED")

    needed_roles = ("scriptwriter", "director", "cinematographer", "continuity_supervisor", "editor")
    routes = {}
    for role in needed_roles:
        try:
            routes[role] = gateway.profile_for_role(db, role)
        except LLMError as exc:
            raise HTTPException(
                409, f"STORYBOARD_AGENT_ROUTE_MISSING:{role}:{exc.code}"
            ) from None
        if routes[role].kind != "mock" and not is_loopback_url(routes[role].base_url) and not body.allow_remote_text:
            raise HTTPException(403, "REMOTE_AGENT_CONSENT_REQUIRED")

    storyboard_request_sha256 = digest({
        "project_id": project_id,
        "script_sha256": hashlib.sha256(script_text.encode("utf-8")).hexdigest(),
        "locked_story_facts": body.locked_story_facts,
        "style_version_id": project.frozen_style_version_id,
        "approved_asset_manifest": asset_context(db, project_id),
        "agent_routes": {role: text_provider_snapshot(routes[role]) for role in needed_roles},
    })
    from shine.agents.execution import UNKNOWN_CODES

    for prior in db.scalars(select(AgentTaskRecord).where(
        AgentTaskRecord.project_id == project_id,
        AgentTaskRecord.role_id.in_(needed_roles),
    )):
        if prior.trace.get("storyboard_request_sha256") != storyboard_request_sha256:
            continue
        unresolved = (
            prior.status in {"RUNNING", "NEEDS_REVIEW", "RECONCILING"}
            or prior.status == "REJECTED" and prior.error_code in UNKNOWN_CODES
            and prior.trace.get("external_outcome") != "NOT_SENT"
            or prior.status == "ABANDONED" and prior.trace.get("external_outcome") == "UNKNOWN"
        )
        if unresolved and prior.trace.get("is_mock") is not True:
            code = ("AGENT_REQUEST_REPLAY_BLOCKED" if prior.status == "ABANDONED"
                    else "AGENT_REQUEST_RECONCILIATION_REQUIRED")
            return {
                "status": "REJECTED",
                "script_source": source,
                "script_sha256": hashlib.sha256(script_text.encode("utf-8")).hexdigest(),
                "steps": {},
                "missing_characters": [],
                "rejections": [{"role": prior.role_id, "code": code, "task_id": prior.id}],
            }

    dispatcher = FilmAgentDispatcher(gateway, request.app.state.settings.data_dir)

    # A definitive contract rejection is a per-sample property of a real text
    # model, not an uncertain external outcome: the guard keeps the rejected
    # task record, and a bounded second call is allowed.  Uncertain outcomes
    # (timeouts, unknown server errors) are never replayed here.
    RETRYABLE_REJECTIONS = {"CONTRACT_OR_POLICY_REJECTED", "AGENT_CALL_FAILED"}

    def _run(role: str, *, attempts: int = 2, **kwargs) -> dict:
        last: ValueError | LLMError | None = None
        for attempt in range(1, attempts + 1):
            try:
                artifact = dispatcher.run(
                    db,
                    project_id,
                    role,
                    FilmRunRequest(**kwargs),
                    storyboard_request_sha256=storyboard_request_sha256,
                )
                return {"artifact_id": artifact.id, "kind": artifact.kind,
                        "sha256": artifact.sha256, "attempt": attempt}
            except (ValueError, LLMError) as exc:
                last = exc
                code = str(exc)
                if code not in RETRYABLE_REJECTIONS or attempt == attempts:
                    raise
        raise last  # pragma: no cover - the loop always raises or returns

    def _rejection(role: str, exc: Exception) -> dict:
        rejection = {"role": role, "code": getattr(exc, "code", None) or str(exc) or "AGENT_CALL_FAILED"}
        details = getattr(exc, "details", {})
        if isinstance(details, dict) and details.get("external_outcome") in {"NOT_SENT", "UNKNOWN"}:
            rejection["external_outcome"] = details["external_outcome"]
        rows = db.scalars(select(AgentTaskRecord).where(
            AgentTaskRecord.project_id == project_id,
            AgentTaskRecord.role_id == role,
        ).order_by(AgentTaskRecord.created_at.desc()).limit(20))
        task = next((row for row in rows
                     if row.trace.get("storyboard_request_sha256") == storyboard_request_sha256), None)
        if task:
            rejection["task_id"] = task.id
            outcome = task.trace.get("external_outcome")
            if outcome in {"NOT_SENT", "UNKNOWN"}:
                rejection["external_outcome"] = outcome
        return rejection

    steps: dict[str, dict] = {}
    rejections: list[dict] = []
    try:
        steps["scriptwriter"] = _run(
            "scriptwriter",
            brief=script_text,
            locked_story_facts=body.locked_story_facts,
            allow_remote_text=body.allow_remote_text,
        )
    except (ValueError, LLMError) as exc:
        rejections.append(_rejection("scriptwriter", exc))
        return {
            "status": "REJECTED",
            "script_source": source,
            "steps": steps,
            "rejections": rejections,
        }

    breakdown = db.get(AgentArtifact, steps["scriptwriter"]["artifact_id"]).payload_json
    missing = _character_coverage(
        asset_context(db, project_id),
        breakdown,
    )
    if missing:
        return {
            "status": "BLOCKED_MISSING_MASTERS",
            "script_source": source,
            "steps": steps,
            "missing_characters": missing,
            "action": "UPLOAD_AND_APPROVE_MISSING_CHARACTER_MASTERS_THEN_REGENERATE",
        }

    active_role = "director"
    try:
        steps["director"] = _run(
            "director",
            brief=script_text,
            input_revision_ids=[steps["scriptwriter"]["artifact_id"]],
            locked_story_facts=body.locked_story_facts,
            allow_remote_text=body.allow_remote_text,
        )
        active_role = "cinematographer"
        steps["cinematographer"] = _run(
            "cinematographer",
            brief=script_text,
            locked_story_facts=body.locked_story_facts,
            input_revision_ids=[steps["scriptwriter"]["artifact_id"], steps["director"]["artifact_id"]],
            allow_remote_text=body.allow_remote_text,
        )
        active_role = "continuity_supervisor"
        steps["continuity_supervisor"] = _run(
            "continuity_supervisor",
            brief=script_text,
            locked_story_facts=body.locked_story_facts,
            # The script breakdown is part of the review basis: without it the
            # supervisor cannot see the dialogue structure and would flag the
            # dialogue it cannot see as unverifiable.
            input_revision_ids=[
                steps["scriptwriter"]["artifact_id"],
                steps["director"]["artifact_id"],
                steps["cinematographer"]["artifact_id"],
            ],
            allow_remote_text=body.allow_remote_text,
        )
        active_role = "editor"
        steps["editor_edit_intent"] = _run(
            "editor",
            tool="edit_intent",
            brief=script_text,
            locked_story_facts=body.locked_story_facts,
            input_revision_ids=[steps["scriptwriter"]["artifact_id"], steps["director"]["artifact_id"],
                                steps["cinematographer"]["artifact_id"],
                                steps["continuity_supervisor"]["artifact_id"]],
            allow_remote_text=body.allow_remote_text,
        )
    except (ValueError, LLMError) as exc:
        db.rollback()
        rejections.append(_rejection(active_role, exc))
        return {
            "status": "REJECTED",
            "script_source": source,
            "missing_characters": missing,
            "steps": steps,
            "rejections": rejections,
        }

    continuity = db.get(AgentArtifact, steps["continuity_supervisor"]["artifact_id"]).payload_json
    issues = list(continuity.get("issues") or [])
    arbitration = None
    if issues:
        # The roles of the crew contradict each other.  The role that owns the
        # shots arbitrates, once, on the official criteria of the locked rule
        # pack; a contradiction that nobody can rule away still needs a human.
        try:
            ruling = _run(
                "director",
                tool="continuity_arbitration",
                brief=script_text,
                locked_story_facts=body.locked_story_facts,
                input_revision_ids=[
                    steps["director"]["artifact_id"],
                    steps["cinematographer"]["artifact_id"],
                    steps["continuity_supervisor"]["artifact_id"],
                ],
                allow_remote_text=body.allow_remote_text,
            )
        except (ValueError, LLMError) as exc:
            db.rollback()
            arbitration_rejection = _rejection("director", exc)
            arbitration_rejection["role"] = "director_arbitration"
            rejections.append(arbitration_rejection)
            return {
                "status": "REJECTED",
                "script_source": source,
                "script_sha256": hashlib.sha256(script_text.encode("utf-8")).hexdigest(),
                "steps": steps,
                "missing_characters": [],
                "rejections": rejections,
                "continuity_issues": issues,
            }
        steps["director_arbitration"] = ruling
        arbitration = db.get(AgentArtifact, ruling["artifact_id"]).payload_json
    blocked = bool(issues) and not _arbitration_releases(arbitration, issues)
    if not issues and continuity.get("recommendation") != "proceed_to_preflight":
        # No issue to arbitrate, but the supervisor still asked for a human.
        blocked = True
    add_audit(
        db, "film-agent", "STORYBOARD_GENERATED", "project", project.id,
        {
            "script_source": source,
            "script_sha256": hashlib.sha256(script_text.encode("utf-8")).hexdigest(),
            "steps": {name: step["sha256"] for name, step in steps.items()},
            "blocked_by_continuity": blocked,
            "arbitrated_by": "director" if arbitration else None,
        },
        project_id=project_id,
    )
    db.commit()
    return {
        "status": "NEEDS_REVIEW" if blocked else "PROPOSED",
        "script_source": source,
        "script_sha256": hashlib.sha256(script_text.encode("utf-8")).hexdigest(),
        "steps": steps,
        "missing_characters": [],
        "rejections": [],
        "continuity_issues": continuity.get("issues", []),
        "prompt_story_facts": breakdown.get("prompt_story_facts", []),
        "prompt_style": breakdown.get("prompt_style"),
        "continuity_arbitration": (
            {"artifact_id": steps["director_arbitration"]["artifact_id"],
             "rulings": arbitration.get("rulings", []),
             "recommendation": arbitration.get("recommendation"),
             "released": _arbitration_releases(arbitration, issues)}
            if arbitration else None
        ),
        "next": {
            "apply_camera": {
                "camera_artifact_id": steps["cinematographer"]["artifact_id"],
                "continuity_artifact_id": steps["continuity_supervisor"]["artifact_id"],
                "edit_intent_artifact_id": steps["editor_edit_intent"]["artifact_id"],
                "arbitration_artifact_id": (
                    steps["director_arbitration"]["artifact_id"] if arbitration else None
                ),
            }
        },
        "user_actions_required": ["APPLY_CAMERA"] if not blocked else ["RESOLVE_CONTINUITY_ISSUES"],
    }
