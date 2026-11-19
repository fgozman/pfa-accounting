"""Workspaces, fiscal profile, members, invitations and notifications."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field

from pfa.api.deps import (
    Principal,
    UserSession,
    admin_workspace_session,
    require_scope,
    workspace_session,
)
from pfa.api.schemas import (
    DashboardResponse,
    MemberListResponse,
    NotificationListResponse,
    ObligationListResponse,
    WorkspaceCreatedResponse,
    WorkspaceListResponse,
)
from pfa.errors import ValidationFailed
from pfa.models.base import MembershipRole
from pfa.services import dashboard as dashboard_service
from pfa.services import facts as facts_service
from pfa.services import fiscal_profile as fiscal_profile_service
from pfa.services import notifications as notifications_service
from pfa.services import obligations as obligations_service
from pfa.services import tenancy
from pfa.services.audit import record_audit

router = APIRouter(prefix="/api/v1", tags=["workspaces"])


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    cui: str | None = Field(default=None, max_length=20)


class TaxFactAnswer(BaseModel):
    fact_key: str = Field(max_length=64)
    value: bool | float | int | str | None = None


class TaxFactsPatch(BaseModel):
    income_year: int
    answers: list[TaxFactAnswer]


class FiscalProfilePatch(BaseModel):
    legal_name: str | None = None
    cui: str | None = None
    #: when the activity started; drives whether the initial registration is due
    started_on: date | None = None
    county: str | None = None
    locality: str | None = None
    iban: str | None = None
    registered_address: str | None = None
    fiscal_domicile: str | None = None
    phone: str | None = None
    vat_registrations: list[dict[str, Any]] | None = None
    tax_regimes: list[dict[str, Any]] | None = None
    year_facts: list[dict[str, Any]] | None = None


class InvitationCreate(BaseModel):
    email: str
    role: MembershipRole = MembershipRole.DEFAULT


class RoleChange(BaseModel):
    role: MembershipRole


class NotificationStateAction(BaseModel):
    until: date | None = None


@router.post("/workspaces", status_code=status.HTTP_201_CREATED)
async def create_workspace(
    payload: WorkspaceCreate,
    principal: Annotated[Principal, Depends(require_scope("pfa.manage"))],
    session: UserSession,
) -> WorkspaceCreatedResponse:
    workspace, membership = await tenancy.create_workspace(
        session, user_id=principal.user_id, name=payload.name, cui=payload.cui
    )
    return WorkspaceCreatedResponse(
        id=str(workspace.id),
        name=workspace.name,
        cui=workspace.cui,
        role=str(membership.role),
        accounting_currency=workspace.accounting_currency,
    )


@router.get("/workspaces")
async def list_workspaces(
    principal: Annotated[Principal, Depends(require_scope("pfa.read"))],
    session: UserSession,
) -> WorkspaceListResponse:
    workspaces = await tenancy.list_workspaces(session, principal.user_id)
    items = []
    for workspace in workspaces:
        membership = await tenancy.get_membership(
            session, workspace_id=workspace.id, user_id=principal.user_id
        )
        items.append(
            {
                "id": str(workspace.id),
                "name": workspace.name,
                "cui": workspace.cui,
                "role": str(membership.role) if membership else None,
                "accounting_currency": workspace.accounting_currency,
                "timezone": workspace.timezone,
            }
        )
    return WorkspaceListResponse(items=items)


@router.get("/workspaces/{workspace_id}/tax-facts")
async def list_tax_facts(
    workspace_id: uuid.UUID,
    session: Annotated[Any, Depends(workspace_session)],
    income_year: int | None = Query(default=None),
) -> dict:
    """Every fiscal fact the user can answer for a year, with the current answer.

    The catalogue comes from the backend so the form cannot drift from what the
    declarations actually read.
    """
    year = income_year or date.today().year
    return {
        "income_year": year,
        "items": await facts_service.list_facts(
            session, workspace_id=workspace_id, income_year=year
        ),
        "supported_years": facts_service.supported_years(),
    }


@router.patch("/workspaces/{workspace_id}/tax-facts")
async def answer_tax_facts(
    workspace_id: uuid.UUID,
    payload: TaxFactsPatch,
    session: Annotated[Any, Depends(workspace_session)],
    principal: Annotated[Principal, Depends(require_scope("pfa.write"))],
) -> dict:
    """Answer the per-year fiscal facts and refresh the obligations they block.

    An empty value clears an answer back to *unknown*: an unanswered fact keeps blocking
    D212 instead of silently becoming zero or "no".
    """
    items = await facts_service.answer_facts(
        session,
        workspace_id=workspace_id,
        actor_user_id=principal.user_id,
        income_year=payload.income_year,
        answers={answer.fact_key: answer.value for answer in payload.answers},
    )
    await record_audit(
        session,
        action="tax_facts.updated",
        entity_type="taxpayer_year_fact",
        entity_id=workspace_id,
        workspace_id=workspace_id,
        actor_user_id=principal.user_id,
        after={
            "income_year": payload.income_year,
            "answered": sorted(
                answer.fact_key for answer in payload.answers if answer.value is not None
            ),
            "cleared": sorted(
                answer.fact_key for answer in payload.answers if answer.value is None
            ),
        },
    )
    return {"income_year": payload.income_year, "items": items}


@router.get("/workspaces/{workspace_id}/fiscal-profile")
async def get_fiscal_profile(
    workspace_id: uuid.UUID,
    session: Annotated[Any, Depends(workspace_session)],
    income_year: int | None = None,
) -> dict:
    return await fiscal_profile_service.get_fiscal_profile(
        session, workspace_id=workspace_id, income_year=income_year
    )


@router.patch("/workspaces/{workspace_id}/fiscal-profile")
async def patch_fiscal_profile(
    workspace_id: uuid.UUID,
    payload: FiscalProfilePatch,
    session: Annotated[Any, Depends(workspace_session)],
    principal: Annotated[Principal, Depends(require_scope("pfa.write"))],
) -> dict:
    """Versioned fiscal facts and history.

    Effective-dated changes are appended, never overwritten: mutually exclusive
    periods cannot overlap.
    """
    profile_id = await fiscal_profile_service.update_fiscal_profile(
        session,
        workspace_id=workspace_id,
        actor_user_id=principal.user_id,
        changes=fiscal_profile_service.FiscalProfileChanges(**payload.model_dump()),
    )
    return {"status": "updated", "profile_id": str(profile_id)}


# ---------------------------------------------------------------------------
# Members and invitations (ADMIN only)
# ---------------------------------------------------------------------------


@router.get("/workspaces/{workspace_id}/members")
async def list_members(
    workspace_id: uuid.UUID,
    session: Annotated[Any, Depends(workspace_session)],
) -> MemberListResponse:
    rows = await tenancy.list_members_with_accounts(session, workspace_id)
    return MemberListResponse(
        items=[
            {
                "user_id": str(row.user_id),
                "email": row.email,
                "role": str(row.role),
                "status": str(row.status),
                "joined_at": row.joined_at.isoformat(),
            }
            for row in rows
        ]
    )


@router.get("/workspaces/{workspace_id}/invitations")
async def list_invitations(
    workspace_id: uuid.UUID,
    session: Annotated[Any, Depends(admin_workspace_session)],
) -> dict:
    invitations = await tenancy.list_invitations(session, workspace_id)
    return {
        "items": [
            {
                "id": str(invitation.id),
                "email": invitation.email_normalized,
                "role": str(invitation.role),
                "status": str(invitation.status),
                "expires_at": invitation.expires_at.isoformat(),
                "created_at": invitation.created_at.isoformat(),
            }
            for invitation in invitations
        ]
    }


@router.post("/workspaces/{workspace_id}/invitations", status_code=status.HTTP_201_CREATED)
async def create_invitation(
    workspace_id: uuid.UUID,
    payload: InvitationCreate,
    session: Annotated[Any, Depends(admin_workspace_session)],
    principal: Annotated[Principal, Depends(require_scope("pfa.manage"))],
) -> dict:
    invitation, _token = await tenancy.invite_member(
        session,
        workspace_id=workspace_id,
        actor_user_id=principal.user_id,
        email=payload.email,
        role=payload.role,
    )
    return {
        "id": str(invitation.id),
        "email": invitation.email_normalized,
        "role": str(invitation.role),
        "expires_at": invitation.expires_at.isoformat(),
        "email_sent": True,
    }


@router.post("/workspaces/{workspace_id}/invitations/{invitation_id}/revoke")
async def revoke_invitation(
    workspace_id: uuid.UUID,
    invitation_id: uuid.UUID,
    session: Annotated[Any, Depends(admin_workspace_session)],
    principal: Annotated[Principal, Depends(require_scope("pfa.manage"))],
) -> dict:
    invitation = await tenancy.revoke_invitation(
        session,
        workspace_id=workspace_id,
        actor_user_id=principal.user_id,
        invitation_id=invitation_id,
    )
    return {"status": str(invitation.status)}


@router.post("/workspaces/{workspace_id}/invitations/{invitation_id}/resend")
async def resend_invitation(
    workspace_id: uuid.UUID,
    invitation_id: uuid.UUID,
    session: Annotated[Any, Depends(admin_workspace_session)],
    principal: Annotated[Principal, Depends(require_scope("pfa.manage"))],
) -> dict:
    invitation, _token = await tenancy.resend_invitation(
        session,
        workspace_id=workspace_id,
        actor_user_id=principal.user_id,
        invitation_id=invitation_id,
    )
    return {"status": "sent", "expires_at": invitation.expires_at.isoformat()}


class InvitationAccept(BaseModel):
    token: str = Field(min_length=16, max_length=200)


@router.post("/invitations/accept")
async def accept_invitation(
    payload: InvitationAccept,
    principal: Annotated[Principal, Depends(require_scope("pfa.read"))],
    session: UserSession,
) -> dict:
    membership = await tenancy.accept_invitation(
        session,
        token=payload.token,
        user_id=principal.user_id,
        user_email=principal.email,
    )
    return {
        "workspace_id": str(membership.workspace_id),
        "role": str(membership.role),
        "status": str(membership.status),
    }


@router.patch("/workspaces/{workspace_id}/members/{user_id}")
async def change_member_role(
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    payload: RoleChange,
    session: Annotated[Any, Depends(admin_workspace_session)],
    principal: Annotated[Principal, Depends(require_scope("pfa.manage"))],
) -> dict:
    membership = await tenancy.change_role(
        session,
        workspace_id=workspace_id,
        actor_user_id=principal.user_id,
        target_user_id=user_id,
        new_role=payload.role,
    )
    return {"user_id": str(user_id), "role": str(membership.role)}


@router.delete("/workspaces/{workspace_id}/members/{user_id}", status_code=status.HTTP_200_OK)
async def remove_member(
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    session: Annotated[Any, Depends(admin_workspace_session)],
    principal: Annotated[Principal, Depends(require_scope("pfa.manage"))],
) -> dict:
    membership = await tenancy.remove_member(
        session,
        workspace_id=workspace_id,
        actor_user_id=principal.user_id,
        target_user_id=user_id,
    )
    return {"user_id": str(user_id), "status": str(membership.status)}


@router.post("/workspaces/{workspace_id}/leave")
async def leave_workspace(
    workspace_id: uuid.UUID,
    session: Annotated[Any, Depends(workspace_session)],
    principal: Annotated[Principal, Depends(require_scope("pfa.read"))],
) -> dict:
    membership = await tenancy.leave_workspace(
        session, workspace_id=workspace_id, user_id=principal.user_id
    )
    return {"status": str(membership.status)}


# ---------------------------------------------------------------------------
# Obligations and notifications
# ---------------------------------------------------------------------------


@router.get("/workspaces/{workspace_id}/obligations")
async def list_obligations(
    workspace_id: uuid.UUID,
    session: Annotated[Any, Depends(workspace_session)],
    income_year: int | None = Query(default=None),
    actionable_only: bool = Query(default=False),
) -> ObligationListResponse:
    rows = await obligations_service.list_obligations(
        session, workspace_id=workspace_id, income_year=income_year, only_actionable=actionable_only
    )
    await obligations_service.load_workspace_view(
        session, workspace_id=workspace_id, income_year=income_year or date.today().year
    )
    today = date.today()
    return ObligationListResponse(
        items=[
            {
                "id": str(row.id),
                "form_code": row.form_code,
                "form_name_ro": row.form_name_ro,
                "obligation_kind": row.obligation_kind,
                "period_start": row.period_start.isoformat(),
                "period_end": row.period_end.isoformat(),
                "applicability": str(row.applicability),
                "applicability_reason": row.applicability_reason,
                "rule_pack_code": row.rule_pack_code,
                "rule_pack_version": row.rule_pack_version,
                "legal_rule_version": row.legal_rule_version,
                "source_url": row.source_url,
                "generation_available_from": (
                    row.generation_available_from.isoformat()
                    if row.generation_available_from
                    else None
                ),
                "generation_blocked_reason": row.generation_blocked_reason,
                "filing_due_date": row.filing_due_date.isoformat() if row.filing_due_date else None,
                "payment_due_date": (
                    row.payment_due_date.isoformat() if row.payment_due_date else None
                ),
                "deadline_provenance": row.deadline_provenance,
                "deadline_revision": row.deadline_revision,
                "days_remaining": (
                    (row.filing_due_date - today).days if row.filing_due_date else None
                ),
                "missing_inputs": row.missing_inputs,
                "triggering_evidence": row.triggering_evidence,
                "capabilities": row.capabilities,
                "unsupported_reason": row.unsupported_reason,
                "declaration_id": str(row.declaration_id) if row.declaration_id else None,
                "resolution_status": str(row.resolution_status),
            }
            for row in rows
        ],
        unassessed_forms=obligations_service.unassessed_forms(),
        supported_rule_pack_years=obligations_service.supported_years_list(),
    )


@router.post("/workspaces/{workspace_id}/obligations/reconcile", status_code=status.HTTP_202_ACCEPTED)
async def reconcile_obligations(
    workspace_id: uuid.UUID,
    session: Annotated[Any, Depends(workspace_session)],
    income_year: int | None = Query(default=None),
) -> dict:
    year = income_year or date.today().year
    rows = await obligations_service.evaluate_obligations(
        session, workspace_id=workspace_id, income_year=year
    )
    created = await notifications_service.evaluate_notifications(
        session, workspace_id=workspace_id, obligations=rows
    )
    return {"obligations": len(rows), "notifications": len(created)}


@router.get("/workspaces/{workspace_id}/notifications")
async def list_notifications(
    workspace_id: uuid.UUID,
    session: Annotated[Any, Depends(workspace_session)],
    principal: Annotated[Principal, Depends(require_scope("pfa.read"))],
) -> NotificationListResponse:
    items = await notifications_service.notification_feed(
        session, workspace_id=workspace_id, user_id=principal.user_id
    )
    return NotificationListResponse(items=items)


@router.post("/workspaces/{workspace_id}/notifications/{notification_id}/read")
async def mark_notification_read(
    workspace_id: uuid.UUID,
    notification_id: uuid.UUID,
    session: Annotated[Any, Depends(workspace_session)],
    principal: Annotated[Principal, Depends(require_scope("pfa.read"))],
) -> dict:
    await notifications_service.mark_read(
        session,
        workspace_id=workspace_id,
        user_id=principal.user_id,
        notification_id=notification_id,
    )
    return {"status": "read"}


@router.post("/workspaces/{workspace_id}/notifications/{notification_id}/dismiss")
async def dismiss_notification(
    workspace_id: uuid.UUID,
    notification_id: uuid.UUID,
    session: Annotated[Any, Depends(workspace_session)],
    principal: Annotated[Principal, Depends(require_scope("pfa.read"))],
) -> dict:
    await notifications_service.dismiss(
        session,
        workspace_id=workspace_id,
        user_id=principal.user_id,
        notification_id=notification_id,
    )
    return {
        "status": "dismissed",
        "note": "Obligația rămâne nerezolvată și vizibilă pentru toți membrii.",
    }


@router.post("/workspaces/{workspace_id}/notifications/{notification_id}/snooze")
async def snooze_notification(
    workspace_id: uuid.UUID,
    notification_id: uuid.UUID,
    payload: NotificationStateAction,
    session: Annotated[Any, Depends(workspace_session)],
    principal: Annotated[Principal, Depends(require_scope("pfa.read"))],
) -> dict:
    if payload.until is None:
        raise ValidationFailed("Data de amânare este obligatorie.")
    await notifications_service.snooze(
        session,
        workspace_id=workspace_id,
        user_id=principal.user_id,
        notification_id=notification_id,
        until=payload.until,
    )
    return {
        "status": "snoozed",
        "until": payload.until.isoformat(),
        "note": "Amânarea nu ascunde starea de restanță.",
    }


@router.get("/workspaces/{workspace_id}/dashboard")
async def dashboard(
    workspace_id: uuid.UUID,
    session: Annotated[Any, Depends(workspace_session)],
    income_year: int | None = Query(default=None),
) -> DashboardResponse:
    """Separates issued revenue, collected revenue, expenses, estimates and
    declared liabilities.  An estimate is never labelled as an ANAF balance."""
    summary = await dashboard_service.dashboard_summary(
        session, workspace_id=workspace_id, income_year=income_year or date.today().year
    )
    return {
        "income_year": summary.income_year,
        "issued_revenue": str(summary.issued_revenue),
        "collected_revenue": str(summary.collected_revenue),
        "documents_count": summary.documents_count,
        "open_obligations": [
            {
                "id": str(row.id),
                "form_code": row.form_code,
                "applicability": str(row.applicability),
                "filing_due_date": row.filing_due_date.isoformat() if row.filing_due_date else None,
                "deadline_provenance": row.deadline_provenance,
                "unsupported_reason": row.unsupported_reason,
            }
            for row in summary.open_obligations
        ],
        "estimated_taxes": {
            "label_ro": "Estimare internă — nu este un sold ANAF",
            "note": (
                "Valorile estimate se obțin prin calcularea declarațiilor; nu sunt "
                "solduri confirmate de ANAF."
            ),
        },
    }
