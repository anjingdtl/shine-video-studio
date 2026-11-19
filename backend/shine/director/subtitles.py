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
from shine.models import Shot, SubtitleTrack, utcnow

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


def _selected_cut_timing(db, project_id: str, episode_id: str) -> dict[str, Any]:
    """The real cut of the version this episode currently points at.

    The delivered edit table is the only authority on where a shot really starts
    in the reel: the run's episode row names the selected ``DeliveryOrder`` (a
    re-cut is a new version and the pointer moves with it), and that order's
    ``edl_payload`` carries the real clip order with ``in_s`` / ``out_s``.  The
    old lookup asked for ``scope_id == episode_id`` and ``status == COMPLETED``,
    which no delivered row satisfies — so every subtitle track silently fell back
    to planned lengths while the picture used the real cut.

    Each entry is ``{shot_id: {start_s, in_s, duration_s}}``; ``start_s`` is the
    clip's real start in the reel, ``in_s`` the source offset the cut begins at.
    """
    from shine.media.delivery_models import DeliveryOrder
    from shine.production.models import ProductionRunEpisode

    rows = db.scalars(select(ProductionRunEpisode).where(
        ProductionRunEpisode.project_id == project_id,
        ProductionRunEpisode.episode_id == episode_id,
        ProductionRunEpisode.delivery_order_id.isnot(None),
    ).order_by(ProductionRunEpisode.updated_at.desc())).all()
    for episode in rows:
        order = db.get(DeliveryOrder, episode.delivery_order_id or "")
        if order is None or order.status != "DELIVERED" or not order.edl_payload:
            continue
        clips: dict[str, dict[str, float]] = {}
        offset = 0.0
        for clip in order.edl_payload.get("clips") or []:
            shot_id = str(clip.get("shot_id") or "")
            in_s = float(clip.get("in_s") or 0.0)
            out_s = float(clip.get("out_s") or 0.0)
            length = round(out_s - in_s, 3)
            if shot_id and length > 0:
                clips[shot_id] = {"start_s": round(offset, 3), "in_s": round(in_s, 3),
                                  "duration_s": length}
                offset += length
        if clips:
            return {"order_id": order.id, "version": order.version, "clips": clips}
    return {}


def compile_episode_subtitle_track(db, project, episode_id: str, *, actor: str = "user") -> dict[str, Any]:
    """Build one subtitle revision for this episode's reel.

    The lines come from the same fact the renderer spoke: the shot spec's
    dialogue, with its editorial times.  A shot whose lines a human froze in ③
    overrides the dialogue for that shot only.  Timing follows the selected
    delivered cut, exactly like the assembly does.
    """
    from shine.media.subtitles import SubtitleCue, render_ass, render_srt, sha256_text, subtitle_font_size

    # 字幕与装配读同一张剪辑表：真实成片在，范围就是这一版成片的镜头
    # （③ 实时镜表可能已应用了不属于这一版的新镜头）；只有还没有真实
    # 交付时才回落到实时镜表的计划时长，并在结果里如实说明。
    cut = _selected_cut_timing(db, project.id, episode_id)
    if cut:
        scope_ids = list(cut["clips"])
        basis = "actual"
    else:
        table = episode_shot_subtitles(db, project, episode_id)
        if not table["shots"]:
            raise HTTPException(404, "EPISODE_NOT_IN_PLAN")
        scope_ids = [row["shot_id"] for row in table["shots"]]
        basis = "planned"
    specs = {
        shot_row.id: (shot_row.spec or {})
        for shot_row in db.scalars(select(Shot).where(Shot.id.in_(scope_ids))).all()
    }
    stored = {
        sub.shot_id: sub for sub in db.scalars(select(ShotSubtitle).where(
            ShotSubtitle.shot_id.in_(scope_ids))).all()
    } if scope_ids else {}
    ordered: list[dict[str, Any]] = []
    manual_shots = 0
    dialogue_shots = 0
    for position, shot_id in enumerate(scope_ids, start=1):
        sub = stored.get(shot_id)
        if sub is not None and sub.status == "FROZEN" and sub.lines:
            lines = list(sub.lines)
            manual_shots += 1
        else:
            lines = [
                {"start_s": float(line.get("start_s") or 0),
                 "end_s": float(line.get("end_s") or 0),
                 "text": str(line.get("text") or "").strip()}
                for line in (specs.get(shot_id, {}).get("dialogue") or [])
                if str(line.get("text") or "").strip()
            ]
            if lines:
                dialogue_shots += 1
        if lines:
            ordered.append({"shot_id": shot_id, "position": position, "lines": lines})
    if not ordered:
        raise HTTPException(409, {
            "code": "EPISODE_SUBTITLE_EMPTY",
            "message": "这一集没有可生成的字幕：镜头既没有对白，也没有人工冻结的字幕",
        })
    warnings: list[str] = []
    if basis == "planned":
        warnings.append("还没用真实成片时长：字幕时间按每镜计划时长排布，出片后建议重新生成一次")

    cues: list[SubtitleCue] = []
    offset = 0.0
    index = 0
    for shot in ordered:
        if basis == "actual":
            clip = cut["clips"][shot["shot_id"]]
            base, source_in = float(clip["start_s"]), float(clip["in_s"])
            duration = float(clip["duration_s"])
        else:
            base, source_in = offset, 0.0
            duration = float(specs.get(shot["shot_id"], {}).get("duration_target_s") or 0)
            offset += duration
        for line in shot["lines"]:
            # 每镜字幕是相对这一镜自己的时间写的；被裁掉的片头不再出现在成片里，
            # 所以整行落在 in_s 之前就丢掉，跨过切点的行从片段起点开始。
            start = base + float(line["start_s"]) - source_in
            end = base + float(line["end_s"]) - source_in
            if end <= base + 0.001:
                continue
            start = max(start, base)
            if end > base + duration + 0.05:
                warnings.append(f"镜头 {shot['position']} 的字幕超出实际片段，已按片段末尾截断")
                end = base + duration
                if end <= start:
                    continue
            index += 1
            cues.append(SubtitleCue(index=index, start_s=round(start, 3),
                                    end_s=round(end, 3), text=str(line["text"])))
    if not cues:
        raise HTTPException(409, {"code": "EPISODE_SUBTITLE_EMPTY",
                                  "message": "字幕没有落在成片镜头的时长内"})

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
        ass=render_ass(cues, font_size=subtitle_font_size(db, project.id)),
        sha256=sha256_text(srt),
        source="director_shot_config" if manual_shots else "shot_dialogue",
        created_by=actor,
    )
    db.add(row)
    db.flush()
    from shine.services import add_audit

    add_audit(
        db, actor, "EPISODE_SUBTITLE_COMPILED", "subtitle_track", row.id,
        {"revision": revision, "episode_id": episode_id, "cues": len(cues),
         "basis": basis, "sha256": row.sha256,
         "manual_shots": manual_shots, "dialogue_shots": dialogue_shots},
        project_id=project.id,
    )
    return {
        "revision": revision,
        "episode_id": episode_id,
        "status": row.status,
        "cue_count": len(cues),
        "basis": basis,
        "manual_shots": manual_shots,
        "dialogue_shots": dialogue_shots,
        # 字幕跟随的是哪一版成片：与装配读同一张剪辑表，页面不必再猜。
        "cut_order_id": cut.get("order_id") if basis == "actual" else None,
        "cut_version": cut.get("version") if basis == "actual" else None,
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
