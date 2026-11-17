"""Controlled multi-step delegation for an authorized, bounded plan.

This is deliberately *not* a second production system.  It keeps only

* the approved plan identity, step orders and budget the person granted,
* one reference to each original executor task/run,

in one ``support_delegations`` row.  Every step is still executed by
``support.actions`` (preview → approval → original executor); this module just
walks the ordered list, stops at human decisions and high-cost steps, never
resubmits an UNKNOWN task, and uses a database-level unique authorization key so
concurrent or repeated authorization cannot create the same work twice.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.attributes import flag_modified

from shine.copilot.contracts import canonical, digest
from shine.copilot.declarations import WRITE_ACTIONS
from shine.models import new_id, utcnow
from shine.support import actions as action_service
from shine.support import contracts as contract_registry
from shine.support.models import SupportDelegation, SupportSession

#: Auto-advanced steps are exactly the non-GPU, no-occupancy-gate capabilities
#: declared by the product.  These sets are derived, never hand-kept.
AUTO_KINDS = frozenset(
    row.action_id
    for row in WRITE_ACTIONS
    if row.occupancy_gate == "NONE" and not row.gpu
)
#: Steps that consume GPU; they must fit the explicitly granted budget.
GPU_KINDS = frozenset(row.action_id for row in WRITE_ACTIONS if row.gpu)
PAID_KINDS = frozenset(row.action_id for row in WRITE_ACTIONS if row.paid)

DEFAULT_BUDGET = {"max_episodes": 1, "max_gpu_actions": 0, "max_paid_actions": 2}


class DelegationError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _budget(raw: dict[str, Any] | None) -> dict[str, int]:
    merged = dict(DEFAULT_BUDGET)
    for key in DEFAULT_BUDGET:
        value = (raw or {}).get(key)
        if isinstance(value, int) and value >= 0:
            merged[key] = value
    return merged


def _public(payload: dict[str, Any]) -> dict[str, Any]:
    return dict(payload)


def _row_payload(row: SupportDelegation) -> dict[str, Any]:
    payload = dict(row.payload or {})
    payload["id"] = payload.get("id") or row.id
    payload["status"] = row.status
    payload["stop_reason"] = row.stop_reason
    payload["waiting_action_id"] = row.waiting_action_id
    payload["plan_id"] = row.plan_id
    payload["plan_hash"] = row.plan_hash
    return payload


def _apply(payload: dict[str, Any], row: SupportDelegation) -> None:
    row.payload = payload
    flag_modified(row, "payload")
    row.status = str(payload.get("status") or row.status)
    row.stop_reason = payload.get("stop_reason")
    row.waiting_action_id = payload.get("waiting_action_id")
    row.plan_id = payload.get("plan_id") or row.plan_id
    row.plan_hash = payload.get("plan_hash") or row.plan_hash
    row.updated_at = utcnow()


def _legacy_context(session: SupportSession) -> dict[str, Any] | None:
    value = dict(session.context_json or {}).get("delegation")
    return value if isinstance(value, dict) else None


def _import_legacy(db, session: SupportSession) -> SupportDelegation | None:
    """Move a V2 context-only delegation into the unified ledger once."""
    legacy = _legacy_context(session)
    if not legacy:
        return None
    payload = {**legacy}
    payload["id"] = str(payload.get("id") or new_id())
    key = "legacy:" + digest(canonical(payload))[:87]
    row = SupportDelegation(
        id=payload["id"],
        session_id=session.id,
        plan_id=payload.get("plan_id"),
        plan_hash=payload.get("plan_hash"),
        authorization_key=key,
        status=str(payload.get("status") or "PAUSED"),
        stop_reason=payload.get("stop_reason"),
        waiting_action_id=payload.get("waiting_action_id"),
        payload=payload,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(SupportDelegation).where(SupportDelegation.authorization_key == key)
        )
        return existing
    return row


def _load(db, session: SupportSession) -> SupportDelegation | None:
    row = db.scalar(
        select(SupportDelegation)
        .where(SupportDelegation.session_id == session.id)
        .order_by(SupportDelegation.created_at.desc())
    )
    if row is not None:
        return row
    return _import_legacy(db, session)


def get(db, session: SupportSession) -> dict[str, Any] | None:
    row = _load(db, session)
    return _row_payload(row) if row is not None else None


def _scope_matches(payload: dict[str, Any], step: dict[str, Any]) -> bool:
    project_id = payload.get("project_id")
    params = ((step.get("action") or {}).get("params")) or step.get("params") or {}
    target = params.get("project_id")
    if project_id and target and project_id != target:
        return False
    allowed_episodes = set(payload.get("episode_ids") or [])
    requested = set(params.get("episode_ids") or [])
    if allowed_episodes and requested and not requested.issubset(allowed_episodes):
        return False
    return True


def _episode_count(step: dict[str, Any]) -> int:
    params = ((step.get("action") or {}).get("params")) or step.get("params") or {}
    return len(params.get("episode_ids") or []) or 1


def _authorization_key(
    session_id: str,
    *,
    plan_id: str | None,
    plan_hash: str | None,
    orders: list[int],
    budget: dict[str, int],
) -> str:
    return digest(
        canonical(
            {
                "session_id": session_id,
                "plan_id": plan_id,
                "plan_hash": plan_hash,
                "orders": sorted(int(value) for value in orders),
                "budget": budget,
            }
        )
    )


def create(
    db,
    settings,
    session_factory,
    *,
    session: SupportSession,
    approved_orders: list[int],
    plan_id: str | None = None,
    plan_hash: str | None = None,
    budget: dict[str, Any] | None = None,
    actor: str = "local-user",
    gateway=None,
    app=None,
) -> dict[str, Any]:
    context = dict(session.context_json or {})
    plan = context.get("last_plan") or {}
    steps = list(plan.get("steps") or [])
    if not steps:
        raise DelegationError("PLAN_REQUIRED", "还没有可委托的计划，请先生成计划。")

    # The authorization must name the exact plan the person previewed.  A stale
    # button rendered for an older plan can never authorize a newer one.
    expected_id = plan.get("plan_id")
    expected_hash = plan.get("plan_hash")
    if expected_id:
        if not plan_id:
            raise DelegationError("PLAN_ID_REQUIRED", "请从你预览过的计划发起授权。")
        if plan_id != expected_id:
            raise DelegationError("PLAN_CHANGED", "这个计划已经在当前会话中更新，请重新预览后再授权。")
    if expected_hash and plan_hash and plan_hash != expected_hash:
        raise DelegationError("PLAN_CHANGED", "计划内容与预览时不一致，请重新预览后再授权。")

    orders = {int(value) for value in approved_orders}
    unknown = orders - {int(step.get("order") or 0) for step in steps}
    if unknown:
        raise DelegationError("STEP_NOT_IN_PLAN", "授权的步骤不在当前计划里。")
    plan_project = plan.get("project_id")
    plan_episodes = set(plan.get("episode_ids") or [])
    for step in steps:
        if int(step.get("order") or 0) not in orders:
            continue
        params = ((step.get("action") or {}).get("params")) or {}
        target = params.get("project_id")
        if plan_project and target and target != plan_project:
            raise DelegationError(
                "SCOPE_EXCEEDED", "步骤的目标项目超出本次计划范围，已拒绝委托。"
            )
        requested = set(params.get("episode_ids") or [])
        if plan_episodes and requested and not requested.issubset(plan_episodes):
            raise DelegationError(
                "SCOPE_EXCEEDED", "步骤的集数超出本次计划范围，已拒绝委托。"
            )

    normalized_budget = _budget(budget)
    key = _authorization_key(
        session.id,
        plan_id=plan_id or expected_id,
        plan_hash=plan_hash or expected_hash,
        orders=sorted(orders),
        budget=normalized_budget,
    )
    existing = db.scalar(
        select(SupportDelegation).where(SupportDelegation.authorization_key == key)
    )
    if existing is not None:
        # Repeated authorization of the whole delegation is idempotent: the
        # original project/run is never created again.
        return _row_payload(existing)

    payload = {
        "id": new_id(),
        "status": "RUNNING",
        "created_at": utcnow().isoformat(),
        "updated_at": utcnow().isoformat(),
        "plan_id": plan_id or expected_id,
        "plan_hash": plan_hash or expected_hash,
        "authorization_key": key,
        "project_id": plan.get("project_id"),
        "episode_ids": list(plan.get("episode_ids") or []),
        "budget": normalized_budget,
        "gpu_actions_used": 0,
        "paid_actions_used": 0,
        "waiting_action_id": None,
        "stop_reason": None,
        "steps": [],
    }
    for step in steps:
        order = int(step.get("order") or 0)
        kind = (step.get("action") or {}).get("kind")
        entry = {
            "order": order,
            "title": step.get("title"),
            "kind": kind,
            "params": (step.get("action") or {}).get("params") or {},
            "plan_state": step.get("state"),
            "state": "PENDING" if order in orders and step.get("state") == "READY" else "SKIPPED",
            "action_id": None,
            "message": "",
            # V2.1: the step is authorized against the contract that existed
            # when the person approved it, not against a future build.
            "contract_digest": contract_registry.live_contract_digest(str(kind)) if kind else None,
            "contract_revision": (contract_registry.live_contract(str(kind)) or {}).get(
                "contract_revision"
            )
            if kind
            else None,
        }
        if order in orders and step.get("state") != "READY":
            entry["state"] = "PAUSED"
            entry["message"] = "这一步不是可自动执行的低风险步骤，需要人工处理。"
        payload["steps"].append(entry)
    if not any(entry["state"] == "PENDING" for entry in payload["steps"]):
        raise DelegationError("NO_DELEGATABLE_STEP", "勾选的步骤里没有可自动执行的低风险步骤。")

    row = SupportDelegation(
        id=payload["id"],
        session_id=session.id,
        plan_id=payload["plan_id"],
        plan_hash=payload["plan_hash"],
        authorization_key=key,
        status="RUNNING",
        payload=payload,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        # Two concurrent authorizations of the same delegation: the database
        # unique key decides; the second caller adopts the winner.
        db.rollback()
        existing = db.scalar(
            select(SupportDelegation).where(SupportDelegation.authorization_key == key)
        )
        if existing is None:
            raise
        return _row_payload(existing)
    return _advance(
        db,
        settings,
        session_factory,
        session=session,
        row=row,
        actor=actor,
        gateway=gateway,
        app=app,
    )


def _finish(payload: dict[str, Any], status: str, reason: str | None = None) -> dict[str, Any]:
    payload["status"] = status
    payload["stop_reason"] = reason
    payload["updated_at"] = utcnow().isoformat()
    payload["waiting_action_id"] = None
    return payload


def _step_contract_state(step: dict[str, Any], app=None) -> tuple[bool, str | None, str | None]:
    """Return (ok, stop_reason, message) for an unexecuted step."""
    kind = str(step.get("kind") or "")
    if not kind:
        return False, "HUMAN_REQUIRED", "这一步没有注册动作，需要人工处理。"
    current_digest = contract_registry.live_contract_digest(kind)
    stored_digest = step.get("contract_digest")
    if not stored_digest:
        return False, "CONTRACT_UNVERIFIED", "这一步是在旧版本授权的，能力合同未核对；请重新确认后再执行。"
    packaged_state = contract_registry.execution_state(kind)
    if not packaged_state["executable"]:
        return (
            False,
            "CONTRACT_CHANGED",
            packaged_state.get("notice")
            or "这个操作在当前版本已经调整，请重新确认这一步。",
        )
    if current_digest != stored_digest:
        return False, "CONTRACT_CHANGED", "这个操作在当前版本已经调整，请重新确认这一步。"
    if app is not None:
        api_state = contract_registry.api_contract_state(kind, app)
        if api_state["state"] != "AVAILABLE":
            return (
                False,
                "CONTRACT_CHANGED",
                "这个操作的请求参数合同发生了不兼容变化，请重新确认这一步。",
            )
    return True, None, None


def _advance(
    db,
    settings,
    session_factory,
    *,
    session: SupportSession,
    row: SupportDelegation,
    actor: str,
    gateway=None,
    app=None,
) -> dict[str, Any]:
    payload = _row_payload(row)
    if payload.get("status") not in {"RUNNING", "WAITING"}:
        return payload
    budget = _budget(payload.get("budget"))
    for step in payload.get("steps") or []:
        state = step.get("state")
        if state == "RUNNING" and step.get("action_id"):
            # Process restart / another refresh reached this step.  Read the
            # original action; never resubmit it merely because it is RUNNING.
            from shine.support.models import SupportAction

            action = db.get(SupportAction, step["action_id"])
            if action is None:
                step["state"] = "PAUSED"
                step["message"] = "这一步的授权记录缺失，无法确认原始任务归属。"
                _apply(_finish(payload, "PAUSED", "TASK_RECORD_MISSING"), row)
                db.commit()
                return _row_payload(row)
            public = action_service.refresh_action(db, settings, action)
            status = str(public.get("status") or "")
            if status == "SUCCEEDED":
                step["state"] = "SUCCEEDED"
                continue
            if status in {"FAILED", "REFUSED", "EXPIRED", "SUPERSEDED"}:
                step["state"] = "FAILED"
                step["message"] = public.get("last_error") or status
                _apply(_finish(payload, "FAILED", status), row)
                db.commit()
                return _row_payload(row)
            if status == "NEEDS_REVIEW":
                step["state"] = "NEEDS_REVIEW"
                step["message"] = "原任务结果不确定，需要先核对；不会自动重提。"
                payload["waiting_action_id"] = step.get("action_id")
                _apply(_finish(payload, "PAUSED", "NEEDS_REVIEW"), row)
                db.commit()
                return _row_payload(row)
            payload["waiting_action_id"] = step.get("action_id")
            payload["status"] = "WAITING"
            payload["updated_at"] = utcnow().isoformat()
            _apply(payload, row)
            db.commit()
            return _row_payload(row)
        if state == "RUNNING" and not step.get("action_id"):
            step["state"] = "PAUSED"
            step["message"] = "这一步在执行前中断，结果不确定；请重新确认，不会自动重提。"
            _apply(_finish(payload, "PAUSED", "UNKNOWN"), row)
            db.commit()
            return _row_payload(row)
        if state != "PENDING":
            continue
        params = dict(step.get("params") or {})
        kind = str(step.get("kind") or "")
        ok, reason, message = _step_contract_state(step, app)
        if not ok:
            step["state"] = "PAUSED"
            step["message"] = message or ""
            _apply(_finish(payload, "PAUSED", reason), row)
            db.commit()
            return _row_payload(row)
        if not _scope_matches(payload, step):
            _apply(_finish(payload, "FAILED", "SCOPE_EXCEEDED"), row)
            db.commit()
            return _row_payload(row)
        if kind in GPU_KINDS:
            if payload.get("gpu_actions_used", 0) >= budget["max_gpu_actions"]:
                step["state"] = "PAUSED"
                step["message"] = "这一步会占用 GPU，但本次授权没有覆盖。"
                _apply(_finish(payload, "PAUSED", "BUDGET_REQUIRED"), row)
                db.commit()
                return _row_payload(row)
            if _episode_count(step) > budget["max_episodes"]:
                step["state"] = "PAUSED"
                step["message"] = "集数超出本次授权预算。"
                _apply(_finish(payload, "PAUSED", "BUDGET_REQUIRED"), row)
                db.commit()
                return _row_payload(row)
            payload["gpu_actions_used"] = payload.get("gpu_actions_used", 0) + 1
        if kind in PAID_KINDS:
            if payload.get("paid_actions_used", 0) >= budget["max_paid_actions"]:
                step["state"] = "PAUSED"
                step["message"] = "付费调用次数超出本次授权预算。"
                _apply(_finish(payload, "PAUSED", "BUDGET_REQUIRED"), row)
                db.commit()
                return _row_payload(row)
            payload["paid_actions_used"] = payload.get("paid_actions_used", 0) + 1
        try:
            action, _ = action_service.preview_action(
                db,
                settings,
                kind=kind,
                params=params,
                actor=actor,
                session_id=session.id,
                project_id=payload.get("project_id"),
                app=app,
            )
            step["action_id"] = action["id"]
            step["state"] = "RUNNING"
            _apply(payload, row)
            db.commit()
            result, _ = action_service.approve_action(
                db,
                settings,
                session_factory,
                action_id=action["id"],
                preview_hash=action["preview_hash"],
                actor=actor,
                gateway=gateway,
                app=app,
            )
        except action_service.ActionBlocked as exc:
            step["state"] = "FAILED"
            step["message"] = exc.message
            _apply(_finish(payload, "FAILED", exc.code), row)
            db.commit()
            return _row_payload(row)
        status = str(result.get("status") or "")
        if status == "SUCCEEDED":
            step["state"] = "SUCCEEDED"
            continue
        if status in {"FAILED", "REFUSED", "EXPIRED", "SUPERSEDED"}:
            step["state"] = "FAILED"
            step["message"] = result.get("last_error") or status
            _apply(_finish(payload, "FAILED", status), row)
            db.commit()
            return _row_payload(row)
        if status == "NEEDS_REVIEW":
            step["state"] = "NEEDS_REVIEW"
            step["message"] = "原任务结果不确定，需要先核对；不会自动重提。"
            payload["waiting_action_id"] = step["action_id"]
            _apply(_finish(payload, "PAUSED", "NEEDS_REVIEW"), row)
            db.commit()
            return _row_payload(row)
        # DELEGATED / EXECUTED: the original task is running outside this loop.
        step["state"] = "WAITING"
        payload["waiting_action_id"] = step["action_id"]
        payload["status"] = "WAITING"
        payload["updated_at"] = utcnow().isoformat()
        _apply(payload, row)
        db.commit()
        return _row_payload(row)
    _apply(_finish(payload, "DONE"), row)
    db.commit()
    return _row_payload(row)


def refresh(
    db,
    settings,
    session_factory,
    *,
    session: SupportSession,
    actor: str = "local-user",
    gateway=None,
    app=None,
) -> dict[str, Any]:
    row = _load(db, session)
    if row is None:
        return {}
    payload = _row_payload(row)
    if payload.get("status") == "WAITING" and payload.get("waiting_action_id"):
        from shine.support.models import SupportAction

        action = db.get(SupportAction, payload["waiting_action_id"])
        if action is None:
            _apply(_finish(payload, "PAUSED", "TASK_RECORD_MISSING"), row)
            db.commit()
            return _row_payload(row)
        public = action_service.refresh_action(db, settings, action)
        status = str(public.get("status") or "")
        step = next(
            (item for item in payload.get("steps") or [] if item.get("action_id") == action.id),
            None,
        )
        if step is None:
            _apply(_finish(payload, "PAUSED", "TASK_RECORD_MISSING"), row)
            db.commit()
            return _row_payload(row)
        if status == "SUCCEEDED":
            step["state"] = "SUCCEEDED"
            payload["waiting_action_id"] = None
            payload["status"] = "RUNNING"
            _apply(payload, row)
            db.commit()
            return _advance(
                db, settings, session_factory, session=session, row=row,
                actor=actor, gateway=gateway, app=app,
            )
        if status in {"FAILED", "REFUSED", "EXPIRED", "SUPERSEDED"}:
            step["state"] = "FAILED"
            step["message"] = public.get("last_error") or status
            _apply(_finish(payload, "FAILED", status), row)
            db.commit()
            return _row_payload(row)
        if status == "NEEDS_REVIEW":
            step["state"] = "NEEDS_REVIEW"
            step["message"] = "原任务结果不确定，需要先核对；不会自动重提。"
            _apply(_finish(payload, "PAUSED", "NEEDS_REVIEW"), row)
            db.commit()
            return _row_payload(row)
        # Still running: keep waiting on the same original task.  Never resubmit.
        return _row_payload(row)
    if payload.get("status") not in {"RUNNING", "WAITING"}:
        return payload
    return _advance(
        db, settings, session_factory, session=session, row=row,
        actor=actor, gateway=gateway, app=app,
    )


def cancel(*, db, session: SupportSession) -> dict[str, Any]:
    row = _load(db, session)
    if row is None:
        raise DelegationError("DELEGATION_REQUIRED", "当前会话没有可取消的委托。")
    payload = _row_payload(row)
    for step in payload.get("steps") or []:
        if step.get("state") in {"PENDING"}:
            step["state"] = "CANCELLED"
        elif step.get("state") in {"RUNNING", "WAITING", "NEEDS_REVIEW"}:
            step["message"] = "已提交给原执行器，取消委托不会断开或取消它。"
    _apply(_finish(payload, "CANCELLED"), row)
    db.commit()
    return _row_payload(row)
