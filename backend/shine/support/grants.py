"""The single write path for a task-level grant (V3.1).

Everything that changes a grant lives here: creating it once from one click,
reserving and settling its budget, recording the tool calls it has spent,
pausing it by name and revoking it.  Nothing here executes business work: a
grant only *permits*, and the permission is consumed by
:func:`shine.support.actions.execute_under_grant`, which still runs the
original preview → approval → executor chain.

Two rules the code below enforces rather than documents:

* repeated authorization of the same objective returns the **same** row, so a
  double click or a replayed HTTP request cannot create a second installation;
* a budget reservation is a compare-and-swap on ``budget_version``, so two
  concurrent tool calls cannot both spend the last allowed download.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from shine.copilot import task_grant as grant_contract
from shine.copilot.contracts import canonical, digest
from shine.models import new_id, utcnow
from shine.support.models import SupportDelegation, SupportSession

#: How often a lost optimistic-lock race is retried before the caller is told.
CAS_ATTEMPTS = 5


@dataclass(frozen=True)
class GrantRow:
    row: SupportDelegation
    grant: dict[str, Any]

    @property
    def id(self) -> str:
        return str(self.grant.get("grant_id") or self.row.grant_id or "")

    def public(self) -> dict[str, Any]:
        return grant_contract.grant_public(self.grant, now=utcnow())


def _row_grant(row: SupportDelegation) -> dict[str, Any] | None:
    payload = row.payload if isinstance(row.payload, dict) else {}
    grant = payload.get("grant")
    return dict(grant) if isinstance(grant, dict) else None


def _delegation_key(
    *,
    session_id: str,
    actor: str,
    objective_digest: str,
    groups: Iterable[str],
    limits: dict[str, int],
    project_ids: list[str],
    installation_ids: list[str],
    generation: int,
) -> str:
    """Identity of *this* authorization, not of the running task.

    ``generation`` is the count of already terminal grants for the same
    identity: after a task really finished (or was revoked/expired), asking for
    the same objective again is a new authorization, while a replay of the same
    click stays the same one.
    """
    return (
        "grant:"
        + digest(
            canonical(
                {
                    "session_id": session_id,
                    "actor": actor,
                    "objective_digest": objective_digest,
                    "groups": sorted(groups),
                    "limits": limits,
                    "project_ids": sorted(project_ids),
                    "installation_ids": sorted(installation_ids),
                    "generation": generation,
                }
            )
        )[:88]
    )


def _existing_generation(db, *, session_id: str, actor: str, objective_digest: str) -> int:
    rows = db.scalars(
        select(SupportDelegation).where(
            SupportDelegation.session_id == session_id,
            SupportDelegation.grant_id.is_not(None),
        )
    ).all()
    terminal = 0
    for row in rows:
        grant = _row_grant(row)
        if not grant:
            continue
        if grant.get("actor_id") != actor or grant.get("objective_digest") != objective_digest:
            continue
        status = grant_contract.grant_status(grant, now=utcnow())
        if status in grant_contract.TERMINAL_STATUSES:
            terminal += 1
    return terminal


def find_by_id(db, grant_id: str) -> SupportDelegation | None:
    return db.scalar(select(SupportDelegation).where(SupportDelegation.grant_id == grant_id))


def load(db, grant_id: str) -> GrantRow:
    row = find_by_id(db, grant_id) if grant_id else None
    if row is None:
        raise grant_contract.GrantError("GRANT_NOT_FOUND", "找不到这次任务授权。")
    grant = _row_grant(row)
    if grant is None:
        raise grant_contract.GrantError("GRANT_NOT_FOUND", "这条记录不是任务授权。")
    # The projection always reports the *server* status: an expired row is
    # expired even when nobody has written the transition down yet.
    live = grant_contract.grant_status(grant, now=utcnow())
    if live != grant.get("status"):
        grant["status"] = live
    return GrantRow(row=row, grant=grant)


def latest_for_session(db, session_id: str) -> GrantRow | None:
    row = db.scalar(
        select(SupportDelegation)
        .where(
            SupportDelegation.session_id == session_id,
            SupportDelegation.grant_id.is_not(None),
        )
        .order_by(SupportDelegation.created_at.desc())
    )
    if row is None:
        return None
    grant = _row_grant(row)
    if grant is None:
        return None
    live = grant_contract.grant_status(grant, now=utcnow())
    if live != grant.get("status"):
        grant["status"] = live
    return GrantRow(row=row, grant=grant)


def create(
    db,
    *,
    session: SupportSession,
    actor: str,
    objective: str,
    groups: Iterable[str],
    limits: dict[str, Any] | None = None,
    project_ids: list[str] | None = None,
    installation_ids: list[str] | None = None,
    allowed_resource_roots: list[str] | None = None,
    validity: timedelta | None = None,
    expires_at: datetime | None = None,
    expected_digest: str | None = None,
) -> tuple[GrantRow, bool]:
    """Create the one authorization of this objective, or return the live one.

    Returns ``(grant, created)``.  ``created`` is False for a replayed click,
    which is exactly the behaviour GRANT-01 requires: one click, one grant, one
    installation plan.
    """
    normalized_groups = grant_contract.normalize_groups(groups)
    normalized_limits = grant_contract.normalize_limits(limits)
    projects = [str(item) for item in (project_ids if project_ids is not None
                                       else ([session.project_id] if session.project_id else []))]
    installations = [str(item) for item in (installation_ids or [])]
    roots = [str(item) for item in (allowed_resource_roots or [])]

    generation = _existing_generation(
        db, session_id=session.id, actor=actor,
        objective_digest=grant_contract.objective_digest(objective),
    )
    key = _delegation_key(
        session_id=session.id,
        actor=actor,
        objective_digest=grant_contract.objective_digest(objective),
        groups=normalized_groups,
        limits=normalized_limits,
        project_ids=projects,
        installation_ids=installations,
        generation=generation,
    )
    existing = db.scalar(
        select(SupportDelegation).where(SupportDelegation.authorization_key == key)
    )
    if existing is not None:
        grant = _row_grant(existing)
        if grant is not None:
            live = grant_contract.grant_status(grant, now=utcnow())
            if live not in grant_contract.TERMINAL_STATUSES:
                return GrantRow(row=existing, grant=grant), False

    created_at = utcnow()
    grant_id = "grant-" + new_id().replace("-", "")[:24]
    grant = grant_contract.build_proposed_grant(
        grant_id=grant_id,
        session_id=session.id,
        actor_id=actor,
        objective=objective,
        project_ids=projects,
        installation_ids=installations,
        allowed_resource_roots=roots,
        groups=normalized_groups,
        limits=normalized_limits,
        created_at=created_at,
        validity=validity,
        expires_at=expires_at,
    )
    if expected_digest is not None and grant["grant_digest"] != expected_digest:
        # The boundary the person previewed is not the boundary this request
        # would create: refuse instead of authorizing something else.
        raise grant_contract.GrantError(
            "GRANT_DIGEST_MISMATCH",
            "授权范围与你预览时不一致，请重新预览后再确认。",
            details={"expected": expected_digest, "actual": grant["grant_digest"]},
        )
    grant["status"] = "AUTHORIZED"
    grant["created_at"] = created_at.replace(microsecond=0).isoformat()
    grant["usage"] = {key_name: 0 for key_name in grant_contract.DEFAULT_LIMITS}
    grant["tool_calls"] = []
    grant["sub_actions"] = []

    payload = {
        "id": new_id(),
        "status": "RUNNING",
        "created_at": created_at.isoformat(),
        "plan_id": None,
        "plan_hash": None,
        "project_id": session.project_id,
        "grant": grant,
        "grant_id": grant_id,
        "grant_digest": grant["grant_digest"],
        "objective": grant["objective"],
    }
    row = SupportDelegation(
        id=payload["id"],
        session_id=session.id,
        plan_id=None,
        plan_hash=None,
        authorization_key=key,
        status="RUNNING",
        payload=payload,
        grant_id=grant_id,
        grant_digest=grant["grant_digest"],
        expires_at=grant_contract.parse_iso(grant["validity"]["expires_at"]),
        budget_version=0,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        # A concurrent identical click won the unique key: adopt it.
        db.rollback()
        winner = db.scalar(
            select(SupportDelegation).where(SupportDelegation.authorization_key == key)
        )
        if winner is None:
            raise
        winner_grant = _row_grant(winner) or grant
        return GrantRow(row=winner, grant=winner_grant), False
    return GrantRow(row=row, grant=grant), True


def _write_payload(
    db,
    row: SupportDelegation,
    payload: dict[str, Any],
    *,
    expected_version: int,
    status: str | None = None,
) -> bool:
    """Compare-and-swap the JSON payload; False means somebody else won."""
    values: dict[str, Any] = {
        "payload": payload,
        "budget_version": expected_version + 1,
        "updated_at": utcnow(),
    }
    if status is not None:
        values["status"] = status
    result = db.execute(
        update(SupportDelegation)
        .where(
            SupportDelegation.id == row.id,
            SupportDelegation.budget_version == expected_version,
        )
        .values(**values)
    )
    db.commit()
    return bool(result.rowcount)


def set_status(
    db, grant_id: str, status: str, *, reason: str | None = None
) -> GrantRow:
    """Move the envelope by name.  Never touches an original executor task."""
    for _ in range(CAS_ATTEMPTS):
        row = find_by_id(db, grant_id)
        if row is None:
            raise grant_contract.GrantError("GRANT_NOT_FOUND", "找不到这次任务授权。")
        payload = dict(row.payload or {})
        grant = dict(payload.get("grant") or {})
        if not grant:
            raise grant_contract.GrantError("GRANT_NOT_FOUND", "这条记录不是任务授权。")
        current = grant_contract.grant_status(grant, now=utcnow())
        if current in grant_contract.TERMINAL_STATUSES and status not in {
            "SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED", "REVOKED",
        }:
            raise grant_contract.GrantError(
                "GRANT_TERMINAL", "这次授权已经结束，不能再改成执行中。",
                details={"status": current},
            )
        grant["status"] = status
        if reason:
            grant["paused_reason"] = reason
        elif status in grant_contract.AUTHORIZED_STATUSES:
            grant["paused_reason"] = None
        grant["updated_at"] = utcnow().replace(microsecond=0).isoformat()
        payload["grant"] = grant
        payload["status"] = status
        delegation_status = (
            "DONE" if status == "SUCCEEDED"
            else "PAUSED" if status in grant_contract.PAUSED_STATUSES
            else "CANCELLED" if status in {"CANCELLED", "REVOKED"}
            else "FAILED" if status in {"FAILED", "EXPIRED"}
            else "RUNNING"
        )
        row_status = "RUNNING" if status in {"AUTHORIZED", "RUNNING", "WAITING_EXECUTOR",
                                             "VERIFYING"} else delegation_status
        if _write_payload(db, row, payload, expected_version=int(row.budget_version or 0),
                          status=row_status):
            return GrantRow(row=row, grant=grant)
    raise grant_contract.GrantError(
        "GRANT_BUSY", "授权状态正在被另一个请求更新，请稍后重试。"
    )


def revoke(db, grant_id: str, *, actor: str, reason: str = "") -> GrantRow:
    """Stop *new* AI actions.  A submitted executor task follows its own rules."""
    loaded = load(db, grant_id)
    grant_contract.assert_identity(loaded.grant, actor=actor, session_id=None)
    now = utcnow()
    current = grant_contract.grant_status(loaded.grant, now=now)
    if current in {"REVOKED", "EXPIRED"}:
        return loaded
    updated = set_status(db, grant_id, "REVOKED", reason=reason or "用户撤销")
    grant = {**updated.grant,
             "revoked_at": now.replace(microsecond=0).isoformat(),
             "revoked_by": actor}
    return _persist_grant(db, updated.row, grant)


def _persist_grant(db, row: SupportDelegation, grant: dict[str, Any]) -> GrantRow:
    for _ in range(CAS_ATTEMPTS):
        db.refresh(row)
        payload = dict(row.payload or {})
        payload["grant"] = grant
        if _write_payload(db, row, payload, expected_version=int(row.budget_version or 0)):
            return GrantRow(row=row, grant=grant)
    raise grant_contract.GrantError("GRANT_BUSY", "授权状态正在被另一个请求更新，请稍后重试。")


def pause(db, grant_id: str, status: str, *, reason: str) -> GrantRow:
    if status not in grant_contract.PAUSED_STATUSES | {"FAILED"}:
        raise grant_contract.GrantError(
            "INVALID_GRANT_STATUS", "这不是一个可用的暂停状态。", details={"status": status}
        )
    return set_status(db, grant_id, status, reason=reason)


def reserve(
    db,
    grant_id: str,
    *,
    capability_id: str,
    cost: dict[str, int] | None = None,
    counters: dict[str, int] | None = None,
) -> GrantRow:
    """Atomically reserve budget for one step before it is handed to an executor.

    A reservation is written *before* the external call, exactly like the submit
    intent of a render job: an interrupted step keeps its reservation and is
    reconciled by a human, never silently re-spent.
    """
    for _ in range(CAS_ATTEMPTS):
        loaded = load(db, grant_id)
        grant_contract.assert_usable(loaded.grant, now=utcnow())
        grant_contract.assert_contract_frozen(loaded.grant, capability_id)
        grant_contract.assert_budget(loaded.grant, cost=cost, counters=counters)
        payload = dict(loaded.row.payload or {})
        grant = dict(loaded.grant)
        usage = grant_contract.usage_of(grant)
        for key, amount in (cost or {}).items():
            if key in usage:
                usage[key] += int(amount)
        for key, amount in (counters or {}).items():
            if key in usage:
                usage[key] += int(amount)
        grant["usage"] = usage
        grant["status"] = "RUNNING" if grant.get("status") == "AUTHORIZED" else grant.get("status")
        grant["updated_at"] = utcnow().replace(microsecond=0).isoformat()
        payload["grant"] = grant
        if _write_payload(db, loaded.row, payload,
                          expected_version=int(loaded.row.budget_version or 0)):
            return GrantRow(row=loaded.row, grant=grant)
    raise grant_contract.GrantError(
        "GRANT_BUSY", "本次授权的预算正在被另一个请求占用，请稍后重试。"
    )


def settle(
    db,
    grant_id: str,
    *,
    reserved: dict[str, int] | None = None,
    actual: dict[str, int] | None = None,
    step_key: str | None = None,
) -> GrantRow:
    """Refund the unused part of THIS step's reservation — nothing else.

    The ledger is cumulative: step 1 spending 80 and step 2 spending 40 must
    read 120, never 40.  The old form compared one step's actual against the
    whole cumulative usage and overwrote it, so every cheap step erased the
    expensive ones.  Here only ``reserved - actual`` (clamped at zero) is
    subtracted, per key, under the same CAS as every other budget write.

    A step that cost MORE than its estimate keeps the reservation: settlement
    never widens the frozen boundary.  ``actual=None`` means the executor has
    not reported yet (a delegated task): the reservation stands until a real
    receipt arrives.  ``step_key`` (the tool call id) makes a replayed
    settlement a no-op instead of a second refund.
    """
    if not reserved:
        return load(db, grant_id)
    for _ in range(CAS_ATTEMPTS):
        loaded = load(db, grant_id)
        payload = dict(loaded.row.payload or {})
        grant = dict(loaded.grant)
        settlements = dict(grant.get("settlements") or {})
        if step_key and step_key in settlements:
            return loaded
        usage = grant_contract.usage_of(grant)
        changed = False
        for key, reserved_amount in reserved.items():
            if key not in usage:
                continue
            declared = max(0, int(reserved_amount))
            real = max(0, int((actual or {}).get(key, declared)))
            refund = max(0, declared - real)
            if refund and usage[key] >= refund:
                usage[key] -= refund
                changed = True
        if step_key:
            settlements[step_key] = {"reserved": dict(reserved), "actual": dict(actual or {})}
            grant["settlements"] = dict(list(settlements.items())[-200:])
        if not changed and not step_key:
            return loaded
        grant["usage"] = usage
        payload["grant"] = grant
        if _write_payload(db, loaded.row, payload,
                          expected_version=int(loaded.row.budget_version or 0)):
            return GrantRow(row=loaded.row, grant=grant)
    return load(db, grant_id)


def record_tool_call(
    db,
    grant_id: str,
    *,
    tool_call_id: str,
    capability_id: str,
    status: str,
    action_id: str | None = None,
    original_task_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> GrantRow:
    """Append one bounded tool-call fact and spend one tool-call slot.

    The list is trimmed to the last 40 entries on purpose: it is a running
    summary for the UI and the model, not an audit log (the ``support_actions``
    rows and the audit ledger remain the authority).
    """
    for _ in range(CAS_ATTEMPTS):
        loaded = load(db, grant_id)
        payload = dict(loaded.row.payload or {})
        grant = dict(loaded.grant)
        calls = list(grant.get("tool_calls") or [])
        if any(item.get("tool_call_id") == tool_call_id for item in calls):
            # The same tool call is recorded once, whatever the caller retried.
            return loaded
        usage = grant_contract.usage_of(grant)
        usage["max_llm_tool_calls"] = usage["max_llm_tool_calls"] + 1
        grant["usage"] = usage
        calls.append(
            {
                "tool_call_id": tool_call_id,
                "capability_id": capability_id,
                "status": status,
                "action_id": action_id,
                "original_task_id": original_task_id,
                "at": utcnow().replace(microsecond=0).isoformat(),
                **(detail or {}),
            }
        )
        grant["tool_calls"] = calls[-40:]
        grant["updated_at"] = utcnow().replace(microsecond=0).isoformat()
        payload["grant"] = grant
        if _write_payload(db, loaded.row, payload,
                          expected_version=int(loaded.row.budget_version or 0)):
            return GrantRow(row=loaded.row, grant=grant)
    raise grant_contract.GrantError(
        "GRANT_BUSY", "本次授权的工具调用记录正在被另一个请求更新。"
    )


def bind_sub_action(
    db,
    grant_id: str,
    *,
    action_id: str,
    tool_call_id: str,
    capability_id: str,
    project_id: str | None,
    original_task_id: str | None,
) -> GrantRow:
    """Keep the grant → SupportAction → original task identity chain."""
    for _ in range(CAS_ATTEMPTS):
        loaded = load(db, grant_id)
        payload = dict(loaded.row.payload or {})
        grant = dict(loaded.grant)
        entries = list(grant.get("sub_actions") or [])
        if any(item.get("action_id") == action_id for item in entries):
            return loaded
        entries.append(
            {
                "action_id": action_id,
                "tool_call_id": tool_call_id,
                "capability_id": capability_id,
                "project_id": project_id,
                "original_task_id": original_task_id,
                "at": utcnow().replace(microsecond=0).isoformat(),
            }
        )
        grant["sub_actions"] = entries[-60:]
        payload["grant"] = grant
        if _write_payload(db, loaded.row, payload,
                          expected_version=int(loaded.row.budget_version or 0)):
            return GrantRow(row=loaded.row, grant=grant)
    return load(db, grant_id)


def assert_tool_call_budget(grant: dict[str, Any]) -> None:
    left = grant_contract.remaining(grant)["max_llm_tool_calls"]
    if left <= 0:
        raise grant_contract.GrantError(
            "GRANT_TOOL_CALL_BUDGET_EXHAUSTED",
            "本次授权的工具调用次数已用尽。",
            details={"limit": "max_llm_tool_calls"},
        )


def assert_runtime_budget(grant: dict[str, Any], *, started_at: datetime | None) -> None:
    if started_at is None:
        return
    elapsed = int((utcnow() - started_at).total_seconds())
    allowed = int((grant.get("limits") or {}).get(
        "max_total_runtime_seconds", grant_contract.DEFAULT_LIMITS["max_total_runtime_seconds"]
    ))
    if elapsed > allowed:
        raise grant_contract.GrantError(
            "GRANT_RUNTIME_BUDGET_EXHAUSTED",
            "本次授权的总运行时长已用尽。",
            details={"limit": "max_total_runtime_seconds", "elapsed": elapsed},
        )


def sub_action_public(grant: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "action_id": item.get("action_id"),
            "tool_call_id": item.get("tool_call_id"),
            "capability_id": item.get("capability_id"),
            "original_task_id": item.get("original_task_id"),
        }
        for item in (grant.get("sub_actions") or [])
    ]


# ---------------------------------------------------------------------------
# The autonomous loop's durable driver state (V3.1 closeout).
#
# The transcript, the step ledger and the pending original task live in
# ``payload.grant["loop"]`` — the SAME row as the authorization, never a second
# task table.  One driver at a time holds a short lease; a closed page, an API
# restart or a repeated click reconnects to this state instead of starting a
# second execution.  The lease is a claim on the *driver*, not on the original
# executor tasks: those keep their own Worker claims and safe-stop rules.
# ---------------------------------------------------------------------------

LOOP_LEASE_SECONDS = 120


def loop_state(grant: dict[str, Any]) -> dict[str, Any]:
    state = grant.get("loop")
    return dict(state) if isinstance(state, dict) else {}


def claim_loop(
    db, grant_id: str, *, owner: str, lease_seconds: int = LOOP_LEASE_SECONDS
) -> bool:
    """Atomically claim the loop driver.  False = another live owner holds it."""
    for _ in range(CAS_ATTEMPTS):
        loaded = load(db, grant_id)
        grant = dict(loaded.grant)
        state = loop_state(grant)
        now = utcnow()
        holder = str(state.get("owner") or "")
        if holder and holder != owner:
            try:
                deadline = grant_contract.parse_iso(str(state.get("lease_expires_at") or ""))
            except (ValueError, TypeError):
                deadline = None
            if deadline is not None and now < deadline:
                return False
        state["owner"] = owner
        state["lease_expires_at"] = (
            (now + timedelta(seconds=lease_seconds)).replace(microsecond=0).isoformat()
        )
        grant["loop"] = state
        payload = dict(loaded.row.payload or {})
        payload["grant"] = grant
        if _write_payload(db, loaded.row, payload,
                          expected_version=int(loaded.row.budget_version or 0)):
            return True
    raise grant_contract.GrantError(
        "GRANT_BUSY", "本次授权的执行权正在被另一个请求更新，请稍后重试。"
    )


def save_loop(
    db, grant_id: str, state: dict[str, Any], *, lease_seconds: int = LOOP_LEASE_SECONDS
) -> GrantRow:
    """Persist the driver state and renew its lease (CAS)."""
    for _ in range(CAS_ATTEMPTS):
        loaded = load(db, grant_id)
        grant = dict(loaded.grant)
        merged = {**loop_state(grant), **state}
        if merged.get("owner"):
            merged["lease_expires_at"] = (
                (utcnow() + timedelta(seconds=lease_seconds)).replace(microsecond=0).isoformat()
            )
        grant["loop"] = merged
        payload = dict(loaded.row.payload or {})
        payload["grant"] = grant
        if _write_payload(db, loaded.row, payload,
                          expected_version=int(loaded.row.budget_version or 0)):
            return GrantRow(row=loaded.row, grant=grant)
    raise grant_contract.GrantError(
        "GRANT_BUSY", "本次授权的执行状态正在被另一个请求更新，请稍后重试。"
    )


def release_loop(db, grant_id: str) -> None:
    """A finished driver leaves the transcript for audit but holds no lease."""
    for _ in range(CAS_ATTEMPTS):
        loaded = load(db, grant_id)
        grant = dict(loaded.grant)
        state = loop_state(grant)
        if not state.get("owner"):
            return
        state["owner"] = None
        state["lease_expires_at"] = None
        grant["loop"] = state
        payload = dict(loaded.row.payload or {})
        payload["grant"] = grant
        if _write_payload(db, loaded.row, payload,
                          expected_version=int(loaded.row.budget_version or 0)):
            return
    # A lost race here is harmless: the lease expires on its own.


def save_objective_verification(db, grant_id: str, verification: dict[str, Any]) -> None:
    """Persist the goal-level conclusion beside the grant (one fact, one write).

    This is the loop's read of the ORIGINAL authorities at finish time; the UI
    and the report read it back instead of re-deriving a second conclusion.
    """
    for _ in range(CAS_ATTEMPTS):
        loaded = load(db, grant_id)
        payload = dict(loaded.row.payload or {})
        payload["objective_verification"] = verification
        if _write_payload(db, loaded.row, payload,
                          expected_version=int(loaded.row.budget_version or 0)):
            return
    # A concurrent revoke wins; the conclusion is still readable from the loop
    # result that produced it.
