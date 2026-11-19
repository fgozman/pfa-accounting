"""Books: accounting events, tax adjustments, assets, depreciation, inventory, locks."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from pfa.models.base import (
    MONEY,
    AssetStatus,
    Base,
    DeductibilityTreatment,
    DepreciationMethod,
    DocumentKind,
    Timestamps,
    UUIDPrimaryKey,
    WorkspaceOwned,
    enum_column,
)


class AccountingEvent(UUIDPrimaryKey, Timestamps, WorkspaceOwned, Base):
    """Normalized accounting event derived from documents and payments.

    Single-entry cash records and tax adjustments stay reconcilable but
    distinct: this table is the recognisable cash/accrual entry, while
    ``tax_adjustments`` holds the non-cash corrections.
    """

    __tablename__ = "accounting_events"

    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    #: revenue | expense | asset_acquisition | owner_movement | vat | tax_payment | inventory
    event_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    document_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    payment_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    asset_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    amount_ron: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    vat_amount_ron: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    #: amount recognised for annual income-tax purposes
    taxable_amount_ron: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    #: realization | accrual — cash recognition versus accrual recognition
    recognition: Mapped[str] = mapped_column(String(16), nullable=False, default="realization")
    category: Mapped[str | None] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(String(400))
    #: which declaration periods this event feeds
    feeds_forms: Mapped[list | None] = mapped_column(JSONB)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )

    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "document_id",
            "payment_id",
            "event_kind",
            name="uq_accounting_event_source",
        ),
        Index("ix_accounting_events_workspace_date", "workspace_id", "event_date"),
        Index("ix_accounting_events_workspace_kind", "workspace_id", "event_kind"),
    )


class TaxAdjustment(UUIDPrimaryKey, Timestamps, WorkspaceOwned, Base):
    """Non-cash income-tax adjustment (deductibility limits, depreciation,
    loss offsets, contribution deductions)."""

    __tablename__ = "tax_adjustments"

    income_year: Mapped[int] = mapped_column(nullable=False)
    adjustment_kind: Mapped[str] = mapped_column(String(48), nullable=False)
    #: increase | decrease | none
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    accounting_event_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    document_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    asset_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    rule_pack_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    legal_reference: Mapped[str | None] = mapped_column(String(200))
    explanation: Mapped[str | None] = mapped_column(String(500))
    computed_by: Mapped[str] = mapped_column(String(16), nullable=False, default="engine")

    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "income_year",
            "adjustment_kind",
            "accounting_event_id",
            "document_id",
            "asset_id",
            name="uq_tax_adjustment_source",
        ),
        Index("ix_tax_adjustments_workspace_year", "workspace_id", "income_year"),
    )


class Asset(UUIDPrimaryKey, Timestamps, WorkspaceOwned, Base):
    __tablename__ = "assets"

    name: Mapped[str] = mapped_column(String(300), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    acquisition_date: Mapped[date] = mapped_column(Date, nullable=False)
    in_service_date: Mapped[date] = mapped_column(Date, nullable=False)
    acquisition_cost: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    #: recoverable (deductible) versus non-recoverable VAT
    recoverable_vat: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    nonrecoverable_vat: Mapped[Decimal] = mapped_column(
        MONEY, nullable=False, default=Decimal("0")
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="RON")
    acquisition_cost_ron: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    business_use_percent: Mapped[Decimal] = mapped_column(
        Numeric(9, 6), nullable=False, default=Decimal("100")
    )
    depreciation_method: Mapped[DepreciationMethod] = mapped_column(
        enum_column(DepreciationMethod), nullable=False, default=DepreciationMethod.STRAIGHT_LINE
    )
    #: months, per the applicable legal category
    useful_life_months: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    status: Mapped[AssetStatus] = mapped_column(
        enum_column(AssetStatus), nullable=False, default=AssetStatus.DRAFT
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    disposal_date: Mapped[date | None] = mapped_column(Date)
    disposal_proceeds: Mapped[Decimal | None] = mapped_column(MONEY)
    disposal_notes: Mapped[str | None] = mapped_column(String(400))
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("id", "workspace_id", name="uq_assets_id_workspace"),
        Index("ix_assets_workspace_status", "workspace_id", "status"),
    )


class DepreciationEntry(UUIDPrimaryKey, Timestamps, WorkspaceOwned, Base):
    __tablename__ = "depreciation_entries"

    asset_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    income_year: Mapped[int] = mapped_column(nullable=False)
    period_month: Mapped[int | None] = mapped_column()
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    deductible_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    book_value_after: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    rule_pack_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    calculation_note: Mapped[str | None] = mapped_column(String(300))

    __table_args__ = (
        ForeignKeyConstraint(
            ["asset_id", "workspace_id"],
            ["assets.id", "assets.workspace_id"],
            name="fk_depreciation_asset_same_workspace",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "asset_id", "income_year", "period_month", name="uq_depreciation_asset_period"
        ),
        Index("ix_depreciation_workspace_year", "workspace_id", "income_year"),
    )


class InventoryEntry(UUIDPrimaryKey, Timestamps, WorkspaceOwned, Base):
    __tablename__ = "inventory_entries"

    income_year: Mapped[int] = mapped_column(nullable=False)
    entry_kind: Mapped[str] = mapped_column(String(24), nullable=False)  # movement | valuation
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False)
    item_name: Mapped[str] = mapped_column(String(300), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=Decimal("0"))
    unit_cost: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    value: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    document_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    note: Mapped[str | None] = mapped_column(String(400))

    __table_args__ = (Index("ix_inventory_workspace_year", "workspace_id", "income_year"),)


class PeriodLock(UUIDPrimaryKey, Timestamps, WorkspaceOwned, Base):
    """A closed period.  Ordinary edits are blocked; reopening requires a reason
    and creates a new revision."""

    __tablename__ = "period_locks"

    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    period_kind: Mapped[str] = mapped_column(String(24), nullable=False, default="month")
    locked_by_user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    locked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    reopened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reopened_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    reopen_reason: Mapped[str | None] = mapped_column(String(500))
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    close_checklist: Mapped[dict | None] = mapped_column(JSONB)
    is_open: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "period_start",
            "period_end",
            "revision",
            name="uq_period_lock_revision",
        ),
        Index("ix_period_locks_workspace_range", "workspace_id", "period_start", "period_end"),
    )


class RegisterExport(UUIDPrimaryKey, Timestamps, WorkspaceOwned, Base):
    """Authenticated export job output for an accounting register."""

    __tablename__ = "register_exports"

    register_type: Mapped[str] = mapped_column(String(48), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    format: Mapped[str] = mapped_column(String(16), nullable=False, default="csv")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    job_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    object_key: Mapped[str | None] = mapped_column(String(400))
    sha256: Mapped[str | None] = mapped_column(String(64))
    row_count: Mapped[int | None] = mapped_column()
    document_kind: Mapped[DocumentKind | None] = mapped_column(enum_column(DocumentKind))
    deductibility: Mapped[DeductibilityTreatment | None] = mapped_column(
        enum_column(DeductibilityTreatment)
    )
