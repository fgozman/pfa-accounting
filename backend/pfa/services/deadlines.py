"""Deadline engine.

Each form has its own deadline strategy; there is no universal "25th" rule.
Rules are versioned rows in ``deadline_rules`` (seeded from the constants below),
and every computed date carries provenance.  When a rule is not verified, the
deadline is reported as unknown ("Termen de verificat") rather than invented.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from pfa.fiscal.calendar import BUCHAREST, CALENDARS, LegalCalendar

UNKNOWN_DEADLINE_PROVENANCE = "Termen de verificat"

#: Calendar capable of covering every year the product supports.
DEFAULT_CALENDAR_VERSION = "RO-2024-2027.1"


@dataclass(frozen=True, slots=True)
class DeadlineRule:
    form_code: str
    version: int
    rule_kind: str
    parameters: dict
    effective_from: date
    effective_to: date | None = None
    timezone: str = "Europe/Bucharest"
    payment_rule_kind: str | None = None
    payment_parameters: dict | None = None
    source_url: str | None = None
    source_note: str | None = None
    #: False until a Romanian tax professional verifies the rule
    reviewed: bool = False
    calendar_version: str = DEFAULT_CALENDAR_VERSION
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DeadlineResult:
    filing_due_date: date | None
    filing_cutoff_at: datetime | None
    payment_due_date: date | None
    provenance: str
    rule_kind: str
    reviewed: bool
    missing_information: tuple[str, ...] = ()

    @property
    def is_known(self) -> bool:
        return self.filing_due_date is not None


def _next_month_day(day: date, month_offset: int, month_day: int) -> date:
    month_index = day.month - 1 + month_offset
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, month_day)


def _quarter_end(day: date) -> date:
    quarter = (day.month - 1) // 3
    end_month = quarter * 3 + 3
    if end_month == 12:
        return date(day.year, 12, 31)
    return date(day.year, end_month + 1, 1) - timedelta(days=1)


def compute_deadline(
    rule: DeadlineRule,
    *,
    period_start: date,
    period_end: date,
    event_date: date | None = None,
) -> DeadlineResult:
    calendar: LegalCalendar = CALENDARS.get(rule.calendar_version)
    provenance = f"{rule.form_code} v{rule.version} ({rule.rule_kind})"
    if not rule.reviewed:
        provenance = f"{provenance} — regulă nerevizuită de un profesionist fiscal"

    if rule.rule_kind == "fixed_day":
        month_offset = int(rule.parameters.get("month_offset", 1))
        month_day = int(rule.parameters.get("day", 25))
        reference = period_end
        if rule.parameters.get("reference") == "year_end":
            reference = date(period_end.year, 12, 31)
        if rule.parameters.get("annual_following_year"):
            due = date(period_end.year + 1, int(rule.parameters["month"]), int(rule.parameters["day"]))
        else:
            due = _next_month_day(reference, month_offset, month_day)
        cutoff_at = LegalCalendar.end_of_day(due, BUCHAREST)
        payment_due = due
        return DeadlineResult(
            filing_due_date=due,
            filing_cutoff_at=cutoff_at,
            payment_due_date=payment_due,
            provenance=provenance,
            rule_kind=rule.rule_kind,
            reviewed=rule.reviewed,
        )

    if rule.rule_kind == "working_days_after_event":
        if event_date is None:
            return DeadlineResult(
                filing_due_date=None,
                filing_cutoff_at=None,
                payment_due_date=None,
                provenance=provenance,
                rule_kind=rule.rule_kind,
                reviewed=rule.reviewed,
                missing_information=("event_date",),
            )
        working_days = int(rule.parameters.get("working_days", 5))
        due = calendar.add_working_days(event_date, working_days)
        # Additional bound: the statutory invoice-issuance-date rule, expressed as
        # a calendar-day bound from the event, whichever is earlier.
        issuance_bound_days = rule.parameters.get("issuance_bound_calendar_days")
        if issuance_bound_days is not None:
            bound = event_date + timedelta(days=int(issuance_bound_days))
            if bound < due:
                due = bound
        return DeadlineResult(
            filing_due_date=due,
            filing_cutoff_at=LegalCalendar.end_of_day(due, BUCHAREST),
            payment_due_date=None,
            provenance=(
                f"{provenance}; {working_days} zile lucrătoare "
                f"(calendar {rule.calendar_version}), termen suplimentar de emitere"
            ),
            rule_kind=rule.rule_kind,
            reviewed=rule.reviewed,
        )

    if rule.rule_kind == "calendar_days_after_event":
        if event_date is None:
            return DeadlineResult(
                filing_due_date=None,
                filing_cutoff_at=None,
                payment_due_date=None,
                provenance=provenance,
                rule_kind=rule.rule_kind,
                reviewed=rule.reviewed,
                missing_information=("event_date",),
            )
        days = int(rule.parameters.get("calendar_days", 30))
        due = event_date + timedelta(days=days)
        return DeadlineResult(
            filing_due_date=due,
            filing_cutoff_at=LegalCalendar.end_of_day(due, BUCHAREST),
            payment_due_date=due,
            provenance=provenance,
            rule_kind=rule.rule_kind,
            reviewed=rule.reviewed,
        )

    if rule.rule_kind == "quarterly_following_month":
        quarter_end = _quarter_end(period_end)
        due = _next_month_day(quarter_end, 1, int(rule.parameters.get("day", 30)))
        return DeadlineResult(
            filing_due_date=due,
            filing_cutoff_at=LegalCalendar.end_of_day(due, BUCHAREST),
            payment_due_date=due,
            provenance=provenance,
            rule_kind=rule.rule_kind,
            reviewed=rule.reviewed,
        )

    if rule.rule_kind == "event_registration":
        # e.g. D700/D070 under a legal text that has not been verified for this
        # year: never invent a date.
        return DeadlineResult(
            filing_due_date=None,
            filing_cutoff_at=None,
            payment_due_date=None,
            provenance=UNKNOWN_DEADLINE_PROVENANCE,
            rule_kind=rule.rule_kind,
            reviewed=rule.reviewed,
            missing_information=("verified_registration_deadline",),
        )

    raise ValueError(f"unknown deadline rule kind {rule.rule_kind!r}")


# ---------------------------------------------------------------------------
# Seeded rules (unreviewed research data; see specification §14)
# ---------------------------------------------------------------------------

DEFAULT_RULES: tuple[DeadlineRule, ...] = (
    DeadlineRule(
        form_code="D212",
        version=1,
        rule_kind="fixed_day",
        parameters={"annual_following_year": True, "month": 5, "day": 25},
        effective_from=date(2025, 1, 1),
        source_url="https://static.anaf.ro/static/3/Cluj/20260522102135_25_%20mai%202026%20.pdf",
        source_note=(
            "Declarația unică pentru veniturile anului precedent se depune până la "
            "25 mai, inclusiv. Termenul de plată este același."
        ),
    ),
    DeadlineRule(
        form_code="D300",
        version=1,
        rule_kind="fixed_day",
        parameters={"month_offset": 1, "day": 25},
        effective_from=date(2025, 1, 1),
        source_url="https://static.anaf.ro/static/10/Anaf/legislatie/OPANAF_2131_2025.pdf",
        source_note="Decontul de TVA se depune până la data de 25 a lunii următoare perioadei.",
    ),
    DeadlineRule(
        form_code="D301",
        version=1,
        rule_kind="fixed_day",
        parameters={"month_offset": 1, "day": 25},
        effective_from=date(2025, 1, 1),
        source_url="https://static.anaf.ro/static/10/Anaf/legislatie/OPANAF_2131_2025.pdf",
        source_note="Decontul special de TVA are termenul de 25 a lunii următoare.",
    ),
    DeadlineRule(
        form_code="D390",
        version=1,
        rule_kind="fixed_day",
        parameters={"month_offset": 1, "day": 25},
        effective_from=date(2025, 1, 1),
        source_url="https://static.anaf.ro/static/10/Anaf/AsistentaContribuabili_r/Calendar/Calendar_obligatii_fiscale_2025.htm",
        source_note="Declarația recapitulativă VIES se depune lunar/până la 25 a lunii următoare.",
    ),
    DeadlineRule(
        form_code="D394",
        version=1,
        rule_kind="quarterly_following_month",
        parameters={"day": 30},
        effective_from=date(2025, 1, 1),
        source_note="Declarația informativă D394 se depune trimestrial. Regula necesită verificare.",
    ),
    DeadlineRule(
        form_code="D700",
        version=1,
        rule_kind="event_registration",
        parameters={},
        effective_from=date(2025, 1, 1),
        source_note=(
            "Termenul pentru modificările de înregistrare fiscală depinde de eveniment și "
            "de formularul permis la data respectivă; nu se inventează o dată."
        ),
    ),
    DeadlineRule(
        form_code="D070",
        version=1,
        rule_kind="event_registration",
        parameters={},
        effective_from=date(2025, 1, 1),
    ),
    DeadlineRule(
        form_code="EFACTURA",
        version=1,
        rule_kind="working_days_after_event",
        # NOTE: OUG 120/2021 also contains a bound tied to the statutory invoice
        # issuance date.  It is deliberately NOT enabled here: a verified value
        # must be recorded in a reviewed rule pack before it changes a deadline.
        parameters={"working_days": 5},
        effective_from=date(2025, 1, 1),
        source_url="https://legislatie.just.ro/Public/DetaliiDocument/247243",
        source_note=(
            "OUG 120/2021 consolidat: termen de 5 zile lucrătoare pentru transmitere, "
            "cu un termen suplimentar legat de data emiterii facturii. Nu se folosește un "
            "temporizator fix de 120 de ore."
        ),
    ),
    DeadlineRule(
        form_code="D112",
        version=1,
        rule_kind="fixed_day",
        parameters={"month_offset": 1, "day": 25},
        effective_from=date(2025, 1, 1),
        source_note="Formular de angajator: necesită modulul de salarizare.",
    ),
)


def default_rule_for(form_code: str) -> DeadlineRule | None:
    for rule in DEFAULT_RULES:
        if rule.form_code == form_code:
            return rule
    return None


def reminder_offsets_days() -> tuple[int, ...]:
    """Default in-app reminder offsets (calendar days before filing)."""
    return (30, 14, 7, 3, 1, 0, -7)


def days_remaining(due: date, as_of: date) -> int:
    return (due - as_of).days


def is_overdue(due: date | None, as_of: date) -> bool:
    return due is not None and as_of > due
