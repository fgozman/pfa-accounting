"""Romanian income-tax and VAT rule pack for income year 2026.

Verified change represented in a separate year pack: the maximum CASS base for
independent activities increases from 60 minimum wages (2025) to 72 (2026).  The
newer ceiling must never be applied to the previous income year.

Sources:
* ANAF guidance for independent activities, 22 April 2026 -
  https://static.anaf.ro/static/10/Cluj/cj_DU_activ_indep_22apr2026.pdf
* Fiscal Code arts. 68-69, 118, 148-151, 170-174 -
  https://legislatie.just.ro/Public/DetaliiDocument/228099
* D212 version page - https://static.anaf.ro/static/10/Anaf/Declaratii_R/declaratie_unica.html
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pfa.fiscal.rules import (
    ContributionBracket,
    ContributionSchedule,
    IncomeTaxParameters,
    MinimumWagePeriod,
    RulePack,
    VatRatePeriod,
    VatRegistrationThresholdPeriod,
)

CODE = "RO-INCOME-2026.1"

PACK = RulePack(
    code=CODE,
    jurisdiction="RO",
    income_year=2026,
    domain="income",
    title="Impozit pe venit, contribuții sociale și TVA — anul 2026",
    version=1,
    valid_from=date(2026, 1, 1),
    valid_to=date(2026, 12, 31),
    publication_date=date(2026, 4, 22),
    source_urls=(
        "https://static.anaf.ro/static/10/Cluj/cj_DU_activ_indep_22apr2026.pdf",
        "https://legislatie.just.ro/Public/DetaliiDocument/228099",
        "https://static.anaf.ro/static/10/Anaf/Declaratii_R/declaratie_unica.html",
    ),
    formula_version="income-2026.1",
    minimum_wages=(
        MinimumWagePeriod(
            effective_from=date(2026, 1, 1),
            monthly_gross_ron=Decimal("4050.00"),
            legal_reference="Salariul de bază minim brut pe țară garantat în plată, 2026",
        ),
        MinimumWagePeriod(
            effective_from=date(2026, 7, 1),
            monthly_gross_ron=Decimal("4325.00"),
            legal_reference="HG 146/2026 (majorarea salariului minim de la 1 iulie 2026)",
        ),
    ),
    cas=ContributionSchedule(
        code="CAS",
        label_ro="Contribuția de asigurări sociale",
        rate=Decimal("25"),
        brackets=(
            ContributionBracket(Decimal("12"), Decimal("12"), False),
            ContributionBracket(Decimal("24"), Decimal("12"), True),
            ContributionBracket(None, Decimal("24"), True),
        ),
        max_base_multiple=None,
        election_base_multiple=Decimal("12"),
        legal_reference="Cod fiscal art. 148-151",
    ),
    cass=ContributionSchedule(
        code="CASS",
        label_ro="Contribuția de asigurări sociale de sănătate",
        rate=Decimal("10"),
        brackets=(
            ContributionBracket(Decimal("6"), Decimal("0"), False),
            ContributionBracket(Decimal("12"), Decimal("6"), True),
            ContributionBracket(Decimal("24"), Decimal("12"), True),
            ContributionBracket(None, Decimal("24"), True),
        ),
        # 2026: the aggregate yearly CASS base ceiling rises from 60 to 72
        # minimum wages.  Never applied to income year 2025.
        max_base_multiple=Decimal("72"),
        election_base_multiple=Decimal("6"),
        legal_reference="Cod fiscal art. 170-174, modificat pentru 2026",
    ),
    income_tax=IncomeTaxParameters(
        rate=Decimal("10"),
        norm_tax_is_final=True,
        loss_offset_limit_rate=Decimal("1"),
        loss_carryforward_years=5,
        legal_reference="Cod fiscal art. 68-69, 118",
    ),
    vat_rates=(
        VatRatePeriod(
            effective_from=date(2025, 8, 1),
            effective_to=None,
            standard=Decimal("21"),
            reduced=Decimal("11"),
            second_reduced=None,
            note="Cotele în vigoare de la 1 august 2025, menținute în 2026",
        ),
    ),
    vat_thresholds=(
        VatRegistrationThresholdPeriod(
            effective_from=date(2025, 9, 1),
            effective_to=None,
            threshold_ron=Decimal("395000"),
            source_url=(
                "https://static.anaf.ro/static/3/Cluj/20250912120026_cj_%20plafon_tva_12sep2025.pdf"
            ),
        ),
    ),
    deadline_calendar_version="RO-2026.1",
    supported_scenarios=(
        "real_system",
        "income_norm",
        "no_vat",
        "vat_article_316",
        "vat_article_317",
        "eu_b2b_services",
        "non_eu_b2b_services",
        "loss_carryforward",
        "partial_year",
        "minimum_wage_midyear_change",
    ),
    regression_fixtures=(
        {
            "id": "2026-cass-cap-72-multiple",
            "note": "Plafonul agregat CASS 2026 este de 72 de salarii minime",
            "expected_max_base_multiple": "72",
        },
        {
            "id": "2026-minimum-wage-midyear",
            "note": "Salariul minim se schimbă la 1 iulie 2026; pragurile se determină "
            "cu valoarea în vigoare la data aplicabilă.",
            "as_of": "2026-07-01",
            "expected_minimum_wage": "4325.00",
        },
    ),
    status="pending_review",
    reviewed=False,
    notes=(
        "Pachetul 2026 nu se aplică retroactiv anului 2025: recalcularea unui return "
        "istoric folosește pachetul fixat la momentul depunerii.",
        "Schema finală a D212 pentru venitul anului 2026 nu este stabilită la data "
        "cercetării; generarea pentru depunere rămâne blocată până la publicare.",
    ),
    extra={
        "cass_ceiling_increased_from": "60",
        "form_2026_d212_schema_published": False,
    },
)
