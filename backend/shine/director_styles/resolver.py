"""Deterministic, non-executable style technique selection."""

from __future__ import annotations

from collections.abc import Iterable

from shine.director_styles.catalog import CatalogEntry, canonical_sha256
from shine.director_styles.contracts import StyleResolution

_TARGETS = ("camera", "lighting", "color", "texture", "editing", "sound")
_STRENGTH_LIMIT = {"subtle": 1, "balanced": 2, "strong": 40}


class DirectorStyleResolver:
    """Selects only declared techniques whose conditions and capabilities match."""

    def resolve(
        self,
        entry: CatalogEntry,
        *,
        strength: str = "balanced",
        scene_tags: Iterable[str] = (),
        capabilities: Iterable[str] = (),
        blocked_constraints: Iterable[str] = (),
    ) -> StyleResolution:
        if strength not in _STRENGTH_LIMIT:
            raise ValueError("STYLE_PROFILE_INVALID_STRENGTH")
        tags = sorted(set(scene_tags))
        available = set(capabilities)
        blocked = set(blocked_constraints)
        input_data = {
            "profile_id": entry.profile.id,
            "profile_version": entry.profile.version,
            "profile_sha256": entry.sha256,
            "strength": strength,
            "scene_tags": tags,
            "capabilities": sorted(available),
            "blocked_constraints": sorted(blocked),
        }
        eligible = []
        rejected: list[dict[str, str]] = []
        for technique in entry.profile.techniques:
            condition = technique.when.scene_tags_any
            if condition and not available_tags_intersect(condition, set(tags)):
                continue
            missing = sorted(set(technique.requires_capabilities) - available)
            if missing:
                rejected.append({
                    "technique_id": technique.id,
                    "reason": "STYLE_TECHNIQUE_UNAVAILABLE:" + ",".join(missing),
                })
                continue
            conflicts = sorted(set(technique.conflicts_with) & blocked)
            if conflicts:
                rejected.append({
                    "technique_id": technique.id,
                    "reason": "STYLE_RESOLUTION_CONFLICT:" + ",".join(conflicts),
                })
                continue
            eligible.append(technique)

        selected = []
        selected_ids: set[str] = set()
        for technique in sorted(eligible, key=lambda item: (-item.priority, item.id)):
            if any(
                technique.id in chosen.conflicts_with or chosen.id in technique.conflicts_with
                for chosen in selected
            ):
                rejected.append({"technique_id": technique.id, "reason": "STYLE_RESOLUTION_CONFLICT:profile_priority"})
                continue
            same_target = sum(item.target == technique.target for item in selected)
            if same_target >= _STRENGTH_LIMIT[strength]:
                continue
            selected.append(technique)
            selected_ids.add(technique.id)

        constraints = {
            target: [item.instruction_en for item in selected if item.target == target]
            for target in _TARGETS
        }
        warnings = sorted({item["reason"] for item in rejected})
        resolution_payload = {
            "schema_version": "director-style-resolution/1",
            "profile_id": entry.profile.id,
            "profile_version": entry.profile.version,
            "profile_sha256": entry.sha256,
            "strength": strength,
            "scene_tags": tags,
            "selected_technique_ids": [item.id for item in selected],
            "constraints": constraints,
            "warnings": warnings,
            "rejected": sorted(rejected, key=lambda item: (item["technique_id"], item["reason"])),
            "input_sha256": canonical_sha256(input_data),
        }
        return StyleResolution(**resolution_payload, resolution_sha256=canonical_sha256(resolution_payload))


def available_tags_intersect(required: list[str], actual: set[str]) -> bool:
    return bool(set(required) & actual)
