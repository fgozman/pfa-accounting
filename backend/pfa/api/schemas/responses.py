"""Typed API responses.

Every handler that returns JSON declares a response model, so the published
OpenAPI document describes what actually comes back instead of ``object``.
That makes ``make types`` produce usable TypeScript, documents the contract for
integrators, and lets FastAPI validate the payload before it leaves the process.

Conventions, matching the specification:

* money and quantities are **decimal strings**, never JSON numbers, so no value is
  ever routed through a binary float;
* dates are ISO 8601 ``YYYY-MM-DD`` strings; instants are ISO 8601 with offset;
* a capability that is unavailable is described (``None`` plus a reason) rather
  than omitted.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Money = str
IsoDate = str
IsoInstant = str


class Response(BaseModel):
    """Base for API payloads: unknown fields are an error in tests, not silently kept."""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Identity and tenancy
# ---------------------------------------------------------------------------


class WorkspaceSummary(Response):
    """A PFA workspace as seen by a member.

    ``accounting_currency`` is the single name used everywhere (it used to be
    ``currency`` on one endpoint and ``accounting_currency`` on another; the typed
    model made the inconsistency visible).
    """

    id: str
    name: str
    cui: str | None = None
    role: Literal["ADMIN", "DEFAULT"] | None = None
    accounting_currency: str | None = None
    timezone: str | None = None


class MeResponse(Response):
    user_id: str
    email: str
    scope: list[str]
    workspaces: list[WorkspaceSummary]


class WorkspaceListResponse(Response):
    items: list[WorkspaceSummary]


class WorkspaceCreatedResponse(Response):
    id: str
    name: str
    cui: str | None = None
    role: Literal["ADMIN", "DEFAULT"]
    accounting_currency: str


class MemberView(Response):
    user_id: str
    email: str | None = None
    role: Literal["ADMIN", "DEFAULT"]
    status: str
    joined_at: IsoInstant


class MemberListResponse(Response):
    items: list[MemberView]


class InvitationView(Response):
    id: str
    email: str
    role: Literal["ADMIN", "DEFAULT"]
    status: str
    expires_at: IsoInstant
    created_at: IsoInstant


class InvitationListResponse(Response):
    items: list[InvitationView]


class InvitationCreatedResponse(Response):
    id: str
    email: str
    role: Literal["ADMIN", "DEFAULT"]
    expires_at: IsoInstant
    email_sent: bool


class InvitationAcceptedResponse(Response):
    workspace_id: str
    role: Literal["ADMIN", "DEFAULT"]
    status: str


# ---------------------------------------------------------------------------
# Documents and settlements
# ---------------------------------------------------------------------------


class DocumentView(Response):
    id: str
    kind: str
    status: str
    series: str | None = None
    number: str | None = None
    description: str | None = None
    issue_date: IsoDate | None = None
    supply_date: IsoDate | None = None
    due_date: IsoDate | None = None
    currency: str
    total_amount: Money
    total_vat: Money
    vat_treatment: str
    deductibility: str
    expense_category: str | None = None
    intracommunity_operation_type: str | None = None
    supplier_country: str | None = None
    supplier_vat_id: str | None = None
    partner_kind: str | None = None
    counterparty_id: str | None = None
    supplier_name_raw: str | None = None
    business_purpose: str | None = None
    deductibility_justification: str | None = None
    reverse_charge_justification: str | None = None
    #: None means the user has not stated the business-use share
    business_use_percent: str | None = None
    revision: int
    external_id: str | None = None
    corrects_document_id: str | None = None
    is_tax_ready: bool


class DocumentLineView(Response):
    line_number: int
    description: str
    quantity: str
    unit_price: Money
    vat_rate: str
    net_amount: Money
    vat_amount: Money
    gross_amount: Money


class SettlementSummary(Response):
    document_id: str
    total: Money
    settled: Money
    outstanding: Money
    paid_in_full: bool
    #: True when the settlements exceed the document total (possible after an edit)
    over_settled: bool = False
    over_amount: Money = "0"


class DocumentDetail(DocumentView):
    lines: list[DocumentLineView]
    settlement: SettlementSummary


class DocumentListResponse(Response):
    items: list[DocumentView]


class PaymentView(Response):
    id: str
    kind: str
    direction: str
    paid_on: IsoDate
    amount: Money
    amount_ron: Money
    is_owner_movement: bool
    reference: str | None = None


class PaymentListResponse(Response):
    items: list[PaymentView]


class PaymentRecordedResponse(Response):
    id: str
    kind: str
    direction: str
    paid_on: IsoDate
    amount: Money
    amount_ron: Money
    is_owner_movement: bool
    note: str | None = None


class AssetView(Response):
    asset_id: str
    name: str
    category: str
    in_service_date: IsoDate
    acquisition_cost: Money
    business_use_percent: str
    method: str
    useful_life_months: int
    depreciated: Money
    book_value: Money
    status: str


class AssetListResponse(Response):
    items: list[AssetView]


class AssetCreatedResponse(Response):
    id: str
    name: str
    status: str
    useful_life_months: int
    note: str


class RegisterResponse(Response):
    register_type: str
    rows: list[dict]
    count: int


class RegisterExportResponse(Response):
    register_export_id: str
    job_id: str
    row_count: int | None = None
    status: str


class CloseChecklistResponse(Response):
    period_start: IsoDate
    period_end: IsoDate
    blockers: dict[str, list[str]]
    ready_to_close: bool


class PeriodLockResponse(Response):
    id: str
    revision: int
    is_open: bool
    checklist: dict | None = None


# ---------------------------------------------------------------------------
# Compliance
# ---------------------------------------------------------------------------


class ObligationCapabilities(Response):
    detect: bool | None = None
    calculate: bool | None = None
    generate: bool | None = None
    submit: bool | None = None
    coverage: str | None = None
    unavailable_reason: str | None = None


class ObligationView(Response):
    id: str
    form_code: str
    form_name_ro: str | None = None
    obligation_kind: str
    period_start: IsoDate
    period_end: IsoDate
    applicability: Literal["required", "not_required", "needs_information"]
    applicability_reason: str
    rule_pack_code: str | None = None
    rule_pack_version: int | None = None
    legal_rule_version: str | None = None
    source_url: str | None = None
    generation_available_from: IsoDate | None = None
    generation_blocked_reason: str | None = None
    filing_due_date: IsoDate | None = None
    payment_due_date: IsoDate | None = None
    deadline_provenance: str | None = None
    deadline_revision: int
    days_remaining: int | None = None
    missing_inputs: list[str]
    triggering_evidence: list[str] | None = None
    capabilities: ObligationCapabilities | None = None
    unsupported_reason: str | None = None
    declaration_id: str | None = None
    resolution_status: str


class UnassessedForm(Response):
    form_code: str
    name_ro: str
    name_en: str
    detection_available: bool
    coverage: str
    reason: str | None = None


class ObligationListResponse(Response):
    items: list[ObligationView]
    #: forms whose detection is not implemented, listed so a missing obligation can
    #: never silently disappear from the dashboard
    unassessed_forms: list[UnassessedForm]
    supported_rule_pack_years: list[int]


class NotificationView(Response):
    id: str
    obligation_id: str
    stage: str
    title_ro: str
    body_ro: str
    due_on: IsoDate | None = None
    payment_due_on: IsoDate | None = None
    days_remaining: int | None = None
    overdue: bool
    read: bool
    dismissed: bool
    snoozed_until: IsoDate | None = None
    snooze_suppressed: bool
    next_step_kind: str | None = None
    next_step_ref: str | None = None
    deadline_revision: int


class NotificationListResponse(Response):
    items: list[NotificationView]


class DeclarationView(Response):
    id: str
    form_code: str
    form_name_ro: str | None = None
    income_year: int
    period_start: IsoDate
    period_end: IsoDate
    status: str
    current_version: int
    rule_pack_code: str | None = None
    rule_pack_version: int | None = None
    taxpayer_identity: dict
    estimate_only: bool
    approved_at: IsoInstant | None = None
    approved_by_user_id: str | None = None
    submitted_at: IsoInstant | None = None
    accepted_at: IsoInstant | None = None
    rejected_at: IsoInstant | None = None
    submission_available: bool
    submission_unavailable_reason: str | None = None
    correction_of_declaration_id: str | None = None
    calculated_values: dict | None = None
    trace: list[dict] | None = None
    artifact_sha256: str | None = None
    artifact_is_preview: bool | None = None


class DeclarationListResponse(Response):
    items: list[DeclarationView]


class DeclarationArtifact(Response):
    object_key: str
    sha256: str
    artifact_format: str
    is_preview: bool
    label_ro: str
    size_bytes: int


class DeclarationPreparedResponse(DeclarationView):
    sharing_notice: str | None = None
    artifact: DeclarationArtifact | None = None
    download_path: str | None = None
    submission_attempt_id: str | None = None
    job_reference: str | None = None
    note: str | None = None


class TraceLine(Response):
    code: str
    label_ro: str
    formula: str
    value: str
    legal_reference: str
    inputs: list[str] = Field(default_factory=list)
    note: str = ""


class DeclarationVersionTrace(Response):
    version_version: int
    created_at: IsoInstant
    rule_pack_code: str | None = None
    rule_pack_version: int | None = None
    form_schema_version: str | None = None
    input_snapshot_sha256: str
    artifact_sha256: str | None = None
    artifact_is_preview: bool | None = None
    trace: list[TraceLine] = Field(default_factory=list)


class CalculationRunTrace(Response):
    id: str
    engine_version: str
    rule_pack_code: str | None = None
    rule_pack_version: int | None = None
    unresolved: list[str] = Field(default_factory=list)
    created_at: IsoInstant


class DeclarationTraceResponse(Response):
    declaration_id: str
    form_code: str
    status: str
    rule_pack: dict
    versions: list[DeclarationVersionTrace]
    calculation_runs: list[CalculationRunTrace]


class ArtifactListResponse(Response):
    items: list[dict]


class ReceiptView(Response):
    id: str
    kind: str
    remote_reference: str | None = None
    verified: bool
    manually_reported: bool
    verified_at: IsoInstant | None = None
    rejection_errors: list[str] | None = None
    created_at: IsoInstant


class ReceiptListResponse(Response):
    items: list[ReceiptView]


class ReceiptImportedResponse(Response):
    id: str
    kind: str
    verified: bool
    manually_reported: bool
    rejection_errors: list[str] | None = None
    note: str | None = None


class DashboardResponse(Response):
    income_year: int
    issued_revenue: Money
    collected_revenue: Money
    documents_count: int
    open_obligations: list[dict]
    #: an internal estimate, explicitly not an ANAF balance
    estimated_taxes: dict


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------


class JobView(Response):
    id: str
    kind: str
    status: str
    attempts: int
    result: dict | None = None
    last_error: str | None = None
    created_at: IsoInstant
    finished_at: IsoInstant | None = None


class AnafConnectionView(Response):
    id: str
    status: str
    environment: str
    connected_at: IsoInstant | None = None
    expires_at: IsoInstant | None = None
    last_error: str | None = None


class AnafCapabilityView(Response):
    form_code: str
    channel: str
    environment: str
    verified_at: IsoInstant | None = None


class AnafStatusResponse(Response):
    connections: list[AnafConnectionView]
    verified_capabilities: list[AnafCapabilityView]
    note: str


class HealthResponse(Response):
    status: str
    version: str


class ReadinessResponse(Response):
    status: str
    environment: str
    database: str
    keys: str


class AcceptedResponse(Response):
    """Generic acknowledgement for endpoints whose work continues asynchronously."""

    status: str
    message: str | None = None
