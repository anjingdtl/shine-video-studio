from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any

from shine.comfy.client import ComfyWorkflowError

PATH_PATTERN = re.compile(r"^(\d+)\.inputs\.([A-Za-z0-9_]+)$")


def canonical_graph(graph: dict[str, Any]) -> str:
    return json.dumps(graph, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def graph_hash(graph: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_graph(graph).encode("utf-8")).hexdigest()


def validate_graph(graph: dict[str, Any], *, required_nodes: list[str] | None = None) -> None:
    if not isinstance(graph, dict) or not graph:
        raise ComfyWorkflowError("A ComfyUI API workflow must be a non-empty object keyed by node id")
    classes: set[str] = set()
    for node_id, node in graph.items():
        if not isinstance(node, dict):
            raise ComfyWorkflowError(f"Workflow node {node_id} must be an object")
        class_type = node.get("class_type")
        if not isinstance(class_type, str) or not class_type:
            raise ComfyWorkflowError(f"Workflow node {node_id} is missing class_type")
        if not isinstance(node.get("inputs"), dict):
            raise ComfyWorkflowError(f"Workflow node {node_id} is missing inputs")
        classes.add(class_type)
    missing = [node for node in (required_nodes or []) if node not in classes]
    if missing:
        raise ComfyWorkflowError("Workflow profile is missing required node classes", details={"missing": missing})


def validate_against_object_info(
    graph: dict[str, Any],
    parameter_map: dict[str, str],
    object_info: dict[str, Any],
) -> dict[str, Any]:
    missing_nodes: list[str] = []
    missing_inputs: list[dict[str, str]] = []
    for node_id, node in graph.items():
        class_type = node.get("class_type", "")
        if class_type not in object_info:
            missing_nodes.append(class_type)
            continue
        definition = object_info.get(class_type) or {}
        input_spec = definition.get("input") or {}
        known = set((input_spec.get("required") or {}).keys()) | set((input_spec.get("optional") or {}).keys())
        for key in (node.get("inputs") or {}):
            if known and key not in known:
                missing_inputs.append({"node_id": node_id, "class_type": class_type, "input": key})
    for logical, path in parameter_map.items():
        match = PATH_PATTERN.match(path)
        if not match:
            raise ComfyWorkflowError(
                "parameter_map paths must look like '<node_id>.inputs.<field>'",
                details={"parameter": logical, "path": path},
            )
        node_id = match.group(1)
        if node_id not in graph:
            raise ComfyWorkflowError(
                "parameter_map references a node that does not exist",
                details={"parameter": logical, "path": path},
            )
        field = match.group(2)
        if field not in (graph[node_id].get("inputs") or {}):
            raise ComfyWorkflowError(
                "parameter_map references an input that the workflow does not define",
                details={"parameter": logical, "path": path},
            )
    return {"missing_nodes": sorted(set(missing_nodes)), "missing_inputs": missing_inputs}


def apply_parameters(graph: dict[str, Any], parameter_map: dict[str, str], values: dict[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(values) - set(parameter_map))
    if unknown:
        raise ComfyWorkflowError("No workflow mapping exists for supplied parameters", details={"parameters": unknown})
    prompt = copy.deepcopy(graph)
    for logical, value in values.items():
        path = parameter_map[logical]
        match = PATH_PATTERN.match(path)
        if not match:
            raise ComfyWorkflowError("Invalid workflow parameter path", details={"parameter": logical})
        node_id, field = match.group(1), match.group(2)
        if node_id not in prompt or field not in (prompt[node_id].get("inputs") or {}):
            raise ComfyWorkflowError("Workflow parameter target is missing", details={"parameter": logical})
        prompt[node_id]["inputs"][field] = value
    return prompt


def required_parameters(parameter_map: dict[str, str]) -> set[str]:
    return set(parameter_map)


def generic_h3_parameters(
    *,
    prompt: str,
    seed: int | None,
    duration_s: float,
    references: dict[str, str],
) -> dict[str, Any]:
    """Build logical values for a profile whose parameter_map names these keys.

    H3 node-specific keys are intentionally not invented here. A real exported
    H3 API workflow must declare its own mapping during local acceptance.
    """
    values: dict[str, Any] = {
        "positive_prompt": prompt,
        "duration": duration_s,
    }
    if seed is not None:
        values["seed"] = seed
    values.update(references)
    return values
