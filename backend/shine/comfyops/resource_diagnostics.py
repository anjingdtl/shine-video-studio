"""Read a strategy's node contract only from a proven owned renderer."""
from __future__ import annotations

import json
from typing import Any

import httpx

from shine.comfy.graph_compiler import GraphCompileError, compile_ref2va_workflow
from shine.comfy.workflow import validate_against_object_info
from shine.strategy import GenerationStrategy

MAX_OBJECT_INFO_BYTES = 32 * 1024 * 1024


def check_owned_node_inputs(db, instance_id: str, strategy: GenerationStrategy) -> dict[str, Any]:
    """Read-only class/input check; presence never proves GPU execution or production readiness."""
    from shine.runtime_orchestrator.identity import installation_activity, verified_live_record
    from shine.runtime_orchestrator.status import _owned_listener_pid

    try:
        compiled = compile_ref2va_workflow(
            variant_id=strategy.model_variant_id, accelerator_id=strategy.accelerator_id,
            second_pass=strategy.second_pass_spec(), image_count=1,
        )
    except GraphCompileError as exc:
        return {"state": "BLOCKED", "code": exc.code, "production_ready": False}
    activity = installation_activity(db, instance_id)
    records = [row for row in activity["h3_records"] if row["state"] == "ACTIVE"]
    image = activity.get("image_record")
    if image and image["state"] == "ACTIVE":
        records.append(image)
    if len(records) != 1:
        return {"state": "LIVE_CHECK_PENDING", "production_ready": False}
    record = records[0]
    facts = verified_live_record(record)
    process = record.get("process") or {}
    port = record.get("port")
    if not facts.get("alive") or not facts.get("owned") or type(port) is not int:
        return {"state": "OWNERSHIP_UNVERIFIED", "production_ready": False}
    listener = _owned_listener_pid(port, process)
    if listener is None:
        return {"state": "OWNERSHIP_UNVERIFIED", "production_ready": False}
    data = bytearray()
    try:
        with httpx.stream("GET", f"http://127.0.0.1:{port}/object_info", timeout=4.0,
                          follow_redirects=False, trust_env=False) as response:
            if response.status_code != 200:
                return {"state": "LIVE_CHECK_UNAVAILABLE", "production_ready": False}
            for chunk in response.iter_bytes(chunk_size=64 * 1024):
                data.extend(chunk)
                if len(data) > MAX_OBJECT_INFO_BYTES:
                    return {"state": "LIVE_CHECK_TOO_LARGE", "production_ready": False}
        info = json.loads(data)
    except (httpx.HTTPError, OSError, ValueError):
        return {"state": "LIVE_CHECK_UNAVAILABLE", "production_ready": False}
    if not isinstance(info, dict):
        return {"state": "LIVE_CHECK_UNAVAILABLE", "production_ready": False}
    after = verified_live_record(record)
    if (not after.get("alive") or not after.get("owned")
            or _owned_listener_pid(port, process) != listener):
        return {"state": "OWNERSHIP_UNVERIFIED", "production_ready": False}
    malformed = set()
    for node in compiled.graph.values():
        name = node["class_type"]
        if name not in info:
            continue
        definition = info[name]
        schema = definition.get("input") if isinstance(definition, dict) else None
        if not isinstance(schema, dict):
            malformed.add(str(name))
            continue
        required, optional = schema.get("required", {}), schema.get("optional", {})
        if (not isinstance(required, dict) or not isinstance(optional, dict)
                or (node.get("inputs") and not (required or optional))
                or any(not isinstance(key, str) or not isinstance(value, list) or not value
                       for key, value in [*required.items(), *optional.items()])):
            malformed.add(str(name))
    if malformed:
        return {"state": "NODE_SCHEMA_UNVERIFIED", "missing_schema": sorted(malformed),
                "production_ready": False}
    checked = validate_against_object_info(compiled.graph, compiled.parameter_map, info)
    missing_schema = []
    state = "NODE_INPUTS_MISSING" if checked["missing_nodes"] or checked["missing_inputs"] else (
        "NODE_SCHEMA_UNVERIFIED" if missing_schema else "NODE_INPUTS_PRESENT")
    return {"state": state, "missing_nodes": checked["missing_nodes"],
            "missing_inputs": checked["missing_inputs"], "missing_schema": missing_schema,
            "recipe_sha256": compiled.recipe_sha256, "listener_pid": listener,
            "reference_counts": compiled.reference_counts,
            "gpu_compatibility": "UNVERIFIED", "production_ready": False}
