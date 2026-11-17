"""Bounded, evidence-first investigation.

The turn is *not* an open-ended agent loop.  A deterministic planner reads the
existing snapshot, decides which additional read-only ledgers are actually
relevant, and calls only tools that exist in the server-side registry.  The
model never names a tool, never supplies an API argument and never executes
anything.

Limits (all enforced here, not in prose):

* at most ``MAX_TOOL_STEPS`` tools per turn;
* at most ``INVESTIGATION_BUDGET_SECONDS`` of wall time;
* every payload is truncated and redacted before leaving this module;
* a layer that cannot be read is reported as ``UNAVAILABLE``, never guessed.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from sqlalchemy import select

from shine.support.occupancy import resource_occupancy
from shine.support.privacy import redact_payload, redact_text

MAX_TOOL_STEPS = 8
INVESTIGATION_BUDGET_SECONDS = 2.5

#: Every tool in this module is READ-only and registered in ``support.tools``.
INVESTIGATION_TOOLS: tuple[str, ...] = (
    "read_task_failure",
    "read_quality_gates",
    "read_workflow_contract",
    "read_installation_receipts",
    "read_gpu_occupancy",
    "read_support_context",
)

_EVIDENCE_LEVEL = {
    "search_local_knowledge": "LOCAL_KB",
    "read_gpu_occupancy": "LOCAL_LEDGER",
}


def _task_detail(db, job_id: str, attempt_id: str | None) -> dict[str, Any]:
    from shine.models import Job, JobAttempt

    job = db.get(Job, job_id)
    if job is None:
        return {"status": "UNAVAILABLE", "detail": "任务记录不存在。"}
    attempt = db.get(JobAttempt, attempt_id) if attempt_id else None
    if attempt is None and job.attempts:
        attempt = sorted(job.attempts, key=lambda row: row.attempt_no, reverse=True)[0]
    request_snapshot = (attempt.request_snapshot if attempt is not None else None) or {}
    return {
        "status": "OK",
        "job": {
            "job_id": job.id,
            "project_id": job.project_id,
            "shot_id": job.shot_id,
            "status": job.status,
            "provider": job.provider,
            "generation_mode": job.generation_mode,
            "failure_code": job.failure_code,
            "workflow_profile_id": job.workflow_profile_id,
            "last_qc_result": job.last_qc_result,
            "stale": job.stale,
        },
        "attempt": {
            "attempt_id": attempt.id,
            "attempt_no": attempt.attempt_no,
            "status": attempt.status,
            "progress": attempt.progress,
            "failure_code": attempt.failure_code,
            "reconciliation_state": attempt.reconciliation_state,
            "attempt_kind": attempt.attempt_kind,
            "repair_root_cause": redact_text(attempt.repair_root_cause, limit=300)
            if attempt.repair_root_cause
            else None,
            "request_keys": sorted(request_snapshot.keys())[:12]
            if isinstance(request_snapshot, dict)
            else [],
        }
        if attempt is not None
        else None,
        "attempt_count": len(job.attempts or []),
    }


def _quality_gates(db, job_id: str) -> dict[str, Any]:
    from shine.models import QualityGate

    rows = db.scalars(
        select(QualityGate)
        .where(QualityGate.job_id == job_id)
        .order_by(QualityGate.created_at.desc())
        .limit(8)
    ).all()
    return {
        "status": "OK",
        "gates": [
            {
                "gate": row.gate_name,
                "status": row.status,
                "required": row.required,
                "error_code": row.error_code,
                "model": row.model,
                "coverage": row.coverage,
                "is_mock": row.is_mock,
            }
            for row in rows
        ],
    }


def _workflow_contract(db, job_id: str) -> dict[str, Any]:
    from shine.models import Job, WorkflowProfile

    job = db.get(Job, job_id)
    if job is None or not job.workflow_profile_id:
        return {"status": "UNAVAILABLE", "detail": "任务没有绑定工作流合同。"}
    profile = db.get(WorkflowProfile, job.workflow_profile_id)
    if profile is None:
        return {"status": "UNAVAILABLE", "detail": "工作流合同记录不存在。"}
    return {
        "status": "OK",
        "profile": {
            "id": profile.id,
            "name": profile.name,
            "mode": profile.mode,
            "scope": "PROJECT" if profile.project_id else "GLOBAL_BUILTIN",
            "validated_against_present": bool(profile.validated_against),
            "required_nodes": list(profile.required_nodes or [])[:12],
            "parameter_keys": sorted((profile.parameter_map or {}).keys())[:12],
        },
    }


def _installation_receipts(db) -> dict[str, Any]:
    from shine.comfyops.models import InstallationAttempt

    rows = db.scalars(
        select(InstallationAttempt)
        .order_by(InstallationAttempt.created_at.desc())
        .limit(6)
    ).all()
    return {
        "status": "OK",
        "attempts": [
            {
                "attempt_id": row.id,
                "plan_id": row.plan_id,
                "status": row.status,
                "stage": (row.payload or {}).get("stage") if isinstance(row.payload, dict) else None,
                "failure_code": (row.payload or {}).get("failure_code")
                if isinstance(row.payload, dict)
                else None,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ],
    }


def _plan(
    db,
    snapshot: dict[str, Any],
    question: str,
    code: str | None,
) -> list[tuple[str, Callable[[], dict[str, Any]]]]:
    """Deterministic plan: only the ledgers this evidence actually requires."""
    from shine.support.knowledge import search_local

    page = snapshot.get("page_context") or {}
    task = snapshot.get("task") or {}
    job_id = str(task.get("job_id") or page.get("job_id") or "")
    attempt_id = str((task.get("attempt") or {}).get("attempt_id") or page.get("attempt_id") or "") or None
    code_upper = (code or page.get("error_code") or "").upper()
    question_lower = (question or "").casefold()

    plan: list[tuple[str, Callable[[], dict[str, Any]]]] = []
    if job_id:
        plan.append(("read_task_failure", lambda: _task_detail(db, job_id, attempt_id)))
        plan.append(("read_quality_gates", lambda: _quality_gates(db, job_id)))
    if job_id and (
        any(word in code_upper for word in ("NODE", "WORKFLOW", "PROFILE"))
        or any(word in question_lower for word in ("节点", "工作流", "node", "workflow"))
    ):
        plan.append(("read_workflow_contract", lambda: _workflow_contract(db, job_id)))
    if any(word in code_upper for word in ("MODEL", "DOWNLOAD", "FILE", "HASH", "INSTALL")) or any(
        word in question_lower for word in ("模型", "下载", "安装", "文件")
    ):
        plan.append(("read_installation_receipts", lambda: _installation_receipts(db)))
    # GPU / lease questions always get the authoritative occupancy answer.
    if any(word in code_upper for word in ("OOM", "CUDA", "GPU", "LEASE")) or any(
        word in question_lower for word in ("显存", "显卡", "gpu", "占用", "忙")
    ):
        plan.append(("read_gpu_occupancy", lambda: {"status": "OK", "occupancy": resource_occupancy(db)}))
    plan.append(("search_local_knowledge", lambda: {"status": "OK", "entries": search_local(question, limit=3)}))
    return plan


def investigate(
    db,
    settings,
    snapshot: dict[str, Any],
    question: str,
    *,
    code: str | None = None,
) -> dict[str, Any]:
    """Run a bounded read-only investigation and return its trace."""
    started = time.monotonic()
    steps: list[dict[str, Any]] = []
    plan = _plan(db, snapshot, question, code)
    truncated = False
    for tool, reader in plan:
        if len(steps) >= MAX_TOOL_STEPS:
            truncated = True
            break
        if time.monotonic() - started > INVESTIGATION_BUDGET_SECONDS:
            steps.append(
                {"tool": tool, "status": "BUDGET_EXCEEDED", "evidence_level": _level(tool)}
            )
            truncated = True
            continue
        step_start = time.monotonic()
        try:
            payload = reader()
        except Exception as exc:  # noqa: BLE001 - an unreadable ledger is UNAVAILABLE
            steps.append(
                {
                    "tool": tool,
                    "status": "UNAVAILABLE",
                    "error": type(exc).__name__,
                    "ms": round((time.monotonic() - step_start) * 1000, 1),
                    "evidence_level": _level(tool),
                }
            )
            continue
        status = str(payload.pop("status", "OK")) if isinstance(payload, dict) else "OK"
        steps.append(
            {
                "tool": tool,
                "status": status,
                "ms": round((time.monotonic() - step_start) * 1000, 1),
                "evidence_level": _level(tool),
                "payload": redact_payload(payload, private_roots=()),
            }
        )
    return {
        "steps": steps,
        "steps_used": len(steps),
        "max_steps": MAX_TOOL_STEPS,
        "budget_seconds": INVESTIGATION_BUDGET_SECONDS,
        "truncated": truncated,
        "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
    }


def _level(tool: str) -> str:
    return _EVIDENCE_LEVEL.get(tool, "LOCAL_LEDGER")
