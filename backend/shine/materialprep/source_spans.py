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


@dataclass(frozen=True)
class SourceSpan:
    """One citable fragment of the original script."""

    id: str
    scene_key: str
    text: str

    def as_public(self) -> dict[str, Any]:
        return {"id": self.id, "scene_key": self.scene_key, "text": self.text}


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


def _paragraph_blocks(lines: list[str]) -> list[tuple[list[str], str]]:
    """Chunk a document without headings into readable scene-sized blocks."""
    blocks: list[tuple[list[str], str]] = []
    current: list[str] = []
    size = 0
    for line in lines:
        if not line.strip():
            continue
        if current and size + len(line) > MAX_SCENE_CHARS:
            blocks.append((current, current[0].strip()[:80]))
            current = []
            size = 0
        current.append(line)
        size += len(line) + 1
    if current:
        blocks.append((current, current[0].strip()[:80]))
    return blocks


def segment_document(text: str) -> list[Scene]:
    """Split a script into numbered scenes with a citable span catalogue."""
    lines = (text or "").splitlines()
    starts = [index for index, line in enumerate(lines) if line.strip() and HEADING.match(line)]
    if starts:
        blocks: list[tuple[list[str], str]] = []
        for position, start in enumerate(starts):
            end = starts[position + 1] if position + 1 < len(starts) else len(lines)
            block = lines[start:end]
            blocks.append((block, block[0].strip()[:80]))
    else:
        blocks = _paragraph_blocks(lines)
    scenes: list[Scene] = []
    for number, (block, title) in enumerate(blocks, start=1):
        key = f"S{number}"
        spans: list[SourceSpan] = []
        for line in block:
            if not line.strip():
                continue
            if len(spans) >= MAX_SPANS_PER_SCENE:
                break
            spans.append(SourceSpan(id=f"{key}#p{len(spans) + 1}", scene_key=key, text=line.strip()))
        body = "\n".join(line.strip() for line in block if line.strip())
        scenes.append(Scene(key=key, title=title, text=body, spans=tuple(spans)))
    return scenes


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
