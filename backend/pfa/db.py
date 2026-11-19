"""Async database access, tenant context and RLS helpers.

Security model
--------------
* The **owner** role owns the schema and runs migrations.  It is never used by
  the API or workers at runtime.
* The **runtime** role (``PFA_APP_USER``) has DML grants only.  It is not a
  superuser, does not own tables and does not have ``BYPASSRLS``, so every
  workspace-scoped query is filtered by row-level security.
* Tenant context is set with ``set_config(..., is_local => true)`` inside a
  transaction, so it is cleared automatically at the transaction boundary and
  can never leak through a pooled connection.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext import asyncio as sa_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from pfa.config import Settings, get_settings
from pfa.observability.telemetry import instrument_libraries


class TenantContextError(RuntimeError):
    """Raised when workspace-scoped work is attempted without tenant context."""


def build_engine(
    url: str,
    *,
    pool_size: int = 10,
    max_overflow: int = 20,
    echo: bool = False,
) -> AsyncEngine:
    # tracing hooks engine creation: install it first, and look the factory up on
    # the module at call time so the instrumented version is the one used
    instrument_libraries()
    return sa_asyncio.create_async_engine(
        url,
        echo=echo,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,
        future=True,
    )


@dataclass(slots=True)
class Database:
    """Owns the runtime engine/session factory."""

    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> Database:
        """The application database."""
        settings = settings or get_settings()
        return cls.from_url(settings.database_url, settings)

    @classmethod
    def from_url(cls, url: str, settings: Settings | None = None) -> Database:
        """Any database of this deployment, with the configured pool settings."""
        settings = settings or get_settings()
        engine = build_engine(
            url,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            echo=settings.db_echo,
        )
        return cls(
            engine=engine,
            session_factory=async_sessionmaker(engine, expire_on_commit=False),
        )

    async def dispose(self) -> None:
        await self.engine.dispose()

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Session without tenant context (global tables such as ``users``)."""
        async with self.session_factory() as session:
            try:
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    @asynccontextmanager
    async def user_session(self, user_id: uuid.UUID | str) -> AsyncIterator[AsyncSession]:
        """Session scoped to an authenticated user but no workspace.

        Used for operations that must discover *which* workspaces a user may see
        (listing/creating a workspace, accepting an invitation).  RLS policies on
        ``workspaces``/``memberships`` key off ``app.user_id``.
        """
        async with self.session_factory() as session:
            try:
                await apply_tenant_context(session, None, actor="user", user_id=user_id)
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    @asynccontextmanager
    async def worker_session(self) -> AsyncIterator[AsyncSession]:
        """Session for a background worker without tenant context.

        Only the plumbing tables (``outbox_events``, ``jobs``) grant UPDATE to the
        ``worker`` actor; payload data is still read inside a tenant transaction.
        """
        async with self.session_factory() as session:
            try:
                await apply_tenant_context(session, None, actor="worker")
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    @asynccontextmanager
    async def tenant_session(
        self,
        workspace_id: uuid.UUID | str,
        *,
        actor: str | None = None,
        user_id: uuid.UUID | str | None = None,
    ) -> AsyncIterator[AsyncSession]:
        """Session with row-level-security tenant context.

        The context is transaction-local: it cannot survive a commit/rollback,
        so a connection returned to the pool never carries another tenant's id.
        """
        async with self.session_factory() as session:
            try:
                await apply_tenant_context(
                    session, workspace_id, actor=actor, user_id=user_id
                )
                verified = await session.scalar(
                    text("SELECT app.current_workspace()::text")
                )
                if verified != str(workspace_id):
                    raise TenantContextError(
                        "database refused to install the requested tenant context"
                    )
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise


async def apply_tenant_context(
    session: AsyncSession,
    workspace_id: uuid.UUID | str | None,
    *,
    actor: str | None = None,
    user_id: uuid.UUID | str | None = None,
) -> None:
    """Install transaction-local tenant context.

    ``actor`` distinguishes interactive requests (``user``) from background
    processing (``worker``) for audit purposes.
    """
    if not session.in_transaction():
        await session.begin()
    await session.execute(
        text(
            """
            SELECT set_config('app.workspace_id', :workspace_id, true),
                   set_config('app.actor', :actor, true),
                   set_config('app.user_id', :user_id, true)
            """
        ),
        {
            "workspace_id": str(workspace_id) if workspace_id else "",
            "actor": actor or "system",
            "user_id": str(user_id) if user_id else "",
        },
    )


async def require_tenant(session: AsyncSession) -> str:
    value = await session.scalar(
        text("SELECT nullif(current_setting('app.workspace_id', true), '')::uuid")
    )
    if not value:
        raise TenantContextError("no tenant context installed on this transaction")
    return str(value)


# ---------------------------------------------------------------------------
# Process-wide singletons (created lazily so tests can point at another DB)
# ---------------------------------------------------------------------------
_database: Database | None = None


def get_database() -> Database:
    global _database
    if _database is None:
        _database = Database.from_settings()
    return _database


def override_database(factory: Callable[[], Database] | None) -> None:
    """Test hook: replace or reset the process-wide database."""
    global _database
    _database = factory() if factory else None


async def dispose_database() -> None:
    global _database
    if _database is not None:
        await _database.dispose()
        _database = None


async def scalar_dict(session: AsyncSession, sql: str, **params: Any) -> dict[str, Any] | None:
    result = await session.execute(text(sql), params)
    row = result.mappings().first()
    return dict(row) if row else None
