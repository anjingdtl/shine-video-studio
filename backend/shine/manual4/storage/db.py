"""Mirrored SQLite engine and session factory for Manual4 (isolated from shine.db)."""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class M4Base(DeclarativeBase):
    """Isolated declarative metadata for Manual4 business tables."""


def manual4_data_root(data_dir: Path) -> Path:
    return Path(data_dir).resolve() / "manual4"


def manual4_database_url(data_dir: Path) -> str:
    override = os.getenv("SHINE_MANUAL4_DATABASE_URL", "").strip()
    if override:
        return override
    db_path = Path(data_dir).resolve() / "manual4.sqlite"
    return f"sqlite:///{db_path.as_posix()}"


def make_m4_engine(database_url: str) -> Engine:
    connect_args = {"check_same_thread": False, "timeout": 10} if database_url.startswith("sqlite") else {}
    pool_kwargs: dict = {}
    is_sqlite_file = (
        database_url.startswith("sqlite")
        and ":memory:" not in database_url
        and database_url.rstrip("/") != "sqlite:"
    )
    if is_sqlite_file:
        pool_kwargs = {"pool_size": 16, "max_overflow": 32, "pool_timeout": 60, "pool_pre_ping": True}
    engine = create_engine(database_url, connect_args=connect_args, future=True, **pool_kwargs)
    if database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def set_sqlite_pragmas(connection, _record) -> None:
            cursor = connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=10000")
            if os.getenv("SHINE_TEST_FAST_SQLITE") == "1":
                cursor.execute("PRAGMA synchronous=OFF")
                cursor.execute("PRAGMA temp_store=MEMORY")
            cursor.close()

    return engine


def make_m4_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def m4_session_scope(factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    session = factory()
    try:
        yield session
    finally:
        session.close()
