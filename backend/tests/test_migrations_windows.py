from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config

HEAD_REVISION = "f180d0010001"


def alembic_config(db_path: Path) -> Config:
    """Resolve alembic.ini from this test file so the suite passes from both
    the repo root and the backend directory."""
    config = Config(str(Path(__file__).resolve().parents[2] / "backend" / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")
    return config


def test_fresh_database_migrates_to_head_with_the_final_hardening_columns(tmp_path, monkeypatch):
    db_path = tmp_path / "fresh.db"
    monkeypatch.setenv("SHINE_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("SHINE_DATA_DIR", str(tmp_path))
    command.upgrade(alembic_config(db_path), "head")
    connection = sqlite3.connect(db_path)
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert "quality_gates" in tables
        assert {"rule_packs", "project_rule_locks", "agent_task_records", "agent_artifacts"}.issubset(tables)
        job_columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
        assert {"request_revision", "pending_repair_context", "quality_verification"}.issubset(job_columns)
        gate_columns = {row[1] for row in connection.execute("PRAGMA table_info(quality_gates)")}
        assert {
            "gate_name",
            "status",
            "required",
            "is_mock",
            "model",
            "confidence",
            "error_code",
            "coverage",
            "evidence",
        }.issubset(gate_columns)
    finally:
        connection.close()


def test_m2_database_upgrades_to_the_final_schema_without_losing_rows(tmp_path, monkeypatch):
    db_path = tmp_path / "m2.db"
    monkeypatch.setenv("SHINE_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("SHINE_DATA_DIR", str(tmp_path))
    config = alembic_config(db_path)
    command.upgrade(config, "9f2c4d7a10b3")
    now = datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ")
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "INSERT INTO projects (id, title, description, style_draft, frozen_style_version_id, "
            "created_at, language, allow_remote_review, auto_repair_enabled) "
            "VALUES ('p2', '既有项目', '', NULL, NULL, ?, 'Chinese', 0, 0)",
            (now,),
        )
        connection.execute(
            "INSERT INTO shots (id, project_id, name, revision, spec, created_at) "
            "VALUES ('sh2', 'p2', '既有镜头', 1, '{}', ?)",
            (now,),
        )
        connection.execute(
            "INSERT INTO jobs (id, project_id, shot_id, shot_revision, style_version_id, request, "
            "idempotency_key, status, provider, mock_scenario, paused, stale, semantic_retries, "
            "max_semantic_retries, created_at, updated_at, state_version) "
            "VALUES ('j2', 'p2', 'sh2', 1, 's2', '{}', 'idem-m2', 'PASSED', 'mock', 'success', 0, 0, 0, 2, "
            "?, ?, 3)",
            (now, now),
        )
        connection.commit()
    finally:
        connection.close()

    command.upgrade(config, "head")

    connection = sqlite3.connect(db_path)
    try:
        assert connection.execute("SELECT title FROM projects WHERE id = 'p2'").fetchone()[0] == "既有项目"
        assert connection.execute("SELECT status FROM jobs WHERE id = 'j2'").fetchone()[0] == "PASSED"
        assert connection.execute("SELECT state_version FROM jobs WHERE id = 'j2'").fetchone()[0] == 3
        assert connection.execute("SELECT request_revision FROM jobs WHERE id = 'j2'").fetchone()[0] == 1
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == HEAD_REVISION
        job_columns = {row[1] for row in connection.execute("PRAGMA table_info(job_attempts)")}
        assert "prompt_graph_sha256" in job_columns
        assert "request_snapshot" in job_columns
    finally:
        connection.close()


def test_migration_can_be_downgraded_and_upgraded_again(tmp_path, monkeypatch):
    db_path = tmp_path / "roundtrip.db"
    monkeypatch.setenv("SHINE_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("SHINE_DATA_DIR", str(tmp_path))
    config = alembic_config(db_path)
    command.upgrade(config, "head")
    command.downgrade(config, "9f2c4d7a10b3")
    connection = sqlite3.connect(db_path)
    try:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "9f2c4d7a10b3"
    finally:
        connection.close()
    command.upgrade(config, "head")
    connection = sqlite3.connect(db_path)
    try:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == HEAD_REVISION
    finally:
        connection.close()
