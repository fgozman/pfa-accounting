"""Documents: creation, user-entered numbering, corrections, imports, evidence.

The series and number of a document are facts of the issued paper: the user enters them,
the application never invents one.  Uniqueness per workspace is enforced, and deleting a
document removes it — the append-only audit event keeps the record that it existed.

Issued invoices are immutable: corrections use linked credit notes.  Cancelled
numbers stay in the audit trail.  A PDF is a human-readable rendition; the
e-Factura XML is a separate artifact.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pfa import clock
from pfa.config import Settings, get_settings
from pfa.errors import (
    BlockingInputs,
    Conflict,
    NotFound,
    StaleRevision,
    ValidationFailed,
)
from pfa.models.anaf import SubmissionAttempt
from pfa.models.base import (
    DeclarationStatus,
    DeductibilityTreatment,
    DocumentKind,
    DocumentSource,
    DocumentStatus,
    EvidenceKind,
    IntracommunityOperationType,
    PartnerKind,
    VatTreatment,
)
from pfa.models.compliance import Declaration
from pfa.repo import books as books_repo
from pfa.repo import documents as repo
from pfa.services.audit import emit_event, record_audit
from pfa.services.storage import ObjectStore, scan_bytes, validate_attachment


@dataclass(frozen=True, slots=True)
class DocumentLineInput:
    description: str
    quantity: Decimal
    unit_price: Decimal
    vat_rate: Decimal = Decimal("0")
    vat_treatment: VatTreatment = VatTreatment.STANDARD
    discount_amount: Decimal = Decimal("0")
    business_use_percent: Decimal = Decimal("100")
    deductibility_percent: Decimal = Decimal("100")
    expense_category: str | None = None


@dataclass(frozen=True, slots=True)
class DocumentInput:
    kind: DocumentKind
    counterparty_id: uuid.UUID | None = None
    series: str | None = None
    number: str | None = None
    issue_date: date | None = None
    supply_date: date | None = None
    due_date: date | None = None
    currency: str = "RON"
    vat_treatment: VatTreatment = VatTreatment.UNDETERMINED
    deductibility: DeductibilityTreatment = DeductibilityTreatment.UNDETERMINED
    #: None means "not stated": never silently treated as 100%
    business_use_percent: Decimal | None = None
    business_purpose: str | None = None
    deductibility_justification: str | None = None
    reverse_charge_justification: str | None = None
    expense_category: str | None = None
    #: supplier country / VAT id when there is no registered counterparty
    supplier_country: str | None = None
    supplier_vat_id: str | None = None
    #: B2B/B2C nature of the other party (None = not stated)
    partner_kind: PartnerKind | None = None
    #: D390/D394 operation type (L/T/A/P/S/R) for intra-community operations
    intracommunity_operation_type: IntracommunityOperationType | None = None
    source: DocumentSource = DocumentSource.MANUAL
    external_id: str | None = None
    supplier_name_raw: str | None = None
    lines: tuple[DocumentLineInput, ...] = ()
    corrects_document_id: uuid.UUID | None = None
    correction_reason: str | None = None


#: treatments that carry a statutory VAT rate
TAXABLE_VAT_TREATMENTS = frozenset(
    {VatTreatment.STANDARD, VatTreatment.REDUCED, VatTreatment.SECOND_REDUCED}
)


def _assert_taxable_document_has_vat(
    payload: DocumentInput, total_net: Decimal, total_vat: Decimal
) -> None:
    """A document declared taxable must record its VAT rate.

    Without the rate the document holds no VAT, while the declaration applies the
    statutory rate from the rule pack — the invoice and the declaration would then
    disagree.  The rate comes from the document; it is never silently assumed.
    """
    if payload.vat_treatment in TAXABLE_VAT_TREATMENTS and total_vat == 0 and total_net != 0:
        raise ValidationFailed(
            "Cota de TVA este obligatorie pentru un document taxabil: fără ea documentul nu "
            "conține TVA, în timp ce declarația ar calcula TVA la cota legală. Completați cota "
            "de pe factură sau schimbați tratamentul TVA.",
            field_errors={"lines": "vat_rate_required"},
        )


def _line_amounts(line: DocumentLineInput) -> tuple[Decimal, Decimal, Decimal]:
    net = (line.quantity * line.unit_price) - line.discount_amount
    net = net.quantize(Decimal("0.000001"))
    if line.vat_treatment in {VatTreatment.EXEMPT, VatTreatment.OUT_OF_SCOPE, VatTreatment.REVERSE_CHARGE}:
        rate = Decimal("0")
    else:
        rate = line.vat_rate
    vat = (net * rate / Decimal("100")).quantize(Decimal("0.000001"))
    return net, vat, net + vat


def content_hash(payload: DocumentInput) -> str:
    canonical = json.dumps(
        {
            "kind": str(payload.kind),
            "counterparty_id": str(payload.counterparty_id) if payload.counterparty_id else None,
            # normalised as stored, so the same document always hashes the same way
            "series": (payload.series or "").strip().upper() or None,
            "number": (payload.number or "").strip() or None,
            "issue_date": payload.issue_date.isoformat() if payload.issue_date else None,
            "currency": payload.currency,
            "supplier_name_raw": payload.supplier_name_raw,
            "lines": [
                {
                    "description": line.description,
                    "quantity": str(line.quantity),
                    "unit_price": str(line.unit_price),
                    "discount": str(line.discount_amount),
                    "vat_rate": str(line.vat_rate),
                }
                for line in payload.lines
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def find_duplicates(
    session: AsyncSession, *, workspace_id: uuid.UUID, payload: DocumentInput
) -> list:
    """Duplicate detection on external identifier, issuer/series/number/date and hash."""
    return await repo.find_duplicates(
        session,
        workspace_id=workspace_id,
        kind=str(payload.kind),
        external_id=payload.external_id,
        # normalised exactly as it is stored, so the comparison cannot miss its own row
        series=(payload.series or "").strip().upper() or None,
        number=(payload.number or "").strip() or None,
        issue_date=payload.issue_date,
        counterparty_id=payload.counterparty_id,
        supplier_name_raw=payload.supplier_name_raw,
        content_hash=content_hash(payload),
    )


async def create_document(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    payload: DocumentInput,
    allow_duplicate: bool = False,
):
    day = payload.issue_date or clock.today()
    await assert_period_open(session, workspace_id=workspace_id, day=day)

    duplicates = await find_duplicates(session, workspace_id=workspace_id, payload=payload)
    if duplicates and not allow_duplicate:
        raise Conflict(
            "Documentul pare a fi deja înregistrat (duplicat detectat).",
            details={
                "duplicates": [
                    {"id": str(item.id), "number": item.number, "issue_date": str(item.issue_date)}
                    for item in duplicates
                ],
                "review_path": "POST resubmission with allow_duplicate=true after review",
            },
        )

    if payload.external_id:
        # Unique import identifiers are a database guarantee, not just a service
        # check: even an explicitly reviewed duplicate must use its own key.
        same_external = await repo.find_by_external_id(
            session, workspace_id=workspace_id, external_id=payload.external_id
        )
        if same_external is not None:
            raise Conflict(
                "Identificatorul extern este deja folosit în acest spațiu de lucru.",
                details={
                    "existing_document_id": str(same_external.id),
                    "field": "external_id",
                },
            )

    # an issued invoice's series/number is unique in the workspace from the moment it is
    # recorded, so the user gets a clear conflict instead of a database error
    if payload.kind in {DocumentKind.SALES_INVOICE, DocumentKind.CREDIT_NOTE}:
        series = (payload.series or "").strip().upper()
        number = (payload.number or "").strip()
        if number:
            await _assert_number_is_available(
                session, workspace_id=workspace_id, series=series, number=number
            )

    if payload.corrects_document_id is not None:
        corrected = await get_document(
            session, workspace_id=workspace_id, document_id=payload.corrects_document_id
        )
        if corrected.status != DocumentStatus.FINALIZED:
            raise Conflict("Doar documentele finalizate pot fi corectate.")
        if payload.kind != DocumentKind.CREDIT_NOTE:
            raise ValidationFailed("Corecția trebuie să fie o notă de credit (storno).")

    prepared = [_line_amounts(line) for line in payload.lines]
    total_net = sum((amounts[0] for amounts in prepared), Decimal("0"))
    total_vat = sum((amounts[1] for amounts in prepared), Decimal("0"))
    _assert_taxable_document_has_vat(payload, total_net, total_vat)

    document = await repo.insert_document(
        session,
        workspace_id=workspace_id,
        values={
            "kind": payload.kind.value if hasattr(payload.kind, "value") else str(payload.kind),
            "status": DocumentStatus.DRAFT.value,
            "source": payload.source.value if hasattr(payload.source, "value") else str(payload.source),
            "counterparty_id": payload.counterparty_id,
            "supplier_name_raw": payload.supplier_name_raw,
            "series": (payload.series or "").strip().upper() or None,
            "number": (payload.number or "").strip() or None,
            "issue_date": payload.issue_date,
            "supply_date": payload.supply_date,
            "due_date": payload.due_date,
            "currency": payload.currency,
            "total_amount": (total_net + total_vat).quantize(Decimal("0.000001")),
            "total_vat": total_vat.quantize(Decimal("0.000001")),
            "vat_treatment": str(payload.vat_treatment),
            "deductibility": str(payload.deductibility),
            "business_use_percent": payload.business_use_percent,
            "business_purpose": payload.business_purpose,
            "deductibility_justification": payload.deductibility_justification,
            "reverse_charge_justification": payload.reverse_charge_justification,
            "expense_category": payload.expense_category,
            "supplier_country": (payload.supplier_country or None),
            "supplier_vat_id": (payload.supplier_vat_id or None),
            "partner_kind": payload.partner_kind.value if payload.partner_kind else None,
            "intracommunity_operation_type": (
                payload.intracommunity_operation_type.value
                if payload.intracommunity_operation_type is not None
                else None
            ),
            "external_id": payload.external_id,
            "content_hash": content_hash(payload),
            "corrects_document_id": payload.corrects_document_id,
            "correction_reason": payload.correction_reason,
            "created_by_user_id": actor_user_id,
        },
    )
    for index, (line_input, amounts) in enumerate(zip(payload.lines, prepared, strict=True), start=1):
        net, vat, gross = amounts
        await repo.insert_line(
            session,
            workspace_id=workspace_id,
            document_id=document.id,
            line_number=index,
            description=line_input.description,
            quantity=line_input.quantity,
            unit_price=line_input.unit_price,
            discount_amount=line_input.discount_amount,
            vat_rate=line_input.vat_rate,
            vat_treatment=str(line_input.vat_treatment),
            net_amount=net,
            vat_amount=vat,
            gross_amount=gross,
            business_use_percent=line_input.business_use_percent,
            deductibility_percent=line_input.deductibility_percent,
            expense_category=line_input.expense_category,
        )
    await record_audit(
        session,
        action="document.created",
        entity_type="document",
        entity_id=document.id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        after={"kind": str(document.kind), "total": str(document.total_amount)},
    )
    return document


async def get_document(
    session: AsyncSession, *, workspace_id: uuid.UUID, document_id: uuid.UUID, with_lines: bool = False
):
    document = await repo.get_document(
        session, workspace_id=workspace_id, document_id=document_id
    )
    if document is None:
        raise NotFound("Documentul nu există în acest spațiu de lucru.")
    return document


async def get_document_lines(session: AsyncSession, document_id: uuid.UUID) -> list:
    return await repo.list_lines(session, document_id)


async def assert_period_open(session: AsyncSession, *, workspace_id: uuid.UUID, day: date) -> None:
    lock = await repo.period_is_closed(session, workspace_id=workspace_id, day=day)
    if lock is not None:
        raise Conflict(
            f"Perioada {lock.period_start}–{lock.period_end} este închisă. "
            "Redeschideți perioada cu un motiv înainte de a modifica documente."
        )


async def _assert_number_is_available(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    series: str,
    number: str,
    document_id: uuid.UUID | None = None,
) -> None:
    """Unique series/number for the invoices the PFA issues, checked before the database.

    A purchase keeps the supplier's number, which is only unique per supplier, so it is
    not checked here; duplicate detection compares the partner for those.
    """
    duplicate = await repo.find_by_series_number(
        session,
        workspace_id=workspace_id,
        series=series,
        number=number,
        exclude_document_id=document_id,
    )
    if duplicate is not None:
        label = f"seria „{series}” și " if series else ""
        raise Conflict(
            f"Există deja un document cu {label}numărul „{number}” în acest spațiu de lucru.",
            details={
                "existing_document_id": str(duplicate.id),
                "existing_status": str(duplicate.status),
                "field": "number",
            },
        )


async def update_document(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    document_id: uuid.UUID,
    expected_revision: int,
    payload: DocumentInput,
):
    """Edit a document, draft or already issued.

    Editing an issued document is allowed because the series, the number and the dates are
    the user's own facts, but it is not silent: the previous state is kept in the
    append-only revision history, and two things still stop it, because they would change
    something that has already left the application — a document inside a generated or
    filed declaration, and an invoice already transmitted through RO e-Factura.
    """
    document = await get_document(session, workspace_id=workspace_id, document_id=document_id)
    if document.revision != expected_revision:
        raise StaleRevision(
            "Documentul a fost modificat între timp. Reîncărcați datele.",
            details={"current_revision": document.revision},
        )
    issued = document.status == DocumentStatus.FINALIZED
    if issued:
        await _assert_not_in_locked_declaration(session, workspace_id=workspace_id, document=document)
        await _assert_not_transmitted(session, workspace_id=workspace_id, document=document)
    lines = await repo.list_lines(session, document.id)
    await repo.snapshot_revision(
        session,
        workspace_id=workspace_id,
        document_id=document.id,
        revision=document.revision,
        reason="issued_edit" if issued else "draft_edit",
        snapshot=_snapshot(document, lines),
        created_by_user_id=actor_user_id,
    )
    prepared = [_line_amounts(line) for line in payload.lines]
    total_net = sum((amounts[0] for amounts in prepared), Decimal("0"))
    total_vat = sum((amounts[1] for amounts in prepared), Decimal("0"))
    _assert_taxable_document_has_vat(payload, total_net, total_vat)
    if document.kind in {DocumentKind.SALES_INVOICE, DocumentKind.CREDIT_NOTE}:
        series = (payload.series or "").strip().upper()
        number = (payload.number or "").strip()
        if number:
            await _assert_number_is_available(
                session,
                workspace_id=workspace_id,
                series=series,
                number=number,
                document_id=document.id,
            )
    document = await repo.update_document_fields(
        session,
        document_id=document.id,
        fields={
            # the series/number are the user's: editable while the document is a draft
            "series": (payload.series or "").strip().upper() or None,
            "number": (payload.number or "").strip() or None,
            "counterparty_id": payload.counterparty_id,
            "supplier_name_raw": payload.supplier_name_raw,
            "supplier_country": payload.supplier_country,
            "supplier_vat_id": payload.supplier_vat_id,
            "partner_kind": payload.partner_kind.value if payload.partner_kind else None,
            "issue_date": payload.issue_date,
            "supply_date": payload.supply_date,
            "due_date": payload.due_date,
            "currency": payload.currency,
            "vat_treatment": str(payload.vat_treatment),
            "deductibility": str(payload.deductibility),
            "business_use_percent": payload.business_use_percent,
            "business_purpose": payload.business_purpose,
            "deductibility_justification": payload.deductibility_justification,
            "reverse_charge_justification": payload.reverse_charge_justification,
            "expense_category": payload.expense_category,
            "intracommunity_operation_type": (
                payload.intracommunity_operation_type.value
                if payload.intracommunity_operation_type is not None
                else None
            ),
            "external_id": payload.external_id,
            "content_hash": content_hash(payload),
            "revision": document.revision + 1,
            "total_amount": (total_net + total_vat).quantize(Decimal("0.000001")),
            "total_vat": total_vat.quantize(Decimal("0.000001")),
        },
    )
    await repo.delete_lines(session, document.id)
    for index, (line_input, amounts) in enumerate(zip(payload.lines, prepared, strict=True), start=1):
        net, vat, gross = amounts
        await repo.insert_line(
            session,
            workspace_id=workspace_id,
            document_id=document.id,
            line_number=index,
            description=line_input.description,
            quantity=line_input.quantity,
            unit_price=line_input.unit_price,
            discount_amount=line_input.discount_amount,
            vat_rate=line_input.vat_rate,
            vat_treatment=str(line_input.vat_treatment),
            net_amount=net,
            vat_amount=vat,
            gross_amount=gross,
            business_use_percent=line_input.business_use_percent,
            deductibility_percent=line_input.deductibility_percent,
            expense_category=line_input.expense_category,
        )

    if issued and document.issue_date is not None:
        # an issued document feeds declarations and the e-Factura deadline: recompute both
        from pfa.services import notifications as notifications_service
        from pfa.services import obligations as obligations_service

        rows = await obligations_service.evaluate_obligations(
            session, workspace_id=workspace_id, income_year=document.issue_date.year
        )
        await notifications_service.evaluate_notifications(
            session, workspace_id=workspace_id, obligations=rows
        )
    return document


def _snapshot(document, lines) -> dict:
    """Immutable JSON snapshot of a document version."""
    return {
        "id": str(document.id),
        "kind": str(document.kind),
        "status": str(document.status),
        "revision": document.revision,
        "series": document.series,
        "number": document.number,
        "issue_date": document.issue_date.isoformat() if document.issue_date else None,
        "total_amount": str(document.total_amount),
        "total_vat": str(document.total_vat),
        "vat_treatment": str(document.vat_treatment),
        "deductibility": str(document.deductibility),
        "lines": [
            {
                "line_number": line.line_number,
                "description": line.description,
                "net_amount": str(line.net_amount),
                "vat_amount": str(line.vat_amount),
            }
            for line in lines
        ],
    }


async def finalize_document(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    document_id: uuid.UUID,
    series: str | None = None,
    number: str | None = None,
    expected_revision: int | None = None,
):
    """Lock the document under the series/number the user entered.

    ``series``/``number`` may also be supplied here (the number is entered when the
    document is issued); otherwise the values already stored on the draft are used.
    Either way they come from the user, never from a counter.
    """
    document = await get_document(session, workspace_id=workspace_id, document_id=document_id)
    if document.status == DocumentStatus.FINALIZED:
        raise Conflict("Documentul este deja finalizat.")
    if expected_revision is not None and document.revision != expected_revision:
        raise StaleRevision(
            "Documentul a fost modificat între timp.",
            details={"current_revision": document.revision},
        )
    if not document.issue_date:
        raise BlockingInputs(["issue_date"], "Data documentului este obligatorie.")
    lines = await repo.list_lines(session, document.id)
    if document.kind in {DocumentKind.SALES_INVOICE, DocumentKind.CREDIT_NOTE} and not lines:
        raise BlockingInputs(["lines"], "Factura nu are linii.")

    # the number on the issued paper is what the user typed; it is never invented here.
    # The series is optional: many documents carry only a number.
    series = ((series if series is not None else document.series) or "").strip().upper()
    number = ((number if number is not None else document.number) or "").strip()
    if not number:
        raise BlockingInputs(
            ["number"],
            "Completați numărul documentului, exact cum apare pe documentul emis.",
        )
    if document.kind in {DocumentKind.SALES_INVOICE, DocumentKind.CREDIT_NOTE}:
        await _assert_number_is_available(
            session,
            workspace_id=workspace_id,
            series=series,
            number=number,
            document_id=document.id,
        )
    # conditional update: two concurrent finalizations cannot both succeed
    updated = await repo.finalize(
        session,
        document_id=document.id,
        # an absent series is stored as NULL, exactly as the create/update paths store it
        series=series or None,
        number=number,
    )
    await _post_accounting_event(session, document=updated, actor_user_id=actor_user_id)
    await emit_event(
        session,
        event_type="document.finalized",
        workspace_id=workspace_id,
        payload={"document_id": str(updated.id), "kind": str(updated.kind)},
        dedupe_key=f"document-finalized:{updated.id}",
    )
    await record_audit(
        session,
        action="document.finalized",
        entity_type="document",
        entity_id=updated.id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        after={"series": updated.series, "number": updated.number},
    )
    return updated


async def _post_accounting_event(session: AsyncSession, *, document, actor_user_id: uuid.UUID):
    """Recognise the document in the books.  Never treats it as settled."""
    event_kind = (
        "revenue"
        if document.kind in (DocumentKind.SALES_INVOICE.value, DocumentKind.CREDIT_NOTE.value)
        else "expense"
        if document.kind == DocumentKind.PURCHASE_INVOICE.value
        else "other"
    )
    sign = Decimal("-1") if document.kind == DocumentKind.CREDIT_NOTE.value else Decimal("1")
    return await books_repo.insert_accounting_event(
        session,
        workspace_id=document.workspace_id,
        event_date=document.issue_date or clock.today(),
        event_kind=event_kind,
        document_id=document.id,
        amount_ron=(document.total_amount * sign).quantize(Decimal("0.000001")),
        vat_amount_ron=(document.total_vat * sign).quantize(Decimal("0.000001")),
        taxable_amount_ron=Decimal("0"),  # accrual recognition is not taxable cash
        recognition="accrual",
        category=document.expense_category,
        description=f"{document.kind} {document.series or ''}{document.number or ''}".strip(),
        feeds_forms=(
            ["D394", "D300"]
            if document.kind == DocumentKind.PURCHASE_INVOICE.value
            else ["D394"]
        ),
        created_by_user_id=actor_user_id,
    )


async def create_credit_note(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    document_id: uuid.UUID,
    payload: DocumentInput,
):
    original = await get_document(
        session, workspace_id=workspace_id, document_id=document_id
    )
    if original.status != DocumentStatus.FINALIZED:
        raise Conflict("Doar documentele finalizate pot fi corectate.")
    original_lines = await repo.list_lines(session, original.id)
    note_input = DocumentInput(
        kind=DocumentKind.CREDIT_NOTE,
        counterparty_id=original.counterparty_id,
        issue_date=payload.issue_date or clock.today(),
        supply_date=original.supply_date,
        currency=original.currency,
        vat_treatment=VatTreatment(original.vat_treatment),
        deductibility=DeductibilityTreatment(original.deductibility),
        business_use_percent=original.business_use_percent,
        business_purpose=original.business_purpose,
        expense_category=original.expense_category,
        source=DocumentSource(payload.source),
        lines=payload.lines
        or tuple(
            DocumentLineInput(
                description=line.description,
                quantity=line.quantity,
                unit_price=line.unit_price,
                vat_rate=line.vat_rate,
                vat_treatment=VatTreatment(line.vat_treatment),
                discount_amount=line.discount_amount,
                business_use_percent=line.business_use_percent,
                deductibility_percent=line.deductibility_percent,
                expense_category=line.expense_category,
            )
            for line in original_lines
        ),
        corrects_document_id=original.id,
        correction_reason=payload.correction_reason,
    )
    note = await create_document(
        session,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        payload=note_input,
        allow_duplicate=True,
    )
    await repo.snapshot_revision(
        session,
        workspace_id=workspace_id,
        document_id=original.id,
        revision=original.revision,
        reason=f"credit_note:{note.id}",
        snapshot=_snapshot(original, original_lines),
        created_by_user_id=actor_user_id,
    )
    await record_audit(
        session,
        action="document.credit_note_created",
        entity_type="document",
        entity_id=original.id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        after={"credit_note_id": str(note.id)},
    )
    return note


async def _assert_not_in_locked_declaration(
    session: AsyncSession, *, workspace_id: uuid.UUID, document
) -> None:
    """Refuse when the document's figures are already inside a generated or filed return."""
    if document.issue_date is None:
        return
    locked = (
        await session.execute(
            select(Declaration.id)
            .where(
                Declaration.workspace_id == workspace_id,
                Declaration.period_start <= document.issue_date,
                Declaration.period_end >= document.issue_date,
                Declaration.status.in_(LOCKED_DECLARATION_STATUSES),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if locked is not None:
        raise Conflict(
            "Documentul este deja inclus într-o declarație generată sau depusă. "
            "Ștergeți declarația ori folosiți o declarație rectificativă."
        )


async def _assert_not_transmitted(
    session: AsyncSession, *, workspace_id: uuid.UUID, document
) -> None:
    """Refuse when the invoice already left through RO e-Factura.

    Changing it afterwards would not change what was transmitted, so the honest answer is a
    credit note (or delete and reissue), not a silent edit.
    """
    attempt = (
        await session.execute(
            select(SubmissionAttempt.id)
            .where(
                SubmissionAttempt.workspace_id == workspace_id,
                SubmissionAttempt.declaration_id == document.id,
                SubmissionAttempt.status.in_(("succeeded", "unknown")),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if attempt is not None:
        raise Conflict(
            "Factura a fost deja transmisă prin RO e-Factura: o modificare nu ar schimba "
            "ce s-a transmis. Folosiți o notă de credit sau ștergeți factura și reemiteți-o."
        )


#: declaration states from which a document can no longer be deleted: its figures are
#: already inside a generated or filed declaration
LOCKED_DECLARATION_STATUSES = (
    DeclarationStatus.APPROVED,
    DeclarationStatus.EXPORTED,
    DeclarationStatus.AWAITING_EXTERNAL_FILING,
    DeclarationStatus.QUEUED,
    DeclarationStatus.SUBMITTED,
    DeclarationStatus.SUBMISSION_UNKNOWN,
    DeclarationStatus.ACCEPTED,
)


async def delete_document(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    document_id: uuid.UUID,
) -> dict:
    """Delete a document, draft or issued, and everything derived from it.

    Deleting is a real removal: the row, its lines, its attachments, its revision history
    and the settlements applied to it all go.  The append-only audit event records that
    the document existed and what it contained, so the trail survives the row.

    Two things still stop a deletion, because removing them would silently falsify a
    filing: a document already inside a generated or filed declaration, and a document a
    credit note points at.
    """
    document = await get_document(session, workspace_id=workspace_id, document_id=document_id)
    if document.status == DocumentStatus.FINALIZED:
        await _assert_not_in_locked_declaration(session, workspace_id=workspace_id, document=document)
    corrections = await repo.count_credit_notes(session, document_id=document.id)
    if corrections:
        raise Conflict(
            "Documentul are o notă de credit legată. Ștergeți mai întâi nota de credit.",
            details={"credit_notes": corrections},
        )

    lines = await repo.list_lines(session, document.id)
    snapshot = _snapshot(document, lines)
    # the settlements recorded **for** this document go with it: the money was recorded
    # because the document existed.  A settlement covering other documents as well keeps
    # them and only loses this allocation.
    payments_for_document = await repo.payments_of_document(
        session, document_id=document.id
    )
    allocated_settlements = [
        {"payment_id": str(payment.id), "kind": str(payment.kind), "amount": str(payment.amount)}
        for payment in payments_for_document
    ]
    allocations = await repo.delete_allocations(session, document_id=document.id)
    attachments = await repo.delete_attachments(session, document_id=document.id)
    revisions = await repo.delete_revisions(session, document_id=document.id)
    removed = await repo.delete_document(session, document_id=document.id)
    orphan_payments = await repo.delete_orphan_payments(
        session, workspace_id=workspace_id,
        affected_payment_ids=[payment.id for payment in payments_for_document],
    )
    removed_ids = {str(payment.id) for payment in orphan_payments}
    removed_settlements = [
        item for item in allocated_settlements if item["payment_id"] in removed_ids
    ]
    from pfa.repo import books as books_repo

    for payment in orphan_payments:
        await books_repo.delete_accounting_events_for_payment(
            session, workspace_id=workspace_id, payment_id=payment.id
        )

    store = ObjectStore.from_settings()
    for attachment in attachments:
        if attachment.object_key:
            store.delete(attachment.object_key)

    await record_audit(
        session,
        action="document.deleted",
        entity_type="document",
        entity_id=document.id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        before=snapshot,
        after={
            "deleted": True,
            "status": str(document.status),
            "allocations_removed": allocations,
            "payments_affected": allocated_settlements,
            "payments_removed": removed_settlements,
            "orphan_payments_removed": len(orphan_payments),
            "attachments_removed": len(attachments),
            "revisions_removed": revisions,
            "rows_removed": removed,
        },
    )
    await emit_event(
        session,
        event_type="document.deleted",
        workspace_id=workspace_id,
        payload={"document_id": str(document.id), "kind": str(document.kind)},
        dedupe_key=f"document-deleted:{document.id}",
    )

    # Obligations derived from this document must stop asking for attention: the
    # per-invoice e-Factura obligation and its reminders are removed, then the period
    # obligations (D300/D390/D394) are recomputed without the document.
    if document.issue_date is not None:
        from pfa.repo import compliance as compliance_repo
        from pfa.services import notifications as notifications_service
        from pfa.services import obligations as obligations_service

        obligation = await compliance_repo.obligation_by_recurrence_key(
            session, workspace_id=workspace_id, recurrence_key=f"EFACTURA:{document.id}"
        )
        if obligation is not None:
            # the document is gone, so its derived obligation and reminders go with it
            await compliance_repo.delete_notifications_for_obligation(
                session, obligation_id=obligation.id
            )
            await compliance_repo.delete_obligation(session, obligation_id=obligation.id)
        rows = await obligations_service.evaluate_obligations(
            session, workspace_id=workspace_id, income_year=document.issue_date.year
        )
        await notifications_service.evaluate_notifications(
            session, workspace_id=workspace_id, obligations=rows
        )
    return {
        **snapshot,
        # what went away with the document, mirrored to the caller
        "payments_removed": removed_settlements,
        "allocations_removed": allocations,
        "payments_left_unallocated": 0,
        "revisions_removed": revisions,
        "rows_removed": removed,
    }


async def attach_evidence(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    document_id: uuid.UUID | None,
    filename: str,
    content_type: str,
    data: bytes,
    evidence_kind: EvidenceKind = EvidenceKind.OTHER,
    store: ObjectStore | None = None,
    settings: Settings | None = None,
):
    settings = settings or get_settings()
    store = store or ObjectStore.from_settings(settings)
    validate_attachment(content_type, len(data), settings)
    if document_id is not None:
        # 404 for another workspace's document (no information leak)
        await get_document(session, workspace_id=workspace_id, document_id=document_id)
    stored = store.put(data, content_type=content_type, namespace=f"workspace/{workspace_id}")
    return await repo.insert_attachment(
        session,
        workspace_id=workspace_id,
        document_id=document_id,
        evidence_kind=str(evidence_kind),
        filename=filename[:300],
        content_type=content_type,
        size_bytes=stored.size_bytes,
        sha256=stored.sha256,
        object_key=stored.key,
        scan_status=scan_bytes(data),
        uploaded_by_user_id=actor_user_id,
    )


async def get_attachment(
    session: AsyncSession, *, workspace_id: uuid.UUID, attachment_id: uuid.UUID
):
    attachment = await repo.get_attachment(
        session, workspace_id=workspace_id, attachment_id=attachment_id
    )
    if attachment is None:
        raise NotFound("Atașamentul nu există.")
    return attachment


async def list_documents(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    kind: DocumentKind | None = None,
    status: DocumentStatus | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    cursor: date | None = None,
    limit: int = 50,
) -> list:
    return await repo.list_documents(
        session,
        workspace_id=workspace_id,
        kind=str(kind) if kind else None,
        status=str(status) if status else None,
        date_from=date_from,
        date_to=date_to,
        cursor=cursor,
        limit=limit,
    )


async def settled_amount(
    session: AsyncSession, *, workspace_id: uuid.UUID, document_id: uuid.UUID
) -> Decimal:
    return await repo.settled_amount(
        session, workspace_id=workspace_id, document_id=document_id
    )


async def record_import(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    source: DocumentSource,
    original_filename: str | None,
    object_key: str | None,
    duplicates: list[dict] | None = None,
):
    return await repo.insert_import(
        session,
        workspace_id=workspace_id,
        source=str(source),
        status="review",
        original_filename=original_filename,
        object_key=object_key,
        detected_duplicates=duplicates or [],
        created_by_user_id=actor_user_id,
    )


def require_ocr_review(proposed: dict) -> dict:
    """OCR may propose data but must not post entries or declare deductibility.

    The returned payload is explicitly marked as needing review; the caller
    cannot finalize a document from OCR output alone.
    """
    return {
        "source": "ocr",
        "requires_review": True,
        "deductibility_declared": False,
        "posted_entries": False,
        "proposal": proposed,
    }
