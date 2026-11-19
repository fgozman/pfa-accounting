"""ANAF integration tables: connections, capabilities, attempts, receipts."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
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

from pfa.models.base import (
    Base,
    SubmissionChannel,
    Timestamps,
    UUIDPrimaryKey,
    WorkspaceOwned,
    enum_column,
)


class AnafConnection(UUIDPrimaryKey, Timestamps, WorkspaceOwned, Base):
    """ANAF authorization, separate from application login.

    Tokens and client credentials are stored as ciphertext references to a
    managed key service.  The browser only ever receives connection status.
    """

    __tablename__ = "anaf_connections"

    environment: Mapped[str] = mapped_column(String(16), nullable=False, default="test")
    connector_client_ref: Mapped[str | None] = mapped_column(String(200))
    access_token_ciphertext: Mapped[str | None] = mapped_column(String(2000))
    refresh_token_ciphertext: Mapped[str | None] = mapped_column(String(2000))
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: taxpayer identities this connector is authorized for (CUI/CIF list)
    allowed_taxpayers: Mapped[list | None] = mapped_column(JSONB)
    authorized_cif: Mapped[str | None] = mapped_column(String(20))
    authorized_identity: Mapped[str | None] = mapped_column(String(200))
    scopes: Mapped[list | None] = mapped_column(JSONB)
    #: pending | active | expired | revoked | error
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))
    #: one-use state binding for the backend-only callback
    pending_state_hash: Mapped[str | None] = mapped_column(String(64))
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    __table_args__ = (
        Index("ix_anaf_connections_workspace_status", "workspace_id", "status"),
    )


class ConnectorCapability(UUIDPrimaryKey, Timestamps, Base):
    """Verified facts about an official submission channel.

    If any required fact is unknown, automated submission stays disabled while
    generation/export remains available with an explanation.
    """

    __tablename__ = "connector_capabilities"

    form_code: Mapped[str] = mapped_column(String(16), nullable=False)
    environment: Mapped[str] = mapped_column(String(16), nullable=False)
    channel: Mapped[SubmissionChannel] = mapped_column(
        enum_column(SubmissionChannel), nullable=False
    )
    endpoint_url: Mapped[str | None] = mapped_column(String(500))
    signing_requirement: Mapped[str | None] = mapped_column(String(200))
    schema_version: Mapped[str | None] = mapped_column(String(64))
    validator_version: Mapped[str | None] = mapped_column(String(64))
    receipt_behavior: Mapped[str | None] = mapped_column(String(500))
    delegation_supported: Mapped[bool | None] = mapped_column(Boolean)
    test_environment_available: Mapped[bool | None] = mapped_column(Boolean)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_by: Mapped[str | None] = mapped_column(String(200))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    evidence: Mapped[dict | None] = mapped_column(JSONB)
    notes: Mapped[str | None] = mapped_column(String(1000))

    __table_args__ = (
        UniqueConstraint(
            "form_code", "environment", "channel", name="uq_connector_capability_key"
        ),
    )


class SubmissionAttempt(UUIDPrimaryKey, Timestamps, WorkspaceOwned, Base):
    __tablename__ = "submission_attempts"

    declaration_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    declaration_version: Mapped[int] = mapped_column(Integer, nullable=False)
    connection_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    channel: Mapped[SubmissionChannel] = mapped_column(
        enum_column(SubmissionChannel), nullable=False
    )
    #: queued | sending | succeeded | failed | timeout | unknown
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    request_object_key: Mapped[str | None] = mapped_column(String(400))
    remote_reference: Mapped[str | None] = mapped_column(String(200))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(1000))
    #: transport error vs fiscal validation rejection are distinct
    error_class: Mapped[str | None] = mapped_column(String(24))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_response: Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "declaration_id",
            "declaration_version",
            "attempt_number",
            name="uq_submission_attempt_number",
        ),
        Index("ix_submission_attempts_pending", "status", "next_retry_at"),
        Index("ix_submission_attempts_remote_ref", "remote_reference"),
    )


class RemoteMessage(UUIDPrimaryKey, Timestamps, WorkspaceOwned, Base):
    __tablename__ = "remote_messages"

    connection_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    direction: Mapped[str] = mapped_column(String(16), nullable=False, default="inbound")
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="status")
    remote_reference: Mapped[str | None] = mapped_column(String(200))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    payload: Mapped[dict | None] = mapped_column(JSONB)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "remote_reference", "kind", name="uq_remote_message_key"
        ),
    )


class Receipt(UUIDPrimaryKey, Timestamps, WorkspaceOwned, Base):
    """ANAF acceptance/rejection evidence.  A successful HTTP response alone is
    not acceptance."""

    __tablename__ = "receipts"

    declaration_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    declaration_version: Mapped[int] = mapped_column(Integer, nullable=False)
    submission_attempt_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="acceptance")
    remote_reference: Mapped[str | None] = mapped_column(String(200))
    artifact_sha256: Mapped[str | None] = mapped_column(String(64))
    #: verification of taxpayer / form / period / artifact binding
    taxpayer_matches: Mapped[bool | None] = mapped_column(Boolean)
    form_matches: Mapped[bool | None] = mapped_column(Boolean)
    period_matches: Mapped[bool | None] = mapped_column(Boolean)
    artifact_matches: Mapped[bool | None] = mapped_column(Boolean)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_by_user_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    #: manually reported acceptance stays unverified until checked
    manually_reported: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    object_key: Mapped[str | None] = mapped_column(String(400))
    sha256: Mapped[str | None] = mapped_column(String(64))
    rejection_errors: Mapped[list | None] = mapped_column(JSONB)
    raw: Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "remote_reference", "kind", name="uq_receipt_ref_kind"
        ),
        Index("ix_receipts_declaration", "declaration_id"),
    )
