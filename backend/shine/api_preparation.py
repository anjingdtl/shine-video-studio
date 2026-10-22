"""V1.3 APIs. Human grants cannot be minted by an Agent identity."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text

from shine.api_common import DBSession, app_settings
from shine.api_operator import Operator
from shine.comfy.workflow import graph_hash
from shine.comfyops import approval, preproduction
from shine.comfyops.assets import (
    candidate_qc,
    create_candidate,
    fake_image,
    promote,
    require_rights,
    verify_candidate,
)
from shine.comfyops.installer import PackageSpec, check_url, safe_target
from shine.comfyops.inventory import FakeWindowsHostProbe
from shine.comfyops.models import (
    CandidateAsset,
    ComfyInstance,
    EnvironmentSnapshot,
    InstallationAttempt,
    InstallationPlan,
    ModelPackage,
    NodePackage,
    PreProductionPlan,
    ResourceLease,
    RightsRecord,
    VoiceProfile,
    VoiceRender,
    WorkflowRevision,
)
from shine.comfyops.registry import CATEGORIES, WorkflowDraft, assemble_template, validate_workflow
from shine.comfyops.scheduler import ResourceScheduler, resource_slot
from shine.comfyops.tasks import preparation_task
from shine.config import Settings
from shine.models import ProjectDocument, WorkflowProfile, utcnow
from shine.services import add_audit, require_project
from shine.voiceops.providers import FakeVoiceProvider, LocalVoiceHTTPProvider, subtitles, verify_dialogue

router = APIRouter(prefix="/api/v1", tags=["V1.3 preparation"])
SettingsDep = Annotated[Settings, Depends(app_settings)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Decision(Strict):
    grant_id: str
    snapshot: str
    offline: bool = True
    evidence: str = Field(min_length=5, max_length=2000)


def public(row):
    return jsonable_encoder({c.name: getattr(row, c.name) for c in row.__table__.columns})


def get(db, cls, identity):
    row = db.get(cls, identity)
    if not row:
        raise HTTPException(404, "PREPARATION_RECORD_NOT_FOUND")
    return row


def save(db, cls, payload, status, **kw):
    row = cls(payload=payload, sha256=graph_hash(payload), status=status, **kw)
    db.add(row)
    db.flush()
    return row


def audit(db, actor, event, row, payload=None):
    add_audit(
        db,
        actor,
        event,
        row.__tablename__,
        row.id,
        payload or {"hash": row.sha256},
        project_id=getattr(row, "project_id", None),
    )


def locked(db):
    db.execute(text("BEGIN IMMEDIATE"))


@router.post("/comfyops/instances/discover")
@router.post("/comfyops/inventory/refresh")
def refresh(db: DBSession):
    payload = FakeWindowsHostProbe().scan()
    row = save(db, EnvironmentSnapshot, payload, "MOCK_VERIFIED")
    for instance in payload["instances"]:
        save(db, ComfyInstance, instance, instance["health"])
    audit(db, "comfyops", "INVENTORY_REFRESHED", row)
    db.commit()
    return public(row)


@router.get("/comfyops/inventory")
def inventory(db: DBSession):
    return [public(r) for r in db.scalars(select(EnvironmentSnapshot).order_by(EnvironmentSnapshot.created_at.desc()))]


@router.get("/comfyops/instances")
def instances(db: DBSession):
    return [public(r) for r in db.scalars(select(ComfyInstance))]


@router.get("/comfyops/models")
def models(db: DBSession):
    return [public(r) for r in db.scalars(select(ModelPackage))]


@router.get("/comfyops/nodes")
def nodes(db: DBSession):
    return [public(r) for r in db.scalars(select(NodePackage))]


@router.post("/comfyops/packages")
def register_package(body: PackageSpec, db: DBSession):
    try:
        for file in body.files:
            check_url(file.url, {"huggingface.co", "raw.githubusercontent.com", "github.com"})
            safe_target(__import__("pathlib").Path.cwd() / "scoped-root", file.relative_path)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    cls = ModelPackage if body.kind == "model" else NodePackage
    row = save(db, cls, body.model_dump(), "UNVERIFIED_ORIGIN")
    audit(db, "comfyops", "PACKAGE_REGISTERED", row)
    db.commit()
    return public(row)


class InstallRequest(Strict):
    project_id: str
    package_ids: list[str] = Field(min_length=1, max_length=50)
    disk_free: int = Field(ge=0)
    isolated: bool = True
    executor: Literal["fake", "local"] = "fake"


@router.post("/comfyops/install-plans")
def plan_install(body: InstallRequest, db: DBSession):
    require_project(db, body.project_id)
    if not body.isolated:
        raise HTTPException(409, "EXISTING_INSTANCE_REQUIRES_LOCAL_BACKUP_AND_IDLE_QUEUE")
    packages, seen, visiting = [], set(), set()

    def include(identity):
        if identity in visiting:
            raise HTTPException(409, "DEPENDENCY_CYCLE")
        if identity in seen:
            return
        row = db.get(ModelPackage, identity) or db.get(NodePackage, identity)
        if not row:
            raise HTTPException(409, "PACKAGE_MISSING")
        visiting.add(identity)
        for dep, revision in row.payload["dependencies"].items():
            found = db.get(ModelPackage, dep) or db.get(NodePackage, dep)
            if not found or found.payload["revision"] != revision:
                raise HTTPException(409, "DEPENDENCY_VERSION_MISMATCH")
            include(dep)
        visiting.remove(identity)
        seen.add(identity)
        packages.append({"id": row.id, "hash": row.sha256, "spec": row.payload})

    for identity in body.package_ids:
        include(identity)
    size = sum(f["size"] for p in packages for f in p["spec"]["files"])
    if size * 2 > body.disk_free:
        raise HTTPException(409, "INSUFFICIENT_DISK")
    row = save(
        db,
        InstallationPlan,
        {
            "packages": packages,
            "download_bytes": size,
            "disk_required": size * 2,
            "isolated": True,
            "executor": "FAKE_ONLY" if body.executor == "fake" else "LOCAL_ISOLATED",
            "risks": ["THIRD_PARTY_CODE" if p["spec"]["kind"] == "node" else "MODEL_LICENSE" for p in packages],
        },
        "AWAITING_APPROVAL",
        project_id=body.project_id,
    )
    audit(db, "comfyops", "INSTALL_PLAN_CREATED", row)
    db.commit()
    return public(row)


@router.get("/comfyops/install-plans")
def plans(db: DBSession):
    return [public(r) for r in db.scalars(select(InstallationPlan))]


@router.get("/comfyops/install-plans/{identity}")
def install_plan(identity: str, db: DBSession):
    return public(get(db, InstallationPlan, identity))


class GrantRequest(Strict):
    action: Literal["install", "promote", "voice_freeze", "manifest_freeze", "workflow_publish", "rights_approve"]
    target_id: str


TARGETS = {
    "install": InstallationPlan,
    "promote": CandidateAsset,
    "voice_freeze": VoiceProfile,
    "manifest_freeze": PreProductionPlan,
    "workflow_publish": WorkflowRevision,
    "rights_approve": RightsRecord,
}


@router.post("/preparation/approval-grants")
def grant(body: GrantRequest, actor: Operator, db: DBSession):
    return approval.issue(db, actor, body.action, get(db, TARGETS[body.action], body.target_id))


@router.post("/comfyops/install-plans/{identity}/approve")
def approve_install(identity: str, body: Decision, actor: Operator, db: DBSession):
    locked(db)
    row = get(db, InstallationPlan, identity)
    if row.status != "AWAITING_APPROVAL":
        raise HTTPException(409, "INSTALL_STATE_INVALID")
    approval.consume(db, actor, "install", row, body.grant_id, body.snapshot)
    for package in row.payload["packages"]:
        if package["spec"]["license_id"].lower() in {"unknown", "unverified"}:
            raise HTTPException(409, "LICENSE_NOT_APPROVED")
        if package["spec"]["usage"] == "commercial" and "research" in package["spec"]["license_id"].lower():
            raise HTTPException(409, "LICENSE_NOT_APPROVED")
    row.status = "APPROVED"
    row.payload = {
        **row.payload,
        "approved_by": actor,
        "approval_evidence": body.evidence,
        "execute_before": (utcnow() + timedelta(minutes=30)).isoformat(),
    }
    row.sha256 = graph_hash(row.payload)
    audit(db, actor, "INSTALL_PLAN_APPROVED", row)
    db.commit()
    return public(row)


class Execute(Strict):
    plan_hash: str
    executor: Literal["fake", "local"] = "fake"


@router.post("/comfyops/install-plans/{identity}/execute")
def execute_install(identity: str, body: Execute, actor: Operator, db: DBSession):
    locked(db)
    row = get(db, InstallationPlan, identity)
    if body.executor != "fake":
        raise HTTPException(409, "LOCAL_EXECUTOR_UNAVAILABLE")
    if row.status != "APPROVED" or row.sha256 != body.plan_hash or graph_hash(row.payload) != row.sha256:
        raise HTTPException(409, "INSTALL_APPROVAL_REQUIRED_OR_PLAN_HASH_CHANGED")
    if row.payload.get("executor") != "FAKE_ONLY":
        raise HTTPException(409, "LOCAL_EXECUTOR_UNAVAILABLE")
    if datetime.fromisoformat(row.payload.get("execute_before", "2000-01-01")) <= utcnow():
        raise HTTPException(409, "INSTALL_APPROVAL_EXPIRED")
    for package in row.payload["packages"]:
        cls = ModelPackage if package["spec"]["kind"] == "model" else NodePackage
        installed = get(db, cls, package["id"])
        if installed.sha256 != package["hash"]:
            raise HTTPException(409, "PLAN_HASH_CHANGED")
        installed.status = "MOCK_AVAILABLE"
    attempt = save(
        db,
        InstallationAttempt,
        {
            "stages": ["STAGING", "INSTALLING", "VERIFYING", "MOCK_AVAILABLE"],
            "is_mock": True,
            "system_changes": False,
            "plan_hash": row.sha256,
        },
        "MOCK_VERIFIED",
        plan_id=row.id,
    )
    row.status = "MOCK_AVAILABLE"
    audit(db, actor, "INSTALL_FAKE_EXECUTED", row)
    db.commit()
    return public(attempt)


@router.get("/comfyops/install-attempts/{identity}")
def attempt(identity: str, db: DBSession):
    return public(get(db, InstallationAttempt, identity))


@router.get("/workflow-capabilities")
def workflows(db: DBSession):
    return {
        "categories": CATEGORIES,
        "revisions": [public(r) for r in db.scalars(select(WorkflowRevision))],
        "legacy_profiles": [
            {
                "id": p.id,
                "name": p.name,
                "graph_sha256": p.graph_sha256,
                "identity": p.model_identity,
                "verification": "PENDING_LOCAL_H3",
            }
            for p in db.scalars(select(WorkflowProfile))
        ],
    }


@router.post("/workflow-capabilities/drafts")
def workflow_draft(body: WorkflowDraft, db: DBSession):
    if body.category not in CATEGORIES:
        raise HTTPException(422, "UNKNOWN_CAPABILITY")
    row = save(db, WorkflowRevision, body.model_dump(), "DRAFT")
    audit(db, "comfyops", "WORKFLOW_DRAFT_CREATED", row)
    db.commit()
    return public(row)


class Template(Strict):
    template: str
    name: str


@router.post("/workflow-capabilities/assemble")
def assemble(body: Template, db: DBSession):
    return workflow_draft(assemble_template(body.template, body.name), db)


class Validation(Strict):
    object_info: dict
    node_versions: dict[str, str] = Field(default_factory=dict)


@router.post("/workflow-capabilities/{identity}/validate")
def validate(identity: str, body: Validation, db: DBSession):
    row = get(db, WorkflowRevision, identity)
    if row.status not in {"DRAFT", "STATIC_VALIDATED"}:
        raise HTTPException(409, "WORKFLOW_REVISION_FROZEN")
    errors = validate_workflow(WorkflowDraft.model_validate(row.payload), body.object_info, body.node_versions)
    if not errors:
        row.status = "STATIC_VALIDATED"
    audit(db, "comfyops", "WORKFLOW_STATIC_CHECKED", row, {"errors": errors, "source": "SUPPLIED_OBJECT_INFO"})
    db.commit()
    return {"revision": public(row), "errors": errors}


@router.post("/workflow-capabilities/{identity}/fake-execute")
def fake_workflow(identity: str, request: Request, db: DBSession):
    row = get(db, WorkflowRevision, identity)
    if row.status != "STATIC_VALIDATED":
        raise HTTPException(409, "WORKFLOW_NOT_VALIDATED")
    for identity in row.payload["model_ids"]:
        if get(db, ModelPackage, identity).status != "MOCK_AVAILABLE":
            raise HTTPException(409, "CAPABILITY_MISSING")
    from shine.comfyops.fake import FakeComfyExecution

    if not hasattr(request.app.state, "preparation_comfy_client"):
        request.app.state.preparation_comfy_client = FakeComfyExecution().client()
    client = request.app.state.preparation_comfy_client
    row.status = "SUBMIT_INTENT"
    audit(db, "comfyops", "WORKFLOW_FAKE_SUBMIT_INTENT", row)
    db.commit()
    try:
        result = client.queue_prompt(row.payload["graph"])
        prompt_id = result["prompt_id"]
        history = client.history(prompt_id).get(prompt_id, {})
        if not history.get("status", {}).get("completed"):
            raise ValueError("FAKE_EXECUTION_INCOMPLETE")
    except Exception as exc:
        row.status = "NEEDS_REVIEW"
        audit(db, "comfyops", "WORKFLOW_FAKE_EXECUTION_UNCERTAIN", row)
        db.commit()
        raise HTTPException(409, "FAKE_EXECUTION_NEEDS_RECONCILIATION") from exc
    row.status = "FAKE_EXECUTED"
    audit(
        db,
        "comfyops",
        "WORKFLOW_FAKE_CONTRACT_EXECUTED",
        row,
        {"is_mock": True, "limitations": "Static graph contract only; no GPU execution"},
    )
    db.commit()
    return public(row)


@router.post("/workflow-capabilities/{identity}/publish")
def publish(identity: str, body: Decision, actor: Operator, db: DBSession):
    row = get(db, WorkflowRevision, identity)
    if row.status != "REAL_TESTED":
        raise HTTPException(409, "MODEL_NOT_VERIFIED_PENDING_LOCAL_GPU")
    approval.consume(db, actor, "workflow_publish", row, body.grant_id, body.snapshot)
    row.status = "PRODUCTION_APPROVED"
    audit(db, actor, "WORKFLOW_PUBLISHED", row)
    db.commit()
    return public(row)


class Match(Strict):
    category: str
    offline: bool = True


@router.post("/workflow-capabilities/match")
def match(body: Match, db: DBSession):
    state = "FAKE_EXECUTED" if body.offline else "PRODUCTION_APPROVED"
    rows = [
        public(r)
        for r in db.scalars(select(WorkflowRevision).where(WorkflowRevision.status == state))
        if r.payload["category"] == body.category
    ]
    return {"matches": rows, "blockers": [] if rows else ["CAPABILITY_MISSING"], "is_mock": body.offline}


class Rights(Strict):
    project_id: str
    scope: Literal["asset", "voice_clone", "voice_design"]
    license_id: str = Field(min_length=1, max_length=200)
    evidence: str = Field(min_length=5, max_length=2000)
    purpose: str = Field(min_length=1, max_length=500)
    reference_candidate_id: str | None = None


@router.post("/preparation/rights")
def rights(body: Rights, db: DBSession):
    require_project(db, body.project_id)
    if body.reference_candidate_id:
        ref = get(db, CandidateAsset, body.reference_candidate_id)
        if ref.project_id != body.project_id:
            raise HTTPException(409, "CROSS_PROJECT_REFERENCE")
    row = save(db, RightsRecord, body.model_dump(), "PENDING", project_id=body.project_id)
    db.commit()
    return public(row)


@router.post("/preparation/rights/{identity}/approve")
def approve_rights(identity: str, body: Decision, actor: Operator, db: DBSession):
    locked(db)
    row = get(db, RightsRecord, identity)
    approval.consume(db, actor, "rights_approve", row, body.grant_id, body.snapshot)
    if row.status != "PENDING" or row.payload["license_id"].lower() in {"unknown", "unverified"}:
        raise HTTPException(409, "LICENSE_NOT_APPROVED")
    row.status = "APPROVED"
    audit(db, actor, "RIGHTS_APPROVED", row)
    db.commit()
    return public(row)


@router.post("/preparation/rights/{identity}/revoke")
def revoke_rights(identity: str, actor: Operator, db: DBSession):
    row = get(db, RightsRecord, identity)
    row.status = "REVOKED"
    audit(db, actor, "RIGHTS_REVOKED", row)
    db.commit()
    return public(row)


@router.get("/projects/{project_id}/candidate-assets")
def candidates(project_id: str, db: DBSession):
    require_project(db, project_id)
    return [public(r) for r in db.scalars(select(CandidateAsset).where(CandidateAsset.project_id == project_id))]


class Generate(Strict):
    family: str
    entity_id: str = Field(min_length=1, max_length=80)
    rights_id: str
    prompt: str = Field(min_length=1, max_length=4000)
    seed: int = 1
    workflow_id: str
    parent_id: str | None = None


@router.post("/projects/{project_id}/candidate-assets/generate")
def generate(project_id: str, body: Generate, request: Request, settings: SettingsDep, db: DBSession):
    project = require_project(db, project_id)
    require_rights(db, project_id, body.rights_id)
    wf = get(db, WorkflowRevision, body.workflow_id)
    if wf.status != "FAKE_EXECUTED":
        raise HTTPException(409, "CAPABILITY_MISSING")
    expected = {
        "character": {"IMAGE_GENERATION", "CHARACTER_SHEET"},
        "costume": {"IMAGE_GENERATION", "OUTFIT_DESIGN"},
        "scene": {"IMAGE_GENERATION", "SCENE_CONCEPT"},
        "prop": {"IMAGE_GENERATION", "PROP_DESIGN"},
        "storyboard": {"IMAGE_GENERATION", "STORYBOARD"},
        "keyframe": {"IMAGE_GENERATION", "STORYBOARD"},
    }
    if body.family not in expected or wf.payload["category"] not in expected[body.family]:
        raise HTTPException(409, "IMPORT_ONLY_OR_CAPABILITY_MISSING")
    with preparation_task(db, project, "asset_factory", body.model_dump(), "fake-image-v1") as task:
        with resource_slot(request.app.state.session_factory, "IMAGE", 12000):
            content = fake_image(f"{body.seed}:{body.entity_id}:{body.family}:{body.prompt}")
        row = create_candidate(
            db,
            settings,
            project,
            body.family,
            body.entity_id,
            content,
            "fake.png",
            body.rights_id,
            {
                "is_mock": True,
                "provider": "FakeImageProvider",
                "workflow_id": wf.id,
                "workflow_hash": wf.sha256,
                "seed": body.seed,
                "prompt": body.prompt,
                "source_task_id": task.id,
            },
            body.parent_id,
        )
    db.commit()
    return public(row)


@router.post("/projects/{project_id}/candidate-assets/import")
async def import_candidate(
    project_id: str,
    settings: SettingsDep,
    db: DBSession,
    family: Annotated[str, Form()],
    entity_id: Annotated[str, Form()],
    rights_id: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
):
    project = require_project(db, project_id)
    require_rights(db, project_id, rights_id)
    content = await file.read(settings.max_upload_bytes + 1)
    row = create_candidate(
        db,
        settings,
        project,
        family,
        entity_id,
        content,
        file.filename,
        rights_id,
        {"is_mock": False, "source": "USER_IMPORT"},
    )
    db.commit()
    return public(row)


@router.post("/candidate-assets/{identity}/qc")
def qc(identity: str, settings: SettingsDep, db: DBSession):
    row = get(db, CandidateAsset, identity)
    result = candidate_qc(settings, row)
    audit(db, "candidate_qc", "CANDIDATE_QC_RECORDED", row, result)
    db.commit()
    return public(row)


@router.get("/candidate-assets/{identity}/media")
def candidate_media(identity: str, settings: SettingsDep, db: DBSession):
    row = get(db, CandidateAsset, identity)
    verify_candidate(settings, row)
    from shine.media.artifacts import safe_asset_path

    return FileResponse(
        safe_asset_path(settings.data_dir, row.payload["storage_path"]),
        media_type=row.payload["content_type"],
        headers={"Cache-Control": "no-store"},
    )


@router.post("/candidate-assets/{identity}/promote")
def promote_candidate(identity: str, body: Decision, actor: Operator, settings: SettingsDep, db: DBSession):
    locked(db)
    row = get(db, CandidateAsset, identity)
    approval.consume(db, actor, "promote", row, body.grant_id, body.snapshot)
    promote(db, settings, row, actor, offline=body.offline)
    db.commit()
    return public(row)


class VoiceDesign(Strict):
    character: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=2000)
    text: str = Field(default="这是我的声音。", min_length=1, max_length=100)
    rights_id: str
    asset_rights_id: str
    source: Literal["ORIGINAL_DESIGN", "AUTHORIZED_CLONE", "USER_IMPORT"] = "ORIGINAL_DESIGN"
    reference_id: str | None = None
    supersedes_voice_id: str | None = None


def voice_provider(request):
    if not hasattr(request.app.state, "voice_provider"):
        request.app.state.voice_provider = (
            LocalVoiceHTTPProvider(request.app.state.settings.local_voice_url)
            if request.app.state.settings.local_voice_url
            else FakeVoiceProvider()
        )
    return request.app.state.voice_provider


@router.get("/projects/{project_id}/voice-profiles")
def voices(project_id: str, db: DBSession):
    return [public(r) for r in db.scalars(select(VoiceProfile).where(VoiceProfile.project_id == project_id))]


@router.post("/projects/{project_id}/voice-profiles/design")
def voice_design(project_id: str, body: VoiceDesign, request: Request, settings: SettingsDep, db: DBSession):
    project = require_project(db, project_id)
    version = 1
    if body.supersedes_voice_id:
        previous = get(db, VoiceProfile, body.supersedes_voice_id)
        if previous.project_id != project_id or previous.payload["character"] != body.character:
            raise HTTPException(409, "CROSS_PROJECT_OR_CHARACTER_REFERENCE")
        version = previous.payload["version"] + 1
    scope = "voice_clone" if body.source == "AUTHORIZED_CLONE" else "voice_design"
    rights = require_rights(db, project_id, body.rights_id, scope)
    provider = voice_provider(request)
    if body.source != "ORIGINAL_DESIGN":
        reference = get(db, CandidateAsset, body.reference_id)
        if reference.project_id != project_id or reference.payload["kind"] != "audio":
            raise HTTPException(409, "CROSS_PROJECT_OR_NON_AUDIO_REFERENCE")
        if scope == "voice_clone" and rights.payload.get("reference_candidate_id") != reference.id:
            raise HTTPException(409, "VOICE_CONSENT_REQUIRED")
        audio = verify_candidate(settings, reference)
        result = (
            provider.clone(audio, body.text, body.character)
            if scope == "voice_clone"
            else {"audio": audio, "is_mock": reference.payload["provenance"].get("is_mock", False)}
        )
    else:
        with preparation_task(
            db,
            project,
            "voice_design",
            body.model_dump(),
            "fake-voice-v1" if provider.is_mock else "local-voice-service",
        ):
            with resource_slot(request.app.state.session_factory, "TTS", 4000):
                result = provider.design(body.description, body.text, body.character)
    candidate = create_candidate(
        db,
        settings,
        project,
        "voice",
        body.character,
        result["audio"],
        "voice.wav",
        body.asset_rights_id,
        {"is_mock": result["is_mock"], "source": body.source},
    )
    payload = {
        **body.model_dump(),
        "candidate_id": candidate.id,
        "conditioning_hash": candidate.payload["content_sha256"],
        "is_mock": result["is_mock"],
        "language": "zh-CN",
        "version": version,
        "style_id": project.frozen_style_version_id,
    }
    row = save(db, VoiceProfile, payload, "PREVIEW", project_id=project_id)
    audit(db, "voiceops", "VOICE_PREVIEW_CREATED", row)
    db.commit()
    return public(row)


@router.post("/voice-profiles/{identity}/approve-and-freeze")
def freeze_voice(identity: str, body: Decision, actor: Operator, settings: SettingsDep, db: DBSession):
    locked(db)
    row = get(db, VoiceProfile, identity)
    approval.consume(db, actor, "voice_freeze", row, body.grant_id, body.snapshot)
    if row.status != "PREVIEW":
        raise HTTPException(409, "VOICE_VERSION_FROZEN")
    candidate = get(db, CandidateAsset, row.payload["candidate_id"])
    verify_candidate(settings, candidate)
    if candidate.status != "PROMOTED":
        raise HTTPException(409, "CANDIDATE_NOT_APPROVED")
    require_rights(
        db,
        row.project_id,
        row.payload["rights_id"],
        "voice_clone" if row.payload["source"] == "AUTHORIZED_CLONE" else "voice_design",
    )
    if row.payload["is_mock"] and not body.offline:
        raise HTTPException(409, "MOCK_REQUIRES_OFFLINE_APPROVAL")
    row.status = "FROZEN"
    row.payload = {**row.payload, "approved_by": actor, "listening_evidence": body.evidence}
    row.sha256 = graph_hash(row.payload)
    audit(db, actor, "VOICE_FROZEN", row)
    db.commit()
    return public(row)


class Dialogue(Strict):
    character: str
    text: str = Field(min_length=1, max_length=1000)
    voice_hash: str
    emotion: str = Field(default="neutral", max_length=100)
    speed: float = Field(default=1, ge=0.5, le=2, allow_inf_nan=False)
    pause: float = Field(default=0, ge=0, le=3, allow_inf_nan=False)
    audio_mode: Literal["H3_NATIVE", "VOICE_REFERENCE", "POST_DUB"] = "VOICE_REFERENCE"


@router.post("/voice-profiles/{identity}/dialogue")
def dialogue(identity: str, body: Dialogue, request: Request, settings: SettingsDep, db: DBSession):
    row = get(db, VoiceProfile, identity)
    if row.status != "FROZEN" or row.payload["character"] != body.character or row.sha256 != body.voice_hash:
        raise HTTPException(409, "VOICE_NOT_FROZEN_OR_VERSION_MISMATCH")
    require_rights(
        db,
        row.project_id,
        row.payload["rights_id"],
        "voice_clone" if row.payload["source"] == "AUTHORIZED_CLONE" else "voice_design",
    )
    provider = voice_provider(request)
    project = require_project(db, row.project_id)
    with preparation_task(
        db, project, "voice_dialogue", body.model_dump(), "fake-voice-v1" if provider.is_mock else "local-voice-service"
    ):
        with resource_slot(request.app.state.session_factory, "TTS", 4000):
            result = provider.synthesize(
                body.text, body.character, row.payload["conditioning_hash"], body.emotion, body.speed, body.pause
            )
            transcript = provider.transcribe(result["audio"])
            words = provider.align(result["audio"], body.text)
            errors = verify_dialogue(result, transcript, words, body.text, body.character)
    project = require_project(db, row.project_id)
    candidate = create_candidate(
        db,
        settings,
        project,
        "dialogue",
        body.character,
        result["audio"],
        "dialogue.wav",
        get(db, CandidateAsset, row.payload["candidate_id"]).payload["rights_id"],
        {"is_mock": result["is_mock"], "voice_id": row.id, "voice_hash": row.sha256},
    )
    candidate_qc(settings, candidate)
    render = save(
        db,
        VoiceRender,
        {
            **body.model_dump(),
            "candidate_id": candidate.id,
            "transcript": transcript,
            "words": words,
            "subtitles": subtitles(words),
            "errors": errors,
            "is_mock": result["is_mock"],
            "lip_sync": "PENDING_LOCAL_GPU",
        },
        "QC_FAILED" if errors else "MOCK_VERIFIED" if result["is_mock"] else "PENDING_HUMAN_REVIEW",
        project_id=row.project_id,
        voice_id=row.id,
    )
    audit(db, "voiceops", "VOICE_RENDER_RECORDED", render)
    db.commit()
    return public(render)


class RequirementInput(Strict):
    family: Literal[
        "character",
        "costume",
        "scene",
        "prop",
        "keyframe",
        "storyboard",
        "action",
        "voice",
        "dialogue",
        "sfx",
        "music",
        "subtitle",
    ]
    entity_id: str = Field(min_length=1, max_length=80)
    critical: bool = True


class Analyze(Strict):
    document_id: str
    requirements: list[RequirementInput] = Field(default_factory=list, max_length=300)


@router.post("/projects/{project_id}/preproduction/analyze")
def analyze(project_id: str, body: Analyze, db: DBSession):
    locked(db)
    project = require_project(db, project_id)
    document = get(db, ProjectDocument, body.document_id)
    existing = preproduction.current_plan(db, project_id)
    if existing and existing.payload["script_hash"] == document.sha256 and not body.requirements:
        return public(existing)
    row = preproduction.analyze(db, project, document, [r.model_dump() for r in body.requirements])
    audit(db, "producer", "PREPRODUCTION_ANALYZED", row)
    db.commit()
    return public(row)


class Readiness(Strict):
    offline: bool = True


@router.post("/projects/{project_id}/preproduction/readiness")
def readiness(project_id: str, body: Readiness, settings: SettingsDep, db: DBSession):
    return preproduction.readiness(db, project_id, offline=body.offline, data_dir=settings.data_dir)


@router.get("/projects/{project_id}/preproduction/requirements")
def requirements(project_id: str, settings: SettingsDep, db: DBSession):
    return preproduction.readiness(db, project_id, data_dir=settings.data_dir)


@router.post("/projects/{project_id}/preproduction/freeze")
def freeze_manifest(project_id: str, body: Decision, actor: Operator, settings: SettingsDep, db: DBSession):
    locked(db)
    row = preproduction.current_plan(db, project_id)
    if not row:
        raise HTTPException(409, "PREPRODUCTION_NOT_ANALYZED")
    approval.consume(db, actor, "manifest_freeze", row, body.grant_id, body.snapshot)
    report = preproduction.readiness(db, project_id, offline=body.offline, data_dir=settings.data_dir)
    if report["status"] != "SHOOT_READY":
        raise HTTPException(409, {"code": "PREPRODUCTION_NOT_READY", "blockers": report["blockers"]})
    row.payload = {**row.payload, "manifest": report["manifest"], "offline": body.offline, "approved_by": actor}
    row.sha256 = graph_hash(row.payload)
    row.status = "FROZEN"
    audit(db, actor, "PREPRODUCTION_FROZEN", row)
    db.commit()
    return public(row)


@router.get("/comfyops/resource-status")
def resources(db: DBSession):
    return {
        "capacity_mb": 16384,
        "measurement": "ESTIMATED_NOT_MEASURED",
        "leases": [public(r) for r in db.scalars(select(ResourceLease))],
    }


class ResourceRequest(Strict):
    owner: str
    budget_mb: int = Field(gt=0)
    kind: Literal["H3", "IMAGE", "TTS", "ASR", "VLM"]


@router.post("/comfyops/resource-reservations")
def reserve(body: ResourceRequest, actor: Operator, db: DBSession):
    row = ResourceScheduler().enqueue(
        db, body.owner, body.budget_mb, body.kind == "H3", 100 if body.kind == "H3" else 10
    )
    db.commit()
    identity = row.id
    ResourceScheduler().admit(db, identity)
    db.expire_all()
    return public(get(db, ResourceLease, identity))


class LeaseAction(Strict):
    owner: str
    reconciled: bool = False


@router.post("/comfyops/resource-reservations/{identity}/heartbeat")
def resource_heartbeat(identity: str, body: LeaseAction, actor: Operator, db: DBSession):
    ResourceScheduler().heartbeat(db, identity, body.owner)
    db.commit()
    return public(get(db, ResourceLease, identity))


@router.post("/comfyops/resource-reservations/{identity}/release")
def resource_release(identity: str, body: LeaseAction, actor: Operator, db: DBSession):
    ResourceScheduler().release(db, identity, body.owner, reconciled=body.reconciled)
    db.commit()
    return public(get(db, ResourceLease, identity))


@router.post("/projects/{project_id}/voice-profiles/clone")
def clone_voice(
    project_id: str, body: VoiceDesign, request: Request, actor: Operator, settings: SettingsDep, db: DBSession
):
    if body.source != "AUTHORIZED_CLONE":
        raise HTTPException(422, "AUTHORIZED_CLONE_SOURCE_REQUIRED")
    return voice_design(project_id, body, request, settings, db)


@router.post("/projects/{project_id}/voice-profiles/import")
def import_voice(project_id: str, body: VoiceDesign, request: Request, settings: SettingsDep, db: DBSession):
    if body.source != "USER_IMPORT":
        raise HTTPException(422, "USER_IMPORT_SOURCE_REQUIRED")
    return voice_design(project_id, body, request, settings, db)


@router.get("/projects/{project_id}/voice-renders")
def voice_renders(project_id: str, db: DBSession):
    require_project(db, project_id)
    return [public(r) for r in db.scalars(select(VoiceRender).where(VoiceRender.project_id == project_id))]
