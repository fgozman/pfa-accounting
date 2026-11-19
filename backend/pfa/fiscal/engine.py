"""Deterministic income-tax and social-contribution engine.

The engine is a pure function of (normalized events, taxpayer facts, rule pack).
It has no HTTP, database or UI dependency, and the React application never
supplies authoritative calculated values.

Sequence implemented for real-system (sistem real) activity — a simple
"invoices minus expenses" formula is deliberately impossible here:

1. taxable collections recognised on the cash/realization basis
2. eligible paid business expenses, each with its own deductibility and
   business-use determination (income-tax deductibility is separate from VAT
   recoverability)
3. fiscal depreciation from the asset schedule
4. loss carryforward, limited per the pack
5. contribution bases and amounts (CAS, CASS) from the pack's bracket schedule,
   including elections, exemptions, zero/loss cases and the aggregate ceiling
6. statutory deductions from the tax base (contributions, only when the regime
   does not make the income tax final)
7. tax rate, foreign tax credit, rounding
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal

from pfa.fiscal.money import (
    HUNDRED,
    ZERO,
    d,
    max_zero,
    quantize,
    round_cents,
    round_ron,
)
from pfa.fiscal.rules import RulePack, RulePackError, contribution_base

ENGINE_VERSION = "fiscal-engine-1.0.0"

Regime = Literal["real", "norm"]

ENGINE_UNRESOLVED_PREFIX = "unresolved"


@dataclass(frozen=True, slots=True)
class RevenueItem:
    id: str
    occurred_on: date
    amount_ron: Decimal
    category: str = "independent"
    caen_code: str | None = None
    description: str = ""
    source_ref: str | None = None


@dataclass(frozen=True, slots=True)
class ExpenseItem:
    id: str
    occurred_on: date
    amount_ron: Decimal
    category: str = "general"
    #: fully_deductible | partially_deductible | non_deductible | undetermined
    deductibility: str = "fully_deductible"
    #: statutory deduction share for partially deductible categories (percent)
    deductibility_percent: Decimal = Decimal("100")
    #: None means the user has not stated it: a deductible expense is then unresolved
    business_use_percent: Decimal | None = None
    #: named legal limit in the pack (e.g. "protocol", "vehicle")
    limit_code: str | None = None
    description: str = ""
    source_ref: str | None = None
    vat_amount_ron: Decimal = ZERO


@dataclass(frozen=True, slots=True)
class DepreciationItem:
    id: str
    asset_id: str
    amount_ron: Decimal
    deductible_amount_ron: Decimal
    period_month: int | None = None


@dataclass(frozen=True, slots=True)
class TaxpayerFacts:
    income_year: int
    regime: Regime = "real"
    #: income-norm inputs
    norm_amount: Decimal | None = None
    norm_adjustment_percent: Decimal | None = None
    active_months: int | None = None
    #: elections / exemptions (None means "unknown")
    cas_elected: bool | None = None
    cass_elected: bool | None = None
    cas_exempt: bool | None = None
    cass_exempt: bool | None = None
    #: other personal circumstances used by statutory exceptions
    salary_income_ron: Decimal | None = None
    #: "pensioner" | "not_pensioner" | None (unknown): recorded and reported, while the
    #: exemption itself stays an explicit, evidenced election (``cass_exempt``)
    pension_status: str | None = None
    #: bases from other income categories, for the aggregate CASS ceiling
    other_income_base_ron: Decimal | None = None
    prior_losses: Decimal | None = None
    foreign_tax_paid_ron: Decimal | None = None
    other_deductions_ron: Decimal | None = None
    #: fact keys still unknown; each one blocks final approval
    unknown_facts: tuple[str, ...] = ()
    #: named legal limits expressed as percentages of the paid amount
    limit_overrides: dict[str, Decimal] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TraceLine:
    code: str
    label_ro: str
    formula: str
    value: Decimal
    legal_reference: str
    inputs: tuple[str, ...] = ()
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "label_ro": self.label_ro,
            "formula": self.formula,
            "value": str(quantize(self.value)),
            "legal_reference": self.legal_reference,
            "inputs": list(self.inputs),
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class IncomeCalculationResult:
    income_year: int
    regime: Regime
    rule_pack_code: str
    rule_pack_version: int
    formula_version: str
    engine_version: str

    gross_revenue: Decimal
    eligible_expenses: Decimal
    non_deductible_expenses: Decimal
    depreciation: Decimal
    net_income_before_losses: Decimal
    loss_offset: Decimal
    net_income: Decimal
    norm_income: Decimal

    cas_base: Decimal
    cas_amount: Decimal
    cas_mandatory: bool
    cass_base: Decimal
    cass_amount: Decimal
    cass_mandatory: bool
    cass_ceiling_applied: Decimal | None

    taxable_income: Decimal
    income_tax: Decimal
    foreign_tax_credit: Decimal

    total_contributions: Decimal
    total_due: Decimal

    unresolved: tuple[str, ...]
    warnings: tuple[str, ...]
    trace: tuple[TraceLine, ...]

    def as_dict(self) -> dict:
        return {
            "income_year": self.income_year,
            "regime": self.regime,
            "rule_pack_code": self.rule_pack_code,
            "rule_pack_version": self.rule_pack_version,
            "formula_version": self.formula_version,
            "engine_version": self.engine_version,
            "gross_revenue": str(quantize(self.gross_revenue)),
            "eligible_expenses": str(quantize(self.eligible_expenses)),
            "non_deductible_expenses": str(quantize(self.non_deductible_expenses)),
            "depreciation": str(quantize(self.depreciation)),
            "net_income_before_losses": str(quantize(self.net_income_before_losses)),
            "loss_offset": str(quantize(self.loss_offset)),
            "net_income": str(quantize(self.net_income)),
            "norm_income": str(quantize(self.norm_income)),
            "cas_base": str(quantize(self.cas_base)),
            "cas_amount": str(quantize(self.cas_amount)),
            "cas_mandatory": self.cas_mandatory,
            "cass_base": str(quantize(self.cass_base)),
            "cass_amount": str(quantize(self.cass_amount)),
            "cass_mandatory": self.cass_mandatory,
            "cass_ceiling_applied": (
                None if self.cass_ceiling_applied is None else str(quantize(self.cass_ceiling_applied))
            ),
            "taxable_income": str(quantize(self.taxable_income)),
            "income_tax": str(quantize(self.income_tax)),
            "foreign_tax_credit": str(quantize(self.foreign_tax_credit)),
            "total_contributions": str(quantize(self.total_contributions)),
            "total_due": str(quantize(self.total_due)),
            "unresolved": list(self.unresolved),
            "warnings": list(self.warnings),
            "trace": [line.as_dict() for line in self.trace],
        }


def minimum_wage_for_thresholds(pack: RulePack) -> Decimal:
    """Minimum wage used to determine contribution thresholds.

    Romanian practice determines the thresholds with the minimum wage in force at
    the beginning of the income year, so a mid-year increase does not change the
    brackets of the same year.  The reference date lives in the pack, not here.
    """
    return pack.minimum_wage_on(pack.valid_from)


def calculate_income_tax(
    pack: RulePack,
    *,
    facts: TaxpayerFacts,
    revenues: tuple[RevenueItem, ...] = (),
    expenses: tuple[ExpenseItem, ...] = (),
    depreciation: tuple[DepreciationItem, ...] = (),
) -> IncomeCalculationResult:
    if facts.income_year != pack.income_year:
        raise RulePackError(
            f"rule pack {pack.code} covers {pack.income_year}, not {facts.income_year}"
        )
    trace: list[TraceLine] = []
    unresolved: list[str] = []
    warnings: list[str] = []
    minimum_wage = minimum_wage_for_thresholds(pack)

    # -- 1. recognised revenue -----------------------------------------
    gross_revenue = round_cents(sum((d(item.amount_ron) for item in revenues), ZERO))
    trace.append(
        TraceLine(
            code="gross_revenue",
            label_ro="Venituri încasate recunoscute",
            formula="sumă(încasări cu natură de venit)",
            value=gross_revenue,
            legal_reference="Cod fiscal art. 67-68",
            inputs=tuple(item.id for item in revenues),
            note="Doar încasările cu natură de venit; mișcările de numerar ale titularului "
            "nu devin venit impozabil.",
        )
    )

    # -- 2. eligible expenses ------------------------------------------
    eligible = ZERO
    non_deductible = ZERO
    ignored_for_norm = ZERO
    for item in expenses:
        if facts.regime == "norm":
            # Norm activity: operating expenses are recorded for the books but do
            # not reduce the taxed base.
            ignored_for_norm += round_cents(d(item.amount_ron))
            continue
        treatment = (item.deductibility or "undetermined").lower()
        if treatment == "undetermined":
            unresolved.append(f"expense_deductibility:{item.id}")
            warnings.append(
                f"Cheltuiala {item.id} nu are tratament de deductibilitate stabilit."
            )
            continue
        if treatment == "non_deductible":
            share = ZERO
        elif treatment == "partially_deductible":
            share = d(item.deductibility_percent)
        else:
            share = HUNDRED
        if item.limit_code:
            override = facts.limit_overrides.get(item.limit_code)
            if override is not None:
                share = min(share, d(override))
        if share > ZERO and item.business_use_percent is None:
            # the share multiplies the deductible amount, so it must be stated, not guessed
            unresolved.append(f"expense_business_use:{item.id}")
            warnings.append(
                f"Cheltuiala {item.id} nu are ponderea de utilizare în scop de afaceri."
            )
            continue
        business_use = (
            ZERO if item.business_use_percent is None else d(item.business_use_percent) / HUNDRED
        )
        deductible_amount = round_cents(d(item.amount_ron) * business_use * share / HUNDRED)
        eligible += deductible_amount
        non_deductible += round_cents(d(item.amount_ron) - deductible_amount)
    eligible = round_cents(eligible)
    non_deductible = round_cents(non_deductible)
    trace.append(
        TraceLine(
            code="eligible_expenses",
            label_ro="Cheltuieli deductibile plătite",
            formula="sumă(cheltuială × pondere utilizare × procent deductibilitate)",
            value=eligible,
            legal_reference="Cod fiscal art. 68, 122",
            inputs=tuple(item.id for item in expenses),
            note="Deductibilitatea pentru impozitul pe venit este determinată separat de "
            "deductibilitatea TVA.",
        )
    )
    if facts.regime == "norm" and ignored_for_norm:
        trace.append(
            TraceLine(
                code="expenses_not_applicable_to_norm",
                label_ro="Cheltuieli care nu reduc baza normată",
                formula="cheltuieli înregistrate (informativ)",
                value=ignored_for_norm,
                legal_reference="Cod fiscal art. 69",
                inputs=tuple(item.id for item in expenses),
                note=(
                    "În regim de normă de venit baza impozabilă este norma publicată, "
                    "nu profitul real: cheltuielile operaționale nu o diminuează."
                ),
            )
        )
    trace.append(
        TraceLine(
            code="non_deductible_expenses",
            label_ro="Cheltuieli nedeductibile",
            formula="sumă(cheltuială) - cheltuieli deductibile",
            value=non_deductible,
            legal_reference="Cod fiscal art. 68",
            inputs=tuple(item.id for item in expenses),
        )
    )

    # -- 3. depreciation ------------------------------------------------
    depreciation_total = round_cents(
        sum((d(item.deductible_amount_ron) for item in depreciation), ZERO)
    )
    if depreciation:
        gross_depreciation = round_cents(sum((d(i.amount_ron) for i in depreciation), ZERO))
        if gross_depreciation > depreciation_total:
            non_deductible = round_cents(
                non_deductible + (gross_depreciation - depreciation_total)
            )
    trace.append(
        TraceLine(
            code="depreciation",
            label_ro="Amortizare fiscală deductibilă",
            formula="sumă(amortizare lunară × pondere utilizare)",
            value=depreciation_total,
            legal_reference="Cod fiscal art. 28, 24-27",
            inputs=tuple(item.id for item in depreciation),
            note="Un activ nu primește simultan deducere integrală de cheltuială și amortizare.",
        )
    )

    # -- norm (normă de venit) branch ----------------------------------
    norm_income = ZERO
    if facts.regime == "norm":
        if facts.norm_amount is None:
            unresolved.append("norm_amount")
            warnings.append(
                "Norma de venit nu este cunoscută: nu se poate determina venitul impozabil."
            )
        else:
            adjustment = d(facts.norm_adjustment_percent) if facts.norm_adjustment_percent is not None else ZERO
            if facts.norm_adjustment_percent is None:
                unresolved.append("norm_adjustment_percent")
            months = facts.active_months
            if months is None:
                unresolved.append("active_months")
                months = 12
            if not 1 <= months <= 12:
                raise RulePackError("active_months must be between 1 and 12")
            base = d(facts.norm_amount) * (HUNDRED + adjustment) / HUNDRED
            norm_income = round_cents(base * Decimal(months) / Decimal(12))
            trace.append(
                TraceLine(
                    code="norm_income",
                    label_ro="Venit net din norma de venit",
                    formula="normă × (1 + factor de ajustare) × luni active / 12",
                    value=norm_income,
                    legal_reference="Cod fiscal art. 69, 180",
                    inputs=("norm_amount", "norm_adjustment_percent", "active_months"),
                    note="Profitul real nu înlocuiește baza normată; cheltuielile operaționale "
                    "nu reduc venitul normat.",
                )
            )
        # operating expenses stay informative for norm activity: they do not
        # reduce the taxable base
        net_income_before_losses = norm_income
    else:
        net_income_before_losses = round_cents(gross_revenue - eligible - depreciation_total)

    trace.append(
        TraceLine(
            code="net_income_before_losses",
            label_ro="Venit net anual înainte de pierderi",
            formula="venituri - cheltuieli deductibile - amortizare",
            value=net_income_before_losses,
            legal_reference="Cod fiscal art. 68",
        )
    )

    # -- 4. loss carryforward -------------------------------------------
    loss_offset = ZERO
    if facts.prior_losses is None:
        pass
    elif d(facts.prior_losses) > ZERO:
        limit_rate = pack.income_tax.loss_offset_limit_rate
        available = max_zero(net_income_before_losses) * limit_rate
        loss_offset = round_cents(min(d(facts.prior_losses), available))
        trace.append(
            TraceLine(
                code="loss_offset",
                label_ro="Pierderi fiscale reportate utilizate",
                formula="min(pierdere reportată, venit net × limită legală)",
                value=loss_offset,
                legal_reference=f"Cod fiscal art. 118 ({pack.income_tax.legal_reference})",
                inputs=("prior_losses",),
                note=f"Limita de utilizare: {limit_rate * 100}% din venitul net al anului.",
            )
        )
    net_income = round_cents(net_income_before_losses - loss_offset)

    # -- 5. contributions ----------------------------------------------
    cas_base, cas_amount, cas_mandatory, cass_base, cass_amount, cass_mandatory, ceiling = (
        _contributions(pack, facts, net_income, minimum_wage, trace, unresolved, warnings)
    )

    # -- 6. taxable income ---------------------------------------------
    if facts.regime == "norm" and pack.income_tax.norm_tax_is_final:
        taxable_income = max_zero(net_income)
        trace.append(
            TraceLine(
                code="contributions_deductible",
                label_ro="Contribuții deductibile din baza impozabilă",
                formula="0 (impozit final pentru norma de venit)",
                value=ZERO,
                legal_reference="Cod fiscal art. 69",
                note="Pentru veniturile impuse pe bază de normă de venit impozitul este final.",
            )
        )
    else:
        other_deductions = d(facts.other_deductions_ron) if facts.other_deductions_ron is not None else ZERO
        if facts.other_deductions_ron is None:
            other_deductions = ZERO
        taxable_income = max_zero(
            net_income - cas_amount - cass_amount - round_cents(other_deductions)
        )
    trace.append(
        TraceLine(
            code="taxable_income",
            label_ro="Venit net anual impozabil",
            formula="venit net - contribuții deductibile - alte deduceri",
            value=taxable_income,
            legal_reference="Cod fiscal art. 68",
        )
    )

    # -- 7. tax ---------------------------------------------------------
    income_tax = round_ron(taxable_income * pack.income_tax.rate / HUNDRED)
    trace.append(
        TraceLine(
            code="income_tax",
            label_ro="Impozit pe venit",
            formula=f"venit impozabil × {pack.income_tax.rate}%",
            value=income_tax,
            legal_reference="Cod fiscal art. 77-78",
            note="Rotunjire la leu întreg, conform regulilor formularului.",
        )
    )

    foreign_tax_credit = ZERO
    if facts.foreign_tax_paid_ron is not None and d(facts.foreign_tax_paid_ron) > ZERO:
        foreign_tax_credit = round_ron(min(d(facts.foreign_tax_paid_ron), income_tax))
        trace.append(
            TraceLine(
                code="foreign_tax_credit",
                label_ro="Credit fiscal extern",
                formula="min(impozit plătit în străinătate, impozit român)",
                value=foreign_tax_credit,
                legal_reference="Cod fiscal art. 131-135",
                inputs=("foreign_tax_paid_ron",),
            )
        )

    total_contributions = round_ron(cas_amount + cass_amount)
    total_due = round_ron(total_contributions + income_tax - foreign_tax_credit)

    unresolved.extend(
        f"{ENGINE_UNRESOLVED_PREFIX}:{key}" for key in facts.unknown_facts
    )

    return IncomeCalculationResult(
        income_year=facts.income_year,
        regime=facts.regime,
        rule_pack_code=pack.code,
        rule_pack_version=pack.version,
        formula_version=pack.formula_version,
        engine_version=ENGINE_VERSION,
        gross_revenue=gross_revenue,
        eligible_expenses=eligible,
        non_deductible_expenses=non_deductible,
        depreciation=depreciation_total,
        net_income_before_losses=net_income_before_losses,
        loss_offset=loss_offset,
        net_income=net_income,
        norm_income=norm_income,
        cas_base=cas_base,
        cas_amount=cas_amount,
        cas_mandatory=cas_mandatory,
        cass_base=cass_base,
        cass_amount=cass_amount,
        cass_mandatory=cass_mandatory,
        cass_ceiling_applied=ceiling,
        taxable_income=taxable_income,
        income_tax=income_tax,
        foreign_tax_credit=foreign_tax_credit,
        total_contributions=total_contributions,
        total_due=total_due,
        unresolved=tuple(sorted(set(unresolved))),
        warnings=tuple(warnings),
        trace=tuple(trace),
    )


def _contributions(
    pack: RulePack,
    facts: TaxpayerFacts,
    net_income: Decimal,
    minimum_wage: Decimal,
    trace: list[TraceLine],
    unresolved: list[str],
    warnings: list[str],
) -> tuple[Decimal, Decimal, bool, Decimal, Decimal, bool, Decimal | None]:
    # -- CAS ------------------------------------------------------------
    cas_base = ZERO
    cas_amount = ZERO
    cas_mandatory = False
    if facts.cas_exempt is True:
        trace.append(
            TraceLine(
                code="cas_exempt",
                label_ro="CAS — scutit",
                formula="0 (scutire aplicabilă)",
                value=ZERO,
                legal_reference=pack.cas.legal_reference,
            )
        )
    elif facts.cas_elected is None and net_income > minimum_wage * Decimal("12"):
        # mandatory above the floor, so an unknown election does not block it
        cas_base, cas_mandatory = contribution_base(
            pack.cas, net_income, minimum_wage=minimum_wage
        )
    else:
        cas_base, cas_mandatory = contribution_base(
            pack.cas,
            net_income,
            minimum_wage=minimum_wage,
            elected=bool(facts.cas_elected),
        )
    if cas_base > ZERO:
        cas_amount = round_ron(cas_base * pack.cas.rate / HUNDRED)
    trace.append(
        TraceLine(
            code="cas",
            label_ro="CAS — baza și contribuția",
            formula=f"{pack.cas.rate}% × bază ({cas_base})",
            value=cas_amount,
            legal_reference=pack.cas.legal_reference,
            inputs=("net_income", "minimum_wage", "cas_elected"),
            note=(
                "Contribuție obligatorie"
                if cas_mandatory
                else "Contribuție opțională (neelectată)"
            ),
        )
    )

    # -- CASS -----------------------------------------------------------
    cass_base = ZERO
    cass_amount = ZERO
    cass_mandatory = False
    ceiling_applied: Decimal | None = None
    if facts.cass_exempt is True:
        trace.append(
            TraceLine(
                code="cass_exempt",
                label_ro="CASS — scutit",
                formula="0 (scutire aplicabilă)",
                value=ZERO,
                legal_reference=pack.cass.legal_reference,
            )
        )
    else:
        cass_base, cass_mandatory = contribution_base(
            pack.cass,
            net_income,
            minimum_wage=minimum_wage,
            elected=bool(facts.cass_elected),
        )
        if cass_base == ZERO and facts.other_income_base_ron:
            # other categories may still require the minimum base
            other = d(facts.other_income_base_ron)
            if other > ZERO:
                cass_base = quantize(pack.cass.election_base_multiple or ZERO) * minimum_wage
                cass_mandatory = True
        cap_multiple = pack.cass.max_base_multiple
        if cap_multiple is not None and cass_base > ZERO:
            ceiling = quantize(cap_multiple * minimum_wage)
            other = d(facts.other_income_base_ron) if facts.other_income_base_ron else ZERO
            remaining = ceiling - other
            if remaining < ZERO:
                remaining = ZERO
            if cass_base > remaining:
                cass_base = quantize(remaining)
                ceiling_applied = ceiling
                warnings.append(
                    "Plafonul anual cumulat CASS a fost atins: baza pentru activitatea "
                    f"independentă a fost limitată la {cass_base}."
                )
    if cass_base > ZERO:
        cass_amount = round_ron(cass_base * pack.cass.rate / HUNDRED)
    trace.append(
        TraceLine(
            code="cass",
            label_ro="CASS — baza și contribuția",
            formula=f"{pack.cass.rate}% × bază ({cass_base})",
            value=cass_amount,
            legal_reference=pack.cass.legal_reference,
            inputs=("net_income", "minimum_wage", "cass_elected", "other_income_base_ron"),
            note=(
                "Contribuție obligatorie"
                if cass_mandatory
                else "Contribuție opțională (neelectată)"
            ),
        )
    )
    if facts.pension_status is not None:
        trace.append(
            TraceLine(
                code="pension_status",
                label_ro="Statut de pensionar",
                formula="se înregistrează în declarație",
                value=ZERO,
                legal_reference="Cod fiscal art. 148, 170",
                inputs=("pension_status",),
                note=(
                    "Statutul de pensionar nu modifică automat bazele de contribuții: "
                    "exceptarea se bifează explicit, după verificarea condițiilor legale."
                ),
            )
        )
    if facts.salary_income_ron is not None and d(facts.salary_income_ron) > ZERO:
        trace.append(
            TraceLine(
                code="salary_status",
                label_ro="Statut de salariat",
                formula="nu modifică bazele de contribuții pentru activitatea independentă",
                value=d(facts.salary_income_ron),
                legal_reference="Cod fiscal art. 148, 170",
                inputs=("salary_income_ron",),
                note="Calitatea de salariat nu suprimă automat contribuțiile datorate "
                "pentru activitatea independentă.",
            )
        )
    return (
        cas_base,
        cas_amount,
        cas_mandatory,
        cass_base,
        cass_amount,
        cass_mandatory,
        ceiling_applied,
    )
