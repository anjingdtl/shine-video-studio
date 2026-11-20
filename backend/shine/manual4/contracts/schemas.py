"""Independent Manual4 contracts, mirrored and calibrated for the four-stage line."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

M4StageKey = Literal["storyboard", "materials", "production", "delivery"]
M4_STAGE_ORDER: tuple[M4StageKey, ...] = ("storyboard", "materials", "production", "delivery")

M4_MIN_SHOT_SECONDS = 4.0
M4_MAX_SHOT_SECONDS = 15.0
M4_REQUIRED_GENERATION_MODE = "Ref2VA"

M4_MAX_IMAGE_REFS = 9
M4_MAX_VIDEO_REFS = 3
M4_MAX_AUDIO_REFS = 3
M4_MAX_TOTAL_REFS = 12


def canonical_digest(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class M4StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class M4StageSummary(BaseModel):
    key: M4StageKey
    title: str
    done: bool
    status: Literal["done", "active", "blocked", "pending"]
    blocker_count: int = 0
    blockers: list[dict[str, Any]] = Field(default_factory=list)
    facts: dict[str, Any] = Field(default_factory=dict)


class M4ReadinessVerdict(BaseModel):
    project_id: str
    workflow_kind: Literal["manual4"] = "manual4"
    next_stage: M4StageKey
    all_done: bool
    stages: list[M4StageSummary]
    can_confirm_storyboard: bool = False
    can_start_production: bool = False
    can_assemble_delivery: bool = False
    summary: dict[str, Any] = Field(default_factory=dict)
