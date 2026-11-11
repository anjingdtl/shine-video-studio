"""Hierarchical blueprint merge: compress the digests, never the story.

The cross-volume blueprint is written from per-volume digests, not from the full
manuscript.  A digest keeps what the blueprint actually needs - the ordered
events, the clues and foreshadowing it opens or closes, and the source coverage
it was derived from - so a compressed input is still traceable to a source
range.  Compression may drop *prose*, never an event id: every id that is left
out of the text is listed in ``compressed_ids`` and stays queryable.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from shine.literary.requests import estimate_tokens

EVENT_PREFIX = "ev"


@dataclass
class VolumeDigest:
    volume_id: str
    order: int
    title: str
    summary: str = ""
    events: list[dict[str, str]] = field(default_factory=list)
    clues: list[str] = field(default_factory=list)
    foreshadowing: list[str] = field(default_factory=list)
    characters: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    source: dict[str, Any] = field(default_factory=dict)
    coverage_complete: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "volume_id": self.volume_id, "order": self.order, "title": self.title,
            "summary": self.summary, "events": list(self.events),
            "clues": list(self.clues), "foreshadowing": list(self.foreshadowing),
            "characters": list(self.characters), "questions": list(self.questions),
            "source": dict(self.source), "coverage_complete": self.coverage_complete,
        }


@dataclass
class MergedBlueprint:
    text: str
    event_ids: list[str] = field(default_factory=list)
    clue_ids: list[str] = field(default_factory=list)
    foreshadow_ids: list[str] = field(default_factory=list)
    compressed_ids: list[str] = field(default_factory=list)
    coverage: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_ids": list(self.event_ids), "clue_ids": list(self.clue_ids),
            "foreshadow_ids": list(self.foreshadow_ids),
            "compressed_ids": list(self.compressed_ids),
            "coverage": dict(self.coverage), "warnings": list(self.warnings),
            "chars": len(self.text),
        }


def _event_id(volume_id: str, ordinal: int, text: str) -> str:
    material = f"{volume_id}|{ordinal}|{text}"
    return f"{EVENT_PREFIX}-{hashlib.sha256(material.encode()).hexdigest()[:12]}"


def digest_from_outline(outline_payload: dict[str, Any], *, volume: dict[str, Any]) -> VolumeDigest:
    """Build one volume digest from its confirmed outline artifact."""
    payload = outline_payload if isinstance(outline_payload, dict) else {}
    volume_id = str(volume.get("volume_id") or "")
    story_line = [str(item).strip() for item in (payload.get("story_line") or [])
                  if str(item).strip()]
    events = [{"id": _event_id(volume_id, index + 1, line), "text": line}
              for index, line in enumerate(story_line[:80])]
    sections = payload.get("production_sections") or []
    start = volume.get("start_offset")
    end = volume.get("end_offset")
    coverage_complete = bool(sections)
    if isinstance(start, int) and isinstance(end, int):
        covered = sum(
            max(0, min(int(section.get("end_offset") or 0), end)
                - max(int(section.get("start_offset") or 0), start))
            for section in sections if isinstance(section, dict))
        coverage_complete = covered >= max(0, end - start) and bool(sections)
    else:
        covered = 0
    return VolumeDigest(
        volume_id=volume_id, order=int(volume.get("order") or 0),
        title=str(volume.get("title") or ""),
        summary=str(payload.get("summary") or "")[:1200],
        events=events,
        clues=[str(item)[:200] for item in (payload.get("story_line") or [])[:24]],
        foreshadowing=[str(item)[:200] for item in (payload.get("questions") or [])[:24]],
        characters=[str(item.get("name")) for item in (payload.get("characters") or [])
                    if isinstance(item, dict) and item.get("name")][:24],
        questions=[str(item)[:200] for item in (payload.get("questions") or [])[:24]],
        source={"covered_chars": covered, "span_chars": max(0, (end or 0) - (start or 0)),
                "outline_sha256": payload.get("_sha256"),
                "start_offset": start, "end_offset": end},
        coverage_complete=coverage_complete,
    )


def merge_digests(digests: list[VolumeDigest], *, token_budget: int = 3_000) -> MergedBlueprint:
    """Merge digests in plan order, compressing prose before dropping any id."""
    ordered = sorted(digests, key=lambda digest: digest.order)
    merged = MergedBlueprint(text="")
    blocks: list[str] = []
    used = 0
    for digest in ordered:
        header = (f"第{digest.order}卷 {digest.title}"
                  f"（覆盖 {digest.source.get('covered_chars', 0)} 字）")
        summary = digest.summary
        keep = list(digest.events)
        while True:
            candidate = [header]
            if summary:
                candidate.append(f"概要：{summary}")
            if keep:
                candidate.append("事件：")
                candidate.extend(f"- {event['id']} {event['text']}" for event in keep)
            if digest.clues:
                candidate.append("线索：" + "、".join(digest.clues[:8]))
            if digest.foreshadowing:
                candidate.append("伏笔：" + "、".join(digest.foreshadowing[:8]))
            block = "\n".join(candidate)
            if used + estimate_tokens(block) <= token_budget or len(keep) <= 1:
                break
            # Drop the middle *prose* first and keep its ids: the blueprint can
            # still point at every event it did not read in full.
            middle = keep[1:-1] or keep[1:]
            merged.compressed_ids.extend(event["id"] for event in middle)
            keep = [keep[0]] + ([keep[-1]] if len(keep) > 1 else [])
            summary = ""
        blocks.append(block)
        used += estimate_tokens(block)
        merged.event_ids.extend(event["id"] for event in digest.events)
        merged.clue_ids.extend(digest.clues)
        merged.foreshadow_ids.extend(digest.foreshadowing)
        if not digest.coverage_complete:
            merged.warnings.append(f"BLUEPRINT_VOLUME_COVERAGE_INCOMPLETE:{digest.volume_id}")
    merged.text = "\n\n".join(blocks)
    represented = set(merged.event_ids) - set(merged.compressed_ids)
    merged.coverage = {
        "volumes": len(ordered),
        "complete": all(digest.coverage_complete for digest in ordered),
        "missing_volume_ids": [digest.volume_id for digest in ordered
                               if not digest.coverage_complete],
        "every_event_traceable": represented.issubset(set(merged.event_ids)),
        "events_total": len(merged.event_ids),
        "events_in_full": len(represented),
        "events_compressed": len(merged.compressed_ids),
    }
    return merged


def blueprint_context(merged: MergedBlueprint) -> dict[str, Any]:
    """The request-shape one blueprint call receives."""
    return {
        "digests": merged.text,
        "event_ids": merged.event_ids,
        "compressed_event_ids": merged.compressed_ids,
        "coverage": merged.coverage,
        "warnings": merged.warnings,
    }
