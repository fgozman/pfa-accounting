"""Query-count guarantees: no N+1 on the read paths that used to have them.

A functional test cannot tell one grouped statement from twenty per-row lookups;
these tests count the SQL actually executed through the engine, so the cost stays
independent of the number of rows.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import event

from pfa.db import Database
from tests.conftest import Session, TenantFixture, finalize_payload


class StatementCounter:
    """Counts statements executed by the runtime engine, by verb."""

    def __init__(self, database: Database) -> None:
        self.statements: list[str] = []
        self._database = database

    def __enter__(self) -> StatementCounter:
        event.listen(self._database.engine.sync_engine, "before_cursor_execute", self._record)
        return self

    def __exit__(self, *_exc: object) -> bool:
        event.remove(self._database.engine.sync_engine, "before_cursor_execute", self._record)
        return False

    def _record(self, _conn, _cursor, statement, _params, _context, _many) -> None:
        self.statements.append(statement.split("\n", 1)[0][:60])

    def selects(self) -> list[str]:
        return [s for s in self.statements if s.upper().startswith("SELECT") or "SELECT" in s[:20]]

    def count(self, verb: str) -> int:
        return sum(1 for s in self.statements if verb.upper() in s.upper())


async def _make_expenses(
    client: AsyncClient, session: Session, workspace_id: str, count: int
) -> None:
    for index in range(count):
        created = await client.post(
            f"/api/v1/workspaces/{workspace_id}/documents",
            json={
                "kind": "purchase_invoice",
                "supplier_name_raw": f"Furnizor {index}",
                "issue_date": "2026-03-05",
                "currency": "RON",
                "vat_treatment": "undetermined",
                "external_id": f"n1-{uuid.uuid4()}",
                "lines": [
                    {"description": "Serviciu", "quantity": "1", "unit_price": "100", "vat_rate": "0"}
                ],
            },
            headers=session.headers(),
        )
        assert created.status_code == 201, created.text
        await client.post(
            f"/api/v1/workspaces/{workspace_id}/documents/{created.json()['id']}/finalize",
            json=finalize_payload(),
            headers=session.headers(**{"Idempotency-Key": f"n1-finalize-{uuid.uuid4()}"}),
        )


@pytest.mark.parametrize("documents", [1, 8])
async def test_outstanding_register_uses_a_constant_number_of_queries(
    client: AsyncClient,
    database: Database,
    admin_session: Session,
    workspace_id: str,
    documents: int,
) -> None:
    await _make_expenses(client, admin_session, workspace_id, documents)

    with StatementCounter(database) as counter:
        response = await client.get(
            f"/api/v1/workspaces/{workspace_id}/registers/outstanding_payables",
            params={"period_start": "2026-01-01", "period_end": "2026-12-31"},
            headers=admin_session.headers(),
        )
    assert response.status_code == 200
    assert response.json()["count"] == documents
    # one statement for the register (plus the session/membership probes), not one
    # per document
    assert counter.count("settled") <= 2, counter.statements


@pytest.mark.parametrize("assets", [1, 6])
async def test_asset_register_uses_a_constant_number_of_queries(
    client: AsyncClient,
    database: Database,
    admin_session: Session,
    workspace_id: str,
    assets: int,
) -> None:
    for index in range(assets):
        created = await client.post(
            f"/api/v1/workspaces/{workspace_id}/assets",
            json={
                "name": f"Laptop {index}",
                "category": "computers",
                "acquisition_date": "2026-02-01",
                "in_service_date": "2026-02-01",
                "acquisition_cost": "3000",
                "acquisition_cost_ron": "3000",
                "business_use_percent": "100",
                "useful_life_months": 36,
            },
            headers=admin_session.headers(),
        )
        assert created.status_code == 201, created.text

    with StatementCounter(database) as counter:
        response = await client.get(
            f"/api/v1/workspaces/{workspace_id}/assets", headers=admin_session.headers()
        )
    assert response.status_code == 200, response.text
    assert len(response.json()["items"]) == assets
    # the depreciation aggregate is joined in the same statement
    assert counter.count("depreciation_entries") <= 1, counter.statements


@pytest.mark.parametrize("members", [2, 5])
async def test_member_list_joins_accounts_in_one_query(
    client: AsyncClient,
    database: Database,
    tenant: TenantFixture,
    admin_session: Session,
    workspace_id: str,
    members: int,
) -> None:
    from sqlalchemy import text

    from tests.conftest import create_verified_user

    for index in range(members - 2):
        email = f"extra{index}-{uuid.uuid4().hex[:6]}@example.com"
        user_id = await create_verified_user(database, email=email)
        async with database.tenant_session(tenant.workspace_id, actor="user") as session:
            await session.execute(
                text(
                    """
                    INSERT INTO memberships (workspace_id, user_id, role, status)
                    VALUES (:workspace_id, :user_id, 'DEFAULT', 'active')
                    """
                ),
                {"workspace_id": tenant.workspace_id, "user_id": uuid.UUID(user_id)},
            )

    with StatementCounter(database) as counter:
        response = await client.get(
            f"/api/v1/workspaces/{workspace_id}/members", headers=admin_session.headers()
        )
    body = response.json()
    assert response.status_code == 200
    assert len(body["items"]) == members
    # one join, not one lookup per member
    assert counter.count("FROM users") <= 1, counter.statements


@pytest.mark.parametrize("documents", [1, 6])
async def test_close_checklist_does_not_look_up_evidence_per_document(
    client: AsyncClient,
    database: Database,
    admin_session: Session,
    workspace_id: str,
    documents: int,
) -> None:
    await _make_expenses(client, admin_session, workspace_id, documents)

    with StatementCounter(database) as counter:
        response = await client.get(
            f"/api/v1/workspaces/{workspace_id}/registers/close-checklist",
            params={"period_start": "2026-01-01", "period_end": "2026-12-31"},
            headers=admin_session.headers(),
        )
    assert response.status_code == 200
    assert response.json()["blockers"]["missing_evidence"]
    # NOT EXISTS instead of an attachment lookup per purchase document
    assert counter.count("FROM attachments") <= 1, counter.statements
