"""Bounded inventory and hash-checked reuse of a managed ComfyUI installation."""
from __future__ import annotations

from contextlib import ExitStack
from typing import Any

from fastapi import HTTPException

from shine.comfy.workflow import graph_hash
from shine.comfyops.models import ComfyInstance
from shine.comfyops.resource_resolver import managed_installation_pin, resolve_resources
from shine.comfyops.variant_binding import load_binding
from shine.strategy import GenerationStrategy


def _inventory(db, settings, strategy: GenerationStrategy, instance_id: str) -> tuple[dict, str]:
    """Inspect only missing graph components under this pinned installation's models directory."""
    from shine.comfyops.existing_installation import inspect

    try:
        pin = managed_installation_pin(db, instance_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    instance = db.get(ComfyInstance, instance_id)
    root = (instance.payload or {}).get("root") if instance else None
    if not isinstance(root, str) or not root:
        raise HTTPException(409, "RESOURCE_INSTANCE_ROOT_MISSING")
    preview = resolve_resources(db, settings, strategy)
    if not preview.get("download_eligible"):
        raise HTTPException(409, "RESOURCE_STRATEGY_UNVERIFIED")
    missing = {row["component_id"] for row in preview["components"] if row["state"] == "MISSING"}
    binding = load_binding(strategy.model_variant_id, fresh=True)
    if (graph_hash(binding.catalog) != preview["catalog_sha256"]
            or graph_hash(binding.manifest) != preview["manifest_sha256"]):
        raise HTTPException(409, "RESOURCE_INVENTORY_CHANGED")
    catalog = {**binding.catalog, "components": [row for row in binding.catalog["components"]
                                                  if row["component_id"] in missing]}
    report = inspect(root, catalog)
    snapshot = graph_hash({"inventory": report["snapshot"], "strategy": strategy.strategy_sha256(),
                           "manifest": preview["manifest_sha256"], "catalog": preview["catalog_sha256"],
                           "instance": pin})
    return report, snapshot


def inspect_managed_resources(db, settings, strategy: GenerationStrategy, instance_id: str) -> dict[str, Any]:
    """Size matches are candidates only; never report them as verified bytes."""
    report, snapshot = _inventory(db, settings, strategy, instance_id)
    return {"snapshot_sha256": snapshot, "candidates": [
        {"component_id": row["component_id"], "size_bytes": row["size"], "state": "PRESENT_UNVERIFIED"}
        for row in report["matches"]], "candidate_bytes": report["candidate_bytes"],
        "scanned_files": report["scanned_files"], "runtime_verified": False}


def import_managed_resources(db, settings, strategy: GenerationStrategy, instance_id: str, snapshot: str) -> dict:
    """Use the existing importer and component locks; never download or replace source files."""
    from shine.comfyops.existing_installation import import_models
    from shine.comfyops.installer import exclusive_file_lock, safe_target
    from shine.comfyops.storage import disk_info, require_storage

    report, actual = _inventory(db, settings, strategy, instance_id)
    if snapshot != actual:
        raise HTTPException(409, "RESOURCE_INVENTORY_CHANGED")
    storage = require_storage(settings)
    if disk_info(storage)["free_bytes"] < report["candidate_bytes"]:
        raise HTTPException(409, "INSUFFICIENT_DISK_ON_HOST")
    package_root = storage / "isolated-packages"
    package_root.mkdir(parents=True, exist_ok=True)
    component_ids = sorted({row["component_id"] for row in report["matches"]})
    try:
        with ExitStack() as stack:
            for component_id in component_ids:
                stack.enter_context(exclusive_file_lock(safe_target(package_root, component_id + ".lock")))
            result = import_models(settings, report, report["snapshot"])
    except ValueError as exc:
        if str(exc) == "INSTALL_OWNER_ACTIVE":
            raise HTTPException(409, "RESOURCE_IMPORT_IN_PROGRESS") from exc
        raise
    return {"status": result["status"], "reused_bytes": result["reused_bytes"],
            "imported": [row["component_id"] for row in result["imported"]],
            "rejected": [row["component_id"] for row in result["rejected"]], "runtime_verified": False}
