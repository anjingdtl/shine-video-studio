"""Dynamic director style catalog, read-only preview, and draft-only adoption."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from shine.api_common import DBSession
from shine.director_styles.catalog import CatalogEntry, DirectorStyleCatalog, DirectorStyleCatalogError
from shine.director_styles.contracts import DirectorStyleApplyRequest, DirectorStylePreviewRequest
from shine.director_styles.resolver import DirectorStyleResolver
from shine.director_styles.service import (
    binding_summary,
    draft_digest,
    make_binding,
    register_profile_revision,
)
from shine.h3.compiler import COMPILER_VERSION, RULE_VERSION
from shine.h3.film_type import FILM_TYPE_INSTRUCTIONS, FILM_TYPE_LABELS
from shine.models import DirectorStyleProfileRevision, Project, StyleVersion
from shine.schemas import STYLE_BIBLE_FIELDS, FilmType
from shine.services import add_audit, require_project, update_style_draft

router = APIRouter(prefix="/api/v1", tags=["director styles"])
_resolver = DirectorStyleResolver()


def _entry_or_http(profile_id: str, version: str | None = None) -> CatalogEntry:
    try:
        return DirectorStyleCatalog().get(profile_id, version)
    except DirectorStyleCatalogError as exc:
        status = 404 if str(exc) == "STYLE_PROFILE_NOT_FOUND" else 503
        raise HTTPException(status_code=status, detail=str(exc)) from exc


def _check_selection(entry: CatalogEntry, profile_version: str, profile_sha256: str) -> None:
    if entry.profile.version != profile_version or entry.sha256 != profile_sha256:
        raise HTTPException(status_code=409, detail="STYLE_PROFILE_STALE")


def _check_persisted_revision(db, entry: CatalogEntry) -> None:
    row = db.scalar(
        select(DirectorStyleProfileRevision).where(
            DirectorStyleProfileRevision.profile_id == entry.profile.id,
            DirectorStyleProfileRevision.version == entry.profile.version,
        )
    )
    if row and row.canonical_sha256 != entry.sha256:
        raise HTTPException(status_code=409, detail="STYLE_PROFILE_VERSION_IMMUTABLE")


def _current_style(db, project: Project) -> dict[str, Any] | None:
    if project.style_draft:
        return project.style_draft
    if project.frozen_style_version_id:
        frozen = db.get(StyleVersion, project.frozen_style_version_id)
        if frozen and frozen.project_id == project.id:
            return frozen.snapshot
    return None


def _preview(
    entry: CatalogEntry,
    request: DirectorStylePreviewRequest,
    current: dict[str, Any] | None,
) -> dict[str, Any]:
    style = request.style_overrides or entry.profile.style_bible
    suggestion = style.model_dump(mode="json")
    resolution = _resolver.resolve(
        entry,
        strength=request.strength,
        scene_tags=request.scene_tags,
    )
    baseline = (request.compare_style.model_dump(mode="json") if request.compare_style else current) or {}
    diff = [
        {
            "field": key,
            "current": baseline.get(key, ""),
            "suggested": suggestion[key],
            "changed": baseline.get(key) != suggestion[key],
        }
        for key in STYLE_BIBLE_FIELDS
        if key in suggestion
    ]
    return {
        "profile": _profile_public(entry),
        "style_bible": suggestion,
        "diff": diff,
        "resolution": resolution.model_dump(mode="json"),
        "h3": {
            "compiler_version": COMPILER_VERSION,
            "official_rule_version": RULE_VERSION,
            "available": True,
            "prompt_entry": "H3PromptCompiler",
        },
        "draft_only": True,
    }


def _profile_public(entry: CatalogEntry, *, availability: str = "available") -> dict[str, Any]:
    profile = entry.profile
    return {
        "id": profile.id,
        "name_zh": profile.name_zh,
        "version": profile.version,
        "summary_zh": profile.summary_zh,
        "categories": profile.categories,
        "sha256": entry.sha256,
        "source": entry.source,
        "technique_count": len(profile.techniques),
        "availability": availability,
    }


@router.get("/film-types")
def list_film_types() -> list[dict[str, str]]:
    """The two fixed H3 film-type contracts; labels stay Chinese, prompts English."""
    return [
        {
            "id": film_type.value,
            "label": FILM_TYPE_LABELS[film_type],
            "instruction_en": FILM_TYPE_INSTRUCTIONS[film_type],
        }
        for film_type in FilmType
    ]


@router.get("/director-styles")
def list_director_styles(db: DBSession) -> list[dict[str, Any]]:
    try:
        entries = DirectorStyleCatalog().list_latest()
        rows = db.scalars(select(DirectorStyleProfileRevision)).all()
        registered = {(row.profile_id, row.version): row.canonical_sha256 for row in rows}
        return [
            _profile_public(
                entry,
                availability=(
                    "version-conflict"
                    if registered.get((entry.profile.id, entry.profile.version), entry.sha256) != entry.sha256
                    else "available"
                ),
            )
            for entry in entries
        ]
    except DirectorStyleCatalogError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/director-styles/{profile_id}")
def get_director_style(profile_id: str, db: DBSession) -> dict[str, Any]:
    entry = _entry_or_http(profile_id)
    try:
        _check_persisted_revision(db, entry)
    except HTTPException:
        return _profile_public(entry, availability="version-conflict") | {
            "profile": entry.profile.model_dump(mode="json")
        }
    return _profile_public(entry) | {"profile": entry.profile.model_dump(mode="json")}


@router.get("/projects/{project_id}/director-style")
def get_project_director_style(project_id: str, db: DBSession) -> dict[str, Any]:
    project = require_project(db, project_id)
    frozen = db.get(StyleVersion, project.frozen_style_version_id) if project.frozen_style_version_id else None
    if frozen and frozen.project_id != project.id:
        frozen = None
    return {
        "project_id": project.id,
        "style_draft": project.style_draft,
        "draft_sha256": draft_digest(project.style_draft, project.director_style_draft),
        "draft_source": binding_summary(project.director_style_draft),
        "frozen_style_version_id": frozen.id if frozen else None,
        "frozen_source": binding_summary(frozen.director_style_binding) if frozen else None,
    }


@router.post("/projects/{project_id}/director-style/preview")
def preview_project_director_style(
    project_id: str,
    body: DirectorStylePreviewRequest,
    db: DBSession,
) -> dict[str, Any]:
    project = require_project(db, project_id)
    entry = _entry_or_http(body.profile_id, body.profile_version)
    _check_selection(entry, body.profile_version, body.profile_sha256)
    _check_persisted_revision(db, entry)
    return _preview(entry, body, _current_style(db, project))


@router.post("/projects/{project_id}/director-style/apply-draft")
def apply_director_style_draft(
    project_id: str,
    body: DirectorStyleApplyRequest,
    db: DBSession,
) -> dict[str, Any]:
    project = require_project(db, project_id)
    if body.expected_draft_sha256 != draft_digest(project.style_draft, project.director_style_draft):
        raise HTTPException(status_code=409, detail="STYLE_DRAFT_CONFLICT")
    entry = _entry_or_http(body.profile_id, body.profile_version)
    _check_selection(entry, body.profile_version, body.profile_sha256)
    _check_persisted_revision(db, entry)
    current = _current_style(db, project) or {}
    carried_film_type = current.get("film_type")
    style = body.style_overrides or entry.profile.style_bible.model_copy(update={
        "dialogue_ratio_percent": current.get("dialogue_ratio_percent"),
        # The film type is a project-level H3 contract, not one of the seven
        # dimensions a director template governs.  Adopting a template must never
        # silently turn an animation project back into the model's default look.
        "film_type": FilmType(carried_film_type) if carried_film_type else None,
    })
    try:
        register_profile_revision(db, entry)
        update_style_draft(db, project, style, preserve_director_style=False)
        project.director_style_draft = make_binding(entry, body.strength, style)
        add_audit(
            db,
            "user",
            "DIRECTOR_STYLE_DRAFT_APPLIED",
            "project",
            project.id,
            {
                "profile_id": entry.profile.id,
                "profile_version": entry.profile.version,
                "profile_sha256": entry.sha256,
                "strength": body.strength,
            },
            project_id=project.id,
        )
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except ValidationError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail={"code": "STYLE_PROFILE_INVALID"}) from exc
    project = require_project(db, project_id)
    return {
        "project_id": project.id,
        "style_draft": project.style_draft,
        "draft_sha256": draft_digest(project.style_draft, project.director_style_draft),
        "draft_source": binding_summary(project.director_style_draft),
        "frozen": False,
    }
