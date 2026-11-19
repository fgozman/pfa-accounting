"""Idempotent development seed.

    make seed-dev

Refuses production configuration, creates synthetic real-system and income-norm
PFAs with an ADMIN and a DEFAULT member each, example documents, a settled
payment, an asset and the exact localhost SPA callback registration.

Running it twice changes nothing.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select, text

from pfa import clock
from pfa.config import get_settings
from pfa.db import Database, get_database
from pfa.errors import DomainError
from pfa.models.base import (
    DeductibilityTreatment,
    DocumentKind,
    MembershipRole,
    PaymentDirection,
    PaymentKind,
    UserStatus,
    VatTreatment,
)
from pfa.models.documents import Counterparty
from pfa.models.tenancy import TaxpayerYearFact, VatRegistration, Workspace
from pfa.oauth import flows as oauth_service
from pfa.oauth.db import get_oauth_database
from pfa.security.passwords import hash_password
from pfa.services import assets as assets_service
from pfa.services import documents as documents_service
from pfa.services import obligations as obligations_service
from pfa.services import settlements as settlements_service
from pfa.services import tenancy
from pfa.services.accounts import normalize_email

SEED_TAG = "seed-dev:v1"


async def _ensure_user(session, email: str, password: str):
    from pfa.models.accounts import User

    normalized = normalize_email(email)
    user = (
        await session.execute(select(User).where(User.email_normalized == normalized))
    ).scalar_one_or_none()
    if user is not None:
        return user, False
    user = User(
        email_normalized=normalized,
        email_display=email,
        password_hash=hash_password(password),
        status=UserStatus.ACTIVE,
        verified_at=clock.now(),
    )
    session.add(user)
    await session.flush()
    return user, True


async def _ensure_workspace(session, *, name: str, cui: str, admin_id, member_id):
    existing = (
        await session.execute(select(Workspace).where(Workspace.cui == cui))
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False
    workspace, _membership = await tenancy.create_workspace(
        session, user_id=admin_id, name=name, cui=cui
    )
    from pfa.models.tenancy import Membership

    session.add(
        Membership(workspace_id=workspace.id, user_id=member_id, role=MembershipRole.DEFAULT)
    )
    await session.flush()
    return workspace, True


async def _seed_legal_data(session) -> None:
    """Reference data: rule packs, form versions, deadline rules, income norms."""
    from pfa.fiscal.packs import PACKS
    from pfa.models.compliance import DeadlineRule, FormVersion, IncomeNorm, RulePack
    from pfa.services.deadlines import DEFAULT_RULES

    for pack in PACKS.values():
        existing = (
            await session.execute(
                select(RulePack).where(RulePack.code == pack.code, RulePack.version == pack.version)
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                RulePack(
                    code=pack.code,
                    jurisdiction=pack.jurisdiction,
                    income_year=pack.income_year,
                    domain=pack.domain,
                    title=pack.title,
                    version=pack.version,
                    valid_from=pack.valid_from,
                    valid_to=pack.valid_to,
                    publication_date=pack.publication_date,
                    source_url=pack.source_urls[0] if pack.source_urls else None,
                    legal_article=pack.income_tax.legal_reference,
                    formula_version=pack.formula_version,
                    parameters=pack.to_parameters(),
                    supported_scenarios=list(pack.supported_scenarios),
                    regression_fixtures={"fixtures": list(pack.regression_fixtures)},
                    status=pack.status,
                    professionally_reviewed=pack.reviewed,
                    notes="\n".join(pack.notes),
                )
            )
    await session.flush()

    for rule in DEFAULT_RULES:
        existing = (
            await session.execute(
                select(DeadlineRule).where(
                    DeadlineRule.form_code == rule.form_code, DeadlineRule.version == rule.version
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                DeadlineRule(
                    form_code=rule.form_code,
                    jurisdiction="RO",
                    version=rule.version,
                    rule_kind=rule.rule_kind,
                    parameters=rule.parameters,
                    timezone=rule.timezone,
                    payment_rule_kind=rule.payment_rule_kind,
                    payment_parameters=rule.payment_parameters,
                    effective_from=rule.effective_from,
                    effective_to=rule.effective_to,
                    source_url=rule.source_url,
                    source_note=rule.source_note,
                    reviewed=rule.reviewed,
                )
            )

    # Form versions are only marked supported when a validator fixture is
    # recorded; until then generation stays workpaper-only.
    for form_code, income_year, schema, supported in (
        ("D212", 2025, "DU-2025.1", True),
        ("D300", 2025, "D300-2025.1", True),
        ("D301", 2025, "D301-2025.1", False),
        ("D390", 2025, "D390-2025.1", False),
        ("D394", 2025, "D394-2025.1", False),
        ("D212", 2026, "DU-2026-pending", False),
    ):
        existing = (
            await session.execute(
                select(FormVersion).where(
                    FormVersion.form_code == form_code,
                    FormVersion.income_year == income_year,
                    FormVersion.schema_version == schema,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                FormVersion(
                    form_code=form_code,
                    income_year=income_year,
                    schema_version=schema,
                    artifact_format="xml",
                    validator_id="anaf-official-validator",
                    filing_channel="external_upload",
                    supported=supported,
                    published=supported,
                    correction_strategy="initial_and_corrective",
                    notes=(
                        "Seed reference data: fixtures for the official validator must be "
                        "recorded before submission-ready generation is enabled."
                    ),
                )
            )
    await session.flush()

    for year in (2025, 2026):
        for county, caen, amount in (
            ("Cluj", "6201", Decimal("45000")),
            ("Cluj", "6202", Decimal("38000")),
            ("București", "6201", Decimal("52000")),
        ):
            existing = (
                await session.execute(
                    select(IncomeNorm).where(
                        IncomeNorm.income_year == year,
                        IncomeNorm.county == county,
                        IncomeNorm.caen_code == caen,
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                session.add(
                    IncomeNorm(
                        income_year=year,
                        county=county,
                        caen_code=caen,
                        activity_name="Activități de consultanță/programare",
                        norm_amount=amount,
                        adjustment_factors={"example": "seed data requiring professional review"},
                        source_url="https://static.anaf.ro/static/10/Anaf/AsistentaContribuabili_r/Toateformularele/Toate_Formularele.html",
                    )
                )
    await session.flush()


async def _seed_workspace_data(session, *, workspace, admin_id, kind: str) -> None:
    existing = await session.scalar(
        text(
            "SELECT 1 FROM documents WHERE workspace_id = :ws AND external_id = :tag LIMIT 1"
        ),
        {"ws": workspace.id, "tag": f"{SEED_TAG}:{kind}:invoice"},
    )
    if existing:
        return

    counterparty = Counterparty(
        workspace_id=workspace.id,
        name=f"Client Demo {kind.upper()} SRL",
        country_code="RO",
        party_kind="business",
        vat_identifier="RO12345678",
        vat_verified_at=clock.now(),
        vat_verification_source="seed",
    )
    session.add(counterparty)
    await session.flush()

    today = clock.local_now().date()
    invoice = await documents_service.create_document(
        session,
        workspace_id=workspace.id,
        actor_user_id=admin_id,
        payload=documents_service.DocumentInput(
            kind=DocumentKind.SALES_INVOICE,
            counterparty_id=counterparty.id,
            issue_date=today - timedelta(days=20),
            supply_date=today - timedelta(days=20),
            due_date=today + timedelta(days=10),
            vat_treatment=VatTreatment.UNDETERMINED,
            lines=(
                documents_service.DocumentLineInput(
                    description="Servicii de consultanță",
                    quantity=Decimal("1"),
                    unit_price=Decimal("10000"),
                    vat_rate=Decimal("0"),
                ),
            ),
            external_id=f"{SEED_TAG}:{kind}:invoice",
        ),
    )
    await documents_service.finalize_document(
        session,
        workspace_id=workspace.id,
        actor_user_id=admin_id,
        document_id=invoice.id,
        # numbers are entered by the user, never allocated: give the demo invoice one
        series="DEMO",
        number="0001",
    )

    expense = await documents_service.create_document(
        session,
        workspace_id=workspace.id,
        actor_user_id=admin_id,
        payload=documents_service.DocumentInput(
            kind=DocumentKind.PURCHASE_INVOICE,
            supplier_name_raw="Furnizor Demo SRL",
            issue_date=today - timedelta(days=15),
            supply_date=today - timedelta(days=15),
            vat_treatment=VatTreatment.UNDETERMINED,
            deductibility=DeductibilityTreatment.PARTIALLY_DEDUCTIBLE,
            business_use_percent=Decimal("50"),
            business_purpose="Deplasare pentru contractul Demo",
            deductibility_justification="Utilizare parțială în scop de afaceri (50%).",
            expense_category="vehicle",
            external_id=f"{SEED_TAG}:{kind}:expense",
            lines=(
                documents_service.DocumentLineInput(
                    description="Combustibil și întreținere auto",
                    quantity=Decimal("1"),
                    unit_price=Decimal("2000"),
                    vat_rate=Decimal("0"),
                ),
            ),
        ),
    )
    await documents_service.finalize_document(
        session,
        workspace_id=workspace.id,
        actor_user_id=admin_id,
        document_id=expense.id,
        # the supplier's own number, as printed on the received invoice
        number="FD-1001",
    )

    # Partial collection in December, the rest in January: separate settlement-year
    # events, no double counting.
    await settlements_service.record_payment(
        session,
        workspace_id=workspace.id,
        actor_user_id=admin_id,
        payload=settlements_service.PaymentInput(
            direction=PaymentDirection.INFLOW,
            kind=PaymentKind.CUSTOMER_RECEIPT,
            paid_on=date(today.year, 12, 20) if today.month == 12 else today - timedelta(days=5),
            amount=Decimal("4000"),
            reference="OP-demo-1",
            external_id=f"{SEED_TAG}:{kind}:payment-1",
            allocations=(
                settlements_service.AllocationInput(document_id=invoice.id, amount=Decimal("4000")),
            ),
        ),
    )
    await settlements_service.record_payment(
        session,
        workspace_id=workspace.id,
        actor_user_id=admin_id,
        payload=settlements_service.PaymentInput(
            direction=PaymentDirection.OUTFLOW,
            kind=PaymentKind.SUPPLIER_PAYMENT,
            paid_on=today - timedelta(days=10),
            amount=Decimal("2000"),
            reference="OP-demo-2",
            external_id=f"{SEED_TAG}:{kind}:payment-2",
            allocations=(
                settlements_service.AllocationInput(document_id=expense.id, amount=Decimal("2000")),
            ),
        ),
    )
    await settlements_service.record_payment(
        session,
        workspace_id=workspace.id,
        actor_user_id=admin_id,
        payload=settlements_service.PaymentInput(
            direction=PaymentDirection.INFLOW,
            kind=PaymentKind.OWNER_CONTRIBUTION,
            paid_on=today - timedelta(days=8),
            amount=Decimal("5000"),
            reference="OP-demo-3",
            external_id=f"{SEED_TAG}:{kind}:payment-3",
            note="Aport al titularului",
        ),
    )

    await assets_service.create_asset(
        session,
        workspace_id=workspace.id,
        actor_user_id=admin_id,
        payload=assets_service.AssetInput(
            name="Laptop demo",
            category="computers",
            acquisition_date=today - timedelta(days=120),
            in_service_date=today - timedelta(days=115),
            acquisition_cost=Decimal("6000"),
            acquisition_cost_ron=Decimal("6000"),
            business_use_percent=Decimal("100"),
            useful_life_months=36,
        ),
    )

    session.add(
        VatRegistration(
            workspace_id=workspace.id,
            kind="none",
            effective_from=clock.now() - timedelta(days=365),
            reporting_frequency=None,
            cash_accounting=False,
            evidence_reference=f"{SEED_TAG}:no-vat",
        )
    )
    for key, value_kind, value in (
        ("regime_confirmed", "text", kind),
        ("cas_election", "boolean", False),
        ("cass_election", "boolean", False),
        ("prior_losses", "decimal", "0"),
        ("salary_income_present", "boolean", False),
        ("pension_status", "text", "not_pensioner"),
    ):
        session.add(
            TaxpayerYearFact(
                workspace_id=workspace.id,
                income_year=today.year,
                fact_key=key,
                value_kind=value_kind,
                value_text=value if value_kind == "text" else None,
                value_boolean=value if value_kind == "boolean" else None,
                value_decimal=Decimal(value) if value_kind == "decimal" else None,
                answered_by_user_id=admin_id,
                answered_at=clock.now(),
            )
        )
    await session.flush()
    if kind == "norm":
        from pfa.models.tenancy import TaxRegime

        session.add(
            TaxRegime(
                workspace_id=workspace.id,
                kind="norm",
                income_year=today.year,
                effective_from=clock.now(),
                norm_county="Cluj",
                norm_locality="Cluj-Napoca",
                norm_activity="6201",
                norm_amount=Decimal("45000"),
            )
        )
        session.add(
            TaxpayerYearFact(
                workspace_id=workspace.id,
                income_year=today.year,
                fact_key="norm_amount",
                value_kind="decimal",
                value_decimal=Decimal("45000"),
                answered_by_user_id=admin_id,
                answered_at=clock.now(),
            )
        )
        session.add(
            TaxpayerYearFact(
                workspace_id=workspace.id,
                income_year=today.year,
                fact_key="norm_adjustment_percent",
                value_kind="decimal",
                value_decimal=Decimal("0"),
                answered_by_user_id=admin_id,
                answered_at=clock.now(),
            )
        )
    await session.flush()

    await obligations_service.evaluate_obligations(
        session, workspace_id=workspace.id, income_year=today.year
    )


async def seed(database: Database | None = None) -> dict:
    """Idempotent seed.  Every step is a no-op when its row already exists."""
    settings = get_settings()
    settings.assert_not_production("seed synthetic development data")
    database = database or get_database()
    created: dict[str, str] = {}

    # OAuth clients live in the authorization server's own database.
    async with get_oauth_database().session() as session:
        # Exact localhost SPA callback registration: the redirect URI must match
        # byte-for-byte what the SPA sends.
        await oauth_service.register_client(
            session,
            client_id=settings.spa_client_id,
            environment=settings.env,
            redirect_uris=[settings.spa_callback_uri],
            allowed_origins=[settings.spa_origin],
            allowed_scopes=list(oauth_service.SUPPORTED_SCOPES),
            settings=settings,
        )
        created["oauth_client"] = settings.spa_client_id

        # The interactive docs authenticate through the same authorization-code +
        # PKCE flow.  Its redirect URI must be registered byte-for-byte.
        if settings.docs_available:
            await oauth_service.register_client(
                session,
                client_id=settings.docs_client_id,
                environment=settings.env,
                redirect_uris=[settings.docs_redirect_uri],
                allowed_origins=[settings.authorization_origin],
                allowed_scopes=list(oauth_service.SUPPORTED_SCOPES),
                settings=settings,
            )
            created["docs_oauth_client"] = settings.docs_client_id
            created["docs_redirect_uri"] = settings.docs_redirect_uri

    # Global/reference data and accounts: no tenant context involved.
    async with database.session() as session:
        await _seed_legal_data(session)
        admin, _ = await _ensure_user(
            session, settings.seed_admin_email, settings.seed_admin_password
        )
        member, _ = await _ensure_user(
            session, settings.seed_member_email, settings.seed_member_password
        )
        admin_id, member_id = admin.id, member.id

    # Workspace creation happens in the creator's user context: RLS requires the
    # creator to be identifiable.
    workspace_ids: dict[str, uuid.UUID] = {}
    async with database.user_session(admin_id) as session:
        for kind, name, cui in (
            ("real", "PFA Demo Sistem Real", "90000001"),
            ("norm", "PFA Demo Normă de Venit", "90000002"),
        ):
            workspace, _created = await _ensure_workspace(
                session, name=name, cui=cui, admin_id=admin_id, member_id=member_id
            )
            workspace_ids[kind] = workspace.id
            created[f"workspace_{kind}"] = str(workspace.id)

    # Workspace data runs inside a tenant transaction so RLS applies as it would
    # for a real request.
    for kind, workspace_id in workspace_ids.items():
        async with database.tenant_session(workspace_id, actor="seed", user_id=admin_id) as session:
            workspace = await session.get(Workspace, workspace_id)
            await _seed_workspace_data(
                session, workspace=workspace, admin_id=admin_id, kind=kind
            )

    created["admin_email"] = settings.seed_admin_email
    created["member_email"] = settings.seed_member_email
    return created


def main() -> int:
    settings = get_settings()
    if settings.is_production:
        print("Refusing to seed with PFA_ENV=production", file=sys.stderr)
        return 2

    async def _run() -> dict:
        from pfa.db import dispose_database
        from pfa.oauth.db import dispose_oauth_database

        try:
            return await seed()
        finally:
            await dispose_database()
            await dispose_oauth_database()

    try:
        created = asyncio.run(_run())
    except DomainError as exc:
        print(f"Seed failed: {exc}", file=sys.stderr)
        return 1

    print("Seed complete (idempotent):")
    for key, value in created.items():
        print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
