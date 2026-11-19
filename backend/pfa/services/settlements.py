"""Settlements: receipts, payments, allocations, advances, owner movements.

Payments are recorded independently of invoices: an uploaded invoice is not
evidence of payment.  Cash recognition happens on the settlement, so a document
collected across two calendar years produces separate settlement-year events.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from pfa.errors import BlockingInputs, Conflict, NotFound, ValidationFailed
from pfa.models.base import PaymentDirection, PaymentKind, PaymentMethod
from pfa.repo import books as books_repo
from pfa.repo import documents as documents_repo
from pfa.repo import money as repo
from pfa.services.audit import emit_event, record_audit
from pfa.services.documents import assert_period_open, get_document

OWNER_KINDS = {
    PaymentKind.OWNER_CONTRIBUTION,
    PaymentKind.OWNER_WITHDRAWAL,
    PaymentKind.OWNER_TRANSFER,
    PaymentKind.LOAN_RECEIVED,
    PaymentKind.LOAN_REPAID,
}

#: movements that are neither gross income nor deductible expenses (Cod fiscal art. 68
#: alin. (2): aporturile și împrumuturile „nu sunt venituri brute"; art. 68 alin. (4):
#: impozitul pe venit datorat nu este deductibil).  They are recorded in the
#: Registrul-jurnal de încasări și plăți (OMFP 170/2015, formularul 14-1-1/b) so the
#: balance agrees with the bank statement, and they are never allocated to a document
#: nor carried into the fiscal register (REF).
NON_DOCUMENT_KINDS = OWNER_KINDS | {PaymentKind.TAX_PAYMENT}


def registration_rule(kind: PaymentKind) -> str:
    """How a recorded movement is registered for accounting, per the legislation."""
    return {
        PaymentKind.OWNER_CONTRIBUTION: (
            "Aportul titularului: se înregistrează în RJIP ca încasare nu-venit "
            "(Cod fiscal art. 68 alin. (2) lit. 1) — nu intră în venitul brut."
        ),
        PaymentKind.OWNER_WITHDRAWAL: (
            "Retragerea titularului (utilizarea venitului net): se înregistrează în RJIP "
            "ca plată nedeductibilă — nu este cheltuială a activității."
        ),
        PaymentKind.OWNER_TRANSFER: (
            "Transferul între conturile personale: se înregistrează în RJIP doar pentru "
            "registru de casă/bancă, fără efect fiscal."
        ),
        PaymentKind.LOAN_RECEIVED: (
            "Împrumutul primit: nu este venit brut (art. 68 alin. (2) lit. 2). Se "
            "înregistrează în RJIP; se deduce numai dobânda, înstiințată ca cheltuială."
        ),
        PaymentKind.LOAN_REPAID: (
            "Rambursarea împrumutului: rata de capital nu este deductibilă (numai "
            "dobânda). Se înregistrează în RJIP."
        ),
        PaymentKind.TAX_PAYMENT: (
            "Plata impozitului/CAS/CASS: se înregistrează în RJIP cu deductibilitate zero "
            "(art. 68 alin. (4)); CAS și CASS se deduc numai prin Declarația Unică."
        ),
    }.get(kind)

REDUCING_ALLOCATION_KINDS = {"credit", "refund"}
ALLOCATION_KINDS = {"settlement", "advance", "credit", "fx_difference", "refund"}

ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class AllocationInput:
    document_id: uuid.UUID
    amount: Decimal
    allocation_kind: str = "settlement"
    fx_difference_amount: Decimal = ZERO
    note: str | None = None


@dataclass(frozen=True, slots=True)
class SettlementUpdate:
    """The settlement fields a user may correct; ``None`` means "leave unchanged"."""

    paid_on: date | None = None
    amount: Decimal | None = None
    method: PaymentMethod | None = None
    reference: str | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class PaymentInput:
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
    bank_fee_amount: Decimal = ZERO
    withholding_amount: Decimal = ZERO
    external_id: str | None = None
    allocations: tuple[AllocationInput, ...] = field(default_factory=tuple)


async def _allocated_total(
    session: AsyncSession, *, workspace_id: uuid.UUID, document_id: uuid.UUID
) -> Decimal:
    totals = await repo.allocation_totals_by_kind(
        session, workspace_id=workspace_id, document_id=document_id
    )
    total = ZERO
    for kind, amount in totals.items():
        total += -amount if kind in REDUCING_ALLOCATION_KINDS else amount
    return total


async def record_payment(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    payload: PaymentInput,
):
    if payload.amount <= ZERO:
        raise ValidationFailed("Suma trebuie să fie pozitivă.", field_errors={"amount": "positive"})
    if payload.currency != "RON" and payload.amount_ron is None:
        raise BlockingInputs(
            ["exchange_rate"],
            "Pentru o sumă în valută este necesar cursul de schimb și data conversiei.",
        )
    await assert_period_open(session, workspace_id=workspace_id, day=payload.paid_on)

    if payload.account_id is not None:
        account = await repo.get_financial_account(
            session, workspace_id=workspace_id, account_id=payload.account_id
        )
        if account is None:
            raise NotFound("Contul financiar nu există.")

    if payload.external_id:
        duplicate = await repo.find_payment_by_external_id(
            session, workspace_id=workspace_id, external_id=payload.external_id
        )
        if duplicate is not None:
            # Import must not double count a settlement.
            return duplicate

    is_owner_movement = payload.kind in OWNER_KINDS
    payment = await repo.insert_payment(
        session,
        workspace_id=workspace_id,
        account_id=payload.account_id,
        direction=payload.direction.value,
        kind=payload.kind.value,
        method=payload.method.value,
        paid_on=payload.paid_on,
        currency=payload.currency,
        amount=payload.amount,
        amount_ron=payload.amount_ron or payload.amount,
        bank_fee_amount=payload.bank_fee_amount,
        withholding_amount=payload.withholding_amount,
        reference=payload.reference,
        note=payload.note,
        gross_amount=payload.gross_amount,
        is_owner_movement=is_owner_movement,
        external_id=payload.external_id,
        created_by_user_id=actor_user_id,
    )

    for allocation in payload.allocations:
        if allocation.allocation_kind not in ALLOCATION_KINDS:
            raise ValidationFailed(
                f"Tip de alocare necunoscut: {allocation.allocation_kind}",
                field_errors={"allocation_kind": "unknown"},
            )
        # Lock the document row so two concurrent allocations cannot both pass the
        # remaining-amount check.
        document = await documents_repo.get_document(
            session,
            workspace_id=workspace_id,
            document_id=allocation.document_id,
            for_update=True,
        )
        if document is None:
            raise NotFound("Documentul alocat nu există în acest spațiu de lucru.")
        already = await _allocated_total(
            session, workspace_id=workspace_id, document_id=document.id
        )
        available = document.total_amount - already + allocation.fx_difference_amount
        if (
            allocation.allocation_kind not in REDUCING_ALLOCATION_KINDS
            and allocation.allocation_kind != "advance"
            and allocation.amount > available
        ):
            label = f"{document.series or ''}{document.number or ''}".strip() or str(document.id)
            raise Conflict(
                f"Alocarea depășește suma disponibilă a documentului {label}: "
                f"disponibil {available}, cerut {allocation.amount}. Dacă documentul are deja "
                "încasări sau plăți înregistrate, modificați ori ștergeți alocarea existentă "
                "în loc să adăugați una nouă.",
                details={
                    "document_id": str(document.id),
                    "document": label,
                    "available": str(available),
                    "requested": str(allocation.amount),
                    "hint": "Modelați diferența ca avans, credit sau diferență de curs.",
                },
            )
        await repo.insert_allocation(
            session,
            workspace_id=workspace_id,
            payment_id=payment.id,
            document_id=document.id,
            amount=allocation.amount,
            currency=payload.currency,
            allocation_kind=allocation.allocation_kind,
            fx_difference_amount=allocation.fx_difference_amount,
            note=allocation.note,
        )

    await _post_settlement_events(session, payment=payment, actor_user_id=actor_user_id)
    await emit_event(
        session,
        event_type="payment.recorded",
        workspace_id=workspace_id,
        payload={"payment_id": str(payment.id), "kind": str(payment.kind)},
        dedupe_key=f"payment-recorded:{payment.id}",
    )
    await record_audit(
        session,
        action="payment.recorded",
        entity_type="payment",
        entity_id=payment.id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        after={
            "direction": str(payload.direction),
            "kind": str(payload.kind),
            "amount": str(payload.amount),
        },
    )
    return payment


async def _post_settlement_events(session: AsyncSession, *, payment, actor_user_id: uuid.UUID) -> None:
    """Recognise cash movement in the books.

    Owner contributions, loans, transfers between own accounts and withdrawals are
    visible in the cash records but never become taxable revenue or deductible
    expenses merely because money moved.
    """
    amount_ron = payment.amount_ron or payment.amount
    if payment.is_owner_movement:
        await books_repo.insert_accounting_event(
            session,
            workspace_id=payment.workspace_id,
            event_date=payment.paid_on,
            event_kind="owner_movement",
            payment_id=payment.id,
            amount_ron=amount_ron,
            taxable_amount_ron=ZERO,
            recognition="realization",
            category=str(payment.kind),
            description=f"Mișcare a titularului: {payment.kind}",
            feeds_forms=[],
            created_by_user_id=actor_user_id,
        )
        return

    if payment.kind == PaymentKind.CUSTOMER_RECEIPT:
        await books_repo.insert_accounting_event(
            session,
            workspace_id=payment.workspace_id,
            event_date=payment.paid_on,
            event_kind="revenue",
            payment_id=payment.id,
            amount_ron=amount_ron,
            vat_amount_ron=ZERO,
            taxable_amount_ron=amount_ron,
            recognition="realization",
            category="collections",
            description="Încasare de la client",
            feeds_forms=["D212", "D394"],
            created_by_user_id=actor_user_id,
        )
    if payment.bank_fee_amount and payment.bank_fee_amount > ZERO:
        await books_repo.insert_accounting_event(
            session,
            workspace_id=payment.workspace_id,
            event_date=payment.paid_on,
            event_kind="expense",
            payment_id=payment.id,
            amount_ron=payment.bank_fee_amount,
            taxable_amount_ron=payment.bank_fee_amount,
            recognition="realization",
            category="bank_fee",
            description="Comision bancar",
            feeds_forms=["D212"],
            created_by_user_id=actor_user_id,
        )
    if payment.kind == PaymentKind.SUPPLIER_PAYMENT.value and payment.direction == PaymentDirection.OUTFLOW:
        await books_repo.insert_accounting_event(
            session,
            workspace_id=payment.workspace_id,
            event_date=payment.paid_on,
            event_kind="expense",
            payment_id=payment.id,
            amount_ron=amount_ron,
            taxable_amount_ron=amount_ron,
            recognition="realization",
            category="supplier_payment",
            description="Plată către furnizor",
            feeds_forms=["D212", "D394"],
            created_by_user_id=actor_user_id,
        )


async def record_processor_settlement(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    payload: PaymentInput,
    processor_fee: Decimal,
):
    """Gross receipts from a processor versus its net payout.

    The fee is recorded as a separate bank/processor expense so revenue is not
    understated and the fee is not silently netted off.
    """
    if payload.gross_amount is None:
        raise BlockingInputs(["gross_amount"], "Suma brută încasată de la procesator lipsește.")
    if payload.amount != payload.gross_amount - processor_fee:
        raise ValidationFailed(
            "Suma netă nu corespunde sumei brute minus comisionul procesatorului.",
            field_errors={"amount": "gross_minus_fee"},
        )
    from dataclasses import replace

    return await record_payment(
        session,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        payload=replace(payload, bank_fee_amount=processor_fee),
    )


async def list_allocations(
    session: AsyncSession, *, workspace_id: uuid.UUID, payment_id: uuid.UUID
) -> list[dict]:
    rows = await repo.list_allocations(session, payment_id=payment_id)
    return [
        {
            "document_id": str(row.document_id),
            "document": f"{row.series or ''}{row.number or ''}".strip() or None,
            # the document may be gone (deleted since the settlement was recorded)
            "document_exists": bool(row.document_exists),
            "issue_date": row.issue_date.isoformat() if row.issue_date else None,
            "amount": str(row.amount),
            "allocation_kind": row.allocation_kind,
            "note": row.note,
        }
        for row in rows
    ]


async def get_settlement(
    session: AsyncSession, *, workspace_id: uuid.UUID, payment_id: uuid.UUID
) -> dict:
    payment = await repo.get_payment(session, workspace_id=workspace_id, payment_id=payment_id)
    if payment is None:
        raise NotFound("Încasarea sau plata nu există în acest spațiu de lucru.")
    return {
        "id": str(payment.id),
        "direction": str(payment.direction),
        "kind": str(payment.kind),
        "method": str(payment.method),
        "paid_on": payment.paid_on.isoformat(),
        "currency": payment.currency,
        "amount": str(payment.amount),
        "amount_ron": str(payment.amount_ron or payment.amount),
        "reference": payment.reference,
        "note": payment.note,
        "allocations": await list_allocations(
            session, workspace_id=workspace_id, payment_id=payment.id
        ),
    }


async def update_settlement(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    payment_id: uuid.UUID,
    fields: SettlementUpdate,
) -> dict:
    """Correct a recorded settlement.

    Only the settlement's own fields are edited here.  When it is applied to exactly one
    document, the allocation follows the amount, so a corrected receipt keeps matching the
    invoice it pays; the over-allocation guard is checked against the *other* settlements,
    never against this payment's own allocation.
    """
    payment = await repo.get_payment(session, workspace_id=workspace_id, payment_id=payment_id)
    if payment is None:
        raise NotFound("Încasarea sau plata nu există în acest spațiu de lucru.")
    before = await get_settlement(session, workspace_id=workspace_id, payment_id=payment_id)

    values: dict = {}
    if fields.paid_on is not None:
        await assert_period_open(session, workspace_id=workspace_id, day=fields.paid_on)
        values["paid_on"] = fields.paid_on
    if fields.amount is not None:
        if fields.amount <= ZERO:
            raise ValidationFailed(
                "Suma încasării sau plății trebuie să fie pozitivă.",
                field_errors={"amount": "must_be_positive"},
            )
        values["amount"] = fields.amount
        values["amount_ron"] = fields.amount
    if fields.method is not None:
        values["method"] = fields.method.value
    if fields.reference is not None:
        values["reference"] = fields.reference or None
    if fields.note is not None:
        values["note"] = fields.note or None

    allocations = await repo.list_allocations(session, payment_id=payment.id)
    if fields.amount is not None and len(allocations) == 1:
        allocation = allocations[0]
        document = await documents_repo.get_document(
            session,
            workspace_id=workspace_id,
            document_id=allocation.document_id,
            for_update=True,
        )
        if document is not None:
            others = await _allocated_total(
                session, workspace_id=workspace_id, document_id=document.id
            ) - allocation.amount
            available = document.total_amount - others
            if fields.amount > available:
                raise Conflict(
                    f"Suma depășește ce mai este de încasat pe documentul "
                    f"{document.series or ''}{document.number or ''}: disponibil {available}.",
                    details={
                        "document_id": str(document.id),
                        "available": str(available),
                        "requested": str(fields.amount),
                    },
                )
            await repo.update_allocation_amount(
                session, allocation_id=allocation.id, amount=fields.amount
            )

    updated = await repo.update_payment(session, payment_id=payment.id, fields=values or {"note": payment.note})
    if fields.amount is not None or fields.paid_on is not None:
        await books_repo.update_accounting_events_for_payment(
            session, workspace_id=workspace_id, payment_id=payment.id,
            paid_on=updated.paid_on, amount_ron=updated.amount_ron or updated.amount,
        )
    await record_audit(
        session,
        action="settlement.updated",
        entity_type="payment",
        entity_id=payment.id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        before=before,
        after={"fields": sorted(values)},
    )
    return await get_settlement(session, workspace_id=workspace_id, payment_id=updated.id)


async def delete_settlement(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    payment_id: uuid.UUID,
) -> dict:
    """Remove a settlement and the allocations it carried.

    The document keeps its own total; the money it was paying simply becomes unallocated.
    """
    payment = await repo.get_payment(session, workspace_id=workspace_id, payment_id=payment_id)
    if payment is None:
        raise NotFound("Încasarea sau plata nu există în acest spațiu de lucru.")
    before = await get_settlement(session, workspace_id=workspace_id, payment_id=payment_id)
    allocations = await repo.delete_allocations_for_payment(session, payment_id=payment.id)
    events_removed = await books_repo.delete_accounting_events_for_payment(
        session, workspace_id=workspace_id, payment_id=payment.id
    )
    removed = await repo.delete_payment(session, payment_id=payment.id)
    await record_audit(
        session,
        action="settlement.deleted",
        entity_type="payment",
        entity_id=payment.id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        before=before,
        after={
            "deleted": True,
            "allocations_removed": allocations,
            "books_events_removed": events_removed,
            "rows_removed": removed,
        },
    )
    return {
        "deleted": True,
        "payment_id": str(payment.id),
        "allocations_removed": allocations,
        "books_events_removed": events_removed,
    }


async def document_settlement_summary(
    session: AsyncSession, *, workspace_id: uuid.UUID, document_id: uuid.UUID
) -> dict:
    document = await get_document(session, workspace_id=workspace_id, document_id=document_id)
    settled = await _allocated_total(session, workspace_id=workspace_id, document_id=document_id)
    outstanding = document.total_amount - settled
    # Editing a document can lower its total below what is already allocated.  That is a
    # real state, so it is reported as such instead of showing a negative "outstanding".
    over_settled = settled > document.total_amount
    return {
        "document_id": str(document_id),
        "total": str(document.total_amount),
        "settled": str(settled),
        "outstanding": str(outstanding),
        "paid_in_full": outstanding <= ZERO,
        "over_settled": over_settled,
        "over_amount": str(settled - document.total_amount) if over_settled else str(ZERO),
    }


async def list_settlements(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 100,
) -> list:
    return await repo.list_payments(
        session, workspace_id=workspace_id, date_from=date_from, date_to=date_to, limit=limit
    )
