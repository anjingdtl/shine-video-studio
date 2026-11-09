"""New-machine setup API: environment probe, catalog, plan assembly, config generation, readiness.

Mutating endpoints require the loopback operator; read-only probes stay open on
loopback like the rest of the loopback-only API. Every mutation still flows
through the existing grant/approve/execute machinery - nothing here executes.
"""

from __future__ import annotations

import threading
from collections import Counter
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from shine.api_common import DBSession, app_settings, secret_store
from shine.api_operator import Operator
from shine.comfyops import deployment
from shine.config import Settings
from shine.llm.secrets import SecretStore

router = APIRouter(prefix="/api/v1/setup", tags=["new-machine-setup"])
SettingsDep = Annotated[Settings, Depends(app_settings)]
_installation_verification_lock = threading.Lock()
_active_installation_verifications: set[tuple[str, str]] = set()


class InstallPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    catalog: str = "h3-ref2va-windows-x64"
    components: list[str] = Field(min_length=1, max_length=14)
    project_id: str
    disk_free: int = Field(ge=0)
    #: The variant this plan installs.  It is normally derived from the
    #: requested components; naming it makes the binding explicit.
    variant_id: str | None = None
    #: The deployment combination the person chose on the recommendation card.
    #: It is recorded in the plan and echoed back, so the size, the components
    #: and the plan the person approves are the same facts.  Image capability
    #: is a *separate* catalog and therefore a separate companion plan: it is
    #: never silently merged into the H3 installation.
    include_image_capability: bool = False


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
    #: The variant whose own catalog and profiles are registered.  Omitting it
    #: keeps the ordinary path: the default catalog and the profiles it
    #: declares.  Naming a variant resolves *that* variant's catalog, so a
    #: candidate stack never borrows the default catalog's profiles.
    variant_id: str | None = None
    #: The deployment catalog the caller expects.  When it disagrees with the
    #: catalog the variant declares, the request is refused instead of
    #: falling back to either of them.
    catalog: str | None = None


class StorageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=1024)
    allow_system_drive: bool = False


class RecommendationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purposes: list[str] | None = Field(default=None, max_length=4)
    reuse_installed: bool = True


class ExistingInstallationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    path: str = Field(min_length=1, max_length=1024)
    #: Compare the found files against this variant's own catalog instead of
    #: the default one.  This is the approved path for a candidate stack: the
    #: official weights are matched by their pinned revision, byte count and
    #: SHA-256 and then cached - never downloaded by the product.
    variant_id: str | None = None
    catalog: str | None = None


class ExistingModelImportRequest(ExistingInstallationRequest):
    snapshot: str = Field(pattern=r'^[a-f0-9]{64}$')


class ExistingInstallationAdoptRequest(ExistingModelImportRequest):
    project_id: str
    instance_name: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,60}$", default="shine-h3")
    port: int = Field(default=8188, ge=1024, le=65535)


def _inspection_catalog(variant_id: str | None = None, catalog: str | None = None) -> dict:
    """The catalog the existing-installation path compares the found files with.

    Without a variant this is the audited default catalog, unchanged.  With a
    variant it is *that* variant's catalog, so the approved local-import path
    can verify the official W6A8 weights against their own pins instead of
    against the INT8 baseline's.
    """
    if not variant_id and not catalog:
        return deployment.load_default_catalog()
    from shine.comfyops.variant_binding import resolve_registration_target

    return resolve_registration_target(variant_id=variant_id, catalog_id=catalog).catalog


@router.get('/existing-installations')
def existing_installations(settings: SettingsDep):
    from shine.comfyops.existing_installation import discover

    return discover(list(settings.comfy_probe_roots))


@router.post('/existing-installations/inspect')
def inspect_existing(body: ExistingInstallationRequest, actor: Operator):
    from shine.comfyops.existing_installation import inspect

    return inspect(body.path, _inspection_catalog(body.variant_id, body.catalog))


@router.post('/existing-installations/import-models')
def import_existing_models(body: ExistingModelImportRequest, actor: Operator, settings: SettingsDep, db: DBSession):
    from shine.comfyops.existing_installation import import_models, inspect
    from shine.services import add_audit

    report = inspect(body.path, _inspection_catalog(body.variant_id, body.catalog))
    result = import_models(settings, report, body.snapshot)
    add_audit(db, actor, 'EXISTING_COMFY_MODELS_CACHED', 'deployment', report['catalog_id'],
              {'root': report['root'], 'snapshot': body.snapshot, 'reused_bytes': result['reused_bytes'],
               'imported': [{'sha256': item['sha256'], 'source': item.get('source')} for item in result['imported']],
               'rejected': [item['sha256'] for item in result['rejected']]})
    db.commit()
    return result


@router.post('/existing-installations/verify')
def verify_existing_installation(body: ExistingModelImportRequest, actor: Operator, db: DBSession):
    """Verify existing software/model identity in place without copying files."""
    from sqlalchemy import select

    from shine.comfyops.existing_installation import inspect, register_verified_packages, verify_models
    from shine.models import AuditEvent
    from shine.services import add_audit

    catalog = _inspection_catalog(body.variant_id, body.catalog)
    report = inspect(body.path, catalog)
    if body.snapshot != report['snapshot']:
        return verify_models(report, body.snapshot)  # raises the snapshot conflict before reading weights

    audits = db.scalars(select(AuditEvent).where(
        AuditEvent.event_type == 'EXISTING_COMFY_INSTALLATION_VERIFIED',
        AuditEvent.entity_type == 'deployment',
        AuditEvent.entity_id == report['catalog_id'],
    ).order_by(AuditEvent.created_at.desc()).limit(100)).all()
    for event in audits:
        payload = event.payload or {}
        # Audit sanitization redacts absolute paths. The inspection snapshot
        # includes the canonical root, so an exact snapshot still binds this
        # result to the same selected installation without storing its path.
        if payload.get('root') not in {report['root'], '[local-path]'} or payload.get('snapshot') != body.snapshot:
            continue
        verified_counts = Counter(payload.get('verified_components') or [])
        rejected_counts = Counter(payload.get('rejected_components') or [])
        if set(verified_counts) & set(rejected_counts):
            continue  # Summary evidence cannot identify mixed per-file results safely.
        verified, rejected = [], []
        for match in report['matches']:
            component = match['component_id']
            if verified_counts[component] > 0:
                verified.append({**match, 'status': 'INSTALLED_HASH_MATCH'})
                verified_counts[component] -= 1
            elif rejected_counts[component] > 0:
                rejected.append({**match, 'status': 'HASH_MISMATCH_OR_CHANGED'})
                rejected_counts[component] -= 1
            else:
                break
        if (len(verified) + len(rejected) != len(report['matches'])
                or any(verified_counts.values()) or any(rejected_counts.values())):
            continue
        verified_bytes = payload.get('verified_bytes')
        if verified_bytes != sum(row['size'] for row in verified):
            continue
        registered = register_verified_packages(db, verified, catalog)
        if registered:
            add_audit(db, actor, 'EXISTING_VERIFIED_PACKAGES_REGISTERED', 'deployment', report['catalog_id'],
                      {'components': sorted(registered)})
            db.commit()
        return {
            **report,
            'status': 'INSTALLED_VERIFIED',
            'verified': verified,
            'rejected': rejected,
            'verified_bytes': verified_bytes,
            'copied_bytes': 0,
            'runtime_verified': False,
            'verification_reused': True,
            'registered_packages': sorted(registered),
        }

    key = (report['root'].casefold(), body.snapshot)
    with _installation_verification_lock:
        if key in _active_installation_verifications:
            return {**report, 'status': 'VERIFYING', 'retry_after_ms': 5000}
        _active_installation_verifications.add(key)
    try:
        result = verify_models(report, body.snapshot)
        add_audit(db, actor, 'EXISTING_COMFY_INSTALLATION_VERIFIED', 'deployment', report['catalog_id'], {
            'root': report['root'],
            'snapshot': body.snapshot,
            'software_version_status': report['software']['version_status'],
            'verified_components': [item['component_id'] for item in result['verified']],
            'rejected_components': [item['component_id'] for item in result['rejected']],
            'verified_files': [
                {'component_id': item['component_id'], 'relative_path': item['relative_path'],
                 'sha256': item['sha256'], 'size': item['size']}
                for item in result['verified']
            ],
            'rejected_files': [
                {'component_id': item['component_id'], 'relative_path': item['relative_path'],
                 'sha256': item['sha256'], 'size': item['size']}
                for item in result['rejected']
            ],
            'verified_bytes': result['verified_bytes'],
            'copied_bytes': 0,
        })
        registered = register_verified_packages(db, result['verified'], catalog)
        if registered:
            add_audit(db, actor, 'EXISTING_VERIFIED_PACKAGES_REGISTERED', 'deployment', report['catalog_id'],
                      {'components': sorted(registered)})
        db.commit()
        return {**report, **result, 'verification_reused': False,
                'registered_packages': sorted(registered)}
    finally:
        with _installation_verification_lock:
            _active_installation_verifications.discard(key)


@router.post('/existing-installations/adopt')
def adopt_existing(body: ExistingInstallationAdoptRequest, actor: Operator, settings: SettingsDep, db: DBSession):
    """Confirm an existing H3 install for reuse without copying or downloading it."""
    from shine.comfyops.adoption import adopt_existing_installation

    return adopt_existing_installation(
        db,
        settings,
        project_id=body.project_id,
        path=body.path,
        snapshot=body.snapshot,
        name=body.instance_name,
        port=body.port,
        actor=actor,
    )


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
            "advisory_reviews": deployment.advisory_review_readiness(settings),
            "bootstrap": "pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/bootstrap.ps1"}


@router.post("/deployment-recommendations")
def deployment_recommendations(body: RecommendationRequest, settings: SettingsDep, db: DBSession):
    """Recommend audited deploy combinations for this machine.

    Read-only: detection never loads a weight, starts a renderer or installs a
    package.  A combination without local measurement is reported as estimated,
    never as verified.
    """
    from shine.comfyops.host_env import probe_host_environment
    from shine.comfyops.recommendation import recommend
    from shine.models import AppSetting

    try:
        env = probe_host_environment(settings.data_dir, deployment_dir=settings.comfyops_dir)
        record = db.get(AppSetting, "deployment_validation")
        validation = dict(record.value or {}) if record else {}
        return recommend(
            env,
            purposes=body.purposes,
            validation=validation,
            reuse_root=settings.comfyops_dir if body.reuse_installed else None,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("/variant-recommendations")
def variant_recommendations(settings: SettingsDep):
    """Which H3 variant fits this machine - as a candidate, never as a promise.

    Read-only.  A variant that has not passed a real acceptance is reported
    with its acceptance status, its evidence gaps and the fact that a human
    has to acknowledge it; it is never reported as production ready.
    """
    from shine.comfyops.host_env import probe_host_facts
    from shine.comfyops.recommendation import recommend_variants

    env = probe_host_facts(settings.data_dir, deployment_dir=settings.comfyops_dir)
    return recommend_variants(env)


@router.get("/connectivity")
def connectivity():
    """Read-only upstream reachability for the deployment materials.

    The download size is not a fixed number: it depends on the selected
    variant and on the components of the plan, so the confirmation screen
    shows the complete catalog, the default production components and the
    optional advisory components as three separate sizes.
    """
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
        "variant_id": raw.get("variant_id"),
        "total_bytes": raw["total_bytes"],
        # The three sizes are different numbers and are never written as one
        # hard-coded total: the complete catalog, the default production
        # components and the optional advisory components.
        "download_breakdown": deployment.download_breakdown(raw),
        "deferred_workflow_profiles": raw.get("deferred_workflow_profiles", []),
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
    """Create an install plan - after the server re-checked the hardware.

    The browser may hide a button, but the button is not the gate: the
    server runs the same qualification again for every plan, so a request
    that skips the user interface is refused in exactly the same way.
    """
    from shine.comfyops import variants as variant_manifests
    from shine.comfyops.host_env import probe_host_facts

    raw = deployment.load_deployment_catalog(body.catalog)
    valid_ids = {c["component_id"] for c in raw["components"]}
    if not set(body.components) <= valid_ids:
        raise HTTPException(422, "CATALOG_COMPONENT_UNKNOWN")
    variant = deployment.resolve_variant(raw, body.components)
    if body.variant_id:
        try:
            variant = variant_manifests.load_variant_manifest(body.variant_id)
        except (OSError, ValueError, FileNotFoundError):
            raise HTTPException(422, "VARIANT_MANIFEST_UNKNOWN") from None
    qualification = None
    if variant is not None:
        env = probe_host_facts(settings.data_dir, deployment_dir=settings.comfyops_dir)
        qualification = variant_manifests.qualify(variant_manifests.variant_requirements(variant), env)
        if not variant.get("auto_download_allowed"):
            raise HTTPException(409, {
                "code": "VARIANT_NOT_DOWNLOADABLE",
                "message": "这个变体还没有进入自动安装路径；它的验收状态是 "
                           + str(variant.get("acceptance_status")) + "。",
                "variant_id": variant.get("variant_id"),
                "acceptance_status": variant.get("acceptance_status"),
            })
        if qualification["status"] != variant_manifests.STATUS_SUPPORTED:
            raise HTTPException(409, {
                "code": "HOST_NOT_QUALIFIED",
                "message": "这台电脑没有通过这套变体的硬件资格复核；绕过界面直接调用接口也一样会被拒绝。",
                "variant_id": variant.get("variant_id"),
                "status": qualification["status"],
                "blockers": qualification["blockers"],
                "unknowns": qualification["unknowns"],
            })
    plan = deployment.create_install_plan(db, settings, raw, body.components, body.project_id, body.disk_free,
                                          variant=variant, qualification=qualification,
                                          configuration={
                                              "h3_video": True,
                                              "image_prep": bool(body.include_image_capability),
                                          })
    companion = []
    if body.include_image_capability:
        # Qwen Image is its own catalog with its own licence and its own disk
        # budget.  It is reported as a separate companion plan instead of being
        # folded into the H3 download, where its size would silently disappear
        # from the number the person approved.
        try:
            image_catalog = deployment.load_deployment_catalog("qwen-image-2.1-windows-x64")
            companion.append({
                "capability": "image_prep",
                "catalog": image_catalog.get("catalog_id"),
                "catalog_version": image_catalog.get("catalog_version"),
                "components": [row["component_id"] for row in image_catalog["components"]],
                "bytes": int(image_catalog.get("total_bytes") or 0),
                "file_declarations": sum(len(row.get("files", [])) for row in image_catalog["components"]),
                "install_entry": "ImageCapabilityCard",
                "note": "图片素材生成是独立能力，安装计划单独批准与执行。",
            })
        except (OSError, ValueError, FileNotFoundError):
            companion.append({"capability": "image_prep", "status": "CATALOG_UNAVAILABLE"})
    return {"plan_id": plan.id, "sha256": plan.sha256, "status": plan.status,
            "download_bytes": plan.payload["download_bytes"], "disk_required": plan.payload["disk_required"],
            "packages": [p["id"] for p in plan.payload["packages"]],
            "variant_id": (variant or {}).get("variant_id"),
            "qualification": qualification,
            "configuration": plan.payload.get("configuration"),
            "companion_plans": companion,
            "download_breakdown": plan.payload.get("download_breakdown")}


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
    from shine.comfyops.review_runtime import prepare_review_runtime
    from shine.comfyops.runtime_configuration import require_idle_configuration
    from shine.services import require_project

    require_project(db, body.project_id)
    require_idle_configuration(db)
    db.rollback()  # Wheel verification and extraction do not hold a SQLite transaction.
    runtime = prepare_review_runtime(settings, deployment.load_default_catalog())
    result = deployment.generate_configuration(db, settings, body.project_id,
                                               instance_name=body.instance_name, port=body.port)
    return {**result, "review_runtime_preparation": runtime}


@router.get("/readiness")
def readiness(settings: SettingsDep, db: DBSession):
    return deployment.deployment_readiness(db, settings)

@router.get('/runtime')
def runtime_state(settings: SettingsDep, db: DBSession):
    from shine.comfyops.runtime_configuration import runtime_report
    return runtime_report(db, settings)


@router.get('/managed-processes')
def managed_processes(db: DBSession):
    """The renderers and local reviewers this workbench owns right now."""
    from shine.comfyops.renderer_release import describe_managed_processes
    return {"processes": describe_managed_processes(db)}


@router.post('/managed-processes/release')
def release_managed_processes(actor: Operator, db: DBSession):
    """Stop the owned renderer and reviewers and give the GPU memory back.

    This is the workbench's own "close the backend" action: ComfyUI and the
    local reviewers are started by this installation, so closing the workbench
    closes them.  A process that serves a running attempt, or whose ownership
    cannot be proven, is reported and left alone.
    """
    from shine.comfyops.renderer_release import release_managed_processes as release
    report = release(db, actor=actor, reason="operator_release")
    db.commit()
    return report


@router.post('/runtime/activate')
def activate_runtime(body: RuntimeActivationRequest, actor: Operator, settings: SettingsDep, db: DBSession):
    from shine.comfyops.runtime_configuration import activate_runtime as activate
    return activate(db, settings, body.project_id, body.recipe_sha256, actor)


@router.post('/services/validate')
def validate_services(body: ServiceValidationRequest, actor: Operator, settings: SettingsDep, db: DBSession):
    """Start installed services once, validate their live contracts, stop them."""
    from shine.comfyops.service_validation import validate_services as run
    return run(db, settings, body.project_id, body.recipe_sha256, actor=actor)


def _register_project_profiles(db, project_id: str, *, variant_id: str | None = None,
                                catalog: str | None = None) -> list[dict]:
    """Register this project's H3 Ref2VA workflow profiles from the canonical files.

    Idempotent per project: the default profiles are project-scoped copies, so
    a second project registers its own usable entries instead of being told
    the name was already taken by another project.  A same-name profile inside
    the same project with different graph/parameter_map/media_transfers/mode
    stays a hard conflict (PROFILE_CONTENT_CHANGED); profiles of other
    projects and historical references are never touched.
    """
    from sqlalchemy import select

    from shine.api_comfy import create_workflow_profile
    from shine.comfy.workflow import graph_hash
    from shine.comfyops.variant_binding import resolve_registration_target
    from shine.models import WorkflowProfile
    from shine.schemas import WorkflowProfileCreate

    deployment._require_project(db, project_id)
    binding = resolve_registration_target(variant_id=variant_id, catalog_id=catalog)
    registered = []
    for raw in binding.profiles:
        name = str(raw.get("name"))
        existing = db.scalar(select(WorkflowProfile).where(
            WorkflowProfile.name == name,
            WorkflowProfile.project_id == project_id,
        ))
        if existing:
            current = dict(existing.parameter_map or {})
            target = dict(raw['parameter_map'])
            # A profile registered before the output-size mapping existed only
            # differs by the two standard keys. Adopting them is a forward
            # migration of the project's own copy, audited, never a silent edit
            # of a customized mapping.
            standard_only = (
                existing.graph_sha256 == graph_hash(raw['graph'])
                and existing.mode == raw['mode']
                and (existing.media_transfers or {}) == (raw.get('media_transfers') or {})
                and {key: value for key, value in target.items() if key not in ("width", "height")} == current
                and all(key not in current and target.get(key) for key in ("width", "height"))
            )
            if standard_only:
                from shine.services import add_audit

                existing.parameter_map = target
                add_audit(
                    db, "system", "WORKFLOW_PROFILE_STANDARD_PARAMETERS", "workflow_profile",
                    existing.id,
                    {"name": name, "added": ["width", "height"], "source": "canonical_profile"},
                    project_id=project_id,
                )
                registered.append({"name": name, "profile_id": existing.id,
                                   "status": "STANDARD_PARAMETERS_UPDATED"})
                continue
            if (existing.graph_sha256 != graph_hash(raw['graph'])
                    or current != target
                    or (existing.media_transfers or {}) != (raw.get('media_transfers') or {})
                    or existing.mode != raw['mode']):
                raise HTTPException(409, 'PROFILE_CONTENT_CHANGED')
            registered.append({"name": name, "profile_id": existing.id, "status": "ALREADY_REGISTERED"})
            continue
        request = WorkflowProfileCreate(
            project_id=project_id, name=raw["name"], mode=raw["mode"],
            graph=raw["graph"], parameter_map=raw["parameter_map"],
            media_transfers=raw.get("media_transfers"), required_nodes=raw["required_nodes"],
            checkpoint_id=raw.get("checkpoint_id"),
        )
        profile = create_workflow_profile(request, db)
        registered.append({"name": name, "profile_id": profile.id, "status": "REGISTERED"})
    db.commit()
    return registered


@router.post("/profiles")
def profiles(body: ProfileRegistrationRequest, db: DBSession):
    """Register this project's Ref2VA workflow profiles from the canonical files.

    Without a variant the ordinary, audited default catalog is used, exactly as
    before.  With a variant that variant's own catalog and its own profiles are
    registered, so the official W6A8 candidate gets its PDD-free profile and
    never the INT8 baseline's profiles.
    """
    return {"profiles": _register_project_profiles(
        db, body.project_id, variant_id=body.variant_id, catalog=body.catalog)}


@router.post("/profiles/validate")
def validate_profiles(body: ProfileRegistrationRequest, settings: SettingsDep, db: DBSession):
    """Register this project's H3 Ref2VA profiles and verify them on the real renderer.

    Registration alone proves nothing: a profile that names a node or an input
    the installed renderer does not have only fails at render time.  The
    managed renderer is started for the check and stopped again, so the result
    states which nodes and inputs the real installation really offers.  A
    profile that cannot be validated stays unvalidated; the response reports
    the reason instead of claiming a pass.

    Every Ref2VA profile of this project is checked, not only the ones this
    call registered: the project may already carry a wider profile, and the
    production gate reads the project's own profiles.
    """
    from sqlalchemy import select

    from shine.comfyops.service_validation import validate_registered_workflow
    from shine.models import WorkflowProfile

    registered = _register_project_profiles(
        db, body.project_id, variant_id=body.variant_id, catalog=body.catalog)
    by_id = {entry["profile_id"]: entry for entry in registered}
    for profile in db.scalars(
        select(WorkflowProfile).where(WorkflowProfile.project_id == body.project_id,
                                       WorkflowProfile.mode == "Ref2VA")
    ):
        by_id.setdefault(profile.id, {"name": profile.name, "profile_id": profile.id,
                                      "status": "ALREADY_REGISTERED"})
    results = []
    for profile_id, entry in by_id.items():
        try:
            validated = validate_registered_workflow(db, settings, profile_id)
        except HTTPException as exc:
            results.append({**entry, "validated": False,
                            "code": exc.detail if isinstance(exc.detail, str) else "VALIDATION_FAILED",
                            "validated_against": None})
            continue
        results.append({**entry, "validated": True, "code": None,
                        "validated_against": validated})
    return {"profiles": results}


@router.get("/variant-readiness")
def variant_readiness(project_id: str, variant_id: str, settings: SettingsDep, db: DBSession):
    """How far one variant got on this machine, and the single next step.

    The five layers are the dependency order: the pinned bytes, the byte-level
    verification, the project-scoped profile, the live node check and finally a
    real shot.  The response names exactly one next step, because "almost
    ready" is not an action.
    """
    from shine.comfyops.variant_binding import load_binding
    from shine.comfyops.variant_readiness import readiness

    deployment._require_project(db, project_id)
    binding = load_binding(variant_id)
    return readiness(db, settings, binding, project_id)


@router.get("/production-gate")
def production_gate(project_id: str, profile_id: str, settings: SettingsDep, db: DBSession):
    """Whether one generation profile may be selected for production right now.

    An accepted variant keeps its historic behaviour.  A candidate variant is
    only selectable when this machine can really execute it; every reason it
    cannot is named.
    """
    from shine.comfyops.profile_gate import profile_gate_report
    from shine.models import WorkflowProfile

    deployment._require_project(db, project_id)
    profile = db.get(WorkflowProfile, profile_id)
    return profile_gate_report(db, settings, project_id, profile)


@router.get('/llm-readiness')
def text_model_readiness(db: DBSession, secrets: Annotated[SecretStore, Depends(secret_store)]):
    from shine.comfyops.llm_readiness import llm_readiness
    return llm_readiness(db, secrets=secrets)


@router.get('/voice-readiness')
def voice_model_readiness(db: DBSession, settings: SettingsDep):
    """Static voice-capability status for the new-machine setup summary."""
    from shine.comfyops.llm_readiness import voice_readiness
    return voice_readiness(db, settings)


@router.get('/voice-runtime')
def voice_runtime(settings: SettingsDep, db: DBSession):
    """The effective local voice configuration and what it can currently do."""
    from shine.comfyops.llm_readiness import voice_readiness
    from shine.voiceops.runtime_config import config_path, load_voice_config

    config = load_voice_config(settings)
    return {
        'config_path': str(config_path(settings)),
        'packages': config.routing(),
        'role_runtimes': {role: spec.model_dump(mode="json") for role, spec in config.runtimes().items()},
        'readiness': voice_readiness(db, settings),
    }


class VoiceRuntimeRequest(BaseModel):
    """Which local package serves which voice role, and how it is executed."""

    model_config = ConfigDict(extra="forbid")
    packages: dict[str, str] = Field(default_factory=dict)
    role_runtimes: dict[str, dict] = Field(default_factory=dict)


@router.post('/voice-runtime')
def configure_voice_runtime(body: VoiceRuntimeRequest, actor: Operator, settings: SettingsDep, db: DBSession):
    """Pin the local voice packages and their interpreters.

    A role may only be routed to a package that is registered, hash-verified
    and declares the role's capability, and every routed role needs a runtime
    whose interpreter digest and overlay versions match that package.  The
    configuration is rejected as a whole: a half-configured voice path would
    otherwise look ready in the UI and fail on first use.
    """
    from shine.comfyops.models import ModelPackage
    from shine.services import add_audit
    from shine.voiceops.runtime_config import (
        VOICE_ROLE_CAPABILITIES,
        VOICE_ROLES,
        RoleRuntime,
        VoiceRuntimeConfig,
        check_role_runtime,
        save_voice_config,
    )

    packages = {role: identity for role, identity in body.packages.items() if role in VOICE_ROLES}
    unknown = sorted(set(body.packages) - set(VOICE_ROLES))
    if unknown:
        raise HTTPException(422, f"VOICE_ROLE_UNKNOWN: {','.join(unknown)}")
    runtimes: dict[str, RoleRuntime] = {}
    for role, spec in body.role_runtimes.items():
        if role not in VOICE_ROLES:
            raise HTTPException(422, f"VOICE_ROLE_UNKNOWN: {role}")
        try:
            runtimes[role] = RoleRuntime.model_validate(spec)
        except Exception as exc:  # noqa: BLE001 - the pydantic message names the field
            raise HTTPException(422, f"VOICE_RUNTIME_SPEC_INVALID: {role}") from exc

    for role, identity in packages.items():
        package = db.get(ModelPackage, identity)
        if package is None:
            raise HTTPException(409, f"VOICE_PACKAGE_MISSING: {role}")
        capability = VOICE_ROLE_CAPABILITIES[role]
        if capability not in (package.payload.get('capabilities') or []):
            raise HTTPException(409, f"VOICE_CAPABILITY_MISSING: {role}")
        if package.status != 'FILES_VERIFIED_PENDING_RUNTIME':
            raise HTTPException(409, f"VOICE_PACKAGE_NOT_VERIFIED: {role}")
        if role not in runtimes:
            raise HTTPException(409, f"VOICE_ROLE_RUNTIME_REQUIRED: {role}")
        problems = check_role_runtime(role, runtimes[role], package.payload)
        if problems:
            raise HTTPException(409, f"{problems[0]}: {role}")
    for role in runtimes:
        if role not in packages:
            raise HTTPException(409, f"VOICE_ROLE_RUNTIME_PACKAGES_REQUIRED: {role}")

    config = VoiceRuntimeConfig(packages=packages, role_runtimes=runtimes)
    path = save_voice_config(settings, config)
    add_audit(db, actor, 'VOICE_RUNTIME_CONFIGURED', 'voice_runtime', 'local',
              {'roles': sorted(packages), 'config_path': str(path)}, project_id=None)
    db.commit()
    from shine.comfyops.llm_readiness import voice_readiness
    return {
        'config_path': str(path),
        'packages': config.routing(),
        'role_runtimes': {role: spec.model_dump(mode="json") for role, spec in config.runtimes().items()},
        'readiness': voice_readiness(db, settings),
    }
