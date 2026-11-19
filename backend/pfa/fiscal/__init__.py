"""Deterministic fiscal domain package.

Independent of HTTP, UI and database.  Accepts normalized accounting events,
taxpayer facts and a versioned rule pack, and returns obligations, amounts,
explanations and unresolved facts.
"""

from pfa.fiscal.calendar import (
    BUCHAREST,
    CALENDARS,
    RO_CALENDAR_2025,
    RO_CALENDAR_2026,
    LegalCalendar,
)
from pfa.fiscal.engine import (
    ENGINE_VERSION,
    DepreciationItem,
    ExpenseItem,
    IncomeCalculationResult,
    RevenueItem,
    TaxpayerFacts,
    TraceLine,
    calculate_income_tax,
    minimum_wage_for_thresholds,
)
from pfa.fiscal.money import (
    CENT,
    PRECISION,
    ZERO,
    d,
    percent_of,
    quantize,
    round_cents,
    round_ron,
)
from pfa.fiscal.rules import RulePack, RulePackError
from pfa.fiscal.vat import (
    RegistrationAssessment,
    TurnoverItem,
    VatAssessment,
    VatJournals,
    VatProfile,
    VatTransaction,
    assess_registration,
    assess_transaction,
    build_journals,
)

__all__ = [
    "BUCHAREST",
    "CALENDARS",
    "CENT",
    "ENGINE_VERSION",
    "PRECISION",
    "RO_CALENDAR_2025",
    "RO_CALENDAR_2026",
    "ZERO",
    "DepreciationItem",
    "ExpenseItem",
    "IncomeCalculationResult",
    "LegalCalendar",
    "RegistrationAssessment",
    "RevenueItem",
    "RulePack",
    "RulePackError",
    "TaxpayerFacts",
    "TraceLine",
    "TurnoverItem",
    "VatAssessment",
    "VatJournals",
    "VatProfile",
    "VatTransaction",
    "assess_registration",
    "assess_transaction",
    "build_journals",
    "calculate_income_tax",
    "d",
    "minimum_wage_for_thresholds",
    "percent_of",
    "quantize",
    "round_cents",
    "round_ron",
]
