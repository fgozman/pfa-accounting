"""Book-keeping SQL: accounting events, tax adjustments, assets, depreciation,
inventory, period locks and register exports."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from pfa.repo.query import Row, all_rows, one, returning, run, val, where
from pfa.repo.query import jsonb as to_jsonb

ASSET_COLUMNS = """
    id, name, category, acquisition_date, in_service_date, acquisition_cost, recoverable_vat,
    nonrecoverable_vat, currency, acquisition_cost_ron, business_use_percent,
    depreciation_method, useful_life_months, status, document_id, disposal_date,
    disposal_proceeds, disposal_notes, created_by_user_id
"""

ACCOUNTING_EVENT_COLUMNS = """
    id, event_date, event_kind, document_id, payment_id, asset_id, amount_ron, vat_amount_ron,
    taxable_amount_ron, recognition, category, description, feeds_forms, created_by_user_id
"""


# ---------------------------------------------------------------------------
# Accounting events
# ---------------------------------------------------------------------------


async def insert_accounting_event(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    event_date: date,
    event_kind: str,
    document_id: uuid.UUID | None = None,
    payment_id: uuid.UUID | None = None,
    asset_id: uuid.UUID | None = None,
    amount_ron: Decimal = Decimal("0"),
    vat_amount_ron: Decimal = Decimal("0"),
    taxable_amount_ron: Decimal = Decimal("0"),
    recognition: str = "realization",
    category: str | None = None,
    description: str | None = None,
    feeds_forms: list | None = None,
    created_by_user_id: uuid.UUID | None = None,
) -> Row:
    return await returning(
        session,
        f"""
        INSERT INTO accounting_events (
            workspace_id, event_date, event_kind, document_id, payment_id, asset_id, amount_ron,
            vat_amount_ron, taxable_amount_ron, recognition, category, description, feeds_forms,
            created_by_user_id
        )
        VALUES (
            :workspace_id, :event_date, :event_kind, :document_id, :payment_id, :asset_id,
            :amount_ron, :vat_amount_ron, :taxable_amount_ron, :recognition, :category,
            :description, CAST(:feeds_forms AS jsonb), :created_by_user_id
        )
        RETURNING {ACCOUNTING_EVENT_COLUMNS}
        """,
        workspace_id=workspace_id,
        event_date=event_date,
        event_kind=event_kind,
        document_id=document_id,
        payment_id=payment_id,
        asset_id=asset_id,
        amount_ron=amount_ron,
        vat_amount_ron=vat_amount_ron,
        taxable_amount_ron=taxable_amount_ron,
        recognition=recognition,
        category=category,
        description=description,
        feeds_forms=to_jsonb(feeds_forms or []),
        created_by_user_id=created_by_user_id,
    )


async def realized_revenue_events(
    session: AsyncSession, *, workspace_id: uuid.UUID, year: int
) -> list[Row]:
    """Cash-recognized revenue events of an income year."""
    return await all_rows(
        session,
        f"""
        SELECT {ACCOUNTING_EVENT_COLUMNS} FROM accounting_events
         WHERE workspace_id = :workspace_id
           AND event_kind = 'revenue'
           AND recognition = 'realization'
           AND event_date >= :year_start AND event_date <= :year_end
         ORDER BY event_date
        """,
        workspace_id=workspace_id,
        year_start=date(year, 1, 1),
        year_end=date(year, 12, 31),
    )


# ---------------------------------------------------------------------------
# Assets and depreciation
# ---------------------------------------------------------------------------


async def insert_asset(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    name: str,
    category: str,
    acquisition_date: date,
    in_service_date: date,
    acquisition_cost: Decimal,
    acquisition_cost_ron: Decimal,
    recoverable_vat: Decimal,
    nonrecoverable_vat: Decimal,
    currency: str,
    business_use_percent: Decimal,
    depreciation_method: str,
    useful_life_months: int,
    document_id: uuid.UUID | None,
    created_by_user_id: uuid.UUID,
) -> Row:
    return await returning(
        session,
        f"""
        INSERT INTO assets (
            workspace_id, name, category, acquisition_date, in_service_date, acquisition_cost,
            acquisition_cost_ron, recoverable_vat, nonrecoverable_vat, currency,
            business_use_percent, depreciation_method, useful_life_months, status, document_id,
            created_by_user_id
        )
        VALUES (
            :workspace_id, :name, :category, :acquisition_date, :in_service_date,
            :acquisition_cost, :acquisition_cost_ron, :recoverable_vat, :nonrecoverable_vat,
            :currency, :business_use_percent, :depreciation_method, :useful_life_months,
            'in_service', :document_id, :created_by_user_id
        )
        RETURNING {ASSET_COLUMNS}
        """,
        workspace_id=workspace_id,
        name=name,
        category=category,
        acquisition_date=acquisition_date,
        in_service_date=in_service_date,
        acquisition_cost=acquisition_cost,
        acquisition_cost_ron=acquisition_cost_ron,
        recoverable_vat=recoverable_vat,
        nonrecoverable_vat=nonrecoverable_vat,
        currency=currency,
        business_use_percent=business_use_percent,
        depreciation_method=depreciation_method,
        useful_life_months=useful_life_months,
        document_id=document_id,
        created_by_user_id=created_by_user_id,
    )


async def get_asset(session: AsyncSession, *, workspace_id: uuid.UUID, asset_id: uuid.UUID) -> Row | None:
    return await one(
        session,
        f"SELECT {ASSET_COLUMNS} FROM assets WHERE id = :asset_id AND workspace_id = :workspace_id",
        asset_id=asset_id,
        workspace_id=workspace_id,
    )


async def list_assets_for_schedule(
    session: AsyncSession, *, workspace_id: uuid.UUID, status: str | None = None
) -> list[Row]:
    clause = where("workspace_id = :workspace_id", "status = :status" if status else None)
    return await all_rows(
        session,
        f"""
        SELECT {ASSET_COLUMNS} FROM assets
        {clause}
         ORDER BY in_service_date, name
        """,
        workspace_id=workspace_id,
        status=status,
    )


async def existing_depreciation(
    session: AsyncSession, *, workspace_id: uuid.UUID, asset_id: uuid.UUID
) -> list[Row]:
    return await all_rows(
        session,
        """
        SELECT id, asset_id, income_year, period_month, amount, deductible_amount,
               book_value_after
          FROM depreciation_entries
         WHERE workspace_id = :workspace_id AND asset_id = :asset_id
         ORDER BY income_year, period_month
        """,
        workspace_id=workspace_id,
        asset_id=asset_id,
    )


async def insert_depreciation_entry(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    asset_id: uuid.UUID,
    income_year: int,
    period_month: int,
    amount: Decimal,
    deductible_amount: Decimal,
    book_value_after: Decimal,
    note: str | None,
) -> Row:
    return await returning(
        session,
        """
        INSERT INTO depreciation_entries (
            workspace_id, asset_id, income_year, period_month, amount, deductible_amount,
            book_value_after, calculation_note
        )
        VALUES (
            :workspace_id, :asset_id, :income_year, :period_month, :amount, :deductible_amount,
            :book_value_after, :note
        )
        RETURNING id, asset_id, income_year, period_month, amount, deductible_amount,
                  book_value_after
        """,
        workspace_id=workspace_id,
        asset_id=asset_id,
        income_year=income_year,
        period_month=period_month,
        amount=amount,
        deductible_amount=deductible_amount,
        book_value_after=book_value_after,
        note=note,
    )


async def delete_depreciation_after(
    session: AsyncSession, *, workspace_id: uuid.UUID, asset_id: uuid.UUID, month: int, year: int
) -> int:
    return await run(
        session,
        """
        DELETE FROM depreciation_entries
         WHERE workspace_id = :workspace_id AND asset_id = :asset_id
           AND (income_year > :year OR (income_year = :year AND period_month > :month))
        """,
        workspace_id=workspace_id,
        asset_id=asset_id,
        year=year,
        month=month,
    )


async def mark_asset_disposed(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    asset_id: uuid.UUID,
    disposal_date: date,
    proceeds: Decimal,
    notes: str | None,
) -> Row:
    return await returning(
        session,
        f"""
        UPDATE assets
           SET status = 'disposed', disposal_date = :disposal_date, disposal_proceeds = :proceeds,
               disposal_notes = :notes, updated_at = now()
         WHERE id = :asset_id AND workspace_id = :workspace_id
        RETURNING {ASSET_COLUMNS}
        """,
        workspace_id=workspace_id,
        asset_id=asset_id,
        disposal_date=disposal_date,
        proceeds=proceeds,
        notes=notes,
    )


async def depreciation_for_year(
    session: AsyncSession, *, workspace_id: uuid.UUID, year: int
) -> list[Row]:
    return await all_rows(
        session,
        """
        SELECT id, asset_id, income_year, period_month, amount, deductible_amount,
               book_value_after
          FROM depreciation_entries
         WHERE workspace_id = :workspace_id AND income_year = :year
         ORDER BY asset_id, period_month
        """,
        workspace_id=workspace_id,
        year=year,
    )


# ---------------------------------------------------------------------------
# Tax adjustments, inventory, period locks, register exports
# ---------------------------------------------------------------------------


async def insert_tax_adjustment(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    income_year: int,
    adjustment_kind: str,
    direction: str,
    amount: Decimal,
    document_id: uuid.UUID | None = None,
    asset_id: uuid.UUID | None = None,
    legal_reference: str | None = None,
    explanation: str | None = None,
    computed_by: str = "engine",
) -> Row:
    return await returning(
        session,
        """
        INSERT INTO tax_adjustments (
            workspace_id, income_year, adjustment_kind, direction, amount, document_id, asset_id,
            legal_reference, explanation, computed_by
        )
        VALUES (
            :workspace_id, :income_year, :adjustment_kind, :direction, :amount, :document_id,
            :asset_id, :legal_reference, :explanation, :computed_by
        )
        ON CONFLICT (workspace_id, income_year, adjustment_kind, accounting_event_id, document_id, asset_id)
            DO NOTHING
        RETURNING id, income_year, adjustment_kind, direction, amount
        """,
        workspace_id=workspace_id,
        income_year=income_year,
        adjustment_kind=adjustment_kind,
        direction=direction,
        amount=amount,
        document_id=document_id,
        asset_id=asset_id,
        legal_reference=legal_reference,
        explanation=explanation,
        computed_by=computed_by,
    )


async def tax_adjustments_for_year(
    session: AsyncSession, *, workspace_id: uuid.UUID, year: int
) -> list[Row]:
    return await all_rows(
        session,
        """
        SELECT id, income_year, adjustment_kind, direction, amount, document_id, asset_id,
               legal_reference, explanation
          FROM tax_adjustments
         WHERE workspace_id = :workspace_id AND income_year = :year
         ORDER BY adjustment_kind
        """,
        workspace_id=workspace_id,
        year=year,
    )


async def insert_inventory_entry(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    income_year: int,
    entry_kind: str,
    occurred_on: date,
    item_name: str,
    quantity: Decimal,
    unit_cost: Decimal,
    value: Decimal,
) -> Row:
    return await returning(
        session,
        """
        INSERT INTO inventory_entries (
            workspace_id, income_year, entry_kind, occurred_on, item_name, quantity, unit_cost, value
        )
        VALUES (
            :workspace_id, :income_year, :entry_kind, :occurred_on, :item_name, :quantity,
            :unit_cost, :value
        )
        RETURNING id, income_year, entry_kind, occurred_on, item_name, quantity, unit_cost, value
        """,
        workspace_id=workspace_id,
        income_year=income_year,
        entry_kind=entry_kind,
        occurred_on=occurred_on,
        item_name=item_name,
        quantity=quantity,
        unit_cost=unit_cost,
        value=value,
    )


async def inventory_for_year(
    session: AsyncSession, *, workspace_id: uuid.UUID, year: int | None
) -> list[Row]:
    clause = where("workspace_id = :workspace_id", "income_year = :year" if year else None)
    return await all_rows(
        session,
        f"""
        SELECT id, income_year, entry_kind, occurred_on, item_name, quantity, unit_cost, value
          FROM inventory_entries
        {clause}
         ORDER BY occurred_on
        """,
        workspace_id=workspace_id,
        year=year,
    )


async def insert_period_lock(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    period_start: date,
    period_end: date,
    period_kind: str,
    locked_by_user_id: uuid.UUID,
    revision: int,
    checklist: dict,
    is_open: bool,
) -> Row:
    return await returning(
        session,
        """
        INSERT INTO period_locks (
            workspace_id, period_start, period_end, period_kind, locked_by_user_id, revision,
            close_checklist, is_open
        )
        VALUES (
            :workspace_id, :period_start, :period_end, :period_kind, :locked_by_user_id,
            :revision, CAST(:checklist AS jsonb), :is_open
        )
        RETURNING id, period_start, period_end, revision, is_open, close_checklist
        """,
        workspace_id=workspace_id,
        period_start=period_start,
        period_end=period_end,
        period_kind=period_kind,
        locked_by_user_id=locked_by_user_id,
        revision=revision,
        checklist=to_jsonb(checklist),
        is_open=is_open,
    )


async def get_period_lock_for_update(
    session: AsyncSession, *, workspace_id: uuid.UUID, period_lock_id: uuid.UUID
) -> Row | None:
    return await one(
        session,
        """
        SELECT id, period_start, period_end, period_kind, revision, is_open, close_checklist
          FROM period_locks
         WHERE id = :id AND workspace_id = :workspace_id
         FOR UPDATE
        """,
        id=period_lock_id,
        workspace_id=workspace_id,
    )


async def closed_period_exists(
    session: AsyncSession, *, workspace_id: uuid.UUID, period_start: date, period_end: date
) -> bool:
    return bool(
        await val(
            session,
            """
            SELECT 1 FROM period_locks
             WHERE workspace_id = :workspace_id
               AND period_start = :period_start AND period_end = :period_end
               AND is_open = false
            """,
            default=0,
            workspace_id=workspace_id,
            period_start=period_start,
            period_end=period_end,
        )
    )


async def latest_period_revision(
    session: AsyncSession, *, workspace_id: uuid.UUID, period_start: date, period_end: date
) -> int | None:
    value = await val(
        session,
        """
        SELECT max(revision) FROM period_locks
         WHERE workspace_id = :workspace_id
           AND period_start = :period_start AND period_end = :period_end
        """,
        workspace_id=workspace_id,
        period_start=period_start,
        period_end=period_end,
    )
    return int(value) if value is not None else None


async def reopen_period_lock(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    period_lock_id: uuid.UUID,
    reason: str,
    reopened_by_user_id: uuid.UUID,
) -> Row:
    return await returning(
        session,
        """
        UPDATE period_locks
           SET is_open = true, reopened_at = now(), reopened_by_user_id = :reopened_by_user_id,
               reopen_reason = :reason
         WHERE id = :id AND workspace_id = :workspace_id
        RETURNING id, period_start, period_end, revision, is_open
        """,
        id=period_lock_id,
        workspace_id=workspace_id,
        reason=reason,
        reopened_by_user_id=reopened_by_user_id,
    )


async def insert_register_export(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    register_type: str,
    period_start: date,
    period_end: date,
    export_format: str,
    status: str,
    object_key: str,
    sha256: str,
    row_count: int,
) -> Row:
    return await returning(
        session,
        """
        INSERT INTO register_exports (
            workspace_id, register_type, period_start, period_end, format, status, object_key,
            sha256, row_count
        )
        VALUES (
            :workspace_id, :register_type, :period_start, :period_end, :export_format, :status,
            :object_key, :sha256, :row_count
        )
        RETURNING id, register_type, period_start, period_end, format, status, object_key,
                  sha256, row_count, job_id
        """,
        workspace_id=workspace_id,
        register_type=register_type,
        period_start=period_start,
        period_end=period_end,
        export_format=export_format,
        status=status,
        object_key=object_key,
        sha256=sha256,
        row_count=row_count,
    )


async def set_register_export_job(
    session: AsyncSession, *, export_id: uuid.UUID, job_id: uuid.UUID
) -> None:
    await run(
        session,
        "UPDATE register_exports SET job_id = :job_id WHERE id = :id",
        id=export_id,
        job_id=job_id,
    )


async def delete_accounting_events_for_payment(
    session: AsyncSession, *, workspace_id: uuid.UUID, payment_id: uuid.UUID
) -> int:
    """Remove derived book entries when their source settlement is deleted.

    The independent, append-only audit event records the original payment and deletion.
    """
    return await run(
        session,
        "DELETE FROM accounting_events WHERE workspace_id = :workspace_id AND payment_id = :payment_id",
        workspace_id=workspace_id,
        payment_id=payment_id,
    )


async def update_accounting_events_for_payment(
    session: AsyncSession, *, workspace_id: uuid.UUID, payment_id: uuid.UUID,
    paid_on: date, amount_ron: Decimal,
) -> int:
    """Keep cash-recognition entries aligned with a corrected payment, not its old value.

    A bank-fee entry has its own amount. Owner/loan/tax movements remain non-taxable.
    """
    return await run(
        session,
        """
        UPDATE accounting_events
           SET event_date = :paid_on,
               amount_ron = CASE WHEN category = 'bank_fee' THEN amount_ron ELSE :amount_ron END,
               taxable_amount_ron = CASE
                   WHEN category = 'bank_fee' THEN taxable_amount_ron
                   WHEN event_kind IN ('revenue', 'expense') THEN :amount_ron
                   ELSE taxable_amount_ron END
         WHERE workspace_id = :workspace_id AND payment_id = :payment_id
        """,
        workspace_id=workspace_id,
        payment_id=payment_id,
        paid_on=paid_on,
        amount_ron=amount_ron,
    )
