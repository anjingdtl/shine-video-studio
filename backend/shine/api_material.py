"""Director AI material-preparation API.

Every mutating route here writes durable rows and returns ``202``.  None of them
contacts a renderer, calls a model, or waits for an image: the resident Worker
owns execution.  A route that returned a finished image would be a synchronous
generator wearing a 202 as a disguise, and it could not survive a restart.

``mode`` on the generate route is an explicit literal ``"real"``.  There is no
``auto`` and no fallback: an unconfigured machine gets a named refusal instead of
a Fake candidate that could later be mistaken for real verification.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field
from sqlalchemy import select, text

from shine.api_common import DBSession, app_settings, llm_gateway
from shine.api_operator import Operator
from shine.config import Settings
from shine.llm.gateway import LLMGateway
from shine.materialprep import runtime, service
from shine.materialprep.contracts import MaterialConfirmation, ScriptPreparation
from shine.materialprep.states import EXECUTION_MODE_REAL
from shine.schemas import APIModel

#: How many times one scene may be released for a fresh request after the
#: provider answered without a usable answer.  The budget keeps a scene the
#: model can never satisfy from burning calls.
MAX_RECONCILE_ATTEMPTS = 2

router = APIRouter(prefix="/api/v1", tags=["director material preparation"])

SettingsDep = Annotated[Settings, Depends(app_settings)]


class AnalyzeBody(APIModel):
    document_id: str
    expected_document_hash: str = Field(min_length=64, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=128)
    # The scene answers are the published ScriptPreparation contract, not free
    # dictionaries. A family outside character/scene/prop or an unknown field is
    # refused here instead of travelling into the plan payload and from there
    # into an image prompt.
    scene_preparations: list[ScriptPreparation] = Field(default_factory=list, max_length=400)


class GenerateBody(APIModel):
    confirmation_id: str = Field(min_length=64, max_length=64)
    confirmation_hash: str = Field(min_length=64, max_length=64)
    requirement_ids: list[str] = Field(min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=128)
    rights_id: str | None = None
    mode: Literal["real"] = EXECUTION_MODE_REAL
    seeds: dict[str, int] = Field(default_factory=dict)


class StateVersionBody(APIModel):
    expected_state_version: int = Field(ge=0)
    idempotency_key: str = Field(default="", max_length=128)


class ResumeBody(StateVersionBody):
    item_ids: list[str] = Field(min_length=1, max_length=200)


class ReconcileBody(StateVersionBody):
    """A reconcile may be limited to the items the operator named."""
    item_ids: list[str] = Field(default_factory=list, max_length=200)


class RegenerateBody(APIModel):
    requirement_id: str
    confirmation_id: str = Field(min_length=64, max_length=64)
    confirmation_hash: str = Field(min_length=64, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=128)
    rights_id: str | None = None
    brief: dict
    seed: int = Field(default=0, ge=0)


class ImageRuntimeBody(APIModel):
    instance_id: str
    base_url: str
    model_root: str
    expected_config_revision: int = Field(default=0, ge=0)


class ValidateBody(APIModel):
    project_id: str
    idempotency_key: str = Field(min_length=1, max_length=128)
    families: list[Literal["character", "scene", "prop"]] = Field(
        default_factory=lambda: ["character", "scene", "prop"]
    )
    rights_id: str | None = None


@router.post("/projects/{project_id}/material-preparation/analyze", status_code=202)
def analyze_material(project_id: str, body: AnalyzeBody, actor: Operator, db: DBSession):
    """Open a material-preparation plan revision from finished scene analyses.

    The scene analyses themselves are Agent tasks created by the crew route; this
    only versions their confirmed-input snapshot.  A document whose hash moved is
    refused, because the user would otherwise confirm a list built from a script
    that is no longer the one on screen.
    """
    from shine.models import ProjectDocument
    from shine.services import require_project

    project = require_project(db, project_id)
    document = db.get(ProjectDocument, body.document_id)
    if document is None or document.project_id != project_id:
        raise HTTPException(404, "MATERIAL_DOCUMENT_NOT_FOUND")
    if document.sha256 != body.expected_document_hash:
        raise HTTPException(409, {"code": "MATERIAL_DOCUMENT_STALE", "actual": document.sha256})
    plan, replayed = service.create_draft_plan(
        db,
        project,
        document,
        [scene.model_dump(mode="json") for scene in body.scene_preparations],
        actor=actor,
    )
    aggregated = service.aggregated_requirements(db, plan)
    db.commit()
    return {
        "status": "accepted",
        "replayed": replayed,
        "plan_revision_id": plan.id,
        "purpose": plan.purpose,
        "requirements": aggregated["requirements"],
        "conflicts": aggregated["conflicts"],
        "open_questions": aggregated["open_questions"],
        # The hash a confirmation has to quote. The server publishes it so the
        # page never has to reproduce the server's canonicalisation.
        "expected_snapshot": service.confirmation_snapshot_hash(plan),
        # The server's own segmentation: the scenes it will accept and the
        # fragments an answer is allowed to cite.
        "source_scenes": plan.payload.get("source_scenes") or [],
        "source_span_ids": plan.payload.get("source_span_ids") or [],
    }


@router.post("/projects/{project_id}/material-preparation/derive", status_code=202)
def derive_material(project_id: str, actor: Operator, db: DBSession, request: Request,
                    gateway: Annotated[LLMGateway, Depends(llm_gateway)]):
    """Open the material plan from the finished four-stage analysis.

    One project-wide consolidation call merges aliases into single characters
    (with per-scene appearance variants) and picks key scenes/props; the plan
    then derives from that catalog.  An unchanged basis reuses the catalog,
    so repeated derives buy no second call.  Same confirmation gates.
    """
    from shine.materialprep import derive, service
    from shine.services import require_project

    project = require_project(db, project_id)
    document, preparations, catalog = derive.derive_preparations(
        db, gateway, request.app.state.session_factory, project_id)
    plan, replayed = service.create_draft_plan(db, project, document, preparations, actor=actor)
    derive.enrich_character_definitions(db, plan, catalog)
    aggregated = service.aggregated_requirements(db, plan)
    db.commit()
    return {
        "status": "accepted",
        "replayed": replayed,
        "source": "four_stage_artifacts",
        "document_id": document.id,
        "plan_revision_id": plan.id,
        "requirements": aggregated["requirements"],
        "conflicts": aggregated["conflicts"],
        "open_questions": aggregated["open_questions"],
        "expected_snapshot": service.confirmation_snapshot_hash(plan),
    }


@router.get("/projects/{project_id}/material-preparation/entity-catalog")
def material_entity_catalog(project_id: str, db: DBSession):
    """The project-wide character/scene/prop consolidation, for display.

    The material centre joins this to each requirement so a character shows its
    canonical full name, stable identity and per-scene variants - the whole
    person, not a fragment.
    """
    from shine.materialprep.catalog import CATALOG_KIND
    from shine.models import AgentArtifact
    from shine.services import require_project

    require_project(db, project_id)
    row = db.scalars(select(AgentArtifact).where(
        AgentArtifact.project_id == project_id,
        AgentArtifact.kind == CATALOG_KIND,
        AgentArtifact.stale.is_(False),
    ).order_by(AgentArtifact.created_at.desc())).first()
    return {"catalog": (row.payload_json if row is not None else None)}


@router.get("/projects/{project_id}/material-preparation")
def material_progress(project_id: str, db: DBSession, settings: SettingsDep):
    from shine.media.output_layout import output_root

    return service.plan_progress(db, project_id, data_dir=output_root(db, settings))


@router.post("/projects/{project_id}/material-preparation/complete")
def complete_materials(project_id: str, actor: Operator, db: DBSession, settings: SettingsDep):
    """Judge the material completion condition and record the outcome.

    The material list is only ready when every required requirement is bound to
    a current master of this project that is approved, locked, of the right
    family and identity, still hashes to its registered digest. Existing source restrictions are
    checked when a master carries them.  The judgement comes back with the items that are still
    open and the reason for each, so the page never claims a completion it
    cannot prove.
    """
    from shine.comfyops.preproduction import current_material_plan
    from shine.services import add_audit, require_project

    require_project(db, project_id)
    from shine.media.output_layout import output_root

    evaluation = service.evaluate_materials(db, project_id, data_dir=output_root(db, settings))
    plan = current_material_plan(db, project_id)
    if plan is not None and evaluation["materials_ready"] and plan.status != "MATERIALS_READY":
        plan.status = "MATERIALS_READY"
        add_audit(db, actor, "MATERIALS_READY", "preproduction_plan", plan.id,
                  {"requirements": len(evaluation["requirements"])}, project_id=project_id)
        db.commit()
    add_audit(db, actor, "MATERIAL_COMPLETION_JUDGED", "preproduction_plan",
              plan.id if plan is not None else "none",
              {"materials_ready": evaluation["materials_ready"],
               "missing": len(evaluation["missing"])}, project_id=project_id)
    db.commit()
    return evaluation


@router.get("/projects/{project_id}/material-preparation/scenes")
def material_scenes(project_id: str, document_id: str, db: DBSession):
    """The server's own segmentation of one script document.

    The scene numbering and the citable fragments are server facts.  The page
    shows them so the user can see which scene is being prepared, and an agent
    answer can only cite a fragment that is on this list.
    """
    from shine.materialprep.source_spans import segment_document
    from shine.models import ProjectDocument
    from shine.services import require_project

    require_project(db, project_id)
    document = db.get(ProjectDocument, document_id)
    if document is None or document.project_id != project_id:
        raise HTTPException(404, "MATERIAL_DOCUMENT_NOT_FOUND")
    scenes = segment_document(document.text or "")
    return {
        "document_id": document.id,
        "document_sha256": document.sha256,
        "scenes": [scene.as_public() for scene in scenes],
        "span_ids": [span.id for scene in scenes for span in scene.spans],
    }


class PrepareScenesBody(APIModel):
    document_id: str
    expected_document_hash: str = Field(min_length=64, max_length=64)
    locked_story_facts: list[str] = Field(default_factory=list, max_length=100)
    allow_remote_text: bool = False
    replace_unknown_task_id: str | None = None
    replacement_key: str | None = Field(default=None, min_length=1, max_length=128)


class ReconcileSceneBody(APIModel):
    expected_state_version: int = Field(ge=0)


class TextConsentBody(APIModel):
    document_id: str
    expected_document_hash: str = Field(min_length=64, max_length=64)
    expected_scope_hash: str = Field(min_length=64, max_length=64)
    granted: bool


def _text_scope(db, gateway, project_id, document):
    from shine.director.pipeline import BIBLE_RUN_ROLE, SCENE_SCRIPT_RUN_ROLE
    from shine.h3.rulebook import digest
    from shine.llm.base import LLMError
    from shine.materialprep import text_consent

    roles = []
    for role in ("scriptwriter", "producer", "director", "cinematographer",
                 "continuity_supervisor", "editor", BIBLE_RUN_ROLE, SCENE_SCRIPT_RUN_ROLE):
        try:
            profile = gateway.profile_for_role(db, role)
        except LLMError:
            roles.append({"role": role, "configured": False})
            continue
        roles.append({"role": role, "configured": True, "provider": profile.name,
                      "base_url": profile.base_url, "model": profile.model,
                      "fingerprint": text_consent.fingerprint(profile),
                      "allowed": text_consent.allowed(db, project_id, document, role, profile)})
    scope = {"document_id": document.id, "document_sha256": document.sha256,
             "roles": [{k: v for k, v in r.items() if k != "allowed"} for r in roles]}
    return {**scope, "roles": roles, "scope_hash": digest(scope),
            "transmission": "仅当前任务必要剧本文字、锁定事实和视觉方向；不发送素材字节。"}


@router.get("/projects/{project_id}/material-preparation/text-consent/{document_id}")
def text_consent_scope(project_id: str, document_id: str, db: DBSession,
                       gateway: Annotated[LLMGateway, Depends(llm_gateway)]):
    from shine.models import ProjectDocument

    document = db.get(ProjectDocument, document_id)
    if document is None or document.project_id != project_id:
        raise HTTPException(404, "MATERIAL_DOCUMENT_NOT_FOUND")
    return _text_scope(db, gateway, project_id, document)


@router.post("/projects/{project_id}/material-preparation/text-consent")
def decide_text_consent(project_id: str, body: TextConsentBody, actor: Operator, db: DBSession,
                        gateway: Annotated[LLMGateway, Depends(llm_gateway)]):
    from shine.materialprep import text_consent
    from shine.models import ProjectDocument

    document = db.get(ProjectDocument, body.document_id)
    if document is None or document.project_id != project_id:
        raise HTTPException(404, "MATERIAL_DOCUMENT_NOT_FOUND")
    scope = _text_scope(db, gateway, project_id, document)
    if document.sha256 != body.expected_document_hash or scope["scope_hash"] != body.expected_scope_hash:
        raise HTTPException(409, "MATERIAL_TEXT_CONSENT_SCOPE_CHANGED")
    for row in scope["roles"]:
        if row["configured"]:
            profile = gateway.profile_for_role(db, row["role"])
            text_consent.decide(db, project_id, document, row["role"], profile, body.granted)
    db.commit()
    return _text_scope(db, gateway, project_id, document)


@router.post("/projects/{project_id}/material-preparation/scenes/prepare")
def prepare_material_scenes(
    project_id: str,
    body: PrepareScenesBody,
    request: Request,
    actor: Operator,
    db: DBSession,
    gateway: Annotated[LLMGateway, Depends(llm_gateway)],
):
    """Run the pre-production scriptwriter over every scene of one document.

    The server owns the segmentation, the numbering and the citation
    catalogue.  Each scene is one real routed call; a scene that fails is
    reported with its reason and the other scenes still run, because a long
    script must not lose the scenes that did work.
    """
    from shine.materialprep import scenes
    from shine.models import ProjectDocument
    from shine.services import require_project

    require_project(db, project_id)
    document = db.get(ProjectDocument, body.document_id)
    if document is None or document.project_id != project_id:
        raise HTTPException(404, "MATERIAL_DOCUMENT_NOT_FOUND")
    if document.sha256 != body.expected_document_hash:
        raise HTTPException(409, {"code": "MATERIAL_DOCUMENT_STALE", "actual": document.sha256})
    if body.replace_unknown_task_id or body.replacement_key:
        raise HTTPException(409, "MATERIAL_UNKNOWN_OUTCOME_REQUIRES_RECONCILIATION")
    if len(document.text or "") > 4000:
        # A long script is analysed one real call per scene, so the size itself
        # is not what makes the analysis unsafe.  What a long script needs is a
        # confirmed durable outline first: without it the scenes cannot be tied
        # back to an approved story.  The gate is therefore the confirmation,
        # not the character count - the previous unconditional refusal told the
        # operator to build and confirm an outline and then refused the analysis
        # anyway, which left the material list impossible to produce.
        from shine.materialprep import story as story_outline

        if story_outline.approved_outline_for(db, project_id, document.id) is None:
            raise HTTPException(409, {"code": "STORY_DURABLE_OUTLINE_REQUIRED",
                                      "message": "这份剧本太长，需要先确认总体大纲，再逐场分析。"
                                                 "请用「总体大纲与生产段落」：后台提取并归并全剧大纲，"
                                                 "确认之后回到这里点击「分析剧本」。",
                                      "note": "长剧本请先确认后台总体大纲任务，确认后即可逐场分析。"})
    result = scenes.prepare_scenes(
        gateway,
        request.app.state.session_factory,
        project_id,
        document,
        locked_story_facts=body.locked_story_facts,
        allow_remote_text=body.allow_remote_text,
        replace_unknown_task_id=body.replace_unknown_task_id,
        replacement_key=body.replacement_key,
    )
    if result.get("error_code"):
        raise HTTPException(409, result)
    return result


@router.post("/projects/{project_id}/material-preparation/scenes/{task_id}/reconcile")
def reconcile_material_scene(project_id: str, task_id: str, body: ReconcileSceneBody,
                             actor: Operator, db: DBSession):
    """Inspect one scene task and release it only with explicit not-sent proof.

    This endpoint never contacts the text provider. An UNKNOWN result remains
    attached to its original task identity and cannot be replaced here.
    """
    from shine.h3.rulebook import digest
    from shine.models import AgentArtifact, AgentTaskRecord, ProjectDocument, utcnow
    from shine.services import add_audit, require_project

    require_project(db, project_id)
    task = db.get(AgentTaskRecord, task_id)
    if (task is None or task.project_id != project_id
            or (task.role_id, (task.trace or {}).get("tool")) not in {
                ("scriptwriter", "script_prepare"), ("producer", "entity_catalog")}):
        raise HTTPException(404, "MATERIAL_SCENE_TASK_NOT_FOUND")
    if task.state_version != body.expected_state_version:
        raise HTTPException(409, "MATERIAL_SCENE_TASK_STATE_CHANGED")
    if task.status == "RUNNING" or task.lease_expires_at and task.lease_expires_at > utcnow():
        raise HTTPException(409, "MATERIAL_SCENE_TASK_STILL_RUNNING")

    decided = (task.trace.get("reconciliation") or {}).get("outcome")
    if decided in {"RESULT_ADOPTED", "NOT_SENT_PROVEN", "RESPONSE_RECEIVED_NO_ANSWER"}:
        # Reconciling twice must never turn a released scene back into an
        # unconfirmable one.
        return {"outcome": decided, "task_id": task.id, "state_version": task.state_version,
                "resubmittable": decided != "RESULT_ADOPTED",
                "note": "这一场已经核对过，结论不变。"}

    artifact = db.scalar(select(AgentArtifact).where(
        AgentArtifact.source_task_id == task.id,
        AgentArtifact.kind == "ScriptPreparation",
        AgentArtifact.stale.is_(False),
    ))
    document = db.get(ProjectDocument, task.trace.get("document_id"))
    artifact_matches = bool(
        artifact and artifact.project_id == project_id and artifact.producer_role == "scriptwriter"
        and artifact.sha256 == digest(artifact.payload_json)
        and task.trace.get("output_hash") == artifact.sha256
        and artifact.payload_json.get("scene_key") == task.trace.get("scene_key")
        and document and document.project_id == project_id
        and document.sha256 == task.trace.get("document_sha256")
    )
    if artifact_matches:
        task.status = "SUCCEEDED"
        task.error_code = None
        task.state_version += 1
        task.trace = {**task.trace, "external_outcome": "SUCCEEDED",
                      "reconciliation": {"outcome": "RESULT_ADOPTED", "artifact_id": artifact.id,
                                         "checked_at": utcnow().isoformat()}}
        add_audit(db, actor, "MATERIAL_SCENE_RESULT_ADOPTED", "agent_task", task.id,
                  {"artifact_id": artifact.id, "artifact_sha256": artifact.sha256,
                   "external_calls": 0}, project_id=project_id)
        db.commit()
        return {"outcome": "RESULT_ADOPTED", "task_id": task.id, "artifact_id": artifact.id,
                "artifact_sha256": artifact.sha256, "resubmittable": False}

    if (task.status in {"FAILED", "REJECTED"}
            and task.trace.get("external_outcome") == "NOT_SENT"):
        previous_identity = task.request_identity
        task.status = "SUPERSEDED"
        task.error_code = None
        task.request_identity = None
        task.lease_owner, task.lease_expires_at = None, None
        task.state_version += 1
        task.trace = {**task.trace, "reconciliation": {
            "outcome": "NOT_SENT_PROVEN", "previous_request_identity": previous_identity,
            "checked_at": utcnow().isoformat(),
        }}
        add_audit(db, actor, "MATERIAL_SCENE_NOT_SENT_PROVEN", "agent_task", task.id,
                  {"previous_request_identity": previous_identity, "resubmittable": True,
                   "external_calls": 0}, project_id=project_id)
        db.commit()
        return {"outcome": "NOT_SENT_PROVEN", "task_id": task.id,
                "state_version": task.state_version, "resubmittable": True,
                "note": "原请求已证明未发出；再次分析时才会创建新的模型请求。"}

    # The provider answered and produced no usable answer: a refusal (wrong
    # key, unknown model, rate limit) or an answer that broke the contract.
    # The request is finished, so no answer exists that a second request
    # could duplicate.  This is not "not sent" and it is not "unknown", and
    # the retry stays bounded so a scene the model can never satisfy stops
    # and waits for a person instead of burning calls.
    if (task.status in {"FAILED", "REJECTED"}
            and task.trace.get("external_outcome") == "RESPONSE_RECEIVED"):
        attempts = int((task.trace.get("reconciliation") or {}).get("attempt", 0))
        if attempts >= MAX_RECONCILE_ATTEMPTS:
            add_audit(db, actor, "MATERIAL_SCENE_RETRIES_EXHAUSTED", "agent_task", task.id,
                      {"attempts": attempts, "external_calls": 0, "resubmittable": False},
                      project_id=project_id)
            db.commit()
            return {"outcome": "RETRIES_EXHAUSTED", "task_id": task.id,
                    "state_version": task.state_version, "resubmittable": False,
                    "note": "这一场已经重试到上限；请修改剧本后重新导入，或取消本次分析。"}
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
        add_audit(db, actor, "MATERIAL_SCENE_RESPONSE_RECEIVED_NO_ANSWER", "agent_task", task.id,
                  {"previous_request_identity": previous_identity, "attempt": attempts + 1,
                   "response_status": task.trace.get("response_status"),
                   "resubmittable": True, "external_calls": 0},
                  project_id=project_id)
        db.commit()
        return {"outcome": "RESPONSE_RECEIVED_NO_ANSWER", "task_id": task.id,
                "state_version": task.state_version, "resubmittable": True,
                "note": "服务商已经答复但没有可用结果；再次分析时会创建一次新的模型请求。"}

    external_outcome = task.trace.get("external_outcome") or "UNKNOWN"
    add_audit(db, actor, "MATERIAL_SCENE_RECONCILIATION_UNCONFIRMABLE", "agent_task", task.id,
              {"external_outcome": external_outcome, "status": task.status,
               "external_calls": 0, "resubmittable": False}, project_id=project_id)
    db.commit()
    return {"outcome": "UNCONFIRMABLE", "task_id": task.id, "state_version": task.state_version,
            "external_outcome": external_outcome, "resubmittable": False,
            "note": "没有结果或未发送证据；保留原 UNKNOWN，不能创建替代请求。"}


@router.get("/projects/{project_id}/material-preparation/scenes/status")
def material_scene_status(project_id: str, document_id: str, db: DBSession):
    """Live per-scene analysis progress, readable after any refresh.

    The prepare request is synchronous and long; this read path lets the page
    show each scene's durable state while it runs and after a restart, instead
    of keeping the outcome only in the browser's memory.
    """
    from shine.materialprep import scenes
    from shine.models import ProjectDocument
    from shine.services import require_project

    require_project(db, project_id)
    document = db.get(ProjectDocument, document_id)
    if document is None or document.project_id != project_id:
        raise HTTPException(404, "MATERIAL_DOCUMENT_NOT_FOUND")
    return scenes.scene_status(db, project_id, document)


class ResendSceneBody(APIModel):
    expected_state_version: int = Field(ge=0)
    evidence: str = Field(min_length=1, max_length=500)


@router.post("/projects/{project_id}/material-preparation/scenes/{task_id}/resend-unknown")
def resend_unknown_material_scene(project_id: str, task_id: str, body: ResendSceneBody,
                                  actor: Operator, db: DBSession):
    """One person's explicit decision to ask one unknown material scene again.

    The old request may still own an answer upstream, so this never happens by
    itself: the person confirms, the decision is audited with their evidence,
    and the next analysis asks the scene exactly once more.  The old task is
    never recorded as "not sent".
    """
    from shine.materialprep import scenes
    from shine.models import AgentTaskRecord
    from shine.services import require_project

    require_project(db, project_id)
    task = db.get(AgentTaskRecord, task_id)
    if (task is None or task.project_id != project_id
            or (task.role_id, (task.trace or {}).get("tool")) not in {
                ("scriptwriter", "script_prepare"), ("producer", "entity_catalog")}):
        raise HTTPException(404, "MATERIAL_SCENE_TASK_NOT_FOUND")
    if task.state_version != body.expected_state_version:
        raise HTTPException(409, "MATERIAL_SCENE_TASK_STATE_CHANGED")
    result = scenes.authorize_scene_unknown_resend(db, task, actor, body.evidence)
    db.commit()
    return result


@router.post("/projects/{project_id}/material-preparation/confirm")
def confirm_material(project_id: str, body: MaterialConfirmation, actor: Operator, db: DBSession):
    return service.confirm(db, actor, project_id, body)


@router.post("/projects/{project_id}/material-preparation/generate", status_code=202)
def generate_material(project_id: str, body: GenerateBody, actor: Operator, db: DBSession):
    """Queue a real batch.  Returns immediately; the Worker renders it."""
    result = service.create_batch(
        db,
        project_id,
        confirmation_id=body.confirmation_id,
        confirmation_hash=body.confirmation_hash,
        requirement_ids=body.requirement_ids,
        idempotency_key=body.idempotency_key,
        rights_id=body.rights_id,
        seeds=body.seeds,
    )
    return {"status": "accepted", **result}


@router.get("/projects/{project_id}/material-preparation/runs/{run_id}")
def run_detail(project_id: str, run_id: str, db: DBSession):
    report = service.refresh_run_status(db, run_id)
    if report["project_id"] != project_id:
        raise HTTPException(409, "CROSS_PROJECT_REFERENCE")
    return report


@router.post("/projects/{project_id}/material-preparation/runs/{run_id}/cancel", status_code=202)
def cancel_run(project_id: str, run_id: str, body: StateVersionBody, actor: Operator, db: DBSession):
    _require_ownership(db, project_id, run_id)
    return {"status": "accepted", **service.request_cancel(db, run_id, body.expected_state_version,
                                                    idempotency_key=body.idempotency_key)}


@router.post("/projects/{project_id}/material-preparation/runs/{run_id}/reconcile", status_code=202)
def reconcile_run(project_id: str, run_id: str, body: ReconcileBody, actor: Operator, db: DBSession):
    _require_ownership(db, project_id, run_id)
    return {"status": "accepted", **service.reconcile_run(db, run_id, body.expected_state_version,
                                                   idempotency_key=body.idempotency_key,
                                                   item_ids=body.item_ids or None)}


@router.post("/projects/{project_id}/material-preparation/runs/{run_id}/resume", status_code=202)
def resume_run(project_id: str, run_id: str, body: ResumeBody, actor: Operator, db: DBSession):
    _require_ownership(db, project_id, run_id)
    return {"status": "accepted", **service.resume_items(db, run_id, body.item_ids, body.expected_state_version,
                                                  idempotency_key=body.idempotency_key)}


class MergeCharacterBody(APIModel):
    canonical_requirement_id: str
    alias_requirement_id: str
    master_asset_id: str


@router.post("/projects/{project_id}/material-preparation/merge-character")
def merge_character(project_id: str, body: MergeCharacterBody, actor: Operator, db: DBSession,
                    settings: SettingsDep):
    from shine.api_preparation import locked
    from shine.comfyops.assets import rights_status
    from shine.comfyops.models import AssetRequirement
    from shine.comfyops.preproduction import current_material_plan
    from shine.media.artifacts import safe_asset_path
    from shine.models import Asset
    from shine.security import sha256_bytes

    locked(db)
    plan = current_material_plan(db, project_id)
    canonical = db.get(AssetRequirement, body.canonical_requirement_id)
    alias = db.get(AssetRequirement, body.alias_requirement_id)
    if (plan is None or canonical is None or alias is None or canonical.id == alias.id
            or any(row.project_id != project_id or row.plan_id != plan.id
                   or row.payload.get("family") != "character" or row.status != "CONFIRMED"
                   for row in (canonical, alias))):
        raise HTTPException(409, "MATERIAL_CHARACTER_REQUIREMENTS_NOT_CURRENT")
    entity = canonical.payload["entity_id"]
    alias_entity = alias.payload["entity_id"]
    if entity == alias_entity or canonical.payload.get("canonical_entity_id"):
        raise HTTPException(409, "MATERIAL_CHARACTER_ALIAS_INVALID")
    asset = db.get(Asset, body.master_asset_id)
    if (asset is None or asset.project_id != project_id or asset.kind != "image" or asset.role != "character"
            or asset.entity_id != entity or not asset.approved or not asset.locked):
        raise HTTPException(409, "ASSET_IDENTITY_MISMATCH")
    provenance = asset.provenance or {}
    if (provenance.get("offline_only") or provenance.get("is_mock")
            or not rights_status(db, project_id, provenance.get("rights_id"))["approved"]):
        raise HTTPException(409, "ASSET_NOT_APPROVED_FOR_PRODUCTION")
    try:
        from shine.media.output_layout import output_root

        content = safe_asset_path(output_root(db, settings), asset.storage_path).read_bytes()
    except OSError as exc:
        raise HTTPException(409, "ASSET_FILE_MISSING") from exc
    if sha256_bytes(content) != asset.sha256:
        raise HTTPException(409, "ASSET_HASH_CHANGED")
    service.bind_material_master(db, canonical, asset, actor)
    service.bind_material_master(db, alias, asset, actor, canonical_entity_id=entity)
    service.retire_unused_masters(db, plan, asset, actor, entities={entity, alias_entity})
    db.commit()
    return {"asset_id": asset.id, "canonical_entity_id": entity, "alias_entity_id": alias_entity}


@router.post("/projects/{project_id}/material-preparation/regenerate", status_code=202)
def regenerate_requirement(project_id: str, body: RegenerateBody, actor: Operator, db: DBSession):
    """Generate another version of one requirement, keeping the old candidates.

    This is a new generation revision with its own brief and seed, not a cleared
    attempt re-run: the user may still want to pick the previous image.
    """
    from shine.comfyops.models import AssetRequirement
    from shine.materialprep.contracts import MaterialImageBrief

    row = db.get(AssetRequirement, body.requirement_id)
    if row is None or row.project_id != project_id:
        raise HTTPException(404, "MATERIAL_REQUIREMENT_NOT_FOUND")
    try:
        brief = MaterialImageBrief.model_validate(body.brief).model_dump(mode="json")
    except Exception as exc:
        raise HTTPException(422, {"code": "MATERIAL_BRIEF_INVALID", "detail": str(exc)[:400]}) from exc
    if brief["questions"]:
        # A supplied brief goes straight to the renderer, and the renderer
        # refuses an unanswered clarification.  Queueing it anyway would spend
        # a generation revision to rediscover the same refusal, so the answer
        # has to be written into the appearance text here.
        raise HTTPException(422, {"code": "MATERIAL_BRIEF_QUESTIONS_UNANSWERED",
                                  "questions": brief["questions"],
                                  "detail": "把对以下问题的回答写进外观描述后再生成；带未回答问题的描述不会出图。"})
    result = service.create_batch(
        db,
        project_id,
        confirmation_id=body.confirmation_id,
        confirmation_hash=body.confirmation_hash,
        requirement_ids=[body.requirement_id],
        idempotency_key=body.idempotency_key,
        rights_id=body.rights_id,
        seeds={body.requirement_id: body.seed},
        briefs={body.requirement_id: brief},
        new_revision=True,
    )
    return {"status": "accepted", **result}


class RegenerateAllBody(APIModel):
    idempotency_key: str = Field(min_length=1, max_length=128)
    requirement_ids: list[str] = Field(default_factory=list, max_length=200)


@router.post("/projects/{project_id}/material-preparation/regenerate-all", status_code=202)
def regenerate_all_requirements(project_id: str, body: RegenerateAllBody, actor: Operator,
                                db: DBSession, settings: SettingsDep):
    """Queue one fresh generation revision for every confirmed requirement.

    A batch whose pictures miss the brief is a normal outcome, not an error to
    repair by hand: the cure is one new call per requirement with the current
    prompt template and the frozen style, which is what this does. The older
    candidates are kept, so the person can still compare and promote either.

    No brief is supplied on purpose. Passing the previous answers forward would
    replay exactly the description that produced the rejected pictures, and a
    stored answer can also carry open questions the renderer refuses.
    """
    from shine.media.output_layout import output_root

    state = service.plan_progress(db, project_id, data_dir=output_root(db, settings))
    confirmation = state.get("confirmation") or {}
    requirements = state.get("requirements") or []
    if not confirmation:
        raise HTTPException(409, "MATERIAL_PLAN_NOT_CONFIRMED")
    wanted = set(body.requirement_ids or [])
    targets = [row for row in requirements if row.get("strategy") not in {"omit_prop", "skip"}
               and (not wanted or row["requirement_id"] in wanted)]
    if not targets:
        raise HTTPException(409, "MATERIAL_NO_REQUIREMENTS")
    # A requirement already satisfied by a master is recorded as "reuse_master",
    # and a batch may not generate for it, so the rows are switched back to
    # generate below before the batch is queued.
    from shine.materialprep.models import MaterialItem
    from shine.materialprep.states import ITEM_IN_FLIGHT, ITEM_NEEDS_REVIEW, ITEM_QUEUED

    # A queue item or an unreviewed result would otherwise block the whole
    # rerun for every requirement at once. Retiring it keeps the row as history
    # and lets the new revision exist; the person asking for a full rerun is
    # explicitly choosing fresh pictures over whatever is still pending.
    blockers = list(db.scalars(select(MaterialItem).where(
        MaterialItem.project_id == project_id,
        MaterialItem.requirement_id.in_([row["requirement_id"] for row in targets]),
        MaterialItem.status.in_([*ITEM_IN_FLIGHT, ITEM_NEEDS_REVIEW, ITEM_QUEUED]),
    )))
    for stale in blockers:
        # CANCELLED is a real terminal state; the row keeps its attempts and
        # candidates as history, it only stops holding the requirement.
        stale.status = "CANCELLED"
        stale.error_code = "SUPERSEDED_BY_FULL_RERUN"
        stale.state_version += 1
    if blockers:
        db.flush()

    # Re-confirming through the plan snapshot proved brittle for this route: the
    # aggregate the plan carries and the requirement rows can drift apart after a
    # long material session, and the confirm then refuses the whole rerun with a
    # bare MATERIAL_PLAN_STALE. What this route needs is narrower than a new
    # confirmation: every target must be asked to generate again. That is written
    # onto the requirement rows directly, with an audit entry, and the existing
    # confirmation keeps covering the batch.
    from shine.comfy.workflow import graph_hash
    from shine.comfyops.models import AssetRequirement
    from shine.services import add_audit

    for entry in targets:
        requirement = db.get(AssetRequirement, entry["requirement_id"])
        if requirement is None or requirement.project_id != project_id:
            raise HTTPException(404, "MATERIAL_REQUIREMENT_NOT_FOUND")
        requirement.payload = {
            **(requirement.payload or {}),
            "strategy": "generate",
            "bound_asset_id": None,
            "bound_asset_sha256": None,
        }
        requirement.sha256 = graph_hash(requirement.payload)
        requirement.status = "CONFIRMED"
    db.flush()
    add_audit(db, actor, "MATERIAL_FULL_RERUN_REQUESTED", "preproduction_plan",
              state["plan_revision_id"], {"requirements": len(targets)},
              project_id=project_id)

    # The hash recorded on the plan is the one the batch is judged against.
    # `plan_progress` recomputes a confirmation from the current requirement
    # rows, so it moves the moment a strategy is switched back to generate -
    # which this route does on purpose. Using the recomputed value would always
    # look stale.
    from shine.comfyops.models import PreProductionPlan

    source_plan = db.get(PreProductionPlan, state["plan_revision_id"])
    if source_plan is None:
        raise HTTPException(409, "MATERIAL_PLAN_NOT_FOUND")
    recorded_hash = ((source_plan.payload or {}).get("confirmation") or {}).get("confirmation_hash")
    if not recorded_hash:
        raise HTTPException(409, "MATERIAL_PLAN_NOT_CONFIRMED")
    result = service.create_batch(
        db,
        project_id,
        confirmation_id=confirmation["confirmation_id"],
        confirmation_hash=recorded_hash,
        requirement_ids=[row["requirement_id"] for row in targets],
        idempotency_key=body.idempotency_key,
        rights_id=None,
        new_revision=True,
    )
    return {"status": "accepted", **result}


def _require_ownership(db, project_id: str, run_id: str) -> None:
    from shine.materialprep.models import MaterialRun

    run = db.get(MaterialRun, run_id)
    if run is None:
        raise HTTPException(404, "MATERIAL_RUN_NOT_FOUND")
    if run.project_id != project_id:
        raise HTTPException(409, "CROSS_PROJECT_REFERENCE")


# --- setup capability ------------------------------------------------------


@router.get("/setup/image-readiness")
def image_readiness(db: DBSession, settings: SettingsDep):
    """Four-layer readiness per family.  Layer four is the only one that renders.

    The node layer is checked against the live renderer when the bound instance
    answers.  The probe is a read-only GET with a short timeout: a renderer that
    is down must not turn the setup card into a hanging request, it only means
    the node layer stays unproven.
    """
    config = runtime.load_config(settings)
    readiness = runtime.readiness(db, settings, object_info=_probe_object_info(config, settings))
    if not readiness["configured"]:
        readiness["binding_candidate"] = _managed_image_binding(db, settings)
    return readiness


def _managed_image_binding(db, settings):
    from shine.comfyops.image_renderer import load_image_recipe
    from shine.comfyops.models import ComfyInstance

    try:
        renderer = load_image_recipe(settings)["renderer"]
    except HTTPException:
        return None
    instance = db.get(ComfyInstance, renderer["instance_id"])
    if (not instance or not instance.payload.get("managed") or instance.payload.get("is_mock")
            or not instance.payload.get("root") or instance.payload.get("port") != renderer["port"]):
        return None
    return {
        "instance_id": instance.id,
        "base_url": f"http://127.0.0.1:{renderer['port']}",
        "model_root": str(Path(instance.payload["root"]) / "models"),
    }


def _probe_object_info(config, settings) -> dict | None:
    """Ask the bound renderer what it declares, without waiting on it."""
    if not (config.base_url and config.instance_id):
        return None
    import httpx

    from shine.security import require_loopback_url

    try:
        base_url = require_loopback_url(
            config.base_url, allow_remote=bool(getattr(settings, "allow_remote_comfyui", False))
        )
    except HTTPException:
        return None
    try:
        response = httpx.get(f"{base_url}/object_info", timeout=3.0, trust_env=False)
        response.raise_for_status()
        payload = response.json()
    except Exception:  # noqa: BLE001 - an unreachable renderer only leaves a layer unproven
        return None
    return payload if isinstance(payload, dict) else None


@router.post("/setup/image-runtime")
def bind_image_runtime(body: ImageRuntimeBody, actor: Operator, db: DBSession, settings: SettingsDep):
    """Bind an already-registered managed instance.  This never downloads."""
    from shine.comfyops.models import ComfyInstance
    from shine.models import utcnow
    from shine.security import require_loopback_url
    from shine.services import add_audit

    db.execute(text("BEGIN IMMEDIATE"))
    instance = db.get(ComfyInstance, body.instance_id)
    if instance is None:
        raise HTTPException(404, "COMFY_INSTANCE_NOT_FOUND")
    if not instance.payload.get("managed"):
        # The image runtime may only bind an instance this installation
        # manages: a renderer the workbench cannot prove it owns would keep
        # the weights resident and could never be released again.
        raise HTTPException(409, {
            "code": "IMAGE_RUNTIME_INSTANCE_NOT_MANAGED",
            "detail": "出图运行时只能绑定本工作台受管的实例。",
        })
    base_url = require_loopback_url(body.base_url, allow_remote=bool(settings.allow_remote_comfyui))
    # The bound URL and the managed recipe must agree on the port: a mismatch
    # used to start a renderer on the recipe port while submitting prompts to
    # whatever answered on base_url (audit R13).  A missing recipe (binding
    # before the local configuration was generated) keeps the old behaviour.
    from shine.comfyops.image_renderer import load_image_recipe

    try:
        recipe = load_image_recipe(settings)
    except HTTPException:
        recipe = None
    if recipe is not None and recipe["renderer"].get("instance_id") == body.instance_id:
        recipe_port = int(recipe["renderer"].get("port") or 0)
        bound_port = 0
        try:
            from urllib.parse import urlparse

            bound_port = int(urlparse(base_url).port or 0)
        except ValueError:
            bound_port = 0
        if recipe_port and bound_port and recipe_port != bound_port:
            raise HTTPException(409, {
                "code": "IMAGE_RUNTIME_URL_MISMATCH",
                "detail": "绑定的 base_url 端口与受管运行配置的渲染端口不一致；请绑定同一端口。",
                "recipe_port": recipe_port,
                "bound_port": bound_port,
            })
    current = runtime.load_config(settings)
    if body.expected_config_revision and current.config_revision != body.expected_config_revision:
        raise HTTPException(409, {"code": "IMAGE_RUNTIME_CONFIG_STALE", "actual": current.config_revision})
    config = runtime.ImageRuntimeConfig(
        config_revision=current.config_revision + 1,
        instance_id=body.instance_id,
        base_url=base_url,
        model_root=body.model_root,
        graph_sha256="",
        parameter_map={},
        model_files=[dict(entry) for entry in runtime.qwen_workflow.MODEL_FILES],
        bound_at=utcnow().isoformat(),
    )
    runtime.save_config(settings, config)
    add_audit(db, actor, "IMAGE_RUNTIME_BOUND", "comfy_instance", body.instance_id,
              {"config_revision": config.config_revision, "base_url": base_url})
    db.commit()
    return {"config_revision": config.config_revision, "digest": runtime.config_digest(settings),
            "readiness": runtime.readiness(db, settings)}


def _control_operation_response(operation) -> dict:
    """Map a terminal control operation onto the legacy route's shape."""
    if operation.status == "SUCCEEDED":
        result = dict(operation.result or {})
        result.setdefault("operation_id", operation.id)
        return result
    if operation.status in ("FAILED", "REFUSED"):
        raise HTTPException(409, {
            "code": operation.failure_code or operation.status,
            "detail": operation.failure_code or operation.status,
            "operation_id": operation.id,
        })
    # Still PENDING: the Worker has not picked the intent up within the
    # bounded wait.  Report it instead of pretending an outcome.
    raise HTTPException(409, {
        "code": "RUNTIME_OPERATION_PENDING",
        "detail": "控制意图已受理，但 Worker 尚未执行完成；稍后用 operation_id 查询。",
        "operation_id": operation.id,
    })


@router.post("/setup/image-runtime/start")
def start_image_runtime_renderer(request: Request, actor: Operator, settings: SettingsDep):
    """Start the managed renderer that serves the bound image runtime.

    ComfyUI belongs to the workbench: a renderer that a script started would
    keep the weights resident in GPU memory and could not be released later.
    Under the unified control plane the request thread only records the
    intent; the resident Worker starts the renderer inside the installation
    lifecycle guard (audit R3).  With the orchestrator flag off, the legacy
    direct call keeps working exactly as before — one path at a time.
    """
    config = runtime.load_config(settings)
    if not config.instance_id or not config.base_url:
        raise HTTPException(409, {"code": "IMAGE_RUNTIME_NOT_BOUND", "detail": "IMAGE_RUNTIME_NOT_BOUND"})
    factory = request.app.state.session_factory
    from shine.runtime_orchestrator.contracts import orchestrator_enabled

    if orchestrator_enabled():
        from shine.runtime_orchestrator.api import submit_control_intent

        operation = submit_control_intent(
            request, actor,
            action="ensure",
            installation_id=config.instance_id,
            idempotency_key=f"image-ensure:{config.instance_id}:{uuid4().hex[:12]}",
            reason="image-runtime/start",
            wait_seconds=240.0,
        )
        return _control_operation_response(operation)
    from shine.comfyops.image_renderer import start_image_renderer

    return start_image_renderer(factory, settings, instance_id=config.instance_id, actor=actor)


@router.post("/setup/image-runtime/stop")
def stop_image_runtime_renderer(request: Request, actor: Operator, settings: SettingsDep):
    """Stop the managed image renderer and give the GPU memory back.

    Executed by the resident Worker through the control plane, so a stop can
    never run concurrently with a start on the same installation and can never
    bypass the in-flight work protection (audit R6).
    """
    config = runtime.load_config(settings)
    if not config.instance_id:
        return {"status": "NOT_STARTED", "instance_id": ""}
    from shine.runtime_orchestrator.contracts import orchestrator_enabled

    if orchestrator_enabled():
        from shine.runtime_orchestrator.api import submit_control_intent

        operation = submit_control_intent(
            request, actor,
            action="stop",
            installation_id=config.instance_id,
            idempotency_key=f"image-stop:{config.instance_id}:{uuid4().hex[:12]}",
            reason="image-runtime/stop",
            wait_seconds=90.0,
        )
        return _control_operation_response(operation)
    factory = request.app.state.session_factory
    from shine.comfyops.image_renderer import stop_image_renderer

    return stop_image_renderer(factory, settings, instance_id=config.instance_id, actor=actor)


@router.post("/setup/image-runtime/validate", status_code=202)
def validate_image_runtime(body: ValidateBody, actor: Operator, db: DBSession, settings: SettingsDep):
    """Queue the three real small-sample renders through the same Worker queue.

    A validation batch is allowed at ``GRAPH_VERIFIED``; that is what breaks the
    first-sample circular dependency.  It still renders for real - a validation
    that did not produce bytes would be claiming a capability it never tested.
    """
    from shine.comfyops.assets import require_rights
    from shine.services import require_project

    project = require_project(db, body.project_id)
    if body.rights_id:
        require_rights(db, project.id, body.rights_id)
    config = runtime.load_config(settings)
    if not config.base_url or not config.instance_id:
        raise HTTPException(409, {"code": "IMAGE_RUNTIME_NOT_READY", "detail": "IMAGE_RUNTIME_NOT_BOUND"})
    result = service.create_validation_batch(
        db,
        body.project_id,
        families=body.families,
        idempotency_key=body.idempotency_key,
        rights_id=body.rights_id,
    )
    return {"status": "accepted", **result}
