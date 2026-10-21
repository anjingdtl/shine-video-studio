"""Conservative capabilities derived from a registered graph, never node-name acceptance."""

from shine.comfy.workflow import graph_hash

SOFT = "SOFT_REFERENCE_CONTINUITY"
HARD = "HARD_FRAME_ANCHOR_CONTINUITY"
VIDEO = "VIDEO_REFERENCE_CONTINUITY"


def profile_metadata(graph, mode, parameter_map, media_transfers):
    weights = [
        str(n.get("inputs", {}).get("unet_name", "")) for n in graph.values() if n.get("class_type") == "UNETLoader"
    ]
    ref2va_int8 = len(weights) == 1 and "ref2va_pruned_int8_convrot" in weights[0].lower()
    return {
        "model_family": "MiniMax-H3",
        "generation_mode": mode,
        "weight_files": weights,
        "variant": "Ref2VA INT8 ConvRot" if ref2va_int8 else "unverified",
        "precision": "INT8 ConvRot" if ref2va_int8 else "unverified",
        "graph_sha256": graph_hash(graph),
        "graph_version": "registered-api-v1",
        "source": "registered_graph_and_explicit_mapping",
        "continuity_modes": [SOFT]
        if mode == "Ref2VA"
        and any(k.startswith("reference_subject_") or k.startswith("reference_picture_") for k in parameter_map)
        else [],
        "hard_continuity": "PENDING_HARD_CONTINUITY_TEST",
        "video_reference": "SUPPORTED_NODE_UNVERIFIED_TRANSFER" if mode == "Ref2VA" else "unverified",
        "reference_slots": sorted(k for k in parameter_map if k.startswith("reference_")),
        "media_transfer_families": sorted(media_transfers or {}),
        "dynamic_autogrow": False,
    }


def continuity_capability(mode):
    return {"last_frame": SOFT, "hard_frame_anchor": HARD, "video_reference": VIDEO}.get(mode)
