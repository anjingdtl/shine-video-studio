"""Selected scan scope is a persisted subset of administrator-provisioned limits."""

from dataclasses import replace
from pathlib import Path

from fastapi import HTTPException

from shine.comfy.workflow import graph_hash
from shine.models import AppSetting

KEY = "comfyops_selected_scope"


def current_scope(db, settings):
    row = db.get(AppSetting, KEY)
    value = row.value if row else {"roots": list(settings.comfy_probe_roots), "ports": list(settings.comfy_probe_ports)}
    roots = value.get("roots", [])
    ports = value.get("ports", [])
    if any(
        not any(Path(r).resolve().is_relative_to(Path(a).resolve()) for a in settings.comfy_probe_roots) for r in roots
    ):
        raise HTTPException(409, "SAVED_SCOPE_OUTSIDE_ADMINISTRATOR_LIMITS")
    if not set(ports) <= set(settings.comfy_probe_ports):
        raise HTTPException(409, "SAVED_PORT_SCOPE_OUTSIDE_ADMINISTRATOR_LIMITS")
    return {"roots": roots, "ports": ports, "configuration_hash": graph_hash({"roots": roots, "ports": ports})}


def scoped_settings(db, settings):
    scope = current_scope(db, settings)
    return replace(settings, comfy_probe_roots=tuple(scope["roots"]), comfy_probe_ports=tuple(scope["ports"]))
