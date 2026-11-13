"""Stage adapters: read a real answer into the existing pipeline contracts.

The pipeline's contracts stay authoritative - ``SceneScriptPayload`` is what a
confirmed scene must satisfy - but a draft only has to be *readable*.  These
adapters therefore do two separate things:

1. **server-owned fields are written from trusted task data**, never from the
   model: a scene key, a section id and a source range come from the queue that
   asked for the scene;
2. **model-owned fields are read tolerantly and capped by name**, so an overlong
   summary is shortened with a recorded note instead of failing the whole
   answer.  Literary prose is never shortened: ``text`` is passed through
   verbatim or the answer is reported as incomplete.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field, field_validator

from shine.script_labels import LITERARY_SCENE_LABELS

SCENE_TEXT_FIELD = "text"
SCENE_TEXT_ALIASES = {"content": "text", "script": "text", "scene_text": "text",
                      "screenplay": "text", "body": "text"}

#: Metadata ceilings from the pipeline contract.  These are *metadata*: a long
#: synopsis is shortened with an explicit note, while the scene text itself is
#: never touched.
CONTINUITY_LIMIT = 400
TITLE_LIMIT = 120
QUESTION_LIMIT = 300

#: Speaker lines a Chinese script writes as ``角色：台词``.  Used only to fill
#: the dialogue *index* locally when the model did not provide one; the scene
#: text itself is never modified.
_SPEAKER_LINE = re.compile(r"^\s*([\u4e00-\u9fa5A-Za-z0-9·]{1,12})\s*[：:]\s*(\S.{0,199})\s*$")
_NON_SPEAKER_LABELS = set(LITERARY_SCENE_LABELS)


class SceneDraft(BaseModel):
    """The minimal external contract for one scene.

    Only what the model alone can know: the literary body and the hand-off
    line.  Scene key, section id, source range and version are server-owned and
    are written by the adapter from the trusted task, so a model that forgets
    to echo an id cannot lose its own scene.
    """

    text: str = Field(min_length=1)
    continuity_out: str = Field(default="", max_length=CONTINUITY_LIMIT)

    @field_validator("text")
    @classmethod
    def _must_carry_prose(cls, value: str) -> str:
        """Whitespace is not a scene: an empty answer must be retried, not saved."""
        if not value.strip():
            raise ValueError("scene text is empty")
        return value


@dataclass
class Adapted:
    """One adapted payload plus everything the caller must know about it."""

    payload: dict[str, Any]
    gaps: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def has_text(self) -> bool:
        return bool(str(self.payload.get("text") or "").strip())

    @property
    def complete(self) -> bool:
        return self.has_text and not self.gaps

    def as_dict(self) -> dict[str, Any]:
        return {"gaps": list(self.gaps), "notes": list(self.notes),
                "has_text": self.has_text, "complete": self.complete}


def _string(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _capped(value: Any, limit: int, field_name: str, adapted: Adapted) -> str:
    text = _string(value).strip()
    if len(text) > limit:
        adapted.notes.append(f"metadata_capped:{field_name}:{len(text)}->{limit}")
        return text[:limit]
    return text


def _string_list(value: Any, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()][:limit]


def _characters(value: Any, limit: int) -> list[str]:
    """Character names from either a string list or an object list."""
    if not isinstance(value, list):
        return []
    names: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            names.append(item.strip())
        elif isinstance(item, dict):
            name = item.get("name") or item.get("character") or item.get("who")
            if isinstance(name, str) and name.strip():
                names.append(name.strip())
    seen: set[str] = set()
    unique = [name for name in names if not (name in seen or seen.add(name))]
    return unique[:limit]


def _dialogue(value: Any, limit: int) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    lines: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        speaker = item.get("speaker") or item.get("role") or item.get("who")
        line = item.get("line") or item.get("text") or item.get("content")
        if isinstance(speaker, str) and isinstance(line, str) and speaker.strip() and line.strip():
            lines.append({"speaker": speaker.strip(), "line": line.strip()})
    return lines[:limit]


def extract_dialogue(text: str, limit: int = 64) -> list[dict[str, str]]:
    """Index the dialogue a script already contains, without editing it.

    A local index is preferable to asking the model to repeat every line in a
    second field: it cannot disagree with the prose, and a deliberate repeated
    line stays repeated because nothing is de-duplicated.
    """
    lines: list[dict[str, str]] = []
    for raw in (text or "").splitlines():
        match = _SPEAKER_LINE.match(raw)
        if not match:
            continue
        speaker, line = match.group(1).strip(), match.group(2).strip()
        if speaker in _NON_SPEAKER_LABELS or len(speaker) > 8:
            continue
        lines.append({"speaker": speaker, "line": line})
        if len(lines) >= limit:
            break
    return lines


def adapt_scene_script(
    raw: dict[str, Any] | None,
    *,
    section: dict[str, Any],
    fallback_text: str = "",
) -> Adapted:
    """Read one scene answer against a trusted section definition.

    ``section`` is the queue's own record of the unit, so the returned payload
    can never claim a different scene than the one that was asked for.
    """
    raw = raw if isinstance(raw, dict) else {}
    adapted = Adapted(payload={})
    # The body is kept verbatim: provenance compares the text hash, and a
    # "helpful" trim would make the stored scene differ from the answer.
    text = _string(raw.get("text"))
    if not text.strip():
        for alias in SCENE_TEXT_ALIASES:
            candidate = _string(raw.get(alias))
            if candidate.strip():
                text = candidate
                adapted.notes.append(f"text_read_from:{alias}")
                break
    if not text.strip():
        text = fallback_text or ""
        if text.strip():
            adapted.notes.append("text_recovered_from_raw_answer")
    payload: dict[str, Any] = {
        "scene_key": str(section.get("scene_key") or section.get("section_id") or ""),
        "section_id": str(section.get("section_id") or ""),
        "title": _capped(raw.get("title") or section.get("title") or "", TITLE_LIMIT, "title", adapted),
        "text": text,
        "characters": _characters(raw.get("characters"), 24),
        "dialogue": _dialogue(raw.get("dialogue"), 64),
        "continuity_in": _capped(raw.get("continuity_in"), CONTINUITY_LIMIT, "continuity_in", adapted),
        "continuity_out": _capped(raw.get("continuity_out"), CONTINUITY_LIMIT, "continuity_out", adapted),
        "questions": [item[:QUESTION_LIMIT] for item in _string_list(raw.get("questions"), 8)],
    }
    if not payload["dialogue"]:
        derived = extract_dialogue(text)
        if derived:
            payload["dialogue"] = derived
            adapted.notes.append(f"dialogue_indexed_locally:{len(derived)}")
    seconds = raw.get("estimated_seconds")
    payload["estimated_seconds"] = seconds if isinstance(seconds, int) and not isinstance(seconds, bool) \
        and 0 <= seconds <= 3600 else 0
    if not text.strip():
        adapted.gaps.append("text")
    if not payload["continuity_out"]:
        # A missing hand-off line affects the *next* unit, not this scene's
        # text: it is a named gap, not a reason to discard the scene.
        adapted.gaps.append("continuity_out")
    if not payload["characters"]:
        adapted.gaps.append("characters")
    adapted.payload = payload
    return adapted


def adapt_bible(raw: dict[str, Any] | None) -> Adapted:
    """Read one blueprint answer into the existing bible payload shape."""
    raw = raw if isinstance(raw, dict) else {}
    adapted = Adapted(payload={})
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "mainline": _capped(raw.get("mainline") or raw.get("story_line") or "",
                            2000, "mainline", adapted),
        "characters": [],
        "world": _capped(raw.get("world"), 2000, "world", adapted),
        "timeline": [item[:300] for item in _string_list(raw.get("timeline"), 48)],
        "foreshadowing": [item[:300] for item in _string_list(raw.get("foreshadowing"), 48)],
        "locked_facts": [item[:300] for item in _string_list(raw.get("locked_facts"), 48)],
        "open_questions": [],
        "ending_direction": _capped(raw.get("ending_direction"), 400, "ending_direction", adapted),
    }
    characters = raw.get("characters")
    if isinstance(characters, list):
        for item in characters[:64]:
            if not isinstance(item, dict):
                continue
            name = _string(item.get("name")).strip()
            if not name:
                continue
            payload["characters"].append({
                "name": name,
                "identity": _capped(item.get("identity"), 300, "identity", adapted),
                "arc_summary": _capped(item.get("arc_summary"), 300, "arc_summary", adapted),
            })
    questions = raw.get("open_questions")
    if isinstance(questions, list):
        for item in questions[:48]:
            if isinstance(item, str) and item.strip():
                payload["open_questions"].append({"question": item.strip()[:300]})
            elif isinstance(item, dict) and _string(item.get("question")).strip():
                payload["open_questions"].append({"question": _string(item.get("question")).strip()[:300]})
    if not payload["mainline"]:
        adapted.gaps.append("mainline")
    if not payload["characters"]:
        adapted.gaps.append("characters")
    adapted.payload = payload
    return adapted
