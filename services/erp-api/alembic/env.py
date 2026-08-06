"""Alembic environment. Reads DATABASE_URL from the environment.

The async engine used by the app is deliberately NOT reused here: Alembic runs
synchronously, and mixing an asyncpg engine into a sync migration context is a
common source of "attached to a different loop" failures during deploys. We
translate the URL to psycopg2 for migrations only.
"""
from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.db.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

url = os.getenv("DATABASE_URL", "postgresql+psycopg2://erp:erp@localhost:5432/erp")
url = url.replace("+asyncpg", "+psycopg2")
config.set_main_option("sqlalchemy.url", url)


def run_migrations_offline() -> None:
    context.configure(
        url=url, target_metadata=target_metadata, literal_binds=True,
        dialect_opts={"paramstyle": "named"}, compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.", poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata,
            compare_type=True, compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
