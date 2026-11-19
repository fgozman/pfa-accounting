"""Obligations, declaration lifecycle and form artifacts.

Requirement IDs: OBLIGATIONS, DEC-01, DEC-02, DEC-03, FORMS, REAL, NORM.
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy import text

from pfa.db import Database
from pfa.models.compliance import FormVersion
from pfa.repo import compliance as compliance_repo
from tests.conftest import Session, TenantFixture, finalize_payload


async def set_facts(
    client: AsyncClient, session: Session, workspace_id: str, *, year: int = 2025, **facts
) -> None:
    payload = [
        {"income_year": year, "fact_key": key, "value_kind": kind, "value": value}
        for key, (kind, value) in facts.items()
    ]
    response = await client.patch(
        f"/api/v1/workspaces/{workspace_id}/fiscal-profile",
        json={"year_facts": payload},
        headers=session.headers(),
    )
    assert response.status_code == 200, response.text


async def make_supported_form(database: Database, *, form_code: str, year: int) -> None:
    async with database.session() as session:
        session.add(
            FormVersion(
                form_code=form_code,
                income_year=year,
                schema_version=f"{form_code}-{year}.1",
                artifact_format="xml",
                validator_id="anaf-official-validator",
                filing_channel="external_upload",
                supported=True,
                published=True,
            )
        )
        await session.flush()


async def prepare(
    client: AsyncClient, session: Session, workspace_id: str, **overrides
) -> dict:
    body = {
        "form_code": "D212",
        "income_year": 2025,
        "period_start": "2025-01-01",
        "period_end": "2025-12-31",
        "taxpayer_key": "taxpayer-x",
        "taxpayer_identity": {"cui": "90000009", "cnp": "1900101123456"},
    }
    body.update(overrides)
    response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/declarations", json=body, headers=session.headers()
    )
    assert response.status_code == 201, response.text
    return response.json()


async def calculate(client: AsyncClient, session: Session, declaration_id: str, key: str) -> dict:
    response = await client.post(
        f"/api/v1/declarations/{declaration_id}/calculate",
        headers=session.headers(**{"Idempotency-Key": key}),
    )
    assert response.status_code == 200, response.text
    return response.json()


async def ensure_revenue(
    client: AsyncClient, session: Session, workspace_id: str, *, amount: str = "60000"
) -> None:
    document = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        json={
            "kind": "sales_invoice",
            "issue_date": "2025-06-10",
            "supply_date": "2025-06-10",
            "currency": "RON",
            "lines": [
                {
                    "description": "Servicii",
                    "quantity": "1",
                    "unit_price": amount,
                    "vat_rate": "0",
                }
            ],
        },
        headers=session.headers(),
    )
    document_id = document.json()["id"]
    await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents/{document_id}/finalize",
        json=finalize_payload(),
        headers=session.headers(**{"Idempotency-Key": f"fin-{document_id}"}),
    )
    await client.post(
        f"/api/v1/workspaces/{workspace_id}/payments",
        json={
            "direction": "inflow",
            "kind": "customer_receipt",
            "paid_on": "2025-06-20",
            "amount": amount,
            "allocations": [{"document_id": document_id, "amount": amount}],
        },
        headers=session.headers(),
    )


# ---------------------------------------------------------------------------
# OBLIGATIONS
# ---------------------------------------------------------------------------


async def test_d212_needs_information_until_the_year_facts_are_answered(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    await set_facts(client, admin_session, workspace_id, regime=("text", "real"))
    await ensure_revenue(client, admin_session, workspace_id)
    await client.post(
        f"/api/v1/workspaces/{workspace_id}/obligations/reconcile",
        params={"income_year": 2025},
        headers=admin_session.headers(),
    )

    response = await client.get(
        f"/api/v1/workspaces/{workspace_id}/obligations",
        params={"income_year": 2025},
        headers=admin_session.headers(),
    )
    assert response.status_code == 200
    items = {item["form_code"]: item for item in response.json()["items"]}
    assert items["D212"]["applicability"] == "needs_information"
    assert "cas_election" in items["D212"]["missing_inputs"]
    assert items["D212"]["filing_due_date"] == "2026-05-25"
    assert items["D212"]["days_remaining"] is not None

    await set_facts(
        client,
        admin_session,
        workspace_id,
        regime=("text", "real"),
        cas_election=("boolean", False),
        cass_election=("boolean", False),
        prior_losses=("decimal", "0"),
        salary_income_present=("boolean", False),
        pension_status=("text", "not_pensioner"),
    )
    await client.post(
        f"/api/v1/workspaces/{workspace_id}/obligations/reconcile",
        params={"income_year": 2025},
        headers=admin_session.headers(),
    )
    response = await client.get(
        f"/api/v1/workspaces/{workspace_id}/obligations",
        params={"income_year": 2025},
        headers=admin_session.headers(),
    )
    items = {item["form_code"]: item for item in response.json()["items"]}
    assert items["D212"]["applicability"] == "required"
    assert items["D212"]["missing_inputs"] == []
    assert items["D212"]["capabilities"]["generate"] is False  # no published schema yet
    assert "schema" in items["D212"]["unsupported_reason"].lower()


async def test_forms_without_detection_stay_visible_with_an_explanation(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    response = await client.get(
        f"/api/v1/workspaces/{workspace_id}/obligations", headers=admin_session.headers()
    )
    assert response.status_code == 200
    unassessed = {item["form_code"]: item for item in response.json()["unassessed_forms"]}
    for code in ("D112", "D100", "D205", "D307", "D398"):
        assert code in unassessed
        assert unassessed[code]["detection_available"] is False
        assert unassessed[code]["reason"]


async def test_d406_is_excluded_for_pfa(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    await client.post(
        f"/api/v1/workspaces/{workspace_id}/obligations/reconcile",
        params={"income_year": 2025},
        headers=admin_session.headers(),
    )
    response = await client.get(
        f"/api/v1/workspaces/{workspace_id}/obligations",
        params={"income_year": 2025},
        headers=admin_session.headers(),
    )
    items = {item["form_code"]: item for item in response.json()["items"]}
    assert items["D406"]["applicability"] == "not_required"
    assert "OPANAF 407/2025" in items["D406"]["applicability_reason"]


async def test_vat_registered_workspace_owes_nil_returns(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    """A nil return exists even when there are no documents."""
    response = await client.patch(
        f"/api/v1/workspaces/{workspace_id}/fiscal-profile",
        json={
            "vat_registrations": [
                {
                    "kind": "article_316",
                    "vat_code": "RO90000009",
                    "effective_from": "2025-01-01",
                    "reporting_frequency": "monthly",
                }
            ]
        },
        headers=admin_session.headers(),
    )
    assert response.status_code == 200, response.text
    await client.post(
        f"/api/v1/workspaces/{workspace_id}/obligations/reconcile",
        params={"income_year": 2025},
        headers=admin_session.headers(),
    )
    listing = await client.get(
        f"/api/v1/workspaces/{workspace_id}/obligations",
        params={"income_year": 2025},
        headers=admin_session.headers(),
    )
    required_forms = {item["form_code"] for item in listing.json()["items"]}
    assert "D300" in required_forms
    assert "D394" in required_forms
    d300 = [
        item
        for item in listing.json()["items"]
        if item["form_code"] == "D300" and item["applicability"] == "required"
    ]
    assert len(d300) == 12
    assert all("zero operațiuni" in item["applicability_reason"] for item in d300)
    assert d300[0]["filing_due_date"] == "2025-02-25"


# ---------------------------------------------------------------------------
# DEC-01 / DEC-02
# ---------------------------------------------------------------------------


async def test_unknown_facts_block_final_approval(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    await set_facts(client, admin_session, workspace_id, regime=("text", "real"))
    await ensure_revenue(client, admin_session, workspace_id)
    declaration = await prepare(client, admin_session, workspace_id)

    calculated = await calculate(client, admin_session, declaration["id"], "calc-blocked")
    assert calculated["status"] == "calculated"
    assert "unresolved:cas_election" in calculated["calculated_values"]["unresolved"]

    blocked = await client.post(
        f"/api/v1/declarations/{declaration['id']}/validate",
        headers=admin_session.headers(),
    )
    assert blocked.status_code == 409
    assert "cas_election" in blocked.json()["details"]["missing_inputs"]


async def test_calculated_values_reconcile_and_every_field_is_traced(
    client: AsyncClient, database: Database, admin_session: Session, workspace_id: str
) -> None:
    await set_facts(
        client,
        admin_session,
        workspace_id,
        regime=("text", "real"),
        cas_election=("boolean", False),
        cass_election=("boolean", False),
        prior_losses=("decimal", "0"),
        salary_income_present=("boolean", False),
        pension_status=("text", "not_pensioner"),
    )
    await ensure_revenue(client, admin_session, workspace_id, amount="60000")
    declaration = await prepare(client, admin_session, workspace_id)
    calculated = await calculate(client, admin_session, declaration["id"], "calc-trace")
    values = calculated["calculated_values"]

    # 60.000 net -> CASS base 12 x 4.050 = 48.600 -> 4.860; CAS 12 x 4.050 -> 12.150
    assert Decimal(values["gross_revenue"]) == Decimal("60000")
    assert Decimal(values["net_income"]) == Decimal("60000")
    assert Decimal(values["cass_base"]) == Decimal("48600")
    assert Decimal(values["cass_amount"]) == Decimal("4860")
    assert Decimal(values["cas_amount"]) == Decimal("12150")
    assert Decimal(values["taxable_income"]) == Decimal("42990")
    assert Decimal(values["income_tax"]) == Decimal("4299")
    assert Decimal(values["total_due"]) == Decimal("21309")
    assert values["chapters"]["activitati_independente"]["venit_net"] == "60000.000000"

    codes = {line["code"] for line in calculated["trace"]}
    for expected in (
        "gross_revenue",
        "eligible_expenses",
        "depreciation",
        "net_income_before_losses",
        "cas",
        "cass",
        "taxable_income",
        "income_tax",
    ):
        assert expected in codes
    for line in calculated["trace"]:
        assert line["legal_reference"]
        assert line["formula"]

    async with database.tenant_session(uuid.UUID(workspace_id), actor="user") as session:
        run = (
            await session.execute(
                text("SELECT input_snapshot_sha256, rule_pack_code FROM calculation_runs LIMIT 1")
            )
        ).one()
    assert run[1] == "RO-INCOME-2025.1"
    assert len(run[0]) == 64


async def test_missing_form_year_schema_allows_only_a_labelled_workpaper(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    await set_facts(
        client,
        admin_session,
        workspace_id,
        year=2026,
        regime=("text", "real"),
        cas_election=("boolean", False),
        cass_election=("boolean", False),
        prior_losses=("decimal", "0"),
        salary_income_present=("boolean", False),
        pension_status=("text", "not_pensioner"),
    )
    declaration = await prepare(
        client,
        admin_session,
        workspace_id,
        form_code="D212",
        income_year=2026,
        period_start="2026-01-01",
        period_end="2026-12-31",
        taxpayer_key="taxpayer-2026",
    )
    assert declaration["estimate_only"] is True
    await calculate(client, admin_session, declaration["id"], "calc-2026")
    validated = await client.post(
        f"/api/v1/declarations/{declaration['id']}/validate", headers=admin_session.headers()
    )
    assert validated.status_code == 200, validated.text
    exported = await client.post(
        f"/api/v1/declarations/{declaration['id']}/export",
        headers=admin_session.headers(**{"Idempotency-Key": "export-2026"}),
    )
    assert exported.status_code == 200, exported.text
    body = exported.json()
    assert body["artifact"]["is_preview"] is True
    assert "estimativ" in body["artifact"]["label_ro"]
    assert body["status"] == "awaiting_external_filing"  # not "submitted"

    approved = await client.post(
        f"/api/v1/declarations/{declaration['id']}/approve", headers=admin_session.headers()
    )
    assert approved.status_code == 200
    submitted = await client.post(
        f"/api/v1/declarations/{declaration['id']}/submit",
        headers=admin_session.headers(**{"Idempotency-Key": "submit-2026"}),
    )
    assert submitted.status_code == 409
    assert "previzualizare" in submitted.json()["title"]


async def test_supported_flag_without_a_conforming_artifact_stays_a_workpaper(
    client: AsyncClient, database: Database, admin_session: Session, workspace_id: str
) -> None:
    await make_supported_form(database, form_code="D212", year=2025)
    await set_facts(
        client,
        admin_session,
        workspace_id,
        regime=("text", "real"),
        cas_election=("boolean", False),
        cass_election=("boolean", False),
        prior_losses=("decimal", "0"),
        salary_income_present=("boolean", False),
        pension_status=("text", "not_pensioner"),
    )
    await ensure_revenue(client, admin_session, workspace_id)
    declaration = await prepare(client, admin_session, workspace_id)
    await calculate(client, admin_session, declaration["id"], "calc-supported")
    assert (
        await client.post(
            f"/api/v1/declarations/{declaration['id']}/validate", headers=admin_session.headers()
        )
    ).status_code == 200
    exported = await client.post(
        f"/api/v1/declarations/{declaration['id']}/export",
        headers=admin_session.headers(**{"Idempotency-Key": "export-supported"}),
    )
    assert exported.status_code == 200, exported.text
    artifact = exported.json()["artifact"]
    # the `supported` flag alone cannot make the file official: the generator emits the
    # internal workpaper structure, which does not satisfy the official ANAF XSD
    assert artifact["is_preview"] is True
    assert artifact["schema_valid"] is False
    assert artifact["official_schema"].startswith("D212:")
    assert "schema oficială" in artifact["label_ro"]
    assert exported.json()["status"] == "awaiting_external_filing"

    # the artifact is downloaded through an authenticated endpoint, not a public URL
    download = await client.get(
        f"/api/v1/declarations/{declaration['id']}/artifacts/latest",
        headers=admin_session.headers(),
    )
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("application/xml")
    payload = download.content
    assert b"DeclaratieUnica" in payload
    assert b"preview=\"true\"" in payload  # stamped as a workpaper in the file itself
    assert b"schema oficial" in payload
    assert b"<Justificari>" in payload  # field-level trace travels with the artifact

    approved = await client.post(
        f"/api/v1/declarations/{declaration['id']}/approve", headers=admin_session.headers()
    )
    assert approved.status_code == 200
    assert approved.json()["approved_at"] is not None

    # submission stays disabled: a workpaper that does not satisfy the official
    # schema cannot be filed at all
    submitted = await client.post(
        f"/api/v1/declarations/{declaration['id']}/submit",
        headers=admin_session.headers(**{"Idempotency-Key": "submit-supported"}),
    )
    assert submitted.status_code == 409
    assert "document de lucru" in submitted.json()["title"]


# ---------------------------------------------------------------------------
# DEC-03: approval invalidation and corrections
# ---------------------------------------------------------------------------


async def test_changing_input_after_approval_invalidates_the_approval(
    client: AsyncClient, database: Database, admin_session: Session, workspace_id: str
) -> None:
    await make_supported_form(database, form_code="D212", year=2025)
    await set_facts(
        client,
        admin_session,
        workspace_id,
        regime=("text", "real"),
        cas_election=("boolean", False),
        cass_election=("boolean", False),
        prior_losses=("decimal", "0"),
        salary_income_present=("boolean", False),
        pension_status=("text", "not_pensioner"),
    )
    await ensure_revenue(client, admin_session, workspace_id, amount="50000")
    declaration = await prepare(client, admin_session, workspace_id)
    await calculate(client, admin_session, declaration["id"], "calc-1")
    await client.post(
        f"/api/v1/declarations/{declaration['id']}/validate", headers=admin_session.headers()
    )
    await client.post(
        f"/api/v1/declarations/{declaration['id']}/export",
        headers=admin_session.headers(**{"Idempotency-Key": "export-1"}),
    )
    approved = await client.post(
        f"/api/v1/declarations/{declaration['id']}/approve", headers=admin_session.headers()
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["approved_at"] is not None

    # new evidence arrives after approval
    await ensure_revenue(client, admin_session, workspace_id, amount="9000")
    recalculated = await calculate(client, admin_session, declaration["id"], "calc-2")
    assert recalculated.get("approved_at") is None, recalculated
    assert recalculated["current_version"] == 2
    assert Decimal(recalculated["calculated_values"]["gross_revenue"]) == Decimal("59000")

    async with database.tenant_session(uuid.UUID(workspace_id), actor="user") as session:
        invalidated = (
            await session.execute(
                text(
                    "SELECT count(*) FROM outbox_events "
                    "WHERE event_type = 'declaration.approval_invalidated'"
                )
            )
        ).scalar_one()
    assert invalidated == 1


async def test_correction_creates_a_linked_new_version_and_keeps_the_original(
    client: AsyncClient, database: Database, admin_session: Session, workspace_id: str
) -> None:
    await make_supported_form(database, form_code="D212", year=2025)
    await set_facts(
        client,
        admin_session,
        workspace_id,
        regime=("text", "real"),
        cas_election=("boolean", False),
        cass_election=("boolean", False),
        prior_losses=("decimal", "0"),
        salary_income_present=("boolean", False),
        pension_status=("text", "not_pensioner"),
    )
    await ensure_revenue(client, admin_session, workspace_id)
    original = await prepare(client, admin_session, workspace_id)
    await calculate(client, admin_session, original["id"], "calc-orig")
    await client.post(
        f"/api/v1/declarations/{original['id']}/validate", headers=admin_session.headers()
    )
    await client.post(
        f"/api/v1/declarations/{original['id']}/export",
        headers=admin_session.headers(**{"Idempotency-Key": "export-orig"}),
    )
    await client.post(
        f"/api/v1/declarations/{original['id']}/approve", headers=admin_session.headers()
    )
    submitted = await client.post(
        f"/api/v1/declarations/{original['id']}/submit",
        headers=admin_session.headers(**{"Idempotency-Key": "submit-orig"}),
    )
    assert submitted.status_code == 409  # no verified channel, as expected
    accepted = await client.post(
        f"/api/v1/declarations/{original['id']}/receipts",
        json={
            "payload": {
                "stare": "ok",
                "cif": "90000009",
                "formular": "D212",
                "perioada": "2025-12-31",
            }
        },
        headers=admin_session.headers(),
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["verified"] is True

    frozen = await client.post(
        f"/api/v1/declarations/{original['id']}/calculate",
        headers=admin_session.headers(**{"Idempotency-Key": "calc-after-accept"}),
    )
    assert frozen.status_code == 409  # accepted declarations are immutable

    corrected = await client.post(
        f"/api/v1/workspaces/{workspace_id}/declarations",
        json={
            "form_code": "D212",
            "income_year": 2025,
            "period_start": "2025-01-01",
            "period_end": "2025-12-31",
            "taxpayer_key": "taxpayer-corrected",
            "correction_of": original["id"],
        },
        headers=admin_session.headers(),
    )
    assert corrected.status_code == 201, corrected.text
    assert corrected.json()["correction_of_declaration_id"] == original["id"]


async def test_rejected_receipt_is_bound_to_the_declaration_and_reopens_attention(
    client: AsyncClient, database: Database, admin_session: Session, workspace_id: str
) -> None:
    await make_supported_form(database, form_code="D212", year=2025)
    await set_facts(
        client,
        admin_session,
        workspace_id,
        regime=("text", "real"),
        cas_election=("boolean", False),
        cass_election=("boolean", False),
        prior_losses=("decimal", "0"),
        salary_income_present=("boolean", False),
        pension_status=("text", "not_pensioner"),
    )
    await ensure_revenue(client, admin_session, workspace_id)
    await client.post(
        f"/api/v1/workspaces/{workspace_id}/obligations/reconcile",
        params={"income_year": 2025},
        headers=admin_session.headers(),
    )
    declaration = await prepare(client, admin_session, workspace_id)
    await calculate(client, admin_session, declaration["id"], "calc-reject")
    await client.post(
        f"/api/v1/declarations/{declaration['id']}/validate", headers=admin_session.headers()
    )
    await client.post(
        f"/api/v1/declarations/{declaration['id']}/export",
        headers=admin_session.headers(**{"Idempotency-Key": "export-reject"}),
    )
    rejection = await client.post(
        f"/api/v1/declarations/{declaration['id']}/receipts",
        json={
            "payload": {
                "stare": "nok",
                "formular": "D212",
                "erori": [{"message": "CNP invalid"}, "Atribut lipsă: perioada"],
            }
        },
        headers=admin_session.headers(),
    )
    assert rejection.status_code == 201
    body = rejection.json()
    assert body["kind"] == "rejection"
    assert body["verified"] is False
    assert body["rejection_errors"] == ["CNP invalid", "Atribut lipsă: perioada"]

    detail = await client.get(
        f"/api/v1/declarations/{declaration['id']}", headers=admin_session.headers()
    )
    assert detail.json()["status"] == "rejected"

    obligations = await client.get(
        f"/api/v1/workspaces/{workspace_id}/obligations",
        params={"income_year": 2025},
        headers=admin_session.headers(),
    )
    d212 = next(
        item for item in obligations.json()["items"] if item["form_code"] == "D212"
    )
    assert d212["resolution_status"] == "open"


async def test_receipt_mismatch_stays_unverified(
    client: AsyncClient, database: Database, admin_session: Session, workspace_id: str
) -> None:
    await make_supported_form(database, form_code="D212", year=2025)
    await set_facts(
        client,
        admin_session,
        workspace_id,
        regime=("text", "real"),
        cas_election=("boolean", False),
        cass_election=("boolean", False),
        prior_losses=("decimal", "0"),
        salary_income_present=("boolean", False),
        pension_status=("text", "not_pensioner"),
    )
    await ensure_revenue(client, admin_session, workspace_id)
    declaration = await prepare(client, admin_session, workspace_id)
    await calculate(client, admin_session, declaration["id"], "calc-mismatch")
    await client.post(
        f"/api/v1/declarations/{declaration['id']}/validate", headers=admin_session.headers()
    )
    await client.post(
        f"/api/v1/declarations/{declaration['id']}/export",
        headers=admin_session.headers(**{"Idempotency-Key": "export-mismatch"}),
    )
    # a receipt for a different taxpayer must not mark the return accepted
    receipt = await client.post(
        f"/api/v1/declarations/{declaration['id']}/receipts",
        json={
            "payload": {
                "stare": "ok",
                "cif": "11111111",
                "formular": "D300",
                "perioada": "2024-12-31",
            }
        },
        headers=admin_session.headers(),
    )
    assert receipt.status_code == 201
    assert receipt.json()["verified"] is False
    detail = await client.get(
        f"/api/v1/declarations/{declaration['id']}", headers=admin_session.headers()
    )
    assert detail.json()["status"] != "accepted"

    manual = await client.post(
        f"/api/v1/declarations/{declaration['id']}/receipts",
        json={
            "payload": {"stare": "ok", "cif": "90000009", "formular": "D212", "perioada": "2025-12-31"},
            "manually_reported": True,
        },
        headers=admin_session.headers(),
    )
    assert manual.status_code == 201
    assert manual.json()["verified"] is False
    assert manual.json()["manually_reported"] is True
    assert "neverificată" in manual.json()["note"]


async def test_trace_reports_the_pinned_rule_pack(
    client: AsyncClient, database: Database, admin_session: Session, workspace_id: str
) -> None:
    await set_facts(
        client,
        admin_session,
        workspace_id,
        regime=("text", "real"),
        cas_election=("boolean", False),
        cass_election=("boolean", False),
        prior_losses=("decimal", "0"),
        salary_income_present=("boolean", False),
        pension_status=("text", "not_pensioner"),
    )
    await ensure_revenue(client, admin_session, workspace_id)
    declaration = await prepare(client, admin_session, workspace_id)
    await calculate(client, admin_session, declaration["id"], "calc-trace-2")
    trace = await client.get(
        f"/api/v1/declarations/{declaration['id']}/trace", headers=admin_session.headers()
    )
    assert trace.status_code == 200
    body = trace.json()
    assert body["rule_pack"]["code"] == "RO-INCOME-2025.1"
    assert body["versions"][0]["rule_pack_code"] == "RO-INCOME-2025.1"
    assert body["versions"][0]["trace"]
    assert body["calculation_runs"][0]["engine_version"].startswith("fiscal-engine")


async def test_norm_profile_calculation_uses_the_published_norm(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    response = await client.patch(
        f"/api/v1/workspaces/{workspace_id}/fiscal-profile",
        json={
            "tax_regimes": [
                {
                    "kind": "norm",
                    "income_year": 2025,
                    "effective_from": "2025-01-01",
                    "norm_county": "Cluj",
                    "norm_locality": "Cluj-Napoca",
                    "norm_activity": "6201",
                    "norm_amount": "45000",
                }
            ],
            "year_facts": [
                {"income_year": 2025, "fact_key": "regime", "value_kind": "text", "value": "norm"},
                {"income_year": 2025, "fact_key": "norm_amount", "value_kind": "decimal", "value": "45000"},
                {
                    "income_year": 2025,
                    "fact_key": "norm_adjustment_percent",
                    "value_kind": "decimal",
                    "value": "0",
                },
                {"income_year": 2025, "fact_key": "cas_election", "value_kind": "boolean", "value": False},
                {"income_year": 2025, "fact_key": "cass_election", "value_kind": "boolean", "value": False},
                {"income_year": 2025, "fact_key": "prior_losses", "value_kind": "decimal", "value": "0"},
                {
                    "income_year": 2025,
                    "fact_key": "salary_income_present",
                    "value_kind": "boolean",
                    "value": False,
                },
                {
                    "income_year": 2025,
                    "fact_key": "pension_status",
                    "value_kind": "text",
                    "value": "not_pensioner",
                },
            ],
        },
        headers=admin_session.headers(),
    )
    assert response.status_code == 200, response.text
    # actual operating profit must not replace the norm
    await ensure_revenue(client, admin_session, workspace_id, amount="500000")
    declaration = await prepare(client, admin_session, workspace_id)
    calculated = await calculate(client, admin_session, declaration["id"], "calc-norm")
    values = calculated["calculated_values"]
    assert values["regime"] == "norm"
    assert Decimal(values["norm_income"]) == Decimal("45000")
    assert Decimal(values["net_income"]) == Decimal("45000")
    assert Decimal(values["income_tax"]) == Decimal("4500")
    codes = {line["code"] for line in calculated["trace"]}
    assert "expenses_not_applicable_to_norm" in codes or "norm_income" in codes


async def test_regime_and_vat_selection_is_idempotent_and_versioned(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    """Selecting a regime/VAT status appends history and never duplicates the open period."""
    url = f"/api/v1/workspaces/{workspace_id}/fiscal-profile"
    real = {"kind": "real", "income_year": 2025, "effective_from": "2025-01-01"}
    for _ in range(2):
        response = await client.patch(
            url, json={"tax_regimes": [real]}, headers=admin_session.headers()
        )
        assert response.status_code == 200, response.text

    regimes = (await client.get(url, headers=admin_session.headers())).json()["tax_regimes"]
    assert len(regimes) == 1, regimes
    assert regimes[0]["kind"] == "real"

    # switching the regime closes the previous period instead of duplicating it
    norm = {
        "kind": "norm",
        "income_year": 2025,
        "effective_from": "2025-06-01",
        "norm_county": "Cluj",
        "norm_locality": "Cluj-Napoca",
        "norm_activity": "6201",
        "norm_amount": "45000",
    }
    response = await client.patch(
        url, json={"tax_regimes": [norm]}, headers=admin_session.headers()
    )
    assert response.status_code == 200, response.text
    regimes = (await client.get(url, headers=admin_session.headers())).json()["tax_regimes"]
    assert len(regimes) == 2, regimes
    by_kind = {row["kind"]: row for row in regimes}
    assert by_kind["real"]["effective_to"] == "2025-06-01"
    assert by_kind["norm"]["effective_to"] is None
    assert by_kind["norm"]["norm_amount"] == "45000.000000"

    # same-day change supersedes in place (no zero-length duplicate period)
    response = await client.patch(
        url,
        json={"tax_regimes": [{"kind": "real", "income_year": 2025, "effective_from": "2025-06-01"}]},
        headers=admin_session.headers(),
    )
    assert response.status_code == 200, response.text
    regimes = (await client.get(url, headers=admin_session.headers())).json()["tax_regimes"]
    assert len(regimes) == 2, regimes
    open_regime = next(row for row in regimes if row["effective_to"] is None)
    assert open_regime["kind"] == "real"

    # VAT: registered, then not registered; exactly one open registration at a time
    for payload in (
        {"kind": "article_316", "effective_from": "2025-01-01", "reporting_frequency": "monthly"},
        {"kind": "none", "effective_from": "2025-07-01"},
    ):
        response = await client.patch(
            url, json={"vat_registrations": [payload]}, headers=admin_session.headers()
        )
        assert response.status_code == 200, response.text
    registrations = (await client.get(url, headers=admin_session.headers())).json()[
        "vat_registrations"
    ]
    open_registrations = [row for row in registrations if row["effective_to"] is None]
    assert len(open_registrations) == 1, registrations
    assert open_registrations[0]["kind"] == "none"
    assert any(row["kind"] == "article_316" and row["effective_to"] for row in registrations)


async def test_preparing_a_declaration_links_the_obligation(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    """The dashboard opens the prepared draft instead of preparing a duplicate."""
    response = await client.patch(
        f"/api/v1/workspaces/{workspace_id}/fiscal-profile",
        json={
            "vat_registrations": [
                {
                    "kind": "article_316",
                    "vat_code": "RO90000009",
                    "effective_from": "2025-01-01",
                    "reporting_frequency": "monthly",
                }
            ]
        },
        headers=admin_session.headers(),
    )
    assert response.status_code == 200, response.text
    await client.post(
        f"/api/v1/workspaces/{workspace_id}/obligations/reconcile",
        params={"income_year": 2025},
        headers=admin_session.headers(),
    )
    before = (
        await client.get(
            f"/api/v1/workspaces/{workspace_id}/obligations",
            params={"income_year": 2025},
            headers=admin_session.headers(),
        )
    ).json()["items"]
    obligation = next(
        item
        for item in before
        if item["form_code"] == "D300" and item["applicability"] == "required"
    )
    assert obligation["declaration_id"] is None

    prepared = await client.post(
        f"/api/v1/workspaces/{workspace_id}/declarations",
        json={
            "form_code": "D300",
            "income_year": 2025,
            "period_start": obligation["period_start"],
            "period_end": obligation["period_end"],
        },
        headers=admin_session.headers(),
    )
    assert prepared.status_code == 201, prepared.text
    declaration_id = prepared.json()["id"]

    after = (
        await client.get(
            f"/api/v1/workspaces/{workspace_id}/obligations",
            params={"income_year": 2025},
            headers=admin_session.headers(),
        )
    ).json()["items"]
    linked = next(item for item in after if item["id"] == obligation["id"])
    assert linked["declaration_id"] == declaration_id


async def test_d390_classification_uses_the_counterparty_country_and_operation_type(
    database: Database, admin_session: Session, workspace_id: str
) -> None:
    """D390 classification comes from stored facts, never from guessing."""
    from datetime import date

    from pfa.models.base import DocumentKind, DocumentStatus
    from pfa.models.compliance import Declaration
    from pfa.models.documents import Counterparty, Document
    from pfa.services.declarations import build_special_vat_snapshot

    async with database.tenant_session(uuid.UUID(workspace_id), actor="user") as session:
        supplier = Counterparty(
            workspace_id=uuid.UUID(workspace_id),
            name="Furnizor GmbH",
            country_code="DE",
            vat_identifier="DE123456789",
        )
        session.add(supplier)
        await session.flush()
        session.add(
            Document(
                workspace_id=uuid.UUID(workspace_id),
                kind=DocumentKind.PURCHASE_INVOICE,
                status=DocumentStatus.FINALIZED,
                issue_date=date(2026, 9, 10),
                currency="RON",
                counterparty_id=supplier.id,
                total_amount=Decimal("1210"),
                total_vat=Decimal("210"),
                vat_treatment="reverse_charge",
                deductibility="fully_deductible",
                business_use_percent=Decimal("100"),
                intracommunity_operation_type="S",
                revision=1,
                created_by_user_id=uuid.UUID(admin_session.user_id),
            )
        )
        await session.flush()

        # a transient declaration: only the attributes the snapshot reads are needed
        declaration = Declaration(
            workspace_id=uuid.UUID(workspace_id),
            form_code="D390",
            income_year=2026,
            period_start=date(2026, 9, 1),
            period_end=date(2026, 9, 30),
            status="draft",
            taxpayer_identity={
                "nume_declar": "Popescu",
                "prenume_declar": "Ion",
                "functie_declar": "Titular",
                "cui": "12345678",
                "den": "PFA Popescu Ion",
                "adresa": "Str. Exemplu 1",
            },
            estimate_only=True,
            created_by_user_id=uuid.UUID(admin_session.user_id),
        )
        # no profile row is required: the explicit identity supplies the header
        snapshot = await build_special_vat_snapshot(session, declaration=declaration)

    assert snapshot["operations"] == [
        {
            "tip": "S",
            "tara": "DE",
            "codO": "123456789",
            "denO": "Furnizor GmbH",
            "baza": 1000,
        }
    ]
    assert snapshot["declarant_missing"] == []
    assert snapshot["operations_missing"] == []
    assert snapshot["d_rec"] == 0


async def test_d300_reports_the_vat_recorded_on_the_documents(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    """The D300 reconciles with the invoices instead of recomputing a different rate."""
    response = await client.patch(
        f"/api/v1/workspaces/{workspace_id}/fiscal-profile",
        json={
            "vat_registrations": [
                {
                    "kind": "article_316",
                    "vat_code": "RO90000009",
                    "effective_from": "2025-01-01",
                    "reporting_frequency": "monthly",
                }
            ]
        },
        headers=admin_session.headers(),
    )
    assert response.status_code == 200, response.text

    async def document(kind: str, net: str, rate: str, day: str, **extra: object) -> dict:
        body = {
            "kind": kind,
            "issue_date": day,
            "supply_date": day,
            "currency": "RON",
            "vat_treatment": "standard",
            **extra,
            "lines": [
                {
                    "description": "X",
                    "quantity": "1",
                    "unit_price": net,
                    "vat_rate": rate,
                    "vat_treatment": "standard",
                }
            ],
        }
        created = await client.post(
            f"/api/v1/workspaces/{workspace_id}/documents",
            json=body,
            headers=admin_session.headers(),
        )
        assert created.status_code == 201, created.text
        document_id = created.json()["id"]
        finalized = await client.post(
            f"/api/v1/workspaces/{workspace_id}/documents/{document_id}/finalize",
            json=finalize_payload(),
            headers=admin_session.headers(**{"Idempotency-Key": f"fin-{document_id}"}),
        )
        assert finalized.status_code == 200, finalized.text
        return finalized.json()

    # 21% is what the invoices carry; the 2025 pack's statutory rate for March is 19%,
    # so recomputing from the pack would silently disagree with the documents
    await document("sales_invoice", "1000", "21", "2025-03-10")
    # a deductible expense states its business-use share; without it the deduction is
    # reported as missing rather than computed from an assumed 100%
    await document("purchase_invoice", "500", "21", "2025-03-12", business_use_percent="100")

    declaration = await client.post(
        f"/api/v1/workspaces/{workspace_id}/declarations",
        json={
            "form_code": "D300",
            "income_year": 2025,
            "period_start": "2025-03-01",
            "period_end": "2025-03-31",
        },
        headers=admin_session.headers(),
    )
    assert declaration.status_code == 201, declaration.text
    calculated = await calculate(
        client, admin_session, declaration.json()["id"], "calc-d300-recorded-vat"
    )
    values = calculated["calculated_values"]
    assert Decimal(values["collected_total"]) == Decimal("210")  # 1000 la 21% din factură
    assert Decimal(values["deductible_total"]) == Decimal("105")  # 500 la 21% din factură
    assert Decimal(values["balance_payable"]) == Decimal("105")
    assert Decimal(str(values["sales"][0]["rate"])) == Decimal("21")


async def test_document_in_a_generated_declaration_cannot_be_deleted(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    """Once the figures are inside a generated declaration, deleting is not the fix."""
    await client.patch(
        f"/api/v1/workspaces/{workspace_id}/fiscal-profile",
        json={
            "vat_registrations": [
                {
                    "kind": "article_316",
                    "vat_code": "RO90000011",
                    "effective_from": "2025-01-01",
                    "reporting_frequency": "monthly",
                }
            ]
        },
        headers=admin_session.headers(),
    )
    created = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        json={
            "kind": "sales_invoice",
            "issue_date": "2025-04-10",
            "supply_date": "2025-04-10",
            "currency": "RON",
            "vat_treatment": "standard",
            "lines": [
                {
                    "description": "Servicii",
                    "quantity": "1",
                    "unit_price": "1000",
                    "vat_rate": "21",
                    "vat_treatment": "standard",
                }
            ],
        },
        headers=admin_session.headers(),
    )
    document_id = created.json()["id"]
    finalized = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents/{document_id}/finalize",
        json=finalize_payload(),
        headers=admin_session.headers(**{"Idempotency-Key": "fin-lock"}),
    )
    assert finalized.status_code == 200, finalized.text

    declaration = await client.post(
        f"/api/v1/workspaces/{workspace_id}/declarations",
        json={
            "form_code": "D300",
            "income_year": 2025,
            "period_start": "2025-04-01",
            "period_end": "2025-04-30",
        },
        headers=admin_session.headers(),
    )
    await calculate(client, admin_session, declaration.json()["id"], "calc-lock")
    exported = await client.post(
        f"/api/v1/declarations/{declaration.json()['id']}/export",
        headers=admin_session.headers(**{"Idempotency-Key": "exp-lock"}),
        json={},
    )
    assert exported.status_code == 200, exported.text

    blocked = await client.delete(
        f"/api/v1/workspaces/{workspace_id}/documents/{document_id}",
        headers=admin_session.headers(),
    )
    assert blocked.status_code == 409
    assert "rectificativ" in blocked.json()["title"].lower()

    # a draft is never read by the snapshot builders, so it can still be removed
    draft = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        json={
            "kind": "sales_invoice",
            "issue_date": "2025-04-12",
            "currency": "RON",
            "vat_treatment": "standard",
            "allow_duplicate": True,
            "lines": [
                {
                    "description": "Ciornă",
                    "quantity": "1",
                    "unit_price": "100",
                    "vat_rate": "21",
                    "vat_treatment": "standard",
                }
            ],
        },
        headers=admin_session.headers(),
    )
    assert draft.status_code == 201, draft.text
    draft_delete = await client.delete(
        f"/api/v1/workspaces/{workspace_id}/documents/{draft.json()['id']}",
        headers=admin_session.headers(),
    )
    assert draft_delete.status_code == 200, draft_delete.text
    assert draft_delete.json()["deleted"] is True


async def test_only_domestic_invoice_requires_e_factura(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    ids = {}
    for country in ("RO", "DE"):
        created = await client.post(
            f"/api/v1/workspaces/{workspace_id}/documents",
            json={
                "kind": "sales_invoice",
                "issue_date": "2025-05-05",
                "supply_date": "2025-05-05",
                "currency": "RON",
                "supplier_name_raw": f"Client {country}",
                "supplier_country": country,
                "vat_treatment": "standard" if country == "RO" else "exempt",
                "lines": [{
                    "description": "Servicii", "quantity": "1", "unit_price": "1000",
                    "vat_rate": "21" if country == "RO" else "0",
                    "vat_treatment": "standard" if country == "RO" else "exempt",
                }],
            },
            headers=admin_session.headers(),
        )
        assert created.status_code == 201, created.text
        ids[country] = created.json()["id"]
        finalized = await client.post(
            f"/api/v1/workspaces/{workspace_id}/documents/{ids[country]}/finalize",
            json=finalize_payload(),
            headers=admin_session.headers(**{"Idempotency-Key": f"fin-{country}"}),
        )
        assert finalized.status_code == 200, finalized.text

    reconciled = await client.post(
        f"/api/v1/workspaces/{workspace_id}/obligations/reconcile",
        params={"income_year": 2025}, headers=admin_session.headers(),
    )
    assert reconciled.status_code == 202, reconciled.text
    obligations = (await client.get(
        f"/api/v1/workspaces/{workspace_id}/obligations",
        params={"income_year": 2025}, headers=admin_session.headers(),
    )).json()["items"]
    efactura = {item["triggering_evidence"][0]: item for item in obligations
                if item["form_code"] == "EFACTURA"}
    assert efactura[ids["RO"]]["applicability"] == "required"
    assert efactura[ids["DE"]]["applicability"] == "not_required"
    feed = (await client.get(
        f"/api/v1/workspaces/{workspace_id}/notifications",
        headers=admin_session.headers(),
    )).json()["items"]
    assert [item["obligation_id"] for item in feed if item["title_ro"].startswith("EFACTURA")] == [
        efactura[ids["RO"]]["id"]
    ]


async def test_home_feed_shows_only_latest_efactura_reminder(
    client: AsyncClient, admin_session: Session, workspace_id: str, database: Database
) -> None:
    """Existing open reminders from older stages must not duplicate one invoice on Acasă."""
    created = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        json={
            "kind": "sales_invoice", "issue_date": "2025-05-05", "supply_date": "2025-05-05",
            "currency": "RON", "supplier_country": "RO", "vat_treatment": "standard",
            "lines": [{"description": "Servicii", "quantity": "1", "unit_price": "1000",
                       "vat_rate": "21", "vat_treatment": "standard"}],
        },
        headers=admin_session.headers(),
    )
    assert created.status_code == 201, created.text
    document_id = created.json()["id"]
    finalized = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents/{document_id}/finalize",
        json=finalize_payload(),
        headers=admin_session.headers(**{"Idempotency-Key": "fin-home-reminder"}),
    )
    assert finalized.status_code == 200, finalized.text
    reconciled = await client.post(
        f"/api/v1/workspaces/{workspace_id}/obligations/reconcile",
        params={"income_year": 2025}, headers=admin_session.headers(),
    )
    assert reconciled.status_code == 202, reconciled.text
    items = (await client.get(
        f"/api/v1/workspaces/{workspace_id}/obligations",
        params={"income_year": 2025}, headers=admin_session.headers(),
    )).json()["items"]
    obligation = next(item for item in items if item["form_code"] == "EFACTURA")

    # Simulate reminders left open by an older release, without reconciling them first.
    async with database.tenant_session(uuid.UUID(workspace_id), actor="worker") as session:
        for offset in (5, 1):
            await compliance_repo.insert_notification(
                session, workspace_id=uuid.UUID(workspace_id),
                obligation_id=uuid.UUID(obligation["id"]), stage="reminder",
                reminder_offset_days=offset,
                deadline_revision=obligation["deadline_revision"],
                title_ro=f"EFACTURA — {offset} zile până la termen",
                body_ro="Factură de transmis", due_on=date.fromisoformat(obligation["filing_due_date"]),
                payment_due_on=None, next_step_kind="transmit_invoice", next_step_ref="EFACTURA",
                dedupe_key=f"test-home-reminder:{document_id}:{offset}",
            )

    feed = (await client.get(
        f"/api/v1/workspaces/{workspace_id}/notifications", headers=admin_session.headers(),
    )).json()["items"]
    assert [item["title_ro"] for item in feed if item["obligation_id"] == obligation["id"]] == [
        "EFACTURA — 1 zile până la termen"
    ]


async def test_deleting_an_invoice_removes_its_e_factura_obligation(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    """A deleted invoice takes its e-Factura obligation and reminders with it."""
    created = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        json={
            "kind": "sales_invoice",
            "issue_date": "2025-05-05",
            "supply_date": "2025-05-05",
            "currency": "RON",
            "vat_treatment": "standard",
            "lines": [
                {
                    "description": "Servicii",
                    "quantity": "1",
                    "unit_price": "1000",
                    "vat_rate": "21",
                    "vat_treatment": "standard",
                }
            ],
        },
        headers=admin_session.headers(),
    )
    document_id = created.json()["id"]
    await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents/{document_id}/finalize",
        json=finalize_payload(),
        headers=admin_session.headers(**{"Idempotency-Key": "fin-efactura"}),
    )
    await client.post(
        f"/api/v1/workspaces/{workspace_id}/obligations/reconcile",
        params={"income_year": 2025},
        headers=admin_session.headers(),
    )
    before = (
        await client.get(
            f"/api/v1/workspaces/{workspace_id}/obligations",
            params={"income_year": 2025},
            headers=admin_session.headers(),
        )
    ).json()["items"]
    efactura = [
        item
        for item in before
        if item["form_code"] == "EFACTURA" and document_id in (item["triggering_evidence"] or [])
    ]
    assert efactura and efactura[0]["applicability"] == "required"

    deleted = await client.delete(
        f"/api/v1/workspaces/{workspace_id}/documents/{document_id}",
        headers=admin_session.headers(),
    )
    assert deleted.status_code == 200, deleted.text

    # the obligation is gone, not merely retired: nothing derived from a deleted
    # document is left behind
    after = (
        await client.get(
            f"/api/v1/workspaces/{workspace_id}/obligations",
            params={"income_year": 2025},
            headers=admin_session.headers(),
        )
    ).json()["items"]
    assert not [
        item
        for item in after
        if item["form_code"] == "EFACTURA" and document_id in (item["triggering_evidence"] or [])
    ]

    feed = (
        await client.get(
            f"/api/v1/workspaces/{workspace_id}/notifications",
            headers=admin_session.headers(),
        )
    ).json()["items"]
    assert not [item for item in feed if "e-Factura" in (item["title_ro"] or "")]


async def test_d390_obligation_requires_intracommunity_evidence(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    """A domestic taxable invoice must not raise a recapitulative declaration."""
    await client.patch(
        f"/api/v1/workspaces/{workspace_id}/fiscal-profile",
        json={
            "vat_registrations": [
                {
                    "kind": "article_316",
                    "vat_code": "RO90000012",
                    "effective_from": "2025-01-01",
                    "reporting_frequency": "monthly",
                }
            ]
        },
        headers=admin_session.headers(),
    )

    async def purchase(day: str, **extra: object) -> str:
        body = {
            "kind": "purchase_invoice",
            "supplier_name_raw": "Furnizor",
            "issue_date": day,
            "supply_date": day,
            "currency": "RON",
            "vat_treatment": "standard",
            "lines": [
                {
                    "description": "Marfă",
                    "quantity": "1",
                    "unit_price": "1000",
                    "vat_rate": "21",
                    "vat_treatment": "standard",
                }
            ],
            **extra,
        }
        created = await client.post(
            f"/api/v1/workspaces/{workspace_id}/documents",
            json=body,
            headers=admin_session.headers(),
        )
        assert created.status_code == 201, created.text
        document_id = created.json()["id"]
        await client.post(
            f"/api/v1/workspaces/{workspace_id}/documents/{document_id}/finalize",
            json=finalize_payload(),
            headers=admin_session.headers(**{"Idempotency-Key": f"fin-{document_id}"}),
        )
        return document_id

    async def d390_forms() -> list[dict]:
        return [
            item
            for item in (
                await client.get(
                    f"/api/v1/workspaces/{workspace_id}/obligations",
                    # what the dashboard asks for: retired months stay out of the way
                    params={"income_year": 2025, "actionable_only": "true"},
                    headers=admin_session.headers(),
                )
            ).json()["items"]
            if item["form_code"] == "D390"
        ]

    await purchase("2025-06-05")  # domestic, taxable, RO supplier
    await client.post(
        f"/api/v1/workspaces/{workspace_id}/obligations/reconcile",
        params={"income_year": 2025},
        headers=admin_session.headers(),
    )
    assert await d390_forms() == []

    await purchase(
        "2025-07-05",
        vat_treatment="reverse_charge",
        supplier_country="DE",
        intracommunity_operation_type="A",
    )
    await client.post(
        f"/api/v1/workspaces/{workspace_id}/obligations/reconcile",
        params={"income_year": 2025},
        headers=admin_session.headers(),
    )
    forms = await d390_forms()
    assert forms, "an intra-community acquisition must raise D390"
    assert forms[0]["applicability"] == "required"
    # an acquisition (A) does not need the partner VAT id, and VIES verification is not
    # something the user can type, so nothing is reported as missing
    assert forms[0]["missing_inputs"] == []

    # an EU purchase with no operation type is blocked on exactly that, and the
    # notification names it in Romanian instead of leaking the storage key
    await purchase("2025-08-05", vat_treatment="reverse_charge", supplier_country="IT")
    await client.post(
        f"/api/v1/workspaces/{workspace_id}/obligations/reconcile",
        params={"income_year": 2025},
        headers=admin_session.headers(),
    )
    forms = await d390_forms()
    # article_316 reports monthly, so the new purchase is its own (August) period
    assert any(
        form["missing_inputs"] == ["intracommunity_operation_type"] for form in forms
    ), forms

    notifications = (
        await client.get(
            f"/api/v1/workspaces/{workspace_id}/notifications",
            params={"income_year": 2025},
            headers=admin_session.headers(),
        )
    ).json()["items"]
    bodies = [
        item["body_ro"]
        for item in notifications
        if item["title_ro"].startswith("D390")
    ]
    assert any("tipul operațiunii intracomunitare" in body for body in bodies)
    assert all("intracommunity_operation_type" not in body for body in bodies)


async def test_d390_retires_with_its_reminder_when_the_evidence_is_deleted(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    """Retirement, not silence: the month stops demanding a stale input."""
    await client.patch(
        f"/api/v1/workspaces/{workspace_id}/fiscal-profile",
        json={
            "vat_registrations": [
                {
                    "kind": "article_316",
                    "vat_code": "RO90000014",
                    "effective_from": "2025-01-01",
                    "reporting_frequency": "monthly",
                }
            ]
        },
        headers=admin_session.headers(),
    )
    created = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        json={
            "kind": "purchase_invoice",
            "supplier_name_raw": "Furnizor DE",
            "supplier_country": "DE",
            "intracommunity_operation_type": "A",
            "issue_date": "2025-07-05",
            "supply_date": "2025-07-05",
            "currency": "RON",
            "vat_treatment": "reverse_charge",
            "lines": [
                {
                    "description": "Marfă",
                    "quantity": "1",
                    "unit_price": "1000",
                    "vat_rate": "21",
                    "vat_treatment": "reverse_charge",
                }
            ],
        },
        headers=admin_session.headers(),
    )
    assert created.status_code == 201, created.text
    document_id = created.json()["id"]
    await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents/{document_id}/finalize",
        json=finalize_payload(),
        headers=admin_session.headers(**{"Idempotency-Key": f"fin-{document_id}"}),
    )

    async def reconcile_and_read() -> tuple[list[dict], list[str]]:
        await client.post(
            f"/api/v1/workspaces/{workspace_id}/obligations/reconcile",
            params={"income_year": 2025},
            headers=admin_session.headers(),
        )
        forms = [
            item
            for item in (
                await client.get(
                    f"/api/v1/workspaces/{workspace_id}/obligations",
                    params={"income_year": 2025, "actionable_only": "true"},
                    headers=admin_session.headers(),
                )
            ).json()["items"]
            if item["form_code"] == "D390"
        ]
        titles = [
            item["title_ro"]
            for item in (
                await client.get(
                    f"/api/v1/workspaces/{workspace_id}/notifications",
                    headers=admin_session.headers(),
                )
            ).json()["items"]
        ]
        return forms, [title for title in titles if title.startswith("D390")]

    forms, titles = await reconcile_and_read()
    assert [form["applicability"] for form in forms] == ["required"], forms
    assert titles, "a D390 reminder is expected while the evidence exists"

    deleted = await client.delete(
        f"/api/v1/workspaces/{workspace_id}/documents/{document_id}",
        headers=admin_session.headers(),
    )
    assert deleted.status_code == 200, deleted.text

    forms, titles = await reconcile_and_read()
    assert forms == [], "the month must stop being actionable"
    assert titles == [], "the withdrawn reminder must leave the feed"


async def test_d394_asks_for_a_partner_only_when_it_cannot_be_classified(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    """The flag must point at real documents, not fire on every quarter."""
    await client.patch(
        f"/api/v1/workspaces/{workspace_id}/fiscal-profile",
        json={
            "vat_registrations": [
                {
                    "kind": "article_316",
                    "vat_code": "RO90000015",
                    "effective_from": "2025-01-01",
                    "reporting_frequency": "quarterly",
                }
            ]
        },
        headers=admin_session.headers(),
    )

    async def sale(day: str, **extra: object) -> str:
        created = await client.post(
            f"/api/v1/workspaces/{workspace_id}/documents",
            json={
                "kind": "sales_invoice",
                "issue_date": day,
                "supply_date": day,
                "currency": "RON",
                "vat_treatment": "standard",
                "lines": [
                    {
                        "description": "Servicii",
                        "quantity": "1",
                        "unit_price": "1000",
                        "vat_rate": "21",
                        "vat_treatment": "standard",
                    }
                ],
                **extra,
            },
            headers=admin_session.headers(),
        )
        assert created.status_code == 201, created.text
        assert created.json()["partner_kind"] == extra.get("partner_kind"), created.text
        document_id = created.json()["id"]
        await client.post(
            f"/api/v1/workspaces/{workspace_id}/documents/{document_id}/finalize",
            json=finalize_payload(),
            headers=admin_session.headers(**{"Idempotency-Key": f"fin-{document_id}"}),
        )
        return document_id

    async def d394_forms() -> list[dict]:
        return [
            item
            for item in (
                await client.get(
                    f"/api/v1/workspaces/{workspace_id}/obligations",
                    params={"income_year": 2025, "actionable_only": "true"},
                    headers=admin_session.headers(),
                )
            ).json()["items"]
            if item["form_code"] == "D394"
        ]

    async def reconcile() -> None:
        await client.post(
            f"/api/v1/workspaces/{workspace_id}/obligations/reconcile",
            params={"income_year": 2025},
            headers=admin_session.headers(),
        )

    # a named client is classifiable, and a quarter with no documents needs nothing
    await sale("2025-08-05", supplier_name_raw="Client SRL", partner_kind="business")
    await reconcile()
    forms = await d394_forms()
    assert forms, "article_316 makes D394 required"
    assert [form["missing_inputs"] for form in forms] == [[]] * len(forms), forms

    # a consumer sale is complete without partner data: nothing to demand
    await sale("2025-11-05", partner_kind="consumer")
    await reconcile()
    assert [form["missing_inputs"] for form in await d394_forms()] == [[]] * len(
        await d394_forms()
    )

    # an unstated partner is not assumed to be a consumer: it stays visible
    unnamed = await sale("2025-08-20")
    await reconcile()
    flagged = [form for form in await d394_forms() if form["missing_inputs"]]
    assert [form["missing_inputs"] for form in flagged] == [["counterparty_classification"]]
    assert flagged[0]["triggering_evidence"] == [unnamed]

    # the same holds for an expense: a purchase from a private individual is complete
    purchase = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        json={
            "kind": "purchase_invoice",
            "issue_date": "2025-11-20",
            "currency": "RON",
            "partner_kind": "consumer",
            "vat_treatment": "standard",
            "lines": [
                {
                    "description": "Materiale",
                    "quantity": "1",
                    "unit_price": "100",
                    "vat_rate": "21",
                    "vat_treatment": "standard",
                }
            ],
        },
        headers=admin_session.headers(),
    )
    assert purchase.status_code == 201, purchase.text
    purchase_id = purchase.json()["id"]
    assert purchase.json()["partner_kind"] == "consumer"
    await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents/{purchase_id}/finalize",
        json=finalize_payload(),
        headers=admin_session.headers(**{"Idempotency-Key": f"fin-{purchase_id}"}),
    )
    await reconcile()
    assert all(
        form["triggering_evidence"] != [purchase_id] for form in await d394_forms()
    ), "a B2C purchase must never be demanded as a D394 partner"

    # cancelling it removes the reason, so the demand goes away with it
    removed = await client.delete(
        f"/api/v1/workspaces/{workspace_id}/documents/{unnamed}",
        headers=admin_session.headers(),
    )
    assert removed.status_code == 200, removed.text
    await reconcile()
    assert [form["missing_inputs"] for form in await d394_forms()] == [[]] * len(
        await d394_forms()
    )
    # the B2C/B2B nature is validated, never silently coerced
    rejected = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        json={
            "kind": "sales_invoice",
            "issue_date": "2025-08-25",
            "currency": "RON",
            "partner_kind": "person",
        },
        headers=admin_session.headers(),
    )
    assert rejected.status_code == 422, rejected.text


async def _nil_d300(client: AsyncClient, session: Session, workspace_id: str) -> str:
    await client.patch(
        f"/api/v1/workspaces/{workspace_id}/fiscal-profile",
        json={
            "vat_registrations": [
                {
                    "kind": "article_316",
                    "vat_code": "RO90000013",
                    "effective_from": "2025-01-01",
                    "reporting_frequency": "monthly",
                }
            ]
        },
        headers=session.headers(),
    )
    declaration = await client.post(
        f"/api/v1/workspaces/{workspace_id}/declarations",
        json={
            "form_code": "D300",
            "income_year": 2025,
            "period_start": "2025-08-01",
            "period_end": "2025-08-31",
        },
        headers=session.headers(),
    )
    declaration_id = declaration.json()["id"]
    await calculate(client, session, declaration_id, f"calc-del-{declaration_id[:8]}")
    return declaration_id


async def test_declaration_can_be_deleted_until_it_is_approved(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    declaration_id = await _nil_d300(client, admin_session, workspace_id)

    deleted = await client.delete(
        f"/api/v1/declarations/{declaration_id}", headers=admin_session.headers()
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["status"] == "deleted"
    assert (
        await client.get(f"/api/v1/declarations/{declaration_id}", headers=admin_session.headers())
    ).status_code == 404
    listed = await client.get(
        f"/api/v1/workspaces/{workspace_id}/declarations", headers=admin_session.headers()
    )
    assert all(item["id"] != declaration_id for item in listed.json()["items"])


async def test_approved_declaration_cannot_be_deleted(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    declaration_id = await _nil_d300(client, admin_session, workspace_id)
    validated = await client.post(
        f"/api/v1/declarations/{declaration_id}/validate", headers=admin_session.headers()
    )
    assert validated.status_code == 200, validated.text
    exported = await client.post(
        f"/api/v1/declarations/{declaration_id}/export",
        headers=admin_session.headers(**{"Idempotency-Key": "exp-del"}),
        json={},
    )
    assert exported.status_code == 200, exported.text
    approved = await client.post(
        f"/api/v1/declarations/{declaration_id}/approve", headers=admin_session.headers()
    )
    assert approved.status_code == 200, approved.text

    blocked = await client.delete(
        f"/api/v1/declarations/{declaration_id}", headers=admin_session.headers()
    )
    assert blocked.status_code == 409
    assert "rectificativ" in blocked.json()["title"].lower()


async def test_deleting_a_declaration_makes_its_obligation_actionable_again(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    """The user still has to file it, so the obligation must return to the dashboard."""
    await client.patch(
        f"/api/v1/workspaces/{workspace_id}/fiscal-profile",
        json={
            "vat_registrations": [
                {
                    "kind": "article_316",
                    "vat_code": "RO90000014",
                    "effective_from": "2025-01-01",
                    "reporting_frequency": "monthly",
                }
            ]
        },
        headers=admin_session.headers(),
    )
    await client.post(
        f"/api/v1/workspaces/{workspace_id}/obligations/reconcile",
        params={"income_year": 2025},
        headers=admin_session.headers(),
    )

    async def obligations() -> list[dict]:
        return (
            await client.get(
                f"/api/v1/workspaces/{workspace_id}/obligations",
                params={"income_year": 2025},
                headers=admin_session.headers(),
            )
        ).json()["items"]

    august = next(
        item
        for item in await obligations()
        if item["form_code"] == "D300" and item["period_start"] == "2025-08-01"
    )
    assert august["applicability"] == "required"
    assert august["declaration_id"] is None

    declaration = await client.post(
        f"/api/v1/workspaces/{workspace_id}/declarations",
        json={
            "form_code": "D300",
            "income_year": 2025,
            "period_start": "2025-08-01",
            "period_end": "2025-08-31",
        },
        headers=admin_session.headers(),
    )
    declaration_id = declaration.json()["id"]
    linked = next(item for item in await obligations() if item["id"] == august["id"])
    assert linked["declaration_id"] == declaration_id

    deleted = await client.delete(
        f"/api/v1/declarations/{declaration_id}", headers=admin_session.headers()
    )
    assert deleted.status_code == 200, deleted.text

    reopened = next(item for item in await obligations() if item["id"] == august["id"])
    assert reopened["applicability"] == "required"
    assert reopened["declaration_id"] is None
    assert reopened["resolution_status"] == "open"

    feed = (
        await client.get(
            f"/api/v1/workspaces/{workspace_id}/notifications",
            headers=admin_session.headers(),
        )
    ).json()["items"]
    assert any(item["obligation_id"] == august["id"] for item in feed), (
        "the reminder must be actionable again"
    )


async def test_efactura_is_not_prepared_as_a_declaration(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    """e-Factura is a transmission task, not a form with a calculation adapter."""
    response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/declarations",
        json={
            "form_code": "EFACTURA",
            "income_year": 2026,
            "period_start": "2026-09-01",
            "period_end": "2026-09-30",
        },
        headers=admin_session.headers(),
    )
    assert response.status_code == 409
    assert "e-Factura" in response.json()["title"]
    assert "nu se pregătește o declarație" in response.json()["title"]


async def test_tax_facts_are_answerable_per_year_and_refresh_the_obligation(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    """Answering the facts the D212 asks for is what clears „Informații lipsă"."""
    await ensure_revenue(client, admin_session, workspace_id)

    catalogue = await client.get(
        f"/api/v1/workspaces/{workspace_id}/tax-facts",
        params={"income_year": 2025},
        headers=admin_session.headers(),
    )
    assert catalogue.status_code == 200, catalogue.text
    body = catalogue.json()
    keys = {item["fact_key"] for item in body["items"]}
    assert {"regime", "cas_election", "cass_election", "prior_losses"} <= keys
    # every fact is self-describing, so the form needs no hardcoded list
    for item in body["items"]:
        assert item["label_ro"] and item["group"]
        assert item["answered"] is False and item["value"] is None
    assert 2025 in body["supported_years"]

    answered = await client.patch(
        f"/api/v1/workspaces/{workspace_id}/tax-facts",
        json={
            "income_year": 2025,
            "answers": [
                {"fact_key": "regime", "value": "real"},
                {"fact_key": "cas_election", "value": False},
                {"fact_key": "cass_election", "value": False},
                {"fact_key": "prior_losses", "value": "0"},
                {"fact_key": "salary_income_present", "value": False},
                {"fact_key": "pension_status", "value": "not_pensioner"},
            ],
        },
        headers=admin_session.headers(),
    )
    assert answered.status_code == 200, answered.text
    stored = {item["fact_key"]: item for item in answered.json()["items"]}
    assert stored["cas_election"]["value"] is False
    assert stored["cas_election"]["answered"] is True
    assert stored["prior_losses"]["value"] == "0.000000" or stored["prior_losses"]["value"] == 0

    # answering refreshes the obligation: no reconcile call is needed
    obligations = (
        await client.get(
            f"/api/v1/workspaces/{workspace_id}/obligations",
            params={"income_year": 2025},
            headers=admin_session.headers(),
        )
    ).json()["items"]
    d212 = next(item for item in obligations if item["form_code"] == "D212")
    assert d212["missing_inputs"] == [], d212["missing_inputs"]

    # clearing an answer returns the fact to unknown instead of zero/"no"
    cleared = await client.patch(
        f"/api/v1/workspaces/{workspace_id}/tax-facts",
        json={"income_year": 2025, "answers": [{"fact_key": "cas_election", "value": None}]},
        headers=admin_session.headers(),
    )
    assert cleared.status_code == 200, cleared.text
    stored = {item["fact_key"]: item for item in cleared.json()["items"]}
    assert stored["cas_election"]["answered"] is False
    assert stored["cas_election"]["value"] is None
    obligations = (
        await client.get(
            f"/api/v1/workspaces/{workspace_id}/obligations",
            params={"income_year": 2025},
            headers=admin_session.headers(),
        )
    ).json()["items"]
    d212 = next(item for item in obligations if item["form_code"] == "D212")
    assert "cas_election" in d212["missing_inputs"]


async def test_tax_facts_reject_unknown_keys_and_wrong_types(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    unknown = await client.patch(
        f"/api/v1/workspaces/{workspace_id}/tax-facts",
        json={"income_year": 2025, "answers": [{"fact_key": "not_a_fact", "value": "x"}]},
        headers=admin_session.headers(),
    )
    assert unknown.status_code == 422, unknown.text
    assert "not_a_fact" in unknown.text

    wrong_type = await client.patch(
        f"/api/v1/workspaces/{workspace_id}/tax-facts",
        json={"income_year": 2025, "answers": [{"fact_key": "cas_election", "value": "poate"}]},
        headers=admin_session.headers(),
    )
    assert wrong_type.status_code == 422, wrong_type.text
    assert wrong_type.json()["field_errors"] == {"cas_election": "expected_boolean"}

    bad_choice = await client.patch(
        f"/api/v1/workspaces/{workspace_id}/tax-facts",
        json={"income_year": 2025, "answers": [{"fact_key": "pension_status", "value": "maybe"}]},
        headers=admin_session.headers(),
    )
    assert bad_choice.status_code == 422, bad_choice.text

    out_of_range = await client.patch(
        f"/api/v1/workspaces/{workspace_id}/tax-facts",
        json={"income_year": 2025, "answers": [{"fact_key": "active_months", "value": "13"}]},
        headers=admin_session.headers(),
    )
    assert out_of_range.status_code == 422, out_of_range.text


async def test_an_expense_without_a_stated_business_use_is_reported_not_assumed(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    """A share the user never stated must not silently multiply the deductible amount."""
    await set_facts(
        client,
        admin_session,
        workspace_id,
        regime=("text", "real"),
        cas_election=("boolean", False),
        cass_election=("boolean", False),
        prior_losses=("decimal", "0"),
        salary_income_present=("boolean", False),
        pension_status=("text", "not_pensioner"),
    )
    await ensure_revenue(client, admin_session, workspace_id)

    async def purchase(day: str, **extra: object) -> str:
        created = await client.post(
            f"/api/v1/workspaces/{workspace_id}/documents",
            json={
                "kind": "purchase_invoice",
                "supplier_name_raw": "Furnizor SRL",
                "issue_date": day,
                "currency": "RON",
                "vat_treatment": "undetermined",
                "deductibility": "fully_deductible",
                "lines": [
                    {
                        "description": "Servicii",
                        "quantity": "1",
                        "unit_price": "1000",
                        "vat_rate": "0",
                        "vat_treatment": "undetermined",
                    }
                ],
                **extra,
            },
            headers=admin_session.headers(),
        )
        assert created.status_code == 201, created.text
        document_id = created.json()["id"]
        stored = created.json()["business_use_percent"]
        assert (stored is None) == (extra.get("business_use_percent") is None)
        await client.post(
            f"/api/v1/workspaces/{workspace_id}/documents/{document_id}/finalize",
            json=finalize_payload(),
            headers=admin_session.headers(**{"Idempotency-Key": f"fin-{document_id}"}),
        )
        # an expense is an input only when a settlement is allocated to it
        await client.post(
            f"/api/v1/workspaces/{workspace_id}/payments",
            json={
                "direction": "outflow",
                "kind": "supplier_payment",
                "paid_on": day,
                "amount": "1000",
                "allocations": [{"document_id": document_id, "amount": "1000"}],
            },
            headers=admin_session.headers(),
        )
        return document_id

    unstated = await purchase("2025-06-11")
    stated = await purchase("2025-06-12", business_use_percent="50")

    # D212 is a personal return, so the taxpayer identity travels with it
    declaration = await prepare(client, admin_session, workspace_id, taxpayer_key="taxpayer-bu")
    calculated = await calculate(client, admin_session, declaration["id"], "calc-business-use")
    # only the stated expense is deducted: 1000 at 50%
    assert Decimal(calculated["calculated_values"]["eligible_expenses"]) == Decimal("500")

    # validation is blocked, and the blocking input names the document that needs a share
    blocked = await client.post(
        f"/api/v1/declarations/{declaration['id']}/validate",
        headers=admin_session.headers(),
    )
    assert blocked.status_code == 409, blocked.text
    assert f"expense_business_use:{unstated}" in blocked.json()["details"]["missing_inputs"]
    assert f"expense_business_use:{stated}" not in blocked.json()["details"]["missing_inputs"]


async def test_registration_forms_are_split_by_event_and_demand_no_fake_input(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    """D070 is the initial registration; a later change is D700, and neither asks for an
    input the user cannot supply."""
    await client.patch(
        f"/api/v1/workspaces/{workspace_id}/fiscal-profile",
        json={
            "vat_registrations": [
                {
                    "kind": "article_316",
                    "vat_code": "RO90000016",
                    "effective_from": "2025-07-01",
                    "reporting_frequency": "monthly",
                }
            ]
        },
        headers=admin_session.headers(),
    )
    await client.post(
        f"/api/v1/workspaces/{workspace_id}/obligations/reconcile",
        params={"income_year": 2025},
        headers=admin_session.headers(),
    )
    items = {
        item["form_code"]: item
        for item in (
            await client.get(
                f"/api/v1/workspaces/{workspace_id}/obligations",
                params={"income_year": 2025},
                headers=admin_session.headers(),
            )
        ).json()["items"]
    }
    # a registration change in a year the activity did not start is D700, not D070
    assert items["D700"]["applicability"] == "needs_information"
    assert items["D700"]["missing_inputs"] == []
    assert items["D700"]["triggering_evidence"]
    assert "modificare a datelor de înregistrare" in items["D700"]["applicability_reason"]
    assert items["D070"]["applicability"] == "not_required"
    assert "nu a început" in items["D070"]["applicability_reason"]

    # the initial registration is claimed only for the year the activity started
    await client.patch(
        f"/api/v1/workspaces/{workspace_id}/fiscal-profile",
        json={"started_on": "2025-03-01"},
        headers=admin_session.headers(),
    )
    await client.post(
        f"/api/v1/workspaces/{workspace_id}/obligations/reconcile",
        params={"income_year": 2025},
        headers=admin_session.headers(),
    )
    items = {
        item["form_code"]: item
        for item in (
            await client.get(
                f"/api/v1/workspaces/{workspace_id}/obligations",
                params={"income_year": 2025},
                headers=admin_session.headers(),
            )
        ).json()["items"]
    }
    assert items["D070"]["applicability"] == "needs_information"
    assert "a început în acest an" in items["D070"]["applicability_reason"]
    assert items["D070"]["missing_inputs"] == []
    # the change is part of the initial registration, so it is not demanded twice
    assert items["D700"]["applicability"] == "not_required"

    # and the next step points at the fiscal profile, not at a declaration we cannot make
    notifications = (
        await client.get(
            f"/api/v1/workspaces/{workspace_id}/notifications",
            headers=admin_session.headers(),
        )
    ).json()["items"]
    registration = [
        item for item in notifications if item["next_step_kind"] == "review_registration"
    ]
    assert registration, notifications
    assert all(item["next_step_ref"] in {"D700", "D070"} for item in registration)


async def test_norm_only_facts_declare_when_they_apply(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    """The form must be able to hide a norm field while the regime is not the norm."""
    catalogue = (
        await client.get(
            f"/api/v1/workspaces/{workspace_id}/tax-facts",
            params={"income_year": 2025},
            headers=admin_session.headers(),
        )
    ).json()["items"]
    items = {item["fact_key"]: item for item in catalogue}
    for key in ("norm_amount", "norm_adjustment_percent", "active_months"):
        assert items[key]["applies_when"] == [{"fact_key": "regime", "values": ["norm"]}], key
    assert items["salary_income_ron"]["applies_when"] == [
        {"fact_key": "salary_income_present", "values": ["true"]}
    ]
    # a fact that always applies says so explicitly
    assert items["cas_election"]["applies_when"] == []


async def test_the_reverse_charge_justification_is_what_unblocks_d301(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    """The classification the D301 asks for is a field on the document, and the block names
    the document it refers to."""
    await client.patch(
        f"/api/v1/workspaces/{workspace_id}/fiscal-profile",
        json={
            "vat_registrations": [
                {
                    "kind": "article_317",
                    "vat_code": "RO90000017",
                    "effective_from": "2025-01-01",
                    "reporting_frequency": "monthly",
                }
            ]
        },
        headers=admin_session.headers(),
    )

    async def purchase(day: str, **extra: object) -> str:
        created = await client.post(
            f"/api/v1/workspaces/{workspace_id}/documents",
            json={
                "kind": "purchase_invoice",
                "supplier_name_raw": "Furnizor DE",
                "supplier_country": "DE",
                "issue_date": day,
                "currency": "RON",
                "vat_treatment": "reverse_charge",
                "lines": [
                    {
                        "description": "Servicii",
                        "quantity": "1",
                        "unit_price": "1000",
                        "vat_rate": "21",
                        "vat_treatment": "reverse_charge",
                    }
                ],
                **extra,
            },
            headers=admin_session.headers(),
        )
        assert created.status_code == 201, created.text
        document_id = created.json()["id"]
        await client.post(
            f"/api/v1/workspaces/{workspace_id}/documents/{document_id}/finalize",
            json=finalize_payload(),
            headers=admin_session.headers(**{"Idempotency-Key": f"fin-{document_id}"}),
        )
        return document_id

    unjustified = await purchase("2025-08-11")
    justified = await purchase(
        "2025-08-12", reverse_charge_justification="achiziție intracomunitară, art. 331"
    )

    declaration = await client.post(
        f"/api/v1/workspaces/{workspace_id}/declarations",
        json={
            "form_code": "D301",
            "income_year": 2025,
            "period_start": "2025-08-01",
            "period_end": "2025-08-31",
        },
        headers=admin_session.headers(),
    )
    assert declaration.status_code == 201, declaration.text
    await calculate(client, admin_session, declaration.json()["id"], "calc-d301-justification")
    blocked = await client.post(
        f"/api/v1/declarations/{declaration.json()['id']}/validate",
        headers=admin_session.headers(),
    )
    assert blocked.status_code == 409, blocked.text
    # the block names the document that needs the justification, not a bare "classification"
    assert blocked.json()["details"]["missing_inputs"] == [f"classification:{unjustified}"]

    # and the justification itself is not flagged
    assert f"classification:{justified}" not in blocked.json()["details"]["missing_inputs"]


async def test_d301_covers_only_the_operations_of_the_special_return(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    """A domestic sale must not block the special return, nor inflate it."""
    await client.patch(
        f"/api/v1/workspaces/{workspace_id}/fiscal-profile",
        json={
            "vat_registrations": [
                {
                    "kind": "article_317",
                    "vat_code": "RO90000019",
                    "effective_from": "2025-01-01",
                    "reporting_frequency": "monthly",
                }
            ]
        },
        headers=admin_session.headers(),
    )

    async def invoice(kind: str, day: str, amount: str, *, treatment: str, **extra: object) -> str:
        created = await client.post(
            f"/api/v1/workspaces/{workspace_id}/documents",
            json={
                "kind": kind,
                "supplier_name_raw": "Partener",
                "issue_date": day,
                "currency": "RON",
                "vat_treatment": treatment,
                "lines": [
                    {
                        "description": "X",
                        "quantity": "1",
                        "unit_price": amount,
                        "vat_rate": "21",
                        "vat_treatment": treatment,
                    }
                ],
                **extra,
            },
            headers=admin_session.headers(),
        )
        assert created.status_code == 201, created.text
        document_id = created.json()["id"]
        await client.post(
            f"/api/v1/workspaces/{workspace_id}/documents/{document_id}/finalize",
            json=finalize_payload(),
            headers=admin_session.headers(**{"Idempotency-Key": f"fin-{document_id}"}),
        )
        return document_id

    # an ordinary sale, with no reverse-charge justification, and a justified acquisition
    sale = await invoice("sales_invoice", "2025-10-06", "1000", treatment="standard")
    purchase = await invoice(
        "purchase_invoice",
        "2025-10-07",
        "2000",
        treatment="reverse_charge",
        supplier_country="DE",
        reverse_charge_justification="achiziție intracomunitară de bunuri, art. 331",
    )

    declaration = await client.post(
        f"/api/v1/workspaces/{workspace_id}/declarations",
        json={
            "form_code": "D301",
            "income_year": 2025,
            "period_start": "2025-10-01",
            "period_end": "2025-10-31",
        },
        headers=admin_session.headers(),
    )
    assert declaration.status_code == 201, declaration.text
    calculated = await calculate(client, admin_session, declaration.json()["id"], "calc-d301-scope")
    rows = calculated["calculated_values"]["rows"]
    assert [row["id"] for row in rows] == [purchase], "the sale is not a D301 operation"
    # only the acquisition's own VAT is in the special return
    assert Decimal(calculated["calculated_values"]["total_vat"]) == Decimal("420")

    validated = await client.post(
        f"/api/v1/declarations/{declaration.json()['id']}/validate",
        headers=admin_session.headers(),
    )
    assert validated.status_code == 200, validated.text
    assert sale not in validated.text


async def test_d394_asks_for_the_partner_not_for_a_reverse_charge_justification(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    """The informative return is about who the partner is."""
    await client.patch(
        f"/api/v1/workspaces/{workspace_id}/fiscal-profile",
        json={
            "vat_registrations": [
                {
                    "kind": "article_316",
                    "vat_code": "RO90000020",
                    "effective_from": "2025-01-01",
                    "reporting_frequency": "quarterly",
                }
            ]
        },
        headers=admin_session.headers(),
    )

    async def invoice(**extra: object) -> str:
        created = await client.post(
            f"/api/v1/workspaces/{workspace_id}/documents",
            json={
                "kind": "sales_invoice",
                "issue_date": "2025-11-10",
                "currency": "RON",
                "vat_treatment": "standard",
                "lines": [
                    {
                        "description": "X",
                        "quantity": "1",
                        "unit_price": "100",
                        "vat_rate": "21",
                        "vat_treatment": "standard",
                    }
                ],
                **extra,
            },
            headers=admin_session.headers(),
        )
        assert created.status_code == 201, created.text
        document_id = created.json()["id"]
        await client.post(
            f"/api/v1/workspaces/{workspace_id}/documents/{document_id}/finalize",
            json=finalize_payload(),
            headers=admin_session.headers(**{"Idempotency-Key": f"fin-{document_id}"}),
        )
        return document_id

    anonymous = await invoice()
    named = await invoice(supplier_name_raw="Client SRL", partner_kind="business")

    declaration = await client.post(
        f"/api/v1/workspaces/{workspace_id}/declarations",
        json={
            "form_code": "D394",
            "income_year": 2025,
            "period_start": "2025-10-01",
            "period_end": "2025-12-31",
        },
        headers=admin_session.headers(),
    )
    assert declaration.status_code == 201, declaration.text
    await calculate(client, admin_session, declaration.json()["id"], "calc-d394-partner")
    blocked = await client.post(
        f"/api/v1/declarations/{declaration.json()['id']}/validate",
        headers=admin_session.headers(),
    )
    assert blocked.status_code == 409, blocked.text
    # the partner is named, and the document that has one is not flagged
    assert blocked.json()["details"]["missing_inputs"] == [
        f"counterparty_classification:{anonymous}"
    ]
    assert named not in blocked.json()["details"]["missing_inputs"]


async def test_d301_asks_for_the_self_assessment_rate_instead_of_reporting_zero(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    """A reverse charge with no recorded rate would make the special return report 0."""
    await client.patch(
        f"/api/v1/workspaces/{workspace_id}/fiscal-profile",
        json={
            "vat_registrations": [
                {
                    "kind": "article_317",
                    "vat_code": "RO90000021",
                    "effective_from": "2025-01-01",
                    "reporting_frequency": "monthly",
                }
            ]
        },
        headers=admin_session.headers(),
    )
    created = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        json={
            "kind": "purchase_invoice",
            "supplier_name_raw": "Furnizor DE",
            "supplier_country": "DE",
            "issue_date": "2025-09-08",
            "currency": "RON",
            "vat_treatment": "reverse_charge",
            "reverse_charge_justification": "achiziție intracomunitară de servicii, art. 331",
            "lines": [
                {
                    "description": "Servicii",
                    "quantity": "1",
                    "unit_price": "1000",
                    "vat_rate": "0",  # the invoice carries no VAT and no rate was recorded
                    "vat_treatment": "reverse_charge",
                }
            ],
        },
        headers=admin_session.headers(),
    )
    assert created.status_code == 201, created.text
    document_id = created.json()["id"]
    await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents/{document_id}/finalize",
        json=finalize_payload(),
        headers=admin_session.headers(**{"Idempotency-Key": f"fin-{document_id}"}),
    )

    declaration = await client.post(
        f"/api/v1/workspaces/{workspace_id}/declarations",
        json={
            "form_code": "D301",
            "income_year": 2025,
            "period_start": "2025-09-01",
            "period_end": "2025-09-30",
        },
        headers=admin_session.headers(),
    )
    assert declaration.status_code == 201, declaration.text
    calculated = await calculate(client, admin_session, declaration.json()["id"], "calc-d301-rate")
    assert Decimal(calculated["calculated_values"]["total_vat"]) == Decimal("0")

    blocked = await client.post(
        f"/api/v1/declarations/{declaration.json()['id']}/validate",
        headers=admin_session.headers(),
    )
    assert blocked.status_code == 409, blocked.text
    # the rate is named, rather than the return silently reporting zero
    assert blocked.json()["details"]["missing_inputs"] == [f"vat_rate:{document_id}"]


async def test_declarations_work_with_a_document_that_has_no_series(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    """An optional series must not leak "None" into a declaration or its artifact."""
    await client.patch(
        f"/api/v1/workspaces/{workspace_id}/fiscal-profile",
        json={
            "vat_registrations": [
                {
                    "kind": "article_316",
                    "vat_code": "RO90000022",
                    "effective_from": "2025-01-01",
                    "reporting_frequency": "monthly",
                }
            ]
        },
        headers=admin_session.headers(),
    )
    created = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        json={
            "kind": "sales_invoice",
            "series": None,
            "number": "2025-555",
            "supplier_name_raw": "Client SRL",
            "partner_kind": "business",
            "issue_date": "2025-05-14",
            "currency": "RON",
            "vat_treatment": "standard",
            "lines": [
                {
                    "description": "Servicii",
                    "quantity": "1",
                    "unit_price": "1000",
                    "vat_rate": "21",
                    "vat_treatment": "standard",
                }
            ],
        },
        headers=admin_session.headers(),
    )
    assert created.status_code == 201, created.text
    assert created.json()["series"] is None
    document_id = created.json()["id"]
    finalized = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents/{document_id}/finalize",
        json={"series": None, "number": "2025-555"},
        headers=admin_session.headers(**{"Idempotency-Key": "fin-no-series-decl"}),
    )
    assert finalized.status_code == 200, finalized.text
    assert finalized.json()["series"] is None

    for form_code in ("D300", "D394"):
        declaration = await client.post(
            f"/api/v1/workspaces/{workspace_id}/declarations",
            json={
                "form_code": form_code,
                "income_year": 2025,
                "period_start": "2025-05-01",
                "period_end": "2025-05-31",
            },
            headers=admin_session.headers(),
        )
        assert declaration.status_code == 201, declaration.text
        calculated = await calculate(
            client, admin_session, declaration.json()["id"], f"calc-no-series-{form_code}"
        )
        values = calculated["calculated_values"]
        if form_code == "D300":
            # the sale is in the return's collected VAT
            assert Decimal(values["collected_total"]) == Decimal("210")
        else:
            assert any(row["id"] == document_id for row in values.get("rows", [])), form_code
        assert "None" not in json.dumps(values), form_code

        exported = await client.post(
            f"/api/v1/declarations/{declaration.json()['id']}/export",
            json={},
            headers=admin_session.headers(
                **{"Idempotency-Key": f"exp-no-series-{form_code}"}
            ),
        )
        assert exported.status_code == 200, exported.text
        artifact = exported.json().get("artifact") or {}
        body = json.dumps(artifact)
        assert "None" not in body, f"{form_code}: {body[:200]}"

    # the outstanding register lists it too
    register = await client.get(
        f"/api/v1/workspaces/{workspace_id}/registers/sales_vat_journal",
        params={"period_start": "2025-01-01", "period_end": "2025-12-31"},
        headers=admin_session.headers(),
    )
    assert register.status_code == 200, register.text
    assert any(row["number"] == "2025-555" for row in register.json()["rows"])


async def test_an_expense_without_a_share_blocks_the_vat_return_by_document(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    """The block names the document, so the user knows which expense to complete."""
    await client.patch(
        f"/api/v1/workspaces/{workspace_id}/fiscal-profile",
        json={
            "vat_registrations": [
                {
                    "kind": "article_316",
                    "vat_code": "RO90000023",
                    "effective_from": "2025-01-01",
                    "reporting_frequency": "monthly",
                }
            ]
        },
        headers=admin_session.headers(),
    )
    created = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        json={
            "kind": "purchase_invoice",
            "supplier_name_raw": "Furnizor SRL",
            "issue_date": "2025-12-09",
            "currency": "RON",
            "vat_treatment": "standard",
            "deductibility": "fully_deductible",
            # no business-use share stated
            "lines": [
                {
                    "description": "Servicii",
                    "quantity": "1",
                    "unit_price": "1000",
                    "vat_rate": "21",
                    "vat_treatment": "standard",
                }
            ],
        },
        headers=admin_session.headers(),
    )
    assert created.status_code == 201, created.text
    document_id = created.json()["id"]
    await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents/{document_id}/finalize",
        json=finalize_payload(),
        headers=admin_session.headers(**{"Idempotency-Key": f"fin-{document_id}"}),
    )

    declaration = await client.post(
        f"/api/v1/workspaces/{workspace_id}/declarations",
        json={
            "form_code": "D300",
            "income_year": 2025,
            "period_start": "2025-12-01",
            "period_end": "2025-12-31",
        },
        headers=admin_session.headers(),
    )
    assert declaration.status_code == 201, declaration.text
    await calculate(client, admin_session, declaration.json()["id"], "calc-share-missing")
    blocked = await client.post(
        f"/api/v1/declarations/{declaration.json()['id']}/validate",
        headers=admin_session.headers(),
    )
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["details"]["missing_inputs"] == [
        f"business_use_percent:{document_id}"
    ]


async def test_every_document_kind_works_without_a_series(
    client: AsyncClient, admin_session: Session, workspace_id: str, database: Database, tenant: TenantFixture
) -> None:
    """A purchase, a credit note and the e-Factura XML must also work with no series."""
    import uuid as uuid_module

    from pfa.anaf import FakeAnafState, FakeAnafTransport
    from pfa.anaf.efactura import transmit_invoice

    await client.patch(
        f"/api/v1/workspaces/{workspace_id}/fiscal-profile",
        json={
            "vat_registrations": [
                {
                    "kind": "article_316",
                    "vat_code": "RO90000024",
                    "effective_from": "2025-01-01",
                    "reporting_frequency": "monthly",
                }
            ]
        },
        headers=admin_session.headers(),
    )

    async def finalize(document_id: str, number: str, *, series: str | None = None) -> dict:
        finalized = await client.post(
            f"/api/v1/workspaces/{workspace_id}/documents/{document_id}/finalize",
            json={"series": series, "number": number},
            headers=admin_session.headers(**{"Idempotency-Key": f"fin-{document_id}"}),
        )
        assert finalized.status_code == 200, finalized.text
        return finalized.json()

    # 1. a purchase with no series, covered by D300 and the purchase VAT journal
    purchase = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        json={
            "kind": "purchase_invoice",
            "series": None,
            "number": "2025-800",
            "supplier_name_raw": "Furnizor SRL",
            "issue_date": "2025-06-18",
            "currency": "RON",
            "vat_treatment": "standard",
            "deductibility": "fully_deductible",
            "business_use_percent": "100",
            "lines": [
                {
                    "description": "Marfă",
                    "quantity": "1",
                    "unit_price": "500",
                    "vat_rate": "21",
                    "vat_treatment": "standard",
                }
            ],
        },
        headers=admin_session.headers(),
    )
    assert purchase.status_code == 201, purchase.text
    await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents/{purchase.json()['id']}/finalize",
        json={"series": None, "number": "2025-800"},
        headers=admin_session.headers(**{"Idempotency-Key": "fin-purchase-no-series"}),
    )

    # 2. a credit note for it, also without a series
    note = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents/{purchase.json()['id']}/credit-notes",
        json={
            "kind": "credit_note",
            "series": None,
            "number": "2025-801",
            "issue_date": "2025-06-20",
            "currency": "RON",
            "vat_treatment": "standard",
            "lines": [
                {
                    "description": "Storno",
                    "quantity": "1",
                    "unit_price": "100",
                    "vat_rate": "21",
                    "vat_treatment": "standard",
                }
            ],
        },
        headers=admin_session.headers(),
    )
    assert note.status_code == 201, note.text
    note_finalized = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents/{note.json()['id']}/finalize",
        json={"series": None, "number": "2025-801"},
        headers=admin_session.headers(**{"Idempotency-Key": "fin-note-no-series"}),
    )
    assert note_finalized.status_code == 200, note_finalized.text

    # 3. the journal and the special return cover both, without leaking "None"
    journal = (
        await client.get(
            f"/api/v1/workspaces/{workspace_id}/registers/purchase_vat_journal",
            params={"period_start": "2025-06-01", "period_end": "2025-06-30"},
            headers=admin_session.headers(),
        )
    ).json()["rows"]
    assert any(row["number"] == "2025-800" for row in journal)
    assert any(row["number"] == "2025-801" for row in journal)
    assert "None" not in json.dumps(journal)

    # 4. the e-Factura XML of a series-less invoice carries its number
    sale = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        json={
            "kind": "sales_invoice",
            "series": None,
            "number": "2025-900",
            "issue_date": "2025-06-24",
            "currency": "RON",
            "vat_treatment": "standard",
            "lines": [
                {
                    "description": "Servicii",
                    "quantity": "1",
                    "unit_price": "1000",
                    "vat_rate": "21",
                    "vat_treatment": "standard",
                }
            ],
        },
        headers=admin_session.headers(),
    )
    assert sale.status_code == 201, sale.text
    sale_id = sale.json()["id"]
    await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents/{sale_id}/finalize",
        json={"series": None, "number": "2025-900"},
        headers=admin_session.headers(**{"Idempotency-Key": "fin-sale-no-series"}),
    )
    state = FakeAnafState()
    async with database.tenant_session(tenant.workspace_id, actor="worker") as session:
        await transmit_invoice(
            session,
            workspace_id=uuid_module.UUID(workspace_id),
            document_id=uuid_module.UUID(sale_id),
            transport=FakeAnafTransport(state),
        )
    xml = state.uploaded_xml[0].decode()
    assert "2025-900" in xml
    assert "None" not in xml

    # 5. both declarations for the period report the documents and no "None"
    declaration = await client.post(
        f"/api/v1/workspaces/{workspace_id}/declarations",
        json={
            "form_code": "D394",
            "income_year": 2025,
            "period_start": "2025-06-01",
            "period_end": "2025-06-30",
        },
        headers=admin_session.headers(),
    )
    assert declaration.status_code == 201, declaration.text
    calculated = await calculate(client, admin_session, declaration.json()["id"], "calc-no-series-all")
    rows = calculated["calculated_values"]["rows"]
    assert {
        purchase.json()["id"],
        note_finalized.json()["id"],
        sale_id,
    } <= {row["id"] for row in rows}
    assert "None" not in json.dumps(calculated["calculated_values"])
    assert json.dumps(rows).count("None") == 0


async def test_the_declarations_list_carries_the_trace_of_latest_version(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    """„Cum s-a calculat?" must work however the page loaded its data."""
    await prepare(client, admin_session, workspace_id)
    await ensure_revenue(client, admin_session, workspace_id)
    declaration = await prepare(client, admin_session, workspace_id, taxpayer_key="taxpayer-tr")
    await calculate(client, admin_session, declaration["id"], "calc-trace-list")
    # a second version so the list must show the *latest* one, not any
    await calculate(client, admin_session, declaration["id"], "calc-trace-latest")

    listed = (
        await client.get(
            f"/api/v1/workspaces/{workspace_id}/declarations", headers=admin_session.headers()
        )
    ).json()["items"]
    entry = next(item for item in listed if item["id"] == declaration["id"])
    assert entry["trace"], "the list must carry the trace"
    assert entry["trace"] == entry.get("trace")
    # the newest version is what the trace belongs to
    assert entry["current_version"] == 2
    assert len(entry["trace"]) > 0
    # every trace line has the fields the panel shows
    for line in entry["trace"]:
        assert line["label_ro"] and line["formula"] and line["value"] and line["legal_reference"]


async def test_d392_is_detected_for_a_non_vat_payer_with_sales(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    """A person with no VAT registration who invoices is in art. 259^1: the annual
    informative return is due by 25 February, and the app detects it from evidence."""
    created = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        json={
            "kind": "sales_invoice",
            "series": None,
            "number": "2025-77",
            "supplier_name_raw": "Client firmă",
            "partner_kind": "business",
            "issue_date": "2025-11-20",
            "currency": "RON",
            "vat_treatment": "undetermined",
            "lines": [
                {
                    "description": "Servicii",
                    "quantity": "1",
                    "unit_price": "1000",
                    "vat_treatment": "undetermined",
                }
            ],
        },
        headers=admin_session.headers(),
    )
    assert created.status_code == 201, created.text
    document_id = created.json()["id"]
    await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents/{document_id}/finalize",
        json=finalize_payload(),
        headers=admin_session.headers(**{"Idempotency-Key": f"fin-{document_id}"}),
    )

    await client.post(
        f"/api/v1/workspaces/{workspace_id}/obligations/reconcile",
        params={"income_year": 2025},
        headers=admin_session.headers(),
    )
    items = {
        item["form_code"]: item
        for item in (
            await client.get(
                f"/api/v1/workspaces/{workspace_id}/obligations",
                params={"income_year": 2025},
                headers=admin_session.headers(),
            )
        ).json()["items"]
    }
    d392 = items.get("D392")
    assert d392 is not None, "non-VAT payer with sales must raise D392"
    assert d392["applicability"] == "needs_information"
    assert d392["missing_inputs"] == ["other_instruments_monthly_totals"]
    assert d392["triggering_evidence"] == [document_id]
    assert "259^1" in d392["applicability_reason"]

    # the reason names the filing deadline the legislature set
    notifications = (
        await client.get(
            f"/api/v1/workspaces/{workspace_id}/notifications", headers=admin_session.headers()
        )
    ).json()["items"]
    assert any(item["title_ro"].startswith("D392") for item in notifications)


async def test_d392_disappears_when_vat_registration_is_active(
    client: AsyncClient, admin_session: Session, workspace_id: str
) -> None:
    """Registering for VAT removes the non-VAT payer return for the year(s) with an open
    registration, without touching the detection of other years."""
    await test_d392_is_detected_for_a_non_vat_payer_with_sales(
        client, admin_session, workspace_id
    )

    # then the PFA registers for VAT effective 2026-01-01 and the 2026 D392 disappears
    await client.patch(
        f"/api/v1/workspaces/{workspace_id}/fiscal-profile",
        json={
            "vat_registrations": [
                {
                    "kind": "article_316",
                    "vat_code": "RO90000025",
                    "effective_from": "2026-01-01",
                    "reporting_frequency": "monthly",
                }
            ]
        },
        headers=admin_session.headers(),
    )
    await client.post(
        f"/api/v1/workspaces/{workspace_id}/obligations/reconcile",
        params={"income_year": 2026},
        headers=admin_session.headers(),
    )
    items = {
        item["form_code"]: item
        for item in (
            await client.get(
                f"/api/v1/workspaces/{workspace_id}/obligations",
                params={"income_year": 2026},
                headers=admin_session.headers(),
            )
        ).json()["items"]
    }
    d392_2026 = items.get("D392")
    assert d392_2026 is None or d392_2026["applicability"] == "not_required"
