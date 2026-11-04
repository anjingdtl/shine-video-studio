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

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field

from shine.api_common import DBSession, app_settings
from shine.api_operator import Operator
from shine.config import Settings
from shine.materialprep import runtime, service
from shine.materialprep.contracts import MaterialConfirmation
from shine.materialprep.states import EXECUTION_MODE_REAL
from shine.schemas import APIModel

router = APIRouter(prefix="/api/v1", tags=["director material preparation"])

SettingsDep = Annotated[Settings, Depends(app_settings)]


class AnalyzeBody(APIModel):
    document_id: str
    expected_document_hash: str = Field(min_length=64, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=128)
    scene_preparations: list[dict] = Field(default_factory=list, max_length=400)


class GenerateBody(APIModel):
    confirmation_id: str = Field(min_length=64, max_length=64)
    confirmation_hash: str = Field(min_length=64, max_length=64)
    requirement_ids: list[str] = Field(min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=128)
    rights_id: str
    mode: Literal["real"] = EXECUTION_MODE_REAL
    seeds: dict[str, int] = Field(default_factory=dict)


class StateVersionBody(APIModel):
    expected_state_version: int = Field(ge=0)
    idempotency_key: str = Field(default="", max_length=128)


class ResumeBody(StateVersionBody):
    item_ids: list[str] = Field(min_length=1, max_length=200)


class RegenerateBody(APIModel):
    requirement_id: str
    confirmation_id: str = Field(min_length=64, max_length=64)
    confirmation_hash: str = Field(min_length=64, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=128)
    rights_id: str
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
    rights_id: str


@router.post("/projects/{project_id}/material-preparation/analyze", status_code=202)
def analyze_material(project_id: str, body: AnalyzeBody, db: DBSession):
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
    plan = service.create_draft_plan(db, project, document, body.scene_preparations)
    aggregated = service.aggregated_requirements(db, plan)
    plan.payload = {**plan.payload, "requirements": aggregated["requirements"]}
    from shine.comfy.workflow import graph_hash

    plan.sha256 = graph_hash(plan.payload)
    db.commit()
    return {
        "status": "accepted",
        "plan_revision_id": plan.id,
        "purpose": plan.purpose,
        "requirements": aggregated["requirements"],
        "conflicts": aggregated["conflicts"],
        "open_questions": aggregated["open_questions"],
    }


@router.get("/projects/{project_id}/material-preparation")
def material_progress(project_id: str, db: DBSession):
    return service.plan_progress(db, project_id)


@router.post("/projects/{project_id}/material-preparation/confirm")
def confirm_material(project_id: str, body: MaterialConfirmation, actor: Operator, db: DBSession):
    return service.confirm(db, actor, project_id, body)


@router.post("/projects/{project_id}/material-preparation/generate", status_code=202)
def generate_material(project_id: str, body: GenerateBody, db: DBSession):
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
def cancel_run(project_id: str, run_id: str, body: StateVersionBody, db: DBSession):
    _require_ownership(db, project_id, run_id)
    return {"status": "accepted", **service.request_cancel(db, run_id, body.expected_state_version)}


@router.post("/projects/{project_id}/material-preparation/runs/{run_id}/reconcile", status_code=202)
def reconcile_run(project_id: str, run_id: str, body: StateVersionBody, db: DBSession):
    _require_ownership(db, project_id, run_id)
    return {"status": "accepted", **service.reconcile_run(db, run_id, body.expected_state_version)}


@router.post("/projects/{project_id}/material-preparation/runs/{run_id}/resume", status_code=202)
def resume_run(project_id: str, run_id: str, body: ResumeBody, db: DBSession):
    _require_ownership(db, project_id, run_id)
    return {"status": "accepted", **service.resume_items(db, run_id, body.item_ids, body.expected_state_version)}


@router.post("/projects/{project_id}/material-preparation/regenerate", status_code=202)
def regenerate_requirement(project_id: str, body: RegenerateBody, db: DBSession):
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
    """Four-layer readiness per family.  Layer four is the only one that renders."""
    return runtime.readiness(db, settings)


@router.post("/setup/image-runtime")
def bind_image_runtime(body: ImageRuntimeBody, actor: Operator, db: DBSession, settings: SettingsDep):
    """Bind an already-registered managed instance.  This never downloads."""
    from shine.comfyops.models import ComfyInstance
    from shine.models import utcnow
    from shine.security import require_loopback_url
    from shine.services import add_audit

    instance = db.get(ComfyInstance, body.instance_id)
    if instance is None:
        raise HTTPException(404, "COMFY_INSTANCE_NOT_FOUND")
    base_url = require_loopback_url(body.base_url, allow_remote=bool(settings.allow_remote_comfyui))
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


@router.post("/setup/image-runtime/validate", status_code=202)
def validate_image_runtime(body: ValidateBody, db: DBSession, settings: SettingsDep):
    """Queue the three real small-sample renders through the same Worker queue.

    A validation batch is allowed at ``GRAPH_VERIFIED``; that is what breaks the
    first-sample circular dependency.  It still renders for real - a validation
    that did not produce bytes would be claiming a capability it never tested.
    """
    from shine.comfyops.assets import require_rights
    from shine.services import require_project

    project = require_project(db, body.project_id)
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
