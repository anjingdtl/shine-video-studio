"""Manual4 Stage 3 manual H3 prompt management and reference manifest integrity."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from shine.foundation.h3_audio import canonical_h3_language, sanitize_h3_dialogue_text
from shine.manual4.contracts.schemas import canonical_digest
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
from shine.manual4.storyboard.parser import canonical_entity_key


def _canonical_sha(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _binding_family_rank(link: M4ShotAssetBinding, asset: M4AssetVersion) -> int:
    """H3 Ref2VA reference slot order: characters (<Subject>) -> other images (<Picture>) -> video -> audio."""
    if link.use_as == "audio":
        return 3
    if link.use_as == "video":
        return 2
    if asset.family == "character":
        return 0
    return 1


def _asset_requested_rank(shot: M4Shot | None, asset: M4AssetVersion, role_label: str) -> int:
    req_assets = list(getattr(shot, "requested_assets", None) or [])
    if not req_assets:
        return 0
    exact_rank: dict[tuple[str, str], int] = {}
    name_rank: dict[tuple[str, str], int] = {}
    bare_rank: dict[str, int] = {}
    for idx, item in enumerate(req_assets):
        if not isinstance(item, dict):
            continue
        fam = str(item.get("family") or "").strip().lower()
        name = str(item.get("name") or "").strip()
        var = str(item.get("variant") or "").strip()
        if not name:
            continue
        exact_rank.setdefault((fam, canonical_entity_key(name, var)), idx)
        name_rank.setdefault((fam, canonical_entity_key(name, "")), idx)
        bare_rank.setdefault(canonical_entity_key(name, ""), idx)

    fam = str(asset.family or "").strip().lower()
    exact_key = (fam, canonical_entity_key(asset.entity_name, asset.variant_name))
    if exact_key in exact_rank:
        return exact_rank[exact_key]
    base_key = (fam, canonical_entity_key(asset.entity_name, ""))
    if base_key in name_rank:
        return name_rank[base_key]
    role_base = canonical_entity_key(role_label or asset.entity_name, "")
    if role_base in bare_rank:
        return bare_rank[role_base]
    return len(req_assets)


def order_m4_shot_bindings(
    links: list[M4ShotAssetBinding],
    assets_by_id: dict[str, M4AssetVersion],
    shot: M4Shot | None = None,
) -> list[M4ShotAssetBinding]:
    """Single authoritative binding order matching H3 Ref2VA slot presentation.

    Characters (<Subject 1..N>) always precede non-character images (<Picture>),
    and characters follow the shot's storyboard cast order rather than arbitrary
    promotion/upload order so Shot 1 and later shots bind the exact right face.
    """
    def _sort_key(link: M4ShotAssetBinding) -> tuple[int, int, int, str]:
        asset = assets_by_id.get(link.asset_version_id)
        if asset is None:
            return (9, 0, link.ordinal, link.asset_version_id)
        return (
            _binding_family_rank(link, asset),
            _asset_requested_rank(shot, asset, link.role_label),
            link.ordinal,
            asset.id,
        )

    return sorted(links, key=_sort_key)


def label_ordered_m4_bindings(
    ordered_links: list[M4ShotAssetBinding],
    assets_by_id: dict[str, M4AssetVersion],
) -> list[tuple[M4ShotAssetBinding, M4AssetVersion, str, int | None]]:
    """Assign deterministic H3 labels (<Subject 1..>, <Picture k>, <Video k>, <Audio k>)."""
    labeled: list[tuple[M4ShotAssetBinding, M4AssetVersion, str, int | None]] = []
    subject_idx = 0
    img_idx = 0
    aud_idx = 0
    vid_idx = 0
    for link in ordered_links:
        asset = assets_by_id.get(link.asset_version_id)
        if asset is None or asset.status != "promoted":
            continue
        if link.use_as == "audio":
            aud_idx += 1
            labeled.append((link, asset, f"<Audio {aud_idx}>", None))
        elif link.use_as == "video":
            vid_idx += 1
            labeled.append((link, asset, f"<Video {vid_idx}>", None))
        else:
            img_idx += 1
            if asset.family == "character":
                subject_idx += 1
                h3_label = f"<Subject {subject_idx}>"
            else:
                h3_label = f"<Picture {img_idx}>"
            labeled.append((link, asset, h3_label, img_idx))
    return labeled


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
    ordered_links = order_m4_shot_bindings(links, assets, shot)
    manifest: list[dict[str, Any]] = []
    for link, asset, h3_label, image_slot in label_ordered_m4_bindings(ordered_links, assets):
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
                "image_slot": image_slot,
                "storage_path": asset.storage_path,
                "media_url": f"/api/v1/manual4/projects/{shot.project_id}/assets/{asset.id}/media",
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
            "h3_label": m.get("h3_label", ""),
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


_D_BLOCK_RE = re.compile(r"<d\s*>(.*?)</d\s*>", flags=re.IGNORECASE | re.DOTALL)
_LANG_TAG_PREFIX_RE = re.compile(r"^\s*\[([^\[\]]{1,32})\]\s*(.*)$", flags=re.DOTALL)


def _normalize_explicit_d_tags(text: str) -> str:
    if "<d" not in (text or "").lower():
        return text

    def _rewrite(match: re.Match[str]) -> str:
        inner = match.group(1).strip()
        m_lang = _LANG_TAG_PREFIX_RE.match(inner)
        if m_lang:
            lang = canonical_h3_language(m_lang.group(1))
            body = sanitize_h3_dialogue_text(m_lang.group(2))
        else:
            lang = "Chinese"
            body = sanitize_h3_dialogue_text(inner)
        return f"<d>[{lang}] {body}</d>" if body else ""

    return _D_BLOCK_RE.sub(_rewrite, text).strip()


def save_prompt_draft(
    m4_db: Session,
    project: M4Project,
    shot_id: str,
    *,
    actor: str,
    prompt_text: str,
) -> dict[str, Any]:
    _confirmed, shot = _get_confirmed_shot(m4_db, project, shot_id)
    text = _normalize_explicit_d_tags((prompt_text or "").strip())
    manifest = build_shot_reference_manifest(m4_db, shot)
    labels = {m["h3_label"] for m in manifest} | {
        f"<Picture {m['image_slot']}>" for m in manifest if m.get("image_slot")
    }
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
        text = _normalize_explicit_d_tags(prompt_text.strip())
    else:
        draft = m4_db.scalar(
            select(M4ManualPromptRevision)
            .where(M4ManualPromptRevision.shot_id == shot.id, M4ManualPromptRevision.status == "DRAFT")
            .order_by(M4ManualPromptRevision.revision.desc())
            .limit(1)
        )
        text = _normalize_explicit_d_tags((draft.prompt_text if draft else "").strip())

    if not text:
        raise HTTPException(422, {"code": "M4_PROMPT_EMPTY", "message": "人工 H3 提示词不能为空，请填写后冻结。"})

    labels = {m["h3_label"] for m in manifest} | {
        f"<Picture {m['image_slot']}>" for m in manifest if m.get("image_slot")
    }
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


def batch_freeze_prompts(
    m4_db: Session,
    project: M4Project,
    *,
    actor: str,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Batch freeze shots that have entered prompt text, delegating each item to freeze_prompt."""
    if not items:
        raise HTTPException(
            422,
            {"code": "M4_PROMPT_BATCH_EMPTY", "message": "当前没有可批量冻结的已填提示词镜头。"},
        )

    deduped: list[tuple[str, str | None]] = []
    seen_ids: set[str] = set()
    for item in items:
        sid = str(item.get("shot_id") or "").strip()
        if not sid or sid in seen_ids:
            continue
        seen_ids.add(sid)
        raw_text = item.get("prompt_text")
        deduped.append((sid, str(raw_text) if raw_text is not None else None))

    if not deduped:
        raise HTTPException(
            422,
            {"code": "M4_PROMPT_BATCH_EMPTY", "message": "当前没有可批量冻结的已填提示词镜头。"},
        )

    shots_by_id = {
        s.id: s
        for s in m4_db.scalars(
            select(M4Shot).where(M4Shot.project_id == project.id, M4Shot.id.in_(list(seen_ids)))
        ).all()
    }

    frozen_shot_ids: list[str] = []
    skipped: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for shot_id, prompt_text in deduped:
        shot_row = shots_by_id.get(shot_id)
        shot_code = shot_row.shot_code if shot_row is not None else shot_id[:8]
        try:
            with m4_db.begin_nested():
                freeze_prompt(m4_db, project, shot_id, actor=actor, prompt_text=prompt_text)
            frozen_shot_ids.append(shot_id)
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
            code = str(detail.get("code") or "M4_PROMPT_BATCH_ERROR")
            msg = str(detail.get("message") or str(exc.detail))
            if code == "M4_PROMPT_EMPTY":
                skipped.append({"shot_id": shot_id, "shot_code": shot_code, "code": code, "reason": msg})
            else:
                errors.append({"shot_id": shot_id, "shot_code": shot_code, "code": code, "message": msg})

    m4_db.add(
        M4AuditEvent(
            project_id=project.id,
            actor=actor,
            stage="production",
            event_type="M4_PROMPT_BATCH_FROZEN",
            entity_type="m4_project",
            entity_id=project.id,
            payload={
                "requested": len(deduped),
                "frozen": len(frozen_shot_ids),
                "skipped": len(skipped),
                "errors": len(errors),
            },
        )
    )
    return {
        "frozen_shot_ids": frozen_shot_ids,
        "skipped": skipped,
        "errors": errors,
    }


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

        # Sync confirmed & any open draft storyboard payloads so Stage 1 and Stage 3 share one truth
        rev_rows = m4_db.scalars(
            select(M4StoryboardRevision).where(
                M4StoryboardRevision.project_id == project.id,
                M4StoryboardRevision.status.in_(["CONFIRMED", "DRAFT"]),
            )
        ).all()
        for rev_row in rev_rows:
            if not isinstance(rev_row.payload, dict) or not isinstance(rev_row.payload.get("episodes"), list):
                continue
            payload_copy = dict(rev_row.payload)
            touched = False
            new_eps: list[dict[str, Any]] = []
            for ep in payload_copy["episodes"]:
                ep_copy = dict(ep)
                new_shots: list[dict[str, Any]] = []
                for psh in ep_copy.get("shots") or []:
                    sh_copy = dict(psh)
                    if sh_copy.get("shot_code") == shot.shot_code:
                        sh_copy["aspect_ratio"] = shot.aspect_ratio
                        sh_copy["continuity_mode"] = shot.continuity_mode
                        touched = True
                    new_shots.append(sh_copy)
                ep_copy["shots"] = new_shots
                new_eps.append(ep_copy)
            if touched:
                payload_copy["episodes"] = new_eps
                rev_row.payload = payload_copy
                rev_row.summary_sha256 = canonical_digest(payload_copy)
                rev_row.updated_at = m4_utcnow()

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
