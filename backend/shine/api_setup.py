"""New-machine setup API: environment probe, catalog, plan assembly, config generation, readiness.

Mutating endpoints require the loopback operator; read-only probes stay open on
loopback like the rest of the loopback-only API. Every mutation still flows
through the existing grant/approve/execute machinery - nothing here executes.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from shine.api_common import DBSession, app_settings
from shine.api_operator import Operator
from shine.comfyops import deployment
from shine.config import Settings

router = APIRouter(prefix="/api/v1/setup", tags=["new-machine-setup"])
SettingsDep = Annotated[Settings, Depends(app_settings)]


class InstallPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    catalog: str = "h3-ref2va-windows-x64"
    components: list[str] = Field(min_length=1, max_length=14)
    project_id: str
    disk_free: int = Field(ge=0)


class InstanceChainRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    catalog: str = "h3-ref2va-windows-x64"
    project_id: str
    instance_name: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,60}$", default="shine-h3")
    port: int = Field(default=8188, ge=1024, le=65535)
    mode: str = Field(default="local", pattern=r"^(fake|local)$")


class ConfigurationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str
    instance_name: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,60}$", default="shine-h3")
    port: int = Field(default=8188, ge=1024, le=65535)


class RuntimeActivationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    project_id: str
    recipe_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


class ServiceValidationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    project_id: str
    recipe_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


class ProfileRegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str


class StorageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=1024)
    allow_system_drive: bool = False


class ExistingInstallationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    path: str = Field(min_length=1, max_length=1024)


class ExistingModelImportRequest(ExistingInstallationRequest):
    snapshot: str = Field(pattern=r'^[a-f0-9]{64}$')


@router.get('/existing-installations')
def existing_installations(settings: SettingsDep):
    from shine.comfyops.existing_installation import discover

    return discover(list(settings.comfy_probe_roots))


@router.post('/existing-installations/inspect')
def inspect_existing(body: ExistingInstallationRequest, actor: Operator):
    from shine.comfyops.existing_installation import inspect

    return inspect(body.path, deployment.load_default_catalog())


@router.post('/existing-installations/import-models')
def import_existing_models(body: ExistingModelImportRequest, actor: Operator, settings: SettingsDep, db: DBSession):
    from shine.comfyops.existing_installation import import_models, inspect
    from shine.services import add_audit

    report = inspect(body.path, deployment.load_default_catalog())
    result = import_models(settings, report, body.snapshot)
    add_audit(db, actor, 'EXISTING_COMFY_MODELS_CACHED', 'deployment', report['catalog_id'],
              {'root': report['root'], 'snapshot': body.snapshot, 'reused_bytes': result['reused_bytes'],
               'imported': [{'sha256': item['sha256'], 'source': item.get('source')} for item in result['imported']],
               'rejected': [item['sha256'] for item in result['rejected']]})
    db.commit()
    return result


@router.get('/storage')
def storage(settings: SettingsDep):
    from shine.comfyops.storage import storage_report
    return storage_report(settings)


@router.put('/storage')
def update_storage(body: StorageRequest, actor: Operator, settings: SettingsDep, db: DBSession):
    from shine.comfyops.storage import configure_storage
    return configure_storage(db, settings, body.path, actor, allow_system_drive=body.allow_system_drive)


@router.get("/environment")
def environment(settings: SettingsDep):
    from shine.comfyops.host_env import probe_host_environment, setup_blockers

    report = probe_host_environment(settings.data_dir, deployment_dir=settings.comfyops_dir)
    return {**report, "blockers": setup_blockers(report),
            "bootstrap": "pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/bootstrap.ps1"}


@router.get("/connectivity")
def connectivity():
    """Read-only upstream reachability for the fixed 60.8 GB deployment."""
    from shine.comfyops.connectivity import connectivity_blockers, probe_upstreams

    report = probe_upstreams()
    return {**report, "blockers": connectivity_blockers(report)}


@router.get("/catalog")
def catalog_summary():
    raw = deployment.load_deployment_catalog()
    return {
        "catalog_id": raw["catalog_id"],
        "catalog_version": raw["catalog_version"],
        "generated": raw["generated"],
        "platform": raw["platform"],
        "total_bytes": raw["total_bytes"],
        "components": [
            {
                "component_id": c["component_id"], "kind": c["kind"], "revision": c["revision"],
                "license_id": c["license_id"], "usage": c["usage"], "license_url": c.get("license_url", ""),
                "destination_family": c.get("destination_family"), "role": c.get("role"),
                "files": len(c["files"]), "bytes": sum(f["size"] for f in c["files"]),
                "status": c["status"], "blocked_reason": c.get("blocked_reason", ""),
            }
            for c in raw["components"]
        ],
    }


@router.post("/install-plan")
def install_plan(body: InstallPlanRequest, settings: SettingsDep, db: DBSession):
    raw = deployment.load_deployment_catalog(body.catalog)
    valid_ids = {c["component_id"] for c in raw["components"]}
    if not set(body.components) <= valid_ids:
        raise HTTPException(422, "CATALOG_COMPONENT_UNKNOWN")
    plan = deployment.create_install_plan(db, settings, raw, body.components, body.project_id, body.disk_free)
    return {"plan_id": plan.id, "sha256": plan.sha256, "status": plan.status,
            "download_bytes": plan.payload["download_bytes"], "disk_required": plan.payload["disk_required"],
            "packages": [p["id"] for p in plan.payload["packages"]]}


@router.post("/instance-chain", deprecated=True)
@router.post("/next-instance-plan")
def next_instance_plan(body: InstanceChainRequest, settings: SettingsDep, db: DBSession):
    raw = deployment.load_deployment_catalog(body.catalog)
    plan, action = deployment.next_instance_plan(db, settings, raw, body.project_id,
                                                 instance_name=body.instance_name, port=body.port,
                                                 mode=body.mode)
    if plan is None:
        return {"complete": True, "action": None, "next_step": "GENERATE_CONFIGURATION"}
    return {"complete": False, "action": action, "plan_id": plan.id, "sha256": plan.sha256,
            "tier": plan.payload["tier"], "status": plan.status}


@router.post("/configuration")
def configuration(body: ConfigurationRequest, actor: Operator, settings: SettingsDep, db: DBSession):
    return deployment.generate_configuration(db, settings, body.project_id,
                                             instance_name=body.instance_name, port=body.port)


@router.get("/readiness")
def readiness(settings: SettingsDep, db: DBSession):
    return deployment.deployment_readiness(db, settings)

@router.get('/runtime')
def runtime_state(settings: SettingsDep, db: DBSession):
    from shine.comfyops.runtime_configuration import runtime_report
    return runtime_report(db, settings)


@router.post('/runtime/activate')
def activate_runtime(body: RuntimeActivationRequest, actor: Operator, settings: SettingsDep, db: DBSession):
    from shine.comfyops.runtime_configuration import activate_runtime as activate
    return activate(db, settings, body.project_id, body.recipe_sha256, actor)


@router.post('/services/validate')
def validate_services(body: ServiceValidationRequest, actor: Operator, settings: SettingsDep, db: DBSession):
    """Start installed services once, validate their live contracts, stop them."""
    from shine.comfyops.service_validation import validate_services as run
    return run(db, settings, body.project_id, body.recipe_sha256, actor=actor)


@router.post("/profiles")
def profiles(body: ProfileRegistrationRequest, db: DBSession):
    """Register the two supported Ref2VA workflow profiles for this project.

    Idempotent per project: the default profiles are project-scoped copies, so
    a second project registers its own usable entries instead of being told
    the name was already taken by another project.  A same-name profile inside
    the same project with different graph/parameter_map/media_transfers/mode
    stays a hard conflict (PROFILE_CONTENT_CHANGED); profiles of other
    projects and historical references are never touched.
    """
    import json as _json

    from sqlalchemy import select

    from shine.api_comfy import create_workflow_profile
    from shine.models import WorkflowProfile
    from shine.schemas import WorkflowProfileCreate

    deployment._require_project(db, body.project_id)
    registered = []
    for name in deployment.load_default_catalog()["workflow_profiles"]:
        from shine.comfy.workflow import graph_hash
        matches = [p for p in deployment.PROFILE_DIR.glob('*.json')
                   if _json.loads(p.read_text(encoding='utf-8')).get('name') == name]
        if len(matches) != 1:
            raise HTTPException(409, f"PROFILE_FILE_MISSING_OR_AMBIGUOUS: {name}")
        raw = _json.loads(matches[0].read_text(encoding='utf-8'))
        existing = db.scalar(select(WorkflowProfile).where(
            WorkflowProfile.name == name,
            WorkflowProfile.project_id == body.project_id,
        ))
        if existing:
            if (existing.graph_sha256 != graph_hash(raw['graph'])
                    or existing.parameter_map != raw['parameter_map']
                    or existing.media_transfers != raw.get('media_transfers', {})
                    or existing.mode != raw['mode']):
                raise HTTPException(409, 'PROFILE_CONTENT_CHANGED')
            registered.append({"name": name, "profile_id": existing.id, "status": "ALREADY_REGISTERED"})
            continue
        request = WorkflowProfileCreate(
            project_id=body.project_id, name=raw["name"], mode=raw["mode"],
            graph=raw["graph"], parameter_map=raw["parameter_map"],
            media_transfers=raw.get("media_transfers"), required_nodes=raw["required_nodes"],
            checkpoint_id=raw.get("checkpoint_id"),
        )
        profile = create_workflow_profile(request, db)
        registered.append({"name": name, "profile_id": profile.id, "status": "REGISTERED"})
    db.commit()
    return {"profiles": registered}
