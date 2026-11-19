"""Alembic environment of the authorization server's own database.

Independent of the application's history (``migrations/``): different database
(``PFA_OAUTH_MIGRATION_DATABASE_URL``), different metadata
(:class:`pfa.oauth.models.OAuthBase`).  Run with ``alembic -c alembic_oauth.ini``.
Migrations run as the **owner** role; the runtime role only receives DML grants.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from pfa.config import get_settings
from pfa.oauth.models import OAuthBase

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = OAuthBase.metadata


def database_url() -> str:
    return (
        os.environ.get("PFA_OAUTH_MIGRATION_DATABASE_URL")
        or get_settings().oauth_migration_database_url
    )


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    configuration = dict(config.get_section(config.config_ini_section) or {})
    configuration["sqlalchemy.url"] = database_url()
    connectable = async_engine_from_config(
        configuration, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
