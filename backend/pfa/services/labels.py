"""Romanian labels for the codes that appear in user-facing messages.

Codes are the storage contract (`intracommunity_operation_type`, `counterparty_*`,
`vat_registration`, …).  A message must name what the user has to complete, never
expose the key — the same rule the frontend follows in `labels.ts`.
"""

from __future__ import annotations

MISSING_INPUT_LABELS_RO: dict[str, str] = {
    # e-Factura / recapitulative classification
    "intracommunity_operation_type": "tipul operațiunii intracomunitare (L/T/A/P/S/R)",
    "supplier_vat_id": "codul de TVA al partenerului",
    "counterparty_vat_id_verification": "verificarea codului VIES al partenerului",
    "counterparty_classification": "partenerul tranzacției (client sau furnizor)",
    "business_use_percent": "ponderea de utilizare în scop de afaceri",
    "expense_business_use": "ponderea de utilizare în scop de afaceri a unei cheltuieli",
    "expense_deductibility": "deductibilitatea unei cheltuieli",
    # classification of a VAT operation (D301/D390): the justification lives on the document
    "classification": "justificarea clasificării operațiunii (taxare inversă)",
    "vat_rate": "cota de TVA de pe document (pentru autotaxare)",
    "vat_transaction_classification": "clasificarea TVA a unor documente",
    "external_services_classification": "clasificarea serviciilor externe",
    "tara": "țara partenerului pentru operațiunea intracomunitară",
    "tip": "tipul operațiunii intracomunitare (L/T/A/P/S/R)",
    "denO": "denumirea partenerului",
    "baza": "baza operațiunii intracomunitare",
    "verified_registration_form": "formularul de înregistrare fiscală verificat",
    # profile / register
    "vat_registration": "înregistrarea în scopuri de TVA",
    "activity_period": "perioada de activitate",
    # income-tax year facts
    "regime": "regimul fiscal",
    "norm_amount": "venitul normat",
    "norm_adjustment_percent": "procentul de ajustare a normei",
    "active_months": "numărul de luni de activitate",
    "cas_election": "opțiunea CAS",
    "cass_election": "opțiunea CASS",
    "prior_losses": "pierderile reportate",
    "salary_income_present": "veniturile din salarii",
    "salary_income_ron": "suma veniturilor din salarii",
    "pension_status": "statutul de pensionar",
    # documents
    "recalculation": "recalcularea declarației",
}


def missing_input_label(key: str) -> str:
    """Readable label for one blocking input; `key:id` suffixes are tolerated."""
    base = key.split(":", 1)[0]
    return MISSING_INPUT_LABELS_RO.get(base, base.replace("_", " "))
