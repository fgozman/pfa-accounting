"""The authorization server and the application running as two separate services.

Each side is its own ASGI app.  They talk over HTTP only (``httpx.ASGITransport``
stands in for the network): the application introspects tokens and revokes
sessions on the server, and the server verifies credentials on the application.
Nothing is wired in-process here, so these tests prove the split works end to end.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from pfa.clients import authorization_server as authorization_server_client
from pfa.clients.authorization_server_http import HttpAuthorizationServer
from pfa.config import reload_settings
from pfa.db import Database
from pfa.http.service_client import ServiceEndpoint
from pfa.security.tokens import new_pkce_verifier, new_state, pkce_challenge_s256
from tests.conftest import (
    TEST_USER_PASSWORD,
    create_verified_user,
    fetch_outbox_payload,
    oauth_login,
)

SERVICE_TOKEN = "split-test-service-token-0123456789abcdef"
APPLICATION_URL = "http://application.internal"
AUTHORIZATION_SERVER_URL = "http://authorization.internal"
SPLIT_ENV = {
    "PFA_AUTHORIZATION_SERVER_URL": AUTHORIZATION_SERVER_URL,
    "PFA_IDENTITY_PROVIDER_URL": APPLICATION_URL,
    "PFA_INTERNAL_SERVICE_TOKEN": SERVICE_TOKEN,
}


@dataclass(slots=True)
class Split:
    #: the browser/SPA talking to the application
    app: AsyncClient
    #: the browser/SPA talking to the authorization server
    auth: AsyncClient
    application: FastAPI
    authorization_server: FastAPI


@pytest.fixture
async def split(database: Database, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Split]:
    from pfa.api.deps import reset_object_store
    from pfa.main import create_app
    from pfa.oauth.app import create_authorization_server_app
    from pfa.oauth.flows import register_client
    from pfa.oauth.identity_http import HttpIdentityProvider
    from pfa.oauth.keys import reset_keys
    from pfa.wiring import wire_in_process

    for name, value in SPLIT_ENV.items():
        monkeypatch.setenv(name, value)
    settings = reload_settings()
    reset_keys()
    reset_object_store()

    application = create_app(settings)
    authorization_server = create_authorization_server_app(
        settings,
        identity_provider=HttpIdentityProvider(
            ServiceEndpoint(APPLICATION_URL, SERVICE_TOKEN, transport=ASGITransport(application))
        ),
    )
    # the application's client, routed to the server app instead of the network
    authorization_server_client.configure(
        HttpAuthorizationServer(
            ServiceEndpoint(
                AUTHORIZATION_SERVER_URL,
                SERVICE_TOKEN,
                transport=ASGITransport(authorization_server),
            )
        )
    )
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
    try:
        async with (
            AsyncClient(
                transport=ASGITransport(app=application), base_url=settings.web_service_origin
            ) as app_client,
            AsyncClient(
                transport=ASGITransport(app=authorization_server),
                base_url=settings.authorization_origin,
            ) as auth_client,
        ):
            yield Split(app_client, auth_client, application, authorization_server)
    finally:
        for name in SPLIT_ENV:
            monkeypatch.delenv(name, raising=False)
        reload_settings()
        wire_in_process()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Login, API access and logout across the two services
# ---------------------------------------------------------------------------


async def test_login_on_the_server_grants_access_to_the_application(
    split: Split, database: Database
) -> None:
    email = "split-login@example.test"
    user_id = await create_verified_user(database, email=email)

    # the whole code + PKCE flow happens on the authorization server; the password
    # is checked by the application over HTTP
    session = await oauth_login(split.auth, email=email)

    me = await split.app.get("/api/v1/me", headers=_bearer(session.access_token))
    assert me.status_code == 200, me.text
    assert me.json()["user_id"] == user_id
    assert me.json()["email"] == email

    # each service serves only its own surface
    assert (await split.app.get("/oauth/authorize")).status_code == 404
    assert (await split.app.get("/.well-known/jwks.json")).status_code == 404
    assert (
        await split.auth.get("/api/v1/me", headers=_bearer(session.access_token))
    ).status_code == 404

    # logout on the server is seen by the application on the very next request
    logout = await split.auth.post("/api/v1/sessions/logout", headers=_bearer(session.access_token))
    assert logout.status_code == 200
    after = await split.app.get("/api/v1/me", headers=_bearer(session.access_token))
    assert after.status_code == 401


async def test_refresh_rotation_stays_on_the_server(split: Split, database: Database) -> None:
    email = "split-refresh@example.test"
    await create_verified_user(database, email=email)
    session = await oauth_login(split.auth, email=email)
    settings = reload_settings()
    rotated = await split.auth.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": settings.spa_client_id,
            "refresh_token": session.refresh_token,
        },
    )
    assert rotated.status_code == 200, rotated.text
    fresh = rotated.json()["access_token"]
    assert (await split.app.get("/api/v1/me", headers=_bearer(fresh))).status_code == 200


async def test_password_reset_on_the_application_revokes_sessions_on_the_server(
    split: Split, database: Database, owner_database: Database
) -> None:
    email = "split-reset@example.test"
    await create_verified_user(database, email=email)
    session = await oauth_login(split.auth, email=email)
    assert (
        await split.app.get("/api/v1/me", headers=_bearer(session.access_token))
    ).status_code == 200

    await split.app.post("/auth/password-reset/request", json={"email": email})
    token = (
        await fetch_outbox_payload(owner_database, event_type="email.password_reset_requested")
    )["token"]
    confirmed = await split.app.post(
        "/auth/password-reset/confirm",
        json={"token": token, "new_password": "Split-New-Password-42"},
    )
    assert confirmed.status_code == 200, confirmed.text

    assert (
        await split.app.get("/api/v1/me", headers=_bearer(session.access_token))
    ).status_code == 401
    # the new password works through the server's login page
    await oauth_login(split.auth, email=email, password="Split-New-Password-42")


# ---------------------------------------------------------------------------
# Refused logins: reasons cross the boundary, attempts and audit persist
# ---------------------------------------------------------------------------


async def _start_login(split: Split) -> tuple[str, str]:
    settings = reload_settings()
    verifier = new_pkce_verifier()
    page = await split.auth.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": settings.spa_client_id,
            "redirect_uri": settings.spa_callback_uri,
            "scope": "pfa.read",
            "state": new_state(),
            "code_challenge": pkce_challenge_s256(verifier),
            "code_challenge_method": "S256",
        },
        headers={"Origin": settings.spa_origin},
    )
    assert page.status_code == 200, page.text
    proof = re.search(r'name="transaction_proof" value="([^"]+)"', page.text).group(1)
    tx = re.search(r"/auth/authorization-transactions/([^/]+)/authenticate", page.text).group(1)
    return tx, proof


async def _submit(split: Split, tx: str, proof: str, *, email: str, password: str):
    return await split.auth.post(
        f"/auth/authorization-transactions/{tx}/authenticate",
        data={"email": email, "password": password, "transaction_proof": proof},
        headers={"Origin": reload_settings().spa_origin},
        follow_redirects=False,
    )


async def test_refused_logins_cross_the_boundary_and_are_recorded(
    split: Split,
    database: Database,
    owner_database: Database,
    oauth_owner_database: Database,
) -> None:
    email = "split-refused@example.test"
    user_id = await create_verified_user(database, email=email)
    tx, proof = await _start_login(split)

    wrong = await _submit(split, tx, proof, email=email, password="wrong-password-123")
    assert wrong.status_code == 303
    assert "error=invalid_credentials" in wrong.headers["location"]

    # the attempt is counted in the OAuth database, the audit is the application's
    async with oauth_owner_database.session() as session:
        attempts = (
            await session.execute(
                text("SELECT attempt_count FROM oauth_authorization_transactions WHERE id = :id"),
                {"id": tx},
            )
        ).scalar_one()
    async with owner_database.session() as session:
        audited = (
            await session.execute(
                text(
                    "SELECT count(*) FROM audit_events "
                    "WHERE action = 'account.login_failed' AND actor_user_id = :user_id"
                ),
                {"user_id": user_id},
            )
        ).scalar_one()
    assert attempts == 1  # counted by the server
    assert audited == 1  # audited by the application


async def test_an_unverified_account_is_reported_as_such_across_the_boundary(
    split: Split, database: Database
) -> None:
    from pfa.services import accounts

    email = "split-unverified@example.test"
    async with database.session() as session:
        await accounts.register_user(session, email=email, password=TEST_USER_PASSWORD)
    tx, proof = await _start_login(split)
    refused = await _submit(split, tx, proof, email=email, password=TEST_USER_PASSWORD)
    assert refused.status_code == 303
    assert "error=email_not_verified" in refused.headers["location"]


# ---------------------------------------------------------------------------
# Internal endpoints
# ---------------------------------------------------------------------------


async def test_internal_endpoints_require_the_service_token(split: Split) -> None:
    for headers in ({}, _bearer("not-the-service-token-at-all-0123456789")):
        introspect = await split.auth.post(
            "/oauth/introspect", data={"token": "x"}, headers=headers
        )
        assert introspect.status_code == 401
        identity = await split.app.post(
            "/internal/identity/authenticate",
            json={"email": "a@example.test", "password": "x", "attempt_key": "k"},
            headers=headers,
        )
        assert identity.status_code == 401


async def test_introspection_reports_unknown_tokens_as_inactive_only(split: Split) -> None:
    response = await split.auth.post(
        "/oauth/introspect", data={"token": "not-a-jwt"}, headers=_bearer(SERVICE_TOKEN)
    )
    assert response.status_code == 200
    # RFC 7662: an inactive answer carries nothing else
    assert response.json() == {"active": False}
    assert response.headers["cache-control"].startswith("no-store")


async def test_introspection_describes_an_active_token(split: Split, database: Database) -> None:
    email = "split-introspect@example.test"
    user_id = await create_verified_user(database, email=email)
    session = await oauth_login(split.auth, email=email, scope="pfa.read pfa.write")
    body = (
        await split.auth.post(
            "/oauth/introspect",
            data={"token": session.access_token},
            headers=_bearer(SERVICE_TOKEN),
        )
    ).json()
    assert body["active"] is True
    assert body["sub"] == user_id
    assert body["scope"] == "pfa.read pfa.write"
    assert body["token_type"] == "Bearer"
    assert isinstance(body["exp"], int)


async def test_the_internal_endpoints_are_not_published(split: Split) -> None:
    paths = (await split.app.get("/openapi.json")).json()["paths"]
    assert not any(path.startswith("/internal") for path in paths)
    # the standalone server publishes no OpenAPI document at all
    assert (await split.auth.get("/openapi.json")).status_code == 404


async def test_the_monolith_exposes_no_internal_endpoint(client: AsyncClient) -> None:
    assert (await client.post("/oauth/introspect", data={"token": "x"})).status_code in {404, 405}
    assert (
        await client.post("/internal/identity/authenticate", json={"email": "a", "password": "b"})
    ).status_code in {404, 405}


# ---------------------------------------------------------------------------
# Failure modes: an unreachable server never means "allowed"
# ---------------------------------------------------------------------------


def _unreachable() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("authorization server down", request=request)

    return httpx.MockTransport(handler)


async def test_an_unreachable_server_denies_access_and_blocks_credential_changes(
    split: Split, database: Database, owner_database: Database
) -> None:
    email = "split-down@example.test"
    await create_verified_user(database, email=email)
    session = await oauth_login(split.auth, email=email)
    await split.app.post("/auth/password-reset/request", json={"email": email})
    token = (
        await fetch_outbox_payload(owner_database, event_type="email.password_reset_requested")
    )["token"]

    authorization_server_client.configure(
        HttpAuthorizationServer(
            ServiceEndpoint(AUTHORIZATION_SERVER_URL, SERVICE_TOKEN, transport=_unreachable())
        )
    )
    denied = await split.app.get("/api/v1/me", headers=_bearer(session.access_token))
    assert denied.status_code == 503
    assert denied.json()["code"] == "upstream_unavailable"

    # sessions could not be revoked, so the password change must not happen either
    blocked = await split.app.post(
        "/auth/password-reset/confirm",
        json={"token": token, "new_password": "Never-Applied-Password-1"},
    )
    assert blocked.status_code == 503
    async with owner_database.session() as db:
        used = (
            await db.execute(
                text(
                    "SELECT t.used_at FROM password_reset_tokens t "
                    "JOIN users u ON u.id = t.user_id WHERE u.email_normalized = :email"
                ),
                {"email": email},
            )
        ).scalar_one()
    assert used is None  # rolled back: the link still works once the server is back


def test_the_standalone_server_refuses_to_start_without_a_service_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pfa.oauth.app import create_authorization_server_app

    monkeypatch.setenv("PFA_INTERNAL_SERVICE_TOKEN", "short")
    monkeypatch.setenv("PFA_IDENTITY_PROVIDER_URL", APPLICATION_URL)
    try:
        with pytest.raises(RuntimeError, match="PFA_INTERNAL_SERVICE_TOKEN"):
            create_authorization_server_app(reload_settings())
    finally:
        monkeypatch.delenv("PFA_INTERNAL_SERVICE_TOKEN")
        monkeypatch.delenv("PFA_IDENTITY_PROVIDER_URL")
        reload_settings()


async def test_one_request_keeps_its_correlation_id_across_both_services(
    split: Split, database: Database, caplog: pytest.LogCaptureFixture
) -> None:
    """The application's introspection call carries the request's correlation id
    (and trace context): the server's log lines for it name the same request."""
    import logging

    email = "split-correlation@example.test"
    await create_verified_user(database, email=email)
    session = await oauth_login(split.auth, email=email)
    caplog.set_level(logging.INFO)
    caplog.clear()

    me = await split.app.get(
        "/api/v1/me",
        headers={**_bearer(session.access_token), "X-Correlation-Id": "split-trace-0001"},
    )
    assert me.status_code == 200

    requests = [r for r in caplog.records if getattr(r, "event", None) == "http.request"]
    by_route = {r.fields["http.route"]: r for r in requests}
    app_line, server_line = by_route["/api/v1/me"], by_route["/oauth/introspect"]
    assert app_line.context["pfa.correlation_id"] == "split-trace-0001"
    assert server_line.context["pfa.correlation_id"] == "split-trace-0001"
    # one distributed trace: the server's span continues the application's
    assert server_line.context["trace_id"] == app_line.context["trace_id"]
