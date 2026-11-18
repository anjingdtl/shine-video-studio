"""Read-only evidence projection.

An EvidenceSnapshot is a disposable projection of existing authoritative
sources: the single setup verdict, the runtime report, the durable resource
ledger, worker rows and a named task/attempt.  It never writes, never starts a
service, never scans the disk (discovery is passed as an empty list) and never
becomes a second readiness ledger.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from shine.models import Job, JobAttempt, ProviderProfile, WorkerInstance, utcnow
from shine.support.privacy import private_roots_for, redact_payload, redact_text

SNAPSHOT_VERSION = "support-evidence-v1"
SNAPSHOT_TTL_SECONDS = 120

PAGE_IDS = frozenset(
    {
        "projects",
        "creative",
        "assets",
        "produce",
        "materials",
        "package",
        "run",
        "deliver",
        "library",
        "setup",
        "advanced",
        "copilot",
        "asset-center",
        "unknown",
    }
)


def normalise_page_id(value: str | None) -> str:
    page = (value or "unknown").strip()
    if page.startswith("assets"):
        return "asset-center"
    return page if page in PAGE_IDS else "unknown"


def _capture(
    provenance: list[dict[str, Any]],
    source: str,
    reader: Callable[[], Any],
    *,
    fallback: Any = None,
    confidence: str = "AUTHORITATIVE",
) -> Any:
    captured_at = utcnow().isoformat()
    try:
        value = reader()
    except Exception as exc:  # noqa: BLE001 - an unreadable layer is UNAVAILABLE, not a fabricated fact
        provenance.append(
            {
                "source": source,
                "captured_at": captured_at,
                "confidence": "UNKNOWN",
                "error": redact_text(type(exc).__name__, limit=80),
            }
        )
        return fallback
    provenance.append(
        {"source": source, "captured_at": captured_at, "confidence": confidence}
    )
    return value


def _setup_verdict(db, settings) -> dict[str, Any]:
    from shine.api_setup import setup_verdict

    return setup_verdict(settings=settings, db=db)


def _setup_summary(db, settings, project_id: str | None, variant_id: str | None) -> dict[str, Any]:
    from shine.comfyops.setup_summary import summary

    return summary(db, settings, project_id=project_id, variant_id=variant_id, discovered=[])


def _runtime_report(db, settings) -> dict[str, Any]:
    from shine.comfyops.runtime_configuration import runtime_report

    return runtime_report(db, settings)


def _managed_processes(db) -> list[dict[str, Any]]:
    from shine.comfyops.renderer_release import describe_managed_processes

    return describe_managed_processes(db)


def _workers(db) -> list[dict[str, Any]]:
    rows = db.scalars(select(WorkerInstance).order_by(WorkerInstance.last_heartbeat_at.desc())).all()
    return [
        {
            "worker_id": row.worker_id,
            "status": row.status,
            "version": row.version,
            "current_job_id": row.current_job_id,
            "current_phase": row.current_phase,
            "last_error": redact_text(row.last_error, limit=300) if row.last_error else None,
            "last_heartbeat_at": row.last_heartbeat_at.isoformat() if row.last_heartbeat_at else None,
        }
        for row in rows[:5]
    ]


def _task_facts(db, *, job_id: str | None, attempt_id: str | None) -> dict[str, Any] | None:
    job = db.get(Job, job_id) if job_id else None
    if job is None and attempt_id:
        attempt_row = db.get(JobAttempt, attempt_id)
        job = db.get(Job, attempt_row.job_id) if attempt_row is not None else None
    if job is None:
        return None
    attempts = sorted(job.attempts or [], key=lambda row: row.attempt_no, reverse=True)
    attempt = db.get(JobAttempt, attempt_id) if attempt_id else (attempts[0] if attempts else None)
    return {
        "job_id": job.id,
        "project_id": job.project_id,
        "shot_id": job.shot_id,
        "status": job.status,
        "provider": job.provider,
        "generation_mode": job.generation_mode,
        "failure_code": job.failure_code,
        "paused": job.paused,
        "artifact_id": job.artifact_id,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        "attempt": {
            "attempt_id": attempt.id,
            "attempt_no": attempt.attempt_no,
            "status": attempt.status,
            "progress": attempt.progress,
            "failure_code": attempt.failure_code,
            "reconciliation_state": attempt.reconciliation_state,
            "external_outcome": attempt.reconciliation_state,
            "output_path_present": bool(attempt.output_path),
            "last_heartbeat_at": attempt.heartbeat_at.isoformat() if attempt.heartbeat_at else None,
        }
        if attempt is not None
        else None,
    }


def _capability_projection(report: dict[str, Any]) -> dict[str, Any]:
    missing = report.get("missing_files") or []
    components = sorted(
        {
            str(row.get("component_id"))
            for row in missing
            if isinstance(row, dict) and row.get("component_id")
        }
    )
    return {
        "key": report.get("key"),
        "label": report.get("label"),
        "state": report.get("state"),
        "optional": bool(report.get("optional")),
        "facts": report.get("facts") or {},
        "summary": report.get("summary"),
        "next_step": report.get("next_step"),
        "missing_bytes": report.get("missing_bytes") or 0,
        "missing_count": len(missing),
        "missing_components": components,
        "advisories": [
            {"code": row.get("code"), "detail": redact_text(row.get("detail"), limit=200)}
            for row in (report.get("advisories") or [])
            if isinstance(row, dict)
        ][:12],
    }


def _project_facts(db, project_id: str | None) -> dict[str, Any] | None:
    if not project_id:
        return None
    from shine.models import Project

    project = db.get(Project, project_id)
    if project is None:
        return None
    return {
        "id": project.id,
        "title": project.title,
        "frozen_style_version_id": project.frozen_style_version_id,
        "auto_repair_enabled": project.auto_repair_enabled,
    }


def _workflow_capability_facts() -> dict[str, Any]:
    from shine.comfy.graph_compiler import (
        AUDIO_REFERENCE_LIMIT,
        IMAGE_REFERENCE_LIMIT,
        TOTAL_REFERENCE_LIMIT,
        VIDEO_REFERENCE_LIMIT,
    )
    from shine.comfyops.mode_capabilities import baseline_profile_names

    return {
        "mode": "Ref2VA",
        "min_image_references": 1,
        "max_image_references": IMAGE_REFERENCE_LIMIT,
        "max_audio_references": AUDIO_REFERENCE_LIMIT,
        "max_video_references": VIDEO_REFERENCE_LIMIT,
        "max_total_references": TOTAL_REFERENCE_LIMIT,
        "baseline_templates": list(baseline_profile_names()),
    }


def _llm_route_facts(db) -> dict[str, Any]:
    from shine.models import AgentRoute

    route = db.scalar(select(AgentRoute).where(AgentRoute.role == "support_agent"))
    if route is None:
        return {"configured": False, "provider": None, "model": None}
    profile = db.get(ProviderProfile, route.provider_profile_id)
    if profile is None:
        return {"configured": False, "provider": None, "model": None, "reason": "ROUTE_DANGLING"}
    return {
        "configured": bool(profile.enabled),
        "provider": profile.name,
        "kind": profile.kind,
        "model": profile.model,
        "enabled": profile.enabled,
    }


def build_evidence_snapshot(
    db,
    settings,
    *,
    page_id: str | None = None,
    project_id: str | None = None,
    episode_id: str | None = None,
    job_id: str | None = None,
    attempt_id: str | None = None,
    task_id: str | None = None,
    error_code: str | None = None,
    variant_id: str | None = None,
) -> dict[str, Any]:
    """A bounded, redacted projection of the facts relevant to one page."""
    now = utcnow()
    provenance: list[dict[str, Any]] = []
    verdict = _capture(provenance, "GET /api/v1/setup/verdict", lambda: _setup_verdict(db, settings),
                       fallback={"mode": "UNKNOWN", "label": "未知", "code": "VERDICT_UNAVAILABLE"})
    report = _capture(provenance, "GET /api/v1/setup/runtime", lambda: _runtime_report(db, settings),
                      fallback={"status": "UNKNOWN"})
    summary = _capture(
        provenance,
        "GET /api/v1/setup/summary",
        lambda: _setup_summary(db, settings, project_id, variant_id),
        fallback=None,
    )
    processes = _capture(provenance, "GET /api/v1/setup/managed-processes",
                         lambda: _managed_processes(db), fallback=[])
    workers = _capture(provenance, "worker_instances ledger", lambda: _workers(db), fallback=[])
    task = _capture(provenance, "jobs/job_attempts ledger",
                    lambda: _task_facts(db, job_id=job_id, attempt_id=attempt_id), fallback=None)
    project = _capture(provenance, "projects ledger", lambda: _project_facts(db, project_id), fallback=None)
    llm = _capture(provenance, "agent_routes ledger", lambda: _llm_route_facts(db), fallback={"configured": False})
    workflow_caps = _capture(
        provenance,
        "shine.comfy.graph_compiler",
        _workflow_capability_facts,
        fallback={"mode": "Ref2VA", "min_image_references": 1, "max_image_references": 9},
    )

    setup: dict[str, Any] = {"verdict": verdict, "runtime": report}
    if isinstance(summary, dict):
        setup["summary"] = {
            "computed_at": summary.get("computed_at"),
            "installation": {
                "selected": {
                    key: (summary.get("installation") or {}).get("selected", {}).get(key)
                    for key in ("instance_id", "port", "managed", "adopted", "status", "exists", "source")
                }
                if (summary.get("installation") or {}).get("selected")
                else None,
                "choice_required": bool((summary.get("installation") or {}).get("choice_required")),
                "candidate_count": len((summary.get("installation") or {}).get("candidates") or []),
            },
            "capabilities": {
                key: _capability_projection(value)
                for key, value in (summary.get("capabilities") or {}).items()
            },
            "progress": summary.get("progress"),
            "primary_capability": summary.get("primary_capability"),
            "action_required": summary.get("action_required"),
            "blockers": summary.get("blockers") or [],
            "preparation": summary.get("preparation"),
        }

    snapshot = {
        "evidence_id": "ev-" + uuid4().hex[:12],
        "snapshot_version": SNAPSHOT_VERSION,
        "snapshot_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=SNAPSHOT_TTL_SECONDS)).isoformat(),
        "stale": False,
        "source_revision": SNAPSHOT_VERSION,
        "page_context": {
            "screen": normalise_page_id(page_id),
            "project_id": project_id,
            "episode_id": episode_id,
            "job_id": job_id,
            "attempt_id": attempt_id,
            "task_id": task_id,
            "error_code": error_code,
        },
        "setup": setup,
        "runtime": {
            "status": report.get("status"),
            "render_provider": report.get("render_provider"),
            "services_verified": bool(report.get("services_verified")),
            "workers": workers,
            "managed_processes": processes[:8],
            "llm": llm,
        },
        "task": task,
        "project": project,
        "workflow_capabilities": workflow_caps,
        "provenance": provenance,
        "notice": "这是现有权威事实的只读投影，不是新的就绪台账；变化后重新读取原事实。",
    }
    return redact_payload(snapshot, private_roots=private_roots_for(settings))


def snapshot_revision(snapshot: dict[str, Any]) -> str:
    import hashlib
    import json

    setup = snapshot.get("setup") or {}
    verdict = setup.get("verdict") or {}
    runtime = snapshot.get("runtime") or {}
    processes = [
        {"id": row.get("id"), "state": row.get("state")}
        for row in (runtime.get("managed_processes") or [])
        if isinstance(row, dict)
    ]
    payload = {
        "mode": verdict.get("mode"),
        "code": verdict.get("code"),
        "runtime_status": runtime.get("status"),
        "services_verified": runtime.get("services_verified"),
        "processes": processes,
        "task_status": (snapshot.get("task") or {}).get("status"),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
