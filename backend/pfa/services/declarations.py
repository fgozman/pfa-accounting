"""Declaration adapters and lifecycle.

Every adapter implements the same contract: applicability, required-input
validation, calculation mapping, official-schema rendering, official validation,
correction strategy, supported filing channels and receipt interpretation.

Lifecycle::

    draft → calculated → validated → approved → exported/queued
          → submitted → accepted / rejected            (submission_unknown)

An upload timeout introduces ``submission_unknown``; an exported file introduces
``awaiting_external_filing``, not ``submitted``.  A successful HTTP response is
never acceptance: a receipt bound to taxpayer, form, period and artifact is.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol

from lxml import etree
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pfa.errors import (
    BlockingInputs,
    CapabilityUnavailable,
    StateTransitionError,
)
from pfa.fiscal.engine import (
    DepreciationItem,
    ExpenseItem,
    RevenueItem,
    TaxpayerFacts,
    calculate_income_tax,
)
from pfa.fiscal.money import ZERO, d, round_cents
from pfa.fiscal.rules import RulePack
from pfa.fiscal.vat import (
    VatProfile,
    VatTransaction,
    build_journals,
)
from pfa.models.base import (
    DocumentKind,
    DocumentStatus,
)
from pfa.models.books import AccountingEvent, Asset, DepreciationEntry, TaxAdjustment
from pfa.models.compliance import (
    Declaration,
    DeclarationSourceGrant,
    FormVersion,
)
from pfa.models.documents import Counterparty, Document, DocumentLine
from pfa.models.money import PaymentAllocation
from pfa.models.tenancy import PfaProfile, TaxpayerYearFact, VatRegistration
from pfa.repo import documents as documents_repo
from pfa.services.obligations import CATALOGUE

ESTIMATE_NOTICE_RO = (
    "Document de lucru estimativ: schema oficială pentru anul de venit nu este "
    "disponibilă sau validată. Nu poate fi folosit ca fișier de depunere."
)


@dataclass(slots=True)
class CalculationOutcome:
    calculated_values: dict
    trace: list[dict]
    unresolved: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    rule_pack_code: str | None = None
    rule_pack_version: int | None = None
    form_schema_version: str | None = None


@dataclass(slots=True)
class RenderedArtifact:
    data: bytes
    artifact_format: str
    is_preview: bool
    schema_version: str | None = None
    label_ro: str = ""


@dataclass(slots=True)
class ReceiptInterpretation:
    accepted: bool
    remote_reference: str | None
    rejection_errors: list[str]
    taxpayer_value: str | None = None
    form_value: str | None = None
    period_value: str | None = None
    artifact_hash: str | None = None


class DeclarationAdapter(Protocol):
    form_code: str

    def required_inputs(self, snapshot: dict) -> list[str]: ...

    def calculate(self, snapshot: dict, pack: RulePack | None) -> CalculationOutcome: ...

    def render(
        self, snapshot: dict, outcome: CalculationOutcome, form_version: FormVersion | None
    ) -> RenderedArtifact: ...

    def correction_strategy(self) -> str: ...

    def filing_channels(self) -> list[str]: ...

    def interpret_receipt(self, payload: dict) -> ReceiptInterpretation: ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256(payload: object) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _xml(root: etree._Element) -> bytes:
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def _attribute(parent: etree._Element, name: str, value: object) -> None:
    parent.set(name, "" if value is None else str(value))


# ---------------------------------------------------------------------------
# D212 — Declarația unică
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class D212Inputs:
    income_year: int
    regime: str
    revenues: list[dict]
    expenses: list[dict]
    depreciation: list[dict]
    adjustments: list[dict]
    facts: dict
    unknown_facts: list[str]
    norm: dict | None
    sources: list[dict]


class D212Adapter:
    form_code = "D212"

    def required_inputs(self, snapshot: dict) -> list[str]:
        missing: list[str] = []
        facts = snapshot.get("facts") or {}
        for key in ("regime", "cas_election", "cass_election"):
            if facts.get(key) in (None, ""):
                missing.append(key)
        if snapshot.get("regime") == "norm":
            if not snapshot.get("norm"):
                missing.append("norm_amount")
            elif snapshot["norm"].get("adjustment_percent") in (None, ""):
                missing.append("norm_adjustment_percent")
        return missing

    def calculate(self, snapshot: dict, pack: RulePack | None) -> CalculationOutcome:
        if pack is None:
            raise CapabilityUnavailable(
                f"Nu există un pachet de reguli pentru anul de venit {snapshot.get('income_year')}."
            )
        facts_raw = snapshot.get("facts") or {}
        unknown = list(snapshot.get("unknown_facts") or [])
        facts = TaxpayerFacts(
            income_year=snapshot["income_year"],
            regime=snapshot.get("regime", "real"),
            norm_amount=_dec((snapshot.get("norm") or {}).get("amount")),
            norm_adjustment_percent=_dec(
                (snapshot.get("norm") or {}).get("adjustment_percent")
            ),
            active_months=(snapshot.get("norm") or {}).get("active_months"),
            cas_elected=_bool(facts_raw.get("cas_election")),
            cass_elected=_bool(facts_raw.get("cass_election")),
            cas_exempt=_bool(facts_raw.get("cas_exempt")),
            cass_exempt=_bool(facts_raw.get("cass_exempt")),
            salary_income_ron=_dec(facts_raw.get("salary_income_ron")),
            pension_status=(
                str(facts_raw["pension_status"]) if facts_raw.get("pension_status") else None
            ),
            other_income_base_ron=_dec(facts_raw.get("other_income_base_ron")),
            prior_losses=_dec(facts_raw.get("prior_losses")),
            foreign_tax_paid_ron=_dec(facts_raw.get("foreign_tax_paid_ron")),
            other_deductions_ron=_dec(facts_raw.get("other_deductions_ron")),
            unknown_facts=tuple(unknown),
        )
        revenues = tuple(
            RevenueItem(
                id=item["id"],
                occurred_on=date.fromisoformat(item["occurred_on"]),
                amount_ron=_dec(item["amount_ron"]) or ZERO,
                description=item.get("description", ""),
            )
            for item in snapshot.get("revenues", [])
        )
        expenses = tuple(
            ExpenseItem(
                id=item["id"],
                occurred_on=date.fromisoformat(item["occurred_on"]),
                amount_ron=_dec(item["amount_ron"]) or ZERO,
                category=item.get("category") or "general",
                deductibility=item.get("deductibility") or "undetermined",
                deductibility_percent=_dec(item.get("deductibility_percent")) or Decimal("100"),
                # no fallback: an unstated share must stay unknown, not become 100%
                business_use_percent=_dec(item.get("business_use_percent")),
                limit_code=item.get("limit_code"),
                source_ref=item.get("source_ref"),
            )
            for item in snapshot.get("expenses", [])
        )
        depreciation = tuple(
            DepreciationItem(
                id=item["id"],
                asset_id=item["asset_id"],
                amount_ron=_dec(item["amount_ron"]) or ZERO,
                deductible_amount_ron=_dec(item["deductible_amount_ron"]) or ZERO,
                period_month=item.get("period_month"),
            )
            for item in snapshot.get("depreciation", [])
        )
        result = calculate_income_tax(
            pack, facts=facts, revenues=revenues, expenses=expenses, depreciation=depreciation
        )
        adjustments_total = sum(
            (_dec(item["amount"]) or ZERO) for item in snapshot.get("adjustments", [])
        )
        values = result.as_dict()
        values["adjustments_total"] = str(round_cents(adjustments_total))
        values["chapters"] = {
            "activitati_independente": {
                "venit_brut": values["gross_revenue"],
                "cheltuieli_deductibile": values["eligible_expenses"],
                "venit_net": values["net_income"],
                "cas": values["cas_amount"],
                "cass": values["cass_amount"],
                "impozit": values["income_tax"],
            }
        }
        return CalculationOutcome(
            calculated_values=values,
            trace=list(result.trace),
            unresolved=list(result.unresolved),
            warnings=list(result.warnings),
            rule_pack_code=pack.code,
            rule_pack_version=pack.version,
            form_schema_version=None,
        )

    def render(
        self, snapshot: dict, outcome: CalculationOutcome, form_version: FormVersion | None
    ) -> RenderedArtifact:
        supported = bool(form_version and form_version.supported and form_version.published)
        root = etree.Element("DeclaratieUnica")
        _attribute(root, "an", snapshot["income_year"])
        _attribute(root, "versiuneSchema", form_version.schema_version if form_version else "workpaper-1")
        _attribute(root, "preview", "true" if not supported else "false")
        if not supported:
            notice = etree.SubElement(root, "Nota")
            notice.text = ESTIMATE_NOTICE_RO
        contribuabil = etree.SubElement(root, "Contribuabil")
        for key, value in (snapshot.get("identity") or {}).items():
            _attribute(contribuabil, key, value)
        cap = etree.SubElement(root, "CapitolII_ActivitatIndependente")
        values = outcome.calculated_values
        for field_name in (
            "gross_revenue",
            "eligible_expenses",
            "net_income",
            "cas_base",
            "cas_amount",
            "cass_base",
            "cass_amount",
            "taxable_income",
            "income_tax",
            "total_due",
        ):
            _attribute(cap, field_name, values.get(field_name))
        trace_el = etree.SubElement(root, "Justificari")
        for line in outcome.trace:
            item = etree.SubElement(trace_el, "Linie")
            _attribute(item, "cod", line.get("code"))
            _attribute(item, "valoare", line.get("value"))
            _attribute(item, "formula", line.get("formula"))
            _attribute(item, "temei", line.get("legal_reference"))
        if outcome.unresolved:
            missing = etree.SubElement(root, "InformatiiLipsa")
            for key in outcome.unresolved:
                etree.SubElement(missing, "Cheie").text = key
        return RenderedArtifact(
            data=_xml(root),
            artifact_format="xml",
            is_preview=not supported,
            schema_version=form_version.schema_version if form_version else None,
            label_ro=(
                "Fișier de lucru estimativ (nu este pentru depunere)"
                if not supported
                else "Declarație unică — fișier generat"
            ),
        )

    def correction_strategy(self) -> str:
        return "initial_and_corrective_per_chapter"

    def filing_channels(self) -> list[str]:
        return ["external_upload", "assisted"]

    def interpret_receipt(self, payload: dict) -> ReceiptInterpretation:
        return _generic_receipt(payload)


# ---------------------------------------------------------------------------
# D300 — VAT return
# ---------------------------------------------------------------------------


class D300Adapter:
    form_code = "D300"

    def required_inputs(self, snapshot: dict) -> list[str]:
        missing: list[str] = []
        if not snapshot.get("vat_registration"):
            missing.append("vat_registration")
        if snapshot.get("unclassified_documents"):
            missing.append("vat_transaction_classification")
        return missing

    def calculate(self, snapshot: dict, pack: RulePack | None) -> CalculationOutcome:
        if pack is None:
            raise CapabilityUnavailable("Nu există un pachet de reguli TVA pentru această perioadă.")
        profile = VatProfile(
            registration_kind=snapshot.get("vat_registration", {}).get("kind", "none"),
            has_deduction_right=snapshot.get("vat_registration", {}).get("has_deduction_right", True),
            cash_accounting=snapshot.get("vat_registration", {}).get("cash_accounting", False),
        )
        transactions = tuple(
            VatTransaction(
                id=item["id"],
                occurred_on=date.fromisoformat(item["occurred_on"]),
                tax_point=date.fromisoformat(item["tax_point"]),
                direction=item["direction"],
                nature=item["nature"],
                category=item.get("category", "standard"),
                net_amount_ron=_dec(item["net_amount_ron"]) or ZERO,
                counterparty_country=item.get("counterparty_country", "RO"),
                counterparty_is_business=item.get("counterparty_is_business", True),
                counterparty_vat_id=item.get("counterparty_vat_id"),
                counterparty_vat_id_verified=item.get("counterparty_vat_id_verified", False),
                place_of_supply=item.get("place_of_supply", "RO"),
                # no fallback on either: an unstated share must stay unknown, not become 100%
                deductibility_percent=_dec(item.get("deductibility_percent")),
                business_use_percent=_dec(item.get("business_use_percent")),
                vat_amount_ron=_dec(item.get("vat_amount_ron")),
                recorded_rate=_dec(item.get("recorded_rate")),
                exempt=item.get("exempt", False),
                note=item.get("note", ""),
            )
            for item in snapshot.get("transactions", [])
        )
        journals = build_journals(
            pack,
            transactions,
            profile,
            previous_carryforward=_dec(snapshot.get("previous_carryforward")) or ZERO,
            refund_requested=_dec(snapshot.get("refund_requested")) or ZERO,
            request_refund=bool(snapshot.get("request_refund")),
        )
        missing = sorted(
            {key for assessment in journals.assessments for key in assessment.missing_inputs}
        )
        values = journals.as_dict()
        values["period"] = {
            "start": snapshot["period_start"],
            "end": snapshot["period_end"],
        }
        return CalculationOutcome(
            calculated_values=values,
            trace=[
                {
                    "code": "d300_totals",
                    "label_ro": "Totaluri decont TVA",
                    "formula": "TVA colectată - TVA deductibilă - sold reportat",
                    "value": values["balance_payable"],
                    "legal_reference": "Cod fiscal art. 323; OPANAF 2131/2025",
                }
            ],
            unresolved=missing,
            warnings=[],
            rule_pack_code=pack.code,
            rule_pack_version=pack.version,
        )

    def render(
        self, snapshot: dict, outcome: CalculationOutcome, form_version: FormVersion | None
    ) -> RenderedArtifact:
        supported = bool(form_version and form_version.supported and form_version.published)
        root = etree.Element("DecontTVA")
        _attribute(root, "preview", "true" if not supported else "false")
        if not supported:
            etree.SubElement(root, "Nota").text = ESTIMATE_NOTICE_RO
        period = etree.SubElement(root, "Perioada")
        _attribute(period, "start", snapshot["period_start"])
        _attribute(period, "end", snapshot["period_end"])
        for field_name, value in outcome.calculated_values.items():
            if field_name in {"sales", "purchases", "assessments", "period"}:
                continue
            _attribute(root, field_name, value)
        sales = etree.SubElement(root, "JurnalVanzari")
        for line in outcome.calculated_values.get("sales", []):
            item = etree.SubElement(sales, "Linie")
            for key, value in line.items():
                _attribute(item, key, value)
        purchases = etree.SubElement(root, "JurnalCumparari")
        for line in outcome.calculated_values.get("purchases", []):
            item = etree.SubElement(purchases, "Linie")
            for key, value in line.items():
                _attribute(item, key, value)
        return RenderedArtifact(
            data=_xml(root),
            artifact_format="xml",
            is_preview=not supported,
            schema_version=form_version.schema_version if form_version else None,
            label_ro="Decont TVA generat" if supported else "Decont TVA — document de lucru",
        )

    def correction_strategy(self) -> str:
        return "regularization_per_opanaf_2131_2025"

    def filing_channels(self) -> list[str]:
        return ["external_upload", "assisted"]

    def interpret_receipt(self, payload: dict) -> ReceiptInterpretation:
        return _generic_receipt(payload)


# ---------------------------------------------------------------------------
# D301 / D390 / D394 — VAT special, VIES and domestic informative returns
# ---------------------------------------------------------------------------


class D301Adapter:
    form_code = "D301"

    def required_inputs(self, snapshot: dict) -> list[str]:
        missing = []
        for item in snapshot.get("transactions", []):
            if not item.get("classification_evidence"):
                missing.append(f"classification:{item['id']}")
            # a reverse charge is reported at the rate the beneficiary self-assesses
            if item.get("self_assessed_rate") is None and _dec(
                item.get("reverse_charge_vat")
            ) == ZERO:
                missing.append(f"vat_rate:{item['id']}")
        return missing

    def calculate(self, snapshot: dict, pack: RulePack | None) -> CalculationOutcome:
        rows = list(snapshot.get("transactions", []))
        total_base = sum((_dec(item.get("net_amount_ron")) or ZERO) for item in rows)
        total_vat = sum((_dec(item.get("reverse_charge_vat")) or ZERO) for item in rows)
        return CalculationOutcome(
            calculated_values={
                "rows": rows,
                "total_base": str(round_cents(total_base)),
                "total_vat": str(round_cents(total_vat)),
            },
            trace=[
                {
                    "code": "d301_total",
                    "label_ro": "Total decont special",
                    "formula": "sumă(baze operațiuni cu taxare inversă)",
                    "value": str(round_cents(total_vat)),
                    "legal_reference": CATALOGUE["D301"].legal_reference,
                }
            ],
            # one definition per form of what it still needs, used by both the
            # calculation and the validation gate
            unresolved=self.required_inputs(snapshot),
            rule_pack_code=pack.code if pack else None,
            rule_pack_version=pack.version if pack else None,
        )

    def render(
        self, snapshot: dict, outcome: CalculationOutcome, form_version: FormVersion | None
    ) -> RenderedArtifact:
        root = etree.Element("DecontSpecialTVA")
        _attribute(root, "preview", "true")
        etree.SubElement(root, "Nota").text = (
            "Formular generat pe baza clasificării operațiunilor; validarea oficială "
            "necesită schema publicată de ANAF pentru perioada respectivă."
        )
        for item in outcome.calculated_values.get("rows", []):
            row = etree.SubElement(root, "Operatiune")
            for key, value in item.items():
                if isinstance(value, (str, int, float, bool)) or value is None:
                    _attribute(row, key, value)
        return RenderedArtifact(
            data=_xml(root), artifact_format="xml", is_preview=True, label_ro="D301 — document de lucru"
        )

    def correction_strategy(self) -> str:
        return "special_return_correction"

    def filing_channels(self) -> list[str]:
        return ["external_upload", "assisted"]

    def interpret_receipt(self, payload: dict) -> ReceiptInterpretation:
        return _generic_receipt(payload)


#: EU member states in the official D390 nomenclator, plus XI for Northern Ireland.
#: GB is deliberately absent: it has not been a member state since 2021.
D390_COUNTRIES = frozenset(
    {
        "AT",
        "BE",
        "BG",
        "CZ",
        "CY",
        "DK",
        "EE",
        "DE",
        "EL",
        "FI",
        "FR",
        "IE",
        "IT",
        "LV",
        "LU",
        "LT",
        "MT",
        "XI",
        "NL",
        "PL",
        "PT",
        "SI",
        "SK",
        "ES",
        "SE",
        "HU",
        "HR",
    }
)
D390_TYPES = ("L", "T", "A", "P", "S", "R")


class D390Adapter(D301Adapter):
    """Declarația recapitulativă (VIES), official schema v3.

    Only classified intra-community operations are reported: the country comes from
    the counterparty and the L/T/A/P/S/R type is stored on the document.  Nothing is
    inferred from amounts — a missing fact blocks validation instead.
    """

    form_code = "D390"

    def required_inputs(self, snapshot: dict) -> list[str]:
        return list(snapshot.get("declarant_missing", [])) + list(
            snapshot.get("operations_missing", [])
        )

    def calculate(self, snapshot: dict, pack: RulePack | None) -> CalculationOutcome:
        operations = list(snapshot.get("operations", []))
        bases = {
            kind: sum(operation["baza"] for operation in operations if operation["tip"] == kind)
            for kind in D390_TYPES
        }
        nr_operators = len(operations)
        total_baza = sum(bases.values())
        values = {
            "operations": operations,
            "nr_pag": 1,
            "nrOPI": nr_operators,
            **{f"baza{kind}": bases[kind] for kind in D390_TYPES},
            "total_baza": total_baza,
            # control sum per OPANAF 705/2020: nrOPI + Σ baze
            "totalPlata_A": nr_operators + total_baza,
        }
        return CalculationOutcome(
            calculated_values=values,
            trace=[
                {
                    "code": "d390_total_baza",
                    "label_ro": "Total bază impozabilă intracomunitară",
                    "formula": "Σ operatie(baza)",
                    "value": str(total_baza),
                    "legal_reference": CATALOGUE["D390"].legal_reference,
                }
            ],
            unresolved=self.required_inputs(snapshot),
            rule_pack_code=pack.code if pack else None,
            rule_pack_version=pack.version if pack else None,
        )

    def render(
        self, snapshot: dict, outcome: CalculationOutcome, form_version: FormVersion | None
    ) -> RenderedArtifact:
        supported = bool(form_version and form_version.supported and form_version.published)
        values = outcome.calculated_values
        # A version calculated before the official mapping existed has the old shape;
        # exporting it must ask for a recalculation, not fail with a 500 (which the
        # browser would only show as an opaque CORS error).
        required = {
            "operations",
            "nr_pag",
            "nrOPI",
            "bazaL",
            "bazaT",
            "bazaA",
            "bazaP",
            "bazaS",
            "bazaR",
            "total_baza",
            "totalPlata_A",
        }
        if not required <= set(values):
            raise StateTransitionError(
                "Declarația a fost calculată cu o versiune veche a formularului D390. "
                "Recalculați declarația (Verifică) înainte de generare."
            )
        declarant = snapshot.get("declarant") or {}
        period_end = date.fromisoformat(str(snapshot["period_end"]))
        namespace = "mfp:anaf:dgti:d390:declaratie:v3"
        root = etree.Element(f"{{{namespace}}}declaratie390", nsmap={None: namespace})
        _attribute(root, "luna", period_end.month)
        _attribute(root, "an", period_end.year)
        _attribute(root, "d_rec", snapshot.get("d_rec", 0))
        for key in ("nume_declar", "prenume_declar", "functie_declar", "cui", "den", "adresa"):
            _attribute(root, key, declarant.get(key, ""))
        if declarant.get("telefon"):
            _attribute(root, "telefon", declarant["telefon"])
        _attribute(root, "totalPlata_A", values["totalPlata_A"])

        rezumat = etree.SubElement(root, f"{{{namespace}}}rezumat")
        for key in (
            "nr_pag",
            "nrOPI",
            "bazaL",
            "bazaT",
            "bazaA",
            "bazaP",
            "bazaS",
            "bazaR",
            "total_baza",
        ):
            _attribute(rezumat, key, values[key])

        for operation in values["operations"]:
            row = etree.SubElement(root, f"{{{namespace}}}operatie")
            _attribute(row, "tip", operation["tip"])
            _attribute(row, "tara", operation["tara"])
            if operation.get("codO"):
                _attribute(row, "codO", operation["codO"])
            _attribute(row, "denO", operation["denO"])
            _attribute(row, "baza", operation["baza"])

        return RenderedArtifact(
            data=_xml(root),
            artifact_format="xml",
            is_preview=not supported,
            schema_version=form_version.schema_version if form_version else None,
            label_ro=(
                "Declarația recapitulativă D390 (operațiuni intracomunitare)"
                if supported
                else "D390 — document de lucru (operațiuni intracomunitare)"
            ),
        )

    def correction_strategy(self) -> str:
        return "recapitulative_correction"


class D394Adapter(D301Adapter):
    form_code = "D394"

    def required_inputs(self, snapshot: dict) -> list[str]:
        """D394 reports operations per partner, so the partner is what it needs."""
        missing: list[str] = []
        for item in snapshot.get("transactions", []):
            partner = item.get("partner") or {}
            if not (partner.get("name") or item.get("counterparty_id")):
                missing.append(f"counterparty_classification:{item['id']}")
        return missing

    def render(
        self, snapshot: dict, outcome: CalculationOutcome, form_version: FormVersion | None
    ) -> RenderedArtifact:
        root = etree.Element("DeclaratieInformative")
        _attribute(root, "preview", "true")
        etree.SubElement(root, "Nota").text = (
            "Clasificarea contrapărților și categoriile de documente determină raportarea."
        )
        for item in outcome.calculated_values.get("rows", []):
            row = etree.SubElement(root, "Operatiune")
            for key, value in item.items():
                if isinstance(value, (str, int, float, bool)) or value is None:
                    _attribute(row, key, value)
        return RenderedArtifact(
            data=_xml(root), artifact_format="xml", is_preview=True, label_ro="D394 — document de lucru"
        )

    def correction_strategy(self) -> str:
        return "informative_correction"


ADAPTERS: dict[str, DeclarationAdapter] = {
    "D212": D212Adapter(),
    "D300": D300Adapter(),
    "D301": D301Adapter(),
    "D390": D390Adapter(),
    "D394": D394Adapter(),
}


def get_adapter(form_code: str) -> DeclarationAdapter:
    adapter = ADAPTERS.get(form_code)
    if adapter is None:
        raise CapabilityUnavailable(
            f"Formularul {form_code} nu are un adaptor de generare implementat. "
            "Fluxul asistat rămâne disponibil."
        )
    return adapter


# ---------------------------------------------------------------------------
# Snapshot builders
# ---------------------------------------------------------------------------


def _dec(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    return d(value)


def _bool(value: object) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"true", "1", "yes", "da"}


async def build_d212_snapshot(
    session: AsyncSession, *, declaration: Declaration
) -> dict:
    year = declaration.income_year
    year_start = date(year, 1, 1)
    year_end = date(year, 12, 31)

    events = (
        await session.execute(
            select(AccountingEvent).where(
                AccountingEvent.workspace_id == declaration.workspace_id,
                AccountingEvent.event_date >= year_start,
                AccountingEvent.event_date <= year_end,
                AccountingEvent.event_kind == "revenue",
            )
        )
    ).scalars().all()
    revenues = [
        {
            "id": str(event.payment_id or event.id),
            "occurred_on": event.event_date.isoformat(),
            "amount_ron": str(event.taxable_amount_ron),
            "description": event.description or "Încasare",
        }
        for event in events
        if event.taxable_amount_ron and event.taxable_amount_ron != ZERO
    ]

    assets = (
        await session.execute(
            select(Asset).where(Asset.workspace_id == declaration.workspace_id)
        )
    ).scalars().all()
    capitalised_documents = {asset.document_id for asset in assets if asset.document_id}

    documents = (
        await session.execute(
            select(Document).where(
                Document.workspace_id == declaration.workspace_id,
                Document.kind == DocumentKind.PURCHASE_INVOICE,
                Document.status == DocumentStatus.FINALIZED,
                Document.issue_date >= year_start,
                Document.issue_date <= year_end,
            )
        )
    ).scalars().all()
    expenses = []
    unpaid_documents: list[str] = []
    for document in documents:
        if document.id in capitalised_documents:
            # capitalised assets are deducted through depreciation, never twice
            continue
        # A document is an expense input only when a settlement is allocated to it:
        # an uploaded invoice is not evidence of payment.
        settled = await session.scalar(
            select(PaymentAllocation.id).where(
                PaymentAllocation.workspace_id == declaration.workspace_id,
                PaymentAllocation.document_id == document.id,
                PaymentAllocation.allocation_kind.in_(("settlement", "advance")),
            )
        )
        if settled is None:
            unpaid_documents.append(str(document.id))
            continue
        net = round_cents(document.total_amount - document.total_vat)
        expenses.append(
            {
                "id": str(document.id),
                "occurred_on": (document.issue_date or year_end).isoformat(),
                "amount_ron": str(net),
                "category": document.expense_category or "general",
                "deductibility": str(document.deductibility),
                "deductibility_percent": "100",
                "business_use_percent": (
                    str(document.business_use_percent)
                    if document.business_use_percent is not None
                    else None
                ),
                "source_ref": str(document.id),
            }
        )

    depreciation_entries = (
        await session.execute(
            select(DepreciationEntry).where(
                DepreciationEntry.workspace_id == declaration.workspace_id,
                DepreciationEntry.income_year == year,
            )
        )
    ).scalars().all()
    depreciation = [
        {
            "id": str(entry.id),
            "asset_id": str(entry.asset_id),
            "amount_ron": str(entry.amount),
            "deductible_amount_ron": str(entry.deductible_amount),
            "period_month": entry.period_month,
        }
        for entry in depreciation_entries
    ]

    adjustments = (
        await session.execute(
            select(TaxAdjustment).where(
                TaxAdjustment.workspace_id == declaration.workspace_id,
                TaxAdjustment.income_year == year,
            )
        )
    ).scalars().all()
    adjustment_rows = [
        {
            "id": str(row.id),
            "kind": row.adjustment_kind,
            "amount": str(row.amount),
            "direction": row.direction,
            "legal_reference": row.legal_reference,
        }
        for row in adjustments
    ]

    facts_rows = (
        await session.execute(
            select(TaxpayerYearFact).where(
                TaxpayerYearFact.workspace_id == declaration.workspace_id,
                TaxpayerYearFact.income_year == year,
            )
        )
    ).scalars().all()
    facts: dict = {}
    unknown: list[str] = []
    answered_keys: set[str] = set()
    for row in facts_rows:
        if row.answered_at is None:
            unknown.append(row.fact_key)
            continue
        answered_keys.add(row.fact_key)
        facts[row.fact_key] = (
            row.value_text
            if row.value_text is not None
            else row.value_decimal
            if row.value_decimal is not None
            else row.value_boolean
        )

    # A required fact that has no answered row at all is still *unknown*: it must
    # never silently become zero or "no".
    from pfa.services.obligations import REQUIRED_FACTS

    for key in REQUIRED_FACTS.get(declaration.form_code, ()):
        if key not in answered_keys and key not in unknown:
            unknown.append(key)

    # The salary boolean gates the amount: "no salary income" is an *answered* zero,
    # while "yes" without an amount stays unknown instead of silently becoming zero.
    salary_present = facts.get("salary_income_present")
    if salary_present is False:
        facts["salary_income_ron"] = Decimal("0")
    elif salary_present is True and facts.get("salary_income_ron") is None:
        unknown.append("salary_income_ron")

    regime = "real"
    norm_payload: dict | None = None
    if facts.get("regime") == "norm":
        regime = "norm"
        norm_payload = {
            "amount": facts.get("norm_amount"),
            "adjustment_percent": facts.get("norm_adjustment_percent"),
            "active_months": facts.get("active_months") or 12,
        }
    elif declaration.form_code == "D212" and facts.get("regime") is None:
        unknown.append("regime")

    grants = (
        await session.execute(
            select(DeclarationSourceGrant).where(
                DeclarationSourceGrant.declaration_id == declaration.id,
                DeclarationSourceGrant.revoked_at.is_(None),
            )
        )
    ).scalars().all()
    sources = [
        {
            "source_workspace_id": str(grant.source_workspace_id) if grant.source_workspace_id else None,
            "taxpayer_id": str(grant.taxpayer_id) if grant.taxpayer_id else None,
            "sharing_notice_version": grant.sharing_notice_version,
        }
        for grant in grants
    ]

    return {
        "income_year": year,
        "regime": regime,
        "revenues": revenues,
        "expenses": expenses,
        "depreciation": depreciation,
        "adjustments": adjustment_rows,
        "facts": facts,
        "unknown_facts": sorted(set(unknown)),
        "norm": norm_payload,
        "identity": declaration.taxpayer_identity or {},
        "unpaid_documents": unpaid_documents,
        "sources": sources,
    }


def _vat_nature(document: Document) -> tuple[str, str, Decimal | None, list[str]]:
    """Derive the VAT nature and the recorded rate from stored evidence.

    Returns ``(nature, category, recorded_rate, missing)``.  ``recorded_rate`` is the
    rate implied by the document's own amounts, so the declaration reports the VAT the
    invoice actually carries instead of recomputing it from the rule pack.
    """
    missing: list[str] = []
    if document.vat_treatment in {"reverse_charge"}:
        if not document.counterparty_id and not document.supplier_name_raw:
            missing.append("counterparty_classification")
        # the invoice carries no VAT: the recipient self-assesses at the legal rate
        return "domestic_reverse_charge", "standard", None, missing
    if document.vat_treatment in {"exempt"}:
        return "domestic_supply", "exempt", Decimal("0"), missing
    if document.vat_treatment in {"out_of_scope"}:
        return "non_eu_services_supply", "out_of_scope", Decimal("0"), missing
    category = "standard"
    recorded_rate: Decimal | None = None
    base = document.total_amount - document.total_vat
    if base > ZERO:
        recorded_rate = (document.total_vat / base * Decimal("100")).quantize(Decimal("0.01"))
        if recorded_rate <= Decimal("6"):
            category = "second_reduced"
        elif recorded_rate <= Decimal("13"):
            category = "reduced"
    return "domestic_supply", category, recorded_rate, missing


async def build_d300_snapshot(session: AsyncSession, *, declaration: Declaration) -> dict:
    registrations = (
        await session.execute(
            select(VatRegistration).where(
                VatRegistration.workspace_id == declaration.workspace_id,
                VatRegistration.effective_from <= declaration.period_end,
            )
        )
    ).scalars().all()
    active = [
        registration
        for registration in registrations
        if registration.effective_to is None
        or registration.effective_to.date() >= declaration.period_start
    ]
    registration = active[-1] if active else None
    if registration is None:
        raise BlockingInputs(
            ["vat_registration"],
            "Nu există o înregistrare TVA efectivă pentru perioada declarației.",
        )

    documents = (
        await session.execute(
            select(Document).where(
                Document.workspace_id == declaration.workspace_id,
                Document.status == DocumentStatus.FINALIZED,
                Document.issue_date >= declaration.period_start,
                Document.issue_date <= declaration.period_end,
            )
        )
    ).scalars().all()

    transactions: list[dict] = []
    unclassified: list[str] = []
    for document in documents:
        if document.kind == DocumentKind.CREDIT_NOTE:
            # A storno reduces the VAT of the operation it corrects: the direction is the
            # corrected document's, and the amounts are negative so the collected or
            # deducted VAT falls.  The note itself is never demanded as classification —
            # its link to the corrected document is the evidence.
            if document.corrects_document_id is None:
                continue
            corrected = await documents_repo.get_document(
                session,
                workspace_id=declaration.workspace_id,
                document_id=document.corrects_document_id,
            )
            if corrected is None:
                continue
            nature, category, recorded_rate, _missing = _vat_nature(document)
            direction = "sale" if corrected.kind == DocumentKind.SALES_INVOICE else "purchase"
            sign = -1
        elif document.kind == DocumentKind.SALES_INVOICE:
            nature, category, recorded_rate, missing = _vat_nature(document)
            if missing:
                unclassified.append(str(document.id))
            direction = "sale"
            sign = 1
        elif document.kind == DocumentKind.PURCHASE_INVOICE:
            nature, category, recorded_rate, missing = _vat_nature(document)
            if missing:
                unclassified.append(str(document.id))
            direction = "purchase"
            sign = 1
        else:
            continue
        net = sign * (document.total_amount - document.total_vat)
        vat = sign * document.total_vat
        transactions.append(
            {
                "id": str(document.id),
                "occurred_on": (document.issue_date or declaration.period_start).isoformat(),
                "tax_point": (
                    document.supply_date or document.issue_date or declaration.period_start
                ).isoformat(),
                "direction": direction,
                "nature": nature,
                "category": category,
                "net_amount_ron": str(round_cents(net)),
                # the declaration reports the VAT recorded on the document
                "vat_amount_ron": str(round_cents(vat)),
                "recorded_rate": None if recorded_rate is None else str(recorded_rate),
                "counterparty_country": "RO",
                "counterparty_is_business": True,
                "counterparty_vat_id": None,
                "counterparty_vat_id_verified": False,
                "place_of_supply": "RO",
                "deductibility_percent": (
                    str(document.business_use_percent)
                    if document.business_use_percent is not None
                    else None
                ),
                "business_use_percent": (
                    str(document.business_use_percent)
                    if document.business_use_percent is not None
                    else None
                ),
                "exempt": str(document.vat_treatment) == "exempt",
                "note": document.deductibility_justification or "",
            }
        )

    return {
        "period_start": declaration.period_start.isoformat(),
        "period_end": declaration.period_end.isoformat(),
        "vat_registration": {
            "kind": registration.kind,
            "cash_accounting": registration.cash_accounting,
            "has_deduction_right": registration.kind == "article_316",
        },
        "transactions": transactions,
        "unclassified_documents": unclassified,
        "previous_carryforward": "0",
        "refund_requested": "0",
        "request_refund": False,
        "identity": declaration.taxpayer_identity or {},
    }


async def build_special_vat_snapshot(session: AsyncSession, *, declaration: Declaration) -> dict:
    documents = (
        await session.execute(
            select(Document).where(
                Document.workspace_id == declaration.workspace_id,
                Document.status == DocumentStatus.FINALIZED,
                Document.issue_date >= declaration.period_start,
                Document.issue_date <= declaration.period_end,
            )
        )
    ).scalars().all()
    # For a reverse charge the supplier charges no VAT: the special return reports the VAT
    # the beneficiary self-assesses, at the rate recorded on the document.  Without that
    # rate the return would silently report zero.
    recorded_rates: dict = {}
    if documents:
        recorded_rates = dict(
            (
                await session.execute(
                    select(DocumentLine.document_id, func.max(DocumentLine.vat_rate))
                    .where(DocumentLine.document_id.in_([document.id for document in documents]))
                    .group_by(DocumentLine.document_id)
                )
            ).all()
        )

    rows: list[dict] = []
    for document in documents:
        if declaration.form_code == "D390" and document.kind != DocumentKind.PURCHASE_INVOICE:
            continue
        if declaration.form_code == "D301" and not (
            document.kind == DocumentKind.PURCHASE_INVOICE
            and str(document.vat_treatment) == "reverse_charge"
        ):
            # The special return covers the operations where the beneficiary owes the VAT.
            # A domestic sale is not one of them: including it made the return demand a
            # reverse-charge justification from an ordinary sale and added its collected
            # VAT to the special return's total.
            continue
        net = document.total_amount - document.total_vat
        recorded_rate = recorded_rates.get(document.id)
        reverse_charge = str(document.vat_treatment) == "reverse_charge"
        self_assessed = (
            round_cents(net * recorded_rate / Decimal("100"))
            if reverse_charge and recorded_rate
            else round_cents(document.total_vat)
        )
        rows.append(
            {
                "id": str(document.id),
                "kind": str(document.kind),
                "issue_date": (document.issue_date or declaration.period_start).isoformat(),
                "net_amount_ron": str(round_cents(net)),
                "reverse_charge_vat": str(self_assessed),
                "invoice_vat": str(round_cents(document.total_vat)),
                "self_assessed_rate": str(recorded_rate) if recorded_rate else None,
                "vat_treatment": str(document.vat_treatment),
                "classification_evidence": document.reverse_charge_justification
                or document.business_purpose,
                # the partner identity D394 reports per operation: name, fiscal code and
                # country as recorded on the document, never inferred
                "partner": {
                    "name": document.supplier_name_raw,
                    "fiscal_id": document.supplier_vat_id,
                    "country": document.supplier_country,
                    "kind": str(document.partner_kind) if document.partner_kind else None,
                },
                "counterparty_id": (
                    str(document.counterparty_id) if document.counterparty_id else None
                ),
            }
        )
    return {
        "period_start": declaration.period_start.isoformat(),
        "period_end": declaration.period_end.isoformat(),
        "transactions": rows,
        "identity": declaration.taxpayer_identity or {},
        **(
            await _d390_classification(
                session, declaration=declaration, documents=list(documents)
            )
            if declaration.form_code == "D390"
            else {}
        ),
    }


async def _d390_classification(
    session: AsyncSession, *, declaration: Declaration, documents: list[Document]
) -> dict:
    """Official D390 header and per-operation classification.

    The L/T/A/P/S/R type and the counterparty's country are facts the document must
    carry; they are never inferred from amounts.  Anything missing is reported and
    blocks validation rather than producing a plausible but wrong declaration.
    """
    profile = (
        await session.execute(
            select(PfaProfile)
            .where(
                PfaProfile.workspace_id == declaration.workspace_id,
                PfaProfile.active.is_(True),
            )
            .order_by(PfaProfile.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    identity = dict(declaration.taxpayer_identity or {})

    declarant_missing: list[str] = []
    legal_name = (profile.legal_name if profile else "") or ""
    name_parts = legal_name.split()
    # A Romanian PFA's legal name is the person's name, written "Nume Prenume"; the
    # first token is the family name.  An explicit identity always wins.
    nume = str(identity.get("nume_declar") or (name_parts[0] if name_parts else "")).strip()
    prenume = str(
        identity.get("prenume_declar") or (" ".join(name_parts[1:]) if len(name_parts) > 1 else "")
    ).strip()
    functie = str(identity.get("functie_declar") or "Titular").strip()
    den = str(identity.get("den") or legal_name).strip()
    adresa = str(
        identity.get("adresa")
        or (profile.registered_address if profile else "")
        or ", ".join(
            part
            for part in (
                profile.locality if profile else None,
                profile.county if profile else None,
            )
            if part
        )
        or ""
    ).strip()
    cui = re.sub(r"\D", "", str(identity.get("cui") or (profile.cui if profile else "") or ""))
    for key, value in (
        ("nume_declar", nume),
        ("prenume_declar", prenume),
        ("functie_declar", functie),
        ("den", den),
        ("adresa", adresa),
    ):
        if not value:
            declarant_missing.append(key)
    if not re.fullmatch(r"[1-9]\d{1,9}", cui):
        declarant_missing.append("cui")

    counterparty_ids = {d.counterparty_id for d in documents if d.counterparty_id}
    counterparties: dict = {}
    if counterparty_ids:
        rows = (
            await session.execute(
                select(Counterparty).where(Counterparty.id.in_(counterparty_ids))
            )
        ).scalars().all()
        counterparties = {row.id: row for row in rows}

    operations: list[dict] = []
    operations_missing: list[str] = []
    for document in documents:
        if document.kind != DocumentKind.PURCHASE_INVOICE:
            continue
        counterparty = counterparties.get(document.counterparty_id)
        country = (
            (counterparty.country_code if counterparty else "")
            or document.supplier_country
            or ""
        ).upper()
        tip = (document.intracommunity_operation_type or "").upper()
        if country == "RO" or (country and country not in D390_COUNTRIES):
            continue  # domestic or extra-community: not a D390 operation
        if not country:
            if tip:
                operations_missing.append(f"tara:{document.id}")
            continue
        if tip not in D390_TYPES:
            operations_missing.append(f"tip:{document.id}")
            continue
        denO = str(
            (counterparty.name if counterparty else "") or document.supplier_name_raw or ""
        ).strip()
        if not denO:
            operations_missing.append(f"denO:{document.id}")
            continue
        baza = int(
            (document.total_amount - document.total_vat).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
        if baza == 0:
            operations_missing.append(f"baza:{document.id}")
            continue
        codO = re.sub(
            r"^[A-Za-z]{2}",
            "",
            str(
                (counterparty.vat_identifier if counterparty else "")
                or document.supplier_vat_id
                or ""
            ),
        ).strip()
        operations.append(
            {"tip": tip, "tara": country, "codO": codO[:12], "denO": denO[:200], "baza": baza}
        )

    return {
        "d_rec": 1 if declaration.correction_of_declaration_id else 0,
        "declarant": {
            "nume_declar": nume,
            "prenume_declar": prenume,
            "functie_declar": functie,
            "cui": cui,
            "den": den,
            "adresa": adresa,
            "telefon": (profile.phone if profile else None) or "",
        },
        "declarant_missing": declarant_missing,
        "operations": operations,
        "operations_missing": operations_missing,
    }


SNAPSHOT_BUILDERS = {
    "D212": build_d212_snapshot,
    "D300": build_d300_snapshot,
    "D301": build_special_vat_snapshot,
    "D390": build_special_vat_snapshot,
    "D394": build_special_vat_snapshot,
}


def _generic_receipt(payload: dict) -> ReceiptInterpretation:
    state = str(payload.get("stare") or payload.get("status") or "").lower()
    accepted = state in {"ok", "accepted", "acceptata", "acceptată", "success", "processed"}
    errors: list[str] = []
    raw_errors = payload.get("erori") or payload.get("errors") or []
    if isinstance(raw_errors, list):
        for item in raw_errors:
            if isinstance(item, dict):
                errors.append(str(item.get("message") or item.get("mesaj") or item))
            else:
                errors.append(str(item))
    elif raw_errors:
        errors.append(str(raw_errors))
    return ReceiptInterpretation(
        accepted=accepted,
        remote_reference=payload.get("id_descarcare")
        or payload.get("id") 
        or payload.get("remote_reference"),
        rejection_errors=errors,
        taxpayer_value=payload.get("cif") or payload.get("cui"),
        form_value=payload.get("formular") or payload.get("form"),
        period_value=payload.get("perioada") or payload.get("period"),
        artifact_hash=payload.get("hash"),
    )
