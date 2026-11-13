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
MAX_REPAIRS_PER_SHOT = 20
MAX_BATCH_SHOT_LIMIT = 50


class PackagePolicyContract(APIModel):
    """The typed, versioned execution policy a director package freezes.

    Strict types and an explicit upper bound on every number: a package that
    cannot say what it allows can never be executed honestly.  Unknown keys are
    refused, not silently ignored — a frozen contract has no room for fields
    nobody reads.
    """

    model_config = {**APIModel.model_config, "extra": "forbid"}

    auto_repair: bool = True
    max_repairs_per_shot: int = Field(default=2, ge=0, le=MAX_REPAIRS_PER_SHOT, strict=True)
    batch_shot_limit: int = Field(default=6, ge=1, le=MAX_BATCH_SHOT_LIMIT, strict=True)
    split_continuity: bool = Field(
        default=True,
        description=(
            "May a batch boundary fall inside a continuity chain?  When true the "
            "next series binds the earlier series' verified source media; when "
            "false a chain always stays inside one series."
        ),
    )
    max_wall_seconds: int = Field(default=0, ge=0, le=31_536_000, strict=True)
    min_free_gb: float = Field(default=0.0, ge=0.0, le=100_000.0)
    stop_on_blocker: bool = False
    deliver_partial_episode: bool = False


def canonical_json(payload: Any) -> str:
    """A stable serialisation: sorted keys, no NaN, UTF-8 preserved."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_of(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


class PlannedEpisode(APIModel):
    """One episode boundary: an ordered, contiguous run of script or shot units.

    A *section* plan (script first) names the script sections an episode
    covers and has no shots yet; once each section was storyboarded the plan is
    materialised into the shots those sections really produced.  A *shot* plan
    (the compatible entry for hand-built shot lists) names shots directly.
    """

    episode_id: str = Field(min_length=1, max_length=80)
    order: int = Field(ge=1, le=MAX_EPISODES)
    title: str = Field(default="", max_length=200)
    shot_ids: list[str] = Field(default_factory=list, max_length=MAX_SHOTS_PER_EPISODE)
    section_ids: list[str] = Field(default_factory=list, max_length=MAX_SHOTS_PER_EPISODE)
    planned_seconds: float = Field(gt=0, allow_inf_nan=False)
    boundary_note: str = Field(default="", max_length=500)
    episode_script_id: str = Field(
        default="",
        max_length=80,
        description=(
            "The confirmed EpisodeScript contract this episode was built from. "
            "It is the episode's own identity: a delivery without it was cut "
            "by the server, not confirmed by the person."
        ),
    )
    episode_script_sha256: str = Field(default="", max_length=64)


class EpisodePlanPayload(APIModel):
    """The episode plan of one project revision.

    ``covered`` is the authoritative coverage map: every confirmed unit must
    appear exactly once.  A repeated unit (a recap) has to be declared, and an
    undeclared repeat is a hard validation error rather than a silent duplicate
    render.
    """

    schema_version: str = Field(default="1.0", max_length=8)
    mode: str = Field(default="shots", pattern="^(shots|sections)$")
    target_seconds: int = Field(ge=8, le=3600)
    tolerance_seconds: int = Field(default=DEFAULT_TOLERANCE_SECONDS, ge=0, le=900)
    episodes: list[PlannedEpisode] = Field(min_length=1, max_length=MAX_EPISODES)
    allowed_repeats: list[str] = Field(default_factory=list, max_length=200)
    source: dict[str, Any] = Field(default_factory=dict)
    """For a section plan: the outline artifact id/hash the sections came from."""

    @model_validator(mode="after")
    def contiguous_and_complete(self) -> EpisodePlanPayload:
        orders = [episode.order for episode in self.episodes]
        if orders != list(range(1, len(orders) + 1)):
            raise ValueError("EPISODE_ORDER_MUST_BE_CONTIGUOUS_FROM_ONE")
        ids = [episode.episode_id for episode in self.episodes]
        if len(set(ids)) != len(ids):
            raise ValueError("EPISODE_ID_REPEATED")
        if self.mode == "sections":
            seen_sections: list[str] = []
            for episode in self.episodes:
                if not episode.section_ids:
                    raise ValueError(f"EPISODE_HAS_NO_SECTIONS:{episode.episode_id}")
                if len(set(episode.section_ids)) != len(episode.section_ids):
                    raise ValueError(f"EPISODE_REPEATS_A_SECTION:{episode.episode_id}")
                seen_sections.extend(episode.section_ids)
            repeated_sections = {sid for sid in seen_sections if seen_sections.count(sid) > 1}
            if repeated_sections - set(self.allowed_repeats):
                raise ValueError("SECTION_COVERED_TWICE:" + ",".join(sorted(repeated_sections))[:400])
        else:
            for episode in self.episodes:
                if not episode.shot_ids:
                    raise ValueError(f"EPISODE_HAS_NO_SHOTS:{episode.episode_id}")
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

    def section_order(self) -> list[str]:
        return [section_id for episode in self.episodes for section_id in episode.section_ids]

    def materialized(self) -> bool:
        """Whether every episode already names the shots it will produce."""
        return all(episode.shot_ids for episode in self.episodes)


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


def derive_episode_plan_from_sections(
    sections: list[dict[str, Any]],
    *,
    target_seconds: int = DEFAULT_TARGET_SECONDS,
    tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
    source: dict[str, Any] | None = None,
    title_prefix: str = "第{n}集",
) -> EpisodePlanPayload:
    """Plan episodes from the confirmed script *before* any shot exists.

    The units are the outline's production sections in script order, each with
    its estimated narrative duration.  A boundary only ever falls between whole
    sections, so a scene — and every line of dialogue in it — stays inside one
    episode.  When one section alone is longer than the target the episode says
    so; nothing is cut to make a number.  The estimate is a planning value: the
    real duration is measured from the delivered reel.
    """
    ordered = sorted(sections, key=lambda row: int(row.get("order") or 0))
    if not ordered:
        raise ValueError("EPISODE_PLAN_REQUIRES_SECTIONS")
    low = max(1, target_seconds - tolerance_seconds)
    high = target_seconds + tolerance_seconds
    episodes: list[PlannedEpisode] = []
    current: list[dict[str, Any]] = []
    total = 0.0

    def close(note: str) -> None:
        nonlocal current, total
        if not current:
            return
        episodes.append(PlannedEpisode(
            episode_id=f"ep-{len(episodes) + 1:02d}",
            order=len(episodes) + 1,
            title=title_prefix.format(n=len(episodes) + 1),
            shot_ids=[],
            section_ids=[str(row["production_section_id"]) for row in current],
            planned_seconds=round(max(total, 1.0), 3),
            boundary_note=note,
        ))
        current = []
        total = 0.0

    for index, section in enumerate(ordered):
        seconds = float(section.get("estimated_duration_s") or 0.0)
        if current and total + seconds > high and total >= low:
            close("section_boundary_at_target")
        current.append(section)
        total += seconds
        if index == len(ordered) - 1:
            break
        if total >= target_seconds:
            close("section_boundary_at_target")
    close("final_episode")
    if len(episodes) > MAX_EPISODES:
        raise ValueError("EPISODE_PLAN_EXCEEDS_MAX_EPISODES")
    for episode in episodes:
        if episode.planned_seconds > high:
            episode.boundary_note = (
                (episode.boundary_note + "; " if episode.boundary_note else "")
                + f"planned {episode.planned_seconds:.1f}s exceeds {high}s: one scene is longer than the target"
            )
    return EpisodePlanPayload(
        mode="sections",
        target_seconds=target_seconds,
        tolerance_seconds=tolerance_seconds,
        episodes=episodes,
        allowed_repeats=[],
        source=source or {},
    )


def derive_episode_plan_by_count(
    sections: list[dict[str, Any]],
    *,
    episode_count: int,
    target_seconds: int = DEFAULT_TARGET_SECONDS,
    tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
    source: dict[str, Any] | None = None,
    title_prefix: str = "第{n}集",
) -> EpisodePlanPayload:
    """Plan a fixed number of episodes from the confirmed script sections.

    The streamline entry: the person names the episode count and a per-episode
    target length.  The count is the priority — the sections are partitioned
    into exactly ``episode_count`` contiguous groups on whole-section boundaries
    (a scene and its dialogue are never severed), balanced so each episode's
    estimated length stays as close to the total/count ideal as the natural
    boundaries allow.  ``target_seconds`` is reported as the goal and any
    deviation is written into ``boundary_note``; nothing is truncated or invented
    to make a number.  When there are fewer sections than episodes the request is
    refused rather than fabricating an empty episode.
    """
    ordered = sorted(sections, key=lambda row: int(row.get("order") or 0))
    if not ordered:
        raise ValueError("EPISODE_PLAN_REQUIRES_SECTIONS")
    if episode_count < 1:
        raise ValueError("EPISODE_COUNT_INVALID")
    if episode_count > len(ordered):
        raise ValueError("EPISODE_COUNT_EXCEEDS_SECTIONS")
    if episode_count > MAX_EPISODES:
        raise ValueError("EPISODE_PLAN_EXCEEDS_MAX_EPISODES")
    seconds = [float(row.get("estimated_duration_s") or 0.0) for row in ordered]
    total = sum(seconds)
    ideal = total / episode_count if episode_count else total
    # Cumulative prefix sums let a boundary cost be read in O(1).
    prefix = [0.0]
    for value in seconds:
        prefix.append(prefix[-1] + value)
    # Dynamic programming: split the first i sections into k episodes minimising
    # the summed squared deviation from the ideal length.  Squared deviation
    # spreads the length evenly instead of dumping the remainder on the last
    # episode.  Whole sections only — a boundary never falls inside a scene.
    import math

    inf = math.inf
    cost = [[inf] * (episode_count + 1) for _ in range(len(ordered) + 1)]
    back = [[0] * (episode_count + 1) for _ in range(len(ordered) + 1)]
    cost[0][0] = 0.0
    for k in range(1, episode_count + 1):
        for i in range(k, len(ordered) + 1):
            for j in range(k - 1, i):
                if cost[j][k - 1] == inf:
                    continue
                length = prefix[i] - prefix[j]
                candidate = cost[j][k - 1] + (length - ideal) ** 2
                if candidate < cost[i][k]:
                    cost[i][k] = candidate
                    back[i][k] = j
    if cost[len(ordered)][episode_count] == inf:
        raise ValueError("EPISODE_COUNT_NOT_PARTITIONABLE")
    bounds: list[int] = []
    i, k = len(ordered), episode_count
    while k > 0:
        j = back[i][k]
        bounds.append(j)
        i, k = j, k - 1
    bounds.reverse()
    bounds.append(len(ordered))
    high = target_seconds + tolerance_seconds
    episodes: list[PlannedEpisode] = []
    for index in range(episode_count):
        left, right = bounds[index], bounds[index + 1]
        group = ordered[left:right]
        length = round(max(prefix[right] - prefix[left], 1.0), 3)
        note = "balanced_by_episode_count"
        if length > high:
            note += f"; planned {length:.1f}s exceeds {high}s target window"
        elif length < max(1, target_seconds - tolerance_seconds):
            note += f"; planned {length:.1f}s below {target_seconds - tolerance_seconds}s target window"
        episodes.append(PlannedEpisode(
            episode_id=f"ep-{index + 1:02d}",
            order=index + 1,
            title=title_prefix.format(n=index + 1),
            shot_ids=[],
            section_ids=[str(row["production_section_id"]) for row in group],
            planned_seconds=length,
            boundary_note=note,
        ))
    return EpisodePlanPayload(
        mode="sections",
        target_seconds=target_seconds,
        tolerance_seconds=tolerance_seconds,
        episodes=episodes,
        allowed_repeats=[],
        source={**(source or {}), "episode_count": episode_count},
    )


def validate_section_coverage(payload: EpisodePlanPayload, section_ids: Iterable[str]) -> list[str]:
    """Coverage problems of a section plan against the confirmed outline sections."""
    confirmed = list(section_ids)
    planned = payload.section_order()
    problems: list[str] = []
    missing = [sid for sid in confirmed if sid not in set(planned)]
    unknown = sorted(set(planned) - set(confirmed))
    if missing:
        problems.append("SECTIONS_NOT_COVERED:" + ",".join(missing)[:400])
    if unknown:
        problems.append("PLAN_REFERENCES_UNKNOWN_SECTIONS:" + ",".join(unknown)[:400])
    order = [sid for sid in confirmed if sid in set(planned)]
    if [sid for sid in planned if sid in set(confirmed)] != order:
        problems.append("SECTIONS_OUT_OF_SCRIPT_ORDER")
    return problems


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
