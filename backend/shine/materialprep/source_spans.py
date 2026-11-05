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

MAX_SCENE_CHARS = 4000
MAX_SPANS_PER_SCENE = 200
MAX_SPAN_CHARS = 256


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
