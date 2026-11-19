"""Documents, settlements, assets and books.

Requirement IDs: DOCUMENTS, SETTLEMENTS, BOOKS, ACC-01, ACC-02, ACC-03, ACC-04.
"""

from __future__ import annotations

import itertools
import uuid
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy import text

from pfa.db import Database
from tests.conftest import Session, TenantFixture


def invoice_body(**overrides) -> dict:
    body = {
        "kind": "sales_invoice",
        "issue_date": "2026-03-10",
        "supply_date": "2026-03-10",
        "due_date": "2026-04-10",
        "currency": "RON",
        "vat_treatment": "undetermined",
        "lines": [
            {"description": "Servicii", "quantity": "1", "unit_price": "10000", "vat_rate": "0"}
        ],
    }
    body.update(overrides)
    return body


def expense_body(**overrides) -> dict:
    body = {
        "kind": "purchase_invoice",
        "supplier_name_raw": "Furnizor SRL",
        "issue_date": "2026-03-05",
        "currency": "RON",
        "vat_treatment": "undetermined",
        "deductibility": "partially_deductible",
        "business_use_percent": "50",
        "business_purpose": "Deplasare",
        "deductibility_justification": "Utilizare 50% în scop de afaceri",
        "expense_category": "vehicle",
        "lines": [
            {"description": "Combustibil", "quantity": "1", "unit_price": "2000", "vat_rate": "0"}
        ],
    }
    body.update(overrides)
    return body


async def create(client: AsyncClient, session: Session, workspace_id: str, body: dict) -> dict:
    response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents", json=body, headers=session.headers()
    )
    assert response.status_code == 201, response.text
    return response.json()


#: the series/number of a document is entered by the user, so tests supply one
TEST_SERIES = "TEST"
_numbers = itertools.count(1)


def next_document_number() -> str:
    return f"{next(_numbers):04d}"


async def finalize(
    client: AsyncClient,
    session: Session,
    workspace_id: str,
    document_id: str,
    *,
    key: str,
    number: str | None = None,
    series: str | None = None,
) -> dict:
    response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents/{document_id}/finalize",
        # ``series=""`` means "this document has no series", so only None takes the default
        json={
            "series": TEST_SERIES if series is None else series,
            "number": number or next_document_number(),
        },
        headers=session.headers(**{"Idempotency-Key": key}),
    )
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# DOCUMENTS
# ---------------------------------------------------------------------------


async def test_finalize_locks_the_document_under_the_number_the_user_entered(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    """The number is a fact of the issued paper: the user enters it, nothing invents it."""
    document = await create(client, admin_session, workspace_id, invoice_body())
    assert document["status"] == "draft"

    # a document without a number cannot be issued; the series is optional
    missing = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents/{document['id']}/finalize",
        json={},
        headers=admin_session.headers(**{"Idempotency-Key": "f-no-number"}),
    )
    assert missing.status_code == 409, missing.text
    assert missing.json()["details"]["missing_inputs"] == ["number"]

    finalized = await finalize(
        client, admin_session, workspace_id, document["id"], key="f-1", number="2026-000123"
    )
    assert finalized["status"] == "finalized"
    assert finalized["series"] == TEST_SERIES
    assert finalized["number"] == "2026-000123"

    # the same series/number cannot be issued twice in the workspace
    second = await create(client, admin_session, workspace_id, invoice_body(issue_date="2026-03-11"))
    duplicate = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents/{second['id']}/finalize",
        json={"series": TEST_SERIES, "number": "2026-000123"},
        headers=admin_session.headers(**{"Idempotency-Key": "f-dup"}),
    )
    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()["details"]["existing_document_id"] == document["id"]

    # an old document can be recorded at any time, under its own series and number
    old = await create(client, admin_session, workspace_id, invoice_body(issue_date="2024-01-15"))
    finalized_old = await finalize(
        client, admin_session, workspace_id, old["id"], key="f-old", series="A", number="77"
    )
    assert finalized_old["issue_date"] == "2024-01-15"
    assert (finalized_old["series"], finalized_old["number"]) == ("A", "77")


async def test_issued_invoices_are_immutable(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    document = await create(client, admin_session, workspace_id, invoice_body())
    await finalize(client, admin_session, workspace_id, document["id"], key="f-3")
    # a correction must be a linked credit note, not an overwrite
    note = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents/{document['id']}/credit-notes",
        json=invoice_body(issue_date="2026-03-20", currency="RON", lines=[
            {"description": "Storno", "quantity": "1", "unit_price": "10000", "vat_rate": "0"}
        ]),
        headers=admin_session.headers(),
    )
    assert note.status_code == 201, note.text
    body = note.json()
    assert body["kind"] == "credit_note"
    assert body["corrects_document_id"] == document["id"]
    assert body["status"] == "draft"


async def test_duplicate_documents_are_detected_with_a_review_path(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    body = invoice_body(external_id="ext-1")
    await create(client, admin_session, workspace_id, body)
    duplicate = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents", json=body, headers=admin_session.headers()
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["details"]["duplicates"]
    assert "review_path" in duplicate.json()["details"]

    # even after review, the unique import identifier stays unique in the tenant
    still_unique = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        json={**body, "allow_duplicate": True},
        headers=admin_session.headers(),
    )
    assert still_unique.status_code == 409

    # the reviewed exception path records the second document under its own key
    reviewed = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        json={
            **body,
            "external_id": "ext-1-reviewed",
            "series": TEST_SERIES,
            "number": next_document_number(),
            "allow_duplicate": True,
        },
        headers=admin_session.headers(),
    )
    assert reviewed.status_code == 201


async def test_a_deleted_document_does_not_block_recording_it_again(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    """Deleting removes the document; recording it again must then be possible."""
    body = invoice_body(external_id="ext-deleted-1")
    first = await create(client, admin_session, workspace_id, body)

    # while it still counts, the same content is a duplicate
    duplicate = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents", json=body, headers=admin_session.headers()
    )
    assert duplicate.status_code == 409

    deleted = await client.delete(
        f"/api/v1/workspaces/{workspace_id}/documents/{first['id']}",
        headers=admin_session.headers(),
    )
    assert deleted.status_code == 200, deleted.text

    # the deleted row neither duplicates nor keeps the import key it was holding
    again = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents", json=body, headers=admin_session.headers()
    )
    assert again.status_code == 201, again.text
    assert again.json()["id"] != first["id"]


async def test_idempotency_key_returns_the_original_result(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    document = await create(client, admin_session, workspace_id, invoice_body())
    payload = {"series": TEST_SERIES, "number": next_document_number()}
    first = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents/{document['id']}/finalize",
        json=payload,
        headers=admin_session.headers(**{"Idempotency-Key": "same-key"}),
    )
    assert first.status_code == 200, first.text
    second = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents/{document['id']}/finalize",
        json=payload,
        headers=admin_session.headers(**{"Idempotency-Key": "same-key"}),
    )
    assert second.status_code == 200
    assert second.json()["number"] == first.json()["number"]


async def test_finalize_requires_an_idempotency_key(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    document = await create(client, admin_session, workspace_id, invoice_body())
    response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents/{document['id']}/finalize",
        headers=admin_session.headers(),
    )
    assert response.status_code == 422
    assert "Idempotency-Key" in response.json()["field_errors"]


async def test_stale_edit_is_rejected_with_a_conflict(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    document = await create(client, admin_session, workspace_id, invoice_body())
    response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents/{document['id']}/finalize",
        params={"expected_revision": 99},
        headers=admin_session.headers(**{"Idempotency-Key": "stale-1"}),
    )
    assert response.status_code == 409
    assert response.json()["code"] == "stale_revision"


async def test_attachment_types_are_restricted(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    document = await create(client, admin_session, workspace_id, expense_body())
    ok = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents/{document['id']}/attachments",
        files={"file": ("invoice.pdf", b"%PDF-1.4 test", "application/pdf")},
        headers=admin_session.headers(),
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["scan_status"] == "clean"

    rejected = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents/{document['id']}/attachments",
        files={"file": ("script.html", b"<script>alert(1)</script>", "text/html")},
        headers=admin_session.headers(),
    )
    assert rejected.status_code == 422
    assert rejected.json()["field_errors"]["content_type"] == "unsupported_type"

    xxe = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents/{document['id']}/attachments",
        files={
            "file": (
                "evil.xml",
                b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><r>&x;</r>',
                "application/xml",
            )
        },
        headers=admin_session.headers(),
    )
    assert xxe.status_code == 201  # stored for review, flagged by the scanner
    assert xxe.json()["scan_status"] in {"clean", "flagged"}


# ---------------------------------------------------------------------------
# SETTLEMENTS and ACC-01/ACC-03
# ---------------------------------------------------------------------------


async def test_partial_collections_across_two_years_are_separate_events(
    client: AsyncClient, database: Database, admin_session: Session, workspace_id: str
) -> None:
    """ACC-01: RON 10.000 collected 4.000 in December and 6.000 in January."""
    document = await create(client, admin_session, workspace_id, invoice_body())
    await finalize(client, admin_session, workspace_id, document["id"], key="acc01")

    first = await client.post(
        f"/api/v1/workspaces/{workspace_id}/payments",
        json={
            "direction": "inflow",
            "kind": "customer_receipt",
            "paid_on": "2026-12-20",
            "amount": "4000",
            "reference": "OP-1",
            "allocations": [{"document_id": document["id"], "amount": "4000"}],
        },
        headers=admin_session.headers(),
    )
    assert first.status_code == 201, first.text
    second = await client.post(
        f"/api/v1/workspaces/{workspace_id}/payments",
        json={
            "direction": "inflow",
            "kind": "customer_receipt",
            "paid_on": "2027-01-15",
            "amount": "6000",
            "reference": "OP-2",
            "allocations": [{"document_id": document["id"], "amount": "6000"}],
        },
        headers=admin_session.headers(),
    )
    assert second.status_code == 201, second.text

    summary = await client.get(
        f"/api/v1/workspaces/{workspace_id}/documents/{document['id']}/settlement",
        headers=admin_session.headers(),
    )
    assert summary.status_code == 200
    assert Decimal(summary.json()["settled"]) == Decimal("10000")
    assert Decimal(summary.json()["outstanding"]) == Decimal("0")

    # the cash journal attributes each collection to its own year
    async with database.tenant_session(uuid.UUID(workspace_id), actor="user") as session:
        events = (
            await session.execute(
                text(
                    "SELECT event_date, taxable_amount_ron FROM accounting_events "
                    "WHERE event_kind = 'revenue' AND recognition = 'realization' "
                    "ORDER BY event_date"
                )
            )
        ).all()
    assert [str(row[0]) for row in events] == ["2026-12-20", "2027-01-15"]
    assert [Decimal(row[1]) for row in events] == [Decimal("4000"), Decimal("6000")]


async def test_over_allocation_is_rejected(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    document = await create(client, admin_session, workspace_id, invoice_body())
    await finalize(client, admin_session, workspace_id, document["id"], key="over-1")
    response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/payments",
        json={
            "direction": "inflow",
            "kind": "customer_receipt",
            "paid_on": "2026-03-30",
            "amount": "12000",
            "allocations": [{"document_id": document["id"], "amount": "12000"}],
        },
        headers=admin_session.headers(),
    )
    assert response.status_code == 409
    assert "diferență de curs" in response.json()["details"]["hint"]

    # an advance is modelled explicitly instead
    advance = await client.post(
        f"/api/v1/workspaces/{workspace_id}/payments",
        json={
            "direction": "inflow",
            "kind": "advance",
            "paid_on": "2026-03-30",
            "amount": "12000",
            "allocations": [
                {
                    "document_id": document["id"],
                    "amount": "12000",
                    "allocation_kind": "advance",
                }
            ],
        },
        headers=admin_session.headers(),
    )
    assert advance.status_code == 201, advance.text


async def test_owner_loan_and_contribution_are_not_taxable_income(
    client: AsyncClient, database: Database, admin_session: Session, workspace_id: str
) -> None:
    """ACC-03: money moving is not revenue."""
    for kind, amount in (("owner_contribution", "5000"), ("loan_received", "20000")):
        response = await client.post(
            f"/api/v1/workspaces/{workspace_id}/payments",
            json={
                "direction": "inflow",
                "kind": kind,
                "paid_on": "2026-04-01",
                "amount": amount,
                "reference": f"OP-{kind}",
            },
            headers=admin_session.headers(),
        )
        assert response.status_code == 201, response.text
        assert response.json()["is_owner_movement"] is True

    # visible in the cash journal
    journal = await client.get(
        f"/api/v1/workspaces/{workspace_id}/registers/cash_journal",
        params={"period_start": "2026-01-01", "period_end": "2026-12-31"},
        headers=admin_session.headers(),
    )
    assert journal.status_code == 200
    assert len(journal.json()["rows"]) == 2

    async with database.tenant_session(uuid.UUID(workspace_id), actor="user") as session:
        taxable = (
            await session.execute(
                text(
                    "SELECT coalesce(sum(taxable_amount_ron), 0) FROM accounting_events "
                    "WHERE event_kind = 'revenue'"
                )
            )
        ).scalar_one()
    assert Decimal(taxable) == 0


async def test_bank_import_does_not_double_count_a_settlement(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    payload = {
        "direction": "inflow",
        "kind": "customer_receipt",
        "paid_on": "2026-05-05",
        "amount": "1500",
        "external_id": "bank-row-1",
    }
    first = await client.post(
        f"/api/v1/workspaces/{workspace_id}/payments", json=payload, headers=admin_session.headers()
    )
    second = await client.post(
        f"/api/v1/workspaces/{workspace_id}/payments", json=payload, headers=admin_session.headers()
    )
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]

    listing = await client.get(
        f"/api/v1/workspaces/{workspace_id}/payments", headers=admin_session.headers()
    )
    assert len(listing.json()["items"]) == 1


async def test_foreign_currency_payment_requires_a_rate(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/payments",
        json={
            "direction": "outflow",
            "kind": "supplier_payment",
            "paid_on": "2026-06-01",
            "amount": "1000",
            "currency": "EUR",
        },
        headers=admin_session.headers(),
    )
    assert response.status_code == 409
    assert "exchange_rate" in response.json()["details"]["missing_inputs"]


# ---------------------------------------------------------------------------
# ACC-04: assets
# ---------------------------------------------------------------------------


async def test_asset_creates_a_depreciation_schedule_and_blocks_double_deduction(
    client: AsyncClient, database: Database, admin_session: Session, workspace_id: str
) -> None:
    expense = await create(client, admin_session, workspace_id, expense_body(expense_category="computers"))
    await finalize(client, admin_session, workspace_id, expense["id"], key="asset-doc")

    asset = await client.post(
        f"/api/v1/workspaces/{workspace_id}/assets",
        json={
            "name": "Laptop",
            "category": "computers",
            "acquisition_date": "2026-03-05",
            "in_service_date": "2026-03-05",
            "acquisition_cost": "6000",
            "acquisition_cost_ron": "6000",
            "business_use_percent": "100",
            "document_id": expense["id"],
        },
        headers=admin_session.headers(),
    )
    assert asset.status_code == 201, asset.text
    assert asset.json()["useful_life_months"] == 36

    async with database.tenant_session(uuid.UUID(workspace_id), actor="user") as session:
        entries = (
            await session.execute(
                text("SELECT count(*), coalesce(sum(amount), 0) FROM depreciation_entries")
            )
        ).one()
        adjustments = (
            await session.execute(
                text(
                    "SELECT adjustment_kind, direction FROM tax_adjustments "
                    "WHERE adjustment_kind = 'capitalized_expense_reversal'"
                )
            )
        ).all()
    assert entries[0] == 36
    assert Decimal(entries[1]) == Decimal("6000")
    assert adjustments == [("capitalized_expense_reversal", "increase")]


async def test_asset_disposal_updates_the_schedule(
    client: AsyncClient, database: Database, admin_session: Session, workspace_id: str
) -> None:
    asset = await client.post(
        f"/api/v1/workspaces/{workspace_id}/assets",
        json={
            "name": "Utilaj",
            "category": "equipment",
            "acquisition_date": "2026-01-10",
            "in_service_date": "2026-01-10",
            "acquisition_cost": "12000",
            "acquisition_cost_ron": "12000",
            "business_use_percent": "100",
            "useful_life_months": 12,
        },
        headers=admin_session.headers(),
    )
    asset_id = asset.json()["id"]
    disposal = await client.post(
        f"/api/v1/workspaces/{workspace_id}/assets/{asset_id}/dispose",
        json={"disposal_date": "2026-07-15", "proceeds": "9000", "notes": "Vândut"},
        headers=admin_session.headers(),
    )
    assert disposal.status_code == 200, disposal.text
    assert disposal.json()["status"] == "disposed"

    async with database.tenant_session(uuid.UUID(workspace_id), actor="user") as session:
        remaining = (
            await session.execute(text("SELECT count(*) FROM depreciation_entries"))
        ).scalar_one()
        result = (
            await session.execute(
                text(
                    "SELECT adjustment_kind FROM tax_adjustments "
                    "WHERE adjustment_kind = 'asset_disposal_result'"
                )
            )
        ).scalar_one()
    assert remaining == 6  # February..July, the disposal month is the last one
    assert result == "asset_disposal_result"


# ---------------------------------------------------------------------------
# BOOKS: registers, close and reopen
# ---------------------------------------------------------------------------


async def test_registers_and_period_close_and_reopen(
    client: AsyncClient, database: Database, admin_session: Session, workspace_id: str
) -> None:
    document = await create(client, admin_session, workspace_id, invoice_body())
    await finalize(client, admin_session, workspace_id, document["id"], key="books-1")

    checklist = await client.get(
        f"/api/v1/workspaces/{workspace_id}/registers/close-checklist",
        params={"period_start": "2026-03-01", "period_end": "2026-03-31"},
        headers=admin_session.headers(),
    )
    assert checklist.status_code == 200
    assert checklist.json()["ready_to_close"] is True

    closed = await client.post(
        f"/api/v1/workspaces/{workspace_id}/periods/close",
        json={"period_start": "2026-03-01", "period_end": "2026-03-31"},
        headers=admin_session.headers(),
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["is_open"] is False

    # ordinary edits in a closed period are blocked
    blocked = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        json=invoice_body(issue_date="2026-03-15"),
        headers=admin_session.headers(),
    )
    assert blocked.status_code == 409

    # reopening requires a documented reason and creates a new revision
    too_short = await client.post(
        f"/api/v1/workspaces/{workspace_id}/periods/{closed.json()['id']}/reopen",
        json={"reason": "scurt"},
        headers=admin_session.headers(),
    )
    assert too_short.status_code == 422  # schema-level minimum length
    blank_reason = await client.post(
        f"/api/v1/workspaces/{workspace_id}/periods/{closed.json()['id']}/reopen",
        json={"reason": " " * 12},
        headers=admin_session.headers(),
    )
    assert blank_reason.status_code == 409  # service-level guard on meaningful text

    reopened = await client.post(
        f"/api/v1/workspaces/{workspace_id}/periods/{closed.json()['id']}/reopen",
        json={"reason": "Corectare document primit tardiv de la furnizor"},
        headers=admin_session.headers(),
    )
    assert reopened.status_code == 200
    assert reopened.json()["revision"] == 2

    allowed = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        json=invoice_body(issue_date="2026-03-15", external_id="late-1"),
        headers=admin_session.headers(),
    )
    assert allowed.status_code == 201


async def test_register_export_runs_asynchronously(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    await create(client, admin_session, workspace_id, expense_body())
    response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/registers/purchase_vat_journal/export",
        params={"period_start": "2026-01-01", "period_end": "2026-12-31"},
        headers=admin_session.headers(),
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]

    job = await client.get(f"/api/v1/jobs/{job_id}", headers=admin_session.headers())
    assert job.status_code == 200
    assert job.json()["kind"] == "register.export"


async def test_document_list_includes_the_line_description(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    """The list shows what the document is about, aggregated from its lines."""
    body = invoice_body(
        lines=[
            {"description": "Consultanță", "quantity": "1", "unit_price": "1000", "vat_rate": "0"},
            {"description": "Suport", "quantity": "1", "unit_price": "500", "vat_rate": "0"},
        ]
    )
    created = await create(client, admin_session, workspace_id, body)
    listing = await client.get(
        f"/api/v1/workspaces/{workspace_id}/documents",
        params={"kind": "sales_invoice"},
        headers=admin_session.headers(),
    )
    assert listing.status_code == 200, listing.text
    item = next(row for row in listing.json()["items"] if row["id"] == created["id"])
    assert item["description"] == "Consultanță, Suport"


async def test_document_list_includes_the_expense_classification(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    """The expense list shows the category and deductibility, not blanks."""
    created = await create(client, admin_session, workspace_id, expense_body())
    listing = await client.get(
        f"/api/v1/workspaces/{workspace_id}/documents",
        params={"kind": "purchase_invoice"},
        headers=admin_session.headers(),
    )
    assert listing.status_code == 200, listing.text
    item = next(row for row in listing.json()["items"] if row["id"] == created["id"])
    assert item["expense_category"] == "vehicle"
    assert item["deductibility"] == "partially_deductible"
    assert item["business_use_percent"] == "50.000000"


async def test_taxable_document_requires_its_vat_rate(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    """'standard' with no rate would hold no VAT while the declaration applies 21%."""
    body = invoice_body(
        vat_treatment="standard",
        lines=[
            {
                "description": "Servicii",
                "quantity": "1",
                "unit_price": "1000",
                "vat_rate": "0",
                "vat_treatment": "standard",
            }
        ],
    )
    response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        json=body,
        headers=admin_session.headers(),
    )
    assert response.status_code == 422, response.text
    assert "cot" in response.json()["title"].lower()
    assert response.json()["field_errors"]["lines"] == "vat_rate_required"

    # with the rate on the invoice the document is consistent and accepted
    body["lines"][0]["vat_rate"] = "21"
    accepted = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        json=body,
        headers=admin_session.headers(),
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["total_vat"] == "210.000000"


async def test_draft_can_be_edited_and_deleted(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    created = await create(client, admin_session, workspace_id, invoice_body())
    assert created["status"] == "draft"

    edited_body = invoice_body(
        lines=[{"description": "Modificat", "quantity": "1", "unit_price": "500", "vat_rate": "0"}]
    )
    edited = await client.patch(
        f"/api/v1/workspaces/{workspace_id}/documents/{created['id']}",
        json={**edited_body, "expected_revision": created["revision"]},
        headers=admin_session.headers(),
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["revision"] == created["revision"] + 1
    assert edited.json()["total_amount"] == "500.000000"
    detail = await client.get(
        f"/api/v1/workspaces/{workspace_id}/documents/{created['id']}",
        headers=admin_session.headers(),
    )
    assert detail.json()["lines"][0]["description"] == "Modificat"

    # a stale revision (someone else saved in the meantime) is rejected
    stale = await client.patch(
        f"/api/v1/workspaces/{workspace_id}/documents/{created['id']}",
        json={**edited_body, "expected_revision": created["revision"]},
        headers=admin_session.headers(),
    )
    assert stale.status_code == 409

    deleted = await client.delete(
        f"/api/v1/workspaces/{workspace_id}/documents/{created['id']}",
        headers=admin_session.headers(),
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted"] is True
    # deleting twice is a 404: the row is gone, not retired
    again = await client.delete(
        f"/api/v1/workspaces/{workspace_id}/documents/{created['id']}",
        headers=admin_session.headers(),
    )
    assert again.status_code == 404


async def test_an_issued_document_can_be_edited_with_its_previous_state_kept(
    client: AsyncClient,
    admin_session: Session,
    workspace_id: str,
    database: Database,
    tenant: TenantFixture,
) -> None:
    """The series, number and dates are the user's facts, so an issued document can be
    corrected — but never silently: the state before the edit is kept."""
    created = await create(client, admin_session, workspace_id, invoice_body())
    finalized = await finalize(
        client, admin_session, workspace_id, created["id"], key="fin-edit-issued"
    )

    edited = await client.patch(
        f"/api/v1/workspaces/{workspace_id}/documents/{created['id']}",
        json={
            **invoice_body(
                series=finalized["series"],
                number=finalized["number"],
                lines=[
                    {"description": "Corectat", "quantity": "1", "unit_price": "750", "vat_rate": "0"}
                ],
            ),
            "expected_revision": finalized["revision"],
        },
        headers=admin_session.headers(),
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["status"] == "finalized"  # stays issued
    assert edited.json()["revision"] == finalized["revision"] + 1
    assert edited.json()["total_amount"] == "750.000000"
    assert edited.json()["number"] == finalized["number"]

    async with database.tenant_session(tenant.workspace_id, actor="user") as session:
        kept = (
            await session.execute(
                text(
                    "SELECT snapshot FROM document_revisions "
                    "WHERE document_id = :d AND reason = 'issued_edit'"
                ),
                {"d": created["id"]},
            )
        ).scalar_one_or_none()
    assert kept is not None, "the previous state must stay in the append-only history"
    assert kept["total_amount"] == "10000.000000"
    assert kept["lines"][0]["description"] == "Servicii"


async def test_an_issued_document_inside_a_filed_declaration_cannot_be_edited(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    """Editing what a generated return already reports would falsify the return."""
    await client.patch(
        f"/api/v1/workspaces/{workspace_id}/fiscal-profile",
        json={
            "vat_registrations": [
                {
                    "kind": "article_316",
                    "vat_code": "RO90000018",
                    "effective_from": "2025-01-01",
                    "reporting_frequency": "monthly",
                }
            ]
        },
        headers=admin_session.headers(),
    )
    created = await create(
        client, admin_session, workspace_id, invoice_body(issue_date="2025-09-10")
    )
    finalized = await finalize(
        client, admin_session, workspace_id, created["id"], key="fin-edit-locked"
    )
    declaration = await client.post(
        f"/api/v1/workspaces/{workspace_id}/declarations",
        json={
            "form_code": "D300",
            "income_year": 2025,
            "period_start": "2025-09-01",
            "period_end": "2025-09-30",
        },
        headers=admin_session.headers(),
    )
    assert declaration.status_code == 201, declaration.text
    calculated = await client.post(
        f"/api/v1/declarations/{declaration.json()['id']}/calculate",
        headers=admin_session.headers(**{"Idempotency-Key": "calc-edit-locked"}),
    )
    assert calculated.status_code == 200, calculated.text
    # a generated artifact is what makes the figures final for the period
    exported = await client.post(
        f"/api/v1/declarations/{declaration.json()['id']}/export",
        json={},
        headers=admin_session.headers(**{"Idempotency-Key": "exp-edit-locked"}),
    )
    assert exported.status_code == 200, exported.text

    blocked = await client.patch(
        f"/api/v1/workspaces/{workspace_id}/documents/{created['id']}",
        json={
            **invoice_body(issue_date="2025-09-10", series=finalized["series"], number=finalized["number"]),
            "expected_revision": finalized["revision"],
        },
        headers=admin_session.headers(),
    )
    assert blocked.status_code == 409, blocked.text
    assert "declarație generată" in blocked.json()["title"]


async def test_deleting_a_document_leaves_the_audit_trail_and_releases_money(
    client: AsyncClient,
    admin_session: Session,
    workspace_id: str,
    database: Database,
    tenant: TenantFixture,
) -> None:
    """Delete is a real removal: lines, settlements and the number go with it, and the
    append-only audit event is what remains of the document."""
    document = await create(client, admin_session, workspace_id, invoice_body())
    finalized = await finalize(
        client, admin_session, workspace_id, document["id"], key="fin-delete-audit"
    )
    receipt = await client.post(
        f"/api/v1/workspaces/{workspace_id}/payments",
        json={
            "direction": "inflow",
            "kind": "customer_receipt",
            "paid_on": "2026-03-20",
            "amount": "4000",
            "method": "bank",
            "allocations": [{"document_id": document["id"], "amount": "4000"}],
        },
        headers=admin_session.headers(),
    )
    assert receipt.status_code == 201, receipt.text

    await client.delete(
        f"/api/v1/workspaces/{workspace_id}/documents/{document['id']}",
        headers=admin_session.headers(),
    )

    async with database.tenant_session(tenant.workspace_id, actor="user") as session:
        lines = await session.scalar(
            text("SELECT count(*) FROM document_lines WHERE document_id = :d"),
            {"d": document["id"]},
        )
        allocations = await session.scalar(
            text("SELECT count(*) FROM payment_allocations WHERE document_id = :d"),
            {"d": document["id"]},
        )
        payments = await session.scalar(
            text("SELECT count(*) FROM payments WHERE workspace_id = :w"),
            {"w": tenant.workspace_id},
        )
        audit = await session.execute(
            text(
                "SELECT before, after FROM audit_events "
                "WHERE entity_id = :d AND action = 'document.deleted'"
            ),
            {"d": document["id"]},
        )
        row = audit.one()
    assert lines == 0
    # the settlement was recorded *for* the document, so it is deleted with it
    assert allocations == 0
    assert payments == 0
    # the deleted content is preserved in the append-only event
    assert row.before["number"] == finalized["number"]
    assert row.after["allocations_removed"] == 1


async def test_deleting_a_document_from_a_year_without_a_rule_pack(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    """An old invoice can be recorded and deleted even when no rule pack covers its year."""
    document = await create(
        client, admin_session, workspace_id, invoice_body(issue_date="2019-05-06")
    )
    finalized = await finalize(
        client, admin_session, workspace_id, document["id"], key="fin-old-year"
    )
    assert finalized["issue_date"] == "2019-05-06"

    deleted = await client.delete(
        f"/api/v1/workspaces/{workspace_id}/documents/{document['id']}",
        headers=admin_session.headers(),
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted"] is True


async def test_a_supplier_number_is_only_unique_per_supplier(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    """Two suppliers may both send an invoice numbered "1"; ours may not repeat."""
    first = await create(
        client,
        admin_session,
        workspace_id,
        expense_body(supplier_name_raw="Alfa SRL", number="1", issue_date="2026-04-02"),
    )
    second = await create(
        client,
        admin_session,
        workspace_id,
        expense_body(supplier_name_raw="Beta SRL", number="1", issue_date="2026-04-02"),
    )
    assert first["number"] == second["number"] == "1"

    # the same supplier's invoice, same number and date, is the same document
    duplicate = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        json=expense_body(supplier_name_raw="Alfa SRL", number="1", issue_date="2026-04-02"),
        headers=admin_session.headers(),
    )
    assert duplicate.status_code == 409, duplicate.text

    # our own invoices are unique per workspace from the moment they are recorded
    invoice = await create(
        client, admin_session, workspace_id, invoice_body(number="7", series="F")
    )
    assert invoice["number"] == "7"
    repeated = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        json=invoice_body(number="7", series="F", issue_date="2026-04-03"),
        headers=admin_session.headers(),
    )
    assert repeated.status_code == 409, repeated.text
    assert "seria" in repeated.json()["title"].lower()


async def test_an_expense_never_collides_with_a_sale_that_shares_its_number(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    """Duplicate detection compares the same kind of document.

    An expense and a sale that happen to share a series, a number, a date and a partner
    name are different documents — and the expense list cannot even show the sale that
    would otherwise "duplicate" it.
    """
    sale = {
        "kind": "sales_invoice",
        "series": "A",
        "number": "123",
        "supplier_name_raw": "GEBS",
        "issue_date": "2026-09-20",
        "currency": "RON",
        "vat_treatment": "standard",
        "lines": [
            {
                "description": "Servicii prestate",
                "quantity": "1",
                "unit_price": "1000",
                "vat_rate": "21",
                "vat_treatment": "standard",
            }
        ],
    }
    created = await create(client, admin_session, workspace_id, sale)

    expense = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        json={
            **sale,
            "kind": "purchase_invoice",
            "lines": [
                {
                    "description": "Achiziție",
                    "quantity": "1",
                    "unit_price": "100",
                    "vat_rate": "21",
                    "vat_treatment": "standard",
                }
            ],
        },
        headers=admin_session.headers(),
    )
    assert expense.status_code == 201, expense.text
    assert expense.json()["kind"] == "purchase_invoice"

    # the same expense again is still a duplicate: same kind, partner, series, number and
    # date, even when the amounts differ (so the content hash alone would not catch it)
    repeated = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        json={
            **sale,
            "kind": "purchase_invoice",
            "lines": [
                {
                    "description": "Achiziție",
                    "quantity": "1",
                    "unit_price": "250",
                    "vat_rate": "21",
                    "vat_treatment": "standard",
                }
            ],
        },
        headers=admin_session.headers(),
    )
    assert repeated.status_code == 409, repeated.text
    assert repeated.json()["details"]["duplicates"][0]["id"] != created["id"]

    # a different series is a different document, even for the same supplier and number
    other_series = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        json={
            **sale,
            "kind": "purchase_invoice",
            "series": "B",
            "lines": [
                {
                    "description": "Achiziție",
                    "quantity": "1",
                    "unit_price": "100",
                    "vat_rate": "21",
                    "vat_treatment": "standard",
                }
            ],
        },
        headers=admin_session.headers(),
    )
    assert other_series.status_code == 201, other_series.text


async def test_a_transmitted_invoice_cannot_be_edited(
    client: AsyncClient,
    admin_session: Session,
    workspace_id: str,
    database: Database,
    tenant: TenantFixture,
) -> None:
    """What already left through e-Factura cannot be changed by editing the document."""
    import uuid as uuid_module

    from pfa.anaf import FakeAnafState, FakeAnafTransport
    from pfa.anaf.efactura import transmit_invoice

    created = await create(client, admin_session, workspace_id, invoice_body())
    finalized = await finalize(
        client, admin_session, workspace_id, created["id"], key="fin-transmitted-edit"
    )
    async with database.tenant_session(tenant.workspace_id, actor="worker") as session:
        await transmit_invoice(
            session,
            workspace_id=uuid_module.UUID(workspace_id),
            document_id=uuid_module.UUID(created["id"]),
            transport=FakeAnafTransport(FakeAnafState()),
        )

    blocked = await client.patch(
        f"/api/v1/workspaces/{workspace_id}/documents/{created['id']}",
        json={
            **invoice_body(series=finalized["series"], number=finalized["number"]),
            "expected_revision": finalized["revision"],
        },
        headers=admin_session.headers(),
    )
    assert blocked.status_code == 409, blocked.text
    assert "e-Factura" in blocked.json()["title"]
    assert "notă de credit" in blocked.json()["title"]


async def test_an_over_settled_document_is_reported_not_hidden(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    """Lowering a document below what is already settled is a real state, so it is said."""
    created = await create(client, admin_session, workspace_id, invoice_body())
    finalized = await finalize(
        client, admin_session, workspace_id, created["id"], key="fin-over-settled"
    )
    receipt = await client.post(
        f"/api/v1/workspaces/{workspace_id}/payments",
        json={
            "direction": "inflow",
            "kind": "customer_receipt",
            "paid_on": "2026-03-20",
            "amount": "6000",
            "method": "bank",
            "allocations": [{"document_id": created["id"], "amount": "6000"}],
        },
        headers=admin_session.headers(),
    )
    assert receipt.status_code == 201, receipt.text

    # the invoice is edited down to 500, below the receipt already allocated to it
    edited = await client.patch(
        f"/api/v1/workspaces/{workspace_id}/documents/{created['id']}",
        json={
            **invoice_body(
                series=finalized["series"],
                number=finalized["number"],
                lines=[
                    {"description": "Redus", "quantity": "1", "unit_price": "500", "vat_rate": "0"}
                ],
            ),
            "expected_revision": finalized["revision"],
        },
        headers=admin_session.headers(),
    )
    assert edited.status_code == 200, edited.text

    summary = await client.get(
        f"/api/v1/workspaces/{workspace_id}/documents/{created['id']}/settlement",
        headers=admin_session.headers(),
    )
    assert summary.status_code == 200, summary.text
    body = summary.json()
    assert body["total"] == "500.000000"
    assert body["settled"] == "6000.000000"
    assert body["over_settled"] is True
    assert body["over_amount"] == "5500.000000"


async def test_a_recorded_receipt_can_be_corrected_and_removed(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    """Recording a settlement is correctable: the allocation follows the amount, and
    removing the settlement releases the document."""
    created = await create(client, admin_session, workspace_id, invoice_body())
    await finalize(client, admin_session, workspace_id, created["id"], key="fin-settlement-edit")
    receipt = await client.post(
        f"/api/v1/workspaces/{workspace_id}/payments",
        json={
            "direction": "inflow",
            "kind": "customer_receipt",
            "paid_on": "2026-03-20",
            "amount": "4000",
            "method": "bank",
            "reference": "OP 1",
            "allocations": [{"document_id": created["id"], "amount": "4000"}],
        },
        headers=admin_session.headers(),
    )
    assert receipt.status_code == 201, receipt.text
    payment_id = receipt.json()["id"]

    listed = (
        await client.get(f"/api/v1/workspaces/{workspace_id}/payments", headers=admin_session.headers())
    ).json()["items"]
    entry = next(item for item in listed if item["id"] == payment_id)
    assert entry["allocations"][0]["document_id"] == created["id"]
    assert entry["allocations"][0]["amount"] == "4000.000000"

    corrected = await client.patch(
        f"/api/v1/workspaces/{workspace_id}/payments/{payment_id}",
        json={"amount": "6000", "reference": "OP 1 corectat"},
        headers=admin_session.headers(),
    )
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["amount"] == "6000.000000"
    # a single allocation follows the corrected amount
    assert corrected.json()["allocations"][0]["amount"] == "6000.000000"
    dashboard = await client.get(
        f"/api/v1/workspaces/{workspace_id}/dashboard",
        params={"income_year": 2026}, headers=admin_session.headers(),
    )
    assert dashboard.json()["collected_revenue"] == "6000.00"

    summary = (
        await client.get(
            f"/api/v1/workspaces/{workspace_id}/documents/{created['id']}/settlement",
            headers=admin_session.headers(),
        )
    ).json()
    assert summary["settled"] == "6000.000000"
    assert summary["outstanding"] == "4000.000000"
    assert summary["over_settled"] is False

    # more than the invoice is worth is still refused, counting only the other allocations
    too_much = await client.patch(
        f"/api/v1/workspaces/{workspace_id}/payments/{payment_id}",
        json={"amount": "20000"},
        headers=admin_session.headers(),
    )
    assert too_much.status_code == 409, too_much.text

    removed = await client.delete(
        f"/api/v1/workspaces/{workspace_id}/payments/{payment_id}",
        headers=admin_session.headers(),
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["deleted"] is True
    dashboard = await client.get(
        f"/api/v1/workspaces/{workspace_id}/dashboard",
        params={"income_year": 2026}, headers=admin_session.headers(),
    )
    assert dashboard.json()["collected_revenue"] == "0.00"
    summary = (
        await client.get(
            f"/api/v1/workspaces/{workspace_id}/documents/{created['id']}/settlement",
            headers=admin_session.headers(),
        )
    ).json()
    assert summary["settled"] == "0"
    assert summary["over_settled"] is False


async def test_the_detail_endpoint_returns_the_reverse_charge_justification(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    """What the user entered must come back, or the edit form shows an empty field."""
    created = await create(
        client,
        admin_session,
        workspace_id,
        expense_body(
            vat_treatment="reverse_charge",
            supplier_country="DE",
            reverse_charge_justification="achiziție intracomunitară de servicii, art. 331",
        ),
    )
    detail = await client.get(
        f"/api/v1/workspaces/{workspace_id}/documents/{created['id']}",
        headers=admin_session.headers(),
    )
    assert detail.status_code == 200, detail.text
    assert (
        detail.json()["reverse_charge_justification"]
        == "achiziție intracomunitară de servicii, art. 331"
    )
    # and the list view carries it too, so a list-driven form can hydrate from it
    listed = (
        await client.get(
            f"/api/v1/workspaces/{workspace_id}/documents",
            params={"kind": "purchase_invoice"},
            headers=admin_session.headers(),
        )
    ).json()["items"]
    entry = next(item for item in listed if item["id"] == created["id"])
    assert entry["reverse_charge_justification"].startswith("achiziție intracomunitară")


async def test_a_document_without_a_series_is_issued_by_its_number(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    """Many documents carry only a number, so the series must not be required."""
    created = await create(
        client, admin_session, workspace_id, invoice_body(series=None, number="2026-77")
    )
    finalized = await finalize(
        client,
        admin_session,
        workspace_id,
        created["id"],
        key="fin-no-series",
        series="",
        number="2026-77",
    )
    assert finalized["status"] == "finalized"
    assert finalized["series"] is None
    assert finalized["number"] == "2026-77"

    # and the number alone still identifies the document in the workspace
    repeated = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        json=invoice_body(series=None, number="2026-77", issue_date="2026-03-11"),
        headers=admin_session.headers(),
    )
    assert repeated.status_code == 409, repeated.text
    assert "numărul" in repeated.json()["title"]


async def test_a_settlement_mentions_a_deleted_document(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    """When the allocated document is deleted, the settlement must say so instead of
    showing a raw identifier."""
    created = await create(client, admin_session, workspace_id, invoice_body())
    await finalize(client, admin_session, workspace_id, created["id"], key="fin-sett-deleted-doc")
    receipt = await client.post(
        f"/api/v1/workspaces/{workspace_id}/payments",
        json={
            "direction": "inflow",
            "kind": "customer_receipt",
            "paid_on": "2026-03-20",
            "amount": "4000",
            "method": "bank",
            "allocations": [{"document_id": created["id"], "amount": "4000"}],
        },
        headers=admin_session.headers(),
    )
    assert receipt.status_code == 201, receipt.text
    payment_id = receipt.json()["id"]

    # the settlement was recorded *for* the document, so it is deleted with the document
    deleted = await client.delete(
        f"/api/v1/workspaces/{workspace_id}/documents/{created['id']}",
        headers=admin_session.headers(),
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["payments_removed"], deleted.json()

    listed = (
        await client.get(f"/api/v1/workspaces/{workspace_id}/payments", headers=admin_session.headers())
    ).json()["items"]
    assert all(item["id"] != payment_id for item in listed)


async def test_deleting_a_document_deletes_the_money_recorded_for_it(
    client: AsyncClient, admin_session: Session, workspace_id: str, database: Database, tenant: TenantFixture
) -> None:
    """The settlement was recorded because the document existed, so it goes with it.
    A settlement covering other documents as well is kept for them."""
    created = await create(client, admin_session, workspace_id, invoice_body())
    await finalize(client, admin_session, workspace_id, created["id"], key="fin-money-gone")

    receipt = await client.post(
        f"/api/v1/workspaces/{workspace_id}/payments",
        json={
            "direction": "inflow",
            "kind": "customer_receipt",
            "paid_on": "2026-03-20",
            "amount": "4000",
            "method": "bank",
            "allocations": [{"document_id": created["id"], "amount": "4000"}],
        },
        headers=admin_session.headers(),
    )
    assert receipt.status_code == 201, receipt.text

    deleted = await client.delete(
        f"/api/v1/workspaces/{workspace_id}/documents/{created['id']}",
        headers=admin_session.headers(),
    )
    assert deleted.status_code == 200, deleted.text
    after = deleted.json()
    assert len(after["payments_removed"]) == 1
    assert after["payments_removed"][0]["payment_id"] == receipt.json()["id"]

    listed = (
        await client.get(f"/api/v1/workspaces/{workspace_id}/payments", headers=admin_session.headers())
    ).json()["items"]
    assert all(item["id"] != receipt.json()["id"] for item in listed), (
        "money recorded for a deleted document must go with it"
    )
    dashboard = await client.get(
        f"/api/v1/workspaces/{workspace_id}/dashboard",
        params={"income_year": 2026}, headers=admin_session.headers(),
    )
    assert dashboard.json()["collected_revenue"] == "0.00", (
        "a deleted invoice's deleted receipt cannot stay in the revenue totals"
    )

    # a settlement covering two documents keeps the other document's share when one goes
    other = await create(client, admin_session, workspace_id, invoice_body(number="555"))
    await finalize(
        client, admin_session, workspace_id, other["id"], key="fin-money-shared-a"
    )
    doomed = await create(client, admin_session, workspace_id, invoice_body(number="556"))
    await finalize(client, admin_session, workspace_id, doomed["id"], key="fin-money-shared-b")
    shared = await client.post(
        f"/api/v1/workspaces/{workspace_id}/payments",
        json={
            "direction": "inflow",
            "kind": "customer_receipt",
            "paid_on": "2026-04-20",
            "amount": "3000",
            "method": "bank",
            "allocations": [
                {"document_id": other["id"], "amount": "2000"},
                {"document_id": doomed["id"], "amount": "1000"},
            ],
        },
        headers=admin_session.headers(),
    )
    assert shared.status_code == 201, shared.text

    # deleting one of the two documents must NOT delete the shared settlement
    gone = await client.delete(
        f"/api/v1/workspaces/{workspace_id}/documents/{doomed['id']}",
        headers=admin_session.headers(),
    )
    assert gone.status_code == 200, gone.text
    listed = (
        await client.get(f"/api/v1/workspaces/{workspace_id}/payments", headers=admin_session.headers())
    ).json()["items"]
    kept = next(item for item in listed if item["id"] == shared.json()["id"])
    assert kept["allocations"] and kept["allocations"][0]["document_id"] == other["id"]


async def test_deleting_an_invoice_preserves_unallocated_receipts(
    client: AsyncClient, admin_session: Session, workspace_id: str,
) -> None:
    """Only its own receipt goes away, not unrelated money in the same workspace."""
    created = await create(client, admin_session, workspace_id, invoice_body())
    await finalize(client, admin_session, workspace_id, created["id"], key="fin-scope-money")
    async def receipt(amount: str, allocations: list[dict]) -> dict:
        response = await client.post(
            f"/api/v1/workspaces/{workspace_id}/payments",
            json={
                "direction": "inflow", "kind": "customer_receipt", "paid_on": "2026-03-20",
                "amount": amount, "method": "bank", "allocations": allocations,
            },
            headers=admin_session.headers(),
        )
        assert response.status_code == 201, response.text
        return response.json()

    own = await receipt("1000", [{"document_id": created["id"], "amount": "1000"}])
    independent = await receipt("500", [])
    dashboard_url = f"/api/v1/workspaces/{workspace_id}/dashboard"
    before = await client.get(dashboard_url, params={"income_year": 2026}, headers=admin_session.headers())
    assert before.json()["collected_revenue"] == "1500.00"

    deleted = await client.delete(
        f"/api/v1/workspaces/{workspace_id}/documents/{created['id']}",
        headers=admin_session.headers(),
    )
    assert deleted.status_code == 200, deleted.text
    assert [item["payment_id"] for item in deleted.json()["payments_removed"]] == [own["id"]]
    listed = await client.get(
        f"/api/v1/workspaces/{workspace_id}/payments", headers=admin_session.headers()
    )
    assert [item["id"] for item in listed.json()["items"]] == [independent["id"]]
    after = await client.get(dashboard_url, params={"income_year": 2026}, headers=admin_session.headers())
    assert after.json()["collected_revenue"] == "500.00"


async def test_corrected_receipt_moves_revenue_to_its_new_year(
    client: AsyncClient, admin_session: Session, workspace_id: str,
) -> None:
    response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/payments",
        json={"direction": "inflow", "kind": "customer_receipt", "paid_on": "2025-12-30",
              "amount": "500", "method": "bank", "allocations": []},
        headers=admin_session.headers(),
    )
    assert response.status_code == 201, response.text
    payment_id = response.json()["id"]
    moved = await client.patch(
        f"/api/v1/workspaces/{workspace_id}/payments/{payment_id}",
        json={"paid_on": "2026-01-02", "amount": "750"}, headers=admin_session.headers(),
    )
    assert moved.status_code == 200, moved.text
    for year, expected in [(2025, "0.00"), (2026, "750.00")]:
        response = await client.get(
            f"/api/v1/workspaces/{workspace_id}/dashboard",
            params={"income_year": year}, headers=admin_session.headers(),
        )
        assert response.json()["collected_revenue"] == expected


async def test_the_other_money_kinds_are_registered_as_the_legislation_says(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    """Aportul, retragerile, împrumuturile și plățile de taxe se înregistrează în RJIP,
    dar nu sunt venituri brute (art. 68 alin. (2)) și nu sunt deductibile (alin. (4)),
    deci nu pot fi alocate niciunui document și nu intră în REF."""
    kinds = [
        ("owner_contribution", "inflow"),
        ("owner_withdrawal", "outflow"),
        ("owner_transfer", "inflow"),
        ("loan_received", "inflow"),
        ("loan_repaid", "outflow"),
        ("tax_payment", "outflow"),
    ]
    ids = []
    for kind, direction in kinds:
        created = await client.post(
            f"/api/v1/workspaces/{workspace_id}/payments",
            json={
                "direction": direction,
                "kind": kind,
                "paid_on": "2026-05-20",
                "amount": "500",
                "method": "bank",
                "reference": f"claritate {kind}",
            },
            headers=admin_session.headers(),
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["is_owner_movement"] is (kind != "tax_payment")
        body["rule_kind"] = kind
        ids.append(body)

    # ce spune legea pentru fiecare fel de ban, ca text complet în răspuns
    rules = [
        entry["note"]
        for entry in ids
    ]
    assert any("art. 68 alin. (2) lit. 1" in rule for rule in rules)
    assert any("art. 68 alin. (4)" in rule for rule in rules)

    # they enter the RJIP as owner movements (balance column), not as receipts/payments
    journal = (
        await client.get(
            f"/api/v1/workspaces/{workspace_id}/registers/cash_journal",
            params={"period_start": "2026-05-01", "period_end": "2026-05-31"},
            headers=admin_session.headers(),
        )
    ).json()["rows"]
    kinds_in_journal = [row["description"] for row in journal]
    assert any("owner_contribution" in description for description in kinds_in_journal)
    assert any("tax_payment" in description for description in kinds_in_journal)

    # and none of them may be allocated to a document
    sale = await create(client, admin_session, workspace_id, invoice_body(number="4444"))
    await finalize(client, admin_session, workspace_id, sale["id"], key="fin-alloc-forbidden")
    denied = await client.post(
        f"/api/v1/workspaces/{workspace_id}/payments",
        json={
            "direction": "inflow",
            "kind": "loan_received",
            "paid_on": "2026-05-21",
            "amount": "300",
            "method": "bank",
            "allocations": [{"document_id": sale["id"], "amount": "300"}],
        },
        headers=admin_session.headers(),
    )
    assert denied.status_code == 422, denied.text
    assert denied.json()["field_errors"] == {"allocations": "not_allowed"}
