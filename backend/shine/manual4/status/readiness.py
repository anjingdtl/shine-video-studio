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

from shine.foundation.h3 import MAX_AUDIO_SLOTS, MAX_IMAGE_SLOTS, MAX_TOTAL_SLOTS, MAX_VIDEO_SLOTS
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


def evaluate_shot_ref2va_bindings(
    links: list[M4ShotAssetBinding],
    assets_by_id: dict[str, M4AssetVersion],
) -> tuple[bool, str | None, str]:
    """Single authoritative Ref2VA verdict for one shot's bound references."""
    if not links:
        return False, "M4_SHOT_REQUIRES_REF2VA_REFERENCE", "缺少合法 Ref2VA 参考素材绑定。"

    img_count = 0
    aud_count = 0
    vid_count = 0
    for link in links:
        asset = assets_by_id.get(link.asset_version_id)
        if asset is None or asset.status != "promoted":
            return False, "M4_SHOT_BINDING_NOT_PROMOTED", "包含未晋升为主素材的绑定版本。"
        if str(getattr(asset, "rights_status", "local_unlinked") or "") == "REVOKED":
            return False, "M4_SHOT_BINDING_RIGHTS_REVOKED", "绑定的公共素材授权已撤销。"
        if link.use_as == "image":
            img_count += 1
        elif link.use_as == "audio":
            aud_count += 1
        elif link.use_as == "video":
            vid_count += 1

    if img_count < 1:
        return (
            False,
            "PROJECT_REQUIRES_REF2VA",
            "Ref2VA 生产要求至少绑定 1 张有效图片参考；仅有音频或视频参考不能通过。",
        )
    total = img_count + aud_count + vid_count
    if (
        img_count > MAX_IMAGE_SLOTS
        or aud_count > MAX_AUDIO_SLOTS
        or vid_count > MAX_VIDEO_SLOTS
        or total > MAX_TOTAL_SLOTS
    ):
        return (
            False,
            "M4_SHOT_REF2VA_SLOT_LIMIT_EXCEEDED",
            f"参考数量超出 H3 上限（图 {img_count}/{MAX_IMAGE_SLOTS}、音 {aud_count}/{MAX_AUDIO_SLOTS}、"
            f"总 {total}/{MAX_TOTAL_SLOTS}）。",
        )
    return True, None, ""


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

    # Stage 2: Materials (per-shot Ref2VA readiness; requirements are advisory, never hard-blocking)
    mat_blockers: list[dict[str, Any]] = []
    shots_ready_count = 0
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
        asset_ids = {
            link.asset_version_id
            for links in bindings_by_shot.values()
            for link in links
        }
        assets_by_id = (
            {
                a.id: a
                for a in db.scalars(
                    select(M4AssetVersion).where(M4AssetVersion.id.in_(asset_ids))
                ).all()
            }
            if asset_ids
            else {}
        )
        for shot in shots:
            ok_ref, ref_code, ref_msg = evaluate_shot_ref2va_bindings(
                bindings_by_shot.get(shot.id, []),
                assets_by_id,
            )
            if ok_ref:
                shots_ready_count += 1
            else:
                mat_blockers.append(
                    {
                        "code": ref_code or "M4_SHOT_REQUIRES_REF2VA_REFERENCE",
                        "stage": "materials",
                        "shot_id": shot.id,
                        "shot_code": shot.shot_code,
                        "message": f"镜头 {shot.shot_code}：{ref_msg}",
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
    ready_episode_ids: list[str] = []
    passed_episode_ids: list[str] = []
    if not stage2_done:
        prod_blockers.append(
            {
                "code": "M4_PRODUCTION_WAIT_MATERIALS",
                "stage": "production",
                "message": "请先在②素材生成确保每一镜均已绑定必需 Ref2VA 参考素材。",
                "action": "go_materials",
            }
        )
    else:
        ep_by_id = {ep.id: ep for ep in episodes}
        latest_job_by_shot: dict[str, M4Job] = {}
        all_jobs = db.scalars(
            select(M4Job)
            .where(M4Job.project_id == project.id, M4Job.shot_id.in_([s.id for s in shots]))
            .order_by(M4Job.created_at.desc(), M4Job.ordinal.desc())
        ).all()
        for job in all_jobs:
            latest_job_by_shot.setdefault(job.shot_id, job)

        passed_jobs_count = 0
        active_jobs_count = sum(
            1 for job in all_jobs if job.status in {"QUEUED", "SUBMITTING", "RUNNING", "QC_PENDING"}
        )
        ep_shot_totals: dict[str, int] = {}
        ep_shot_frozen: dict[str, int] = {}
        ep_shot_passed: dict[str, int] = {}
        for shot in shots:
            ep_shot_totals[shot.episode_id] = ep_shot_totals.get(shot.episode_id, 0) + 1
            frozen = frozen_prompts_by_shot.get(shot.id)
            if (
                frozen is None
                or frozen.shot_revision != shot.revision
                or abs(frozen.target_seconds - shot.target_seconds) > 1e-6
            ):
                ep_row = ep_by_id.get(shot.episode_id)
                prod_blockers.append(
                    {
                        "code": "M4_SHOT_PROMPT_NOT_FROZEN",
                        "stage": "production",
                        "episode_id": shot.episode_id,
                        "episode_code": ep_row.episode_code if ep_row else None,
                        "shot_id": shot.id,
                        "shot_code": shot.shot_code,
                        "message": f"镜头 {shot.shot_code} 尚未冻结有效的当前版本 H3 提示词。",
                        "action": "freeze_prompt",
                    }
                )
                continue
            ep_shot_frozen[shot.episode_id] = ep_shot_frozen.get(shot.episode_id, 0) + 1
            lj = latest_job_by_shot.get(shot.id)
            if (
                lj is not None
                and lj.status == "PASSED"
                and lj.shot_revision == shot.revision
                and lj.prompt_sha256 == frozen.prompt_sha256
                and lj.reference_manifest_sha256 == frozen.reference_manifest_sha256
            ):
                passed_jobs_count += 1
                ep_shot_passed[shot.episode_id] = ep_shot_passed.get(shot.episode_id, 0) + 1

        ready_episode_ids = [
            ep.id
            for ep in episodes
            if ep_shot_totals.get(ep.id, 0) > 0
            and ep_shot_frozen.get(ep.id, 0) == ep_shot_totals.get(ep.id, 0)
        ]
        passed_episode_ids = [
            ep.id
            for ep in episodes
            if ep_shot_totals.get(ep.id, 0) > 0
            and ep_shot_passed.get(ep.id, 0) == ep_shot_totals.get(ep.id, 0)
        ]

        if latest_run is None:
            if not prod_blockers:
                prod_blockers.append(
                    {
                        "code": "M4_NO_PRODUCTION_RUN",
                        "stage": "production",
                        "message": "所有镜头提示词已就绪，请点击「确认排片并开拍」。",
                        "action": "confirm_and_run",
                    }
                )
            elif ready_episode_ids:
                prod_blockers.append(
                    {
                        "code": "M4_NO_PRODUCTION_RUN",
                        "stage": "production",
                        "message": "已有分集提示词全部就绪，可选择「按集排片」先行开拍。",
                        "action": "confirm_and_run",
                    }
                )
        elif (
            latest_run.status in {"PENDING", "CLAIMED", "RUNNING", "PAUSED", "PAUSE_REQUESTED"}
            or passed_jobs_count < len(shots)
        ):
            prod_blockers.append(
                {
                    "code": f"M4_RUN_{latest_run.status}",
                    "stage": "production",
                    "run_id": latest_run.id,
                    "message": (
                        f"当前排片批次状态：{latest_run.status}"
                        f"（当前版本已通过 {passed_jobs_count}/{len(shots)} 镜）。"
                    ),
                    "action": "inspect_run",
                }
            )
    stage3_done = stage2_done and not prod_blockers

    # Stage 4: Delivery (counted per confirmed episode)
    confirmed_ep_ids = {e.id for e in episodes}
    deliveries = (
        db.scalars(
            select(M4Delivery)
            .where(
                M4Delivery.project_id == project.id,
                M4Delivery.status == "DELIVERED",
                M4Delivery.episode_id.in_(confirmed_ep_ids),
            )
            .order_by(M4Delivery.created_at.desc())
        ).all()
        if confirmed_ep_ids
        else []
    )
    accepted_episode_ids: set[str] = set()
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
        accepted_episode_ids = {
            d.episode_id
            for d in deliveries
            if latest_acc.get(d.id) and latest_acc[d.id].decision == "accepted"
        }
    accepted_count = len(accepted_episode_ids)
    delivered_episodes_count = len({d.episode_id for d in deliveries})

    deliv_blockers: list[dict[str, Any]] = []
    if not stage3_done and not passed_episode_ids:
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
                "message": (
                    "所有镜头已通过，请点击「合成并导出成片」。"
                    if stage3_done
                    else "已有分集完成出片，可先合成并验收已就绪分集。"
                ),
                "action": "assemble_delivery",
            }
        )
    elif accepted_count < len(episodes):
        deliv_blockers.append(
            {
                "code": "M4_DELIVERY_PENDING_ACCEPTANCE",
                "stage": "delivery",
                "message": (
                    f"已合成 {delivered_episodes_count}/{len(episodes)} 集（共 {len(deliveries)} 个版本），"
                    f"已验收 {accepted_count}/{len(episodes)} 集。"
                ),
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
                "requirements_ready": sum(
                    1 for r in reqs if r.status in {"promoted", "bound", "declined"} or r.user_declined
                ),
                "promoted_assets": promoted_assets_count,
                "total_assets": total_assets_count,
                "shots_bound": sum(1 for s in shots if bindings_by_shot.get(s.id)),
                "shots_ready": shots_ready_count,
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
                "ready_episode_ids": ready_episode_ids,
                "passed_episode_ids": passed_episode_ids,
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
        can_start_production=stage2_done
        and (
            not any(b.get("code") == "M4_SHOT_PROMPT_NOT_FROZEN" for b in prod_blockers)
            or bool(ready_episode_ids)
        ),
        can_assemble_delivery=stage3_done or bool(passed_episode_ids),
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


def build_m4_run_progress(db: Session, project_id: str) -> dict[str, Any] | None:
    """Single authoritative production run progress & phase projection from manual4.sqlite."""
    active_run = db.scalar(
        select(M4ProductionRun)
        .where(
            M4ProductionRun.project_id == project_id,
            M4ProductionRun.status.in_(["PENDING", "CLAIMED", "RUNNING", "PAUSE_REQUESTED"]),
        )
        .order_by(M4ProductionRun.created_at.desc())
        .limit(1)
    )
    run = active_run or db.scalar(
        select(M4ProductionRun)
        .where(M4ProductionRun.project_id == project_id)
        .order_by(M4ProductionRun.created_at.desc())
        .limit(1)
    )
    if run is None:
        return None

    jobs = db.scalars(
        select(M4Job).where(M4Job.run_id == run.id).order_by(M4Job.ordinal.asc())
    ).all()
    if not jobs:
        return None

    shot_ids = [j.shot_id for j in jobs]
    shot_code_by_id = {
        s.id: s.shot_code
        for s in db.scalars(select(M4Shot).where(M4Shot.id.in_(shot_ids))).all()
    }
    total_jobs = len(jobs)
    passed_jobs = sum(1 for j in jobs if j.status == "PASSED")
    failed_jobs = sum(1 for j in jobs if j.status in {"FAILED", "BLOCKED"})
    needs_review_jobs = sum(1 for j in jobs if j.status == "NEEDS_REVIEW")
    queued_jobs = sum(1 for j in jobs if j.status == "QUEUED")
    in_progress = next((j for j in jobs if j.status in {"SUBMITTING", "RUNNING", "QC_PENDING"}), None)
    next_queued = next((j for j in jobs if j.status == "QUEUED"), None)
    active_job = in_progress or (next_queued if run.status in {"PENDING", "CLAIMED", "RUNNING"} else None)

    is_working = run.status in {"PENDING", "CLAIMED", "RUNNING", "PAUSE_REQUESTED"}
    if run.status == "COMPLETED":
        phase = "COMPLETED"
        phase_label = "本批次全部镜头已通过"
    elif run.status == "PENDING":
        phase = "WAITING_WORKER"
        phase_label = "等待 Worker 认领排片批次"
    elif run.status == "CLAIMED":
        phase = "STARTING_RENDERER"
        phase_label = "正在启动/接管 H3 渲染器与显存租约"
    elif run.status == "PAUSE_REQUESTED":
        phase = "PAUSE_REQUESTED"
        phase_label = "正在等待当前镜完成后暂停"
    elif run.status == "PAUSED":
        phase = "PAUSED"
        phase_label = "已暂停，可随时恢复继续"
    elif run.status == "NEEDS_REVIEW":
        phase = "NEEDS_REVIEW"
        phase_label = "存在未知结果镜头，等待人工对账"
    elif run.status == "CANCELLED":
        phase = "CANCELLED"
        phase_label = "本批次已取消"
    elif run.status == "FAILED":
        phase = "FAILED"
        phase_label = "批次存在失败镜头"
    elif in_progress is not None and in_progress.status == "SUBMITTING":
        phase = "SUBMITTING"
        phase_label = "正在提交参考与提示词到 ComfyUI"
    elif in_progress is not None and in_progress.status == "RUNNING":
        phase = "RENDERING"
        phase_label = "H3 正在渲染视频"
    elif in_progress is not None and in_progress.status == "QC_PENDING":
        phase = "QC"
        phase_label = "正在执行技术质检"
    else:
        phase = "PREPARING_NEXT"
        phase_label = "准备下一镜"

    if total_jobs > 0:
        if run.status == "COMPLETED":
            progress_pct = 100
        elif in_progress is not None and passed_jobs < total_jobs:
            # Show settled shots + half-step credit for the actively executing shot so bar visibly advances
            stage_fraction = {"SUBMITTING": 0.25, "RUNNING": 0.55, "QC_PENDING": 0.85}.get(in_progress.status, 0.2)
            progress_pct = min(99, max(1, round(((passed_jobs + stage_fraction) / total_jobs) * 100)))
        else:
            progress_pct = min(100, round((passed_jobs / total_jobs) * 100))
    else:
        progress_pct = 0

    current_shot_no = (
        (active_job.ordinal + 1)
        if active_job is not None
        else min(total_jobs, passed_jobs + (0 if passed_jobs == total_jobs else 1))
    )

    return {
        "run_id": run.id,
        "status": run.status,
        "scope_kind": run.scope_kind,
        "provider": run.provider,
        "phase": phase,
        "phase_label": phase_label,
        "is_working": is_working,
        "progress_pct": progress_pct,
        "total_jobs": total_jobs,
        "passed_jobs": passed_jobs,
        "failed_jobs": failed_jobs,
        "needs_review_jobs": needs_review_jobs,
        "queued_jobs": queued_jobs,
        "current_shot_no": current_shot_no,
        "active_job": (
            {
                "id": active_job.id,
                "shot_id": active_job.shot_id,
                "shot_code": shot_code_by_id.get(active_job.shot_id, active_job.shot_id[:8]),
                "status": active_job.status,
                "ordinal": active_job.ordinal,
            }
            if active_job is not None
            else None
        ),
    }


def build_m4_overview(
    db: Session,
    project: M4Project,
    *,
    gpu_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    verdict = evaluate_m4_readiness(db, project)
    run_progress = build_m4_run_progress(db, project.id)
    now = m4_utcnow()
    workers = db.scalars(select(M4WorkerInstance).order_by(M4WorkerInstance.started_at.desc())).all()
    online_workers = [
        w
        for w in workers
        if w.status not in {"OFFLINE", "stopped"} and w.last_heartbeat_at >= now - timedelta(seconds=60)
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
        "production_progress": run_progress,
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
