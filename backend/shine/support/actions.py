"""Preview → explicit authorization → existing executor → re-read authority.

The copilot never executes a change itself.  Each action is a thin, idempotent
authorization envelope around an executor that already exists (the durable
preparation worker, the reconcile verifier or the guarded renderer release).
Readiness after the run is taken from the same capability verdict the setup
pages use; the agent is not allowed to declare success.

Four identities are kept separate on purpose:

* ``request_key``      — the requested change (kind + params + actor + project)
* ``attempt_no``       — a new legal attempt after a terminal one
* ``original_task_id`` — the executor's own durable task for *this* attempt
* ``preview_hash``     — the previewed facts (snapshot revision) that were approved
"""
from __future__ import annotations

import hashlib
import inspect
import json
from datetime import datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from shine.copilot.declarations import (
    MACHINE_SCOPED_KINDS,
    NO_GPU_GATE_KINDS,
    PROJECT_OPTIONAL_KINDS,
    PROJECT_SCOPED_KINDS,
)
from shine.models import AppSetting, AuditEvent, new_id, utcnow
from shine.support.context import build_evidence_snapshot, snapshot_revision
from shine.support.models import SupportAction
from shine.support.occupancy import occupancy_facts, release_occupancy, resource_occupancy
from shine.support.privacy import private_roots_for, redact_payload
from shine.support.tools import ACTION_SPECS, ToolDenied, action_spec

SETUP_TERMINAL = {
    "COMPLETED",
    "DONE",
    "DONE_WITH_WARNINGS",
    "FAILED",
    "NEEDS_REVIEW",
    "CANCELLED",
    "SUPERSEDED",
}
EXECUTOR_FAILED = {"FAILED", "CANCELLED", "SUPERSEDED"}
#: An attempt that is still the live envelope for its request key.
REUSABLE_STATUSES = {"PREVIEWED", "APPROVED", "DELEGATED"}

#: The authority's own vocabulary for "this capability can really run here".
READY_CAPABILITY_STATES = {"READY", "VERIFIED_RENDER"}
#: Reconcile only proves files; it never claims a render contract.
VERIFIED_CAPABILITY_STATES = {"VERIFIED", "BOUND", "READY", "VERIFIED_RENDER"}

#: An authorization claimed but never finished (crash between the atomic claim
#: and the terminal write) may not stay APPROVED forever.  After this grace it
#: becomes NEEDS_REVIEW and a fresh preview may be requested again.
APPROVAL_GRACE = timedelta(minutes=10)

#: Final states of the authorization lifecycle after the executor has spoken.
TERMINAL_ACTION_STATUSES = {
    "SUCCEEDED",
    "FAILED",
    "REFUSED",
    "EXPIRED",
    "NEEDS_REVIEW",
    "SUPERSEDED",
    "ROLLED_BACK",
}

#: Executor outcomes of a guarded release, kept verbatim for the user.
RELEASE_OUTCOMES = {"RELEASED", "PARTIAL", "REFUSED", "UNKNOWN"}


class ActionBlocked(RuntimeError):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class ActionStale(ActionBlocked):
    def __init__(self, message: str = "环境事实已变化，请重新预览后再授权。"):
        super().__init__("PREVIEW_STALE", message)


class ActionUnknown(ActionBlocked):
    def __init__(self, message: str = "授权动作不存在。"):
        super().__init__("ACTION_NOT_FOUND", message)


class ActionInvalid(ActionBlocked):
    def __init__(self, message: str):
        super().__init__("INVALID_ACTION_PARAMS", message)


#: Parameter whitelist and scoping are *derived* from the one capability
#: declaration and its original Pydantic request model.  There is no second
#: hand-kept parameter table here.
def _param_names(kind: str) -> frozenset[str]:
    from shine.copilot import declarations as decl

    row = decl.write_action(kind)
    return frozenset(row.param_names()) if row is not None else frozenset()

#: Actions that always need a concrete project; the target may never be guessed.
_PROJECT_SCOPED_KINDS = PROJECT_SCOPED_KINDS
#: Actions whose project is optional (the executor derives it from another
#: identifier); a declared project must still be validated.
_PROJECT_OPTIONAL_KINDS = PROJECT_OPTIONAL_KINDS
#: Machine-level actions never inherit the current project.
_MACHINE_SCOPED_KINDS = MACHINE_SCOPED_KINDS

#: Actions whose submission does not claim the GPU. Setup uses this for the
#: existing task enqueue; the Worker checks resources at the relevant step.
_NO_GPU_GATE: frozenset[str] = NO_GPU_GATE_KINDS


def _validate_params(kind: str, params: dict[str, Any]) -> dict[str, Any]:
    """Use the one resolved contract: declaration + original Pydantic schema."""
    from shine.copilot.params import ParamError, validate_params

    try:
        return validate_params(kind, params)
    except ParamError as exc:
        raise ActionInvalid(str(exc)) from exc


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _occupancy(db) -> dict[str, Any]:
    """The one composed occupancy answer (existing ledgers, fail-closed)."""
    return resource_occupancy(db)


def _action_occupancy(db, kind: str, params: dict[str, Any]) -> dict[str, Any]:
    """Occupancy for this kind of action.

    Releasing idle managed services is the one action whose *purpose* is to
    clear a GPU lease, so it must not be refused by the generic lease gate.
    Operations that only enqueue work use occupancy as context. Their own
    executors enforce the resource checks when they perform the work.

    ``production_start`` deliberately keeps the full gate: a second real shoot
    must not be slipped past an active run.
    """
    if kind == "managed_release":
        return release_occupancy(db, only=set(params.get("only") or []) or None)
    if kind in _NO_GPU_GATE:
        occupancy = _occupancy(db)
        return {
            **occupancy,
            "busy": False,
            "advisory_busy": bool(occupancy.get("busy")),
            "advisory_code": occupancy.get("code"),
            "label": "由原入口执行，资源占用仅作提示。",
        }
    return _occupancy(db)


def _release_verification(value: dict[str, Any], only: set[str] | None) -> dict[str, Any]:
    """Keep the guarded executor's own verdict: RELEASED/PARTIAL/REFUSED/UNKNOWN."""
    stopped = [row for row in (value.get("stopped") or []) if isinstance(row, dict)]
    refused = [row for row in (value.get("refused") or []) if isinstance(row, dict)]
    codes = sorted(
        {
            str(row.get("outcome") or row.get("reason") or "REFUSED")
            for row in refused
        }
    )
    if not stopped and not refused:
        outcome, reason = "UNKNOWN", "NOTHING_TO_RELEASE"
    elif refused and not stopped:
        outcome, reason = "REFUSED", codes[0]
    elif refused:
        outcome, reason = "PARTIAL", codes[0]
    else:
        outcome, reason = "RELEASED", None
    return {
        "outcome": outcome,
        "reason": reason,
        "refused_codes": codes,
        "stopped": stopped,
        "refused": refused,
        "released": value.get("released") or [],
        "only": sorted(only) if only else None,
        "authority": "/api/v1/setup/managed-processes",
        "checked_at": utcnow().isoformat(),
    }


def _receipt_facts(db) -> list[dict[str, Any]]:
    """Verification receipts as stable facts (no mtime, no display fields)."""
    rows = db.scalars(
        select(AppSetting).where(AppSetting.key.like("verify-receipt:%"))
    ).all()
    facts: list[dict[str, Any]] = []
    for row in rows:
        value = row.value if isinstance(row.value, dict) else {}
        raw_files = value.get("files")
        if isinstance(raw_files, dict):
            entries = list(raw_files.values())
        elif isinstance(raw_files, list):
            entries = raw_files
        else:
            entries = []
        facts.append(
            {
                "key": row.key,
                "variant_id": value.get("variant_id"),
                "component_id": value.get("component_id"),
                "revision": value.get("revision"),
                "identity": value.get("identity"),
                "files": sorted(
                    (
                        {
                            "relative_path": str(item.get("relative_path") or ""),
                            "sha256": str(item.get("sha256") or "").lower(),
                            "size": int(item.get("size") or 0),
                        }
                        for item in entries
                        if isinstance(item, dict)
                    ),
                    key=lambda item: item["relative_path"],
                ),
            }
        )
    return sorted(facts, key=lambda item: item["key"])


def _installation_facts(selected: dict[str, Any]) -> dict[str, Any]:
    """Identity of the installation this action targets."""
    return {
        "instance_id": selected.get("instance_id"),
        "root": selected.get("root"),
        "source": selected.get("source"),
        "managed": bool(selected.get("managed")),
        "status": selected.get("status"),
        "exists": selected.get("exists"),
        "port": selected.get("port"),
    }


def _setup_target_facts(
    db, settings, params: dict[str, Any], project_id: str | None
) -> tuple[dict[str, Any], str | None]:
    from shine.comfyops.setup_summary import (
        capability_report,
        resolve_installation,
        runtime_closure_gap,
    )

    resolved = resolve_installation(
        db, settings, path=params.get("installation_path"), discovered=[]
    )
    selected = resolved.get("selected") or {}
    facts: dict[str, Any] = {
        "installation": _installation_facts(selected),
        "variant_id": params.get("variant_id"),
        "occupancy": occupancy_facts(db),
        "receipts": _receipt_facts(db),
    }
    targets = params.get("capabilities") or []
    if params.get("capability"):
        targets = [str(params.get("capability"))]
    capability_facts: dict[str, Any] = {}
    for capability in targets:
        report = capability_report(
            db, settings, capability, installation=selected, project_id=project_id,
            variant_id=params.get("variant_id"),
        )
        report_facts = report.get("facts") or {}
        missing = [
            {
                "component_id": item.get("component_id"),
                "relative_path": item.get("relative_path"),
                "size": item.get("size"),
            }
            for item in (report.get("missing_files") or [])
            if isinstance(item, dict)
        ]
        capability_facts[capability] = {
            "state": report.get("state"),
            "facts": {key: bool(value) for key, value in report_facts.items()},
            "missing": sorted(
                missing,
                key=lambda item: (str(item["component_id"]), str(item["relative_path"])),
            ),
        }
    facts["capabilities"] = capability_facts
    if params.get("capabilities"):
        gap_sig = inspect.signature(runtime_closure_gap).parameters
        gap = (
            runtime_closure_gap(db, settings, selected, capabilities=list(targets))
            if "capabilities" in gap_sig
            else runtime_closure_gap(db, settings, selected)
        )
        facts["runtime_closure"] = sorted(gap.get("missing_components") or [])
    return facts, None


def _workflow_target_facts(
    db, params: dict[str, Any]
) -> tuple[dict[str, Any], str | None]:
    from shine.comfy.workflow import graph_hash
    from shine.models import WorkflowProfile

    source = db.get(WorkflowProfile, str(params.get("source_profile_id") or ""))
    if source is None:
        return {}, "SOURCE_PROFILE_MISSING"
    return {
        "source": {
            "id": source.id,
            "project_id": source.project_id,
            "name": source.name,
            "mode": source.mode,
            "graph_sha256": graph_hash(source.graph or {}),
            "stored_graph_sha256": source.graph_sha256,
            "parameter_map": source.parameter_map or {},
            "media_transfers": source.media_transfers or {},
            "required_nodes": sorted(source.required_nodes or []),
            "checkpoint_id": source.checkpoint_id,
            "model_identity": source.model_identity or {},
            "validated_against": source.validated_against,
        },
        "requested": {
            "name": params.get("name"),
            "overrides": params.get("parameter_overrides") or {},
            "project_id": params.get("project_id") or source.project_id,
        },
    }, None


def _resource_plan_target_facts(
    db, settings, params: dict[str, Any], project_id: str | None
) -> tuple[dict[str, Any], str | None]:
    """Facts a frozen installation plan depends on.

    The resolved snapshot is the *original* resource authority: the plan may
    only be frozen against the same catalog/manifest/recipe/strategy bytes the
    person previewed, and the target storage root is part of the identity so a
    plan cannot silently move to another drive.
    """
    from shine.comfyops.resource_resolver import resolve_resources
    from shine.strategy import GenerationStrategy

    strategy_raw = params.get("strategy")
    if not isinstance(strategy_raw, dict) or not strategy_raw:
        return {}, "RESOURCE_STRATEGY_MISSING"
    try:
        strategy = GenerationStrategy.model_validate(strategy_raw)
    except Exception:  # noqa: BLE001 - an invalid strategy is a named refusal
        return {}, "RESOURCE_STRATEGY_INVALID"
    preview = resolve_resources(db, settings, strategy)
    components = sorted(
        (
            {
                "component_id": item.get("component_id"),
                "kind": item.get("kind"),
                "bytes": item.get("bytes"),
                "present": item.get("present"),
            }
            for item in preview.get("components") or []
        ),
        key=lambda item: str(item.get("component_id")),
    )
    return {
        "catalog_sha256": preview.get("catalog_sha256"),
        "manifest_sha256": preview.get("manifest_sha256"),
        "recipe_sha256": preview.get("compiled_recipe_sha256"),
        "snapshot_sha256": preview.get("snapshot_sha256"),
        "download_eligible": bool(preview.get("download_eligible")),
        "strategy_sha256": strategy.strategy_sha256(),
        "components": components,
        "requested": {
            "instance_id": params.get("instance_id"),
            "source_preference": params.get("source_preference") or "AUTO",
        },
    }, None


def _workflow_graph_target_facts(
    db, params: dict[str, Any], project_id: str | None
) -> tuple[dict[str, Any], str | None]:
    """Facts a new user-level workflow registration depends on.

    A name collision inside the same scope is the original business identity of
    that registration; the graph hash is what makes "the same draft" the same
    draft across a retry.
    """
    from shine.comfy.workflow import graph_hash
    from shine.models import WorkflowProfile

    graph = params.get("graph")
    if not isinstance(graph, dict) or not graph:
        return {}, "WORKFLOW_GRAPH_MISSING"
    name = str(params.get("name") or "")
    if not name:
        return {}, "WORKFLOW_NAME_MISSING"
    scoped = select(WorkflowProfile).where(WorkflowProfile.name == name)
    scoped = (
        scoped.where(WorkflowProfile.project_id == project_id)
        if project_id
        else scoped.where(WorkflowProfile.project_id.is_(None))
    )
    existing = db.scalar(scoped)
    return {
        "graph_sha256": graph_hash(graph),
        "name": name,
        "project_id": project_id,
        "existing_profile_id": existing.id if existing is not None else None,
        "existing_graph_sha256": existing.graph_sha256 if existing is not None else None,
        "mode": params.get("mode"),
        "required_nodes": sorted(params.get("required_nodes") or []),
        "parameter_map": params.get("parameter_map") or {},
        "media_transfers": params.get("media_transfers") or {},
    }, None


def _release_target_facts(
    db, params: dict[str, Any]
) -> tuple[dict[str, Any], str | None]:
    from shine.comfyops.renderer_release import _record_key, describe_managed_processes

    only = set(params.get("only") or []) or None
    targets = []
    for row in describe_managed_processes(db):
        if not isinstance(row, dict) or (only is not None and _record_key(row) not in only):
            continue
        targets.append(
            {
                "key": _record_key(row),
                "kind": row.get("kind"),
                "state": row.get("state"),
                "in_flight": bool(row.get("in_flight")),
                "lease_id": row.get("lease_id"),
                "lease_status": row.get("lease_status"),
                "job_id": row.get("job_id"),
                "job_status": row.get("job_status"),
            }
        )
    targets.sort(key=lambda item: str(item["key"]))
    return {"targets": targets, "occupancy": occupancy_facts(db)}, None


def _project_target_facts(db, project_id: str | None) -> tuple[dict[str, Any], str | None]:
    from shine.models import Project

    if not project_id:
        return {}, None
    project = db.get(Project, project_id)
    if project is None:
        return {}, "PROJECT_NOT_FOUND"
    return {
        "project": {
            "id": project.id,
            "language": project.language,
            "frozen_style_version_id": project.frozen_style_version_id,
            "current_document_id": project.current_document_id,
        }
    }, None


def _document_target_facts(
    db, project_id: str, text: str
) -> tuple[dict[str, Any], str | None]:
    from shine.models import Project, ProjectDocument

    project = db.get(Project, project_id)
    if project is None:
        return {}, "PROJECT_NOT_FOUND"
    documents = db.scalars(
        select(ProjectDocument)
        .where(ProjectDocument.project_id == project_id)
        .order_by(ProjectDocument.created_at)
    ).all()
    return {
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "project": {
            "id": project.id,
            "current_document_id": project.current_document_id,
            "language": project.language,
        },
        "documents": sorted(
            [{"id": row.id, "sha256": row.sha256} for row in documents],
            key=lambda item: item["id"],
        ),
    }, None


def _outline_target_facts(db, project_id: str) -> tuple[dict[str, Any], str | None]:
    from shine.materialprep import story
    from shine.models import AgentRoute, Project, ProjectDocument, ProviderProfile

    project = db.get(Project, project_id)
    if project is None:
        return {}, "PROJECT_NOT_FOUND"
    document = db.get(ProjectDocument, project.current_document_id) if project.current_document_id else None
    if document is None:
        return {}, "SCRIPT_DOCUMENT_MISSING"
    latest = db.scalar(
        select(story.AgentTaskRecord)
        .where(
            story.AgentTaskRecord.project_id == project_id,
            story.AgentTaskRecord.role_id == story.OUTLINE_RUN,
        )
        .order_by(story.AgentTaskRecord.created_at.desc())
        .limit(1)
    )
    route = db.scalar(select(AgentRoute).where(AgentRoute.role == "scriptwriter"))
    profile = db.get(ProviderProfile, route.provider_profile_id) if route is not None else None
    return {
        "document": {"id": document.id, "sha256": document.sha256, "text_length": len(document.text)},
        "latest_task": (
            {"id": latest.id, "status": latest.status, "input_hash": latest.input_hash}
            if latest is not None
            else None
        ),
        "llm_role": {
            "configured": bool(profile is not None and profile.enabled),
            "profile_id": profile.id if profile is not None else None,
            "model": profile.model if profile is not None else None,
        },
    }, None


def _storyboard_target_facts(db, project_id: str, plan_version: int | None) -> tuple[dict[str, Any], str | None]:
    from shine.director.models import DirectorEpisodePlan
    from shine.models import AgentTaskRecord, Project, WorkflowProfile

    project = db.get(Project, project_id)
    if project is None:
        return {}, "PROJECT_NOT_FOUND"
    query = select(DirectorEpisodePlan).where(DirectorEpisodePlan.project_id == project_id)
    if plan_version is not None:
        query = query.where(DirectorEpisodePlan.version == int(plan_version))
    plan = db.scalar(query.order_by(DirectorEpisodePlan.version.desc()).limit(1))
    latest = db.scalar(
        select(AgentTaskRecord)
        .where(
            AgentTaskRecord.project_id == project_id,
            AgentTaskRecord.role_id == "DIRECTOR_STORYBOARD_RUN",
        )
        .order_by(AgentTaskRecord.created_at.desc())
        .limit(1)
    )
    return {
        "project": {
            "id": project.id,
            "frozen_style_version_id": project.frozen_style_version_id,
        },
        "plan": (
            {
                "id": plan.id,
                "version": plan.version,
                "status": plan.status,
                "payload_sha256": hashlib.sha256(_canonical(plan.payload or {}).encode()).hexdigest(),
            }
            if plan is not None
            else None
        ),
        "latest_storyboard_task": (
            {"id": latest.id, "status": latest.status} if latest is not None else None
        ),
        "workflow_profiles": sorted(
            (
                {"id": row.id, "name": row.name, "graph_sha256": row.graph_sha256}
                for row in db.scalars(
                    select(WorkflowProfile).where(WorkflowProfile.project_id == project_id)
                ).all()
            ),
            key=lambda item: item["id"],
        ),
    }, None


def _production_target_facts(
    db, project_id: str, episode_ids: list[str], params: dict[str, Any]
) -> tuple[dict[str, Any], str | None]:
    from shine.director.overview import build_overview
    from shine.models import Project, WorkflowProfile
    from shine.strategy import GenerationStrategy

    project = db.get(Project, project_id)
    if project is None:
        return {}, "PROJECT_NOT_FOUND"
    overview = build_overview(db, project)
    selected = [
        row
        for row in (overview.get("episodes") or [])
        if row.get("episode_id") in set(episode_ids)
    ]
    if not selected:
        return {}, "EPISODE_NOT_IN_PLAN"
    strategy_sha = None
    if params.get("strategy"):
        strategy_sha = GenerationStrategy.model_validate(params["strategy"]).strategy_sha256()
    profile_facts = None
    profile_id = params.get("workflow_profile_id")
    if profile_id:
        profile = db.get(WorkflowProfile, str(profile_id))
        if profile is None:
            return {}, "WORKFLOW_PROFILE_NOT_FOUND"
        profile_facts = {
            "id": profile.id,
            "graph_sha256": profile.graph_sha256,
            "validated_against": profile.validated_against,
            "checkpoint_id": profile.checkpoint_id,
        }
    return {
        "episodes": sorted(
            (
                {
                    "id": row.get("episode_id"),
                    "state": row.get("state"),
                    "shots_confirmed": bool((row.get("shots") or {}).get("confirmed")),
                    "shots_landed": int((row.get("shots") or {}).get("landed_count") or 0),
                    "materials_missing": int((row.get("materials") or {}).get("missing_count") or 0),
                    "package": {
                        "id": (row.get("package") or {}).get("package_id"),
                        "revision": (row.get("package") or {}).get("revision"),
                        "startable": (row.get("package") or {}).get("startable"),
                    },
                }
                for row in selected
            ),
            key=lambda item: str(item["id"]),
        ),
        "strategy_sha256": strategy_sha,
        "workflow_profile": profile_facts,
        "frozen_style_version_id": project.frozen_style_version_id,
    }, None


def _target_fingerprint(
    db, settings, kind: str, params: dict[str, Any], project_id: str | None
) -> tuple[str | None, dict[str, Any] | None, str | None]:
    """Action-scoped facts that make a preview authorization meaningful.

    Only facts the action actually depends on are included, and every volatile
    display field (timestamps, heartbeats, expiry) is excluded.  ``None`` means
    a required fact could not be read; the caller must refuse rather than guess
    that nothing changed.
    """
    try:
        facts: dict[str, Any] = {"kind": kind, "project_id": project_id}
        reason = None
        if kind in {"setup_repair", "setup_reconcile"}:
            scoped, reason = _setup_target_facts(db, settings, params, project_id)
            facts.update(scoped)
        elif kind == "workflow_user_copy":
            scoped, reason = _workflow_target_facts(db, params)
            facts.update(scoped)
        elif kind == "resource_install_plan":
            scoped, reason = _resource_plan_target_facts(db, settings, params, project_id)
            facts.update(scoped)
        elif kind == "workflow_graph_register":
            scoped, reason = _workflow_graph_target_facts(db, params, project_id)
            facts.update(scoped)
        elif kind == "managed_release":
            scoped, reason = _release_target_facts(db, params)
            facts.update(scoped)
        elif kind == "project_create":
            facts.update({"title": params.get("title"), "language": params.get("language") or "Chinese"})
        elif kind == "support_web_search_update":
            from shine.support.agent import web_search_enabled

            facts.update({"current": bool(web_search_enabled(db))})
        elif kind == "document_import_text":
            scoped, reason = _document_target_facts(
                db, str(params.get("project_id") or ""), str(params.get("text") or "")
            )
            facts.update(scoped)
        elif kind == "project_settings_update":
            scoped, reason = _project_target_facts(db, params.get("project_id"))
            facts.update(scoped)
            facts["requested_language"] = params.get("language")
        elif kind == "story_outline_generate":
            scoped, reason = _outline_target_facts(db, str(params.get("project_id") or ""))
            facts.update(scoped)
            facts["requested"] = {
                "document_id": params.get("document_id"),
                "locked_facts": params.get("locked_facts") or [],
                "pacing": params.get("pacing") or "balanced",
            }
        elif kind == "storyboard_generate":
            scoped, reason = _storyboard_target_facts(
                db, str(params.get("project_id") or ""), params.get("plan_version")
            )
            facts.update(scoped)
        elif kind == "production_start":
            scoped, reason = _production_target_facts(
                db,
                str(params.get("project_id") or ""),
                list(params.get("episode_ids") or []),
                params,
            )
            facts.update(scoped)
        elif kind == "document_switch_basis":
            from shine.models import Project, ProjectDocument

            project = db.get(Project, str(params.get("project_id") or ""))
            document = db.get(ProjectDocument, str(params.get("document_id") or ""))
            if project is None or document is None or document.project_id != project.id:
                return None, facts, "TARGET_DOCUMENT_MISSING"
            facts.update(
                {
                    "current_document_id": project.current_document_id,
                    "document_id": document.id,
                    "document_sha256": hashlib.sha256(document.text.encode("utf-8")).hexdigest(),
                    "document_name": document.filename,
                }
            )
        elif kind == "production_control":
            from shine.production.models import ProductionRun

            run = db.get(ProductionRun, str(params.get("run_id") or ""))
            if run is None:
                return None, facts, "PRODUCTION_RUN_NOT_FOUND"
            facts.update(
                {
                    "run_id": run.id,
                    "run_status": run.status,
                    "state_version": run.state_version,
                }
            )
        elif kind == "literary_scripts_generate":
            from shine.director import pipeline
            from shine.models import Project

            project = db.get(Project, str(params.get("project_id") or ""))
            if project is None:
                return None, facts, "PROJECT_NOT_FOUND"
            plan = pipeline.current_volume_plan(db, project.id)
            if plan is None:
                return None, facts, "PIPELINE_VOLUME_PLAN_NOT_CONFIRMED"
            volume = next(
                (
                    item
                    for item in (plan.payload_json or {}).get("volumes") or []
                    if item.get("volume_id") == params.get("volume_id")
                ),
                None,
            )
            if volume is None:
                return None, facts, "PIPELINE_VOLUME_NOT_IN_PLAN"
            facts.update(
                {
                    "plan_sha256": plan.sha256,
                    "volume_id": volume.get("volume_id"),
                    "section_count": len(volume.get("sections") or []),
                }
            )
        else:
            return None, None, "UNKNOWN_KIND"
        if reason:
            return None, facts, reason
        if kind in {"setup_repair", "setup_reconcile"}:
            unreadable = (facts.get("occupancy") or {}).get("unreadable") or []
            if unreadable:
                return None, facts, "TARGET_FACTS_UNAVAILABLE"
        digest = hashlib.sha256(_canonical(facts).encode("utf-8")).hexdigest()
        return digest, facts, None
    except Exception as exc:  # noqa: BLE001 - unreadable facts are never "unchanged"
        return None, None, type(exc).__name__


def resolve_target_project(
    db, *, kind: str, params: dict[str, Any], project_id: str | None
) -> str | None:
    """The single, canonical project an action may touch.

    Three identities exist and must not be mixed: the session/current project,
    the explicitly declared project, and the project an action's own parameters
    point at.  Every distinct declared value must agree; otherwise the action
    is refused before a preview can be shown and before an approval can run.
    """
    from shine.models import Project, WorkflowProfile

    declared = {
        str(value)
        for value in (project_id, params.get("project_id"))
        if isinstance(value, str) and value.strip()
    }
    if len(declared) > 1:
        raise ActionInvalid("会话项目与动作目标项目不一致；已拒绝跨项目操作。")
    canonical = next(iter(declared), None)
    if kind in _MACHINE_SCOPED_KINDS:
        # project_create creates a *new* project; managed_release targets
        # processes, not a project.  Neither may inherit the current project.
        return None
    if kind == "workflow_user_copy":
        source = db.get(WorkflowProfile, str(params.get("source_profile_id") or ""))
        if source is None:
            raise ActionInvalid("找不到要复制的工作流合同。")
        if source.project_id and canonical and canonical != source.project_id:
            raise ActionInvalid("不能把其它项目的私有工作流复制到当前项目。")
        canonical = source.project_id or canonical
    if kind == "production_control":
        from shine.production.models import ProductionRun

        run = db.get(ProductionRun, str(params.get("run_id") or ""))
        if run is None:
            raise ActionInvalid("找不到这次生产任务。")
        if canonical and canonical != run.project_id:
            raise ActionInvalid("生产任务不属于当前项目；已拒绝跨项目控制。")
        canonical = run.project_id
    if kind in _PROJECT_SCOPED_KINDS and not canonical:
        raise ActionInvalid("这个动作必须明确指定目标项目。")
    if canonical and db.get(Project, canonical) is None:
        raise ActionInvalid("目标项目不存在。")
    return canonical


def _validate_scope(db, *, kind: str, params: dict[str, Any], project_id: str | None) -> None:
    """Project scoping is checked before a preview exists, not only at approval."""
    from shine.models import Project, WorkflowProfile

    canonical = project_id
    declared = params.get("project_id")
    if canonical and declared and canonical != declared:
        raise ActionInvalid("会话项目与动作目标项目不一致；已拒绝跨项目操作。")
    target_project = canonical or declared
    if target_project and db.get(Project, str(target_project)) is None:
        raise ActionInvalid("目标项目不存在。")
    if kind == "production_start":
        from shine.director.overview import build_overview

        if not target_project:
            raise ActionInvalid("开始拍摄必须指定项目。")
        project = db.get(Project, str(target_project))
        if project is None:
            raise ActionInvalid("目标项目不存在。")
        overview = build_overview(db, project)
        known = {row["episode_id"] for row in (overview.get("episodes") or [])}
        unknown = [item for item in (params.get("episode_ids") or []) if item not in known]
        if unknown:
            raise ActionInvalid(f"这些集不在当前分集方案里：{', '.join(unknown)}")
        profile_id = params.get("workflow_profile_id")
        if profile_id:
            profile = db.get(WorkflowProfile, str(profile_id))
            if profile is None:
                raise ActionInvalid("选择的生成方案不存在。")
            if profile.project_id is not None and profile.project_id != target_project:
                raise ActionInvalid("选择的生成方案不属于这个作品。")
        if params.get("workflow_profile_id") and params.get("strategy"):
            raise ActionInvalid("生产策略与静态生成方案不能同时指定。")


def _preflight(db, settings, kind: str, params: dict[str, Any]) -> dict[str, Any]:
    occupancy = _action_occupancy(db, kind, params)
    busy = bool(occupancy["busy"])
    common = {
        "kind": kind,
        "blocked": busy,
        "blocked_code": occupancy["code"] if busy else None,
        "occupancy": occupancy,
        "signals": occupancy.get("signals") or [],
    }
    if kind in {"setup_repair", "setup_reconcile"}:
        from shine.api_setup import PreparePlanRequest, _build_prepare_plan

        is_reconcile = kind == "setup_reconcile"
        capabilities = (
            [str(params.get("capability") or "h3")]
            if is_reconcile
            else list(params.get("capabilities") or ["h3"])
        )
        raw_variant = params.get("variant_id")
        plan_body = PreparePlanRequest(
            project_id=params.get("project_id"),
            capabilities=capabilities,
            installation_path=params.get("installation_path"),
            port=params.get("port"),
            variant_id=str(raw_variant)[:80] if raw_variant else None,
            verify_runtime=False if is_reconcile else bool(params.get("verify_runtime", True)),
            deep_scan=False if is_reconcile else bool(params.get("deep_scan", False)),
        )
        plan = _build_prepare_plan(plan_body, settings, db, discovered=[])
        selected = (plan.get("installation") or {}).get("selected") or {}
        reports = plan.get("capabilities") or {}
        plans = plan.get("plans") or []
        unknown_files = sum(int(row.get("unknown_files") or 0) for row in plans)
        unknown_bytes = sum(int(row.get("unknown_bytes") or 0) for row in plans)
        scan_incomplete = any(bool(row.get("scan_incomplete")) for row in plans)
        if not is_reconcile:
            missing_components = sorted(
                {str(cid) for row in plans for cid in (row.get("components") or [])}
            )
            return {
                **common,
                "summary": "补齐固定清单中的缺失文件，并复用统一安装器的持久任务。",
                "download_bytes": int(plan.get("download_bytes") or 0),
                "unknown_bytes": unknown_bytes,
                "unknown_files": unknown_files,
                "scan_incomplete": scan_incomplete,
                "missing_components": missing_components,
                "capabilities": {
                    key: {
                        "label": row.get("label"),
                        "state": row.get("state"),
                        "missing_count": len(row.get("missing_files") or []),
                        "unknown_count": len(row.get("unknown_files") or []),
                    }
                    for key, row in reports.items()
                },
                "installation": {
                    "instance_id": selected.get("instance_id"),
                    "managed": selected.get("managed"),
                    "status": selected.get("status"),
                    # Displayed redacted; the executor still receives `params` verbatim.
                    "path": selected.get("root") or params.get("installation_path"),
                },
                "will_start_gpu": bool(plan.get("will_start_gpu")),
                "will_modify_user_comfyui": False,
                "authorization_summary": "只下载缺失的固定文件、登记已核验文件并生成运行配置；"
                "不会删除模型、修改节点源码或终止别人的 ComfyUI。",
            }
        capability = capabilities[0]
        cap_report = reports.get(capability) or {}
        return {
            **common,
            "summary": "只读取并核验本机已有文件，并交给统一整备任务按固定清单登记通过校验的文件。",
            "missing_components": [],
            "download_bytes": 0,
            "unknown_bytes": unknown_bytes,
            "unknown_files": unknown_files,
            "scan_incomplete": scan_incomplete,
            "capability": capability,
            "capability_state": cap_report.get("state"),
            "adopt": bool(params.get("adopt")),
            "installation": {
                "instance_id": selected.get("instance_id"),
                "managed": selected.get("managed"),
                "status": selected.get("status"),
                "path_known": bool(selected.get("root") or params.get("installation_path")),
            },
            "will_start_gpu": False,
            "will_modify_user_comfyui": False,
            "authorization_summary": "核验由持久整备任务完成，不会移动或替换你的文件；"
            "adopt=true 才会把这个实例登记为工作台使用。",
        }
    if kind == "managed_release":
        from shine.comfyops.renderer_release import describe_managed_processes

        processes = describe_managed_processes(db)
        return {
            **common,
            "summary": "停止本工作台拥有且有归属证明的渲染/审片服务，释放显存。",
            "missing_components": [],
            "download_bytes": 0,
            "processes": [
                {
                    "id": row.get("id"),
                    "kind": row.get("kind"),
                    "state": row.get("state"),
                    "in_flight": bool(row.get("in_flight")),
                    "lease_status": row.get("lease_status"),
                    "ownership_evidence": row.get("ownership_evidence"),
                }
                for row in processes[:10]
            ],
            # A live lease is not a blocker here: the guarded executor re-checks
            # whether that lease belongs to an in-flight job before stopping it.
            "idle_leases": occupancy.get("idle_leases") or [],
            "will_start_gpu": False,
            "will_modify_user_comfyui": False,
            "authorization_summary": "不会强停外部 ComfyUI；提交中、生成中、审片中、"
            "结果待核对或无法证明归属的服务保持不变，底层租约释放合同原样复用。",
        }
    if kind == "workflow_user_copy":
        from shine.models import WorkflowProfile

        source = db.get(WorkflowProfile, str(params.get("source_profile_id") or ""))
        if source is None:
            raise ActionInvalid("找不到要复制的工作流合同。")
        overrides = dict(params.get("parameter_overrides") or {})
        unknown = sorted(set(overrides) - set(source.parameter_map or {}))
        if unknown:
            raise ActionInvalid(f"只能覆盖该工作流已映射的参数：{', '.join(unknown)}")
        name = str(params.get("name") or "").strip()
        if not name or name == source.name:
            raise ActionInvalid("新副本必须使用新的工作流名称。")
        before = {key: (source.parameter_map or {}).get(key) for key in overrides}
        return {
            **common,
            "summary": "复制一条新的用户级工作流合同，内置模板与源合同保持不变。",
            "missing_components": [],
            "download_bytes": 0,
            "source": {
                "profile_id": source.id,
                "name": source.name,
                "scope": "GLOBAL_BUILTIN" if source.project_id is None else "PROJECT",
                "mode": source.mode,
                "validated_against": source.validated_against,
            },
            "target": {"name": name, "project_id": params.get("project_id") or source.project_id},
            "parameter_diff": [
                {"key": key, "before": before.get(key), "after": overrides[key]}
                for key in overrides
            ],
            "will_modify_builtin_template": False,
            "will_start_gpu": False,
            "will_modify_user_comfyui": False,
            "authorization_summary": "只新增副本并调用既有工作流校验；源合同和内置模板不会被修改，"
            "可在执行后回滚删除该副本。",
        }
    if kind == "resource_install_plan":
        from shine.comfyops.resource_resolver import resolve_resources
        from shine.strategy import GenerationStrategy

        raw = params.get("strategy")
        if not isinstance(raw, dict) or not raw:
            raise ActionInvalid("缺少可解析的策略合同。")
        try:
            strategy = GenerationStrategy.model_validate(raw)
        except Exception as exc:  # noqa: BLE001 - the original contract refused it
            raise ActionInvalid(f"策略合同不符合既有定义：{str(exc)[:160]}") from exc
        preview = resolve_resources(db, settings, strategy)
        components = [
            {
                "component_id": item.get("component_id"),
                "kind": item.get("kind"),
                "bytes": item.get("bytes"),
                "present": bool(item.get("present")),
            }
            for item in preview.get("components") or []
        ]
        missing = [item for item in components if not item["present"]]
        return {
            **common,
            "summary": "把当前缺失资源冻结成一份待批准安装计划；这一步不下载任何文件。",
            "missing_components": [item["component_id"] for item in missing],
            "download_bytes": int(preview.get("download_bytes") or 0),
            "components": components,
            "snapshot_sha256": preview.get("snapshot_sha256"),
            "download_eligible": bool(preview.get("download_eligible")),
            "will_start_gpu": False,
            "will_modify_user_comfyui": False,
            "authorization_summary": "只冻结计划并登记组件；下载与安装仍由原安装计划、原 Installer "
            "与 Setup Worker 执行，不会写入节点源码。",
        }
    if kind == "workflow_graph_register":
        from shine.comfy.workflow import graph_hash, validate_graph

        graph = params.get("graph")
        if not isinstance(graph, dict) or not graph:
            raise ActionInvalid("缺少待注册的工作流图。")
        try:
            validate_graph(graph, required_nodes=list(params.get("required_nodes") or []))
        except Exception as exc:  # noqa: BLE001 - the original validator refused it
            raise ActionInvalid(f"工作流图未通过既有静态校验：{str(exc)[:160]}") from exc
        parameter_map = dict(params.get("parameter_map") or {})
        for key in ("positive_prompt", "duration"):
            if key not in parameter_map:
                raise ActionInvalid(f"parameter_map 必须映射 {key} 才能在真实生产中选择。")
        # Reuse, don't duplicate: the same name and the same graph bytes are the
        # same registration, whatever the caller's tool_call_id says (GRANT-18).
        from shine.models import WorkflowProfile

        target_project = params.get("project_id")
        name = str(params.get("name") or "")
        scoped = select(WorkflowProfile).where(WorkflowProfile.name == name)
        scoped = (
            scoped.where(WorkflowProfile.project_id == target_project)
            if target_project
            else scoped.where(WorkflowProfile.project_id.is_(None))
        )
        existing = db.scalar(scoped)
        same_graph = bool(
            existing is not None and existing.graph_sha256 == graph_hash(graph)
        )
        return {
            **common,
            "summary": "把这份图注册成用户级工作流合同，并调用原有节点校验。",
            "missing_components": [],
            "download_bytes": 0,
            "graph_sha256": graph_hash(graph),
            "node_count": len(graph),
            "node_classes": sorted(
                {str((node or {}).get("class_type")) for node in graph.values()}
            )[:24],
            "parameter_map": sorted(parameter_map),
            "media_transfers": sorted(params.get("media_transfers") or {}),
            "target": {"name": params.get("name"), "project_id": params.get("project_id")},
            "reuse_existing_profile_id": existing.id if same_graph else None,
            "name_conflict_profile_id": (
                existing.id if existing is not None and not same_graph else None
            ),
            "will_modify_builtin_template": False,
            "will_start_gpu": False,
            "will_modify_user_comfyui": False,
            "authorization_summary": "只新增一条用户级工作流合同并调用原注册与节点校验；"
            "内置模板与节点源码不变。登记成功不等于可生产，仍需原 validate 与小样任务。",
        }
    if kind == "project_create":
        return {
            **common,
            "summary": "创建一个空项目；不会导入原文、不会冻结风格、不会启动任何服务。",
            "missing_components": [],
            "download_bytes": 0,
            "target": {"title": params.get("title"), "language": params.get("language") or "Chinese"},
            "will_create_project": True,
            "will_start_gpu": False,
            "will_modify_user_comfyui": False,
            "authorization_summary": "确认后只新增一个空项目；可在项目页继续导入与创作。",
        }
    if kind == "document_import_text":
        from shine.agents.script import parse_document
        from shine.models import Project

        project = db.get(Project, str(params.get("project_id") or ""))
        if project is None:
            raise ActionInvalid("目标项目不存在。")
        text = str(params.get("text") or "")
        filename = str(params.get("filename") or "导入原文.txt")
        try:
            parsed = parse_document(filename, text.encode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - the parser's own refusal is shown
            raise ActionInvalid(f"原文无法解析：{str(exc)[:200]}") from exc
        return {
            **common,
            "summary": "按既有解析与去重规则导入这段原文；不会自动切换正式底稿。",
            "missing_components": [],
            "download_bytes": 0,
            "document": {
                "filename": filename,
                "source_format": parsed.source_format,
                "text_length": len(parsed.text),
            },
            "will_switch_basis": bool(project.current_document_id is None),
            "will_start_gpu": False,
            "will_modify_user_comfyui": False,
            "authorization_summary": "确认后与界面导入同样落库；重复内容会复用已有文档。",
        }
    if kind == "project_settings_update":
        from shine.models import Project
        from shine.services import canonical_project_language

        project = db.get(Project, str(params.get("project_id") or ""))
        if project is None:
            raise ActionInvalid("目标项目不存在。")
        try:
            language = canonical_project_language(str(params.get("language") or ""))
        except Exception as exc:  # noqa: BLE001
            raise ActionInvalid(str(getattr(exc, "detail", exc))[:240]) from exc
        return {
            **common,
            "summary": "只修改项目的用户级设置（口播语言），不改冻结风格与生产合同。",
            "missing_components": [],
            "download_bytes": 0,
            "parameter_diff": [
                {"key": "language", "before": project.language, "after": language}
            ],
            "will_start_gpu": False,
            "will_modify_user_comfyui": False,
            "authorization_summary": "确认后立即生效；如需恢复，可以再次预览改回原值。",
        }
    if kind == "story_outline_generate":
        facts, reason = _outline_target_facts(db, str(params.get("project_id") or ""))
        if reason:
            raise ActionInvalid({"SCRIPT_DOCUMENT_MISSING": "项目还没有可用的原文底稿。",
                                "PROJECT_NOT_FOUND": "目标项目不存在。"}.get(reason, reason))
        route = facts.get("llm_role") or {}
        return {
            **common,
            "summary": "用一个持久大纲任务把原文整理成大纲；相同底稿与参数不会重复调用模型。",
            "missing_components": [],
            "download_bytes": 0,
            "document": facts.get("document"),
            "latest_task": facts.get("latest_task"),
            "llm_role": route,
            "blocked_by_role": not route.get("configured"),
            "blocked_code": None if route.get("configured") else "TEXT_ROLE_NOT_READY",
            "will_start_gpu": False,
            "will_modify_user_comfyui": False,
            "authorization_summary": "确认后只排队大纲任务；大纲确认仍由你在导演页完成。",
        }
    if kind == "storyboard_generate":
        facts, reason = _storyboard_target_facts(
            db, str(params.get("project_id") or ""), params.get("plan_version")
        )
        if reason:
            raise ActionInvalid("目标项目不存在。" if reason == "PROJECT_NOT_FOUND" else reason)
        missing: list[str] = []
        if not (facts.get("project") or {}).get("frozen_style_version_id"):
            missing.append("STYLE_NOT_FROZEN")
        if not facts.get("plan"):
            missing.append("EPISODE_PLAN_MISSING")
        elif (facts.get("plan") or {}).get("status") != "CONFIRMED":
            missing.append("EPISODE_PLAN_NOT_CONFIRMED")
        return {
            **common,
            "summary": "按已确认的分集方案排队一个持久分镜任务；缺风格/方案时不会开始。",
            "missing_components": [],
            "download_bytes": 0,
            "plan": facts.get("plan"),
            "latest_task": facts.get("latest_storyboard_task"),
            "blocked": busy or bool(missing),
            "blocked_code": occupancy["code"] if busy else (missing[0] if missing else None),
            "blockers": missing,
            "will_start_gpu": False,
            "will_modify_user_comfyui": False,
            "authorization_summary": "确认后只排队分镜任务；不会替你确认镜头或冻结风格。",
        }
    if kind == "production_start":
        from shine.director.overview import build_overview
        from shine.models import Project

        project = db.get(Project, str(params.get("project_id") or ""))
        if project is None:
            raise ActionInvalid("目标项目不存在。")
        overview = build_overview(db, project)
        selected = [
            row
            for row in (overview.get("episodes") or [])
            if row.get("episode_id") in set(params.get("episode_ids") or [])
        ]
        blocked_episodes = [
            {
                "episode_id": row["episode_id"],
                "state": row.get("state"),
                "state_label": row.get("state_label"),
                "next_action": (row.get("next_action") or {}).get("label"),
                "materials_missing": int((row.get("materials") or {}).get("missing_count") or 0),
            }
            for row in selected
            if row.get("state") not in {"READY_TO_SHOOT", "READY_TO_PACKAGE", "SHOOTING",
                                        "REVIEW_READY", "DELIVERED"}
        ]
        return {
            **common,
            "summary": "把选中的集交给既有导演包与 Run 引擎开始真实拍摄；相同选择只会得到同一次运行。",
            "missing_components": [],
            "download_bytes": 0,
            "episodes": [
                {
                    "episode_id": row["episode_id"],
                    "title": row.get("title"),
                    "state": row.get("state"),
                    "state_label": row.get("state_label"),
                    "shots_confirmed": bool((row.get("shots") or {}).get("confirmed")),
                    "materials_missing": int((row.get("materials") or {}).get("missing_count") or 0),
                }
                for row in selected
            ],
            "blocked": busy or bool(blocked_episodes),
            "blocked_code": occupancy["code"] if busy else ("EPISODE_NOT_SHOOTABLE" if blocked_episodes else None),
            "blocked_episodes": blocked_episodes,
            "strategy": params.get("strategy"),
            "workflow_profile_id": params.get("workflow_profile_id"),
            "will_start_gpu": True,
            "will_modify_user_comfyui": True,
            "paid": False,
            "authorization_summary": "确认后按原门禁真实开拍（占用 GPU 并生成视频）；不替用户确认风格、主资产或审片。",
        }
    if kind == "document_switch_basis":
        from shine.models import Project, ProjectDocument

        project = db.get(Project, str(params.get("project_id") or ""))
        document = db.get(ProjectDocument, str(params.get("document_id") or ""))
        if project is None:
            raise ActionInvalid("目标项目不存在。")
        if document is None or document.project_id != project.id:
            raise ActionInvalid("这份文档不属于当前项目。")
        unchanged = project.current_document_id == document.id
        return {
            **common,
            "summary": "把选中的已导入文档切换为正式原文底稿；不会改写或删除其它文档。",
            "document": {
                "id": document.id,
                "filename": document.filename,
                "words": len((document.text or "").strip()),
            },
            "current_basis_id": project.current_document_id,
            "will_start_gpu": False,
            "will_modify_user_comfyui": False,
            "blocked": busy or unchanged,
            "blocked_code": occupancy["code"] if busy else ("BASIS_UNCHANGED" if unchanged else None),
            "authorization_summary": "确认后只切换正式底稿；原文内容保持不变。",
        }
    if kind == "production_control":
        from shine.production.models import ProductionRun

        run = db.get(ProductionRun, str(params.get("run_id") or ""))
        if run is None:
            raise ActionInvalid("找不到这次生产任务。")
        operation = params.get("operation")
        return {
            **common,
            "summary": "暂停、继续或取消这次真实生产任务；只操作这一个已存在的 run。",
            "run": {
                "id": run.id,
                "status": run.status,
                "state_version": run.state_version,
                "episode_ids": list(run.episode_ids or []),
            },
            "operation": operation,
            "will_start_gpu": True,
            "will_modify_user_comfyui": False,
            "authorization_summary": "确认后由既有 Run 引擎执行所选控制操作；不会改动其它任务。",
        }
    if kind == "literary_scripts_generate":
        from shine.director import pipeline
        from shine.models import Project

        project = db.get(Project, str(params.get("project_id") or ""))
        if project is None:
            raise ActionInvalid("目标项目不存在。")
        plan = pipeline.current_volume_plan(db, project.id)
        if plan is None:
            return {
                **common,
                "summary": "还没有已确认的分集方案，无法生成分集剧本。",
                "blocked": True,
                "blocked_code": "PIPELINE_VOLUME_PLAN_NOT_CONFIRMED",
                "will_start_gpu": False,
                "will_modify_user_comfyui": False,
                "authorization_summary": "先生成并确认分集方案，再回来生成剧本。",
            }
        volume = next(
            (
                item
                for item in (plan.payload_json or {}).get("volumes") or []
                if item.get("volume_id") == params.get("volume_id")
            ),
            None,
        )
        if volume is None:
            raise ActionInvalid("这一卷不在当前分集方案里。")
        return {
            **common,
            "summary": "为这一卷里还没有确认剧本的分节排队文学剧本任务；不会覆盖已确认剧本。",
            "volume": {
                "volume_id": volume.get("volume_id"),
                "title": volume.get("title"),
                "section_count": len(volume.get("sections") or []),
            },
            "will_start_gpu": False,
            "will_modify_user_comfyui": False,
            "paid": True,
            "authorization_summary": "确认后只排队缺失的分节剧本任务，由既有流水线执行者生成。",
        }
    if kind == "support_web_search_update":
        return {
            **common,
            "summary": "只修改 AI 管家自身的联网检索开关；不会触发下载、脚本或生产任务。",
            "web_search": bool(params.get("web_search")),
            "will_start_gpu": False,
            "will_modify_user_comfyui": False,
            "authorization_summary": "确认后写入本机偏好；可随时改回。",
        }
    raise ToolDenied(f"未知动作：{kind}")


def _request_identity(kind: str, params: dict[str, Any], actor: str, project_id: str | None) -> str:
    """The stable key of *what* is requested (not of one attempt)."""
    target = {
        "project_id": project_id or params.get("project_id"),
        "installation_path": params.get("installation_path"),
        "source_profile_id": params.get("source_profile_id"),
        "name": params.get("name"),
        "capability": params.get("capability"),
        "capabilities": sorted(params.get("capabilities") or []),
    }
    return hashlib.sha256(
        _canonical({"kind": kind, "params": params, "actor": actor, "target": target}).encode()
    ).hexdigest()


def _failure_context(row: SupportAction) -> dict[str, Any]:
    """Everything a person (or a report) needs to diagnose this exact failure."""
    verification = row.verification or {}
    raw = verification.get("reason") or row.last_error or ""
    code = str(raw).split(":", 1)[0].strip()[:80] if raw else None
    return {
        "action_id": row.id,
        "action_kind": row.kind,
        "target_project_id": row.project_id,
        "original_task_id": row.original_task_id,
        "original_error_code": code,
        "phase": row.status,
        "target_preview": (row.preview or {}).get("summary"),
        "snapshot_revision": row.snapshot_revision,
        "target_fingerprint": row.target_fingerprint,
        "executed_outcome": verification.get("outcome"),
        "can_repreview": row.status
        not in {"APPROVED", "DELEGATED", "EXECUTED"},
        "raw_error": row.last_error,
    }


def _public(row: SupportAction) -> dict[str, Any]:
    return {
        "id": row.id,
        "session_id": row.session_id,
        "project_id": row.project_id,
        "kind": row.kind,
        "label": ACTION_SPECS.get(row.kind).label if row.kind in ACTION_SPECS else row.kind,
        "status": row.status,
        "attempt_no": row.attempt_no,
        "preview": row.preview,
        "preview_hash": row.preview_hash,
        "snapshot_revision": row.snapshot_revision,
        "target_fingerprint": row.target_fingerprint,
        "approved_at": row.approved_at.isoformat() if row.approved_at else None,
        "original_task_id": row.original_task_id,
        "verification": row.verification,
        "last_error": row.last_error,
        "failure_context": _failure_context(row),
        "created_at": row.created_at.isoformat(),
        "executor": action_spec(row.kind).executor,
    }


def _is_expired(row: SupportAction) -> bool:
    raw = (row.preview or {}).get("expires_at")
    if not raw:
        return False
    try:
        return datetime.fromisoformat(str(raw)) < utcnow()
    except ValueError:
        return False


def _approval_abandoned(row: SupportAction) -> bool:
    """An APPROVED row older than the grace period never reached its executor."""
    if row.status != "APPROVED":
        return False
    stamp = row.approved_at or row.updated_at or row.created_at
    return stamp is not None and (utcnow() - stamp) > APPROVAL_GRACE


def preview_action(
    db,
    settings,
    *,
    kind: str,
    params: dict[str, Any],
    actor: str = "local-user",
    session_id: str | None = None,
    project_id: str | None = None,
    app=None,
) -> tuple[dict[str, Any], bool]:
    spec = action_spec(kind)
    params = _validate_params(kind, params)
    from shine.support import contracts as contract_registry

    # V2.1: a packaged contract must exist, match this build and be reviewed.
    # A missing or stale contract blocks *this* action only.
    contract_state = contract_registry.execution_state(kind)
    if not contract_state["executable"]:
        raise ActionBlocked(
            contract_state["state"],
            contract_state["notice"],
            details={
                "availability": contract_state.get("availability"),
                "reason": contract_state.get("reason"),
            },
        )
    if app is not None:
        api_state = contract_registry.api_contract_state(kind, app)
        if api_state["state"] != "AVAILABLE":
            raise ActionBlocked(
                api_state["state"],
                "请求参数合同发生不兼容变化，请重新确认这个操作。",
                details={"changes": api_state.get("changes"), "reason": api_state.get("reason")},
            )
    if session_id:
        from shine.support.models import SupportSession

        session = db.get(SupportSession, session_id)
        if session is None:
            raise ActionInvalid("授权动作关联的会话不存在。")
        if (
            session.project_id
            and project_id
            and session.project_id != project_id
            and kind not in _MACHINE_SCOPED_KINDS
        ):
            raise ActionInvalid("授权动作与当前会话的项目不一致。")
        if kind not in _MACHINE_SCOPED_KINDS:
            project_id = project_id or session.project_id
    canonical = resolve_target_project(
        db, kind=kind, params=params, project_id=project_id
    )
    if canonical and "project_id" in _param_names(kind):
        params["project_id"] = canonical
    else:
        params.pop("project_id", None)
    _validate_scope(db, kind=kind, params=params, project_id=canonical)
    request_key = _request_identity(kind, params, actor, canonical)

    latest = db.scalar(
        select(SupportAction)
        .where(SupportAction.request_key == request_key)
        .order_by(SupportAction.attempt_no.desc())
    )
    if latest is not None and latest.status in REUSABLE_STATUSES:
        if latest.status == "PREVIEWED" and _is_expired(latest):
            latest.status = "EXPIRED"
            db.commit()
        elif latest.status == "APPROVED" and _approval_abandoned(latest):
            # The process died between the atomic claim and the terminal write.
            # Never leave the user with an authorization that can neither run
            # nor be re-created; a fresh preview lets them confirm again.
            latest.status = "NEEDS_REVIEW"
            latest.last_error = "上一次授权未完成就中断了，请核对后重新预览并确认。"
            latest.verification = {
                **(latest.verification or {}),
                "outcome": "UNKNOWN",
                "reason": "APPROVAL_INTERRUPTED",
                "checked_at": utcnow().isoformat(),
            }
            db.commit()
        else:
            return _public(latest), True

    attempt_no = int(
        db.scalar(
            select(func.max(SupportAction.attempt_no)).where(SupportAction.request_key == request_key)
        )
        or 0
    ) + 1
    identity = f"{request_key}:{attempt_no}"

    snapshot = build_evidence_snapshot(
        db, settings, page_id="setup", project_id=canonical
    )
    revision = snapshot_revision(snapshot)
    fingerprint, _, fingerprint_reason = _target_fingerprint(
        db, settings, kind, params, canonical
    )
    preview = _preflight(db, settings, kind, params)
    preview["snapshot_revision"] = revision
    preview["fingerprint"] = {
        "available": fingerprint is not None,
        "reason": fingerprint_reason,
    }
    # V2.1: the authorization is bound to this exact capability contract, so an
    # upgrade can never replay old parameters against a changed API.
    preview["capability_contract"] = contract_registry.contract_binding(kind)
    preview["requires_authorization"] = True
    preview["expires_at"] = (utcnow() + timedelta(minutes=15)).replace(microsecond=0).isoformat()
    digest = hashlib.sha256(
        (identity + "|" + revision + "|" + str(fingerprint)).encode()
    ).hexdigest()
    row = SupportAction(
        id=new_id(),
        session_id=session_id,
        project_id=canonical,
        kind=kind,
        status="PREVIEWED",
        request_key=request_key,
        attempt_no=attempt_no,
        request_identity=identity,
        preview_hash=digest,
        snapshot_revision=revision,
        target_fingerprint=fingerprint,
        # Executor parameters are stored verbatim so a delegated call can never
        # receive a redaction placeholder as a real path.  Nothing redacted is
        # ever executed; the preview below is the redacted projection that is
        # shown and logged.
        params=dict(params),
        preview=redact_payload(preview, private_roots=private_roots_for(settings)),
        actor=actor,
        verification={"outcome": "NOT_STARTED"},
    )
    db.add(row)
    try:
        db.flush()
        from shine.services import add_audit

        add_audit(
            db,
            actor,
            "SUPPORT_ACTION_PREVIEWED",
            "support_action",
            row.id,
            {"kind": kind, "executor": spec.executor, "blocked": preview.get("blocked")},
            project_id=row.project_id,
        )
        db.commit()
    except IntegrityError:
        # Two identical previews can arrive at the same time (double click or a
        # retry).  The request identity is the unique key, so the second caller
        # adopts the winner instead of surfacing a 500.
        db.rollback()
        existing = db.scalar(
            select(SupportAction).where(SupportAction.request_identity == identity)
        )
        if existing is None:
            raise
        return _public(existing), True
    return _public(row), False


def _current_verdict(db, settings) -> dict[str, Any]:
    from shine.api_setup import setup_verdict

    return setup_verdict(settings=settings, db=db)


def _target_capabilities(row: SupportAction) -> list[str]:
    params = row.params or {}
    if row.kind == "setup_repair":
        return list(params.get("capabilities") or ["h3"])
    if row.kind == "setup_reconcile":
        return [str(params.get("capability") or "h3")]
    return []


def _capability_evidence(db, settings, row: SupportAction) -> dict[str, Any]:
    """The authority's per-capability answer for this action's own targets."""
    from shine.comfyops.setup_summary import capability_report, resolve_installation

    params = row.params or {}
    resolved = resolve_installation(
        db, settings, path=params.get("installation_path"), discovered=[]
    )
    selected = resolved.get("selected") or {}
    evidence: dict[str, Any] = {}
    for capability in _target_capabilities(row):
        report = capability_report(
            db,
            settings,
            capability,
            installation=selected,
            project_id=row.project_id,
            variant_id=params.get("variant_id"),
        )
        facts = report.get("facts") or {}
        evidence[capability] = {
            "state": report.get("state"),
            "label": report.get("label"),
            "files_verified": bool(facts.get("FILES_VERIFIED")),
            "instance_bound": bool(facts.get("INSTANCE_BOUND")),
            "workflow_validated": bool(facts.get("WORKFLOW_VALIDATED")),
            "production_ready": bool(facts.get("PRODUCTION_READY")),
            "next_step": (report.get("next_step") or {}).get("code"),
        }
    return evidence


def _target_satisfied(kind: str, evidence: dict[str, Any]) -> bool | None:
    if not evidence:
        return None
    if kind == "setup_repair":
        return all(
            str(row.get("state")) in READY_CAPABILITY_STATES
            for row in evidence.values()
        )
    if kind == "setup_reconcile":
        return all(
            str(row.get("state")) in VERIFIED_CAPABILITY_STATES and bool(row.get("files_verified"))
            for row in evidence.values()
        )
    return None


def _record_verification(db, settings, row: SupportAction) -> dict[str, Any]:
    """Read the authority, per capability, for this action's own targets."""
    try:
        verdict = _current_verdict(db, settings)
        mode = str(verdict.get("mode") or "UNKNOWN")
        try:
            evidence = _capability_evidence(db, settings, row)
            satisfied = _target_satisfied(row.kind, evidence)
            unreadable = None
        except Exception as exc:  # noqa: BLE001 - an unreadable layer stays unproven
            evidence = {}
            satisfied = None
            unreadable = type(exc).__name__
        if satisfied is True:
            outcome = "VERIFIED"
        elif satisfied is False:
            outcome = "NOT_VERIFIED"
        else:
            outcome = "UNKNOWN"
        verification = {
            "outcome": outcome,
            "authority": "/api/v1/setup/verdict",
            "capability_authority": "/api/v1/setup/summary#capabilities",
            "mode": mode,
            "label": verdict.get("label"),
            "code": verdict.get("code"),
            "targets": evidence,
            "target_satisfied": satisfied,
            "unreadable_layer": unreadable,
            "checked_at": utcnow().isoformat(),
        }
    except Exception as exc:  # noqa: BLE001
        verification = {
            "outcome": "UNKNOWN",
            "authority": "/api/v1/setup/verdict",
            "error": type(exc).__name__,
            "checked_at": utcnow().isoformat(),
        }
    row.verification = {**(row.verification or {}), **verification}
    return verification


def _setup_task_state(db, task_id: str | None) -> tuple[dict[str, Any], str]:
    """Read the durable preparation task *and prove it is still this attempt's."""
    from shine.comfyops.setup_preparation import TASK_KEY

    current_row = db.get(AppSetting, TASK_KEY)
    current = dict(current_row.value) if current_row is not None and isinstance(current_row.value, dict) else {}
    current_id = str(current.get("task_id") or "")
    if not task_id:
        return {}, "MISSING_TASK_ID"
    if not current:
        return {}, "TASK_RECORD_MISSING"
    if current_id != task_id:
        # A different request replaced the durable record.  Its status belongs
        # to that request and must never be reported as this attempt's result.
        enqueued = db.scalar(
            select(AuditEvent.id).where(
                AuditEvent.event_type == "SETUP_PREPARATION_ENQUEUED",
                AuditEvent.entity_id == task_id,
            ).limit(1)
        )
        return current, "SUPERSEDED" if enqueued else "TASK_RECORD_LOST"
    return current, str(current.get("status") or "UNKNOWN")


def _blocked_from_executor(exc: HTTPException) -> ActionBlocked:
    """Keep the executor's own refusal code instead of inventing a new one."""
    detail = exc.detail
    if isinstance(detail, dict):
        code = str(detail.get("code") or f"HTTP_{exc.status_code}")
        message = str(detail.get("message") or detail)[:240]
    elif isinstance(detail, str) and detail.isupper() and " " not in detail:
        # Several product gates raise a bare machine code (e.g.
        # STORYBOARD_STYLE_NOT_FROZEN); keep it as the real refusal code.
        code = detail
        message = detail
    else:
        code = f"HTTP_{exc.status_code}"
        message = str(detail)[:240]
    return ActionBlocked(code, message)


def _claim_approval(db, row: SupportAction, actor: str) -> bool:
    """Atomically move PREVIEWED → APPROVED exactly once."""
    result = db.execute(
        update(SupportAction)
        .where(SupportAction.id == row.id, SupportAction.status == "PREVIEWED")
        .values(status="APPROVED", approved_at=utcnow(), actor=actor, updated_at=utcnow())
    )
    db.commit()
    return bool(result.rowcount)


def approve_action(
    db,
    settings,
    session_factory,
    *,
    action_id: str,
    preview_hash: str,
    actor: str = "local-user",
    gateway=None,
    app=None,
) -> tuple[dict[str, Any], bool]:
    row = db.get(SupportAction, action_id)
    if row is None:
        raise ActionUnknown()
    if row.status != "PREVIEWED":
        return _public(row), True
    if preview_hash != row.preview_hash:
        raise ActionStale("预览内容已变化，请重新预览并授权。")
    if _is_expired(row):
        row.status = "EXPIRED"
        db.commit()
        raise ActionStale("预览已过期，请重新预览并授权。")

    # V2.1: never apply a stored authorization to a changed contract.  A
    # preview made before an upgrade must be re-confirmed, and a capability
    # that lost its reviewed contract is refused instead of half-executed.
    from shine.support import contracts as contract_registry

    contract_state = contract_registry.execution_state(row.kind)
    stored_contract = (row.preview or {}).get("capability_contract") or {}
    stored_digest = stored_contract.get("contract_digest")
    if not contract_state["executable"]:
        row.status = "EXPIRED"
        row.last_error = contract_state["state"]
        db.commit()
        raise ActionBlocked(
            contract_state["state"],
            contract_state["notice"],
            details={"action_id": row.id, "capability_contract": stored_contract},
        )
    if app is not None:
        api_state = contract_registry.api_contract_state(row.kind, app)
        if api_state["state"] != "AVAILABLE":
            row.status = "EXPIRED"
            row.last_error = "CONTRACT_CHANGED"
            db.commit()
            raise ActionBlocked(
                "CONTRACT_CHANGED",
                "请求参数合同发生不兼容变化，请重新预览并确认。",
                details={
                    "action_id": row.id,
                    "changes": api_state.get("changes"),
                    "reason": api_state.get("reason"),
                },
            )
    if not stored_digest or stored_digest != contract_state.get("contract_digest"):
        row.status = "EXPIRED"
        row.last_error = "CONTRACT_CHANGED"
        db.commit()
        raise ActionBlocked(
            "CONTRACT_CHANGED",
            "这个操作在当前版本已经调整，请重新预览并确认。",
            details={
                "action_id": row.id,
                "previewed_contract": stored_contract,
                "current_contract": contract_registry.contract_binding(row.kind),
            },
        )

    # The target project is recomputed from the stored envelope *and* the
    # session's current project.  A database edit or a session switch after the
    # preview must never redirect an already-authorized action to another
    # project.
    from shine.support.models import SupportSession

    session_project = None
    if row.session_id:
        session = db.get(SupportSession, row.session_id)
        session_project = session.project_id if session is not None else None
    try:
        canonical = resolve_target_project(
            db,
            kind=row.kind,
            params=dict(row.params or {}),
            project_id=session_project or row.project_id,
        )
    except ActionInvalid as exc:
        row.status = "EXPIRED"
        row.last_error = "TARGET_PROJECT_CONFLICT"
        db.commit()
        raise ActionBlocked(
            "TARGET_PROJECT_CONFLICT",
            exc.message,
            details={"action_project_id": row.project_id, "session_project_id": session_project},
        ) from exc
    if canonical != row.project_id:
        row.status = "EXPIRED"
        row.last_error = "TARGET_PROJECT_CONFLICT"
        db.commit()
        raise ActionBlocked(
            "TARGET_PROJECT_CONFLICT",
            "动作目标项目与会话项目不一致；历史授权不能跨项目复用。",
            details={"action_project_id": row.project_id, "canonical_project_id": canonical},
        )

    # Facts are re-read before execution: an environment change between preview
    # and approval invalidates the old authorization instead of applying it.
    snapshot = build_evidence_snapshot(
        db, settings, page_id="setup", project_id=canonical
    )
    if snapshot_revision(snapshot) != row.snapshot_revision:
        row.status = "EXPIRED"
        row.last_error = "PREVIEW_STALE"
        db.commit()
        raise ActionStale("配置在确认前发生变化，请重新确认。")

    # The snapshot covers the setup verdict and managed processes; the action
    # fingerprint covers the resources *this* action targets (installation,
    # instance binding, receipts, workflow contract, release targets).
    current_fingerprint, _, fingerprint_reason = _target_fingerprint(
        db, settings, row.kind, dict(row.params or {}), row.project_id
    )
    if row.target_fingerprint is None or current_fingerprint is None:
        row.status = "EXPIRED"
        row.last_error = "TARGET_FACTS_UNAVAILABLE"
        db.commit()
        raise ActionBlocked(
            "TARGET_FACTS_UNAVAILABLE",
            "无法读取这次操作针对的资源事实，已拒绝执行；请稍后重新预览。",
            details={"reason": fingerprint_reason, "action_fingerprint": row.target_fingerprint},
        )
    if current_fingerprint != row.target_fingerprint:
        row.status = "EXPIRED"
        row.last_error = "PREVIEW_STALE"
        db.commit()
        raise ActionStale("配置在确认前发生变化，请重新确认。")

    if not _claim_approval(db, row, actor):
        db.refresh(row)
        return _public(row), True

    db.refresh(row)
    params = dict(row.params or {})
    spec = action_spec(row.kind)
    # A reviewed alias may reuse an already-bound dispatcher; the stored
    # preview decides which one, never a runtime input.
    dispatch = str(stored_contract.get("dispatch") or row.kind)
    from shine.copilot import declarations as decl
    from shine.copilot.params import ParamError, model_payload, routed_kwargs

    declaration = decl.write_action(row.kind)
    if declaration is None:
        raise ActionBlocked("CONTRACT_NOT_DECLARED", "当前构建没有这个动作的参数合同。")

    # The second occupancy read closes the preview→approval race: a render can
    # start between the two requests.  Release uses the release-specific gate so
    # an idle lease the action exists to clear is not a self-blocker.
    occupancy = _action_occupancy(db, row.kind, params)
    if occupancy["busy"]:
        row.status = "REFUSED"
        row.last_error = str(occupancy["code"] or "BUSY")
        row.verification = {
            **(row.verification or {}),
            "outcome": "REFUSED",
            "reason": row.last_error,
            "occupancy": occupancy,
            "checked_at": utcnow().isoformat(),
        }
        db.commit()
        raise ActionBlocked(
            row.last_error,
            "本机正在生产或 GPU 使用权未释放，已拒绝执行配置动作；请等任务结束并释放显存后再试。",
            details={"occupancy": occupancy},
        )

    from shine.services import add_audit

    try:
        if dispatch in {"setup_repair", "setup_reconcile"}:
            from shine.comfyops.setup_preparation import TASK_KEY, enqueue

            if dispatch == "setup_reconcile":
                from shine.comfyops.setup_summary import resolve_installation, resolve_setup_port

                resolved = resolve_installation(
                    db, settings, path=params.get("installation_path"), discovered=[]
                )
                selected = resolved.get("selected") or {}
                target = params.get("installation_path") or selected.get("root")
                if not target:
                    raise ActionBlocked(
                        "INSTALLATION_UNKNOWN",
                        "还没有可核验的 ComfyUI 路径，请先在基础配置中选择或整备环境。",
                    )
                if resolved.get("choice_required") and not params.get("installation_path"):
                    raise ActionBlocked(
                        "INSTALLATION_SELECTION_REQUIRED",
                        "检索发现多套 ComfyUI，请先明确指定安装路径。",
                    )
                adopt = bool(params.get("adopt"))
                port = (
                    resolve_setup_port(db, settings, selected, params.get("port"))
                    if adopt
                    else (params.get("port") or selected.get("port"))
                )
                sig = inspect.signature(enqueue)
                accepts_var_kw = any(
                    p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
                )
                call_kwargs: dict[str, Any] = {
                    "project_id": params.get("project_id"),
                    "capabilities": [str(params.get("capability") or "h3")],
                    "installation_path": str(target),
                    "port": port,
                    "variant_id": params.get("variant_id"),
                    "verify_runtime": False,
                    "deep_scan": False,
                }
                if accepts_var_kw or "adopt" in sig.parameters:
                    call_kwargs["adopt"] = adopt
                if accepts_var_kw or "reconcile_only" in sig.parameters:
                    call_kwargs["reconcile_only"] = True
                value = enqueue(db, actor=actor, settings=settings, **call_kwargs)
            else:
                from shine.api_setup import PrepareRequest, setup_prepare_execute

                value = setup_prepare_execute(PrepareRequest.model_validate(params), actor, settings, db)
            row.status = "DELEGATED"
            # Bind the action to the executor's own durable task id.
            row.original_task_id = str(value.get("task_id") or "") or None
            row.verification = {
                "outcome": "PENDING",
                "executor": spec.executor,
                "task_id": row.original_task_id,
                "task_status": value.get("status"),
                "task_bound_at": utcnow().isoformat(),
                "checked_at": utcnow().isoformat(),
            }
            if row.original_task_id:
                # Record the binding in the audit ledger so the relationship
                # between this authorization and the executor task stays
                # auditable even after the AppSetting task row is replaced.
                add_audit(
                    db,
                    actor,
                    "SUPPORT_ACTION_TASK_BOUND",
                    "support_action",
                    row.id,
                    {"kind": row.kind, "task_id": row.original_task_id,
                     "setup_task_key": TASK_KEY},
                    project_id=row.project_id,
                )
        elif dispatch == "support_web_search_update":
            from shine.support.agent import set_web_search

            value = set_web_search(
                db,
                actor=actor,
                enabled=bool(params.get("web_search")),
                **routed_kwargs(
                    declaration, params, set_web_search, handled={"web_search"}
                ),
            )
            row.status = "SUCCEEDED"
            row.verification = {
                "outcome": "VERIFIED",
                "executor": spec.executor,
                "authority": "GET /api/v1/support/preferences",
                "web_search": bool(value.get("web_search")),
                "checked_at": utcnow().isoformat(),
            }
        elif dispatch == "resource_install_plan":
            from shine.api_setup import ResourcePlanRequest, create_resource_install_plan

            body = ResourcePlanRequest.model_validate(model_payload(declaration, params))
            value = create_resource_install_plan(body, settings, db)
            row.status = "EXECUTED"
            row.verification = {
                "outcome": "PLAN_FROZEN",
                # A frozen plan is not a download and is definitely not an
                # installed component: the executor's own state is reported.
                "executor": spec.executor,
                "authority": "GET /api/v1/setup/verdict",
                "installation_plan_id": value.get("plan_id"),
                "plan_sha256": value.get("sha256"),
                "plan_status": value.get("status"),
                "download_bytes": value.get("download_bytes"),
                "disk_required": value.get("disk_required"),
                "production_ready": False,
                "checked_at": utcnow().isoformat(),
            }
        elif dispatch == "workflow_graph_register":
            from shine.api_comfy import create_workflow_profile
            from shine.schemas import WorkflowProfileCreate

            body = WorkflowProfileCreate.model_validate(model_payload(declaration, params))
            created = create_workflow_profile(body, settings=settings, db=db)
            row.status = "EXECUTED"
            row.verification = {
                # Registration alone never claims production readiness: the
                # live node contract decides between VERIFIED and pending.
                "outcome": "VERIFIED" if created.validated_against else "REGISTERED_NOT_EXECUTABLE",
                "executor": spec.executor,
                "authority": "workflow_profiles.validated_against",
                "created_profile_id": created.id,
                "created_profile_name": created.name,
                "graph_sha256": created.graph_sha256,
                "validated_against": created.validated_against,
                "production_ready": False,
                "checked_at": utcnow().isoformat(),
            }
        elif dispatch == "workflow_user_copy":
            import copy

            from shine.api_comfy import create_workflow_profile
            from shine.models import WorkflowProfile
            from shine.schemas import GenerationMode, WorkflowProfileCreate

            source = db.get(WorkflowProfile, str(params.get("source_profile_id") or ""))
            if source is None:
                raise ActionBlocked("SOURCE_PROFILE_MISSING", "源工作流合同已不存在，请重新预览。")
            target_project = params.get("project_id") or source.project_id
            if source.project_id is not None and target_project != source.project_id:
                raise ActionBlocked(
                    "CROSS_PROJECT_COPY",
                    "不能把其它项目的私有工作流复制到当前项目。",
                )
            parameter_map = dict(source.parameter_map or {})
            parameter_map.update(dict(params.get("parameter_overrides") or {}))
            body = WorkflowProfileCreate.model_validate(
                {
                    **model_payload(declaration, params),
                    "mode": GenerationMode(source.mode),
                    "graph": copy.deepcopy(source.graph),
                    "parameter_map": parameter_map,
                    "media_transfers": copy.deepcopy(source.media_transfers),
                    "required_nodes": list(source.required_nodes or []),
                    "checkpoint_id": source.checkpoint_id,
                    "project_id": target_project,
                }
            )
            created = create_workflow_profile(body, settings=settings, db=db)
            row.status = "EXECUTED"
            row.verification = {
                "outcome": "VERIFIED" if created.validated_against else "STATIC_VALIDATED_LIVE_PENDING",
                "executor": spec.executor,
                "authority": "workflow_profiles.validated_against",
                "created_profile_id": created.id,
                "created_profile_name": created.name,
                "source_profile_id": source.id,
                "source_unchanged": True,
                "checked_at": utcnow().isoformat(),
            }
        elif dispatch == "managed_release":
            from shine.comfyops.renderer_release import release_managed_processes_guarded

            only = set(params.get("only") or []) or None
            value = release_managed_processes_guarded(
                session_factory,
                actor=actor,
                reason="AI 管家受控释放（用户已授权）",
                only=only,
            )
            verification = _release_verification(value, only)
            verification["executor"] = spec.executor
            row.verification = verification
            if verification["outcome"] == "RELEASED":
                row.status = "SUCCEEDED"
            elif verification["outcome"] == "REFUSED":
                row.status = "REFUSED"
                row.last_error = f"底层释放执行器拒绝：{verification['reason']}"
            else:
                row.status = "NEEDS_REVIEW"
                row.last_error = (
                    f"释放结果 {verification['outcome']}：{verification['reason'] or '需要人工核对'}"
                )
            add_audit(
                db,
                actor,
                "SUPPORT_ACTION_RELEASE_RESULT",
                "support_action",
                row.id,
                {
                    "kind": row.kind,
                    "outcome": verification["outcome"],
                    "reason": verification["reason"],
                    "stopped": [item.get("id") for item in verification["stopped"]],
                    "refused": [item.get("id") for item in verification["refused"]],
                },
                project_id=row.project_id,
            )
        elif dispatch == "project_create":
            from shine.services import create_project

            try:
                project = create_project(
                    db,
                    actor=actor,
                    **routed_kwargs(declaration, params, create_project),
                )
            except HTTPException as exc:
                raise _blocked_from_executor(exc) from exc
            except ValueError as exc:
                raise ActionBlocked("PROJECT_CREATE_REFUSED", str(exc)[:240]) from exc
            row.project_id = project.id
            row.original_task_id = project.id
            row.status = "SUCCEEDED"
            row.verification = {
                "outcome": "VERIFIED",
                "executor": spec.executor,
                "authority": "GET /api/v1/projects/{project_id}",
                "created_project_id": project.id,
                "title": project.title,
                "language": project.language,
                "checked_at": utcnow().isoformat(),
            }
        elif dispatch == "document_import_text":
            from shine.agents.script import ScriptParseError
            from shine.models import Project
            from shine.services import import_project_document

            project = db.get(Project, str(params.get("project_id") or ""))
            if project is None:
                raise ActionBlocked("PROJECT_NOT_FOUND", "目标项目不存在，请重新预览。")
            try:
                document, created = import_project_document(
                    db,
                    project,
                    filename=str(params.get("filename") or "导入原文.txt"),
                    content=str(params.get("text") or "").encode("utf-8"),
                    content_type="text/plain",
                    actor=actor,
                )
            except ScriptParseError as exc:
                raise ActionBlocked("SCRIPT_PARSE_FAILED", f"原文无法解析（{exc.code}）。") from exc
            except HTTPException as exc:
                raise _blocked_from_executor(exc) from exc
            row.original_task_id = document.id
            row.status = "SUCCEEDED"
            row.verification = {
                "outcome": "VERIFIED",
                "executor": spec.executor,
                "authority": "GET /api/v1/projects/{project_id}/script-basis",
                "document_id": document.id,
                "sha256": document.sha256,
                "text_length": len(document.text),
                "reused": not created,
                "current_basis": project.current_document_id,
                "checked_at": utcnow().isoformat(),
            }
        elif dispatch == "project_settings_update":
            from shine.models import Project
            from shine.services import update_project_settings

            project = db.get(Project, str(params.get("project_id") or ""))
            if project is None:
                raise ActionBlocked("PROJECT_NOT_FOUND", "目标项目不存在，请重新预览。")
            previous = project.language
            try:
                update_project_settings(
                    db,
                    project,
                    model_payload(declaration, params),
                    actor=actor,
                )
            except HTTPException as exc:
                raise _blocked_from_executor(exc) from exc
            row.status = "SUCCEEDED"
            row.verification = {
                "outcome": "VERIFIED",
                "executor": spec.executor,
                "authority": "GET /api/v1/projects/{project_id}",
                "previous_language": previous,
                "language": project.language,
                "checked_at": utcnow().isoformat(),
            }
        elif dispatch == "story_outline_generate":
            from shine.llm.base import LLMError
            from shine.materialprep import story
            from shine.models import Project, ProjectDocument

            if gateway is None:
                raise ActionBlocked(
                    "TEXT_ROLE_NOT_READY",
                    "生成大纲需要「故事大纲」这一个文本角色已配置；其它角色不影响当前操作。",
                )
            project = db.get(Project, str(row.project_id or ""))
            if project is None:
                raise ActionBlocked("PROJECT_NOT_FOUND", "目标项目不存在，请重新预览。")
            document_id = str(params.get("document_id") or project.current_document_id or "")
            document = db.get(ProjectDocument, document_id)
            if document is None or document.project_id != project.id:
                raise ActionBlocked("SCRIPT_DOCUMENT_MISSING", "项目还没有可用的原文底稿，请先导入原文。")
            if not document.text.strip():
                raise ActionBlocked("STORY_DOCUMENT_EMPTY", "原文底稿是空的，无法整理大纲。")
            try:
                task = story.queue_outline(
                    db,
                    project.id,
                    document,
                    gateway,
                    list(params.get("locked_facts") or []),
                    params.get("pacing") or "balanced",
                    **routed_kwargs(
                        declaration,
                        params,
                        story.queue_outline,
                        handled={"document_id", "locked_facts", "pacing"},
                    ),
                )
            except LLMError as exc:
                raise ActionBlocked(
                    "TEXT_ROLE_NOT_READY",
                    f"生成大纲需要「故事大纲」角色就绪（{getattr(exc, 'code', type(exc).__name__)}）。",
                ) from exc
            except HTTPException as exc:
                raise _blocked_from_executor(exc) from exc
            row.original_task_id = task.id
            row.status = "DELEGATED"
            row.verification = {
                "outcome": "PENDING",
                "executor": spec.executor,
                "task_id": task.id,
                "task_status": task.status,
                "document_id": document.id,
                "document_sha256": document.sha256,
                "bound_at": utcnow().isoformat(),
                "checked_at": utcnow().isoformat(),
            }
            add_audit(
                db,
                actor,
                "SUPPORT_ACTION_TASK_BOUND",
                "support_action",
                row.id,
                {"kind": row.kind, "task_id": task.id, "role": story.OUTLINE_RUN},
                project_id=row.project_id,
            )
        elif dispatch == "storyboard_generate":
            from shine.director.streamline import queue_director_storyboard

            try:
                value = queue_director_storyboard(
                    db,
                    str(row.project_id or ""),
                    actor=actor,
                    **routed_kwargs(declaration, params, queue_director_storyboard),
                )
            except HTTPException as exc:
                raise _blocked_from_executor(exc) from exc
            task_id = str(value.get("task_id") or "") or None
            row.original_task_id = task_id
            row.status = "DELEGATED"
            row.verification = {
                "outcome": "PENDING",
                "executor": spec.executor,
                "task_id": task_id,
                "task_status": value.get("status"),
                "episode_count": len(value.get("episodes") or []),
                "bound_at": utcnow().isoformat(),
                "checked_at": utcnow().isoformat(),
            }
            if task_id:
                add_audit(
                    db,
                    actor,
                    "SUPPORT_ACTION_TASK_BOUND",
                    "support_action",
                    row.id,
                    {"kind": row.kind, "task_id": task_id},
                    project_id=row.project_id,
                )
        elif dispatch == "production_start":
            from shine.director.overview import build_overview
            from shine.director.streamline import start_director_production
            from shine.models import Project

            project = db.get(Project, str(row.project_id or ""))
            if project is None:
                raise ActionBlocked("PROJECT_NOT_FOUND", "目标项目不存在，请重新预览。")
            overview = build_overview(db, project)
            selected = [
                row_view
                for row_view in (overview.get("episodes") or [])
                if row_view.get("episode_id") in set(params.get("episode_ids") or [])
            ]
            not_shootable = [item for item in selected
                             if item.get("state") not in {"READY_TO_SHOOT", "READY_TO_PACKAGE", "SHOOTING",
                                                         "REVIEW_READY", "DELIVERED"}]
            if not selected:
                raise ActionBlocked("EPISODE_NOT_IN_PLAN", "选择的集不在当前分集方案里，请重新预览。")
            if not_shootable:
                detail = "；".join(
                    f"{item.get('title') or item['episode_id']}：{item.get('state_label') or item.get('state')}"
                    for item in not_shootable[:4]
                )
                raise ActionBlocked(
                    "EPISODE_NOT_SHOOTABLE",
                    f"选中的集还没准备好（{detail}）；先处理这些集，其它集不受影响。",
                )
            try:
                value = start_director_production(
                    db,
                    project,
                    settings=settings,
                    actor=actor,
                    **routed_kwargs(declaration, params, start_director_production),
                )
            except HTTPException as exc:
                raise _blocked_from_executor(exc) from exc
            run_id = str(value.get("run_id") or "") or None
            row.original_task_id = run_id
            row.status = "DELEGATED"
            row.verification = {
                "outcome": "PENDING",
                "executor": spec.executor,
                "authority": "GET /api/v1/production-runs/{run_id}",
                "run_id": run_id,
                "run_status": value.get("run_status"),
                "package_id": value.get("package_id"),
                "package_revision": value.get("package_revision"),
                "package_built": value.get("package_built"),
                "created": value.get("created"),
                "episode_ids": value.get("episode_ids"),
                "bound_at": utcnow().isoformat(),
                "checked_at": utcnow().isoformat(),
            }
            if run_id:
                add_audit(
                    db,
                    actor,
                    "SUPPORT_ACTION_TASK_BOUND",
                    "support_action",
                    row.id,
                    {"kind": row.kind, "task_id": run_id, "run_status": value.get("run_status")},
                    project_id=row.project_id,
                )
        elif dispatch == "document_switch_basis":
            from shine.models import Project, ProjectDocument
            from shine.services import switch_script_basis

            project = db.get(Project, str(row.project_id or ""))
            document = db.get(ProjectDocument, str(params.get("document_id") or ""))
            if project is None:
                raise ActionBlocked("PROJECT_NOT_FOUND", "目标项目不存在，请重新预览。")
            if document is None or document.project_id != project.id:
                raise ActionBlocked("TARGET_DOCUMENT_MISSING", "这份文档不属于当前项目。")
            changed = switch_script_basis(db, project, document, actor=actor)
            row.status = "SUCCEEDED"
            row.verification = {
                "outcome": "VERIFIED",
                "executor": spec.executor,
                "authority": "GET /api/v1/projects/{project_id}/script-basis",
                "changed": changed,
                "current_basis_id": document.id,
                "checked_at": utcnow().isoformat(),
            }
        elif dispatch == "production_control":
            from shine.director import run_engine
            from shine.production.models import ProductionRun

            run_id = str(params.get("run_id") or "")
            if db.get(ProductionRun, run_id) is None:
                raise ActionBlocked("PRODUCTION_RUN_NOT_FOUND", "找不到这次生产任务。")
            operation = str(params.get("operation") or "")
            # Control operations open their own transaction in the original
            # executor; riding this read transaction would hold a writer lock.
            db.rollback()
            if operation == "pause":
                status = run_engine.pause_run(session_factory, run_id, worker_id=actor)
            elif operation == "resume":
                status = run_engine.resume_run(session_factory, run_id, actor=actor)
            elif operation == "cancel":
                status = run_engine.cancel_run(session_factory, run_id, actor=actor)
            else:  # pragma: no cover - already validated
                raise ActionBlocked("INVALID_OPERATION", "只支持暂停、继续或取消。")
            db.expire_all()
            row = db.get(SupportAction, action_id)
            fresh = db.get(ProductionRun, run_id)
            row.original_task_id = run_id
            row.status = "SUCCEEDED"
            row.verification = {
                "outcome": "VERIFIED",
                "executor": spec.executor,
                "authority": "GET /api/v1/production-runs/{run_id}",
                "operation": operation,
                "run_status": status,
                "state_version": fresh.state_version if fresh else None,
                "checked_at": utcnow().isoformat(),
            }
        elif dispatch == "literary_scripts_generate":
            from shine.director import pipeline

            if gateway is None:
                raise ActionBlocked(
                    "TEXT_ROLE_NOT_READY",
                    "生成分集剧本需要「分集剧本」文本角色已配置。",
                )
            try:
                tasks = pipeline.queue_scene_scripts(
                    db, str(row.project_id or ""), str(params.get("volume_id") or ""), gateway
                )
            except ValueError as exc:
                raise ActionBlocked(str(exc), f"原流水线拒绝：{exc}") from exc
            db.commit()
            task_ids = [task.id for task in tasks]
            row.original_task_id = task_ids[0] if task_ids else None
            row.status = "DELEGATED" if task_ids else "SUCCEEDED"
            row.verification = {
                "outcome": "PENDING" if task_ids else "VERIFIED",
                "executor": spec.executor,
                "authority": "GET /api/v1/projects/{project_id}/pipeline/volumes/{volume_id}",
                "task_ids": task_ids,
                "queued": len(task_ids),
                "checked_at": utcnow().isoformat(),
            }
        else:  # pragma: no cover - the registry above is exhaustive
            raise ToolDenied(f"未知动作：{row.kind}")
    except (ActionBlocked, ParamError) as caught:
        exc = (
            caught
            if isinstance(caught, ActionBlocked)
            else ActionBlocked("PARAM_NOT_ROUTABLE", str(caught))
        )
        # The authorization was already claimed.  A pre-execution refusal must
        # become a real terminal state, otherwise the APPROVED row would be
        # reused forever and the user could never preview again.
        row.status = "REFUSED"
        row.last_error = f"{exc.code}: {exc.message}"
        row.verification = {
            **(row.verification or {}),
            "outcome": "REFUSED",
            "reason": exc.code,
            "message": exc.message,
            "executor": spec.executor,
            "checked_at": utcnow().isoformat(),
        }
        db.commit()
        add_audit(
            db,
            actor,
            "SUPPORT_ACTION_REFUSED",
            "support_action",
            row.id,
            {"kind": row.kind, "code": exc.code, "message": exc.message[:200]},
            project_id=row.project_id,
        )
        db.commit()
        raise exc from caught
    except Exception as exc:  # noqa: BLE001 - a delegated executor error is reported, not hidden
        row.status = "FAILED"
        row.last_error = f"{type(exc).__name__}: {str(exc)[:240]}"
        db.commit()
        add_audit(
            db,
            actor,
            "SUPPORT_ACTION_FAILED",
            "support_action",
            row.id,
            {"kind": row.kind, "error": row.last_error},
            project_id=row.project_id,
        )
        db.commit()
        return _public(row), False

    add_audit(
        db,
        actor,
        "SUPPORT_ACTION_APPROVED",
        "support_action",
        row.id,
        {"kind": row.kind, "executor": spec.executor, "task_id": row.original_task_id},
        project_id=row.project_id,
    )
    db.commit()
    return _public(row), False


def refresh_action(db, settings, row: SupportAction) -> dict[str, Any]:
    """Refresh a delegated action from its *bind* executor task, never a lookalike."""
    if row.status not in {"DELEGATED", "EXECUTED", "APPROVED"}:
        return _public(row)

    if row.status == "APPROVED" and _approval_abandoned(row):
        row.status = "NEEDS_REVIEW"
        row.last_error = "上一次授权未完成就中断了，请核对后重新预览并确认。"
        row.verification = {
            **(row.verification or {}),
            "outcome": "UNKNOWN",
            "reason": "APPROVAL_INTERRUPTED",
            "checked_at": utcnow().isoformat(),
        }
        db.commit()
        return _public(row)

    if row.kind == "setup_repair" or (row.kind == "setup_reconcile" and row.original_task_id):
        if row.status != "DELEGATED":
            return _public(row)
        state, executor_status = _setup_task_state(db, row.original_task_id)
        if executor_status in {"MISSING_TASK_ID", "TASK_RECORD_MISSING", "TASK_RECORD_LOST"}:
            row.verification = {
                "outcome": "UNKNOWN",
                "executor": action_spec(row.kind).executor,
                "original_task_id": row.original_task_id,
                "executor_status": executor_status,
                "authority": "/api/v1/setup/verdict",
                "checked_at": utcnow().isoformat(),
            }
            row.status = "NEEDS_REVIEW"
            row.last_error = "原执行任务记录缺失，无法确认这次恢复的结果。"
            db.commit()
            return _public(row)

        if executor_status == "SUPERSEDED":
            row.verification = {
                "outcome": "UNKNOWN",
                "executor": action_spec(row.kind).executor,
                "original_task_id": row.original_task_id,
                "executor_status": "SUPERSEDED",
                "current_task_id": state.get("task_id"),
                "authority": "/api/v1/setup/verdict",
                "checked_at": utcnow().isoformat(),
            }
            row.status = "FAILED"
            row.last_error = "原执行任务已被新的整备请求取代，不能借用新任务结果。"
            db.commit()
            return _public(row)

        if executor_status not in SETUP_TERMINAL:
            row.verification = {
                "outcome": "PENDING",
                "executor": action_spec(row.kind).executor,
                "task_id": row.original_task_id,
                "task_status": executor_status,
                "current_step": state.get("current"),
                "message": (state.get("message") or "")[:200] or None,
                "authority": "/api/v1/setup/verdict",
                "checked_at": utcnow().isoformat(),
            }
            if executor_status == "UNKNOWN":
                row.status = "NEEDS_REVIEW"
                row.last_error = "原执行任务状态不明确，先保留现场并核对。"
            db.commit()
            return _public(row)

        # Terminal executor state.  The action's own capability targets decide
        # success; a globally READY machine or a COMPLETED task alone is not
        # enough evidence that *this* repair succeeded.
        verification = _record_verification(db, settings, row)
        if row.kind == "setup_reconcile":
            params = row.params or {}
            cap = str(params.get("capability") or "h3")
            target_facts = (verification.get("targets") or {}).get(cap) or {}
            layers = {
                "files_verified": bool(target_facts.get("files_verified")),
                "instance_bound": bool(target_facts.get("instance_bound")),
                "production_ready": bool(target_facts.get("production_ready")),
            }
            verification = {**verification, "layers": layers}
            if params.get("adopt") and not layers["instance_bound"]:
                verification["outcome"] = "NOT_VERIFIED"
                verification["target_satisfied"] = False
        row.verification = {
            **(row.verification or {}),
            **verification,
            "task_id": row.original_task_id,
            "executor_status": executor_status,
        }
        if executor_status in EXECUTOR_FAILED:
            row.status = "FAILED"
            row.last_error = f"原执行器状态 {executor_status}，本次恢复未成功。"
        elif row.verification.get("outcome") == "VERIFIED":
            row.status = "SUCCEEDED"
        elif executor_status in {"COMPLETED", "DONE", "DONE_WITH_WARNINGS"}:
            row.status = "NEEDS_REVIEW"
            row.last_error = "原执行器已完成，但本次恢复目标的权威能力裁决仍未就绪。"
        else:
            row.status = "NEEDS_REVIEW"
            row.last_error = f"原执行器状态 {executor_status}，需要人工核对。"
        db.commit()
        return _public(row)

    if row.kind in {"setup_reconcile"}:
        # A DELEGATED reconcile means the executor held its lock and returned
        # IN_PROGRESS without a durable task id.  Nothing proves the pending
        # result belongs to this authorization, so the global file verdict must
        # never be promoted into this action's success.
        if row.status == "DELEGATED":
            row.status = "NEEDS_REVIEW"
            row.last_error = "核验由另一轮请求执行，本次授权无法证明结果归属；请重新预览后再核验。"
            row.verification = {
                **(row.verification or {}),
                "outcome": "IN_PROGRESS",
                "attributed": False,
                "authority": "/api/v1/setup/summary#capabilities",
                "checked_at": utcnow().isoformat(),
            }
            db.commit()
        elif row.status == "EXECUTED":
            # Only the result already attributed to this authorization may settle
            # it; the global file verdict is never re-read into a success here.
            outcome = str((row.verification or {}).get("outcome") or "")
            if outcome == "VERIFIED":
                row.status = "SUCCEEDED"
            else:
                row.status = "NEEDS_REVIEW"
                row.last_error = row.last_error or "核验结果没有可归属的最终结论，请重新预览。"
            db.commit()
        return _public(row)

    if row.kind == "resource_install_plan":
        if row.status != "EXECUTED":
            return _public(row)
        from shine.comfyops.models import InstallationPlan

        plan_id = (row.verification or {}).get("installation_plan_id")
        plan = db.get(InstallationPlan, str(plan_id or "")) if plan_id else None
        if plan is None:
            # The plan row is the original ledger; without it the outcome is
            # unknown, never "installed".
            row.status = "NEEDS_REVIEW"
            row.last_error = "安装计划记录已不存在，无法确认这一步的结果。"
            row.verification = {
                **(row.verification or {}),
                "outcome": "UNKNOWN",
                "installation_plan_id": plan_id,
                "checked_at": utcnow().isoformat(),
            }
            db.commit()
            return _public(row)
        row.verification = {
            **(row.verification or {}),
            "plan_status": plan.status,
            "plan_sha256": plan.sha256,
            "original_authority": "installation_plans",
            "production_ready": False,
            "checked_at": utcnow().isoformat(),
        }
        db.commit()
        return _public(row)

    if row.kind == "workflow_graph_register":
        if row.status != "EXECUTED":
            return _public(row)
        from shine.models import WorkflowProfile

        profile_id = (row.verification or {}).get("created_profile_id")
        profile = db.get(WorkflowProfile, str(profile_id or "")) if profile_id else None
        if profile is None:
            row.status = "NEEDS_REVIEW"
            row.last_error = "注册出来的工作流合同已不存在，无法确认这一步的结果。"
            row.verification = {
                **(row.verification or {}),
                "outcome": "UNKNOWN",
                "created_profile_id": profile_id,
                "checked_at": utcnow().isoformat(),
            }
            db.commit()
            return _public(row)
        validated = profile.validated_against or None
        missing_nodes = list((validated or {}).get("missing_nodes") or [])
        missing_inputs = list((validated or {}).get("missing_inputs") or [])
        executable = bool(validated) and not missing_nodes and not missing_inputs
        row.verification = {
            **(row.verification or {}),
            "created_profile_id": profile.id,
            "graph_sha256": profile.graph_sha256,
            "validated_against": validated,
            # Registration is not production: the node contract and a real
            # sample task decide the last mile.
            "outcome": "VERIFIED" if executable else "REGISTERED_NOT_EXECUTABLE",
            "node_contract_executable": executable,
            "missing_nodes": missing_nodes,
            "missing_input_count": len(missing_inputs),
            "production_ready": False,
            "checked_at": utcnow().isoformat(),
        }
        db.commit()
        return _public(row)

    if row.kind == "managed_release":
        if row.status in {"DELEGATED", "EXECUTED"}:
            # Legacy or interrupted release: there is no attributable executor
            # report.  Never synthesise "RELEASED" from the global process list.
            row.status = "NEEDS_REVIEW"
            row.last_error = "这次释放没有留下可归属的执行结果，请核对服务状态后重新预览。"
            row.verification = {
                **(row.verification or {}),
                "outcome": "UNKNOWN",
                "attributed": False,
                "authority": "/api/v1/setup/managed-processes",
                "checked_at": utcnow().isoformat(),
            }
            db.commit()
            return _public(row)
        # A terminal release keeps the executor's own outcome.  Only append the
        # current occupancy as observational context.
        occupancy = _action_occupancy(db, row.kind, dict(row.params or {}))
        row.verification = {
            **(row.verification or {}),
            "current_occupancy": {
                "busy": occupancy["busy"],
                "code": occupancy["code"],
                "checked_at": utcnow().isoformat(),
            },
        }
        db.commit()
        return _public(row)

    if row.kind == "story_outline_generate":
        if row.status != "DELEGATED":
            return _public(row)
        from shine.models import AgentTaskRecord

        task = db.get(AgentTaskRecord, str(row.original_task_id or ""))
        if task is None:
            row.status = "NEEDS_REVIEW"
            row.last_error = "找不到这次大纲执行任务的记录，无法确认结果；请重新预览。"
            row.verification = {
                **(row.verification or {}),
                "outcome": "UNKNOWN",
                "attributed": False,
                "executor": action_spec(row.kind).executor,
                "checked_at": utcnow().isoformat(),
            }
        else:
            status = str(task.status or "UNKNOWN")
            row.verification = {
                **(row.verification or {}),
                "task_id": task.id,
                "task_status": status,
                "stage": (task.trace or {}).get("stage"),
                "cursor": (task.trace or {}).get("cursor"),
                "checked_at": utcnow().isoformat(),
            }
            if status == "SUCCEEDED":
                row.status = "SUCCEEDED"
                row.verification["outcome"] = "VERIFIED"
                row.verification["note"] = "大纲已生成；确认大纲仍是用户在导演页的裁决。"
            elif status in {"FAILED", "CANCELLED", "SUPERSEDED"}:
                row.status = "FAILED" if status != "SUPERSEDED" else "SUPERSEDED"
                row.last_error = f"原大纲任务状态 {status}。"
                row.verification["outcome"] = "FAILED"
            elif status == "NEEDS_REVIEW":
                row.status = "NEEDS_REVIEW"
                row.last_error = "大纲任务结果不确定，需要先核对，不能自动重试。"
                row.verification["outcome"] = "UNKNOWN"
        db.commit()
        return _public(row)

    if row.kind == "storyboard_generate":
        if row.status != "DELEGATED":
            return _public(row)
        from shine.director.streamline import director_storyboard_state

        try:
            state = director_storyboard_state(db, str(row.project_id or ""))
        except Exception as exc:  # noqa: BLE001 - unreadable progress is unknown
            state = {"task_id": None, "status": None, "error": type(exc).__name__}
        if not row.original_task_id or str(state.get("task_id") or "") != str(row.original_task_id):
            # A newer storyboard run replaced this task's record; its result is
            # not this authorization's result.
            row.status = "NEEDS_REVIEW"
            row.last_error = "分镜记录已被新的任务取代或无法归属，本次授权不能借用它的结果。"
            row.verification = {
                **(row.verification or {}),
                "outcome": "UNKNOWN",
                "attributed": False,
                "task_id": row.original_task_id,
                "current_task_id": state.get("task_id"),
                "checked_at": utcnow().isoformat(),
            }
        else:
            status = str(state.get("status") or "UNKNOWN")
            episodes = state.get("episodes") or []
            blocked = [row_ep for row_ep in episodes if row_ep.get("status") == "BLOCKED"]
            row.verification = {
                **(row.verification or {}),
                "task_id": state.get("task_id"),
                "task_status": status,
                "episode_count": len(episodes),
                "blocked_episodes": [
                    {"episode_id": item.get("episode_id"), "blocker": item.get("blocker")}
                    for item in blocked[:5]
                ],
                "checked_at": utcnow().isoformat(),
            }
            if status in {"SUCCEEDED", "COMPLETED"} and not blocked:
                row.status = "SUCCEEDED"
                row.verification["outcome"] = "VERIFIED"
                row.verification["note"] = "分镜已落库；确认镜头仍是用户在导演页的裁决。"
            elif status in {"FAILED", "CANCELLED", "SUPERSEDED"}:
                row.status = "FAILED" if status != "SUPERSEDED" else "SUPERSEDED"
                row.last_error = f"原分镜任务状态 {status}。"
                row.verification["outcome"] = "FAILED"
            elif status in {"NEEDS_REVIEW", "UNKNOWN"} or blocked:
                row.status = "NEEDS_REVIEW"
                row.last_error = "分镜任务有未完成或结果不确定的集，需要先核对。"
                row.verification["outcome"] = "UNKNOWN"
            else:
                row.verification["outcome"] = "PENDING"
        db.commit()
        return _public(row)

    if row.kind == "production_start":
        if row.status != "DELEGATED":
            return _public(row)
        from sqlalchemy import select as _select

        from shine.production.models import TERMINAL_RUN_STATUSES, ProductionRun, ProductionRunEpisode

        run = db.get(ProductionRun, str(row.original_task_id or ""))
        if run is None:
            row.status = "NEEDS_REVIEW"
            row.last_error = "找不到这次生产运行的记录，无法确认结果；请核对后再操作。"
            row.verification = {
                **(row.verification or {}),
                "outcome": "UNKNOWN",
                "attributed": False,
                "authority": "GET /api/v1/production-runs/{run_id}",
                "checked_at": utcnow().isoformat(),
            }
            db.commit()
            return _public(row)
        episodes = list(
            db.scalars(_select(ProductionRunEpisode).where(ProductionRunEpisode.run_id == run.id)).all()
        )
        episode_status = sorted({str(item.status) for item in episodes})
        delivered = all(item.status == "DELIVERED" for item in episodes) if episodes else False
        row.verification = {
            **(row.verification or {}),
            "run_id": run.id,
            "run_status": run.status,
            "episode_count": len(episodes),
            "episode_statuses": episode_status,
            "delivered": delivered,
            "checked_at": utcnow().isoformat(),
        }
        if run.status in TERMINAL_RUN_STATUSES:
            if run.status == "COMPLETED" and delivered:
                row.status = "SUCCEEDED"
                row.verification["outcome"] = "VERIFIED"
            elif run.status == "COMPLETED":
                row.status = "NEEDS_REVIEW"
                row.last_error = "拍摄已结束，但仍有集未完成审片/交付；这部分必须由你确认。"
                row.verification["outcome"] = "PENDING_REVIEW"
            else:
                row.status = "FAILED"
                row.last_error = f"原生产运行状态 {run.status}。"
                row.verification["outcome"] = "FAILED"
        else:
            row.verification["outcome"] = "PENDING"
        db.commit()
        return _public(row)

    if row.kind == "literary_scripts_generate":
        if row.status != "DELEGATED":
            return _public(row)
        from shine.models import AgentTaskRecord

        task_ids = [str(item) for item in (row.verification or {}).get("task_ids") or []]
        tasks = (
            list(db.scalars(select(AgentTaskRecord).where(AgentTaskRecord.id.in_(task_ids))).all())
            if task_ids
            else []
        )
        statuses = [str(task.status) for task in tasks]
        row.verification = {
            **(row.verification or {}),
            "task_ids": task_ids,
            "task_statuses": statuses,
            "checked_at": utcnow().isoformat(),
        }
        if not task_ids or len(tasks) != len(task_ids):
            row.status = "NEEDS_REVIEW"
            row.last_error = "分集剧本任务记录缺失，无法确认本次授权的结果，不能自动重试。"
            row.verification["outcome"] = "UNKNOWN"
        elif all(status == "SUCCEEDED" for status in statuses):
            row.status = "SUCCEEDED"
            row.verification["outcome"] = "VERIFIED"
            row.verification["note"] = "分集剧本已由原流水线生成；确认剧本仍是用户在导演页的裁决。"
        elif any(status in {"FAILED", "CANCELLED", "SUPERSEDED"} for status in statuses):
            row.status = "FAILED"
            row.last_error = "至少一个分集剧本任务失败或被取消。"
            row.verification["outcome"] = "FAILED"
        elif any(status == "NEEDS_REVIEW" for status in statuses):
            row.status = "NEEDS_REVIEW"
            row.last_error = "分集剧本任务出现不确定结果，需要先核对，不能自动重提。"
            row.verification["outcome"] = "UNKNOWN"
        else:
            row.verification["outcome"] = "PENDING"
        db.commit()
        return _public(row)

    return _public(row)


def action_public(row: SupportAction) -> dict[str, Any]:
    return _public(row)


def rollback_action(
    db,
    settings,
    *,
    action_id: str,
    actor: str = "local-user",
) -> dict[str, Any]:
    """Delete only the user-level copy this action created.

    The source contract and any built-in template are never touched.  A copy that
    is already referenced by real work is kept and the rollback is refused.
    """
    from shine.models import Job, ProductionSeries, WorkflowProfile

    row = db.get(SupportAction, action_id)
    if row is None:
        raise ActionUnknown()
    if row.kind != "workflow_user_copy":
        raise ActionBlocked("ROLLBACK_NOT_SUPPORTED", "该授权动作不支持回滚。")
    if row.status == "ROLLED_BACK":
        return _public(row)
    created_id = (row.verification or {}).get("created_profile_id")
    if not created_id:
        raise ActionBlocked("ROLLBACK_UNAVAILABLE", "没有可回滚的用户副本。")
    profile = db.get(WorkflowProfile, str(created_id))
    if profile is not None and row.project_id and profile.project_id != row.project_id:
        raise ActionBlocked(
            "CROSS_PROJECT_ROLLBACK",
            "该副本不属于本次授权动作的项目，不能删除。",
        )
    jobs = db.scalar(select(func.count(Job.id)).where(Job.workflow_profile_id == created_id))
    series = db.scalar(
        select(func.count(ProductionSeries.id)).where(ProductionSeries.workflow_profile_id == created_id)
    )
    if jobs or series:
        raise ActionBlocked(
            "PROFILE_IN_USE",
            "该副本已被生产任务引用，不能删除；请保留现场并由开发者处理。",
            details={"jobs": int(jobs or 0), "series": int(series or 0)},
        )
    if profile is not None:
        db.delete(profile)
    row.status = "ROLLED_BACK"
    row.verification = {
        **(row.verification or {}),
        "outcome": "ROLLED_BACK",
        "rolled_back_at": utcnow().isoformat(),
        "rolled_back_profile_id": created_id,
    }
    from shine.services import add_audit

    add_audit(
        db,
        actor,
        "SUPPORT_WORKFLOW_COPY_ROLLED_BACK",
        "support_action",
        row.id,
        {"created_profile_id": created_id, "deleted": profile is not None},
        project_id=row.project_id,
    )
    db.commit()
    return _public(row)


# ---------------------------------------------------------------------------
# V3.1: one task grant, many sub-actions, the same executor
# ---------------------------------------------------------------------------

#: Capabilities whose declared cost is a *count* of new business artefacts.
#: Only declared capabilities appear here: a name without a declaration has
#: no contract and must never be costed (the old fake names are gone).
_COUNT_COSTS: dict[str, dict[str, int]] = {
    "workflow_graph_register": {"max_new_workflow_profiles": 1},
    "workflow_user_copy": {"max_new_workflow_profiles": 1},
    "production_start": {"max_test_inference_jobs": 1},
}


def _grant_cost(kind: str, preview: dict[str, Any]) -> dict[str, int]:
    """The server-computed cost of one step, never a model estimate."""
    cost: dict[str, int] = {}
    download_bytes = preview.get("download_bytes")
    if isinstance(download_bytes, int) and download_bytes > 0:
        cost["max_download_bytes"] = download_bytes
        # A download writes the same bytes to disk, plus a little overhead.
        cost["max_disk_write_bytes"] = download_bytes
    cost.update(_COUNT_COSTS.get(kind, {}))
    return cost


def _session_factory(db):
    from shine.db import make_session_factory

    return make_session_factory(db.get_bind())


def execute_under_grant(
    db,
    settings,
    *,
    grant_id: str,
    capability_id: str,
    params: dict[str, Any],
    tool_call_id: str,
    actor: str = "local-user",
    session_id: str | None = None,
    expected_target: dict[str, Any] | None = None,
    app=None,
    gateway=None,
) -> dict[str, Any]:
    """Execute one reviewed capability inside an already authorized task grant.

    This is the *only* autonomous write path of the copilot.  It does not
    replace any existing check: it runs the original ``preview_action`` (which
    re-reads the snapshot, the target fingerprint, the capability contract and
    the original Pydantic parameters) and then the original ``approve_action``
    (which re-verifies all of them again before the atomic claim).  What it adds
    is the grant layer:

    1. the envelope must be usable (actor, session, status, validity, revoke),
    2. the capability must be inside the frozen scope and permission set,
    3. its contract must be the one frozen at authorization time,
    4. its server-computed cost must fit the frozen budget,
    5. one ``tool_call_id`` maps to exactly one ``SupportAction`` row.

    A refusal is returned as a structured receipt, never as an exception the
    model could interpret as "try another way".
    """
    from shine.copilot import task_grant as grant_contract
    from shine.support import grants as grant_service
    from shine.support.tools import ToolTier

    expected = dict(expected_target or {})
    receipt: dict[str, Any] = {
        "status": "REFUSED",
        "capability_id": capability_id,
        "tool_call_id": tool_call_id,
        "grant_id": grant_id,
        "action_id": None,
        "original_task_id": None,
        "observed_facts": {},
        "authority": None,
        "resource_usage": {},
        "next_available_actions": [],
        "error_code": None,
        "message": "",
    }
    try:
        loaded = grant_service.load(db, grant_id)
        grant_contract.assert_identity(
            loaded.grant, actor=actor, session_id=session_id
        )
        grant_contract.assert_usable(loaded.grant, now=utcnow())
        grant_contract.assert_contract_frozen(loaded.grant, capability_id)
        if not grant_contract.capability_allowed(loaded.grant, capability_id):
            raise grant_contract.GrantError(
                "GRANT_PERMISSION_NOT_GRANTED",
                "这次授权没有包含该操作所需的能力权限。",
                details={"capability_id": capability_id},
            )
        spec = action_spec(capability_id)
        if spec.tier is not ToolTier.CONFIRM:
            raise grant_contract.GrantError(
                "CAPABILITY_NOT_WRITABLE",
                "这个操作不是可以在授权内自动执行的写动作。",
                details={"capability_id": capability_id},
            )
        session = None
        if session_id:
            from shine.support.models import SupportSession

            session = db.get(SupportSession, session_id)
        scope_project = (
            str(params.get("project_id") or "")
            or (session.project_id if session is not None else None)
        )
        grant_contract.assert_project_scope(
            loaded.grant, project_id=scope_project, capability_id=capability_id
        )
        installation_id = (
            expected.get("installation_id")
            or params.get("installation_id")
            or params.get("instance_id")
        )
        grant_contract.assert_installation_scope(
            loaded.grant, installation_id=installation_id
        )
        grant_service.assert_tool_call_budget(loaded.grant)
        grant_service.assert_runtime_budget(
            loaded.grant,
            started_at=grant_contract.parse_iso(loaded.grant["created_at"])
            if loaded.grant.get("created_at")
            else None,
        )

        # --- idempotency: one tool call is one action, whatever is retried ---
        identity = grant_contract.child_key(
            grant_id,
            tool_call_id,
            capability_id,
            dict(params or {}),
            str(expected.get("target_identity") or installation_id or scope_project or ""),
        )
        existing = db.scalar(
            select(SupportAction).where(SupportAction.grant_tool_key == identity)
        )
        if existing is not None:
            return _grant_receipt(
                db, settings, existing, receipt, reused=True
            )

        # --- the original preview: facts, fingerprint, contract, preflight ---
        prepared, _ = preview_action(
            db,
            settings,
            kind=capability_id,
            params=dict(params or {}),
            actor=actor,
            session_id=session_id,
            project_id=scope_project or None,
            app=app,
        )
        row = db.get(SupportAction, str(prepared.get("id")))
        if row is None:
            raise ActionUnknown()
        reuse_profile_id = (prepared.get("preview") or {}).get("reuse_existing_profile_id")
        if reuse_profile_id:
            # The same name and the same graph bytes already exist.  Report the
            # existing original record instead of creating an infinite copy.
            reuse = _reuse_existing_profile(db, row, str(reuse_profile_id), grant_id,
                                            tool_call_id, identity)
            return _grant_receipt(db, settings, row, receipt, reused=True) | {
                "observed_facts": {**reuse, "reused_existing_action": True},
            }
        cost = _grant_cost(capability_id, prepared.get("preview") or {})

        if row.status in {"SUCCEEDED", "DELEGATED", "EXECUTED"}:
            # The business identity already produced this work (for example the
            # same installation plan).  Bind the grant and report the original
            # state instead of creating a duplicate.
            _bind_grant(db, row, grant_id, tool_call_id, identity)
            return _grant_receipt(db, settings, row, receipt, reused=True)
        if row.status in TERMINAL_ACTION_STATUSES and row.status not in {"EXPIRED"}:
            _bind_grant(db, row, grant_id, tool_call_id, identity)
            return _grant_receipt(db, settings, row, receipt, reused=True)
        if row.status == "APPROVED" and _approval_abandoned(row):
            raise grant_contract.GrantError(
                "PREVIEW_STALE",
                "上一步授权未完成就中断了，请重新发起这一步。",
                details={"action_id": row.id},
            )

        # --- reserve before the external call, exactly like a submit intent ---
        # Only the *resource* cost is reserved here.  The tool-call counter is
        # spent exactly once, by ``record_tool_call`` below, so one call can
        # never consume two slots.
        grant_service.reserve(
            db,
            grant_id,
            capability_id=capability_id,
            cost=cost,
        )
        _bind_grant(db, row, grant_id, tool_call_id, identity)
        grant_service.record_tool_call(
            db,
            grant_id,
            tool_call_id=tool_call_id,
            capability_id=capability_id,
            status="SUBMITTED",
            action_id=row.id,
            detail={"target_fingerprint": row.target_fingerprint},
        )

        result, _ = approve_action(
            db,
            settings,
            _session_factory(db),
            action_id=row.id,
            preview_hash=row.preview_hash,
            actor=actor,
            gateway=gateway,
            app=app,
        )
        db.refresh(row)
        grant_service.bind_sub_action(
            db,
            grant_id,
            action_id=row.id,
            tool_call_id=tool_call_id,
            capability_id=capability_id,
            project_id=row.project_id,
            original_task_id=row.original_task_id,
        )
        # Settle THIS step's reservation against the executor's real receipt.
        # A frozen installation plan reports its own download bytes; a delegated
        # task has no final bytes yet and keeps its reservation until readback.
        actual_cost = dict(cost)
        if capability_id == "resource_install_plan":
            verified_bytes = (row.verification or {}).get("download_bytes")
            if isinstance(verified_bytes, int) and verified_bytes >= 0:
                actual_cost["max_download_bytes"] = verified_bytes
                actual_cost["max_disk_write_bytes"] = verified_bytes
        grant_service.settle(db, grant_id, reserved=cost, actual=actual_cost,
                             step_key=tool_call_id)
        receipt = _grant_receipt(db, settings, row, receipt, reused=False)
        receipt["observed_facts"]["executor_status"] = result.get("status")
        return receipt
    except ActionBlocked as exc:
        receipt.update(
            {
                "status": "BLOCKED",
                "error_code": exc.code,
                "message": exc.message,
                "observed_facts": {"details": exc.details},
                "next_available_actions": _next_actions_after_block(exc.code),
            }
        )
        return receipt
    except grant_contract.GrantError as exc:
        receipt.update(
            {
                "status": "BLOCKED",
                "error_code": exc.code,
                "message": exc.message,
                "observed_facts": {"details": exc.details},
                "next_available_actions": _next_actions_after_block(exc.code),
            }
        )
        return receipt


def _reuse_existing_profile(
    db,
    row: SupportAction,
    profile_id: str,
    grant_id: str,
    tool_call_id: str,
    identity: str,
) -> dict[str, Any]:
    """Record a reuse of an already-registered identical user profile."""
    from shine.models import WorkflowProfile

    profile = db.get(WorkflowProfile, profile_id)
    validated = (profile.validated_against or None) if profile is not None else None
    missing_nodes = list((validated or {}).get("missing_nodes") or [])
    missing_inputs = list((validated or {}).get("missing_inputs") or [])
    executable = bool(validated) and not missing_nodes and not missing_inputs
    row.status = "EXECUTED"
    row.verification = {
        "outcome": "VERIFIED" if executable else "REGISTERED_NOT_EXECUTABLE",
        "executor": "shine.api_comfy.create_workflow_profile",
        "authority": "workflow_profiles.validated_against",
        "created_profile_id": profile_id,
        "reused_existing_profile": True,
        "graph_sha256": profile.graph_sha256 if profile is not None else None,
        "node_contract_executable": executable,
        "missing_nodes": missing_nodes,
        "missing_input_count": len(missing_inputs),
        "production_ready": False,
        "checked_at": utcnow().isoformat(),
    }
    _bind_grant(db, row, grant_id, tool_call_id, identity)
    db.commit()
    return {
        "created_profile_id": profile_id,
        "verification_outcome": row.verification["outcome"],
        "reused_existing_profile": True,
        "node_contract_executable": executable,
    }


def _next_actions_after_block(code: str) -> list[str]:
    """What the model may usefully try next, by refusal family.

    A refusal that needs a person says so; a refusal that is out of scope says
    so.  None of them suggests "bypass the check".
    """
    if code in {"SCOPE_EXCEEDED", "GRANT_PERMISSION_NOT_GRANTED"}:
        return ["report_blocked", "finish"]
    if code in {"GRANT_BUDGET_EXCEEDED", "GRANT_TOOL_CALL_BUDGET_EXHAUSTED",
                "GRANT_RUNTIME_BUDGET_EXHAUSTED"}:
        return ["report_blocked", "finish"]
    if code in {"GRANT_EXPIRED", "GRANT_REVOKED", "GRANT_NOT_RUNNING", "GRANT_CANCELLED"}:
        return ["finish"]
    if code in {"GRANT_CONTRACT_CHANGED"}:
        return ["report_blocked", "finish"]
    if code in {"PREVIEW_STALE", "TARGET_FACTS_UNAVAILABLE", "BUSY", "GPU_BUSY"}:
        return ["re_inspect", "finish"]
    return ["re_inspect", "report_blocked", "finish"]


def _bind_grant(
    db, row: SupportAction, grant_id: str, tool_call_id: str, identity: str
) -> None:
    """Persist the grant identity on the original action row.

    The unique constraint on ``grant_tool_key`` is what makes a concurrent
    duplicate collapse here instead of executing twice.
    """
    if row.grant_tool_key == identity and row.grant_id == grant_id:
        return
    try:
        db.execute(
            update(SupportAction)
            .where(SupportAction.id == row.id)
            .values(
                grant_id=row.grant_id or grant_id,
                tool_call_id=row.tool_call_id or tool_call_id,
                grant_tool_key=row.grant_tool_key or identity,
                updated_at=utcnow(),
            )
        )
        db.commit()
    except IntegrityError:
        db.rollback()
    db.refresh(row)


def _grant_receipt(
    db,
    settings,
    row: SupportAction,
    base: dict[str, Any],
    *,
    reused: bool,
) -> dict[str, Any]:
    """The uniform tool output: original status, original task, real evidence."""
    public = _public(row)
    verification = row.verification or {}
    outcome = str(verification.get("outcome") or "")
    return {
        **base,
        "status": str(public.get("status") or row.status),
        "action_id": row.id,
        "original_task_id": row.original_task_id,
        "observed_facts": {
            "action_status": row.status,
            "verification_outcome": outcome,
            "executor": verification.get("executor"),
            "task_status": verification.get("task_status"),
            "created_profile_id": verification.get("created_profile_id"),
            "installation_plan_id": verification.get("installation_plan_id"),
            "target_fingerprint": row.target_fingerprint,
            "reused_existing_action": bool(reused),
        },
        "authority": verification.get("authority"),
        "resource_usage": {
            key: value
            for key, value in (row.preview or {}).get("fingerprint", {}).items()
            if key in {"available", "reason"}
        },
        "next_available_actions": _next_actions_for_status(row.status),
        "error_code": row.last_error if row.status in TERMINAL_ACTION_STATUSES else None,
        "message": str(public.get("last_error") or ""),
    }


def _next_actions_for_status(status: str) -> list[str]:
    if status == "SUCCEEDED":
        return ["re_inspect", "finish"]
    if status in {"DELEGATED", "EXECUTED", "APPROVED"}:
        return ["read_original_task", "finish"]
    if status == "NEEDS_REVIEW":
        return ["report_blocked", "finish"]
    if status in {"FAILED", "REFUSED", "EXPIRED", "SUPERSEDED"}:
        return ["re_inspect", "report_blocked", "finish"]
    return ["re_inspect", "finish"]


def read_under_grant(
    db,
    settings,
    *,
    action_id: str,
    grant_id: str,
) -> dict[str, Any]:
    """Read one sub-action's original result back, never resubmitting it."""

    row = db.get(SupportAction, action_id)
    if row is None:
        raise ActionUnknown()
    if row.grant_id and row.grant_id != grant_id:
        raise ActionBlocked(
            "ACTION_NOT_IN_GRANT",
            "这个动作不属于当前任务授权，不能读取。",
            details={"action_id": action_id},
        )
    public = refresh_action(db, settings, row)
    db.refresh(row)
    return {
        "status": str(public.get("status") or row.status),
        "action_id": row.id,
        "original_task_id": row.original_task_id,
        "observed_facts": {
            "action_status": row.status,
            "verification": row.verification or {},
        },
        "authority": (row.verification or {}).get("authority"),
        "error_code": row.last_error,
        "message": str(public.get("last_error") or ""),
    }
