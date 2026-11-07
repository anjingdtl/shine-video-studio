"""Conservative capabilities derived from a registered graph, never node-name acceptance.

The variant identity of a profile comes from the versioned variant manifests:
a profile is the verified variant only when its own graph hash is declared by
that variant and the variant has a real acceptance.  A profile that merely
names another variant's checkpoint file inherits nothing - a W6A8, NVFP4 or
W4 profile is unverified until it has its own checkpoint, graph hash,
parameter map and evidence.
"""

from shine.comfy.workflow import graph_hash

SOFT = "SOFT_REFERENCE_CONTINUITY"
HARD = "HARD_FRAME_ANCHOR_CONTINUITY"
VIDEO = "VIDEO_REFERENCE_CONTINUITY"

#: The acceptance states that let a profile carry a verified variant name.
VERIFIED_ACCEPTANCE = ("SUPPORTED", "EXPERIMENTAL")


def _weight_names(graph) -> list:
    return [
        str(node.get("inputs", {}).get("unet_name", ""))
        for node in graph.values()
        if node.get("class_type") == "UNETLoader"
    ]


def _file_name(value: str) -> str:
    return str(value or "").replace("\\", "/").split("/")[-1].strip().lower()


def _manifest_index() -> tuple:
    """(graph hash -> variant), (checkpoint file name -> variant)."""
    from shine.comfyops.variants import list_variant_manifests

    by_graph: dict = {}
    by_checkpoint: dict = {}
    try:
        manifests = list_variant_manifests()
    except (OSError, ValueError):
        return by_graph, by_checkpoint
    for manifest in manifests:
        for name, digest in (manifest.get("graph_sha256") or {}).items():
            by_graph[digest] = (manifest, name)
        checkpoint = _file_name(manifest.get("checkpoint_id") or "")
        if checkpoint and checkpoint != "unknown":
            by_checkpoint[checkpoint] = manifest
    return by_graph, by_checkpoint


def resolve_variant(graph, weights: list) -> dict:
    """The variant this graph belongs to, and how far that conclusion goes.

    The verdict is deliberately narrow: a graph that a manifest declares and
    whose variant has a real acceptance is verified; everything else is
    unverified, even when it names a known checkpoint file.
    """
    digest = graph_hash(graph)
    by_graph, by_checkpoint = _manifest_index()
    match = by_graph.get(digest)
    if match is not None:
        manifest, profile_name = match
        acceptance = manifest.get("acceptance_status")
        verified = acceptance in VERIFIED_ACCEPTANCE
        return {
            "variant_id": manifest.get("variant_id"),
            "label": manifest.get("label") or manifest.get("title"),
            "precision": manifest.get("precision"),
            "acceptance_status": acceptance,
            "verified": verified,
            "declared_profile": profile_name,
            "graph_sha256": digest,
        }
    for weight in weights:
        manifest = by_checkpoint.get(_file_name(weight))
        if manifest is not None:
            # The checkpoint is known, but this graph is not the one the
            # manifest declares: no conclusion may be borrowed.
            return {
                "variant_id": manifest.get("variant_id"),
                "label": manifest.get("label") or manifest.get("title"),
                "precision": manifest.get("precision"),
                "acceptance_status": manifest.get("acceptance_status"),
                "verified": False,
                "declared_profile": None,
                "graph_sha256": digest,
            }
    return {
        "variant_id": None,
        "label": None,
        "precision": None,
        "acceptance_status": None,
        "verified": False,
        "declared_profile": None,
        "graph_sha256": digest,
    }


def profile_metadata(graph, mode, parameter_map, media_transfers):
    weights = _weight_names(graph)
    identity = resolve_variant(graph, weights)
    verified = identity["verified"]
    return {
        "model_family": "MiniMax-H3",
        "generation_mode": mode,
        "weight_files": weights,
        "variant": (identity["label"] or "unverified") if verified else "unverified",
        "precision": (identity["precision"] or "unverified") if verified else "unverified",
        "variant_id": identity["variant_id"],
        "variant_acceptance_status": identity["acceptance_status"],
        "variant_verified": verified,
        "variant_declared_profile": identity["declared_profile"],
        "graph_sha256": identity["graph_sha256"],
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
