"""Minimum runtime capabilities derived from the shared Ref2VA definition."""
from __future__ import annotations

from typing import Any

from shine.comfy.graph_compiler import INT8_VARIANT_ID, VARIANT_ACCELERATORS
from shine.foundation.h3 import CompiledWorkflow, compile_h3_ref2va_graph

# Approved catalog mappings for the custom node classes in the minimum graph.
# All other installed packs can still be whitelisted for explicit shot options.
_CORE_NODE_PACK_CLASSES = {
    "ComfyUI-KJNodes": {"MiniMaxChunkFeedForward", "MiniMaxLowVRAMAttention"},
    "ComfyUI-SolAttn_triton": {"SolAttnPatch"},
}


def baseline_profile_names() -> list[str]:
    """Archived profile names, used by the compatibility diagnostics."""
    from shine.comfyops import deployment

    return list(deployment.load_default_catalog().get("workflow_profiles") or [])


def mode_workflows(variant_id: str = INT8_VARIANT_ID) -> tuple[CompiledWorkflow, ...]:
    """Compile single-image, single-pass shapes for this variant's audited samplers."""
    return tuple(compile_h3_ref2va_graph(variant_id=variant_id, accelerator_id=accelerator, image_count=1)
                 for accelerator in VARIANT_ACCELERATORS.get(variant_id, ()))


def core_mode_node_types(variant_id: str = INT8_VARIANT_ID) -> set[str]:
    """Every selectable sampling path needs these classes."""
    workflows = mode_workflows(variant_id)
    if not workflows:
        return set()
    return set.intersection(*(set(workflow.required_nodes) for workflow in workflows))


def required_node_packs(catalog: dict) -> list[dict]:
    """Physical packs needed for the baseline of an audited variant."""
    variant_id = catalog.get("variant_id")
    nodes = [component for component in catalog.get("components", []) if component["kind"] == "node"]
    if variant_id not in VARIANT_ACCELERATORS:
        return nodes
    core = core_mode_node_types(variant_id)
    return [component for component in nodes
            if core & _CORE_NODE_PACK_CLASSES.get(component["component_id"], set())]


def sealed_mode_nodes_compatible(sealed: Any, variant_id: str = INT8_VARIANT_ID) -> bool:
    """Accept pre-P1 seals which covered the optional samplers and audio loader."""
    if not isinstance(sealed, list) or not all(isinstance(item, str) and item for item in sealed):
        return False
    workflows = mode_workflows(variant_id)
    known = {node for workflow in workflows for node in workflow.required_nodes} | {"VHS_LoadAudioUpload"}
    core = core_mode_node_types(variant_id)
    sealed_set = set(sealed)
    return bool(core) and len(sealed_set) == len(sealed) and core <= sealed_set <= known
