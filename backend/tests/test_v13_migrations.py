import sqlite3

from sqlalchemy import inspect

from alembic import command
from shine.db import Base, make_engine, make_session_factory
from shine.models import Asset, AuditEvent, Job, JobAttempt, Project, QCReport, Shot, StyleVersion, utcnow
from tests.test_alembic import alembic_config


def test_v121_upgrade_preserves_every_existing_table_and_matches_metadata(tmp_path, monkeypatch):
    path = tmp_path / "副本.db"
    url = f"sqlite:///{path.as_posix()}"
    monkeypatch.setenv("SHINE_DATABASE_URL", url)
    monkeypatch.setenv("SHINE_DATA_DIR", str(tmp_path))
    config = alembic_config(path)
    command.upgrade(config, "f121a0010001")
    engine = make_engine(url)
    factory = make_session_factory(engine)
    with factory.begin() as db:
        project = Project(title="Legacy migration fixture")
        db.add(project)
        db.flush()
        db.add(
            Asset(
                project_id=project.id,
                filename="legacy.png",
                kind="image",
                content_type="image/png",
                sha256="a" * 64,
                storage_path="assets/aa/fixture",
                byte_size=10,
            )
        )
    with factory.begin() as db:
        style = StyleVersion(
            id="legacy-style",
            project_id=project.id,
            version=1,
            snapshot={},
            sha256="b" * 64,
            approved_by="legacy-migration-fixture",
        )
        db.add(style)
        db.add(Shot(id="legacy-shot", project_id=project.id, name="legacy fixture", spec={}))
        db.flush()
        db.execute(
            Job.__table__.insert().values(
                id="legacy-job",
                project_id=project.id,
                shot_id="legacy-shot",
                shot_revision=1,
                style_version_id=style.id,
                request={},
                idempotency_key="legacy-key",
                status="QUEUED",
            )
        )
        db.add(
            JobAttempt(
                id="legacy-attempt",
                job_id="legacy-job",
                attempt_no=1,
                request_fingerprint="c" * 64,
                status="SUBMIT_INTENT",
                lease_owner="legacy-worker",
                lease_expires_at=utcnow(),
            )
        )
        db.flush()
        db.add(
            QCReport(
                job_id="legacy-job",
                attempt_id="legacy-attempt",
                result="needs_review",
                is_mock=True,
                report={"fixture": "migration preservation only"},
            )
        )
        db.add(
            AuditEvent(
                project_id=project.id,
                actor="legacy-fixture",
                event_type="MIGRATION_FIXTURE",
                entity_type="job",
                entity_id="legacy-job",
                payload={},
            )
        )
    engine.dispose()
    with sqlite3.connect(path) as db:
        legacy = [
            r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'") if r[0] != "alembic_version"
        ]
        columns = {
            name: ",".join('"' + r[1] + '"' for r in db.execute(f'PRAGMA table_info("{name}")')) for name in legacy
        }
        before = {name: db.execute(f'SELECT {columns[name]} FROM "{name}"').fetchall() for name in legacy}
    command.upgrade(config, "head")
    with sqlite3.connect(path) as db:
        after = {name: db.execute(f'SELECT {columns[name]} FROM "{name}"').fetchall() for name in legacy}
        assert before == after
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "f130a0030001"
    engine = make_engine(url)
    inspector = inspect(engine)
    assert set(Base.metadata.tables) <= set(inspector.get_table_names())
    for table in Base.metadata.sorted_tables:
        assert set(table.columns.keys()) == {c["name"] for c in inspector.get_columns(table.name)}
    engine.dispose()
    command.downgrade(config, "f121a0010001")
    with sqlite3.connect(path) as db:
        assert before == {name: db.execute(f'SELECT {columns[name]} FROM "{name}"').fetchall() for name in legacy}
