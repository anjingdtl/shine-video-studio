"""Bind the characters a script names to the image masters the user uploaded.

The scriptwriter already produced the character names.  What it cannot know is
which uploaded image is which character, because ``entity_id`` is the only join
key the crew uses (``shine.asset_identity.resolve_character_asset``).  This
module proposes that link deterministically and lets the user confirm it.

Nothing here approves, locks, creates or replaces a master asset.  A proposal
is a reading of the user's own uploads; the confirmation is the user's act and
is recorded by the caller.
"""

from __future__ import annotations

import re
from typing import Any

from shine.asset_identity import character_reference_key, split_character_names

# Names a script uses for "somebody" instead of a person: S1, 角色2, 第3位…
# A placeholder cannot become a prompt name, so it needs a real name from the
# user's own material.
_PLACEHOLDER = re.compile(
    r"^(?:s\d+|shot\d*|no\.?\s*\d+|char(?:acter)?\s*\d*|c\d+|"
    r"角色\s*[一二三四五六七八九十\d]*|第[一二三四五六七八九十\d]+[位个]?)$",
    re.IGNORECASE,
)

# Words that describe a role rather than name a person.
ROLE_WORDS = {
    "主角", "男主角", "女主角", "男主", "女主", "角色", "群演", "主角群像",
    "主角男", "主角女", "lead", "hero", "heroine", "protagonist",
}

_SEPARATORS = re.compile(r"[,，、;；/]+")

# The score is a reading confidence for the proposal, not a probability.
SCORE_ENTITY = 1.0
SCORE_TAG = 0.9
SCORE_FILENAME = 0.8
SCORE_ROLE = 0.5
SCORE_ORDER = 0.3


def is_placeholder_name(name: str) -> bool:
    """True for a script label that names nobody (``S1``, ``角色2``)."""
    text = (name or "").strip()
    if not text:
        return True
    if text in ROLE_WORDS:
        return True
    return bool(_PLACEHOLDER.match(text))


def script_characters(breakdown: dict[str, Any]) -> list[str]:
    """Every character and dialogue speaker the scriptwriter produced, in order."""
    names: list[str] = []

    def remember(value: str | None) -> None:
        for name in split_character_names(value):
            if name not in names:
                names.append(name)

    for beat in (breakdown or {}).get("beats", []) or []:
        for name in beat.get("characters", []) or []:
            remember(name if isinstance(name, str) else str(name))
        for line in beat.get("dialogue", []) or []:
            speaker = line.get("speaker_id") if isinstance(line, dict) else None
            remember(speaker if isinstance(speaker, str) else None)
    return names


def _asset_tags(asset: dict[str, Any]) -> list[str]:
    tags = asset.get("tags") or []
    return [str(tag) for tag in tags if str(tag).strip()]


def _stem(filename: str | None) -> str:
    if not filename:
        return ""
    return re.sub(r"\.[^.]+$", "", str(filename))


def _score(character: str, asset: dict[str, Any]) -> tuple[float, str]:
    """How well one master answers one script name, and why."""
    key = character_reference_key(character)
    if not key:
        return 0.0, ""
    if key == character_reference_key(asset.get("entity_id")):
        return SCORE_ENTITY, "素材实体 ID 与剧本人物名完全一致"
    for tag in _asset_tags(asset):
        if key == character_reference_key(tag):
            return SCORE_TAG, "素材标签与剧本人物名一致"
    if key == character_reference_key(_stem(asset.get("filename"))):
        return SCORE_FILENAME, "素材文件名与剧本人物名一致"
    if character in ROLE_WORDS:
        for tag in _asset_tags(asset):
            if character_reference_key(tag) == character_reference_key(character):
                return SCORE_ROLE, f"素材标签含角色「{character}」，需人工核对是哪张"
    return 0.0, ""


def suggested_name(character: str, asset: dict[str, Any] | None) -> str:
    """The name this master should carry once the user confirms the link."""
    if character and not is_placeholder_name(character):
        return character
    if not asset:
        return character or ""
    for tag in _asset_tags(asset):
        # One tag can carry several words ("主角，小美"); the name is the part
        # that is not a role word.
        for part in split_character_names(tag):
            if part and part not in ROLE_WORDS:
                return part
    stem = _stem(asset.get("filename"))
    return stem or character


def propose_bindings(
    characters: list[str], assets: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Propose a master for every script character.

    ``assets`` are the project's character images.  The proposal never hides a
    candidate: a name the system could not read still lists every master, so
    the user can pick one by picture.
    """
    masters = [a for a in assets if a.get("kind") == "image" and a.get("role") == "character"]
    order = list(masters)
    proposals: list[dict[str, Any]] = []
    used: set[str] = set()
    for index, character in enumerate(characters):
        scored: list[dict[str, Any]] = []
        for asset in masters:
            score, reason = _score(character, asset)
            if score <= 0:
                continue
            scored.append({
                "asset_id": asset.get("id"),
                "filename": asset.get("filename"),
                "entity_id": asset.get("entity_id"),
                "tags": _asset_tags(asset),
                "score": score,
                "reason": reason,
                "approved": bool(asset.get("approved")),
                "locked": bool(asset.get("locked")),
            })
        scored.sort(key=lambda row: (-row["score"], row["filename"] or ""))
        # A script that lists its cast as S1..S6 and one image per character is
        # the only case where the upload order says anything.  It is offered as
        # the weakest candidate and still has to be confirmed by the user.
        if not scored and len(characters) == len(order) and index < len(order):
            asset = order[index]
            scored.append({
                "asset_id": asset.get("id"),
                "filename": asset.get("filename"),
                "entity_id": asset.get("entity_id"),
                "tags": _asset_tags(asset),
                "score": SCORE_ORDER,
                "reason": f"剧本第 {index + 1} 位人物，按上传顺序对应，请人工核对",
                "approved": bool(asset.get("approved")),
                "locked": bool(asset.get("locked")),
            })
        chosen = next((row for row in scored if row["asset_id"] not in used), None)
        if chosen:
            used.add(str(chosen["asset_id"]))
        proposals.append({
            "script_name": character,
            "needs_real_name": is_placeholder_name(character),
            "candidates": scored,
            "suggested_asset_id": chosen["asset_id"] if chosen else None,
            "suggested_name": suggested_name(character, chosen),
            "confidence": _confidence(scored[0]["score"] if scored else 0.0),
        })
    return proposals


def _confidence(score: float) -> str:
    if score >= SCORE_ENTITY:
        return "high"
    if score >= SCORE_FILENAME:
        return "medium"
    if score > 0:
        return "low"
    return "none"


def unbound_masters(assets: list[dict[str, Any]], proposals: list[dict[str, Any]]) -> list[str]:
    """Masters that no proposal points at, so the user can reuse or drop them."""
    bound = {str(row["suggested_asset_id"]) for row in proposals if row.get("suggested_asset_id")}
    return [
        str(asset.get("id"))
        for asset in assets
        if asset.get("kind") == "image" and asset.get("role") == "character"
        and str(asset.get("id")) not in bound
    ]
