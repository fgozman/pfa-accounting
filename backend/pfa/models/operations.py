"""Operations: outbox, durable jobs, idempotency, audit, notifications."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from pfa import clock
from pfa.models.base import (
    Base,
    JobStatus,
    NotificationStage,
    Timestamps,
    UUIDPrimaryKey,
    WorkspaceOwned,
    enum_column,
)


class OutboxEvent(UUIDPrimaryKey, Base):
    """Transactional outbox: committing an accounting change and its required
    background event is one atomic operation."""

    __tablename__ = "outbox_events"

    workspace_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    dedupe_key: Mapped[str | None] = mapped_column(String(200))
    #: pending | processing | processed | failed
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=clock.now, server_default=func.now()
    )
    locked_by: Mapped[str | None] = mapped_column(String(64))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(1000))
    #: client-side default: account-level events are written from contexts that
    #: cannot (and must not) read the row back
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=clock.now, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_outbox_dedupe_key"),
        Index("ix_outbox_pending", "status", "available_at"),
    )


class Job(UUIDPrimaryKey, Timestamps, WorkspaceOwned, Base):
    """Durable job with leases.  Workers are restartable and idempotent."""

    __tablename__ = "jobs"

    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[JobStatus] = mapped_column(
        enum_column(JobStatus), nullable=False, default=JobStatus.PENDING
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=8)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    leased_by: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(1000))
    result: Mapped[dict | None] = mapped_column(JSONB)
    #: logical identity so a crash/restart cannot duplicate work
    logical_key: Mapped[str | None] = mapped_column(String(160))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    __table_args__ = (
        UniqueConstraint("workspace_id", "logical_key", name="uq_job_logical_key"),
        Index("ix_jobs_lease", "status", "available_at", "priority"),
        Index("ix_jobs_workspace_status", "workspace_id", "status"),
    )


class IdempotencyKey(UUIDPrimaryKey, Base):
    """Bound to user/workspace/action/request hash.  A retry returns the original
    result; different content under the same key is rejected."""

    __tablename__ = "idempotency_keys"

    workspace_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    key: Mapped[str] = mapped_column(String(120), nullable=False)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(48))
    resource_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    response: Mapped[dict | None] = mapped_column(JSONB)
    status_code: Mapped[int | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "user_id", "action", "key", name="uq_idempotency_scope"
        ),
    )


class AuditEvent(UUIDPrimaryKey, Base):
    """Append-only audit trail.

    ``created_at`` has a client-side default so an insert never needs
    ``INSERT ... RETURNING``: the SELECT policy on this table is deliberately
    narrower than the INSERT policy (a workspace-NULL account-level audit row must
    still be writable from a context that cannot read it back).
    """

    __tablename__ = "audit_events"

    workspace_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    #: user | worker | system
    actor_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(48), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=clock.now,
        server_default=func.now(),
    )

    __table_args__ = (
        Index("ix_audit_events_workspace_created", "workspace_id", "created_at"),
        Index("ix_audit_events_entity", "entity_type", "entity_id"),
    )


class Notification(UUIDPrimaryKey, Base):
    """A notification is a *view* of a persisted obligation, never the source of
    compliance truth."""

    __tablename__ = "notifications"

    workspace_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    obligation_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    stage: Mapped[NotificationStage] = mapped_column(
        enum_column(NotificationStage), nullable=False
    )
    reminder_offset_days: Mapped[int | None] = mapped_column()
    deadline_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    title_ro: Mapped[str] = mapped_column(String(300), nullable=False)
    body_ro: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    due_on: Mapped[date | None] = mapped_column(Date)
    payment_due_on: Mapped[date | None] = mapped_column(Date)
    #: where the single next-step button should send the user
    next_step_kind: Mapped[str | None] = mapped_column(String(32))
    next_step_ref: Mapped[str | None] = mapped_column(String(64))
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    #: cleared when the obligation is resolved
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_notification_dedupe"),
        Index("ix_notifications_workspace_created", "workspace_id", "created_at"),
    )


class NotificationDelivery(UUIDPrimaryKey, Base):
    __tablename__ = "notification_deliveries"

    notification_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("notifications.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    recipient_user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="in_app")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(String(500))
    dedupe_key: Mapped[str] = mapped_column(String(240), nullable=False)

    __table_args__ = (UniqueConstraint("dedupe_key", name="uq_notification_delivery_dedupe"),)


class NotificationUserState(UUIDPrimaryKey, Base):
    """Per-user read/dismiss/snooze state.  Reading a notification never
    completes a filing and never hides it from colleagues."""

    __tablename__ = "notification_user_states"

    notification_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("notifications.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    snoozed_until: Mapped[date | None] = mapped_column(Date)
    email_reminders: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint("notification_id", "user_id", name="uq_notification_user_state"),
    )
