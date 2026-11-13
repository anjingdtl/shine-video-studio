from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alembic import command
from alembic.config import Config

pytestmark = pytest.mark.integration

HEAD_REVISION = "f180c0030001"

REPO = Path(__file__).resolve().parents[2]


def alembic_config(db_path: Path) -> Config:
    """Resolve alembic.ini from this test file, never from the current working
    directory: the suite must behave identically from the repo root and from
    ``backend/``."""
    config = Config(str(REPO / "backend" / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")
    return config


def test_fresh_database_upgrades_to_the_latest_schema(tmp_path, monkeypatch):
    db_path = tmp_path / "fresh.db"
    monkeypatch.setenv("SHINE_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("SHINE_DATA_DIR", str(tmp_path))
    command.upgrade(alembic_config(db_path), "head")
    connection = sqlite3.connect(db_path)
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
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
        assert {"quality_policy", "director_style_draft"}.issubset(project_columns)
        style_columns = {row[1] for row in connection.execute("PRAGMA table_info(style_versions)")}
        assert "director_style_binding" in style_columns
        assert "director_style_profile_revisions" in tables
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
        provider_columns = {row[1] for row in connection.execute("PRAGMA table_info(provider_profiles)")}
        assert "generation_options" in provider_columns
        series_columns = {row[1] for row in connection.execute("PRAGMA table_info(production_series)")}
        assert "output_spec" in series_columns
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == HEAD_REVISION
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


def test_delivery_order_indexes_exist_after_upgrade(tmp_path, monkeypatch):
    """f150a0030001 closes the model/migration drift: the two delivery-order
    foreign-key indexes exist on a freshly upgraded database."""
    db_path = tmp_path / "delivery-indexes.db"
    monkeypatch.setenv("SHINE_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("SHINE_DATA_DIR", str(tmp_path))
    command.upgrade(alembic_config(db_path), "head")
    connection = sqlite3.connect(db_path)
    try:
        names = {row[1] for row in connection.execute("PRAGMA index_list(delivery_orders)")}
        assert {"ix_delivery_orders_project_id", "ix_delivery_orders_series_id"} <= names
    finally:
        connection.close()


def test_upgraded_database_has_no_autogenerate_drift(tmp_path, monkeypatch):
    """The long-term regression for model/migration drift: after upgrading a
    fresh database to head, ``alembic check`` must find zero differences.
    Runs with the working directory of the test runner, not the repo root."""
    from alembic.util.exc import AutogenerateDiffsDetected

    db_path = tmp_path / "check.db"
    monkeypatch.setenv("SHINE_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("SHINE_DATA_DIR", str(tmp_path))
    config = alembic_config(db_path)
    command.upgrade(config, "head")
    try:
        command.check(config)
    except AutogenerateDiffsDetected as exc:
        pytest.fail(f"alembic check reported drift after upgrade: {exc}")


def test_upgrade_restores_missing_series_item_fk_and_keeps_existing_job_links(api, monkeypatch):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from shine.models import ProductionSeries, ProductionSeriesItem

    from .conftest import create_job, create_project, create_shot, freeze_style

    client, factory, settings = api
    project = create_project(client)
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"])
    job = create_job(client, project, shot["id"])
    with factory.begin() as db:
        series = ProductionSeries(project_id=project, shot_count=2)
        db.add(series)
        db.flush()
        for position in range(2):
            db.add(ProductionSeriesItem(
                series_id=series.id, shot_id=shot["id"], position=position, job_id=job["id"],
            ))

    engine = factory.kw["bind"]
    with engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        with operations.batch_alter_table(
            "production_series_items", naming_convention={"fk": "fk_%(table_name)s_%(column_0_name)s"},
        ) as batch:
            batch.drop_constraint("fk_production_series_items_job_id", type_="foreignkey")
        connection.exec_driver_sql("UPDATE production_series_items SET job_id = 'missing' WHERE position = 1")

    monkeypatch.setenv("SHINE_DATABASE_URL", settings.database_url)
    monkeypatch.setenv("SHINE_DATA_DIR", str(settings.data_dir))
    config = alembic_config(settings.data_dir / "shine.sqlite3")
    command.stamp(config, "f180c0010001")
    command.upgrade(config, "head")
    command.check(config)
    with engine.connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT position, job_id FROM production_series_items ORDER BY position"
        ).all()
        assert rows == [(0, job["id"]), (1, None)]
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []


def test_delivery_order_indexes_downgrade_and_reupgrade(tmp_path, monkeypatch):
    """The index migration downgrades and re-upgrades cleanly, and re-running
    the upgrade on a database that already has the indexes (create_all
    semantics) stays idempotent."""
    db_path = tmp_path / "cycle.db"
    monkeypatch.setenv("SHINE_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("SHINE_DATA_DIR", str(tmp_path))
    config = alembic_config(db_path)
    command.upgrade(config, "head")
    command.downgrade(config, "f150a0020001")
    connection = sqlite3.connect(db_path)
    try:
        names = {row[1] for row in connection.execute("PRAGMA index_list(delivery_orders)")}
        assert "ix_delivery_orders_project_id" not in names
        assert "ix_delivery_orders_series_id" not in names
    finally:
        connection.close()
    command.upgrade(config, "head")
    connection = sqlite3.connect(db_path)
    try:
        names = {row[1] for row in connection.execute("PRAGMA index_list(delivery_orders)")}
        assert {"ix_delivery_orders_project_id", "ix_delivery_orders_series_id"} <= names
    finally:
        connection.close()
