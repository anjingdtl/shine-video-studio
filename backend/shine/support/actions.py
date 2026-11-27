"""Preview → explicit authorization → existing executor → re-read authority.

The copilot never executes a change itself. Each action is a thin, idempotent
authorization envelope around an authoritative executor, dispatched cleanly to
domain handlers under :mod:`shine.support.action_handlers`.
"""
from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from shine.copilot.declarations import (
    MACHINE_SCOPED_KINDS,
    NO_GPU_GATE_KINDS,
    PROJECT_OPTIONAL_KINDS,
    PROJECT_SCOPED_KINDS,
)
from shine.models import new_id, utcnow
from shine.support.action_handlers import (
    ActionBlocked,
    ActionInvalid,
    ActionStale,
    ActionUnknown,
    get_handler,
)
from shine.support.action_handlers.setup import (
    READY_CAPABILITY_STATES,
    VERIFIED_CAPABILITY_STATES,
)
from shine.support.context import build_evidence_snapshot, snapshot_revision
from shine.support.models import SupportAction
from shine.support.occupancy import resource_occupancy
from shine.support.privacy import private_roots_for, redact_payload
from shine.support.tools import ACTION_SPECS, ToolDenied, action_spec

__all__ = [
    "ActionBlocked",
    "ActionInvalid",
    "ActionStale",
    "ActionUnknown",
    "ACTION_SPECS",
    "APPROVAL_GRACE",
    "RELEASE_OUTCOMES",
    "REUSABLE_STATUSES",
    "TERMINAL_ACTION_STATUSES",
    "action_public",
    "approve_action",
    "execute_under_grant",
    "preview_action",
    "read_under_grant",
    "refresh_action",
    "resolve_target_project",
    "rollback_action",
]

REUSABLE_STATUSES = {"PREVIEWED", "APPROVED", "DELEGATED"}
APPROVAL_GRACE = timedelta(minutes=10)

TERMINAL_ACTION_STATUSES = {
    "SUCCEEDED",
    "FAILED",
    "REFUSED",
    "EXPIRED",
    "NEEDS_REVIEW",
    "SUPERSEDED",
    "ROLLED_BACK",
}
RELEASE_OUTCOMES = {"RELEASED", "PARTIAL", "REFUSED", "UNKNOWN"}

_PROJECT_SCOPED_KINDS = PROJECT_SCOPED_KINDS
_PROJECT_OPTIONAL_KINDS = PROJECT_OPTIONAL_KINDS
_MACHINE_SCOPED_KINDS = MACHINE_SCOPED_KINDS
_NO_GPU_GATE: frozenset[str] = NO_GPU_GATE_KINDS


def _param_names(kind: str) -> frozenset[str]:
    from shine.copilot import declarations as decl

    row = decl.write_action(kind)
    return frozenset(row.param_names()) if row is not None else frozenset()


def _validate_params(kind: str, params: dict[str, Any]) -> dict[str, Any]:
    from shine.copilot.params import ParamError, validate_params

    try:
        return validate_params(kind, params)
    except ParamError as exc:
        raise ActionInvalid(str(exc)) from exc


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _occupancy(db) -> dict[str, Any]:
    return resource_occupancy(db)


def _action_occupancy(db, kind: str, params: dict[str, Any]) -> dict[str, Any]:
    if kind == "managed_release":
        from shine.support.occupancy import release_occupancy

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
        {str(row.get("outcome") or row.get("reason") or "REFUSED") for row in refused}
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


def _current_verdict(db, settings) -> dict[str, Any]:
    from shine.api_setup import setup_verdict

    return setup_verdict(settings=settings, db=db)


def _target_capabilities(row: SupportAction) -> list[str]:
    params = row.params or {}
    if row.kind == "setup_repair":
        return list(params.get("capabilities") or ["h3"])
    if row.kind == "setup_reconcile":
        return [str(params.get("capability") or "h3")]
    return ["h3"]


def _capability_evidence(db, settings, row: SupportAction) -> dict[str, Any]:
    from shine.comfyops.setup_summary import summarize_setup

    summary = summarize_setup(db, settings)
    reports = summary.get("capabilities") or {}
    evidence: dict[str, Any] = {}
    for cap in _target_capabilities(row):
        report = reports.get(cap) or {}
        evidence[cap] = {
            "state": report.get("state") or "UNKNOWN",
            "production_ready": bool(report.get("production_ready")),
            "files_verified": bool(report.get("files_verified")),
            "instance_bound": bool(report.get("instance_bound")),
            "missing_count": len(report.get("missing_files") or []),
            "unknown_count": len(report.get("unknown_files") or []),
            "blockers": list(report.get("blockers") or [])[:5],
        }
    return evidence


def _target_satisfied(kind: str, evidence: dict[str, Any]) -> bool | None:
    if not evidence:
        return None
    if kind == "setup_repair":
        return all(str(row.get("state")) in READY_CAPABILITY_STATES for row in evidence.values())
    if kind == "setup_reconcile":
        return all(
            str(row.get("state")) in VERIFIED_CAPABILITY_STATES and bool(row.get("files_verified"))
            for row in evidence.values()
        )
    return None


def _record_verification(db, settings, row: SupportAction) -> dict[str, Any]:
    try:
        verdict = _current_verdict(db, settings)
        mode = str(verdict.get("mode") or "UNKNOWN")
        try:
            evidence = _capability_evidence(db, settings, row)
            satisfied = _target_satisfied(row.kind, evidence)
            unreadable = None
        except Exception as exc:  # noqa: BLE001
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
    from shine.models import AppSetting, AuditEvent

    current_row = db.get(AppSetting, TASK_KEY)
    current = dict(current_row.value) if current_row is not None and isinstance(current_row.value, dict) else {}
    current_id = str(current.get("task_id") or "")
    if not task_id:
        return {}, "MISSING_TASK_ID"
    if not current:
        return {}, "TASK_RECORD_MISSING"
    if current_id != task_id:
        enqueued = db.scalar(
            select(AuditEvent.id).where(
                AuditEvent.event_type == "SETUP_PREPARATION_ENQUEUED",
                AuditEvent.entity_id == task_id,
            ).limit(1)
        )
        return current, "SUPERSEDED" if enqueued else "TASK_RECORD_LOST"
    return current, str(current.get("status") or "UNKNOWN")


def _target_fingerprint(
    db, settings, kind: str, params: dict[str, Any], project_id: str | None
) -> tuple[str | None, dict[str, Any] | None, str | None]:
    """Action-scoped facts that make a preview authorization meaningful."""
    try:
        handler = get_handler(kind)
        if handler is None:
            return None, None, "UNKNOWN_KIND"
        facts: dict[str, Any] = {"kind": kind, "project_id": project_id}
        scoped, reason = handler.target_facts(db, settings, params, project_id)
        facts.update(scoped)
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
    """The single, canonical project an action may touch."""
    from shine.models import Project

    declared = {
        str(value)
        for value in (project_id, params.get("project_id"))
        if isinstance(value, str) and value.strip()
    }
    if len(declared) > 1:
        raise ActionInvalid("会话项目与动作目标项目不一致；已拒绝跨项目操作。")
    canonical = next(iter(declared), None)
    if kind in _MACHINE_SCOPED_KINDS:
        return None
    handler = get_handler(kind)
    if handler is not None and handler.resolve_project is not None:
        canonical = handler.resolve_project(db, params, canonical)
    if kind in _PROJECT_SCOPED_KINDS and not canonical:
        raise ActionInvalid("这个动作必须明确指定目标项目。")
    if canonical and db.get(Project, canonical) is None:
        raise ActionInvalid("目标项目不存在。")
    return canonical


def _validate_scope(db, *, kind: str, params: dict[str, Any], project_id: str | None) -> None:
    """Project scoping is checked before a preview exists, not only at approval."""
    from shine.models import Project

    canonical = project_id
    declared = params.get("project_id")
    if canonical and declared and canonical != declared:
        raise ActionInvalid("会话项目与动作目标项目不一致；已拒绝跨项目操作。")
    target_project = canonical or declared
    if target_project and db.get(Project, str(target_project)) is None:
        raise ActionInvalid("目标项目不存在。")
    handler = get_handler(kind)
    if handler is not None and handler.validate_scope is not None:
        handler.validate_scope(db, params, target_project)


def _preflight(db, settings, kind: str, params: dict[str, Any]) -> dict[str, Any]:
    handler = get_handler(kind)
    if handler is None:
        raise ToolDenied(f"未知动作：{kind}")
    occupancy = _action_occupancy(db, kind, params)
    busy = bool(occupancy["busy"])
    common = {
        "kind": kind,
        "blocked": busy,
        "blocked_code": occupancy["code"] if busy else None,
        "occupancy": occupancy,
        "signals": occupancy.get("signals") or [],
    }
    return handler.preflight(db, settings, params, common)


def _request_identity(kind: str, params: dict[str, Any], actor: str, project_id: str | None) -> str:
    target = {
        "kind": kind,
        "params": params,
        "actor": actor,
        "project_id": project_id,
    }
    return hashlib.sha256(_canonical(target).encode("utf-8")).hexdigest()


def _failure_context(row: SupportAction) -> dict[str, Any]:
    preview = row.preview if isinstance(row.preview, dict) else {}
    return {
        "executor": action_spec(row.kind).executor if row.kind in ACTION_SPECS else None,
        "original_task_id": row.original_task_id,
        "last_error": row.last_error,
        "preview_summary": preview.get("summary"),
        "verification": row.verification,
        "next_step": "保留原任务与日志；根据 verification 中的权威裁决处理后再重新预览。",
    }


def _public(row: SupportAction) -> dict[str, Any]:
    return {
        "id": row.id,
        "session_id": row.session_id,
        "project_id": row.project_id,
        "kind": row.kind,
        "status": row.status,
        "attempt_no": int(row.attempt_no or 1),
        "preview_hash": row.preview_hash,
        "snapshot_revision": row.snapshot_revision,
        "target_fingerprint": row.target_fingerprint,
        "preview": row.preview,
        "original_task_id": row.original_task_id,
        "verification": row.verification,
        "last_error": row.last_error,
        "failure_context": _failure_context(row) if row.last_error or row.status in {"FAILED", "REFUSED", "NEEDS_REVIEW"} else None,
        "actor": row.actor,
        "approved_at": row.approved_at.isoformat() if row.approved_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _is_expired(row: SupportAction) -> bool:
    raw = (row.preview or {}).get("expires_at") if isinstance(row.preview, dict) else None
    if not raw:
        return False
    try:
        from datetime import datetime

        return utcnow() > datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return False


def _approval_abandoned(row: SupportAction) -> bool:
    stamp = row.approved_at or row.updated_at or row.created_at
    if stamp is None:
        return False
    return utcnow() - stamp > APPROVAL_GRACE


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
    canonical = resolve_target_project(db, kind=kind, params=params, project_id=project_id)
    if canonical and "project_id" in _param_names(kind):
        params["project_id"] = canonical
    elif kind not in _PROJECT_SCOPED_KINDS and kind not in _PROJECT_OPTIONAL_KINDS:
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

    snapshot = build_evidence_snapshot(db, settings, page_id="setup", project_id=canonical)
    revision = snapshot_revision(snapshot)
    fingerprint, _, fingerprint_reason = _target_fingerprint(db, settings, kind, params, canonical)
    preview = _preflight(db, settings, kind, params)
    preview["snapshot_revision"] = revision
    preview["fingerprint"] = {
        "available": fingerprint is not None,
        "reason": fingerprint_reason,
    }
    preview["capability_contract"] = contract_registry.contract_binding(kind)
    preview["requires_authorization"] = True
    preview["expires_at"] = (utcnow() + timedelta(minutes=15)).replace(microsecond=0).isoformat()
    digest = hashlib.sha256((identity + "|" + revision + "|" + str(fingerprint)).encode()).hexdigest()
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
        db.rollback()
        existing = db.scalar(select(SupportAction).where(SupportAction.request_identity == identity))
        if existing is None:
            raise
        return _public(existing), True
    return _public(row), False


def _claim_approval(db, row: SupportAction, actor: str) -> bool:
    result = db.execute(
        update(SupportAction)
        .where(
            SupportAction.id == row.id,
            SupportAction.status == "PREVIEWED",
            SupportAction.preview_hash == row.preview_hash,
        )
        .values(status="APPROVED", actor=actor, approved_at=utcnow(), updated_at=utcnow())
    )
    db.commit()
    return result.rowcount == 1


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

    snapshot = build_evidence_snapshot(db, settings, page_id="setup", project_id=canonical)
    if snapshot_revision(snapshot) != row.snapshot_revision:
        row.status = "EXPIRED"
        row.last_error = "PREVIEW_STALE"
        db.commit()
        raise ActionStale("配置在确认前发生变化，请重新确认。")

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
    dispatch = str(stored_contract.get("dispatch") or row.kind)
    from shine.copilot import declarations as decl
    from shine.copilot.params import ParamError

    declaration = decl.write_action(row.kind)
    if declaration is None:
        raise ActionBlocked("CONTRACT_NOT_DECLARED", "当前构建没有这个动作的参数合同。")

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

    handler = get_handler(dispatch)
    if handler is None:
        raise ToolDenied(f"未知动作：{row.kind}")

    try:
        handler.execute(db, settings, session_factory, row, params, declaration, spec, actor, gateway)
        row = db.get(SupportAction, action_id) or row
    except (ActionBlocked, ParamError) as caught:
        exc = (
            caught
            if isinstance(caught, ActionBlocked)
            else ActionBlocked("PARAM_NOT_ROUTABLE", str(caught))
        )
        row = db.get(SupportAction, action_id) or row
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
    except Exception as exc:  # noqa: BLE001
        row = db.get(SupportAction, action_id) or row
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
    """Refresh a delegated action from its bound executor task, never a lookalike."""
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

    handler = get_handler(row.kind)
    if handler is not None and handler.readback is not None:
        handler.readback(db, settings, row)
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
    """Run domain rollback if supported by the action handler."""
    row = db.get(SupportAction, action_id)
    if row is None:
        raise ActionUnknown()
    handler = get_handler(row.kind)
    if handler is None or handler.rollback is None:
        raise ActionBlocked("ROLLBACK_NOT_SUPPORTED", "该授权动作不支持回滚。")
    handler.rollback(db, settings, row, actor)
    return _public(row)


_COUNT_COSTS: dict[str, dict[str, int]] = {
    "workflow_graph_register": {"max_new_workflow_profiles": 1},
    "workflow_user_copy": {"max_new_workflow_profiles": 1},
    "production_start": {"max_test_inference_jobs": 1},
    "image_runtime_validate": {"max_test_inference_jobs": 1},
    "material_generate": {"max_test_inference_jobs": 1},
    "material_regenerate": {"max_test_inference_jobs": 1},
    "asset_center_image_generate": {"max_test_inference_jobs": 1},
    "asset_center_character_sheet_generate": {"max_test_inference_jobs": 1},
    "shot_rerender": {"max_test_inference_jobs": 1},
}


def _grant_cost(kind: str, preview: dict[str, Any]) -> dict[str, int]:
    cost: dict[str, int] = {}
    download_bytes = preview.get("download_bytes")
    if isinstance(download_bytes, int) and download_bytes > 0:
        cost["max_download_bytes"] = download_bytes
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
    """Execute one reviewed capability inside an already authorized task grant."""
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
        grant_contract.assert_identity(loaded.grant, actor=actor, session_id=session_id)
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
        scope_project = str(params.get("project_id") or "") or (
            session.project_id if session is not None else None
        )
        grant_contract.assert_project_scope(loaded.grant, project_id=scope_project, capability_id=capability_id)
        installation_id = (
            expected.get("installation_id")
            or params.get("installation_id")
            or params.get("instance_id")
        )
        grant_contract.assert_installation_scope(loaded.grant, installation_id=installation_id)
        grant_service.assert_tool_call_budget(loaded.grant)
        grant_service.assert_runtime_budget(
            loaded.grant,
            started_at=grant_contract.parse_iso(loaded.grant["created_at"])
            if loaded.grant.get("created_at")
            else None,
        )

        identity = grant_contract.child_key(
            grant_id,
            tool_call_id,
            capability_id,
            dict(params or {}),
            str(expected.get("target_identity") or installation_id or scope_project or ""),
        )
        existing = db.scalar(select(SupportAction).where(SupportAction.grant_tool_key == identity))
        if existing is not None:
            return _grant_receipt(db, settings, existing, receipt, reused=True)

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
            reuse = _reuse_existing_profile(
                db, row, str(reuse_profile_id), grant_id, tool_call_id, identity
            )
            return _grant_receipt(db, settings, row, receipt, reused=True) | {
                "observed_facts": {**reuse, "reused_existing_action": True},
            }
        cost = _grant_cost(capability_id, prepared.get("preview") or {})

        if row.status in {"SUCCEEDED", "DELEGATED", "EXECUTED"}:
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

        grant_service.reserve(db, grant_id, capability_id=capability_id, cost=cost)
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
        actual_cost = dict(cost)
        if capability_id == "resource_install_plan":
            verified_bytes = (row.verification or {}).get("download_bytes")
            if isinstance(verified_bytes, int) and verified_bytes >= 0:
                actual_cost["max_download_bytes"] = verified_bytes
                actual_cost["max_disk_write_bytes"] = verified_bytes
        grant_service.settle(db, grant_id, reserved=cost, actual=actual_cost, step_key=tool_call_id)
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
    db, row: SupportAction, profile_id: str, grant_id: str, tool_call_id: str, identity: str
) -> dict[str, Any]:
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
    if code in {"SCOPE_EXCEEDED", "GRANT_PERMISSION_NOT_GRANTED"}:
        return ["report_blocked", "finish"]
    if code in {"GRANT_BUDGET_EXCEEDED", "GRANT_TOOL_CALL_BUDGET_EXHAUSTED", "GRANT_RUNTIME_BUDGET_EXHAUSTED"}:
        return ["report_blocked", "finish"]
    if code in {"GRANT_EXPIRED", "GRANT_REVOKED", "GRANT_NOT_RUNNING", "GRANT_CANCELLED"}:
        return ["finish"]
    if code in {"GRANT_CONTRACT_CHANGED"}:
        return ["report_blocked", "finish"]
    if code in {"PREVIEW_STALE", "TARGET_FACTS_UNAVAILABLE", "BUSY", "GPU_BUSY"}:
        return ["re_inspect", "finish"]
    return ["re_inspect", "report_blocked", "finish"]


def _bind_grant(db, row: SupportAction, grant_id: str, tool_call_id: str, identity: str) -> None:
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


def _grant_receipt(db, settings, row: SupportAction, base: dict[str, Any], *, reused: bool) -> dict[str, Any]:
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


def read_under_grant(db, settings, *, action_id: str, grant_id: str) -> dict[str, Any]:
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
