"""Documents: counterparties, invoices, lines, revisions, attachments, numbering."""

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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from pfa.models.base import (
    MONEY,
    Base,
    DeductibilityTreatment,
    DocumentKind,
    DocumentSource,
    DocumentStatus,
    EvidenceKind,
    PartnerKind,
    Timestamps,
    UUIDPrimaryKey,
    VatTreatment,
    WorkspaceOwned,
    enum_column,
)


class Counterparty(UUIDPrimaryKey, Timestamps, WorkspaceOwned, Base):
    __tablename__ = "counterparties"

    name: Mapped[str] = mapped_column(String(300), nullable=False)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False, default="RO")
    #: business | consumer | public_body — determines e-Factura applicability
    party_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="business")
    vat_identifier: Mapped[str | None] = mapped_column(String(32))
    vat_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    vat_verification_source: Mapped[str | None] = mapped_column(String(200))
    tax_identifier: Mapped[str | None] = mapped_column(String(32))
    address: Mapped[str | None] = mapped_column(String(400))
    county: Mapped[str | None] = mapped_column(String(64))
    locality: Mapped[str | None] = mapped_column(String(128))
    is_related_party: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint("id", "workspace_id", name="uq_counterparties_id_workspace"),
        UniqueConstraint(
            "workspace_id", "name", "country_code", name="uq_counterparty_workspace_name_country"
        ),
        Index("ix_counterparties_workspace_vat", "workspace_id", "vat_identifier"),
    )


class NumberSeries(UUIDPrimaryKey, Timestamps, WorkspaceOwned, Base):
    """Transactional invoice numbering.  Numbers are never reused."""

    __tablename__ = "number_series"

    document_kind: Mapped[DocumentKind] = mapped_column(
        enum_column(DocumentKind), nullable=False
    )
    prefix: Mapped[str] = mapped_column(String(24), nullable=False, default="")
    #: last assigned number; allocation happens under SELECT ... FOR UPDATE
    last_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    year: Mapped[int | None] = mapped_column()

    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "document_kind", "prefix", "year", name="uq_number_series_key"
        ),
    )


class Document(UUIDPrimaryKey, Timestamps, WorkspaceOwned, Base):
    __tablename__ = "documents"

    kind: Mapped[DocumentKind] = mapped_column(enum_column(DocumentKind), nullable=False)
    status: Mapped[DocumentStatus] = mapped_column(
        enum_column(DocumentStatus), nullable=False, default=DocumentStatus.DRAFT
    )
    source: Mapped[DocumentSource] = mapped_column(
        enum_column(DocumentSource), nullable=False, default=DocumentSource.MANUAL
    )
    counterparty_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True)
    )
    supplier_name_raw: Mapped[str | None] = mapped_column(String(300))
    #: ISO-3166 alpha-2 country of the supplier when there is no registered counterparty
    supplier_country: Mapped[str | None] = mapped_column(String(2))
    supplier_vat_id: Mapped[str | None] = mapped_column(String(32))
    #: B2B/B2C nature of the other party; NULL means it was never stated
    partner_kind: Mapped[PartnerKind | None] = mapped_column(enum_column(PartnerKind, length=16))

    series: Mapped[str | None] = mapped_column(String(24))
    number: Mapped[str | None] = mapped_column(String(32))
    issue_date: Mapped[date | None] = mapped_column(Date)
    supply_date: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)

    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="RON")
    #: total in document currency
    total_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    total_vat: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    #: RON amounts per the legally applicable conversion date
    total_amount_ron: Mapped[Decimal | None] = mapped_column(MONEY)
    exchange_rate_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    vat_treatment: Mapped[VatTreatment] = mapped_column(
        enum_column(VatTreatment), nullable=False, default=VatTreatment.UNDETERMINED
    )
    deductibility: Mapped[DeductibilityTreatment] = mapped_column(
        enum_column(DeductibilityTreatment),
        nullable=False,
        default=DeductibilityTreatment.UNDETERMINED,
    )
    #: business-use share of the expense; NULL means the user has not stated it, which
    #: the declaration reports instead of multiplying by an invented share
    business_use_percent: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    business_purpose: Mapped[str | None] = mapped_column(String(400))
    deductibility_justification: Mapped[str | None] = mapped_column(String(500))
    reverse_charge_justification: Mapped[str | None] = mapped_column(String(500))
    #: category used for the income-tax versus VAT split (e.g. vehicle, hotel)
    expense_category: Mapped[str | None] = mapped_column(String(64))
    #: D390/D394 intra-community operation type (L/T/A/P/S/R); NULL when domestic
    intracommunity_operation_type: Mapped[str | None] = mapped_column(String(1))
    is_tax_ready: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    #: external identifier for duplicate detection (e-Factura id, import key)
    external_id: Mapped[str | None] = mapped_column(String(200))
    #: hash of the canonical content, used for duplicate detection on import
    content_hash: Mapped[str | None] = mapped_column(String(64))
    #: credit note linkage: the corrected document
    corrects_document_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    correction_reason: Mapped[str | None] = mapped_column(String(300))
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    lines: Mapped[list[DocumentLine]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="DocumentLine.line_number",
    )

    __table_args__ = (
        # composite uniqueness lets children enforce the same workspace
        UniqueConstraint("id", "workspace_id", name="uq_documents_id_workspace"),
        #: uniqueness is enforced only for the invoices the PFA issues: a supplier's number
        #: belongs to that supplier, so two suppliers may both send an invoice numbered "1"
        #: (migrations 0007/0008/0009)
        Index(
            "uq_document_workspace_series_number",
            "workspace_id",
            # COALESCE because NULLs are distinct in a unique index: without it two
            # number-only invoices would both be accepted (migration 0012)
            text("COALESCE(series, '')"),
            "number",
            unique=True,
            postgresql_where=text(
                "kind IN ('sales_invoice', 'credit_note') AND number IS NOT NULL"
            ),
        ),
        #: a deleted document releases its import key, so the same source can be recorded
        #: again (migrations 0006, 0009)
        Index(
            "uq_document_workspace_external_id",
            "workspace_id",
            "external_id",
            unique=True,
            postgresql_where=text("external_id IS NOT NULL"),
        ),
        ForeignKeyConstraint(
            ["counterparty_id", "workspace_id"],
            ["counterparties.id", "counterparties.workspace_id"],
            name="fk_document_counterparty_same_workspace",
        ),
        ForeignKeyConstraint(
            ["corrects_document_id", "workspace_id"],
            ["documents.id", "documents.workspace_id"],
            name="fk_document_corrects_same_workspace",
        ),
        Index("ix_documents_workspace_date", "workspace_id", "issue_date"),
        Index("ix_documents_workspace_status", "workspace_id", "status"),
        Index("ix_documents_content_hash", "workspace_id", "content_hash"),
    )


class DocumentLine(UUIDPrimaryKey, Timestamps, WorkspaceOwned, Base):
    __tablename__ = "document_lines"

    document_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(String(400), nullable=False, default="")
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=Decimal("1"))
    unit_price: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    discount_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    vat_rate: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False, default=Decimal("0"))
    vat_treatment: Mapped[VatTreatment] = mapped_column(
        enum_column(VatTreatment), nullable=False, default=VatTreatment.UNDETERMINED
    )
    #: net, VAT and gross in document currency; line-level precision preserved
    net_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    vat_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    gross_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    #: business-use share of this line.  Unlike ``Document.business_use_percent`` (where
    #: NULL means "not stated", migration 0010) the line value is always written: the
    #: API and service default it to 100, and the column has been NOT NULL since 0001.
    business_use_percent: Mapped[Decimal] = mapped_column(
        Numeric(9, 6), nullable=False, default=Decimal("100")
    )
    deductibility_percent: Mapped[Decimal] = mapped_column(
        Numeric(9, 6), nullable=False, default=Decimal("100")
    )
    expense_category: Mapped[str | None] = mapped_column(String(64))

    document: Mapped[Document] = relationship(back_populates="lines")

    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "workspace_id"],
            ["documents.id", "documents.workspace_id"],
            name="fk_document_line_same_workspace",
            ondelete="CASCADE",
        ),
        UniqueConstraint("document_id", "line_number", name="uq_document_line_number"),
    )


class DocumentRevision(UUIDPrimaryKey, Base):
    """Immutable snapshot of a document version (draft edits and corrections)."""

    __tablename__ = "document_revisions"

    workspace_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(300))
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "workspace_id"],
            ["documents.id", "documents.workspace_id"],
            name="fk_document_revision_same_workspace",
        ),
        UniqueConstraint("document_id", "revision", name="uq_document_revision"),
    )


class Attachment(UUIDPrimaryKey, Timestamps, WorkspaceOwned, Base):
    __tablename__ = "attachments"

    document_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    evidence_kind: Mapped[EvidenceKind] = mapped_column(
        enum_column(EvidenceKind), nullable=False, default=EvidenceKind.OTHER
    )
    filename: Mapped[str] = mapped_column(String(300), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    #: opaque object-storage key; never a public URL
    object_key: Mapped[str] = mapped_column(String(400), nullable=False)
    scan_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    uploaded_by_user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "workspace_id"],
            ["documents.id", "documents.workspace_id"],
            name="fk_attachment_same_workspace",
        ),
        Index("ix_attachments_workspace_document", "workspace_id", "document_id"),
    )


class DocumentImport(UUIDPrimaryKey, Timestamps, WorkspaceOwned, Base):
    """Import batch (XML / CSV / reviewed OCR) with duplicate reporting."""

    __tablename__ = "document_imports"

    source: Mapped[DocumentSource] = mapped_column(enum_column(DocumentSource), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    original_filename: Mapped[str | None] = mapped_column(String(300))
    object_key: Mapped[str | None] = mapped_column(String(400))
    detected_duplicates: Mapped[list | None] = mapped_column(JSONB)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
