"""Persistence helpers for draft-only selection and immutable frozen bindings."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from shine.director_styles.catalog import CatalogEntry, canonical_sha256
from shine.director_styles.contracts import DirectorStyleProfile, StyleResolution
from shine.director_styles.resolver import DirectorStyleResolver
from shine.models import DirectorStyleProfileRevision
from shine.schemas import StyleBible

_RESOLVER = DirectorStyleResolver()


def draft_digest(style: dict[str, Any] | None, binding: dict[str, Any] | None = None) -> str:
    """Version both visible style fields and the selected source for DSE CAS."""
    return canonical_sha256({"style": style, "director_style": binding_summary(binding)})


def make_binding(entry: CatalogEntry, strength: str, style: StyleBible) -> dict[str, Any]:
    resolution = _RESOLVER.resolve(entry, strength=strength)
    style_payload = style.model_dump(mode="json")
    return {
        "schema_version": "director-style-binding/1",
        "source": entry.source,
        "profile_id": entry.profile.id,
        "profile_version": entry.profile.version,
        "profile_sha256": entry.sha256,
        "strength": strength,
        "style_bible_snapshot": style_payload,
        "style_bible_sha256": canonical_sha256(style_payload),
        "profile_snapshot": entry.profile.model_dump(mode="json"),
        "preview_resolution": resolution.model_dump(mode="json"),
        "resolution_sha256": resolution.resolution_sha256,
    }


def validate_binding(
    binding: dict[str, Any],
    *,
    expected_style: dict[str, Any] | None = None,
) -> tuple[CatalogEntry, StyleResolution]:
    try:
        if binding.get("schema_version") != "director-style-binding/1":
            raise ValueError("STYLE_PROFILE_INVALID")
        profile = DirectorStyleProfile.model_validate(binding["profile_snapshot"])
        profile_payload = profile.model_dump(mode="json")
        profile_sha = canonical_sha256(profile_payload)
        if (
            profile.id != binding.get("profile_id")
            or profile.version != binding.get("profile_version")
            or profile_sha != binding.get("profile_sha256")
        ):
            raise ValueError("STYLE_PROFILE_STALE")
        style = StyleBible.model_validate(binding["style_bible_snapshot"]).model_dump(mode="json")
        if canonical_sha256(style) != binding.get("style_bible_sha256"):
            raise ValueError("STYLE_RESOLUTION_STALE")
        if expected_style is not None and style != StyleBible.model_validate(expected_style).model_dump(mode="json"):
            raise ValueError("STYLE_DRAFT_CONFLICT")
        entry = CatalogEntry(profile=profile, sha256=profile_sha, source=binding.get("source", "builtin"))
        resolution = _RESOLVER.resolve(entry, strength=binding["strength"])
        saved = StyleResolution.model_validate(binding["preview_resolution"])
        if saved != resolution or binding.get("resolution_sha256") != resolution.resolution_sha256:
            raise ValueError("STYLE_RESOLUTION_STALE")
        return entry, resolution
    except HTTPException:
        raise
    except Exception as exc:  # validate imported JSON snapshots at the trust boundary
        code = str(exc) if str(exc).startswith("STYLE_") else "STYLE_PROFILE_INVALID"
        raise HTTPException(status_code=409, detail=code) from exc


def resolve_frozen_binding(
    binding: dict[str, Any],
    *,
    scene_tags: Iterable[str] = (),
    capabilities: Iterable[str] = (),
    blocked_constraints: Iterable[str] = (),
) -> StyleResolution:
    entry, _ = validate_binding(binding)
    return _RESOLVER.resolve(
        entry,
        strength=binding["strength"],
        scene_tags=scene_tags,
        capabilities=capabilities,
        blocked_constraints=blocked_constraints,
    )


def camera_style_text(resolution: StyleResolution) -> str:
    return " ".join(
        instruction
        for target in ("camera", "lighting", "color", "texture")
        for instruction in resolution.constraints[target]
    )


def sound_style_text(resolution: StyleResolution) -> str:
    return " ".join(resolution.constraints["sound"])


def editing_style_text(resolution: StyleResolution) -> str:
    return " ".join(resolution.constraints["editing"])[:1800]


def bind_editing_style(proposal, profile_id: str, resolution: StyleResolution):
    """Attach deterministic editing guidance without changing order or timing."""
    return proposal.model_copy(update={
        "director_style_id": profile_id,
        "director_style_resolution_sha256": resolution.resolution_sha256,
        "editing_direction": editing_style_text(resolution) or None,
    })


def register_profile_revision(db: Session, entry: CatalogEntry) -> DirectorStyleProfileRevision:
    existing = db.scalar(
        select(DirectorStyleProfileRevision).where(
            DirectorStyleProfileRevision.profile_id == entry.profile.id,
            DirectorStyleProfileRevision.version == entry.profile.version,
        )
    )
    if existing:
        if existing.canonical_sha256 != entry.sha256:
            raise HTTPException(status_code=409, detail="STYLE_PROFILE_VERSION_IMMUTABLE")
        return existing
    row = DirectorStyleProfileRevision(
        profile_id=entry.profile.id,
        version=entry.profile.version,
        source=entry.source,
        canonical_sha256=entry.sha256,
        payload_json=entry.profile.model_dump(mode="json"),
    )
    db.add(row)
    db.flush()
    return row


def binding_summary(binding: dict[str, Any] | None) -> dict[str, Any] | None:
    if not binding:
        return None
    return {
        "profile_id": binding.get("profile_id"),
        "profile_version": binding.get("profile_version"),
        "profile_sha256": binding.get("profile_sha256"),
        "source": binding.get("source"),
        "strength": binding.get("strength"),
        "resolution_sha256": binding.get("resolution_sha256"),
    }


def english_style_bible(binding: dict[str, Any] | None) -> StyleBible | None:
    """Render a frozen director template's English techniques as a StyleBible.

    The official H3 rule accepts only English visual description outside the
    tagged dialogue.  A manually created shot carries no per-shot English
    ``prompt_style``, while the visible Style Bible may be Chinese; the same
    frozen template already declares English ``instruction_en`` for each
    technique, so the compiler can use that faithful projection instead of
    copying CJK into the prompt.  The frozen version and hash stay unchanged.
    """
    if not isinstance(binding, dict):
        return None
    snapshot = binding.get("profile_snapshot")
    if not isinstance(snapshot, dict):
        return None
    grouped: dict[str, list[tuple[int, str, str]]] = {}
    for item in snapshot.get("techniques") or []:
        if not isinstance(item, dict):
            continue
        target = str(item.get("target") or "")
        instruction = str(item.get("instruction_en") or "").strip()
        if not target or not instruction:
            continue
        grouped.setdefault(target, []).append(
            (int(item.get("priority") or 0), str(item.get("id") or ""), instruction)
        )
    if not grouped:
        return None
    ordered = {
        target: [text for _priority, _key, text in sorted(items, key=lambda row: (-row[0], row[1]))]
        for target, items in grouped.items()
    }

    def join(target: str, fallback: str) -> str:
        return " ".join(ordered.get(target, [])[:2]) or fallback

    camera = ordered.get("camera", [])
    return StyleBible(
        photography_language=join(
            "camera", "Use restrained, balanced compositions with readable spatial depth."
        ),
        shot_blocking=(
            camera[1] if len(camera) > 1 else join(
                "camera", "Keep the subject's movement direction and screen axis clear."
            )
        ),
        color_palette=join(
            "color", "Use muted, coherent colors with a readable subject-background contrast."
        ),
        lighting_tone=join(
            "lighting", "Use motivated light with legible shadows and facial detail."
        ),
        lens_texture=join(
            "texture", "Preserve natural surface detail and spatial clarity."
        ),
        editing_rhythm=join(
            "editing", "Let each shot hold briefly after its information lands."
        ),
        sound_performance=join(
            "sound", "Keep sparse room tone and low ambient texture; preserve scripted dialogue."
        ),
    )
