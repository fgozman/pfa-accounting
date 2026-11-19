"""OAuth 2.0 registration and runtime tables.

Every table used by the authorization server is prefixed ``oauth_`` and lives in
the server's **own database** (``PFA_OAUTH_DATABASE_URL``), with its own metadata
(:class:`OAuthBase`) and its own migration history (``migrations_oauth/``).  None
of these tables exists in the application database, and foreign keys stay inside
the set.

``user_id`` / ``authenticated_user_id`` hold the **subject identifier** returned by
the identity provider (:class:`pfa.oauth.ports.IdentityProvider`) and emitted as the
JWT ``sub``.  It is an opaque UUID here: there is deliberately no foreign key to
``users``, which belongs to the application.

Cookie-free: authorization transactions (stored in PostgreSQL) replace a
cookie-based login session.  Authorization codes and refresh tokens are stored
as hashes only.
"""

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
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from pfa.models.base import Timestamps, UUIDPrimaryKey


class OAuthBase(DeclarativeBase):
    """Declarative base of the authorization server's database (separate metadata)."""


class OAuthClient(UUIDPrimaryKey, Timestamps, OAuthBase):
    __tablename__ = "oauth_clients"

    #: human-readable public client identifier, e.g. "pfa-web-production"
    client_id: Mapped[str] = mapped_column(String(64), nullable=False)
    client_type: Mapped[str] = mapped_column(String(16), nullable=False, default="public")
    #: public clients use token_endpoint_auth_method=none
    token_endpoint_auth_method: Mapped[str] = mapped_column(
        String(32), nullable=False, default="none"
    )
    environment: Mapped[str] = mapped_column(String(16), nullable=False, default="development")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    require_pkce: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    grant_types: Mapped[list] = mapped_column(JSONB, nullable=False)
    response_types: Mapped[list] = mapped_column(JSONB, nullable=False)
    allowed_scopes: Mapped[list] = mapped_column(JSONB, nullable=False)
    allowed_pkce_methods: Mapped[list] = mapped_column(JSONB, nullable=False)
    #: hashed secret; only present for confidential clients (not the SPA)
    client_secret_hash: Mapped[str | None] = mapped_column(String(255))

    redirect_uris: Mapped[list[OAuthRedirectUri]] = relationship(
        back_populates="client", cascade="all, delete-orphan", lazy="selectin"
    )
    origins: Mapped[list[OAuthOrigin]] = relationship(
        back_populates="client", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        UniqueConstraint("client_id", "environment", name="uq_oauth_client_env"),
    )


class OAuthRedirectUri(UUIDPrimaryKey, OAuthBase):
    """Exact-match redirect registration.  Wildcards, fragments and prefix
    matching are rejected at registration time."""

    __tablename__ = "oauth_redirect_uris"

    client_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("oauth_clients.id", ondelete="CASCADE"), nullable=False
    )
    exact_uri: Mapped[str] = mapped_column(String(500), nullable=False)

    client: Mapped[OAuthClient] = relationship(back_populates="redirect_uris")

    __table_args__ = (
        UniqueConstraint("client_id", "exact_uri", name="uq_oauth_redirect_client_uri"),
    )


class OAuthOrigin(UUIDPrimaryKey, OAuthBase):
    __tablename__ = "oauth_origins"

    client_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("oauth_clients.id", ondelete="CASCADE"), nullable=False
    )
    origin: Mapped[str] = mapped_column(String(500), nullable=False)

    client: Mapped[OAuthClient] = relationship(back_populates="origins")

    __table_args__ = (UniqueConstraint("client_id", "origin", name="uq_oauth_origin"),)


class OAuthAuthorizationTransaction(UUIDPrimaryKey, OAuthBase):
    """Short-lived authorization request.  Replaces a login-session cookie.

    The transaction proof protects the login form: it is generated with the
    transaction and must be echoed by the login POST.  Client, redirect URI and
    PKCE values are always read from this stored row, never from the login POST.
    """

    __tablename__ = "oauth_authorization_transactions"

    client_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("oauth_clients.id", ondelete="CASCADE"), nullable=False
    )
    redirect_uri: Mapped[str] = mapped_column(String(500), nullable=False)
    scope: Mapped[str] = mapped_column(String(500), nullable=False)
    state: Mapped[str] = mapped_column(String(500), nullable=False)
    code_challenge: Mapped[str] = mapped_column(String(128), nullable=False)
    code_challenge_method: Mapped[str] = mapped_column(String(8), nullable=False)
    #: hash of the one-use proof embedded in the rendered login page
    proof_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    #: hash of the client's Origin header at authorize time
    origin_hash: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: subject id from the identity provider (opaque; no foreign key)
    authenticated_user_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    authenticated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_oauth_authz_tx_expires", "expires_at"),
        UniqueConstraint("proof_hash", name="uq_oauth_authz_tx_proof_hash"),
    )


class OAuthAuthorizationCode(UUIDPrimaryKey, OAuthBase):
    __tablename__ = "oauth_authorization_codes"

    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    #: subject id from the identity provider (opaque; no foreign key)
    user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    client_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("oauth_clients.id", ondelete="CASCADE"), nullable=False
    )
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("oauth_authorization_transactions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    #: the exact registered URI this code was issued for; the token exchange
    #: must present the same value
    redirect_uri: Mapped[str] = mapped_column(String(500), nullable=False)
    code_challenge: Mapped[str] = mapped_column(String(128), nullable=False)
    code_challenge_method: Mapped[str] = mapped_column(String(8), nullable=False)
    scope: Mapped[str] = mapped_column(String(500), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    session_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (UniqueConstraint("code_hash", name="uq_oauth_authz_code_hash"),)


class OAuthSession(UUIDPrimaryKey, OAuthBase):
    """Refresh-token family / login session."""

    __tablename__ = "oauth_sessions"

    #: subject id from the identity provider (opaque; no foreign key)
    user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    client_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("oauth_clients.id", ondelete="CASCADE"), nullable=False
    )
    scope: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    #: absolute session lifetime; refresh rotation never extends it
    absolute_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoke_reason: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (Index("ix_oauth_sessions_user_active", "user_id", "revoked_at"),)


class OAuthRefreshToken(UUIDPrimaryKey, OAuthBase):
    __tablename__ = "oauth_refresh_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    jti: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    #: subject id from the identity provider (opaque; no foreign key)
    user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("oauth_sessions.id", ondelete="CASCADE"), nullable=False
    )
    #: rotation chain: the token this one replaced
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("oauth_refresh_tokens.id", ondelete="SET NULL")
    )
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_oauth_refresh_token_hash"),
        UniqueConstraint("jti", name="uq_oauth_refresh_token_jti"),
        Index("ix_oauth_refresh_tokens_session", "session_id"),
    )
