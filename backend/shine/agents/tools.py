from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from shine.schemas import StyleBible


@dataclass(frozen=True)
class AssetView:
    id: str
    filename: str
    kind: str
    role: str
    entity_id: str
    tags: tuple[str, ...]
    duration_s: float | None
    approved: bool
    locked: bool


@dataclass(frozen=True)
class ProjectContext:
    project_id: str
    title: str
    language: str
    style_version_id: str
    style: StyleBible | None
    assets: tuple[AssetView, ...] = ()
    shot_summaries: tuple[dict[str, Any], ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    def usable_assets(self) -> list[AssetView]:
        return [asset for asset in self.assets if asset.approved and asset.locked]

    def safe_prompt_view(self) -> dict[str, Any]:
        """Only non-sensitive metadata is exposed to the model."""
        return {
            "project": {"title": self.title, "language": self.language, "style_version_id": self.style_version_id},
            "style": self.style.model_dump() if self.style else None,
            "assets": [
                {
                    "asset_id": asset.id,
                    "filename": asset.filename,
                    "kind": asset.kind,
                    "role": asset.role,
                    "entity_id": asset.entity_id,
                    "tags": list(asset.tags),
                }
                for asset in self.usable_assets()
            ],
            "shots": list(self.shot_summaries),
        }


class AssetMatcher:
    """Deterministic retrieval over user-approved assets. It never creates assets."""

    def score(self, asset: AssetView, text: str, characters: set[str]) -> int:
        haystack = text.lower()
        score = 0
        identifiers = {asset.entity_id.lower(), asset.role.lower()} - {""}
        if identifiers & {character.lower() for character in characters}:
            score += 5
        for identifier in identifiers:
            if identifier and identifier in haystack:
                score += 3
        for tag in asset.tags:
            if tag.lower() and tag.lower() in haystack:
                score += 2
        stem = asset.filename.rsplit(".", 1)[0].lower()
        if stem and stem in haystack:
            score += 1
        return score

    def characters_in(self, text: str, assets: tuple[AssetView, ...]) -> list[str]:
        found: list[str] = []
        lowered = text.lower()
        for asset in assets:
            if not asset.approved or not asset.locked:
                continue
            for candidate in {asset.entity_id, asset.role, *asset.tags}:
                if candidate and candidate.lower() in lowered and candidate not in found:
                    found.append(candidate)
        return found[:20]

    def match(self, text: str, assets: tuple[AssetView, ...], characters: list[str], *, limit: int = 12) -> list[str]:
        usable = [asset for asset in assets if asset.approved and asset.locked]
        scored = [(self.score(asset, text, set(characters)), asset) for asset in usable]
        scored = [(score, asset) for score, asset in scored if score > 0]
        scored.sort(key=lambda item: (-item[0], item[1].id))
        return [asset.id for _score, asset in scored[:limit]]
