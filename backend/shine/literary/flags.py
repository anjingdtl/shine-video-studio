"""Feature switches: off / shadow / on, one reader, no hidden defaults.

``shadow`` is the important mode: the new strategy is computed and recorded, but
the request the user pays for is unchanged.  It is how memory can be introduced
without changing a single prompt until its facts have been reviewed.

Resolution order: environment variable, then the project's ``AppSetting``
override, then the feature's declared default.  An unknown value is treated as
``off`` - a typo must never silently enable a paid path.
"""

from __future__ import annotations

import os

from sqlalchemy import select

MODES = ("off", "shadow", "on")

FEATURES = {
    # V2.1 ships enabled.  ``shadow`` and ``off`` remain as escape hatches for an
    # operator who needs to stop paying for guidance or review on one project;
    # a fresh project gets the full engine without anyone flipping a switch.
    "memory": ("SHINE_LITERARY_MEMORY", "on"),
    "beats": ("SHINE_LITERARY_BEATS", "on"),
    "qa": ("SHINE_LITERARY_QA", "on"),
    "governor": ("SHINE_LITERARY_GOVERNOR", "on"),
}


def _normalise(value: str | None) -> str | None:
    if value is None:
        return None
    mode = value.strip().casefold()
    return mode if mode in MODES else "off"


def mode(feature: str, db=None, project_id: str | None = None) -> str:
    env_name, default = FEATURES.get(feature, ("", "off"))
    resolved = _normalise(os.getenv(env_name)) if env_name else None
    if resolved is not None:
        return resolved
    if db is not None and project_id:
        from shine.models import AppSetting

        row = db.get(AppSetting, f"literary-feature:{project_id}:{feature}")
        if row is not None and isinstance(row.value, dict):
            override = _normalise(str(row.value.get("mode") or ""))
            if override is not None:
                return override
    return default


def enabled(feature: str, db=None, project_id: str | None = None) -> bool:
    return mode(feature, db, project_id) != "off"


def affects_request(feature: str, db=None, project_id: str | None = None) -> bool:
    """True only in ``on``: ``shadow`` never changes what is sent."""
    return mode(feature, db, project_id) == "on"


def set_mode(db, project_id: str, feature: str, value: str) -> None:
    from shine.models import AppSetting

    if feature not in FEATURES:
        raise ValueError("LITERARY_FEATURE_UNKNOWN")
    normalised = _normalise(value)
    if normalised is None:
        raise ValueError("LITERARY_FEATURE_MODE_INVALID")
    db.merge(AppSetting(key=f"literary-feature:{project_id}:{feature}",
                        value={"mode": normalised}))


def all_modes(db, project_id: str) -> dict[str, str]:
    return {feature: mode(feature, db, project_id) for feature in FEATURES}


def project_overrides(db, project_id: str) -> dict[str, str]:
    from shine.models import AppSetting

    rows = db.scalars(select(AppSetting).where(
        AppSetting.key.like(f"literary-feature:{project_id}:%"))).all()
    result = {}
    for row in rows:
        feature = row.key.rsplit(":", 1)[-1]
        value = (row.value or {}).get("mode")
        if feature in FEATURES and value in MODES:
            result[feature] = value
    return result
