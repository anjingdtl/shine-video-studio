"""Manual4 Stage 3 production scheduling, immutable execution snapshots, and job control."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from shine.manual4.comfy.h3_adapter import (
    ACCELERATORS,
    M4GraphCompileError,
    M4SecondPassSpec,
    compile_m4_ref2va_workflow,
    verified_variants_catalog,
)
from shine.manual4.h3.output_frame import frame_options, frame_size
from shine.manual4.prompts.service import (
    _serialize_prompt,
    build_shot_reference_manifest,
    validate_frozen_prompt_for_shot,
)
from shine.manual4.status.readiness import evaluate_m4_readiness
from shine.manual4.storage.models import (
    M4AuditEvent,
    M4EpisodePlan,
    M4ExecutionSnapshot,
    M4Job,
    M4JobAttempt,
    M4ManualPromptRevision,
    M4ProductionRun,
    M4Project,
    M4Shot,
    M4StoryboardRevision,
    m4_utcnow,
)


def _canonical_sha(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _serialize_job(job: M4Job, attempts: list[M4JobAttempt] | None = None) -> dict[str, Any]:
    return {
        "id": job.id,
        "run_id": job.run_id,
        "episode_id": job.episode_id,
        "shot_id": job.shot_id,
        "shot_revision": job.shot_revision,
        "ordinal": job.ordinal,
        "idempotency_key": job.idempotency_key,
        "prompt_revision_id": job.prompt_revision_id,
        "prompt_sha256": job.prompt_sha256,
        "reference_manifest_sha256": job.reference_manifest_sha256,
        "target_seconds": job.target_seconds,
        "continuity_mode": job.continuity_mode,
        "previous_job_id": job.previous_job_id,
        "execution_generation": int(getattr(job, "execution_generation", 1) or 1),
        "provider": job.provider,
        "status": job.status,
        "state_version": job.state_version,
        "attempt_count": job.attempt_count,
        "output_storage_path": job.output_storage_path,
        "output_sha256": job.output_sha256,
        "output_bytes": job.output_bytes,
        "measured_duration_s": job.measured_duration_s,
        "measured_width": job.measured_width,
        "measured_height": job.measured_height,
        "measured_fps": job.measured_fps,
        "qc_result": job.qc_result,
        "failure_code": job.failure_code,
        "failure_detail": job.failure_detail,
        "media_url": (
            f"/api/v1/manual4/projects/{job.project_id}/jobs/{job.id}/media"
            if job.output_storage_path
            else None
        ),
        "attempts": [
            {
                "id": a.id,
                "attempt_no": a.attempt_no,
                "status": a.status,
                "prompt_id": a.prompt_id,
                "prompt_graph_sha256": a.prompt_graph_sha256,
                "error_code": a.error_code,
                "error_detail": a.error_detail,
                "created_at": a.created_at,
            }
            for a in (attempts or [])
        ],
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


def _serialize_run(run: M4ProductionRun, jobs: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": run.id,
        "project_id": run.project_id,
        "storyboard_revision_id": getattr(run, "storyboard_revision_id", None),
        "execution_generation": int(getattr(run, "execution_generation", 1) or 1),
        "execution_snapshot_id": run.execution_snapshot_id,
        "idempotency_key": run.idempotency_key,
        "scope_kind": run.scope_kind,
        "scope_target_ids": run.scope_target_ids,
        "provider": run.provider,
        "status": run.status,
        "worker_id": run.worker_id,
        "lease_id": run.lease_id,
        "lease_owner": run.lease_owner,
        "error_code": run.error_code,
        "error_detail": run.error_detail,
        "jobs": jobs,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }


def get_production_state(m4_db: Session, project: M4Project) -> dict[str, Any]:
    confirmed = m4_db.scalar(
        select(M4StoryboardRevision)
        .where(M4StoryboardRevision.project_id == project.id, M4StoryboardRevision.status == "CONFIRMED")
        .order_by(M4StoryboardRevision.revision.desc())
        .limit(1)
    )
    episodes_data: list[dict[str, Any]] = []
    if confirmed is not None:
        episodes = m4_db.scalars(
            select(M4EpisodePlan)
            .where(M4EpisodePlan.storyboard_revision_id == confirmed.id)
            .order_by(M4EpisodePlan.episode_no)
        ).all()
        shots = m4_db.scalars(
            select(M4Shot)
            .where(M4Shot.storyboard_revision_id == confirmed.id)
            .order_by(M4Shot.episode_id, M4Shot.shot_no)
        ).all()
        shot_ids = [s.id for s in shots]
        prompts_by_shot: dict[str, list[M4ManualPromptRevision]] = {}
        latest_job_by_shot: dict[str, M4Job] = {}
        if shot_ids:
            for pr in m4_db.scalars(
                select(M4ManualPromptRevision)
                .where(M4ManualPromptRevision.shot_id.in_(shot_ids))
                .order_by(M4ManualPromptRevision.revision.desc())
            ).all():
                prompts_by_shot.setdefault(pr.shot_id, []).append(pr)
            for job in m4_db.scalars(
                select(M4Job)
                .where(M4Job.project_id == project.id, M4Job.shot_id.in_(shot_ids))
                .order_by(M4Job.created_at.desc(), M4Job.ordinal.desc())
            ).all():
                latest_job_by_shot.setdefault(job.shot_id, job)

        shots_by_ep: dict[str, list[dict[str, Any]]] = {}
        for sh in shots:
            manifest = build_shot_reference_manifest(m4_db, sh)
            revs = prompts_by_shot.get(sh.id, [])
            frozen = next((r for r in revs if r.status == "FROZEN"), None)
            draft = next((r for r in revs if r.status == "DRAFT"), None)
            invalidated = next((r for r in revs if r.status == "INVALIDATED"), None)
            active_prompt = frozen or draft or invalidated
            valid_frozen, invalid_reason = validate_frozen_prompt_for_shot(m4_db, sh, frozen or invalidated)
            lj = latest_job_by_shot.get(sh.id)
            shots_by_ep.setdefault(sh.episode_id, []).append(
                {
                    "id": sh.id,
                    "episode_id": sh.episode_id,
                    "shot_no": sh.shot_no,
                    "shot_code": sh.shot_code,
                    "revision": sh.revision,
                    "target_seconds": sh.target_seconds,
                    "summary": sh.summary,
                    "dialogue": sh.dialogue,
                    "aspect_ratio": sh.aspect_ratio,
                    "continuity_mode": sh.continuity_mode,
                    "reference_manifest": manifest,
                    "frozen_prompt": _serialize_prompt(frozen) if frozen else None,
                    "draft_prompt": _serialize_prompt(draft) if draft else None,
                    "active_prompt": _serialize_prompt(active_prompt) if active_prompt else None,
                    "prompt_valid_for_run": valid_frozen,
                    "prompt_invalid_reason": invalid_reason,
                    "latest_job": _serialize_job(lj) if lj else None,
                }
            )
        for ep in episodes:
            episodes_data.append(
                {
                    "id": ep.id,
                    "episode_no": ep.episode_no,
                    "episode_code": ep.episode_code,
                    "title": ep.title,
                    "planned_total_seconds": ep.planned_total_seconds,
                    "shots": shots_by_ep.get(ep.id, []),
                }
            )

    runs = m4_db.scalars(
        select(M4ProductionRun)
        .where(M4ProductionRun.project_id == project.id)
        .order_by(M4ProductionRun.created_at.desc())
        .limit(10)
    ).all()
    run_ids = [r.id for r in runs]
    jobs_by_run: dict[str, list[dict[str, Any]]] = {}
    if run_ids:
        jobs = m4_db.scalars(
            select(M4Job).where(M4Job.run_id.in_(run_ids)).order_by(M4Job.ordinal.asc())
        ).all()
        job_ids = [j.id for j in jobs]
        attempts_by_job: dict[str, list[M4JobAttempt]] = {}
        if job_ids:
            for att in m4_db.scalars(
                select(M4JobAttempt)
                .where(M4JobAttempt.job_id.in_(job_ids))
                .order_by(M4JobAttempt.attempt_no.asc())
            ).all():
                attempts_by_job.setdefault(att.job_id, []).append(att)
        for j in jobs:
            jobs_by_run.setdefault(j.run_id, []).append(_serialize_job(j, attempts_by_job.get(j.id, [])))

    verdict = evaluate_m4_readiness(m4_db, project)
    return {
        "confirmed_revision": {"id": confirmed.id, "revision": confirmed.revision} if confirmed else None,
        "episodes": episodes_data,
        "runs": [_serialize_run(r, jobs_by_run.get(r.id, [])) for r in runs],
        "options": {
            "variants": verified_variants_catalog(),
            "accelerators": [{"id": k, **v} for k, v in ACCELERATORS.items()],
            "frames": frame_options(),
        },
        "readiness": verdict.model_dump(),
    }


def _resolve_scope_shots(
    m4_db: Session,
    project: M4Project,
    confirmed: M4StoryboardRevision,
    scope_kind: str,
    scope_target_ids: list[str] | None,
) -> list[M4Shot]:
    all_episodes = m4_db.scalars(
        select(M4EpisodePlan)
        .where(M4EpisodePlan.storyboard_revision_id == confirmed.id)
        .order_by(M4EpisodePlan.episode_no)
    ).all()
    ep_order = {ep.id: ep.episode_no for ep in all_episodes}
    all_shots = m4_db.scalars(
        select(M4Shot).where(M4Shot.storyboard_revision_id == confirmed.id)
    ).all()
    all_shots = sorted(all_shots, key=lambda s: (ep_order.get(s.episode_id, 0), s.shot_no))

    sk = (scope_kind or "project").strip().lower()
    targets = [str(t) for t in (scope_target_ids or []) if str(t).strip()]
    if sk == "project":
        selected = all_shots
    elif sk == "episode":
        if not targets:
            raise HTTPException(422, {"code": "M4_SCOPE_TARGETS_REQUIRED", "message": "按集开拍必须指定集 ID。"})
        target_set = set(targets)
        selected = [s for s in all_shots if s.episode_id in target_set]
    elif sk == "shot":
        if not targets:
            raise HTTPException(422, {"code": "M4_SCOPE_TARGETS_REQUIRED", "message": "按镜开拍必须指定镜头 ID。"})
        target_set = set(targets)
        selected = [s for s in all_shots if s.id in target_set]
    else:
        raise HTTPException(422, {"code": "M4_SCOPE_KIND_INVALID", "message": f"不支持的排片范围：{sk}"})

    if not selected:
        raise HTTPException(404, {"code": "M4_SCOPE_EMPTY", "message": "指定范围内没有任何有效镜头。"})
    return selected


def _build_snapshot_payload(
    m4_db: Session,
    project: M4Project,
    *,
    scope_kind: str,
    scope_target_ids: list[str] | None,
    variant_id: str,
    accelerator_id: str,
    resolution_scale: float,
    second_pass: dict[str, Any] | None,
    seed: int,
    provider: str,
    previous_passed_job_id: str | None = None,
) -> tuple[M4StoryboardRevision, dict[str, Any], list[dict[str, Any]], str]:
    confirmed = m4_db.scalar(
        select(M4StoryboardRevision)
        .where(M4StoryboardRevision.project_id == project.id, M4StoryboardRevision.status == "CONFIRMED")
        .order_by(M4StoryboardRevision.revision.desc())
        .limit(1)
    )
    if confirmed is None:
        raise HTTPException(409, {"code": "M4_STORYBOARD_NOT_CONFIRMED", "message": "请先在①确认分镜计划。"})

    selected_shots = _resolve_scope_shots(m4_db, project, confirmed, scope_kind, scope_target_ids)
    sp_spec: M4SecondPassSpec | None = None
    if second_pass and second_pass.get("enabled"):
        sp_spec = M4SecondPassSpec(
            denoise=float(second_pass.get("denoise", 0.25)),
            latent_scale=float(second_pass.get("latent_scale", 1.0)),
            nfe=str(second_pass.get("nfe", "8")),
        )

    items: list[dict[str, Any]] = []
    for idx, sh in enumerate(selected_shots):
        latest_prompt = m4_db.scalar(
            select(M4ManualPromptRevision)
            .where(
                M4ManualPromptRevision.shot_id == sh.id,
                M4ManualPromptRevision.status.in_(["FROZEN", "INVALIDATED"]),
            )
            .order_by(M4ManualPromptRevision.revision.desc())
            .limit(1)
        )
        ok, err_code = validate_frozen_prompt_for_shot(m4_db, sh, latest_prompt)
        invalid_codes = {
            "M4_DURATION_CHANGED",
            "M4_REFERENCE_MANIFEST_CHANGED",
            "M4_SHOT_REVISION_CHANGED",
            "M4_MANUAL_PROMPT_INVALIDATED",
        }
        if not ok or latest_prompt is None:
            if err_code in invalid_codes:
                raise HTTPException(
                    409,
                    {
                        "code": "M4_MANUAL_PROMPT_INVALIDATED",
                        "shot_id": sh.id,
                        "shot_code": sh.shot_code,
                        "reason": err_code,
                        "message": (
                            f"镜头 {sh.shot_code} 的人工 H3 提示词已失效（{err_code}），"
                            "请人工修订并重新冻结，严禁自动回退模板。"
                        ),
                    },
                )
            if err_code == "M4_PROJECT_REQUIRES_REF2VA":
                raise HTTPException(
                    409,
                    {
                        "code": "M4_PROJECT_REQUIRES_REF2VA",
                        "shot_id": sh.id,
                        "shot_code": sh.shot_code,
                        "message": f"镜头 {sh.shot_code} 缺少合法 Ref2VA 参考图绑定。",
                    },
                )
            raise HTTPException(
                409,
                {
                    "code": "M4_PROMPT_NOT_FROZEN",
                    "shot_id": sh.id,
                    "shot_code": sh.shot_code,
                    "message": f"镜头 {sh.shot_code} 尚未冻结人工 H3 提示词。",
                },
            )

        manifest = build_shot_reference_manifest(m4_db, sh)
        img_count = sum(1 for m in manifest if m["use_as"] == "image")
        aud_count = sum(1 for m in manifest if m["use_as"] == "audio")
        vid_count = sum(1 for m in manifest if m["use_as"] == "video")
        same_episode_prev = idx > 0 and selected_shots[idx - 1].episode_id == sh.episode_id
        has_continuity_source = sh.continuity_mode == "last_frame" and (
            same_episode_prev or (idx == 0 and previous_passed_job_id is not None)
        )
        extra_last_frame = 1 if has_continuity_source else 0
        try:
            compiled = compile_m4_ref2va_workflow(
                variant_id=variant_id,
                accelerator_id=accelerator_id,
                second_pass=sp_spec,
                aspect_ratio=sh.aspect_ratio,
                resolution_scale=resolution_scale,
                image_count=img_count + extra_last_frame,
                audio_count=aud_count,
                video_count=vid_count,
            )
        except M4GraphCompileError as exc:
            raise HTTPException(422, {"code": exc.code, "message": exc.message, "shot_code": sh.shot_code}) from exc

        width, height = frame_size(sh.aspect_ratio, resolution_scale)
        items.append(
            {
                "ordinal": idx,
                "episode_id": sh.episode_id,
                "shot_id": sh.id,
                "shot_code": sh.shot_code,
                "shot_revision": sh.revision,
                "target_seconds": float(sh.target_seconds),
                "aspect_ratio": sh.aspect_ratio,
                "width": width,
                "height": height,
                "continuity_mode": sh.continuity_mode,
                "prompt_revision_id": latest_prompt.id,
                "prompt_text": latest_prompt.prompt_text,
                "prompt_sha256": latest_prompt.prompt_sha256,
                "prompt_excerpt": latest_prompt.prompt_text[:120],
                "reference_manifest": manifest,
                "reference_manifest_sha256": latest_prompt.reference_manifest_sha256,
                "recipe_sha256": compiled.recipe_sha256,
                "seed": int(seed) + idx,
            }
        )

    config_json = {
        "variant_id": variant_id,
        "accelerator_id": accelerator_id,
        "resolution_scale": float(resolution_scale),
        "second_pass": second_pass if (second_pass and second_pass.get("enabled")) else None,
        "seed": int(seed),
        "provider": provider,
    }
    snap_sha = _canonical_sha(
        {
            "storyboard_revision_id": confirmed.id,
            "scope_kind": scope_kind,
            "scope_target_ids": [sh.id for sh in selected_shots],
            "config": config_json,
            "items": [
                {
                    "shot_id": i["shot_id"],
                    "shot_revision": i["shot_revision"],
                    "prompt_sha256": i["prompt_sha256"],
                    "reference_manifest_sha256": i["reference_manifest_sha256"],
                    "recipe_sha256": i["recipe_sha256"],
                }
                for i in items
            ],
        }
    )
    return confirmed, config_json, items, snap_sha


def preview_execution_snapshot(
    m4_db: Session,
    project: M4Project,
    *,
    scope_kind: str = "project",
    scope_target_ids: list[str] | None = None,
    variant_id: str = "h3-ref2va-int8-convrot",
    accelerator_id: str = "pdd8",
    resolution_scale: float = 1.0,
    second_pass: dict[str, Any] | None = None,
    seed: int = 77,
    provider: str = "comfyui",
) -> dict[str, Any]:
    confirmed, config_json, items, snap_sha = _build_snapshot_payload(
        m4_db,
        project,
        scope_kind=scope_kind,
        scope_target_ids=scope_target_ids,
        variant_id=variant_id,
        accelerator_id=accelerator_id,
        resolution_scale=resolution_scale,
        second_pass=second_pass,
        seed=seed,
        provider=provider,
    )
    return {
        "storyboard_revision_id": confirmed.id,
        "scope_kind": scope_kind,
        "snapshot_sha256": snap_sha,
        "config": config_json,
        "items": items,
    }


def create_production_run(
    m4_db: Session,
    project: M4Project,
    *,
    actor: str,
    scope_kind: str = "project",
    scope_target_ids: list[str] | None = None,
    variant_id: str = "h3-ref2va-int8-convrot",
    accelerator_id: str = "pdd8",
    resolution_scale: float = 1.0,
    second_pass: dict[str, Any] | None = None,
    seed: int = 77,
    provider: str = "comfyui",
    idempotency_key: str | None = None,
    previous_passed_job_id: str | None = None,
) -> dict[str, Any]:
    confirmed, config_json, items, snap_sha = _build_snapshot_payload(
        m4_db,
        project,
        scope_kind=scope_kind,
        scope_target_ids=scope_target_ids,
        variant_id=variant_id,
        accelerator_id=accelerator_id,
        resolution_scale=resolution_scale,
        second_pass=second_pass,
        seed=seed,
        provider=provider,
        previous_passed_job_id=previous_passed_job_id,
    )

    idem = (idempotency_key or "").strip() or f"m4run:{project.id}:{snap_sha[:24]}"
    existing = m4_db.scalar(
        select(M4ProductionRun).where(
            M4ProductionRun.project_id == project.id,
            M4ProductionRun.idempotency_key == idem,
        )
    )
    if existing is not None:
        jobs = m4_db.scalars(select(M4Job).where(M4Job.run_id == existing.id).order_by(M4Job.ordinal)).all()
        return _serialize_run(existing, [_serialize_job(j) for j in jobs])

    active_run = m4_db.scalar(
        select(M4ProductionRun)
        .where(
            M4ProductionRun.project_id == project.id,
            M4ProductionRun.status.in_(["PENDING", "CLAIMED", "RUNNING"]),
        )
        .limit(1)
    )
    if active_run is not None:
        raise HTTPException(
            409,
            {
                "code": "M4_RUN_ALREADY_ACTIVE",
                "run_id": active_run.id,
                "message": f"项目已有正在执行的排片批次（{active_run.status}），请先等待完成或暂停/取消。",
            },
        )

    snapshot = M4ExecutionSnapshot(
        project_id=project.id,
        storyboard_revision_id=confirmed.id,
        snapshot_sha256=snap_sha,
        config_json=config_json,
        items_json=items,
        created_by=actor,
    )
    m4_db.add(snapshot)
    m4_db.flush()

    run = M4ProductionRun(
        project_id=project.id,
        storyboard_revision_id=confirmed.id,
        execution_snapshot_id=snapshot.id,
        idempotency_key=idem,
        scope_kind=scope_kind,
        scope_target_ids=list(scope_target_ids or []),
        provider=provider,
        execution_generation=2,
        status="PENDING",
    )
    m4_db.add(run)
    m4_db.flush()

    prev_episode_id: str | None = None
    prev_job_id: str | None = None
    created_jobs: list[M4Job] = []
    for item_idx, item in enumerate(items):
        job_idem = f"{run.id}:s{item['shot_id']}:o{item['ordinal']}"
        if item["continuity_mode"] == "last_frame":
            if item_idx == 0:
                eff_prev_job_id = previous_passed_job_id
            elif prev_episode_id == item["episode_id"]:
                eff_prev_job_id = prev_job_id
            else:
                eff_prev_job_id = None
        else:
            eff_prev_job_id = None
        job = M4Job(
            project_id=project.id,
            run_id=run.id,
            episode_id=item["episode_id"],
            shot_id=item["shot_id"],
            shot_revision=item["shot_revision"],
            ordinal=item["ordinal"],
            idempotency_key=job_idem,
            prompt_revision_id=item["prompt_revision_id"],
            prompt_sha256=item["prompt_sha256"],
            reference_manifest_sha256=item["reference_manifest_sha256"],
            target_seconds=item["target_seconds"],
            continuity_mode=item["continuity_mode"],
            previous_job_id=eff_prev_job_id,
            provider=provider,
            execution_generation=2,
            status="QUEUED",
            state_version=1,
        )
        m4_db.add(job)
        m4_db.flush()
        created_jobs.append(job)
        prev_episode_id = item["episode_id"]
        prev_job_id = job.id

    m4_db.add(
        M4AuditEvent(
            project_id=project.id,
            actor=actor,
            stage="production",
            event_type="M4_RUN_CREATED",
            entity_type="m4_production_run",
            entity_id=run.id,
            payload={
                "snapshot_id": snapshot.id,
                "snapshot_sha256": snap_sha,
                "jobs_count": len(created_jobs),
                "scope_kind": scope_kind,
            },
        )
    )
    return _serialize_run(run, [_serialize_job(j) for j in created_jobs])


def create_retake_job_run(
    m4_db: Session,
    project: M4Project,
    shot_id: str,
    *,
    actor: str,
    variant_id: str = "h3-ref2va-int8-convrot",
    accelerator_id: str = "pdd8",
    resolution_scale: float = 1.0,
    second_pass: dict[str, Any] | None = None,
    seed: int = 99,
    provider: str = "comfyui",
    previous_passed_job_id: str | None = None,
) -> dict[str, Any]:
    """Schedule a single-shot retake run, preserving all historical passed jobs & artifacts."""
    shot = m4_db.get(M4Shot, shot_id)
    if shot is None or shot.project_id != project.id:
        raise HTTPException(404, {"code": "M4_SHOT_NOT_FOUND", "message": "镜头不存在。"})

    prev_passed_id: str | None = None
    if shot.continuity_mode == "last_frame":
        if previous_passed_job_id is not None:
            explicit_prev = m4_db.get(M4Job, previous_passed_job_id)
            if (
                explicit_prev is None
                or explicit_prev.project_id != project.id
                or explicit_prev.episode_id != shot.episode_id
                or explicit_prev.status != "PASSED"
                or not explicit_prev.output_storage_path
            ):
                raise HTTPException(
                    409,
                    {
                        "code": "M4_CONTINUITY_SOURCE_INVALID",
                        "message": "指定的续帧源任务无效、非同一集或未通过质检。",
                    },
                )
            prev_passed_id = explicit_prev.id
        else:
            prev_shot = m4_db.scalar(
                select(M4Shot).where(
                    M4Shot.storyboard_revision_id == shot.storyboard_revision_id,
                    M4Shot.episode_id == shot.episode_id,
                    M4Shot.shot_no < shot.shot_no,
                ).order_by(M4Shot.shot_no.desc()).limit(1)
            )
            if prev_shot is not None:
                prev_prompt = m4_db.scalar(
                    select(M4ManualPromptRevision)
                    .where(
                        M4ManualPromptRevision.shot_id == prev_shot.id,
                        M4ManualPromptRevision.status == "FROZEN",
                    )
                    .order_by(M4ManualPromptRevision.revision.desc())
                    .limit(1)
                )
                ok_prev_prompt, _ = validate_frozen_prompt_for_shot(m4_db, prev_shot, prev_prompt)
                prev_manifest_sha = _canonical_sha(build_shot_reference_manifest(m4_db, prev_shot))
                candidates = m4_db.scalars(
                    select(M4Job)
                    .where(
                        M4Job.shot_id == prev_shot.id,
                        M4Job.status == "PASSED",
                        M4Job.shot_revision == prev_shot.revision,
                    )
                    .order_by(M4Job.created_at.desc())
                ).all()
                for cand in candidates:
                    if (
                        ok_prev_prompt
                        and prev_prompt is not None
                        and cand.prompt_sha256 == prev_prompt.prompt_sha256
                        and cand.reference_manifest_sha256 == prev_manifest_sha
                        and cand.output_storage_path
                    ):
                        prev_passed_id = cand.id
                        break
                if prev_passed_id is None:
                    raise HTTPException(
                        409,
                        {
                            "code": "M4_CONTINUITY_SOURCE_MISSING",
                            "message": (
                                f"上一镜 {prev_shot.shot_code} 尚未产出匹配当前版本的通过成片，"
                                "无法按 last_frame 续帧重拍。"
                            ),
                        },
                    )

    unique_key = f"retake:{project.id}:{shot_id}:{m4_utcnow().isoformat()}"
    return create_production_run(
        m4_db,
        project,
        actor=actor,
        scope_kind="shot",
        scope_target_ids=[shot_id],
        variant_id=variant_id,
        accelerator_id=accelerator_id,
        resolution_scale=resolution_scale,
        second_pass=second_pass,
        seed=seed,
        provider=provider,
        idempotency_key=unique_key,
        previous_passed_job_id=prev_passed_id,
    )


def control_production_run(
    m4_db: Session,
    project: M4Project,
    run_id: str,
    *,
    actor: str,
    action: str,
) -> dict[str, Any]:
    run = m4_db.get(M4ProductionRun, run_id)
    if run is None or run.project_id != project.id:
        raise HTTPException(404, {"code": "M4_RUN_NOT_FOUND", "message": "排片批次不存在。"})
    act = (action or "").strip().lower()
    now = m4_utcnow()
    if act == "pause":
        if run.status not in {"PENDING", "CLAIMED", "RUNNING"}:
            raise HTTPException(409, {"code": "M4_RUN_CANNOT_PAUSE", "message": f"状态 {run.status} 不可暂停。"})
        run.status = "PAUSED"
    elif act == "resume":
        if run.status != "PAUSED":
            raise HTTPException(409, {"code": "M4_RUN_CANNOT_RESUME", "message": "仅 PAUSED 状态可恢复继续跑。"})
        run.status = "PENDING"
    elif act == "cancel":
        if run.status in {"COMPLETED", "CANCELLED"}:
            raise HTTPException(409, {"code": "M4_RUN_ALREADY_TERMINAL", "message": "批次已结束。"})
        run.status = "CANCELLED"
        for job in m4_db.scalars(select(M4Job).where(M4Job.run_id == run.id, M4Job.status == "QUEUED")).all():
            job.status = "CANCELLED"
            job.state_version += 1
            job.updated_at = now
    else:
        raise HTTPException(422, {"code": "M4_RUN_ACTION_INVALID", "message": f"不支持的批次控制动作：{act}"})
    run.updated_at = now
    m4_db.add(
        M4AuditEvent(
            project_id=project.id,
            actor=actor,
            stage="production",
            event_type=f"M4_RUN_{act.upper()}",
            entity_type="m4_production_run",
            entity_id=run.id,
            payload={"status": run.status},
        )
    )
    jobs = m4_db.scalars(select(M4Job).where(M4Job.run_id == run.id).order_by(M4Job.ordinal)).all()
    return _serialize_run(run, [_serialize_job(j) for j in jobs])


def reconcile_m4_job(
    m4_db: Session,
    project: M4Project,
    job_id: str,
    *,
    actor: str,
    decision: str,
) -> dict[str, Any]:
    """Explicit reconciliation of a NEEDS_REVIEW job (never blind-resubmit without operator choice)."""
    job = m4_db.get(M4Job, job_id)
    if job is None or job.project_id != project.id:
        raise HTTPException(404, {"code": "M4_JOB_NOT_FOUND", "message": "镜头任务不存在。"})
    if job.status != "NEEDS_REVIEW":
        raise HTTPException(
            409,
            {"code": "M4_JOB_NOT_NEEDS_REVIEW", "message": f"当前任务状态为 {job.status}，无需对账。"},
        )

    dec = (decision or "").strip().lower()
    now = m4_utcnow()
    if dec == "requeue":
        job.status = "QUEUED"
        job.failure_code = None
        job.failure_detail = ""
        job.state_version += 1
        job.updated_at = now
        run = m4_db.get(M4ProductionRun, job.run_id)
        if run is not None and run.status in {"FAILED", "NEEDS_REVIEW"}:
            run.status = "PENDING"
            run.updated_at = now
    elif dec == "mark_failed":
        job.status = "FAILED"
        job.state_version += 1
        job.updated_at = now
    else:
        raise HTTPException(
            422,
            {"code": "M4_RECONCILE_DECISION_INVALID", "message": "仅支持 requeue 或 mark_failed。"},
        )

    m4_db.add(
        M4AuditEvent(
            project_id=project.id,
            actor=actor,
            stage="production",
            event_type="M4_JOB_RECONCILED",
            entity_type="m4_job",
            entity_id=job.id,
            payload={"decision": dec, "new_status": job.status},
        )
    )
    return _serialize_job(job)
