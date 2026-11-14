"""Deterministic scene and span segmentation for a script document.

The pre-production scriptwriter reads ONE numbered scene at a time and cites
the source fragments it used.  Both the numbering and the citation catalogue
are therefore server facts: the model may only refer to a span the server
handed it, and a span that does not exist is refused by name instead of being
silently dropped.

The segmentation is deliberately simple and reproducible:

* a scene starts at a heading line (场景 / 第N场 / SCENE / INT. / EXT. ...);
* a document without headings is split into paragraph chunks of at most
  MAX_SCENE_CHARS characters;
* inside a scene every non-empty line is one span, numbered p1, p2, ...

Nothing here calls a model, so the same document always produces the same
scenes and the same span ids.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

HEADING = re.compile(
    r"^\s*(?:场景|場景|第\s*[0-9一二三四五六七八九十百千]+\s*[场場幕]|"
    r"(?:INT|EXT|INT\.?/EXT\.?|I/E)\b|SCENE\b)",
    re.IGNORECASE,
)

CHAPTER_MARKER = re.compile(r"^\s*第[^\n]{1,20}[章节回卷]")
"""A chapter/volume title line.  Like a scene heading it names a unit but is
not itself story prose, so a fragment made only of these lines carries no
plot a crew could storyboard."""

MIN_NARRATIVE_BODY_CHARS = 20
"""The smallest body that counts as a narratable creation unit.

A fragment whose prose (headings and markers removed) is shorter than this is
a title, a marker or a stray blank — not a scene.  Sending it to the five-role
crew buys an empty or contract-breaking answer; it is merged into a neighbouring
narrative unit instead, or refused by name when it stands alone.
"""


def narrative_body(text: str) -> str:
    """The story prose of a fragment, with heading/marker lines removed."""
    parts: list[str] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped or HEADING.match(line) or CHAPTER_MARKER.match(line):
            continue
        parts.append(stripped)
    return "".join(parts)


def is_narrative_fragment(text: str, *, min_body_chars: int = MIN_NARRATIVE_BODY_CHARS) -> bool:
    """True when a fragment carries enough prose to be a creation unit."""
    return len(narrative_body(text)) >= min_body_chars

MAX_SCENE_CHARS = 4000
"""The floor for one extraction unit.  The real ceiling is measured from the
provider's own budget in ``story._extract_segment_chars``; this constant only
keeps a small-window model from being asked for more than it can read."""

MAX_SPANS_PER_SCENE = 1_024
"""How many citable fragments one unit may contain.

The old value (200) capped a unit at 200 x 256 = 51k characters, but line
boundaries make each fragment shorter, so a 44k-character volume was split into
17 requests.  Reading a whole volume once is both cheaper and better: the model
sees every chapter instead of a ninth of the arc.  With 1,024 fragments the same
volume is one request, and every event keeps a citable span id."""

MAX_SPAN_CHARS = 256
"""Evidence granularity.  Kept small on purpose: a cited span must be short
enough to check by eye."""


@dataclass(frozen=True)
class SourceSpan:
    """One citable fragment of the original script."""

    id: str
    scene_key: str
    text: str
    start_offset: int = 0
    end_offset: int = 0

    def as_public(self) -> dict[str, Any]:
        return {"id": self.id, "scene_key": self.scene_key, "text": self.text,
                "start_offset": self.start_offset, "end_offset": self.end_offset}


@dataclass(frozen=True)
class Scene:
    """One numbered scene with its span catalogue."""

    key: str
    title: str
    text: str
    spans: tuple[SourceSpan, ...]

    def as_public(self) -> dict[str, Any]:
        return {
            "scene_key": self.key,
            "title": self.title,
            "text": self.text,
            "span_ids": [span.id for span in self.spans],
        }


def segment_document(text: str, *, max_scene_chars: int = MAX_SCENE_CHARS) -> list[Scene]:
    """Partition all original bytes-as-text, including whitespace and preamble.

    Offsets are Python Unicode character offsets into the immutable document.
    Every chunk is bounded, even a single line or a heading scene; reaching the
    span limit starts another chunk instead of dropping the tail.
    """
    if max_scene_chars < 1:
        raise ValueError("max_scene_chars must be positive")
    if not text or not text.strip():
        return []
    lines = list(re.finditer(r"[^\n]*\n|[^\n]+$", text))
    headings = [line.start() for line in lines if HEADING.match(line.group())]
    boundaries = sorted({0, *headings, len(text)})
    scenes: list[Scene] = []
    for start, end in zip(boundaries, boundaries[1:], strict=False):
        cursor = start
        while cursor < end:
            chunk_end = cursor
            fragments: list[tuple[int, int]] = []
            while chunk_end < end and len(fragments) < MAX_SPANS_PER_SCENE:
                remaining = max_scene_chars - (chunk_end - cursor)
                if remaining <= 0:
                    break
                newline = text.find("\n", chunk_end, end)
                line_end = newline + 1 if newline >= 0 else end
                stop = min(line_end, chunk_end + remaining, chunk_end + MAX_SPAN_CHARS)
                fragments.append((chunk_end, stop))
                chunk_end = stop
            key = f"S{len(scenes) + 1}"
            spans = tuple(SourceSpan(
                id=f"{key}#p{i}", scene_key=key, text=text[left:right],
                start_offset=left, end_offset=right,
            ) for i, (left, right) in enumerate(fragments, 1))
            body = text[cursor:chunk_end]
            title = next((line.strip()[:80] for line in body.splitlines() if line.strip()), "空白来源")
            scenes.append(Scene(key=key, title=title, text=body, spans=spans))
            cursor = chunk_end
    return scenes


def merge_non_narrative_fragments(fragments: list[dict]) -> list[dict]:
    """Deterministically absorb title-only / empty fragments into a neighbour.

    Each fragment carries ``episode_id``, ``production_section_id``,
    ``section_order``, ``start_offset``, ``end_offset`` and ``text``.  A fragment
    with no narrative body is merged into the adjacent narrative fragment of the
    SAME episode (forward first, backward for a trailing title), so the crew is
    never asked to storyboard a bare heading.  Merging only moves boundaries: the
    concatenation of every unit's text still covers the episode's source exactly,
    in order, with no dropped or duplicated character.

    A merged unit keeps the narrative owner's ``production_section_id`` and lists
    the absorbed sections in ``merged_section_ids`` (only those different from the
    owner).  A fragment that cannot merge (an isolated title with no adjacent
    narrative) is returned unchanged and flagged ``non_narrative`` so the caller
    can refuse it by name instead of inventing plot.
    """
    units: list[dict] = []
    pending: list[dict] = []

    def absorb(target: dict, extras: list[dict], *, forward: bool) -> None:
        for extra in extras:
            if forward:
                target["start_offset"] = extra["start_offset"]
                target["text"] = extra["text"] + target["text"]
            else:
                target["end_offset"] = extra["end_offset"]
                target["text"] = target["text"] + extra["text"]
            section = extra["production_section_id"]
            if section != target["production_section_id"] \
                    and section not in target["merged_section_ids"]:
                target["merged_section_ids"].append(section)

    for fragment in fragments:
        if is_narrative_fragment(fragment["text"]):
            unit = {**fragment, "merged_section_ids": []}
            adjacent = [p for p in pending if p["end_offset"] == unit["start_offset"]]
            isolated = [p for p in pending if p["end_offset"] != unit["start_offset"]]
            # An isolated title cannot join this unit without spanning unrelated
            # text; it stays its own (refusable) unit.
            for stray in isolated:
                units.append({**stray, "merged_section_ids": [], "non_narrative": True})
            absorb(unit, adjacent, forward=True)
            units.append(unit)
            pending = []
        else:
            pending.append(fragment)
    if pending:
        narrative_units = [u for u in units if not u.get("non_narrative")]
        if narrative_units:
            last = narrative_units[-1]
            adjacent = [p for p in pending if p["start_offset"] == last["end_offset"]]
            isolated = [p for p in pending if p["start_offset"] != last["end_offset"]]
            absorb(last, adjacent, forward=False)
            for stray in isolated:
                units.append({**stray, "merged_section_ids": [], "non_narrative": True})
        else:
            for stray in pending:
                units.append({**stray, "merged_section_ids": [], "non_narrative": True})
    return units


def spans_for_scene_key(scenes: list[Scene], key: str) -> set[str] | None:
    """Resolve a budgeted fragment only inside its original source scene."""
    for scene in scenes:
        if key == scene.key:
            return {span.id for span in scene.spans}
        match = re.fullmatch(re.escape(scene.key) + r"@(\d+)-(\d+)", key)
        if match:
            left, right = map(int, match.groups())
            spans = [s for s in scene.spans if s.start_offset >= left and s.end_offset <= right]
            if spans and spans[0].start_offset == left and spans[-1].end_offset == right:
                return {span.id for span in spans}
    return None


def span_catalogue(scenes: list[Scene]) -> list[dict[str, Any]]:
    """The catalogue the model is allowed to cite."""
    return [span.as_public() for scene in scenes for span in scene.spans]


def known_span_ids(scenes: list[Scene]) -> set[str]:
    return {span.id for scene in scenes for span in scene.spans}


def unknown_span_ids(preparation: dict[str, Any], known: set[str]) -> list[str]:
    """The span ids a scene answer invented.

    The plan requires an invented fragment number to be refused, not dropped:
    a silently removed citation turns a fact into an uncited one.
    """
    invented: list[str] = []
    for requirement in preparation.get("requirements") or []:
        for span_id in requirement.get("source_span_ids") or []:
            if span_id not in known and span_id not in invented:
                invented.append(span_id)
    return invented
