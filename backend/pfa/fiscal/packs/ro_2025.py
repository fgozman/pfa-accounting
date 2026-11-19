"""Romanian income-tax and VAT rule pack for income year 2025.

Sources (retrieved for the research baseline of the specification):
* Fiscal Code arts. 68-69, 118, 148-151, 170-174 -
  https://legislatie.just.ro/Public/DetaliiDocument/228099
* VAT rates change to 21% / 11% from 1 August 2025 -
  https://static.anaf.ro/static/10/Iasi/material_informativ_17-12-2025.pdf
* VAT exemption threshold 395.000 lei from 1 September 2025 -
  https://static.anaf.ro/static/3/Cluj/20250912120026_cj_%20plafon_tva_12sep2025.pdf
* D300 transition instructions (OPANAF 2131/2025) -
  https://static.anaf.ro/static/10/Anaf/legislatie/OPANAF_2131_2025.pdf
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

CODE = "RO-INCOME-2025.1"

PACK = RulePack(
    code=CODE,
    jurisdiction="RO",
    income_year=2025,
    domain="income",
    title="Impozit pe venit, contribuții sociale și TVA — anul 2025",
    version=1,
    valid_from=date(2025, 1, 1),
    valid_to=date(2025, 12, 31),
    publication_date=date(2026, 4, 22),
    source_urls=(
        "https://legislatie.just.ro/Public/DetaliiDocument/228099",
        "https://static.anaf.ro/static/10/Iasi/material_informativ_17-12-2025.pdf",
        "https://static.anaf.ro/static/3/Cluj/20250912120026_cj_%20plafon_tva_12sep2025.pdf",
        "https://static.anaf.ro/static/10/Anaf/legislatie/OPANAF_2131_2025.pdf",
    ),
    formula_version="income-2025.1",
    minimum_wages=(
        MinimumWagePeriod(
            effective_from=date(2025, 1, 1),
            monthly_gross_ron=Decimal("4050.00"),
            legal_reference="HG 1506/2024 (salariul de bază minim brut pe țară garantat în plată)",
        ),
    ),
    cas=ContributionSchedule(
        code="CAS",
        label_ro="Contribuția de asigurări sociale",
        rate=Decimal("25"),
        brackets=(
            # below 12 minimum wages CAS is not mandatory for independent activity
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
        # for 2025 the aggregate yearly CASS base is capped at 60 minimum wages
        max_base_multiple=Decimal("60"),
        election_base_multiple=Decimal("6"),
        legal_reference="Cod fiscal art. 170-174",
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
            effective_from=date(2025, 1, 1),
            effective_to=date(2025, 7, 31),
            standard=Decimal("19"),
            reduced=Decimal("9"),
            second_reduced=Decimal("5"),
            note="Cote în vigoare până la 31 iulie 2025",
        ),
        VatRatePeriod(
            effective_from=date(2025, 8, 1),
            effective_to=None,
            standard=Decimal("21"),
            reduced=Decimal("11"),
            second_reduced=None,
            note="Cota de 5% a fost eliminată; cotele au devenit 21% și 11%",
        ),
    ),
    vat_thresholds=(
        VatRegistrationThresholdPeriod(
            effective_from=date(2025, 1, 1),
            effective_to=date(2025, 8, 31),
            threshold_ron=Decimal("300000"),
            source_url=(
                "https://static.anaf.ro/static/3/Cluj/20250912120026_cj_%20plafon_tva_12sep2025.pdf"
            ),
            note="Prag istoric, reținut pentru livrări anterioare și corecții",
        ),
        VatRegistrationThresholdPeriod(
            effective_from=date(2025, 9, 1),
            effective_to=None,
            threshold_ron=Decimal("395000"),
            source_url=(
                "https://static.anaf.ro/static/3/Cluj/20250912120026_cj_%20plafon_tva_12sep2025.pdf"
            ),
        ),
    ),
    deadline_calendar_version="RO-2025.1",
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
    ),
    regression_fixtures=(
        {
            "id": "2025-cass-cap-60-multiple",
            "note": "Plafonul agregat CASS 2025 este de 60 de salarii minime",
            "expected_max_base_multiple": "60",
        },
        {
            "id": "2025-vat-threshold-395000-from-september",
            "note": "Pragul de scutire TVA devine 395.000 lei de la 1 septembrie 2025",
            "as_of": "2025-09-01",
            "expected_threshold_ron": "395000",
        },
    ),
    status="pending_review",
    reviewed=False,
    notes=(
        "Valorile sunt cercetate, nu aprobate: este necesară verificarea de către un "
        "profesionist fiscal român înainte de aprobarea pentru producție.",
        "Pragurile CAS/CASS se determină în multipli de salariu minim, astfel încât "
        "modificarea salariului minim să nu rescrie istoricul.",
        "CASS se calculează din venitul net al activității independente; plafonul de 60 de "
        "salarii minime se aplică bazei anuale cumulate.",
    ),
    extra={
        "vat_change_effective": "2025-08-01",
        "vat_threshold_change_effective": "2025-09-01",
    },
)
