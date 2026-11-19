"""Versioned fiscal rule packs.

A rule pack is the authoritative, reviewable description of one jurisdiction for
one income year: effective-dated parameters, formulas version, legal sources and
regression fixtures.  Effective dates apply independently to income taxation,
VAT transactions and form schemas.

Nothing here is a claim that a pack has been approved.  ``reviewed`` is False
until a Romanian tax professional signs the pack off (specification §14).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal

from pfa.fiscal.money import ZERO, d, quantize


class RulePackError(KeyError):
    pass


@dataclass(frozen=True, slots=True)
class MinimumWagePeriod:
    effective_from: date
    monthly_gross_ron: Decimal
    legal_reference: str


@dataclass(frozen=True, slots=True)
class ContributionBracket:
    """One bracket of a contribution base schedule, in minimum-wage multiples.

    ``up_to_multiple is None`` means the bracket is open-ended.
    """

    up_to_multiple: Decimal | None
    base_multiple: Decimal
    mandatory: bool


@dataclass(frozen=True, slots=True)
class ContributionSchedule:
    code: str
    label_ro: str
    rate: Decimal
    brackets: tuple[ContributionBracket, ...]
    #: statutory ceiling for the *aggregate* yearly base of this contribution
    max_base_multiple: Decimal | None
    #: base used when the taxpayer elects to contribute below the mandatory floor
    election_base_multiple: Decimal | None
    legal_reference: str

    def bracket_for(self, net_income_in_wages: Decimal) -> ContributionBracket:
        for bracket in self.brackets:
            if bracket.up_to_multiple is None or net_income_in_wages <= bracket.up_to_multiple:
                return bracket
        return self.brackets[-1]  # pragma: no cover - the last bracket is open


@dataclass(frozen=True, slots=True)
class VatRatePeriod:
    effective_from: date
    effective_to: date | None
    standard: Decimal
    reduced: Decimal
    second_reduced: Decimal | None
    note: str = ""

    def covers(self, day: date) -> bool:
        if day < self.effective_from:
            return False
        return self.effective_to is None or day <= self.effective_to


@dataclass(frozen=True, slots=True)
class VatRegistrationThresholdPeriod:
    effective_from: date
    effective_to: date | None
    threshold_ron: Decimal
    source_url: str
    note: str = ""

    def covers(self, day: date) -> bool:
        if day < self.effective_from:
            return False
        return self.effective_to is None or day <= self.effective_to


@dataclass(frozen=True, slots=True)
class IncomeTaxParameters:
    rate: Decimal
    #: for income-norm taxation the income tax is final: contributions are not
    #: deductible from the tax base
    norm_tax_is_final: bool
    #: share of the year's net income that carryforward losses may offset
    loss_offset_limit_rate: Decimal
    loss_carryforward_years: int
    legal_reference: str


@dataclass(frozen=True, slots=True)
class RulePack:
    code: str
    jurisdiction: str
    income_year: int
    domain: str
    title: str
    version: int
    valid_from: date
    valid_to: date | None
    publication_date: date | None
    source_urls: tuple[str, ...]
    formula_version: str
    minimum_wages: tuple[MinimumWagePeriod, ...]
    cas: ContributionSchedule
    cass: ContributionSchedule
    income_tax: IncomeTaxParameters
    vat_rates: tuple[VatRatePeriod, ...]
    vat_thresholds: tuple[VatRegistrationThresholdPeriod, ...]
    deadline_calendar_version: str
    supported_scenarios: tuple[str, ...] = ()
    regression_fixtures: tuple[dict, ...] = ()
    status: str = "pending_review"
    reviewed: bool = False
    professional_reviewer: str | None = None
    notes: tuple[str, ...] = ()
    extra: dict = field(default_factory=dict)

    # -- parameters ------------------------------------------------------
    def minimum_wage_on(self, day: date) -> Decimal:
        applicable = None
        for period in sorted(self.minimum_wages, key=lambda p: p.effective_from):
            if period.effective_from <= day:
                applicable = period
        if applicable is None:
            raise RulePackError(
                f"no minimum wage is defined in {self.code} for {day.isoformat()}"
            )
        return applicable.monthly_gross_ron

    def vat_rate_period(self, day: date) -> VatRatePeriod:
        for period in self.vat_rates:
            if period.covers(day):
                return period
        raise RulePackError(f"no VAT rate period in {self.code} covers {day.isoformat()}")

    def vat_rate(self, category: str, day: date) -> Decimal:
        """Statutory VAT rate for a category on the supply/tax-point date.

        The rate is chosen from the supply date, never from the invoice entry
        date, so historical supplies keep their original rate.
        """
        period = self.vat_rate_period(day)
        normalised = (category or "standard").lower()
        if normalised in {"standard", "21", "19"}:
            return period.standard
        if normalised in {"reduced", "11", "9"}:
            return period.reduced
        if normalised in {"second_reduced", "5", "11_former_5"}:
            # the 5% rate was abolished: supplies that used to fall in it move to
            # the reduced rate, but only from the change effective date
            if period.second_reduced is not None:
                return period.second_reduced
            return period.reduced
        if normalised in {"exempt", "reverse_charge", "out_of_scope", "zero"}:
            return ZERO
        raise RulePackError(f"unknown VAT category {category!r}")

    def vat_threshold_on(self, day: date) -> Decimal:
        for period in sorted(self.vat_thresholds, key=lambda p: p.effective_from):
            if period.covers(day):
                return period.threshold_ron
        raise RulePackError(f"no VAT registration threshold for {day.isoformat()}")

    # -- serialisation ---------------------------------------------------
    def to_parameters(self) -> dict:
        """JSON-serialisable view stored in ``rule_packs.parameters``."""
        return {
            "code": self.code,
            "jurisdiction": self.jurisdiction,
            "income_year": self.income_year,
            "domain": self.domain,
            "formula_version": self.formula_version,
            "minimum_wages": [
                {
                    "effective_from": p.effective_from.isoformat(),
                    "monthly_gross_ron": str(p.monthly_gross_ron),
                    "legal_reference": p.legal_reference,
                }
                for p in self.minimum_wages
            ],
            "contributions": {
                schedule.code: {
                    "label_ro": schedule.label_ro,
                    "rate": str(schedule.rate),
                    "max_base_multiple": (
                        None
                        if schedule.max_base_multiple is None
                        else str(schedule.max_base_multiple)
                    ),
                    "election_base_multiple": (
                        None
                        if schedule.election_base_multiple is None
                        else str(schedule.election_base_multiple)
                    ),
                    "brackets": [
                        {
                            "up_to_multiple": (
                                None if b.up_to_multiple is None else str(b.up_to_multiple)
                            ),
                            "base_multiple": str(b.base_multiple),
                            "mandatory": b.mandatory,
                        }
                        for b in schedule.brackets
                    ],
                    "legal_reference": schedule.legal_reference,
                }
                for schedule in (self.cas, self.cass)
            },
            "income_tax": {
                "rate": str(self.income_tax.rate),
                "norm_tax_is_final": self.income_tax.norm_tax_is_final,
                "loss_offset_limit_rate": str(self.income_tax.loss_offset_limit_rate),
                "loss_carryforward_years": self.income_tax.loss_carryforward_years,
                "legal_reference": self.income_tax.legal_reference,
            },
            "vat_rates": [
                {
                    "effective_from": p.effective_from.isoformat(),
                    "effective_to": p.effective_to.isoformat() if p.effective_to else None,
                    "standard": str(p.standard),
                    "reduced": str(p.reduced),
                    "second_reduced": (
                        None if p.second_reduced is None else str(p.second_reduced)
                    ),
                    "note": p.note,
                }
                for p in self.vat_rates
            ],
            "vat_thresholds": [
                {
                    "effective_from": p.effective_from.isoformat(),
                    "effective_to": p.effective_to.isoformat() if p.effective_to else None,
                    "threshold_ron": str(p.threshold_ron),
                    "source_url": p.source_url,
                    "note": p.note,
                }
                for p in self.vat_thresholds
            ],
            "deadline_calendar_version": self.deadline_calendar_version,
            "supported_scenarios": list(self.supported_scenarios),
            "status": self.status,
            "professionally_reviewed": self.reviewed,
            "notes": list(self.notes),
        }

    def with_status(self, status: str, *, reviewer: str | None = None) -> RulePack:
        return replace(
            self,
            status=status,
            reviewed=status == "approved" and reviewer is not None,
            professional_reviewer=reviewer or self.professional_reviewer,
        )


def contribution_base(
    schedule: ContributionSchedule,
    net_income: Decimal,
    *,
    minimum_wage: Decimal,
    elected: bool = False,
) -> tuple[Decimal, bool]:
    """Return ``(base, mandatory)`` for a contribution schedule.

    The bracket is selected on the net income expressed in minimum wages, so a
    change to the minimum wage never silently changes the bracket of a past year.
    """
    if minimum_wage <= ZERO:
        raise RulePackError("minimum wage must be positive")
    multiple = d(net_income) / minimum_wage
    bracket = schedule.bracket_for(multiple)
    if elected:
        base_multiple = schedule.election_base_multiple
        if base_multiple is None:
            base_multiple = bracket.base_multiple
        return quantize(base_multiple * minimum_wage), True
    if not bracket.mandatory:
        return ZERO, False
    return quantize(bracket.base_multiple * minimum_wage), True
