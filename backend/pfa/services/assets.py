"""Assets, depreciation schedules, disposals and inventory.

A capital asset never receives both a full expense deduction and depreciation:
when a document is capitalised, the expense is replaced by the depreciation
schedule, and the disposal updates the schedule and records the result.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from pfa.errors import Conflict, NotFound, ValidationFailed
from pfa.models.base import AssetStatus, DepreciationMethod
from pfa.repo import books as books_repo
from pfa.repo import documents as documents_repo
from pfa.services.audit import emit_event, record_audit

ZERO = Decimal("0")
HUNDRED = Decimal("100")


@dataclass(frozen=True, slots=True)
class AssetInput:
    name: str
    category: str
    acquisition_date: date
    in_service_date: date
    acquisition_cost: Decimal
    acquisition_cost_ron: Decimal
    recoverable_vat: Decimal = ZERO
    nonrecoverable_vat: Decimal = ZERO
    currency: str = "RON"
    business_use_percent: Decimal = Decimal("100")
    depreciation_method: DepreciationMethod = DepreciationMethod.STRAIGHT_LINE
    useful_life_months: int = 60
    document_id: uuid.UUID | None = None


DEFAULT_USEFUL_LIFE_MONTHS = {
    "buildings": 480,
    "equipment": 96,
    "computers": 36,
    "vehicles": 72,
    "furniture": 108,
    "intangibles": 36,
}


def default_useful_life(category: str) -> int:
    return DEFAULT_USEFUL_LIFE_MONTHS.get(category.lower(), 60)


async def create_asset(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    payload: AssetInput,
):
    if payload.acquisition_cost <= ZERO:
        raise ValidationFailed("Costul de achiziție trebuie să fie pozitiv.")
    if not 1 <= payload.useful_life_months <= 600:
        raise ValidationFailed("Durata de utilizare trebuie să fie între 1 și 600 de luni.")
    if not ZERO <= payload.business_use_percent <= HUNDRED:
        raise ValidationFailed("Ponderea de utilizare în scop de afaceri este invalidă.")
    if payload.document_id is not None:
        document = await documents_repo.get_document(
            session, workspace_id=workspace_id, document_id=payload.document_id
        )
        if document is None:
            raise NotFound("Documentul de achiziție nu există în acest spațiu de lucru.")

    asset = await books_repo.insert_asset(
        session,
        workspace_id=workspace_id,
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
        depreciation_method=payload.depreciation_method.value,
        useful_life_months=payload.useful_life_months,
        document_id=payload.document_id,
        created_by_user_id=actor_user_id,
    )

    # capitalising replaces the expense with depreciation for the same document
    if payload.document_id is not None:
        await books_repo.insert_tax_adjustment(
            session,
            workspace_id=workspace_id,
            income_year=payload.in_service_date.year,
            adjustment_kind="capitalized_expense_reversal",
            direction="increase",
            amount=payload.acquisition_cost_ron,
            document_id=payload.document_id,
            asset_id=asset.id,
            legal_reference="Cod fiscal art. 24-28",
            explanation=(
                "Cheltuiala nu se deduce integral: activul se amortizează pe durata "
                "de utilizare."
            ),
        )

    await build_schedule(session, workspace_id=workspace_id, asset=asset)
    await emit_event(
        session,
        event_type="asset.created",
        workspace_id=workspace_id,
        payload={"asset_id": str(asset.id)},
        dedupe_key=f"asset-created:{asset.id}",
    )
    await record_audit(
        session,
        action="asset.created",
        entity_type="asset",
        entity_id=asset.id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        after={"name": asset.name, "cost": str(asset.acquisition_cost_ron)},
    )
    return asset


def _monthly_depreciation(asset) -> Decimal:
    base = asset.acquisition_cost_ron
    return (base / Decimal(asset.useful_life_months)).quantize(Decimal("0.000001"))


def _add_months(day: date, months: int) -> date:
    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


async def build_schedule(session: AsyncSession, *, workspace_id: uuid.UUID, asset):
    """Straight-line monthly schedule, capped so it never exceeds the cost."""
    existing = await books_repo.existing_depreciation(
        session, workspace_id=workspace_id, asset_id=asset.id
    )
    if existing:
        return list(existing)

    monthly = _monthly_depreciation(asset)
    business_use = asset.business_use_percent / HUNDRED
    book_value = asset.acquisition_cost_ron
    entries = []
    start = asset.in_service_date
    # Romanian practice: depreciation starts the month after the in-service month
    for offset in range(asset.useful_life_months):
        period_day = _add_months(start, offset + 1)
        if asset.disposal_date and period_day > asset.disposal_date:
            break
        amount = monthly
        if amount > book_value:
            amount = book_value
        book_value = (book_value - amount).quantize(Decimal("0.000001"))
        entries.append(
            await books_repo.insert_depreciation_entry(
                session,
                workspace_id=workspace_id,
                asset_id=asset.id,
                income_year=period_day.year,
                period_month=period_day.month,
                amount=amount,
                deductible_amount=(amount * business_use).quantize(Decimal("0.000001")),
                book_value_after=book_value,
                note=f"Amortizare liniară {asset.useful_life_months} luni",
            )
        )
        if book_value <= ZERO:
            break
    return entries


async def dispose_asset(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    asset_id: uuid.UUID,
    disposal_date: date,
    proceeds: Decimal,
    notes: str | None = None,
):
    asset = await books_repo.get_asset(session, workspace_id=workspace_id, asset_id=asset_id)
    if asset is None:
        raise NotFound("Activul nu există.")
    if asset.status == AssetStatus.DISPOSED:
        raise Conflict("Activul este deja scos din evidență.")
    if disposal_date < asset.in_service_date:
        raise ValidationFailed("Data scoaterii din evidență nu poate precede punerea în funcțiune.")

    entries = await books_repo.existing_depreciation(
        session, workspace_id=workspace_id, asset_id=asset.id
    )
    # the remaining book value is computed before the affected entries are removed
    remaining_base = asset.acquisition_cost_ron
    for entry in entries:
        after_disposal = entry.income_year > disposal_date.year or (
            entry.income_year == disposal_date.year and (entry.period_month or 0) > disposal_date.month
        )
        if not after_disposal:
            remaining_base -= entry.amount

    updated = await books_repo.mark_asset_disposed(
        session,
        workspace_id=workspace_id,
        asset_id=asset.id,
        disposal_date=disposal_date,
        proceeds=proceeds,
        notes=notes,
    )
    await books_repo.delete_depreciation_after(
        session,
        workspace_id=workspace_id,
        asset_id=asset.id,
        month=disposal_date.month,
        year=disposal_date.year,
    )
    result = (proceeds - remaining_base).quantize(Decimal("0.000001"))
    await books_repo.insert_tax_adjustment(
        session,
        workspace_id=workspace_id,
        income_year=disposal_date.year,
        adjustment_kind="asset_disposal_result",
        direction="decrease" if result > ZERO else "increase",
        amount=abs(result),
        asset_id=asset.id,
        legal_reference="Cod fiscal art. 26, 28",
        explanation="Rezultatul scoaterii din evidență ajustează venitul impozabil.",
    )
    await record_audit(
        session,
        action="asset.disposed",
        entity_type="asset",
        entity_id=asset.id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        after={"disposal_date": disposal_date.isoformat(), "proceeds": str(proceeds)},
    )
    return updated


async def year_depreciation(session: AsyncSession, *, workspace_id: uuid.UUID, income_year: int):
    return await books_repo.depreciation_for_year(
        session, workspace_id=workspace_id, year=income_year
    )


async def add_inventory_entry(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    income_year: int,
    entry_kind: str,
    occurred_on: date,
    item_name: str,
    quantity: Decimal,
    unit_cost: Decimal,
):
    if entry_kind not in {"movement", "valuation"}:
        raise ValidationFailed("Tipul de înregistrare de inventar este invalid.")
    return await books_repo.insert_inventory_entry(
        session,
        workspace_id=workspace_id,
        income_year=income_year,
        entry_kind=entry_kind,
        occurred_on=occurred_on,
        item_name=item_name,
        quantity=quantity,
        unit_cost=unit_cost,
        value=(quantity * unit_cost).quantize(Decimal("0.000001")),
    )


async def inventory_value(session: AsyncSession, *, workspace_id: uuid.UUID, income_year: int) -> Decimal:
    entries = await books_repo.inventory_for_year(
        session, workspace_id=workspace_id, year=income_year
    )
    total = ZERO
    for entry in entries:
        total += entry.value if entry.entry_kind == "movement" else ZERO
    return total.quantize(Decimal("0.000001"))
