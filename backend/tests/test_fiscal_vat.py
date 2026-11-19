"""VAT fixtures: rates, place of supply, reverse charge, D301/D390, registration.

Requirement IDs: VAT, VAT-01, VAT-02, VAT-03.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from pfa.fiscal.packs import RulePackError, get_pack
from pfa.fiscal.vat import (
    TurnoverItem,
    VatProfile,
    VatTransaction,
    assess_registration,
    assess_transaction,
    build_journals,
)


def tx(**overrides) -> VatTransaction:
    base = {
        "id": "tx-1",
        "occurred_on": date(2025, 6, 1),
        "tax_point": date(2025, 6, 1),
        "direction": "sale",
        "nature": "domestic_supply",
        "category": "standard",
        "net_amount_ron": Decimal("1000"),
    }
    base.update(overrides)
    return VatTransaction(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# VAT-03: rates are selected from the tax point, historical rates are retained
# ---------------------------------------------------------------------------


def test_rate_transition_on_1_august_2025() -> None:
    pack = get_pack(2025)
    assert pack.vat_rate("standard", date(2025, 7, 31)) == Decimal("19")
    assert pack.vat_rate("standard", date(2025, 8, 1)) == Decimal("21")
    assert pack.vat_rate("reduced", date(2025, 7, 31)) == Decimal("9")
    assert pack.vat_rate("reduced", date(2025, 8, 1)) == Decimal("11")
    # the 5% rate was removed; former 5% categories move to the reduced rate
    assert pack.vat_rate("second_reduced", date(2025, 7, 31)) == Decimal("5")
    assert pack.vat_rate("second_reduced", date(2025, 8, 1)) == Decimal("11")


def test_rate_is_chosen_from_the_supply_date_not_the_entry_date() -> None:
    pack = get_pack(2025)
    # a July supply entered in September keeps the July rate
    old_supply = tx(tax_point=date(2025, 7, 15), occurred_on=date(2025, 9, 3))
    assessment = assess_transaction(pack, old_supply, VatProfile(registration_kind="article_316"))
    assert assessment.vat_rate == Decimal("19")
    assert assessment.collected_vat == Decimal("190.00")


def test_tax_point_and_cash_recognition_dates_are_independent() -> None:
    pack = get_pack(2025)
    credit_note = tx(
        id="cn-1",
        direction="sale",
        tax_point=date(2025, 7, 10),
        occurred_on=date(2025, 8, 5),
        net_amount_ron=Decimal("-500"),
        note="Storno pentru livrare iulie",
    )
    assessment = assess_transaction(pack, credit_note, VatProfile(registration_kind="article_316"))
    assert assessment.vat_rate == Decimal("19")
    assert assessment.collected_vat == Decimal("-95.00")


# ---------------------------------------------------------------------------
# VAT-01 / VAT-02: article 317, D301 and D390 are separate questions
# ---------------------------------------------------------------------------


def test_article_317_eu_b2b_service_triggers_d301_and_d390_without_normal_vat() -> None:
    pack = get_pack(2025)
    purchase = tx(
        id="eu-svc",
        direction="purchase",
        nature="ic_services_acquisition",
        category="standard",
        net_amount_ron=Decimal("2000"),
        tax_point=date(2025, 9, 1),
        occurred_on=date(2025, 9, 1),
        counterparty_country="DE",
        counterparty_vat_id="DE123456789",
        counterparty_vat_id_verified=True,
        place_of_supply="RO",
    )
    assessment = assess_transaction(pack, purchase, VatProfile(registration_kind="article_317"))
    assert assessment.requires_d301 is True
    assert assessment.requires_d390 is True
    assert assessment.reportable_in_vies is True
    # a special-code taxpayer is not a normal domestic VAT registrant
    assert assessment.requires_d300 is False
    # reverse charge: collected and, with a deduction right, deducted
    assert assessment.collected_vat == Decimal("420.00")


def test_non_eu_service_requires_d301_but_not_d390() -> None:
    pack = get_pack(2025)
    purchase = tx(
        id="us-svc",
        direction="purchase",
        nature="non_eu_services_acquisition",
        net_amount_ron=Decimal("3000"),
        counterparty_country="US",
        counterparty_vat_id=None,
        place_of_supply="RO",
    )
    assessment = assess_transaction(pack, purchase, VatProfile(registration_kind="article_317"))
    assert assessment.requires_d301 is True
    assert assessment.requires_d390 is False
    assert assessment.reportable_in_vies is False


def test_eu_classification_without_a_verified_vat_id_is_missing_information() -> None:
    pack = get_pack(2025)
    purchase = tx(
        id="eu-unverified",
        direction="purchase",
        nature="ic_services_acquisition",
        net_amount_ron=Decimal("1000"),
        counterparty_country="IT",
        counterparty_vat_id="IT99999999999",
        counterparty_vat_id_verified=False,
    )
    assessment = assess_transaction(pack, purchase, VatProfile(registration_kind="article_317"))
    assert assessment.requires_d390 is False
    assert "counterparty_vat_id_verification" in assessment.missing_inputs


def test_eu_country_alone_does_not_create_an_obligation() -> None:
    """A foreign customer is not automatically a reportable intra-community sale."""
    pack = get_pack(2025)
    sale = tx(
        id="sale-fr",
        direction="sale",
        nature="domestic_supply",
        place_of_supply="RO",
        tax_point=date(2025, 9, 1),
        occurred_on=date(2025, 9, 1),
        counterparty_country="FR",
        counterparty_vat_id="FR12345678901",
        counterparty_vat_id_verified=True,
    )
    assessment = assess_transaction(pack, sale, VatProfile(registration_kind="article_316"))
    assert assessment.requires_d390 is False
    assert assessment.collected_vat == Decimal("210.00")  # domestic place of supply


# ---------------------------------------------------------------------------
# Registration turnover
# ---------------------------------------------------------------------------


def test_registration_threshold_changes_on_1_september_2025() -> None:
    pack = get_pack(2025)
    before = assess_registration(pack, (), as_of=date(2025, 8, 31))
    after = assess_registration(pack, (), as_of=date(2025, 9, 1))
    assert before.threshold_ron == Decimal("300000")
    assert after.threshold_ron == Decimal("395000")


def test_registration_deadline_and_effective_date() -> None:
    pack = get_pack(2025)
    items = tuple(
        TurnoverItem(id=f"i-{index}", occurred_on=date(2025, 10, index), amount_ron=Decimal("100000"))
        for index in range(1, 5)
    )
    assessment = assess_registration(pack, items, as_of=date(2025, 10, 31))
    assert assessment.exceeded is True
    # the ceiling is crossed on 4 October; registration is requested within 10 days
    assert assessment.crossing_date == date(2025, 10, 4)
    assert assessment.registration_deadline == date(2025, 10, 14)
    assert assessment.registration_effective_from == date(2025, 11, 1)
    # 395.000 was crossed by the fourth 100.000 item, not before
    assert assessment.rolling_12m_turnover == Decimal("400000")


def test_special_code_is_evaluated_below_the_domestic_threshold() -> None:
    pack = get_pack(2025)
    special = tx(
        id="special",
        direction="purchase",
        nature="ic_goods_acquisition",
        net_amount_ron=Decimal("5000"),
        counterparty_country="HU",
        counterparty_vat_id="HU12345678",
        counterparty_vat_id_verified=True,
    )
    assessment = assess_registration(pack, (), as_of=date(2025, 5, 1), special_code_transactions=(special,))
    assert assessment.exceeded is False
    assert assessment.special_code_required is True
    assert "art. 317" in (assessment.special_code_reason or "")


def test_vat_registration_turnover_is_not_bank_deposits() -> None:
    """Only taxable supplies with a Romanian place of supply count."""
    pack = get_pack(2025)
    items = (
        TurnoverItem(id="ro", occurred_on=date(2025, 3, 1), amount_ron=Decimal("200000")),
        TurnoverItem(
            id="export",
            occurred_on=date(2025, 3, 2),
            amount_ron=Decimal("500000"),
            place_of_supply="non-EU",
            counts_towards_registration=False,
        ),
        TurnoverItem(
            id="exempt", occurred_on=date(2025, 3, 3), amount_ron=Decimal("90000"), exempt=True
        ),
    )
    assessment = assess_registration(pack, items, as_of=date(2025, 3, 31))
    assert assessment.rolling_12m_turnover == Decimal("200000")
    assert assessment.exceeded is False


# ---------------------------------------------------------------------------
# Journals and D300 arithmetic
# ---------------------------------------------------------------------------


def test_journals_split_sales_purchases_and_carryforward() -> None:
    pack = get_pack(2025)
    transactions = (
        tx(id="sale", direction="sale", net_amount_ron=Decimal("10000"), tax_point=date(2025, 9, 5)),
        tx(
            id="purchase",
            direction="purchase",
            nature="domestic_acquisition",
            net_amount_ron=Decimal("4000"),
            tax_point=date(2025, 9, 6),
            deductibility_percent=Decimal("50"),
        ),
    )
    journals = build_journals(
        pack,
        transactions,
        VatProfile(registration_kind="article_316"),
        previous_carryforward=Decimal("500"),
    )
    assert journals.collected_total == Decimal("2100.00")
    assert journals.deductible_total == Decimal("420.00")  # 840 x 50%
    assert journals.balance_payable == Decimal("1180.00")  # 2100 - 420 - 500
    assert journals.balance_carryforward == Decimal("0")


def test_negative_balance_becomes_carryforward_and_refund_is_bounded() -> None:
    pack = get_pack(2025)
    transactions = (
        tx(
            id="purchase",
            direction="purchase",
            nature="domestic_acquisition",
            net_amount_ron=Decimal("10000"),
            tax_point=date(2025, 9, 6),
        ),
    )
    journals = build_journals(pack, transactions, VatProfile(registration_kind="article_316"))
    assert journals.balance_payable == Decimal("0")
    assert journals.balance_carryforward == Decimal("2100.00")
    with pytest.raises(RulePackError):
        build_journals(
            pack,
            transactions,
            VatProfile(registration_kind="article_316"),
            refund_requested=Decimal("5000"),
            request_refund=True,
        )
