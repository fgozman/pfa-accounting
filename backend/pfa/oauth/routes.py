"""OAuth 2.0 authorization server endpoints.

``/oauth/authorize`` (login page), the login POST inside an authorization
transaction, ``/oauth/token``, ``/oauth/revoke``, the JWKS and session logout.
Each handler adapts HTTP to :mod:`pfa.oauth.flows` / :mod:`pfa.oauth.sessions`;
pages are rendered by :mod:`pfa.oauth.pages`.  Nothing here depends on the
application (accounts, workspaces, ...) or reads a repository directly, and bearer
tokens are checked by the server itself (:mod:`pfa.oauth.introspection`).

Cookie-free: the login page is rendered by this service, carries a one-use
transaction proof, and posts credentials back to the authorization origin.  No
``Set-Cookie`` is ever emitted, and the SPA never sees a password.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field

from pfa.config import get_settings
from pfa.errors import NotFound, Unauthenticated, ValidationFailed
from pfa.http.service_auth import require_service_token
from pfa.oauth import flows as oauth
from pfa.oauth import sessions
from pfa.oauth.db import get_oauth_database
from pfa.oauth.flows import OAuthError
from pfa.oauth.introspection import ActiveToken, introspect_access_token
from pfa.oauth.keys import get_keys
from pfa.oauth.pages import error_page, login_page
from pfa.oauth.ports import LoginRejected
from pfa.observability import context
from pfa.security.bearer import bearer_scheme

router = APIRouter(tags=["oauth"])

#: service-to-service endpoints, mounted only by the standalone server
#: (:mod:`pfa.oauth.app`); never part of the public contract
internal_router = APIRouter(
    tags=["oauth-internal"],
    include_in_schema=False,
    dependencies=[Depends(require_service_token)],
)

NO_STORE = {
    "Cache-Control": "no-store, no-cache, must-revalidate, private",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
}


@router.get("/oauth/authorize", response_class=HTMLResponse)
async def authorize(
    request: Request,
    response_type: str | None = None,
    client_id: str | None = None,
    redirect_uri: str | None = None,
    scope: str = "",
    state: str = "",
    code_challenge: str = "",
    code_challenge_method: str = "S256",
    prompt: str | None = None,
    transaction: uuid.UUID | None = None,
    error: str | None = None,
) -> Response:
    """Validate the request and render the login page, or fail locally.

    An invalid client/redirect shows a local error page and never redirects to the
    supplied URI.

    ``transaction`` re-renders an existing, still-valid authorization transaction:
    that is how a failed login returns here (with a fresh one-use proof) instead of
    leaving a stale POST URL in the browser, so a reload is safe.
    """
    settings = get_settings()
    if transaction is not None:
        return await _render_existing_transaction(transaction, error)

    if not response_type or not client_id or not redirect_uri:
        return HTMLResponse(
            error_page(
                "Cererea de autorizare este incompletă (response_type, client_id, redirect_uri)."
            ),
            status_code=400,
            headers=NO_STORE,
        )

    origin = request.headers.get("origin")
    try:
        async with get_oauth_database().session() as session:
            handle = await oauth.create_authorization_transaction(
                session,
                oauth.AuthorizationRequest(
                    client_id=client_id,
                    redirect_uri=redirect_uri,
                    response_type=response_type,
                    scope=scope,
                    state=state,
                    code_challenge=code_challenge,
                    code_challenge_method=code_challenge_method,
                ),
                origin=origin,
                settings=settings,
            )
            transaction_id = handle.transaction.id
            proof = handle.proof
    except (OAuthError, ValidationFailed, NotFound) as exc:
        message = getattr(exc, "description", None) or getattr(exc, "message", str(exc))
        return HTMLResponse(
            error_page(message, settings=settings),
            status_code=400,
            headers=NO_STORE,
        )

    return HTMLResponse(login_page(transaction_id, proof), headers=NO_STORE)


async def _render_existing_transaction(
    transaction_id: uuid.UUID, error_code: str | None
) -> Response:
    """Re-render the login page for a still-valid transaction with a fresh proof."""
    try:
        async with get_oauth_database().session() as session:
            proof = await oauth.reissue_login_proof(session, transaction_id)
    except NotFound as exc:
        return HTMLResponse(error_page(exc.message), status_code=400, headers=NO_STORE)
    return HTMLResponse(login_page(transaction_id, proof, error_code), headers=NO_STORE)


@router.post("/auth/authorization-transactions/{transaction_id}/authenticate")
async def authenticate_transaction(
    transaction_id: uuid.UUID,
    request: Request,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    transaction_proof: Annotated[str, Form()],
) -> Response:
    """Authenticate within a validated transaction and return the SPA callback.

    Client, redirect URI and PKCE values are read from the stored transaction:
    nothing in this POST can replace them.
    """
    settings = get_settings()
    origin = request.headers.get("origin")
    try:
        async with get_oauth_database().session() as session:
            transaction = await oauth.authenticate_transaction(
                session,
                transaction_id=transaction_id,
                proof=transaction_proof,
                email=email,
                password=password,
                origin=origin,
                settings=settings,
                client_ip=request.client.host if request.client else None,
            )
            code = await oauth.issue_authorization_code(session, transaction=transaction)
            redirect_uri = transaction.redirect_uri
            state_value = transaction.state
    except LoginRejected as exc:
        # Credential failures return to the canonical authorization URL (a GET), so
        # the browser history holds no stale POST and a reload is safe.  The error
        # is a fixed code, never reflected request text.
        location = f"/oauth/authorize?transaction={transaction_id}&error={exc.reason}"
        return RedirectResponse(location, status_code=status.HTTP_303_SEE_OTHER, headers=NO_STORE)
    except Exception as exc:
        # The transaction itself cannot be trusted (bad proof, wrong origin) or is no
        # longer usable: render locally instead of redirecting anywhere.
        message = getattr(exc, "message", None) or str(exc)
        return HTMLResponse(
            error_page(message),
            status_code=int(getattr(exc, "http_status", 401)),
            headers=NO_STORE,
        )

    separator = "&" if "?" in redirect_uri else "?"
    location = f"{redirect_uri}{separator}code={code}&state={state_value}"
    # HTTP 303 to the exact registered callback with the unchanged state.
    return RedirectResponse(location, status_code=status.HTTP_303_SEE_OTHER, headers=NO_STORE)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    refresh_token: str
    scope: str


@router.post("/oauth/token")
async def token(
    grant_type: Annotated[str, Form()],
    client_id: Annotated[str, Form()],
    code: Annotated[str | None, Form()] = None,
    redirect_uri: Annotated[str | None, Form()] = None,
    code_verifier: Annotated[str | None, Form()] = None,
    refresh_token: Annotated[str | None, Form()] = None,
    scope: Annotated[str | None, Form()] = None,
) -> JSONResponse:
    settings = get_settings()
    keys = get_keys()
    if grant_type == "authorization_code":
        missing = [
            name
            for name, value in (
                ("code", code),
                ("redirect_uri", redirect_uri),
                ("code_verifier", code_verifier),
            )
            if not value
        ]
        if missing:
            raise ValidationFailed(
                "Cererea de token este incompletă.",
                field_errors={name: "required" for name in missing},
            )
        async with get_oauth_database().session() as session:
            pair = await oauth.exchange_code(
                session,
                store=keys,
                client_id=client_id,
                code=code or "",
                redirect_uri=redirect_uri or "",
                code_verifier=code_verifier or "",
                settings=settings,
            )
    elif grant_type == "refresh_token":
        if not refresh_token:
            raise ValidationFailed(
                "refresh_token este obligatoriu.",
                field_errors={"refresh_token": "required"},
            )
        async with get_oauth_database().session() as session:
            pair = await oauth.rotate_refresh_token(
                session,
                store=keys,
                client_id=client_id,
                refresh_token=refresh_token,
                settings=settings,
            )
    else:
        raise OAuthError("unsupported_grant_type", "Grantul cerut nu este acceptat.")

    return JSONResponse(pair.as_response(), headers=NO_STORE)


@router.post("/oauth/revoke")
async def revoke(
    client_id: Annotated[str, Form()],
    token: Annotated[str, Form()],
    token_type_hint: Annotated[str | None, Form()] = None,
) -> Response:
    async with get_oauth_database().session() as session:
        await oauth.revoke_token(
            session,
            store=get_keys(),
            client_id=client_id,
            token=token,
            token_type_hint=token_type_hint,
        )
    return Response(status_code=200, headers=NO_STORE)


@router.get("/.well-known/jwks.json")
async def jwks() -> JSONResponse:
    return JSONResponse(get_keys().jwks(), headers={"Cache-Control": "public, max-age=300"})


async def active_token(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
) -> ActiveToken:
    """The caller's own access token, validated by this server (signature + session)."""
    if credentials is None or not credentials.credentials:
        raise Unauthenticated("Lipsește antetul Authorization: Bearer <access_token>.")
    if credentials.scheme.lower() != "bearer":
        raise Unauthenticated("Antetul Authorization trebuie să fie 'Bearer <token>'.")
    token = await introspect_access_token(credentials.credentials)
    context.bind(user_id=token.subject_id)
    request.state.user_id = str(token.subject_id)
    return token


@router.post("/api/v1/sessions/logout")
async def logout(token: Annotated[ActiveToken, Depends(active_token)]) -> dict:
    # ends the caller's own login session: its whole refresh-token family
    async with get_oauth_database().session() as session:
        await sessions.logout(session, session_id=token.session_id, user_id=token.subject_id)
    return {"status": "logged_out", "message": "Sesiunea a fost revocată."}


# ---------------------------------------------------------------------------
# Internal (service-to-service) endpoints
# ---------------------------------------------------------------------------


@internal_router.post("/oauth/introspect")
async def introspect(
    token: Annotated[str, Form()],
    token_type_hint: Annotated[str | None, Form()] = None,
) -> JSONResponse:
    """RFC 7662 token introspection for the application (the resource server).

    Only access tokens are introspected; anything else is reported inactive.  An
    inactive answer carries no other member, as the RFC recommends.
    """
    _ = token_type_hint
    try:
        active = await introspect_access_token(token)
    except Unauthenticated:
        return JSONResponse({"active": False}, headers=NO_STORE)
    return JSONResponse(
        {
            "active": True,
            "token_type": "Bearer",
            "sub": str(active.subject_id),
            "sid": str(active.session_id),
            "client_id": active.client_id,
            "scope": " ".join(sorted(active.scope)),
            "jti": active.jti,
            "exp": active.expires_at,
        },
        headers=NO_STORE,
    )


class SessionRevocation(BaseModel):
    reason: str = Field(min_length=1, max_length=64)


@internal_router.post("/oauth/subjects/{subject_id}/sessions/revoke")
async def revoke_subject_sessions(subject_id: uuid.UUID, payload: SessionRevocation) -> JSONResponse:
    """Revoke every login session of a subject (the application changed its credentials)."""
    async with get_oauth_database().session() as session:
        revoked = await sessions.revoke_all_for_user(session, subject_id, reason=payload.reason)
    return JSONResponse({"revoked_sessions": revoked}, headers=NO_STORE)
