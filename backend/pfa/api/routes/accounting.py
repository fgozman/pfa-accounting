"""Documents, settlements, assets and registers."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from pydantic import BaseModel, Field

from pfa.api.deps import Principal, idempotency_key, require_scope, workspace_session
from pfa.api.schemas import (
    DocumentDetail,
    DocumentLineView,
    DocumentListResponse,
    DocumentView,
    SettlementSummary,
)
from pfa.errors import ValidationFailed
from pfa.models.base import (
    DeductibilityTreatment,
    DepreciationMethod,
    DocumentKind,
    DocumentSource,
    EvidenceKind,
    IntracommunityOperationType,
    PartnerKind,
    PaymentDirection,
    PaymentKind,
    PaymentMethod,
    VatTreatment,
)
from pfa.services import assets as assets_service
from pfa.services import books as books_service
from pfa.services import documents as documents_service
from pfa.services import settlements as settlements_service
from pfa.services.idempotency import execute_idempotent

router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}", tags=["accounting"])


class LinePayload(BaseModel):
    description: str = Field(max_length=400)
    quantity: Decimal = Decimal("1")
    unit_price: Decimal = Decimal("0")
    vat_rate: Decimal = Decimal("0")
    vat_treatment: VatTreatment = VatTreatment.STANDARD
    discount_amount: Decimal = Decimal("0")
    business_use_percent: Decimal = Decimal("100")
    deductibility_percent: Decimal = Decimal("100")
    expense_category: str | None = None


class FinalizePayload(BaseModel):
    """The series and number entered by the user when the document is issued."""

    series: str | None = Field(default=None, max_length=24)
    number: str | None = Field(default=None, max_length=32)


class DocumentPayload(BaseModel):
    kind: DocumentKind
    counterparty_id: uuid.UUID | None = None
    supplier_name_raw: str | None = None
    #: ISO-3166 alpha-2 supplier country and VAT id (no registered counterparty)
    supplier_country: str | None = Field(default=None, max_length=2)
    supplier_vat_id: str | None = Field(default=None, max_length=32)
    #: B2B/B2C nature of the other party; omitted means "not stated"
    partner_kind: PartnerKind | None = None
    series: str | None = None
    number: str | None = None
    issue_date: date | None = None
    supply_date: date | None = None
    due_date: date | None = None
    currency: str = "RON"
    vat_treatment: VatTreatment = VatTreatment.UNDETERMINED
    deductibility: DeductibilityTreatment = DeductibilityTreatment.UNDETERMINED
    #: omitted means "not stated"; the declaration reports it instead of assuming 100%
    business_use_percent: Decimal | None = None
    business_purpose: str | None = None
    deductibility_justification: str | None = None
    reverse_charge_justification: str | None = None
    expense_category: str | None = None
    intracommunity_operation_type: IntracommunityOperationType | None = None
    external_id: str | None = None
    source: DocumentSource = DocumentSource.MANUAL
    lines: list[LinePayload] = Field(default_factory=list)
    allow_duplicate: bool = False


class DocumentUpdatePayload(DocumentPayload):
    """Draft edit: the caller must state the revision it read (optimistic locking)."""

    expected_revision: int


class PaymentAllocationPayload(BaseModel):
    document_id: uuid.UUID
    amount: Decimal
    allocation_kind: str = "settlement"
    fx_difference_amount: Decimal = Decimal("0")
    note: str | None = None


class PaymentPayload(BaseModel):
    direction: PaymentDirection
    kind: PaymentKind
    paid_on: date
    amount: Decimal
    currency: str = "RON"
    amount_ron: Decimal | None = None
    method: PaymentMethod = PaymentMethod.BANK
    account_id: uuid.UUID | None = None
    reference: str | None = None
    note: str | None = None
    gross_amount: Decimal | None = None
    bank_fee_amount: Decimal = Decimal("0")
    withholding_amount: Decimal = Decimal("0")
    external_id: str | None = None
    allocations: list[PaymentAllocationPayload] = Field(default_factory=list)


class AssetPayload(BaseModel):
    name: str
    category: str
    acquisition_date: date
    in_service_date: date
    acquisition_cost: Decimal
    acquisition_cost_ron: Decimal
    recoverable_vat: Decimal = Decimal("0")
    nonrecoverable_vat: Decimal = Decimal("0")
    currency: str = "RON"
    business_use_percent: Decimal = Decimal("100")
    depreciation_method: DepreciationMethod = DepreciationMethod.STRAIGHT_LINE
    useful_life_months: int | None = None
    document_id: uuid.UUID | None = None


class DisposalPayload(BaseModel):
    disposal_date: date
    proceeds: Decimal = Decimal("0")
    notes: str | None = None


def _document_input(payload: DocumentPayload) -> documents_service.DocumentInput:
    return documents_service.DocumentInput(
        kind=payload.kind,
        counterparty_id=payload.counterparty_id,
        supplier_name_raw=payload.supplier_name_raw,
        supplier_country=payload.supplier_country.upper() if payload.supplier_country else None,
        supplier_vat_id=payload.supplier_vat_id,
        partner_kind=payload.partner_kind,
        series=payload.series,
        number=payload.number,
        issue_date=payload.issue_date,
        supply_date=payload.supply_date,
        due_date=payload.due_date,
        currency=payload.currency,
        vat_treatment=payload.vat_treatment,
        deductibility=payload.deductibility,
        business_use_percent=payload.business_use_percent,
        business_purpose=payload.business_purpose,
        deductibility_justification=payload.deductibility_justification,
        reverse_charge_justification=payload.reverse_charge_justification,
        expense_category=payload.expense_category,
        intracommunity_operation_type=payload.intracommunity_operation_type,
        external_id=payload.external_id,
        source=payload.source,
        lines=tuple(
            documents_service.DocumentLineInput(
                description=line.description,
                quantity=line.quantity,
                unit_price=line.unit_price,
                vat_rate=line.vat_rate,
                vat_treatment=line.vat_treatment,
                discount_amount=line.discount_amount,
                business_use_percent=line.business_use_percent,
                deductibility_percent=line.deductibility_percent,
                expense_category=line.expense_category,
            )
            for line in payload.lines
        ),
    )


def _document_view(document: Any) -> dict:
    return {
        "id": str(document.id),
        "kind": str(document.kind),
        "status": str(document.status),
        "series": document.series,
        "number": document.number,
        # present in list responses (aggregated from the lines), None on writes
        "description": getattr(document, "description", None),
        "issue_date": document.issue_date.isoformat() if document.issue_date else None,
        "supply_date": document.supply_date.isoformat() if document.supply_date else None,
        "due_date": document.due_date.isoformat() if document.due_date else None,
        "currency": document.currency,
        "total_amount": str(document.total_amount),
        "total_vat": str(document.total_vat),
        "vat_treatment": str(document.vat_treatment),
        "deductibility": str(document.deductibility),
        "expense_category": document.expense_category,
        "intracommunity_operation_type": document.intracommunity_operation_type,
        "supplier_country": document.supplier_country,
        "supplier_vat_id": document.supplier_vat_id,
        "partner_kind": document.partner_kind,
        "counterparty_id": str(document.counterparty_id) if document.counterparty_id else None,
        "supplier_name_raw": document.supplier_name_raw,
        "business_purpose": document.business_purpose,
        "deductibility_justification": document.deductibility_justification,
        # the evidence D301 needs: without it the edit form came back empty
        "reverse_charge_justification": document.reverse_charge_justification,
        "business_use_percent": (
            str(document.business_use_percent)
            if document.business_use_percent is not None
            else None
        ),
        "revision": document.revision,
        "external_id": document.external_id,
        "corrects_document_id": (
            str(document.corrects_document_id) if document.corrects_document_id else None
        ),
        "is_tax_ready": document.is_tax_ready,
    }


@router.post("/documents", status_code=status.HTTP_201_CREATED)
async def create_document(
    workspace_id: uuid.UUID,
    payload: DocumentPayload,
    session: Annotated[Any, Depends(workspace_session)],
    principal: Annotated[Principal, Depends(require_scope("pfa.write"))],
) -> DocumentView:
    document = await documents_service.create_document(
        session,
        workspace_id=workspace_id,
        actor_user_id=principal.user_id,
        payload=_document_input(payload),
        allow_duplicate=payload.allow_duplicate,
    )
    return DocumentView(**_document_view(document))


@router.get("/documents")
async def list_documents(
    workspace_id: uuid.UUID,
    session: Annotated[Any, Depends(workspace_session)],
    kind: DocumentKind | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = Query(default=50, le=200),
) -> DocumentListResponse:
    documents = await documents_service.list_documents(
        session,
        workspace_id=workspace_id,
        kind=kind,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )
    return DocumentListResponse(items=[_document_view(document) for document in documents])


@router.get("/documents/{document_id}")
async def get_document(
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    session: Annotated[Any, Depends(workspace_session)],
) -> DocumentDetail:
    document = await documents_service.get_document(
        session, workspace_id=workspace_id, document_id=document_id
    )
    # explicit second statement instead of an ORM relationship
    lines = await documents_service.get_document_lines(session, document.id)
    view = _document_view(document)
    return DocumentDetail(
        **view,
        lines=[
            DocumentLineView(
                line_number=line.line_number,
                description=line.description,
                quantity=str(line.quantity),
                unit_price=str(line.unit_price),
                vat_rate=str(line.vat_rate),
                net_amount=str(line.net_amount),
                vat_amount=str(line.vat_amount),
                gross_amount=str(line.gross_amount),
            )
            for line in lines
        ],
        settlement=SettlementSummary(
            **await settlements_service.document_settlement_summary(
                session, workspace_id=workspace_id, document_id=document_id
            )
        ),
    )


@router.post("/documents/{document_id}/finalize")
async def finalize_document(
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    session: Annotated[Any, Depends(workspace_session)],
    principal: Annotated[Principal, Depends(require_scope("pfa.write"))],
    key: Annotated[str, Depends(idempotency_key)] = "",
    payload: FinalizePayload | None = None,
    expected_revision: int | None = Query(default=None),
) -> dict:
    """Lock the document under the series/number the user entered."""

    async def handler() -> tuple[dict, int, uuid.UUID | None, str | None]:
        document = await documents_service.finalize_document(
            session,
            workspace_id=workspace_id,
            actor_user_id=principal.user_id,
            document_id=document_id,
            series=payload.series if payload else None,
            number=payload.number if payload else None,
            expected_revision=expected_revision,
        )
        return _document_view(document), 200, document.id, "document"

    outcome = await execute_idempotent(
        session,
        workspace_id=workspace_id,
        user_id=principal.user_id,
        action="document.finalize",
        key=key,
        request_payload={
            "document_id": str(document_id),
            "series": payload.series if payload else None,
            "number": payload.number if payload else None,
        },
        handler=handler,
    )
    return outcome.response


@router.patch("/documents/{document_id}")
async def update_document(
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    payload: DocumentUpdatePayload,
    session: Annotated[Any, Depends(workspace_session)],
    principal: Annotated[Principal, Depends(require_scope("pfa.write"))],
) -> DocumentView:
    """Edit a document, draft or issued, keeping the previous state in the revision history."""
    document = await documents_service.update_document(
        session,
        workspace_id=workspace_id,
        actor_user_id=principal.user_id,
        document_id=document_id,
        expected_revision=payload.expected_revision,
        payload=_document_input(payload),
    )
    return DocumentView(**_document_view(document))


@router.delete("/documents/{document_id}")
async def delete_document(
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    session: Annotated[Any, Depends(workspace_session)],
    principal: Annotated[Principal, Depends(require_scope("pfa.write"))],
) -> dict:
    """Delete a draft or an issued document, with everything derived from it."""
    snapshot = await documents_service.delete_document(
        session,
        workspace_id=workspace_id,
        actor_user_id=principal.user_id,
        document_id=document_id,
    )
    return {
        "deleted": True,
        "document_id": str(document_id),
        "series": snapshot.get("series"),
        "number": snapshot.get("number"),
        "payments_removed": snapshot.get("payments_removed", []),
        "allocations_removed": snapshot.get("allocations_removed", 0),
    }


@router.post("/documents/{document_id}/credit-notes", status_code=status.HTTP_201_CREATED)
async def create_credit_note(
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    payload: DocumentPayload,
    session: Annotated[Any, Depends(workspace_session)],
    principal: Annotated[Principal, Depends(require_scope("pfa.write"))],
) -> dict:
    note = await documents_service.create_credit_note(
        session,
        workspace_id=workspace_id,
        actor_user_id=principal.user_id,
        document_id=document_id,
        payload=_document_input(payload),
    )
    return _document_view(note)


@router.post("/documents/{document_id}/attachments", status_code=status.HTTP_201_CREATED)
async def upload_attachment(
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    session: Annotated[Any, Depends(workspace_session)],
    principal: Annotated[Principal, Depends(require_scope("pfa.write"))],
    file: Annotated[UploadFile, File()],
    evidence_kind: Annotated[EvidenceKind, Form()] = EvidenceKind.OTHER,
) -> dict:
    data = await file.read()
    attachment = await documents_service.attach_evidence(
        session,
        workspace_id=workspace_id,
        actor_user_id=principal.user_id,
        document_id=document_id,
        filename=file.filename or "attachment",
        content_type=file.content_type or "application/octet-stream",
        data=data,
        evidence_kind=evidence_kind,
    )
    return {
        "id": str(attachment.id),
        "filename": attachment.filename,
        "content_type": attachment.content_type,
        "size_bytes": attachment.size_bytes,
        "sha256": attachment.sha256,
        "scan_status": attachment.scan_status,
    }


# ---------------------------------------------------------------------------
# Settlements
# ---------------------------------------------------------------------------


@router.post("/payments", status_code=status.HTTP_201_CREATED)
async def record_payment(
    workspace_id: uuid.UUID,
    payload: PaymentPayload,
    session: Annotated[Any, Depends(workspace_session)],
    principal: Annotated[Principal, Depends(require_scope("pfa.write"))],
) -> dict:
    # movements that are not for a document (aport, retrageri, împrumuturi, taxe) are
    # recorded as bank facts only; they must never be allocated to a document
    if payload.kind in settlements_service.NON_DOCUMENT_KINDS and payload.allocations:
        raise ValidationFailed(
            "Aporturile, retragerile, împrumuturile și plățile de taxe nu se alocă "
            "documentelor: banii nu sunt pentru document (Cod fiscal art. 68 alin. (2)).",
            field_errors={"allocations": "not_allowed"},
        )
    payment = await settlements_service.record_payment(
        session,
        workspace_id=workspace_id,
        actor_user_id=principal.user_id,
        payload=settlements_service.PaymentInput(
            direction=payload.direction,
            kind=payload.kind,
            paid_on=payload.paid_on,
            amount=payload.amount,
            currency=payload.currency,
            amount_ron=payload.amount_ron,
            method=payload.method,
            account_id=payload.account_id,
            reference=payload.reference,
            note=payload.note,
            gross_amount=payload.gross_amount,
            bank_fee_amount=payload.bank_fee_amount,
            withholding_amount=payload.withholding_amount,
            external_id=payload.external_id,
            allocations=tuple(
                settlements_service.AllocationInput(
                    document_id=allocation.document_id,
                    amount=allocation.amount,
                    allocation_kind=allocation.allocation_kind,
                    fx_difference_amount=allocation.fx_difference_amount,
                    note=allocation.note,
                )
                for allocation in payload.allocations
            ),
        ),
    )
    return {
        "id": str(payment.id),
        "kind": str(payment.kind),
        "direction": str(payment.direction),
        "paid_on": payment.paid_on.isoformat(),
        "amount": str(payment.amount),
        "amount_ron": str(payment.amount_ron or payment.amount),
        "is_owner_movement": payment.is_owner_movement,
        "note": (
            settlements_service.registration_rule(payload.kind)
            if payment.kind in settlements_service.NON_DOCUMENT_KINDS
            else None
        ),
    }


@router.get("/payments")
async def list_payments(
    workspace_id: uuid.UUID,
    session: Annotated[Any, Depends(workspace_session)],
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict:
    payments = await settlements_service.list_settlements(
        session, workspace_id=workspace_id, date_from=date_from, date_to=date_to
    )
    return {
        "items": [
            {
                "id": str(payment.id),
                "kind": str(payment.kind),
                "direction": str(payment.direction),
                "paid_on": payment.paid_on.isoformat(),
                "amount": str(payment.amount),
                "amount_ron": str(payment.amount_ron or payment.amount),
                "is_owner_movement": payment.is_owner_movement,
                "reference": payment.reference,
                "method": str(payment.method),
                "note": payment.note,
                "allocations": await settlements_service.list_allocations(
                    session, workspace_id=workspace_id, payment_id=payment.id
                ),
            }
            for payment in payments
        ]
    }


class SettlementUpdatePayload(BaseModel):
    """The settlement fields a user may correct; omitted fields stay unchanged."""

    paid_on: date | None = None
    amount: Decimal | None = None
    method: PaymentMethod | None = None
    reference: str | None = None
    note: str | None = None


@router.patch("/payments/{payment_id}")
async def update_payment(
    workspace_id: uuid.UUID,
    payment_id: uuid.UUID,
    payload: SettlementUpdatePayload,
    session: Annotated[Any, Depends(workspace_session)],
    principal: Annotated[Principal, Depends(require_scope("pfa.write"))],
) -> dict:
    """Correct an already recorded receipt or payment.

    When it is applied to a single document, the allocation follows the corrected amount,
    so the receipt keeps matching the invoice it pays.
    """
    return await settlements_service.update_settlement(
        session,
        workspace_id=workspace_id,
        actor_user_id=principal.user_id,
        payment_id=payment_id,
        fields=settlements_service.SettlementUpdate(
            paid_on=payload.paid_on,
            amount=payload.amount,
            method=payload.method,
            reference=payload.reference,
            note=payload.note,
        ),
    )


@router.delete("/payments/{payment_id}")
async def delete_payment(
    workspace_id: uuid.UUID,
    payment_id: uuid.UUID,
    session: Annotated[Any, Depends(workspace_session)],
    principal: Annotated[Principal, Depends(require_scope("pfa.write"))],
) -> dict:
    """Remove a receipt or payment, releasing what it was allocated to."""
    return await settlements_service.delete_settlement(
        session,
        workspace_id=workspace_id,
        actor_user_id=principal.user_id,
        payment_id=payment_id,
    )


@router.get("/documents/{document_id}/settlement")
async def document_settlement(
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    session: Annotated[Any, Depends(workspace_session)],
) -> dict:
    return await settlements_service.document_settlement_summary(
        session, workspace_id=workspace_id, document_id=document_id
    )


# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------


@router.post("/assets", status_code=status.HTTP_201_CREATED)
async def create_asset(
    workspace_id: uuid.UUID,
    payload: AssetPayload,
    session: Annotated[Any, Depends(workspace_session)],
    principal: Annotated[Principal, Depends(require_scope("pfa.write"))],
) -> dict:
    asset = await assets_service.create_asset(
        session,
        workspace_id=workspace_id,
        actor_user_id=principal.user_id,
        payload=assets_service.AssetInput(
            name=payload.name,
            category=payload.category,
            acquisition_date=payload.acquisition_date,
            in_service_date=payload.in_service_date,
            acquisition_cost=payload.acquisition_cost,
            acquisition_cost_ron=payload.acquisition_cost_ron,
            recoverable_vat=payload.recoverable_vat,
            nonrecoverable_vat=payload.nonrecoverable_vat,
            currency=payload.currency,
            business_use_percent=payload.business_use_percent,
            depreciation_method=payload.depreciation_method,
            useful_life_months=payload.useful_life_months
            or assets_service.default_useful_life(payload.category),
            document_id=payload.document_id,
        ),
    )
    return {
        "id": str(asset.id),
        "name": asset.name,
        "status": str(asset.status),
        "useful_life_months": asset.useful_life_months,
        "note": (
            "Cheltuiala nu se deduce integral: activul se amortizează, iar amortizarea "
            "intră în calculul impozitului."
        ),
    }


@router.get("/assets")
async def list_assets(
    workspace_id: uuid.UUID,
    session: Annotated[Any, Depends(workspace_session)],
) -> dict:
    return {"items": await books_service.asset_register(session, workspace_id=workspace_id)}


@router.post("/assets/{asset_id}/dispose")
async def dispose_asset(
    workspace_id: uuid.UUID,
    asset_id: uuid.UUID,
    payload: DisposalPayload,
    session: Annotated[Any, Depends(workspace_session)],
    principal: Annotated[Principal, Depends(require_scope("pfa.write"))],
) -> dict:
    asset = await assets_service.dispose_asset(
        session,
        workspace_id=workspace_id,
        actor_user_id=principal.user_id,
        asset_id=asset_id,
        disposal_date=payload.disposal_date,
        proceeds=payload.proceeds,
        notes=payload.notes,
    )
    return {"id": str(asset.id), "status": str(asset.status)}


# ---------------------------------------------------------------------------
# Registers and period close
# ---------------------------------------------------------------------------


@router.post("/registers/{register_type}/export", status_code=status.HTTP_202_ACCEPTED)
async def export_register(
    workspace_id: uuid.UUID,
    register_type: str,
    session: Annotated[Any, Depends(workspace_session)],
    principal: Annotated[Principal, Depends(require_scope("pfa.write"))],
    period_start: date,
    period_end: date,
    income_year: int | None = None,
) -> dict:
    export, job_id = await books_service.export_register(
        session,
        workspace_id=workspace_id,
        actor_user_id=principal.user_id,
        register_type=register_type,
        period_start=period_start,
        period_end=period_end,
        income_year=income_year,
    )
    return {
        "register_export_id": str(export.id),
        "job_id": str(job_id),
        "row_count": export.row_count,
        "status": export.status,
    }


class PeriodClosePayload(BaseModel):
    period_start: date
    period_end: date
    period_kind: str = "month"
    force: bool = False


class PeriodReopenPayload(BaseModel):
    reason: str = Field(min_length=10, max_length=500)


@router.get("/registers/close-checklist")
async def close_checklist(
    workspace_id: uuid.UUID,
    session: Annotated[Any, Depends(workspace_session)],
    period_start: date,
    period_end: date,
) -> dict:
    return await books_service.close_checklist(
        session, workspace_id=workspace_id, period_start=period_start, period_end=period_end
    )


@router.get("/registers/{register_type}")
async def get_register(
    workspace_id: uuid.UUID,
    register_type: str,
    session: Annotated[Any, Depends(workspace_session)],
    period_start: date,
    period_end: date,
    income_year: int | None = None,
) -> dict:
    rows = await books_service.register_rows(
        session,
        workspace_id=workspace_id,
        register_type=register_type,
        period_start=period_start,
        period_end=period_end,
        income_year=income_year,
    )
    return {"register_type": register_type, "rows": rows, "count": len(rows)}


@router.post("/periods/close")
async def close_period(
    workspace_id: uuid.UUID,
    payload: PeriodClosePayload,
    session: Annotated[Any, Depends(workspace_session)],
    principal: Annotated[Principal, Depends(require_scope("pfa.write"))],
) -> dict:
    lock = await books_service.close_period(
        session,
        workspace_id=workspace_id,
        actor_user_id=principal.user_id,
        period_start=payload.period_start,
        period_end=payload.period_end,
        period_kind=payload.period_kind,
        force=payload.force,
    )
    return {
        "id": str(lock.id),
        "revision": lock.revision,
        "is_open": lock.is_open,
        "checklist": lock.close_checklist,
    }


@router.post("/periods/{period_lock_id}/reopen")
async def reopen_period(
    workspace_id: uuid.UUID,
    period_lock_id: uuid.UUID,
    payload: PeriodReopenPayload,
    session: Annotated[Any, Depends(workspace_session)],
    principal: Annotated[Principal, Depends(require_scope("pfa.write"))],
) -> dict:
    lock = await books_service.reopen_period(
        session,
        workspace_id=workspace_id,
        actor_user_id=principal.user_id,
        period_lock_id=period_lock_id,
        reason=payload.reason,
    )
    return {"id": str(lock.id), "revision": lock.revision, "is_open": lock.is_open}
