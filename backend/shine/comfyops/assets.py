from __future__ import annotations

import hashlib
import struct
import zlib
from datetime import UTC

from fastapi import HTTPException
from sqlalchemy import select

from shine.comfy.workflow import graph_hash
from shine.comfyops.models import CandidateAsset, RightsRecord
from shine.media.artifacts import safe_asset_path
from shine.models import Asset, utcnow
from shine.security import probe_upload, safe_filename, verify_upload
from shine.services import add_audit, store_asset_file

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
    return rights


def create_candidate(db, settings, project, family, entity, content, filename, rights_id, provenance, parent=None):
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
    payload = {
        "family": family,
        "entity_id": entity,
        "kind": kind,
        "filename": filename,
        "content_type": content_type,
        "content_sha256": digest,
        "byte_size": len(content),
        "storage_path": store_asset_file(settings.data_dir, digest, content),
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


def verify_candidate(settings, row):
    path = safe_asset_path(settings.data_dir, row.payload["storage_path"])
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise HTTPException(409, "CANDIDATE_FILE_MISSING") from exc
    if hashlib.sha256(content).hexdigest() != row.payload["content_sha256"]:
        raise HTTPException(409, "CANDIDATE_HASH_CHANGED")
    return content


def candidate_qc(settings, row, reviewer=None):
    if row.status == "PROMOTED":
        raise HTTPException(409, "CANDIDATE_VERSION_FROZEN")
    content = verify_candidate(settings, row)
    is_mock = row.payload["provenance"].get("is_mock", False)
    p = row.payload
    if p["kind"] != "text":
        verify_upload(p["kind"], p["filename"], content, settings.max_upload_bytes)
        probe_upload(p["kind"], content, p["content_type"])
    from shine.quality.technical import _full_decode_check

    if p["kind"] == "text":
        decode = {"status": "pass", "detail": "UTF-8 subtitle structure checked"}
    else:
        path = safe_asset_path(settings.data_dir, p["storage_path"])
        decode = _full_decode_check(path, settings.ffmpeg_path).model_dump()
    qc = {
        "technical": decode["status"],
        "decode": decode,
        "semantic": "MOCK_VERIFIED" if is_mock else "PENDING_HUMAN_REVIEW",
        "is_mock": is_mock,
        "content_sha256": p["content_sha256"],
        "limitations": "No real semantic quality, identity, voice or lip-sync acceptance.",
    }
    semantic_failed = False
    if reviewer is not None:
        from shine.comfyops.candidate_quality import CandidateAssessment

        assessment = CandidateAssessment.model_validate(reviewer.review(content, p))
        assessment.is_mock = assessment.is_mock or reviewer.is_mock or is_mock
        qc["semantic_evidence"] = assessment.model_dump()
        semantic_failed = any(f.result == "FAIL" for f in assessment.findings)
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
    require_rights(db, row.project_id, row.payload["rights_id"])
    verify_candidate(settings, row)
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
    if mock and not offline:
        raise HTTPException(409, "MOCK_REQUIRES_OFFLINE_APPROVAL")
    if p["kind"] == "text":
        # Subtitles live in the same candidate ledger; they are not H3 media references.
        row.status = "PROMOTED"
        return None
    existing = db.scalar(select(Asset).where(Asset.project_id == row.project_id, Asset.sha256 == p["content_sha256"]))
    if existing:
        raise HTTPException(409, "ASSET_HASH_ALREADY_REGISTERED")
    media = p.get("media") or {}
    asset = Asset(
        project_id=row.project_id,
        filename=p["filename"],
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
            "offline_only": mock or offline,
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
        {"candidate_id": row.id, "offline_only": mock or offline},
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
