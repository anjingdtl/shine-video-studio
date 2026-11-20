"""Independent schema versioning and initialization for manual4.sqlite."""

from __future__ import annotations

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from shine.manual4.storage.db import M4Base, make_m4_session_factory
from shine.manual4.storage.models import M4SchemaMeta, m4_utcnow

MANUAL4_SCHEMA_VERSION = "1"
MANUAL4_RUNTIME_VERSION = "manual4-runtime/1.0"


def ensure_m4_schema(engine: Engine) -> dict[str, str]:
    """Create Manual4 tables in manual4.sqlite without touching shine.db."""
    M4Base.metadata.create_all(engine)
    factory = make_m4_session_factory(engine)
    with factory.begin() as db:
        _upsert_meta(db, "manual4_schema_version", {"version": MANUAL4_SCHEMA_VERSION})
        _upsert_meta(db, "manual4_runtime_version", {"version": MANUAL4_RUNTIME_VERSION})
    return {
        "manual4_schema_version": MANUAL4_SCHEMA_VERSION,
        "manual4_runtime_version": MANUAL4_RUNTIME_VERSION,
    }


def _upsert_meta(db: Session, key: str, value: dict) -> None:
    row = db.get(M4SchemaMeta, key)
    if row is None:
        db.add(M4SchemaMeta(key=key, value=value, updated_at=m4_utcnow()))
    else:
        row.value = value
        row.updated_at = m4_utcnow()


def read_m4_schema_meta(db: Session) -> dict[str, str]:
    rows = db.query(M4SchemaMeta).all()
    out: dict[str, str] = {}
    for row in rows:
        out[row.key] = str((row.value or {}).get("version") or "")
    return out
