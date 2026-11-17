"""Controlled multi-step delegation for an authorized, bounded plan.

This is deliberately *not* a second production system.  It keeps only

* the approved step orders and budget the person granted,
* one reference to each original executor task/run,

inside the support session's own context JSON.  Every step is still executed by
``support.actions`` (preview → approval → original executor); this module just
walks the ordered list, stops at human decisions and high-cost steps, and never
resubmits an UNKNOWN task.
"""
from __future__ import annotations

from typing import Any

from shine.models import new_id, utcnow
from shine.support import actions as action_service
from shine.support.models import SupportSession

#: Steps the person may authorize as one low-risk batch.
AUTO_KINDS = frozenset(
    {
        "project_create",
        "document_import_text",
        "document_switch_basis",
        "project_settings_update",
        "story_outline_generate",
        "storyboard_generate",
        "literary_scripts_generate",
        "production_control",
    }
)
#: Steps that consume GPU; they must fit the explicitly granted budget.
GPU_KINDS = frozenset({"production_start"})
PAID_KINDS = frozenset({"story_outline_generate", "storyboard_generate", "literary_scripts_generate"})

DEFAULT_BUDGET = {"max_episodes": 1, "max_gpu_actions": 0, "max_paid_actions": 2}


class DelegationError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _context(session: SupportSession) -> dict[str, Any]:
    return dict(session.context_json or {})


def _save(session: SupportSession, payload: dict[str, Any]) -> None:
    session.context_json = {**_context(session), "delegation": payload}
    session.updated_at = utcnow()


def get(session: SupportSession) -> dict[str, Any] | None:
    value = _context(session).get("delegation")
    return value if isinstance(value, dict) else None


def _budget(raw: dict[str, Any] | None) -> dict[str, int]:
    merged = dict(DEFAULT_BUDGET)
    for key in DEFAULT_BUDGET:
        value = (raw or {}).get(key)
        if isinstance(value, int) and value >= 0:
            merged[key] = value
    return merged


def _scope_matches(payload: dict[str, Any], step: dict[str, Any]) -> bool:
    project_id = payload.get("project_id")
    params = ((step.get("action") or {}).get("params")) or {}
    target = params.get("project_id")
    if project_id and target and project_id != target:
        return False
    allowed_episodes = set(payload.get("episode_ids") or [])
    requested = set(params.get("episode_ids") or [])
    if allowed_episodes and requested and not requested.issubset(allowed_episodes):
        return False
    return True


def _episode_count(step: dict[str, Any]) -> int:
    params = ((step.get("action") or {}).get("params")) or {}
    return len(params.get("episode_ids") or []) or 1


def create(
    db,
    settings,
    session_factory,
    *,
    session: SupportSession,
    approved_orders: list[int],
    budget: dict[str, Any] | None,
    actor: str = "local-user",
    gateway=None,
) -> dict[str, Any]:
    context = _context(session)
    plan = context.get("last_plan") or {}
    steps = list(plan.get("steps") or [])
    if not steps:
        raise DelegationError("PLAN_REQUIRED", "还没有可委托的计划，请先生成计划。")
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

    payload = {
        "id": new_id(),
        "status": "RUNNING",
        "created_at": utcnow().isoformat(),
        "updated_at": utcnow().isoformat(),
        "project_id": plan.get("project_id"),
        "episode_ids": list(plan.get("episode_ids") or []),
        "budget": _budget(budget),
        "gpu_actions_used": 0,
        "paid_actions_used": 0,
        "waiting_action_id": None,
        "stop_reason": None,
        "steps": [],
    }
    for step in steps:
        order = int(step.get("order") or 0)
        entry = {
            "order": order,
            "title": step.get("title"),
            "kind": (step.get("action") or {}).get("kind"),
            "params": (step.get("action") or {}).get("params") or {},
            "plan_state": step.get("state"),
            "state": "PENDING" if order in orders and step.get("state") == "READY" else "SKIPPED",
            "action_id": None,
            "message": "",
        }
        if order in orders and step.get("state") != "READY":
            entry["state"] = "PAUSED"
            entry["message"] = "这一步不是可自动执行的低风险步骤，需要人工处理。"
        payload["steps"].append(entry)
    if not any(entry["state"] == "PENDING" for entry in payload["steps"]):
        raise DelegationError("NO_DELEGATABLE_STEP", "勾选的步骤里没有可自动执行的低风险步骤。")
    _save(session, payload)
    db.commit()
    return _advance(db, settings, session_factory, session=session, actor=actor, gateway=gateway)


def _finish(payload: dict[str, Any], status: str, reason: str | None = None) -> dict[str, Any]:
    payload["status"] = status
    payload["stop_reason"] = reason
    payload["updated_at"] = utcnow().isoformat()
    payload["waiting_action_id"] = None
    return payload


def _advance(
    db,
    settings,
    session_factory,
    *,
    session: SupportSession,
    actor: str,
    gateway=None,
) -> dict[str, Any]:
    payload = get(session) or {}
    if payload.get("status") not in {"RUNNING", "WAITING"}:
        return payload
    budget = _budget(payload.get("budget"))
    for step in payload.get("steps") or []:
        if step.get("state") not in {"PENDING", "RUNNING"}:
            continue
        if step.get("state") == "RUNNING" and step.get("action_id"):
            continue
        params = dict(step.get("params") or {})
        kind = str(step.get("kind") or "")
        if not kind:
            step["state"] = "PAUSED"
            step["message"] = "这一步没有注册动作，需要人工处理。"
            return _finish(payload, "PAUSED", "HUMAN_REQUIRED")
        if not _scope_matches(payload, step):
            return _finish(payload, "FAILED", "SCOPE_EXCEEDED")
        if kind in GPU_KINDS:
            if payload.get("gpu_actions_used", 0) >= budget["max_gpu_actions"]:
                step["state"] = "PAUSED"
                step["message"] = "这一步会占用 GPU，但本次授权没有覆盖。"
                return _finish(payload, "PAUSED", "BUDGET_REQUIRED")
            if _episode_count(step) > budget["max_episodes"]:
                step["state"] = "PAUSED"
                step["message"] = "集数超出本次授权预算。"
                return _finish(payload, "PAUSED", "BUDGET_REQUIRED")
            payload["gpu_actions_used"] = payload.get("gpu_actions_used", 0) + 1
        if kind in PAID_KINDS:
            if payload.get("paid_actions_used", 0) >= budget["max_paid_actions"]:
                step["state"] = "PAUSED"
                step["message"] = "付费调用次数超出本次授权预算。"
                return _finish(payload, "PAUSED", "BUDGET_REQUIRED")
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
            )
            step["action_id"] = action["id"]
            step["state"] = "RUNNING"
            _save(session, payload)
            db.commit()
            result, _ = action_service.approve_action(
                db,
                settings,
                session_factory,
                action_id=action["id"],
                preview_hash=action["preview_hash"],
                actor=actor,
                gateway=gateway,
            )
        except action_service.ActionBlocked as exc:
            step["state"] = "FAILED"
            step["message"] = exc.message
            return _finish(payload, "FAILED", exc.code)
        status = str(result.get("status") or "")
        if status == "SUCCEEDED":
            step["state"] = "SUCCEEDED"
            continue
        if status in {"FAILED", "REFUSED", "EXPIRED", "SUPERSEDED"}:
            step["state"] = "FAILED"
            step["message"] = result.get("last_error") or status
            return _finish(payload, "FAILED", status)
        if status == "NEEDS_REVIEW":
            step["state"] = "NEEDS_REVIEW"
            step["message"] = "原任务结果不确定，需要先核对；不会自动重提。"
            payload["waiting_action_id"] = step["action_id"]
            return _finish(payload, "PAUSED", "NEEDS_REVIEW")
        # DELEGATED / EXECUTED: the original task is running outside this loop.
        step["state"] = "WAITING"
        payload["waiting_action_id"] = step["action_id"]
        payload["status"] = "WAITING"
        payload["updated_at"] = utcnow().isoformat()
        return payload
    return _finish(payload, "DONE")


def refresh(
    db,
    settings,
    session_factory,
    *,
    session: SupportSession,
    actor: str = "local-user",
    gateway=None,
) -> dict[str, Any]:
    payload = get(session) or {}
    if payload.get("status") != "WAITING" or not payload.get("waiting_action_id"):
        return payload
    from shine.support.models import SupportAction

    row = db.get(SupportAction, payload["waiting_action_id"])
    if row is None:
        return _finish(payload, "PAUSED", "TASK_RECORD_MISSING")
    public = action_service.refresh_action(db, settings, row)
    status = str(public.get("status") or "")
    step = next(
        (item for item in payload.get("steps") or [] if item.get("action_id") == row.id),
        None,
    )
    if step is None:
        return _finish(payload, "PAUSED", "TASK_RECORD_MISSING")
    if status == "SUCCEEDED":
        step["state"] = "SUCCEEDED"
        payload["waiting_action_id"] = None
        payload["status"] = "RUNNING"
        _save(session, payload)
        db.commit()
        return _advance(db, settings, session_factory, session=session, actor=actor, gateway=gateway)
    if status in {"FAILED", "REFUSED", "EXPIRED", "SUPERSEDED"}:
        step["state"] = "FAILED"
        step["message"] = public.get("last_error") or status
        return _finish(payload, "FAILED", status)
    if status == "NEEDS_REVIEW":
        step["state"] = "NEEDS_REVIEW"
        step["message"] = "原任务结果不确定，需要先核对；不会自动重提。"
        return _finish(payload, "PAUSED", "NEEDS_REVIEW")
    # Still running: keep waiting on the same original task.  Never resubmit.
    return payload


def cancel(*, session: SupportSession) -> dict[str, Any]:
    payload = get(session) or {}
    if not payload:
        raise DelegationError("DELEGATION_REQUIRED", "当前会话没有可取消的委托。")
    for step in payload.get("steps") or []:
        if step.get("state") in {"PENDING"}:
            step["state"] = "CANCELLED"
        elif step.get("state") in {"RUNNING", "WAITING", "NEEDS_REVIEW"}:
            step["message"] = "已提交给原执行器，取消委托不会断开或取消它。"
    return _finish(payload, "CANCELLED")
