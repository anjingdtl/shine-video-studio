"""Single authoritative Manual4 readiness verdict and stage projection.

Rules:
* One source of truth: ``manual4.sqlite`` ledger rows only.
* One verdict function: ``evaluate_m4_readiness`` serves the overview endpoint,
  the UI stage rail, the portfolio row, and the pre-run / pre-delivery gates.
* Never calls or queries seven-stage tables (``director_*``, ``jobs``, etc.).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from shine.manual4.contracts.schemas import M4ReadinessVerdict, M4StageKey, M4StageSummary
from shine.manual4.storage.migrations import MANUAL4_RUNTIME_VERSION, MANUAL4_SCHEMA_VERSION
from shine.manual4.storage.models import (
    M4AssetVersion,
    M4AuditEvent,
    M4Delivery,
    M4DeliveryAcceptance,
    M4EpisodePlan,
    M4Job,
    M4ManualPromptRevision,
    M4MaterialRequirement,
    M4ProductionRun,
    M4Project,
    M4Shot,
    M4ShotAssetBinding,
    M4SourceDocument,
    M4StoryboardRevision,
    M4WorkerInstance,
    m4_utcnow,
)


def evaluate_m4_readiness(db: Session, project: M4Project) -> M4ReadinessVerdict:
    confirmed_sb = db.scalar(
        select(M4StoryboardRevision)
        .where(
            M4StoryboardRevision.project_id == project.id,
            M4StoryboardRevision.status == "CONFIRMED",
        )
        .order_by(M4StoryboardRevision.revision.desc())
        .limit(1)
    )
    draft_sb = db.scalar(
        select(M4StoryboardRevision)
        .where(
            M4StoryboardRevision.project_id == project.id,
            M4StoryboardRevision.status == "DRAFT",
        )
        .order_by(M4StoryboardRevision.revision.desc())
        .limit(1)
    )
    doc_count = db.scalar(
        select(func.count(M4SourceDocument.id)).where(M4SourceDocument.project_id == project.id)
    ) or 0

    episodes: list[M4EpisodePlan] = []
    shots: list[M4Shot] = []
    reqs: list[M4MaterialRequirement] = []
    bindings_by_shot: dict[str, list[M4ShotAssetBinding]] = {}
    frozen_prompts_by_shot: dict[str, M4ManualPromptRevision] = {}

    if confirmed_sb is not None:
        episodes = db.scalars(
            select(M4EpisodePlan)
            .where(M4EpisodePlan.storyboard_revision_id == confirmed_sb.id)
            .order_by(M4EpisodePlan.episode_no)
        ).all()
        shots = db.scalars(
            select(M4Shot)
            .where(M4Shot.storyboard_revision_id == confirmed_sb.id)
            .order_by(M4Shot.episode_id, M4Shot.shot_no)
        ).all()
        reqs = db.scalars(
            select(M4MaterialRequirement)
            .where(M4MaterialRequirement.storyboard_revision_id == confirmed_sb.id)
            .order_by(M4MaterialRequirement.family, M4MaterialRequirement.entity_name)
        ).all()
        shot_ids = [s.id for s in shots]
        if shot_ids:
            for link in db.scalars(
                select(M4ShotAssetBinding)
                .where(M4ShotAssetBinding.shot_id.in_(shot_ids))
                .order_by(M4ShotAssetBinding.ordinal)
            ).all():
                bindings_by_shot.setdefault(link.shot_id, []).append(link)
            for pr in db.scalars(
                select(M4ManualPromptRevision)
                .where(
                    M4ManualPromptRevision.shot_id.in_(shot_ids),
                    M4ManualPromptRevision.status == "FROZEN",
                )
                .order_by(M4ManualPromptRevision.revision.desc())
            ).all():
                frozen_prompts_by_shot.setdefault(pr.shot_id, pr)

    # Stage 1: Storyboard
    sb_blockers: list[dict[str, Any]] = []
    if confirmed_sb is None:
        sb_blockers.append(
            {
                "code": "M4_STORYBOARD_NOT_CONFIRMED",
                "stage": "storyboard",
                "message": "尚未确认并冻结人工分镜计划；请导入/填写集镜表后确认。",
                "action": "confirm_storyboard",
            }
        )
    elif not episodes or not shots:
        sb_blockers.append(
            {
                "code": "M4_STORYBOARD_EMPTY",
                "stage": "storyboard",
                "message": "已确认的分镜计划至少需要 1 集 1 镜。",
                "action": "edit_storyboard",
            }
        )
    else:
        for shot in shots:
            if not (4.0 <= float(shot.target_seconds) <= float(project.max_shot_seconds)):
                sb_blockers.append(
                    {
                        "code": "M4_SHOT_DURATION_OUT_OF_RANGE",
                        "stage": "storyboard",
                        "shot_id": shot.id,
                        "shot_code": shot.shot_code,
                        "message": (
                            f"镜头 {shot.shot_code} 时长 {shot.target_seconds}s "
                            f"超出允许范围（4–{project.max_shot_seconds}s）。"
                        ),
                        "action": "edit_storyboard",
                    }
                )
    stage1_done = confirmed_sb is not None and not sb_blockers

    # Stage 2: Materials (Ref2VA readiness + required requirements promoted/bound)
    mat_blockers: list[dict[str, Any]] = []
    promoted_assets_count = db.scalar(
        select(func.count(M4AssetVersion.id)).where(
            M4AssetVersion.project_id == project.id,
            M4AssetVersion.status == "promoted",
        )
    ) or 0
    total_assets_count = db.scalar(
        select(func.count(M4AssetVersion.id)).where(M4AssetVersion.project_id == project.id)
    ) or 0
    if not stage1_done:
        mat_blockers.append(
            {
                "code": "M4_MATERIALS_WAIT_STORYBOARD",
                "stage": "materials",
                "message": "请先在①人工分镜书确认并冻结集镜计划。",
                "action": "go_storyboard",
            }
        )
    else:
        for req in reqs:
            if req.status not in {"promoted", "bound"} or not req.bound_asset_version_id:
                mat_blockers.append(
                    {
                        "code": "M4_MATERIAL_REQUIREMENT_UNBOUND",
                        "stage": "materials",
                        "requirement_id": req.id,
                        "family": req.family,
                        "entity_name": req.entity_name,
                        "message": f"素材「{req.entity_name}」（{req.family}）尚未晋升并绑定主素材。",
                        "action": "prepare_material",
                    }
                )
        for shot in shots:
            if not bindings_by_shot.get(shot.id):
                mat_blockers.append(
                    {
                        "code": "M4_SHOT_REQUIRES_REF2VA_REFERENCE",
                        "stage": "materials",
                        "shot_id": shot.id,
                        "shot_code": shot.shot_code,
                        "message": f"镜头 {shot.shot_code} 至少需要 1 项合法 Ref2VA 参考素材。",
                        "action": "bind_shot_material",
                    }
                )
    stage2_done = stage1_done and not mat_blockers

    # Stage 3: Production (manual frozen H3 prompts + run/jobs)
    prod_blockers: list[dict[str, Any]] = []
    latest_run = db.scalar(
        select(M4ProductionRun)
        .where(M4ProductionRun.project_id == project.id)
        .order_by(M4ProductionRun.created_at.desc())
        .limit(1)
    )
    passed_jobs_count = 0
    active_jobs_count = 0
    if not stage2_done:
        prod_blockers.append(
            {
                "code": "M4_PRODUCTION_WAIT_MATERIALS",
                "stage": "production",
                "message": "请先在②素材生成完成全部必需参考素材晋升与绑定。",
                "action": "go_materials",
            }
        )
    else:
        for shot in shots:
            frozen = frozen_prompts_by_shot.get(shot.id)
            if (
                frozen is None
                or frozen.shot_revision != shot.revision
                or abs(frozen.target_seconds - shot.target_seconds) > 1e-6
            ):
                prod_blockers.append(
                    {
                        "code": "M4_SHOT_PROMPT_NOT_FROZEN",
                        "stage": "production",
                        "shot_id": shot.id,
                        "shot_code": shot.shot_code,
                        "message": f"镜头 {shot.shot_code} 尚未冻结有效的当前版本 H3 提示词。",
                        "action": "freeze_prompt",
                    }
                )
        if latest_run is None:
            prod_blockers.append(
                {
                    "code": "M4_NO_PRODUCTION_RUN",
                    "stage": "production",
                    "message": "所有镜头提示词已就绪，请点击「确认排片并开拍」。",
                    "action": "confirm_and_run",
                }
            )
        else:
            run_jobs = db.scalars(select(M4Job).where(M4Job.run_id == latest_run.id)).all()
            passed_jobs_count = sum(1 for j in run_jobs if j.status == "PASSED")
            active_jobs_count = sum(
                1 for j in run_jobs if j.status in {"QUEUED", "SUBMITTING", "RUNNING", "QC_PENDING"}
            )
            if latest_run.status != "COMPLETED" or passed_jobs_count < len(shots):
                prod_blockers.append(
                    {
                        "code": f"M4_RUN_{latest_run.status}",
                        "stage": "production",
                        "run_id": latest_run.id,
                        "message": (
                            f"当前排片批次状态：{latest_run.status}"
                            f"（已通过 {passed_jobs_count}/{len(shots)} 镜）。"
                        ),
                        "action": "inspect_run",
                    }
                )
    stage3_done = stage2_done and not prod_blockers

    # Stage 4: Delivery
    deliveries = db.scalars(
        select(M4Delivery)
        .where(M4Delivery.project_id == project.id, M4Delivery.status == "DELIVERED")
        .order_by(M4Delivery.created_at.desc())
    ).all()
    accepted_count = 0
    if deliveries:
        delivery_ids = [d.id for d in deliveries]
        acc_rows = db.scalars(
            select(M4DeliveryAcceptance)
            .where(M4DeliveryAcceptance.delivery_id.in_(delivery_ids))
            .order_by(M4DeliveryAcceptance.created_at.asc())
        ).all()
        latest_acc: dict[str, M4DeliveryAcceptance] = {}
        for acc in acc_rows:
            latest_acc[acc.delivery_id] = acc
        accepted_count = sum(1 for d in deliveries if latest_acc.get(d.id) and latest_acc[d.id].decision == "accepted")

    deliv_blockers: list[dict[str, Any]] = []
    if not stage3_done:
        deliv_blockers.append(
            {
                "code": "M4_DELIVERY_WAIT_PRODUCTION",
                "stage": "delivery",
                "message": "请先在③排片生产完成镜头生成与质检。",
                "action": "go_production",
            }
        )
    elif not deliveries:
        deliv_blockers.append(
            {
                "code": "M4_NO_DELIVERY_ASSEMBLED",
                "stage": "delivery",
                "message": "所有镜头已通过，请点击「合成并导出成片」。",
                "action": "assemble_delivery",
            }
        )
    elif accepted_count < len(episodes):
        deliv_blockers.append(
            {
                "code": "M4_DELIVERY_PENDING_ACCEPTANCE",
                "stage": "delivery",
                "message": f"已合成 {len(deliveries)} 个成片，已验收 {accepted_count}/{len(episodes)} 集。",
                "action": "accept_delivery",
            }
        )
    stage4_done = stage3_done and not deliv_blockers

    next_stage: M4StageKey = "storyboard"
    if stage1_done and not stage2_done:
        next_stage = "materials"
    elif stage2_done and not stage3_done:
        next_stage = "production"
    elif stage3_done:
        next_stage = "delivery"

    def _status(done: bool, key: M4StageKey, blockers: list[dict[str, Any]]) -> str:
        if done:
            return "done"
        if key == next_stage:
            return "active"
        return "blocked" if blockers else "pending"

    stages = [
        M4StageSummary(
            key="storyboard",
            title="① 人工分镜书",
            done=stage1_done,
            status=_status(stage1_done, "storyboard", sb_blockers),  # type: ignore[arg-type]
            blocker_count=len(sb_blockers),
            blockers=sb_blockers,
            facts={
                "source_documents": doc_count,
                "confirmed_revision": confirmed_sb.revision if confirmed_sb else None,
                "draft_revision": draft_sb.revision if draft_sb else None,
                "episodes": len(episodes),
                "shots": len(shots),
                "planned_seconds": round(sum(s.target_seconds for s in shots), 2),
            },
        ),
        M4StageSummary(
            key="materials",
            title="② 素材生成",
            done=stage2_done,
            status=_status(stage2_done, "materials", mat_blockers),  # type: ignore[arg-type]
            blocker_count=len(mat_blockers),
            blockers=mat_blockers,
            facts={
                "requirements_total": len(reqs),
                "requirements_ready": sum(1 for r in reqs if r.status in {"promoted", "bound"}),
                "promoted_assets": promoted_assets_count,
                "total_assets": total_assets_count,
                "shots_bound": sum(1 for s in shots if bindings_by_shot.get(s.id)),
                "shots_total": len(shots),
            },
        ),
        M4StageSummary(
            key="production",
            title="③ 排片生产",
            done=stage3_done,
            status=_status(stage3_done, "production", prod_blockers),  # type: ignore[arg-type]
            blocker_count=len(prod_blockers),
            blockers=prod_blockers,
            facts={
                "prompts_frozen": len(frozen_prompts_by_shot),
                "shots_total": len(shots),
                "latest_run_id": latest_run.id if latest_run else None,
                "latest_run_status": latest_run.status if latest_run else None,
                "passed_jobs": passed_jobs_count,
                "active_jobs": active_jobs_count,
            },
        ),
        M4StageSummary(
            key="delivery",
            title="④ 产出成片",
            done=stage4_done,
            status=_status(stage4_done, "delivery", deliv_blockers),  # type: ignore[arg-type]
            blocker_count=len(deliv_blockers),
            blockers=deliv_blockers,
            facts={
                "deliveries": len(deliveries),
                "accepted_deliveries": accepted_count,
                "episodes": len(episodes),
            },
        ),
    ]

    return M4ReadinessVerdict(
        project_id=project.id,
        next_stage=next_stage,
        all_done=stage4_done,
        stages=stages,
        can_confirm_storyboard=bool(draft_sb is not None),
        can_start_production=stage2_done and not any(
            b.get("code") == "M4_SHOT_PROMPT_NOT_FROZEN" for b in prod_blockers
        ),
        can_assemble_delivery=stage3_done,
        summary={
            "episodes": len(episodes),
            "shots": len(shots),
            "promoted_assets": promoted_assets_count,
            "total_assets": total_assets_count,
            "deliveries": len(deliveries),
            "accepted_deliveries": accepted_count,
            "schema_version": MANUAL4_SCHEMA_VERSION,
            "runtime_version": MANUAL4_RUNTIME_VERSION,
        },
    )


def build_m4_overview(
    db: Session,
    project: M4Project,
    *,
    gpu_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    verdict = evaluate_m4_readiness(db, project)
    now = m4_utcnow()
    workers = db.scalars(select(M4WorkerInstance).order_by(M4WorkerInstance.started_at.desc())).all()
    online_workers = [
        w for w in workers if w.status != "stopped" and w.last_heartbeat_at >= now - timedelta(seconds=30)
    ]
    recent_events = db.scalars(
        select(M4AuditEvent)
        .where(M4AuditEvent.project_id == project.id)
        .order_by(M4AuditEvent.created_at.desc())
        .limit(15)
    ).all()
    return {
        "project": {
            "id": project.id,
            "title": project.title,
            "description": project.description,
            "language": project.language,
            "max_shot_seconds": project.max_shot_seconds,
            "workflow_kind": "manual4",
            "created_at": project.created_at.isoformat() + "Z",
        },
        "readiness": verdict.model_dump(mode="json"),
        "worker_online": bool(online_workers),
        "gpu": gpu_status or {"busy": False, "active_leases": [], "reconcile_required": []},
        "recent_events": [
            {
                "id": ev.id,
                "stage": ev.stage,
                "event_type": ev.event_type,
                "entity_type": ev.entity_type,
                "entity_id": ev.entity_id,
                "payload": ev.payload,
                "created_at": ev.created_at.isoformat() + "Z",
            }
            for ev in recent_events
        ],
    }
