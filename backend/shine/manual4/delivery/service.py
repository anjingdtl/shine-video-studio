"""Manual4 Stage 4 episode delivery assembly, versioning & human acceptance."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from shine.manual4.delivery.assembler import M4ClipSpec, assemble_m4_episode_reel, build_episode_srt
from shine.manual4.status.readiness import evaluate_m4_readiness
from shine.manual4.storage.files import safe_m4_path, sha256_file, store_m4_bytes
from shine.manual4.storage.models import (
    M4AuditEvent,
    M4Delivery,
    M4DeliveryAcceptance,
    M4EpisodePlan,
    M4Job,
    M4Project,
    M4Shot,
    M4StoryboardRevision,
)

ACCEPTANCE_DECISIONS = {"accepted", "rejected"}


def _canonical_sha(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _serialize_acceptance(row: M4DeliveryAcceptance | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "delivery_id": row.delivery_id,
        "decision": row.decision,
        "watched": bool(row.watched),
        "evidence": row.evidence,
        "delivery_sha256": row.delivery_sha256,
        "actor": row.actor,
        "request_id": row.request_id,
        "created_at": row.created_at,
    }


def _serialize_delivery(row: M4Delivery, acceptance: M4DeliveryAcceptance | None = None) -> dict[str, Any]:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "run_id": row.run_id,
        "episode_id": row.episode_id,
        "version": row.version,
        "status": row.status,
        "shots_digest": row.shots_digest,
        "edl_json": row.edl_json,
        "sha256": row.sha256,
        "byte_size": row.byte_size,
        "duration_s": row.duration_s,
        "width": row.width,
        "height": row.height,
        "fps": row.fps,
        "is_mock": bool(row.is_mock),
        "has_subtitles": bool((row.subtitle_srt or "").strip()),
        "error_code": row.error_code,
        "error_detail": row.error_detail,
        "media_url": f"/api/v1/manual4/projects/{row.project_id}/deliveries/{row.id}/media" if row.sha256 else None,
        "subtitle_url": (
            f"/api/v1/manual4/projects/{row.project_id}/deliveries/{row.id}/subtitles.srt"
            if (row.subtitle_srt or "").strip()
            else None
        ),
        "acceptance": _serialize_acceptance(acceptance),
        "created_at": row.created_at,
    }


def get_delivery_state(m4_db: Session, project: M4Project) -> dict[str, Any]:
    confirmed = m4_db.scalar(
        select(M4StoryboardRevision)
        .where(M4StoryboardRevision.project_id == project.id, M4StoryboardRevision.status == "CONFIRMED")
        .order_by(M4StoryboardRevision.revision.desc())
        .limit(1)
    )
    deliveries = m4_db.scalars(
        select(M4Delivery)
        .where(M4Delivery.project_id == project.id)
        .order_by(M4Delivery.version.desc(), M4Delivery.created_at.desc())
    ).all()
    acc_by_deliv: dict[str, M4DeliveryAcceptance] = {}
    if deliveries:
        for acc in m4_db.scalars(
            select(M4DeliveryAcceptance)
            .where(M4DeliveryAcceptance.delivery_id.in_([d.id for d in deliveries]))
            .order_by(M4DeliveryAcceptance.created_at.asc())
        ).all():
            acc_by_deliv[acc.delivery_id] = acc

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
        passed_jobs_by_shot: dict[str, list[M4Job]] = {}
        if shot_ids:
            for job in m4_db.scalars(
                select(M4Job)
                .where(
                    M4Job.project_id == project.id,
                    M4Job.shot_id.in_(shot_ids),
                    M4Job.status == "PASSED",
                )
                .order_by(M4Job.created_at.desc())
            ).all():
                passed_jobs_by_shot.setdefault(job.shot_id, []).append(job)

        for ep in episodes:
            ep_shots = [s for s in shots if s.episode_id == ep.id]
            ep_deliveries = [
                _serialize_delivery(d, acc_by_deliv.get(d.id))
                for d in deliveries
                if d.episode_id == ep.id
            ]
            shot_options = [
                {
                    "shot_id": s.id,
                    "shot_code": s.shot_code,
                    "target_seconds": s.target_seconds,
                    "passed_jobs": [
                        {
                            "id": j.id,
                            "output_sha256": j.output_sha256,
                            "measured_duration_s": j.measured_duration_s,
                            "provider": j.provider,
                            "created_at": j.created_at,
                        }
                        for j in passed_jobs_by_shot.get(s.id, [])
                    ],
                }
                for s in ep_shots
            ]
            episodes_data.append(
                {
                    "id": ep.id,
                    "episode_no": ep.episode_no,
                    "episode_code": ep.episode_code,
                    "title": ep.title,
                    "ready_to_assemble": all(len(so["passed_jobs"]) > 0 for so in shot_options),
                    "shots": shot_options,
                    "deliveries": ep_deliveries,
                }
            )

    verdict = evaluate_m4_readiness(m4_db, project)
    return {
        "confirmed_revision": {"id": confirmed.id, "revision": confirmed.revision} if confirmed else None,
        "episodes": episodes_data,
        "deliveries": [_serialize_delivery(d, acc_by_deliv.get(d.id)) for d in deliveries],
        "readiness": verdict.model_dump(),
    }


def assemble_episode_delivery(
    m4_db: Session,
    m4_root: Path,
    project: M4Project,
    episode_id: str,
    *,
    actor: str,
    job_overrides: dict[str, str] | None = None,
    include_subtitles: bool = True,
    burn_subtitles: bool = False,
    assembler_stub: Any = None,
) -> dict[str, Any]:
    confirmed = m4_db.scalar(
        select(M4StoryboardRevision)
        .where(M4StoryboardRevision.project_id == project.id, M4StoryboardRevision.status == "CONFIRMED")
        .order_by(M4StoryboardRevision.revision.desc())
        .limit(1)
    )
    if confirmed is None:
        raise HTTPException(409, {"code": "M4_STORYBOARD_NOT_CONFIRMED", "message": "请先在①确认分镜计划。"})

    ep = m4_db.get(M4EpisodePlan, episode_id)
    if ep is None or ep.project_id != project.id or ep.storyboard_revision_id != confirmed.id:
        raise HTTPException(404, {"code": "M4_EPISODE_NOT_FOUND", "message": "未找到目标分集。"})

    shots = m4_db.scalars(
        select(M4Shot).where(M4Shot.episode_id == ep.id).order_by(M4Shot.shot_no.asc())
    ).all()
    if not shots:
        raise HTTPException(409, {"code": "M4_EPISODE_EMPTY", "message": "分集内没有任何镜头。"})

    overrides = dict(job_overrides or {})
    clips_with_dialogue: list[tuple[M4ClipSpec, list[dict[str, Any]], M4Job]] = []
    for sh in shots:
        chosen: M4Job | None = None
        if sh.id in overrides:
            chosen = m4_db.get(M4Job, overrides[sh.id])
            if chosen is None or chosen.shot_id != sh.id or chosen.status != "PASSED":
                raise HTTPException(
                    409,
                    {
                        "code": "M4_OVERRIDE_JOB_NOT_PASSED",
                        "message": f"镜头 {sh.shot_code} 指定的替代任务未通过质检。",
                    },
                )
        else:
            chosen = m4_db.scalar(
                select(M4Job)
                .where(
                    M4Job.project_id == project.id,
                    M4Job.shot_id == sh.id,
                    M4Job.status == "PASSED",
                )
                .order_by(M4Job.created_at.desc(), M4Job.ordinal.desc())
                .limit(1)
            )
        if chosen is None or not chosen.output_storage_path:
            raise HTTPException(
                409,
                {
                    "code": "M4_DELIVERY_MISSING_PASSED_SHOT",
                    "shot_code": sh.shot_code,
                    "message": f"镜头 {sh.shot_code} 尚无通过质检的视频产物，不能合成成片。",
                },
            )
        dur = float(chosen.measured_duration_s or sh.target_seconds)
        clip = M4ClipSpec(
            job_id=chosen.id,
            shot_id=sh.id,
            shot_code=sh.shot_code,
            storage_path=chosen.output_storage_path,
            expected_sha256=chosen.output_sha256,
            in_s=0.0,
            out_s=dur,
            target_width=int(chosen.measured_width or 1344),
            target_height=int(chosen.measured_height or 768),
        )
        clips_with_dialogue.append((clip, list(sh.dialogue or []), chosen))

    srt_text = build_episode_srt([(c, d) for c, d, _j in clips_with_dialogue]) if include_subtitles else ""
    edl = [
        {
            "ordinal": idx,
            "shot_id": c.shot_id,
            "shot_code": c.shot_code,
            "job_id": c.job_id,
            "sha256": c.expected_sha256,
            "in_s": c.in_s,
            "out_s": c.out_s,
        }
        for idx, (c, _d, _j) in enumerate(clips_with_dialogue)
    ]
    shots_digest = _canonical_sha({"edl": edl, "burn_subtitles": burn_subtitles, "srt": srt_text})
    is_mock = any(j.provider == "mock" for _c, _d, j in clips_with_dialogue)

    existing_same = m4_db.scalar(
        select(M4Delivery)
        .where(
            M4Delivery.project_id == project.id,
            M4Delivery.episode_id == ep.id,
            M4Delivery.shots_digest == shots_digest,
            M4Delivery.status == "DELIVERED",
        )
        .order_by(M4Delivery.version.desc())
        .limit(1)
    )
    if existing_same is not None:
        return _serialize_delivery(existing_same)

    latest_ver = m4_db.scalar(
        select(M4Delivery.version)
        .where(M4Delivery.project_id == project.id, M4Delivery.episode_id == ep.id)
        .order_by(M4Delivery.version.desc())
        .limit(1)
    )
    next_ver = int(latest_ver or 0) + 1
    out_filename = f"{ep.episode_code}_v{next_ver}.mp4"

    if assembler_stub is not None:
        stub_res = assembler_stub(
            episode_code=ep.episode_code,
            clips=[c for c, _d, _j in clips_with_dialogue],
            subtitle_srt=srt_text,
        )
        raw_bytes = stub_res["bytes"] if isinstance(stub_res, dict) else bytes(stub_res)
        rel_path, digest, _abs = store_m4_bytes(m4_root, project.id, "deliveries", out_filename, raw_bytes)
        out_meta = {
            "storage_path": rel_path,
            "sha256": digest,
            "byte_size": len(raw_bytes),
            "duration_s": round(sum(c.duration_s for c, _d, _j in clips_with_dialogue), 3),
            "width": clips_with_dialogue[0][0].target_width,
            "height": clips_with_dialogue[0][0].target_height,
            "fps": 24.0,
        }
        is_mock = bool(stub_res.get("is_mock", True)) if isinstance(stub_res, dict) else True
    else:
        out_meta = assemble_m4_episode_reel(
            m4_root,
            project.id,
            [c for c, _d, _j in clips_with_dialogue],
            out_filename=out_filename,
            subtitle_srt=srt_text,
            burn_subtitles=burn_subtitles,
        )

    delivery = M4Delivery(
        project_id=project.id,
        run_id=clips_with_dialogue[-1][2].run_id,
        episode_id=ep.id,
        version=next_ver,
        idempotency_key=f"m4deliv:{project.id}:{ep.id}:v{next_ver}",
        edl_json=edl,
        shots_digest=shots_digest,
        status="DELIVERED",
        storage_path=out_meta["storage_path"],
        sha256=out_meta["sha256"],
        byte_size=out_meta["byte_size"],
        duration_s=out_meta["duration_s"],
        width=out_meta["width"],
        height=out_meta["height"],
        fps=out_meta["fps"],
        is_mock=is_mock,
        subtitle_srt=srt_text,
    )
    m4_db.add(delivery)
    m4_db.flush()

    m4_db.add(
        M4AuditEvent(
            project_id=project.id,
            actor=actor,
            stage="delivery",
            episode_id=ep.id,
            event_type="M4_DELIVERY_ASSEMBLED",
            entity_type="m4_delivery",
            entity_id=delivery.id,
            payload={
                "episode_code": ep.episode_code,
                "version": next_ver,
                "sha256": out_meta["sha256"],
                "is_mock": is_mock,
            },
        )
    )
    return _serialize_delivery(delivery)


def record_delivery_acceptance(
    m4_db: Session,
    m4_root: Path,
    project: M4Project,
    delivery_id: str,
    *,
    actor: str,
    decision: str,
    watched: bool,
    evidence: str,
    expected_sha256: str,
    request_id: str,
) -> dict[str, Any]:
    dec = (decision or "").strip().lower()
    if dec not in ACCEPTANCE_DECISIONS:
        raise HTTPException(422, {"code": "M4_ACCEPTANCE_DECISION_INVALID", "message": "仅支持 accepted 或 rejected。"})

    delivery = m4_db.get(M4Delivery, delivery_id)
    if delivery is None or delivery.project_id != project.id:
        raise HTTPException(404, {"code": "M4_DELIVERY_NOT_FOUND", "message": "成片交付版本不存在。"})
    if delivery.status != "DELIVERED" or not delivery.storage_path or not delivery.sha256:
        raise HTTPException(409, {"code": "M4_DELIVERY_NOT_READY", "message": "成片尚未合成成功，无法验收。"})
    if delivery.is_mock:
        raise HTTPException(
            409,
            {"code": "M4_MOCK_DELIVERY_NOT_ACCEPTABLE", "message": "Mock 演示合成件不允许记为正式人工验收通过。"},
        )
    if expected_sha256 != delivery.sha256:
        raise HTTPException(
            409,
            {"code": "M4_DELIVERY_VERSION_CHANGED", "message": "成片文件校验哈希已变化，请刷新观看后再验收。"},
        )
    abs_file = safe_m4_path(m4_root, delivery.storage_path)
    if not abs_file.is_file() or sha256_file(abs_file) != delivery.sha256:
        raise HTTPException(
            409,
            {"code": "M4_DELIVERY_FILE_CORRUPTED", "message": "成片磁盘文件缺失或哈希与台账不符，禁止验收。"},
        )
    if m4_db.scalar(select(M4DeliveryAcceptance.id).where(M4DeliveryAcceptance.request_id == request_id)):
        raise HTTPException(409, {"code": "M4_ACCEPTANCE_REQUEST_REPLAYED", "message": "重复的验收请求 ID。"})
    if dec == "accepted" and not watched:
        raise HTTPException(
            422,
            {"code": "M4_ACCEPTANCE_REQUIRES_WATCHED", "message": "请勾选「已完整观看成片」后再确认通过验收。"},
        )
    if dec == "rejected" and not (evidence or "").strip():
        raise HTTPException(422, {"code": "M4_REJECTION_REQUIRES_REASON", "message": "驳回验收必须填写具体原因。"})

    acc = M4DeliveryAcceptance(
        project_id=project.id,
        delivery_id=delivery.id,
        decision=dec,
        watched=bool(watched),
        evidence=(evidence or "").strip(),
        delivery_sha256=delivery.sha256,
        actor=actor,
        request_id=request_id,
    )
    m4_db.add(acc)
    m4_db.flush()

    m4_db.add(
        M4AuditEvent(
            project_id=project.id,
            actor=actor,
            stage="delivery",
            episode_id=delivery.episode_id,
            event_type=f"M4_DELIVERY_{dec.upper()}",
            entity_type="m4_delivery",
            entity_id=delivery.id,
            payload={"version": delivery.version, "sha256": delivery.sha256, "watched": bool(watched)},
        )
    )
    return _serialize_acceptance(acc)
