"""Manual4 Stage 3 manual H3 prompt management and reference manifest integrity."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from shine.manual4.h3.spec_review import review_h3_prompt
from shine.manual4.storage.models import (
    M4AssetVersion,
    M4AuditEvent,
    M4ManualPromptRevision,
    M4Project,
    M4Shot,
    M4ShotAssetBinding,
    M4StoryboardRevision,
    m4_utcnow,
)


def _canonical_sha(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_shot_reference_manifest(m4_db: Session, shot: M4Shot) -> list[dict[str, Any]]:
    links = m4_db.scalars(
        select(M4ShotAssetBinding)
        .where(M4ShotAssetBinding.shot_id == shot.id)
        .order_by(M4ShotAssetBinding.ordinal)
    ).all()
    if not links:
        return []
    asset_ids = [link.asset_version_id for link in links]
    assets = {
        a.id: a
        for a in m4_db.scalars(select(M4AssetVersion).where(M4AssetVersion.id.in_(asset_ids))).all()
    }
    manifest: list[dict[str, Any]] = []
    img_idx = 0
    aud_idx = 0
    vid_idx = 0
    for link in links:
        asset = assets.get(link.asset_version_id)
        if asset is None or asset.status != "promoted":
            continue
        if link.use_as == "audio":
            aud_idx += 1
            h3_label = f"<Audio {aud_idx}>"
        elif link.use_as == "video":
            vid_idx += 1
            h3_label = f"<Video {vid_idx}>"
        else:
            img_idx += 1
            prefix = "Subject" if asset.family == "character" else "Picture"
            h3_label = f"<{prefix} {img_idx}>"
        manifest.append(
            {
                "asset_version_id": asset.id,
                "sha256": asset.sha256,
                "content_type": asset.content_type,
                "family": asset.family,
                "entity_name": asset.entity_name,
                "use_as": link.use_as,
                "role_label": link.role_label or asset.entity_name,
                "ordinal": link.ordinal,
                "h3_label": h3_label,
                "storage_path": asset.storage_path,
                "is_mock": bool(asset.is_mock),
            }
        )
    return manifest


def manifest_digest(manifest: list[dict[str, Any]]) -> str:
    stable = [
        {
            "asset_version_id": m["asset_version_id"],
            "sha256": m["sha256"],
            "use_as": m["use_as"],
            "role_label": m["role_label"],
            "ordinal": m["ordinal"],
        }
        for m in manifest
    ]
    return _canonical_sha(stable)


def validate_frozen_prompt_for_shot(
    m4_db: Session,
    shot: M4Shot,
    prompt_row: M4ManualPromptRevision | None,
) -> tuple[bool, str | None]:
    if prompt_row is None:
        return False, "M4_PROMPT_NOT_FROZEN"
    if prompt_row.status == "INVALIDATED":
        return False, prompt_row.invalidated_reason or "M4_MANUAL_PROMPT_INVALIDATED"
    if prompt_row.status != "FROZEN":
        return False, "M4_PROMPT_NOT_FROZEN"
    if prompt_row.shot_revision != shot.revision:
        return False, "M4_SHOT_REVISION_CHANGED"
    if abs(float(prompt_row.target_seconds) - float(shot.target_seconds)) > 1e-6:
        return False, "M4_DURATION_CHANGED"
    manifest = build_shot_reference_manifest(m4_db, shot)
    links = m4_db.scalars(
        select(M4ShotAssetBinding)
        .where(M4ShotAssetBinding.shot_id == shot.id)
        .order_by(M4ShotAssetBinding.ordinal)
    ).all()
    asset_ids = [link.asset_version_id for link in links]
    assets_by_id = (
        {
            a.id: a
            for a in m4_db.scalars(select(M4AssetVersion).where(M4AssetVersion.id.in_(asset_ids))).all()
        }
        if asset_ids
        else {}
    )
    from shine.manual4.status.readiness import evaluate_shot_ref2va_bindings

    ok_ref, ref_code, _ = evaluate_shot_ref2va_bindings(links, assets_by_id)
    if not ok_ref:
        return False, (
            "M4_PROJECT_REQUIRES_REF2VA"
            if ref_code in {"PROJECT_REQUIRES_REF2VA", "M4_SHOT_REQUIRES_REF2VA_REFERENCE"}
            else (ref_code or "M4_PROJECT_REQUIRES_REF2VA")
        )
    if prompt_row.reference_manifest_sha256 != manifest_digest(manifest):
        return False, "M4_REFERENCE_MANIFEST_CHANGED"
    return True, None


def _get_confirmed_shot(m4_db: Session, project: M4Project, shot_id: str) -> tuple[M4StoryboardRevision, M4Shot]:
    confirmed = m4_db.scalar(
        select(M4StoryboardRevision)
        .where(M4StoryboardRevision.project_id == project.id, M4StoryboardRevision.status == "CONFIRMED")
        .order_by(M4StoryboardRevision.revision.desc())
        .limit(1)
    )
    if confirmed is None:
        raise HTTPException(409, {"code": "M4_STORYBOARD_NOT_CONFIRMED", "message": "请先确认①人工分镜计划。"})
    shot = m4_db.get(M4Shot, shot_id)
    if shot is None or shot.project_id != project.id or shot.storyboard_revision_id != confirmed.id:
        raise HTTPException(404, {"code": "M4_SHOT_NOT_FOUND", "message": "当前分镜版本中未找到该镜头。"})
    return confirmed, shot


def save_prompt_draft(
    m4_db: Session,
    project: M4Project,
    shot_id: str,
    *,
    actor: str,
    prompt_text: str,
) -> dict[str, Any]:
    _confirmed, shot = _get_confirmed_shot(m4_db, project, shot_id)
    text = (prompt_text or "").strip()
    manifest = build_shot_reference_manifest(m4_db, shot)
    labels = {m["h3_label"] for m in manifest}
    warnings = review_h3_prompt(
        text,
        mode="Ref2VA",
        dialogue=shot.dialogue,
        duration_s=shot.target_seconds,
        defined_labels=labels,
    )

    draft = m4_db.scalar(
        select(M4ManualPromptRevision)
        .where(M4ManualPromptRevision.shot_id == shot.id, M4ManualPromptRevision.status == "DRAFT")
        .order_by(M4ManualPromptRevision.revision.desc())
        .limit(1)
    )
    now = m4_utcnow()
    p_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    m_sha = manifest_digest(manifest)
    if draft is None:
        latest_rev = m4_db.scalar(
            select(M4ManualPromptRevision.revision)
            .where(M4ManualPromptRevision.shot_id == shot.id)
            .order_by(M4ManualPromptRevision.revision.desc())
            .limit(1)
        )
        draft = M4ManualPromptRevision(
            project_id=project.id,
            shot_id=shot.id,
            revision=int(latest_rev or 0) + 1,
            status="DRAFT",
            prompt_text=text,
            prompt_sha256=p_sha,
            shot_revision=shot.revision,
            target_seconds=shot.target_seconds,
            generation_mode="Ref2VA",
            reference_manifest=manifest,
            reference_manifest_sha256=m_sha,
            warnings=warnings,
        )
        m4_db.add(draft)
        m4_db.flush()
    else:
        draft.prompt_text = text
        draft.prompt_sha256 = p_sha
        draft.shot_revision = shot.revision
        draft.target_seconds = shot.target_seconds
        draft.reference_manifest = manifest
        draft.reference_manifest_sha256 = m_sha
        draft.warnings = warnings
        draft.updated_at = now

    m4_db.add(
        M4AuditEvent(
            project_id=project.id,
            actor=actor,
            stage="production",
            event_type="M4_PROMPT_DRAFT_SAVED",
            entity_type="m4_manual_prompt_revision",
            entity_id=draft.id,
            payload={"shot_code": shot.shot_code, "revision": draft.revision, "prompt_sha256": p_sha},
        )
    )
    return _serialize_prompt(draft)


def freeze_prompt(
    m4_db: Session,
    project: M4Project,
    shot_id: str,
    *,
    actor: str,
    prompt_text: str | None = None,
) -> dict[str, Any]:
    _confirmed, shot = _get_confirmed_shot(m4_db, project, shot_id)
    manifest = build_shot_reference_manifest(m4_db, shot)
    if not any(m["use_as"] == "image" for m in manifest):
        raise HTTPException(
            409,
            {
                "code": "M4_PROJECT_REQUIRES_REF2VA",
                "message": f"镜头 {shot.shot_code} 至少需要 1 张已晋升绑定的参考图，严禁纯文本冻结。",
            },
        )

    if prompt_text is not None:
        text = prompt_text.strip()
    else:
        draft = m4_db.scalar(
            select(M4ManualPromptRevision)
            .where(M4ManualPromptRevision.shot_id == shot.id, M4ManualPromptRevision.status == "DRAFT")
            .order_by(M4ManualPromptRevision.revision.desc())
            .limit(1)
        )
        text = (draft.prompt_text if draft else "").strip()

    if not text:
        raise HTTPException(422, {"code": "M4_PROMPT_EMPTY", "message": "人工 H3 提示词不能为空，请填写后冻结。"})

    labels = {m["h3_label"] for m in manifest}
    warnings = review_h3_prompt(
        text,
        mode="Ref2VA",
        dialogue=shot.dialogue,
        duration_s=shot.target_seconds,
        defined_labels=labels,
    )

    now = m4_utcnow()
    for prior in m4_db.scalars(
        select(M4ManualPromptRevision).where(
            M4ManualPromptRevision.shot_id == shot.id,
            M4ManualPromptRevision.status.in_(["FROZEN", "DRAFT"]),
        )
    ).all():
        prior.status = "SUPERSEDED"
        prior.updated_at = now

    latest_rev = m4_db.scalar(
        select(M4ManualPromptRevision.revision)
        .where(M4ManualPromptRevision.shot_id == shot.id)
        .order_by(M4ManualPromptRevision.revision.desc())
        .limit(1)
    )
    p_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    m_sha = manifest_digest(manifest)
    frozen = M4ManualPromptRevision(
        project_id=project.id,
        shot_id=shot.id,
        revision=int(latest_rev or 0) + 1,
        status="FROZEN",
        prompt_text=text,
        prompt_sha256=p_sha,
        shot_revision=shot.revision,
        target_seconds=shot.target_seconds,
        generation_mode="Ref2VA",
        reference_manifest=manifest,
        reference_manifest_sha256=m_sha,
        warnings=warnings,
        frozen_by=actor,
        frozen_at=now,
    )
    m4_db.add(frozen)
    m4_db.flush()

    m4_db.add(
        M4AuditEvent(
            project_id=project.id,
            actor=actor,
            stage="production",
            event_type="M4_PROMPT_FROZEN",
            entity_type="m4_manual_prompt_revision",
            entity_id=frozen.id,
            payload={
                "shot_code": shot.shot_code,
                "revision": frozen.revision,
                "prompt_sha256": p_sha,
                "reference_manifest_sha256": m_sha,
            },
        )
    )
    return _serialize_prompt(frozen)


def update_shot_render_options(
    m4_db: Session,
    project: M4Project,
    shot_id: str,
    *,
    actor: str,
    aspect_ratio: str | None = None,
    continuity_mode: str | None = None,
) -> dict[str, Any]:
    _confirmed, shot = _get_confirmed_shot(m4_db, project, shot_id)
    prev_ar = shot.aspect_ratio
    prev_cm = shot.continuity_mode
    if aspect_ratio is not None:
        ar = aspect_ratio.strip()
        if ar not in {"16:9", "9:16"}:
            raise HTTPException(422, {"code": "M4_ASPECT_RATIO_INVALID", "message": f"不支持的画幅：{ar}"})
        shot.aspect_ratio = ar
    if continuity_mode is not None:
        cm = continuity_mode.strip().lower()
        if cm not in {"cut", "last_frame"}:
            raise HTTPException(422, {"code": "M4_CONTINUITY_MODE_INVALID", "message": f"不支持的衔接方式：{cm}"})
        shot.continuity_mode = cm

    changed = (shot.aspect_ratio != prev_ar) or (shot.continuity_mode != prev_cm)
    invalidated_count = 0
    if changed:
        shot.revision += 1
        shot.content_sha256 = _canonical_sha(
            {
                "target_seconds": round(float(shot.target_seconds), 4),
                "summary": str(shot.summary or ""),
                "dialogue": list(shot.dialogue or []),
                "requested_assets": list(shot.requested_assets or []),
                "aspect_ratio": str(shot.aspect_ratio or "16:9"),
                "continuity_mode": str(shot.continuity_mode or "cut"),
            }
        )
        for pr in m4_db.scalars(
            select(M4ManualPromptRevision).where(
                M4ManualPromptRevision.shot_id == shot.id,
                M4ManualPromptRevision.status == "FROZEN",
            )
        ).all():
            pr.status = "INVALIDATED"
            pr.invalidated_reason = "M4_SHOT_OPTIONS_CHANGED"
            pr.updated_at = m4_utcnow()
            invalidated_count += 1

    shot.updated_at = m4_utcnow()
    m4_db.add(
        M4AuditEvent(
            project_id=project.id,
            actor=actor,
            stage="production",
            event_type="M4_SHOT_OPTIONS_UPDATED",
            entity_type="m4_shot",
            entity_id=shot.id,
            payload={
                "aspect_ratio": shot.aspect_ratio,
                "continuity_mode": shot.continuity_mode,
                "shot_revision": shot.revision,
                "invalidated_prompts": invalidated_count,
            },
        )
    )
    return {
        "shot_id": shot.id,
        "shot_code": shot.shot_code,
        "shot_revision": shot.revision,
        "aspect_ratio": shot.aspect_ratio,
        "continuity_mode": shot.continuity_mode,
    }


def _serialize_prompt(row: M4ManualPromptRevision) -> dict[str, Any]:
    return {
        "id": row.id,
        "shot_id": row.shot_id,
        "revision": row.revision,
        "status": row.status,
        "prompt_text": row.prompt_text,
        "prompt_sha256": row.prompt_sha256,
        "shot_revision": row.shot_revision,
        "target_seconds": row.target_seconds,
        "generation_mode": row.generation_mode,
        "reference_manifest": row.reference_manifest,
        "reference_manifest_sha256": row.reference_manifest_sha256,
        "warnings": row.warnings,
        "frozen_by": row.frozen_by,
        "frozen_at": row.frozen_at,
        "invalidated_reason": row.invalidated_reason,
        "updated_at": row.updated_at,
    }
