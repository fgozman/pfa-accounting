"""Append-only audit trail, transactional outbox and durable jobs.

Every audit row is also emitted as a structured ``audit`` log event, **after its
transaction commits** (and dropped on rollback), so a log pipeline sees exactly
the audit trail the database holds, no more: a failed operation never appears as
done.  Rows carry the request's correlation id, client IP and user agent by
default, which joins a database audit row to the request's log lines.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from pfa import clock
from pfa.observability import context
from pfa.observability.logs import log_event
from pfa.repo import operations as repo

audit_logger = logging.getLogger("pfa.audit")

#: audit events waiting for their transaction's outcome (in ``Session.info``)
_PENDING_AUDIT_LOGS = "pfa_pending_audit_logs"


@event.listens_for(Session, "after_commit")
def _emit_committed_audit_logs(session: Session) -> None:
    for attributes in session.info.pop(_PENDING_AUDIT_LOGS, []):
        log_event(
            audit_logger,
            "audit",
            message=f"{attributes['pfa.audit.action']} {attributes['pfa.audit.entity_type']}",
            attributes=attributes,
        )


@event.listens_for(Session, "after_rollback")
def _discard_rolled_back_audit_logs(session: Session) -> None:
    session.info.pop(_PENDING_AUDIT_LOGS, None)


async def record_audit(
    session: AsyncSession,
    *,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | str | None = None,
    workspace_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    actor_kind: str = "user",
    before: dict | None = None,
    after: dict | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    correlation_id: str | None = None,
):
    """Append one audit record (the table is append-only by trigger).

    Request context fills ``ip_address``, ``user_agent`` and ``correlation_id``
    when the caller does not pass them.
    """
    ip_address = ip_address or context.client_ip()
    user_agent = user_agent or context.user_agent()
    correlation_id = correlation_id or context.correlation_id()
    entity = uuid.UUID(str(entity_id)) if entity_id else None
    row = await repo.insert_audit_event(
        session,
        action=action,
        entity_type=entity_type,
        entity_id=entity,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        actor_kind=actor_kind,
        before=before,
        after=after,
        ip_address=ip_address,
        user_agent=user_agent,
        correlation_id=correlation_id,
    )
    # before/after stay in the database: they can hold personal or fiscal data
    session.info.setdefault(_PENDING_AUDIT_LOGS, []).append(
        {
            "pfa.audit.action": action,
            "pfa.audit.entity_type": entity_type,
            "pfa.audit.entity_id": str(entity) if entity else None,
            "pfa.audit.actor_kind": actor_kind,
            "pfa.audit.changed_fields": (
                sorted((after or {}).keys()) if isinstance(after, dict) else None
            ),
            "pfa.workspace.id": str(workspace_id) if workspace_id else None,
            # the acting user, the request's client and correlation, as recorded
            "user.id": str(actor_user_id) if actor_user_id else None,
            "client.address": ip_address,
            "pfa.correlation_id": correlation_id,
        }
    )
    return row


async def emit_event(
    session: AsyncSession,
    *,
    event_type: str,
    payload: dict[str, Any] | None = None,
    workspace_id: uuid.UUID | None = None,
    dedupe_key: str | None = None,
):
    """Write to the transactional outbox in the same transaction as the change."""
    return await repo.insert_outbox_event(
        session,
        event_type=event_type,
        payload=payload or {},
        workspace_id=workspace_id,
        dedupe_key=dedupe_key,
    )


async def enqueue_job(
    session: AsyncSession,
    *,
    kind: str,
    workspace_id: uuid.UUID,
    payload: dict[str, Any] | None = None,
    logical_key: str | None = None,
    priority: int = 100,
    delay_seconds: int = 0,
    created_by_user_id: uuid.UUID | None = None,
    max_attempts: int = 8,
):
    """Enqueue a durable job.

    ``logical_key`` gives the job a logical identity inside the workspace so a
    crash and retry cannot create a second logical job: an identical enqueue
    returns the existing job instead of inserting a duplicate.
    """
    if logical_key:
        existing = await repo.find_job_by_logical_key(
            session, workspace_id=workspace_id, logical_key=logical_key
        )
        if existing is not None:
            return existing
    return await repo.insert_job(
        session,
        workspace_id=workspace_id,
        kind=kind,
        payload=payload or {},
        logical_key=logical_key,
        priority=priority,
        available_at=clock.now() + timedelta(seconds=delay_seconds),
        created_by_user_id=created_by_user_id,
        max_attempts=max_attempts,
    )


# ---------------------------------------------------------------------------
# Worker-side leasing.  These SECURITY DEFINER functions return identifiers and
# tenant ids only; the worker then reads payloads inside a tenant transaction.
# ---------------------------------------------------------------------------


async def list_workspace_ids(session: AsyncSession) -> list[uuid.UUID]:
    result = await session.execute(text("SELECT app.list_workspace_ids()"))
    return [row[0] for row in result.all()]


async def lease_outbox(session: AsyncSession, *, worker_id: str, batch: int = 20, lease_seconds: int = 120):
    result = await session.execute(
        text("SELECT * FROM app.lease_outbox(:worker, :batch, :lease)"),
        {"worker": worker_id, "batch": batch, "lease": lease_seconds},
    )
    return [dict(row._mapping) for row in result.all()]


async def lease_jobs(session: AsyncSession, *, worker_id: str, batch: int = 10, lease_seconds: int = 300):
    result = await session.execute(
        text("SELECT * FROM app.lease_jobs(:worker, :batch, :lease)"),
        {"worker": worker_id, "batch": batch, "lease": lease_seconds},
    )
    return [dict(row._mapping) for row in result.all()]
