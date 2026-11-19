"""Operations SQL: audit trail, transactional outbox, durable jobs.

The outbox and job *leasing* logic lives in ``SECURITY DEFINER`` SQL functions
(migration 0002) so workers can find work without bypassing row-level security for
payloads.  What is here are the inserts and the ordinary reads/updates.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from pfa.repo.query import Row, all_rows, one, returning, run, val, where
from pfa.repo.query import jsonb as to_jsonb

AUDIT_COLUMNS = """
    id, workspace_id, actor_user_id, actor_kind, action, entity_type, entity_id, before, after,
    ip_address, user_agent, correlation_id, created_at
"""

JOB_COLUMNS = """
    id, workspace_id, kind, payload, status, priority, attempts, max_attempts, available_at,
    leased_by, lease_expires_at, started_at, finished_at, last_error, result, logical_key
"""

OUTBOX_COLUMNS = "id, workspace_id, event_type, payload, dedupe_key, status, attempts, available_at"


# ---------------------------------------------------------------------------
# Audit trail (append-only)
# ---------------------------------------------------------------------------


async def insert_audit_event(
    session: AsyncSession,
    *,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | None,
    workspace_id: uuid.UUID | None,
    actor_user_id: uuid.UUID | None,
    actor_kind: str,
    before: dict | None,
    after: dict | None,
    ip_address: str | None,
    user_agent: str | None,
    correlation_id: str | None,
) -> int:
    """Append-only insert; no ``RETURNING``.

    ``audit_events`` (like ``outbox_events``) may be written from a context that has
    no tenant — an account-level event — and its SELECT policy is deliberately
    tenant-scoped, so reading the row back is not allowed there.  Nothing needs it.
    """
    return await run(
        session,
        """
        INSERT INTO audit_events (
            workspace_id, actor_user_id, actor_kind, action, entity_type, entity_id, before,
            after, ip_address, user_agent, correlation_id
        )
        VALUES (
            :workspace_id, :actor_user_id, :actor_kind, :action, :entity_type, :entity_id,
            CAST(:before AS jsonb), CAST(:after AS jsonb), :ip_address, :user_agent,
            :correlation_id
        )
        """,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        actor_kind=actor_kind,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before=to_jsonb(before),
        after=to_jsonb(after),
        ip_address=ip_address,
        user_agent=user_agent,
        correlation_id=correlation_id,
    )


async def list_audit_events(session: AsyncSession, *, workspace_id: uuid.UUID, limit: int = 100) -> list[Row]:
    bounded = min(max(int(limit), 1), 500)
    return await all_rows(
        session,
        f"""
        SELECT {AUDIT_COLUMNS} FROM audit_events
         WHERE workspace_id = :workspace_id
         ORDER BY created_at DESC
         LIMIT {bounded}
        """,
        workspace_id=workspace_id,
    )


# ---------------------------------------------------------------------------
# Transactional outbox
# ---------------------------------------------------------------------------


async def insert_outbox_event(
    session: AsyncSession,
    *,
    event_type: str,
    payload: dict,
    workspace_id: uuid.UUID | None,
    dedupe_key: str | None,
) -> int:
    """Insert one outbox event; a repeated ``dedupe_key`` is skipped.

    ``ON CONFLICT DO NOTHING`` is deliberately **not** used here: PostgreSQL has to
    read the conflicting row to decide, and the SELECT policy on this table is
    tenant-scoped, so an account-level event (``workspace_id IS NULL``) would be
    rejected.  The unique index still guarantees deduplication: a duplicate raises
    a unique violation, which is confined to a savepoint and then ignored.
    """
    try:
        async with session.begin_nested():
            await run(
                session,
                """
                INSERT INTO outbox_events (
                    workspace_id, event_type, payload, dedupe_key, status, attempts
                )
                VALUES (:workspace_id, :event_type, CAST(:payload AS jsonb), :dedupe_key,
                        'pending', 0)
                """,
                workspace_id=workspace_id,
                event_type=event_type,
                payload=to_jsonb(payload),
                dedupe_key=dedupe_key,
            )
            return 1
    except IntegrityError:
        # the logical event is already queued
        return 0


# ---------------------------------------------------------------------------
# Durable jobs
# ---------------------------------------------------------------------------


async def find_job_by_logical_key(
    session: AsyncSession, *, workspace_id: uuid.UUID, logical_key: str
) -> Row | None:
    return await one(
        session,
        f"""
        SELECT {JOB_COLUMNS} FROM jobs
         WHERE workspace_id = :workspace_id AND logical_key = :logical_key
        """,
        workspace_id=workspace_id,
        logical_key=logical_key,
    )


async def get_job(session: AsyncSession, job_id: uuid.UUID) -> Row | None:
    return await one(session, f"SELECT {JOB_COLUMNS} FROM jobs WHERE id = :id", id=job_id)


async def insert_job(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    kind: str,
    payload: dict,
    logical_key: str | None,
    priority: int,
    available_at: datetime,
    created_by_user_id: uuid.UUID | None,
    max_attempts: int,
) -> Row:
    return await returning(
        session,
        f"""
        INSERT INTO jobs (
            workspace_id, kind, payload, status, priority, attempts, max_attempts, available_at,
            logical_key, created_by_user_id
        )
        VALUES (
            :workspace_id, :kind, CAST(:payload AS jsonb), 'pending', :priority, 0, :max_attempts,
            :available_at, :logical_key, :created_by_user_id
        )
        ON CONFLICT (workspace_id, logical_key) DO NOTHING
        RETURNING {JOB_COLUMNS}
        """,
        workspace_id=workspace_id,
        kind=kind,
        payload=to_jsonb(payload),
        priority=priority,
        max_attempts=max_attempts,
        available_at=available_at,
        logical_key=logical_key,
        created_by_user_id=created_by_user_id,
    )


async def mark_job_succeeded(
    session: AsyncSession, *, job_id: uuid.UUID, result: dict | None, finished_at: datetime
) -> Row:
    return await returning(
        session,
        f"""
        UPDATE jobs
           SET status = 'succeeded', result = CAST(:result AS jsonb), finished_at = :finished_at,
               last_error = NULL, leased_by = NULL, lease_expires_at = NULL, updated_at = now()
         WHERE id = :id
        RETURNING {JOB_COLUMNS}
        """,
        id=job_id,
        result=to_jsonb(result or {}),
        finished_at=finished_at,
    )


async def jobs_in_workspace(session: AsyncSession, *, workspace_id: uuid.UUID, limit: int = 50) -> list[Row]:
    bounded = min(max(int(limit), 1), 200)
    return await all_rows(
        session,
        f"""
        SELECT {JOB_COLUMNS} FROM jobs
         WHERE workspace_id = :workspace_id
         ORDER BY created_at DESC
         LIMIT {bounded}
        """,
        workspace_id=workspace_id,
    )


async def pending_job_count(session: AsyncSession) -> int:
    return int(
        await val(
            session,
            "SELECT count(*) FROM jobs WHERE status IN ('pending', 'leased')",
            default=0,
        )
    )


async def job_workspace(session: AsyncSession, job_id: uuid.UUID) -> uuid.UUID | None:
    """Resolve a job's workspace without a tenant context (identifier only)."""
    value = await val(session, "SELECT app.job_workspace(:id)", id=job_id)
    return uuid.UUID(str(value)) if value else None


# ---------------------------------------------------------------------------
# Idempotency keys
# ---------------------------------------------------------------------------


async def claim_idempotency_key(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID | None,
    user_id: uuid.UUID,
    action: str,
    key: str,
    request_sha256: str,
    expires_at: datetime,
) -> uuid.UUID | None:
    """Insert-or-nothing; ``None`` means somebody else already claimed the key."""
    return await val(
        session,
        """
        INSERT INTO idempotency_keys
            (workspace_id, user_id, action, key, request_sha256, expires_at)
        VALUES (:workspace_id, :user_id, :action, :key, :sha, :expires_at)
        ON CONFLICT (workspace_id, user_id, action, key) DO NOTHING
        RETURNING id
        """,
        workspace_id=workspace_id,
        user_id=user_id,
        action=action,
        key=key,
        sha=request_sha256,
        expires_at=expires_at,
    )


async def get_idempotency_record(
    session: AsyncSession, *, workspace_id: uuid.UUID | None, user_id: uuid.UUID, action: str, key: str
) -> Row | None:
    return await one(
        session,
        """
        SELECT id, request_sha256, response, status_code
          FROM idempotency_keys
         WHERE workspace_id IS NOT DISTINCT FROM :workspace_id
           AND user_id = :user_id AND action = :action AND key = :key
        """,
        workspace_id=workspace_id,
        user_id=user_id,
        action=action,
        key=key,
    )


async def store_idempotent_result(
    session: AsyncSession,
    *,
    key_id: uuid.UUID,
    response: dict,
    status_code: int,
    resource_id: uuid.UUID | None,
    resource_type: str | None,
) -> None:
    await run(
        session,
        """
        UPDATE idempotency_keys
           SET response = CAST(:response AS jsonb), status_code = :status_code,
               resource_id = :resource_id, resource_type = :resource_type
         WHERE id = :id
        """,
        id=key_id,
        response=to_jsonb(response),
        status_code=status_code,
        resource_id=resource_id,
        resource_type=resource_type,
    )


__all__ = ["Row", "all_rows", "where"]
