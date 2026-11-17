from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from shine import models  # noqa: F401  Register model metadata.
from shine.config import Settings
from shine.db import Base

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

settings = Settings.from_env()
settings.data_dir.mkdir(parents=True, exist_ok=True)
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
target_metadata = Base.metadata

#: FTS5 is a virtual table plus its shadow tables; SQLAlchemy metadata cannot
#: represent it, so autogenerate must not propose dropping a working index.
FTS_TABLE = "copilot_memory_fts"


def include_object(object_, name, type_, reflected, compare_to):
    if type_ == "table" and (
        name == FTS_TABLE or str(name).startswith(f"{FTS_TABLE}_")
    ):
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        if connection.dialect.name == "sqlite":
            connection.exec_driver_sql("PRAGMA journal_mode=WAL")
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            # PRAGMA opens SQLAlchemy's implicit transaction; finish it before Alembic
            # starts its migration transaction so the revision row is committed.
            connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
