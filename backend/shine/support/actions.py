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

SETUP_TERMINAL = {"COMPLETED", "FAILED", "NEEDS_REVIEW", "CANCELLED", "SUPERSEDED"}
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

#: Actions that do not compete for the GPU.  They must not be refused by the
#: production/lease gate: a project import, a story outline or a setting edit
#: does not touch the renderer.  Their own executors still enforce every gate.
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
    Configuration changes keep the full fail-closed protection.  User-level
    edits that never touch the renderer report occupancy as *context* only: they
    are still refused if the product's own executor says so, but a running
    render must not make "import the novel" or "change the language" impossible.

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
            "label": "不占用 GPU 的用户级操作；生产占用仅作提示。",
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
        gap = runtime_closure_gap(db, settings, selected)
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
    if kind == "setup_repair":
        from shine.comfyops.setup_summary import capability_report, resolve_installation, runtime_closure_gap

        resolved = resolve_installation(db, settings, path=params.get("installation_path"), discovered=[])
        selected = resolved.get("selected") or {}
        capabilities = params.get("capabilities") or []
        reports = {
            capability: capability_report(
                db, settings, capability, installation=selected, project_id=params.get("project_id")
            )
            for capability in capabilities
        }
        missing_components: list[str] = []
        for report in reports.values():
            missing_components.extend(
                str(row.get("component_id"))
                for row in (report.get("missing_files") or [])
                if isinstance(row, dict) and row.get("component_id")
            )
        gap = runtime_closure_gap(db, settings, selected) if "h3" in capabilities else {"missing_components": []}
        return {
            **common,
            "summary": "补齐固定清单中的缺失文件，并复用统一安装器的持久任务。",
            "download_bytes": sum(int((row or {}).get("missing_bytes") or 0) for row in reports.values()),
            "missing_components": sorted(set(missing_components) | set(gap.get("missing_components") or [])),
            "capabilities": {
                key: {"label": row.get("label"), "state": row.get("state"),
                      "missing_count": len(row.get("missing_files") or [])}
                for key, row in reports.items()
            },
            "installation": {
                "instance_id": selected.get("instance_id"),
                "managed": selected.get("managed"),
                "status": selected.get("status"),
                # Displayed redacted; the executor still receives `params` verbatim.
                "path": selected.get("root") or params.get("installation_path"),
            },
            "will_start_gpu": bool(params.get("verify_runtime", True)) and "h3" in capabilities,
            "will_modify_user_comfyui": False,
            "authorization_summary": "只下载缺失的固定文件、登记已核验文件并生成运行配置；"
            "不会删除模型、修改节点源码或终止别人的 ComfyUI。",
        }
    if kind == "setup_reconcile":
        from shine.comfyops.setup_summary import resolve_installation

        resolved = resolve_installation(db, settings, path=params.get("installation_path"), discovered=[])
        selected = resolved.get("selected") or {}
        return {
            **common,
            "summary": "只读取并核验本机已有文件，按固定清单登记通过校验的文件。",
            "missing_components": [],
            "download_bytes": 0,
            "capability": params.get("capability", "h3"),
            "adopt": bool(params.get("adopt")),
            "installation": {
                "instance_id": selected.get("instance_id"),
                "managed": selected.get("managed"),
                "status": selected.get("status"),
                "path_known": bool(selected.get("root") or params.get("installation_path")),
            },
            "will_start_gpu": False,
            "will_modify_user_comfyui": False,
            "authorization_summary": "核验不会移动或替换你的文件；adopt=true 才会把这个实例登记为工作台使用。",
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
        if dispatch == "setup_repair":
            from shine.comfyops.setup_preparation import TASK_KEY, enqueue

            value = enqueue(
                db,
                actor=actor,
                settings=settings,
                **routed_kwargs(declaration, params, enqueue),
            )
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
        elif dispatch == "setup_reconcile":
            from pathlib import Path

            from shine.comfyops.setup_reconcile import reconcile_installation
            from shine.comfyops.setup_summary import resolve_installation

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
            capability = str(params.get("capability") or "h3")
            value = reconcile_installation(
                db,
                settings,
                path=str(Path(str(target))),
                actor=actor,
                **routed_kwargs(
                    declaration, params, reconcile_installation, handled={"installation_path"}
                ),
            )
            result_status = str(value.get("status") or "UNKNOWN")
            adoption = value.get("adoption") if isinstance(value.get("adoption"), dict) else None
            adoption_status = str((adoption or {}).get("status") or "")
            result_identity = hashlib.sha256(
                _canonical(
                    {
                        "action": row.id,
                        "attempt": row.request_identity,
                        "target": str(target),
                        "snapshot": value.get("snapshot"),
                        "capability": capability,
                        "variant_id": params.get("variant_id"),
                        "catalog": value.get("catalog"),
                    }
                ).encode()
            ).hexdigest()
            base_verification = {
                "executor": spec.executor,
                "result_status": result_status,
                "result_identity": result_identity,
                "target": str(target),
                "capability": capability,
                "variant_id": params.get("variant_id"),
                "snapshot": value.get("snapshot"),
                "catalog": value.get("catalog"),
                "verified": value.get("verified"),
                "rejected": value.get("rejected"),
                "registered": value.get("registered"),
                "complete": value.get("complete"),
                "missing_components": value.get("missing_components") or [],
                "adoption": adoption,
                "checked_at": utcnow().isoformat(),
            }
            if result_status == "IN_PROGRESS":
                # Another identical reconcile already holds the executor's lock.
                # It has no durable task id we could cite, so nothing proves the
                # pending result belongs to this authorization.  Report the real
                # state and never borrow the global file verdict for success.
                row.status = "NEEDS_REVIEW"
                row.last_error = "已有一轮相同核验正在执行，本次无法证明结果归属；请等它结束后重新预览核验。"
                row.verification = {
                    **base_verification,
                    "outcome": "IN_PROGRESS",
                    "attributed": False,
                    "result_identity": None,
                    "authority": "/api/v1/setup/summary#capabilities",
                }
                add_audit(
                    db,
                    actor,
                    "SUPPORT_ACTION_RECONCILE_BUSY",
                    "support_action",
                    row.id,
                    {"kind": row.kind, "target": str(target), "capability": capability},
                    project_id=row.project_id,
                )
            else:
                executor_failed = result_status not in {"RECONCILED", "VERIFIED"}
                files_ok = (
                    not executor_failed
                    and value.get("complete") is not False
                    and int(value.get("rejected") or 0) == 0
                    and not (value.get("missing_components") or [])
                )
                adoption_ok = adoption_status in {"ADOPTED", "ALREADY_ADOPTED"}
                row.status = "EXECUTED"
                row.verification = {
                    **base_verification,
                    "outcome": "PENDING",
                    "attributed": True,
                    "files_ok": files_ok,
                    "adoption_ok": adoption_ok if params.get("adopt") else None,
                }
                evidence = _record_verification(db, settings, row) or {}
                targets = evidence.get("targets") or {}
                target_facts = targets.get(capability) or {}
                layers = {
                    "files_verified": bool(target_facts.get("files_verified")),
                    "instance_bound": bool(target_facts.get("instance_bound")),
                    "production_ready": bool(target_facts.get("production_ready")),
                }
                if not files_ok or executor_failed:
                    outcome = "NOT_VERIFIED"
                elif params.get("adopt") and not (adoption_status in {"ADOPTED", "ALREADY_ADOPTED"}
                                                  and layers["instance_bound"]):
                    outcome = "NOT_VERIFIED"
                elif evidence.get("outcome") == "VERIFIED":
                    outcome = "VERIFIED"
                elif evidence.get("outcome") == "NOT_VERIFIED":
                    outcome = "NOT_VERIFIED"
                else:
                    outcome = "UNKNOWN"
                row.verification = {
                    **(row.verification or {}),
                    "outcome": outcome,
                    "layers": layers,
                    "files_ok": files_ok,
                    "adoption_ok": adoption_ok if params.get("adopt") else None,
                }
                if outcome == "VERIFIED":
                    row.status = "SUCCEEDED"
                else:
                    row.status = "NEEDS_REVIEW"
                    reasons = []
                    if executor_failed:
                        reasons.append(f"核验执行器状态 {result_status}")
                    if value.get("complete") is False:
                        reasons.append("固定清单尚未全部通过核验")
                    if int(value.get("rejected") or 0):
                        reasons.append(f"{int(value.get('rejected') or 0)} 个文件未通过哈希校验")
                    if value.get("missing_components"):
                        reasons.append("仍有缺失文件")
                    if params.get("adopt") and adoption_status not in {"ADOPTED", "ALREADY_ADOPTED"}:
                        reasons.append(f"实例接管状态 {adoption_status or 'UNKNOWN'}")
                    row.last_error = "；".join(reasons) or "核验结果尚未被权威裁决确认。"
                add_audit(
                    db,
                    actor,
                    "SUPPORT_ACTION_RECONCILE_RESULT",
                    "support_action",
                    row.id,
                    {
                        "kind": row.kind,
                        "target": str(target),
                        "capability": capability,
                        "result_status": result_status,
                        "result_identity": result_identity,
                        "outcome": outcome,
                        "layers": layers,
                    },
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

    if row.kind == "setup_repair":
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
        elif executor_status == "COMPLETED":
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
