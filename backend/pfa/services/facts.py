"""Per-year fiscal facts the user answers.

The D212 obligation blocks until every fact it needs is answered, so each fact needs a
place to answer it and a control that matches its type.  A fact is *answered* only when
the user supplies a value; clearing it returns the fact to *unknown*, which stays visible
rather than becoming zero or "no".
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pfa import clock
from pfa.errors import ValidationFailed
from pfa.models.tenancy import TaxpayerYearFact

#: fact kinds, matching the typed columns on ``taxpayer_year_facts``
TEXT, DECIMAL, BOOLEAN = "text", "decimal", "boolean"


@dataclass(frozen=True, slots=True)
class FactDefinition:
    key: str
    value_kind: str
    label_ro: str
    group: str
    choices: tuple[tuple[str, str], ...] = ()
    unit_ro: str | None = None
    help_ro: str | None = None
    #: other answers that make this fact apply, as ``(fact_key, accepted_values)``.  A
    #: norm-only field must not be shown, asked for, or reported while the regime is
    #: "real" — the user would be answering something that cannot matter.
    applies_when: tuple[tuple[str, tuple[str, ...]], ...] = ()


#: Everything a user can answer, in the order the form shows it.  Adding a fact here is
#: what makes it answerable; the declaration builders keep asking for what they need.
FACT_DEFINITIONS: tuple[FactDefinition, ...] = (
    FactDefinition(
        key="regime",
        value_kind=TEXT,
        label_ro="Regimul fiscal al anului",
        group="Regim fiscal",
        choices=(("real", "Sistem real"), ("norm", "Normă de venit")),
        help_ro="Determină ce formulare se aplică și cum se calculează impozitul.",
    ),
    FactDefinition(
        key="norm_amount",
        value_kind=DECIMAL,
        label_ro="Venitul normat anual",
        group="Regim fiscal",
        unit_ro="lei",
        help_ro="Numai pentru norma de venit: suma din nomenclator pentru activitate.",
        applies_when=(("regime", ("norm",)),),
    ),
    FactDefinition(
        key="norm_adjustment_percent",
        value_kind=DECIMAL,
        label_ro="Ajustarea normei",
        group="Regim fiscal",
        unit_ro="%",
        help_ro="Procentul de ajustare aplicat normei, dacă se aplică.",
        applies_when=(("regime", ("norm",)),),
    ),
    FactDefinition(
        key="active_months",
        value_kind=DECIMAL,
        label_ro="Luni de activitate",
        group="Regim fiscal",
        unit_ro="luni",
        help_ro="Câte luni a fost activă activitatea în anul respectiv (1–12).",
        applies_when=(("regime", ("norm",)),),
    ),
    FactDefinition(
        key="cas_election",
        value_kind=BOOLEAN,
        label_ro="Opțiunea CAS (pensie)",
        group="Contribuții",
        help_ro="Ați optat pentru plata contribuției de asigurări sociale?",
    ),
    FactDefinition(
        key="cass_election",
        value_kind=BOOLEAN,
        label_ro="Opțiunea CASS (sănătate)",
        group="Contribuții",
        help_ro="Ați optat pentru plata contribuției de asigurări sociale de sănătate?",
    ),
    FactDefinition(
        key="cas_exempt",
        value_kind=BOOLEAN,
        label_ro="Exceptare CAS",
        group="Contribuții",
        help_ro="Există o excepție legală documentată pentru CAS în anul respectiv?",
    ),
    FactDefinition(
        key="cass_exempt",
        value_kind=BOOLEAN,
        label_ro="Exceptare CASS",
        group="Contribuții",
        help_ro="Există o excepție legală documentată pentru CASS în anul respectiv?",
    ),
    FactDefinition(
        key="salary_income_present",
        value_kind=BOOLEAN,
        label_ro="Venituri din salarii",
        group="Alte situații",
        help_ro="Aveți și venituri din salarii în acest an? Dacă da, completați suma.",
    ),
    FactDefinition(
        key="salary_income_ron",
        value_kind=DECIMAL,
        label_ro="Suma veniturilor din salarii",
        group="Alte situații",
        unit_ro="lei",
        help_ro="Venitul brut din salarii, folosit la plafonul agregat al CASS.",
        applies_when=(("salary_income_present", ("true",)),),
    ),
    FactDefinition(
        key="prior_losses",
        value_kind=DECIMAL,
        label_ro="Pierderi reportate",
        group="Alte situații",
        unit_ro="lei",
        help_ro="Pierderea fiscală din anii anteriori, care se scade din venitul net.",
    ),
    FactDefinition(
        key="pension_status",
        value_kind=TEXT,
        label_ro="Statutul de pensionar",
        group="Alte situații",
        choices=(("not_pensioner", "Nu sunt pensionar"), ("pensioner", "Sunt pensionar")),
        help_ro=(
            "Se înregistrează în declarație. Exceptarea de la contribuții se bifează "
            "explicit mai sus, după verificarea condițiilor legale."
        ),
    ),
)

FACTS_BY_KEY: dict[str, FactDefinition] = {fact.key: fact for fact in FACT_DEFINITIONS}


def required_forms(key: str) -> list[str]:
    """Which declarations block on this fact, so the form warns about real blockers."""
    from pfa.services.obligations import REQUIRED_FACTS

    return sorted(form for form, keys in REQUIRED_FACTS.items() if key in keys)


def definition_for(key: str) -> FactDefinition:
    definition = FACTS_BY_KEY.get(key)
    if definition is None:
        raise ValidationFailed(
            f"Informația fiscală „{key}” nu este cunoscută.",
            field_errors={"fact_key": "unknown"},
            details={"allowed": sorted(FACTS_BY_KEY)},
        )
    return definition


def _coerce(definition: FactDefinition, value: object) -> tuple[str | None, Decimal | None, bool | None]:
    """Validate one answer against its definition; ``None`` means "not answered"."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, None, None
    if definition.value_kind == BOOLEAN:
        if not isinstance(value, bool):
            raise ValidationFailed(
                f"„{definition.label_ro}” se răspunde cu da sau nu.",
                field_errors={definition.key: "expected_boolean"},
            )
        return None, None, value
    if definition.value_kind == DECIMAL:
        try:
            number = Decimal(str(value))
        except (InvalidOperation, ValueError):
            raise ValidationFailed(
                f"„{definition.label_ro}” trebuie să fie o sumă.",
                field_errors={definition.key: "expected_decimal"},
            ) from None
        if number < 0:
            raise ValidationFailed(
                f"„{definition.label_ro}” nu poate fi negativ.",
                field_errors={definition.key: "negative"},
            )
        if definition.key == "active_months" and not (1 <= number <= 12):
            raise ValidationFailed(
                "Numărul de luni de activitate trebuie să fie între 1 și 12.",
                field_errors={definition.key: "out_of_range"},
            )
        return None, number, None
    text = str(value).strip()
    if definition.choices and text not in {choice for choice, _ in definition.choices}:
        raise ValidationFailed(
            f"„{definition.label_ro}” are valori permise: "
            + ", ".join(label for _, label in definition.choices)
            + ".",
            field_errors={definition.key: "unexpected_choice"},
        )
    return text, None, None


def view(row: TaxpayerYearFact) -> dict:
    """One fact as the form needs it: the definition plus the current answer."""
    definition = FACTS_BY_KEY.get(row.fact_key)
    value = (
        row.value_text
        if row.value_text is not None
        else row.value_decimal
        if row.value_decimal is not None
        else row.value_boolean
    )
    return {
        "fact_key": row.fact_key,
        "income_year": row.income_year,
        "value_kind": row.value_kind,
        "value": value,
        "answered": row.answered_at is not None,
        "answered_at": row.answered_at.isoformat() if row.answered_at else None,
        "evidence_reference": row.evidence_reference,
        "label_ro": definition.label_ro if definition else row.fact_key,
        "group": definition.group if definition else "Altele",
        "unit_ro": definition.unit_ro if definition else None,
        "help_ro": definition.help_ro if definition else None,
        "choices": [
            {"value": code, "label": label} for code, label in (definition.choices if definition else ())
        ],
        "required_for": required_forms(row.fact_key),
        "applies_when": [
            {"fact_key": key, "values": list(values)}
            for key, values in (definition.applies_when if definition else ())
        ],
    }


def catalogue(rows: list[TaxpayerYearFact], income_year: int) -> list[dict]:
    """Every answerable fact, with its current value (unanswered facts included)."""
    stored = {row.fact_key: row for row in rows}
    items = []
    for definition in FACT_DEFINITIONS:
        row = stored.pop(definition.key, None)
        if row is not None:
            items.append(view(row))
            continue
        items.append(
            {
                "fact_key": definition.key,
                "income_year": income_year,
                "value_kind": definition.value_kind,
                "value": None,
                "answered": False,
                "answered_at": None,
                "evidence_reference": None,
                "label_ro": definition.label_ro,
                "group": definition.group,
                "unit_ro": definition.unit_ro,
                "help_ro": definition.help_ro,
                "choices": [{"value": code, "label": label} for code, label in definition.choices],
                "required_for": required_forms(definition.key),
                "applies_when": [
                    {"fact_key": key, "values": list(values)}
                    for key, values in definition.applies_when
                ],
            }
        )
    # facts stored by an older version stay visible instead of disappearing
    items.extend(view(row) for row in stored.values())
    return items


async def list_facts(
    session: AsyncSession, *, workspace_id: uuid.UUID, income_year: int
) -> list[dict]:
    rows = (
        (
            await session.execute(
                select(TaxpayerYearFact).where(
                    TaxpayerYearFact.workspace_id == workspace_id,
                    TaxpayerYearFact.income_year == income_year,
                )
            )
        )
        .scalars()
        .all()
    )
    return catalogue(list(rows), income_year)


async def answer_facts(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    income_year: int,
    answers: dict[str, object],
) -> list[dict]:
    """Record the answers and refresh the obligations they block.

    Answering is what clears a D212 „informații lipsă" line, so the obligations for that
    year are re-evaluated here: the user sees the effect of the answer immediately.
    """
    if not 2000 <= income_year <= 2100:
        raise ValidationFailed(
            "Anul de venit este în afara intervalului acceptat.",
            field_errors={"income_year": "out_of_range"},
        )
    rows = {
        row.fact_key: row
        for row in (
            await session.execute(
                select(TaxpayerYearFact).where(
                    TaxpayerYearFact.workspace_id == workspace_id,
                    TaxpayerYearFact.income_year == income_year,
                )
            )
        )
        .scalars()
        .all()
    }
    for key, raw in answers.items():
        definition = definition_for(key)
        text, number, boolean = _coerce(definition, raw)
        fact = rows.get(key)
        if fact is None:
            fact = TaxpayerYearFact(
                workspace_id=workspace_id, income_year=income_year, fact_key=key, value_kind=definition.value_kind
            )
            session.add(fact)
        fact.value_kind = definition.value_kind
        fact.value_text, fact.value_decimal, fact.value_boolean = text, number, boolean
        answered = any(value is not None for value in (text, number, boolean))
        fact.answered_by_user_id = actor_user_id if answered else None
        fact.answered_at = clock.now() if answered else None
    await session.flush()

    from pfa.services import notifications as notifications_service
    from pfa.services import obligations as obligations_service

    obligations = await obligations_service.evaluate_obligations(
        session, workspace_id=workspace_id, income_year=income_year
    )
    await notifications_service.evaluate_notifications(
        session, workspace_id=workspace_id, obligations=obligations
    )
    return await list_facts(session, workspace_id=workspace_id, income_year=income_year)


def supported_years(today: date | None = None) -> list[int]:
    current = (today or clock.today()).year
    return list(range(current - 5, current + 2))
