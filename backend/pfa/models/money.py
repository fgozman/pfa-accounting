"""Money: accounts, settlements, allocations, bank imports, exchange rates."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from pfa.models.base import (
    MONEY,
    RATE,
    Base,
    PaymentDirection,
    PaymentKind,
    PaymentMethod,
    Timestamps,
    UUIDPrimaryKey,
    WorkspaceOwned,
    enum_column,
)


class FinancialAccount(UUIDPrimaryKey, Timestamps, WorkspaceOwned, Base):
    __tablename__ = "financial_accounts"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # bank | cash | card | processor
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="RON")
    iban: Mapped[str | None] = mapped_column(String(34))
    opening_balance: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        UniqueConstraint("id", "workspace_id", name="uq_financial_accounts_id_workspace"),
        UniqueConstraint("workspace_id", "name", name="uq_financial_account_name"),
    )


class Payment(UUIDPrimaryKey, Timestamps, WorkspaceOwned, Base):
    """A receipt or payment.  Independently recorded from documents: an uploaded
    invoice is not evidence of payment."""

    __tablename__ = "payments"

    account_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    direction: Mapped[PaymentDirection] = mapped_column(
        enum_column(PaymentDirection), nullable=False
    )
    kind: Mapped[PaymentKind] = mapped_column(
        enum_column(PaymentKind), nullable=False, default=PaymentKind.OTHER
    )
    method: Mapped[PaymentMethod] = mapped_column(
        enum_column(PaymentMethod), nullable=False, default=PaymentMethod.BANK
    )
    paid_on: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="RON")
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    amount_ron: Mapped[Decimal | None] = mapped_column(MONEY)
    exchange_rate_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    bank_fee_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    withholding_amount: Mapped[Decimal] = mapped_column(
        MONEY, nullable=False, default=Decimal("0")
    )
    reference: Mapped[str | None] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(String(500))
    #: gross amount received from a processor before its net payout
    gross_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    is_owner_movement: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    #: external identifier from a bank import, for duplicate detection
    external_id: Mapped[str | None] = mapped_column(String(200))

    __table_args__ = (
        UniqueConstraint("id", "workspace_id", name="uq_payments_id_workspace"),
        ForeignKeyConstraint(
            ["account_id", "workspace_id"],
            ["financial_accounts.id", "financial_accounts.workspace_id"],
            name="fk_payment_account_same_workspace",
        ),
        UniqueConstraint("workspace_id", "external_id", name="uq_payment_workspace_external_id"),
        Index("ix_payments_workspace_date", "workspace_id", "paid_on"),
    )


class PaymentAllocation(UUIDPrimaryKey, Timestamps, WorkspaceOwned, Base):
    """Links a settlement to a document.  Totals may not exceed the document
    amount except for explicitly modelled advances, credit or FX differences."""

    __tablename__ = "payment_allocations"

    payment_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="RON")
    #: advance | credit | fx_difference | settlement
    allocation_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="settlement")
    fx_difference_amount: Mapped[Decimal] = mapped_column(
        MONEY, nullable=False, default=Decimal("0")
    )
    note: Mapped[str | None] = mapped_column(String(300))

    __table_args__ = (
        ForeignKeyConstraint(
            ["payment_id", "workspace_id"],
            ["payments.id", "payments.workspace_id"],
            name="fk_allocation_payment_same_workspace",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["document_id", "workspace_id"],
            ["documents.id", "documents.workspace_id"],
            name="fk_allocation_document_same_workspace",
        ),
        Index("ix_allocations_document", "workspace_id", "document_id"),
        Index("ix_allocations_payment", "workspace_id", "payment_id"),
    )


class BankImport(UUIDPrimaryKey, Timestamps, WorkspaceOwned, Base):
    __tablename__ = "bank_imports"

    account_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    original_filename: Mapped[str | None] = mapped_column(String(300))
    object_key: Mapped[str | None] = mapped_column(String(400))
    rows_total: Mapped[int] = mapped_column(nullable=False, default=0)
    rows_imported: Mapped[int] = mapped_column(nullable=False, default=0)
    rows_duplicate: Mapped[int] = mapped_column(nullable=False, default=0)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    details: Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (
        ForeignKeyConstraint(
            ["account_id", "workspace_id"],
            ["financial_accounts.id", "financial_accounts.workspace_id"],
            name="fk_bank_import_account_same_workspace",
        ),
    )


class ExchangeRate(UUIDPrimaryKey, Timestamps, Base):
    """Official exchange rates.  Missing official data blocks affected final
    calculations until an authorized user supplies evidenced data."""

    __tablename__ = "exchange_rates"

    workspace_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    quote_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="RON")
    rate_date: Mapped[date] = mapped_column(Date, nullable=False)
    rate: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    #: bnm | ecb | user_evidenced | anaf
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_reference: Mapped[str | None] = mapped_column(String(300))
    #: invoice | settlement | vat | annual_eligibility
    purpose: Mapped[str] = mapped_column(String(32), nullable=False, default="invoice")
    supplied_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    evidence_object_key: Mapped[str | None] = mapped_column(String(400))
    is_official: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        Index(
            "ix_exchange_rates_lookup",
            "currency",
            "quote_currency",
            "rate_date",
            "purpose",
        ),
        UniqueConstraint(
            "workspace_id",
            "currency",
            "quote_currency",
            "rate_date",
            "purpose",
            "source",
            name="uq_exchange_rate_key",
        ),
    )
