"""Per-shot subtitle lines that compile into the existing external subtitle track.

The subtitles of this product are deliberately outside the picture: a person can
fix a line or a timing without rendering the shot again, and the delivery
composites the approved revision at the very end.  This module adds the place
where those lines are *written*: the shot table of stage ③, one shot at a time.

Nothing here reaches an H3 prompt.  A shot's lines are stored relative to that
shot and compiled into an episode revision with absolute times, using the real
delivered clip lengths when they exist and the planned lengths before that.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import select

from shine.director.models import ShotSubtitle
from shine.models import Job, Shot, SubtitleTrack, utcnow

MAX_LINES_PER_SHOT = 12
MAX_SUBTITLE_TEXT = 200


def _episode_of_shot(db, project_id: str, shot_id: str) -> str:
    """Which episode owns this shot.

    The answer comes from the confirmed episode plan, which is the same source
    the shot table reads, so a shot cannot belong to one episode in the table
    and to another in the subtitle ledger.
    """
    from shine.director.streamline import episode_of_shot

    return episode_of_shot(db, project_id, shot_id)


def _validate_lines(lines: list[dict[str, Any]], duration: float) -> list[dict[str, Any]]:
    if len(lines) > MAX_LINES_PER_SHOT:
        raise HTTPException(422, {
            "code": "SHOT_SUBTITLE_TOO_MANY_LINES",
            "message": f"一镜最多 {MAX_LINES_PER_SHOT} 行字幕",
        })
    cleaned: list[dict[str, Any]] = []
    previous_end = -1.0
    for row in sorted(lines, key=lambda item: float(item.get("start_s") or 0)):
        text = str(row.get("text") or "").strip()
        start = float(row.get("start_s") or 0)
        end = float(row.get("end_s") or 0)
        if not text:
            raise HTTPException(422, {"code": "SHOT_SUBTITLE_TEXT_EMPTY",
                                      "message": "字幕文本不能为空"})
        if len(text) > MAX_SUBTITLE_TEXT:
            raise HTTPException(422, {"code": "SHOT_SUBTITLE_TEXT_TOO_LONG",
                                      "message": f"单行字幕最多 {MAX_SUBTITLE_TEXT} 字"})
        if start < 0 or end <= start:
            raise HTTPException(422, {"code": "SHOT_SUBTITLE_TIME_INVALID",
                                      "message": "字幕结束时间必须晚于开始时间"})
        if end > duration + 0.05:
            raise HTTPException(422, {
                "code": "SHOT_SUBTITLE_OUTSIDE_SHOT",
                "message": f"字幕超出这一镜的时长（{duration:.2f} 秒）",
                "duration_target_s": duration,
            })
        if start < previous_end - 0.001:
            raise HTTPException(422, {"code": "SHOT_SUBTITLE_OVERLAP",
                                      "message": "同一镜内的字幕不能互相重叠"})
        previous_end = max(previous_end, end)
        cleaned.append({"start_s": round(start, 3), "end_s": round(end, 3), "text": text})
    return cleaned


def save_shot_subtitle_lines(db, project, shot: Shot, lines: list[dict[str, Any]], *,
                             freeze: bool, actor: str = "user") -> dict[str, Any]:
    import hashlib
    import json

    spec = shot.spec or {}
    duration = float(spec.get("duration_target_s") or 0)
    cleaned = _validate_lines(lines, duration)
    episode_id = _episode_of_shot(db, shot.project_id, shot.id)
    row = db.scalar(select(ShotSubtitle).where(ShotSubtitle.shot_id == shot.id))
    digest = hashlib.sha256(json.dumps(
        {"shot_id": shot.id, "lines": cleaned}, ensure_ascii=False, sort_keys=True,
    ).encode("utf-8")).hexdigest()
    if row is None:
        row = ShotSubtitle(
            project_id=shot.project_id, shot_id=shot.id, episode_id=episode_id,
            lines=cleaned, revision=1, status="FROZEN" if freeze else "DRAFT",
            sha256=digest, created_by=actor,
            frozen_at=utcnow() if freeze else None,
        )
        db.add(row)
    else:
        row.episode_id = episode_id
        row.lines = cleaned
        row.revision += 1
        row.status = "FROZEN" if freeze else "DRAFT"
        row.sha256 = digest
        row.frozen_at = utcnow() if freeze else None
    db.flush()
    from shine.services import add_audit

    add_audit(
        db, actor, "SHOT_SUBTITLE_FROZEN" if freeze else "SHOT_SUBTITLE_SAVED",
        "shot", shot.id,
        {"revision": row.revision, "lines": len(cleaned), "episode_id": episode_id,
         "sha256": digest},
        project_id=shot.project_id,
    )
    return {
        "shot_id": shot.id, "episode_id": episode_id, "revision": row.revision,
        "status": row.status, "lines": cleaned, "sha256": digest,
        "frozen_at": row.frozen_at.isoformat() + "Z" if row.frozen_at else None,
    }


def episode_shot_subtitles(db, project, episode_id: str) -> dict[str, Any]:
    from shine.director.streamline import episode_shot_table

    table = episode_shot_table(db, project.id, episode_id)
    shots = table.get("shots") or []
    ids = [row["shot_id"] for row in shots]
    stored = {
        row.shot_id: row for row in db.scalars(select(ShotSubtitle).where(
            ShotSubtitle.shot_id.in_(ids))).all()
    } if ids else {}
    rows = []
    for shot in shots:
        row = stored.get(shot["shot_id"])
        rows.append({
            "shot_id": shot["shot_id"],
            "position": shot["position"],
            "name": shot["name"],
            "duration_target_s": shot["duration_target_s"],
            "lines": list(row.lines or []) if row is not None else [],
            "revision": row.revision if row is not None else 0,
            "status": row.status if row is not None else "EMPTY",
            "sha256": row.sha256 if row is not None else None,
            "frozen_at": (row.frozen_at.isoformat() + "Z"
                          if row is not None and row.frozen_at else None),
        })
    return {
        "episode_id": episode_id,
        "shots": rows,
        "configured_count": sum(1 for row in rows if row["lines"]),
        "frozen_count": sum(1 for row in rows if row["status"] == "FROZEN"),
    }


def _real_clip_durations(db, project_id: str, episode_id: str) -> dict[str, float]:
    """The delivered, real length of each shot of this episode, when it exists."""
    from shine.media.delivery_models import DeliveryOrder

    order = db.scalars(select(DeliveryOrder).where(
        DeliveryOrder.project_id == project_id,
        DeliveryOrder.scope_kind == "episode",
        DeliveryOrder.scope_id == episode_id,
        DeliveryOrder.status == "COMPLETED",
    ).order_by(DeliveryOrder.version.desc())).first()
    if order is None or not order.edl_payload:
        return {}
    clips = [row for row in (order.edl_payload.get("clips") or []) if row.get("job_id")]
    if not clips:
        return {}
    jobs = {
        row.id: row for row in db.scalars(select(Job).where(
            Job.id.in_([clip["job_id"] for clip in clips]))).all()
    }
    durations: dict[str, float] = {}
    for clip in clips:
        job = jobs.get(clip["job_id"])
        if job is None:
            continue
        length = float(clip.get("out_s") or 0) - float(clip.get("in_s") or 0)
        if length > 0:
            durations[job.shot_id] = round(length, 3)
    return durations


def compile_episode_subtitle_track(db, project, episode_id: str, *, actor: str = "user") -> dict[str, Any]:
    """Build one subtitle revision for this episode's reel from frozen shot lines."""
    from shine.media.subtitles import SubtitleCue, render_ass, render_srt, sha256_text

    require = episode_shot_subtitles(db, project, episode_id)
    shots = require["shots"]
    if not shots:
        raise HTTPException(404, "EPISODE_NOT_IN_PLAN")
    frozen = [row for row in shots if row["status"] == "FROZEN" and row["lines"]]
    if not frozen:
        raise HTTPException(409, {
            "code": "EPISODE_SUBTITLE_EMPTY",
            "message": "这一集还没有冻结的镜头字幕；先给至少一镜配置并冻结字幕",
        })
    real = _real_clip_durations(db, project.id, episode_id)
    basis = "actual" if real and all(row["shot_id"] in real for row in shots) else "planned"
    warnings: list[str] = []
    if basis == "planned":
        warnings.append("还没用真实成片时长：字幕时间按每镜计划时长排布，出片后建议重新生成一次")

    cues: list[SubtitleCue] = []
    offset = 0.0
    index = 0
    for shot in shots:
        duration = float(real.get(shot["shot_id"]) or shot["duration_target_s"] or 0)
        for line in shot["lines"] if shot["status"] == "FROZEN" else []:
            start = offset + float(line["start_s"])
            end = offset + float(line["end_s"])
            if end > offset + duration + 0.05:
                warnings.append(f"镜头 {shot['position']} 的字幕超出实际片段，已按片段末尾截断")
                end = offset + duration
                if end <= start:
                    continue
            index += 1
            cues.append(SubtitleCue(index=index, start_s=round(start, 3),
                                    end_s=round(end, 3), text=str(line["text"])))
        offset += duration
    if not cues:
        raise HTTPException(409, {"code": "EPISODE_SUBTITLE_EMPTY",
                                  "message": "冻结的字幕没有落在这一镜的时长内"})

    previous = db.scalars(select(SubtitleTrack).where(
        SubtitleTrack.project_id == project.id)).all()
    revision = max((row.revision for row in previous), default=0) + 1
    srt = render_srt(cues)
    row = SubtitleTrack(
        project_id=project.id,
        revision=revision,
        status="DRAFT",
        episode_id=episode_id,
        cues=[{"index": cue.index, "start_s": cue.start_s, "end_s": cue.end_s, "text": cue.text}
              for cue in cues],
        srt=srt,
        ass=render_ass(cues),
        sha256=sha256_text(srt),
        source="director_shot_config",
        created_by=actor,
    )
    db.add(row)
    db.flush()
    from shine.services import add_audit

    add_audit(
        db, actor, "EPISODE_SUBTITLE_COMPILED", "subtitle_track", row.id,
        {"revision": revision, "episode_id": episode_id, "cues": len(cues),
         "basis": basis, "sha256": row.sha256},
        project_id=project.id,
    )
    return {
        "revision": revision,
        "episode_id": episode_id,
        "status": row.status,
        "cue_count": len(cues),
        "basis": basis,
        "warnings": warnings,
        "sha256": row.sha256,
        "cues": row.cues,
    }


def track_matches_episode(track: SubtitleTrack, episode_id: str) -> bool:
    """A revision compiled for one episode may not be composited into another."""
    return not track.episode_id or track.episode_id == episode_id


__all__ = [
    "compile_episode_subtitle_track",
    "episode_shot_subtitles",
    "save_shot_subtitle_lines",
    "track_matches_episode",
]
