"""The one continuity verdict of a storyboard (V4).

Before V4 the continuity of a storyboard was the opinion of one more model
call: it read the plan's prose and reported what it believed contradicted.
Prose cannot be checked, so its findings were arbitrated by another call and
repaired by four more.  A storyboard that states its facts can be checked:
which beat a shot covers, who stands where and which way they face, when every
line is spoken and why the camera moves.

This module is that check.  It calls no model, opens no database and writes
nothing: every function reads a shot board and the drama scene it was shot
from as plain dicts, and returns what is refused, what is only reported and
what the server decides.

What it refuses, each by name so the retry is told what to repair:

* a spoken line that is not the scene's own word for word, said by its own
  speaker, in order and exactly once — nothing is invented, dropped or
  reordered, so there is no hallucinated line and no narration the scene
  never wrote;
* a line that does not fit the sub-shot that performs it, overlaps another,
  or is too fast to say at a natural pace;
* a drama beat no shot covers, or two shots cover;
* a performer placed in a shot whose beats never show them;
* a shot staged somewhere other than the scene's one place;
* an internal cut outside its window, or more than one in a segment;
* a camera move without a motive, prose that disagrees with the move, or a
  crane / orbit / zoom crammed into a two-beat segment;
* the 180-degree axis: a left-right order flipped, or a performer facing away
  from the person the axis puts on their other side.

What it decides: the boundary of every shot.  The director states only the
dramatic reason (``transition_motivation``); the mode — ``cut`` or
``last_frame`` — follows from that reason and from whether an earlier shot of
the same episode exists.  A scene's or an episode's first shot therefore can
never inherit another place's last frame.

What it only reports: a prop that appears from nowhere in a continued take, a
shot so full of speech that nobody breathes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from shine.asset_identity import split_character_names
from shine.literary.drama import spoken_seconds

#: Each sub-shot of a segment gets at least this long: a cut that leaves less
#: gives the renderer no readable image on either side of it.
MIN_SUB_SHOT_SECONDS = 2.0
#: A line may be delivered a little faster than its natural pace, not much.
FASTEST_SPEECH_RATIO = 0.75
#: Above this share of a shot taken by speech, the shot has no room to breathe.
WALL_TO_WALL_SPEECH = 0.85

#: The dramatic reasons a shot continues the take of the shot before it.
CONTINUE_MOTIVATIONS = frozenset({"continuous_action", "emotional_hold"})
#: The only reasons a shot may start on a new spatial arrangement.
BREAK_MOTIVATIONS = frozenset({"time_or_space_jump", "establishing_reset", "match_on_action"})

CAMERA_MOVES = (
    "static", "push_in", "pull_back", "pan", "tilt", "track", "handheld",
    "crane", "orbit", "zoom", "whip_pan",
)
#: Moves too large to sit inside a segment that is already two shots.
BIG_MOVES_IN_CUT_SEGMENT = frozenset({"crane", "orbit", "zoom", "whip_pan"})
#: The word a shot's movement prose must contain for the move it declares.
_MOVE_WORDS = {
    "static": ("static", "still", "locked", "fixed", "held", "holds", "steady", "immobile", "no movement"),
    "push_in": ("push",), "pull_back": ("pull",), "pan": ("pan",), "tilt": ("tilt",),
    "track": ("track", "dolly", "trucking", "following"),
    "handheld": ("handheld", "hand-held"), "crane": ("crane", "jib"),
    "orbit": ("orbit", "arc", "circl"), "zoom": ("zoom",), "whip_pan": ("whip",),
}
_SIDE = {"left": -1, "center": 0, "right": 1}
#: The way a performer faces when they look away from the person the axis puts
#: on their other side.
_FACING_LEFT = frozenset({"facing_left", "three_quarter_left"})
_FACING_RIGHT = frozenset({"facing_right", "three_quarter_right"})


def normalised_dialogue(text: str) -> str:
    """Typography-insensitive form of a line: whitespace and ellipsis/dash
    variants carry no content, so a faithful copy must not fail on them."""
    text = re.sub(r"\s+", "", text or "")
    text = text.replace("……", "…").replace("...", "…")
    text = text.replace("——", "—").replace("--", "—")
    return text


def beat_id(position: int) -> str:
    """The id of a scene's beat: its 1-based place in the scene script."""
    return f"B{position}"


@dataclass
class BoardVerdict:
    """What a shot board is refused for, what is only reported, and what follows.

    ``refusals`` are the gate: any one of them sends the board back to the
    director.  ``warnings`` never block and are stored with the board.
    ``cast`` and ``boundaries`` are what the server decides rather than the
    model: the people of every shot (read from its beats) and how each shot
    joins the one before it.
    """

    refusals: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    cast: dict[str, list[str]] = field(default_factory=dict)
    boundaries: list[dict[str, str]] = field(default_factory=list)
    #: The shots exactly as the verdict judged them, boundaries decided, so a
    #: landing reads the same views instead of reading the board again.
    views: list[ShotView] = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return not self.refusals

    def first_code(self) -> str | None:
        return self.refusals[0]["code"] if self.refusals else None


# --- Reading the board ----------------------------------------------------------


def scene_beats(scene: dict[str, Any]) -> list[dict[str, Any]]:
    """The scene's beats with the ids the board refers to, their people and lines."""
    beats = []
    for position, beat in enumerate(scene.get("beats") or [], start=1):
        if not isinstance(beat, dict):
            continue
        beats.append({
            "id": beat_id(position),
            "characters": [name for entry in beat.get("characters") or []
                           for name in split_character_names(str(entry)) if name],
            "lines": [{"speaker": str(line.get("speaker") or "").strip(),
                       "text": str(line.get("text") or "")}
                      for line in beat.get("spoken_lines") or []
                      if isinstance(line, dict) and str(line.get("text") or "").strip()],
        })
    return beats


@dataclass
class SubShot:
    """One picture of a render segment: the opening, or what an internal cut shows."""

    start_s: float
    end_s: float
    beat_ids: list[str]
    visual: str


@dataclass
class ShotView:
    """One shot as the engine reads it: what the director wrote and where it sits."""

    index: int
    id: str
    duration_s: float
    sub_shots: list[SubShot]
    cast: list[str]
    lines: list[dict[str, Any]]
    mode: str = "cut"
    motivation: str = ""

    @property
    def beat_ids(self) -> list[str]:
        return [beat for sub in self.sub_shots for beat in sub.beat_ids]


def read_board(board: dict[str, Any], scene: dict[str, Any]) -> tuple[list[ShotView], list[dict[str, Any]]]:
    """Normalise a board against its scene, or say why it cannot be read.

    Reading never judges timing or staging; it only pairs each beat id with the
    scene's beat and each shot with the people those beats show.  An id the
    scene does not have makes the board unreadable, because nothing after it
    can be checked.
    """
    beats = {beat["id"]: beat for beat in scene_beats(scene)}
    views: list[ShotView] = []
    refusals: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, shot in enumerate(board.get("shots") or []):
        shot_id = str(shot.get("id") or "")
        if shot_id in seen:
            refusals.append({"code": "DUPLICATE_SHOT_ID", "shot_id": shot_id})
        seen.add(shot_id)
        duration = float(shot.get("duration_s") or 0)
        opening = [str(beat) for beat in shot.get("beat_ids") or []]
        if not opening:
            refusals.append({"code": "SHOT_WITHOUT_BEAT", "shot_id": shot_id})
        sub_shots = [SubShot(0.0, duration, opening, str(shot.get("visual_description") or ""))]
        unknown = [beat for beat in opening if beat not in beats]
        for cut in shot.get("internal_cuts") or []:
            cut_beats = [str(beat) for beat in cut.get("beat_ids") or []]
            if not cut_beats:
                refusals.append({"code": "CUT_WITHOUT_BEAT", "shot_id": shot_id})
            unknown += [beat for beat in cut_beats if beat not in beats]
            sub_shots.append(SubShot(float(cut.get("start_s") or 0), duration, cut_beats,
                                     str(cut.get("visual_description") or "")))
        # Every sub-shot ends where the next one begins.
        for position in range(len(sub_shots) - 1):
            sub_shots[position].end_s = sub_shots[position + 1].start_s
        if unknown:
            refusals.append({"code": "UNKNOWN_BEAT", "shot_id": shot_id, "beat_ids": sorted(set(unknown))})
        covered = [beat for sub in sub_shots for beat in sub.beat_ids if beat in beats]
        cast = list(dict.fromkeys(
            [name for beat in covered for name in beats[beat]["characters"]]
            + [line["speaker"] for beat in covered for line in beats[beat]["lines"] if line["speaker"]]))
        lines = sorted(({**line, "shot_id": shot_id} for line in shot.get("dialogue") or [] if isinstance(line, dict)),
                       key=lambda line: float(line.get("start_s") or 0))
        views.append(ShotView(index, shot_id, duration, sub_shots, cast, lines))
    return views, refusals


def english_prose_fields(board: dict[str, Any]) -> list[tuple[str, str]]:
    """Every board field the H3 prompt is compiled from, as ``(field, text)``.

    The one list the verdict reads, so a field can never be prompt prose to
    the compiler and be skipped by the language check.
    """
    fields: list[tuple[str, str]] = []
    for index, shot in enumerate(board.get("shots") or []):
        root = f"shots[{index}]"
        camera = shot.get("camera") or {}
        blocking = shot.get("blocking") or {}
        environment = shot.get("environment") or {}
        fields += [(f"{root}.visual_description", str(shot.get("visual_description") or "")),
                   (f"{root}.camera.composition", str(camera.get("composition") or "")),
                   (f"{root}.camera.movement", str(camera.get("movement") or "")),
                   (f"{root}.camera.lighting", str(camera.get("lighting") or "")),
                   (f"{root}.ambience", str(shot.get("ambience") or "")),
                   (f"{root}.music", str(shot.get("music") or "")),
                   (f"{root}.entry_state", str(shot.get("entry_state") or "")),
                   (f"{root}.exit_state", str(shot.get("exit_state") or ""))]
        fields += [(f"{root}.internal_cuts[{cut_index}].visual_description",
                    str((cut or {}).get("visual_description") or ""))
                   for cut_index, cut in enumerate(shot.get("internal_cuts") or [])]
        fields += [(f"{root}.timeline[{window_index}].{key}", str((window or {}).get(key) or ""))
                   for window_index, window in enumerate(shot.get("timeline") or [])
                   for key in ("action", "expression", "camera")]
        for place_index, placement in enumerate(blocking.get("placements") or []):
            fields.append((f"{root}.blocking.placements[{place_index}].posture",
                           str((placement or {}).get("posture") or "")))
            fields += [(f"{root}.blocking.placements[{place_index}].props", str(prop))
                       for prop in (placement or {}).get("props") or []]
        fields += [(f"{root}.environment.{key}", str(environment.get(key) or ""))
                   for key in ("depth", "light", "micro_motion")]
    return fields


# --- The verdict ----------------------------------------------------------------


def review_scene_board(board: dict[str, Any], scene: dict[str, Any], context: dict[str, Any]) -> BoardVerdict:
    """The zero-token verdict on one shot board.

    ``context`` carries the two facts that are not in the board: the frozen
    per-shot ceiling (``max_shot_seconds``) and whether an earlier shot of the
    same episode exists (``continuity_predecessor``).  Everything the verdict
    needs about the story it reads from the scene itself.
    """
    verdict = BoardVerdict()
    views, unreadable = read_board(board, scene)
    verdict.refusals += unreadable
    verdict.views = views
    if unreadable or not views:
        if not unreadable:
            verdict.refusals.append({"code": "BOARD_WITHOUT_SHOTS"})
        return verdict
    shots = board["shots"]
    beats = {beat["id"]: beat for beat in scene_beats(scene)}
    registered_locations = _location_names(context.get("registered_locations"))
    for view in views:
        verdict.cast[view.id] = list(view.cast)
        _judge_segment(verdict, view, shots[view.index], context.get("max_shot_seconds"),
                       str(scene.get("location") or "").strip(),
                       registered_locations=registered_locations)
        _judge_cast(verdict, view, shots[view.index])
        _judge_camera(verdict, view, shots[view.index])
    _judge_coverage(verdict, views, beats)
    _judge_dialogue(verdict, views, beats)
    _decide_boundaries(verdict, views, shots, bool(context.get("continuity_predecessor")))
    _judge_axis(verdict, views, shots, previous_blocking=context.get("previous_blocking"))
    _report_props(verdict, views, shots)
    return verdict


def _location_names(raw: Any) -> set[str]:
    names: set[str] = set()
    for item in raw or ():
        name = item.get("name") if isinstance(item, dict) else item
        text = str(name or "").strip()
        if text:
            names.add(text)
    return names


def _judge_segment(verdict: BoardVerdict, view: ShotView, shot: dict[str, Any],
                   cap: float | None, place: str,
                   *, registered_locations: set[str] | None = None) -> None:
    if cap is not None and view.duration_s > float(cap) + 1e-6:
        verdict.refusals.append({"code": "SHOT_DURATION_OVER_LIMIT", "shot_id": view.id,
                                 "duration_s": view.duration_s, "max_shot_seconds": float(cap)})
    location = str(shot.get("location") or "").strip()
    allowed = ({place} if place else set()) | set(registered_locations or ())
    if place and location not in allowed:
        # A scene stays in its own place unless switching cleanly between
        # places explicitly registered in SeriesArchitecture.
        verdict.refusals.append({"code": "SHOT_LOCATION_CONFLICT", "shot_id": view.id,
                                 "location": location, "scene_location": place})
    cuts = shot.get("internal_cuts") or []
    if len(cuts) > 1:
        verdict.refusals.append({"code": "INTERNAL_CUT_COUNT", "shot_id": view.id, "count": len(cuts)})
    starts = [sub.start_s for sub in view.sub_shots[1:]]
    if starts != sorted(set(starts)) or any(not 0.0 < start < view.duration_s for start in starts):
        verdict.refusals.append({"code": "EDITORIAL_TIMING_CONFLICT", "shot_id": view.id,
                                 "cut_starts_s": starts, "duration_s": view.duration_s})
        return
    for sub in view.sub_shots:
        if sub.end_s - sub.start_s < MIN_SUB_SHOT_SECONDS - 1e-9:
            verdict.refusals.append({"code": "INTERNAL_CUT_WINDOW", "shot_id": view.id,
                                     "sub_shot_seconds": round(sub.end_s - sub.start_s, 2),
                                     "min_sub_shot_seconds": MIN_SUB_SHOT_SECONDS})


def _judge_cast(verdict: BoardVerdict, view: ShotView, shot: dict[str, Any]) -> None:
    for placement in (shot.get("blocking") or {}).get("placements") or []:
        name = str(placement.get("character") or "").strip()
        if name and name not in view.cast:
            # Somebody stands in the picture whom none of the shot's beats show.
            verdict.refusals.append({"code": "PLACEMENT_NOT_IN_CAST", "shot_id": view.id, "character": name})


def _judge_camera(verdict: BoardVerdict, view: ShotView, shot: dict[str, Any]) -> None:
    camera = shot.get("camera") or {}
    move = str(camera.get("move") or "static")
    prose = str(camera.get("movement") or "").lower()
    if move != "static" and not str(camera.get("motive") or "").strip():
        verdict.refusals.append({"code": "CAMERA_MOVE_WITHOUT_MOTIVE", "shot_id": view.id, "move": move})
    if not any(word in prose for word in _MOVE_WORDS.get(move, ())):
        verdict.refusals.append({"code": "CAMERA_PROSE_MISMATCH", "shot_id": view.id, "move": move})
    if len(view.sub_shots) > 1 and move in BIG_MOVES_IN_CUT_SEGMENT:
        verdict.refusals.append({"code": "CAMERA_MOVE_IN_CUT_SEGMENT", "shot_id": view.id, "move": move})


def _judge_coverage(verdict: BoardVerdict, views: list[ShotView], beats: dict[str, Any]) -> None:
    covered = [beat for view in views for beat in view.beat_ids]
    expected = list(beats)
    missing = [beat for beat in expected if beat not in covered]
    if missing:
        verdict.refusals.append({"code": "BEAT_NOT_COVERED", "beat_ids": missing})
        return
    repeated = [beat for beat in expected if covered.count(beat) > 1]
    if repeated:
        verdict.refusals.append({"code": "BEAT_COVERED_TWICE", "beat_ids": repeated})
        return
    if covered != expected:
        first = next(index for index, pair in enumerate(zip(covered, expected, strict=True)) if pair[0] != pair[1])
        verdict.refusals.append({"code": "BEAT_OUT_OF_ORDER", "beat_ids": covered[first:first + 2],
                                 "expected": expected[first:first + 2]})


def _judge_dialogue(verdict: BoardVerdict, views: list[ShotView], beats: dict[str, Any]) -> None:
    scene_lines = {normalised_dialogue(line["text"]) for beat in beats.values() for line in beat["lines"]}
    for view in views:
        expected = [(beat, line) for sub in view.sub_shots for beat in sub.beat_ids for line in beats[beat]["lines"]]
        if len(view.lines) > len(expected):
            verdict.refusals.append({
                "code": "DIALOGUE_NOT_IN_SCENE", "shot_id": view.id,
                "offending_line": str(view.lines[len(expected)].get("text") or "")[:200]})
        elif len(view.lines) < len(expected):
            verdict.refusals.append({"code": "DIALOGUE_DROPPED", "shot_id": view.id,
                                     "missing_lines": [line["text"] for _, line in expected[len(view.lines):]][:5]})
        previous_end, spoken = 0.0, 0.0
        for index, (line, (beat, want)) in enumerate(zip(view.lines, expected, strict=False)):
            got = normalised_dialogue(str(line.get("text") or ""))
            if got != normalised_dialogue(want["text"]):
                # A line the scene does speak, but not here, is out of order;
                # a line the scene never speaks was invented.
                known = got in scene_lines
                verdict.refusals.append({
                    "code": "DIALOGUE_OUT_OF_ORDER" if known else "DIALOGUE_NOT_IN_SCENE",
                    "shot_id": view.id, "line": index,
                    "offending_line": str(line.get("text") or "")[:200],
                    **({} if known else {"expected_line": want["text"][:200]})})
                continue
            speaker = str(line.get("speaker_id") or "").strip()
            if speaker and speaker != want["speaker"]:
                verdict.refusals.append({"code": "DIALOGUE_SPEAKER_CHANGED", "shot_id": view.id, "line": index,
                                         "speaker": speaker, "scene_speaker": want["speaker"]})
            start, end = float(line.get("start_s") or 0), float(line.get("end_s") or 0)
            if end <= start:
                continue  # the contract's own validator names this before the engine runs
            window = next(sub for sub in view.sub_shots if beat in sub.beat_ids)
            if start < window.start_s - 1e-6 or end > window.end_s + 1e-6:
                verdict.refusals.append({"code": "DIALOGUE_OUTSIDE_SUB_SHOT", "shot_id": view.id, "line": index,
                                         "start_s": start, "end_s": end,
                                         "sub_shot": [round(window.start_s, 3), round(window.end_s, 3)]})
            if start < previous_end - 1e-6:
                verdict.refusals.append({"code": "DIALOGUE_OVERLAP", "shot_id": view.id, "line": index})
            previous_end = max(previous_end, end)
            pace = spoken_seconds(want["text"])
            if end - start < pace * FASTEST_SPEECH_RATIO - 1e-6:
                verdict.refusals.append({"code": "DIALOGUE_TOO_FAST_TO_SPEAK", "shot_id": view.id, "line": index,
                                         "characters": len(re.sub(r"\s", "", want["text"])),
                                         "needs_seconds": round(pace * FASTEST_SPEECH_RATIO, 2),
                                         "given_seconds": round(end - start, 2)})
            spoken += end - start
        if view.duration_s and spoken > view.duration_s * WALL_TO_WALL_SPEECH:
            verdict.warnings.append({"code": "DIALOGUE_WALL_TO_WALL", "shot_id": view.id,
                                     "spoken_seconds": round(spoken, 2), "duration_s": view.duration_s})


def _decide_boundaries(verdict: BoardVerdict, views: list[ShotView], shots: list[dict[str, Any]],
                       has_predecessor: bool) -> None:
    """The mode of every boundary, from the director's stated reason."""
    for view in views:
        motivation = str(shots[view.index].get("transition_motivation") or "")
        continues = motivation in CONTINUE_MOTIVATIONS
        if view.index > 0 and continues:
            prev_loc = str(shots[view.index - 1].get("location") or "").strip()
            cur_loc = str(shots[view.index].get("location") or "").strip()
            if prev_loc and cur_loc and prev_loc != cur_loc:
                # Never carry a final frame across two distinct physical places.
                verdict.warnings.append({
                    "code": "CONTINUATION_IGNORED_ON_LOCATION_CHANGE",
                    "shot_id": view.id,
                    "from": prev_loc,
                    "to": cur_loc,
                })
                continues = False
        if view.index == 0 and continues and not has_predecessor:
            # Nothing of an earlier episode or unit exists to continue: the
            # shot opens on a cut, and the board records that it does.
            verdict.warnings.append({"code": "CONTINUATION_IGNORED_WITHOUT_PREDECESSOR", "shot_id": view.id})
            continues = False
        view.mode, view.motivation = ("last_frame" if continues else "cut"), motivation
        verdict.boundaries.append({"shot_id": view.id, "mode": view.mode, "motivation": motivation})


def _placed(shot: dict[str, Any]) -> list[tuple[str, int, str]]:
    rows = []
    for placement in (shot.get("blocking") or {}).get("placements") or []:
        name = str(placement.get("character") or "").strip()
        if name:
            side = _SIDE.get(str(placement.get("zone") or "").partition("_")[0], 0)
            rows.append((name, side, str(placement.get("facing") or "")))
    return rows


def _judge_axis(
    verdict: BoardVerdict,
    views: list[ShotView],
    shots: list[dict[str, Any]],
    *,
    previous_blocking: dict[str, Any] | None = None,
) -> None:
    """The 180-degree rule, read from where the board puts people.

    The first two performers a group shows together fix which of them stands
    on which side; every later pair shown together adds its own order.  In
    every later shot of the group two performers may not swap sides, and a
    performer may not look away from the person they were last shown with.
    A new arrangement needs a new group, and a group may only begin at a
    boundary that can carry people to new places.  When ``previous_blocking``
    is supplied from the episode's prior landed shot, the opening shot of the
    board inherits that axis and left-right order unless it explicitly opens
    on a spatial-break cut.  A shot with three or more performers is a triangle
    this rule does not model: order is still checked in it, facing is not.
    """
    label: str | None = None
    order: set[tuple[str, str]] = set()
    partner: dict[str, str] = {}
    if isinstance(previous_blocking, dict):
        prev_axis = str(previous_blocking.get("axis") or "").strip()
        if prev_axis:
            label = prev_axis
        prev_rows = _placed({"blocking": previous_blocking})
        if len(prev_rows) == 2:
            (first, first_side, _), (second, second_side, _) = prev_rows
            if first != second and first_side != second_side:
                order.add((first, second) if first_side < second_side else (second, first))
            partner[first], partner[second] = second, first
    for view in views:
        shot = shots[view.index]
        axis = str((shot.get("blocking") or {}).get("axis") or "").strip()
        if view.index == 0 and isinstance(previous_blocking, dict) and view.motivation in BREAK_MOTIVATIONS:
            order, partner = set(), {}
        elif label is not None and axis != label:
            if view.motivation not in BREAK_MOTIVATIONS:
                verdict.refusals.append({"code": "AXIS_CHANGED_WITHOUT_BREAK", "shot_id": view.id,
                                         "motivation": view.motivation})
                continue
            order, partner = set(), {}
        label = axis
        rows = _placed(shot)
        if len(rows) == 2:
            (first, first_side, _), (second, second_side, _) = rows
            if first != second and first_side != second_side:
                order.add((first, second) if first_side < second_side else (second, first))
            partner[first], partner[second] = second, first
        sides = {name: side for name, side, _ in rows}
        facings = {name: facing for name, _, facing in rows}
        for left, right in order:
            if left in sides and right in sides and sides[left] > sides[right]:
                verdict.refusals.append({"code": "AXIS_ORDER_FLIPPED", "shot_id": view.id,
                                         "left": left, "right": right})
        if len(rows) > 2:
            continue
        for name, facing in facings.items():
            other = partner.get(name)
            if other is None or other == name:
                continue
            away = (_FACING_LEFT if (name, other) in order
                    else _FACING_RIGHT if (other, name) in order else None)
            if away and facing in away:
                verdict.refusals.append({"code": "AXIS_FACING_REVERSED", "shot_id": view.id,
                                         "character": name, "faces": facing, "toward": other})


def _report_props(verdict: BoardVerdict, views: list[ShotView], shots: list[dict[str, Any]]) -> None:
    for view in views[1:]:
        held = _props(shots[view.index])
        if view.mode == "last_frame" and held - _props(shots[view.index - 1]):
            # One take cannot cut to a prop nobody picked up; the picture may
            # still show it being taken, so this is reported, not refused.
            for prop in sorted(held - _props(shots[view.index - 1])):
                verdict.warnings.append({"code": "PROP_FROM_NOWHERE", "shot_id": view.id, "prop": prop})


def _props(shot: dict[str, Any]) -> set[str]:
    return {str(prop).strip() for placement in (shot.get("blocking") or {}).get("placements") or []
            for prop in placement.get("props") or [] if str(prop).strip()}


# --- What a refusal says ---------------------------------------------------------

#: One plain sentence per refusal, for the person who sees a blocked scene.
REFUSAL_LABELS = {
    "BOARD_WITHOUT_SHOTS": "镜头板里没有任何镜头",
    "DUPLICATE_SHOT_ID": "镜头板里有两个镜头用了同一个编号",
    "SHOT_WITHOUT_BEAT": "有镜头没有说明拍的是哪个动作拍",
    "UNKNOWN_BEAT": "镜头引用了这一场里不存在的动作拍",
    "CUT_WITHOUT_BEAT": "镜内切没有说明切到哪个动作拍",
    "BEAT_NOT_COVERED": "有戏剧动作拍没有被任何镜头拍到",
    "BEAT_COVERED_TWICE": "同一个动作拍被两个镜头重复拍到",
    "BEAT_OUT_OF_ORDER": "镜头顺序和这一场戏的顺序不一致",
    "DIALOGUE_NOT_IN_SCENE": "镜头里有台词不是这一场戏写出来的",
    "DIALOGUE_DROPPED": "这一场戏的台词被漏掉了",
    "DIALOGUE_OUT_OF_ORDER": "台词顺序和这一场戏不一致",
    "DIALOGUE_SPEAKER_CHANGED": "台词的说话人被换了",
    "DIALOGUE_OUTSIDE_SUB_SHOT": "有台词落在了不属于它的画面之外",
    "DIALOGUE_OVERLAP": "两句台词在时间上重叠",
    "DIALOGUE_TOO_FAST_TO_SPEAK": "台词分到的时间按正常语速说不完",
    "PLACEMENT_NOT_IN_CAST": "镜头里站了这场戏里没有的人",
    "SHOT_LOCATION_CONFLICT": "镜头拍的不是已登记的场景地点",
    "INTERNAL_CUT_COUNT": "一个渲染段里有不止一次镜内切",
    "INTERNAL_CUT_WINDOW": "镜内切离镜头开头或结尾太近，两边都看不清",
    "CAMERA_MOVE_WITHOUT_MOTIVE": "运镜没有说明理由",
    "CAMERA_PROSE_MISMATCH": "运镜的文字描述和声明的机位运动对不上",
    "CAMERA_MOVE_IN_CUT_SEGMENT": "带镜内切的镜头里用了过大的运镜",
    "AXIS_ORDER_FLIPPED": "人物的左右站位跨过了 180° 轴线",
    "AXIS_FACING_REVERSED": "人物的视线背离了对话对象，轴线错位",
    "AXIS_CHANGED_WITHOUT_BREAK": "人物站位在没有转场理由的地方变了",
    "EXCLUDED_REFERENCE_USED": "镜头把风格图或末帧图当成了参考图",
}


def refusal_label(code: str | None) -> str | None:
    """The refusal's one sentence for a person, or ``None`` for a code this engine does not refuse."""
    return REFUSAL_LABELS.get(code or "")


def refusal_codes() -> tuple[str, ...]:
    """Every code this engine refuses by, for the one place that records refusal reasons."""
    return tuple(REFUSAL_LABELS)


#: The same refusals as one instruction each, for the director's next answer.
#: The braces are filled from the verdict's own details.
_REPAIRS = {
    "BOARD_WITHOUT_SHOTS": "The previous answer had no shots. Direct the whole scene as at least one shot.",
    "DUPLICATE_SHOT_ID": "The previous answer used one shot id twice ({where}). Give every shot its own id.",
    "SHOT_WITHOUT_BEAT": "The previous answer has a shot with no beat_ids ({where}). Every shot names the scene "
                         "beats it opens on.",
    "CUT_WITHOUT_BEAT": "The previous answer has an internal cut with no beat_ids ({where}). A cut always moves to "
                        "a later beat and names it.",
    "UNKNOWN_BEAT": "The previous answer named beats the scene does not have ({beat_ids}). Beat i of the scene's "
                    "beats is B i: copy the ids from inputs, invent none.",
    "BEAT_NOT_COVERED": "The previous answer left scene beats without any shot ({beat_ids}). Every beat appears "
                        "exactly once, in order, as a shot's opening beat_ids or inside a cut.",
    "BEAT_COVERED_TWICE": "The previous answer shot these beats more than once ({beat_ids}). Each beat belongs to "
                          "exactly one shot.",
    "BEAT_OUT_OF_ORDER": "The previous answer shot the beats out of the scene's order (got {beat_ids}; the scene "
                         "has {expected}). Shoot the beats in the order the scene lists them.",
    "DIALOGUE_NOT_IN_SCENE": "The previous answer has a spoken line the scene does not say ({where}). Copy every "
                             "line character-for-character from the scene's own spoken_lines with its speaker, and "
                             "write no line the scene does not contain.",
    "DIALOGUE_DROPPED": "The previous answer left out spoken lines ({where}): {missing_lines}. Every line of a "
                        "shot's beats is in that shot's dialogue, changed in no way.",
    "DIALOGUE_OUT_OF_ORDER": "The previous answer speaks the lines out of the scene's order ({where}). Keep each "
                             "line in the shot that performs its beat and in the scene's order.",
    "DIALOGUE_SPEAKER_CHANGED": "The previous answer gave a line to another speaker ({where}). Each line is said "
                                "by the speaker the scene names.",
    "DIALOGUE_OUTSIDE_SUB_SHOT": "The previous answer placed a line outside the picture that performs its beat "
                                 "({where}). A line's start_s and end_s, counted from the start of this shot, lie "
                                 "inside the sub-shot that covers its beat.",
    "DIALOGUE_OVERLAP": "The previous answer let two lines overlap in time ({where}). Two lines of one shot never "
                        "overlap.",
    "DIALOGUE_TOO_FAST_TO_SPEAK": "The previous answer gave a line too little time ({where}). A line of "
                                  "{characters} characters needs at least {needs_seconds} seconds: give it that "
                                  "long plus a little air, and lengthen the shot if it must.",
    "PLACEMENT_NOT_IN_CAST": "The previous answer placed someone the shot's beats do not show ({where}): "
                             "{character}. blocking places only the people its beats name.",
    "SHOT_LOCATION_CONFLICT": "The previous answer staged a shot at an unregistered location ({where}): "
                              "{location}. Keep each shot at the scene's own location, or another exact name "
                              "from production_context.registered_locations when stepping across spaces.",
    "INTERNAL_CUT_COUNT": "The previous answer put more than one internal cut in a shot ({where}). A render "
                          "segment carries at most one cut.",
    "INTERNAL_CUT_WINDOW": "The previous answer cut too close to the start or the end of a shot ({where}): "
                           "{sub_shot_seconds} s on one side of the cut. Leave at least {min_sub_shot_seconds} s "
                           "on each side.",
    "CAMERA_MOVE_WITHOUT_MOTIVE": "The previous answer moved the camera without saying why ({where}). "
                                  "camera.motive states the reason in one clause, or camera.move stays static.",
    "CAMERA_PROSE_MISMATCH": "The previous answer's camera.movement does not match its camera.move ({where}): "
                             "move is {move}. Name that move in the prose, or write 'The camera holds still.' "
                             "for static.",
    "CAMERA_MOVE_IN_CUT_SEGMENT": "The previous answer used a {move} in a shot that has an internal cut "
                                  "({where}). Such a shot keeps the camera static or small.",
    "AXIS_ORDER_FLIPPED": "The previous answer swapped the sides of two performers ({where}): {left} stands left "
                          "of {right} in this scene. Keep the same left-right order in every shot of the "
                          "arrangement.",
    "AXIS_FACING_REVERSED": "The previous answer turned a performer away from the person they face ({where}): "
                            "{character} faces {faces} but {toward} is on their other side. The performer on "
                            "the left faces right, the one on the right faces left.",
    "AXIS_CHANGED_WITHOUT_BREAK": "The previous answer changed blocking.axis at a boundary that cannot carry "
                                  "people to new places ({where}). Keep the label, or change it only at a "
                                  "time_or_space_jump, establishing_reset or match_on_action cut.",
    "EXCLUDED_REFERENCE_USED": "The previous answer listed a style or last-frame image in asset_ids. Those are "
                               "never shot references: list only character, place and prop masters.",
}


class _Detail(dict):
    """A detail the verdict did not record reads as a question mark, never as a crash."""

    def __missing__(self, key: str) -> str:
        return "?"


def repair_note(code: str, refusals: list[dict[str, Any]]) -> str | None:
    """The one instruction a retry may add, in the verdict's own words.

    A refusal without a named reason repeats itself.  The note says what the
    engine refused and where — which shot, which line, who stands where — and
    carries no answer text beyond the line the engine itself names.
    """
    template = _REPAIRS.get(code)
    if template is None:
        return None
    details = [item for item in refusals if item.get("code") == code]
    first = details[0] if details else {}

    def where(item: dict[str, Any]) -> str:
        if not item.get("shot_id"):
            return "the board"
        return (f"shot {item['shot_id']}, line {item['line'] + 1}" if isinstance(item.get("line"), int)
                else f"shot {item['shot_id']}")

    values = _Detail(first)
    values.update(
        where="; ".join(where(item) for item in details[:5]) or "the board",
        sub_shot_seconds=first.get("sub_shot_seconds", "?"),
        min_sub_shot_seconds=first.get("min_sub_shot_seconds", MIN_SUB_SHOT_SECONDS),
        characters=first.get("characters", "?"), needs_seconds=first.get("needs_seconds", "?"))
    return " " + template.format_map(values)
