"""Minimum runtime capabilities derived from the shared Ref2VA definition."""
from __future__ import annotations

from typing import Any

from shine.comfy.graph_compiler import INT8_VARIANT_ID, LATENT_UPSCALER, PDD_FILE, VARIANT_ACCELERATORS
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


def _combo_weight_missing(
    object_info: dict[str, Any],
    node_class: str,
    input_name: str,
    expected_filename: str,
) -> bool:
    definition = object_info.get(node_class) or {}
    input_spec = definition.get("input") or {}
    entry = (input_spec.get("required") or {}).get(input_name) or (input_spec.get("optional") or {}).get(input_name)
    if not isinstance(entry, list) or not entry or not isinstance(entry[0], list):
        return False
    expected_leaf = expected_filename.replace("\\", "/").split("/")[-1].lower()
    available = {str(item).replace("\\", "/").split("/")[-1].lower() for item in entry[0]}
    return expected_leaf not in available


def probe_optional_capabilities(object_info: dict[str, Any]) -> dict[str, Any]:
    """Derive optional on-demand capability status from one live object_info.

    Missing optional nodes/weights never block baseline 1-image Ref2VA; they
    only mark that specific optional capability unavailable with exact reasons.
    """
    from shine.comfyops import relayvsr

    pdd_nodes = ("MiniMaxH3PDDAccApply", "ShineH3SamplingMode")
    pdd_missing_nodes = [name for name in pdd_nodes if name not in object_info]
    pdd_missing_weights = (
        [PDD_FILE]
        if "MiniMaxH3PDDAccApply" in object_info
        and _combo_weight_missing(object_info, "MiniMaxH3PDDAccApply", "pdd_file", PDD_FILE)
        else []
    )
    sp_nodes = (
        "MinimaxH3LatentUpscaler3DRefineHandoff",
        "MiniMaxH3PDDAccScheduler",
        "MiniMaxH3PDDAccApply",
        "VRAM_Debug",
    )
    sp_missing_nodes = [name for name in sp_nodes if name not in object_info]
    sp_missing_weights: list[str] = []
    if (
        "MinimaxH3LatentUpscaler3DRefineHandoff" in object_info
        and _combo_weight_missing(
            object_info, "MinimaxH3LatentUpscaler3DRefineHandoff", "model_name", LATENT_UPSCALER
        )
    ):
        sp_missing_weights.append(LATENT_UPSCALER)
    if (
        "MiniMaxH3PDDAccApply" in object_info
        and _combo_weight_missing(object_info, "MiniMaxH3PDDAccApply", "pdd_file", PDD_FILE)
    ):
        sp_missing_weights.append(PDD_FILE)

    relay = relayvsr.relayvsr_readiness(object_info=object_info)
    return {
        "pdd": {
            "available": not pdd_missing_nodes and not pdd_missing_weights,
            "missing_nodes": pdd_missing_nodes,
            "missing_weights": pdd_missing_weights,
        },
        "second_pass": {
            "available": not sp_missing_nodes and not sp_missing_weights,
            "missing_nodes": sp_missing_nodes,
            "missing_weights": sp_missing_weights,
        },
        "video_reference": {
            "available": "VHS_LoadVideo" in object_info,
            "missing_nodes": [] if "VHS_LoadVideo" in object_info else ["VHS_LoadVideo"],
        },
        "audio_reference": {
            "available": "VHS_LoadAudioUpload" in object_info,
            "missing_nodes": [] if "VHS_LoadAudioUpload" in object_info else ["VHS_LoadAudioUpload"],
        },
        "relayvsr": {
            "available": bool(relay.get("available")),
            "status": str(relay.get("status") or "BLOCKED"),
            "blockers": list(relay.get("blockers") or []),
        },
    }
