from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config


def alembic_config(db_path: Path) -> Config:
    config = Config(str(Path("backend/alembic.ini").resolve()))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")
    return config


def test_fresh_database_upgrades_to_the_m2_schema(tmp_path, monkeypatch):
    db_path = tmp_path / "fresh.db"
    monkeypatch.setenv("SHINE_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("SHINE_DATA_DIR", str(tmp_path))
    command.upgrade(alembic_config(db_path), "head")
    connection = sqlite3.connect(db_path)
    try:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert {
            "projects",
            "assets",
            "shots",
            "style_versions",
            "jobs",
            "job_attempts",
            "job_dependencies",
            "qc_reports",
            "audit_events",
            "project_documents",
            "shot_asset_bindings",
            "media_artifacts",
            "repair_records",
            "provider_profiles",
            "agent_routes",
            "workflow_profiles",
            "external_job_reconciliations",
            "app_settings",
            "worker_instances",
            "quality_gates",
        }.issubset(tables)
        job_columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
        assert {
            "state_version",
            "generation_mode",
            "workflow_profile_id",
            "compiled_prompt_sha256",
            "artifact_id",
            "last_qc_result",
            "request_revision",
            "pending_repair_context",
            "quality_verification",
        }.issubset(job_columns)
        attempt_columns = {row[1] for row in connection.execute("PRAGMA table_info(job_attempts)")}
        assert {
            "attempt_kind",
            "parent_attempt_id",
            "request_snapshot",
            "prompt_sha256",
            "parameter_sha256",
            "manifest_sha256",
            "prompt_graph_sha256",
            "repair_reason",
        }.issubset(attempt_columns)
        profile_columns = {row[1] for row in connection.execute("PRAGMA table_info(workflow_profiles)")}
        assert "media_transfers" in profile_columns
        project_columns = {row[1] for row in connection.execute("PRAGMA table_info(projects)")}
        assert "quality_policy" in project_columns
        asset_columns = {row[1] for row in connection.execute("PRAGMA table_info(assets)")}
        assert {
            "role",
            "entity_id",
            "tags",
            "duration_s",
            "width",
            "height",
            "approved_at",
            "locked_at",
        }.issubset(asset_columns)
        audit_columns = {row[1] for row in connection.execute("PRAGMA table_info(audit_events)")}
        assert "project_id" in audit_columns
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "e8f1a2b3c4d5"
    finally:
        connection.close()


def test_existing_m1_database_upgrades_without_losing_rows_and_backfills_audit_scope(tmp_path, monkeypatch):
    db_path = tmp_path / "m1.db"
    monkeypatch.setenv("SHINE_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("SHINE_DATA_DIR", str(tmp_path))
    config = alembic_config(db_path)
    command.upgrade(config, "70afa9c775ab")
    now = datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ")
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "INSERT INTO projects (id, title, description, style_draft, frozen_style_version_id, created_at) "
            "VALUES ('p1', '旧项目', '', NULL, 's1', ?)",
            (now,),
        )
        connection.execute(
            "INSERT INTO style_versions (id, project_id, version, snapshot, sha256, approved_by, frozen_at) "
            "VALUES ('s1', 'p1', 1, '{}', ?, 'user', ?)",
            ("a" * 64, now),
        )
        connection.execute(
            "INSERT INTO assets (id, project_id, filename, kind, content_type, sha256, storage_path, byte_size, "
            "approved, locked, created_at) VALUES ('a1', 'p1', '旧素材.png', 'image', 'image/png', ?, "
            "'assets/aa/x', 10, 1, 1, ?)",
            ("b" * 64, now),
        )
        connection.execute(
            "INSERT INTO shots (id, project_id, name, revision, spec, created_at) "
            "VALUES ('sh1', 'p1', '旧镜头', 1, '{}', ?)",
            (now,),
        )
        connection.execute(
            "INSERT INTO jobs (id, project_id, shot_id, shot_revision, style_version_id, request, idempotency_key, "
            "status, provider, mock_scenario, paused, stale, semantic_retries, "
            "max_semantic_retries, created_at, updated_at) "
            "VALUES ('j1', 'p1', 'sh1', 1, 's1', '{}', 'idem-old', 'PASSED', 'mock', 'success', 0, 0, 0, 2, ?, ?)",
            (now, now),
        )
        for event_id, entity_type, entity_id in (
            ("e1", "project", "p1"),
            ("e2", "asset", "a1"),
            ("e3", "shot", "sh1"),
            ("e4", "job", "j1"),
            ("e5", "style_version", "s1"),
        ):
            connection.execute(
                "INSERT INTO audit_events (id, actor, event_type, entity_type, entity_id, payload, created_at) "
                "VALUES (?, 'user', 'LEGACY_EVENT', ?, ?, '{}', ?)",
                (event_id, entity_type, entity_id, now),
            )
        connection.commit()
    finally:
        connection.close()

    command.upgrade(config, "head")

    connection = sqlite3.connect(db_path)
    try:
        assert connection.execute("SELECT title FROM projects WHERE id = 'p1'").fetchone()[0] == "旧项目"
        assert connection.execute("SELECT status FROM jobs WHERE id = 'j1'").fetchone()[0] == "PASSED"
        scopes = dict(
            connection.execute("SELECT id, project_id FROM audit_events WHERE event_type = 'LEGACY_EVENT'").fetchall()
        )
        assert scopes == {"e1": "p1", "e2": "p1", "e3": "p1", "e4": "p1", "e5": "p1"}
        assert connection.execute("SELECT state_version FROM jobs WHERE id = 'j1'").fetchone()[0] == 0
        assert connection.execute("SELECT language FROM projects WHERE id = 'p1'").fetchone()[0] == "Chinese"
    finally:
        connection.close()
