"""Books: registers, reconciliation, period close and reopen.

Single-entry cash records and tax adjustments stay reconcilable but distinct:
the cash journal is built from *settlements*, while the tax engine consumes
documents, settlements and adjustments separately.
"""

from __future__ import annotations

import csv
import io
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from pfa.errors import BlockingInputs, Conflict, NotFound
from pfa.fiscal.money import ZERO, round_cents
from pfa.models.base import DocumentKind
from pfa.repo import books as books_repo
from pfa.repo import documents as documents_repo
from pfa.repo import registers
from pfa.services.audit import enqueue_job, record_audit
from pfa.services.storage import ObjectStore

REGISTER_TYPES = (
    "cash_journal",
    "sales_vat_journal",
    "purchase_vat_journal",
    "asset_register",
    "inventory_register",
    "outstanding_receivables",
    "outstanding_payables",
)


@dataclass(frozen=True, slots=True)
class CashJournalRow:
    occurred_on: date
    description: str
    document_ref: str | None
    receipts: Decimal
    payments: Decimal
    owner_movement: Decimal
    balance: Decimal


async def cash_journal(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    period_start: date,
    period_end: date,
) -> tuple[list[CashJournalRow], Decimal, Decimal]:
    """Registrul-jurnal de încasări și plăți.

    The running balance is a window function inside the query, and the period
    totals come from the same rows.
    """
    rows = await registers.cash_journal(
        session, workspace_id=workspace_id, period_start=period_start, period_end=period_end
    )
    journal: list[CashJournalRow] = []
    total_receipts = ZERO
    total_payments = ZERO
    for row in rows:
        receipts = ZERO
        payments = ZERO
        owner = ZERO
        if row.is_owner_movement:
            owner = row.amount_ron if row.direction == "inflow" else -row.amount_ron
        elif row.direction == "inflow":
            receipts = row.amount_ron
        else:
            payments = row.amount_ron
        total_receipts += receipts
        total_payments += payments
        journal.append(
            CashJournalRow(
                occurred_on=row.paid_on,
                description=f"{row.kind} {row.reference or ''}".strip(),
                document_ref=None,
                receipts=receipts,
                payments=payments,
                owner_movement=owner,
                balance=row.balance,
            )
        )
    return journal, total_receipts, total_payments


async def vat_journal(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    period_start: date,
    period_end: date,
    kind: DocumentKind,
) -> list[dict]:
    rows_kind = kind.value if hasattr(kind, "value") else str(kind)
    documents = await documents_repo.finalized_documents_between(
        session,
        workspace_id=workspace_id,
        kind=rows_kind,
        date_from=period_start,
        date_to=period_end,
    )
    # A storno reduces the VAT of the direction it corrects, so credit notes correcting a
    # document of this register are listed with negative amounts — the register then agrees
    # with the decont.
    notes = await documents_repo.finalized_credit_notes_between(
        session,
        workspace_id=workspace_id,
        corrected_kind=rows_kind,
        date_from=period_start,
        date_to=period_end,
    )
    return [
        {
            "issue_date": document.issue_date.isoformat(),
            "series": document.series,
            "number": document.number,
            "counterparty_id": str(document.counterparty_id) if document.counterparty_id else None,
            "supplier_name": document.supplier_name_raw,
            "vat_treatment": str(document.vat_treatment),
            "base": str(round_cents(document.total_amount - document.total_vat)),
            "vat": str(round_cents(document.total_vat)),
            "total": str(round_cents(document.total_amount)),
            "deductibility": str(document.deductibility),
            "business_use_percent": str(document.business_use_percent),
        }
        for document in documents
    ] + [
        {
            "issue_date": note.issue_date.isoformat(),
            "series": note.series,
            "number": note.number,
            "counterparty_id": str(note.counterparty_id) if note.counterparty_id else None,
            "supplier_name": note.supplier_name_raw,
            "vat_treatment": str(note.vat_treatment),
            "base": str(round_cents(-(note.total_amount - note.total_vat))),
            "vat": str(round_cents(-note.total_vat)),
            "total": str(round_cents(-note.total_amount)),
            "deductibility": str(note.deductibility),
            "business_use_percent": str(note.business_use_percent),
        }
        for note in notes
    ]


async def outstanding(
    session: AsyncSession, *, workspace_id: uuid.UUID, kind: DocumentKind
) -> list[dict]:
    """Outstanding receivables/payables.

    One grouped query (see :mod:`pfa.repo.registers`): the previous version ran a
    settlement lookup per document, so the cost grew with the number of rows.
    """
    rows = await registers.outstanding_documents(
        session, workspace_id=workspace_id, kind=str(kind)
    )
    return [
        {
            "document_id": row.document_id,
            "series": row.series,
            "number": row.number,
            "issue_date": row.issue_date.isoformat() if row.issue_date else None,
            "due_date": row.due_date.isoformat() if row.due_date else None,
            "total": str(round_cents(row.total_amount)),
            "settled": str(round_cents(row.settled)),
            "outstanding": str(round_cents(row.outstanding)),
        }
        for row in rows
    ]


async def asset_register(session: AsyncSession, *, workspace_id: uuid.UUID) -> list[dict]:
    """Assets with depreciation totals and book value, in one grouped query."""
    rows = await registers.asset_register(session, workspace_id=workspace_id)
    return [
        {
            "asset_id": row.asset_id,
            "name": row.name,
            "category": row.category,
            "in_service_date": row.in_service_date.isoformat(),
            "acquisition_cost": str(round_cents(row.acquisition_cost_ron)),
            "business_use_percent": str(row.business_use_percent),
            "method": row.depreciation_method,
            "useful_life_months": row.useful_life_months,
            "depreciated": str(round_cents(row.depreciated)),
            "book_value": str(round_cents(row.book_value)),
            "status": row.status,
        }
        for row in rows
    ]


async def inventory_register(
    session: AsyncSession, *, workspace_id: uuid.UUID, income_year: int | None = None
) -> list[dict]:
    entries = await books_repo.inventory_for_year(
        session, workspace_id=workspace_id, year=income_year
    )
    return [
        {
            "occurred_on": entry.occurred_on.isoformat(),
            "kind": entry.entry_kind,
            "item": entry.item_name,
            "quantity": str(entry.quantity),
            "unit_cost": str(round_cents(entry.unit_cost)),
            "value": str(round_cents(entry.value)),
        }
        for entry in entries
    ]


async def tax_adjustments(
    session: AsyncSession, *, workspace_id: uuid.UUID, income_year: int
) -> list[dict]:
    rows = await books_repo.tax_adjustments_for_year(
        session, workspace_id=workspace_id, year=income_year
    )
    return [
        {
            "kind": row.adjustment_kind,
            "direction": row.direction,
            "amount": str(round_cents(row.amount)),
            "legal_reference": row.legal_reference,
            "explanation": row.explanation,
        }
        for row in rows
    ]


async def register_rows(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    register_type: str,
    period_start: date,
    period_end: date,
    income_year: int | None = None,
) -> list[dict]:
    if register_type not in REGISTER_TYPES:
        raise NotFound(f"Registrul {register_type} nu este un tip cunoscut.")
    if register_type == "cash_journal":
        rows, _, _ = await cash_journal(
            session, workspace_id=workspace_id, period_start=period_start, period_end=period_end
        )
        return [
            {
                "occurred_on": row.occurred_on.isoformat(),
                "description": row.description,
                "receipts": str(round_cents(row.receipts)),
                "payments": str(round_cents(row.payments)),
                "owner_movement": str(round_cents(row.owner_movement)),
                "balance": str(round_cents(row.balance)),
            }
            for row in rows
        ]
    if register_type == "sales_vat_journal":
        return await vat_journal(
            session,
            workspace_id=workspace_id,
            period_start=period_start,
            period_end=period_end,
            kind=DocumentKind.SALES_INVOICE,
        )
    if register_type == "purchase_vat_journal":
        return await vat_journal(
            session,
            workspace_id=workspace_id,
            period_start=period_start,
            period_end=period_end,
            kind=DocumentKind.PURCHASE_INVOICE,
        )
    if register_type == "asset_register":
        return await asset_register(session, workspace_id=workspace_id)
    if register_type == "inventory_register":
        return await inventory_register(
            session, workspace_id=workspace_id, income_year=income_year
        )
    if register_type == "outstanding_receivables":
        return await outstanding(
            session, workspace_id=workspace_id, kind=DocumentKind.SALES_INVOICE
        )
    if register_type == "outstanding_payables":
        return await outstanding(
            session, workspace_id=workspace_id, kind=DocumentKind.PURCHASE_INVOICE
        )
    raise NotFound(f"Registrul {register_type} nu este implementat.")  # pragma: no cover


async def to_csv(rows: list[dict]) -> bytes:
    if not rows:
        return b""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()), delimiter=";")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue().encode("utf-8")


# ---------------------------------------------------------------------------
# Period close / reopen
# ---------------------------------------------------------------------------


async def close_checklist(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    period_start: date,
    period_end: date,
) -> dict:
    """Checks run before a period may be closed.

    Grouped statements only (see :mod:`pfa.repo.registers`): no per-document or
    per-asset lookups.
    """
    blockers = await registers.close_checklist(
        session, workspace_id=workspace_id, period_start=period_start, period_end=period_end
    )
    return {
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "blockers": blockers,
        "ready_to_close": not any(blockers.values()),
    }


async def close_period(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    period_start: date,
    period_end: date,
    period_kind: str = "month",
    force: bool = False,
):
    if period_end < period_start:
        raise Conflict("Intervalul perioadei este invalid.")
    if await books_repo.closed_period_exists(
        session, workspace_id=workspace_id, period_start=period_start, period_end=period_end
    ):
        raise Conflict("Perioada este deja închisă.")

    checklist = await close_checklist(
        session, workspace_id=workspace_id, period_start=period_start, period_end=period_end
    )
    if not checklist["ready_to_close"] and not force:
        raise BlockingInputs(
            sorted({key for key, value in checklist["blockers"].items() if value}),
            "Perioada nu poate fi închisă: există verificări nerezolvate.",
        )
    previous_revision = await books_repo.latest_period_revision(
        session, workspace_id=workspace_id, period_start=period_start, period_end=period_end
    )
    lock = await books_repo.insert_period_lock(
        session,
        workspace_id=workspace_id,
        period_start=period_start,
        period_end=period_end,
        period_kind=period_kind,
        locked_by_user_id=actor_user_id,
        revision=int(previous_revision or 0) + 1,
        checklist=checklist,
        is_open=False,
    )
    await record_audit(
        session,
        action="period.closed",
        entity_type="period_lock",
        entity_id=lock.id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        after={"period_start": period_start.isoformat(), "period_end": period_end.isoformat()},
    )
    return lock


async def reopen_period(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    period_lock_id: uuid.UUID,
    reason: str,
):
    lock = await books_repo.get_period_lock_for_update(
        session, workspace_id=workspace_id, period_lock_id=period_lock_id
    )
    if lock is None:
        raise NotFound("Perioada nu există.")
    if lock.is_open:
        raise Conflict("Perioada este deja deschisă.")
    if not reason or len(reason.strip()) < 10:
        raise BlockingInputs(["reason"], "Redeschiderea necesită un motiv documentat.")

    reopened = await books_repo.reopen_period_lock(
        session,
        workspace_id=workspace_id,
        period_lock_id=lock.id,
        reason=reason.strip(),
        reopened_by_user_id=actor_user_id,
    )
    # reopening creates a new revision with the reason recorded
    revision = await books_repo.insert_period_lock(
        session,
        workspace_id=workspace_id,
        period_start=lock.period_start,
        period_end=lock.period_end,
        period_kind=lock.period_kind,
        locked_by_user_id=actor_user_id,
        revision=lock.revision + 1,
        checklist=lock.close_checklist or {},
        is_open=True,
    )
    await record_audit(
        session,
        action="period.reopened",
        entity_type="period_lock",
        entity_id=lock.id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        before={"is_open": False, "revision": lock.revision},
        after={"is_open": True, "revision": revision.revision, "reason": reason},
    )
    _ = reopened
    return revision


async def export_register(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    register_type: str,
    period_start: date,
    period_end: date,
    export_format: str = "csv",
    income_year: int | None = None,
    store: ObjectStore | None = None,
):
    rows = await register_rows(
        session,
        workspace_id=workspace_id,
        register_type=register_type,
        period_start=period_start,
        period_end=period_end,
        income_year=income_year,
    )
    payload = await to_csv(rows)
    store = store or ObjectStore.from_settings()
    stored = store.put(
        payload, content_type="text/csv", namespace=f"workspace/{workspace_id}/registers"
    )
    export = await books_repo.insert_register_export(
        session,
        workspace_id=workspace_id,
        register_type=register_type,
        period_start=period_start,
        period_end=period_end,
        export_format=export_format,
        status="ready",
        object_key=stored.key,
        sha256=stored.sha256,
        row_count=len(rows),
    )
    job = await enqueue_job(
        session,
        kind="register.export",
        workspace_id=workspace_id,
        payload={"register_export_id": str(export.id)},
        logical_key=f"register-export:{export.id}",
        created_by_user_id=actor_user_id,
    )
    await books_repo.set_register_export_job(session, export_id=export.id, job_id=job.id)
    return export, job.id
