"""Assemble one episode whose clips come from several production series.

A series order publishes one reel per series.  An episode of a whole-film run is
different: its clips came from more than one series, and publishing each series
as its own reel would be a duplicate release of "the episode".  This module owns
that second scope.  It builds the ordered EDL from the run's plan (not from
creation time), verifies every source identity and byte hash before assembly,
and refuses to publish when a required shot is missing.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import timedelta
from pathlib import Path
from typing import Any

from pydantic import Field, model_validator
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from shine.director.contracts import episode_video_seconds
from shine.media.assembly import DeliveryAssembler, DeliveryClip, DeliveryError
from shine.media.delivery_engine import (
    ASSEMBLY_LEASE_SECONDS,
    AssemblyHeartbeat,
    DeliveryBlock,
    DeliveryClaim,
    _write_back,
)
from shine.media.delivery_models import DeliveryOrder
from shine.models import Job, MediaArtifact, Shot, SubtitleTrack, utcnow
from shine.production.models import ProductionRun, ProductionRunBatch, ProductionRunEpisode
from shine.schemas import APIModel
from shine.security import sha256_bytes

EPISODE_RULES_VERSION = "run-episode-cuts-v2"
EPISODE_PAGE_SIZE = 100
MIN_CUT_SECONDS = 0.5


class EpisodeCut(APIModel):
    """One confirmed cut: which part of a take this episode really uses."""

    shot_id: str = Field(min_length=1, max_length=36)
    in_s: float = Field(ge=0, le=600, allow_inf_nan=False)
    out_s: float = Field(gt=0, le=600, allow_inf_nan=False)


class EpisodePostContract(APIModel):
    """The post-production contract of one episode.

    It only ever changes how already-produced takes are assembled — cut points
    and the subtitle layer — so a revision is a new delivery version and never
    a new render.  Audio mixing is not implemented by this build's assembler,
    so asking for it is refused by name instead of being silently ignored.
    """

    schema_version: str = Field(default="1.0", pattern="^1\\.0$")
    cuts: list[EpisodeCut] = Field(default_factory=list, max_length=400)
    subtitle_revision: int | None = Field(default=None, ge=1)
    audio_gain_db: float | None = Field(default=None, allow_inf_nan=False)

    @model_validator(mode="after")
    def coherent(self) -> EpisodePostContract:
        if self.audio_gain_db is not None:
            raise ValueError("EDIT_AUDIO_NOT_SUPPORTED")
        seen: set[str] = set()
        for cut in self.cuts:
            if cut.shot_id in seen:
                raise ValueError(f"EDIT_CUT_REPEATED:{cut.shot_id}")
            seen.add(cut.shot_id)
            if cut.out_s - cut.in_s < MIN_CUT_SECONDS:
                raise ValueError(f"EDIT_CUT_TOO_SHORT:{cut.shot_id}")
        return self


def normalized_post_contract(
    db: Session, episode: ProductionRunEpisode, body: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Validate a contract against this episode and pin its subtitle identity."""
    from fastapi import HTTPException
    from pydantic import ValidationError

    if not body:
        return None
    try:
        contract = EpisodePostContract.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(422, {
            "code": "EPISODE_POST_CONTRACT_INVALID",
            "problems": [str(error.get("msg", ""))[:200] for error in exc.errors()][:20],
        }) from exc
    foreign = [cut.shot_id for cut in contract.cuts if cut.shot_id not in episode.shot_ids]
    if foreign:
        raise HTTPException(422, {"code": "EDIT_CUT_SHOT_NOT_IN_EPISODE", "shot_ids": foreign[:20]})
    pinned: dict[str, Any] = contract.model_dump(mode="json")
    pinned.pop("audio_gain_db", None)
    if contract.subtitle_revision is not None:
        track = db.scalar(
            select(SubtitleTrack).where(
                SubtitleTrack.project_id == episode.project_id,
                SubtitleTrack.revision == contract.subtitle_revision,
            )
        )
        if track is None or track.status != "APPROVED":
            raise HTTPException(409, "EPISODE_SUBTITLE_NOT_APPROVED")
        from shine.director.subtitles import track_matches_episode

        if not track_matches_episode(track, episode.episode_id):
            # A revision compiled for another episode's reel has its own zero
            # point; compositing it here would put the wrong words on the picture.
            raise HTTPException(409, {
                "code": "EPISODE_SUBTITLE_EPISODE_MISMATCH",
                "message": "这一版字幕是为另一集生成的，不能用于本集",
                "track_episode_id": track.episode_id,
            })
        pinned["subtitle_sha256"] = track.sha256
    return pinned


def post_contract_digest(episode: ProductionRunEpisode) -> str | None:
    return (
        hashlib.sha256(json.dumps(episode.post_contract, ensure_ascii=False, sort_keys=True)
                       .encode("utf-8")).hexdigest()
        if episode.post_contract else None
    )


def episode_input_digest(
    db: Session,
    run: ProductionRun,
    episode: ProductionRunEpisode,
    batches: list[ProductionRunBatch],
) -> str:
    """The content identity of one episode version.

    It is built from the plan order plus the real passed artifacts and their
    hashes, so a re-take, a re-cut or a re-ordered plan each produce a different
    version and never silently overwrite a published reel.  Only jobs this
    run's own series items produced are part of the identity: an unrelated
    newer take never re-identifies somebody else's reel.
    """
    from shine.comfyops.relay_stage import relay_artifact_for_job
    from shine.media.subtitles import subtitle_font_size
    from shine.production.runner import episode_current_jobs

    passed = episode_current_jobs(db, run, episode)
    clips: list[list[str]] = []
    for shot_id in episode.shot_ids:
        job = passed.get(shot_id)
        entry = [shot_id, (job.id if job else ""), (job.artifact_id if job else "")]
        if job is not None:
            relay = relay_artifact_for_job(db, job.id)
            if relay is not None:
                entry.append(relay.id)
        clips.append(entry)
    payload = {
        "run_id": run.id,
        "episode_id": episode.episode_id,
        "manifest_sha256": episode.manifest_sha256,
        # A cut or subtitle revision is a new delivery identity of the same takes.
        "post_contract": post_contract_digest(episode),
        # Font is a presentation fact of the burn; changing it re-identifies the
        # reel so the next apply re-burns instead of reusing the old file.
        "subtitle_font_size": subtitle_font_size(db, run.project_id),
        "batches": [
            [batch.batch_key, batch.input_digest, batch.series_id]
            for batch in sorted(batches, key=lambda row: row.batch_key)
        ],
        "clips": clips,
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _passed_jobs(db: Session, shot_ids: list[str]) -> dict[str, Job]:
    """The passed jobs of these shots, newest first by creation order.

    Kept only for callers that have no run scope of their own.  Whole-film
    episodes must NOT use this: a job that no series item of the owning run
    points at is somebody else's product (see ``run_owned_passed_jobs``).
    """
    from shine.models import Shot

    if not shot_ids:
        return {}
    shots = {shot.id: shot for shot in db.scalars(select(Shot).where(Shot.id.in_(shot_ids))).all()}
    jobs = db.scalars(
        select(Job)
        .where(Job.shot_id.in_(shot_ids), Job.status == "PASSED", Job.stale.is_(False))
        .order_by(Job.created_at)
    ).all()
    passed: dict[str, Job] = {}
    for job in jobs:
        shot = shots.get(job.shot_id)
        if shot is None or shot.revision != job.shot_revision or not job.artifact_id:
            continue
        passed[job.shot_id] = job
    return passed


def build_episode_edl(
    db: Session,
    order: DeliveryOrder,
    *,
    ffprobe_path: str = "ffprobe",
    ffmpeg_path: str = "ffmpeg",
    asset_root: Path,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    """The ordered edit decision list of one episode, from real passed takes."""
    from shine.media.artifacts import safe_asset_path
    from shine.media.probe import probe_file

    episode = db.get(ProductionRunEpisode, order.scope_id or "")
    if episode is None or episode.project_id != order.project_id:
        raise DeliveryBlock("EPISODE_SCOPE_MISSING")
    run = db.get(ProductionRun, episode.run_id)
    if run is None:
        raise DeliveryBlock("EPISODE_RUN_MISSING")

    from shine.comfyops.relay_stage import relay_artifact_for_job, run_upscale_enabled
    from shine.director.models import DirectorPackageVersion
    from shine.production.runner import episode_current_jobs

    package = db.get(DirectorPackageVersion, run.package_id)
    upscale_required = run_upscale_enabled(package.manifest if package else None)

    passed = episode_current_jobs(db, run, episode)
    missing = [shot_id for shot_id in episode.shot_ids if shot_id not in passed]
    if missing:
        raise DeliveryBlock("EPISODE_SHOT_NOT_PASSED")

    cuts = {cut["shot_id"]: cut for cut in (episode.post_contract or {}).get("cuts", [])}
    clips: list[dict[str, Any]] = []
    for position, shot_id in enumerate(episode.shot_ids):
        job = passed[shot_id]
        attempts = list(job.attempts)
        if not attempts:
            raise DeliveryBlock("EPISODE_SHOT_ATTEMPT_MISSING")
        attempt = max(attempts, key=lambda row: row.attempt_no)
        # The shot's approved highest stage wins: a passed RelayVSR X2 upscale
        # artifact is required when upscale was frozen in the package strategy.
        relay_artifact = relay_artifact_for_job(db, job.id)
        if upscale_required and relay_artifact is None:
            raise DeliveryBlock("EPISODE_UPSCALE_ARTIFACT_MISSING")
        artifact = relay_artifact or (db.get(MediaArtifact, job.artifact_id) if job.artifact_id else None)
        if artifact is None or artifact.is_mock:
            raise DeliveryBlock("EPISODE_SOURCE_NOT_REAL_VIDEO")
        if artifact.artifact_kind != "video":
            raise DeliveryBlock("EPISODE_SOURCE_IDENTITY_MISMATCH")
        if relay_artifact is None:
            if artifact.attempt_id != attempt.id or artifact.sha256 != attempt.output_sha256:
                raise DeliveryBlock("EPISODE_SOURCE_IDENTITY_MISMATCH")
        path = safe_asset_path(asset_root, artifact.storage_path)
        if not path.is_file():
            raise DeliveryBlock("EPISODE_SOURCE_FILE_MISSING")
        if sha256_bytes(path.read_bytes()) != artifact.sha256:
            raise DeliveryBlock("EPISODE_SOURCE_FILE_HASH_MISMATCH")
        info = probe_file(path, ffprobe_path=ffprobe_path, ffmpeg_path=ffmpeg_path)
        if info.duration_s is None:
            raise DeliveryBlock("EPISODE_SOURCE_DURATION_UNKNOWN")
        duration = round(info.duration_s, 3)
        cut = cuts.get(shot_id)
        if cut is not None:
            # A confirmed cut must fit the real take: the contract is checked
            # against the measured media, never trusted as a number on paper.
            if cut["out_s"] > duration + 0.05 or cut["in_s"] >= cut["out_s"]:
                raise DeliveryBlock("EPISODE_CUT_OUTSIDE_TAKE")
            in_s, out_s = round(cut["in_s"], 3), round(min(cut["out_s"], duration), 3)
            reason = f"{episode.episode_id} 第 {position + 1} 镜：按确认的剪辑合同取 {in_s}–{out_s}s"
        else:
            in_s, out_s = 0.0, duration
            reason = f"{episode.episode_id} 第 {position + 1} 镜：当前通过的真实产物，整条使用"
        clips.append(
            {
                "job_id": job.id,
                "attempt_id": attempt.id,
                "artifact_id": artifact.id,
                "shot_id": shot_id,
                "in_s": in_s,
                "out_s": out_s,
                "transition": "CUT",
                "reason": reason,
            }
        )
    if not clips:
        raise DeliveryBlock("EPISODE_EMPTY_EDIT")
    rules = {
        "rules_version": EPISODE_RULES_VERSION,
        "order": "production_run_episodes.shot_ids",
        "transition": "CUT",
        "cut_source": "post_contract" if cuts else "full_take",
        "post_contract_sha256": post_contract_digest(episode),
        "subtitle_revision": (episode.post_contract or {}).get("subtitle_revision"),
        "episode_id": episode.episode_id,
        "planned_seconds": episode_video_seconds(
            [shot for shot in (db.get(Shot, shot_id) for shot_id in episode.shot_ids or [])
             if shot is not None],
            target_seconds=episode.planned_seconds),
        "edited_seconds": round(sum(clip["out_s"] - clip["in_s"] for clip in clips), 3),
    }
    return {"schema_version": "1.1", "clips": clips}, "run_episode_plan", rules


#: Cut-suggestion thresholds (plan P1-B).  A silent margin larger than these is a
#: *candidate* trim, never an automatic cut: the human confirms through the same
#: EpisodePostContract.  Buffers keep a breath around the spoken line so a trim
#: never lands on the first/last syllable.
SUGGEST_HEAD_MARGIN_S = 1.0
SUGGEST_TAIL_MARGIN_S = 1.5
SUGGEST_HEAD_BUFFER_S = 0.3
SUGGEST_TAIL_BUFFER_S = 0.5
CUT_SUGGESTION_RULES_VERSION = "run-episode-cut-suggestions-v1"


def suggest_episode_cuts(
    db: Session,
    run: ProductionRun,
    episode: ProductionRunEpisode,
    *,
    ffprobe_path: str = "ffprobe",
    ffmpeg_path: str = "ffmpeg",
    asset_root: Path,
) -> dict[str, Any]:
    """Read-only, real-video-aware cut suggestions for one episode (plan P1-B).

    Reads the SAME current passed takes (``episode_current_jobs``) and the SAME
    measured media durations the EDL uses — never the planned seconds.  For each
    shot it proposes one legal continuous ``in_s/out_s`` range that keeps every
    scripted line intact and only trims a large silent head/tail margin, marked
    ``uncertain`` for human review.  It never writes a contract, never assembles,
    and never claims to have judged picture content it could not see: a shot whose
    media cannot be measured is reported ``unavailable``, not guessed.  Applying a
    suggestion goes through the existing ``EpisodePostContract``/EDL/FFmpeg path.
    """
    from shine.media.artifacts import safe_asset_path
    from shine.media.probe import probe_file
    from shine.models import Shot
    from shine.production.runner import episode_current_jobs

    passed = episode_current_jobs(db, run, episode)
    current_cuts = {cut["shot_id"]: cut for cut in (episode.post_contract or {}).get("cuts", [])}
    suggestions: list[dict[str, Any]] = []
    for position, shot_id in enumerate(episode.shot_ids):
        base = {"shot_id": shot_id, "position": position + 1}
        job = passed.get(shot_id)
        if job is None:
            suggestions.append({**base, "status": "not_passed",
                                "reason": "这一镜还没有当前通过的产物，无法建议剪辑"})
            continue
        attempts = list(job.attempts)
        attempt = max(attempts, key=lambda row: row.attempt_no) if attempts else None
        from shine.comfyops.relay_stage import relay_artifact_for_job

        relay_artifact = relay_artifact_for_job(db, job.id)
        artifact = relay_artifact or (db.get(MediaArtifact, job.artifact_id) if job.artifact_id else None)
        if attempt is None or artifact is None or artifact.is_mock or artifact.artifact_kind != "video":
            suggestions.append({**base, "status": "unavailable", "job_id": job.id,
                                "reason": "缺少可核验的真实视频产物"})
            continue
        if relay_artifact is None and (
                artifact.attempt_id != attempt.id or artifact.sha256 != attempt.output_sha256):
            suggestions.append({**base, "status": "unavailable", "job_id": job.id,
                                "reason": "产物身份链不一致"})
            continue
        path = safe_asset_path(asset_root, artifact.storage_path)
        if not path.is_file() or sha256_bytes(path.read_bytes()) != artifact.sha256:
            suggestions.append({**base, "status": "unavailable", "job_id": job.id,
                                "reason": "产物文件缺失或哈希不一致"})
            continue
        info = probe_file(path, ffprobe_path=ffprobe_path, ffmpeg_path=ffmpeg_path)
        if info.duration_s is None:
            suggestions.append({**base, "status": "unavailable", "job_id": job.id,
                                "reason": "无法测得真实时长"})
            continue
        duration = round(info.duration_s, 3)
        shot = db.get(Shot, shot_id)
        dialogue = list((shot.spec or {}).get("dialogue") or []) if shot is not None else []
        lines = sorted(dialogue, key=lambda row: float(row.get("start_s", 0)))
        current = current_cuts.get(shot_id)
        cur_in = round(float(current["in_s"]), 3) if current else 0.0
        cur_out = round(min(float(current["out_s"]), duration), 3) if current else duration

        in_s, out_s = 0.0, duration
        confidence = "verified"
        reasons = [f"真实时长 {duration}s（实测，非计划值）"]
        if lines:
            first_start = float(lines[0].get("start_s", 0))
            last_end = max(float(row.get("end_s", 0)) for row in lines)
            head_margin = first_start
            tail_margin = duration - last_end
            if head_margin > SUGGEST_HEAD_MARGIN_S:
                in_s = round(max(0.0, first_start - SUGGEST_HEAD_BUFFER_S), 3)
                confidence = "uncertain"
                reasons.append(f"台词前静默 {round(head_margin, 2)}s，建议裁到首句前 {SUGGEST_HEAD_BUFFER_S}s")
            if tail_margin > SUGGEST_TAIL_MARGIN_S:
                out_s = round(min(duration, last_end + SUGGEST_TAIL_BUFFER_S), 3)
                confidence = "uncertain"
                reasons.append(f"末句后静默 {round(tail_margin, 2)}s，建议裁到末句后 {SUGGEST_TAIL_BUFFER_S}s")
            reasons.append(f"保留全部台词 {round(first_start, 2)}–{round(last_end, 2)}s")
        else:
            reasons.append("无台词：不凭画面猜测裁剪，整条保留，交由人工看镜决定")
        # A suggestion must stay legal: keep dialogue, fit the take, min span.
        if lines:
            in_s = min(in_s, float(lines[0].get("start_s", 0)))
            out_s = max(out_s, max(float(row.get("end_s", 0)) for row in lines))
        out_s = min(out_s, duration)
        if out_s - in_s < MIN_CUT_SECONDS:
            in_s, out_s, confidence = 0.0, duration, "verified"
            reasons.append("裁剪后过短，回退整条")
        changed = abs(in_s - cur_in) > 0.001 or abs(out_s - cur_out) > 0.001
        suggestions.append({
            **base, "status": "ok", "job_id": job.id, "artifact_id": artifact.id,
            "real_duration_s": duration, "in_s": round(in_s, 3), "out_s": round(out_s, 3),
            "current_in_s": cur_in, "current_out_s": cur_out, "changed": changed,
            "confidence": confidence, "dialogue_line_count": len(lines),
            "reason": "；".join(reasons),
        })
    return {
        "episode_id": episode.episode_id,
        "rules_version": CUT_SUGGESTION_RULES_VERSION,
        "advisory": True,
        "suggestions": suggestions,
        "note": "剪辑建议只读，基于真实产物时长与台词时码；经既有剪辑合同确认后才生效，不自动装配。",
    }


def _claim_order(db: Session, order: DeliveryOrder, worker_id: str | None) -> bool:
    """The same conditional claim as a series order, scoped to this row."""
    now = utcnow()
    read_version = order.state_version
    fresh = db.execute(
        update(DeliveryOrder)
        .where(
            DeliveryOrder.id == order.id,
            DeliveryOrder.status == "PENDING",
            DeliveryOrder.state_version == read_version,
        )
        .values(
            status="ASSEMBLING",
            worker_id=worker_id,
            state_version=DeliveryOrder.state_version + 1,
            attempt_count=DeliveryOrder.attempt_count + 1,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    if fresh.rowcount == 1:
        return True
    cutoff = now - timedelta(seconds=ASSEMBLY_LEASE_SECONDS)
    reclaimed = db.execute(
        update(DeliveryOrder)
        .where(
            DeliveryOrder.id == order.id,
            DeliveryOrder.status == "ASSEMBLING",
            DeliveryOrder.state_version == read_version,
            DeliveryOrder.updated_at <= cutoff,
            DeliveryOrder.attempt_count < DeliveryOrder.max_attempts,
        )
        .values(
            status="ASSEMBLING",
            worker_id=worker_id,
            state_version=DeliveryOrder.state_version + 1,
            attempt_count=DeliveryOrder.attempt_count + 1,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    return reclaimed.rowcount == 1


def _order_status_now(db: Session, order_id: str) -> str:
    row = db.get(DeliveryOrder, order_id)
    return row.status if row else "GONE"


def run_episode_delivery(
    session_factory,
    order_id: str,
    *,
    settings,
    asset_root: Path,
    worker_id: str | None = None,
) -> str:
    """Execute one episode order: plan EDL -> FFmpeg -> registered episode reel."""
    with session_factory.begin() as db:
        order = db.get(DeliveryOrder, order_id)
        if order is None:
            return "GONE"
        if order.scope_kind != "episode":
            return order.status
        read_version = order.state_version
        if not _claim_order(db, order, worker_id):
            db.refresh(order)
            return order.status
        claim = DeliveryClaim(owner=worker_id, version=read_version + 1)
        project_id = order.project_id
        episode_scope_id = order.scope_id
        attempt = order.attempt_count + 1
        max_attempts = order.max_attempts

    assembler = DeliveryAssembler(
        asset_root=asset_root,
        ffmpeg_path=settings.ffmpeg_path,
        ffprobe_path=settings.ffprobe_path,
    )
    try:
        with session_factory() as db:
            order = db.get(DeliveryOrder, order_id)
            edl, edl_source, rules = build_episode_edl(
                db,
                order,
                ffprobe_path=settings.ffprobe_path,
                ffmpeg_path=settings.ffmpeg_path,
                asset_root=asset_root,
            )
            subtitle_ass = None
            subtitle_sha256 = None
            if order.subtitle_revision:
                track = db.scalar(
                    select(SubtitleTrack).where(
                        SubtitleTrack.project_id == project_id,
                        SubtitleTrack.revision == order.subtitle_revision,
                    )
                )
                if track is None or track.status != "APPROVED":
                    raise DeliveryBlock("EPISODE_SUBTITLE_NOT_APPROVED")
                from shine.director.subtitles import track_matches_episode

                if not track_matches_episode(track, order.scope_id or ""):
                    raise DeliveryBlock("EPISODE_SUBTITLE_EPISODE_MISMATCH")
                if order.subtitle_sha256 and track.sha256 != order.subtitle_sha256:
                    # The order pinned one subtitle identity; a different text
                    # under the same revision number is not what was confirmed.
                    raise DeliveryBlock("EPISODE_SUBTITLE_CHANGED")
                edited = float(rules.get("edited_seconds") or 0.0)
                last_cue = max((float(cue.get("end_s") or 0.0) for cue in (track.cues or [])), default=0.0)
                if last_cue > edited + 0.05:
                    # Cues timed for a longer cut would burn lines over the
                    # wrong pictures or past the end; refuse instead.
                    raise DeliveryBlock("EPISODE_SUBTITLE_EXCEEDS_CUT")
                from shine.media.subtitles import burn_ass_for_track
                subtitle_ass = burn_ass_for_track(db, track)
                subtitle_sha256 = track.sha256
            clips = [
                DeliveryClip(
                    job_id=clip["job_id"],
                    attempt_id=clip["attempt_id"],
                    artifact_id=clip["artifact_id"],
                    in_s=clip["in_s"],
                    out_s=clip["out_s"],
                )
                for clip in edl["clips"]
            ]
        with session_factory() as work_db, AssemblyHeartbeat(session_factory, order_id, claim):
            result = assembler.assemble(
                work_db,
                project_id,
                clips,
                fps=24.0,
                width=None,
                height=None,
                crf=18,
                subtitle_ass=subtitle_ass,
                subtitle_sha256=subtitle_sha256,
                reuse_existing=True,
                page_size=EPISODE_PAGE_SIZE,
            )
            work_db.commit()
        with session_factory.begin() as db:
            if not _write_back(db, order_id, claim, {
                "status": "DELIVERED",
                "artifact_id": result.artifact.id,
                "delivered_at": utcnow(),
                "edl_payload": edl,
                "edl_source": edl_source,
                "edit_rules": rules,
                "failure_code": None,
                "failure_note": None,
                "state_version": DeliveryOrder.state_version + 1,
            }):
                return _order_status_now(db, order_id)
            episode = db.get(ProductionRunEpisode, episode_scope_id or "")
            if episode is not None:
                episode.delivery_order_id = order_id
                episode.delivered_artifact_id = result.artifact.id
                episode.delivered_sha256 = result.artifact.sha256
                episode.actual_seconds = float(result.duration_s or 0.0)
                episode.delivered_at = utcnow()
                episode.status = "DELIVERED"
                episode.blocker_code = None
            from shine.services import add_audit

            add_audit(
                db,
                "worker",
                "EPISODE_DELIVERED",
                "delivery_order",
                order_id,
                {
                    "episode_id": episode.episode_id if episode else None,
                    "artifact_id": result.artifact.id,
                    "sha256": result.artifact.sha256,
                    "duration_s": result.duration_s,
                    "review_state": result.review_state,
                    "clip_count": len(result.clips),
                    "edl_source": edl_source,
                },
                project_id=project_id,
            )
    except DeliveryBlock as exc:
        with session_factory.begin() as db:
            if not _write_back(db, order_id, claim, {
                "status": "BLOCKED",
                "failure_code": exc.code,
                "state_version": DeliveryOrder.state_version + 1,
            }):
                return _order_status_now(db, order_id)
            episode = db.get(ProductionRunEpisode, episode_scope_id or "")
            if episode is not None:
                episode.status = "BLOCKED"
                episode.blocker_code = exc.code
            from shine.services import add_audit

            add_audit(db, "worker", "EPISODE_DELIVERY_BLOCKED", "delivery_order", order_id,
                      {"code": exc.code}, project_id=project_id)
    except DeliveryError as exc:
        retryable = exc.status_code >= 500
        with session_factory.begin() as db:
            retry_scheduled = retryable and attempt < max_attempts
            if not _write_back(db, order_id, claim, {
                "status": "PENDING" if retry_scheduled else "FAILED",
                "worker_id": None if retry_scheduled else claim.owner,
                "failure_code": str(exc.detail)[:400],
                "failure_note": "bounded retry scheduled" if retry_scheduled else "episode assembly refused",
                "state_version": DeliveryOrder.state_version + 1,
            }):
                return _order_status_now(db, order_id)
    except Exception as exc:  # noqa: BLE001 - the order must record every failure mode
        with session_factory.begin() as db:
            retry_scheduled = attempt < max_attempts and isinstance(exc, OSError | subprocess.SubprocessError)
            if not _write_back(db, order_id, claim, {
                "status": "PENDING" if retry_scheduled else "FAILED",
                "worker_id": None if retry_scheduled else claim.owner,
                "failure_code": f"EPISODE_DELIVERY_ERROR:{type(exc).__name__}"[:400],
                "failure_note": ("bounded retry scheduled" if retry_scheduled else str(exc)[:400]),
                "state_version": DeliveryOrder.state_version + 1,
            }):
                return _order_status_now(db, order_id)
    with session_factory() as db:
        return _order_status_now(db, order_id)


