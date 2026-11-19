"""The caller's own view: identity from the access token plus workspace memberships."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from pfa.api.deps import Principal, UserSession, require_scope
from pfa.api.schemas.responses import MeResponse, WorkspaceSummary
from pfa.security.scopes import SCOPE_READ
from pfa.services import tenancy

#: tag kept from the original grouping so the published OpenAPI contract is stable
router = APIRouter(tags=["oauth"])


@router.get("/api/v1/me")
async def me(
    principal: Annotated[Principal, Depends(require_scope(SCOPE_READ))],
    session: UserSession,
) -> MeResponse:
    workspaces = await tenancy.list_workspaces(session, principal.user_id)
    return MeResponse(
        user_id=str(principal.user_id),
        email=principal.email,
        scope=sorted(principal.scope),
        workspaces=[
            WorkspaceSummary(
                id=str(workspace.id),
                name=workspace.name,
                cui=workspace.cui,
                accounting_currency=workspace.accounting_currency,
            )
            for workspace in workspaces
        ],
    )
