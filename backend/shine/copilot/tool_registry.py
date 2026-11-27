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
    "managed_release",
    "support_web_search_update",
    "image_runtime_validate",
    "workflow_graph_register",
    "workflow_user_copy",
    "project_create",
    "project_settings_update",
    "document_import_text",
    "document_switch_basis",
    "story_outline_generate",
    "story_outline_confirm",
    "storyboard_book_generate",
    "storyboard_book_confirm",
    "literary_scripts_generate",
    "storyboard_generate",
    "storyboard_confirm",
    "shot_revise",
    "material_derive",
    "material_preferences_update",
    "material_generate",
    "material_regenerate",
    "material_promote",
    "shot_swap_material_binding",
    "asset_center_image_generate",
    "asset_center_character_sheet_generate",
    "asset_center_version_promote",
    "asset_center_assign_to_project",
    "lora_import_local",
    "lora_update",
    "lora_delete",
    "director_package_create",
    "production_start",
    "production_control",
    "shot_rerender",
    "episode_delivery_assemble",
    "task_reconcile",
    "stale_run_recover",
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
            entry["items"] = {"type": "object" if field.get("type") == "list" else "string"}
        enum = list(field.get("enum") or [])
        if enum:
            (entry["items"] if kind == "array" else entry)["enum"] = enum
        size_keyword = {"array": "Items", "string": "Length"}.get(kind)
        if size_keyword:
            for bound in ("min", "max"):
                if field.get(f"{bound}_length") is not None:
                    entry[f"{bound}{size_keyword}"] = int(field[f"{bound}_length"])
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
