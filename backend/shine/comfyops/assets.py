from __future__ import annotations

import hashlib
import struct
import zlib
from datetime import UTC
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select

from shine.comfy.workflow import graph_hash
from shine.comfyops.models import CandidateAsset, RightsRecord
from shine.media.artifacts import artifact_suffix, safe_asset_path
from shine.models import Asset, utcnow
from shine.security import probe_upload, safe_filename, verify_upload
from shine.services import add_audit

FAMILIES = {
    "character": "image",
    "costume": "image",
    "scene": "image",
    "prop": "image",
    "keyframe": "image",
    "storyboard": "image",
    "action": "video",
    "voice": "audio",
    "dialogue": "audio",
    "sfx": "audio",
    "music": "audio",
    "subtitle": "text",
}


def fake_image(seed):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    color = hashlib.sha256(str(seed).encode()).digest()[:3]
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">2I5B", 32, 32, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress((b"\0" + color * 32) * 32))
        + chunk(b"IEND", b"")
    )


def require_rights(db, project_id, rights_id, scope="asset"):
    rights = db.get(RightsRecord, rights_id)
    if (
        not rights
        or rights.project_id != project_id
        or rights.status != "APPROVED"
        or rights.payload.get("scope") != scope
    ):
        raise HTTPException(409, "VOICE_CONSENT_REQUIRED" if scope == "voice_clone" else "LICENSE_NOT_APPROVED")
    expiry = rights.payload.get("expires_at")
    if expiry:
        from datetime import datetime

        if datetime.fromisoformat(expiry).replace(tzinfo=UTC) <= datetime.now(UTC):
            raise HTTPException(409, "CONSENT_EXPIRED")
    for source in rights.payload.get("shine_assets_sources") or []:
        from shine.asset_center import service as library_service
        from shine.asset_center.models import LibraryMedia, LibraryRights, LibraryVersion

        usage_scope = source.get("usage_scope")
        kind = source.get("kind")
        if kind == "version":
            version = db.get(LibraryVersion, source.get("version_id"))
            if version is None:
                raise HTTPException(409, "LIBRARY_SOURCE_MISSING")
            current = library_service.require_version_rights(
                db, version, project_id=project_id, usage_scope=usage_scope
            )
        elif kind == "media":
            media = db.get(LibraryMedia, source.get("media_id"))
            if media is None:
                raise HTTPException(409, "LIBRARY_SOURCE_MISSING")
            current = library_service.require_rights(
                db.get(LibraryRights, source.get("rights_id") or media.rights_id),
                project_id=project_id,
                usage_scope=usage_scope,
            )
            if media.rights_id != current.id:
                raise HTTPException(409, "LIBRARY_SOURCE_RIGHTS_CHANGED")
        elif kind == "rights":
            current = library_service.require_rights(
                db.get(LibraryRights, source.get("rights_id")),
                project_id=project_id,
                usage_scope=usage_scope,
            )
        else:
            raise HTTPException(409, "LIBRARY_SOURCE_INVALID")
    return rights


#: Why a rights chain is not in force, in the words the person sees.
_RIGHTS_MESSAGES = {
    "LICENSE_NOT_APPROVED": "这个素材的授权已被撤销，或从未批准",
    "CONSENT_EXPIRED": "这个素材的授权已经过期",
    "LIBRARY_RIGHTS_NOT_APPROVED": "这个素材在全局素材库里的授权已被撤销",
    "LIBRARY_SOURCE_RIGHTS_CHANGED": "这个素材的来源授权已经变更",
    "LIBRARY_SOURCE_MISSING": "这个素材的来源记录读不到",
    "VOICE_CONSENT_REQUIRED": "这个素材缺少音色同意记录",
}


def rights_status(db, project_id: str, rights_id: str | None, scope: str = "asset") -> dict[str, Any]:
    """Whether an asset's rights chain is still in force, and why it is not.

    ``require_rights`` refuses; this one answers.  The adoption preview and
    the package readiness need the *reason* next to the shot that carries
    it, not a bare refusal.  An asset without a rights record (a personal
    upload) has no licence chain to lose and is always in force.
    """
    if not rights_id:
        return {"approved": True, "code": None, "message": None, "rights_id": None}
    try:
        require_rights(db, project_id, rights_id, scope)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else (exc.detail or {}).get("code")
        return {
            "approved": False,
            "code": detail or "LICENSE_NOT_APPROVED",
            "message": _RIGHTS_MESSAGES.get(detail or "", "这个素材的授权已经不在有效状态"),
            "rights_id": rights_id,
        }
    return {"approved": True, "code": None, "message": None, "rights_id": rights_id}


def create_candidate(db, settings, project, family, entity, content, filename, rights_id, provenance, parent=None):
    if rights_id:
        require_rights(db, project.id, rights_id)
    if family not in FAMILIES:
        raise HTTPException(422, "UNKNOWN_ASSET_FAMILY")
    if parent:
        previous = db.get(CandidateAsset, parent)
        if not previous or previous.project_id != project.id or previous.payload["entity_id"] != entity:
            raise HTTPException(409, "CROSS_PROJECT_OR_ENTITY_REFERENCE")
    kind = FAMILIES[family]
    filename = safe_filename(filename)
    if kind == "text":
        if not filename.lower().endswith((".srt", ".ass")) or len(content) > settings.max_upload_bytes:
            raise HTTPException(415, "SUBTITLE_FORMAT_INVALID")
        try:
            decoded_text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(415, "SUBTITLE_ENCODING_INVALID") from exc
        if not decoded_text.strip() or ("-->" not in decoded_text and "[Events]" not in decoded_text):
            raise HTTPException(415, "SUBTITLE_FORMAT_INVALID")
        content_type = "text/plain"
        info = {}
    else:
        content_type = verify_upload(kind, filename, content, settings.max_upload_bytes)
        info = probe_upload(kind, content, content_type).__dict__
    digest = hashlib.sha256(content).hexdigest()
    known = db.scalars(
        select(CandidateAsset).where(CandidateAsset.payload["content_sha256"].as_string() == digest)
    ).all()
    if any(c.payload.get("provenance", {}).get("is_mock", False) for c in known):
        provenance = {**provenance, "is_mock": True, "mock_source": "KNOWN_MOCK_CONTENT_HASH"}
    from pathlib import Path

    from shine.media.output_layout import ensure_storage_writable, store_project_asset
    from shine.media.output_names import is_readable_ledger_name, readable_base

    ensure_storage_writable(db)
    base = entity or readable_base(filename, family)
    storage_path, stored_name = store_project_asset(
        db, settings, project, content, digest,
        kind=kind,
        base=base,
        extension=Path(filename).suffix or artifact_suffix(content_type),
    )
    payload = {
        "family": family,
        "entity_id": entity,
        "kind": kind,
        "filename": filename if is_readable_ledger_name(filename) else stored_name,
        "content_type": content_type,
        "content_sha256": digest,
        "byte_size": len(content),
        "storage_path": storage_path,
        "rights_id": rights_id,
        "provenance": provenance,
        "parent_id": parent,
        "style_id": project.frozen_style_version_id,
        "media": info,
        "qc": None,
    }
    row = CandidateAsset(project_id=project.id, payload=payload, sha256=graph_hash(payload), status="GENERATED")
    db.add(row)
    db.flush()
    add_audit(
        db,
        "asset_factory",
        "CANDIDATE_CREATED",
        "candidate",
        row.id,
        {"family": family, "sha256": digest, "is_mock": provenance.get("is_mock", False)},
        project_id=project.id,
    )
    return row


def verify_candidate(settings, row, *, root=None):
    path = safe_asset_path(root or settings.data_dir, row.payload["storage_path"])
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise HTTPException(409, "CANDIDATE_FILE_MISSING") from exc
    if hashlib.sha256(content).hexdigest() != row.payload["content_sha256"]:
        raise HTTPException(409, "CANDIDATE_HASH_CHANGED")
    return content


MEDIA_OVERLAY_CODES = {
    "WATERMARK_PRESENT", "WATERMARK", "LOGO_PRESENT", "LOGO",
    "SIGNATURE_PRESENT", "SIGNATURE", "TEXT_IN_FRAME", "CAPTION_PRESENT",
    "UI_OVERLAY_PRESENT", "PLATFORM_BADGE_PRESENT", "TIMESTAMP_PRESENT",
}
"""Reviewer finding codes that disqualify a picture as a reference master.

The prompt already forbids every one of these marks; this set is the server-side
half of the same rule, so a marked picture is refused rather than retouched.
"""


def overlay_findings(findings) -> list[str]:
    """The reviewer findings that disqualify a picture as a reference master.

    Kept as a pure function so the rule is testable without a reviewer, a file
    or a database: the prompt forbids these marks, and this is the same rule
    applied to whatever came back.
    """
    return [finding.code for finding in findings
            if finding.result == "FAIL" and finding.code.upper() in MEDIA_OVERLAY_CODES]


def candidate_qc(settings, row, reviewer=None, *, root=None):
    if row.status == "PROMOTED":
        raise HTTPException(409, "CANDIDATE_VERSION_FROZEN")
    content = verify_candidate(settings, row, root=root)
    is_mock = row.payload["provenance"].get("is_mock", False)
    p = row.payload
    if p["kind"] != "text":
        verify_upload(p["kind"], p["filename"], content, settings.max_upload_bytes)
        probe_upload(p["kind"], content, p["content_type"])
    from shine.quality.technical import _full_decode_check

    if p["kind"] == "text":
        decode = {"status": "pass", "detail": "UTF-8 subtitle structure checked"}
    else:
        path = safe_asset_path(root or settings.data_dir, p["storage_path"])
        decode = _full_decode_check(path, settings.ffmpeg_path).model_dump()
    qc = {
        "technical": decode["status"],
        "decode": decode,
        "semantic": "MOCK_VERIFIED" if is_mock else "PENDING_HUMAN_REVIEW",
        "is_mock": is_mock,
        "content_sha256": p["content_sha256"],
        "watermark": "UNVERIFIED",
        "limitations": "No real semantic quality, identity, voice or lip-sync acceptance.",
    }
    semantic_failed = False
    if reviewer is not None:
        from shine.comfyops.candidate_quality import CandidateAssessment

        assessment = CandidateAssessment.model_validate(reviewer.review(content, p))
        assessment.is_mock = assessment.is_mock or reviewer.is_mock or is_mock
        qc["semantic_evidence"] = assessment.model_dump()
        # An overlay is a material defect, not a stylistic note: a picture that
        # carries a watermark, logo, signature, badge or timestamp can never be
        # used as a reference master, and cropping it by hand is not a repair the
        # product may assume.  Such a finding fails QC so the material is
        # regenerated from the prompt instead of silently entering a shot.
        overlays = overlay_findings(assessment.findings)
        qc["watermark"] = "FAIL" if overlays else "PASS"
        if overlays:
            qc["overlay_findings"] = overlays
        semantic_failed = bool(overlays) or any(f.result == "FAIL" for f in assessment.findings)
        uncertain = any(f.result == "UNCERTAIN" or f.confidence < 0.9 for f in assessment.findings)
        qc["semantic"] = (
            "FAILED"
            if semantic_failed
            else "PENDING_HUMAN_REVIEW"
            if uncertain
            else ("MOCK_VERIFIED" if assessment.is_mock else "REVIEWED_PENDING_HUMAN_APPROVAL")
        )
        qc["is_mock"] = qc["is_mock"] or assessment.is_mock
    row.payload = {**p, "qc": qc}
    row.sha256 = graph_hash(row.payload)
    row.status = (
        "READY_FOR_REVIEW"
        if decode["status"] == "pass" and not p.get("dialogue_errors") and not semantic_failed
        else "QC_FAILED"
    )
    return qc


def promote(db, settings, row, actor, decision, *, offline):
    from shine.comfyops import approval

    approval.consume(db, actor, "promote", row, decision.grant_id, decision.snapshot)
    if row.status != "READY_FOR_REVIEW":
        raise HTTPException(409, "CANDIDATE_NOT_APPROVED")
    if row.payload.get("rights_id"):
        require_rights(db, row.project_id, row.payload["rights_id"])
    from shine.media.output_layout import output_root

    verify_candidate(settings, row, root=output_root(db, settings))
    p = row.payload
    if p.get("dialogue_review_errors"):
        if not decision.listened:
            raise HTTPException(409, "DIALOGUE_HUMAN_LISTENING_REQUIRED")
        row.payload = {
            **p,
            "human_dialogue_review": {
                "actor": actor,
                "evidence": decision.evidence,
                "listened": True,
                "reviewed_findings": p["dialogue_review_errors"],
            },
        }
        row.sha256 = graph_hash(row.payload)
        p = row.payload
    mock = p["provenance"].get("is_mock", False)
    # Bytes that came from a renderer this installation does not own are not a
    # real render either.  They may be promoted for offline rehearsal, but the
    # master they become stays offline-only and can never count as a verified
    # production material.
    unowned_renderer = p["provenance"].get("renderer_owned", True) is False
    if (mock or unowned_renderer) and not offline:
        raise HTTPException(409, "MOCK_REQUIRES_OFFLINE_APPROVAL")
    if p["kind"] == "text":
        # Subtitles live in the same candidate ledger; they are not H3 media references.
        row.status = "PROMOTED"
        return None
    existing = db.scalar(select(Asset).where(Asset.project_id == row.project_id, Asset.sha256 == p["content_sha256"]))
    if existing:
        raise HTTPException(409, "ASSET_HASH_ALREADY_REGISTERED")
    media = p.get("media") or {}
    # The full-picture character definition travels with the master so the
    # asset centre keeps the accurate whole character, not just a short name.
    character_definition = None
    item_id = (p.get("provenance") or {}).get("item_id")
    if item_id:
        from shine.comfyops.models import AssetRequirement
        from shine.materialprep.models import MaterialItem
        item = db.get(MaterialItem, item_id)
        if item is not None and item.requirement_id:
            requirement = db.get(AssetRequirement, item.requirement_id)
            if requirement is not None:
                character_definition = (requirement.payload or {}).get("character_definition")
    # The candidate's stored file already carries 项目_类型_名称; promotion
    # keeps that readable name instead of inventing a second one.
    filename = p["filename"]
    asset = Asset(
        project_id=row.project_id,
        filename=filename,
        kind=p["kind"],
        content_type=p["content_type"],
        sha256=p["content_sha256"],
        storage_path=p["storage_path"],
        byte_size=p["byte_size"],
        role=p["family"] if p["family"] in {"character", "costume", "scene", "prop", "voice"} else "generic",
        entity_id=p["entity_id"],
        tags=[p["family"]],
        duration_s=media.get("duration_s"),
        width=media.get("width"),
        height=media.get("height"),
        provenance={
            **p["provenance"],
            "candidate_id": row.id,
            "rights_id": p["rights_id"],
            "offline_only": mock or unowned_renderer or offline,
            **({"character_definition": character_definition} if character_definition else {}),
        },
        approved=True,
        locked=True,
        approved_by=actor,
        approved_at=utcnow(),
        locked_at=utcnow(),
    )
    db.add(asset)
    db.flush()
    row.formal_asset_id = asset.id
    row.status = "PROMOTED"
    add_audit(
        db,
        actor,
        "CANDIDATE_PROMOTED",
        "asset",
        asset.id,
        {"candidate_id": row.id, "offline_only": mock or unowned_renderer or offline},
        project_id=row.project_id,
    )
    return asset


def approve_master(db, settings, asset, actor, decision, *, locking=False, asset_root=None):
    """Authenticated API passes a server identity; grants bind all mutable asset fields."""
    from shine.comfyops import approval

    approval.consume(
        db, actor, "asset_lock" if locking else "asset_approve", asset, decision.grant_id, decision.snapshot
    )
    path = safe_asset_path(asset_root or settings.data_dir, asset.storage_path)
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != asset.sha256:
        raise HTTPException(409, "ASSET_HASH_CHANGED")
    if (asset.provenance or {}).get("candidate_id"):
        raise HTTPException(409, "CANDIDATE_APPROVAL_PATH_REQUIRED")
    if locking and not asset.approved:
        raise HTTPException(409, "ASSET_APPROVAL_REQUIRED")
    if asset.locked or (asset.approved and not locking):
        raise HTTPException(409, "ASSET_ALREADY_APPROVED_OR_LOCKED")
    if locking:
        asset.locked, asset.locked_at = True, utcnow()
    else:
        asset.approved, asset.approved_at, asset.approved_by = True, utcnow(), actor
    add_audit(
        db,
        actor,
        "ASSET_LOCKED" if locking else "ASSET_APPROVED",
        "asset",
        asset.id,
        {"sha256": asset.sha256, "snapshot": decision.snapshot, "evidence": decision.evidence},
        project_id=asset.project_id,
    )
