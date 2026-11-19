"""Obligation catalogue and evaluator.

For every candidate obligation the evaluator returns ``required``,
``not_required`` or ``needs_information`` with the legal rule version, an
explanation, missing inputs, filing period, deadlines and the supported delivery
capabilities.

Four capabilities are tracked separately: **detect**, **calculate**,
**generate** and **submit**.  A form whose detection is not implemented is still
listed, explicitly flagged, so a missing obligation can never silently disappear
from the dashboard.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pfa import clock
from pfa.errors import NotFound
from pfa.fiscal.packs import get_pack, supported_years
from pfa.models.base import (
    DocumentKind,
    DocumentStatus,
    ObligationApplicability,
    PartnerKind,
)
from pfa.models.compliance import FormVersion, Obligation
from pfa.models.documents import Document
from pfa.models.tenancy import (
    ActivityPeriod,
    PfaProfile,
    TaxpayerYearFact,
    TaxRegime,
    VatRegistration,
)
from pfa.repo import compliance as compliance_repo
from pfa.services.audit import record_audit
from pfa.services.deadlines import (
    UNKNOWN_DEADLINE_PROVENANCE,
    DeadlineRule,
    compute_deadline,
    default_rule_for,
)


@dataclass(frozen=True, slots=True)
class FormDescriptor:
    code: str
    name_ro: str
    name_en: str
    kind: str  # filing | registration | transmission | payment
    recurrence: str  # annual | monthly | quarterly | event
    #: supported: this release generates the artifact; assisted: guided workflow
    #: or export only; outside_coverage: detected and flagged, not generated.
    coverage: str
    description_ro: str
    legal_reference: str
    detect: bool = True
    calculate: bool = True
    generate: bool = True
    submit: bool = False
    #: natural-language reason shown when a capability is unavailable
    unsupported_reason: str | None = None


CATALOGUE: dict[str, FormDescriptor] = {
    "D212": FormDescriptor(
        code="D212",
        name_ro="Declarația unică privind impozitul pe venit și contribuțiile sociale datorate de persoanele fizice",
        name_en="Single declaration on income tax and social contributions",
        kind="filing",
        recurrence="annual",
        coverage="supported",
        description_ro=(
            "Consolidează veniturile din activități independente (sistem real și/sau normă "
            "de venit) cu alte venituri autorizate explicit, contribuțiile CAS/CASS și "
            "creditul fiscal extern."
        ),
        legal_reference="Cod fiscal art. 122, 148-151, 170-174",
        submit=False,
        unsupported_reason=(
            "Nu există un canal API verificat pentru depunerea automată a D212: se "
            "generează fișierul validat și se încarcă în SPV de către contribuabil."
        ),
    ),
    "D300": FormDescriptor(
        code="D300",
        name_ro="Decontul de taxă pe valoarea adăugată",
        name_en="VAT return",
        kind="filing",
        recurrence="monthly",
        coverage="supported",
        description_ro="Jurnalele de vânzări și cumpărări, ajustările și soldul reportat.",
        legal_reference="Cod fiscal art. 323; OPANAF 2131/2025",
        submit=False,
        unsupported_reason=(
            "Depunerea automată nu este activată până la înregistrarea și verificarea "
            "conectorului ANAF pentru acest formular."
        ),
    ),
    "D301": FormDescriptor(
        code="D301",
        name_ro="Decontul special de taxă pe valoarea adăugată",
        name_en="Special VAT return",
        kind="filing",
        recurrence="monthly",
        coverage="partial",
        description_ro=(
            "Operațiuni care impun decontul special: achiziții intracomunitare, taxare "
            "inversă pentru servicii, inclusiv pentru persoane cu cod special (art. 317)."
        ),
        legal_reference="Cod fiscal art. 317, 325",
    ),
    "D390": FormDescriptor(
        code="D390",
        name_ro="Declarația recapitulativă privind livrările/prestările intracomunitare (VIES)",
        name_en="VIES recapitulative statement",
        kind="filing",
        recurrence="monthly",
        coverage="partial",
        description_ro=(
            "Raportează operațiunile intracomunitare raportabile. Statutul art. 316/317 și "
            "clasificarea operațiunii sunt evaluate; apartenența la UE nu este suficientă."
        ),
        legal_reference="Cod fiscal art. 325, 394",
    ),
    "D394": FormDescriptor(
        code="D394",
        name_ro="Declarația informativă privind livrările/prestările și achizițiile efectuate pe teritoriul național",
        name_en="Domestic transactions informative return",
        kind="filing",
        recurrence="quarterly",
        coverage="partial",
        description_ro="Clasificarea contrapărților și categoriile de documente pe perioade.",
        legal_reference="Cod fiscal art. 325",
    ),
    "D700": FormDescriptor(
        code="D700",
        name_ro="Declarația pentru înregistrarea/modificarea ulterioară a datelor de identificare fiscală",
        name_en="Fiscal registration / modification declaration",
        kind="registration",
        recurrence="event",
        coverage="assisted",
        description_ro=(
            "Flux ghidat pentru evenimente de înregistrare fiscală, schimbare de opțiuni și "
            "anulare. Se selectează formularul permis la data evenimentului."
        ),
        legal_reference="Cod procedură fiscală art. 82-83",
        generate=False,
        unsupported_reason=(
            "Generarea este asistată: formularul permis și canalul se verifică manual pentru "
            "fiecare eveniment."
        ),
    ),
    "D070": FormDescriptor(
        code="D070",
        name_ro="Declarația de înregistrare fiscală pentru persoane fizice care desfășoară activități independente",
        name_en="Fiscal registration for independent activities",
        kind="registration",
        recurrence="event",
        coverage="assisted",
        description_ro="Înregistrarea inițială a PFA.",
        legal_reference="Cod procedură fiscală art. 82",
        generate=False,
    ),
    "D112": FormDescriptor(
        code="D112",
        name_ro="Declarația privind obligațiile de plată a contribuțiilor sociale, impozitului pe venit și evidența nominală a persoanelor asigurate",
        name_en="Employer payroll return",
        kind="filing",
        recurrence="monthly",
        coverage="assisted",
        description_ro="Necesită modulul de salarizare sau un import de salarizare validat.",
        legal_reference="Cod fiscal art. 147",
        detect=False,
        calculate=False,
        generate=False,
        unsupported_reason=(
            "Modulul de salarizare nu este implementat în această versiune: facturile și "
            "cheltuielile singure nu pot produce D112."
        ),
    ),
    "D100": FormDescriptor(
        code="D100",
        name_ro="Declarația privind obligațiile de plată la bugetul de stat (retenții)",
        name_en="Withholding obligations statement",
        kind="filing",
        recurrence="monthly",
        coverage="assisted",
        description_ro="Rețineri la sursă și plăți corespunzătoare.",
        legal_reference="Cod fiscal art. 147",
        detect=False,
        calculate=False,
        generate=False,
        unsupported_reason="Modulul de rețineri la sursă nu este implementat în această versiune.",
    ),
    "D205": FormDescriptor(
        code="D205",
        name_ro="Declarația informativă privind impozitul reținut la sursă pe beneficiari de venit",
        name_en="Annual withholding information return",
        kind="filing",
        recurrence="annual",
        coverage="assisted",
        description_ro="Identitatea și rezidența beneficiarilor, tipul de venit, reținerea.",
        legal_reference="Cod fiscal art. 147",
        detect=False,
        calculate=False,
        generate=False,
        unsupported_reason="Modulul de rețineri la sursă nu este implementat în această versiune.",
    ),
    "D207": FormDescriptor(
        code="D207",
        name_ro="Declarația informativă privind impozitul pe veniturile din activități independente",
        name_en="Informative return on independent-activity income",
        kind="filing",
        recurrence="annual",
        coverage="assisted",
        description_ro="Informativ, pentru plătitorii de venituri către PFA.",
        legal_reference="Cod fiscal art. 147",
        detect=False,
        calculate=False,
        generate=False,
        unsupported_reason="Se aplică plătitorilor de venituri, nu PFA care își declară propriul venit.",
    ),
    "D710": FormDescriptor(
        code="D710",
        name_ro="Declarația rectificativă pentru declarațiile informative",
        name_en="Corrective informative return",
        kind="filing",
        recurrence="event",
        coverage="assisted",
        description_ro="Corecții pentru declarațiile informative.",
        legal_reference="Cod fiscal art. 147",
        detect=False,
        calculate=False,
        generate=False,
        unsupported_reason="Se activează împreună cu modulul de rețineri la sursă.",
    ),
    "D307": FormDescriptor(
        code="D307",
        name_ro="Declarația privind ajustările de TVA pentru bunurile mobile corporale",
        name_en="VAT adjustment on capital goods",
        kind="filing",
        recurrence="event",
        coverage="assisted",
        description_ro="Scenarii dedicate de ajustare a TVA.",
        legal_reference="Cod fiscal art. 305",
        detect=False,
        calculate=False,
        generate=False,
        unsupported_reason="Necesită revizuire de specialitate înainte de a fi generat automat.",
    ),
    "D311": FormDescriptor(
        code="D311",
        name_ro="Declarația privind ajustarea TVA în cazul anulării înregistrării",
        name_en="VAT adjustment on deregistration",
        kind="filing",
        recurrence="event",
        coverage="assisted",
        description_ro="Anularea înregistrării în scopuri de TVA și ajustările aferente.",
        legal_reference="Cod fiscal art. 310-311",
        detect=False,
        calculate=False,
        generate=False,
        unsupported_reason="Necesită revizuire de specialitate înainte de a fi generat automat.",
    ),
    "D398": FormDescriptor(
        code="D398",
        name_ro="Declarația privind taxa pe valoarea adăugată pentru regimul special (OSS/IOSS)",
        name_en="OSS/IOSS special regime return",
        kind="filing",
        recurrence="quarterly",
        coverage="assisted",
        description_ro="Necesită date separate de țară, cotă și înregistrare.",
        legal_reference="Cod fiscal art. 315",
        detect=False,
        calculate=False,
        generate=False,
        unsupported_reason=(
            "Regimul special nu se deduce din vânzările externe: necesită date de consum "
            "pe fiecare stat membru."
        ),
    ),
    "EFACTURA": FormDescriptor(
        code="EFACTURA",
        name_ro="RO e-Factura — transmiterea facturilor",
        name_en="RO e-Factura invoice transmission",
        kind="transmission",
        recurrence="event",
        coverage="supported",
        description_ro=(
            "Generare XML CIUS/UBL, validare față de schema oficială, încărcare, urmărirea "
            "stării, descărcarea pachetului de răspuns și păstrarea artefactelor ANAF."
        ),
        legal_reference="OUG 120/2021",
    ),
    "D392": FormDescriptor(
        code="D392",
        name_ro="Declarația informativă privind operațiunile încasate de către persoanele neplătitoare de TVA",
        name_en="Informative return: non-VAT payer operations settled other than in cash/e-Factura",
        kind="filing",
        recurrence="annual",
        coverage="assisted",
        detect=True,
        calculate=False,
        generate=False,
        description_ro=(
            "Cod fiscal art. 259^1: persoana neplătitoare de TVA care facturează anual către "
            "persoane juridice raportează până în 25 februarie operațiunile stabilite a "
            "fi plătite prin alte instrumente decât numerar și e-Factura."
        ),
        legal_reference="Cod fiscal art. 259^1; ANAF Ordin 1718/2022 (formularul)",
        unsupported_reason=(
            "Generarea este asistată: totalurile lunare pe instrumente de plată "
            "altele decât numerar/e-Factura se completează din extrasele de cont."
        ),
    ),
    "D406": FormDescriptor(
        code="D406",
        name_ro="SAF-T (D406)",
        name_en="SAF-T",
        kind="filing",
        recurrence="annual",
        coverage="supported",
        description_ro=(
            "PFA se află în categoriile excluse de la D406 conform OPANAF 407/2025: "
            "nu este activat ca obligație implicită."
        ),
        legal_reference="OPANAF 407/2025",
    ),
}


@dataclass(slots=True)
class WorkspaceFiscalView:
    workspace_id: uuid.UUID
    income_year: int
    regimes: list[TaxRegime] = field(default_factory=list)
    vat_registrations: list[VatRegistration] = field(default_factory=list)
    activities: list[ActivityPeriod] = field(default_factory=list)
    facts: dict[str, TaxpayerYearFact] = field(default_factory=dict)
    documents: list[Document] = field(default_factory=list)
    has_activity: bool = False


#: fact keys that must be answered before a declaration becomes final
REQUIRED_FACTS: dict[str, tuple[str, ...]] = {
    "D212": (
        "regime",
        "cas_election",
        "cass_election",
        "prior_losses",
        "salary_income_present",
        "pension_status",
    ),
}


async def load_workspace_view(
    session: AsyncSession, *, workspace_id: uuid.UUID, income_year: int
) -> WorkspaceFiscalView:
    view = WorkspaceFiscalView(workspace_id=workspace_id, income_year=income_year)
    view.regimes = list(
        (
            await session.execute(
                select(TaxRegime).where(
                    TaxRegime.workspace_id == workspace_id,
                    TaxRegime.income_year == income_year,
                )
            )
        ).scalars().all()
    )
    year_start = date(income_year, 1, 1)
    year_end = date(income_year, 12, 31)
    view.vat_registrations = list(
        (
            await session.execute(
                select(VatRegistration).where(
                    VatRegistration.workspace_id == workspace_id,
                    VatRegistration.effective_from <= year_end,
                )
            )
        ).scalars().all()
    )
    view.activities = list(
        (
            await session.execute(
                select(ActivityPeriod).where(
                    ActivityPeriod.workspace_id == workspace_id,
                    ActivityPeriod.starts_on <= year_end,
                )
            )
        ).scalars().all()
    )
    facts = (
        await session.execute(
            select(TaxpayerYearFact).where(
                TaxpayerYearFact.workspace_id == workspace_id,
                TaxpayerYearFact.income_year == income_year,
            )
        )
    ).scalars().all()
    view.facts = {fact.fact_key: fact for fact in facts}
    view.documents = list(
        (
            await session.execute(
                select(Document).where(
                    Document.workspace_id == workspace_id,
                    Document.issue_date >= year_start,
                    Document.issue_date <= year_end,
                    Document.status == DocumentStatus.FINALIZED,
                )
            )
        ).scalars().all()
    )
    view.has_activity = bool(view.activities) or bool(view.regimes) or bool(view.documents)
    return view


def _is_intracommunity(document) -> bool:
    """Whether a document is a recapitulative (D390/VIES) operation.

    Intra-community evidence is required: either the L/T/A/P/S/R type is recorded, or
    the supplier is in another member state.  A domestic invoice is never a
    recapitulative operation, however it is taxed — taxing every standard purchase
    would raise a D390 that the taxpayer cannot fill in.
    """
    if document.intracommunity_operation_type:
        return True
    country = (document.supplier_country or "").upper()
    return bool(country) and country != "RO"


def _missing_facts(view: WorkspaceFiscalView, form_code: str) -> list[str]:
    missing = []
    for key in REQUIRED_FACTS.get(form_code, ()):
        fact = view.facts.get(key)
        if fact is None or fact.answered_at is None:
            missing.append(key)
    return missing


def capabilities_for(form_code: str, *, income_year: int, form_version: FormVersion | None = None) -> dict:
    descriptor = CATALOGUE[form_code]
    generate = descriptor.generate
    reason = descriptor.unsupported_reason
    if generate and form_code in {"D212", "D300", "D301", "D390", "D394"}:
        published = form_version is not None and form_version.supported
        if not published:
            generate = False
            reason = (
                f"Schema oficială pentru {form_code} / anul de venit {income_year} nu este "
                "publicată sau validată: se poate produce doar un document de lucru estimativ. "
                "Disponibilitatea formularului folosit în 2026 nu stabilește schema pentru "
                "venitul anului 2026 declarat în 2027."
            )
    return {
        "detect": descriptor.detect,
        "calculate": descriptor.calculate and descriptor.coverage != "assisted",
        "generate": generate,
        "submit": descriptor.submit,
        "coverage": descriptor.coverage,
        "unavailable_reason": reason,
    }


async def _upsert(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    recurrence_key: str,
    values: dict,
):
    """Insert or update one obligation.

    The deadline revision is advanced by the SQL statement itself, and only when
    the filing deadline actually changed (see :mod:`pfa.repo.compliance`).
    """
    values.setdefault("taxpayer_id", None)
    values.setdefault("generation_available_from", None)
    values.setdefault("generation_blocked_reason", None)
    values.setdefault("filing_due_date", None)
    values.setdefault("filing_cutoff_at", None)
    values.setdefault("payment_due_date", None)
    values.setdefault("deadline_rule_id", None)
    values.setdefault("deadline_provenance", UNKNOWN_DEADLINE_PROVENANCE)
    values.setdefault("source_url", None)
    values.setdefault("rule_pack_code", None)
    values.setdefault("rule_pack_version", None)
    values.setdefault("legal_rule_version", None)
    values.setdefault("capabilities", None)
    values.setdefault("unsupported_reason", None)
    values.setdefault("triggering_evidence", [])
    values.setdefault("missing_inputs", [])
    values.setdefault("filing_timezone", "Europe/Bucharest")
    return await compliance_repo.upsert_obligation(
        session,
        workspace_id=workspace_id,
        recurrence_key=recurrence_key,
        values=values,
    )


def _deadline_values(form_code: str, *, period_start: date, period_end: date, event_date: date | None = None) -> dict:
    rule: DeadlineRule | None = default_rule_for(form_code)
    if rule is None:
        return {
            "filing_due_date": None,
            "filing_cutoff_at": None,
            "payment_due_date": None,
            "deadline_provenance": UNKNOWN_DEADLINE_PROVENANCE,
        }
    result = compute_deadline(
        rule, period_start=period_start, period_end=period_end, event_date=event_date
    )
    return {
        "filing_due_date": result.filing_due_date,
        "filing_cutoff_at": result.filing_cutoff_at,
        "payment_due_date": result.payment_due_date,
        "deadline_provenance": result.provenance,
    }


def _periods_for(
    *, recurrence: str, income_year: int, frequency: str | None
) -> list[tuple[date, date]]:
    year_start = date(income_year, 1, 1)
    year_end = date(income_year, 12, 31)
    if recurrence == "annual":
        return [(year_start, year_end)]
    if recurrence == "monthly":
        periods = []
        for month in range(1, 13):
            start = date(income_year, month, 1)
            end = (
                date(income_year, month + 1, 1) - timedelta(days=1)
                if month < 12
                else year_end
            )
            periods.append((start, end))
        if frequency == "quarterly":
            return [
                (date(income_year, 1, 1), date(income_year, 3, 31)),
                (date(income_year, 4, 1), date(income_year, 6, 30)),
                (date(income_year, 7, 1), date(income_year, 9, 30)),
                (date(income_year, 10, 1), year_end),
            ]
        return periods
    if recurrence == "quarterly":
        return [
            (date(income_year, 1, 1), date(income_year, 3, 31)),
            (date(income_year, 4, 1), date(income_year, 6, 30)),
            (date(income_year, 7, 1), date(income_year, 9, 30)),
            (date(income_year, 10, 1), year_end),
        ]
    return [(year_start, year_end)]


def _vat_active(view: WorkspaceFiscalView, kind: str, period_start: date, period_end: date) -> VatRegistration | None:
    for registration in view.vat_registrations:
        if registration.kind != kind:
            continue
        start = registration.effective_from.date() if hasattr(registration.effective_from, "date") else registration.effective_from
        end = (
            registration.effective_to.date()
            if registration.effective_to and hasattr(registration.effective_to, "date")
            else registration.effective_to
        )
        if start <= period_end and (end is None or end >= period_start):
            return registration
    return None


def _document_nature(document: Document) -> str:
    """Classify a document for D301/D390/D394 purposes from stored evidence."""
    if document.kind == DocumentKind.SALES_INVOICE:
        return "sale"
    if document.kind == DocumentKind.PURCHASE_INVOICE:
        return "purchase"
    return "other"


async def evaluate_obligations(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    income_year: int,
    as_of: date | None = None,
) -> list[Obligation]:
    as_of = as_of or clock.today()
    view = await load_workspace_view(session, workspace_id=workspace_id, income_year=income_year)
    results: list[Obligation] = []

    form_versions = {
        row.form_code: row
        for row in (
            await session.execute(
                select(FormVersion).where(FormVersion.income_year == income_year)
            )
        ).scalars().all()
    }

    #: a year without a rule pack is a supported state: the obligation is still reported,
    #: with no legal version attached (the ``except`` below used to leave ``pack`` unbound)
    pack = None
    try:
        pack = get_pack(income_year)
        rule_pack_code, rule_pack_version, pack_sources = pack.code, pack.version, pack.source_urls
    except Exception:
        rule_pack_code, rule_pack_version, pack_sources = None, None, ()

    # ---- D212 ---------------------------------------------------------
    d212_missing = _missing_facts(view, "D212")
    if view.has_activity:
        applicability = (
            ObligationApplicability.NEEDS_INFORMATION
            if d212_missing
            else ObligationApplicability.REQUIRED
        )
    else:
        applicability = ObligationApplicability.NOT_REQUIRED
    values = {
        "taxpayer_id": None,
        "form_code": "D212",
        "form_name_ro": CATALOGUE["D212"].name_ro,
        "obligation_kind": "filing",
        "period_start": date(income_year, 1, 1),
        "period_end": date(income_year, 12, 31),
        "applicability": applicability,
        "applicability_reason": (
            "Există activitate înregistrată în anul de venit; declarația unică este obligatorie."
            if view.has_activity
            else "Nu există activitate înregistrată în acest an de venit."
        ),
        "rule_pack_code": rule_pack_code,
        "rule_pack_version": rule_pack_version,
        "legal_rule_version": pack.formula_version if pack else None,
        "source_url": (pack_sources[0] if pack_sources else None),
        "generation_available_from": None,
        "generation_blocked_reason": None,
        "missing_inputs": d212_missing,
        "triggering_evidence": [],
        "capabilities": capabilities_for(
            "D212", income_year=income_year, form_version=form_versions.get("D212")
        ),
        "unsupported_reason": capabilities_for(
            "D212", income_year=income_year, form_version=form_versions.get("D212")
        )["unavailable_reason"],
        **_deadline_values(
            "D212", period_start=date(income_year, 1, 1), period_end=date(income_year, 12, 31)
        ),
    }
    if applicability != ObligationApplicability.NOT_REQUIRED:
        results.append(
            await _upsert(
                session,
                workspace_id=workspace_id,
                recurrence_key=f"D212:{income_year}",
                values=values,
            )
        )
    else:
        results.append(
            await _upsert(
                session,
                workspace_id=workspace_id,
                recurrence_key=f"D212:{income_year}",
                values=values,
            )
        )

    # ---- D300 (normal VAT registration) ------------------------------
    frequency = next(
        (
            registration.reporting_frequency
            for registration in view.vat_registrations
            if registration.kind == "article_316"
        ),
        "monthly",
    )
    d300_needed = False
    for period_start, period_end in _periods_for(
        recurrence="monthly", income_year=income_year, frequency=frequency
    ):
        registration = _vat_active(view, "article_316", period_start, period_end)
        if registration is None:
            continue
        d300_needed = True
        documents = [
            document
            for document in view.documents
            if document.issue_date and period_start <= document.issue_date <= period_end
        ]
        missing = [] if documents else []
        results.append(
            await _upsert(
                session,
                workspace_id=workspace_id,
                recurrence_key=f"D300:{period_start.isoformat()}:{period_end.isoformat()}",
                values={
                    "form_code": "D300",
                    "form_name_ro": CATALOGUE["D300"].name_ro,
                    "obligation_kind": "filing",
                    "period_start": period_start,
                    "period_end": period_end,
                    "applicability": ObligationApplicability.REQUIRED,
                    "applicability_reason": (
                        "Înregistrare normală în scopuri de TVA (art. 316) activă în perioadă"
                        + ("; decont cu zero operațiuni (nil)." if not documents else ".")
                    ),
                    "rule_pack_code": rule_pack_code,
                    "rule_pack_version": rule_pack_version,
                    "legal_rule_version": pack.formula_version if pack else None,
                    "missing_inputs": missing,
                    "triggering_evidence": [str(document.id) for document in documents],
                    "capabilities": capabilities_for(
                        "D300", income_year=income_year, form_version=form_versions.get("D300")
                    ),
                    "unsupported_reason": capabilities_for(
                        "D300", income_year=income_year, form_version=form_versions.get("D300")
                    )["unavailable_reason"],
                    **_deadline_values(
                        "D300", period_start=period_start, period_end=period_end
                    ),
                },
            )
        )
    if not d300_needed:
        results.append(
            await _upsert(
                session,
                workspace_id=workspace_id,
                recurrence_key=f"D300:{income_year}:not-required",
                values={
                    "form_code": "D300",
                    "form_name_ro": CATALOGUE["D300"].name_ro,
                    "obligation_kind": "filing",
                    "period_start": date(income_year, 1, 1),
                    "period_end": date(income_year, 12, 31),
                    "applicability": ObligationApplicability.NOT_REQUIRED,
                    "applicability_reason": "Nu există înregistrare normală în scopuri de TVA (art. 316).",
                    "missing_inputs": [],
                    "capabilities": capabilities_for(
                        "D300", income_year=income_year, form_version=form_versions.get("D300")
                    ),
                    **_deadline_values(
                        "D300",
                        period_start=date(income_year, 12, 1),
                        period_end=date(income_year, 12, 31),
                    ),
                },
            )
        )

    # ---- D301 / D390 --------------------------------------------------
    for period_start, period_end in _periods_for(
        recurrence="monthly", income_year=income_year, frequency="monthly"
    ):
        reverse_charge_documents = [
            document
            for document in view.documents
            if document.issue_date
            and period_start <= document.issue_date <= period_end
            and document.vat_treatment in {"reverse_charge"}
        ]
        special = _vat_active(view, "article_317", period_start, period_end)
        if reverse_charge_documents or special is not None:
            missing: list[str] = []
            if special is not None and not reverse_charge_documents:
                missing.append("external_services_classification")
            results.append(
                await _upsert(
                    session,
                    workspace_id=workspace_id,
                    recurrence_key=f"D301:{period_start.isoformat()}:{period_end.isoformat()}",
                    values={
                        "form_code": "D301",
                        "form_name_ro": CATALOGUE["D301"].name_ro,
                        "obligation_kind": "filing",
                        "period_start": period_start,
                        "period_end": period_end,
                        "applicability": (
                            ObligationApplicability.NEEDS_INFORMATION
                            if missing
                            else ObligationApplicability.REQUIRED
                        ),
                        "applicability_reason": (
                            "Operațiuni cu taxare inversă sau cod special art. 317 activ."
                            if reverse_charge_documents
                            else "Cod special art. 317 activ: obligațiile se evaluează chiar sub pragul de scutire."
                        ),
                        "missing_inputs": missing,
                        "triggering_evidence": [str(d.id) for d in reverse_charge_documents],
                        "capabilities": capabilities_for(
                            "D301", income_year=income_year, form_version=form_versions.get("D301")
                        ),
                        **_deadline_values("D301", period_start=period_start, period_end=period_end),
                    },
                )
            )
        # D390 is evaluated for every month it could apply to — also when nothing is
        # reportable — so a period whose evidence disappeared (documents edited or
        # deleted) is retired instead of keeping a stale, un-actionable obligation.
        reportable = [
            document
            for document in view.documents
            if document.issue_date
            and period_start <= document.issue_date <= period_end
            and _is_intracommunity(document)
            and _vat_active(view, "article_316", period_start, period_end) is not None
        ]
        results.append(
            await _upsert(
                session,
                workspace_id=workspace_id,
                recurrence_key=f"D390:{period_start.isoformat()}:{period_end.isoformat()}",
                values={
                    "form_code": "D390",
                    "form_name_ro": CATALOGUE["D390"].name_ro,
                    "obligation_kind": "filing",
                    "period_start": period_start,
                    "period_end": period_end,
                    "applicability": (
                        ObligationApplicability.REQUIRED
                        if reportable
                        else ObligationApplicability.NOT_REQUIRED
                    ),
                    "applicability_reason": (
                        "Operațiuni intracomunitare raportabile identificate pentru perioadă; "
                        "apartenența la UE nu este suficientă, clasificarea decide."
                        if reportable
                        else "Nicio operațiune intracomunitară raportabilă în perioadă."
                    ),
                    "missing_inputs": sorted(
                        # only what the user can actually provide: the operation type,
                        # and — only where the D390 structure requires it (L/T/P/R) —
                        # the partner's VAT id.  VIES verification is not a user-entered
                        # fact, so it is never demanded here.
                        {
                            "intracommunity_operation_type"
                            for document in reportable
                            if not document.intracommunity_operation_type
                        }
                        | {
                            "supplier_vat_id"
                            for document in reportable
                            if document.intracommunity_operation_type in {"L", "T", "P", "R"}
                            and not (document.supplier_vat_id or document.counterparty_id)
                        }
                    ),
                    "triggering_evidence": [str(document.id) for document in reportable],
                    "capabilities": capabilities_for(
                        "D390", income_year=income_year, form_version=form_versions.get("D390")
                    ),
                    **_deadline_values("D390", period_start=period_start, period_end=period_end),
                },
            )
        )

    # ---- D394 ---------------------------------------------------------
    for period_start, period_end in _periods_for(
        recurrence="quarterly", income_year=income_year, frequency=None
    ):
        if _vat_active(view, "article_316", period_start, period_end) is None:
            continue
        # a consumer sale (B2C) needs no partner data; anything else that carries no
        # partner identity cannot be classified
        unclassified = [
            document
            for document in view.documents
            if document.issue_date
            and period_start <= document.issue_date <= period_end
            and document.partner_kind != PartnerKind.CONSUMER
            and not (document.counterparty_id or document.supplier_name_raw)
        ]
        results.append(
            await _upsert(
                session,
                workspace_id=workspace_id,
                recurrence_key=f"D394:{period_start.isoformat()}:{period_end.isoformat()}",
                values={
                    "form_code": "D394",
                    "form_name_ro": CATALOGUE["D394"].name_ro,
                    "obligation_kind": "filing",
                    "period_start": period_start,
                    "period_end": period_end,
                    "applicability": ObligationApplicability.REQUIRED,
                    "applicability_reason": (
                        "Înregistrare normală în scopuri de TVA (art. 316) activă."
                        + (
                            f" {len(unclassified)} documente nu au partenerul identificat."
                            if unclassified
                            else ""
                        )
                    ),
                    "missing_inputs": (
                        ["counterparty_classification"] if unclassified else []
                    ),
                    "triggering_evidence": [str(document.id) for document in unclassified],
                    "capabilities": capabilities_for(
                        "D394", income_year=income_year, form_version=form_versions.get("D394")
                    ),
                    **_deadline_values("D394", period_start=period_start, period_end=period_end),
                },
            )
        )

    # ---- e-Factura: per domestic invoice, working-day deadline ---------
    for document in view.documents:
        if document.kind != DocumentKind.SALES_INVOICE or not document.issue_date:
            continue
        # Keep the same recurrence key for foreign invoices so a corrected partner country
        # retires any previously created obligation (and its open reminders).
        domestic = (document.supplier_country or "RO").upper() == "RO"
        results.append(
            await _upsert(
                session,
                workspace_id=workspace_id,
                recurrence_key=f"EFACTURA:{document.id}",
                values={
                    "form_code": "EFACTURA",
                    "form_name_ro": CATALOGUE["EFACTURA"].name_ro,
                    "obligation_kind": "transmission",
                    "period_start": document.issue_date,
                    "period_end": document.issue_date,
                    "applicability": (
                        ObligationApplicability.REQUIRED
                        if domestic else ObligationApplicability.NOT_REQUIRED
                    ),
                    "applicability_reason": (
                        "Factură emisă către un client din România: transmiterea prin RO e-Factura "
                        "are termen de 5 zile lucrătoare și un termen suplimentar legat de data emiterii."
                        if domestic else "Factură emisă către un client din afara României."
                    ),
                    "missing_inputs": [],
                    "triggering_evidence": [str(document.id)],
                    "capabilities": capabilities_for("EFACTURA", income_year=income_year),
                    **_deadline_values(
                        "EFACTURA",
                        period_start=document.issue_date,
                        period_end=document.issue_date,
                        event_date=document.issue_date,
                    ),
                },
            )
        )

    # ---- D392: annual informative return for a non-VAT payer (art. 259^1) ----------
    # A person not registered for VAT who invoices annually to legal entities reports,
    # by 25 February of the following year, the operations taxable in Romania settled
    # through other payment instruments than cash or e-Factura (Cod fiscal art. 259^1).
    # Detection from evidence: no active article 316 registration, yet sales exist.
    non_vat_payer = (
        _vat_active(view, "article_316", date(income_year, 1, 1), date(income_year, 12, 31))
        is None
    )
    sales_in_year = [
        document
        for document in view.documents
        if document.kind == DocumentKind.SALES_INVOICE and document.issue_date
    ]
    if non_vat_payer and sales_in_year:
        descriptor = CATALOGUE["D392"]
        results.append(
            await _upsert(
                session,
                workspace_id=workspace_id,
                recurrence_key=f"D392:{income_year}",
                values={
                    "form_code": "D392",
                    "form_name_ro": descriptor.name_ro,
                    "obligation_kind": "filing",
                    "period_start": date(income_year, 1, 1),
                    "period_end": date(income_year, 12, 31),
                    "applicability": ObligationApplicability.NEEDS_INFORMATION,
                    "applicability_reason": (
                        "Persoană neplătitoare de TVA cu facturi emise în anul de venit: "
                        "operațiunile încasate prin alte instrumente decât numerarul sau "
                        "e-Factura se raportează anual până în 25 februarie (art. 259^1)."
                    ),
                    "missing_inputs": ["other_instruments_monthly_totals"],
                    "triggering_evidence": [str(document.id) for document in sales_in_year],
                    "capabilities": capabilities_for(
                        "D392", income_year=income_year, form_version=form_versions.get("D392")
                    ),
                    "unsupported_reason": capabilities_for(
                        "D392", income_year=income_year
                    )["unavailable_reason"],
                    **_deadline_values(
                        "D392",
                        period_start=date(income_year, 1, 1),
                        period_end=date(income_year, 12, 31),
                    ),
                },
            )
        )

    # ---- registration forms (assisted) ---------------------------------
    # D070 is the *initial* fiscal registration, so it belongs to the year the activity
    # started; a later change of registration data is D700.  Marking both for the same
    # event asked the user twice for one thing, and demanded a "verified registration
    # form" that no input in the application can produce — the assisted flow cannot
    # generate these forms at all, which is what the obligation already says.
    profile_started_on = await session.scalar(
        select(PfaProfile.started_on)
        .where(PfaProfile.workspace_id == workspace_id, PfaProfile.active.is_(True))
        .order_by(PfaProfile.created_at.desc())
        .limit(1)
    )
    started_this_year = (
        profile_started_on is not None and profile_started_on.year == income_year
    ) or any(
        activity.starts_on and getattr(activity.starts_on, "year", None) == income_year
        for activity in view.activities
    )
    registrations_this_year = [
        registration
        for registration in view.vat_registrations
        if registration.effective_from
        and getattr(registration.effective_from, "year", None) == income_year
    ]
    for code in ("D700", "D070"):
        if code == "D070":
            required = started_this_year
            reason = (
                "Activitatea a început în acest an: înregistrarea fiscală inițială se "
                "depune prin fluxul ghidat."
                if required
                else "Activitatea nu a început în acest an: nu există înregistrare "
                "fiscală inițială de depus."
            )
        else:
            required = bool(registrations_this_year) and not started_this_year
            reason = (
                "Există o modificare a datelor de înregistrare fiscală în acest an: "
                "formularul permis și canalul se stabilesc prin fluxul ghidat."
                if required
                else "Nu s-a înregistrat nicio modificare de înregistrare fiscală în acest an."
            )
        descriptor = CATALOGUE[code]
        capabilities = capabilities_for(code, income_year=income_year)
        results.append(
            await _upsert(
                session,
                workspace_id=workspace_id,
                recurrence_key=f"{code}:{income_year}",
                values={
                    "form_code": code,
                    "form_name_ro": descriptor.name_ro,
                    "obligation_kind": "registration",
                    "period_start": date(income_year, 1, 1),
                    "period_end": date(income_year, 12, 31),
                    "applicability": (
                        ObligationApplicability.NEEDS_INFORMATION
                        if required
                        else ObligationApplicability.NOT_REQUIRED
                    ),
                    "applicability_reason": reason,
                    # nothing for the user to supply: the limitation is the assisted flow,
                    # reported through ``unsupported_reason`` below
                    "missing_inputs": [],
                    "triggering_evidence": (
                        [str(registration.id) for registration in registrations_this_year]
                        if required
                        else []
                    ),
                    "capabilities": capabilities,
                    "unsupported_reason": capabilities["unavailable_reason"],
                    **_deadline_values(
                        code, period_start=date(income_year, 1, 1), period_end=date(income_year, 12, 31)
                    ),
                },
            )
        )

    # ---- D406 exclusion -------------------------------------------------
    results.append(
        await _upsert(
            session,
            workspace_id=workspace_id,
            recurrence_key=f"D406:{income_year}",
            values={
                "form_code": "D406",
                "form_name_ro": CATALOGUE["D406"].name_ro,
                "obligation_kind": "filing",
                "period_start": date(income_year, 1, 1),
                "period_end": date(income_year, 12, 31),
                "applicability": ObligationApplicability.NOT_REQUIRED,
                "applicability_reason": (
                    "PFA se află în categoriile excluse de la D406 conform OPANAF 407/2025; "
                    "nu se activează ca declarație implicită."
                ),
                "source_url": "https://static.anaf.ro/static/10/Anaf/legislatie/OPANAF_407_2025.pdf",
                "missing_inputs": [],
                "capabilities": capabilities_for("D406", income_year=income_year),
                **_deadline_values(
                    "D406", period_start=date(income_year, 1, 1), period_end=date(income_year, 12, 31)
                ),
            },
        )
    )

    await record_audit(
        session,
        action="obligations.evaluated",
        entity_type="workspace",
        entity_id=workspace_id,
        workspace_id=workspace_id,
        actor_kind="system",
        after={"income_year": income_year, "count": len(results)},
    )
    return results


async def list_obligations(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    income_year: int | None = None,
    only_actionable: bool = False,
) -> list:
    return await compliance_repo.list_obligations(
        session,
        workspace_id=workspace_id,
        income_year=income_year,
        actionable_only=only_actionable,
    )


async def get_obligation(session: AsyncSession, *, workspace_id: uuid.UUID, obligation_id: uuid.UUID):
    row = await compliance_repo.get_obligation(
        session, workspace_id=workspace_id, obligation_id=obligation_id
    )
    if row is None:
        raise NotFound("Obligația nu există.")
    return row


def unassessed_forms() -> list[dict]:
    """Forms in the catalogue whose detection is not implemented.

    They stay visible (with an explanation) instead of silently disappearing.
    """
    return [
        {
            "form_code": descriptor.code,
            "name_ro": descriptor.name_ro,
            "name_en": descriptor.name_en,
            "detection_available": descriptor.detect,
            "coverage": descriptor.coverage,
            "reason": descriptor.unsupported_reason,
        }
        for descriptor in CATALOGUE.values()
        if not descriptor.detect
    ]


def supported_years_list() -> list[int]:
    return supported_years()
