"""Short-lived, one-use, action and snapshot-bound human grants."""

from datetime import timedelta
from secrets import token_hex

from fastapi import HTTPException
from sqlalchemy import update

from shine.comfy.workflow import graph_hash
from shine.comfyops.models import ApprovalGrant
from shine.models import utcnow
from shine.services import add_audit


def snapshot(row):
    if row.__tablename__ == "projects":
        return graph_hash(
            {"id": row.id, "style_draft": row.style_draft, "frozen_style_version_id": row.frozen_style_version_id}
        )
    if row.__tablename__ == "assets":
        return graph_hash({c.name: str(getattr(row, c.name)) for c in row.__table__.columns})
    return graph_hash(
        {
            "id": row.id,
            "project": getattr(row, "project_id", None),
            "payload": row.payload,
            "status": row.status,
            "sha256": row.sha256,
        }
    )


def issue(db, actor, action, row, *, context=None):
    grant = ApprovalGrant(
        id=token_hex(32),
        actor=actor,
        action=action,
        target_id=row.id,
        snapshot=graph_hash({"target": snapshot(row), "context": context}) if context is not None else snapshot(row),
        expires_at=utcnow() + timedelta(minutes=5),
    )
    db.add(grant)
    add_audit(
        db,
        actor,
        "PREPARATION_GRANT_ISSUED",
        row.__tablename__,
        row.id,
        {"action": action, "snapshot": grant.snapshot},
        project_id=getattr(row, "project_id", row.id if row.__tablename__ == "projects" else None),
    )
    db.commit()
    return {"grant_id": grant.id, "snapshot": grant.snapshot, "expires_at": grant.expires_at}


def consume(db, actor, action, row, grant_id, expected, *, context=None):
    current = graph_hash({"target": snapshot(row), "context": context}) if context is not None else snapshot(row)
    if current != expected:
        raise HTTPException(409, "PLAN_HASH_CHANGED")
    result = db.execute(
        update(ApprovalGrant)
        .where(
            ApprovalGrant.id == grant_id,
            ApprovalGrant.actor == actor,
            ApprovalGrant.action == action,
            ApprovalGrant.target_id == row.id,
            ApprovalGrant.snapshot == expected,
            ApprovalGrant.used.is_(False),
            ApprovalGrant.expires_at > utcnow(),
        )
        .values(used=True)
    )
    if result.rowcount != 1:
        raise HTTPException(409, "APPROVAL_REQUIRED_OR_REPLAYED")
