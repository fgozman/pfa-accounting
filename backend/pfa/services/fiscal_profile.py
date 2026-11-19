"""PFA fiscal profile: identity, VAT registrations, tax regimes and yearly facts.

Effective-dated history is appended, never overwritten: a change closes the
previous open period on the day the new one starts, and mutually exclusive
periods cannot overlap.  A change that affects which forms are due re-evaluates
the obligations (and their notifications) of the affected years in the same
transaction.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, fields
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pfa.errors import Conflict
from pfa.models.tenancy import PfaProfile, TaxpayerYearFact, TaxRegime, VatRegistration
from pfa.services import facts as facts_service
from pfa.services import notifications as notifications_service
from pfa.services import obligations as obligations_service
from pfa.services import tenancy
from pfa.services.audit import record_audit

#: plain profile fields a change may set (``None`` means "leave unchanged")
PROFILE_FIELDS = (
    "legal_name",
    "county",
    "locality",
    "iban",
    "registered_address",
    "fiscal_domicile",
    "phone",
)


@dataclass(frozen=True, slots=True)
class FiscalProfileChanges:
    legal_name: str | None = None
    cui: str | None = None
    #: when the activity started; drives whether the initial registration is due
    started_on: date | None = None
    county: str | None = None
    locality: str | None = None
    iban: str | None = None
    registered_address: str | None = None
    fiscal_domicile: str | None = None
    phone: str | None = None
    vat_registrations: list[dict[str, Any]] | None = None
    tax_regimes: list[dict[str, Any]] | None = None
    year_facts: list[dict[str, Any]] | None = None

    def provided_fields(self) -> list[str]:
        return sorted(item.name for item in fields(self) if getattr(self, item.name) is not None)


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


async def get_fiscal_profile(
    session: AsyncSession, *, workspace_id: uuid.UUID, income_year: int | None = None
) -> dict:
    profile = (
        await session.execute(
            select(PfaProfile)
            .where(PfaProfile.workspace_id == workspace_id, PfaProfile.active.is_(True))
            .order_by(PfaProfile.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    registrations = (
        (
            await session.execute(
                select(VatRegistration)
                .where(VatRegistration.workspace_id == workspace_id)
                .order_by(VatRegistration.effective_from)
            )
        )
        .scalars()
        .all()
    )
    regimes = (
        (
            await session.execute(
                select(TaxRegime)
                .where(TaxRegime.workspace_id == workspace_id)
                .order_by(TaxRegime.income_year, TaxRegime.effective_from)
            )
        )
        .scalars()
        .all()
    )
    facts_statement = select(TaxpayerYearFact).where(TaxpayerYearFact.workspace_id == workspace_id)
    if income_year is not None:
        facts_statement = facts_statement.where(TaxpayerYearFact.income_year == income_year)
    facts = (await session.execute(facts_statement)).scalars().all()
    # The profile row is created on first write; until then the workspace's own
    # name and CUI are the effective identity, so they must never render empty.
    workspace = await tenancy.get_workspace(session, workspace_id)
    profile_view = {
        "legal_name": (profile.legal_name if profile else None)
        or (workspace.name if workspace else None),
        "started_on": (profile.started_on.isoformat() if profile and profile.started_on else None),
        "cui": (profile.cui if profile else None) or (workspace.cui if workspace else None),
        "county": profile.county if profile else None,
        "locality": profile.locality if profile else None,
        "iban": profile.iban if profile else None,
        "registered_address": profile.registered_address if profile else None,
        "fiscal_domicile": profile.fiscal_domicile if profile else None,
        "phone": profile.phone if profile else None,
    }
    return {
        "profile": profile_view,
        "vat_registrations": [
            {
                "id": str(item.id),
                "kind": item.kind,
                "vat_code": item.vat_code,
                "effective_from": item.effective_from.date().isoformat(),
                "effective_to": item.effective_to.date().isoformat() if item.effective_to else None,
                "reporting_frequency": item.reporting_frequency,
                "cash_accounting": item.cash_accounting,
            }
            for item in registrations
        ],
        "tax_regimes": [
            {
                "id": str(item.id),
                "kind": item.kind,
                "income_year": item.income_year,
                "effective_from": item.effective_from.date().isoformat(),
                "effective_to": item.effective_to.date().isoformat() if item.effective_to else None,
                "norm_county": item.norm_county,
                "norm_locality": item.norm_locality,
                "norm_activity": item.norm_activity,
                "norm_amount": str(item.norm_amount) if item.norm_amount is not None else None,
            }
            for item in regimes
        ],
        "year_facts": [
            {
                "fact_key": fact.fact_key,
                "income_year": fact.income_year,
                "value_kind": fact.value_kind,
                "value": (
                    fact.value_text
                    if fact.value_text is not None
                    else str(fact.value_decimal)
                    if fact.value_decimal is not None
                    else fact.value_boolean
                ),
                "answered": fact.answered_at is not None,
                "evidence_reference": fact.evidence_reference,
            }
            for fact in facts
        ],
    }


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


async def update_fiscal_profile(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    changes: FiscalProfileChanges,
) -> uuid.UUID:
    """Apply a profile change and return the profile id."""
    profile = (
        await session.execute(
            select(PfaProfile)
            .where(PfaProfile.workspace_id == workspace_id, PfaProfile.active.is_(True))
            .limit(1)
        )
    ).scalar_one_or_none()
    if profile is None:
        workspace = await tenancy.get_workspace(session, workspace_id)
        profile = PfaProfile(
            workspace_id=workspace_id,
            legal_name=changes.legal_name or (workspace.name if workspace else None) or "PFA",
            cui=changes.cui or (workspace.cui if workspace else None) or "",
            started_on=changes.started_on,
            created_by_user_id=actor_user_id,
        )
        session.add(profile)
    else:
        if changes.started_on is not None:
            profile.started_on = changes.started_on
        for field_name in PROFILE_FIELDS:
            value = getattr(changes, field_name)
            if value is not None:
                setattr(profile, field_name, value)
        if changes.cui:
            profile.cui = changes.cui
    await session.flush()

    for entry in changes.vat_registrations or []:
        await _upsert_vat_registration(session, workspace_id, entry)

    for entry in changes.tax_regimes or []:
        await _upsert_tax_regime(session, workspace_id, entry)

    # one implementation for the facts, whether they arrive here or at /tax-facts
    for year, answers in _group_year_facts(changes.year_facts or []).items():
        await facts_service.answer_facts(
            session,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            income_year=year,
            answers=answers,
        )

    # A registration, a regime or the activity start changes which forms are due, so the
    # obligations for the affected years are recomputed here: otherwise the dashboard keeps
    # showing the state from before the change until something else reconciles.
    for year in _affected_years(changes):
        obligations = await obligations_service.evaluate_obligations(
            session, workspace_id=workspace_id, income_year=year
        )
        await notifications_service.evaluate_notifications(
            session, workspace_id=workspace_id, obligations=obligations
        )

    await session.flush()
    await record_audit(
        session,
        action="fiscal_profile.updated",
        entity_type="pfa_profile",
        entity_id=profile.id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        after={"fields": changes.provided_fields()},
    )
    return profile.id


def _affected_years(changes: FiscalProfileChanges) -> list[int]:
    """Years whose obligations a fiscal-profile change can affect."""
    years: set[int] = set()
    if changes.started_on is not None:
        years.add(changes.started_on.year)
    for entry in changes.vat_registrations or []:
        effective_from = entry.get("effective_from")
        if effective_from:
            years.add(int(str(effective_from)[:4]))
    for entry in changes.tax_regimes or []:
        if entry.get("income_year"):
            years.add(int(entry["income_year"]))
    return sorted(years)


def _group_year_facts(entries: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Group the generic ``year_facts`` payload by year for the facts service."""
    grouped: dict[int, dict[str, Any]] = {}
    for entry in entries:
        grouped.setdefault(int(entry["income_year"]), {})[str(entry["fact_key"])] = entry.get(
            "value"
        )
    return grouped


def _parse_ts(value: Any) -> datetime:
    """Parse an ISO date/datetime into an aware timestamp.

    ``vat_registrations`` and ``tax_regimes`` are ``timestamptz`` columns; comparing
    them with the naive value ``date.fromisoformat`` produces would raise, so every
    input is normalised to UTC.
    """
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


async def _upsert_vat_registration(
    session: AsyncSession, workspace_id: uuid.UUID, entry: dict
) -> None:
    """Apply one VAT registration, keeping exactly one open period.

    Effective-dated changes are appended, not overwritten: a change closes the
    previous open registration on the day the new one starts, and saving the same
    values twice is idempotent (no duplicate rows).
    """
    kind = str(entry["kind"])
    effective_from = _parse_ts(entry["effective_from"])
    effective_to = _parse_ts(entry["effective_to"]) if entry.get("effective_to") else None
    if effective_to is not None and effective_to < effective_from:
        raise Conflict("Perioada de valabilitate a înregistrării TVA este invalidă.")

    existing = (
        await session.execute(
            select(VatRegistration).where(
                VatRegistration.workspace_id == workspace_id,
                VatRegistration.kind == kind,
                VatRegistration.effective_from == effective_from,
            )
        )
    ).scalar_one_or_none()
    open_rows = (
        (
            await session.execute(
                select(VatRegistration).where(
                    VatRegistration.workspace_id == workspace_id,
                    VatRegistration.effective_to.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )

    target = existing
    for row in open_rows:
        if row is existing:
            continue
        if row.effective_from < effective_from:
            row.effective_to = effective_from
        elif row.effective_from == effective_from:
            target = row  # same day, new kind: supersede in place
        else:
            raise Conflict("Perioada se suprapune cu o înregistrare TVA existentă.")
    if target is None:
        target = VatRegistration(workspace_id=workspace_id)
        session.add(target)
    target.kind = kind
    target.effective_from = effective_from
    target.effective_to = effective_to
    target.vat_code = entry.get("vat_code")
    target.reporting_frequency = entry.get("reporting_frequency")
    target.cash_accounting = bool(entry.get("cash_accounting", False))
    target.evidence_reference = entry.get("evidence_reference")


async def _upsert_tax_regime(session: AsyncSession, workspace_id: uuid.UUID, entry: dict) -> None:
    """Apply one tax regime for an income year, keeping one open period."""
    kind = str(entry["kind"])
    income_year = int(entry["income_year"])
    effective_from = _parse_ts(entry["effective_from"])
    effective_to = _parse_ts(entry["effective_to"]) if entry.get("effective_to") else None
    if effective_to is not None and effective_to < effective_from:
        raise Conflict("Perioada de valabilitate a regimului fiscal este invalidă.")

    existing = (
        await session.execute(
            select(TaxRegime).where(
                TaxRegime.workspace_id == workspace_id,
                TaxRegime.income_year == income_year,
                TaxRegime.kind == kind,
                TaxRegime.effective_from == effective_from,
            )
        )
    ).scalar_one_or_none()
    open_rows = (
        (
            await session.execute(
                select(TaxRegime).where(
                    TaxRegime.workspace_id == workspace_id,
                    TaxRegime.income_year == income_year,
                    TaxRegime.effective_to.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )

    target = existing
    for row in open_rows:
        if row is existing:
            continue
        if row.effective_from < effective_from:
            row.effective_to = effective_from
        elif row.effective_from == effective_from:
            target = row  # same day, new kind: supersede in place
        else:
            raise Conflict("Perioada se suprapune cu un regim fiscal existent.")
    if target is None:
        target = TaxRegime(workspace_id=workspace_id)
        session.add(target)
    target.kind = kind
    target.income_year = income_year
    target.effective_from = effective_from
    target.effective_to = effective_to
    target.norm_county = entry.get("norm_county")
    target.norm_locality = entry.get("norm_locality")
    target.norm_activity = entry.get("norm_activity")
    target.norm_amount = Decimal(str(entry["norm_amount"])) if entry.get("norm_amount") else None
    target.norm_source_url = entry.get("norm_source_url")
