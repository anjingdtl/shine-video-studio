"""Preview → explicit authorization → existing executor → re-read authority.

The copilot never executes a change itself.  Each action is a thin, idempotent
authorization envelope around an executor that already exists (the durable
preparation worker, the reconcile verifier or the guarded renderer release).
Readiness after the run is taken from ``/api/v1/setup/verdict`` again; the agent
is not allowed to declare success.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from shine.models import AppSetting, new_id, utcnow
from shine.support.context import build_evidence_snapshot, snapshot_revision
from shine.support.models import SupportAction
from shine.support.privacy import private_roots_for, redact_payload
from shine.support.tools import ACTION_SPECS, ToolDenied, action_spec

SETUP_TERMINAL = {"COMPLETED", "FAILED", "NEEDS_REVIEW", "CANCELLED", "SUPERSEDED"}


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
            limit = 120 if key == "name" else 120
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


def _active_production(db) -> dict[str, int]:
    from sqlalchemy import func

    from shine.models import Job, ProductionSeries

    jobs = db.scalar(
        select(func.count(Job.id)).where(
            Job.status.in_(("SUBMITTING", "RUNNING", "QC_PENDING")), Job.provider == "comfyui"
        )
    )
    series = db.scalar(
        select(func.count(ProductionSeries.id)).where(
            ProductionSeries.status.in_(("QUEUED", "RUNNING"))
        )
    )
    return {"jobs": int(jobs or 0), "series": int(series or 0)}


def _preflight(db, settings, kind: str, params: dict[str, Any]) -> dict[str, Any]:
    active = _active_production(db)
    busy = active["jobs"] + active["series"] > 0
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
            "kind": kind,
            "blocked": busy,
            "blocked_code": "PRODUCTION_ACTIVE" if busy else None,
            "active_production": active,
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
            "kind": kind,
            "blocked": busy,
            "blocked_code": "PRODUCTION_ACTIVE" if busy else None,
            "active_production": active,
            "summary": "只读取并核验本机已有文件，按固定清单登记通过校验的文件。",
            "missing_components": [],
            "download_bytes": 0,
            "capability": params.get("capability", "h3"),
            "adopt": bool(params.get("adopt")),
            "installation": {
                "instance_id": selected.get("instance_id"),
                "managed": selected.get("managed"),
                "status": selected.get("status"),
                "path_known": bool(selected.get("root") or params.get("path")),
            },
            "will_start_gpu": False,
            "will_modify_user_comfyui": False,
            "authorization_summary": "核验不会移动或替换你的文件；adopt=true 才会把这个实例登记为工作台使用。",
        }
    if kind == "managed_release":
        from shine.comfyops.renderer_release import describe_managed_processes

        processes = describe_managed_processes(db)
        return {
            "kind": kind,
            "blocked": busy,
            "blocked_code": "PRODUCTION_ACTIVE" if busy else None,
            "active_production": active,
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
            "kind": kind,
            "blocked": busy,
            "blocked_code": "PRODUCTION_ACTIVE" if busy else None,
            "active_production": active,
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


def _request_identity(kind: str, params: dict[str, Any], actor: str) -> str:
    return hashlib.sha256(_canonical({"kind": kind, "params": params, "actor": actor}).encode()).hexdigest()


def _public(row: SupportAction) -> dict[str, Any]:
    return {
        "id": row.id,
        "session_id": row.session_id,
        "kind": row.kind,
        "label": ACTION_SPECS.get(row.kind).label if row.kind in ACTION_SPECS else row.kind,
        "status": row.status,
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
    identity = _request_identity(kind, params, actor)
    existing = db.scalar(select(SupportAction).where(SupportAction.request_identity == identity))
    if existing is not None:
        return _public(existing), True

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
        request_identity=identity,
        preview_hash=digest,
        snapshot_revision=revision,
        params=redact_payload(params, private_roots=private_roots_for(settings)),
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


def _record_verification(db, settings, row: SupportAction) -> dict[str, Any]:
    verdict = _current_verdict(db, settings)
    mode = str(verdict.get("mode") or "UNKNOWN")
    outcome = "VERIFIED" if mode in {"READY", "RUNNING"} else "NOT_VERIFIED"
    verification = {
        "outcome": outcome,
        "authority": "/api/v1/setup/verdict",
        "mode": mode,
        "label": verdict.get("label"),
        "code": verdict.get("code"),
        "checked_at": utcnow().isoformat(),
    }
    row.verification = verification
    return verification


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
    expires_raw = (row.preview or {}).get("expires_at")
    if expires_raw:
        try:
            if datetime.fromisoformat(str(expires_raw)) < utcnow():
                row.status = "EXPIRED"
                db.commit()
                raise ActionStale("预览已过期，请重新预览并授权。")
        except ValueError:
            pass
    if bool(row.preview.get("blocked")):
        row.status = "REFUSED"
        row.last_error = str(row.preview.get("blocked_code") or "BLOCKED")
        db.commit()
        raise ActionBlocked(
            row.last_error or "BLOCKED",
            "当前有正在进行的生产任务或 GPU 使用权未释放，已拒绝执行。",
            details={"action": _public(row)},
        )

    spec = action_spec(row.kind)
    params = dict(row.params or {})
    # Re-check production right before delegation: a render can start between
    # preview and approval.
    active = _active_production(db)
    if active["jobs"] + active["series"] > 0:
        row.status = "REFUSED"
        row.last_error = "PRODUCTION_ACTIVE"
        db.commit()
        raise ActionBlocked(
            "PRODUCTION_ACTIVE",
            "生产任务已经开始，已拒绝执行配置动作；请等任务结束并释放 GPU 后再试。",
            details={"active_production": active},
        )

    from shine.services import add_audit

    row.status = "APPROVED"
    row.approved_at = utcnow()
    row.actor = actor
    try:
        if row.kind == "setup_repair":
            from shine.comfyops.setup_preparation import enqueue

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
            row.original_task_id = value.get("task_id")
            row.verification = {
                "outcome": "PENDING",
                "executor": spec.executor,
                "task_id": row.original_task_id,
                "task_status": value.get("status"),
                "checked_at": utcnow().isoformat(),
            }
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
                "outcome": "PENDING" if value.get("status") == "IN_PROGRESS" else "AWAITING_VERDICT",
                "executor": spec.executor,
                "result_status": value.get("status"),
                "verified": value.get("verified"),
                "registered": value.get("registered"),
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
            if target_project and source.project_id not in {None, target_project}:
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
    """Refresh a delegated action from the original executor's own ledger."""
    if row.status not in {"DELEGATED", "EXECUTED", "APPROVED"}:
        return _public(row)
    if row.kind == "setup_repair" and row.status == "DELEGATED":
        from shine.comfyops.setup_preparation import TASK_KEY

        existing = db.get(AppSetting, TASK_KEY)
        state = dict(existing.value) if existing is not None and isinstance(existing.value, dict) else {}
        task_status = str(state.get("status") or "UNKNOWN")
        row.verification = {
            "outcome": "PENDING" if task_status not in SETUP_TERMINAL else "EXECUTOR_FINISHED",
            "executor": action_spec(row.kind).executor,
            "task_id": state.get("task_id") or row.original_task_id,
            "task_status": task_status,
            "current_step": state.get("current"),
            "message": state.get("message"),
            "checked_at": utcnow().isoformat(),
        }
        if task_status in SETUP_TERMINAL:
            _record_verification(db, settings, row)
            row.status = "SUCCEEDED" if row.verification.get("outcome") == "VERIFIED" else "FAILED"
            if row.status == "FAILED":
                row.last_error = "原执行器已结束，但权威裁决仍未就绪"
        db.commit()
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
    from sqlalchemy import func

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
