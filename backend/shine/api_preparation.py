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
from shine.api_operator import Operator, operator
from shine.comfy.workflow import graph_hash
from shine.comfyops import approval, preproduction
from shine.comfyops.assets import (
    candidate_qc,
    create_candidate,
    promote,
    require_rights,
    verify_candidate,
)
from shine.comfyops.images import FakeImageProvider, LocalImageHTTPProvider
from shine.comfyops.installer import PackageSpec, check_url, safe_target
from shine.comfyops.inventory import FakeWindowsHostProbe, WindowsHostProbe
from shine.comfyops.model_calls import TrackedProvider
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
from shine.models import AgentTaskRecord, Asset, Project, ProjectDocument, WorkflowProfile, utcnow
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
    listened: bool = False
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


class RefreshInventory(Strict):
    mode: Literal["fake", "local"] = "fake"


@router.post("/comfyops/instances/discover")
@router.post("/comfyops/inventory/refresh")
def refresh(body: RefreshInventory, settings: SettingsDep, db: DBSession):
    if body.mode == "local":
        from shine.comfyops.scope import scoped_settings

        settings = scoped_settings(db, settings)
        from pathlib import Path

        if not settings.comfy_probe_roots or not settings.comfy_probe_ports:
            raise HTTPException(409, "LOCAL_PROBE_SCOPE_NOT_CONFIGURED")
        try:
            payload = WindowsHostProbe(
                [Path(p) for p in settings.comfy_probe_roots], list(settings.comfy_probe_ports)
            ).scan()
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
    else:
        payload = FakeWindowsHostProbe().scan()
    row = save(db, EnvironmentSnapshot, payload, "MOCK_VERIFIED" if payload["is_mock"] else "DISCOVERED")
    from uuid import NAMESPACE_URL, uuid5

    for instance in payload["instances"]:
        key = ("fake:" if payload.get("is_mock") else "local:") + instance["url"].rstrip("/")
        identity = str(uuid5(NAMESPACE_URL, "shine-comfy-instance:" + key))
        current = db.get(ComfyInstance, identity)
        details = {**instance, "identity_key": key, "snapshot_id": row.id}
        if current:
            if current.payload.get("managed"):
                merged = {**current.payload, "scan_evidence": instance, "snapshot_id": row.id}
                current.payload, current.sha256 = merged, graph_hash(merged)
            else:
                current.payload, current.sha256, current.status = details, graph_hash(details), instance["health"]
        else:
            save(db, ComfyInstance, details, instance["health"], id=identity)
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
    action: Literal[
        "asset_approve",
        "asset_lock",
        "install",
        "promote",
        "voice_execute",
        "scope_configure",
        "model_reconcile",
        "instance_reconcile",
        "resource_reconcile",
        "voice_freeze",
        "manifest_freeze",
        "workflow_publish",
        "workflow_deprecate",
        "rights_approve",
        "workflow_validate",
        "workflow_preview",
        "subtitle_correct",
        "style_freeze",
    ]
    target_id: str
    context: dict | None = None


TARGETS = {
    "asset_approve": Asset,
    "asset_lock": Asset,
    "install": InstallationPlan,
    "promote": CandidateAsset,
    "voice_freeze": VoiceProfile,
    "voice_execute": RightsRecord,
    "scope_configure": Project,
    "model_reconcile": AgentTaskRecord,
    "instance_reconcile": InstallationAttempt,
    "resource_reconcile": ResourceLease,
    "manifest_freeze": PreProductionPlan,
    "workflow_publish": WorkflowRevision,
    "workflow_deprecate": WorkflowRevision,
    "rights_approve": RightsRecord,
    "workflow_validate": WorkflowRevision,
    "workflow_preview": WorkflowRevision,
    "subtitle_correct": VoiceRender,
    "style_freeze": Project,
}


@router.post("/preparation/approval-grants")
def grant(body: GrantRequest, actor: Operator, db: DBSession):
    if (body.action in {"voice_execute", "scope_configure"}) != (body.context is not None):
        raise HTTPException(422, "EXECUTION_CONTEXT_REQUIRED_ONLY_FOR_VOICE")
    return approval.issue(db, actor, body.action, get(db, TARGETS[body.action], body.target_id), context=body.context)


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
def execute_install(
    identity: str, body: Execute, actor: Operator, request: Request, settings: SettingsDep, db: DBSession
):
    locked(db)
    row = get(db, InstallationPlan, identity)
    if row.payload.get("kind") in {"INSTANCE_LIFECYCLE", "INSTANCE_BACKUP"}:
        raise HTTPException(409, "USE_INSTANCE_LIFECYCLE_EXECUTOR")
    if body.executor == "local":
        import os

        from shine.comfyops.installer import ControlledDownloader, HTTPTransport, LocalInstaller
        from shine.comfyops.local import execute_approved

        if os.name != "nt" or os.getenv("SHINE_ENABLE_LOCAL_EXECUTION") != "1":
            raise HTTPException(409, "LOCAL_EXECUTOR_UNAVAILABLE")
        db.rollback()
        installer = LocalInstaller(
            settings.data_dir / "comfyops" / "isolated-packages",
            ControlledDownloader({"huggingface.co", "raw.githubusercontent.com", "github.com"}, HTTPTransport()),
        )
        try:
            attempt_id = execute_approved(
                request.app.state.session_factory, settings, identity, body.plan_hash, actor, installer
            )
        except (ValueError, OSError) as exc:
            raise HTTPException(409, str(exc)) from exc
        return public(get(db, InstallationAttempt, attempt_id))
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
    dependencies = {}
    for identity in body.model_ids + body.node_package_ids:
        package = db.get(ModelPackage, identity) or db.get(NodePackage, identity)
        if not package:
            raise HTTPException(409, "DEPENDENCY_NOT_REGISTERED")
        dependencies[identity] = package.sha256
    if body.package_hashes and body.package_hashes != dependencies:
        raise HTTPException(409, "DEPENDENCY_SNAPSHOT_CHANGED")
    body = body.model_copy(update={"package_hashes": dependencies})
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
    for identity in row.payload["model_ids"] + row.payload.get("node_package_ids", []):
        package = db.get(ModelPackage, identity) or db.get(NodePackage, identity)
        if not package or package.sha256 != row.payload.get("package_hashes", {}).get(identity):
            errors.append({"code": "DEPENDENCY_SNAPSHOT_CHANGED", "package": identity})
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
    for identity in row.payload["model_ids"] + row.payload.get("node_package_ids", []):
        package = db.get(ModelPackage, identity) or db.get(NodePackage, identity)
        if (
            not package
            or package.sha256 != row.payload.get("package_hashes", {}).get(identity)
            or package.status != "MOCK_AVAILABLE"
        ):
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
    locked(db)
    row = get(db, WorkflowRevision, identity)
    if row.status != "REAL_TESTED":
        raise HTTPException(409, "MODEL_NOT_VERIFIED_PENDING_LOCAL_GPU")
    for identity in row.payload["model_ids"] + row.payload.get("node_package_ids", []):
        package = db.get(ModelPackage, identity) or db.get(NodePackage, identity)
        if not package or package.sha256 != row.payload.get("package_hashes", {}).get(identity):
            raise HTTPException(409, "DEPENDENCY_SNAPSHOT_CHANGED")
        if not package or package.status not in {"AVAILABLE", "FILES_VERIFIED_PENDING_RUNTIME"}:
            raise HTTPException(409, "MODEL_NOT_VERIFIED")
        if package.payload["license_id"].lower() in {"unknown", "unverified"}:
            raise HTTPException(409, "LICENSE_NOT_APPROVED")
    approval.consume(db, actor, "workflow_publish", row, body.grant_id, body.snapshot)
    row.status = "PRODUCTION_APPROVED"
    audit(db, actor, "WORKFLOW_PUBLISHED", row)
    db.commit()
    return public(row)


class Match(Strict):
    category: str
    offline: bool = True
    generation_mode: str | None = None
    duration_s: float | None = Field(default=None, gt=0)
    aspect_ratio: str | None = None
    instance_id: str | None = None
    references: dict[str, int] = Field(default_factory=dict)
    package_revisions: dict[str, str] = Field(default_factory=dict)
    budget_mb: int | None = Field(default=None, gt=0)
    usage: Literal["research", "commercial"] = "research"
    continuity: str = "cut"


@router.post("/workflow-capabilities/match")
def match(body: Match, db: DBSession):
    from shine.comfyops.registry import match_constraints

    state = "FAKE_EXECUTED" if body.offline else "PRODUCTION_APPROVED"
    matches, rejected = [], []
    for row in db.scalars(select(WorkflowRevision).where(WorkflowRevision.status == state)):
        if row.payload["category"] != body.category:
            continue
        errors = match_constraints(row, body.model_dump(), db)
        if errors:
            rejected.append({"id": row.id, "reasons": errors})
        else:
            matches.append(public(row))
    return {
        "matches": matches,
        "rejected": rejected,
        "blockers": [] if matches else ["CAPABILITY_MISSING"],
        "is_mock": body.offline,
    }


@router.get("/workflow-capabilities/{identity}")
def workflow_version(identity: str, db: DBSession):
    return public(get(db, WorkflowRevision, identity))


@router.post("/workflow-capabilities/{identity}/deprecate")
def deprecate_workflow(identity: str, body: Decision, actor: Operator, db: DBSession):
    locked(db)
    row = get(db, WorkflowRevision, identity)
    approval.consume(db, actor, "workflow_deprecate", row, body.grant_id, body.snapshot)
    row.status = "DEPRECATED"
    audit(db, actor, "WORKFLOW_DEPRECATED", row, {"evidence": body.evidence})
    db.commit()
    return public(row)


class Rights(Strict):
    project_id: str
    scope: Literal["asset", "voice_clone", "voice_design"]
    license_id: str = Field(min_length=1, max_length=200)
    evidence: str = Field(min_length=5, max_length=2000)
    purpose: str = Field(min_length=1, max_length=500)
    reference_candidate_id: str | None = None
    expires_at: datetime | None = None


@router.post("/preparation/rights")
def rights(body: Rights, db: DBSession):
    require_project(db, body.project_id)
    if body.reference_candidate_id:
        ref = get(db, CandidateAsset, body.reference_candidate_id)
        if ref.project_id != body.project_id:
            raise HTTPException(409, "CROSS_PROJECT_REFERENCE")
    row = save(db, RightsRecord, body.model_dump(mode="json"), "PENDING", project_id=body.project_id)
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
    preview_decision: Decision | None = None
    execution_mode: Literal["fake", "local"] | None = None


@router.post("/projects/{project_id}/candidate-assets/generate")
def generate(project_id: str, body: Generate, request: Request, settings: SettingsDep, db: DBSession):
    project = require_project(db, project_id)
    require_rights(db, project_id, body.rights_id)
    wf = get(db, WorkflowRevision, body.workflow_id)
    provider = getattr(request.app.state, "image_provider", None)
    if provider is None:
        import os

        comfy_image = os.getenv("SHINE_IMAGE_COMFY_URL")
        if comfy_image:
            from shine.comfy.client import ComfyUIClient
            from shine.comfyops.images import ComfyImageProvider
            from shine.security import require_loopback_url

            provider = ComfyImageProvider(ComfyUIClient(require_loopback_url(comfy_image), max_retries=0))
        else:
            provider = (
                LocalImageHTTPProvider(settings.local_image_url) if settings.local_image_url else FakeImageProvider()
            )
    request.app.state.image_provider = provider
    check_provider_mode(provider, body.execution_mode)
    if not provider.is_mock and wf.status == "STATIC_VALIDATED" and body.preview_decision:
        actor = operator(request, settings, request.headers.get("authorization", ""))
        decision = body.preview_decision
        approval.consume(db, actor, "workflow_preview", wf, decision.grant_id, decision.snapshot)
        audit(db, actor, "WORKFLOW_LOCAL_PREVIEW_AUTHORIZED", wf)
        db.commit()
    elif wf.status != ("FAKE_EXECUTED" if provider.is_mock else "PRODUCTION_APPROVED"):
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
    provider = TrackedProvider(provider, request.app.state.session_factory, project_id)
    references = []
    if body.parent_id:
        parent = get(db, CandidateAsset, body.parent_id)
        if parent.project_id != project_id:
            raise HTTPException(409, "CROSS_PROJECT_REFERENCE")
        references.append(verify_candidate(settings, parent))
    with preparation_task(
        db, project, "asset_factory", body.model_dump(), "fake-image-v1" if provider.is_mock else "local-image-service"
    ) as task:
        with resource_slot(
            request.app.state.session_factory, "IMAGE", 12000, release_models=getattr(provider, "release_models", None)
        ):
            result = provider.generate(
                f"{body.family}:{body.entity_id}:{body.prompt}", body.seed, wf.payload, references
            )
            content = result["content"]
        row = create_candidate(
            db,
            settings,
            project,
            body.family,
            body.entity_id,
            content,
            result["filename"],
            body.rights_id,
            {
                "is_mock": provider.is_mock or result.get("is_mock", False),
                "provider": provider.name,
                "model": result.get("model", "UNVERIFIED_LOCAL_SERVICE"),
                "model_ids": wf.payload["model_ids"],
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
def qc(identity: str, request: Request, settings: SettingsDep, db: DBSession):
    row = get(db, CandidateAsset, identity)
    reviewer = getattr(request.app.state, "candidate_reviewer", None)
    if reviewer:
        reviewer = TrackedProvider(reviewer, request.app.state.session_factory, row.project_id)
    result = candidate_qc(settings, row, reviewer=reviewer)
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
    promote(db, settings, row, actor, body, offline=body.offline)
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
    execution_decision: Decision | None = None
    execution_mode: Literal["fake", "local"] | None = None


def check_provider_mode(provider, mode):
    if mode is not None and provider.is_mock != (mode == "fake"):
        raise HTTPException(409, "REQUESTED_MODE_EXECUTOR_MISMATCH")


def voice_provider(request):
    if not hasattr(request.app.state, "voice_provider"):
        import json
        import os

        from shine.voiceops.local_sdk import QwenLocalVoiceProvider

        configured = os.getenv("SHINE_VOICE_SDK_PACKAGES")
        if configured:
            request.app.state.voice_provider = QwenLocalVoiceProvider(
                request.app.state.session_factory, request.app.state.settings, json.loads(configured)
            )
            return request.app.state.voice_provider
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
    from shine.voiceops.authorization import authorize

    actor = (
        operator(request, settings, request.headers.get("authorization", ""))
        if body.source == "AUTHORIZED_CLONE"
        else None
    )
    reference = authorize(db, project_id, body, actor, settings)
    provider = TrackedProvider(voice_provider(request), request.app.state.session_factory, project_id)
    if body.source != "USER_IMPORT":
        check_provider_mode(provider, body.execution_mode)
    if body.source == "USER_IMPORT":
        result = {
            "audio": verify_candidate(settings, reference),
            "is_mock": reference.payload["provenance"].get("is_mock", False),
        }
    else:
        with preparation_task(
            db,
            project,
            "voice_" + body.source.lower(),
            body.model_dump(exclude={"execution_decision"}),
            "fake-voice-v1" if provider.is_mock else "local-voice-service",
        ):
            with resource_slot(
                request.app.state.session_factory, "TTS", 4000, release_models=getattr(provider, "release_models", None)
            ):
                # Recheck consent immediately before model execution, after scheduling.
                require_rights(
                    db,
                    project_id,
                    body.rights_id,
                    "voice_clone" if body.source == "AUTHORIZED_CLONE" else "voice_design",
                )
                result = (
                    provider.clone(verify_candidate(settings, reference), body.text, body.character)
                    if reference
                    else provider.design(body.description, body.text, body.character)
                )
    candidate = create_candidate(
        db,
        settings,
        project,
        "voice",
        body.character,
        result["audio"],
        "voice.wav",
        body.asset_rights_id,
        {
            "is_mock": result["is_mock"],
            "source": body.source,
            "model": result.get("model", "USER_IMPORT"),
            "provider": type(provider).__name__,
        },
    )
    payload = {
        **body.model_dump(exclude={"execution_decision"}),
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
    if not body.listened:
        raise HTTPException(409, "VOICE_LISTENING_CONFIRMATION_REQUIRED")
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
    execution_decision: Decision | None = None
    execution_mode: Literal["fake", "local"] | None = None


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
    if row.payload["source"] == "AUTHORIZED_CLONE":
        actor = operator(request, settings, request.headers.get("authorization", ""))
        if not body.execution_decision:
            raise HTTPException(409, "VOICE_EXECUTION_GRANT_REQUIRED")
        consent = get(db, RightsRecord, row.payload["rights_id"])
        decision = body.execution_decision
        approval.consume(
            db,
            actor,
            "voice_execute",
            consent,
            decision.grant_id,
            decision.snapshot,
            context={"voice_id": row.id, **body.model_dump(exclude={"execution_decision"})},
        )
    provider = TrackedProvider(voice_provider(request), request.app.state.session_factory, row.project_id)
    check_provider_mode(provider, body.execution_mode)
    project = require_project(db, row.project_id)
    with preparation_task(
        db,
        project,
        "voice_dialogue",
        body.model_dump(exclude={"execution_decision"}),
        "fake-voice-v1" if provider.is_mock else "local-voice-service",
    ):
        with resource_slot(
            request.app.state.session_factory, "TTS", 4000, release_models=getattr(provider, "release_models", None)
        ):
            if hasattr(provider, "prepare_conditioning"):
                reference = get(db, CandidateAsset, row.payload["candidate_id"])
                provider.prepare_conditioning(
                    verify_candidate(settings, reference), row.payload["conditioning_hash"], row.payload["text"]
                )
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
    measured_duration = (candidate.payload.get("media") or {}).get("duration_s")
    if measured_duration is None or abs(measured_duration - result["duration_s"]) > 0.1:
        errors = sorted(set(errors + ["DIALOGUE_DURATION_MISMATCH"]))
    elif any(w["end_s"] > measured_duration + 0.001 for w in words):
        errors = sorted(set(errors + ["ALIGNMENT_TIME_INVALID"]))
    review_errors = [e for e in errors if e in {"ASR_REVIEW_REQUIRED", "VOICE_REVIEW_REQUIRED"}]
    hard_errors = [e for e in errors if e not in review_errors]
    if review_errors:
        candidate.payload = {**candidate.payload, "dialogue_review_errors": review_errors, "asr_evidence": transcript}
    candidate_qc(settings, candidate)
    if hard_errors:
        candidate.status = "QC_FAILED"
        candidate.payload = {**candidate.payload, "dialogue_errors": hard_errors}
        candidate.sha256 = graph_hash(candidate.payload)
    render = save(
        db,
        VoiceRender,
        {
            **body.model_dump(exclude={"execution_decision"}),
            "candidate_id": candidate.id,
            "transcript": transcript,
            "words": words,
            "subtitles": subtitles(words),
            "errors": errors,
            "is_mock": result["is_mock"],
            "lip_sync": "PENDING_LOCAL_GPU",
        },
        "QC_FAILED"
        if hard_errors
        else "PENDING_HUMAN_REVIEW"
        if review_errors
        else "MOCK_VERIFIED"
        if result["is_mock"]
        else "PENDING_HUMAN_REVIEW",
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
    create_revision: bool = False
    h3_category: Literal["H3_REF2VA", "H3_T2VA", "H3_I2VA", "H3_L2VA", "H3_FL2VA"] = "H3_REF2VA"


@router.post("/projects/{project_id}/preproduction/analyze")
def analyze(project_id: str, body: Analyze, db: DBSession):
    locked(db)
    project = require_project(db, project_id)
    document = get(db, ProjectDocument, body.document_id)
    if document.project_id != project_id:
        raise HTTPException(409, "CROSS_PROJECT_REFERENCE")
    existing = preproduction.current_plan(db, project_id)
    if (
        existing
        and existing.payload["script_hash"] == document.sha256
        and not body.requirements
        and not body.create_revision
        and existing.payload.get("h3_category", "H3_REF2VA") == body.h3_category
    ):
        return public(existing)
    row = preproduction.analyze(db, project, document, [r.model_dump() for r in body.requirements], body.h3_category)
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
    if row.status == "PREPARING":
        raise HTTPException(409, "PREPARATION_RUNNING")
    if row.status == "FROZEN":
        raise HTTPException(409, "MANIFEST_ALREADY_FROZEN_CREATE_REVISION")
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
    proof_decision: Decision | None = None


@router.post("/comfyops/resource-reservations/{identity}/heartbeat")
def resource_heartbeat(identity: str, body: LeaseAction, actor: Operator, db: DBSession):
    ResourceScheduler().heartbeat(db, identity, body.owner)
    db.commit()
    return public(get(db, ResourceLease, identity))


@router.post("/comfyops/resource-reservations/{identity}/release")
def resource_release(
    identity: str, body: LeaseAction, actor: Operator, request: Request, settings: SettingsDep, db: DBSession
):
    locked(db)
    row = get(db, ResourceLease, identity)
    if row.owner != body.owner:
        raise HTTPException(403, "RESOURCE_OWNER_MISMATCH")
    tasks = db.scalars(
        select(AgentTaskRecord).where(AgentTaskRecord.trace["resource_id"].as_string() == identity)
    ).all()
    from shine.models import Job

    job = db.get(Job, row.owner.removeprefix("h3:")) if row.owner.startswith("h3:") else None
    attached = bool(tasks or job)
    if (
        job
        and row.status != "RELEASED"
        and (job.lease_owner or job.lease_expires_at or job.status not in {"PASSED", "FAILED", "NEEDS_REVIEW"})
    ):
        raise HTTPException(409, "H3_WORKER_MUST_RECONCILE_BEFORE_RESOURCE_RELEASE")
    if attached and row.status != "RELEASED":
        if any(t.status in {"RUNNING", "NEEDS_REVIEW", "RECONCILING"} for t in tasks):
            raise HTTPException(409, "MODEL_CALL_RECONCILIATION_REQUIRED")
        if not body.proof_decision:
            raise HTTPException(409, "MODEL_RELEASE_PROOF_REQUIRED")
        decision = body.proof_decision
        approval.consume(db, actor, "resource_reconcile", row, decision.grant_id, decision.snapshot)
        if job:
            from shine.comfy.client import ComfyUIClient

            ComfyUIClient(settings.comfyui_base_url, max_retries=0).free_models()
        elif any(not t.trace.get("is_mock") for t in tasks):
            raise HTTPException(409, "USE_MODEL_CALL_RECONCILIATION_WITH_PROVIDER_PROOF")
        ResourceScheduler().release(db, identity, body.owner, reconciled=True)
        add_audit(
            db,
            actor,
            "RESOURCE_RELEASE_RECONCILED",
            "resource_lease",
            identity,
            {"evidence": decision.evidence, "physical_release_confirmed": bool(job), "is_mock": not bool(job)},
        )
    else:
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


class LocalEvidence(Decision):
    job_id: str | None = None
    candidate_id: str | None = None
    graph_sha256: str


@router.post("/workflow-capabilities/{identity}/local-evidence")
def local_evidence(identity: str, body: LocalEvidence, actor: Operator, settings: SettingsDep, db: DBSession):
    """Only current, manually reviewed real H3 evidence can unlock production publication.

    Other local capability types remain pending independent provider acceptance;
    a Fake execution or a user-supplied boolean cannot upgrade the verification level.
    """
    import hashlib

    from shine.media.artifacts import safe_asset_path
    from shine.models import Job, JobAttempt, MediaArtifact, QualityGate, Shot

    locked(db)
    row = get(db, WorkflowRevision, identity)
    approval.consume(db, actor, "workflow_validate", row, body.grant_id, body.snapshot)
    if body.candidate_id:
        candidate = get(db, CandidateAsset, body.candidate_id)
        proof = candidate.payload["provenance"]
        if (
            body.offline
            or row.status != "STATIC_VALIDATED"
            or candidate.status != "PROMOTED"
            or proof.get("is_mock", True)
            or proof.get("workflow_id") != row.id
            or proof.get("workflow_hash") != row.sha256
            or body.graph_sha256 != graph_hash(row.payload["graph"])
        ):
            raise HTTPException(409, "REAL_LOCAL_WORKFLOW_CANDIDATE_REQUIRED")
        verify_candidate(settings, candidate)
        row.status = "REAL_TESTED"
        audit(
            db,
            actor,
            "WORKFLOW_REAL_CANDIDATE_EVIDENCE_BOUND",
            row,
            {
                "candidate_id": candidate.id,
                "content_sha256": candidate.payload["content_sha256"],
                "evidence": body.evidence,
            },
        )
        db.commit()
        return public(row)
    job = get(db, Job, body.job_id)
    attempt = db.scalar(
        select(JobAttempt).where(JobAttempt.job_id == job.id).order_by(JobAttempt.attempt_no.desc()).limit(1)
    )
    artifact = db.get(MediaArtifact, job.artifact_id) if job.artifact_id else None
    profile = db.get(WorkflowProfile, job.workflow_profile_id) if job.workflow_profile_id else None
    shot = db.get(Shot, job.shot_id)
    expected_graph = graph_hash(row.payload["graph"])
    if (
        body.offline
        or row.status not in {"STATIC_VALIDATED", "FAKE_EXECUTED"}
        or job.status != "PASSED"
        or job.stale
        or job.provider != "comfyui"
        or job.quality_verification != "manual_verified"
        or not attempt
        or not artifact
        or artifact.is_mock
        or artifact.attempt_id != attempt.id
        or not profile
        or not shot
        or shot.revision != job.shot_revision
        or not row.payload["category"].startswith("H3_")
        or body.graph_sha256 != expected_graph
        or profile.graph_sha256 != expected_graph
        or profile.model_identity.get("family", "MiniMax-H3") != "MiniMax-H3"
    ):
        raise HTTPException(409, "CURRENT_REAL_MANUAL_H3_EVIDENCE_REQUIRED")
    path = safe_asset_path(settings.data_dir, artifact.storage_path)
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != artifact.sha256:
        raise HTTPException(409, "ARTIFACT_HASH_CHANGED")
    gates = db.scalars(
        select(QualityGate).where(QualityGate.attempt_id == attempt.id, QualityGate.artifact_id == artifact.id)
    ).all()
    for name in ("technical", "semantic", "audio", "style", "final"):
        latest = [g for g in gates if g.gate_name == name]
        latest.sort(key=lambda g: g.created_at)
        if not latest or latest[-1].is_mock or latest[-1].status not in {"passed", "not_applicable"}:
            raise HTTPException(409, "REAL_QUALITY_GATES_REQUIRED")
    row.status = "REAL_TESTED"
    audit(
        db,
        actor,
        "WORKFLOW_REAL_EVIDENCE_BOUND",
        row,
        {
            "job_id": job.id,
            "attempt_id": attempt.id,
            "artifact_sha256": artifact.sha256,
            "graph_sha256": expected_graph,
            "evidence": body.evidence,
        },
    )
    db.commit()
    return public(row)


@router.get("/voice-renders/{identity}/subtitles")
def export_subtitles(identity: str, db: DBSession, format: Literal["srt", "ass"] = "srt"):
    from fastapi.responses import Response

    row = get(db, VoiceRender, identity)
    return Response(
        row.payload["subtitles"][format],
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{row.id}.{format}"', "Cache-Control": "no-store"},
    )


@router.get("/projects/{project_id}/comfyops/gaps")
def capability_gaps(project_id: str, settings: SettingsDep, db: DBSession):
    report = preproduction.readiness(db, project_id, offline=True, data_dir=settings.data_dir)
    model_rows = list(db.scalars(select(ModelPackage)))
    node_rows = list(db.scalars(select(NodePackage)))
    return {
        "project_id": project_id,
        "readiness": report["status"],
        "asset_and_workflow_gaps": report["blockers"],
        "model_gaps": [public(m) for m in model_rows if m.status not in {"MOCK_AVAILABLE", "AVAILABLE"}],
        "node_gaps": [public(n) for n in node_rows if n.status not in {"MOCK_AVAILABLE", "AVAILABLE"}],
        "strategies": ["REUSE_REGISTERED_HASH", "PLAN_ISOLATED_INSTALL", "IMPORT_AUTHORIZED_ASSET"],
        "resource_measurement": "ESTIMATED_NOT_MEASURED",
        "is_mock": True,
    }


class SubtitleWord(Strict):
    text: str = Field(min_length=1, max_length=100)
    start_s: float = Field(ge=0, allow_inf_nan=False)
    end_s: float = Field(gt=0, allow_inf_nan=False)


class SubtitleCorrection(Decision):
    words: list[SubtitleWord] = Field(min_length=1, max_length=2000)


@router.post("/voice-renders/{identity}/correct-subtitles")
def correct_subtitles(identity: str, body: SubtitleCorrection, actor: Operator, db: DBSession):
    locked(db)
    row = get(db, VoiceRender, identity)
    approval.consume(db, actor, "subtitle_correct", row, body.grant_id, body.snapshot)
    words = [w.model_dump() for w in body.words]
    last = 0
    if "".join(w["text"] for w in words) != row.payload["text"]:
        raise HTTPException(409, "LOCKED_DIALOGUE_TEXT_MISMATCH")
    source = get(db, CandidateAsset, row.payload["candidate_id"])
    duration = (source.payload.get("media") or {}).get("duration_s")
    if duration is None:
        raise HTTPException(409, "AUDIO_TIMEBASE_UNAVAILABLE")
    for word in words:
        if not last <= word["start_s"] < word["end_s"] <= duration:
            raise HTTPException(409, "ALIGNMENT_TIME_INVALID")
        last = word["end_s"]
    revision = save(
        db,
        VoiceRender,
        {
            **row.payload,
            "parent_render_id": row.id,
            "words": words,
            "subtitles": subtitles(words),
            "corrected_by": actor,
            "evidence": body.evidence,
        },
        "MANUAL_SUBTITLE_CORRECTED",
        project_id=row.project_id,
        voice_id=row.voice_id,
    )
    audit(db, actor, "SUBTITLE_CORRECTION_RECORDED", revision)
    db.commit()
    return public(revision)


@router.get("/comfyops/model-references")
def model_references():
    import json
    from pathlib import Path

    return json.loads((Path(__file__).parent / "comfyops" / "reference_catalog.json").read_text(encoding="utf-8"))


class RunPreparation(Strict):
    asset_rights_id: str
    workflow_id: str
    voice_rights_id: str | None = None
    max_tasks: int = Field(default=20, ge=1, le=20)


@router.post("/projects/{project_id}/preproduction/run")
def run_preparation(project_id: str, body: RunPreparation, request: Request, settings: SettingsDep, db: DBSession):
    """Run bounded candidate tasks in dependency order; always stop at human promotion."""
    from sqlalchemy import update

    require_project(db, project_id)
    require_rights(db, project_id, body.asset_rights_id)
    plan = preproduction.current_plan(db, project_id)
    if not plan:
        raise HTTPException(409, "PREPRODUCTION_NOT_ANALYZED")
    claim = db.execute(
        update(PreProductionPlan)
        .where(PreProductionPlan.id == plan.id, PreProductionPlan.status == "ANALYZED")
        .values(status="PREPARING")
    )
    if claim.rowcount != 1:
        raise HTTPException(409, "PREPARATION_RUNNING_OR_FROZEN")
    db.commit()
    generated, blocked = [], []
    try:
        report = preproduction.readiness(db, project_id, data_dir=settings.data_dir)
        requirements = report["requirements"]
        satisfied = {r["id"] for r in requirements if r["status"] == "SATISFIED"}
        pending = list(db.scalars(select(CandidateAsset).where(CandidateAsset.project_id == project_id)))
        for req in requirements:
            if not req["critical"] or req["status"] == "SATISFIED":
                continue
            if len(generated) >= body.max_tasks:
                blocked.append({"requirement": req["id"], "code": "TASK_BATCH_LIMIT"})
                continue
            if not set(req["depends_on"]).issubset(satisfied):
                blocked.append({"requirement": req["id"], "code": "DEPENDENCY_APPROVAL_PENDING"})
                continue
            if any(
                c.payload["family"] == req["family"]
                and c.payload["entity_id"] == req["entity_id"]
                and c.status not in {"REJECTED", "QC_FAILED"}
                for c in pending
            ):
                blocked.append({"requirement": req["id"], "code": "CANDIDATE_APPROVAL_PENDING"})
                continue
            failed_versions = sum(
                c.payload["family"] == req["family"]
                and c.payload["entity_id"] == req["entity_id"]
                and c.status == "QC_FAILED"
                for c in pending
            )
            if failed_versions >= 2:
                blocked.append({"requirement": req["id"], "code": "CANDIDATE_RETRY_BUDGET_EXHAUSTED"})
                continue
            try:
                if req["family"] in {"character", "costume", "scene", "prop", "storyboard", "keyframe"}:
                    item = generate(
                        project_id,
                        Generate(
                            family=req["family"],
                            entity_id=req["entity_id"],
                            rights_id=body.asset_rights_id,
                            workflow_id=body.workflow_id,
                            prompt=f"Prepare {req['family']} for {req['entity_id']} from the locked script.",
                        ),
                        request,
                        settings,
                        db,
                    )
                    item = qc(item["id"], request, settings, db)
                    generated.append({"requirement": req["id"], "candidate_id": item["id"], "status": item["status"]})
                elif req["family"] == "voice" and body.voice_rights_id:
                    item = voice_design(
                        project_id,
                        VoiceDesign(
                            character=req["entity_id"],
                            description=f"Original Mandarin voice for {req['entity_id']}.",
                            text="这是用于确认角色普通话音色的原创试听样音。",
                            rights_id=body.voice_rights_id,
                            asset_rights_id=body.asset_rights_id,
                        ),
                        request,
                        settings,
                        db,
                    )
                    candidate_id = item["payload"]["candidate_id"]
                    qc(candidate_id, request, settings, db)
                    generated.append(
                        {
                            "requirement": req["id"],
                            "candidate_id": candidate_id,
                            "voice_id": item["id"],
                            "status": "PENDING_HUMAN_APPROVAL",
                        }
                    )
                else:
                    blocked.append({"requirement": req["id"], "code": "IMPORT_OR_RIGHTS_REQUIRED"})
            except HTTPException as exc:
                db.rollback()
                blocked.append({"requirement": req["id"], "code": str(exc.detail)})
            except Exception as exc:
                db.rollback()
                blocked.append(
                    {
                        "requirement": req["id"],
                        "code": "PROVIDER_RECONCILIATION_REQUIRED",
                        "error_type": type(exc).__name__,
                    }
                )
        add_audit(
            db,
            "producer",
            "PREPRODUCTION_CANDIDATE_BATCH",
            "preproduction_plan",
            plan.id,
            {"generated": generated, "blocked": blocked},
            project_id=project_id,
        )
        db.commit()
    finally:
        db.rollback()
        current = db.get(PreProductionPlan, plan.id)
        if current and current.status == "PREPARING":
            current.status = "ANALYZED"
            db.commit()
    return {
        "generated": generated,
        "blocked": blocked,
        "status": "PENDING_HUMAN_APPROVAL",
        "is_mock": not bool(settings.local_image_url or settings.local_voice_url),
    }


class LocalExecutionEvidence(Decision):
    instance_id: str
    prompt_id: str = Field(min_length=1, max_length=128)
    output_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


@router.post("/workflow-capabilities/{identity}/local-execution-evidence")
def local_execution_evidence(
    identity: str, body: LocalExecutionEvidence, actor: Operator, settings: SettingsDep, db: DBSession
):
    """Read an actual allowlisted ComfyUI history; never submit or infer on this route.

    Non-video capability execution evidence is separate from H3 film acceptance.
    H3 and voice cloning retain their stronger dedicated review/consent paths.
    """
    from shine.comfy.client import ComfyError, ComfyUIClient
    from shine.comfy.workflow import PATH_PATTERN, apply_parameters
    from shine.security import require_loopback_url

    row = get(db, WorkflowRevision, identity)
    instance = get(db, ComfyInstance, body.instance_id)
    if (
        body.offline
        or row.status not in {"STATIC_VALIDATED", "FAKE_EXECUTED"}
        or instance.status != "ONLINE"
        or instance.payload.get("is_mock", False)
        or row.payload["category"].startswith("H3_")
        or row.payload["category"] == "VOICE_CLONE"
    ):
        raise HTTPException(409, "REAL_NON_VIDEO_INSTANCE_EVIDENCE_REQUIRED")
    url = require_loopback_url(instance.payload["url"])
    try:
        client = ComfyUIClient(url, max_retries=0)
        stats = client.system_stats()
        import json

        if any(x in json.dumps(stats).lower() for x in ("fake", "mock")):
            raise HTTPException(409, "FAKE_INSTANCE_CANNOT_SUPPLY_REAL_EVIDENCE")
        history = client.history(body.prompt_id).get(body.prompt_id, {})
        if not history.get("status", {}).get("completed") or history.get("status", {}).get("status_str") != "success":
            raise HTTPException(409, "LOCAL_WORKFLOW_NOT_COMPLETED")
        if history["prompt"][1] != body.prompt_id or not history.get("outputs"):
            raise HTTPException(409, "LOCAL_WORKFLOW_OUTPUT_EVIDENCE_REQUIRED")
        actual = history["prompt"][2]
        values = {}
        for logical, path in row.payload["parameter_map"].items():
            match = PATH_PATTERN.fullmatch(path)
            if not match:
                raise ValueError("PARAMETER_MAP_INVALID")
            values[logical] = actual[match.group(1)]["inputs"][match.group(2)]
        expected = apply_parameters(row.payload["graph"], row.payload["parameter_map"], values)
        if graph_hash(expected) != graph_hash(actual) or graph_hash(history.get("outputs", {})) != body.output_sha256:
            raise HTTPException(409, "LOCAL_WORKFLOW_EVIDENCE_HASH_MISMATCH")
        errors = validate_workflow(
            WorkflowDraft.model_validate(row.payload), client.object_info(), instance.payload.get("node_versions", {})
        )
        if errors:
            raise HTTPException(409, {"code": "LOCAL_WORKFLOW_VALIDATION_FAILED", "errors": errors})
    except (ComfyError, KeyError, ValueError, IndexError) as exc:
        raise HTTPException(409, "LOCAL_EXECUTION_EVIDENCE_UNAVAILABLE") from exc
    db.rollback()  # Finish external read checks before taking the SQLite writer lock.
    locked(db)
    row = get(db, WorkflowRevision, identity)
    approval.consume(db, actor, "workflow_validate", row, body.grant_id, body.snapshot)
    row.status = "REAL_TESTED"
    audit(
        db,
        actor,
        "WORKFLOW_LOCAL_EXECUTION_EVIDENCE_BOUND",
        row,
        {
            "instance_id": instance.id,
            "prompt_id": body.prompt_id,
            "output_sha256": body.output_sha256,
            "graph_sha256": graph_hash(actual),
            "evidence": body.evidence,
        },
    )
    db.commit()
    return public(row)


class LocalValidation(Strict):
    instance_id: str


@router.post("/workflow-capabilities/{identity}/validate-local")
def validate_local_workflow(
    identity: str, body: LocalValidation, actor: Operator, settings: SettingsDep, db: DBSession
):
    from shine.comfy.client import ComfyUIClient
    from shine.security import require_loopback_url

    row = get(db, WorkflowRevision, identity)
    instance = get(db, ComfyInstance, body.instance_id)
    if instance.payload.get("is_mock") or instance.status not in {"ONLINE", "RUNNING", "STOPPED"}:
        raise HTTPException(409, "REAL_INSTANCE_REQUIRED")
    url = require_loopback_url(instance.payload["url"])
    if not instance.payload.get("managed"):
        from urllib.parse import urlparse

        if urlparse(url).port not in settings.comfy_probe_ports:
            raise HTTPException(403, "INSTANCE_OUTSIDE_APPROVED_SCOPE")
    info = ComfyUIClient(url, max_retries=0).object_info()
    versions = instance.payload.get("node_versions", {})
    result = validate(identity, Validation(object_info=info, node_versions=versions), db)
    audit(db, actor, "WORKFLOW_LOCAL_CONTRACT_CHECKED", row, {"instance": body.instance_id, "errors": result["errors"]})
    db.commit()
    return {**result, "source": "LOCAL_OBJECT_INFO", "instance_id": body.instance_id, "is_mock": False}


@router.get("/projects/{project_id}/preparation/model-calls")
def model_call_records(project_id: str, actor: Operator, db: DBSession):
    require_project(db, project_id)
    return [
        public(row)
        for row in db.scalars(
            select(AgentTaskRecord).where(
                AgentTaskRecord.project_id == project_id, AgentTaskRecord.role_id.like("model_call:%")
            )
        )
    ]


class ReleaseProviderProposal(Strict):
    provider_profile_id: str


@router.post("/preparation/model-calls/{identity}/release-provider-proposal")
def release_provider_proposal(identity: str, body: ReleaseProviderProposal, db: DBSession):
    """Pin a legacy reviewer's cleanup target for subsequent human approval only."""
    import hashlib

    from shine.models import ProviderProfile
    from shine.security import require_loopback_url

    locked(db)
    row = get(db, AgentTaskRecord, identity)
    if row.status != "NEEDS_REVIEW" or row.trace.get("kind") not in {"VLM", "ASR"}:
        raise HTTPException(409, "REVIEW_RELEASE_PROPOSAL_NOT_APPLICABLE")
    if row.trace.get("review_provider"):
        raise HTTPException(409, "REVIEW_RELEASE_PROVIDER_ALREADY_PINNED")
    profile = get(db, ProviderProfile, body.provider_profile_id)
    require_loopback_url(profile.base_url)
    if profile.kind not in {"openai_compatible", "ollama"}:
        raise HTTPException(409, "REVIEW_RELEASE_PROVIDER_KIND_UNSUPPORTED")
    row.trace = {
        **row.trace,
        "review_provider": {
            "profile_id": profile.id,
            "kind": profile.kind,
            "model": profile.model,
            "base_url_sha256": hashlib.sha256(profile.base_url.encode()).hexdigest(),
            "legacy_proposal_only": True,
        },
    }
    add_audit(
        db,
        "model-tool",
        "REVIEW_RELEASE_PROVIDER_PROPOSED",
        "agent_task",
        row.id,
        {"provider_profile_id": profile.id, "execution": False, "requires_human_grant": True},
        project_id=row.project_id,
    )
    db.commit()
    return public(row)


@router.post("/preparation/model-calls/{identity}/abandon")
def abandon_model_call(
    identity: str, body: Decision, actor: Operator, request: Request, settings: SettingsDep, db: DBSession
):
    """Explicit reconciliation of a lost result, never a fake successful result or automatic retry."""
    import os

    from shine.comfyops.installer import process_identity
    from shine.comfyops.model_calls import IN_FLIGHT, IN_FLIGHT_LOCK

    row = get(db, AgentTaskRecord, identity)
    with IN_FLIGHT_LOCK:
        if identity in IN_FLIGHT:
            raise HTTPException(409, "MODEL_CALL_STILL_RUNNING")
    pid = row.trace.get("owner_pid")
    birth = row.trace.get("owner_birth")
    if not pid or not birth:
        raise HTTPException(409, "MODEL_CALL_OWNER_UNVERIFIABLE")
    if pid == os.getpid() and row.status == "RUNNING":
        raise HTTPException(409, "MODEL_CALL_STILL_RUNNING")
    if pid and pid != os.getpid() and process_identity(pid) == birth:
        raise HTTPException(409, "MODEL_CALL_OWNER_STILL_ALIVE")
    reconcile_owner = row.trace.get("reconciliation_owner")
    if row.status == "RECONCILING" and (
        not reconcile_owner or process_identity(reconcile_owner["pid"]) == reconcile_owner["birth"]
    ):
        raise HTTPException(409, "MODEL_RECONCILIATION_OWNER_ALIVE_OR_UNVERIFIABLE")
    if row.status not in {"RUNNING", "NEEDS_REVIEW", "RECONCILING"}:
        raise HTTPException(409, "MODEL_CALL_NOT_UNCERTAIN")
    db.rollback()
    locked(db)
    row = get(db, AgentTaskRecord, identity)
    if row.status not in {"RUNNING", "NEEDS_REVIEW", "RECONCILING"}:
        raise HTTPException(409, "MODEL_CALL_NOT_UNCERTAIN")
    approval.consume(db, actor, "model_reconcile", row, body.grant_id, body.snapshot)
    trace = dict(row.trace)
    row.status = "RECONCILING"
    row.trace = {**row.trace, "reconciliation_owner": {"pid": os.getpid(), "birth": process_identity(os.getpid())}}
    db.commit()  # authorize before any provider side effect; never replay a lost reconciliation
    try:
        if not trace.get("is_mock"):
            binding = trace.get("review_provider")
            if binding:
                import hashlib

                from shine.llm.gateway import LLMGateway
                from shine.llm.secrets import SecretStore
                from shine.models import ProviderProfile
                from shine.quality.semantic import (
                    LocalASRReviewer,
                    LocalVisionReviewer,
                    OpenAICompatibleASRReviewer,
                    OpenAICompatibleVisionReviewer,
                )
                from shine.security import is_loopback_url

                profile = get(db, ProviderProfile, binding["profile_id"])
                if (
                    profile.kind != binding["kind"]
                    or profile.model != binding["model"]
                    or hashlib.sha256(profile.base_url.encode()).hexdigest() != binding["base_url_sha256"]
                ):
                    raise HTTPException(409, "REVIEW_RELEASE_PROVIDER_CHANGED")
                config = LLMGateway(SecretStore(settings.secret_store_dir or settings.data_dir / "secrets")).config_for(
                    profile
                )
                if is_loopback_url(config.base_url):
                    provider = (LocalASRReviewer if trace.get("kind") == "ASR" else LocalVisionReviewer)(config)
                else:
                    # Remote review cannot hold a model on this host. This
                    # reconciliation sends no evidence or new inference call.
                    provider = (
                        OpenAICompatibleASRReviewer if trace.get("kind") == "ASR" else OpenAICompatibleVisionReviewer
                    )(config)
            else:
                provider = (
                    voice_provider(request)
                    if trace.get("kind") in {"TTS", "ASR", "ALIGNMENT"}
                    else getattr(request.app.state, "image_provider", None)
                    if trace.get("kind") == "IMAGE"
                    else getattr(request.app.state, "candidate_reviewer", None)
                )
            if provider is None or not hasattr(provider, "release_models"):
                raise HTTPException(409, "MODEL_RELEASE_PROOF_UNAVAILABLE")
            # ComfyUI unload does not cancel a queued prompt. Require an idle queue first.
            if hasattr(provider, "client") and trace.get("external_id"):
                queue = provider.client.queue()
                if queue.get("queue_running") or queue.get("queue_pending"):
                    raise HTTPException(409, "MODEL_EXTERNAL_QUEUE_NOT_IDLE")
            provider.release_models()
    except Exception:
        row = get(db, AgentTaskRecord, identity)
        row.status = "NEEDS_REVIEW"
        db.commit()
        raise
    locked(db)
    row = get(db, AgentTaskRecord, identity)
    row.status = "ABANDONED"
    row.trace = {
        **row.trace,
        "reconciled_by": actor,
        "lost_result_evidence": body.evidence,
        "outcome": "LOST_RESULT_NOT_APPROVED",
        "automatic_replay": False,
    }
    resource_id = trace.get("resource_id")
    if resource_id:
        lease = db.get(ResourceLease, resource_id)
        if lease:
            ResourceScheduler().release(db, lease.id, lease.owner, reconciled=True)
    parent = db.get(AgentTaskRecord, trace.get("parent_task_id")) if trace.get("parent_task_id") else None
    if parent and parent.status in {"RUNNING", "NEEDS_REVIEW"}:
        parent.status = "ABANDONED"
    audit_payload = {"outcome": "LOST_RESULT_NOT_APPROVED", "evidence": body.evidence}
    add_audit(db, actor, "MODEL_CALL_RECONCILED", "agent_task", row.id, audit_payload, project_id=row.project_id)
    db.commit()
    return public(row)
