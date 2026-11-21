from __future__ import annotations

import os
from collections.abc import Generator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def make_engine(database_url: str) -> Engine:
    connect_args = {"check_same_thread": False, "timeout": 10} if database_url.startswith("sqlite") else {}
    pool_kwargs: dict = {}
    is_sqlite_file = database_url.startswith("sqlite") and ":memory:" not in database_url \
        and database_url.rstrip("/") != "sqlite:"
    if is_sqlite_file:
        # The workbench polls several read projections (status, journey, lists)
        # concurrently. A slow projection holds its connection until it finishes
        # even after the browser aborts on its own timeout, so the default pool
        # (5 + 10) exhausts and writes start failing with a pool timeout. WAL
        # supports many concurrent readers; give the pool room for the polls.
        # In-memory SQLite uses SingletonThreadPool, which rejects these kwargs.
        pool_kwargs = {"pool_size": 20, "max_overflow": 40, "pool_timeout": 60, "pool_pre_ping": True}
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


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def session_scope(factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    session = factory()
    try:
        yield session
    finally:
        session.close()
