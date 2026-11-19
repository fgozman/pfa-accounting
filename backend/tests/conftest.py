"""Shared test fixtures.

Safety rules enforced here:
* the target database name must be explicitly configured as a **test** database,
  so a mistyped URL can never wipe development data;
* signing keys live in a throwaway directory outside the repository;
* every test starts from a clean tenant state while reference data is preserved.
"""

from __future__ import annotations

import itertools
import os
import shutil
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

# ---------------------------------------------------------------------------
# Environment guard rails - set before any pfa import reads settings
# ---------------------------------------------------------------------------
#: The backend project directory (``backend/``), which owns ``.run`` test state.
ROOT = Path(__file__).resolve().parents[1]
TEST_PORT = os.environ.get("TEST_POSTGRES_PORT", "5433")
TEST_DB = os.environ.get("TEST_POSTGRES_DB", "pfa_test")
TEST_OAUTH_DB = os.environ.get("TEST_POSTGRES_OAUTH_DB", "pfa_oauth_test")
TEST_OWNER_USER = os.environ.get("TEST_POSTGRES_OWNER_USER", "pfa_test_owner")
TEST_OWNER_PASSWORD = os.environ.get("TEST_POSTGRES_OWNER_PASSWORD", "test_owner_password")
TEST_APP_USER = os.environ.get("TEST_POSTGRES_APP_USER", "pfa_test_app")
TEST_APP_PASSWORD = os.environ.get("TEST_POSTGRES_APP_PASSWORD", "test_app_password")

os.environ["PFA_ENV"] = "test"
os.environ.setdefault(
    "PFA_DATABASE_URL",
    f"postgresql+asyncpg://{TEST_APP_USER}:{TEST_APP_PASSWORD}@127.0.0.1:{TEST_PORT}/{TEST_DB}",
)
os.environ.setdefault(
    "PFA_MIGRATION_DATABASE_URL",
    f"postgresql+asyncpg://{TEST_OWNER_USER}:{TEST_OWNER_PASSWORD}@127.0.0.1:{TEST_PORT}/{TEST_DB}",
)
# The OAuth database must be pinned here too: .env names the *development* one.
os.environ.setdefault(
    "PFA_OAUTH_DATABASE_URL",
    f"postgresql+asyncpg://{TEST_APP_USER}:{TEST_APP_PASSWORD}@127.0.0.1:{TEST_PORT}/{TEST_OAUTH_DB}",
)
os.environ.setdefault(
    "PFA_OAUTH_MIGRATION_DATABASE_URL",
    f"postgresql+asyncpg://{TEST_OWNER_USER}:{TEST_OWNER_PASSWORD}@127.0.0.1:{TEST_PORT}/{TEST_OAUTH_DB}",
)
os.environ.setdefault("PFA_APP_USER", TEST_APP_USER)
os.environ.setdefault("PFA_KEY_DIR", str(ROOT / ".run" / "test-keys"))
os.environ.setdefault("PFA_KEY_ACTIVE_KID", "test-2026-01")
os.environ.setdefault("PFA_OBJECT_STORAGE_DIR", str(ROOT / ".run" / "test-objects"))
os.environ.setdefault("PFA_EMAIL_DIR", str(ROOT / ".run" / "test-emails"))
os.environ.setdefault("PFA_ANAF_DRIVER", "fake")
os.environ.setdefault("PFA_SPA_ORIGIN", "http://127.0.0.1:5173")
os.environ.setdefault("PFA_SPA_CALLBACK_URI", "http://127.0.0.1:5173/auth/callback")
os.environ.setdefault("PFA_SPA_CLIENT_ID", "pfa-web-test")
os.environ.setdefault("PFA_AUTHORIZATION_ORIGIN", "http://127.0.0.1:8000")
# fast, deterministic password hashing for tests only
os.environ.setdefault("PFA_ARGON2_TIME_COST", "1")
os.environ.setdefault("PFA_ARGON2_MEMORY_COST", "8192")
os.environ.setdefault("PFA_ARGON2_PARALLELISM", "1")


# ---------------------------------------------------------------------------
# The series/number of a document is entered by the user, so tests supply one.
# Nothing in the application allocates it any more.
# ---------------------------------------------------------------------------

_numbers = itertools.count(1)

#: the series tests issue documents under
TEST_SERIES = "TEST"


def document_number() -> str:
    """A unique number for the test run (uniqueness is per workspace)."""
    return f"{next(_numbers):06d}"


def finalize_payload(*, series: str = TEST_SERIES, number: str | None = None) -> dict:
    return {"series": series, "number": number or document_number()}


def assert_test_database(url: str) -> None:
    lowered = url.lower()
    if "test" not in lowered:
        raise RuntimeError(
            "Refusing to run tests against a database whose URL does not identify it as a "
            f"test database: {url}"
        )


for _variable in (
    "PFA_DATABASE_URL",
    "PFA_MIGRATION_DATABASE_URL",
    "PFA_OAUTH_DATABASE_URL",
    "PFA_OAUTH_MIGRATION_DATABASE_URL",
):
    assert_test_database(os.environ[_variable])

from pfa import clock  # noqa: E402
from pfa.config import reload_settings  # noqa: E402
from pfa.db import Database, build_engine, override_database  # noqa: E402
from pfa.oauth.db import override_oauth_database  # noqa: E402

TEST_USER_PASSWORD = "Correct-Horse-Battery-9"


@dataclass(slots=True)
class TenantFixture:
    workspace_id: uuid.UUID
    admin_id: uuid.UUID
    member_id: uuid.UUID
    admin_email: str
    member_email: str


@pytest.fixture(scope="session", autouse=True)
def _settings() -> None:
    settings = reload_settings()
    key_dir = Path(settings.key_dir)
    if key_dir.exists():
        shutil.rmtree(key_dir)
    from pfa.security.keys import KeyStore

    KeyStore.generate(key_dir, settings.key_active_kid)
    object_dir = Path(settings.object_storage_dir)
    if object_dir.exists():
        shutil.rmtree(object_dir)
    reload_settings()


@pytest.fixture
async def database(_settings: None) -> AsyncIterator[Database]:
    """Runtime-role engine: cannot bypass RLS, cannot TRUNCATE.

    One engine per test keeps every connection bound to the running event loop.
    """
    settings = reload_settings()
    db = Database.from_settings(settings)
    override_database(lambda: db)
    # the authorization server's own database, also on the runtime role
    oauth_db = Database.from_url(settings.oauth_database_url, settings)
    override_oauth_database(lambda: oauth_db)
    try:
        yield db
    finally:
        await db.dispose()
        await oauth_db.dispose()
        override_database(None)
        override_oauth_database(None)


def _owner(url: str) -> Database:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    engine = build_engine(url, pool_size=2, max_overflow=2)
    return Database(engine=engine, session_factory=async_sessionmaker(engine, expire_on_commit=False))


@pytest.fixture
def oauth_database(database: Database) -> Database:
    """The authorization server's database (runtime role), as the app sees it."""
    from pfa.oauth.db import get_oauth_database

    return get_oauth_database()


@pytest.fixture
async def owner_database(_settings: None, database: Database) -> AsyncIterator[Database]:
    """Owner-role engine used only for test housekeeping (never by the app)."""
    owner = _owner(reload_settings().migration_database_url)
    try:
        yield owner
    finally:
        await owner.dispose()


@pytest.fixture
async def oauth_owner_database(_settings: None, database: Database) -> AsyncIterator[Database]:
    """Owner-role engine on the OAuth database, for housekeeping and inspection."""
    owner = _owner(reload_settings().oauth_migration_database_url)
    try:
        yield owner
    finally:
        await owner.dispose()


TENANT_TABLES = [
    "payment_allocations",
    "payments",
    "financial_accounts",
    "document_lines",
    "document_revisions",
    "attachments",
    "document_imports",
    "documents",
    "number_series",
    "counterparties",
    "accounting_events",
    "tax_adjustments",
    "depreciation_entries",
    "assets",
    "inventory_entries",
    "period_locks",
    "register_exports",
    "calculation_runs",
    "declaration_versions",
    "declaration_source_grants",
    "declarations",
    "obligations",
    "notifications",
    "notification_deliveries",
    "notification_user_states",
    "jobs",
    "outbox_events",
    "idempotency_keys",
    "submission_attempts",
    "remote_messages",
    "receipts",
    "anaf_connections",
    "taxpayer_access_grants",
    "taxpayer_year_facts",
    "loss_carryforwards",
    "tax_regimes",
    "vat_registrations",
    "activity_periods",
    "pfa_profiles",
    "invitations",
    "memberships",
    "taxpayers",
    "workspaces",
    # account rows: global tables, but they must not leak between tests either
    "email_verification_tokens",
    "password_reset_tokens",
    "mfa_factors",
    "users",
    # reference data is per-test too, so a fixture never depends on the order of
    # other tests
    "form_versions",
    "rule_packs",
    "deadline_rules",
    "income_norms",
    "deduction_rules",
    "legal_calendar_days",
    "connector_capabilities",
    "exchange_rates",
]

#: authorization runtime rows, in the OAuth database (registered clients stay)
OAUTH_RUNTIME_TABLES = [
    "oauth_authorization_codes",
    "oauth_authorization_transactions",
    "oauth_refresh_tokens",
    "oauth_sessions",
]


@pytest.fixture(autouse=True)
async def clean_tenants(
    database: Database, owner_database: Database, oauth_owner_database: Database
) -> AsyncIterator[None]:
    """Remove tenant state between tests; reference data and oauth clients stay.

    Housekeeping uses the owner connection because the runtime role deliberately
    has no TRUNCATE privilege.
    """
    async with owner_database.session() as session:
        await session.execute(
            text(f"TRUNCATE {', '.join(TENANT_TABLES)} RESTART IDENTITY CASCADE")
        )
    async with oauth_owner_database.session() as session:
        await session.execute(
            text(f"TRUNCATE {', '.join(OAUTH_RUNTIME_TABLES)} RESTART IDENTITY CASCADE")
        )
    clock.unfreeze()
    from pfa.security.ratelimit import reset_all

    reset_all()
    yield


@pytest.fixture(autouse=True)
def freeze_time():
    """Freeze time to the specification's research baseline date."""
    clock.freeze(datetime(2026, 9, 16, 10, 0, tzinfo=UTC))
    yield
    clock.unfreeze()


# ---------------------------------------------------------------------------
# HTTP client and the full OAuth code+PKCE login helper
# ---------------------------------------------------------------------------


@pytest.fixture
async def app(database: Database):
    from pfa.api.deps import reset_object_store
    from pfa.main import create_app
    from pfa.oauth.flows import register_client
    from pfa.oauth.keys import reset_keys

    reset_keys()
    reset_object_store()
    application = create_app(reload_settings())
    settings = reload_settings()
    from pfa.oauth.db import get_oauth_database

    async with get_oauth_database().session() as session:
        await register_client(
            session,
            client_id=settings.spa_client_id,
            environment=settings.env,
            redirect_uris=[settings.spa_callback_uri],
            allowed_origins=[settings.spa_origin],
            allowed_scopes=["pfa.read", "pfa.write", "pfa.manage", "profile"],
            settings=settings,
        )
    return application


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://127.0.0.1:8000"
    ) as async_client:
        yield async_client


@dataclass(slots=True)
class Session:
    """An authenticated SPA-like session: bearer tokens only, no cookies."""

    access_token: str
    refresh_token: str
    user_id: str
    email: str

    def headers(self, **extra: str) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.access_token}"}
        headers.update(extra)
        return headers


async def create_verified_user(
    database: Database, *, email: str, password: str = TEST_USER_PASSWORD
) -> str:
    """Register a user and consume the verification token out of band."""
    from pfa.services import accounts

    async with database.session() as session:
        user, issued = await accounts.register_user(session, email=email, password=password)
        user_id = str(user.id)
    async with database.session() as session:
        await accounts.verify_email(session, token=issued.token)
    return user_id


async def fetch_outbox_payload(owner: Database, *, event_type: str) -> dict:
    """Read an account-level outbox event as the owner role.

    Account e-mails carry ``workspace_id = NULL`` and are therefore invisible to
    the runtime role (and to every tenant) by design; the worker delivers them
    through the SECURITY DEFINER leasing function.  Tests read them with the owner
    connection, exactly as a mail-provider integration would.
    """
    async with owner.session() as session:
        payload = (
            await session.execute(
                text(
                    "SELECT payload FROM outbox_events WHERE event_type = :event_type "
                    "ORDER BY created_at DESC LIMIT 1"
                ),
                {"event_type": event_type},
            )
        ).scalar_one()
    return payload


async def oauth_login(
    client: AsyncClient,
    *,
    email: str,
    password: str = TEST_USER_PASSWORD,
    scope: str = "pfa.read pfa.write pfa.manage",
    settings=None,
) -> Session:
    """Full cookie-free authorization-code + PKCE S256 round trip."""
    import re

    from pfa.security.tokens import new_pkce_verifier, new_state, pkce_challenge_s256

    settings = settings or reload_settings()
    verifier = new_pkce_verifier()
    state = new_state()
    response = await client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": settings.spa_client_id,
            "redirect_uri": settings.spa_callback_uri,
            "scope": scope,
            "state": state,
            "code_challenge": pkce_challenge_s256(verifier),
            "code_challenge_method": "S256",
        },
        headers={"Origin": settings.spa_origin},
    )
    assert response.status_code == 200, response.text
    # no cookie is ever set on an authentication path
    assert "set-cookie" not in {key.lower() for key in response.headers}
    match = re.search(r'name="transaction_proof" value="([^"]+)"', response.text)
    assert match, response.text
    proof = match.group(1)
    tx = re.search(r'action="/auth/authorization-transactions/([^/]+)/authenticate"', response.text)
    assert tx, response.text
    transaction_id = tx.group(1)

    response = await client.post(
        f"/auth/authorization-transactions/{transaction_id}/authenticate",
        data={"email": email, "password": password, "transaction_proof": proof},
        headers={"Origin": settings.spa_origin},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    assert "set-cookie" not in {key.lower() for key in response.headers}
    location = response.headers["location"]
    assert location.startswith(settings.spa_callback_uri)
    code = re.search(r"[?&]code=([^&]+)", location).group(1)
    returned_state = re.search(r"[?&]state=([^&]+)", location).group(1)
    assert returned_state == state

    token_response = await client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": settings.spa_client_id,
            "code": code,
            "redirect_uri": settings.spa_callback_uri,
            "code_verifier": verifier,
        },
    )
    assert token_response.status_code == 200, token_response.text
    payload = token_response.json()
    return Session(
        access_token=payload["access_token"],
        refresh_token=payload["refresh_token"],
        user_id="",
        email=email,
    )


@pytest.fixture
async def tenant(database: Database) -> TenantFixture:
    """A workspace with an ADMIN and a DEFAULT member (the acceptance shape)."""
    from pfa.models.base import MembershipRole
    from pfa.models.tenancy import Membership
    from pfa.services import tenancy

    admin_email = "admin@example.test"
    member_email = "member@example.test"
    admin_id = await create_verified_user(database, email=admin_email)
    member_id = await create_verified_user(database, email=member_email)

    async with database.user_session(admin_id) as session:
        workspace, _membership = await tenancy.create_workspace(
            session, user_id=uuid.UUID(admin_id), name="PFA Test SRL", cui="90000009"
        )
        session.add(
            Membership(
                workspace_id=workspace.id,
                user_id=uuid.UUID(member_id),
                role=MembershipRole.DEFAULT,
            )
        )
        await session.flush()
        workspace_id = workspace.id
    return TenantFixture(
        workspace_id=workspace_id,
        admin_id=uuid.UUID(admin_id),
        member_id=uuid.UUID(member_id),
        admin_email=admin_email,
        member_email=member_email,
    )


@pytest.fixture
async def admin_session(client: AsyncClient, tenant: TenantFixture) -> Session:
    session = await oauth_login(client, email=tenant.admin_email)
    session.user_id = str(tenant.admin_id)
    return session


@pytest.fixture
async def member_session(client: AsyncClient, tenant: TenantFixture) -> Session:
    session = await oauth_login(client, email=tenant.member_email)
    session.user_id = str(tenant.member_id)
    return session


@pytest.fixture
def workspace_id(tenant: TenantFixture) -> str:
    return str(tenant.workspace_id)


@pytest.fixture
async def frozen_now() -> datetime:
    return datetime(2026, 9, 16, 10, 0, tzinfo=UTC)


@pytest.fixture
def plus_days():
    def _plus(days: int) -> datetime:
        return datetime(2026, 9, 16, 10, 0, tzinfo=UTC) + timedelta(days=days)

    return _plus
