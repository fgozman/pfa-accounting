"""Money SQL: accounts, settlements, allocations.

Cash movement is recorded independently of documents.  Allocations are validated
against the document's remaining amount while holding a lock on the document row,
so two concurrent allocations cannot both pass the check.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from pfa.repo.query import Row, all_rows, one, returning, run, val, where

PAYMENT_COLUMNS = """
    id, workspace_id, account_id, direction, kind, method, paid_on, currency, amount,
    amount_ron, exchange_rate_id, bank_fee_amount, withholding_amount, reference, note,
    gross_amount, is_owner_movement, created_by_user_id, external_id
"""

ALLOCATION_COLUMNS = """
    id, workspace_id, payment_id, document_id, amount, currency, allocation_kind,
    fx_difference_amount, note
"""


async def get_financial_account(
    session: AsyncSession, *, workspace_id: uuid.UUID, account_id: uuid.UUID
) -> Row | None:
    return await one(
        session,
        """
        SELECT id, name, kind, currency, iban, opening_balance, active
          FROM financial_accounts
         WHERE id = :account_id AND workspace_id = :workspace_id
        """,
        account_id=account_id,
        workspace_id=workspace_id,
    )


async def find_payment_by_external_id(
    session: AsyncSession, *, workspace_id: uuid.UUID, external_id: str
) -> Row | None:
    return await one(
        session,
        f"""
        SELECT {PAYMENT_COLUMNS} FROM payments
         WHERE workspace_id = :workspace_id AND external_id = :external_id
        """,
        workspace_id=workspace_id,
        external_id=external_id,
    )


async def insert_payment(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    account_id: uuid.UUID | None,
    direction: str,
    kind: str,
    method: str,
    paid_on: date,
    currency: str,
    amount: Decimal,
    amount_ron: Decimal,
    bank_fee_amount: Decimal,
    withholding_amount: Decimal,
    reference: str | None,
    note: str | None,
    gross_amount: Decimal | None,
    is_owner_movement: bool,
    external_id: str | None,
    created_by_user_id: uuid.UUID,
) -> Row:
    return await returning(
        session,
        f"""
        INSERT INTO payments (
            workspace_id, account_id, direction, kind, method, paid_on, currency, amount,
            amount_ron, bank_fee_amount, withholding_amount, reference, note, gross_amount,
            is_owner_movement, external_id, created_by_user_id
        )
        VALUES (
            :workspace_id, :account_id, :direction, :kind, :method, :paid_on, :currency, :amount,
            :amount_ron, :bank_fee_amount, :withholding_amount, :reference, :note, :gross_amount,
            :is_owner_movement, :external_id, :created_by_user_id
        )
        RETURNING {PAYMENT_COLUMNS}
        """,
        workspace_id=workspace_id,
        account_id=account_id,
        direction=direction,
        kind=kind,
        method=method,
        paid_on=paid_on,
        currency=currency,
        amount=amount,
        amount_ron=amount_ron,
        bank_fee_amount=bank_fee_amount,
        withholding_amount=withholding_amount,
        reference=reference,
        note=note,
        gross_amount=gross_amount,
        is_owner_movement=is_owner_movement,
        external_id=external_id,
        created_by_user_id=created_by_user_id,
    )


async def insert_allocation(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    payment_id: uuid.UUID,
    document_id: uuid.UUID,
    amount: Decimal,
    currency: str,
    allocation_kind: str,
    fx_difference_amount: Decimal,
    note: str | None,
) -> Row:
    return await returning(
        session,
        f"""
        INSERT INTO payment_allocations (
            workspace_id, payment_id, document_id, amount, currency, allocation_kind,
            fx_difference_amount, note
        )
        VALUES (
            :workspace_id, :payment_id, :document_id, :amount, :currency, :allocation_kind,
            :fx_difference_amount, :note
        )
        RETURNING {ALLOCATION_COLUMNS}
        """,
        workspace_id=workspace_id,
        payment_id=payment_id,
        document_id=document_id,
        amount=amount,
        currency=currency,
        allocation_kind=allocation_kind,
        fx_difference_amount=fx_difference_amount,
        note=note,
    )


async def allocation_totals_by_kind(
    session: AsyncSession, *, workspace_id: uuid.UUID, document_id: uuid.UUID
) -> dict[str, Decimal]:
    """Settled total per allocation kind for one document (also used to lock)."""
    rows = await all_rows(
        session,
        """
        SELECT allocation_kind, coalesce(sum(amount), 0) AS total
          FROM payment_allocations
         WHERE workspace_id = :workspace_id AND document_id = :document_id
         GROUP BY allocation_kind
        """,
        workspace_id=workspace_id,
        document_id=document_id,
    )
    return {row.allocation_kind: Decimal(row.total) for row in rows}


async def list_payments(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    date_from: date | None,
    date_to: date | None,
    limit: int,
) -> list[Row]:
    clause = where(
        "workspace_id = :workspace_id",
        "paid_on >= :date_from" if date_from else None,
        "paid_on <= :date_to" if date_to else None,
    )
    bounded = min(max(int(limit), 1), 200)
    return await all_rows(
        session,
        f"""
        SELECT {PAYMENT_COLUMNS} FROM payments
        {clause}
         ORDER BY paid_on DESC, id DESC
         LIMIT {bounded}
        """,
        workspace_id=workspace_id,
        date_from=date_from,
        date_to=date_to,
    )


async def payment_count_for_period(
    session: AsyncSession, *, workspace_id: uuid.UUID, period_start: date, period_end: date
) -> int:
    return int(
        await val(
            session,
            """
            SELECT count(*) FROM payments
             WHERE workspace_id = :workspace_id
               AND paid_on >= :period_start AND paid_on <= :period_end
            """,
            default=0,
            workspace_id=workspace_id,
            period_start=period_start,
            period_end=period_end,
        )
    )


async def get_payment(
    session: AsyncSession, *, workspace_id: uuid.UUID, payment_id: uuid.UUID
) -> Row | None:
    return await one(
        session,
        f"""
        SELECT {PAYMENT_COLUMNS} FROM payments
         WHERE id = :id AND workspace_id = :workspace_id
        """,
        id=payment_id,
        workspace_id=workspace_id,
    )


async def list_allocations(session: AsyncSession, *, payment_id: uuid.UUID) -> list[Row]:
    """What each payment covers, with the document it is applied to."""
    return await all_rows(
        session,
        """
        SELECT a.id, a.document_id, a.amount, a.currency, a.allocation_kind,
               a.fx_difference_amount, a.note,
               d.series, d.number, d.issue_date, d.total_amount,
               d.id IS NOT NULL AS document_exists
          FROM payment_allocations a
          LEFT JOIN documents d ON d.id = a.document_id
         WHERE a.payment_id = :payment_id
         ORDER BY a.created_at
        """,
        payment_id=payment_id,
    )


async def update_payment(
    session: AsyncSession, *, payment_id: uuid.UUID, fields: dict
) -> Row:
    assignments = ", ".join(f"{column} = :{column}" for column in fields)
    return await returning(
        session,
        f"""
        UPDATE payments SET {assignments}, updated_at = now()
         WHERE id = :payment_id
        RETURNING {PAYMENT_COLUMNS}
        """,
        payment_id=payment_id,
        **fields,
    )


async def delete_allocations_for_payment(
    session: AsyncSession, *, payment_id: uuid.UUID
) -> int:
    return await run(
        session,
        "DELETE FROM payment_allocations WHERE payment_id = :payment_id",
        payment_id=payment_id,
    )


async def delete_payment(session: AsyncSession, *, payment_id: uuid.UUID) -> int:
    return await run(session, "DELETE FROM payments WHERE id = :payment_id", payment_id=payment_id)


async def update_allocation_amount(
    session: AsyncSession, *, allocation_id: uuid.UUID, amount: Decimal
) -> Row:
    return await returning(
        session,
        """
        UPDATE payment_allocations SET amount = :amount, updated_at = now()
         WHERE id = :allocation_id
        RETURNING id, document_id, amount
        """,
        allocation_id=allocation_id,
        amount=amount,
    )
