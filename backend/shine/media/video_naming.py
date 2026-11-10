"""Give a produced video a real, human-readable disk name.

The ledger identity stays the content hash; only the file name on disk becomes
readable: 《项目名称_003镜_02集》.mp4 for one shot and
《项目名称_001-006镜_02集》.mp4 for a reel.  Every part comes from the ledger
(project title, the run's episode row, the shot's place in the plan); a part
that is not registered is written as 未分集 / 镜号未登记 instead of being
guessed from sort order or an old file name.
"""

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

# Windows rejects these names regardless of extension.
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_INVALID = re.compile(r'[\x00-\x1f<>:"/\\|?*]')
_WHITESPACE = re.compile(r"\s+")
# Leaves room for the episode/shot suffix and a _vN disambiguator inside the
# 255-character component limit, without depending on a long data directory.
MAX_TITLE_LENGTH = 60
MAX_COMPONENT_LENGTH = 120


def sanitize_component(text: str | None, *, fallback: str = "未命名", limit: int = MAX_TITLE_LENGTH) -> str:
    """A legal, non-reserved Windows path component that keeps its meaning."""
    cleaned = _INVALID.sub("-", text or "")
    cleaned = _WHITESPACE.sub(" ", cleaned).strip(" .")
    if not cleaned:
        return fallback
    if cleaned.split(".")[0].upper() in _RESERVED:
        cleaned = "_" + cleaned
    cleaned = cleaned[:limit].strip(" .")
    return cleaned or fallback


def project_shot_order(db: Session, project_id: str) -> list[str]:
    """The workbench's own shot order: the read order of the shot list."""
    from shine.models import Shot

    rows = db.scalars(
        select(Shot).where(Shot.project_id == project_id).order_by(Shot.created_at, Shot.name)
    ).all()
    return [shot.id for shot in rows]


def episode_for_job(db: Session, job) -> tuple[int | None, list[str] | None]:
    """The episode position and its ordered shots for one job, if it has one.

    The series item names the series; a run batch names the episode that the
    series was dispatched for.  A retake series is not dispatched by a run, so
    the shot falls back to the run episode that actually lists it.
    """
    from shine.models import ProductionSeriesItem
    from shine.production.models import ProductionRunBatch, ProductionRunEpisode

    item = db.scalar(select(ProductionSeriesItem).where(ProductionSeriesItem.job_id == job.id))
    if item is not None and item.series_id:
        batch = db.scalar(
            select(ProductionRunBatch).where(ProductionRunBatch.series_id == item.series_id)
        )
        if batch is not None:
            episode = db.get(ProductionRunEpisode, batch.episode_row_id)
            if episode is not None:
                return episode.position, list(episode.shot_ids)
    rows = db.scalars(
        select(ProductionRunEpisode)
        .where(ProductionRunEpisode.project_id == job.project_id)
        .order_by(ProductionRunEpisode.created_at.desc())
    ).all()
    for episode in rows:
        if job.shot_id in (episode.shot_ids or []):
            return episode.position, list(episode.shot_ids)
    return None, None


def shot_context(db: Session, job, *, project_order: list[str] | None = None) -> tuple[int | None, str]:
    """The shot's number and episode label, both from the ledger."""
    position, episode_shots = episode_for_job(db, job)
    number: int | None = None
    if episode_shots and job.shot_id in episode_shots:
        number = episode_shots.index(job.shot_id) + 1
    else:
        order = project_order if project_order is not None else project_shot_order(db, job.project_id)
        if job.shot_id in order:
            number = order.index(job.shot_id) + 1
    label = f"{position:02d}集" if position is not None else "未分集"
    return number, label


def clip_filename(db: Session, project, job) -> str:
    """The real file name for one produced shot."""
    title = sanitize_component(getattr(project, "title", None))
    number, episode = shot_context(db, job)
    shot_part = f"{number:03d}镜" if number is not None else "镜号未登记"
    return _bounded(f"《{title}_{shot_part}_{episode}》.mp4")


def delivery_filename(db: Session, project_id: str, project_title: str | None, jobs: list) -> str:
    """The real file name for a reel assembled over the given source jobs."""
    title = sanitize_component(project_title)
    order = project_shot_order(db, project_id)
    numbers: list[int] = []
    episodes: set[int] = set()
    for job in jobs:
        number, label = shot_context(db, job, project_order=order)
        if number is not None:
            numbers.append(number)
        if label != "未分集":
            episodes.add(int(label[:2]))
    if numbers:
        low, high = min(numbers), max(numbers)
        shot_part = f"{low:03d}镜" if low == high else f"{low:03d}-{high:03d}镜"
    else:
        shot_part = "镜号未登记"
    episode = f"{next(iter(episodes)):02d}集" if len(episodes) == 1 else "未分集"
    return _bounded(f"《{title}_{shot_part}_{episode}》.mp4")


def _bounded(name: str) -> str:
    """Keep the file-name component short without cutting the extension."""
    stem, suffix = Path(name).stem, Path(name).suffix
    if len(stem) > MAX_COMPONENT_LENGTH:
        stem = stem[:MAX_COMPONENT_LENGTH].rstrip(" .")
    return stem + suffix
