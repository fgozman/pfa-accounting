"""Income-tax and social-contribution fixtures.

Requirement IDs: TAX-01, TAX-02, TAX-03, ACC-02, REAL, NORM.

Expected values here are written independently of the engine (hand-computed from
the rule-pack parameters), so the suite does not merely assert that the
calculator agrees with itself.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from pfa.fiscal.engine import (
    DepreciationItem,
    ExpenseItem,
    RevenueItem,
    TaxpayerFacts,
    calculate_income_tax,
    minimum_wage_for_thresholds,
)
from pfa.fiscal.money import MoneyError, allocate_proportionally, d, round_cents
from pfa.fiscal.packs import get_pack


def facts(**overrides) -> TaxpayerFacts:
    base = {"income_year": 2025, "regime": "real"}
    base.update(overrides)
    return TaxpayerFacts(**base)  # type: ignore[arg-type]


def revenue(amount: str, day: date = date(2025, 6, 1)) -> RevenueItem:
    return RevenueItem(id=f"rev-{amount}", occurred_on=day, amount_ron=d(amount))


# ---------------------------------------------------------------------------
# Money
# ---------------------------------------------------------------------------


def test_decimal_arithmetic_is_exact() -> None:
    assert d("0.1") + d("0.2") == d("0.3")
    assert round_cents(Decimal("2.345")) == Decimal("2.35")
    assert round_cents(Decimal("2.344")) == Decimal("2.34")
    with pytest.raises(MoneyError):
        d(True)


def test_allocation_preserves_total() -> None:
    shares = allocate_proportionally(Decimal("100.00"), [d("1"), d("1"), d("1")])
    assert sum(shares) == Decimal("100.00")
    assert shares == [Decimal("33.34"), Decimal("33.33"), Decimal("33.33")]


# ---------------------------------------------------------------------------
# TAX-01: threshold boundaries
# ---------------------------------------------------------------------------


def test_zero_and_loss_cases_produce_no_contributions() -> None:
    result = calculate_income_tax(get_pack(2025), facts=facts())
    assert result.net_income == 0
    assert result.cas_amount == 0
    assert result.cass_amount == 0
    assert result.income_tax == 0
    assert result.total_due == 0


def test_cass_starts_above_six_minimum_wages_2025() -> None:
    pack = get_pack(2025)
    minimum_wage = minimum_wage_for_thresholds(pack)
    assert minimum_wage == Decimal("4050.00")

    # exactly at the threshold: still not mandatory
    at_threshold = calculate_income_tax(
        pack, facts=facts(), revenues=(revenue("24300"),)
    )
    assert at_threshold.cass_mandatory is False
    assert at_threshold.cass_base == 0

    # one leu above: the six-wage base becomes mandatory
    above = calculate_income_tax(pack, facts=facts(), revenues=(revenue("24301"),))
    assert above.cass_mandatory is True
    assert above.cass_base == Decimal("24300.000000")
    assert above.cass_amount == Decimal("2430")


def test_cass_second_bracket_2025() -> None:
    pack = get_pack(2025)
    # net income 60.000 -> between 12 and 24 minimum wages (48.600 / 97.200)
    result = calculate_income_tax(pack, facts=facts(), revenues=(revenue("60000"),))
    assert result.cass_base == Decimal("48600.000000")
    assert result.cass_amount == Decimal("4860")


def test_cas_is_not_mandatory_below_twelve_minimum_wages() -> None:
    pack = get_pack(2025)
    result = calculate_income_tax(pack, facts=facts(), revenues=(revenue("40000"),))
    assert result.cas_mandatory is False
    assert result.cas_amount == 0
    assert result.cass_amount == Decimal("2430")  # CASS is still due above 6 wages


def test_cas_becomes_mandatory_above_twelve_minimum_wages() -> None:
    pack = get_pack(2025)
    result = calculate_income_tax(pack, facts=facts(), revenues=(revenue("60000"),))
    assert result.cas_mandatory is True
    assert result.cas_base == Decimal("48600.000000")
    assert result.cas_amount == Decimal("12150")


def test_cas_election_below_the_floor_uses_the_elected_base() -> None:
    pack = get_pack(2025)
    result = calculate_income_tax(
        pack, facts=facts(cas_elected=True), revenues=(revenue("30000"),)
    )
    assert result.cas_base == Decimal("48600.000000")
    assert result.cas_amount == Decimal("12150")


def test_exemptions_zero_the_contribution() -> None:
    pack = get_pack(2025)
    result = calculate_income_tax(
        pack,
        facts=facts(cas_exempt=True, cass_exempt=True),
        revenues=(revenue("200000"),),
    )
    assert result.cas_amount == 0
    assert result.cass_amount == 0


def test_salary_status_does_not_suppress_contributions() -> None:
    pack = get_pack(2025)
    result = calculate_income_tax(
        pack, facts=facts(salary_income_ron=d("120000")), revenues=(revenue("60000"),)
    )
    assert result.cas_amount > 0
    assert result.cass_amount > 0
    assert any(line.code == "salary_status" for line in result.trace)


# ---------------------------------------------------------------------------
# TAX-02: 2025 vs 2026 parameters, historical immutability
# ---------------------------------------------------------------------------


def test_cass_ceiling_differs_between_2025_and_2026() -> None:
    """Same nominal case, different legal ceiling per income year."""
    income = "1500000"  # well above every bracket
    other_income_base = "200000"

    result_2025 = calculate_income_tax(
        get_pack(2025),
        facts=facts(
            income_year=2025,
            other_income_base_ron=d(other_income_base),
        ),
        revenues=(revenue(income),),
    )
    result_2026 = calculate_income_tax(
        get_pack(2026),
        facts=facts(
            income_year=2026,
            other_income_base_ron=d(other_income_base),
        ),
        revenues=(revenue(income, day=date(2026, 6, 1)),),
    )

    # 2025 ceiling: 60 x 4.050 = 243.000; remaining after the other category: 43.000
    assert result_2025.cass_ceiling_applied == Decimal("243000.000000")
    assert result_2025.cass_base == Decimal("43000.000000")
    # 2026 ceiling: 72 x 4.050 = 291.600; remaining: 91.600
    assert result_2026.cass_ceiling_applied == Decimal("291600.000000")
    assert result_2026.cass_base == Decimal("91600.000000")
    assert result_2025.cass_ceiling_applied != result_2026.cass_ceiling_applied


def test_historical_pack_is_pinned_and_never_rewritten() -> None:
    pack_2025 = get_pack(2025)
    result = calculate_income_tax(
        pack_2025, facts=facts(income_year=2025), revenues=(revenue("100000"),)
    )
    assert result.rule_pack_code == "RO-INCOME-2025.1"
    assert result.rule_pack_version == 1
    parameters = pack_2025.to_parameters()
    assert parameters["contributions"]["CASS"]["max_base_multiple"] == "60"
    assert get_pack(2026).to_parameters()["contributions"]["CASS"]["max_base_multiple"] == "72"


def test_minimum_wage_midyear_change_does_not_move_the_bracket() -> None:
    pack_2026 = get_pack(2026)
    # thresholds use the wage in force at the start of the income year
    assert minimum_wage_for_thresholds(pack_2026) == Decimal("4050.00")
    assert pack_2026.minimum_wage_on(date(2026, 12, 1)) == Decimal("4325.00")


# ---------------------------------------------------------------------------
# ACC-02 / REAL: deductibility and business use are separate from VAT
# ---------------------------------------------------------------------------


def test_partially_deductible_expense_is_adjusted() -> None:
    pack = get_pack(2025)
    expenses = (
        ExpenseItem(
            id="exp-1",
            occurred_on=date(2025, 3, 1),
            amount_ron=d("2000"),
            deductibility="partially_deductible",
            deductibility_percent=d("100"),
            business_use_percent=d("50"),
        ),
    )
    result = calculate_income_tax(
        pack, facts=facts(), revenues=(revenue("10000"),), expenses=expenses
    )
    assert result.eligible_expenses == Decimal("1000.000000")
    assert result.non_deductible_expenses == Decimal("1000.000000")
    assert result.net_income == Decimal("9000.000000")


def test_undetermined_deductibility_blocks_and_is_reported() -> None:
    pack = get_pack(2025)
    expenses = (
        ExpenseItem(
            id="exp-1",
            occurred_on=date(2025, 3, 1),
            amount_ron=d("2000"),
            deductibility="undetermined",
        ),
    )
    result = calculate_income_tax(
        pack, facts=facts(), revenues=(revenue("10000"),), expenses=expenses
    )
    assert "expense_deductibility:exp-1" in result.unresolved
    assert result.eligible_expenses == 0


def test_capital_asset_does_not_get_both_expense_and_depreciation() -> None:
    pack = get_pack(2025)
    result = calculate_income_tax(
        pack,
        facts=facts(),
        revenues=(revenue("50000"),),
        depreciation=(
            DepreciationItem(
                id="dep-1",
                asset_id="asset-1",
                amount_ron=d("6000"),
                deductible_amount_ron=d("6000"),
            ),
        ),
    )
    # the acquisition cost is not an eligible expense anywhere in the trace
    assert result.depreciation == Decimal("6000.000000")
    assert result.eligible_expenses == 0
    assert result.net_income == Decimal("44000.000000")


# ---------------------------------------------------------------------------
# Loss carryforward and final tax
# ---------------------------------------------------------------------------


def test_loss_carryforward_reduces_the_base_then_contributions_are_computed() -> None:
    pack = get_pack(2025)
    with_loss = calculate_income_tax(
        pack, facts=facts(prior_losses=d("20000")), revenues=(revenue("60000"),)
    )
    without_loss = calculate_income_tax(pack, facts=facts(), revenues=(revenue("60000"),))
    assert with_loss.loss_offset == Decimal("20000.000000")
    assert with_loss.net_income == Decimal("40000.000000")
    # with the loss applied the CASS bracket falls from 12 to 6 minimum wages
    assert with_loss.cass_base == Decimal("24300.000000")
    assert without_loss.cass_base == Decimal("48600.000000")


def test_income_tax_is_rounded_to_whole_ron_and_contributions_are_deductible() -> None:
    pack = get_pack(2025)
    result = calculate_income_tax(pack, facts=facts(), revenues=(revenue("100000"),))
    # net 100.000 -> above 24 minimum wages: CASS base 24 x 4.050 = 97.200 -> 9.720
    assert result.cass_base == Decimal("97200.000000")
    assert result.cass_amount == Decimal("9720")
    # CAS mandatory above 24 minimum wages: base 24 x 4.050 = 97.200 -> 24.300
    assert result.cas_base == Decimal("97200.000000")
    assert result.cas_amount == Decimal("24300")
    assert result.total_contributions == Decimal("34020")
    assert result.taxable_income == Decimal("65980.000000")
    # 10% of 65.980 = 6.598,00 -> whole RON
    assert result.income_tax == Decimal("6598")
    assert result.total_due == Decimal("40618")


def test_foreign_tax_credit_is_capped_by_the_romanian_tax() -> None:
    pack = get_pack(2025)
    result = calculate_income_tax(
        pack,
        facts=facts(foreign_tax_paid_ron=d("99999")),
        revenues=(revenue("100000"),),
    )
    assert result.foreign_tax_credit == result.income_tax
    assert result.total_due == result.total_contributions


# ---------------------------------------------------------------------------
# TAX-03 / NORM
# ---------------------------------------------------------------------------


def test_norm_tax_uses_the_published_norm_not_actual_profit() -> None:
    pack = get_pack(2025)
    result = calculate_income_tax(
        pack,
        facts=facts(
            regime="norm",
            norm_amount=d("45000"),
            norm_adjustment_percent=d("0"),
            active_months=12,
        ),
        revenues=(revenue("1000000"),),
        expenses=(
            ExpenseItem(
                id="exp-1", occurred_on=date(2025, 3, 1), amount_ron=d("800000")
            ),
        ),
    )
    assert result.norm_income == Decimal("45000.000000")
    assert result.net_income == Decimal("45000.000000")
    # norm income is final-taxed: contributions are not deducted
    assert result.taxable_income == Decimal("45000.000000")
    assert result.income_tax == Decimal("4500")
    assert result.eligible_expenses == 0  # operating expenses do not reduce the norm base


def test_norm_proration_for_a_partial_year() -> None:
    pack = get_pack(2025)
    result = calculate_income_tax(
        pack,
        facts=facts(
            regime="norm",
            norm_amount=d("48000"),
            norm_adjustment_percent=d("0"),
            active_months=6,
        ),
    )
    assert result.norm_income == Decimal("24000.000000")


def test_norm_missing_inputs_are_unresolved() -> None:
    pack = get_pack(2025)
    result = calculate_income_tax(pack, facts=facts(regime="norm"))
    assert "norm_amount" in result.unresolved
    assert result.norm_income == 0


def test_norm_adjustment_factors_are_applied() -> None:
    pack = get_pack(2025)
    result = calculate_income_tax(
        pack,
        facts=facts(
            regime="norm",
            norm_amount=d("40000"),
            norm_adjustment_percent=d("10"),
            active_months=12,
        ),
    )
    assert result.norm_income == Decimal("44000.000000")


def test_unknown_facts_are_never_treated_as_zero() -> None:
    pack = get_pack(2025)
    result = calculate_income_tax(
        pack, facts=facts(unknown_facts=("cas_election",)), revenues=(revenue("60000"),)
    )
    assert any(item.endswith("cas_election") for item in result.unresolved)
