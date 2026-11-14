"""New-machine deployment: versioned catalog, controlled plan assembly and generated configuration.

The catalog is configuration, not authority. Package registration, plan approval,
execution, instance lifecycle and workflow registration stay on the existing
controlled paths; this module only assembles plans from the pinned catalog and
generates the instance-bound configuration. Unverifiable origins stay blocked,
and no generated configuration ever claims runtime acceptance by itself.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text

from shine.comfy.workflow import graph_hash
from shine.comfyops.installer import TRUSTED_DOWNLOAD_HOSTS, PackageFile, PackageSpec, SourceWheelBuild, safe_target
from shine.comfyops.managed_recipe import adapter_identity as managed_recipe_adapter_identity
from shine.comfyops.models import (
    ComfyInstance,
    InstallationAttempt,
    InstallationPlan,
    ModelPackage,
    NodePackage,
)
from shine.config import Settings
from shine.models import WorkflowProfile
from shine.services import add_audit

REPO_ROOT = Path(__file__).resolve().parents[3]
ENV_PATH = REPO_ROOT / ".env"
CATALOG_DIR = REPO_ROOT / "config" / "deployment"
PROFILE_DIR = REPO_ROOT / "config" / "workflows"
ADAPTER_DIR = REPO_ROOT / "integrations" / "shine_comfy_handoff"
SAMPLING_NODE = REPO_ROOT / "integrations" / "shine_comfy_sampling_mode" / "shine_h3_sampling_mode.py"

MODEL_FAMILIES = ("diffusion_models", "vae", "loras", "text_encoders", "pdd_acc")
WHEEL_COMPONENT_ID = "comfy-runtime-wheels"


class CatalogFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str
    relative_path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size: int = Field(gt=0)
    redirect_hosts: list[str] = Field(default_factory=list)
    dependency: str | None = None
    version: str | None = None


class CatalogComponent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    component_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
    kind: Literal["source", "node", "wheel", "model", "review-model"]
    revision: str
    license_id: str
    usage: Literal["research", "commercial"]
    license_url: str = ""
    origin_repo: str = ""
    origin_revision: str = ""
    origin_kind: str = ""
    destination_family: str | None = None
    dependencies: list[str] = Field(default_factory=list)
    role: str | None = None
    #: What this component can be used for once it is installed.  A review
    #: model without its capability can never be bound to a native revision.
    capabilities: list[str] = Field(default_factory=list, max_length=30)
    #: The admission budget this component declares, in MiB.  It must be at
    #: least the local baseline for the role it serves.
    resource_mb: int | None = Field(default=None, ge=0)
    #: ``UNKNOWN`` means nothing was measured; ``ESTIMATED`` is a declared
    #: baseline, ``KNOWN`` a measured one.
    resource_measurement: str = Field(default="UNKNOWN", pattern=r"^(UNKNOWN|ESTIMATED|KNOWN)$")
    depends_on_wheels: bool = False
    files: list[CatalogFile] = Field(min_length=1, max_length=256)
    status: Literal["READY", "BLOCKED"] = "READY"
    blocked_reason: str = ""
    source_builds: list[SourceWheelBuild] = Field(default_factory=list, max_length=10)


class DeploymentCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")
    catalog_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,60}$")
    catalog_version: str
    schema_version: int
    generated: str
    platform: dict
    components: list[CatalogComponent] = Field(min_length=1)
    review_runtime_wheels: list[str] = Field(default_factory=list)
    workflow_profiles: list[str] = Field(default_factory=list)
    #: Profiles that are registered but not part of the default ready set.
    #: A profile whose weights or nodes are not closed stays here until the
    #: gap is filled and it has passed a real acceptance.
    deferred_workflow_profiles: list[dict] = Field(default_factory=list)
    #: The variant manifest whose thresholds and acceptance status govern
    #: this catalog.  It is the single source of the admission floors.
    variant_id: str | None = None
    dependency_policy: dict = Field(default_factory=dict)
    total_bytes: int = Field(ge=0)


def load_deployment_catalog(name: str = "h3-ref2va-windows-x64") -> dict:
    """Strictly validate the committed catalog and return the parsed dict."""
    path = CATALOG_DIR / f"{name}.json"
    if not path.is_file():
        raise HTTPException(404, "DEPLOYMENT_CATALOG_MISSING")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(409, "DEPLOYMENT_CATALOG_INVALID") from exc
    try:
        catalog = DeploymentCatalog.model_validate(raw)
    except ValueError as exc:
        raise HTTPException(409, f"DEPLOYMENT_CATALOG_INVALID: {exc}") from exc
    for component in catalog.components:
        if component.status != "READY":
            continue
        for file_entry in component.files:
            from shine.comfyops.installer import check_url
            try:
                check_url(file_entry.url, TRUSTED_DOWNLOAD_HOSTS)
            except ValueError as exc:
                raise HTTPException(409, f"UNTRUSTED_CATALOG_URL: {file_entry.url}") from exc
            try:
                safe_target(Path("_catalog_path_validation_root"), file_entry.relative_path)
            except ValueError as exc:
                raise HTTPException(409, f"UNSAFE_CATALOG_PATH: {file_entry.relative_path}") from exc
    return raw


def load_default_catalog_path() -> Path:
    return CATALOG_DIR / "h3-ref2va-windows-x64.json"


def load_default_catalog() -> dict:
    return load_deployment_catalog("h3-ref2va-windows-x64")


def _component_map(catalog: dict) -> dict:
    return {component["component_id"]: component for component in catalog["components"]}


def _resolve_closure(components: dict, requested: list[str]) -> list[str]:
    """Deterministic dependency closure, cycle-safe."""
    ordered: list[str] = []
    seen: set[str] = set()

    def visit(component_id: str, chain: tuple):
        if component_id in seen:
            return
        if component_id in chain:
            raise HTTPException(409, "CATALOG_DEPENDENCY_CYCLE")
        component = components.get(component_id)
        if not component:
            raise HTTPException(409, "CATALOG_COMPONENT_MISSING")
        for dep in component.get("dependencies", []):
            visit(dep, chain + (component_id,))
        seen.add(component_id)
        ordered.append(component_id)

    for component_id in requested:
        visit(component_id, ())
    return ordered


def _catalog_package_spec(component: dict, dependency_map: dict) -> PackageSpec:
    kind_map = {"source": "source", "node": "node", "wheel": "wheel", "model": "model", "review-model": "model"}
    files = [
        PackageFile(
            url=f["url"],
            relative_path=f["relative_path"],
            sha256=f["sha256"],
            size=f["size"],
            redirect_hosts=f.get("redirect_hosts") or [],
        )
        for f in component["files"]
    ]
    return PackageSpec(
        name=component["component_id"],
        kind=kind_map[component["kind"]],
        revision=component["revision"],
        license_id=component["license_id"],
        usage=component["usage"],
        license_url=component.get("license_url", ""),
        family=component.get("destination_family") or component.get("role") or "UNSPECIFIED",
        capabilities=list(component.get("capabilities") or []),
        resource_mb=component.get("resource_mb"),
        resource_measurement=component.get("resource_measurement", "UNKNOWN"),
        files=files,
        dependencies=dependency_map,
        runtime_versions={"python": "3.12"},
        source_builds=component.get("source_builds", []),
    )


def _package_class(kind: str):
    return ModelPackage if kind == "model" else NodePackage


#: The component groups the download confirmation screen talks about.  The
#: optional advisory reviewers are never part of the default production
#: download, so the two are never written as one "total".
COMPONENT_GROUP_PRODUCTION = "default_production"
COMPONENT_GROUP_OPTIONAL_REVIEW = "optional_review"
COMPONENT_GROUP_MODEL = "model"


def component_groups(catalog: dict) -> dict:
    """Split a catalog into the default production parts and the optional ones.

    The model group is the weight bytes on their own: it is the number the
    user compares between variants, and it is always smaller than the whole
    catalog because the runtime (ComfyUI source, wheels, nodes) is not a
    weight.
    """
    production: list[str] = []
    optional: list[str] = []
    models: list[str] = []
    for component in catalog.get("components", []):
        if component.get("kind") == "review-model" or component.get("role") in {"VLM", "ASR"}:
            optional.append(component["component_id"])
        else:
            production.append(component["component_id"])
        if component.get("kind") == "model":
            models.append(component["component_id"])
    return {
        COMPONENT_GROUP_PRODUCTION: production,
        COMPONENT_GROUP_OPTIONAL_REVIEW: optional,
        COMPONENT_GROUP_MODEL: models,
    }


def _component_bytes(catalog: dict, component_ids) -> int:
    wanted = set(component_ids or [])
    return sum(
        int(file.get("size") or 0)
        for component in catalog.get("components", [])
        if component.get("component_id") in wanted
        for file in component.get("files", [])
    )


def download_breakdown(catalog: dict, requested: list[str] | None = None,
                       *, disk_free_bytes: int | None = None) -> dict:
    """The download size of a selection, split by component group.

    There is no single hard-coded "total": the complete catalog, the default
    production components and the optional advisory components are three
    different numbers, and the selected set is a fourth one.
    """
    groups = component_groups(catalog)
    production_bytes = _component_bytes(catalog, groups[COMPONENT_GROUP_PRODUCTION])
    optional_bytes = _component_bytes(catalog, groups[COMPONENT_GROUP_OPTIONAL_REVIEW])
    model_bytes = _component_bytes(catalog, groups[COMPONENT_GROUP_MODEL])
    selected = list(requested) if requested is not None else list(groups[COMPONENT_GROUP_PRODUCTION])
    selected_bytes = _component_bytes(catalog, selected)
    complete = int(catalog.get("total_bytes") or (production_bytes + optional_bytes))
    breakdown = {
        "complete_catalog_bytes": complete,
        "default_production_bytes": production_bytes,
        "optional_review_bytes": optional_bytes,
        "model_bytes": model_bytes,
        "runtime_bytes": max(0, production_bytes - model_bytes),
        "selected_bytes": selected_bytes,
        "selected_components": sorted(selected),
        "optional_review_components": sorted(groups[COMPONENT_GROUP_OPTIONAL_REVIEW]),
        "model_components": sorted(groups[COMPONENT_GROUP_MODEL]),
    }
    if disk_free_bytes is not None:
        breakdown["free_bytes"] = int(disk_free_bytes)
        breakdown["free_after_bytes"] = int(disk_free_bytes) - selected_bytes
    return breakdown


def default_variant_requirements() -> dict:
    """The admission thresholds of the default production variant."""
    from shine.comfyops.variants import default_variant_id, load_variant_manifest, variant_requirements

    try:
        return variant_requirements(load_variant_manifest(default_variant_id()))
    except (OSError, ValueError, FileNotFoundError):
        return {}


def resolve_variant(catalog: dict, requested: list[str]) -> dict | None:
    """The variant a plan installs, or None when the plan is component-only.

    A plan binds a variant when it carries that variant's checkpoint
    components.  A candidate variant is never bound automatically: its files
    live in their own catalog and it is never part of the default download.
    """
    from shine.comfyops.variants import list_variant_manifests

    wanted = set(requested or [])
    for manifest in list_variant_manifests():
        required = set(manifest.get("required_component_ids") or [])
        if required and required <= wanted:
            return manifest
    return None


def _pinned_files(payload: dict) -> list[tuple[str, str, int]]:
    """The file identity of a package payload, independent of its declaration."""
    return sorted(
        (str(f.get("relative_path", "")), str(f.get("sha256", "")), int(f.get("size", 0)))
        for f in (payload.get("files") or [])
    )


#: The declaration fields that may change without a new content identity.
#: Everything else - revision, licence, usage, dependencies, the pinned files -
#: is content: a different value there is a different component.
DECLARATION_FIELDS = ("capabilities", "resource_mb", "resource_measurement")


def _declaration_delta(previous: dict, current: dict) -> list[str]:
    """The declaration keys that changed while the pinned files stayed the same."""
    return sorted(
        key for key in set(previous) | set(current)
        if key in DECLARATION_FIELDS and previous.get(key) != current.get(key)
    )


def _declaration_only_change(previous: dict, current: dict) -> bool:
    """True when the change is a declaration, not a content change."""
    if _pinned_files(previous) != _pinned_files(current):
        return False
    return all(
        previous.get(key) == current.get(key)
        for key in set(previous) | set(current)
        if key not in DECLARATION_FIELDS
    )


def register_catalog_packages(db, catalog: dict, requested: list[str]) -> dict:
    """Idempotent, content-addressed package registration from the catalog.

    Returns {component_id: package_row}. A component whose content changed
    since registration raises CATALOG_COMPONENT_CHANGED instead of silently
    reusing the old identity.
    """
    components = _component_map(catalog)
    ordered = _resolve_closure(components, requested)
    rows = {}
    for component_id in ordered:
        component = components[component_id]
        dependency_map = {}
        for dep_id in component.get("dependencies", []):
            dep_row = rows[dep_id]
            dependency_map[dep_row.id] = dep_row.payload["revision"]
        spec = _catalog_package_spec(component, dependency_map)
        payload = spec.model_dump()
        identity = graph_hash(payload)
        existing = (
            db.query(ModelPackage).filter(ModelPackage.id == component_id).first()
            or db.query(NodePackage).filter(NodePackage.id == component_id).first()
        )
        if existing is not None:
            if graph_hash(existing.payload) != identity:
                if not _declaration_only_change(existing.payload, payload):
                    raise HTTPException(409, "CATALOG_COMPONENT_CHANGED")
                # The pinned files are byte-identical and only the declaration
                # changed (the capability and the declared admission budget).
                # The verified files stay verified and the package gains the
                # new declaration under a new identity.
                previous = existing.payload
                changed = _declaration_delta(previous, payload)
                existing.payload = payload
                existing.sha256 = identity
                add_audit(db, "comfyops", "PACKAGE_DECLARATION_UPDATED", "package", existing.id,
                          {"component": component_id, "changed": changed,
                           "previous_sha256": graph_hash(previous), "sha256": identity},
                          project_id=None)
            rows[component_id] = existing
            continue
        cls = _package_class(spec.kind)
        row = cls(id=component_id, payload=payload, sha256=identity, status="UNVERIFIED_ORIGIN")
        db.add(row)
        db.flush()
        add_audit(db, "comfyops", "PACKAGE_REGISTERED_FROM_CATALOG", "package", row.id,
                  {"catalog": catalog["catalog_id"], "component": component_id}, project_id=None)
        rows[component_id] = row
    return rows


def target_identity(storage_root: str | None, executor: str | None, packages: list[dict]) -> str:
    """The physical work identity: one directory, one component closure.

    A plan is not the owner of an installation — the target it writes is.  The
    identity is deliberately independent of project id, plan id and approval
    timestamps so two tabs or two projects that ask for the same bytes in the
    same directory resolve to the same physical work.
    """
    from shine.comfyops.installation import normalized_storage_root

    return graph_hash({
        "storage_root": normalized_storage_root(storage_root),
        "executor": executor,
        "packages": [
            {"id": package.get("id"), "hash": package.get("hash")}
            for package in packages
        ],
    })


def create_install_plan(db, settings: Settings, catalog: dict, requested: list[str],
                        project_id: str, disk_free: int, *,
                        variant: dict | None = None,
                        qualification: dict | None = None,
                        configuration: dict | None = None,
                        resource_context: dict | None = None) -> InstallationPlan:
    """Assemble a LOCAL_ISOLATED install plan from the pinned catalog.

    Reuses the same payload contract as the manual /comfyops/install-plans path,
    so approval and execution remain the existing controlled endpoints.

    The variant and its host qualification are recorded in the plan payload:
    a plan that installs a production variant always says which variant it
    installs and what the host was measured to be.
    """
    _require_project(db, project_id)
    from shine.comfyops.storage import disk_info, require_storage
    storage_root = require_storage(settings)
    components = _component_map(catalog)
    ordered = _resolve_closure(components, requested)
    specs = [
        _catalog_package_spec(components[component_id], {
            dep: components[dep]["revision"] for dep in components[component_id].get("dependencies", [])
        }).model_dump()
        for component_id in ordered
    ]
    files = [f for spec in specs for f in spec["files"]]
    size = sum(f["size"] for f in files)
    from shine.comfyops.installer import ControlledDownloader

    # File hashing can take minutes. Do it before package registration opens
    # a SQLite writer transaction, so Worker heartbeats remain unblocked.
    cache = storage_root / 'isolated-packages' / '.cache'
    cached = [f for f in files if (cache / f['sha256']).is_file()
              and ControlledDownloader.valid(cache / f['sha256'], PackageFile.model_validate(f))]
    cached_bytes = sum(f['size'] for f in cached)
    if resource_context is not None:
        cached_ids = {(f['sha256'], f['size']) for f in cached}
        resource_context = {**resource_context, "authorized_download_files": sorted(
            ({"sha256": f["sha256"], "size": f["size"]} for f in files
             if (f["sha256"], f["size"]) not in cached_ids),
            key=lambda item: (item["sha256"], item["size"]),
        )}
    reserve = int(catalog.get('dependency_policy', {}).get('runtime_disk', {}).get('runtime_reserve_bytes', 0))
    if WHEEL_COMPONENT_ID not in ordered:
        reserve = 0
    disk_required = size * 2 + reserve
    if disk_required > disk_free:
        raise HTTPException(409, "INSUFFICIENT_DISK")
    if disk_info(storage_root)['free_bytes'] < disk_required:
        raise HTTPException(409, "INSUFFICIENT_DISK_ON_HOST")
    rows = register_catalog_packages(db, catalog, requested)
    packages = [{"id": rows[component_id].id, "hash": rows[component_id].sha256,
                 "spec": rows[component_id].payload} for component_id in ordered]
    payload = {
        "packages": packages,
        "download_bytes": size - cached_bytes,
        "cached_bytes": cached_bytes,
        "disk_required": disk_required,
        "runtime_reserve_bytes": reserve,
        "storage_root": str(storage_root),
        "isolated": True,
        "executor": "LOCAL_ISOLATED",
        "risks": ["THIRD_PARTY_CODE" if p["spec"]["kind"] in {"node", "source", "wheel"}
                  else "MODEL_LICENSE" for p in packages],
        "deployment": {"catalog_id": catalog["catalog_id"], "catalog_version": catalog["catalog_version"]},
        "variant": {
            "variant_id": (variant or {}).get("variant_id"),
            "acceptance_status": (variant or {}).get("acceptance_status"),
            "auto_download_allowed": bool((variant or {}).get("auto_download_allowed", False)),
        } if variant else None,
        "qualification": qualification,
        "download_breakdown": download_breakdown(catalog, requested),
        # The chosen combination is part of the approved payload: the person
        # approving the plan approves the same configuration the card showed.
        "configuration": configuration or {"h3_video": True, "image_prep": False},
    }
    if resource_context is not None:
        payload["resource_context"] = resource_context
    payload["target_key"] = target_identity(
        payload["storage_root"], payload["executor"], payload["packages"]
    )
    # Repeating the same plan in another tab returns the plan that is already
    # awaiting approval instead of creating a second row for the same work.
    # An approved plan keeps its own approval; a new request that arrives after
    # approval is resolved by the attempt-level target ownership below.
    existing_plan = next(
        (
            row
            for row in db.scalars(
                select(InstallationPlan).where(
                    InstallationPlan.project_id == project_id,
                    InstallationPlan.status == "AWAITING_APPROVAL",
                )
            ).all()
            if (row.payload or {}).get("target_key") == payload["target_key"]
            and (row.payload or {}).get("configuration") == payload["configuration"]
            and (row.payload or {}).get("resource_context") == payload.get("resource_context")
            # A cache that changed since the plan was assembled changes the
            # download this approval covers; reusing the stale number would
            # approve bytes the host no longer has to fetch.
            and (row.payload or {}).get("download_bytes") == payload["download_bytes"]
            and (row.payload or {}).get("cached_bytes") == payload["cached_bytes"]
        ),
        None,
    )
    if existing_plan is not None:
        add_audit(db, "comfyops", "DEPLOYMENT_INSTALL_PLAN_REUSED", "installation_plan", existing_plan.id,
                  {"hash": existing_plan.sha256, "target_key": payload["target_key"]},
                  project_id=project_id)
        db.commit()
        return existing_plan
    row = InstallationPlan(
        project_id=project_id, payload=payload, sha256=graph_hash(payload), status="AWAITING_APPROVAL"
    )
    db.add(row)
    db.flush()
    add_audit(db, "comfyops", "DEPLOYMENT_INSTALL_PLAN_CREATED", "installation_plan", row.id,
              {"hash": row.sha256, "packages": len(packages)}, project_id=project_id)
    db.commit()
    return row


def _require_project(db, project_id: str):
    from shine.services import require_project as svc_require_project
    return svc_require_project(db, project_id)


def next_instance_plan(db, settings: Settings, catalog: dict, project_id: str,
                       *, instance_name: str, port: int, mode: str = "local"):
    """Create the NEXT controlled instance plan based on current instance state.

    The existing lifecycle requires each plan to be approved and executed before
    the next can even be planned (the instance row appears after CREATE runs),
    so a batch "chain" is impossible by design. Returns (plan, action) or
    (None, action) when the chain is complete.
    """
    _require_project(db, project_id)
    from shine.comfyops.instances import InstanceOperation, instance_identity, instance_root, plan_operation
    components = _component_map(catalog)
    expected_nodes = {cid: c['revision'] for cid, c in components.items() if c['kind'] == 'node'}
    expected_bindings = {c['destination_family']: cid for cid, c in components.items()
                         if c['kind'] == 'model' and c.get('destination_family')}
    instance = db.get(ComfyInstance, instance_identity(mode, port))
    if instance is None and any(
            row.payload.get('managed') and row.payload.get('root') == str(instance_root(settings, instance_name, mode))
            for row in db.scalars(select(ComfyInstance))):
        raise HTTPException(409, 'INSTANCE_IDENTITY_CHANGED')
    if instance and (not instance.payload.get('managed') or instance.payload.get('name') != instance_name
                     or instance.payload.get('port') != port
                     or bool(instance.payload.get('is_mock')) != (mode == 'fake')
                     or Path(instance.payload.get('root', '')).resolve()
                     != instance_root(settings, instance_name, mode).resolve()):
        raise HTTPException(409, 'INSTANCE_IDENTITY_CHANGED')
    if instance and instance.status not in {'STOPPED', 'MOCK_VERIFIED', 'RUNNING_PENDING_HEALTH'}:
        raise HTTPException(409, 'INSTANCE_REQUIRES_RECONCILIATION')
    installed_nodes = instance.payload.get('node_versions', {}) if instance else {}
    if any(cid in installed_nodes and installed_nodes[cid] != revision for cid, revision in expected_nodes.items()):
        raise HTTPException(409, 'PINNED_NODE_REVISION_CHANGED')
    missing_nodes = [cid for cid in expected_nodes if cid not in installed_nodes]
    bindings = instance.payload.get('model_bindings', {}) if instance else {}
    if any(family not in expected_bindings or expected_bindings[family] != package_id
           for family, package_id in bindings.items()):
        raise HTTPException(409, 'INSTANCE_MODEL_BINDINGS_CHANGED')
    if instance is None:
        action = "CREATE"
    elif instance.payload.get('process'):
        # A previous setup version started naked main.py here. Stop only its
        # recorded owned process through the approved lifecycle before mutation.
        action = 'STOP'
    elif missing_nodes:
        action = "INSTALL_NODE"
    elif bindings != expected_bindings:
        action = "BIND_MODELS"
    else:
        # Production owns the first fenced startup and GPU admission. An ordinary
        # START here would keep the same port occupied and bypass that boundary.
        return None, None
    # Resume the same durable intent after a closed/disconnected wizard. Creating
    # another plan must not provide a route around an ambiguous execution.
    pending = list(db.scalars(select(InstallationPlan).where(
        InstallationPlan.project_id == project_id,
        InstallationPlan.status.in_(['AWAITING_APPROVAL', 'APPROVED', 'INSTALLING', 'NEEDS_REVIEW']),
    )))
    for existing in pending:
        op = existing.payload.get('operation', {})
        if (existing.payload.get('kind') == 'INSTANCE_LIFECYCLE'
                and op.get('name') == instance_name and op.get('port') == port and op.get('mode') == mode):
            if existing.status in {'INSTALLING', 'NEEDS_REVIEW'}:
                raise HTTPException(409, 'INSTANCE_REQUIRES_RECONCILIATION')
            if op.get('action') == action:
                return existing, action
    rows = register_catalog_packages(db, catalog, [
        "comfyui", WHEEL_COMPONENT_ID,
        *[cid for cid, c in components.items() if c["kind"] == "node"],
        *[cid for cid, c in components.items()
          if c["kind"] == "model" and c.get("destination_family")],
    ])
    # risk_confirmed is the operator's explicit L2/L3 acknowledgment flag; the
    # human approval itself still happens per plan through the grant machinery.
    risk = True
    if action == "CREATE":
        plan = plan_operation(db, settings, InstanceOperation(
            project_id=project_id, name=instance_name, action=action, mode=mode, port=port,
            source_package_id=rows["comfyui"].id, package_ids=[rows[WHEEL_COMPONENT_ID].id],
            risk_confirmed=risk))
    elif action == "INSTALL_NODE":
        plan = plan_operation(db, settings, InstanceOperation(
            project_id=project_id, name=instance_name, action=action, mode=mode, port=port,
            package_ids=[rows[WHEEL_COMPONENT_ID].id,
                         *[rows[cid].id for cid in missing_nodes]],
            risk_confirmed=risk))
    elif action == "BIND_MODELS":
        plan = plan_operation(db, settings, InstanceOperation(
            project_id=project_id, name=instance_name, action=action, mode=mode, port=port,
            model_bindings={c["destination_family"]: rows[cid].id
                            for cid, c in components.items()
                            if c["kind"] == "model" and c.get("destination_family")},
            risk_confirmed=risk))
    else:
        plan = plan_operation(db, settings, InstanceOperation(
            project_id=project_id, name=instance_name, action=action, mode=mode, port=port,
            risk_confirmed=risk))
    db.commit()
    return plan, action


def _deploy_repo_file(source: Path, target: Path, *, what: str) -> dict:
    """Idempotent repo-file deployment: same bytes = no-op, changed bytes = error."""
    if not source.is_file():
        raise HTTPException(409, f"REPO_FILE_MISSING: {what}")
    payload = source.read_bytes()
    if target.exists():
        if target.read_bytes() == payload:
            return {"path": str(target), "status": "ALREADY_DEPLOYED"}
        raise HTTPException(409, f"{what}_CHANGED")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return {"path": str(target), "status": "DEPLOYED"}


REVIEW_PORTS = {"VLM": 8116, "ASR": 8117}


REVIEW_COMPONENTS = {"VLM": "qwen3-vl-8b-fp8", "ASR": "qwen3-asr-0.6b"}


def advisory_review_readiness(settings: Settings) -> dict:
    """Read-only inventory for optional local review resources.

    Review models are suggestions, not prerequisites for H3 production. This
    report checks only whether the pinned files and small runtime packages are
    already present; it never downloads, copies, hashes multi-GB weights, or
    changes activation readiness.
    """
    try:
        catalog = load_default_catalog()
        components = _component_map(catalog)
        lock_path = REPO_ROOT / "config" / "review-decoder-lock.json"
        decoder_lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError, TypeError):
        return {
            role: {"status": "UNKNOWN", "model_id": component_id, "advisory": True,
                   "code": "REVIEW_RESOURCE_INVENTORY_UNAVAILABLE"}
            for role, component_id in REVIEW_COMPONENTS.items()
        }

    runtime_root = settings.comfyops_dir / "setup" / "review-runtime"
    result = {}
    for role, component_id in REVIEW_COMPONENTS.items():
        component = components.get(component_id)
        files = component.get("files", []) if component else []
        model_root = (settings.comfyops_dir / "isolated-packages" / component_id).resolve()
        present = 0
        for item in files:
            relative = Path(item.get("relative_path", ""))
            try:
                target = (model_root / relative).resolve(strict=True)
                valid = (not relative.is_absolute() and ".." not in relative.parts
                         and target.is_relative_to(model_root) and target.is_file()
                         and target.stat().st_size == item.get("size"))
            except (OSError, ValueError, TypeError):
                valid = False
            present += int(valid)

        if role == "VLM":
            runtime_roots = [root for package in decoder_lock.get("packages", [])
                             for root in package.get("roots", [])]
        else:
            runtime_roots = ["qwen_asr", "qwen_asr-0.0.6.dist-info"]
        runtime_missing = sorted(name for name in runtime_roots if not (runtime_root / name).is_dir())
        model_ready = bool(files) and present == len(files)
        runtime_ready = not runtime_missing
        result[role] = {
            "status": "READY" if model_ready and runtime_ready else "NOT_READY",
            "model_id": component_id,
            "advisory": True,
            "model_files_present": present,
            "model_files_expected": len(files),
            "runtime_ready": runtime_ready,
            "runtime_packages_missing": [Path(name).name.split("-")[0] for name in runtime_missing],
            "code": None if model_ready and runtime_ready else "ADVISORY_REVIEW_RESOURCES_NOT_READY",
        }
    return result


def generate_configuration(db, settings: Settings, project_id: str, *,
                           instance_name: str = 'shine-h3', port: int = 8188,
                           variant_id: str | None = None) -> dict:
    """Generate the instance-bound managed recipe, review manifests and .env entries.

    Everything is derived from the deployed isolated instance and the repo, so a
    new machine needs no personal paths, GPU UUID hardcoding or hand-edited JSON.
    ``variant_id`` selects the startup contract whose memory floors the recipe
    carries; without it the default production variant is used, unchanged.
    """
    from shine.comfyops.instances import instance_identity, instance_root

    # The variant contract is resolved before any transaction: an undetermined
    # startup (unknown render-time floors) is refused by name here and never
    # written into the recipe with the baseline's numbers.
    if variant_id is None:
        requirements = default_variant_requirements()
    else:
        from shine.comfyops.variant_recipe import build_startup_contract
        contract = build_startup_contract(variant_id)
        admission = contract["admission"]
        if not admission["startup_determined"]:
            raise HTTPException(409, {
                "code": "VARIANT_STARTUP_CONTRACT_NOT_DETERMINED",
                "variant_id": variant_id,
                "contract_sha256": contract["contract_sha256"],
                "blockers": admission["blockers"],
                "missing_upstream_evidence": contract["missing_upstream_evidence"],
            })
        requirements = contract["memory_facts"]

    # Serialize with resource admission and instance lifecycle intent. A lease
    # timeout does not prove that a renderer has stopped.
    db.execute(text('BEGIN IMMEDIATE'))
    _require_project(db, project_id)
    instance = db.get(ComfyInstance, instance_identity('local', port))
    adopted = bool(instance and instance.payload.get('adopted'))
    if (instance is None or not instance.payload.get('managed') or instance.payload.get('is_mock')
            or instance.payload.get('name') != instance_name
            or instance.payload.get('port') != port
            or (not adopted and Path(instance.payload.get('root', '')).resolve()
                != instance_root(settings, instance_name, 'local').resolve())):
        raise HTTPException(409, "MANAGED_INSTANCE_REQUIRED")
    payload = instance.payload
    if payload.get('process') or instance.status != 'STOPPED':
        raise HTTPException(409, 'STOP_OWNED_INSTANCE_BEFORE_CONFIGURATION')
    from shine.comfyops.runtime_configuration import require_idle_configuration
    require_idle_configuration(db)
    root = Path(payload["root"]).resolve()
    if adopted:
        from shine.comfyops.adoption import adopted_installation_blockers
        blockers = adopted_installation_blockers(instance, load_default_catalog())
        if blockers:
            raise HTTPException(409, {"code": "ADOPTED_INSTALLATION_CHANGED", "blockers": blockers})
        python = Path(payload["python"]).resolve()
    else:
        python = root / ".venv" / "Scripts" / "python.exe"
    if not python.is_file():
        raise HTTPException(409, "INSTANCE_VENV_MISSING")
    venv_python = str(python.resolve())
    # 1) Prepare the handoff adapter. Adoption only validates existing bytes;
    # it never writes into the user's ComfyUI tree.
    adapter_dir = root / "custom_nodes" / "shine_comfy_handoff"
    results = []
    if adopted:
        if managed_recipe_adapter_identity(adapter_dir) != managed_recipe_adapter_identity(ADAPTER_DIR):
            raise HTTPException(409, "EXISTING_COMFY_HANDOFF_ADAPTER_MISMATCH")
        results.append({"status": "REUSED_EXISTING", "kind": "HANDOFF_ADAPTER"})
        if not (root / "custom_nodes" / "shine_h3_sampling_mode.py").is_file():
            raise HTTPException(409, "EXISTING_COMFY_SAMPLING_NODE_MISSING")
        results.append({"status": "REUSED_EXISTING", "kind": "SAMPLING_NODE"})
    else:
        for name in ("__init__.py", "barrier.py"):
            results.append(_deploy_repo_file(ADAPTER_DIR / name, adapter_dir / name, what="HANDOFF_ADAPTER"))
        results.append(_deploy_repo_file(SAMPLING_NODE, root / "custom_nodes" / "shine_h3_sampling_mode.py",
                                         what="SAMPLING_NODE"))
    # 2) GPU identity from the live probe — a generated recipe without the real
    # UUID would fail the renderer admission check at first production start.
    from shine.comfyops.host_env import probe_gpu
    gpu = probe_gpu()
    if gpu.get("status") != "OK":
        raise HTTPException(409, f"GPU_UUID_REQUIRED: {gpu.get('reason')}")
    SEP = os.sep
    setup_dir = settings.comfyops_dir / "setup"
    (setup_dir / "manifests").mkdir(parents=True, exist_ok=True)
    (setup_dir / "review-runtime").mkdir(parents=True, exist_ok=True)
    runtime_data_root = (settings.comfyops_dir / "adopted-runtime" / graph_hash({
        "root": str(root), "instance_id": instance.id,
    })[:24]) if adopted else root
    if adopted:
        for folder in ("user/default/ComfyUI-Manager", "input", "output", "temp"):
            (runtime_data_root / folder).mkdir(parents=True, exist_ok=True)
        # An isolated offline Manager profile prevents catalog/network activity
        # and keeps the user's own ComfyUI user directory untouched.
        manager_config = runtime_data_root / "user/default/ComfyUI-Manager/config.ini"
        if not manager_config.exists():
            manager_config.write_text("[default]\nnetwork_mode=offline\nbypass_ssl=false\nsecurity_level=strong\n",
                                     encoding="utf-8")
    recipe_path = setup_dir / "managed_recipe.json"
    adapter_identity = managed_recipe_adapter_identity(adapter_dir)
    whitelist = ["shine_comfy_handoff", "shine_h3_sampling_mode.py"]
    node_packs = [cid for cid, c in _component_map(load_default_catalog()).items() if c["kind"] == "node"]
    whitelist.extend(sorted(node_packs))
    argv = [
        "{python}", str(REPO_ROOT / "scripts" / "managed_comfy_bootstrap.py"),
        "--listen", "127.0.0.1", "--port", "{port}",
    ]
    if adopted:
        argv.extend([
            "--user-directory", "{runtime_data_root}" + SEP + "user",
            "--input-directory", "{runtime_data_root}" + SEP + "input",
            "--output-directory", "{runtime_data_root}" + SEP + "output",
            "--temp-directory", "{runtime_data_root}" + SEP + "temp",
        ])
    argv.extend([
        "--disable-async-offload", "--disable-pinned-memory",
        "--reserve-vram", "1",
        "--fast", "fp16_accumulation",
        "--disable-all-custom-nodes", "--whitelist-custom-nodes", *whitelist,
    ])
    renderer = {
        "instance_id": instance.id,
        "installation": {"id": instance.id, "sha256": instance.sha256},
        "python": venv_python,
        "root": str(root),
        "base_directory": str(root),
        "runtime_data_root": str(runtime_data_root.resolve()),
        "bootstrap": str(REPO_ROOT / "scripts" / "managed_comfy_bootstrap.py"),
        "adapter_dir": str(adapter_dir),
        "adapter_sha256": adapter_identity,
        "gpu_uuid": gpu["uuid"],
        "port": int(payload["port"]),
        # The production start measures the *available* memory of this one
        # card.  Its floors come from the variant manifest, so the renderer
        # admission and the deployment recommendation cannot disagree.
        "min_ram_mb": int(requirements.get("min_free_ram_mb") or 8192),
        "min_vram_mb": int(requirements.get("min_free_vram_mb") or 12000),
        "argv": argv,
        "pinned_files": [
            "{python}", "{root}" + SEP + "main.py", "{adapter_dir}" + SEP + "__init__.py",
            "{adapter_dir}" + SEP + "barrier.py", "{root}" + SEP + "custom_nodes" + SEP
            + "shine_h3_sampling_mode.py", "{bootstrap}",
        ],
    }
    reviews = {}
    catalog = load_default_catalog()
    components = _component_map(catalog)
    for role, component_id in REVIEW_COMPONENTS.items():
        component = components[component_id]
        manifest = {
            "model_root": str((settings.comfyops_dir / "isolated-packages" / component_id).resolve()),
            "model_id": component_id,
            "license_id": component["license_id"],
            "files": [
                {"path": f["relative_path"], "sha256": f["sha256"], "bytes": f["size"]}
                for f in component["files"]
            ],
        }
        if role == "ASR":
            manifest = {**manifest, "model_id": REVIEW_COMPONENTS["VLM"],
                        "model_root": str((settings.comfyops_dir / "isolated-packages"
                                           / REVIEW_COMPONENTS["VLM"]).resolve()),
                        "files": [{"path": f["relative_path"], "sha256": f["sha256"], "bytes": f["size"]}
                                  for f in components[REVIEW_COMPONENTS["VLM"]]["files"]],
                        "asr": {**manifest, "executor": "qwen3-asr",
                                "runtime_packages": str(root / ".venv" / "Lib" / "site-packages")}}
        manifest["sha256"] = graph_hash(manifest)
        manifest_path = setup_dir / "manifests" / (role.lower() + ".json")
        from shine.comfyops.installer import durable_json
        durable_json(manifest_path, manifest)
        reviews[role] = {
            "service_id": "shine-local-" + role.lower(),
            "python": venv_python,
            "manifest": str(manifest_path),
            "runtime_packages": str(setup_dir / "review-runtime"),
            "port": REVIEW_PORTS[role],
            "min_ram_mb": 4096 if role == "VLM" else 2048,
            "min_vram_mb": 8000 if role == "VLM" else 4000,
            "argv": [
                "{python}", str(REPO_ROOT / "scripts" / "local_review_isolated_server.py"),
                "--manifest", "{manifest}", "--runtime-packages", "{runtime_packages}",
                "--port", "{port}",
            ],
        }


    # 3) Write the recipe and update .env (no secrets ever go through here).
    recipe = {"renderer": renderer, "reviews": reviews}
    from shine.comfyops.installer import durable_json
    durable_json(recipe_path, recipe)
    from shine.comfyops.runtime_configuration import record_candidate
    record_candidate(db, settings, project_id, recipe_path)
    env_updates = {
        "SHINE_DATA_DIR": str(settings.data_dir),
        "SHINE_SETUP_RECIPE": str(recipe_path),
        "SHINE_ENABLE_LOCAL_EXECUTION": "1",
    }
    env_written = _update_env_file(ENV_PATH, env_updates)
    add_audit(db, "comfyops", "DEPLOYMENT_CONFIGURATION_GENERATED", "comfy_instance", instance.id, {
        "recipe": str(recipe_path), "adapter_sha256": adapter_identity,
        "gpu_uuid": gpu["uuid"], "env_keys": sorted(env_updates),
    }, project_id=project_id)
    db.commit()
    return {
        "recipe": str(recipe_path),
        "recipe_sha256": graph_hash(recipe),
        "activation_required": True,
        "adapter_sha256": adapter_identity,
        "gpu_uuid": gpu["uuid"],
        "env_file": str(ENV_PATH),
        "env_keys": sorted(env_written),
        "deployed_files": results,
        "review_manifests": {role: str(setup_dir / "manifests" / (role.lower() + ".json")) for role in reviews},
    }


def persist_env(updates: dict, env_path: Path | None = None) -> list:
    """Persist host-local settings into .env; the single writer for this file."""
    return _update_env_file(env_path or ENV_PATH, updates)


def _update_env_file(env_path: Path, updates: dict) -> list:
    """Update or create the repo .env; never touches values for keys it does not own."""
    existing_lines = []
    if env_path.exists():
        existing_lines = env_path.read_text(encoding="utf-8").splitlines()
    handled = set()
    result = []
    for line in existing_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            result.append(line)
        else:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                result.append(f"{key}={updates[key]}")
                handled.add(key)
            else:
                result.append(line)
    for key, value in updates.items():
        if key not in handled:
            result.append(f"{key}={value}")
    env_path.write_text("\n".join(result) + "\n", encoding="utf-8")
    return sorted(handled | set(updates))


def installed_instance_blockers(db, settings, instance, catalog):
    components = _component_map(catalog)
    install_blockers = []
    if instance is None:
        install_blockers.append("REAL_INSTANCE_MISSING_OR_AMBIGUOUS")
    else:
        payload = instance.payload
        root = Path(payload.get("root", "")).resolve()
        attempt = db.get(InstallationAttempt, payload.get("last_attempt")) if payload.get("last_attempt") else None
        if (instance.status not in {"STOPPED", "RUNNING_PENDING_HEALTH"}
                or instance.sha256 != graph_hash(payload)
                or not attempt or attempt.status != "COMPLETED"
                or attempt.sha256 != graph_hash(attempt.payload)
                or attempt.payload.get("is_mock", True)
                or attempt.payload.get("result") != payload):
            install_blockers.append("COMPLETED_REAL_INSTALL_EVIDENCE_REQUIRED")
        if payload.get("adopted"):
            from shine.comfyops.adoption import adopted_installation_blockers

            if (payload.get("external_installation") is not True
                    or payload.get("status_origin") != "USER_CONFIRMED_EXISTING_INSTALLATION"):
                install_blockers.append("EXISTING_INSTALL_CONFIRMATION_REQUIRED")
            install_blockers.extend(adopted_installation_blockers(instance, catalog))
            return sorted(set(install_blockers))
        if (not root.is_relative_to((settings.comfyops_dir / "instances").resolve())
                or not (root / ".venv/Scripts/python.exe").is_file()
                or not (root / "main.py").is_file()
                or not (root / ".shine-requirements.txt").is_file()
                or not payload.get("dependency_hash")):
            install_blockers.append("INSTALLED_RUNTIME_FILES_MISSING")
        expected_nodes = {cid: c["revision"] for cid, c in components.items() if c["kind"] == "node"}
        if any(payload.get("node_versions", {}).get(cid) != revision for cid, revision in expected_nodes.items()):
            install_blockers.append("PINNED_NODE_INSTALL_INCOMPLETE")
        expected_bindings = {c['destination_family']: cid for cid, c in components.items()
                             if c['kind'] == 'model' and c.get('destination_family')}
        if payload.get('model_bindings') != expected_bindings:
            install_blockers.append('PINNED_MODEL_BINDINGS_INCOMPLETE')
    return install_blockers


def _verified_h3_key(item: dict) -> tuple[str, str, str, int]:
    return item.get("component_id"), item.get("relative_path"), item.get("sha256"), item.get("size")


def deployment_readiness(db, settings: Settings) -> dict:
    """Layered readiness: never lets a lower layer stand in for a higher one.

    0 tools (bootstrap manifest), 1 catalog loaded, 2 files verified,
    3 environment installed, 4 services + node contracts, 5 real inference.
    Layer 5 only ever comes from real (is_mock=false) production evidence; it
    is never inferred from downloads, object_info or fake runs.
    """
    from shine.comfyops.host_env import probe_host_environment, setup_blockers

    catalog = load_default_catalog()
    components = _component_map(catalog)
    layers = []
    env_report = probe_host_environment(settings.data_dir, deployment_dir=settings.comfyops_dir)
    blockers = setup_blockers(env_report)
    layers.append({
        "layer": "L0_host_tools",
        "status": "READY" if not blockers else "BLOCKED",
        "blockers": blockers,
    })

    def package_layer_status(component_ids: list) -> dict:
        rows = {}
        for component_id in component_ids:
            row = db.get(ModelPackage, component_id) or db.get(NodePackage, component_id)
            rows[component_id] = row.status if row else None
        statuses = set(rows.values())
        if statuses == {"FILES_VERIFIED_PENDING_RUNTIME"}:
            return {"status": "READY", "packages": rows}
        if any(status == "UNVERIFIED_ORIGIN" for status in statuses):
            return {"status": "PENDING", "packages": rows}
        return {"status": "NOT_STARTED", "packages": rows}

    core_ids = ["comfyui", WHEEL_COMPONENT_ID, *[
        component_id for component_id in components
        if components[component_id]["kind"] in {"node", "model"}
        and component_id not in REVIEW_COMPONENTS.values()
    ]]
    layers.append({"layer": "L1_catalog", "status": "READY", "catalog_id": catalog["catalog_id"],
                   "catalog_version": catalog["catalog_version"], "total_bytes": catalog["total_bytes"]})
    candidates = [row for row in db.scalars(select(ComfyInstance))
                  if row.payload.get("managed") and not row.payload.get("is_mock", True)
                  and row.payload.get("root") and not row.payload.get("installation")]
    instance = candidates[0] if len(candidates) == 1 else None
    if instance and instance.payload.get("adopted"):
        adoption = instance.payload.get("adoption") or {}
        verified_h3 = {_verified_h3_key(item) for item in adoption.get("verified_h3_files", [])}
        required_h3 = {
            (component["component_id"], item["relative_path"], item["sha256"], item["size"])
            for component in catalog.get("components", [])
            if component.get("kind") == "model" and component["component_id"].startswith("minimax-h3-")
            for item in component.get("files", [])
        }
        layer_two = {
            "layer": "L2_files_verified",
            "status": "READY" if required_h3 <= verified_h3 else "PENDING",
            "evidence_source": "USER_CONFIRMED_EXISTING_INSTALLATION",
            "verified_h3_components": sorted({key[0] for key in verified_h3}),
            "verified_h3_bytes": sum(item["size"] for item in adoption.get("verified_h3_files", [])),
            "copied_bytes": 0,
            "downloaded_bytes": 0,
            "review_model_file_counts": adoption.get("review_model_file_counts", {}),
            "review_model_expected_counts": adoption.get("review_model_expected_counts", {}),
        }
    else:
        layer_two = package_layer_status(core_ids)
        layer_two["layer"] = "L2_files_verified"
    layers.append(layer_two)
    install_blockers = installed_instance_blockers(db, settings, instance, catalog)
    environment_layer = {
        "layer": "L3_environment_installed",
        "status": "READY" if not install_blockers else "PENDING",
        "blockers": install_blockers,
        "instance": instance.id if instance else None,
        "root": instance.payload.get("root") if instance else None,
    }
    layers.append(environment_layer)
    from shine.comfyops.runtime_configuration import service_evidence_status
    service_status = service_evidence_status(db, settings)
    l4_blockers = list(service_status.get("blockers") or [])
    if environment_layer["status"] != "READY":
        l4_blockers.append("L3_ENVIRONMENT_NOT_READY")
    layers.append({
        "layer": "L4_services_and_contracts",
        "status": "READY" if not l4_blockers and service_status.get("status") == "READY" else "PENDING",
        "blockers": l4_blockers,
        "validated_at": service_status.get("validated_at"),
        "node_count": service_status.get("node_count"),
        "workflow_profiles": service_status.get("workflow_profiles", []),
        "review_models": service_status.get("review_models", {}),
        "note": "Live object_info and workflow input contracts are required; small-model review "
                "services are advisory",
        "advisory_reviews": service_status.get("advisory_reviews", {}),
    })
    layers.append({
        "layer": "L5_real_inference",
        "status": "NOT_VERIFIED",
        "note": "only real (is_mock=false) H3 production evidence can set this; never inferred",
    })
    production_gates = {
        "workflow_profiles_registered": bool(db.scalars(select(WorkflowProfile)).first()),
        # Compatibility key: desktop confirmations no longer need a provisioned
        # password. Origin checks and revision-bound grants remain in force.
        "operator_configured": True,
        "local_confirmation_available": True,
        "services_verified": service_status.get("status") == "READY",
    }
    from shine.comfyops.runtime_configuration import runtime_report
    return {
        "catalog": {"id": catalog["catalog_id"], "version": catalog["catalog_version"]},
        "layers": layers,
        "advisory_reviews": advisory_review_readiness(settings),
        "production_gates": production_gates,
        "runtime_activation": runtime_report(db, settings),
        "human_steps": [
            "点击检测，确认下载安装（后续环境准备自动完成）",
            "在设置中填写自己的语言模型 API 配置",
            "确认使用素材、冻结风格，然后确认生产",
        ],
    }
