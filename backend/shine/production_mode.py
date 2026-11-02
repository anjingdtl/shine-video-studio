"""The one authoritative production-mode summary the Web workbench shows.

The API, the frontend badge and the submission gates all read the same state
computed here.  Four different kinds of evidence stay separate on purpose:

* configuration validity — the ACTIVE runtime identity still re-verifies;
* service validation     — sealed live evidence from /setup/services/validate;
* renderer liveness      — the managed renderer is idle-stopped by design, so
                           "service not running" never invalidates the mode;
* execution activity     — real jobs / claimed production series in flight.

A stopped renderer never demotes READY; only reconciliation, configuration,
validation or worker problems do.  ``mock`` jobs never turn the mode RUNNING:
the badge reports real production, demo tasks stay demo tasks.
"""
from __future__ import annotations

from sqlalchemy import func, select

from shine.comfyops.models import ResourceLease
from shine.comfyops.runtime_configuration import (
    ACTIVE_KEY,
    CANDIDATE_KEY,
    service_evidence_status,
)
from shine.models import AppSetting, Job, ProductionSeries

REAL_RUNNING_STATUSES = ("SUBMITTING", "RUNNING", "QC_PENDING")
LIVE_SERIES_STATUSES = ("QUEUED", "RUNNING")

# Chinese explanations for the engineering codes; the raw code always travels
# in ``code`` so logs and the professional view stay traceable.
_CODE_ZH = {
    "ACTIVE_RUNTIME_CONFIGURATION_CHANGED": "本机制作环境的固定文件发生了变化，需要重新检查配置。",
    "ACTIVE_RUNTIME_CONFIGURATION_INVALID": "本机制作环境配置未通过完整性检查，需要重新配置。",
    "ACTIVE_RUNTIME_OFFLINE_SIMULATION_FORBIDDEN": "离线演练模式与真实制作不能同时开启。",
    "ACTIVE_RUNTIME_RECIPE_CHANGED": "本机受管渲染器的配置文件发生了变化。",
    "WORKER_OFFLINE": "后台制作服务未在线，请用启动器重新打开工作台后再试。",
}


def _blocked_detail(code: str) -> str:
    return _CODE_ZH.get(code, f"真实制作环境暂时不可用（{code}），请运行环境检查定位原因。")


def summarize_production_mode(db, settings, *, effective, runtime_error, worker_online, renderer_online):
    """Return the single production-mode dict for /api/v1/system/status."""
    active = db.get(AppSetting, ACTIVE_KEY)
    candidate = db.get(AppSetting, CANDIDATE_KEY)
    provider = effective.render_provider if effective is not None else "unavailable"

    running_real = db.scalar(
        select(func.count(Job.id)).where(Job.status.in_(REAL_RUNNING_STATUSES), Job.provider == "comfyui")
    ) or 0
    series_active = db.scalar(
        select(func.count(ProductionSeries.id)).where(ProductionSeries.status.in_(LIVE_SERIES_STATUSES))
    ) or 0
    reconcile_leases = db.scalar(
        select(func.count(ResourceLease.id)).where(ResourceLease.status == "RECONCILE_REQUIRED")
    ) or 0

    if active is not None:
        runtime_status = "ACTIVE"
    elif runtime_error is not None:
        runtime_status = "INVALID_REQUIRES_REVIEW"
    else:
        runtime_status = "NOT_ACTIVATED"

    # Priority: uncertain external state first (never mask it), then a hard
    # configuration block, then live activity, then validation/worker
    # readiness, then READY, and only then the not-yet-activated states.
    if reconcile_leases:
        mode, label, detail, action, code = (
            "RECONCILE_REQUIRED", "有任务待核对",
            "有结果不确定的生成任务需要人工核对，系统不会自动重试或覆盖。",
            "check_fix", "LEASE_RECONCILE_REQUIRED",
        )
    elif runtime_error is not None:
        error = str(runtime_error)
        mode, label, detail, action, code = (
            "BLOCKED", "真实制作暂不可用", _blocked_detail(error), "check_fix", error,
        )
    elif active is None:
        if candidate is not None or provider == "comfyui":
            mode, label, detail, action, code = (
                "SETUP_REQUIRED", "真实制作未完成",
                "已保存真实制作配置，还差验证与激活的最后几步。",
                "enable_real", "REAL_RUNTIME_NOT_ACTIVATED",
            )
        else:
            mode, label, detail, action, code = (
                "DEMO", "演示体验",
                "当前用演示引擎演练流程，不会生成真实影片。",
                "enable_real", None,
            )
    else:
        validation = service_evidence_status(db, settings)
        if running_real or series_active:
            mode, label, detail, action, code = (
                "RUNNING", "正在生成影片",
                "本机 H3 正在生成镜头，可在制作页查看每个镜头的进度。",
                "view_progress", None,
            )
        elif validation.get("status") != "READY":
            mode, label, detail, action, code = (
                "VALIDATION_REQUIRED", "真实制作待验证",
                "本机服务或工作流发生了变化，需要重新验证后才能继续真实制作。",
                "check_fix", "SERVICE_VALIDATION_REQUIRED",
            )
        elif not worker_online:
            mode, label, detail, action, code = (
                "BLOCKED", "真实制作暂不可用", _blocked_detail("WORKER_OFFLINE"), "check_fix", "WORKER_OFFLINE",
            )
        else:
            mode, label, detail, action, code = (
                "READY", "真实制作已就绪",
                "本机 H3 制作环境可用；渲染器会在开始制作时自动启动，平时空闲属于正常。",
                "start_making", None,
            )

    return {
        "mode": mode,
        "label": label,
        "detail": detail,
        "action": action,
        "code": code,
        "render_provider": provider,
        "runtime_status": runtime_status,
        "renderer_online": bool(renderer_online),
        "worker_online": bool(worker_online),
        "running_jobs": int(running_real),
        "series_active": int(series_active),
    }
