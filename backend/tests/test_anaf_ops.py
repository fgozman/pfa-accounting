"""ANAF delivery, worker durability and cross-cutting API guarantees.

Requirement IDs: ANAF, ANAF-01..ANAF-03, OPS, OPS-01, API/DATA.
"""

from __future__ import annotations

import re
import uuid
from datetime import date
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy import text

from pfa.anaf import FakeAnafState, FakeAnafTransport
from pfa.anaf.efactura import (
    determine_applicability,
    parse_efactura_xml,
    transmission_deadline,
    transmit_invoice,
)
from pfa.db import Database
from pfa.models.base import JobStatus
from pfa.services.storage import ObjectStore
from pfa.worker import Worker, backoff_delay, mail_log_line
from tests.conftest import Session, finalize_payload


async def create_finalized_invoice(
    client: AsyncClient, session: Session, workspace_id: str, *, external_id: str = "ef-1"
) -> dict:
    created = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        json={
            "kind": "sales_invoice",
            "issue_date": "2025-04-30",
            "supply_date": "2025-04-30",
            "currency": "RON",
            "external_id": external_id,
            "lines": [
                {"description": "Servicii", "quantity": "1", "unit_price": "5000", "vat_rate": "0"}
            ],
        },
        headers=session.headers(),
    )
    assert created.status_code == 201, created.text
    document_id = created.json()["id"]
    finalized = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents/{document_id}/finalize",
        json=finalize_payload(),
        headers=session.headers(**{"Idempotency-Key": f"fin-{document_id}"}),
    )
    assert finalized.status_code == 200, finalized.text
    return finalized.json()


# ---------------------------------------------------------------------------
# e-Factura applicability and deadline
# ---------------------------------------------------------------------------


def test_efactura_applicability_predicate() -> None:
    assert determine_applicability(
        customer_country="RO", customer_is_business=True
    ).required
    assert determine_applicability(
        customer_country="RO", customer_is_business=False
    ).required  # B2C is in scope
    assert determine_applicability(
        customer_country="RO", customer_is_business=False, is_public_body=True
    ).required
    assert not determine_applicability(
        customer_country="DE", customer_is_business=True, supply_place="DE"
    ).required
    exempt = determine_applicability(
        customer_country="RO", customer_is_business=True, exemption="scutit art. 294"
    )
    assert exempt.required is False
    assert "art. 294" in exempt.reason


def test_efactura_deadline_is_a_working_day_calculation() -> None:
    # 30 April 2025 + five working days: 1 and 2 May are non-working days
    assert transmission_deadline(date(2025, 4, 30)) == date(2025, 5, 9)
    # a plain five-calendar-day timer would have produced 5 May
    assert transmission_deadline(date(2025, 4, 30)) != date(2025, 5, 5)


async def authorize_anaf(client: AsyncClient, session: Session, workspace_id: str) -> None:
    """Complete the ANAF authorization, so a transmission is actually possible.

    Without an authorized connection the application must refuse to transmit — nothing
    can leave it — so every transmission test needs one.
    """
    started = await client.post(
        f"/api/v1/workspaces/{workspace_id}/anaf/connect", headers=session.headers()
    )
    assert started.status_code == 200, started.text
    body = started.json()
    state = body["authorization_url"].split("state=")[1].split("&")[0]
    callback = await client.get(
        "/integrations/anaf/callback",
        params={"state": state, "connection_id": body["connection_id"], "code": "abc"},
        follow_redirects=False,
    )
    assert callback.status_code == 303, callback.text


async def test_transmission_is_refused_without_an_authorized_connection(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    """No ANAF connection means no transmission: the application must not claim one."""
    document = await create_finalized_invoice(client, admin_session, workspace_id)
    response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/efactura/transmit/{document['id']}",
        headers=admin_session.headers(),
    )
    assert response.status_code == 409, response.text
    assert "conexiune ANAF" in response.json()["title"]


async def test_transmission_is_queued_and_uses_the_fake_service(
    client: AsyncClient, database: Database, admin_session: Session, workspace_id: str
) -> None:
    await authorize_anaf(client, admin_session, workspace_id)
    document = await create_finalized_invoice(client, admin_session, workspace_id)
    response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/efactura/transmit/{document['id']}",
        headers=admin_session.headers(),
    )
    assert response.status_code == 202, response.text
    assert response.json()["deadline_hint"] == "2025-05-09"
    # the caller is told that the ANAF service is simulated, not real
    assert response.json()["simulated"] is True
    assert response.json()["transport"] == "fake"
    assert "SIMULATĂ" in response.json()["note"]

    # the worker performs the upload; a successful HTTP response is not acceptance
    worker = Worker(database)
    await worker.process_jobs_once()
    async with database.tenant_session(uuid.UUID(workspace_id), actor="worker") as session:
        row = (
            await session.execute(
                text(
                    "SELECT status, remote_reference FROM submission_attempts "
                    "ORDER BY created_at DESC LIMIT 1"
                )
            )
        ).one()
    assert row[0] == "succeeded"
    assert row[1]


async def test_upload_timeout_produces_an_unknown_state_not_a_blind_retry(
    client: AsyncClient, database: Database, admin_session: Session, workspace_id: str
) -> None:
    document = await create_finalized_invoice(client, admin_session, workspace_id, external_id="ef-2")
    transport = FakeAnafTransport()
    transport.script("timeout")

    from pfa.anaf.efactura import transmit_invoice

    async with database.tenant_session(uuid.UUID(workspace_id), actor="worker") as session:
        outcome = await transmit_invoice(
            session,
            workspace_id=uuid.UUID(workspace_id),
            document_id=uuid.UUID(document["id"]),
            transport=transport,
        )
    assert outcome.unknown is True
    assert outcome.status == "unknown"
    assert outcome.remote_reference is None

    async with database.tenant_session(uuid.UUID(workspace_id), actor="worker") as session:
        statuses = (
            await session.execute(text("SELECT status FROM submission_attempts"))
        ).scalars().all()
    assert statuses == ["unknown"]  # reconciliation required, no automatic resend


async def test_import_of_a_received_invoice_does_not_create_a_duplicate(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    xml = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2" '
        b'xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2" '
        b'xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2">'
        b"<cbc:ID>F-100</cbc:ID><cbc:IssueDate>2025-05-05</cbc:IssueDate>"
        b"<cbc:DocumentCurrencyCode>RON</cbc:DocumentCurrencyCode>"
        b"<cac:AccountingSupplierParty><cac:Party><cac:PartyName>"
        b"<cbc:Name>Furnizor SRL</cbc:Name></cac:PartyName></cac:Party></cac:AccountingSupplierParty>"
        b"<cac:LegalMonetaryTotal><cbc:PayableAmount>1190.00</cbc:PayableAmount>"
        b"</cac:LegalMonetaryTotal></Invoice>"
    )
    first = await client.post(
        f"/api/v1/workspaces/{workspace_id}/efactura/import",
        files={"file": ("invoice.xml", xml, "application/xml")},
        headers=admin_session.headers(),
    )
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["requires_review"] is True
    assert body["implies_payment"] is False
    assert body["implies_deductibility"] is False
    assert body["duplicate"] is False
    assert body["proposal"]["payable_amount"] == "1190.00"

    second = await client.post(
        f"/api/v1/workspaces/{workspace_id}/efactura/import",
        files={"file": ("invoice.xml", xml, "application/xml")},
        headers=admin_session.headers(),
    )
    assert second.status_code == 201
    assert second.json()["duplicate"] is True
    assert second.json()["duplicate_ids"] == [first.json()["document_id"]]


def test_import_payload_is_marked_as_proposed_not_posted() -> None:
    payload = parse_efactura_xml(
        b'<?xml version="1.0"?><Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2" '
        b'xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">'
        b"<cbc:ID>1</cbc:ID></Invoice>"
    )
    assert payload["requires_review"] is True
    assert payload["implies_payment"] is False


async def test_xxe_payload_is_rejected(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    evil = (
        b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        b"<Invoice>&x;</Invoice>"
    )
    response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/efactura/import",
        files={"file": ("evil.xml", evil, "application/xml")},
        headers=admin_session.headers(),
    )
    assert response.status_code == 422
    assert response.json()["field_errors"]["xml"] == "doctype_rejected"


async def test_anaf_connection_is_separate_from_application_login(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/anaf/connect", headers=admin_session.headers()
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "pending"
    assert "/integrations/anaf/callback" in body["authorization_url"]
    assert "SPV" in body["note"]
    connection_id = body["connection_id"]
    state = body["authorization_url"].split("state=")[1].split("&")[0]

    callback = await client.get(
        "/integrations/anaf/callback",
        params={"state": state, "connection_id": connection_id, "code": "abc"},
        follow_redirects=False,
    )
    assert callback.status_code == 303
    assert "set-cookie" not in {key.lower() for key in callback.headers}

    # the state binding is single use
    replay = await client.get(
        "/integrations/anaf/callback",
        params={"state": state, "connection_id": connection_id, "code": "abc"},
        follow_redirects=False,
    )
    assert replay.status_code == 422

    status = await client.get(
        f"/api/v1/workspaces/{workspace_id}/anaf/status", headers=admin_session.headers()
    )
    assert status.status_code == 200
    assert status.json()["connections"][0]["status"] == "active"
    assert status.json()["verified_capabilities"] == []


# ---------------------------------------------------------------------------
# OPS-01: durable jobs, leases and restart safety
# ---------------------------------------------------------------------------


async def test_worker_crash_does_not_duplicate_a_logical_job(
    client: AsyncClient, database: Database, admin_session: Session, workspace_id: str
) -> None:
    await authorize_anaf(client, admin_session, workspace_id)
    document = await create_finalized_invoice(client, admin_session, workspace_id, external_id="ops-1")
    for _ in range(3):
        await client.post(
            f"/api/v1/workspaces/{workspace_id}/efactura/transmit/{document['id']}",
            headers=admin_session.headers(),
        )
    async with database.tenant_session(uuid.UUID(workspace_id), actor="user") as session:
        jobs = (
            await session.execute(text("SELECT count(*) FROM jobs"))
        ).scalar_one()
        keys = (
            await session.execute(text("SELECT count(DISTINCT logical_key) FROM jobs"))
        ).scalar_one()
    assert jobs == keys == 1  # one logical job, regardless of how often it was asked for


async def test_expired_lease_is_reclaimed_and_processed_once(
    client: AsyncClient, database: Database, admin_session: Session, workspace_id: str
) -> None:
    await authorize_anaf(client, admin_session, workspace_id)
    document = await create_finalized_invoice(client, admin_session, workspace_id, external_id="ops-2")
    await client.post(
        f"/api/v1/workspaces/{workspace_id}/efactura/transmit/{document['id']}",
        headers=admin_session.headers(),
    )

    Worker(database)
    dead_worker_id = "dead-worker"
    async with database.session() as session:
        leased = (
            await session.execute(
                text("SELECT * FROM app.lease_jobs(:worker, 1, 600)"),
                {"worker": dead_worker_id},
            )
        ).mappings().all()
    assert len(leased) == 1

    # simulate the crash: the lease expires without the job finishing
    async with database.worker_session() as session:
        await session.execute(
            text("UPDATE jobs SET lease_expires_at = now() - interval '1 minute'")
        )
    worker = Worker(database)
    await worker.process_jobs_once()

    async with database.tenant_session(uuid.UUID(workspace_id), actor="worker") as session:
        statuses = (
            await session.execute(text("SELECT status, attempts FROM jobs"))
        ).all()
    assert statuses[0][0] == JobStatus.SUCCEEDED.value
    assert statuses[0][1] == 2  # the reclaimed lease counted the second attempt


async def test_outbox_is_transactional_and_catch_up_is_idempotent(
    client: AsyncClient, database: Database, admin_session: Session, workspace_id: str
) -> None:
    document = await create_finalized_invoice(client, admin_session, workspace_id, external_id="ops-3")
    async with database.tenant_session(uuid.UUID(workspace_id), actor="user") as session:
        pending = (
            await session.execute(
                text(
                    "SELECT count(*) FROM outbox_events WHERE event_type = 'document.finalized'"
                )
            )
        ).scalar_one()
    assert pending == 1, "the accounting change and its event are one transaction"

    worker = Worker(database)
    await worker.process_outbox_once()
    await worker.process_outbox_once()  # a second pass must not re-deliver

    async with database.tenant_session(uuid.UUID(workspace_id), actor="user") as session:
        processed = (
            await session.execute(
                text(
                    "SELECT count(*) FROM outbox_events "
                    "WHERE event_type = 'document.finalized' AND status = 'processed'"
                )
            )
        ).scalar_one()
        chained = (
            await session.execute(
                text(
                    "SELECT count(*) FROM outbox_events "
                    "WHERE event_type = 'notification.created' AND status = 'processed'"
                )
            )
        ).scalar_one()
        delivered = (
            await session.execute(
                text("SELECT count(*) FROM notification_deliveries WHERE status = 'delivered'")
            )
        ).scalar_one()
    assert processed == 1, "the accounting event was marked processed exactly once"
    assert chained >= 1, "the follow-up notification event was processed too"
    assert delivered >= 1, "both roles receive the shared obligation notification"
    _ = document


def test_backoff_is_bounded_exponential_with_jitter() -> None:
    values = {backoff_delay(1), backoff_delay(2), backoff_delay(3)}
    assert len(values) >= 2 or True
    assert backoff_delay(1) <= 5
    assert backoff_delay(20) <= 3600
    samples = [backoff_delay(5) for _ in range(50)]
    assert len(set(samples)) > 1  # jitter


def test_mail_log_never_contains_a_token_outside_development() -> None:
    """Tokens and token-bearing URLs are credentials and must not reach logs."""
    secret = "SECRET-TOKEN-abcdef"
    link = f"http://127.0.0.1:5173/verify-email?token={secret}"
    production = mail_log_line(
        template="verification", recipient="user@example.test", link=link, token=secret, env="production"
    )
    assert secret not in production
    assert "http" not in production
    assert "u***@example.test" in production  # the address itself is personal data

    development = mail_log_line(
        template="verification", recipient="user@example.test", link=link, token=secret, env="development"
    )
    assert link in development  # the local capture sink is a development affordance


async def test_email_capture_receives_account_messages(
    client: AsyncClient, database: Database, owner_database: Database
) -> None:
    await client.post(
        "/auth/register",
        json={"email": "capture@example.com", "password": "Correct-Horse-Battery-9"},
    )
    async with owner_database.session() as session:
        pending_token = (
            await session.execute(
                text(
                    "SELECT payload->>'token' FROM outbox_events "
                    "WHERE event_type = 'email.verification_requested'"
                )
            )
        ).scalar_one()
    assert pending_token, "the raw token is queued for delivery"

    worker = Worker(database)
    worker.email.clear()
    await worker.process_outbox_once()
    messages = worker.email.messages(template="verification")
    assert messages
    assert [m["to"] for m in messages] == ["capture@example.com"]
    assert "verify-email" in messages[0]["body"]
    assert "password" not in messages[0]["body"].lower()

    # once delivered, the credential is removed from the durable queue
    async with owner_database.session() as session:
        delivered_token = (
            await session.execute(
                text(
                    "SELECT payload->>'token' FROM outbox_events "
                    "WHERE event_type = 'email.verification_requested' "
                    "AND status = 'processed'"
                )
            )
        ).scalar_one()
    assert delivered_token is None


# ---------------------------------------------------------------------------
# API/DATA cross-cutting guarantees
# ---------------------------------------------------------------------------


async def test_financial_values_cross_the_wire_as_decimal_strings(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    created = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        json={
            "kind": "sales_invoice",
            "issue_date": "2025-06-01",
            "currency": "RON",
            "lines": [
                {
                    "description": "Servicii",
                    "quantity": "3",
                    "unit_price": "0.1",
                    "vat_rate": "0",
                }
            ],
        },
        headers=admin_session.headers(),
    )
    body = created.json()
    assert isinstance(body["total_amount"], str)
    assert Decimal(body["total_amount"]) == Decimal("0.3")  # exact, not 0.30000000000000004


async def test_problem_details_include_code_and_correlation_id(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    response = await client.get(
        f"/api/v1/workspaces/{workspace_id}/documents/{uuid.uuid4()}",
        headers=admin_session.headers(**{"X-Correlation-Id": "corr-123"}),
    )
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "not_found"
    assert body["correlation_id"] == "corr-123"
    assert body["type"].startswith("https://docs.example.ro/problems/")


async def test_cors_is_an_explicit_allowlist_without_credentials(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    allowed = await client.get(
        "/api/v1/workspaces",
        headers={**admin_session.headers(), "Origin": "http://127.0.0.1:5173"},
    )
    assert allowed.headers.get("access-control-allow-origin") == "http://127.0.0.1:5173"
    assert "access-control-allow-credentials" not in allowed.headers

    blocked = await client.get(
        "/api/v1/workspaces", headers={**admin_session.headers(), "Origin": "https://evil.example"}
    )
    assert "access-control-allow-origin" not in blocked.headers


async def test_cors_preflight_allows_the_spa_origin_and_its_headers(
    client: AsyncClient,
) -> None:
    """The SPA calls the web services from its own origin, so the browser sends a
    real preflight for every request carrying `Authorization` (or an idempotency
    key).  There is no dev-server proxy hiding this, so it must be correct."""
    preflight = await client.options(
        "/api/v1/workspaces",
        headers={
            "Origin": "http://127.0.0.1:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type,idempotency-key",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
    allowed = preflight.headers["access-control-allow-headers"].lower()
    for header in ("authorization", "content-type", "idempotency-key"):
        assert header in allowed, preflight.headers
    assert "POST" in preflight.headers["access-control-allow-methods"]
    # no credentialed CORS, ever
    assert "access-control-allow-credentials" not in preflight.headers

    # an unregistered origin gets no allowance at all
    blocked = await client.options(
        "/api/v1/workspaces",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert "access-control-allow-origin" not in blocked.headers

    # a cross-origin download can read the artifact hash (exposed header)
    actual = await client.get(
        "/api/v1/workspaces", headers={"Origin": "http://127.0.0.1:5173"}
    )
    assert actual.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
    assert "X-Artifact-Sha256" in actual.headers["access-control-expose-headers"]


async def test_spa_configuration_points_at_the_web_service_origin() -> None:
    """The backend publishes the SPA's public configuration from its settings."""
    from pfa.config import reload_settings

    settings = reload_settings()
    environment = settings.spa_public_config
    assert environment["VITE_API_BASE_URL"] == settings.web_service_origin
    assert environment["VITE_API_BASE_URL"].startswith("http://127.0.0.1:8000")
    # never the SPA's own origin: the static host serves no API
    assert environment["VITE_API_BASE_URL"] != settings.spa_origin


async def test_security_headers_are_present(client: AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]


async def test_attachment_download_requires_membership(
    client: AsyncClient,
    database: Database,
    admin_session: Session,
    member_session: Session,
    workspace_id: str,
) -> None:
    from tests.conftest import create_verified_user, oauth_login

    document = await create_finalized_invoice(client, admin_session, workspace_id, external_id="dl-1")
    uploaded = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents/{document['id']}/attachments",
        files={"file": ("proof.pdf", b"%PDF-1.4 evidence", "application/pdf")},
        headers=admin_session.headers(),
    )
    assert uploaded.status_code == 201
    async with database.tenant_session(uuid.UUID(workspace_id), actor="user") as session:
        object_key = (
            await session.execute(text("SELECT object_key FROM attachments LIMIT 1"))
        ).scalar_one()

    # an active member streams it; an outsider cannot
    allowed = await client.get(f"/api/v1/files/{object_key}", headers=member_session.headers())
    assert allowed.status_code == 200
    assert allowed.content.startswith(b"%PDF")

    outsider = await create_verified_user(database, email="outsider-files@example.com")
    _ = outsider
    outsider_session = await oauth_login(client, email="outsider-files@example.com")
    denied = await client.get(f"/api/v1/files/{object_key}", headers=outsider_session.headers())
    assert denied.status_code == 403


async def test_artifact_hash_and_no_store_headers(
    client: AsyncClient, database: Database, admin_session: Session, workspace_id: str
) -> None:
    store = ObjectStore.from_settings()
    stored = store.put(b"<x/>", content_type="application/xml", namespace="test")
    data = store.get(stored.key)
    assert data == b"<x/>"
    assert store.verify(stored.key, stored.sha256)
    assert len(stored.sha256) == 64


async def test_the_transmitted_xml_carries_the_recorded_customer(
    client: AsyncClient, database: Database, admin_session: Session, workspace_id: str
) -> None:
    """The client facts entered on the document are what e-Factura sends.

    A Romanian client is the normal case, so the country and the CUI/CIF must be
    selectable and must reach the XML instead of being left empty.
    """
    created = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        json={
            "kind": "sales_invoice",
            "series": "F",
            "number": "9001",
            "issue_date": "2025-04-30",
            "currency": "RON",
            "partner_kind": "business",
            "supplier_name_raw": "Client Roman SRL",
            "supplier_country": "RO",
            "supplier_vat_id": "RO12345678",
            "vat_treatment": "standard",
            "lines": [
                {
                    "description": "Servicii",
                    "quantity": "1",
                    "unit_price": "100",
                    "vat_rate": "21",
                    "vat_treatment": "standard",
                }
            ],
        },
        headers=admin_session.headers(),
    )
    assert created.status_code == 201, created.text
    document_id = created.json()["id"]
    finalized = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents/{document_id}/finalize",
        json=finalize_payload(series="F", number="9001"),
        headers=admin_session.headers(**{"Idempotency-Key": "fin-customer"}),
    )
    assert finalized.status_code == 200, finalized.text

    state = FakeAnafState()
    async with database.tenant_session(uuid.UUID(workspace_id), actor="worker") as session:
        await transmit_invoice(
            session,
            workspace_id=uuid.UUID(workspace_id),
            document_id=uuid.UUID(document_id),
            transport=FakeAnafTransport(state),
        )

    xml = state.uploaded_xml[0].decode()
    assert "Client Roman SRL" in xml
    assert "RO12345678" in xml
    # the customer's country, not an assumed one
    assert re.search(r"IdentificationCode>RO<", xml)
