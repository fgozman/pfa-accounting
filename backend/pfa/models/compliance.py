"""Compliance: rule packs, form versions, obligations, calculations, declarations."""

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
    Base,
    DeclarationStatus,
    NotificationResolution,
    ObligationApplicability,
    Timestamps,
    UUIDPrimaryKey,
    WorkspaceOwned,
    enum_column,
)


class RulePack(UUIDPrimaryKey, Timestamps, Base):
    """A versioned, reviewable set of fiscal parameters and formulas."""

    __tablename__ = "rule_packs"

    code: Mapped[str] = mapped_column(String(64), nullable=False)
    jurisdiction: Mapped[str] = mapped_column(String(8), nullable=False, default="RO")
    income_year: Mapped[int] = mapped_column(nullable=False)
    domain: Mapped[str] = mapped_column(String(24), nullable=False)  # income | vat | deadline
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date)
    publication_date: Mapped[date | None] = mapped_column(Date)
    source_url: Mapped[str | None] = mapped_column(String(500))
    source_snapshot_sha256: Mapped[str | None] = mapped_column(String(64))
    legal_article: Mapped[str | None] = mapped_column(String(200))
    formula_version: Mapped[str] = mapped_column(String(32), nullable=False)
    parameters: Mapped[dict] = mapped_column(JSONB, nullable=False)
    supported_scenarios: Mapped[list | None] = mapped_column(JSONB)
    regression_fixtures: Mapped[dict | None] = mapped_column(JSONB)
    #: draft | pending_review | approved | superseded
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft")
    reviewer: Mapped[str | None] = mapped_column(String(200))
    reviewed_at: Mapped[date | None] = mapped_column(Date)
    #: True only after a Romanian tax-professional review is recorded
    professionally_reviewed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[str | None] = mapped_column(String(1000))

    __table_args__ = (
        UniqueConstraint("code", "version", name="uq_rule_pack_code_version"),
        Index("ix_rule_packs_domain_year", "domain", "income_year", "status"),
    )


class FormVersion(UUIDPrimaryKey, Timestamps, Base):
    """Official form/schema version for one form and one declared year."""

    __tablename__ = "form_versions"

    form_code: Mapped[str] = mapped_column(String(16), nullable=False)
    #: the year whose income/period the form declares (not the filing year)
    income_year: Mapped[int] = mapped_column(nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_format: Mapped[str] = mapped_column(String(24), nullable=False)  # xml | pdf_xml | pdf
    official_schema_url: Mapped[str | None] = mapped_column(String(500))
    official_schema_sha256: Mapped[str | None] = mapped_column(String(64))
    validator_id: Mapped[str | None] = mapped_column(String(200))
    #: api | spv_upload | external_upload | assisted | none
    filing_channel: Mapped[str] = mapped_column(String(32), nullable=False, default="none")
    supported: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: verified upstream endpoint/channel facts; empty means submission disabled
    channel_evidence: Mapped[dict | None] = mapped_column(JSONB)
    correction_strategy: Mapped[str | None] = mapped_column(String(64))
    published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(String(1000))

    __table_args__ = (
        UniqueConstraint(
            "form_code", "income_year", "schema_version", name="uq_form_version_key"
        ),
        Index("ix_form_versions_lookup", "form_code", "income_year"),
    )


class Obligation(UUIDPrimaryKey, Timestamps, WorkspaceOwned, Base):
    """The evaluated compliance state of one form for one period."""

    __tablename__ = "obligations"

    taxpayer_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    form_code: Mapped[str] = mapped_column(String(16), nullable=False)
    form_name_ro: Mapped[str | None] = mapped_column(String(200))
    obligation_kind: Mapped[str] = mapped_column(String(24), nullable=False, default="filing")
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)

    applicability: Mapped[ObligationApplicability] = mapped_column(
        enum_column(ObligationApplicability), nullable=False
    )
    applicability_reason: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    rule_pack_code: Mapped[str | None] = mapped_column(String(64))
    rule_pack_version: Mapped[int | None] = mapped_column(Integer)
    legal_rule_version: Mapped[str | None] = mapped_column(String(64))
    source_url: Mapped[str | None] = mapped_column(String(500))

    #: when the artifact can first be generated (form publication), separate
    #: from the legal filing deadline
    generation_available_from: Mapped[date | None] = mapped_column(Date)
    generation_blocked_reason: Mapped[str | None] = mapped_column(String(500))
    filing_due_date: Mapped[date | None] = mapped_column(Date)
    filing_cutoff_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    filing_timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default="Europe/Bucharest"
    )
    payment_due_date: Mapped[date | None] = mapped_column(Date)
    #: versioned rule that produced the dates above
    deadline_rule_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    deadline_provenance: Mapped[str | None] = mapped_column(String(500))

    missing_inputs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    triggering_evidence: Mapped[list | None] = mapped_column(JSONB)
    declaration_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    resolution_status: Mapped[NotificationResolution] = mapped_column(
        enum_column(NotificationResolution), nullable=False, default=NotificationResolution.OPEN
    )
    filing_resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payment_resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: idempotency for the evaluator: workspace+form+period+revision
    recurrence_key: Mapped[str] = mapped_column(String(200), nullable=False)
    deadline_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    #: supported capability summary: detect/calculate/generate/submit
    capabilities: Mapped[dict | None] = mapped_column(JSONB)
    unsupported_reason: Mapped[str | None] = mapped_column(String(500))

    __table_args__ = (
        UniqueConstraint("workspace_id", "recurrence_key", name="uq_obligation_recurrence"),
        Index("ix_obligations_workspace_due", "workspace_id", "filing_due_date"),
        Index("ix_obligations_workspace_period", "workspace_id", "period_end"),
    )


class CalculationRun(UUIDPrimaryKey, Base):
    __tablename__ = "calculation_runs"

    workspace_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    declaration_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    taxpayer_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    form_code: Mapped[str] = mapped_column(String(16), nullable=False)
    income_year: Mapped[int] = mapped_column(nullable=False)
    rule_pack_code: Mapped[str | None] = mapped_column(String(64))
    rule_pack_version: Mapped[int | None] = mapped_column(Integer)
    engine_version: Mapped[str] = mapped_column(String(32), nullable=False)
    input_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    input_snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    result: Mapped[dict] = mapped_column(JSONB, nullable=False)
    trace: Mapped[list] = mapped_column(JSONB, nullable=False)
    unresolved: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_calculation_runs_declaration", "declaration_id"),
        Index("ix_calculation_runs_workspace_year", "workspace_id", "income_year"),
    )


class Declaration(UUIDPrimaryKey, Timestamps, WorkspaceOwned, Base):
    __tablename__ = "declarations"

    taxpayer_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    form_code: Mapped[str] = mapped_column(String(16), nullable=False)
    form_name_ro: Mapped[str | None] = mapped_column(String(200))
    income_year: Mapped[int] = mapped_column(nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[DeclarationStatus] = mapped_column(
        enum_column(DeclarationStatus), nullable=False, default=DeclarationStatus.DRAFT
    )
    form_version_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    #: the rule pack pinned when the return was first calculated
    rule_pack_code: Mapped[str | None] = mapped_column(String(64))
    rule_pack_version: Mapped[int | None] = mapped_column(Integer)
    #: which identity the form's own instructions require (PFA CUI vs CNP/NIF)
    taxpayer_identity: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    #: the taxpayer is unique per (taxpayer, form, year)
    taxpayer_key: Mapped[str | None] = mapped_column(String(64))
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    correction_of_declaration_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    #: True when the exact year schema is unavailable (estimate/workpaper only)
    estimate_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: ANAF may only be submitted when the adapter capability is verified
    submission_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    submission_unavailable_reason: Mapped[str | None] = mapped_column(String(500))

    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "taxpayer_key",
            "form_code",
            "income_year",
            name="uq_declaration_taxpayer_form_year",
        ),
        Index("ix_declarations_workspace_status", "workspace_id", "status"),
        Index("ix_declarations_workspace_period", "workspace_id", "period_end"),
    )


class DeclarationVersion(UUIDPrimaryKey, Base):
    """An immutable revision of a declaration (accepted filings included)."""

    __tablename__ = "declaration_versions"

    workspace_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    declaration_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[DeclarationStatus] = mapped_column(
        enum_column(DeclarationStatus), nullable=False
    )
    input_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    input_snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    calculation: Mapped[dict] = mapped_column(JSONB, nullable=False)
    trace: Mapped[list] = mapped_column(JSONB, nullable=False)
    calculated_values: Mapped[dict] = mapped_column(JSONB, nullable=False)
    form_schema_version: Mapped[str | None] = mapped_column(String(64))
    rule_pack_code: Mapped[str | None] = mapped_column(String(64))
    rule_pack_version: Mapped[int | None] = mapped_column(Integer)
    artifact_object_key: Mapped[str | None] = mapped_column(String(400))
    artifact_sha256: Mapped[str | None] = mapped_column(String(64))
    artifact_format: Mapped[str | None] = mapped_column(String(24))
    artifact_is_preview: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("declaration_id", "version", name="uq_declaration_version"),
        Index("ix_declaration_versions_declaration", "declaration_id"),
    )


class DeclarationSourceGrant(UUIDPrimaryKey, Timestamps, WorkspaceOwned, Base):
    """Explicit authorization to consolidate another workspace/personal source
    into this declaration.  Storing a source here means its data and attachments
    become visible to every active member of this workspace."""

    __tablename__ = "declaration_source_grants"

    declaration_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    source_workspace_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    taxpayer_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    granted_by_user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    #: the warning the user acknowledged before consolidating personal data
    sharing_notice_version: Mapped[str | None] = mapped_column(String(32))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "declaration_id",
            "source_workspace_id",
            "taxpayer_id",
            name="uq_declaration_source_grant",
        ),
    )


class DeadlineRule(UUIDPrimaryKey, Timestamps, Base):
    """Versioned deadline strategy.  Each form has its own; there is no universal
    "25th" rule."""

    __tablename__ = "deadline_rules"

    form_code: Mapped[str] = mapped_column(String(16), nullable=False)
    jurisdiction: Mapped[str] = mapped_column(String(8), nullable=False, default="RO")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    #: fixed_day | working_days_after_event | calendar_days_after_event | event_registration
    rule_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    parameters: Mapped[dict] = mapped_column(JSONB, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Europe/Bucharest")
    payment_rule_kind: Mapped[str | None] = mapped_column(String(32))
    payment_parameters: Mapped[dict | None] = mapped_column(JSONB)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    source_url: Mapped[str | None] = mapped_column(String(500))
    source_note: Mapped[str | None] = mapped_column(String(500))
    reviewed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint(
            "form_code", "version", "effective_from", name="uq_deadline_rule_version"
        ),
    )


class LegalCalendarDay(UUIDPrimaryKey, Timestamps, Base):
    """Versioned Romanian legal calendar (weekend/holiday/working-day rules)."""

    __tablename__ = "legal_calendar_days"

    day: Mapped[date] = mapped_column(Date, nullable=False)
    is_non_working: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reason: Mapped[str | None] = mapped_column(String(200))
    calendar_version: Mapped[str] = mapped_column(String(32), nullable=False, default="RO-2026.1")
    source_url: Mapped[str | None] = mapped_column(String(500))

    __table_args__ = (
        UniqueConstraint("day", "calendar_version", name="uq_legal_calendar_day"),
    )


class DeductionRule(UUIDPrimaryKey, Timestamps, Base):
    """Parameterised income-tax/VAT deduction rules (limits, categories)."""

    __tablename__ = "deduction_rules"

    code: Mapped[str] = mapped_column(String(64), nullable=False)
    income_year: Mapped[int] = mapped_column(nullable=False)
    domain: Mapped[str] = mapped_column(String(16), nullable=False)  # income_tax | vat
    parameters: Mapped[dict] = mapped_column(JSONB, nullable=False)
    limit_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    legal_reference: Mapped[str | None] = mapped_column(String(200))
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    source_url: Mapped[str | None] = mapped_column(String(500))

    __table_args__ = (
        UniqueConstraint("code", "income_year", name="uq_deduction_rule_code_year"),
    )


class IncomeNorm(UUIDPrimaryKey, Timestamps, Base):
    """Published income norms (norma de venit) per county/locality/activity."""

    __tablename__ = "income_norms"

    income_year: Mapped[int] = mapped_column(nullable=False)
    county: Mapped[str] = mapped_column(String(64), nullable=False)
    locality: Mapped[str | None] = mapped_column(String(128))
    caen_code: Mapped[str] = mapped_column(String(8), nullable=False)
    activity_name: Mapped[str | None] = mapped_column(String(200))
    norm_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    adjustment_factors: Mapped[dict | None] = mapped_column(JSONB)
    source_url: Mapped[str | None] = mapped_column(String(500))
    approved_on: Mapped[date | None] = mapped_column(Date)

    __table_args__ = (
        UniqueConstraint(
            "income_year", "county", "locality", "caen_code", name="uq_income_norm_key"
        ),
        Index("ix_income_norms_lookup", "income_year", "county", "caen_code"),
    )
