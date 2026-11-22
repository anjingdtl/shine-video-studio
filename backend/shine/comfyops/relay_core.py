"""The official RelayVSR-capable core, loaded by the shared managed renderer."""
from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import HTTPException

from shine.comfy.workflow import graph_hash
from shine.comfyops import deployment
from shine.comfyops.installer import exclusive_file_lock, extract_zip, safe_target
from shine.comfyops.verification_receipts import file_digest, installed_directory

CORE_PINS = (
    "comfy_extras/nodes_wan.py",
    "comfy_extras/nodes_video.py",
    "comfy/ldm/wan/model_vsr.py",
    ".shine-python/comfy_kitchen/__init__.py",
    ".shine-python/comfy_aimdo/control.py",
)


def core_components(catalog: dict) -> list[dict]:
    components = deployment._component_map(catalog)
    if deployment.RELAYVSR_CORE not in components:
        return []
    return [components[cid] for cid in deployment._resolve_closure(components, [deployment.RELAYVSR_CORE])]


def core_root(settings) -> Path:
    components = core_components(deployment.load_deployment_catalog(deployment.RELAYVSR_CATALOG))
    revision = next(row["origin_revision"] for row in components if row["component_id"] == deployment.RELAYVSR_CORE)
    digest = graph_hash([row["files"] for row in components])[:16]
    return safe_target(settings.comfyops_dir, f"relay-core/{digest}/ComfyUI-{revision}")


def stage_core(settings) -> Path:
    """Extract the installer's hash-verified archives without touching ComfyUI or its Python."""
    root = core_root(settings)
    root.parent.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(root.parent.with_suffix(".lock")):
        if (root / "main.py").is_file() and all((root / pin).is_file() for pin in CORE_PINS):
            return root
        if root.parent.exists():
            raise HTTPException(409, "RELAYVSR_CORE_FILES_CHANGED")
        with tempfile.TemporaryDirectory(dir=root.parent.parent, prefix="extract-") as temporary:
            staged = Path(temporary) / "runtime"
            staged.mkdir()
            catalog = deployment.load_deployment_catalog(deployment.RELAYVSR_CATALOG)
            for component in core_components(catalog):
                directory = installed_directory(settings, component)
                for entry in component["files"]:
                    archive = directory / entry["relative_path"]
                    if not file_digest(archive, entry["sha256"], entry["size"])["verified"]:
                        raise HTTPException(409, "RELAYVSR_CORE_DOWNLOAD_REQUIRED")
                    target = staged if archive.suffix == ".zip" else staged / root.name / ".shine-python"
                    extract_zip(archive, target)
            source = staged / root.name
            if not (source / "main.py").is_file() or not all((source / pin).is_file() for pin in CORE_PINS):
                raise HTTPException(409, "RELAYVSR_CORE_FILES_INCOMPLETE")
            staged.rename(root.parent)
    return root


def with_core(renderer: dict, root: Path) -> dict:
    """Use new source with the selected installation's data and node packs."""
    if Path(renderer["root"]).resolve() == root:
        return renderer
    argv = [*renderer["argv"], "--base-directory", "{base_directory}"]
    extra_paths = Path(renderer["base_directory"]) / "extra_model_paths.yaml"
    if extra_paths.is_file():
        # Load upstream's installation config before Shine's registered path mappings.
        argv[2:2] = ["--extra-model-paths-config", str(extra_paths)]
    return {
        **renderer, "root": str(root), "argv": argv,
        "pinned_files": [
            *[path.replace("{root}", "{base_directory}") if
              path.replace("\\", "/") != "{root}/main.py" else path
              for path in renderer["pinned_files"]],
            *["{root}/" + pin for pin in CORE_PINS],
            *([str(extra_paths)] if extra_paths.is_file() else []),
        ],
    }


def prepare_runtime(factory, settings, *, instance_id: str, claim_guard) -> None:
    """Probe the selected installation and activate the pinned core only if needed."""
    from shine.comfy.graph_compiler import INT8_VARIANT_ID
    from shine.comfyops.models import ComfyInstance
    from shine.comfyops.renderer_release import (
        _physical_installation_id,
        _record_key,
        managed_processes,
        release_managed_processes_guarded,
    )
    from shine.comfyops.runtime_configuration import (
        ACTIVE_KEY,
        CANDIDATE_KEY,
        SERVICE_EVIDENCE_KEY,
        _current_identity,
        load_runtime_recipe,
        publish_validated_renderer,
        recover_relay_recipe,
        require_idle_configuration,
        service_evidence_status,
    )
    from shine.comfyops.scheduler import resource_slot
    from shine.comfyops.service_validation import _probe_renderer, renderer_service_evidence
    from shine.models import AppSetting
    from shine.runtime_orchestrator.guard import renderer_lifecycle_guard

    with renderer_lifecycle_guard(instance_id), factory() as db:
        recover_relay_recipe(db, settings, instance_id)
        identity = _current_identity(db, settings)
        if identity["instance_id"] != instance_id:
            raise HTTPException(409, "RELAYVSR_RUNTIME_TARGET_CHANGED")
        recipe, _ = load_runtime_recipe(settings)
        instance = db.get(ComfyInstance, instance_id)
        source = (db.get(AppSetting, ACTIVE_KEY) or db.get(AppSetting, CANDIDATE_KEY)).value
        variant = source.get("variant_id") or INT8_VARIANT_ID
        previous = db.get(AppSetting, SERVICE_EVIDENCE_KEY)
        sealed = (previous.value if previous and service_evidence_status(db, settings, identity=identity)
                  .get("status") == "READY" else {})
        target_records = {_record_key(row) for row in managed_processes(db)
                          if _physical_installation_id(row) == instance_id
                          and row.get("port") == recipe["renderer"]["port"]}
        if instance is None:
            raise HTTPException(409, "RELAYVSR_RUNTIME_TARGET_CHANGED")

    if target_records:
        result = release_managed_processes_guarded(
            factory, actor="worker:setup", reason="relayvsr-core", only=target_records,
        )
        if any(row.get("outcome") == "busy" for row in result["refused"]):
            raise HTTPException(409, "GPU_BUSY_RETRY_OR_QUEUE")
        if result["refused"]:
            raise HTTPException(409, {
                "code": "RELAYVSR_RENDERER_RELEASE_UNVERIFIED",
                "message": "未能确认当前 ComfyUI 已退出。释放这套受管渲染器后可继续整备。",
            })
    with factory() as db:
        claim_guard(db)
        require_idle_configuration(db)

    clean = True

    def require_clean_stop():
        if not clean:
            raise RuntimeError("SERVICE_VALIDATION_CLEANUP_FAILED")

    def inspect(candidate):
        nonlocal clean
        try:
            probe = _probe_renderer(candidate, lease_id=lease_id, factory=factory)
        except HTTPException as exc:
            clean = "SERVICE_VALIDATION_CLEANUP_FAILED" not in str(exc.detail)
            raise
        return renderer_service_evidence(candidate, probe, variant_id=variant)

    def relay_evidence(evidence):
        return (evidence.get("optional_capabilities") or {}).get("relayvsr") or {}

    renderer = recipe["renderer"]
    with resource_slot(factory, "WORKFLOW_VALIDATION", 1, release_models=require_clean_stop) as lease_id:
        evidence = {}
        if not relay_evidence(sealed):
            evidence = inspect(recipe)
        if not relay_evidence(evidence).get("available"):
            renderer = with_core(renderer, stage_core(settings))
            evidence = inspect({**recipe, "renderer": renderer})
        relay = relay_evidence(evidence)
        if not relay.get("available"):
            raise HTTPException(409, {
                "code": "RELAYVSR_CORE_VALIDATION_FAILED",
                "message": "官方核心已启动，但 RelayVSR 图的运行合同校验失败。",
                "blockers": relay.get("blockers") or [],
            })
    evidence.update({key: sealed[key] for key in ("review_profiles", "review_runtime", "advisory_reviews")
                     if key in sealed})
    evidence["project_id"] = source.get("project_id")
    with renderer_lifecycle_guard(instance_id), factory() as db:
        publish_validated_renderer(db, settings, previous_identity=identity, renderer=renderer,
                                   evidence=evidence, claim_guard=claim_guard)
