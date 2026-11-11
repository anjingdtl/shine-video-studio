"""The physical-call ledger: what left the machine, and what came back.

The task trace answers "what happened to this unit".  This ledger answers the
cross-unit questions a recovery path needs:

* which calls are still ``UNKNOWN`` in this project, and therefore may never be
  replayed without a person's decision;
* how many physical requests one logical unit really spent;
* whether a slow unit is slow on the network (an event arrived) or was never
  answered at all - the distinction a lease timeout cannot make.

A row is written twice: once as ``SENDING`` *before* the request leaves, once
with the real outcome after.  Writing the intent first is what makes a crash
reconcilable instead of a guess.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from shine.literary.models import LLMCallAttempt
from shine.models import utcnow

logger = logging.getLogger(__name__)


def recorder(factory, project_id: str, task_id: str | None = None):
    """A durable recorder for one task, safe to attach to any governor.

    A ledger fault is logged and swallowed: the literary work must not fail
    because bookkeeping did.  Every path that can spend money on a provider gets
    the same recorder, so the ledger never has blind spots between stages.
    """
    def record(attempt: dict[str, Any]) -> None:
        try:
            with factory.begin() as db:
                record_row(db, project_id=project_id, attempt=attempt, task_id=task_id)
        except Exception:  # noqa: BLE001 - bookkeeping never fails a scene
            logger.exception("literary_attempt_ledger_failed task=%s unit=%s",
                             task_id, attempt.get("unit_id"))

    return record


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def record_row(db, *, project_id: str, attempt: dict[str, Any],
               task_id: str | None = None) -> LLMCallAttempt:
    """Insert or update one attempt row.  Never raises into the caller."""
    phase = str(attempt.get("phase") or "FINISHED")
    unit_id = str(attempt.get("unit_id") or "unknown")[:160]
    fingerprint = str(attempt.get("request_fingerprint") or "")
    attempt_no = _as_int(attempt.get("attempt_no")) or 1
    now = utcnow()
    row = None
    if fingerprint:
        row = db.scalars(select(LLMCallAttempt).where(
            LLMCallAttempt.project_id == project_id,
            LLMCallAttempt.unit_id == unit_id,
            LLMCallAttempt.request_fingerprint == fingerprint,
            LLMCallAttempt.attempt_no == attempt_no,
        ).order_by(LLMCallAttempt.started_at.desc())).first()
    if row is None:
        row = LLMCallAttempt(
            project_id=project_id, task_id=task_id, unit_id=unit_id,
            role=str(attempt.get("role") or "")[:48],
            purpose=str(attempt.get("purpose") or "creative")[:32],
            attempt_no=attempt_no, request_fingerprint=fingerprint,
            status="SENDING" if phase == "SENDING" else "FINISHED",
            external_outcome=str(attempt.get("external_outcome") or "UNKNOWN"),
            corrective=bool(attempt.get("corrective")), started_at=now,
        )
        db.add(row)
    if phase == "SENDING":
        db.flush()
        return row
    row.status = "FINISHED"
    row.task_id = task_id or row.task_id
    row.external_outcome = str(attempt.get("external_outcome") or row.external_outcome)
    row.corrective = bool(attempt.get("corrective", row.corrective))
    row.input_tokens = _as_int(attempt.get("input_tokens_estimated")) or row.input_tokens
    row.output_tokens = _as_int(attempt.get("completion_tokens")) or row.output_tokens
    row.reasoning_tokens = _as_int(attempt.get("reasoning_tokens")) or row.reasoning_tokens
    row.output_budget = attempt.get("output_budget") if isinstance(attempt.get("output_budget"), dict) \
        else row.output_budget
    row.http_status = _as_int(attempt.get("http_status")) or row.http_status
    row.provider_code = _as_int(attempt.get("provider_code")) or row.provider_code
    row.finish_reason = (str(attempt.get("finish_reason"))[:32]
                         if attempt.get("finish_reason") else row.finish_reason)
    row.error_code = str(attempt.get("error_code"))[:64] if attempt.get("error_code") else row.error_code
    row.latency_ms = _as_int(attempt.get("latency_ms")) or row.latency_ms
    row.first_event_ms = _as_int(attempt.get("first_event_ms")) or row.first_event_ms
    row.last_event_ms = _as_int(attempt.get("last_event_ms")) or row.last_event_ms
    row.finished_at = now
    db.flush()
    return row


def record(db, *, project_id: str, attempt: dict[str, Any],
           task_id: str | None = None) -> LLMCallAttempt:
    """Compatibility name for the row writer."""
    return record_row(db, project_id=project_id, attempt=attempt, task_id=task_id)


def unknown_attempts(db, project_id: str) -> list[LLMCallAttempt]:
    """Calls whose answer may exist upstream and must never be replayed blindly."""
    return list(db.scalars(select(LLMCallAttempt).where(
        LLMCallAttempt.project_id == project_id,
        LLMCallAttempt.external_outcome == "UNKNOWN",
        LLMCallAttempt.status == "FINISHED",
    ).order_by(LLMCallAttempt.started_at.desc())).all())


def attempts_for_unit(db, project_id: str, unit_id: str, limit: int = 20) -> list[LLMCallAttempt]:
    return list(db.scalars(select(LLMCallAttempt).where(
        LLMCallAttempt.project_id == project_id,
        LLMCallAttempt.unit_id == unit_id,
    ).order_by(LLMCallAttempt.started_at.desc()).limit(limit)).all())


def reconcile(db, attempt_id: str, *, outcome: str, evidence: str,
              actor: str = "local-user") -> dict[str, Any]:
    """Record a person's reconciliation of one unknown call.

    ``outcome`` is ``adopted``, ``safe_to_resend`` or ``abandoned``.  The row
    keeps the original unknown evidence and gains the decision, so a later
    reader can see why a request was or was not sent again.
    """
    if outcome not in {"adopted", "safe_to_resend", "abandoned"}:
        raise ValueError("RECONCILIATION_OUTCOME_UNKNOWN")
    row = db.get(LLMCallAttempt, attempt_id)
    if row is None:
        raise ValueError("LLM_CALL_ATTEMPT_NOT_FOUND")
    decision = {
        "outcome": outcome, "evidence": evidence[:1000], "actor": actor,
        "decided_at": utcnow().isoformat(),
        "previous_external_outcome": row.external_outcome,
    }
    budget = dict(row.output_budget or {})
    budget["reconciliation"] = decision
    row.output_budget = budget
    if outcome == "safe_to_resend":
        # Only a person may turn an unknown outcome into "nothing was sent".
        row.external_outcome = "NOT_SENT"
    row.status = "RECONCILED"
    db.flush()
    return decision


def public(row: LLMCallAttempt) -> dict[str, Any]:
    """The UI-safe view of one attempt: no prompt, no provider body."""
    return {
        "id": row.id, "unit_id": row.unit_id, "role": row.role, "purpose": row.purpose,
        "attempt_no": row.attempt_no, "status": row.status,
        "external_outcome": row.external_outcome,
        "error_code": row.error_code, "finish_reason": row.finish_reason,
        "http_status": row.http_status,
        "input_tokens": row.input_tokens, "output_tokens": row.output_tokens,
        "reasoning_tokens": row.reasoning_tokens,
        "latency_ms": row.latency_ms,
        "first_event_ms": row.first_event_ms, "last_event_ms": row.last_event_ms,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "corrective": row.corrective,
    }
