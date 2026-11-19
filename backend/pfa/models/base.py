"""Declarative base, shared mixins and enumerations."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Numeric, func, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Monetary values: NUMERIC(20, 6).  Never binary floating point.
MONEY = Numeric(20, 6)
# Exchange rates need higher explicit precision.
RATE = Numeric(28, 12)
# Percentages stored as exact decimals (e.g. 21.000000 for 21%).
PERCENT = Numeric(9, 6)


class Base(DeclarativeBase):
    pass


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )


class UUIDPrimaryKey:
    id: Mapped[uuid.UUID] = uuid_pk()


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


def workspace_fk() -> Mapped[uuid.UUID]:
    return mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )


class WorkspaceOwned:
    """Every workspace-owned row carries ``workspace_id``.

    Composite foreign keys ``(parent_id, workspace_id)`` prevent cross-workspace
    references; row-level security provides defence in depth.
    """

    workspace_id: Mapped[uuid.UUID] = workspace_fk()


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class StrEnum(str, enum.Enum):
    def __str__(self) -> str:  # pragma: no cover - convenience
        return str(self.value)


def enum_column(enum_cls: type[enum.Enum], *, length: int = 40) -> SAEnum:
    """VARCHAR + CHECK constraint (avoids painful native-enum migrations)."""
    return SAEnum(
        enum_cls,
        native_enum=False,
        length=length,
        values_callable=lambda cls: [m.value for m in cls],
        validate_strings=True,
    )


class UserStatus(StrEnum):
    PENDING_VERIFICATION = "pending_verification"
    ACTIVE = "active"
    DISABLED = "disabled"


class MembershipRole(StrEnum):
    ADMIN = "ADMIN"
    DEFAULT = "DEFAULT"


class MembershipStatus(StrEnum):
    ACTIVE = "active"
    REMOVED = "removed"


class InvitationStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REVOKED = "revoked"


class TaxRegimeKind(StrEnum):
    REAL = "real"
    NORM = "norm"


class VatRegistrationKind(StrEnum):
    NONE = "none"
    ARTICLE_316 = "article_316"
    ARTICLE_317 = "article_317"


class IntracommunityOperationType(StrEnum):
    """D390/D394 operation type.

    L livrări de bunuri, T livrări în operațiune triunghiulară, A achiziții de
    bunuri, P prestări de servicii, S achiziții de servicii, R regim special
    pentru agricultori.
    """

    GOODS_SUPPLY = "L"
    TRIANGULAR_SUPPLY = "T"
    GOODS_ACQUISITION = "A"
    SERVICES_SUPPLY = "P"
    SERVICES_ACQUISITION = "S"
    AGRICULTURAL_SUPPLY = "R"


class DocumentKind(StrEnum):
    SALES_INVOICE = "sales_invoice"
    PURCHASE_INVOICE = "purchase_invoice"
    CREDIT_NOTE = "credit_note"
    RECEIPT = "receipt"
    OTHER = "other"


class DocumentStatus(StrEnum):
    """A document exists or it does not: deleting removes the row (no retired state)."""

    DRAFT = "draft"
    FINALIZED = "finalized"


class DocumentSource(StrEnum):
    MANUAL = "manual"
    XML_IMPORT = "xml_import"
    CSV_IMPORT = "csv_import"
    OCR = "ocr"
    EFACTURA = "efactura"


class DeductibilityTreatment(StrEnum):
    FULLY_DEDUCTIBLE = "fully_deductible"
    PARTIALLY_DEDUCTIBLE = "partially_deductible"
    NON_DEDUCTIBLE = "non_deductible"
    UNDETERMINED = "undetermined"


class VatTreatment(StrEnum):
    STANDARD = "standard"
    REDUCED = "reduced"
    SECOND_REDUCED = "second_reduced"
    EXEMPT = "exempt"
    REVERSE_CHARGE = "reverse_charge"
    OUT_OF_SCOPE = "out_of_scope"
    NON_DEDUCTIBLE = "non_deductible"
    UNDETERMINED = "undetermined"


class PartnerKind(StrEnum):
    """Who the other party is.  ``None`` on a document means it was never stated.

    A B2C sale is complete without partner data; a B2B sale is not.  The distinction
    is recorded, never inferred from the absence of a name.
    """

    BUSINESS = "business"
    CONSUMER = "consumer"


class PaymentDirection(StrEnum):
    INFLOW = "inflow"
    OUTFLOW = "outflow"


class PaymentMethod(StrEnum):
    BANK = "bank"
    CASH = "cash"
    CARD = "card"
    PROCESSOR = "processor"
    NON_CASH = "non_cash"
    OTHER = "other"


class PaymentKind(StrEnum):
    CUSTOMER_RECEIPT = "customer_receipt"
    SUPPLIER_PAYMENT = "supplier_payment"
    OWNER_CONTRIBUTION = "owner_contribution"
    OWNER_WITHDRAWAL = "owner_withdrawal"
    OWNER_TRANSFER = "owner_transfer"
    LOAN_RECEIVED = "loan_received"
    LOAN_REPAID = "loan_repaid"
    BANK_FEE = "bank_fee"
    TAX_PAYMENT = "tax_payment"
    REFUND = "refund"
    ADVANCE = "advance"
    OTHER = "other"


class AssetStatus(StrEnum):
    DRAFT = "draft"
    IN_SERVICE = "in_service"
    DISPOSED = "disposed"
    TRANSFERRED = "transferred"


class DepreciationMethod(StrEnum):
    STRAIGHT_LINE = "straight_line"
    DECLINING = "declining"
    ACCELERATED = "accelerated"
    NO_DEPRECIATION = "no_depreciation"


class ObligationApplicability(StrEnum):
    REQUIRED = "required"
    NOT_REQUIRED = "not_required"
    NEEDS_INFORMATION = "needs_information"


class DeclarationStatus(StrEnum):
    DRAFT = "draft"
    CALCULATED = "calculated"
    VALIDATED = "validated"
    APPROVED = "approved"
    EXPORTED = "exported"
    AWAITING_EXTERNAL_FILING = "awaiting_external_filing"
    QUEUED = "queued"
    SUBMITTED = "submitted"
    SUBMISSION_UNKNOWN = "submission_unknown"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class JobStatus(StrEnum):
    PENDING = "pending"
    LEASED = "leased"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD = "dead"


class SubmissionChannel(StrEnum):
    API = "api"
    SPV_UPLOAD = "spv_upload"
    EXTERNAL_UPLOAD = "external_upload"
    ASSISTED = "assisted"


class NotificationStage(StrEnum):
    DETECTED = "detected"
    REMINDER = "reminder"
    DUE = "due"
    OVERDUE = "overdue"
    REJECTED = "rejected"
    DEADLINE_REVISED = "deadline_revised"


class NotificationResolution(StrEnum):
    OPEN = "open"
    FILED = "filed"
    PAID = "paid"
    WITHDRAWN = "withdrawn"


class EvidenceKind(StrEnum):
    RECEIPT = "receipt"
    INVOICE_PDF = "invoice_pdf"
    CONTRACT = "contract"
    BANK_STATEMENT = "bank_statement"
    IDENTITY = "identity"
    OTHER = "other"


__all__ = [
    "MONEY",
    "PERCENT",
    "RATE",
    "AssetStatus",
    "Base",
    "DeclarationStatus",
    "DeductibilityTreatment",
    "DepreciationMethod",
    "DocumentKind",
    "DocumentSource",
    "DocumentStatus",
    "EvidenceKind",
    "IntracommunityOperationType",
    "InvitationStatus",
    "JobStatus",
    "MembershipRole",
    "MembershipStatus",
    "NotificationResolution",
    "NotificationStage",
    "ObligationApplicability",
    "PaymentDirection",
    "PaymentKind",
    "PaymentMethod",
    "StrEnum",
    "SubmissionChannel",
    "TaxRegimeKind",
    "Timestamps",
    "UUIDPrimaryKey",
    "UserStatus",
    "VatRegistrationKind",
    "VatTreatment",
    "WorkspaceOwned",
    "enum_column",
    "uuid_pk",
    "workspace_fk",
]
