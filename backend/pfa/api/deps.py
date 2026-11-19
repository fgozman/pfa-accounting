"""Request dependencies: bearer auth, units of work and tenant scopes.

Route handlers never open database sessions or run queries themselves.  They
declare one of the dependencies below and receive a ready unit of work
(``AsyncSession``) that they pass to services:

* :func:`public_session` — no caller, no workspace (account sign-up, reset, ...);
* :func:`user_session` — the caller's own, workspace-less context;
* :func:`workspace_session` / :func:`admin_workspace_session` — a tenant context,
  installed only after a current-membership check;
* :func:`declaration_scope`, :func:`job_scope`, :func:`personal_declaration_scope`
  — a tenant context for a resource addressed by its own id: the owning workspace
  is resolved first (identifiers only), membership is checked, then the context is
  installed;
* :func:`anaf_connection_scope` — the system context for the ANAF callback.

Every request checks the access token *through the authorization server*
(:mod:`pfa.clients.authorization_server`), the account status, and the current
workspace membership.  A client-supplied workspace identifier is never
authorization: it is only a lookup key that must resolve to an active membership.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from pfa.clients.authorization_server import authorization_server
from pfa.db import Database, get_database
from pfa.errors import NotFound, PermissionDenied, Unauthenticated, ValidationFailed
from pfa.models.base import MembershipRole, MembershipStatus
from pfa.observability import context
from pfa.security.bearer import bearer_scheme
from pfa.security.scopes import SCOPE_MANAGE, SCOPE_READ, SCOPE_WRITE
from pfa.services import accounts, anaf_connections, declaration_service, jobs, tenancy
from pfa.services.storage import ObjectStore


@lru_cache(maxsize=1)
def get_object_store() -> ObjectStore:
    return ObjectStore.from_settings()


def reset_object_store() -> None:
    get_object_store.cache_clear()


def get_shared_database() -> Database:
    """Always the process-wide database (mutable so tests can point elsewhere)."""
    return get_database()


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: uuid.UUID
    session_id: uuid.UUID
    client_id: str
    scope: set[str]
    jti: str
    email: str
    display_name: str | None


async def bearer_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
) -> Principal:
    """Introspect the access token, then check the account behind it."""
    if credentials is None or not credentials.credentials:
        raise Unauthenticated("Lipsește antetul Authorization: Bearer <access_token>.")
    if credentials.scheme.lower() != "bearer":
        raise Unauthenticated("Antetul Authorization trebuie să fie 'Bearer <token>'.")
    # signature, audience, expiry and the server-side session (logout / password
    # reset must invalidate access before JWT expiry) are the server's to judge
    grant = await authorization_server().introspect(credentials.credentials)

    async with get_shared_database().session() as session:
        user = await accounts.find_active_user(session, grant.user_id)
    if user is None:
        raise Unauthenticated("Contul nu este activ.")
    # every log record and audit row of this request now names the user
    context.bind(user_id=grant.user_id)
    request.state.user_id = str(grant.user_id)
    request.state.principal = Principal(
        user_id=grant.user_id,
        session_id=grant.session_id,
        client_id=grant.client_id,
        scope=set(grant.scope),
        jti=grant.jti,
        email=user.email_display,
        display_name=None,
    )
    return request.state.principal


def require_scope(*required: str):
    async def _dependency(principal: Annotated[Principal, Depends(bearer_principal)]) -> Principal:
        missing = [scope for scope in required if scope not in principal.scope]
        if missing:
            raise PermissionDenied(
                "Tokenul nu are scope-ul necesar pentru această operațiune.",
                details={"missing_scopes": missing},
            )
        return principal

    return _dependency


# ---------------------------------------------------------------------------
# Units of work
# ---------------------------------------------------------------------------


async def public_session() -> AsyncIterator[AsyncSession]:
    """Tenant-less unit of work for unauthenticated account endpoints (sign-up, ...)."""
    async with get_shared_database().session() as session:
        yield session


async def user_session(
    principal: Annotated[Principal, Depends(bearer_principal)],
) -> AsyncIterator[AsyncSession]:
    """The caller's own context, no workspace (listing/creating workspaces, ...)."""
    async with get_shared_database().user_session(principal.user_id) as session:
        yield session


@dataclass(frozen=True, slots=True)
class TenantScope:
    """A tenant-context unit of work for a resource whose workspace was resolved."""

    workspace_id: uuid.UUID
    session: AsyncSession


async def workspace_session(
    workspace_id: uuid.UUID,
    principal: Annotated[Principal, Depends(require_scope(SCOPE_READ, SCOPE_WRITE))],
) -> AsyncIterator[AsyncSession]:
    """Open a session with tenant context only after a membership check."""
    db = get_shared_database()
    async with db.user_session(principal.user_id) as probe:
        if not await tenancy.is_active_member(
            probe, workspace_id=workspace_id, user_id=principal.user_id
        ):
            # identical answer for "not a member" and "does not exist"
            raise PermissionDenied("Nu aveți acces la acest spațiu de lucru.")
    async with db.tenant_session(workspace_id, actor="user", user_id=principal.user_id) as session:
        yield session


async def admin_workspace_session(
    workspace_id: uuid.UUID,
    principal: Annotated[Principal, Depends(require_scope(SCOPE_MANAGE))],
) -> AsyncIterator[AsyncSession]:
    db = get_shared_database()
    async with db.user_session(principal.user_id) as probe:
        membership = await tenancy.get_membership(
            probe, workspace_id=workspace_id, user_id=principal.user_id
        )
        if (
            membership is None
            or membership.status != MembershipStatus.ACTIVE
            or membership.role != MembershipRole.ADMIN
        ):
            raise PermissionDenied("Această operațiune este permisă doar administratorilor.")
    async with db.tenant_session(workspace_id, actor="user", user_id=principal.user_id) as session:
        yield session


async def _member_workspace(
    principal: Principal,
    resolve: Callable[[AsyncSession], Awaitable[uuid.UUID | None]],
    *,
    not_found: str,
    denied: str,
) -> uuid.UUID:
    """Resolve a resource's workspace (identifier only) and require membership in it."""
    async with get_shared_database().user_session(principal.user_id) as probe:
        workspace_id = await resolve(probe)
        if workspace_id is None:
            raise NotFound(not_found)
        if not await tenancy.is_active_member(
            probe, workspace_id=workspace_id, user_id=principal.user_id
        ):
            raise PermissionDenied(denied)
    return workspace_id


async def declaration_scope(
    declaration_id: uuid.UUID,
    principal: Annotated[Principal, Depends(require_scope(SCOPE_READ, SCOPE_WRITE))],
) -> AsyncIterator[TenantScope]:
    workspace_id = await _member_workspace(
        principal,
        lambda probe: declaration_service.declaration_workspace(probe, declaration_id),
        not_found="Declarația nu există.",
        denied="Nu aveți acces la această declarație.",
    )
    async with get_shared_database().tenant_session(
        workspace_id, actor="user", user_id=principal.user_id
    ) as session:
        # the row must also be visible under the tenant's row-level security
        await declaration_service.load_declaration(session, declaration_id)
        yield TenantScope(workspace_id=workspace_id, session=session)


async def job_scope(
    job_id: uuid.UUID,
    principal: Annotated[Principal, Depends(require_scope(SCOPE_READ))],
) -> AsyncIterator[TenantScope]:
    workspace_id = await _member_workspace(
        principal,
        lambda probe: jobs.job_workspace(probe, job_id),
        not_found="Lucrarea nu există.",
        denied="Nu aveți acces la această lucrare.",
    )
    async with get_shared_database().tenant_session(
        workspace_id, actor="user", user_id=principal.user_id
    ) as session:
        yield TenantScope(workspace_id=workspace_id, session=session)


async def personal_declaration_scope(
    taxpayer_id: uuid.UUID,
    principal: Annotated[Principal, Depends(bearer_principal)],
) -> AsyncIterator[TenantScope]:
    """The workspace in which the caller may prepare a taxpayer's personal D212."""
    db = get_shared_database()
    async with db.user_session(principal.user_id) as probe:
        workspace_id = await tenancy.workspace_for_taxpayer(
            probe, taxpayer_id=taxpayer_id, user_id=principal.user_id
        )
    async with db.tenant_session(workspace_id, actor="user", user_id=principal.user_id) as session:
        yield TenantScope(workspace_id=workspace_id, session=session)


async def anaf_connection_scope(connection_id: uuid.UUID) -> AsyncIterator[TenantScope]:
    """System context for the unauthenticated ANAF callback.

    The workspace is resolved from the connection (identifier only); the one-use
    state secret checked by the service is the proof of the callback.
    """
    db = get_shared_database()
    async with db.session() as probe:
        workspace_id = await anaf_connections.connection_workspace(probe, connection_id)
    if workspace_id is None:
        raise NotFound("Conexiunea ANAF nu există.")
    async with db.tenant_session(workspace_id, actor="system") as session:
        yield TenantScope(workspace_id=workspace_id, session=session)


#: annotated aliases, so route modules never need to import SQLAlchemy
PublicSession = Annotated[AsyncSession, Depends(public_session)]
UserSession = Annotated[AsyncSession, Depends(user_session)]
DeclarationScope = Annotated[TenantScope, Depends(declaration_scope)]
JobScope = Annotated[TenantScope, Depends(job_scope)]
PersonalDeclarationScope = Annotated[TenantScope, Depends(personal_declaration_scope)]
AnafConnectionScope = Annotated[TenantScope, Depends(anaf_connection_scope)]


# ---------------------------------------------------------------------------
# Headers
# ---------------------------------------------------------------------------


def idempotency_key(key: Annotated[str | None, Header(alias="Idempotency-Key")] = None) -> str:
    if not key:
        raise ValidationFailed(
            "Antetul Idempotency-Key este obligatoriu pentru această operațiune.",
            field_errors={"Idempotency-Key": "required"},
        )
    return key


__all__ = [
    "SCOPE_MANAGE",
    "SCOPE_READ",
    "SCOPE_WRITE",
    "AnafConnectionScope",
    "DeclarationScope",
    "JobScope",
    "PersonalDeclarationScope",
    "Principal",
    "TenantScope",
    "UserSession",
    "admin_workspace_session",
    "anaf_connection_scope",
    "bearer_principal",
    "declaration_scope",
    "get_object_store",
    "idempotency_key",
    "job_scope",
    "personal_declaration_scope",
    "public_session",
    "require_scope",
    "reset_object_store",
    "user_session",
    "workspace_session",
]
