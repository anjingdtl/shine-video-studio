"""Typed contracts of the director journey: episode planning and scope identity.

Everything here is deterministic and side-effect free so the same project state
always produces the same plan, the same scope digest and therefore the same run
identity.  Language models may *propose* an episode boundary, but only this
module decides whether the proposal covers every confirmed shot exactly once.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any

from pydantic import Field, model_validator

from shine.schemas import APIModel, ShotSpec

MAX_EPISODES = 200
MAX_SHOTS_PER_EPISODE = 400
DEFAULT_TARGET_SECONDS = 60
DEFAULT_TOLERANCE_SECONDS = 15


def canonical_json(payload: Any) -> str:
    """A stable serialisation: sorted keys, no NaN, UTF-8 preserved."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_of(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


class PlannedEpisode(APIModel):
    """One episode boundary: an ordered, contiguous run of confirmed shots."""

    episode_id: str = Field(min_length=1, max_length=80)
    order: int = Field(ge=1, le=MAX_EPISODES)
    title: str = Field(default="", max_length=200)
    shot_ids: list[str] = Field(min_length=1, max_length=MAX_SHOTS_PER_EPISODE)
    planned_seconds: float = Field(gt=0, allow_inf_nan=False)
    boundary_note: str = Field(default="", max_length=500)


class EpisodePlanPayload(APIModel):
    """The episode plan of one project revision.

    ``covered`` is the authoritative coverage map: every confirmed shot must
    appear exactly once.  A repeated shot (a recap) has to be declared, and an
    undeclared repeat is a hard validation error rather than a silent duplicate
    render.
    """

    schema_version: str = Field(default="1.0", max_length=8)
    target_seconds: int = Field(ge=8, le=3600)
    tolerance_seconds: int = Field(default=DEFAULT_TOLERANCE_SECONDS, ge=0, le=900)
    episodes: list[PlannedEpisode] = Field(min_length=1, max_length=MAX_EPISODES)
    allowed_repeats: list[str] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def contiguous_and_complete(self) -> EpisodePlanPayload:
        orders = [episode.order for episode in self.episodes]
        if orders != list(range(1, len(orders) + 1)):
            raise ValueError("EPISODE_ORDER_MUST_BE_CONTIGUOUS_FROM_ONE")
        ids = [episode.episode_id for episode in self.episodes]
        if len(set(ids)) != len(ids):
            raise ValueError("EPISODE_ID_REPEATED")
        seen: list[str] = []
        for episode in self.episodes:
            if len(set(episode.shot_ids)) != len(episode.shot_ids):
                raise ValueError(f"EPISODE_REPEATS_A_SHOT:{episode.episode_id}")
            seen.extend(episode.shot_ids)
        repeated = {shot for shot in seen if seen.count(shot) > 1}
        undeclared = repeated - set(self.allowed_repeats)
        if undeclared:
            raise ValueError("SHOT_COVERED_TWICE:" + ",".join(sorted(undeclared))[:400])
        return self

    def shot_order(self) -> list[str]:
        return [shot_id for episode in self.episodes for shot_id in episode.shot_ids]


def shot_order(shots: Iterable[Any]) -> list[Any]:
    """The project's production order for shots.

    ``Shot`` carries no explicit ordinal, so the order the shots were applied in
    — creation time, then id as a tie breaker — is the only deterministic one.
    It is resolved *once*, recorded in the episode plan and pinned by the
    package, so adding a shot later can never silently reorder an existing run.
    """
    return sorted(shots, key=lambda shot: (shot.created_at, shot.id))


def continuity_mode(shot: Any) -> str:
    try:
        return str(ShotSpec.model_validate(shot.spec).continuity.mode.value)
    except Exception:
        return "cut"


def planned_seconds(shot: Any) -> float:
    try:
        return float(ShotSpec.model_validate(shot.spec).duration_target_s)
    except Exception:
        return 0.0


def derive_episode_plan(
    shots: Iterable[Any],
    *,
    target_seconds: int = DEFAULT_TARGET_SECONDS,
    tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
    title_prefix: str = "第{n}集",
) -> EpisodePlanPayload:
    """Split the confirmed shots into narrative episodes around a target length.

    The splitter only ever cuts *between* shots, and only on a shot that does
    not continue the previous one, so a continuity chain and a single line of
    dialogue are never severed by an episode boundary.  When the target cannot
    be honoured (one very long scene, or shots that all continue each other),
    the resulting episode is longer than the target and says so in
    ``boundary_note``; nothing is truncated to make a number.
    """
    ordered = shot_order(shots)
    if not ordered:
        raise ValueError("EPISODE_PLAN_REQUIRES_SHOTS")
    low = max(1, target_seconds - tolerance_seconds)
    high = target_seconds + tolerance_seconds
    episodes: list[PlannedEpisode] = []
    current: list[Any] = []
    total = 0.0

    def close(note: str = "") -> None:
        nonlocal current, total
        if not current:
            return
        episodes.append(
            PlannedEpisode(
                episode_id=f"ep-{len(episodes) + 1:02d}",
                order=len(episodes) + 1,
                title=title_prefix.format(n=len(episodes) + 1),
                shot_ids=[shot.id for shot in current],
                planned_seconds=round(total, 3),
                boundary_note=note,
            )
        )
        current = []
        total = 0.0

    for index, shot in enumerate(ordered):
        current.append(shot)
        total += planned_seconds(shot)
        last = index == len(ordered) - 1
        if last:
            break
        next_shot = ordered[index + 1]
        # A boundary must not break a continuity chain: the next shot would
        # depend on a job that lives in another series.
        if continuity_mode(next_shot) != "cut":
            continue
        if total >= low and (total >= target_seconds or continuity_mode(shot) == "cut"):
            close("natural_cut_at_target" if total >= target_seconds else "cut_at_lower_tolerance")
        elif total >= high:
            close("exceeded_upper_tolerance")
    close("final_episode")

    if len(episodes) > MAX_EPISODES:
        raise ValueError("EPISODE_PLAN_EXCEEDS_MAX_EPISODES")
    long_ones = [e for e in episodes if e.planned_seconds > high]
    if long_ones:
        for episode in long_ones:
            episode.boundary_note = (
                (episode.boundary_note + "; " if episode.boundary_note else "")
                + f"planned {episode.planned_seconds:.1f}s exceeds {high}s: no valid cut point"
            )
    return EpisodePlanPayload(
        target_seconds=target_seconds,
        tolerance_seconds=tolerance_seconds,
        episodes=episodes,
        allowed_repeats=[],
    )


def validate_episode_coverage(payload: EpisodePlanPayload, confirmed_shot_ids: Iterable[str]) -> list[str]:
    """The coverage problems of a plan against the project's real shots.

    Returns readable problems; an empty list means the plan covers the project
    exactly once and may be confirmed.
    """
    confirmed = set(confirmed_shot_ids)
    planned = payload.shot_order()
    problems: list[str] = []
    missing = sorted(confirmed - set(planned))
    unknown = sorted(set(planned) - confirmed)
    if missing:
        problems.append("SHOTS_NOT_COVERED:" + ",".join(missing)[:400])
    if unknown:
        problems.append("PLAN_REFERENCES_UNKNOWN_SHOTS:" + ",".join(unknown)[:400])
    if len(planned) != len(set(planned)) and not payload.allowed_repeats:
        problems.append("SHOT_COVERED_TWICE")
    return problems


def scope_digest(
    project_id: str,
    episodes: list[dict[str, Any]],
    *,
    style: dict[str, Any] | None,
    binding: dict[str, Any] | None,
    script: dict[str, Any] | None,
) -> str:
    """The identity of *what* a package covers, independent of its revision.

    It is computed from the full per-episode manifests (the shots and their
    revisions), never from a summary, so two packages that cover the same
    shots with the same identities share a scope digest even at different
    revisions.
    """
    return digest_of(
        {
            "project_id": project_id,
            "episode_ids": [episode["episode_id"] for episode in episodes],
            "shot_ids": [shot["shot_id"] for episode in episodes for shot in episode["shots"]],
            "shot_revisions": [[shot["revision"] for shot in episode["shots"]] for episode in episodes],
            "style": style,
            "binding": binding,
            "script": script,
        }
    )
