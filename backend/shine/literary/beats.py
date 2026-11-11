"""Scene beats: split by story boundary, assemble without losing a character.

A scene that cannot fit one request is split at its own paragraph and scene
boundaries, never by a character count alone.  Each beat is a real unit with a
stable identity, its own source range and a dependency on the beat before it,
so a failure in beat 3 leaves beats 1-2 saved and marks only what really
depends on beat 3.

The assembly rule is single and strict: every beat's text is appended verbatim,
in order, without trimming, de-duplicating or "fixing" dialogue.  A scene is
complete only when every beat produced text; a partial assembly is a candidate,
never a confirmed script.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from shine.literary.models import LiteraryUnit

BEAT_KIND = "beat"
SCENE_KIND = "scene"

#: A paragraph larger than this multiple of the target is cut explicitly.
HARD_CUT_RATIO = 1.3
#: A trailing piece smaller than this is merged into the previous beat.
MIN_TAIL_CHARS = 400

#: Boundaries a Chinese screenplay already uses.
_BOUNDARY = re.compile(
    r"(^\s*(?:场景|第[0-9一二三四五六七八九十百]+场|内景|外景|INT\.|EXT\.)[^\n]{0,40}$)",
    re.MULTILINE)


@dataclass(frozen=True)
class Beat:
    beat_id: str
    ordinal: int
    section_id: str
    start: int
    end: int
    title: str
    source_sha256: str
    hard_cut: bool = False
    notes: tuple[str, ...] = ()

    @property
    def chars(self) -> int:
        return self.end - self.start

    def as_dict(self) -> dict[str, Any]:
        return {"beat_id": self.beat_id, "ordinal": self.ordinal,
                "section_id": self.section_id, "start": self.start, "end": self.end,
                "title": self.title, "source_sha256": self.source_sha256,
                "hard_cut": self.hard_cut, "notes": list(self.notes), "chars": self.chars}


@dataclass
class Assembly:
    text: str = ""
    complete: bool = False
    missing_ordinals: list[int] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    continuity: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"complete": self.complete, "chars": len(self.text),
                "missing_ordinals": list(self.missing_ordinals),
                "notes": list(self.notes), "beats": len(self.continuity)}


def _digest(*parts: Any) -> str:
    material = "|".join(str(part) for part in parts)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _boundaries(text: str) -> list[int]:
    """Offsets that begin a new narrative unit: blank lines and scene headings."""
    points = {0}
    for match in _BOUNDARY.finditer(text):
        if match.start() > 0:
            points.add(match.start())
    for match in re.finditer(r"\n\s*\n", text):
        points.add(match.end())
    return sorted(points)


def plan_beats(text: str, *, section_id: str, target_chars: int,
               min_chars: int = MIN_TAIL_CHARS) -> list[Beat]:
    """Split one section's source into beats at its own boundaries.

    A section that already fits is one beat, so the caller never needs a second
    code path.  Splitting is deterministic: the same text and target produce the
    same beat ids, which is what lets a resumed run adopt the same units.
    """
    text = text or ""
    if not text.strip():
        return []
    if target_chars <= 0 or len(text) <= target_chars:
        return [Beat(_digest(section_id, 1, 0, len(text)), 1, section_id, 0, len(text),
                     _title(text, 0), _digest(text))]

    points = _boundaries(text)
    ranges: list[tuple[int, int, bool, tuple[str, ...]]] = []
    start = 0
    for point in [*points[1:], len(text)]:
        if point <= start:
            continue
        # Close the current beat once it reaches the target at a real boundary.
        if point - start >= target_chars or point == len(text):
            hard = False
            notes: tuple[str, ...] = ()
            if point - start > target_chars * HARD_CUT_RATIO and len(points) == 1:
                point = start + target_chars
                hard = True
                notes = ("hard_cut_within_paragraph",)
            ranges.append((start, point, hard, notes))
            start = point
    if start < len(text):
        ranges.append((start, len(text), False, ()))

    beats: list[Beat] = []
    for ordinal, (left, right, hard, notes) in enumerate(ranges, start=1):
        beats.append(Beat(_digest(section_id, ordinal, left, right), ordinal, section_id,
                          left, right, _title(text, left), _digest(text[left:right]),
                          hard_cut=hard, notes=notes))
    if len(beats) > 1 and beats[-1].chars < min_chars:
        tail = beats.pop()
        previous = beats[-1]
        merged = Beat(_digest(section_id, previous.ordinal, previous.start, tail.end),
                      previous.ordinal, section_id, previous.start, tail.end, previous.title,
                      _digest(text[previous.start:tail.end]), previous.hard_cut,
                      (*previous.notes, "tail_merged"))
        beats[-1] = merged
    return beats


def _title(text: str, offset: int) -> str:
    snippet = text[offset:offset + 60].strip().splitlines()
    first = snippet[0].strip() if snippet else ""
    return first[:40]


def assemble(section_id: str, pieces: list[dict[str, Any]]) -> Assembly:
    """Join beat texts verbatim, in ordinal order, and report what is missing.

    ``pieces`` are ``{"beat": Beat, "text": str, "continuity_out": str}``.
    """
    ordered = sorted(pieces, key=lambda piece: piece["beat"].ordinal)
    assembly = Assembly()
    for piece in ordered:
        text = piece.get("text") or ""
        if not text.strip():
            assembly.missing_ordinals.append(piece["beat"].ordinal)
            continue
        if assembly.text and not assembly.text.endswith("\n") and not text.startswith("\n"):
            assembly.text += "\n"
        assembly.text += text
        assembly.continuity.append(piece.get("continuity_out") or "")
    assembly.complete = bool(assembly.text.strip()) and not assembly.missing_ordinals
    if assembly.missing_ordinals:
        assembly.notes.append("assembled_from_partial_beats")
    return assembly


def save_units(db, *, project_id: str, section_id: str, beats: list[Beat],
               task_id: str | None = None, parent_id: str | None = None) -> list[LiteraryUnit]:
    """Persist the beat DAG for one section, idempotently.

    Each beat depends on its predecessor: that dependency is what makes a failed
    beat mark only the beats after it, and never an independent section.
    """
    existing = {row.id: row for row in db.scalars(select(LiteraryUnit).where(
        LiteraryUnit.project_id == project_id,
        LiteraryUnit.kind == BEAT_KIND,
        LiteraryUnit.parent_id == (parent_id or section_id))).all()}
    units: list[LiteraryUnit] = []
    previous_id: str | None = None
    for beat in beats:
        row = existing.get(beat.beat_id)
        if row is None:
            row = LiteraryUnit(
                id=beat.beat_id, project_id=project_id, parent_id=parent_id or section_id,
                kind=BEAT_KIND, ordinal=beat.ordinal, title=beat.title,
                source_start=beat.start, source_end=beat.end,
                source_sha256=beat.source_sha256, task_id=task_id,
                dependency_ids=[previous_id] if previous_id else [],
                state={"notes": list(beat.notes), "hard_cut": beat.hard_cut})
            db.add(row)
        else:
            row.task_id = task_id or row.task_id
            row.source_start, row.source_end = beat.start, beat.end
        previous_id = beat.beat_id
        units.append(row)
    db.flush()
    return units


def mark(db, unit: LiteraryUnit, status: str, **state: Any) -> LiteraryUnit:
    unit.status = status
    unit.state = {**(unit.state or {}), **state}
    db.flush()
    return unit


def propagate(units: list[LiteraryUnit], *, failed_ordinal: int) -> dict[str, int]:
    """Mark only what really depends on a failed beat.

    A beat that was already written keeps its draft: it is not deleted and not
    marked failed.  Its state says it waits for a dependency, which is a
    different fact from "the text is wrong".
    """
    counts = {"waiting_dependency": 0, "kept": 0}
    for unit in sorted(units, key=lambda row: row.ordinal):
        if unit.ordinal == failed_ordinal:
            continue
        if unit.ordinal > failed_ordinal:
            if unit.status in {"PASSED", "SUCCEEDED"}:
                unit.state = {**(unit.state or {}), "dependency_pending": failed_ordinal}
                counts["kept"] += 1
            else:
                unit.status = "WAITING_DEPENDENCY"
                unit.state = {**(unit.state or {}), "blocked_by": failed_ordinal}
                counts["waiting_dependency"] += 1
    return counts


def coverage(beats: list[Beat], total_chars: int) -> dict[str, Any]:
    """Proof that the split covers the source exactly, with nothing dropped."""
    covered = sum(beat.chars for beat in beats)
    gaps = []
    previous_end = 0
    for beat in sorted(beats, key=lambda item: item.ordinal):
        if beat.start != previous_end:
            gaps.append({"from": previous_end, "to": beat.start})
        previous_end = beat.end
    if previous_end != total_chars:
        gaps.append({"from": previous_end, "to": total_chars})
    return {"covered_chars": covered, "total_chars": total_chars,
            "complete": covered == total_chars and not gaps, "gaps": gaps}
