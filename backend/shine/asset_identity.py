"""Deterministic matching for user-approved character masters.

Character labels often omit a view hint present in the approved asset name
(for example, ``R01 杜万山`` versus ``R01 杜万山胸上``).  Matching may ignore
those presentation suffixes, but it must resolve to exactly one approved
character asset before the reference is used.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

_VIEW_SUFFIXES = (
    "角色参考图",
    "人物参考图",
    "角色图",
    "人物图",
    "证件照",
    "胸上",
    "半身",
    "全身",
    "头像",
    "立绘",
    "人像",
    "正面",
    "侧面",
    "背面",
    "portrait",
    "headshot",
    "halfbody",
    "fullbody",
)


def character_reference_key(value: str | None) -> str:
    """Return a conservative comparison key for a character name or asset id."""
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", value).strip().replace("\\", "/").rsplit("/", 1)[-1]
    text = re.sub(r"\.[^.]+$", "", text)
    text = re.sub(r"(?i)^[A-Z]{1,3}\d+[\s._-]*", "", text)
    text = re.sub(r"[\s\u3000._\-·（）()\[\]【】]+", "", text).casefold()
    while True:
        suffix = next((item for item in _VIEW_SUFFIXES if text.endswith(item.casefold())), None)
        if suffix is None:
            return text
        text = text[: -len(suffix)]


def character_reference_matches(reference: str | None, candidate: str | None) -> bool:
    key = character_reference_key(reference)
    return bool(key and key == character_reference_key(candidate))


def resolve_character_asset(reference: str | None, assets: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Resolve a name to one approved image master; ambiguous matches stay blocked."""
    exact_id_matches = [
        asset
        for asset in assets
        if asset.get("kind") == "image"
        and asset.get("role") == "character"
        and reference
        and asset.get("id") == reference
    ]
    if exact_id_matches:
        return exact_id_matches[0] if len(exact_id_matches) == 1 else None
    matches = [
        asset
        for asset in assets
        if asset.get("kind") == "image"
        and asset.get("role") == "character"
        and character_reference_matches(reference, asset.get("entity_id"))
    ]
    return matches[0] if len(matches) == 1 else None


def canonical_character_name_for_asset_id(reference: str | None, assets: list[dict[str, Any]]) -> str | None:
    """Replace an exact approved character asset UUID with its readable entity name."""
    asset = resolve_character_asset(reference, assets)
    if asset and asset.get("id") == reference and asset.get("entity_id"):
        return str(asset["entity_id"])
    return reference
