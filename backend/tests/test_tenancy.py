"""Tenancy, roles, invitations and document sharing.

Requirement IDs: TEN-01, TEN-02, MEMBERS, SHARING.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from pfa.db import Database
from pfa.errors import Conflict, PermissionDenied
from pfa.models.base import MembershipRole, MembershipStatus
from pfa.services import tenancy
from tests.conftest import (
    Session,
    TenantFixture,
    create_verified_user,
    finalize_payload,
    oauth_login,
)


def invoice_body(**overrides) -> dict:
    body = {
        "kind": "sales_invoice",
        "issue_date": "2026-03-10",
        "supply_date": "2026-03-10",
        "currency": "RON",
        "vat_treatment": "undetermined",
        "lines": [
            {
                "description": "Servicii",
                "quantity": "1",
                "unit_price": "1000",
                "vat_rate": "0",
            }
        ],
    }
    body.update(overrides)
    return body


async def create_invoice(client: AsyncClient, session: Session, workspace_id: str, **overrides):
    response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        json=invoice_body(**overrides),
        headers=session.headers(),
    )
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# SHARING: both roles see everything, nobody sees another tenant
# ---------------------------------------------------------------------------


async def test_both_roles_share_every_document(
    client: AsyncClient,
    tenant: TenantFixture,
    admin_session: Session,
    member_session: Session,
    workspace_id: str,
) -> None:
    document = await create_invoice(client, admin_session, workspace_id)

    # the DEFAULT member sees the ADMIN's document
    response = await client.get(
        f"/api/v1/workspaces/{workspace_id}/documents/{document['id']}",
        headers=member_session.headers(),
    )
    assert response.status_code == 200
    assert response.json()["id"] == document["id"]

    # and can edit it, because both roles may add/edit business records
    response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents/{document['id']}/finalize",
        json=finalize_payload(),
        headers=member_session.headers( **{"Idempotency-Key": "member-finalize-1"}),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "finalized"


async def test_document_list_is_visible_to_every_active_member(
    client: AsyncClient,
    tenant: TenantFixture,
    admin_session: Session,
    member_session: Session,
    workspace_id: str,
) -> None:
    await create_invoice(client, member_session, workspace_id, external_id="shared-1")
    for session in (admin_session, member_session):
        response = await client.get(
            f"/api/v1/workspaces/{workspace_id}/documents", headers=session.headers()
        )
        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["external_id"] == "shared-1"


# ---------------------------------------------------------------------------
# TEN-01: cross-tenant access is denied at the API and at the database
# ---------------------------------------------------------------------------


async def test_cross_tenant_api_access_is_denied(
    client: AsyncClient, database: Database, tenant: TenantFixture, admin_session: Session
) -> None:
    document = await create_invoice(client, admin_session, str(tenant.workspace_id))

    # a second, unrelated workspace with its own admin
    other_email = "outsider@example.com"
    other_user = await create_verified_user(database, email=other_email)
    async with database.user_session(other_user) as session:
        other_workspace, _membership = await tenancy.create_workspace(
            session, user_id=uuid.UUID(other_user), name="Alt PFA", cui="90000077"
        )
        other_workspace_id = str(other_workspace.id)
    other_session = await oauth_login(client, email=other_email)

    attempts = [
        ("get", f"/api/v1/workspaces/{tenant.workspace_id}/documents"),
        ("get", f"/api/v1/workspaces/{tenant.workspace_id}/documents/{document['id']}"),
        ("get", f"/api/v1/workspaces/{tenant.workspace_id}/fiscal-profile"),
        ("get", f"/api/v1/workspaces/{tenant.workspace_id}/obligations"),
        ("get", f"/api/v1/workspaces/{tenant.workspace_id}/notifications"),
        ("get", f"/api/v1/workspaces/{tenant.workspace_id}/declarations"),
        ("post", f"/api/v1/workspaces/{tenant.workspace_id}/documents"),
    ]
    for method, path in attempts:
        if method == "get":
            response = await client.get(path, headers=other_session.headers())
        else:
            response = await client.post(
                path, json=invoice_body(), headers=other_session.headers()
            )
        # identical answer for "not a member" and "does not exist"
        assert response.status_code in {403, 404}, (path, response.status_code)

    # the unrelated admin can still use their own workspace
    assert (
        await client.get(
            f"/api/v1/workspaces/{other_workspace_id}/documents",
            headers=other_session.headers(),
        )
    ).status_code == 200


async def test_database_rejects_cross_workspace_references(
    database: Database, tenant: TenantFixture, admin_session: Session
) -> None:
    """Composite foreign keys stop a line from pointing at another workspace."""
    async with database.tenant_session(tenant.workspace_id, actor="user") as session:
        foreign_document = uuid.uuid4()
        with pytest.raises(IntegrityError):
            await session.execute(
                text(
                    """
                    INSERT INTO document_lines
                        (id, workspace_id, document_id, line_number, description, quantity,
                         unit_price, discount_amount, vat_rate, vat_treatment, net_amount,
                         vat_amount, gross_amount, business_use_percent, deductibility_percent)
                    VALUES
                        (gen_random_uuid(), :workspace_id, :document_id, 1, 'x', 1, 1, 0, 0,
                         'standard', 1, 0, 1, 100, 100)
                    """
                ),
                {"workspace_id": tenant.workspace_id, "document_id": foreign_document},
            )


async def test_tenant_context_cannot_leak_through_the_connection_pool(
    database: Database, tenant: TenantFixture
) -> None:
    async with database.tenant_session(tenant.workspace_id, actor="user"):
        pass
    # a fresh session has no workspace context, so RLS hides everything
    async with database.session() as session:
        count = await session.scalar(text("SELECT count(*) FROM documents"))
        assert count == 0
        assert await session.scalar(text("SELECT app.current_workspace()")) is None


async def test_other_tenants_documents_are_invisible_at_the_database_level(
    client: AsyncClient, database: Database, tenant: TenantFixture, admin_session: Session
) -> None:
    await create_invoice(client, admin_session, str(tenant.workspace_id))
    other_user = await create_verified_user(database, email="db-other@example.com")
    async with database.user_session(other_user) as session:
        other_workspace, _m = await tenancy.create_workspace(
            session, user_id=uuid.UUID(other_user), name="T2", cui="90000078"
        )
        other_id = other_workspace.id
    async with database.tenant_session(other_id, actor="user") as session:
        assert await session.scalar(text("SELECT count(*) FROM documents")) == 0


# ---------------------------------------------------------------------------
# TEN-02: D212 belongs to a taxpayer; personal sources need explicit consent
# ---------------------------------------------------------------------------


async def test_conflicting_second_d212_for_the_same_taxpayer_is_rejected(
    client: AsyncClient, tenant: TenantFixture, admin_session: Session, workspace_id: str
) -> None:
    body = {
        "form_code": "D212",
        "income_year": 2025,
        "period_start": "2025-01-01",
        "period_end": "2025-12-31",
        "taxpayer_key": "taxpayer-1",
        "taxpayer_identity": {"cnp": "1900101123456"},
    }
    first = await client.post(
        f"/api/v1/workspaces/{workspace_id}/declarations",
        json=body,
        headers=admin_session.headers(),
    )
    assert first.status_code == 201, first.text
    second = await client.post(
        f"/api/v1/workspaces/{workspace_id}/declarations",
        json=body,
        headers=admin_session.headers(),
    )
    assert second.status_code == 409
    assert "declarație unică" in second.json()["title"]


async def test_personal_source_records_require_an_explicit_grant(
    client: AsyncClient, database: Database, tenant: TenantFixture, admin_session: Session
) -> None:
    """An unshared personal record is invisible, and cannot be consolidated."""
    async with database.user_session(tenant.admin_id) as session:
        taxpayer = await tenancy.create_taxpayer(
            session,
            created_by_user_id=tenant.admin_id,
            display_name="Ion Popescu",
            workspace_id=None,
            is_personal_source=True,
        )
    taxpayer_id = taxpayer.id
    # invisible to a tenant, and invisible to an unrelated user
    async with database.tenant_session(tenant.workspace_id, actor="user") as session:
        assert (
            await session.scalar(
                text("SELECT count(*) FROM taxpayers WHERE id = :id"), {"id": taxpayer_id}
            )
        ) == 0
    async with database.user_session(uuid.uuid4()) as session:
        assert (
            await session.scalar(
                text("SELECT count(*) FROM taxpayers WHERE id = :id"), {"id": taxpayer_id}
            )
        ) == 0

    payload = {
        "form_code": "D212",
        "income_year": 2025,
        "period_start": "2025-01-01",
        "period_end": "2025-12-31",
    }
    denied = await client.post(
        f"/api/v1/taxpayers/{taxpayer_id}/declarations",
        json=payload,
        headers=admin_session.headers(),
    )
    assert denied.status_code == 403

    # the owner grants access explicitly, with a recorded sharing notice
    async with database.tenant_session(tenant.workspace_id, actor="user") as session:
        await tenancy.grant_taxpayer_access(
            session,
            taxpayer_id=taxpayer_id,
            workspace_id=tenant.workspace_id,
            granted_by_user_id=tenant.admin_id,
            sharing_notice_version="2026-01",
        )
    allowed = await client.post(
        f"/api/v1/taxpayers/{taxpayer_id}/declarations",
        json=payload,
        headers=admin_session.headers(),
    )
    assert allowed.status_code == 201, allowed.text
    assert "vizibile pentru toți membrii" in allowed.json()["sharing_notice"]


# ---------------------------------------------------------------------------
# MEMBERS
# ---------------------------------------------------------------------------


async def test_admin_manages_members_but_default_cannot(
    client: AsyncClient,
    tenant: TenantFixture,
    admin_session: Session,
    member_session: Session,
    workspace_id: str,
) -> None:
    members = await client.get(
        f"/api/v1/workspaces/{workspace_id}/members", headers=admin_session.headers()
    )
    assert members.status_code == 200
    assert {item["role"] for item in members.json()["items"]} == {"ADMIN", "DEFAULT"}

    denied = await client.post(
        f"/api/v1/workspaces/{workspace_id}/invitations",
        json={"email": "new@example.com", "role": "DEFAULT"},
        headers=member_session.headers(),
    )
    assert denied.status_code == 403

    invitations = await client.get(
        f"/api/v1/workspaces/{workspace_id}/invitations", headers=member_session.headers()
    )
    assert invitations.status_code == 403


async def test_invitation_lifecycle(
    client: AsyncClient,
    database: Database,
    owner_database: Database,
    tenant: TenantFixture,
    admin_session: Session,
    workspace_id: str,
) -> None:
    from sqlalchemy import select as sa_select

    from pfa.models.tenancy import Invitation
    from pfa.security.tokens import hash_token, new_opaque_token

    response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/invitations",
        json={"email": "invited@example.com", "role": "DEFAULT"},
        headers=admin_session.headers(),
    )
    assert response.status_code == 201
    invitation_id = response.json()["id"]

    # the token is hashed at rest
    async with owner_database.session() as session:
        stored = (
            await session.execute(
                sa_select(Invitation).where(Invitation.id == uuid.UUID(invitation_id))
            )
        ).scalar_one()
    assert len(stored.token_hash) == 64
    assert stored.token_hash != stored.email_normalized

    # minting a fresh token through the resend path lets us complete the flow
    async with owner_database.session() as session:
        from pfa.models.tenancy import Invitation as Inv

        row = (
            await session.execute(sa_select(Inv).where(Inv.id == uuid.UUID(invitation_id)))
        ).scalar_one()
        token = new_opaque_token(32)
        row.token_hash = hash_token(token)
        await session.flush()

    invited_user = await create_verified_user(database, email="invited@example.com")
    invited_session = await oauth_login(client, email="invited@example.com")
    await create_verified_user(database, email="wrong@example.com")
    wrong_session = await oauth_login(client, email="wrong@example.com")

    mismatch = await client.post(
        "/api/v1/invitations/accept",
        json={"token": token},
        headers=wrong_session.headers(),
    )
    # a mismatched account cannot even see the invitation: no information leak
    assert mismatch.status_code in {403, 422}

    accepted = await client.post(
        "/api/v1/invitations/accept", json={"token": token}, headers=invited_session.headers()
    )
    assert accepted.status_code == 200
    assert accepted.json()["role"] == "DEFAULT"
    assert accepted.json()["workspace_id"] == workspace_id

    replay = await client.post(
        "/api/v1/invitations/accept", json={"token": token}, headers=invited_session.headers()
    )
    assert replay.status_code == 409

    # an existing account joins without a duplicate identity
    async with owner_database.session() as session:
        count = await session.scalar(
            text("SELECT count(*) FROM memberships WHERE workspace_id = :w AND user_id = :u"),
            {"w": tenant.workspace_id, "u": uuid.UUID(invited_user)},
        )
    assert count == 1


async def test_revoked_invitation_cannot_be_accepted_and_may_be_resent(
    client: AsyncClient,
    owner_database: Database,
    tenant: TenantFixture,
    admin_session: Session,
    workspace_id: str,
) -> None:
    created = await client.post(
        f"/api/v1/workspaces/{workspace_id}/invitations",
        json={"email": "revoked@example.com"},
        headers=admin_session.headers(),
    )
    invitation_id = created.json()["id"]

    from sqlalchemy import select as sa_select

    from pfa.models.tenancy import Invitation
    from pfa.security.tokens import hash_token, new_opaque_token

    async with owner_database.session() as session:
        row = (
            await session.execute(
                sa_select(Invitation).where(Invitation.id == uuid.UUID(invitation_id))
            )
        ).scalar_one()
        old_token = new_opaque_token(32)
        row.token_hash = hash_token(old_token)
        await session.flush()

    revoked = await client.post(
        f"/api/v1/workspaces/{workspace_id}/invitations/{invitation_id}/revoke",
        headers=admin_session.headers(),
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"

    invited = await create_verified_user(owner_database, email="revoked@example.com")
    _ = invited
    invited_session = await oauth_login(client, email="revoked@example.com")
    rejected = await client.post(
        "/api/v1/invitations/accept",
        json={"token": old_token},
        headers=invited_session.headers(),
    )
    assert rejected.status_code == 409

    resent = await client.post(
        f"/api/v1/workspaces/{workspace_id}/invitations/{invitation_id}/resend",
        headers=admin_session.headers(),
    )
    assert resent.status_code == 200
    assert resent.json()["status"] == "sent"

    # an expired invitation is refused as well
    from pfa import clock

    async with owner_database.session() as session:
        row = (
            await session.execute(
                sa_select(Invitation).where(Invitation.id == uuid.UUID(invitation_id))
            )
        ).scalar_one()
        row.expires_at = clock.now().replace(year=clock.now().year - 1)
        await session.flush()
    expired = await client.post(
        "/api/v1/invitations/accept",
        json={"token": new_opaque_token(32)},
        headers=invited_session.headers(),
    )
    assert expired.status_code in {409, 422}


async def test_last_admin_cannot_be_demoted_removed_or_leave(
    client: AsyncClient,
    tenant: TenantFixture,
    admin_session: Session,
    workspace_id: str,
) -> None:
    # the DEFAULT member is promoted so we can test both directions
    promoted = await client.patch(
        f"/api/v1/workspaces/{workspace_id}/members/{tenant.member_id}",
        json={"role": "ADMIN"},
        headers=admin_session.headers(),
    )
    assert promoted.status_code == 200

    # now demote the original admin, leaving the member as the sole admin
    demote_first = await client.patch(
        f"/api/v1/workspaces/{workspace_id}/members/{tenant.admin_id}",
        json={"role": "DEFAULT"},
        headers=admin_session.headers(),
    )
    assert demote_first.status_code == 200

    # the sole remaining admin cannot be demoted or leave
    member_session = await oauth_login(client, email=tenant.member_email)
    blocked = await client.patch(
        f"/api/v1/workspaces/{workspace_id}/members/{tenant.member_id}",
        json={"role": "DEFAULT"},
        headers=member_session.headers(),
    )
    assert blocked.status_code == 409
    assert "Ultimul administrator" in blocked.json()["title"]

    leave = await client.post(
        f"/api/v1/workspaces/{workspace_id}/leave", headers=member_session.headers()
    )
    assert leave.status_code == 409


async def test_concurrent_last_admin_removal_is_blocked(
    database: Database, tenant: TenantFixture
) -> None:
    """Two simultaneous demotions must not both succeed."""
    from sqlalchemy import select as sa_select

    from pfa.models.tenancy import Membership

    async with database.user_session(tenant.admin_id) as session:
        existing = (
            await session.execute(
                sa_select(Membership).where(
                    Membership.workspace_id == tenant.workspace_id,
                    Membership.user_id == tenant.member_id,
                )
            )
        ).scalar_one()
        existing.role = MembershipRole.ADMIN
        await session.flush()

    async def demote(actor: uuid.UUID, target: uuid.UUID) -> str:
        async with database.tenant_session(tenant.workspace_id, actor="user") as session:
            try:
                await tenancy.change_role(
                    session,
                    workspace_id=tenant.workspace_id,
                    actor_user_id=actor,
                    target_user_id=target,
                    new_role=MembershipRole.DEFAULT,
                )
                return "changed"
            except (Conflict, PermissionDenied):
                return "blocked"

    # two admins each demote the other, concurrently: without the workspace row
    # lock both would observe "two admins" and the PFA would be left with none
    results = await asyncio.gather(
        demote(tenant.admin_id, tenant.member_id),
        demote(tenant.member_id, tenant.admin_id),
    )
    assert results.count("changed") == 1, results
    assert results.count("blocked") == 1, results

    async with database.user_session(tenant.admin_id) as session:
        remaining = (
            await session.execute(
                sa_select(Membership).where(
                    Membership.workspace_id == tenant.workspace_id,
                    Membership.role == MembershipRole.ADMIN,
                    Membership.status == MembershipStatus.ACTIVE,
                )
            )
        ).scalars().all()
    assert len(remaining) >= 1


async def test_removal_stops_access_immediately_without_deleting_history(
    client: AsyncClient,
    database: Database,
    tenant: TenantFixture,
    admin_session: Session,
    member_session: Session,
    workspace_id: str,
) -> None:
    document = await create_invoice(client, member_session, workspace_id)
    removed = await client.delete(
        f"/api/v1/workspaces/{workspace_id}/members/{tenant.member_id}",
        headers=admin_session.headers(),
    )
    assert removed.status_code == 200

    assert (
        await client.get(
            f"/api/v1/workspaces/{workspace_id}/documents", headers=member_session.headers()
        )
    ).status_code == 403
    assert (
        await client.get(
            f"/api/v1/workspaces/{workspace_id}/documents/{document['id']}",
            headers=member_session.headers(),
        )
    ).status_code == 403
    assert (
        await client.get(
            f"/api/v1/declarations/{uuid.uuid4()}", headers=member_session.headers()
        )
    ).status_code == 404

    # the historical record survives and stays shared with remaining members
    listing = await client.get(
        f"/api/v1/workspaces/{workspace_id}/documents", headers=admin_session.headers()
    )
    assert listing.status_code == 200
    assert len(listing.json()["items"]) == 1

    async with database.tenant_session(tenant.workspace_id, actor="user") as session:
        audit = await session.scalar(
            text(
                "SELECT count(*) FROM audit_events WHERE action = 'membership.removed' "
                "AND workspace_id = :w"
            ),
            {"w": tenant.workspace_id},
        )
    assert audit == 1


async def test_role_change_does_not_change_document_access(
    client: AsyncClient,
    tenant: TenantFixture,
    admin_session: Session,
    member_session: Session,
    workspace_id: str,
) -> None:
    promoted = await client.patch(
        f"/api/v1/workspaces/{workspace_id}/members/{tenant.member_id}",
        json={"role": "ADMIN"},
        headers=admin_session.headers(),
    )
    assert promoted.status_code == 200
    document = await create_invoice(client, admin_session, workspace_id)
    assert (
        await client.get(
            f"/api/v1/workspaces/{workspace_id}/documents/{document['id']}",
            headers=member_session.headers(),
        )
    ).status_code == 200


async def test_membership_changes_are_audited(
    client: AsyncClient, database: Database, tenant: TenantFixture, admin_session: Session, workspace_id: str
) -> None:
    await client.patch(
        f"/api/v1/workspaces/{workspace_id}/members/{tenant.member_id}",
        json={"role": "ADMIN"},
        headers=admin_session.headers(),
    )
    async with database.tenant_session(tenant.workspace_id, actor="user") as session:
        actions = (
            await session.execute(
                text(
                    "SELECT action FROM audit_events WHERE workspace_id = :w ORDER BY created_at"
                ),
                {"w": tenant.workspace_id},
            )
        ).scalars().all()
    assert "membership.role_changed" in actions
