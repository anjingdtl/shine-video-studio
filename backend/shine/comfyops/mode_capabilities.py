"""Mode-level capability checks for the Ref2VA production mode.

The workbench no longer treats the historical static profile JSON files as a
byte-exact startup contract.  Production readiness is derived from the
capability the active mode actually needs: the core H3 node classes a managed
renderer must expose, plus per-strategy requirements that are checked when a
shot workflow is compiled.  A drifted or missing historical template is an
advisory fact ("this template needs re-validation"), never a machine-wide
block on the whole Ref2VA mode.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from shine.models import WorkflowProfile

#: Repository profile templates, one JSON per audited static recipe.
PROFILE_FILES_DIR_NAME = "workflows"


def _profile_files() -> dict[str, dict[str, Any]]:
    from shine.comfyops import deployment

    files: dict[str, dict[str, Any]] = {}
    for path in sorted(Path(deployment.PROFILE_DIR).glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        name = raw.get("name")
        if isinstance(name, str) and name:
            files[name] = raw
    return files


def baseline_profile_names() -> list[str]:
    """Profile names the default deployment catalog registers for this mode.

    ``deferred_workflow_profiles`` are deliberately excluded: they are
    registered identities for recipes whose weights were never pinned or
    verified on this machine (for example the historical latent-upscale
    template), so they must not become a startup requirement for a renderer
    that only has to serve the baseline Ref2VA mode.
    """
    from shine.comfyops import deployment

    catalog = deployment.load_default_catalog()
    names = [str(name) for name in (catalog.get("workflow_profiles") or [])]
    deferred = {str(name) for name in (catalog.get("deferred_workflow_profiles") or [])}
    return [name for name in names if name not in deferred]


def _graph_node_types(graph: Any) -> set[str]:
    if isinstance(graph, str):
        try:
            graph = json.loads(graph)
        except ValueError:
            return set()
    types: set[str] = set()
    if isinstance(graph, dict):
        for node in graph.values():
            if isinstance(node, dict) and node.get("class_type"):
                types.add(str(node["class_type"]))
    return types


def core_mode_node_types() -> set[str]:
    """Node classes a renderer must expose to serve the baseline Ref2VA mode.

    Derived from the audited repository templates of the default catalog so
    the statement is about this installation's baseline mode, not about one
    project's database rows.  Templates of other model variants (candidate
    low-VRAM graphs, project-local wide graphs) are not part of the baseline:
    their extra nodes are checked when a shot actually compiles them.
    """
    types: set[str] = set()
    files = _profile_files()
    for name in baseline_profile_names():
        raw = files.get(name)
        if raw:
            types |= _graph_node_types(raw.get("graph"))
    return types


def template_contract(raw: dict[str, Any]) -> dict[str, Any]:
    """The byte-comparable identity of a repository workflow template."""
    return {
        "graph": raw.get("graph"),
        "parameter_map": raw.get("parameter_map") or {},
        "media_transfers": raw.get("media_transfers") or {},
        "required_nodes": raw.get("required_nodes") or [],
    }


def template_drift(profile: WorkflowProfile, raw: dict[str, Any]) -> bool:
    """True when a registered profile row no longer matches its template."""
    from shine.comfy.workflow import graph_hash

    expected = template_contract(raw)
    return bool(
        profile.graph_sha256 != graph_hash(expected["graph"])
        or (profile.parameter_map or {}) != expected["parameter_map"]
        or (profile.media_transfers or {}) != expected["media_transfers"]
        or (profile.required_nodes or []) != expected["required_nodes"]
    )


def classify_baseline_profiles(db: Session, project_id: str) -> dict[str, Any]:
    """Split the baseline templates into ready/drifted/missing, per project.

    Returns ``{"contracts": {name: profile}, "drifted": [names],
    "missing": [names], "templates": {name: raw}}``.  Only names whose
    database row is present and byte-identical to the repository template
    become ``contracts``; everything else is advisory and must never abort a
    service validation on its own.
    """
    files = _profile_files()
    profiles = db.scalars(
        select(WorkflowProfile).where(WorkflowProfile.project_id == project_id)
    ).all()
    by_name = {profile.name: profile for profile in profiles}
    contracts: dict[str, WorkflowProfile] = {}
    drifted: list[str] = []
    missing: list[str] = []
    for name in baseline_profile_names():
        raw = files.get(name)
        profile = by_name.get(name)
        if raw is None:
            # The catalog names a template the repository no longer ships.
            # That is a packaging defect, reported as drift, not a mode block.
            drifted.append(name)
            continue
        if profile is None:
            missing.append(name)
            continue
        if template_drift(profile, raw):
            drifted.append(name)
            continue
        contracts[name] = profile
    return {"contracts": contracts, "drifted": sorted(drifted),
            "missing": sorted(missing), "templates": files}


def auto_register_missing_baseline_profiles(db: Session, project_id: str, *, actor: str) -> list[str]:
    """Create database rows for baseline templates this project has not registered.

    This is the audited "standard recipe auto-fill": the content comes from
    the repository's versioned template files, exactly as the manual
    ``POST /setup/profiles`` registration path would install them.  A row with
    the same name but different content is never overwritten - drift stays
    visible to the operator instead of being silently absorbed.
    """
    from shine.comfy.capabilities import profile_metadata
    from shine.comfy.workflow import graph_hash
    from shine.services import add_audit

    state = classify_baseline_profiles(db, project_id)
    files = state["templates"]
    created: list[str] = []
    for name in state["missing"]:
        raw = files.get(name)
        if not raw:
            continue
        metadata = profile_metadata(
            raw.get("graph") or {}, raw.get("mode") or "Ref2VA",
            raw.get("parameter_map") or {}, raw.get("media_transfers") or {},
        )
        profile = WorkflowProfile(
            project_id=project_id,
            name=name,
            mode=raw.get("mode") or "Ref2VA",
            graph=raw.get("graph") or {},
            parameter_map=raw.get("parameter_map") or {},
            media_transfers=raw.get("media_transfers") or {},
            required_nodes=raw.get("required_nodes") or [],
            checkpoint_id=raw.get("checkpoint_id"),
            graph_sha256=graph_hash(raw.get("graph") or {}),
            model_identity=metadata,
            validated_against=None,
        )
        db.add(profile)
        db.flush()
        add_audit(db, actor, "WORKFLOW_PROFILE_AUTO_REGISTERED", "workflow_profile", profile.id,
                  {"name": name, "source": "repository-template"}, project_id=project_id)
        created.append(name)
    return created
