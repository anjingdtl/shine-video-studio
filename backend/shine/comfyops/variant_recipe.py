"""Variant-level managed startup recipe contract — configuration, not authority.

The managed renderer recipe must state which variant it starts: the model
stack, runtime ABI, node dependencies, launch parameters and memory facts that
belong to that variant, not to the baseline.  A threshold declared ``unknown``
is a fact about missing upstream evidence, never a number to guess: the
contract refuses a determined startup by name instead of borrowing the INT8
floors, and the real activation/production gates stay exactly where they were
(``runtime_configuration`` and ``profile_gate``).  A determined contract is
therefore a prerequisite for generating a recipe, not a permission to render.
"""

from __future__ import annotations

from typing import Any

from shine.comfy.workflow import graph_hash
from shine.comfyops.variant_binding import load_binding
from shine.comfyops.variants import THRESHOLD_FIELDS, UNKNOWN, variant_requirements

#: The floors a managed start actually measures: render-time *available*
#: memory on the one selected card, not the total-memory qualification floors.
STARTUP_THRESHOLD_FIELDS = ("min_free_vram_mb", "min_free_ram_mb")

#: Structural legality of the managed launch flags.  These checks validate the
#: shape and the loopback security invariant of the parameters; they are never
#: a hardware claim about the variant.
_KNOWN_BOOLEAN_FLAGS = {
    "--disable-async-offload",
    "--disable-pinned-memory",
    "--disable-all-custom-nodes",
}
_KNOWN_FAST_MODES = {"fp16_accumulation"}


def _launch_parameters() -> dict[str, Any]:
    """The variant-independent base flags of the managed bootstrap.

    The custom-node whitelist is instance-derived at configuration time and is
    deliberately not part of this contract.
    """
    return {
        "listen": "127.0.0.1",
        "flags": [
            "--disable-async-offload",
            "--disable-pinned-memory",
            "--disable-all-custom-nodes",
        ],
        "reserve_vram_gb": 1,
        "fast": "fp16_accumulation",
    }


def validate_launch_parameters(params: dict[str, Any]) -> list[str]:
    """Named structural violations; an empty list means the shape is legal."""
    violations: list[str] = []
    if params.get("listen") != "127.0.0.1":
        violations.append("listen must stay loopback: reference bytes are uploaded to it")
    flags = params.get("flags")
    if not isinstance(flags, list):
        violations.append("flags must be a list")
    else:
        for flag in flags:
            if flag not in _KNOWN_BOOLEAN_FLAGS:
                violations.append(f"unknown managed flag: {flag}")
    reserve = params.get("reserve_vram_gb")
    if not isinstance(reserve, int | float) or isinstance(reserve, bool) or reserve < 0:
        violations.append("reserve_vram_gb must be a non-negative number")
    if params.get("fast") not in _KNOWN_FAST_MODES:
        violations.append(f"unknown fast mode: {params.get('fast')}")
    return violations


def build_startup_contract(variant_id: str) -> dict[str, Any]:
    """The verifiable startup contract of one variant, bound to its identity.

    Reads only committed configuration (manifest, catalog, workflow graphs);
    it never probes hardware, starts a process or writes a recipe.  The
    ``contract_sha256`` covers every declared fact, so a changed manifest,
    graph or threshold produces a different contract identity.
    """
    binding = load_binding(variant_id)
    manifest = binding.manifest
    requirements = variant_requirements(manifest)
    unknown_thresholds = [
        field for field in STARTUP_THRESHOLD_FIELDS
        if requirements.get(field) in (None, UNKNOWN)
    ]

    model_stack = [
        {
            "role": item.get("role"),
            "component_id": item.get("component_id"),
            "relative_path": item.get("relative_path"),
            "size": item.get("size"),
            "sha256": item.get("sha256"),
            "revision": item.get("revision"),
        }
        for item in manifest.get("files", [])
    ]

    graphs = []
    for profile in binding.profiles:
        name = str(profile.get("name") or "")
        digest = graph_hash(profile["graph"])
        declared = (manifest.get("graph_sha256") or {}).get(name)
        graphs.append({
            "profile_id": name,
            "graph_sha256": digest,
            "declared_graph_sha256": declared,
            "graph_matches_manifest": digest == declared,
            "required_nodes": sorted(profile.get("required_nodes") or []),
            "node_class_types": sorted({
                node["class_type"] for node in profile["graph"].values()
                if isinstance(node, dict) and "class_type" in node
            }),
        })

    params = _launch_parameters()
    violations = validate_launch_parameters(params)

    blockers: list[dict[str, Any]] = []
    if unknown_thresholds:
        blockers.append({
            "code": "VARIANT_STARTUP_THRESHOLDS_UNKNOWN",
            "fields": unknown_thresholds,
            "message": "官方未公布这一组合的渲染期可用显存/内存门槛；没有实测就不写门槛，"
                       "也不借用基线门槛。需要真机渲染峰值证据后才能确定启动配置。",
        })
    if violations:
        blockers.append({"code": "VARIANT_LAUNCH_PARAMETERS_INVALID", "violations": violations})
    for graph in graphs:
        if not graph["graph_matches_manifest"]:
            blockers.append({"code": "VARIANT_RECIPE_DRIFT", "profile_id": graph["profile_id"]})

    determined = not blockers
    acceptance = str(manifest.get("acceptance_status") or UNKNOWN)
    contract: dict[str, Any] = {
        "contract_version": 1,
        "variant_id": variant_id,
        "catalog_id": binding.catalog_id,
        "acceptance_status": acceptance,
        "checkpoint_id": manifest.get("checkpoint_id"),
        "model_stack": model_stack,
        "graphs": graphs,
        "runtime_abi": dict(manifest.get("runtime") or {}),
        "architecture_requirements": dict(manifest.get("architecture_requirements") or {}),
        "memory_facts": {field: requirements.get(field) for field in THRESHOLD_FIELDS},
        "launch_parameters": params,
        "parameters_verified_on_variant": acceptance in ("SUPPORTED", "EXPERIMENTAL"),
        "admission": {
            "startup_determined": determined,
            "status": "DECLARED" if determined else "CANDIDATE_CONTRACT",
            "blockers": blockers,
            "startup_floors": None if unknown_thresholds else {
                "min_vram_mb": int(requirements["min_free_vram_mb"]),
                "min_ram_mb": int(requirements["min_free_ram_mb"]),
            },
        },
        "missing_upstream_evidence": [
            {"field": entry.get("field"), "reason": entry.get("reason")}
            for entry in manifest.get("unknown_fields") or []
        ],
    }
    contract["contract_sha256"] = graph_hash(contract)
    return contract