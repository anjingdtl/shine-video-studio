from __future__ import annotations

import logging
import os
import time
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import Field, ValidationError
from sqlalchemy import literal_column, select
from sqlalchemy.orm import Session

from shine.agents import character_binding
from shine.agents.editorial_timing import editorial_timing_preview
from shine.agents.execution import UNKNOWN_CODES
from shine.agents.film import FilmAgentDispatcher, asset_context, load_inputs
from shine.agents.film_contracts import CameraPlan, FilmAgentRegistry, FilmRunRequest, StyleInputWire, StyleProposal
from shine.api_common import DBSession, llm_gateway
from shine.api_operator import Operator
from shine.asset_identity import resolve_character_asset, split_character_names
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
from shine.services import (
    add_audit,
    create_shot_record,
    make_render_request,
    order_bindings_for_cast,
    require_project,
)

router = APIRouter(prefix="/api/v1", tags=["film-crew", "rulebook"])

#: How many times one agent task may be released for a fresh request after the
#: provider answered without a usable answer.  The budget keeps a role the
#: model can never satisfy from burning calls.
MAX_RECONCILE_ATTEMPTS = 2

logger = logging.getLogger(__name__)


class AbandonRemoteResponse(APIModel):
    grant_id: str
    snapshot: str
    evidence: str = Field(min_length=1, max_length=2000)


class ReconcileFilmTask(APIModel):
    expected_state_version: int = Field(ge=0)


@router.post("/projects/{project_id}/film-runs/{task_id}/abandon-remote-response")
def abandon_remote(project_id: str, task_id: str, body: AbandonRemoteResponse, actor: Operator, db: DBSession):
    from shine.agents.execution import abandon_remote_response
    from shine.api_preparation import locked

    locked(db)
    row = db.get(AgentTaskRecord, task_id)
    if not row or row.project_id != project_id:
        raise HTTPException(404, "Agent task not found for project")
    return abandon_remote_response(db, row, actor, body.grant_id, body.snapshot, body.evidence)


@router.post("/projects/{project_id}/film-runs/{task_id}/reconcile")
def reconcile_film_task(project_id: str, task_id: str, body: ReconcileFilmTask,
                        actor: Operator, db: DBSession):
    """Inspect a film-agent result without contacting the provider."""
    from shine.h3.rulebook import digest
    from shine.models import AgentArtifact
    from shine.services import add_audit

    require_project(db, project_id)
    task = db.get(AgentTaskRecord, task_id)
    if not task or task.project_id != project_id or task.role_id not in FilmAgentRegistry.roles:
        raise HTTPException(404, "FILM_AGENT_TASK_NOT_FOUND")
    if task.state_version != body.expected_state_version:
        raise HTTPException(409, "FILM_AGENT_TASK_STATE_CHANGED")
    if task.status == "RUNNING" or task.lease_expires_at and task.lease_expires_at > utcnow():
        raise HTTPException(409, "FILM_AGENT_TASK_STILL_RUNNING")

    decided = (task.trace.get("reconciliation") or {}).get("outcome")
    if decided in {"RESULT_ADOPTED", "NOT_SENT_PROVEN", "RESPONSE_RECEIVED_NO_ANSWER"}:
        # Reconciling twice must never turn a released task back into an
        # unconfirmable one.
        return {"outcome": decided, "task_id": task.id, "state_version": task.state_version,
                "resubmittable": decided != "RESULT_ADOPTED",
                "note": "这一项已经核对过，结论不变。"}

    artifact = db.scalar(select(AgentArtifact).where(
        AgentArtifact.source_task_id == task.id, AgentArtifact.stale.is_(False),
    ))
    if (artifact and artifact.project_id == project_id
            and artifact.sha256 == digest(artifact.payload_json)
            and task.trace.get("output_hash") == artifact.sha256):
        if task.status != "SUCCEEDED":
            task.status = "SUCCEEDED"
            task.error_code = None
            task.state_version += 1
            task.trace = {**task.trace, "reconciliation": {
                "outcome": "RESULT_ADOPTED", "artifact_id": artifact.id,
                "checked_at": utcnow().isoformat(),
            }}
            add_audit(db, actor, "FILM_AGENT_RESULT_ADOPTED", "agent_task", task.id,
                      {"artifact_id": artifact.id, "artifact_sha256": artifact.sha256,
                       "external_calls": 0}, project_id=project_id)
            db.commit()
        return {"outcome": "RESULT_ADOPTED", "task_id": task.id, "artifact_id": artifact.id,
                "artifact_sha256": artifact.sha256, "resubmittable": False}

    if (task.status in {"REJECTED", "FAILED"}
            and task.trace.get("external_outcome") == "NOT_SENT"):
        task.status = "SUPERSEDED"
        task.error_code = None
        task.state_version += 1
        task.trace = {**task.trace, "reconciliation": {
            "outcome": "NOT_SENT_PROVEN", "checked_at": utcnow().isoformat(),
        }}
        add_audit(db, actor, "FILM_AGENT_NOT_SENT_PROVEN", "agent_task", task.id,
                  {"role": task.role_id, "resubmittable": True, "external_calls": 0},
                  project_id=project_id)
        db.commit()
        return {"outcome": "NOT_SENT_PROVEN", "task_id": task.id,
                "state_version": task.state_version, "resubmittable": True}

    # The provider answered and produced no usable answer: a refusal (wrong
    # key, unknown model, rate limit) or an answer that broke the contract.
    # The request is finished, so no answer exists that a second request
    # could duplicate.  This is not "not sent" and it is not "unknown", and
    # the retry stays bounded.
    if (task.status in {"REJECTED", "FAILED"}
            and task.trace.get("external_outcome") == "RESPONSE_RECEIVED"):
        attempts = int((task.trace.get("reconciliation") or {}).get("attempt", 0))
        if attempts >= MAX_RECONCILE_ATTEMPTS:
            add_audit(db, actor, "FILM_AGENT_RETRIES_EXHAUSTED", "agent_task", task.id,
                      {"attempts": attempts, "external_calls": 0, "resubmittable": False})
            db.commit()
            return {"outcome": "RETRIES_EXHAUSTED", "task_id": task.id,
                    "state_version": task.state_version, "resubmittable": False,
                    "note": "这一项已经重试到上限；请修改输入后重新提交，或取消本次任务。"}
        previous_identity = task.request_identity
        task.status = "SUPERSEDED"
        task.error_code = None
        task.request_identity = None
        task.lease_owner, task.lease_expires_at = None, None
        task.state_version += 1
        task.trace = {**task.trace, "reconciliation": {
            "outcome": "RESPONSE_RECEIVED_NO_ANSWER", "attempt": attempts + 1,
            "error_code": task.error_code, "response_status": task.trace.get("response_status"),
            "previous_request_identity": previous_identity, "checked_at": utcnow().isoformat(),
        }}
        add_audit(db, actor, "FILM_AGENT_RESPONSE_RECEIVED_NO_ANSWER", "agent_task", task.id,
                  {"previous_request_identity": previous_identity, "attempt": attempts + 1,
                   "response_status": task.trace.get("response_status"),
                   "resubmittable": True, "external_calls": 0})
        db.commit()
        return {"outcome": "RESPONSE_RECEIVED_NO_ANSWER", "task_id": task.id,
                "state_version": task.state_version, "resubmittable": True,
                "note": "服务商已经答复但没有可用结果；再次提交时会创建一次新的模型请求。"}

    external_outcome = task.trace.get("external_outcome") or "UNKNOWN"
    add_audit(db, actor, "FILM_AGENT_RECONCILIATION_UNCONFIRMABLE", "agent_task", task.id,
              {"status": task.status, "external_outcome": external_outcome,
               "resubmittable": False, "external_calls": 0}, project_id=project_id)
    db.commit()
    return {"outcome": "UNCONFIRMABLE", "task_id": task.id, "state_version": task.state_version,
            "external_outcome": external_outcome, "resubmittable": False}


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
            "state_version": r.state_version,
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
            cuts = intent.get("cuts", [])
            visual = intent["visual_description"]
            if cuts:
                characters = list(beat["characters"])
                lines = list(beat["dialogue"])
                for cut in cuts:
                    cut_beat = beats[cut["beat_id"]]
                    offset = cut["start_s"]
                    visual += f'\nAt 00:{offset:06.3f}, hard cut. {cut["visual_description"]}'
                    characters.extend(cut_beat["characters"])
                    lines.extend({**line, "start_s": round(line["start_s"] + offset, 3),
                                  "end_s": round(line["end_s"] + offset, 3)} for line in cut_beat["dialogue"])
                beat = {**beat, "characters": list(dict.fromkeys(characters)), "dialogue": lines}
            # The scriptwriter and the director choose timings independently;
            # at this materialisation boundary the spoken beats are fitted
            # proportionally into the shot the director actually planned, so
            # the spec stays renderable without changing a single word.
            duration = float(intent["duration_s"])
            dialogue = []
            for line in beat["dialogue"]:
                line = dict(line)
                start, end = float(line["start_s"]), float(line["end_s"])
                if not cuts and end > start:
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
                story_beat=visual,
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
            # The H3 presentation numbers images in one sequence, so a static
            # workflow profile can only map the slots if the references arrive
            # in a fixed family order: subjects, then pictures, then the other
            # media families.  Inside the subject family the beat's own cast
            # order decides, so <Subject 1> really is the first described person.
            ordered_assets = {identity: db.get(Asset, identity) for identity in intent["asset_ids"]}
            bindings = order_bindings_for_cast(bindings, list(beat["characters"]), ordered_assets)
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
    confirm_characters: bool = False
    # Resume support: start at one crew role and reuse the artifacts the
    # earlier roles already produced.  A failed role is retried from itself
    # instead of paying for the whole chain again.
    start_role: Literal[
        "scriptwriter", "director", "cinematographer", "continuity_supervisor", "editor", "director_arbitration"
    ] | None = None
    resume_steps: dict[str, str] = Field(default_factory=dict, max_length=8)
    replace_interrupted_task_id: str | None = Field(default=None, max_length=36)
    production_context: dict | None = None
    consent_document_id: str | None = None
    consent_document_sha256: str | None = None


# The crew order.  The chain and the resume request both walk this list.
STORYBOARD_CHAIN = (
    "scriptwriter", "director", "cinematographer", "continuity_supervisor", "editor",
)


def _agent_step_delay_seconds() -> float:
    """The pause between two crew calls.

    Two real model calls back to back share the same provider connection and
    the same rate limit; the gap keeps the second call from starting while the
    first one is still settling, and gives the workbench a visible boundary
    between two steps.
    """
    try:
        return max(0.0, int(os.getenv("SHINE_AGENT_STEP_DELAY_MS", "1200")) / 1000)
    except ValueError:
        return 1.2


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
            for part in split_character_names(name if isinstance(name, str) else str(name)):
                if part not in named:
                    named.append(part)
        for line in beat.get("dialogue", []):
            speaker = line.get("speaker_id") if isinstance(line, dict) else getattr(line, "speaker_id", None)
            for part in split_character_names(speaker if isinstance(speaker, str) else None):
                if part not in named:
                    named.append(part)
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

    if body.replace_interrupted_task_id:
        raise HTTPException(409, "AGENT_UNKNOWN_OUTCOME_REQUIRES_RECONCILIATION")

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

    if body.production_context is not None:
        from shine.materialprep.story import verified_production_context
        body.production_context = verified_production_context(db, project_id, script_text, body.production_context)
    needed_roles = STORYBOARD_CHAIN
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
        **({"production_context": body.production_context} if body.production_context is not None else {}),
        "style_version_id": project.frozen_style_version_id,
        "approved_asset_manifest": asset_context(db, project_id),
        "agent_routes": {role: text_provider_snapshot(routes[role]) for role in needed_roles},
    })
    recovered_director = None
    recovered_editor = None
    for prior in db.scalars(select(AgentTaskRecord).where(
        AgentTaskRecord.project_id == project_id,
        AgentTaskRecord.role_id.in_(needed_roles),
    )):
        if prior.trace.get("storyboard_request_sha256") != storyboard_request_sha256:
            continue
        # A historical replacement result may be adopted after its successful
        # response was durably recorded. This path only reads the artifact and
        # never authorizes another provider request.
        receipt = db.get(AppSetting, f"film-replacement:{prior.id}")
        replacement = db.get(AgentTaskRecord, receipt.value.get("replacement_task_id")) if receipt else None
        writer_id = (body.resume_steps or {}).get("scriptwriter")
        candidate = db.scalar(select(AgentArtifact).where(
            AgentArtifact.source_task_id == replacement.id,
            AgentArtifact.kind == "DirectorialPlan",
        )) if replacement else None
        requested_director_id = (body.resume_steps or {}).get("director")
        requested_director = db.get(AgentArtifact, requested_director_id) if requested_director_id else None
        repair_source = db.get(AgentTaskRecord, requested_director.source_task_id) if requested_director else None
        valid_repair = bool(candidate and requested_director and repair_source
            and requested_director.project_id == project_id and requested_director.kind == "DirectorialPlan"
            and not requested_director.stale and requested_director.sha256 == digest(requested_director.payload_json)
            and len(requested_director.input_revision_ids) == 5
            and requested_director.input_revision_ids[:2] == [writer_id, candidate.id]
            and repair_source.project_id == project_id and repair_source.role_id == "director"
            and repair_source.status == "SUCCEEDED"
            and repair_source.trace.get("storyboard_request_sha256") == storyboard_request_sha256
            and repair_source.trace.get("output_hash") == requested_director.sha256)
        if (body.start_role and body.start_role != "scriptwriter" and writer_id
                and prior.role_id == "director" and prior.status == "ABANDONED"
                and prior.trace.get("local_interruption_verified") is True
                and prior.trace.get("external_outcome") == "UNKNOWN"
                and prior.trace.get("input_revision_ids") == [writer_id]
                and replacement and replacement.project_id == project_id
                and replacement.role_id == "director" and replacement.status == "SUCCEEDED"
                and replacement.trace.get("storyboard_request_sha256") == storyboard_request_sha256
                and replacement.trace.get("input_revision_ids") == [writer_id]
                and candidate and candidate.project_id == project_id and not candidate.stale
                and candidate.producer_role == "director" and candidate.input_revision_ids == [writer_id]
                and candidate.sha256 == digest(candidate.payload_json)
                and replacement.trace.get("output_hash") == candidate.sha256
                and (not requested_director_id or requested_director_id == candidate.id or valid_repair)):
            if recovered_director and recovered_director.id != candidate.id:
                raise HTTPException(409, "STORYBOARD_RECOVERY_AMBIGUOUS")
            recovered_director = candidate
            continue
        # Reuse an already-recorded editor result from a historical run only
        # when its artifact and input lineage are still intact.
        if (body.start_role and body.start_role != "scriptwriter" and prior.role_id == "editor"
                and prior.status == "ABANDONED" and prior.trace.get("local_interruption_verified") is True
                and prior.trace.get("external_outcome") == "UNKNOWN"):
            editor_receipt = db.get(AppSetting, f"film-replacement:{prior.id}")
            editor_replacement = (db.get(AgentTaskRecord, editor_receipt.value.get("replacement_task_id"))
                                  if editor_receipt else None)
            editor_artifact = db.scalar(select(AgentArtifact).where(
                AgentArtifact.source_task_id == editor_replacement.id,
                AgentArtifact.kind == "EditIntentPlan",
            )) if editor_replacement else None
            selected_editor_id = (body.resume_steps or {}).get("editor_edit_intent")
            valid_editor = bool(
                editor_replacement and editor_artifact
                and selected_editor_id == editor_artifact.id
                and editor_replacement.project_id == project_id and editor_replacement.role_id == "editor"
                and editor_replacement.status == "SUCCEEDED"
                and editor_replacement.trace.get("storyboard_request_sha256") == storyboard_request_sha256
                and editor_replacement.trace.get("input_revision_ids") == prior.trace.get("input_revision_ids")
                and editor_artifact.project_id == project_id and not editor_artifact.stale
                and editor_artifact.producer_role == "editor"
                and editor_artifact.input_revision_ids == prior.trace.get("input_revision_ids")
                and editor_artifact.sha256 == digest(editor_artifact.payload_json)
                and editor_replacement.trace.get("output_hash") == editor_artifact.sha256
            )
            if valid_editor:
                recovered_editor = editor_artifact
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
                "failed_role": prior.role_id,
                # Keep the validated resume selection so reconciliation can
                # inspect the original task without changing its identity.
                "resume_steps": dict(body.resume_steps or {}),
            }

    dispatcher = FilmAgentDispatcher(gateway, request.app.state.settings.data_dir)

    # A definitive contract rejection is a per-sample property of a real text
    # model, not an uncertain external outcome: the guard keeps the rejected
    # task record, and a bounded second call is allowed.  Uncertain outcomes
    # (timeouts, unknown server errors) are never replayed here.
    RETRYABLE_REJECTIONS = {"CONTRACT_OR_POLICY_REJECTED", "AGENT_CALL_FAILED"}

    steps: dict[str, dict] = {}
    rejections: list[dict] = []

    # Resume: reuse the artifacts the earlier roles already produced instead of
    # calling them again.  The server checks every referenced artifact: it must
    # belong to this project, carry the role's contract kind and not be stale.
    start_role = body.start_role or "scriptwriter"
    if start_role not in (*STORYBOARD_CHAIN, "director_arbitration"):
        raise HTTPException(422, "STORYBOARD_RESUME_ROLE_UNKNOWN")
    if start_role != "scriptwriter":
        prior_roles = (STORYBOARD_CHAIN if start_role == "director_arbitration"
                       else STORYBOARD_CHAIN[:STORYBOARD_CHAIN.index(start_role)])
        for role in prior_roles:
            step_name = "editor_edit_intent" if role == "editor" else role
            artifact_id = (body.resume_steps or {}).get(step_name)
            if not artifact_id:
                raise HTTPException(409, f"STORYBOARD_RESUME_STEP_MISSING:{role}")
            artifact = db.get(AgentArtifact, artifact_id)
            expected_kind = "EditIntentPlan" if role == "editor" else FilmAgentRegistry.roles[role][1].__name__
            if (
                not artifact
                or artifact.project_id != project_id
                or artifact.stale
                or artifact.kind != expected_kind
            ):
                raise HTTPException(409, f"STORYBOARD_RESUME_STEP_INVALID:{role}")
            steps[step_name] = {"artifact_id": artifact.id, "kind": artifact.kind, "sha256": artifact.sha256}

    if start_role == "director_arbitration":
        lineage = [steps[role]["artifact_id"] for role in (
            "scriptwriter", "director", "cinematographer", "continuity_supervisor", "editor_edit_intent")]
        for index, artifact_id in enumerate(lineage[1:], start=1):
            artifact = db.get(AgentArtifact, artifact_id)
            inputs_match = artifact.input_revision_ids == lineage[:index]
            if index == 1 and len(artifact.input_revision_ids) == 5:
                repair_inputs = [db.get(AgentArtifact, item) for item in artifact.input_revision_ids]
                inputs_match = (artifact.input_revision_ids[0] == lineage[0]
                                and all(item and item.project_id == project_id and not item.stale
                                        and item.sha256 == digest(item.payload_json) for item in repair_inputs)
                                and [item.kind for item in repair_inputs] == ["ScriptBreakdown", "DirectorialPlan",
                                    "CameraPlan", "ContinuityReport", "ContinuityArbitration"])
            if not inputs_match or artifact.sha256 != digest(artifact.payload_json):
                raise HTTPException(409, "STORYBOARD_RESUME_LINEAGE_INVALID")

    if recovered_director and start_role == "director":
        steps["director"] = {"artifact_id": recovered_director.id, "kind": recovered_director.kind,
                             "sha256": recovered_director.sha256}
    if recovered_editor and start_role == "editor":
        steps["editor_edit_intent"] = {"artifact_id": recovered_editor.id, "kind": recovered_editor.kind,
                                       "sha256": recovered_editor.sha256}

    delay = _agent_step_delay_seconds()
    calls_in_request = 0

    def _run(role: str, *, attempts: int = 2, repair_director: bool = False, **kwargs) -> dict:
        nonlocal calls_in_request
        if body.production_context is not None:
            kwargs.update(production_context=body.production_context, consent_document_id=body.consent_document_id,
                          consent_document_sha256=body.consent_document_sha256)
        last: ValueError | LLMError | None = None
        repair_visual_language = False
        for attempt in range(1, attempts + 1):
            if calls_in_request:
                # Leave a real gap between two crew calls, and between the
                # retry of one crew call and its next attempt.
                time.sleep(delay)
            calls_in_request += 1
            try:
                artifact = dispatcher.run(
                    db,
                    project_id,
                    role,
                    FilmRunRequest(**kwargs),
                    storyboard_request_sha256=storyboard_request_sha256,
                    repair_visual_language=repair_visual_language,
                    repair_director=repair_director,
                    replace_interrupted_task_id=(body.replace_interrupted_task_id
                        if role == body.start_role and role in {"director", "editor"} and not repair_director
                        and kwargs.get("tool") != "continuity_arbitration" else None),
                )
                return {"artifact_id": artifact.id, "kind": artifact.kind,
                        "sha256": artifact.sha256, "attempt": attempt}
            except (ValueError, LLMError) as exc:
                last = exc
                code = str(exc)
                if code not in RETRYABLE_REJECTIONS or attempt == attempts:
                    raise
                failure = _rejection(role, exc)
                if failure.get("external_outcome") == "UNKNOWN":
                    raise
                repair_visual_language = (
                    role in {"director", "cinematographer"}
                    and failure.get("rejection_reason_code") == "VISUAL_TEXT_MUST_BE_PROMPT_LANGUAGE"
                )
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
            reason = task.trace.get("rejection_reason_code")
            if reason:
                # The precise reason the role's answer was refused.  Without it
                # the operator only sees a generic code and cannot tell a
                # contract miss from an unrepairable request.
                rejection["rejection_reason_code"] = reason
            outcome = task.trace.get("external_outcome")
            if outcome in {"NOT_SENT", "UNKNOWN"}:
                rejection["external_outcome"] = outcome
        return rejection

    try:
        if "scriptwriter" not in steps:
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
            "failed_role": "scriptwriter",
            "resume_steps": {role: step["artifact_id"] for role, step in steps.items()},
        }

    breakdown = db.get(AgentArtifact, steps["scriptwriter"]["artifact_id"]).payload_json
    missing = _character_coverage(
        asset_context(db, project_id),
        breakdown,
    )
    if body.confirm_characters and "director" not in steps and character_binding.script_characters(breakdown):
        return {
            "status": "AWAITING_CHARACTER_CONFIRMATION",
            "script_source": source,
            "steps": steps,
            "missing_characters": missing,
            "resume_steps": {role: step["artifact_id"] for role, step in steps.items()},
            "action": "CONFIRM_CHARACTER_BINDINGS_THEN_RESUME_DIRECTOR",
        }
    if missing:
        return {
            "status": "BLOCKED_MISSING_MASTERS",
            "script_source": source,
            "steps": steps,
            "missing_characters": missing,
            "action": "UPLOAD_AND_APPROVE_MISSING_CHARACTER_MASTERS_THEN_REGENERATE",
            "failed_role": "scriptwriter",
            "resume_steps": {role: step["artifact_id"] for role, step in steps.items()},
        }

    def _run_step(name: str, role: str, **kwargs) -> dict:
        """Run one crew role unless a resumed step already produced it."""
        if name in steps:
            return steps[name]
        return _run(role, **kwargs)

    active_role = "director"
    try:
        steps["director"] = _run_step(
            "director", "director",
            brief=script_text,
            input_revision_ids=[steps["scriptwriter"]["artifact_id"]],
            locked_story_facts=body.locked_story_facts,
            allow_remote_text=body.allow_remote_text,
        )
        active_role = "cinematographer"
        steps["cinematographer"] = _run_step(
            "cinematographer", "cinematographer",
            brief=script_text,
            locked_story_facts=body.locked_story_facts,
            input_revision_ids=[steps["scriptwriter"]["artifact_id"], steps["director"]["artifact_id"]],
            allow_remote_text=body.allow_remote_text,
        )
        active_role = "continuity_supervisor"
        steps["continuity_supervisor"] = _run_step(
            "continuity_supervisor", "continuity_supervisor",
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
        steps["editor_edit_intent"] = _run_step(
            "editor_edit_intent", "editor",
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
            "failed_role": active_role,
            "resume_steps": {role: step["artifact_id"] for role, step in steps.items()},
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
                "failed_role": "director_arbitration",
                "resume_steps": {role: step["artifact_id"] for role, step in steps.items()},
            }
        steps["director_arbitration"] = ruling
        arbitration = db.get(AgentArtifact, ruling["artifact_id"]).payload_json
    blocked = bool(issues) and not _arbitration_releases(arbitration, issues)
    if not issues and continuity.get("recommendation") != "proceed_to_preflight":
        # No issue to arbitrate, but the supervisor still asked for a human.
        blocked = True
    repair_failure = None
    if blocked and arbitration and any(r.get("verdict") == "conflict" for r in arbitration.get("rulings", [])):
        # One bounded repair round creates new proposals; no master, frozen style,
        # prior artifact or user approval is overwritten. Unknown outcomes stop.
        original_steps = dict(steps)
        repair_role = "director"
        try:
            common = {"brief": script_text, "locked_story_facts": body.locked_story_facts,
                      "allow_remote_text": body.allow_remote_text}
            steps["director"] = _run("director", repair_director=True, **common, input_revision_ids=[
                original_steps["scriptwriter"]["artifact_id"], original_steps["director"]["artifact_id"],
                original_steps["cinematographer"]["artifact_id"],
                original_steps["continuity_supervisor"]["artifact_id"],
                original_steps["director_arbitration"]["artifact_id"],
            ])
            repair_role = "cinematographer"
            steps["cinematographer"] = _run("cinematographer", **common, input_revision_ids=[
                steps["scriptwriter"]["artifact_id"], steps["director"]["artifact_id"]])
            repair_role = "continuity_supervisor"
            steps["continuity_supervisor"] = _run("continuity_supervisor", **common, input_revision_ids=[
                steps["scriptwriter"]["artifact_id"], steps["director"]["artifact_id"],
                steps["cinematographer"]["artifact_id"]])
            repair_role = "editor"
            steps["editor_edit_intent"] = _run("editor", tool="edit_intent", **common, input_revision_ids=[
                steps["scriptwriter"]["artifact_id"], steps["director"]["artifact_id"],
                steps["cinematographer"]["artifact_id"], steps["continuity_supervisor"]["artifact_id"]])
            continuity = db.get(AgentArtifact, steps["continuity_supervisor"]["artifact_id"]).payload_json
            issues = list(continuity.get("issues") or [])
            arbitration = None
            steps.pop("director_arbitration", None)
            if issues:
                repair_role = "director_arbitration"
                steps["director_arbitration"] = _run("director", tool="continuity_arbitration", **common,
                    input_revision_ids=[steps["director"]["artifact_id"], steps["cinematographer"]["artifact_id"],
                                        steps["continuity_supervisor"]["artifact_id"]])
                arbitration = db.get(AgentArtifact, steps["director_arbitration"]["artifact_id"]).payload_json
            blocked = (bool(issues) and not _arbitration_releases(arbitration, issues)) or (
                not issues and continuity.get("recommendation") != "proceed_to_preflight")
        except (ValueError, LLMError) as exc:
            db.rollback()
            failure = _rejection("director" if repair_role == "director_arbitration" else repair_role, exc)
            failure["role"] = repair_role
            if failure.get("external_outcome") == "UNKNOWN" or failure["code"] in {
                "AGENT_REQUEST_RECONCILIATION_REQUIRED", "AGENT_REQUEST_REPLAY_BLOCKED",
                "LLM_TIMEOUT", "LLM_CONNECTION", "LLM_SERVER",
            }:
                return {"status": "REJECTED", "script_source": source, "steps": steps,
                        "rejections": [failure], "failed_role": repair_role,
                        "resume_steps": {role: step["artifact_id"] for role, step in steps.items()},
                        "continuity_issues": issues}
            repair_failure = failure
            steps = original_steps
            continuity = db.get(AgentArtifact, steps["continuity_supervisor"]["artifact_id"]).payload_json
            issues = list(continuity.get("issues") or [])
            arbitration = db.get(AgentArtifact, steps["director_arbitration"]["artifact_id"]).payload_json
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
        "resume_steps": {role: step["artifact_id"] for role, step in steps.items()},
        "continuity_issues": continuity.get("issues", []),
        "repair_failure": repair_failure,
        "editorial_timing_preview": editorial_timing_preview(script_text),
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


class CharacterBindingRequest(APIModel):
    """Ask which uploaded master answers each character the script named."""

    breakdown_artifact_id: str | None = None


class CharacterBindingConfirm(APIModel):
    """The user's own answer: this script character is this master, under this name."""

    script_name: str = Field(min_length=1, max_length=80)
    asset_id: str = Field(min_length=1, max_length=36)
    name: str = Field(min_length=1, max_length=80)


class CharacterBindingApply(APIModel):
    breakdown_artifact_id: str = Field(min_length=1, max_length=36)
    bindings: list[CharacterBindingConfirm] = Field(default_factory=list, max_length=32)


def _character_masters(db: Session, project_id: str) -> list[dict]:
    """Every character image of the project, confirmed or not.

    The naming step happens before the material is frozen, so an image the
    user has not confirmed yet still has to be offered.
    """
    rows = db.scalars(
        select(Asset)
        .where(Asset.project_id == project_id, Asset.kind == "image", Asset.role == "character")
        .order_by(Asset.created_at, Asset.id)
    ).all()
    return [
        {
            "id": row.id,
            "kind": row.kind,
            "role": row.role,
            "filename": row.filename,
            "entity_id": row.entity_id,
            "tags": list(row.tags or []),
            "approved": bool(row.approved),
            "locked": bool(row.locked),
        }
        for row in rows
    ]


def _load_breakdown(db: Session, project_id: str, artifact_id: str | None) -> AgentArtifact:
    if artifact_id:
        artifact = db.get(AgentArtifact, artifact_id)
    else:
        artifact = db.scalar(
            select(AgentArtifact)
            .where(
                AgentArtifact.project_id == project_id,
                AgentArtifact.kind == "ScriptBreakdown",
                AgentArtifact.stale.is_(False),
            )
            .order_by(
                AgentArtifact.created_at.desc(),
                literal_column("agent_artifacts.rowid").desc(),
            )
            .limit(1)
        )
    if not artifact or artifact.project_id != project_id or artifact.kind != "ScriptBreakdown":
        raise HTTPException(404, "Script breakdown not found in this project")
    if artifact.stale:
        raise HTTPException(409, "SCRIPT_BREAKDOWN_STALE")
    return artifact


@router.post("/projects/{project_id}/storyboard/character-binding/proposal", status_code=201)
def character_binding_proposal(project_id: str, body: CharacterBindingRequest, db: DBSession) -> dict:
    """Propose, for every character the script names, which master answers it.

    The proposal is a reading of the user's own uploads.  It never approves,
    locks or replaces an asset, and the user still confirms every row.
    """
    require_project(db, project_id)
    breakdown = _load_breakdown(db, project_id, body.breakdown_artifact_id)
    payload = breakdown.payload_json or {}
    characters = character_binding.script_characters(payload)
    masters = _character_masters(db, project_id)
    proposals = character_binding.propose_bindings(characters, masters)
    proposed_names = payload.get("character_names") or {}
    for row in proposals:
        name = proposed_names.get(row["script_name"])
        if isinstance(name, str) and name.strip() and len(name.strip()) <= 80:
            row["suggested_name"] = name.strip()
    confirmed_rows = payload.get("character_bindings") or []
    approved_masters = asset_context(db, project_id)
    already_confirmed = bool(confirmed_rows) and {row.get("name") for row in confirmed_rows} == set(characters)
    already_confirmed = already_confirmed and all(
        (resolve_character_asset(row.get("name"), approved_masters) or {}).get("id") == row.get("asset_id")
        for row in confirmed_rows
    )
    proposal_payload = {
        "breakdown_artifact_id": breakdown.id,
        "breakdown_sha256": breakdown.sha256,
        "characters": proposals,
        "unbound_masters": character_binding.unbound_masters(masters, proposals),
        "source": "DETERMINISTIC_NAME_AND_ORDER_MATCH",
    }
    artifact = AgentArtifact(
        project_id=project_id,
        kind="CharacterBindingProposal",
        schema_version="1.1",
        payload_json=proposal_payload,
        sha256=digest(proposal_payload),
        producer_role="scriptwriter",
        source_task_id=breakdown.source_task_id,
        input_revision_ids=[breakdown.id],
        stale=False,
    )
    db.add(artifact)
    add_audit(
        db, "film-agent", "CHARACTER_BINDING_PROPOSED", "project", project_id,
        {"breakdown_artifact_id": breakdown.id, "characters": len(characters)},
        project_id=project_id,
    )
    db.commit()
    return {
        "proposal_id": artifact.id,
        "breakdown_artifact_id": breakdown.id,
        "already_confirmed": already_confirmed,
        "characters": proposals,
        "unbound_masters": proposal_payload["unbound_masters"],
        "masters": masters,
    }


@router.post("/projects/{project_id}/storyboard/character-binding/apply", status_code=201)
def character_binding_apply(project_id: str, body: CharacterBindingApply, db: DBSession) -> dict:
    """Apply the user's confirmed mapping: name the masters and re-key the script.

    The master keeps its bytes and its identity; only the name the crew binds
    to changes, and only because the user said so.  The script breakdown is
    rewritten into a new artifact so the earlier one stays readable.
    """
    require_project(db, project_id)
    breakdown = _load_breakdown(db, project_id, body.breakdown_artifact_id)
    if not body.bindings:
        raise HTTPException(422, "CHARACTER_BINDING_EMPTY")

    expected = set(character_binding.script_characters(breakdown.payload_json))
    supplied = [row.script_name.strip() for row in body.bindings]
    if set(supplied) != expected or len(supplied) != len(expected):
        raise HTTPException(422, "CHARACTER_BINDING_CONFIRM_EVERY_CHARACTER")
    if len({row.asset_id for row in body.bindings}) != len(body.bindings):
        raise HTTPException(409, "CHARACTER_BINDING_MASTER_REUSED")

    names = [row.name.strip() for row in body.bindings]
    if len(set(names)) != len(names):
        raise HTTPException(409, "CHARACTER_NAME_DUPLICATED_IN_CONFIRMATION")

    assets = {
        row.id: row
        for row in db.scalars(
            select(Asset).where(Asset.project_id == project_id, Asset.id.in_([r.asset_id for r in body.bindings]))
        ).all()
    }
    bound_ids = set(assets)
    for row in body.bindings:
        asset = assets.get(row.asset_id)
        if not asset or asset.kind != "image" or asset.role != "character":
            raise HTTPException(409, f"CHARACTER_MASTER_NOT_IN_PROJECT:{row.asset_id}")

    # A name that already belongs to another master makes the binding
    # ambiguous, and an ambiguous binding is a blocked shot.
    others = db.scalars(
        select(Asset).where(
            Asset.project_id == project_id,
            Asset.kind == "image",
            Asset.role == "character",
            Asset.id.notin_(bound_ids),
        )
    ).all()
    taken = {str(row.entity_id or "").strip() for row in others if row.entity_id}
    for row in body.bindings:
        if row.name.strip() in taken:
            raise HTTPException(409, f"CHARACTER_NAME_ALREADY_IN_USE:{row.name.strip()}")

    rename = {row.script_name.strip(): row.name.strip() for row in body.bindings}
    for row in body.bindings:
        assets[row.asset_id].entity_id = row.name.strip()

    payload = dict(breakdown.payload_json or {})
    rewritten_beats = []
    for beat in payload.get("beats", []) or []:
        beat = dict(beat)
        characters: list[str] = []
        for name in beat.get("characters", []) or []:
            for part in split_character_names(name if isinstance(name, str) else str(name)):
                canonical = rename.get(part, part)
                if canonical not in characters:
                    characters.append(canonical)
        beat["characters"] = characters
        dialogue = []
        for line in beat.get("dialogue", []) or []:
            line = dict(line)
            speaker = line.get("speaker_id")
            if isinstance(speaker, str):
                # A chorus line can name several speakers in one field
                # ("S1, S2, S3").  The rename is per label, so the field has to
                # be split the same way every other consumer splits it; a
                # whole-list speaker would otherwise keep its placeholders
                # and block the shot forever.
                parts = split_character_names(speaker)
                if parts:
                    line["speaker_id"] = ", ".join(rename.get(part, part) for part in parts)
            dialogue.append(line)
        beat["dialogue"] = dialogue
        rewritten_beats.append(beat)
    payload["beats"] = rewritten_beats
    payload["character_bindings"] = [
        {"script_name": row.script_name.strip(), "asset_id": row.asset_id, "name": row.name.strip()}
        for row in body.bindings
    ]

    artifact = AgentArtifact(
        project_id=project_id,
        kind="ScriptBreakdown",
        schema_version="1.1",
        payload_json=payload,
        sha256=digest(payload),
        producer_role="scriptwriter",
        source_task_id=breakdown.source_task_id,
        input_revision_ids=[breakdown.id],
        stale=False,
    )
    db.add(artifact)
    add_audit(
        db, "local-user", "CHARACTER_BINDING_APPLIED", "project", project_id,
        {
            "breakdown_artifact_id": breakdown.id,
            "new_breakdown_artifact_id": artifact.id,
            "bindings": payload["character_bindings"],
        },
        project_id=project_id,
    )
    db.commit()
    return {
        "artifact_id": artifact.id,
        "breakdown_artifact_id": breakdown.id,
        "assets": [
            {"id": row.asset_id, "entity_id": row.name.strip(), "locked": bool(assets[row.asset_id].locked)}
            for row in body.bindings
        ],
        "resume_steps": {"scriptwriter": artifact.id},
        "next": "FREEZE_AND_RESUME",
    }
