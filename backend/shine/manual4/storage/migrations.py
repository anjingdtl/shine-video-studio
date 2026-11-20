"""Independent schema versioning and incremental initialization for manual4.sqlite."""

from __future__ import annotations

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from shine.manual4.storage.db import M4Base, make_m4_session_factory
from shine.manual4.storage.models import M4SchemaMeta, m4_utcnow

MANUAL4_SCHEMA_VERSION = "2"
MANUAL4_RUNTIME_VERSION = "manual4-runtime/2.0"

_INCREMENTAL_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("m4_shots", "content_sha256", "VARCHAR(64) NOT NULL DEFAULT ''"),
    ("m4_material_requirements", "pending_task_id", "VARCHAR(36)"),
    ("m4_asset_versions", "library_asset_id", "VARCHAR(36)"),
    ("m4_asset_versions", "library_version_id", "VARCHAR(36)"),
    ("m4_asset_versions", "library_rights_id", "VARCHAR(36)"),
    ("m4_asset_versions", "rights_status", "VARCHAR(24) NOT NULL DEFAULT 'approved'"),
    ("m4_asset_versions", "library_task_id", "VARCHAR(36)"),
    ("m4_production_runs", "storyboard_revision_id", "VARCHAR(36)"),
    ("m4_production_runs", "execution_generation", "INTEGER NOT NULL DEFAULT 1"),
    ("m4_jobs", "execution_generation", "INTEGER NOT NULL DEFAULT 1"),
    ("m4_job_attempts", "execution_generation", "INTEGER NOT NULL DEFAULT 1"),
)


def _apply_incremental_columns(engine: Engine) -> None:
    with engine.begin() as conn:
        existing_by_table: dict[str, set[str]] = {}
        for table, column, sql_type in _INCREMENTAL_COLUMNS:
            if table not in existing_by_table:
                rows = conn.exec_driver_sql(f'PRAGMA table_info("{table}")').fetchall()
                existing_by_table[table] = {str(r[1]) for r in rows}
            if column not in existing_by_table[table]:
                conn.exec_driver_sql(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {sql_type}')
                existing_by_table[table].add(column)


def ensure_m4_schema(engine: Engine) -> dict[str, str]:
    """Create and migrate Manual4 tables in manual4.sqlite without touching shine.db."""
    M4Base.metadata.create_all(engine)
    _apply_incremental_columns(engine)
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
