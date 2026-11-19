"""VAT engine: place of supply, tax point, reverse charge, registration turnover.

Separate from income taxation and from the cash journal.  Article 317
registration is *not* normal domestic VAT registration, and a special-code
taxpayer must still be evaluated for D301/D390 even when domestic turnover is
below the ordinary exemption threshold.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from pfa.fiscal.money import ZERO, d, round_cents
from pfa.fiscal.rules import RulePack, RulePackError

VatRegistrationKind = Literal["none", "article_316", "article_317"]

#: classification of a transaction for VAT purposes; "EU country" alone is not a
#: classification, so this must be supplied from evidenced source data
VatNature = Literal[
    "domestic_supply",
    "domestic_acquisition",
    "domestic_reverse_charge",
    "ic_goods_supply",
    "ic_services_supply",
    "ic_goods_acquisition",
    "ic_services_acquisition",
    "non_eu_services_acquisition",
    "non_eu_services_supply",
    "import_goods",
    "export_goods",
]

INTRA_COMMUNITY_NATURES: frozenset[str] = frozenset(
    {
        "ic_goods_supply",
        "ic_services_supply",
        "ic_goods_acquisition",
        "ic_services_acquisition",
    }
)
ACQUISITION_NATURES: frozenset[str] = frozenset(
    {
        "ic_goods_acquisition",
        "ic_services_acquisition",
        "non_eu_services_acquisition",
        "import_goods",
        "domestic_reverse_charge",
    }
)


@dataclass(frozen=True, slots=True)
class VatProfile:
    registration_kind: VatRegistrationKind = "none"
    has_deduction_right: bool = True
    cash_accounting: bool = False
    vies_registered: bool = False


@dataclass(frozen=True, slots=True)
class VatTransaction:
    id: str
    occurred_on: date
    #: tax point (exigibilitate) — may differ from the invoice and settlement dates
    tax_point: date
    direction: Literal["sale", "purchase"]
    nature: VatNature
    category: str = "standard"
    net_amount_ron: Decimal = ZERO
    #: VAT recorded on the document.  When present this is what the declaration reports;
    #: the rule-pack rate is used only for reverse-charge self-assessment (and as a
    #: fallback for versions calculated before the recorded VAT was carried).
    vat_amount_ron: Decimal | None = None
    #: rate recorded on the document, so the journal line shows the invoice's rate
    recorded_rate: Decimal | None = None
    #: evidenced VAT identifier of the counterparty (VIES-verified for EU)
    counterparty_country: str = "RO"
    counterparty_is_business: bool = True
    counterparty_vat_id: str | None = None
    counterparty_vat_id_verified: bool = False
    #: place of supply determined from evidence; RO when domestic
    place_of_supply: str = "RO"
    #: None means the user has not stated it; the deduction is then reported as missing
    #: rather than computed from an invented share
    deductibility_percent: Decimal | None = Decimal("100")
    business_use_percent: Decimal | None = Decimal("100")
    exempt: bool = False
    cash_accounting: bool = False
    note: str = ""


@dataclass(frozen=True, slots=True)
class VatAssessment:
    transaction_id: str
    vat_rate: Decimal
    collected_vat: Decimal
    deductible_vat: Decimal
    requires_d300: bool
    requires_d301: bool
    requires_d390: bool
    reportable_in_vies: bool
    journal: str | None
    explanation: str
    legal_reference: str
    missing_inputs: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "transaction_id": self.transaction_id,
            "vat_rate": str(self.vat_rate),
            "collected_vat": str(round_cents(self.collected_vat)),
            "deductible_vat": str(round_cents(self.deductible_vat)),
            "requires_d300": self.requires_d300,
            "requires_d301": self.requires_d301,
            "requires_d390": self.requires_d390,
            "reportable_in_vies": self.reportable_in_vies,
            "journal": self.journal,
            "explanation": self.explanation,
            "legal_reference": self.legal_reference,
            "missing_inputs": list(self.missing_inputs),
        }


def assess_transaction(
    pack: RulePack, tx: VatTransaction, profile: VatProfile
) -> VatAssessment:
    """Evaluate one transaction for VAT, D300, D301 and D390."""
    missing: list[str] = []
    rate = pack.vat_rate(tx.category, tx.tax_point)
    net = d(tx.net_amount_ron)
    recorded_vat = d(tx.vat_amount_ron) if tx.vat_amount_ron is not None else None

    vies_ok = bool(
        tx.counterparty_vat_id and tx.counterparty_vat_id_verified and tx.counterparty_country != "RO"
    )
    if tx.nature in INTRA_COMMUNITY_NATURES and not tx.counterparty_vat_id_verified:
        missing.append("counterparty_vat_id_verification")

    requires_d390 = tx.nature in INTRA_COMMUNITY_NATURES and vies_ok
    requires_d301 = tx.nature in ACQUISITION_NATURES or tx.nature == "domestic_reverse_charge"
    requires_d300 = False

    collected = ZERO
    deductible = ZERO
    journal: str | None = None
    explanation: str

    if tx.direction == "sale":
        if tx.exempt:
            explanation = (
                "Livrare/prestare scutită: TVA nu se colectează, dar operațiunea se "
                "raportează în jurnalul de vânzări."
            )
            requires_d300 = True
            journal = "sales"
        elif tx.place_of_supply != "RO":
            explanation = (
                f"Locul livrării/prestării este {tx.place_of_supply}: nu se colectează TVA "
                "în România. Raportarea depinde de clasificarea operațiunii, nu doar de "
                "țara clientului."
            )
            collected = ZERO
        else:
            collected = round_cents(
                recorded_vat if recorded_vat is not None else net * rate / Decimal("100")
            )
            requires_d300 = True
            journal = "sales"
            explanation = f"Livrare/prestare taxabilă în România la cota {rate}%."
    else:  # purchase
        if tx.nature in ACQUISITION_NATURES:
            reverse_charge_vat = round_cents(net * rate / Decimal("100"))
            collected = reverse_charge_vat  # taxare inversă: TVA colectată
            if profile.has_deduction_right:
                if tx.deductibility_percent is None or tx.business_use_percent is None:
                    missing.append(f"business_use_percent:{tx.id}")
                else:
                    share = d(tx.deductibility_percent) / Decimal("100")
                    use = d(tx.business_use_percent) / Decimal("100")
                    deductible = round_cents(reverse_charge_vat * share * use)
            # A normal article 316 registrant reports the reverse charge in D300;
            # a non-registered person or a special-code (art. 317) holder reports
            # it through the special return D301 instead.
            if profile.registration_kind == "article_316":
                requires_d300 = True
                requires_d301 = False
                journal = "purchases"
                explanation = (
                    "Achiziție cu taxare inversă: TVA este colectată și, dacă există drept "
                    "de deducere, dedusă prin același decont (D300)."
                )
            else:
                requires_d300 = False
                requires_d301 = True
                explanation = (
                    "Achiziție cu taxare inversă raportată prin decontul special (D301), "
                    "persoana nefiind înregistrată normal în scopuri de TVA (art. 316) sau "
                    "deținând cod special (art. 317)."
                )
        else:
            if profile.registration_kind == "article_316" and profile.has_deduction_right:
                purchase_vat = (
                    recorded_vat
                    if recorded_vat is not None
                    else round_cents(net * rate / Decimal("100"))
                )
                if tx.deductibility_percent is None or tx.business_use_percent is None:
                    missing.append(f"business_use_percent:{tx.id}")
                else:
                    share = d(tx.deductibility_percent) / Decimal("100")
                    use = d(tx.business_use_percent) / Decimal("100")
                    deductible = round_cents(purchase_vat * share * use)
            requires_d300 = profile.registration_kind == "article_316"
            journal = "purchases" if requires_d300 else None
            explanation = (
                "Achiziție internă: TVA deductibilă conform dreptului de deducere."
                if profile.registration_kind == "article_316"
                else "Achiziție pentru care persoana nu este înregistrată normal în scopuri de TVA."
            )

    if profile.registration_kind == "none" and requires_d300:
        # an unregistered person cannot file a normal VAT return
        requires_d300 = False

    return VatAssessment(
        transaction_id=tx.id,
        vat_rate=rate,
        collected_vat=collected,
        deductible_vat=deductible,
        requires_d300=requires_d300,
        requires_d301=requires_d301,
        requires_d390=requires_d390,
        reportable_in_vies=requires_d390,
        journal=journal,
        explanation=explanation,
        legal_reference="Cod fiscal art. 268-291, 314-317",
        missing_inputs=tuple(missing),
    )


# ---------------------------------------------------------------------------
# Registration turnover
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TurnoverItem:
    id: str
    occurred_on: date
    amount_ron: Decimal
    #: only taxable supplies whose place of supply is Romania count towards the
    #: registration ceiling
    counts_towards_registration: bool = True
    place_of_supply: str = "RO"
    exempt: bool = False


@dataclass(frozen=True, slots=True)
class RegistrationAssessment:
    as_of: date
    rolling_12m_turnover: Decimal
    threshold_ron: Decimal
    exceeded: bool
    crossing_date: date | None
    registration_deadline: date | None
    registration_effective_from: date | None
    special_code_required: bool
    special_code_reason: str | None
    threshold_period_note: str
    legal_reference: str

    def as_dict(self) -> dict:
        return {
            "as_of": self.as_of.isoformat(),
            "rolling_12m_turnover": str(round_cents(self.rolling_12m_turnover)),
            "threshold_ron": str(self.threshold_ron),
            "exceeded": self.exceeded,
            "crossing_date": self.crossing_date.isoformat() if self.crossing_date else None,
            "registration_deadline": (
                self.registration_deadline.isoformat() if self.registration_deadline else None
            ),
            "registration_effective_from": (
                self.registration_effective_from.isoformat()
                if self.registration_effective_from
                else None
            ),
            "special_code_required": self.special_code_required,
            "special_code_reason": self.special_code_reason,
            "threshold_period_note": self.threshold_period_note,
            "legal_reference": self.legal_reference,
        }


def _month_end(day: date) -> date:
    if day.month == 12:
        return date(day.year, 12, 31)
    return date(day.year, day.month + 1, 1) - timedelta(days=1)


def assess_registration(
    pack: RulePack,
    items: tuple[TurnoverItem, ...],
    *,
    as_of: date,
    special_code_transactions: tuple[VatTransaction, ...] = (),
) -> RegistrationAssessment:
    """Evaluate the art. 316 registration ceiling.

    VAT-registration turnover is a distinct statutory measure: it is not the sum
    of bank deposits and not D212 taxable income.
    """
    counted = [item for item in items if item.counts_towards_registration and not item.exempt]
    window_start = date(as_of.year - 1, as_of.month, 1)
    if as_of.month == 12:
        window_start = date(as_of.year - 1, 12, 1)
    else:
        window_start = date(as_of.year - 1, as_of.month + 1, 1)
    window_start = min(window_start, as_of)
    rolling = sum(
        (d(item.amount_ron) for item in counted if window_start <= item.occurred_on <= as_of),
        ZERO,
    )

    threshold = pack.vat_threshold_on(as_of)
    threshold_period_note = (
        f"Pragul aplicabil la {as_of.isoformat()} este {threshold} RON. "
        "Pragurile anterioare se păstrează pentru perioade istorice."
    )

    running = ZERO
    crossing: date | None = None
    ordered = sorted(counted, key=lambda item: item.occurred_on)
    for item in ordered:
        if item.occurred_on > as_of:
            break
        running += d(item.amount_ron)
        if running > threshold and crossing is None:
            crossing = item.occurred_on

    exceeded = crossing is not None
    registration_deadline = (crossing + timedelta(days=10)) if crossing else None
    # registration is effective from the first day of the month following the
    # month in which the ceiling was exceeded
    if crossing is None:
        effective_from = None
    elif crossing.month == 12:
        effective_from = date(crossing.year + 1, 1, 1)
    else:
        effective_from = date(crossing.year, crossing.month + 1, 1)

    special_required = any(
        tx.nature in INTRA_COMMUNITY_NATURES or tx.nature == "non_eu_services_acquisition"
        for tx in special_code_transactions
    )
    special_reason = (
        "Există achiziții intracomunitare de bunuri/servicii sau servicii din afara UE: "
        "eligibilitatea și obligațiile codului special (art. 317) trebuie evaluate chiar "
        "dacă cifra de afaceri internă este sub pragul de scutire."
        if special_required
        else None
    )

    return RegistrationAssessment(
        as_of=as_of,
        rolling_12m_turnover=rolling,
        threshold_ron=threshold,
        exceeded=exceeded,
        crossing_date=crossing,
        registration_deadline=registration_deadline,
        registration_effective_from=effective_from,
        special_code_required=special_required,
        special_code_reason=special_reason,
        threshold_period_note=threshold_period_note,
        legal_reference="Cod fiscal art. 310, 316, 317",
    )


# ---------------------------------------------------------------------------
# VAT journals (D300 support)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VatJournalLine:
    transaction_id: str
    tax_point: date
    direction: str
    nature: str
    base_ron: Decimal
    rate: Decimal
    vat_ron: Decimal
    category: str
    counterparty_country: str
    note: str = ""


@dataclass(frozen=True, slots=True)
class VatJournals:
    sales: tuple[VatJournalLine, ...]
    purchases: tuple[VatJournalLine, ...]
    collected_total: Decimal
    deductible_total: Decimal
    balance_payable: Decimal
    balance_carryforward: Decimal
    previous_carryforward: Decimal
    refund_requested: Decimal
    assessments: tuple[VatAssessment, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {
            "sales": [
                {
                    "transaction_id": line.transaction_id,
                    "tax_point": line.tax_point.isoformat(),
                    "base_ron": str(round_cents(line.base_ron)),
                    "rate": str(line.rate),
                    "vat_ron": str(round_cents(line.vat_ron)),
                    "category": line.category,
                    "counterparty_country": line.counterparty_country,
                    "note": line.note,
                }
                for line in self.sales
            ],
            "purchases": [
                {
                    "transaction_id": line.transaction_id,
                    "tax_point": line.tax_point.isoformat(),
                    "base_ron": str(round_cents(line.base_ron)),
                    "rate": str(line.rate),
                    "vat_ron": str(round_cents(line.vat_ron)),
                    "category": line.category,
                    "counterparty_country": line.counterparty_country,
                    "note": line.note,
                }
                for line in self.purchases
            ],
            "collected_total": str(round_cents(self.collected_total)),
            "deductible_total": str(round_cents(self.deductible_total)),
            "balance_payable": str(round_cents(self.balance_payable)),
            "balance_carryforward": str(round_cents(self.balance_carryforward)),
            "previous_carryforward": str(round_cents(self.previous_carryforward)),
            "refund_requested": str(round_cents(self.refund_requested)),
            "assessments": [assessment.as_dict() for assessment in self.assessments],
        }


def build_journals(
    pack: RulePack,
    transactions: tuple[VatTransaction, ...],
    profile: VatProfile,
    *,
    previous_carryforward: Decimal = ZERO,
    refund_requested: Decimal = ZERO,
    request_refund: bool = False,
) -> VatJournals:
    sales: list[VatJournalLine] = []
    purchases: list[VatJournalLine] = []
    assessments: list[VatAssessment] = []
    collected = ZERO
    deductible = ZERO

    for tx in sorted(transactions, key=lambda t: (t.tax_point, t.id)):
        assessment = assess_transaction(pack, tx, profile)
        assessments.append(assessment)
        if assessment.missing_inputs:
            continue
        line = VatJournalLine(
            transaction_id=tx.id,
            tax_point=tx.tax_point,
            direction=tx.direction,
            nature=tx.nature,
            base_ron=d(tx.net_amount_ron),
            rate=(d(tx.recorded_rate) if tx.recorded_rate is not None else assessment.vat_rate),
            vat_ron=(
                assessment.collected_vat
                if tx.direction == "sale"
                else assessment.deductible_vat
            ),
            category=tx.category,
            counterparty_country=tx.counterparty_country,
            note=tx.note,
        )
        if assessment.journal == "sales":
            sales.append(line)
        elif assessment.journal == "purchases":
            purchases.append(line)
        collected += assessment.collected_vat
        deductible += assessment.deductible_vat

    collected = round_cents(collected)
    deductible = round_cents(deductible)
    previous_carryforward = round_cents(previous_carryforward)
    net = collected - deductible - previous_carryforward
    if net > ZERO:
        payable = net
        carryforward = ZERO
    else:
        payable = ZERO
        carryforward = round_cents(-net)
    refund = round_cents(refund_requested) if request_refund else ZERO
    if refund > carryforward:
        raise RulePackError("the refund requested cannot exceed the carryforward balance")

    return VatJournals(
        sales=tuple(sales),
        purchases=tuple(purchases),
        collected_total=collected,
        deductible_total=deductible,
        balance_payable=payable,
        balance_carryforward=carryforward,
        previous_carryforward=previous_carryforward,
        refund_requested=refund,
        assessments=tuple(assessments),
    )
