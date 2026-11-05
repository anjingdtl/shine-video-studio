"""Readable names for generated material.

A person matches a generated picture to the script and the storyboard by its
name, so a material file carries the entity (a person, a prop, a scene) and the
number of the place it takes in the script.  The number follows the order the
entity first appears in the script, not the alphabetical order of the ledger,
so a sorted file list reads like the script does.
"""

from __future__ import annotations

import re
from typing import Any

_FILENAME_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def readable_name(value: str, fallback: str = "material") -> str:
    """A name a person can read in a file list."""
    name = _FILENAME_UNSAFE.sub("_", str(value or "")).strip().strip(".")
    return name[:60] or fallback


def requirement_ordinal(db, project_id: str, family: str, entity_id: str) -> int:
    """The place this entity takes in the confirmed script order.

    Zero means "not bound to a numbered requirement": a manual upload has no
    place in the script, and the caller then uses the plain name.
    """
    from shine.comfyops.models import PreProductionPlan

    rows = db.scalars(
        select_asset_requirements(project_id, family, entity_id)
    ).all()
    if not rows:
        return 0
    row = rows[0]
    plan = db.get(PreProductionPlan, row.plan_id)
    payload = (plan.payload or {}) if plan else {}
    entries = list(payload.get("requirements") or [])
    position = next(
        (i for i, e in enumerate(entries) if e.get("requirement_id") == row.id),
        None,
    )
    if position is None:
        return 0
    scenes = [str(key) for key in (payload.get("source_scenes") or [])]

    def scene_rank(entry: dict[str, Any]) -> int:
        ranks = [
            scenes.index(str(key))
            for key in (entry.get("scene_keys") or [])
            if str(key) in scenes
        ]
        return min(ranks) if ranks else len(scenes)

    ordered = sorted(range(len(entries)), key=lambda i: (scene_rank(entries[i]), i))
    return ordered.index(position) + 1


def select_asset_requirements(project_id: str, family: str, entity_id: str):
    """The requirement rows of one entity, oldest plan first."""
    from sqlalchemy import select

    from shine.comfyops.models import AssetRequirement

    return select(AssetRequirement).where(
        AssetRequirement.project_id == project_id,
        AssetRequirement.payload["entity_id"].as_string() == entity_id,
        AssetRequirement.payload["family"].as_string() == family,
    )


def material_filename(
    db,
    project_id: str,
    family: str,
    entity_id: str,
    extension: str = ".png",
) -> str:
    """The file name of one material: number, then the readable entity name."""
    ordinal = requirement_ordinal(db, project_id, family, entity_id)
    name = readable_name(entity_id, family)
    return f"{ordinal:02d}-{name}{extension}" if ordinal else f"{name}{extension}"
