"""The human acceptance of a delivered reel: explicit, bounded, append-only.

A delivery answers "the file is assembled and technically delivered".  This
module answers the other question the workbench asks — "have you watched it
and do you accept it" — with a durable decision instead of a dead label.

Rules:

* only a real, live, non-mock delivery artifact can be decided;
* the decision binds the exact SHA-256 the person saw, and the stored bytes
  must still match it (a changed file cannot be accepted sight-unseen);
* accepting requires the watched declaration; rejecting requires a reason;
* every decision is a new row with a one-use request id; the newest row for
  an artifact is the current answer and earlier rows stay as history;
* nothing here rewrites a job, a quality gate or the frozen assembly
  provenance — acceptance is an overlay, not a retroactive edit.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from shine.media.artifacts import safe_asset_path
from shine.media.delivery_models import DeliveryAcceptance
from shine.media.integrity import verify_media_file
from shine.models import MediaArtifact
from shine.services import add_audit

DECISIONS = ("accepted", "rejected")


def acceptance_public(row: DeliveryAcceptance | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "decision": row.decision,
        "watched": bool(row.watched),
        "evidence": row.evidence,
        "actor": row.actor,
        "artifact_sha256": row.artifact_sha256,
        "created_at": row.created_at.isoformat() + "Z",
    }


def latest_acceptance(db: Session, artifact_id: str) -> DeliveryAcceptance | None:
    return db.scalars(
        select(DeliveryAcceptance)
        .where(DeliveryAcceptance.artifact_id == artifact_id)
        .order_by(DeliveryAcceptance.created_at.desc(), DeliveryAcceptance.id.desc())
        .limit(1)
    ).first()


def acceptances_for(db: Session, artifact_ids: list[str]) -> dict[str, dict[str, Any]]:
    """The current decision of each artifact, in one query."""
    ids = [artifact_id for artifact_id in artifact_ids if artifact_id]
    if not ids:
        return {}
    rows = db.scalars(
        select(DeliveryAcceptance)
        .where(DeliveryAcceptance.artifact_id.in_(ids))
        .order_by(DeliveryAcceptance.created_at.asc(), DeliveryAcceptance.id.asc())
    ).all()
    latest: dict[str, DeliveryAcceptance] = {}
    for row in rows:  # ascending order: the last row per artifact wins
        latest[row.artifact_id] = row
    return {artifact_id: acceptance_public(row) for artifact_id, row in latest.items()}


def record_acceptance(
    db: Session,
    settings,
    artifact_id: str,
    *,
    actor: str,
    decision: str,
    watched: bool,
    evidence: str,
    request_id: str,
    expected_sha256: str,
) -> dict[str, Any]:
    if decision not in DECISIONS:
        raise HTTPException(422, "ACCEPTANCE_DECISION_INVALID")
    artifact = db.get(MediaArtifact, artifact_id)
    if artifact is None or artifact.artifact_kind != "delivery":
        raise HTTPException(404, "DELIVERY_NOT_FOUND")
    if artifact.deleted_at is not None:
        raise HTTPException(404, "VIDEO_DELETED")
    if artifact.is_mock:
        raise HTTPException(409, "MOCK_DELIVERY_NOT_ACCEPTABLE")
    if expected_sha256 != artifact.sha256:
        raise HTTPException(409, "DELIVERY_VERSION_CHANGED")
    if db.scalar(select(DeliveryAcceptance.id).where(DeliveryAcceptance.request_id == request_id)):
        raise HTTPException(409, "ACCEPTANCE_REQUEST_REPLAYED")
    if decision == "accepted" and not watched:
        raise HTTPException(422, "ACCEPTANCE_REQUIRES_WATCHED")
    if decision == "rejected" and not evidence.strip():
        raise HTTPException(422, "ACCEPTANCE_REJECTION_NEEDS_EVIDENCE")

    from shine.media.output_layout import output_root

    path = safe_asset_path(output_root(db, settings), artifact.storage_path)
    verify_media_file(
        path, artifact.sha256, artifact.byte_size,
        mismatch_code="DELIVERY_FILE_HASH_MISMATCH",
        missing_detail="Delivery file is missing on disk",
    )

    row = DeliveryAcceptance(
        project_id=artifact.project_id,
        artifact_id=artifact.id,
        decision=decision,
        watched=bool(watched),
        evidence=evidence[:2000],
        actor=actor,
        artifact_sha256=artifact.sha256,
        request_id=request_id,
    )
    db.add(row)
    add_audit(db, actor, "DELIVERY_ACCEPTANCE_RECORDED", "delivery", artifact.id, {
        "decision": decision,
        "watched": bool(watched),
        "evidence": evidence[:200],
        "artifact_sha256": artifact.sha256,
        "request_id": request_id,
    }, project_id=artifact.project_id)
    db.commit()
    return {"acceptance": acceptance_public(row), "artifact_id": artifact.id}