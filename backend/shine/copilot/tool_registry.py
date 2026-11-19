"""Render the *existing* capability declarations into a model tool menu.

This module adds no capability: it projects ``copilot.declarations.WRITE_ACTIONS``
(already the single capability registry) into the OpenAI-style function schema a
model needs to propose a tool call.  A capability that is not declared simply
does not appear, so the menu and the executor can never disagree.

Only capabilities that the grant layer can authorize are offered, and only those
the chosen capability groups actually include: a menu that lists a forbidden
operation invites the model to keep trying it.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from shine.copilot import declarations as decl
from shine.copilot import task_grant as grant_contract
from shine.copilot.params import resolved_params
from shine.llm.tool_calls import ToolSpec

#: Capabilities the autonomous loop may call.  Each one is already reviewed and
#: declared; this list is the *subset* whose executor can be driven end to end
#: without a person choosing an artefact in the middle of the flow.
AUTONOMOUS_CAPABILITIES: tuple[str, ...] = (
    "setup_reconcile",
    "setup_repair",
    "resource_install_plan",
    "workflow_graph_register",
    "workflow_user_copy",
    "production_start",
)

_TYPE_MAP = {
    "string": "string",
    "boolean": "boolean",
    "integer": "integer",
    "number": "number",
    "string_list": "array",
    "list": "array",
    "object": "object",
    "enum": "string",
}


def _json_schema_for(row) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for field in resolved_params(row):
        name = str(field.get("name"))
        kind = _TYPE_MAP.get(str(field.get("type")), "string")
        entry: dict[str, Any] = {"type": kind}
        if kind == "array":
            entry["items"] = {"type": "string"}
        enum = list(field.get("enum") or [])
        if enum:
            entry["enum"] = enum
        if field.get("max_length") is not None and kind == "string":
            entry["maxLength"] = int(field["max_length"])
        if field.get("minimum") is not None and kind in {"integer", "number"}:
            entry["minimum"] = field["minimum"]
        if field.get("maximum") is not None and kind in {"integer", "number"}:
            entry["maximum"] = field["maximum"]
        if field.get("pattern"):
            entry["pattern"] = str(field["pattern"])
        if field.get("description"):
            entry["description"] = str(field["description"])
        properties[name] = entry
        if field.get("required"):
            required.append(name)
    schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
    }
    if required:
        schema["required"] = required
    return schema


def tool_specs(capability_ids: Iterable[str]) -> list[ToolSpec]:
    """Tool specs for the reviewed capabilities that exist in this build."""
    specs: list[ToolSpec] = []
    for capability_id in capability_ids:
        row = decl.write_action(str(capability_id))
        if row is None or row.availability != "AVAILABLE":
            continue
        if row.permission_tier == "FORBIDDEN":
            continue
        specs.append(
            ToolSpec(
                name=row.action_id,
                description=f"{row.label}：{row.description}",
                parameters=_json_schema_for(row),
            )
        )
    return specs


def grant_tool_specs(grant: dict[str, Any]) -> list[ToolSpec]:
    """The menu for one grant: autonomous capabilities it actually authorizes."""
    allowed: list[str] = []
    for capability_id in AUTONOMOUS_CAPABILITIES:
        try:
            if grant_contract.capability_allowed(grant, capability_id):
                allowed.append(capability_id)
        except grant_contract.GrantError:
            continue
    return tool_specs(allowed)
