"""Resolve the selected graph's pinned resources without downloading or starting a renderer."""
from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from shine.comfy.graph_compiler import GraphCompileError, compile_ref2va_workflow
from shine.comfy.workflow import graph_hash
from shine.comfyops.variant_binding import load_binding
from shine.comfyops.variant_readiness import weight_evidence
from shine.strategy import GenerationStrategy, strategy_blockers

# These are filename inputs, not arbitrary text supplied by the browser.
_MODEL_KEYS = {"unet_name", "clip_name", "vae_name", "lora_name", "pdd_file", "model_name", "model_path"}


def _name(value: str) -> str:
    return value.replace("\\", "/").split("/")[-1].lower()


def resolve_resources(db, settings, strategy: GenerationStrategy) -> dict[str, Any]:
    """Derive model closure from the actual compiled graph and verified catalog pins.

    This is evidence for a download preview, never permission to download or
    proof of production readiness. Node ABI is intentionally pending until a
    live owned renderer has been checked.
    """
    binding = load_binding(strategy.model_variant_id, fresh=True)
    try:
        compiled = compile_ref2va_workflow(
            variant_id=strategy.model_variant_id, accelerator_id=strategy.accelerator_id,
            second_pass=strategy.second_pass_spec(), image_count=1,
        )
    except GraphCompileError as exc:
        return {"schema": "shine.resources.v2", "variant_id": strategy.model_variant_id,
                "strategy_sha256": strategy.strategy_sha256(), "components": [],
                "blockers": [{"code": exc.code}], "download_eligible": False,
                "trial_ready": False, "production_ready": False}

    names = {_name(value) for node in compiled.graph.values()
             for key, value in node.get("inputs", {}).items()
             if key in _MODEL_KEYS and isinstance(value, str) and value}
    matches: dict[str, list[dict]] = {name: [] for name in names}
    for component in binding.catalog.get("components", []):
        if component.get("kind") != "model":
            continue
        for entry in component.get("files", []):
            name = _name(str(entry.get("relative_path") or ""))
            if name in matches:
                matches[name].append(component)
    blockers = []
    selected = []
    for name, rows in sorted(matches.items()):
        if len(rows) != 1:
            blockers.append({"code": "RESOURCE_PIN_MISSING" if not rows else "RESOURCE_PIN_AMBIGUOUS",
                             "filename": name})
            continue
        component = rows[0]
        entries = [f for f in component.get("files", []) if _name(str(f.get("relative_path") or "")) == name]
        entry = entries[0]
        declared = [item for item in binding.manifest.get("files", [])
                    if item.get("component_id") == component.get("component_id")]
        if declared and any(
            item.get("sha256") != entry.get("sha256") or item.get("size") != entry.get("size")
            or item.get("url") != entry.get("url")
            or len(str(component.get("revision") or "")) < 12
            or not str(item.get("revision") or "").startswith(str(component.get("revision") or ""))
            for item in declared
        ):
            blockers.append({"code": "VARIANT_CATALOG_MISMATCH", "filename": name})
            continue
        if (not re.fullmatch(r"[a-f0-9]{64}", str(entry.get("sha256") or ""))
                or not isinstance(entry.get("size"), int) or entry["size"] <= 0
                or not entry.get("url") or not component.get("license_id")
                or not component.get("license_url") or component.get("status") != "READY"):
            blockers.append({"code": "RESOURCE_PIN_MISSING", "filename": name})
            continue
        selected.append(component)
    # Existing byte-evidence logic checks receipts/cache hashes rather than
    # treating a filename or a registry status as proof.
    graph_binding = replace(binding, profiles=({"graph": compiled.graph},))
    evidence = {row["component_id"]: row for row in weight_evidence(db, settings, graph_binding)}
    components = []
    for component in selected:
        cid = component["component_id"]
        item = evidence.get(cid) or {}
        verified = bool(item.get("verified"))
        # A verified package directory is not proof that the current renderer
        # reads it; only the current managed binding may establish that.
        consumable = bool(item.get("bound_to_instance"))
        total = sum(int(f["size"]) for f in component["files"])
        components.append({"component_id": cid, "kind": "model", "size_bytes": total,
                           "state": "CONSUMABLE" if consumable else "HASH_VERIFIED" if verified else "MISSING",
                           "missing_download_bytes": 0 if verified else total,
                           "files": [{"sha256": f["sha256"], "size": f["size"],
                                      "relative_path": f["relative_path"], "url": f["url"]}
                                     for f in component["files"]],
                           "license_id": component["license_id"], "license_url": component["license_url"]})
    if strategy.upscale.enabled:
        from shine.comfyops.relayvsr import relayvsr_readiness
        blockers.extend(relayvsr_readiness()["blockers"])
    from sqlalchemy import select

    from shine.comfyops.models import ComfyInstance

    installations = []
    for instance_id in db.scalars(select(ComfyInstance.id)).all():
        try:
            installations.append(managed_installation_pin(db, instance_id))
        except (ValueError, TypeError):
            continue
    result = {
        "schema": "shine.resources.v2", "variant_id": binding.variant_id,
        "strategy_sha256": strategy.strategy_sha256(), "catalog_sha256": graph_hash(binding.catalog),
        "manifest_sha256": graph_hash(binding.manifest),
        "compiled_recipe_sha256": compiled.recipe_sha256, "components": components,
        "missing_download_bytes": sum(row["missing_download_bytes"] for row in components),
        "required_nodes": compiled.required_nodes, "node_abi": "LIVE_CHECK_PENDING",
        "managed_installations": sorted(installations, key=lambda item: item["id"]),
        "blockers": blockers, "production_blockers": strategy_blockers(strategy),
        "download_eligible": not blockers and len(components) == len(names),
        "trial_ready": False, "production_ready": False,
    }
    result["snapshot_sha256"] = graph_hash(result)
    return result



def managed_installation_pin(db, instance_id: str) -> dict:
    """Pin a physical installation without requiring the default INT8 launch recipe."""
    from shine.comfyops.instances import instance_identity
    from shine.comfyops.models import ComfyInstance

    instance = db.get(ComfyInstance, instance_id)
    value = instance.payload if instance and isinstance(instance.payload, dict) else {}
    port = value.get("port")
    if (not instance or value.get("managed") is not True or value.get("is_mock") is not False
            or not isinstance(port, int) or instance_identity("local", port) != instance.id
            or instance.sha256 != graph_hash(value)):
        raise ValueError("RESOURCE_INSTANCE_UNVERIFIED")
    return {"id": instance.id, "sha256": instance.sha256}


def validate_resource_plan(db, settings, payload: dict) -> None:
    """Re-derive a frozen resource plan before any local byte is transferred."""
    context = payload.get("resource_context")
    if context is None:
        return  # historical installation plans keep their existing contract
    if not isinstance(context, dict) or context.get("schema") != "shine.resource-plan.v2":
        raise ValueError("RESOURCE_PLAN_INVALID")
    if context.get("source_preference") not in {"AUTO", "MIRROR_FIRST", "OFFICIAL_FIRST"}:
        raise ValueError("DOWNLOAD_SOURCE_PREFERENCE_INVALID")
    strategy = GenerationStrategy.model_validate(context.get("strategy"))
    preview = resolve_resources(db, settings, strategy)
    binding = load_binding(strategy.model_variant_id, fresh=True)
    from shine.comfyops.deployment import _catalog_package_spec, _component_map, _resolve_closure

    identity = managed_installation_pin(db, str(context.get("instance_id") or ""))
    if (context.get("strategy_sha256") != strategy.strategy_sha256()
            or context.get("catalog_sha256") != graph_hash(binding.catalog)
            or context.get("manifest_sha256") != graph_hash(binding.manifest)
            or context.get("recipe_sha256") != preview.get("compiled_recipe_sha256")
            or context.get("instance_pin") != identity
            or (payload.get("variant") or {}).get("variant_id") != binding.variant_id
            or (payload.get("deployment") or {}).get("catalog_id") != binding.catalog_id
            or not preview.get("download_eligible")):
        raise ValueError("RESOURCE_PLAN_IDENTITY_CHANGED")
    components = _component_map(binding.catalog)
    requested = [item["component_id"] for item in preview["components"]]
    ordered = _resolve_closure(components, requested)
    packages = payload.get("packages") or []
    if [item.get("id") for item in packages] != ordered:
        raise ValueError("RESOURCE_PLAN_COMPONENTS_CHANGED")
    from pathlib import Path

    from shine.comfyops.storage import require_storage
    from shine.comfyops.verification_receipts import cache_candidates

    selected_root = require_storage(settings)
    if Path(str(payload.get("storage_root") or "")).resolve() != selected_root.resolve():
        raise ValueError("DEPLOYMENT_STORAGE_CHANGED_RECREATE_PLAN")
    cache = selected_root / "isolated-packages" / ".cache"
    authorized = context.get("authorized_download_files")
    if not isinstance(authorized, list) or any(
        not isinstance(item, dict) or set(item) != {"sha256", "size"}
        or not isinstance(item["sha256"], str) or type(item["size"]) is not int or item["size"] <= 0
        for item in authorized
    ):
        raise ValueError("RESOURCE_PLAN_INVALID")
    authorized_files = {(item["sha256"], item["size"]) for item in authorized}
    approved_bytes = payload.get("download_bytes")
    if type(approved_bytes) is not int or sum(item["size"] for item in authorized) != approved_bytes:
        raise ValueError("RESOURCE_DOWNLOAD_CHANGED")
    plan_files = {(entry["sha256"], entry["size"]) for package in packages
                  for entry in (package.get("spec") or {}).get("files") or []}
    if not authorized_files <= plan_files:
        raise ValueError("RESOURCE_PLAN_COMPONENTS_CHANGED")
    for package in packages:
        files = (package.get("spec") or {}).get("files") or []
        checked = cache_candidates(cache, {"files": files})["files"]
        for entry, evidence in zip(files, checked, strict=True):
            if not evidence.get("verified") and (entry["sha256"], entry["size"]) not in authorized_files:
                raise ValueError("RESOURCE_DOWNLOAD_CHANGED")
    for package in packages:
        component = components[package["id"]]
        dependencies = {dep: str(components[dep].get("revision") or "")
                        for dep in component.get("dependencies", [])}
        spec = _catalog_package_spec(component, dependencies).model_dump()
        if package.get("spec") != spec or package.get("hash") != graph_hash(spec):
            raise ValueError("RESOURCE_PLAN_COMPONENTS_CHANGED")


def reject_unbound_candidate_checkpoint(payload: dict) -> None:
    """A generic local plan cannot install a candidate DiT without its frozen context."""
    from shine.comfyops.variants import list_variant_manifests

    candidate_digests = {
        str(item.get("sha256") or "").lower()
        for manifest in list_variant_manifests()
        if not manifest.get("auto_download_allowed")
        for item in manifest.get("files", []) if item.get("role") == "dit" and item.get("sha256")
    }
    package_digests = {
        str(item.get("sha256") or "").lower()
        for package in payload.get("packages", [])
        for item in (package.get("spec") or {}).get("files", [])
    }
    if candidate_digests & package_digests and not payload.get("resource_context"):
        raise ValueError("VARIANT_ID_REQUIRED")
