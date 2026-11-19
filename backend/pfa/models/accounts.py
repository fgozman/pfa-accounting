"""Accounts: users, email verification, password reset, MFA factors."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from pfa.models.base import Base, Timestamps, UserStatus, UUIDPrimaryKey, enum_column


class User(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "users"

    #: case-folded address; the only value used for uniqueness and lookups
    email_normalized: Mapped[str] = mapped_column(String(320), nullable=False)
    #: as the user typed it, for display in notifications
    email_display: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[UserStatus] = mapped_column(
        enum_column(UserStatus), nullable=False, default=UserStatus.PENDING_VERIFICATION
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mfa_required: Mapped[bool] = mapped_column(default=False, nullable=False)

    factors: Mapped[list[MfaFactor]] = relationship(back_populates="user")

    __table_args__ = (
        Index("uq_users_email_normalized", "email_normalized", unique=True),
    )


class EmailTokenMixin:
    """Shared shape for single-use, hashed, short-lived email tokens."""

    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class EmailVerificationToken(UUIDPrimaryKey, EmailTokenMixin, Base):
    __tablename__ = "email_verification_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    email_normalized: Mapped[str] = mapped_column(String(320), nullable=False)

    __table_args__ = (UniqueConstraint("token_hash", name="uq_email_verification_token_hash"),)


class PasswordResetToken(UUIDPrimaryKey, EmailTokenMixin, Base):
    __tablename__ = "password_reset_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    __table_args__ = (UniqueConstraint("token_hash", name="uq_password_reset_token_hash"),)


class MfaFactor(UUIDPrimaryKey, Base):
    __tablename__ = "mfa_factors"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="totp")
    #: ciphertext reference; the key lives in a managed key service
    secret_ciphertext: Mapped[str] = mapped_column(String(512), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="factors")
