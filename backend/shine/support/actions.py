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

from sqlalchemy import func, select, update

from shine.models import AppSetting, AuditEvent, new_id, utcnow
from shine.support.context import build_evidence_snapshot, snapshot_revision
from shine.support.models import SupportAction
from shine.support.occupancy import resource_occupancy
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


_ALLOWED_PARAMS: dict[str, frozenset[str]] = {
    "setup_repair": frozenset(
        {
            "project_id",
            "capabilities",
            "installation_path",
            "port",
            "variant_id",
            "verify_runtime",
            "deep_scan",
        }
    ),
    "setup_reconcile": frozenset(
        {"project_id", "installation_path", "capability", "adopt", "port", "variant_id"}
    ),
    "managed_release": frozenset({"only"}),
    "workflow_user_copy": frozenset(
        {"source_profile_id", "name", "parameter_overrides", "project_id"}
    ),
}


def _validate_params(kind: str, params: dict[str, Any]) -> dict[str, Any]:
    """Reject anything the executor does not accept; never forward free-form keys."""
    if not isinstance(params, dict):
        raise ActionInvalid("动作参数必须是对象。")
    extra = set(params) - _ALLOWED_PARAMS[kind]
    if extra:
        raise ActionInvalid(f"动作不接受的参数：{', '.join(sorted(extra))}")
    clean: dict[str, Any] = {}
    for key, value in params.items():
        if key in {"project_id", "variant_id", "capability", "source_profile_id", "name"}:
            limit = 120
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise ActionInvalid(f"{key} 必须是长度不超过 {limit} 的字符串。")
            clean[key] = value.strip() if key == "name" else value
        elif key == "parameter_overrides":
            if not isinstance(value, dict) or len(value) > 12 or not all(
                isinstance(item_key, str)
                and isinstance(item_value, str)
                and len(item_key) <= 64
                and len(item_value) <= 200
                for item_key, item_value in value.items()
            ):
                raise ActionInvalid("parameter_overrides 必须是最多 12 项的字符串键值表。")
            clean[key] = dict(value)
        elif key == "installation_path":
            if not isinstance(value, str) or len(value) > 1024 or "\x00" in value:
                raise ActionInvalid("installation_path 非法。")
            clean[key] = value
        elif key == "port":
            if value is not None and (not isinstance(value, int) or not 1 <= value <= 65535):
                raise ActionInvalid("port 必须在 1-65535 之间。")
            clean[key] = value
        elif key in {"verify_runtime", "deep_scan", "adopt"}:
            if not isinstance(value, bool):
                raise ActionInvalid(f"{key} 必须是布尔值。")
            clean[key] = value
        elif key == "capabilities":
            if not isinstance(value, list) or not 1 <= len(value) <= 5:
                raise ActionInvalid("capabilities 必须是 1-5 项列表。")
            from shine.comfyops.setup_summary import CAPABILITIES

            unknown = [item for item in value if item not in CAPABILITIES]
            if unknown or len(set(value)) != len(value):
                raise ActionInvalid("capabilities 含未知或重复项。")
            clean[key] = list(value)
        elif key == "only":
            if not isinstance(value, list) or len(value) > 10 or not all(
                isinstance(item, str) and len(item) <= 80 for item in value
            ):
                raise ActionInvalid("only 必须是最多 10 个服务 id 的字符串列表。")
            clean[key] = list(value)
    return clean


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _occupancy(db) -> dict[str, Any]:
    """The one composed occupancy answer (existing ledgers, fail-closed)."""
    return resource_occupancy(db)


def _validate_scope(db, *, kind: str, params: dict[str, Any], project_id: str | None) -> None:
    """Project scoping is checked before a preview exists, not only at approval."""
    from shine.models import Project, WorkflowProfile

    target_project = project_id or params.get("project_id")
    if target_project and db.get(Project, str(target_project)) is None:
        raise ActionInvalid("目标项目不存在。")
    if kind == "workflow_user_copy":
        source = db.get(WorkflowProfile, str(params.get("source_profile_id") or ""))
        if source is None:
            raise ActionInvalid("找不到要复制的工作流合同。")
        if source.project_id is not None and target_project and source.project_id != target_project:
            raise ActionInvalid("不能把其它项目的私有工作流复制到当前项目。")
        if source.project_id is None and target_project is None:
            # A built-in template may become a machine-level copy; that copy
            # still belongs to no project and is never a shared template.
            pass


def _preflight(db, settings, kind: str, params: dict[str, Any]) -> dict[str, Any]:
    occupancy = _occupancy(db)
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
                    "ownership_evidence": row.get("ownership_evidence"),
                }
                for row in processes[:10]
            ],
            "will_start_gpu": False,
            "will_modify_user_comfyui": False,
            "authorization_summary": "不会强停外部 ComfyUI；忙碌或无法证明归属的服务保持不变。",
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


def _public(row: SupportAction) -> dict[str, Any]:
    return {
        "id": row.id,
        "session_id": row.session_id,
        "kind": row.kind,
        "label": ACTION_SPECS.get(row.kind).label if row.kind in ACTION_SPECS else row.kind,
        "status": row.status,
        "attempt_no": row.attempt_no,
        "preview": row.preview,
        "preview_hash": row.preview_hash,
        "snapshot_revision": row.snapshot_revision,
        "approved_at": row.approved_at.isoformat() if row.approved_at else None,
        "original_task_id": row.original_task_id,
        "verification": row.verification,
        "last_error": row.last_error,
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


def preview_action(
    db,
    settings,
    *,
    kind: str,
    params: dict[str, Any],
    actor: str = "local-user",
    session_id: str | None = None,
    project_id: str | None = None,
) -> tuple[dict[str, Any], bool]:
    spec = action_spec(kind)
    params = _validate_params(kind, params)
    if session_id:
        from shine.support.models import SupportSession

        session = db.get(SupportSession, session_id)
        if session is None:
            raise ActionInvalid("授权动作关联的会话不存在。")
        if session.project_id and project_id and session.project_id != project_id:
            raise ActionInvalid("授权动作与当前会话的项目不一致。")
        project_id = project_id or session.project_id
    _validate_scope(db, kind=kind, params=params, project_id=project_id)
    request_key = _request_identity(kind, params, actor, project_id)

    latest = db.scalar(
        select(SupportAction)
        .where(SupportAction.request_key == request_key)
        .order_by(SupportAction.attempt_no.desc())
    )
    if latest is not None and latest.status in REUSABLE_STATUSES:
        if latest.status == "PREVIEWED" and _is_expired(latest):
            latest.status = "EXPIRED"
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
        db, settings, page_id="setup", project_id=project_id or params.get("project_id")
    )
    revision = snapshot_revision(snapshot)
    preview = _preflight(db, settings, kind, params)
    preview["snapshot_revision"] = revision
    preview["requires_authorization"] = True
    preview["expires_at"] = (utcnow() + timedelta(minutes=15)).replace(microsecond=0).isoformat()
    digest = hashlib.sha256((identity + "|" + revision).encode()).hexdigest()
    row = SupportAction(
        id=new_id(),
        session_id=session_id,
        project_id=project_id or params.get("project_id"),
        kind=kind,
        status="PREVIEWED",
        request_key=request_key,
        attempt_no=attempt_no,
        request_identity=identity,
        preview_hash=digest,
        snapshot_revision=revision,
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

    # Facts are re-read before execution: an environment change between preview
    # and approval invalidates the old authorization instead of applying it.
    snapshot = build_evidence_snapshot(
        db, settings, page_id="setup", project_id=row.project_id
    )
    if snapshot_revision(snapshot) != row.snapshot_revision:
        row.status = "EXPIRED"
        row.last_error = "PREVIEW_STALE"
        db.commit()
        raise ActionStale("预览之后本机事实已变化，请重新预览并授权。")

    if not _claim_approval(db, row, actor):
        db.refresh(row)
        return _public(row), True

    db.refresh(row)
    params = dict(row.params or {})
    spec = action_spec(row.kind)

    # The second occupancy read closes the preview→approval race: a render can
    # start between the two requests.
    occupancy = _occupancy(db)
    if occupancy["busy"]:
        row.status = "REFUSED"
        row.last_error = str(occupancy["code"] or "BUSY")
        db.commit()
        raise ActionBlocked(
            row.last_error,
            "本机正在生产或 GPU 使用权未释放，已拒绝执行配置动作；请等任务结束并释放显存后再试。",
            details={"occupancy": occupancy},
        )

    from shine.services import add_audit

    try:
        if row.kind == "setup_repair":
            from shine.comfyops.setup_preparation import TASK_KEY, enqueue

            value = enqueue(
                db,
                project_id=params.get("project_id"),
                capabilities=list(params.get("capabilities") or ["h3"]),
                installation_path=params.get("installation_path"),
                port=params.get("port"),
                variant_id=params.get("variant_id"),
                verify_runtime=bool(params.get("verify_runtime", True)),
                actor=actor,
                settings=settings,
                deep_scan=bool(params.get("deep_scan", False)),
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
        elif row.kind == "setup_reconcile":
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
            value = reconcile_installation(
                db,
                settings,
                path=str(Path(str(target))),
                project_id=params.get("project_id"),
                capability=str(params.get("capability") or "h3"),
                adopt=bool(params.get("adopt")),
                port=params.get("port"),
                actor=actor,
                variant_id=params.get("variant_id"),
            )
            row.status = "DELEGATED" if value.get("status") == "IN_PROGRESS" else "EXECUTED"
            row.verification = {
                "outcome": "PENDING",
                "executor": spec.executor,
                "result_status": value.get("status"),
                "verified": value.get("verified"),
                "registered": value.get("registered"),
                "target": str(target),
                "checked_at": utcnow().isoformat(),
            }
            _record_verification(db, settings, row)
        elif row.kind == "workflow_user_copy":
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
            body = WorkflowProfileCreate(
                name=str(params.get("name")),
                mode=GenerationMode(source.mode),
                graph=copy.deepcopy(source.graph),
                parameter_map=parameter_map,
                media_transfers=copy.deepcopy(source.media_transfers),
                required_nodes=list(source.required_nodes or []),
                checkpoint_id=source.checkpoint_id,
                project_id=target_project,
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
        elif row.kind == "managed_release":
            from shine.comfyops.renderer_release import release_managed_processes_guarded

            value = release_managed_processes_guarded(
                session_factory,
                actor=actor,
                reason="AI 管家受控释放（用户已授权）",
                only=set(params.get("only") or []) or None,
            )
            row.status = "EXECUTED"
            row.verification = {
                "outcome": "RELEASED" if not value.get("failed") else "PARTIAL",
                "executor": spec.executor,
                "released": value.get("released"),
                "skipped": value.get("skipped"),
                "failed": value.get("failed"),
                "checked_at": utcnow().isoformat(),
            }
        else:  # pragma: no cover - the registry above is exhaustive
            raise ToolDenied(f"未知动作：{row.kind}")
    except ActionBlocked:
        raise
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
        verification = _record_verification(db, settings, row)
        row.verification = {**(row.verification or {}), **verification}
        if row.status == "DELEGATED":
            if verification.get("outcome") == "VERIFIED":
                row.status = "SUCCEEDED"
            elif verification.get("outcome") == "UNKNOWN":
                row.status = "NEEDS_REVIEW"
            db.commit()
        return _public(row)

    if row.kind == "managed_release":
        from shine.comfyops.renderer_release import describe_managed_processes

        remaining = [row_ for row_ in describe_managed_processes(db) if row_.get("in_flight")]
        occupancy = _occupancy(db)
        verification = {
            "outcome": "RELEASED" if not remaining else "PARTIAL",
            "authority": "/api/v1/setup/managed-processes",
            "occupancy": {"busy": occupancy["busy"], "code": occupancy["code"]},
            "checked_at": utcnow().isoformat(),
        }
        row.verification = {**(row.verification or {}), **verification}
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
