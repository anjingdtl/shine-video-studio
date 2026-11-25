"""The drama layer between a novel and the storyboard (V4, Layers 1 and 2).

A novel's prose is not a shooting script.  Psychology, summary and narration
cannot be filmed, and a paragraph of reported speech cannot be spoken inside one
H3 shot.  So a scene is turned into a structured drama scene script BEFORE the
storyboard crew reads it, and the numbers that decide whether a line can be
spoken are the server's, never the model's.

This module is the deterministic half of that layer.  It owns no state, calls no
model and writes nothing: the model writes a ``DramaSceneScript`` or a
``SeriesArchitecture`` proposal through the ordinary crew dispatcher, and
everything here reads a payload as a plain dict.

One fact, one verdict, one executor:

* **speech time** is computed here from the characters of a line at a natural
  pace; a line or a beat that cannot be spoken inside its window is refused by
  name before any shot exists;
* **scene budgets** are the confirmed per-episode seconds shared out by the
  dramatic weight the architecture gave each scene, so the writer aims for a
  length instead of a landing gate cutting the tail of an episode;
* **the creative text** of a scene script is rendered by one function, so the
  storyboard crew and the source-range verifier can never read two versions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# --- Numbers the server owns ----------------------------------------------------

#: Natural Mandarin pace: spoken characters per second.  4 characters is a
#: comfortable line, 24 is the longest line that still fits a shot with room
#: for the action around it (about 5.8 s).
SPEECH_CHARS_PER_SECOND = 4.2
#: The shortest a spoken line lasts, however few its characters.
MIN_LINE_SECONDS = 1.2
#: One line longer than this cannot be delivered at a natural pace inside a
#: shot that also has to show the action: the writer splits it.
MAX_LINE_SECONDS = 6.5
#: All the lines of one beat together; a beat carries at most this much speech.
MAX_BEAT_SPEECH_SECONDS = 7.5
#: The action a beat shows around its speech, and the pause between two lines.
BEAT_ACTION_SECONDS = 2.0
LINE_GAP_SECONDS = 0.4
#: A scene is never asked for less than one render segment.
MIN_SCENE_SECONDS = 4.0

TIME_OF_DAY_LABELS = {
    "dawn": "清晨", "day": "白天", "dusk": "黄昏", "night": "夜晚",
    "late_night": "深夜", "unspecified": "时间未定",
}
TRANSITIONS = (
    "episode_open", "continuous_time", "same_day_later", "time_jump",
    "location_switch", "flashback_in", "flashback_out",
)

#: Verbs of an inner state.  Nothing here can be photographed: a beat that uses
#: one has not been dramatized, it has been summarised.  Emotions a performer
#: can show (fear, resolve) are deliberately absent: "she steps back, afraid"
#: is filmable, "she is afraid of what she will remember" is not.
_INNER_STATE = re.compile(
    r"心想|暗想|心中想|心里想|内心|心底|心头|想起|回忆起|回忆着|回忆|记得|意识到|明白到|"
    r"知道|感到|觉得|认为|以为|思绪|念头"
)
_SPOKEN_CHARACTERS = re.compile(r"\s")


def spoken_seconds(text: str) -> float:
    """Seconds a line lasts at a natural pace: its characters over the rate.

    Punctuation counts: a comma or an ellipsis is a pause the performer takes.
    """
    return max(MIN_LINE_SECONDS, len(_SPOKEN_CHARACTERS.sub("", text or "")) / SPEECH_CHARS_PER_SECOND)


def beat_speech_seconds(beat: dict[str, Any]) -> float:
    lines = beat.get("spoken_lines") or []
    if not lines:
        return 0.0
    return sum(spoken_seconds(str(line.get("text") or "")) for line in lines if isinstance(line, dict))


def beat_seconds(beat: dict[str, Any]) -> float:
    """The screen time one beat takes: its action, plus its speech with a breath between lines."""
    lines = [line for line in (beat.get("spoken_lines") or []) if isinstance(line, dict) and line.get("text")]
    speech = beat_speech_seconds(beat) + LINE_GAP_SECONDS * max(0, len(lines) - 1)
    return round(BEAT_ACTION_SECONDS + speech, 2)


def scene_seconds(payload: dict[str, Any]) -> float:
    """The screen time a whole drama scene script asks for."""
    return round(sum(beat_seconds(beat) for beat in payload.get("beats") or [] if isinstance(beat, dict)), 2)


# --- The L0 verdict on one scene ------------------------------------------------


@dataclass
class DramaVerdict:
    """What a drama scene script gets refused for, and what is only reported.

    ``refusals`` are the deterministic gate: any one of them sends the answer
    back to the writer.  ``warnings`` never block; they are stored with the
    scene so a person can see what the server chose not to trust.
    """

    refusals: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return not self.refusals

    def first_code(self) -> str | None:
        return self.refusals[0]["code"] if self.refusals else None


def check_drama_scene(
    payload: dict[str, Any],
    *,
    cast: tuple[str, ...] | list[str] = (),
    locations: tuple[str, ...] | list[str] = (),
    transition: str | None = None,
) -> DramaVerdict:
    """The zero-token verdict on one drama scene script.

    Speech that cannot fit its window is a refusal: the model is asked to split
    it, and nothing is shortened here.  An inner state, a character the story
    never established or an unregistered place is reported, not refused: those
    are judgement calls the storyboard and the material stage still check.
    """
    verdict = DramaVerdict()
    location = str(payload.get("location") or "").strip()
    if not location:
        verdict.refusals.append({"code": "DRAMA_LOCATION_MISSING"})
    elif locations and location not in set(locations):
        verdict.refusals.append({"code": "DRAMA_LOCATION_UNKNOWN", "location": location})
    beats = [beat for beat in payload.get("beats") or [] if isinstance(beat, dict)]
    if not beats:
        verdict.refusals.append({"code": "DRAMA_BEATS_MISSING"})
    if transition == "continuous_time" and not str(payload.get("entry_bridge") or "").strip():
        # A scene that continues the previous one must say how it picks it up.
        verdict.refusals.append({"code": "DRAMA_ENTRY_BRIDGE_MISSING"})
    known = set(cast)
    for beat_index, beat in enumerate(beats):
        if not str(beat.get("visible_action") or "").strip():
            verdict.refusals.append({"code": "DRAMA_ACTION_MISSING", "beat": beat_index})
        for line_index, line in enumerate(beat.get("spoken_lines") or []):
            if not isinstance(line, dict):
                continue
            text = str(line.get("text") or "")
            if not text.strip():
                verdict.refusals.append({"code": "DRAMA_LINE_EMPTY", "beat": beat_index, "line": line_index})
                continue
            if spoken_seconds(text) > MAX_LINE_SECONDS + 1e-9:
                verdict.refusals.append({
                    "code": "DRAMA_LINE_TOO_LONG", "beat": beat_index, "line": line_index,
                    "characters": len(_SPOKEN_CHARACTERS.sub("", text)),
                    "seconds": round(spoken_seconds(text), 2),
                    "max_characters": int(MAX_LINE_SECONDS * SPEECH_CHARS_PER_SECOND),
                })
        if beat_speech_seconds(beat) > MAX_BEAT_SPEECH_SECONDS + 1e-9:
            verdict.refusals.append({
                "code": "DRAMA_BEAT_SPEECH_TOO_LONG", "beat": beat_index,
                "seconds": round(beat_speech_seconds(beat), 2),
            })
        inner = _INNER_STATE.search(str(beat.get("visible_action") or ""))
        if inner:
            verdict.warnings.append({
                "code": "DRAMA_INNER_STATE_REPORTED", "beat": beat_index, "word": inner.group(0),
            })
        beat_chars = {
            name.strip()
            for name in beat.get("characters") or []
            if isinstance(name, str) and name.strip()
        }
        for name in beat.get("characters") or []:
            if isinstance(name, str) and name.strip() and known and name.strip() not in known:
                verdict.warnings.append({"code": "DRAMA_CHARACTER_UNESTABLISHED", "beat": beat_index,
                                         "character": name.strip()})
        # A known cast member explicitly named in visible_action must be listed
        # in beat.characters; omitting an on-screen bystander caused downstream
        # shots to render a two-person description with only one bound subject.
        action_text = str(beat.get("visible_action") or "")
        for member in known:
            if len(member) < 2 or member in beat_chars:
                continue
            if member in action_text:
                verdict.refusals.append({
                    "code": "DRAMA_VISIBLE_CHARACTER_OMITTED",
                    "beat": beat_index,
                    "character": member,
                })
    return verdict


# --- Scene budgets --------------------------------------------------------------


def scene_budgets(architecture: dict[str, Any] | None, plan: dict[str, Any]) -> dict[str, float]:
    """The writing budget of every scene, in seconds.

    Each episode's confirmed ``target_seconds`` is shared among its scenes by
    the dramatic weight the architecture gave them, so a scene carries the
    episode's goal as a length to write to.  The numbers are the server's: the
    architecture only ranks the scenes, and its weights are normalised here.  A
    scene the architecture did not rank weighs like every other unranked scene;
    with no architecture at all the budget is an even share.
    """
    weights: dict[str, float] = {}
    for slot in (architecture or {}).get("scene_slots") or []:
        if not isinstance(slot, dict):
            continue
        try:
            weight = float(slot.get("weight") or 0)
        except (TypeError, ValueError):
            weight = 0.0
        # Any positive weight ranks as given; only a missing or unusable one
        # falls back to "like every other unranked scene".
        weights[str(slot.get("production_section_id") or "")] = weight if weight > 0 else 1.0
    budgets: dict[str, float] = {}
    for episode in plan.get("episodes") or []:
        section_ids = [str(section_id) for section_id in episode.get("section_ids") or []]
        shares = [weights.get(section_id, 1.0) for section_id in section_ids]
        total = sum(shares) or float(len(section_ids)) or 1.0
        target = float(plan.get("target_seconds") or 0)
        for section_id, share in zip(section_ids, shares, strict=True):
            budgets[section_id] = round(max(MIN_SCENE_SECONDS, target * share / total), 1)
    return budgets


# --- Architecture coverage ------------------------------------------------------


def architecture_refusals(architecture: dict[str, Any], plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Why an architecture cannot serve the confirmed plan, if it cannot.

    The plan is the person's decision: every episode and every section of it
    must be addressed exactly once, and every scene must stand in a registered
    place.  An answer that invents, drops or repeats one of them is refused.
    """
    refusals: list[dict[str, Any]] = []
    names = [str(entry.get("name") or "").strip() for entry in architecture.get("locations") or []
             if isinstance(entry, dict)]
    registered = {name for name in names if name}
    if not registered:
        refusals.append({"code": "ARCHITECTURE_LOCATIONS_MISSING"})
    elif len(names) != len(registered):
        refusals.append({"code": "ARCHITECTURE_LOCATION_REPEATED"})
    expected_episodes = [str(episode.get("episode_id")) for episode in plan.get("episodes") or []]
    blueprint_ids = [str(entry.get("episode_id") or "") for entry in architecture.get("episodes") or []
                     if isinstance(entry, dict)]
    if sorted(blueprint_ids) != sorted(expected_episodes):
        refusals.append({"code": "ARCHITECTURE_EPISODE_COVERAGE",
                         "missing": sorted(set(expected_episodes) - set(blueprint_ids)),
                         "unknown": sorted(set(blueprint_ids) - set(expected_episodes))})
    expected_sections = [str(section_id) for episode in plan.get("episodes") or []
                         for section_id in episode.get("section_ids") or []]
    slots = [entry for entry in architecture.get("scene_slots") or [] if isinstance(entry, dict)]
    slot_ids = [str(slot.get("production_section_id") or "") for slot in slots]
    if sorted(slot_ids) != sorted(expected_sections):
        refusals.append({"code": "ARCHITECTURE_SCENE_COVERAGE",
                         "missing": sorted(set(expected_sections) - set(slot_ids)),
                         "unknown": sorted(set(slot_ids) - set(expected_sections))})
    unknown_places = sorted({str(slot.get("location") or "") for slot in slots
                             if str(slot.get("location") or "") not in registered})
    if unknown_places:
        refusals.append({"code": "ARCHITECTURE_LOCATION_UNKNOWN", "locations": unknown_places})
    for slot in slots:
        if str(slot.get("transition_from_prev") or "") not in TRANSITIONS:
            refusals.append({"code": "ARCHITECTURE_TRANSITION_UNKNOWN",
                             "production_section_id": slot.get("production_section_id")})
    return refusals


def scene_slot(architecture: dict[str, Any] | None, production_section_id: str) -> dict[str, Any] | None:
    for slot in (architecture or {}).get("scene_slots") or []:
        if isinstance(slot, dict) and str(slot.get("production_section_id")) == str(production_section_id):
            return slot
    return None


def episode_blueprint(architecture: dict[str, Any] | None, episode_id: str) -> dict[str, Any] | None:
    for entry in (architecture or {}).get("episodes") or []:
        if isinstance(entry, dict) and str(entry.get("episode_id")) == str(episode_id):
            return entry
    return None


# --- The creative text of one scene ---------------------------------------------


def drama_scene_text(payload: dict[str, Any], section: dict[str, Any] | None = None) -> str:
    """Render one drama scene script as the text the storyboard crew reads.

    The only rendering: the queue and the source-range verifier both call it.
    Spoken lines are written as ``说话方：台词`` on their own line, so a later
    beat breakdown finds every line verbatim and the cast of a beat is read
    from the same text the action is.
    """
    section = section or {}
    order = section.get("order", "?")
    title = section.get("title") or payload.get("title") or ""
    when = TIME_OF_DAY_LABELS.get(str(payload.get("time_of_day") or "unspecified"), "时间未定")
    weather = str(payload.get("weather_atmosphere") or "").strip()
    lines = [f"第{order}场 {title}".rstrip(), f"场景：{payload.get('location') or ''}（{when}）"
             + (f"，{weather}" if weather else "")]
    entry = str(payload.get("entry_bridge") or "").strip()
    if entry:
        lines.append(f"（承接上一场）{entry}")
    for index, beat in enumerate(payload.get("beats") or [], start=1):
        if not isinstance(beat, dict):
            continue
        action = str(beat.get("visible_action") or "").strip()
        lines.append(f"{index}. {action}" if action else f"{index}.")
        for line in beat.get("spoken_lines") or []:
            if not isinstance(line, dict) or not str(line.get("text") or "").strip():
                continue
            emotion = str(line.get("emotion") or "").strip()
            speaker = str(line.get("speaker") or "").strip()
            if emotion:
                lines.append(f"（{speaker}，{emotion}）")
            lines.append(f"{speaker}：{str(line['text']).strip()}")
    exit_state = str(payload.get("exit_state") or "").strip()
    if exit_state:
        lines.append(f"（本场收尾）{exit_state}")
    return "\n".join(lines)


def architecture_brief(outline: dict[str, Any], plan: dict[str, Any], document_text: str,
                       *, brief_limit: int = 20_000) -> str:
    """What the architect reads besides the plan: an excerpt of every scene.

    The plan itself (episodes, section ids, titles) reaches the architect once,
    as ``production_context.plan``; the brief adds only what the plan cannot
    hold — the prose of each section, shortened to fit the request.  Ids are
    never shortened, so an excerpt can always be told apart from its neighbour.
    """
    sections = {str(row.get("production_section_id")): row for row in outline.get("production_sections") or []}
    wanted = [str(section_id) for episode in plan.get("episodes") or []
              for section_id in episode.get("section_ids") or []]
    head = ("【各场原文摘录】每条以 production_section_id 开头，后接该场原文的开头部分；"
            "集与场次的归属、顺序和标题见 production_context.plan。\n")
    room = max(0, brief_limit - len(head) - 40 * len(wanted))
    per_section = max(80, room // max(1, len(wanted)))
    excerpts = []
    for section_id in wanted:
        row = sections.get(section_id) or {}
        start, end = int(row.get("start_offset") or 0), int(row.get("end_offset") or 0)
        text = document_text[start:end].replace("\n", " ")
        excerpts.append(f"{section_id}：{text[:per_section]}")
    return (head + "\n".join(excerpts))[:brief_limit]


# --- The server's verdict on what the model wrote --------------------------------


def scene_verdict(payload: dict[str, Any], context: dict[str, Any]) -> DramaVerdict:
    """``check_drama_scene`` against the verified context of one scene.

    The cast and the registered places are read from the same verified
    production context the writer read, so the verdict and the prompt can
    never disagree about who and what exists.
    """
    production = context.get("production_context") or {}
    cast = [name.strip() for character in production.get("characters") or [] if isinstance(character, dict)
            for name in (character.get("name"), character.get("entity_id"))
            if isinstance(name, str) and name.strip()]
    locations = [str(entry.get("name")) for entry in production.get("locations") or []
                 if isinstance(entry, dict) and entry.get("name")]
    return check_drama_scene(payload, cast=cast, locations=locations,
                             transition=(production.get("scene_slot") or {}).get("transition_from_prev"))


def architecture_verdict(payload: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
    """``architecture_refusals`` against the confirmed plan the architect was given."""
    return architecture_refusals(payload, (context.get("production_context") or {}).get("plan") or {})


def drama_server_fields(payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """What the server writes into a scene the model wrote.

    The model writes the scene; where it came from, which architecture ranks
    it, how long it is and the one rendering of it the storyboard crew will
    read are the server's.  Nothing here changes a word of the scene.
    """
    production = context.get("production_context") or {}
    span = production.get("source_offsets") or {}
    slot = production.get("scene_slot") or {}
    seconds = scene_seconds(payload)
    budget = production.get("scene_budget_seconds")
    warnings = list(scene_verdict(payload, context).warnings)
    if budget:
        if seconds > budget * 1.25:
            warnings.append({"code": "DRAMA_SCENE_OVER_BUDGET", "seconds": seconds, "budget_seconds": budget})
        elif seconds < budget * 0.75:
            warnings.append({"code": "DRAMA_SCENE_UNDER_BUDGET", "seconds": seconds, "budget_seconds": budget})
    return {
        "production_section_id": production.get("production_section_id"),
        "episode_id": production.get("episode_id"),
        "source_offsets": {"start_offset": span.get("start_offset"), "end_offset": span.get("end_offset"),
                           "merged_section_ids": sorted(span.get("merged_section_ids") or [])},
        "outline_sha256": production.get("outline_sha256"),
        "plan_sha256": production.get("plan_sha256"),
        "architecture_artifact_id": production.get("architecture_artifact_id"),
        "architecture_sha256": production.get("architecture_sha256"),
        "scene_slot": slot,
        "scene_budget_seconds": budget,
        "scene_seconds": seconds,
        "warnings": warnings,
        "text": drama_scene_text(payload, production.get("section")),
    }
