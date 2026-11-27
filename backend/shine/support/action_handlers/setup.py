"""Domain 1: Setup, resource, managed-release, web-search and image-runtime handlers."""
from __future__ import annotations

import inspect
from typing import Any

from fastapi import HTTPException

from shine.copilot.params import ParamError, routed_kwargs
from shine.models import AppSetting, utcnow
from shine.support.action_handlers import (
    ActionBlocked,
    ActionHandler,
    blocked_from_executor,
    register_handler,
)
from shine.support.occupancy import occupancy_facts

SETUP_TERMINAL = {
    "COMPLETED",
    "DONE",
    "DONE_WITH_WARNINGS",
    "FAILED",
    "NEEDS_REVIEW",
    "CANCELLED",
    "SUPERSEDED",
}
EXECUTOR_FAILED = {"FAILED", "CANCELLED", "SUPERSEDED"}
READY_CAPABILITY_STATES = {"READY", "VERIFIED_RENDER"}
VERIFIED_CAPABILITY_STATES = {"VERIFIED", "BOUND", "READY", "VERIFIED_RENDER"}


def receipt_facts(db) -> list[dict[str, Any]]:
    from sqlalchemy import select

    rows = db.scalars(select(AppSetting).where(AppSetting.key.like("verify-receipt:%"))).all()
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
        facts.append({
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
        })
    return sorted(facts, key=lambda item: item["key"])


def installation_facts(selected: dict[str, Any]) -> dict[str, Any]:
    return {
        "instance_id": selected.get("instance_id"),
        "root": selected.get("root"),
        "source": selected.get("source"),
        "managed": bool(selected.get("managed")),
        "status": selected.get("status"),
        "exists": selected.get("exists"),
        "port": selected.get("port"),
    }


def setup_target_facts(
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
        "installation": installation_facts(selected),
        "variant_id": params.get("variant_id"),
        "occupancy": occupancy_facts(db),
        "receipts": receipt_facts(db),
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
        gap_sig = inspect.signature(runtime_closure_gap).parameters
        gap = (
            runtime_closure_gap(db, settings, selected, capabilities=list(targets))
            if "capabilities" in gap_sig
            else runtime_closure_gap(db, settings, selected)
        )
        facts["runtime_closure"] = sorted(gap.get("missing_components") or [])
    return facts, None


def current_verdict(db, settings) -> dict[str, Any]:
    from shine.comfyops.setup_summary import setup_verdict

    return setup_verdict(db, settings)


def target_capabilities(row) -> list[str]:
    params = row.params or {}
    if row.kind == "setup_reconcile":
        return [str(params.get("capability") or "h3")]
    return [str(item) for item in (params.get("capabilities") or ["h3"])]


def capability_evidence(db, settings, row) -> dict[str, Any]:
    from shine.comfyops.setup_summary import summarize_setup

    summary = summarize_setup(db, settings)
    reports = summary.get("capabilities") or {}
    evidence: dict[str, Any] = {}
    for cap in target_capabilities(row):
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


def target_satisfied(kind: str, evidence: dict[str, Any]) -> bool | None:
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


def record_verification(db, settings, row) -> dict[str, Any]:
    try:
        verdict = current_verdict(db, settings)
        mode = str(verdict.get("mode") or "UNKNOWN")
        try:
            evidence = capability_evidence(db, settings, row)
            satisfied = target_satisfied(row.kind, evidence)
            unreadable = None
        except Exception as exc:  # noqa: BLE001
            evidence = {}
            satisfied = None
            unreadable = type(exc).__name__
        outcome = "VERIFIED" if satisfied is True else ("NOT_VERIFIED" if satisfied is False else "UNKNOWN")
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


def setup_task_state(db, task_id: str | None) -> tuple[dict[str, Any], str]:
    import shine.support.actions as actions_mod

    return actions_mod._setup_task_state(db, task_id)


def release_verification(value: dict[str, Any], only: set[str] | None) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    outcome = str(raw.get("outcome") or "UNKNOWN")
    if outcome not in {"RELEASED", "PARTIAL", "REFUSED", "UNKNOWN"}:
        outcome = "UNKNOWN"
    stopped = list(raw.get("stopped") or [])
    refused = list(raw.get("refused") or [])
    remaining = list(raw.get("remaining") or [])
    target_names = sorted(only) if only is not None else sorted(
        {str(item.get("id")) for item in stopped + refused + remaining if isinstance(item, dict) and item.get("id")}
    )
    return {
        "outcome": outcome,
        "attributed": True,
        "authority": "/api/v1/setup/managed-processes",
        "only": target_names,
        "reason": raw.get("reason") or raw.get("error_code"),
        "stopped": stopped,
        "refused": refused,
        "remaining": remaining,
        "checked_at": utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# setup_repair & setup_reconcile
# ---------------------------------------------------------------------------


def _setup_preflight(db, settings, params: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    from shine.api_setup import PreparePlanRequest, _build_prepare_plan

    kind = str(common["kind"])
    is_reconcile = kind == "setup_reconcile"
    capabilities = (
        [str(params.get("capability") or "h3")]
        if is_reconcile
        else list(params.get("capabilities") or ["h3"])
    )
    raw_variant = params.get("variant_id")
    plan_body = PreparePlanRequest(
        project_id=params.get("project_id"),
        capabilities=capabilities,
        installation_path=params.get("installation_path"),
        port=params.get("port"),
        variant_id=str(raw_variant)[:80] if raw_variant else None,
        verify_runtime=False if is_reconcile else bool(params.get("verify_runtime", True)),
        deep_scan=False if is_reconcile else bool(params.get("deep_scan", False)),
    )
    plan = _build_prepare_plan(plan_body, settings, db, discovered=[])
    selected = (plan.get("installation") or {}).get("selected") or {}
    reports = plan.get("capabilities") or {}
    plans = plan.get("plans") or []
    unknown_files = sum(int(row.get("unknown_files") or 0) for row in plans)
    unknown_bytes = sum(int(row.get("unknown_bytes") or 0) for row in plans)
    scan_incomplete = any(bool(row.get("scan_incomplete")) for row in plans)
    if not is_reconcile:
        missing_components = sorted(
            {str(cid) for row in plans for cid in (row.get("components") or [])}
        )
        return {
            **common,
            "summary": "补齐固定清单中的缺失文件，并复用统一安装器的持久任务。",
            "download_bytes": int(plan.get("download_bytes") or 0),
            "unknown_bytes": unknown_bytes,
            "unknown_files": unknown_files,
            "scan_incomplete": scan_incomplete,
            "missing_components": missing_components,
            "capabilities": {
                key: {
                    "label": row.get("label"),
                    "state": row.get("state"),
                    "missing_count": len(row.get("missing_files") or []),
                    "unknown_count": len(row.get("unknown_files") or []),
                }
                for key, row in reports.items()
            },
            "installation": {
                "instance_id": selected.get("instance_id"),
                "managed": selected.get("managed"),
                "status": selected.get("status"),
                "path": selected.get("root") or params.get("installation_path"),
            },
            "will_start_gpu": bool(plan.get("will_start_gpu")),
            "will_modify_user_comfyui": False,
            "authorization_summary": "只下载缺失的固定文件、登记已核验文件并生成运行配置；"
            "不会删除模型、修改节点源码或终止别人的 ComfyUI。",
        }
    capability = capabilities[0]
    cap_report = reports.get(capability) or {}
    return {
        **common,
        "summary": "只读取并核验本机已有文件，并交给统一整备任务按固定清单登记通过校验的文件。",
        "missing_components": [],
        "download_bytes": 0,
        "unknown_bytes": unknown_bytes,
        "unknown_files": unknown_files,
        "scan_incomplete": scan_incomplete,
        "capability": capability,
        "capability_state": cap_report.get("state"),
        "adopt": bool(params.get("adopt")),
        "installation": {
            "instance_id": selected.get("instance_id"),
            "managed": selected.get("managed"),
            "status": selected.get("status"),
            "path_known": bool(selected.get("root") or params.get("installation_path")),
        },
        "will_start_gpu": False,
        "will_modify_user_comfyui": False,
        "authorization_summary": "核验由持久整备任务完成，不会移动或替换你的文件；"
        "adopt=true 才会把这个实例登记为工作台使用。",
    }


def _setup_execute(db, settings, session_factory, row, params, declaration, spec, actor, gateway) -> None:
    from shine.comfyops.setup_preparation import TASK_KEY, enqueue
    from shine.services import add_audit

    dispatch = str(declaration.dispatch or row.kind)
    if dispatch == "setup_reconcile":
        from shine.comfyops.setup_summary import resolve_installation, resolve_setup_port

        resolved = resolve_installation(db, settings, path=params.get("installation_path"), discovered=[])
        selected = resolved.get("selected") or {}
        target = params.get("installation_path") or selected.get("root")
        if not target:
            raise ActionBlocked("INSTALLATION_UNKNOWN", "还没有可核验的 ComfyUI 路径，请先在基础配置中选择或整备环境。")
        if resolved.get("choice_required") and not params.get("installation_path"):
            raise ActionBlocked("INSTALLATION_SELECTION_REQUIRED", "检索发现多套 ComfyUI，请先明确指定安装路径。")
        adopt = bool(params.get("adopt"))
        port = resolve_setup_port(db, settings, selected, params.get("port")) if adopt else (params.get("port") or selected.get("port"))
        sig = inspect.signature(enqueue)
        accepts_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
        call_kwargs: dict[str, Any] = {
            "project_id": params.get("project_id"),
            "capabilities": [str(params.get("capability") or "h3")],
            "installation_path": str(target),
            "port": port,
            "variant_id": params.get("variant_id"),
            "verify_runtime": False,
            "deep_scan": False,
        }
        if accepts_var_kw or "adopt" in sig.parameters:
            call_kwargs["adopt"] = adopt
        if accepts_var_kw or "reconcile_only" in sig.parameters:
            call_kwargs["reconcile_only"] = True
        value = enqueue(db, actor=actor, settings=settings, **call_kwargs)
    else:
        from shine.api_setup import PrepareRequest, setup_prepare_execute

        value = setup_prepare_execute(PrepareRequest.model_validate(params), actor, settings, db)
    row.status = "DELEGATED"
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
        add_audit(
            db, actor, "SUPPORT_ACTION_TASK_BOUND", "support_action", row.id,
            {"kind": row.kind, "task_id": row.original_task_id, "setup_task_key": TASK_KEY},
            project_id=row.project_id,
        )


def _setup_readback(db, settings, row) -> None:
    import shine.support.actions as actions_mod

    if row.kind == "setup_repair" or (row.kind == "setup_reconcile" and row.original_task_id):
        if row.status != "DELEGATED":
            return
        state, executor_status = actions_mod._setup_task_state(db, row.original_task_id)
        if executor_status in {"MISSING_TASK_ID", "TASK_RECORD_MISSING", "TASK_RECORD_LOST"}:
            row.verification = {
                "outcome": "UNKNOWN",
                "executor": row.verification.get("executor") if isinstance(row.verification, dict) else None,
                "original_task_id": row.original_task_id,
                "executor_status": executor_status,
                "authority": "/api/v1/setup/verdict",
                "checked_at": utcnow().isoformat(),
            }
            row.status = "NEEDS_REVIEW"
            row.last_error = "原执行任务记录缺失，无法确认这次恢复的结果。"
            db.commit()
            return
        if executor_status == "SUPERSEDED":
            row.verification = {
                "outcome": "UNKNOWN",
                "executor": row.verification.get("executor") if isinstance(row.verification, dict) else None,
                "original_task_id": row.original_task_id,
                "executor_status": "SUPERSEDED",
                "current_task_id": state.get("task_id"),
                "authority": "/api/v1/setup/verdict",
                "checked_at": utcnow().isoformat(),
            }
            row.status = "FAILED"
            row.last_error = "原执行任务已被新的整备请求取代，不能借用新任务结果。"
            db.commit()
            return
        if executor_status not in SETUP_TERMINAL:
            row.verification = {
                "outcome": "PENDING",
                "executor": row.verification.get("executor") if isinstance(row.verification, dict) else None,
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
            return
        verification = actions_mod._record_verification(db, settings, row)
        if row.kind == "setup_reconcile":
            params = row.params or {}
            cap = str(params.get("capability") or "h3")
            target_facts = (verification.get("targets") or {}).get(cap) or {}
            layers = {
                "files_verified": bool(target_facts.get("files_verified")),
                "instance_bound": bool(target_facts.get("instance_bound")),
                "production_ready": bool(target_facts.get("production_ready")),
            }
            verification = {**verification, "layers": layers}
            if params.get("adopt") and not layers["instance_bound"]:
                verification["outcome"] = "NOT_VERIFIED"
                verification["target_satisfied"] = False
        row.verification = {**(row.verification or {}), **verification, "task_id": row.original_task_id, "executor_status": executor_status}
        if executor_status in EXECUTOR_FAILED:
            row.status = "FAILED"
            row.last_error = f"原执行器状态 {executor_status}，本次恢复未成功。"
        elif row.verification.get("outcome") == "VERIFIED":
            row.status = "SUCCEEDED"
        elif executor_status in {"COMPLETED", "DONE", "DONE_WITH_WARNINGS"}:
            row.status = "NEEDS_REVIEW"
            row.last_error = "原执行器已完成，但本次恢复目标的权威能力裁决仍未就绪。"
        else:
            row.status = "NEEDS_REVIEW"
            row.last_error = f"原执行器状态 {executor_status}，需要人工核对。"
        db.commit()
        return

    if row.kind == "setup_reconcile":
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
            outcome = str((row.verification or {}).get("outcome") or "")
            if outcome == "VERIFIED":
                row.status = "SUCCEEDED"
            else:
                row.status = "NEEDS_REVIEW"
                row.last_error = row.last_error or "核验结果没有可归属的最终结论，请重新预览。"
            db.commit()


# ---------------------------------------------------------------------------
# resource_install_plan
# ---------------------------------------------------------------------------


def _resource_plan_target_facts(db, settings, params: dict[str, Any], project_id: str | None) -> tuple[dict[str, Any], str | None]:
    from shine.comfyops.resource_resolver import resolve_resources
    from shine.strategy import GenerationStrategy

    strategy_raw = params.get("strategy")
    if not isinstance(strategy_raw, dict) or not strategy_raw:
        return {}, "RESOURCE_STRATEGY_MISSING"
    try:
        strategy = GenerationStrategy.model_validate(strategy_raw)
    except Exception:  # noqa: BLE001
        return {}, "RESOURCE_STRATEGY_INVALID"
    preview = resolve_resources(db, settings, strategy)
    components = sorted(
        (
            {"component_id": item.get("component_id"), "kind": item.get("kind"), "bytes": item.get("bytes"), "present": item.get("present")}
            for item in preview.get("components") or []
        ),
        key=lambda item: str(item.get("component_id")),
    )
    return {
        "catalog_sha256": preview.get("catalog_sha256"),
        "manifest_sha256": preview.get("manifest_sha256"),
        "recipe_sha256": preview.get("compiled_recipe_sha256"),
        "snapshot_sha256": preview.get("snapshot_sha256"),
        "download_eligible": bool(preview.get("download_eligible")),
        "strategy_sha256": strategy.strategy_sha256(),
        "components": components,
        "requested": {"instance_id": params.get("instance_id"), "source_preference": params.get("source_preference") or "AUTO"},
    }, None


def _resource_plan_preflight(db, settings, params: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    from shine.comfyops.resource_resolver import resolve_resources
    from shine.strategy import GenerationStrategy
    from shine.support.action_handlers import ActionInvalid

    raw = params.get("strategy")
    if not isinstance(raw, dict) or not raw:
        raise ActionInvalid("缺少可解析的策略合同。")
    try:
        strategy = GenerationStrategy.model_validate(raw)
    except Exception as exc:  # noqa: BLE001
        raise ActionInvalid(f"策略合同不符合既有定义：{str(exc)[:160]}") from exc
    preview = resolve_resources(db, settings, strategy)
    components = [
        {"component_id": item.get("component_id"), "kind": item.get("kind"), "bytes": item.get("bytes"), "present": bool(item.get("present"))}
        for item in preview.get("components") or []
    ]
    missing = [item for item in components if not item["present"]]
    return {
        **common,
        "summary": "把当前缺失资源冻结成一份待批准安装计划；这一步不下载任何文件。",
        "missing_components": [item["component_id"] for item in missing],
        "download_bytes": int(preview.get("download_bytes") or 0),
        "components": components,
        "snapshot_sha256": preview.get("snapshot_sha256"),
        "download_eligible": bool(preview.get("download_eligible")),
        "will_start_gpu": False,
        "will_modify_user_comfyui": False,
        "authorization_summary": "只冻结计划并登记组件；下载与安装仍由原安装计划、原 Installer 与 Setup Worker 执行，不会写入节点源码。",
    }


def _resource_plan_execute(db, settings, session_factory, row, params, declaration, spec, actor, gateway) -> None:
    from shine.api_setup import ResourcePlanRequest, create_resource_install_plan
    from shine.copilot.params import model_payload

    body = ResourcePlanRequest.model_validate(model_payload(declaration, params))
    value = create_resource_install_plan(body, settings, db)
    row.status = "EXECUTED"
    row.verification = {
        "outcome": "PLAN_FROZEN",
        "executor": spec.executor,
        "authority": "GET /api/v1/setup/verdict",
        "installation_plan_id": value.get("plan_id"),
        "plan_sha256": value.get("sha256"),
        "plan_status": value.get("status"),
        "download_bytes": value.get("download_bytes"),
        "disk_required": value.get("disk_required"),
        "production_ready": False,
        "checked_at": utcnow().isoformat(),
    }


def _resource_plan_readback(db, settings, row) -> None:
    if row.status != "EXECUTED":
        return
    from shine.comfyops.models import InstallationPlan

    plan_id = (row.verification or {}).get("installation_plan_id")
    plan = db.get(InstallationPlan, str(plan_id or "")) if plan_id else None
    if plan is None:
        row.status = "NEEDS_REVIEW"
        row.last_error = "安装计划记录已不存在，无法确认这一步的结果。"
        row.verification = {**(row.verification or {}), "outcome": "UNKNOWN", "installation_plan_id": plan_id, "checked_at": utcnow().isoformat()}
        db.commit()
        return
    row.verification = {
        **(row.verification or {}),
        "plan_status": plan.status,
        "plan_sha256": plan.sha256,
        "original_authority": "installation_plans",
        "production_ready": False,
        "checked_at": utcnow().isoformat(),
    }
    db.commit()


# ---------------------------------------------------------------------------
# managed_release
# ---------------------------------------------------------------------------


def _release_target_facts(db, settings, params: dict[str, Any], project_id: str | None) -> tuple[dict[str, Any], str | None]:
    from shine.comfyops.renderer_release import _record_key, describe_managed_processes

    only = set(params.get("only") or []) or None
    targets = []
    for row in describe_managed_processes(db):
        if not isinstance(row, dict) or (only is not None and _record_key(row) not in only):
            continue
        targets.append({
            "key": _record_key(row),
            "kind": row.get("kind"),
            "state": row.get("state"),
            "in_flight": bool(row.get("in_flight")),
            "lease_id": row.get("lease_id"),
            "lease_status": row.get("lease_status"),
            "job_id": row.get("job_id"),
            "job_status": row.get("job_status"),
        })
    targets.sort(key=lambda item: str(item["key"]))
    return {"targets": targets, "occupancy": occupancy_facts(db)}, None


def _release_preflight(db, settings, params: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    from shine.comfyops.renderer_release import describe_managed_processes

    processes = describe_managed_processes(db)
    occupancy = common["occupancy"]
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
        "idle_leases": occupancy.get("idle_leases") or [],
        "will_start_gpu": False,
        "will_modify_user_comfyui": False,
        "authorization_summary": "不会强停外部 ComfyUI；提交中、生成中、审片中、结果待核对或无法证明归属的服务保持不变，底层租约释放合同原样复用。",
    }


def _release_execute(db, settings, session_factory, row, params, declaration, spec, actor, gateway) -> None:
    import shine.support.actions as actions_mod
    from shine.comfyops.renderer_release import release_managed_processes_guarded
    from shine.services import add_audit

    only = set(params.get("only") or []) or None
    value = release_managed_processes_guarded(
        session_factory,
        actor=actor,
        reason="AI 管家受控释放（用户已授权）",
        only=only,
    )
    verification = actions_mod._release_verification(value, only)
    verification["executor"] = spec.executor
    row.verification = verification
    if verification["outcome"] == "RELEASED":
        row.status = "SUCCEEDED"
    elif verification["outcome"] == "REFUSED":
        row.status = "REFUSED"
        row.last_error = f"底层释放执行器拒绝：{verification['reason']}"
    else:
        row.status = "NEEDS_REVIEW"
        row.last_error = f"释放结果 {verification['outcome']}：{verification['reason'] or '需要人工核对'}"
    add_audit(
        db, actor, "SUPPORT_ACTION_RELEASE_RESULT", "support_action", row.id,
        {
            "kind": row.kind,
            "outcome": verification["outcome"],
            "reason": verification["reason"],
            "stopped": [item.get("id") for item in verification["stopped"]],
            "refused": [item.get("id") for item in verification["refused"]],
        },
        project_id=row.project_id,
    )


def _release_readback(db, settings, row) -> None:
    import shine.support.actions as actions_mod

    if row.status in {"DELEGATED", "EXECUTED"}:
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
        return
    occupancy = actions_mod._action_occupancy(db, row.kind, dict(row.params or {}))
    row.verification = {
        **(row.verification or {}),
        "current_occupancy": {"busy": occupancy["busy"], "code": occupancy["code"], "checked_at": utcnow().isoformat()},
    }
    db.commit()


# ---------------------------------------------------------------------------
# support_web_search_update
# ---------------------------------------------------------------------------


def _web_search_target_facts(db, settings, params: dict[str, Any], project_id: str | None) -> tuple[dict[str, Any], str | None]:
    from shine.support.agent import web_search_enabled

    return {"current": bool(web_search_enabled(db))}, None


def _web_search_preflight(db, settings, params: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    return {
        **common,
        "summary": "只修改 AI 管家自身的联网检索开关；不会触发下载、脚本或生产任务。",
        "web_search": bool(params.get("web_search")),
        "will_start_gpu": False,
        "will_modify_user_comfyui": False,
        "authorization_summary": "确认后写入本机偏好；可随时改回。",
    }


def _web_search_execute(db, settings, session_factory, row, params, declaration, spec, actor, gateway) -> None:
    from shine.support.agent import set_web_search

    try:
        extra = routed_kwargs(declaration, params, set_web_search, handled={"web_search"})
    except ParamError as exc:
        raise ActionBlocked("PARAM_NOT_ROUTABLE", str(exc)) from exc
    value = set_web_search(db, actor=actor, enabled=bool(params.get("web_search")), **extra)
    row.status = "SUCCEEDED"
    row.verification = {
        "outcome": "VERIFIED",
        "executor": spec.executor,
        "authority": "GET /api/v1/support/preferences",
        "web_search": bool(value.get("web_search")),
        "checked_at": utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# image_runtime_validate (NEW V4)
# ---------------------------------------------------------------------------


def _image_validate_target_facts(db, settings, params: dict[str, Any], project_id: str | None) -> tuple[dict[str, Any], str | None]:
    from shine.comfyops import image_runtime as runtime
    from shine.models import Project

    pid = str(project_id or params.get("project_id") or "")
    if not pid or db.get(Project, pid) is None:
        return {}, "PROJECT_NOT_FOUND"
    config = runtime.load_config(settings)
    return {
        "project_id": pid,
        "families": sorted(params.get("families") or ["character", "scene", "prop"]),
        "instance_id": config.instance_id,
        "base_url": config.base_url,
    }, None


def _image_validate_preflight(db, settings, params: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    from shine.comfyops import image_runtime as runtime

    config = runtime.load_config(settings)
    occupancy = common["occupancy"]
    not_bound = not config.instance_id or not config.base_url
    return {
        **common,
        "summary": "提交三合一最小生图验证批次，通过真实渲染证明本地 Qwen-Image-2.1 能力就绪。",
        "families": list(params.get("families") or ["character", "scene", "prop"]),
        "instance_id": config.instance_id,
        "blocked": bool(occupancy["busy"] or not_bound),
        "blocked_code": occupancy["code"] if occupancy["busy"] else ("IMAGE_RUNTIME_NOT_BOUND" if not_bound else None),
        "will_start_gpu": True,
        "will_modify_user_comfyui": False,
        "authorization_summary": "确认后按原队列提交验证小样；通过后即解锁项目素材生图与定妆照能力。",
    }


def _image_validate_execute(db, settings, session_factory, row, params, declaration, spec, actor, gateway) -> None:
    from shine.materialprep.service import validate_image_runtime_setup

    try:
        res = validate_image_runtime_setup(
            db,
            settings,
            project_id=str(row.project_id or params.get("project_id") or ""),
            idempotency_key=f"copilot-validate:{row.id}",
            families=params.get("families"),
            rights_id=params.get("rights_id"),
            actor=actor,
        )
    except HTTPException as exc:
        raise blocked_from_executor(exc) from exc
    row.original_task_id = str(res.get("run_id") or "") or None
    row.status = "DELEGATED"
    row.verification = {
        "outcome": "PENDING",
        "executor": spec.executor,
        "authority": "GET /api/v1/setup/image-readiness",
        "run_id": row.original_task_id,
        "run_status": res.get("status"),
        "checked_at": utcnow().isoformat(),
    }


def _image_validate_readback(db, settings, row) -> None:
    if row.status != "DELEGATED" or not row.original_task_id:
        return
    from shine.materialprep.models import MaterialRun

    run = db.get(MaterialRun, str(row.original_task_id))
    if run is None:
        row.status = "NEEDS_REVIEW"
        row.last_error = "找不到生图验证批次记录，请先核对。"
        row.verification = {**(row.verification or {}), "outcome": "UNKNOWN", "checked_at": utcnow().isoformat()}
        db.commit()
        return
    status = str(run.status or "")
    row.verification = {**(row.verification or {}), "run_status": status, "checked_at": utcnow().isoformat()}
    if status == "COMPLETED":
        row.status = "SUCCEEDED"
        row.verification["outcome"] = "VERIFIED"
    elif status in {"FAILED", "CANCELLED"}:
        row.status = "FAILED"
        row.last_error = f"验证批次结束，状态：{status}"
        row.verification["outcome"] = "FAILED"
    elif status == "NEEDS_REVIEW":
        row.status = "NEEDS_REVIEW"
        row.last_error = "验证批次存在待核对项，不能自动重试。"
        row.verification["outcome"] = "UNKNOWN"
    db.commit()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

register_handler("setup_repair", ActionHandler(target_facts=setup_target_facts, preflight=_setup_preflight, execute=_setup_execute, readback=_setup_readback))
register_handler("setup_reconcile", ActionHandler(target_facts=setup_target_facts, preflight=_setup_preflight, execute=_setup_execute, readback=_setup_readback))
register_handler("resource_install_plan", ActionHandler(target_facts=_resource_plan_target_facts, preflight=_resource_plan_preflight, execute=_resource_plan_execute, readback=_resource_plan_readback))
register_handler("managed_release", ActionHandler(target_facts=_release_target_facts, preflight=_release_preflight, execute=_release_execute, readback=_release_readback))
register_handler("support_web_search_update", ActionHandler(target_facts=_web_search_target_facts, preflight=_web_search_preflight, execute=_web_search_execute))
register_handler("image_runtime_validate", ActionHandler(target_facts=_image_validate_target_facts, preflight=_image_validate_preflight, execute=_image_validate_execute, readback=_image_validate_readback))
