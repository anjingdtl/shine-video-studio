"""Global-name constraint upgrades preserve ids and refuse ambiguous old data."""

import sqlite3

import pytest

from alembic import command
from tests.test_alembic import alembic_config

HEAD_REVISION = "f180b0030001"


def previous_database(tmp_path, monkeypatch):
    path = tmp_path / "profile-upgrade.db"
    monkeypatch.setenv("SHINE_DATABASE_URL", f"sqlite:///{path.as_posix()}")
    monkeypatch.setenv("SHINE_DATA_DIR", str(tmp_path))
    config = alembic_config(path)
    command.upgrade(config, "f150a0010001")
    return path, config


def insert_profile(connection, identity, project=None):
    connection.execute(
        "INSERT INTO workflow_profiles "
        "(id,project_id,name,mode,graph,parameter_map,required_nodes,checkpoint_id,graph_sha256,created_at) "
        "VALUES (?,?, 'shared-name', 'T2VA', '{}', '{}', '[]', 'MiniMax-H3-Base', ?, '2026-11-01')",
        (identity, project, "a" * 64),
    )


def test_global_index_upgrade_preserves_private_scopes_and_reverses_without_row_changes(tmp_path, monkeypatch):
    path, config = previous_database(tmp_path, monkeypatch)
    with sqlite3.connect(path) as db:
        for project in ("A", "B"):
            db.execute(
                "INSERT INTO projects (id,title,description,created_at) VALUES (?,?, '', '2026-11-01')",
                (project, project),
            )
            insert_profile(db, f"private-{project}", project)
        insert_profile(db, "original-global")
        before = db.execute("SELECT * FROM workflow_profiles ORDER BY id").fetchall()
    command.upgrade(config, "head")
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT * FROM workflow_profiles ORDER BY id").fetchall() == before
        with pytest.raises(sqlite3.IntegrityError):
            insert_profile(db, "duplicate-global")
        db.rollback()
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == HEAD_REVISION
    command.downgrade(config, "f150a0010001")
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT * FROM workflow_profiles ORDER BY id").fetchall() == before


def test_global_index_upgrade_refuses_existing_duplicates_without_mutating_history(tmp_path, monkeypatch):
    path, config = previous_database(tmp_path, monkeypatch)
    with sqlite3.connect(path) as db:
        insert_profile(db, "existing-global-1")
        insert_profile(db, "existing-global-2")
        before = db.execute("SELECT * FROM workflow_profiles ORDER BY id").fetchall()
    with pytest.raises(RuntimeError, match="GLOBAL_WORKFLOW_NAME_CONFLICT"):
        command.upgrade(config, "head")
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT * FROM workflow_profiles ORDER BY id").fetchall() == before
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "f150a0010001"
        indexes = {row[1] for row in db.execute("PRAGMA index_list(workflow_profiles)")}
        assert "uq_workflow_profile_global_name" not in indexes
