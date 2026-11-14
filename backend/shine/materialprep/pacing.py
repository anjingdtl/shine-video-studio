"""Outline pacing profiles: three user-selectable narrative rhythms.

The pacing choice is part of the outline request identity (changing it creates
a new outline input) and is carried into the merge prompt so the narrative
projection — summary density, story-line beat count, timeline granularity —
adapts to the intended shooting rhythm.  It never changes source segmentation,
locked facts, character identity numbering or the production sections: those
stay owned by the server and faithful to the script.
"""

from __future__ import annotations

from typing import Literal

Pacing = Literal["compact", "balanced", "relaxed"]

PACING_DEFAULT: Pacing = "balanced"

#: Ordered for the UI selector.  ``label`` is what the person reads; the
#: instruction is appended to the merge system prompt (English duty text, the
#: narrative content itself stays in the script's language).
PACING_PROFILES: dict[str, dict[str, str]] = {
    "compact": {
        "label": "紧凑剧情",
        "summary": "冲突密集、推进快，适合短时长快切拍摄。",
        "instruction":
            "PACING=compact (紧凑剧情): write a fast, dense narrative. Compress the story line to the "
            "fewest beats that still carry every locked fact and conflict; escalate quickly, cut "
            "lingering atmosphere, and keep the timeline coarse (major turns only). Prefer short "
            "cause-effect beats suited to quick cutting and a short runtime.",
    },
    "balanced": {
        "label": "均衡剧情",
        "summary": "情节与人物/氛围均衡，默认节奏。",
        "instruction":
            "PACING=balanced (均衡剧情): write a balanced narrative. Advance the plot while keeping "
            "character and atmosphere beats; a moderate story-line beat count and a timeline that "
            "records both turns and notable quiet moments.",
    },
    "relaxed": {
        "label": "舒缓剧情",
        "summary": "氛围与人物内心停留多、节奏慢，适合文艺慢切。",
        "instruction":
            "PACING=relaxed (舒缓剧情): write a slow, lyrical narrative. Linger on atmosphere, "
            "character interiority and small gestures; expand the story line with breathing beats "
            "and a finer timeline that records quiet moments, suited to long takes and slow cutting.",
    },
}


def normalize_pacing(value: str | None) -> Pacing:
    """Accept only the three declared profiles; anything else is the default."""
    return value if value in PACING_PROFILES else PACING_DEFAULT  # type: ignore[return-value]


def pacing_label(value: str | None) -> str:
    return PACING_PROFILES[normalize_pacing(value)]["label"]


def pacing_instruction(value: str | None) -> str:
    return PACING_PROFILES[normalize_pacing(value)]["instruction"]