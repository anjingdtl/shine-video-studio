"""One home for the words a Chinese script uses as labels and as a narrator.

A Chinese script writes ``角色：台词``.  Some words in front of the colon are
*not* a person speaking: they are the page's own labels (``场景：``, ``字幕：``).
``旁白`` is the interesting one — in a script it is a page label, and in a
shot's structured dialogue it is an off-screen narrator, a speaker who owns a
voice and never owns a face.

Both meanings live here so the three consumers cannot drift apart:
``literary.adapters`` and ``agents.planner`` read the label vocabulary, and
``h3.narrator`` reads the narrator identity.  The sets are different on purpose
and say so: a label is stripped from the page, a narrator is a speaker.
"""

from __future__ import annotations

import re

NARRATOR_SPEAKER_NAMES: frozenset[str] = frozenset(
    {"旁白", "画外音", "內心獨白", "内心独白", "narrator", "narration", "voiceover", "voice-over", "vo"}
)
"""The names that identify an off-screen narrator in structured dialogue.

These are *speaker* names.  A speaker with one of these names talks over the
picture and is never visible in it, so it needs no face reference.
"""

SCENE_LAYOUT_LABELS: frozenset[str] = frozenset(
    {
        "时间", "地点", "场景", "内景", "外景", "日", "夜", "旁白", "字幕", "画外音",
        "注", "备注", "标题", "人物", "道具", "音效", "音乐", "镜头", "转场", "动作",
    }
)
"""Every word a Chinese script may put in front of the colon as a page label.

The canonical vocabulary.  The two consumers below keep their own historical
memberships because they serve different scripts, but neither invents a word of
its own any more: a word that belongs to the vocabulary and to no consumer's set
is still recognised as a label.
"""

LITERARY_SCENE_LABELS: frozenset[str] = frozenset(
    {
        "时间", "地点", "场景", "内景", "外景", "日", "夜", "旁白", "字幕", "画外音",
        "注", "备注", "标题", "人物", "道具", "音效", "音乐", "镜头", "转场",
    }
)
"""The labels the literary four-stage adapter strips from a scene page."""

PLANNER_SCENE_LABELS: frozenset[str] = frozenset(
    {"场景", "地点", "时间", "人物", "动作", "旁白", "字幕", "备注", "镜头"}
)
"""The labels the plain script planner strips from a beat."""


def normalize_label(name: str) -> str:
    """Case-fold and collapse whitespace so ``Voice-Over`` and ``voice over`` agree."""
    return re.sub(r"\s+", "", (name or "").strip()).casefold()


def is_narrator_speaker(name: str) -> bool:
    """Whether this speaker is the off-screen narrator rather than a performer."""
    return normalize_label(name) in {normalize_label(item) for item in NARRATOR_SPEAKER_NAMES}


def is_scene_layout_label(name: str) -> bool:
    """Whether this word is a page label rather than the name of a speaker."""
    return (name or "").strip() in SCENE_LAYOUT_LABELS


__all__ = [
    "LITERARY_SCENE_LABELS",
    "NARRATOR_SPEAKER_NAMES",
    "PLANNER_SCENE_LABELS",
    "SCENE_LAYOUT_LABELS",
    "is_narrator_speaker",
    "is_scene_layout_label",
    "normalize_label",
]
