"""Tenancy: workspaces, memberships, invitations, taxpayers and access grants."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from pfa.models.base import (
    Base,
    InvitationStatus,
    MembershipRole,
    MembershipStatus,
    Timestamps,
    UUIDPrimaryKey,
    WorkspaceOwned,
    enum_column,
)


class Workspace(UUIDPrimaryKey, Timestamps, Base):
    """Tenant root.  Not itself RLS-filtered by ``workspace_id``: membership is
    the predicate (see the RLS migration)."""

    __tablename__ = "workspaces"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: CUI / CIF of the PFA
    cui: Mapped[str | None] = mapped_column(String(20))
    accounting_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="RON")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Europe/Bucharest")
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_workspaces_cui", "cui"),)


class Membership(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "memberships"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    role: Mapped[MembershipRole] = mapped_column(enum_column(MembershipRole), nullable=False)
    status: Mapped[MembershipStatus] = mapped_column(
        enum_column(MembershipStatus), nullable=False, default=MembershipStatus.ACTIVE
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_membership_workspace_user"),
        Index("ix_memberships_user_active", "user_id", "status"),
    )


class Invitation(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "invitations"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    email_normalized: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[MembershipRole] = mapped_column(
        enum_column(MembershipRole), nullable=False, default=MembershipRole.DEFAULT
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[InvitationStatus] = mapped_column(
        enum_column(InvitationStatus), nullable=False, default=InvitationStatus.PENDING
    )
    invited_by_user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_invitation_token_hash"),
        Index(
            "ix_invitations_pending",
            "workspace_id",
            "email_normalized",
            "status",
        ),
    )


class Taxpayer(UUIDPrimaryKey, Timestamps, Base):
    """An individual taxpayer.

    ``workspace_id`` is NULL for a *personal source record* that lives outside
    any workspace.  A workspace may only use such a record after an explicit
    ``TaxpayerAccessGrant`` exists.  D212 belongs to the taxpayer, not to a PFA
    CUI, so an annual return is unique per (taxpayer, year, form).
    """

    __tablename__ = "taxpayers"

    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="RESTRICT"), index=True
    )
    #: the person who owns the personal record
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: encrypted CNP/NIF; ciphertext reference only, key managed separately
    cnp_ciphertext: Mapped[str | None] = mapped_column(String(512))
    cif: Mapped[str | None] = mapped_column(String(20))
    is_personal_source: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class TaxpayerAccessGrant(UUIDPrimaryKey, Timestamps, WorkspaceOwned, Base):
    """Explicit, revocable authorization to consolidate a personal source record
    into a workspace return."""

    __tablename__ = "taxpayer_access_grants"

    taxpayer_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("taxpayers.id", ondelete="RESTRICT"), nullable=False
    )
    granted_by_user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: version of the sharing notice the user acknowledged before consolidating
    #: personal data into a workspace return
    sharing_notice_version: Mapped[str | None] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint("taxpayer_id", "workspace_id", name="uq_taxpayer_grant"),
    )


class PfaProfile(UUIDPrimaryKey, Timestamps, WorkspaceOwned, Base):
    __tablename__ = "pfa_profiles"

    legal_name: Mapped[str] = mapped_column(String(200), nullable=False)
    cui: Mapped[str] = mapped_column(String(20), nullable=False)
    trade_registry_number: Mapped[str | None] = mapped_column(String(64))
    registered_address: Mapped[str | None] = mapped_column(String(400))
    fiscal_domicile: Mapped[str | None] = mapped_column(String(400))
    county: Mapped[str | None] = mapped_column(String(64))
    locality: Mapped[str | None] = mapped_column(String(128))
    phone: Mapped[str | None] = mapped_column(String(32))
    iban: Mapped[str | None] = mapped_column(String(34))
    #: when the activity started; NULL means "not stated", so no initial registration is
    #: claimed for any year
    started_on: Mapped[date | None] = mapped_column(Date)
    #: regulated entity identifier for D212/D300 headers
    cnp_ciphertext: Mapped[str | None] = mapped_column(String(512))
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (Index("ix_pfa_profiles_workspace_active", "workspace_id", "active"),)


class ActivityPeriod(UUIDPrimaryKey, Timestamps, WorkspaceOwned, Base):
    """Effective-dated CAEN activity periods (start / suspension / termination)."""

    __tablename__ = "activity_periods"

    caen_code: Mapped[str] = mapped_column(String(8), nullable=False)
    description: Mapped[str | None] = mapped_column(String(200))
    starts_on: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_on: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    suspended_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    suspension_reason: Mapped[str | None] = mapped_column(String(200))

    __table_args__ = (Index("ix_activity_periods_workspace_dates", "workspace_id", "starts_on"),)


class VatRegistration(UUIDPrimaryKey, Timestamps, WorkspaceOwned, Base):
    __tablename__ = "vat_registrations"

    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    vat_code: Mapped[str | None] = mapped_column(String(20))
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reporting_frequency: Mapped[str | None] = mapped_column(String(16))
    cash_accounting: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: documentary evidence for the registration/change
    evidence_reference: Mapped[str | None] = mapped_column(String(300))

    __table_args__ = (
        Index("ix_vat_registrations_workspace_effective", "workspace_id", "effective_from"),
    )


class TaxRegime(UUIDPrimaryKey, Timestamps, WorkspaceOwned, Base):
    __tablename__ = "tax_regimes"

    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    income_year: Mapped[int] = mapped_column(nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: for norm taxation: county / locality / activity the published norm belongs to
    norm_county: Mapped[str | None] = mapped_column(String(64))
    norm_locality: Mapped[str | None] = mapped_column(String(128))
    norm_activity: Mapped[str | None] = mapped_column(String(200))
    norm_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    norm_source_url: Mapped[str | None] = mapped_column(String(500))

    __table_args__ = (Index("ix_tax_regimes_workspace_year", "workspace_id", "income_year"),)


class TaxpayerYearFact(UUIDPrimaryKey, Timestamps, WorkspaceOwned, Base):
    """Per-year fiscal facts.  ``value`` is NULL when the answer is *unknown*:
    unknown must never become zero or "no"."""

    __tablename__ = "taxpayer_year_facts"

    income_year: Mapped[int] = mapped_column(nullable=False)
    fact_key: Mapped[str] = mapped_column(String(64), nullable=False)
    value_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    value_text: Mapped[str | None] = mapped_column(String(500))
    value_decimal: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    value_boolean: Mapped[bool | None] = mapped_column(Boolean)
    answered_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    evidence_reference: Mapped[str | None] = mapped_column(String(300))
    #: which declarations this fact blocks while unanswered
    blocks_declarations: Mapped[list | None] = mapped_column(JSONB)

    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "income_year", "fact_key", name="uq_year_fact_workspace_year_key"
        ),
        Index("ix_year_facts_workspace_year", "workspace_id", "income_year"),
    )


class LossCarryforward(UUIDPrimaryKey, Timestamps, WorkspaceOwned, Base):
    __tablename__ = "loss_carryforwards"

    income_year: Mapped[int] = mapped_column(nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    consumed_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=Decimal("0")
    )
    expires_after_year: Mapped[int | None] = mapped_column()
    source_calculation_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    __table_args__ = (
        UniqueConstraint("workspace_id", "income_year", name="uq_loss_workspace_year"),
    )
