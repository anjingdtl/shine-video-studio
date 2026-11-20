"""Mirrored ComfyUI API graph compiler helpers (from shine/comfy/workflow.py)."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any

from shine.manual4.comfy.client import M4ComfyWorkflowError

PATH_PATTERN = re.compile(r"^(\d+)\.inputs\.([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*)$")
REFERENCE_INPUT_GROUPS = ("ref_images.", "ref_videos.", "ref_video_audios.", "ref_audios.")


def canonical_graph(graph: dict[str, Any]) -> str:
    return json.dumps(graph, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def graph_hash(graph: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_graph(graph).encode("utf-8")).hexdigest()


def prune_unused_reference_nodes(graph: dict[str, Any], used_loader_nodes: set[str]) -> dict[str, Any]:
    pruned = copy.deepcopy(graph)
    dropped_inputs: list[tuple[str, str]] = []
    for node_id, node in pruned.items():
        for key, value in (node.get("inputs") or {}).items():
            if not key.startswith(REFERENCE_INPUT_GROUPS):
                continue
            if not (isinstance(value, list) and len(value) >= 2 and isinstance(value[0], str)):
                continue
            if value[0] not in pruned or value[0] in used_loader_nodes:
                continue
            dropped_inputs.append((node_id, key))
    if not dropped_inputs:
        return pruned
    removed_targets = set()
    for node_id, key in dropped_inputs:
        source = (graph[node_id].get("inputs") or {})[key]
        (pruned[node_id].get("inputs") or {}).pop(key, None)
        if isinstance(source, list) and source and isinstance(source[0], str):
            removed_targets.add(source[0])
    still_used = {
        value[0]
        for node in pruned.values()
        for value in (node.get("inputs") or {}).values()
        if isinstance(value, list) and len(value) >= 2 and isinstance(value[0], str)
    }
    for target in removed_targets:
        if target not in still_used:
            pruned.pop(target, None)
    return pruned


def validate_graph(graph: dict[str, Any], *, required_nodes: list[str] | None = None) -> None:
    if not isinstance(graph, dict) or not graph:
        raise M4ComfyWorkflowError("ComfyUI workflow must be a non-empty dict")
    classes: set[str] = set()
    for node_id, node in graph.items():
        if not isinstance(node, dict) or not node.get("class_type") or not isinstance(node.get("inputs"), dict):
            raise M4ComfyWorkflowError(f"Workflow node {node_id} is malformed")
        classes.add(str(node["class_type"]))
    missing = [node for node in (required_nodes or []) if node not in classes]
    if missing:
        raise M4ComfyWorkflowError("Workflow missing required node classes", details={"missing": missing})


def validate_against_object_info(
    graph: dict[str, Any],
    parameter_map: dict[str, str],
    object_info: dict[str, Any],
) -> dict[str, Any]:
    missing_nodes: list[str] = []
    missing_inputs: list[dict[str, str]] = []
    for node_id, node in graph.items():
        class_type = str(node.get("class_type") or "")
        if class_type not in object_info:
            missing_nodes.append(class_type)
            continue
        input_spec = (object_info.get(class_type) or {}).get("input") or {}
        known = set((input_spec.get("required") or {}).keys()) | set((input_spec.get("optional") or {}).keys())
        for group in tuple(known):
            known.add(group.split(".", 1)[0])
        definitions = {**(input_spec.get("required") or {}), **(input_spec.get("optional") or {})}
        for key in node.get("inputs") or {}:
            group = key.split(".", 1)[0]
            declared = definitions.get(group) or []
            autogrow = bool(declared and declared[0] == "COMFY_AUTOGROW_V3")
            known_input = key in known or ("." in key and group in known and autogrow)
            if known and not known_input:
                missing_inputs.append({"node_id": node_id, "class_type": class_type, "input": key})
    for logical, path in parameter_map.items():
        m = PATH_PATTERN.match(path)
        if not m:
            raise M4ComfyWorkflowError(f"Invalid parameter_map path for {logical}: {path}")
        node_id, field = m.group(1), m.group(2)
        if node_id not in graph or field not in (graph[node_id].get("inputs") or {}):
            raise M4ComfyWorkflowError(f"parameter_map target missing for {logical}: {path}")
    return {"missing_nodes": sorted(set(missing_nodes)), "missing_inputs": missing_inputs}


def apply_parameters(graph: dict[str, Any], parameter_map: dict[str, str], values: dict[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(values) - set(parameter_map))
    if unknown:
        raise M4ComfyWorkflowError(f"Unknown workflow parameters: {unknown}")
    updated = copy.deepcopy(graph)
    for logical, value in values.items():
        m = PATH_PATTERN.match(parameter_map[logical])
        if not m:
            raise M4ComfyWorkflowError(f"Invalid parameter_map path: {parameter_map[logical]}")
        node_id, field = m.group(1), m.group(2)
        updated[node_id]["inputs"][field] = value
    return updated
