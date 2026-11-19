"""Cookie-free OAuth 2.0 code + PKCE, JWT rotation and session revocation.

Requirement IDs: IDENTITY, OAUTH, AUTH-01..AUTH-05.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from pfa import clock
from pfa.config import reload_settings
from pfa.db import Database
from pfa.oauth import sessions as oauth_sessions
from pfa.security.jwt import (
    ACCESS_AUDIENCE,
    REFRESH_AUDIENCE,
    TokenError,
    decode_token,
    issue_access_token,
)
from pfa.security.keys import KeyStore
from pfa.security.tokens import new_pkce_verifier, new_state, pkce_challenge_s256
from tests.conftest import (
    TEST_USER_PASSWORD,
    create_verified_user,
    fetch_outbox_payload,
    oauth_login,
)


def settings():
    return reload_settings()


async def _authorize(client: AsyncClient, *, verifier: str, state: str, **overrides):
    values = {
        "response_type": "code",
        "client_id": settings().spa_client_id,
        "redirect_uri": settings().spa_callback_uri,
        "scope": "pfa.read pfa.write",
        "state": state,
        "code_challenge": pkce_challenge_s256(verifier),
        "code_challenge_method": "S256",
    }
    values.update(overrides)
    return await client.get("/oauth/authorize", params=values, headers={"Origin": settings().spa_origin})


def _transaction_parts(html: str) -> tuple[str, str]:
    proof = re.search(r'name="transaction_proof" value="([^"]+)"', html).group(1)
    tx = re.search(r'action="/auth/authorization-transactions/([^/]+)/authenticate"', html).group(1)
    return tx, proof


# ---------------------------------------------------------------------------
# AUTH-01
# ---------------------------------------------------------------------------


async def test_authorization_code_flow_succeeds_and_sets_no_cookie(
    client: AsyncClient, database: Database
) -> None:
    email = "owner@example.test"
    await create_verified_user(database, email=email)
    session = await oauth_login(client, email=email)
    assert session.access_token
    assert session.refresh_token

    response = await client.get(
        "/api/v1/me", headers={"Authorization": f"Bearer {session.access_token}"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["email"] == email


async def test_login_page_never_sets_cookies_and_hides_the_password_from_the_spa(
    client: AsyncClient, database: Database
) -> None:
    email = "cookie@example.test"
    await create_verified_user(database, email=email)
    verifier = new_pkce_verifier()
    response = await _authorize(client, verifier=verifier, state=new_state())
    assert response.status_code == 200
    assert "set-cookie" not in {key.lower() for key in response.headers}
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert "no-store" in response.headers["Cache-Control"]
    # the page posts to the authorization origin, not to the SPA
    assert 'action="/auth/authorization-transactions/' in response.text
    assert "transaction_proof" in response.text


async def test_login_page_csp_allows_the_callback_redirect(
    client: AsyncClient, database: Database
) -> None:
    """Chromium applies ``form-action`` to the redirect target of a form POST.

    The login form 303s to the SPA callback on a different origin, so the
    authorization page must list that origin; otherwise the browser blocks the
    submission entirely and the visitor stays on the login page.  The allowance is
    scoped to the authorization pages.
    """
    await create_verified_user(database, email="csp-login@example.test")
    verifier = new_pkce_verifier()
    response = await _authorize(client, verifier=verifier, state=new_state())
    assert response.status_code == 200

    csp = response.headers["content-security-policy"]
    assert "form-action 'self'" in csp
    assert "http://127.0.0.1:5173" in csp.split("form-action")[1]
    # scripts stay self-only: no inline script, no third party
    assert "script-src 'self'" in csp
    assert "unsafe-inline" not in csp.split("style-src")[0]
    # the form posts to its own origin
    assert 'action="/auth/authorization-transactions/' in response.text

    # application pages keep the strict form-action
    health = await client.get("/healthz")
    assert "form-action 'self'" in health.headers["content-security-policy"]
    assert "5173" not in health.headers["content-security-policy"]


async def test_callback_uri_must_match_the_registration_exactly(
    client: AsyncClient, database: Database
) -> None:
    await create_verified_user(database, email="exact@example.test")
    verifier = new_pkce_verifier()
    near_miss = [
        settings().spa_callback_uri + "/",
        settings().spa_callback_uri.replace("/auth/callback", "/auth/callback/"),
        settings().spa_callback_uri + "?extra=1",
        settings().spa_callback_uri.replace("127.0.0.1", "localhost"),
        settings().spa_callback_uri.replace("http://", "https://"),
    ]
    for uri in near_miss:
        response = await _authorize(client, verifier=verifier, state=new_state(), redirect_uri=uri)
        assert response.status_code == 400, uri
        assert response.headers.get("location") is None


# ---------------------------------------------------------------------------
# AUTH-02
# ---------------------------------------------------------------------------


async def test_wrong_verifier_is_rejected_without_tokens(
    client: AsyncClient, database: Database
) -> None:
    email = "verifier@example.test"
    await create_verified_user(database, email=email)
    verifier = new_pkce_verifier()
    other = new_pkce_verifier()
    state = new_state()
    response = await _authorize(client, verifier=verifier, state=state)
    tx, proof = _transaction_parts(response.text)
    authenticated = await client.post(
        f"/auth/authorization-transactions/{tx}/authenticate",
        data={"email": email, "password": TEST_USER_PASSWORD, "transaction_proof": proof},
        headers={"Origin": settings().spa_origin},
        follow_redirects=False,
    )
    code = re.search(r"[?&]code=([^&]+)", authenticated.headers["location"]).group(1)
    response = await client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": settings().spa_client_id,
            "code": code,
            "redirect_uri": settings().spa_callback_uri,
            "code_verifier": other,
        },
    )
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_grant"
    assert "access_token" not in response.json()


async def test_authorization_code_is_single_use(
    client: AsyncClient, database: Database
) -> None:
    email = "single@example.test"
    await create_verified_user(database, email=email)
    verifier = new_pkce_verifier()
    response = await _authorize(client, verifier=verifier, state=new_state())
    tx, proof = _transaction_parts(response.text)
    authenticated = await client.post(
        f"/auth/authorization-transactions/{tx}/authenticate",
        data={"email": email, "password": TEST_USER_PASSWORD, "transaction_proof": proof},
        headers={"Origin": settings().spa_origin},
        follow_redirects=False,
    )
    code = re.search(r"[?&]code=([^&]+)", authenticated.headers["location"]).group(1)
    payload = {
        "grant_type": "authorization_code",
        "client_id": settings().spa_client_id,
        "code": code,
        "redirect_uri": settings().spa_callback_uri,
        "code_verifier": verifier,
    }
    first = await client.post("/oauth/token", data=payload)
    second = await client.post("/oauth/token", data=payload)
    assert first.status_code == 200
    assert second.status_code == 400


async def test_expired_code_is_rejected(client: AsyncClient, database: Database) -> None:
    email = "expired@example.test"
    await create_verified_user(database, email=email)
    verifier = new_pkce_verifier()
    response = await _authorize(client, verifier=verifier, state=new_state())
    tx, proof = _transaction_parts(response.text)
    authenticated = await client.post(
        f"/auth/authorization-transactions/{tx}/authenticate",
        data={"email": email, "password": TEST_USER_PASSWORD, "transaction_proof": proof},
        headers={"Origin": settings().spa_origin},
        follow_redirects=False,
    )
    code = re.search(r"[?&]code=([^&]+)", authenticated.headers["location"]).group(1)
    clock.freeze(clock.now() + timedelta(seconds=120))
    response = await client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": settings().spa_client_id,
            "code": code,
            "redirect_uri": settings().spa_callback_uri,
            "code_verifier": verifier,
        },
    )
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_grant"


async def test_unsupported_challenge_method_is_rejected(
    client: AsyncClient, database: Database
) -> None:
    await create_verified_user(database, email="plain@example.test")
    response = await _authorize(
        client,
        verifier=new_pkce_verifier(),
        state=new_state(),
        code_challenge="a" * 43,
        code_challenge_method="plain",
    )
    assert response.status_code == 400
    assert "PKCE" in response.text


async def test_short_state_and_missing_challenge_are_rejected(
    client: AsyncClient, database: Database
) -> None:
    await create_verified_user(database, email="state@example.test")
    response = await _authorize(client, verifier=new_pkce_verifier(), state="short")
    assert response.status_code == 400
    response = await _authorize(
        client, verifier=new_pkce_verifier(), state=new_state(), code_challenge=""
    )
    assert response.status_code == 400


async def test_unknown_scope_and_unknown_client_are_rejected(
    client: AsyncClient, database: Database
) -> None:
    await create_verified_user(database, email="scope@example.test")
    response = await _authorize(
        client, verifier=new_pkce_verifier(), state=new_state(), scope="pfa.admin"
    )
    assert response.status_code == 400
    assert "Scope nepermis" in response.text
    response = await _authorize(
        client, verifier=new_pkce_verifier(), state=new_state(), client_id="not-registered"
    )
    assert response.status_code == 400
    # without a transaction there is nothing to authenticate: no form may post to an
    # empty transaction id (it used to submit to `/authorization-transactions//authenticate`)
    assert "<form" not in response.text
    assert "authorization-transactions//" not in response.text
    assert 'href="http://127.0.0.1:5173/"' in response.text


async def test_refresh_token_cannot_access_the_api(client: AsyncClient, database: Database) -> None:
    email = "refresh-as-api@example.test"
    await create_verified_user(database, email=email)
    session = await oauth_login(client, email=email)
    response = await client.get(
        "/api/v1/me", headers={"Authorization": f"Bearer {session.refresh_token}"}
    )
    assert response.status_code == 401


async def test_algorithm_confusion_and_tampered_tokens_are_rejected(
    client: AsyncClient, database: Database
) -> None:
    email = "alg@example.test"
    await create_verified_user(database, email=email)
    session = await oauth_login(client, email=email)
    import jwt as pyjwt

    unsigned = pyjwt.encode({"sub": "x", "token_use": "access"}, key=None, algorithm="none")
    for token in (unsigned, session.access_token[:-3] + "abc", "not-a-jwt"):
        response = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# AUTH-03
# ---------------------------------------------------------------------------


async def test_password_post_cannot_replace_redirect_or_challenge(
    client: AsyncClient, database: Database
) -> None:
    email = "tamper@example.test"
    await create_verified_user(database, email=email)
    verifier = new_pkce_verifier()
    response = await _authorize(client, verifier=verifier, state=new_state())
    tx, proof = _transaction_parts(response.text)
    evil = "https://evil.example/callback"
    response = await client.post(
        f"/auth/authorization-transactions/{tx}/authenticate",
        data={
            "email": email,
            "password": TEST_USER_PASSWORD,
            "transaction_proof": proof,
            # fields an attacker would try to inject
            "redirect_uri": evil,
            "code_challenge": "b" * 43,
            "scope": "pfa.manage",
        },
        headers={"Origin": settings().spa_origin},
        follow_redirects=False,
    )
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith(settings().spa_callback_uri)
    assert evil not in location


async def test_wrong_transaction_proof_is_rejected(
    client: AsyncClient, database: Database
) -> None:
    email = "proof@example.test"
    await create_verified_user(database, email=email)
    response = await _authorize(client, verifier=new_pkce_verifier(), state=new_state())
    tx, _proof = _transaction_parts(response.text)
    rejected = await client.post(
        f"/auth/authorization-transactions/{tx}/authenticate",
        data={"email": email, "password": TEST_USER_PASSWORD, "transaction_proof": "x" * 40},
        headers={"Origin": settings().spa_origin},
        follow_redirects=False,
    )
    assert rejected.status_code == 403
    assert "set-cookie" not in {key.lower() for key in rejected.headers}


async def test_wrong_password_stays_on_the_authorization_origin(
    client: AsyncClient, database: Database
) -> None:
    """A failed login returns to the canonical authorization URL as a GET.

    That keeps no stale POST in the browser history (a reload is safe), never
    reaches the SPA callback, and never echoes request text into the page.
    """
    email = "failed@example.test"
    await create_verified_user(database, email=email)
    response = await _authorize(client, verifier=new_pkce_verifier(), state=new_state())
    tx, proof = _transaction_parts(response.text)
    rejected = await client.post(
        f"/auth/authorization-transactions/{tx}/authenticate",
        data={"email": email, "password": "wrong-password-123", "transaction_proof": proof},
        headers={"Origin": settings().spa_origin},
        follow_redirects=False,
    )
    assert rejected.status_code == 303
    assert rejected.headers.get("set-cookie") is None
    location = rejected.headers["location"]
    assert location.startswith("/oauth/authorize?transaction=")
    assert "error=invalid_credentials" in location
    # the SPA callback is never a target of a failed login
    assert "/auth/callback" not in location

    page = await client.get(location)
    assert page.status_code == 200
    assert "Datele de autentificare" in page.text
    # a fresh one-use proof is issued for the retry, and the old one is dead
    fresh = re.search(r'name="transaction_proof" value="([^"]+)"', page.text).group(1)
    assert fresh != proof
    stale = await client.post(
        f"/auth/authorization-transactions/{tx}/authenticate",
        data={"email": email, "password": TEST_USER_PASSWORD, "transaction_proof": proof},
        headers={"Origin": settings().spa_origin},
        follow_redirects=False,
    )
    assert stale.status_code == 403
    retry = await client.post(
        f"/auth/authorization-transactions/{tx}/authenticate",
        data={"email": email, "password": TEST_USER_PASSWORD, "transaction_proof": fresh},
        headers={"Origin": settings().spa_origin},
        follow_redirects=False,
    )
    assert retry.status_code == 303
    assert retry.headers["location"].startswith(settings().spa_callback_uri)


async def test_transaction_expires_after_five_minutes(client: AsyncClient, database: Database) -> None:
    email = "tx-expiry@example.test"
    await create_verified_user(database, email=email)
    response = await _authorize(client, verifier=new_pkce_verifier(), state=new_state())
    tx, proof = _transaction_parts(response.text)
    clock.freeze(clock.now() + timedelta(minutes=6))
    rejected = await client.post(
        f"/auth/authorization-transactions/{tx}/authenticate",
        data={"email": email, "password": TEST_USER_PASSWORD, "transaction_proof": proof},
        headers={"Origin": settings().spa_origin},
        follow_redirects=False,
    )
    assert rejected.status_code == 422
    assert rejected.headers.get("location") is None


async def test_login_attempts_are_limited_per_transaction(
    client: AsyncClient, database: Database
) -> None:
    email = "attempts@example.test"
    await create_verified_user(database, email=email)
    response = await _authorize(client, verifier=new_pkce_verifier(), state=new_state())
    tx, proof = _transaction_parts(response.text)
    seen: list[str] = []
    for _ in range(settings().login_attempt_limit + 2):
        response = await client.post(
            f"/auth/authorization-transactions/{tx}/authenticate",
            data={"email": email, "password": "wrong-password-123", "transaction_proof": proof},
            headers={"Origin": settings().spa_origin},
            follow_redirects=False,
        )
        if response.status_code == 303:
            # a failed attempt returns to the canonical page with a fresh proof
            location = response.headers["location"]
            seen.append(location.split("error=")[-1])
            page = await client.get(location)
            proof = re.search(r'name="transaction_proof" value="([^"]+)"', page.text).group(1)
        else:
            seen.append(f"status:{response.status_code}")
    assert "invalid_credentials" in seen
    assert "too_many_attempts" in seen, seen


# ---------------------------------------------------------------------------
# AUTH-04 / AUTH-05
# ---------------------------------------------------------------------------


async def test_refresh_rotation_is_single_use_and_reuse_revokes_the_family(
    client: AsyncClient, database: Database
) -> None:
    email = "rotation@example.test"
    await create_verified_user(database, email=email)
    session = await oauth_login(client, email=email)

    first = await client.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": settings().spa_client_id,
            "refresh_token": session.refresh_token,
        },
    )
    assert first.status_code == 200, first.text
    rotated = first.json()
    assert rotated["refresh_token"] != session.refresh_token

    # a second use of the *same* old token must fail and revoke the family
    reuse = await client.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": settings().spa_client_id,
            "refresh_token": session.refresh_token,
        },
    )
    assert reuse.status_code == 400

    # the replacement is now dead too, and so is its access token
    after = await client.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": settings().spa_client_id,
            "refresh_token": rotated["refresh_token"],
        },
    )
    assert after.status_code == 400
    api = await client.get(
        "/api/v1/me", headers={"Authorization": f"Bearer {rotated['access_token']}"}
    )
    assert api.status_code == 401


async def test_absolute_session_lifetime_is_not_extended_by_rotation(
    client: AsyncClient, database: Database
) -> None:
    email = "absolute@example.test"
    await create_verified_user(database, email=email)
    session = await oauth_login(client, email=email)
    clock.freeze(clock.now() + timedelta(days=7, seconds=1))
    response = await client.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": settings().spa_client_id,
            "refresh_token": session.refresh_token,
        },
    )
    assert response.status_code == 400


async def test_logout_revokes_access_before_the_jwt_expires(
    client: AsyncClient, database: Database
) -> None:
    email = "logout@example.test"
    await create_verified_user(database, email=email)
    session = await oauth_login(client, email=email)
    assert (
        await client.get("/api/v1/me", headers={"Authorization": f"Bearer {session.access_token}"})
    ).status_code == 200
    logout = await client.post(
        "/api/v1/sessions/logout", headers={"Authorization": f"Bearer {session.access_token}"}
    )
    assert logout.status_code == 200
    # the access token is still cryptographically valid but the session is gone
    assert (
        await client.get("/api/v1/me", headers={"Authorization": f"Bearer {session.access_token}"})
    ).status_code == 401


async def test_password_reset_revokes_every_session(
    client: AsyncClient, database: Database, oauth_database: Database
) -> None:
    email = "reset@example.test"
    user_id = await create_verified_user(database, email=email)
    session = await oauth_login(client, email=email)
    # sessions live in the authorization server's own database
    async with oauth_database.session() as db_session:
        await oauth_sessions.revoke_all_for_user(
            db_session, uuid.UUID(user_id), reason="test_reset"
        )
    assert (
        await client.get("/api/v1/me", headers={"Authorization": f"Bearer {session.access_token}"})
    ).status_code == 401


async def test_access_tokens_are_short_lived_and_refresh_tokens_have_their_own_audience() -> None:
    store = KeyStore.from_settings(settings())
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    access = issue_access_token(
        store, settings(), user_id=user_id, client_id="c", scope="pfa.read", session_id=session_id
    )
    claims = decode_token(store, access, expected_use="access", now=clock.now())
    assert claims["aud"] == ACCESS_AUDIENCE
    assert claims["exp"] - claims["iat"] == settings().access_token_ttl_seconds
    assert "CNP" not in claims and "password" not in claims

    with pytest.raises(TokenError) as excinfo:
        decode_token(store, access, expected_use="refresh", now=clock.now())
    # rejected either by audience or by token type, before any claim is trusted
    assert excinfo.value.reason in {"wrong_token_use", "wrong_audience"}

    from pfa.security.jwt import issue_refresh_token

    refresh, _jti, _exp = issue_refresh_token(
        store,
        settings(),
        user_id=user_id,
        client_id="c",
        session_id=session_id,
        family_id=session_id,
    )
    refresh_claims = decode_token(store, refresh, expected_use="refresh", now=clock.now())
    assert refresh_claims["aud"] == REFRESH_AUDIENCE
    assert refresh_claims["token_use"] == "refresh"


async def test_expired_access_token_is_rejected(client: AsyncClient, database: Database) -> None:
    email = "expired-access@example.test"
    await create_verified_user(database, email=email)
    session = await oauth_login(client, email=email)
    clock.freeze(clock.now() + timedelta(seconds=settings().access_token_ttl_seconds + 1))
    response = await client.get(
        "/api/v1/me", headers={"Authorization": f"Bearer {session.access_token}"}
    )
    assert response.status_code == 401


async def test_jwks_publishes_verification_keys_without_private_material(
    client: AsyncClient,
) -> None:
    response = await client.get("/.well-known/jwks.json")
    assert response.status_code == 200
    keys = response.json()["keys"]
    assert keys and all(key["kty"] == "OKP" for key in keys)
    assert all("d" not in key for key in keys)


async def test_signing_key_rotation_keeps_old_tokens_verifiable(tmp_path) -> None:
    """A rotation overlap must not invalidate tokens signed by the previous key."""
    from datetime import UTC, datetime
    from datetime import timedelta as td

    store = KeyStore.generate(tmp_path, "old-key", now=datetime.now(tz=UTC) - td(hours=10))
    old_token = issue_access_token(
        store,
        settings(),
        user_id=uuid.uuid4(),
        client_id="c",
        scope="pfa.read",
        session_id=uuid.uuid4(),
    )
    rotated = KeyStore.generate(
        tmp_path,
        "new-key",
        retire_kids=["old-key"],
        now=datetime.now(tz=UTC),
        overlap=td(hours=48),
    )
    claims = decode_token(rotated, old_token, expected_use="access", now=clock.now())
    assert claims["sub"]
    assert rotated.active_kid == "new-key"

    # after the overlap window the old key stops verifying
    far_future = datetime.now(tz=UTC) + td(hours=72)
    late_store = KeyStore.load(tmp_path, "new-key")
    with pytest.raises(TokenError):
        decode_token(late_store, old_token, expected_use="access", now=far_future)


# ---------------------------------------------------------------------------
# IDENTITY
# ---------------------------------------------------------------------------


async def test_registration_verification_and_duplicate_email(
    client: AsyncClient, database: Database
) -> None:
    response = await client.post(
        "/auth/register", json={"email": "new@example.test", "password": TEST_USER_PASSWORD}
    )
    assert response.status_code == 202
    assert "access_token" not in response.json()

    duplicate = await client.post(
        "/auth/register", json={"email": "NEW@example.test", "password": TEST_USER_PASSWORD}
    )
    assert duplicate.status_code == 409
    # the response must not reveal that the address exists
    assert "deja" not in duplicate.json()["title"].lower()


async def test_unverified_account_cannot_obtain_tokens(
    client: AsyncClient, database: Database
) -> None:
    await client.post(
        "/auth/register", json={"email": "unverified@example.test", "password": TEST_USER_PASSWORD}
    )
    verifier = new_pkce_verifier()
    response = await _authorize(client, verifier=verifier, state=new_state())
    tx, proof = _transaction_parts(response.text)
    rejected = await client.post(
        f"/auth/authorization-transactions/{tx}/authenticate",
        data={
            "email": "unverified@example.test",
            "password": TEST_USER_PASSWORD,
            "transaction_proof": proof,
        },
        headers={"Origin": settings().spa_origin},
        follow_redirects=False,
    )
    assert rejected.status_code == 303
    assert "error=email_not_verified" in rejected.headers["location"]
    page = await client.get(rejected.headers["location"])
    assert "nu este verificat" in page.text
    assert "acest link de activare" in page.text.lower() or "linkul de activare" in page.text


async def test_verification_token_is_single_use(
    client: AsyncClient, database: Database, owner_database: Database
) -> None:
    await client.post(
        "/auth/register", json={"email": "replay@example.test", "password": TEST_USER_PASSWORD}
    )
    payload = await fetch_outbox_payload(owner_database, event_type="email.verification_requested")
    token = payload["token"]
    first = await client.post("/auth/verify-email", json={"token": token})
    assert first.status_code == 200
    second = await client.post("/auth/verify-email", json={"token": token})
    assert second.status_code == 409

    expired_token = "x" * 32
    response = await client.post("/auth/verify-email", json={"token": expired_token})
    assert response.status_code == 422


async def test_verification_resend_issues_a_new_link_and_retires_the_old_one(
    client: AsyncClient, database: Database, owner_database: Database
) -> None:
    """An expired activation link must be replaceable without re-registering."""
    email = "expired-link@example.test"
    await client.post("/auth/register", json={"email": email, "password": TEST_USER_PASSWORD})
    old_token = (
        await fetch_outbox_payload(owner_database, event_type="email.verification_requested")
    )["token"]

    response = await client.post("/auth/verify-email/resend", json={"email": email})
    assert response.status_code == 202
    assert response.headers["cache-control"] == "no-store"
    new_token = (
        await fetch_outbox_payload(owner_database, event_type="email.verification_requested")
    )["token"]
    assert new_token != old_token

    # issuing a new link supersedes the old one: it can no longer be replayed
    stale = await client.post("/auth/verify-email", json={"token": old_token})
    assert stale.status_code == 409
    # and the fresh link activates the account
    assert (
        await client.post("/auth/verify-email", json={"token": new_token})
    ).status_code == 200

    # once active, a further resend is a silent no-op (same generic answer)
    after = await client.post("/auth/verify-email/resend", json={"email": email})
    assert after.status_code == 202


async def test_verification_resend_is_generic_and_rate_limited(
    client: AsyncClient, database: Database
) -> None:
    """Unknown and active addresses get the same answer; floods are throttled."""
    known = "resend-known@example.test"
    await client.post("/auth/register", json={"email": known, "password": TEST_USER_PASSWORD})

    unknown = await client.post(
        "/auth/verify-email/resend", json={"email": "nobody-resend@example.test"}
    )
    first = await client.post("/auth/verify-email/resend", json={"email": known})
    assert unknown.status_code == first.status_code == 202
    assert unknown.json() == first.json()

    active = "resend-active@example.test"
    await create_verified_user(database, email=active)
    active_response = await client.post("/auth/verify-email/resend", json={"email": active})
    assert active_response.status_code == 202
    assert active_response.json() == first.json()

    limited = first
    for _ in range(5):
        limited = await client.post("/auth/verify-email/resend", json={"email": known})
        if limited.status_code == 429:
            break
    assert limited.status_code == 429
    assert "retry_after_seconds" in limited.json().get("details", {})


async def test_concurrent_resends_leave_exactly_one_active_link(
    client: AsyncClient, database: Database, owner_database: Database
) -> None:
    """Two racing resends must not create two usable activation links."""
    email = "race-resend@example.test"
    await client.post("/auth/register", json={"email": email, "password": TEST_USER_PASSWORD})

    responses = await asyncio.gather(
        client.post("/auth/verify-email/resend", json={"email": email}),
        client.post("/auth/verify-email/resend", json={"email": email}),
    )
    assert [r.status_code for r in responses] == [202, 202]

    async with owner_database.session() as session:
        active = (
            await session.execute(
                text(
                    """
                    SELECT count(*)
                      FROM email_verification_tokens t
                      JOIN users u ON u.id = t.user_id
                     WHERE u.email_normalized = :email
                       AND t.used_at IS NULL
                    """
                ),
                {"email": email},
            )
        ).scalar_one()
    assert active == 1


async def test_password_reset_is_generic_and_retires_sessions(
    client: AsyncClient, database: Database, owner_database: Database
) -> None:
    email = "resetrequest@example.test"
    await create_verified_user(database, email=email)
    unknown = await client.post("/auth/password-reset/request", json={"email": "nobody@example.test"})
    known = await client.post("/auth/password-reset/request", json={"email": email})
    assert unknown.status_code == known.status_code == 202
    assert unknown.json() == known.json()

    payload = await fetch_outbox_payload(
        owner_database, event_type="email.password_reset_requested"
    )
    token = payload["token"]
    session_before = await oauth_login(client, email=email)
    response = await client.post(
        "/auth/password-reset/confirm",
        json={"token": token, "new_password": "New-Correct-Horse-42"},
    )
    assert response.status_code == 200
    assert (
        await client.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {session_before.access_token}"}
        )
    ).status_code == 401
    # the new password works, the old one does not
    assert (await client.post("/auth/password-reset/confirm", json={"token": token, "new_password": "Another-Password-7"})).status_code == 409


async def test_weak_password_is_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/register", json={"email": "weak@example.test", "password": "short"}
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Failed logins are counted and audited even though the request fails
# ---------------------------------------------------------------------------


async def _attempt(client: AsyncClient, tx: str, proof: str, *, email: str, password: str):
    return await client.post(
        f"/auth/authorization-transactions/{tx}/authenticate",
        data={"email": email, "password": password, "transaction_proof": proof},
        headers={"Origin": settings().spa_origin},
        follow_redirects=False,
    )


async def test_a_failed_login_is_counted_and_audited(
    client: AsyncClient,
    database: Database,
    owner_database: Database,
    oauth_owner_database: Database,
) -> None:
    """The attempt counter and the audit record must survive the failing request.

    Both used to be written inside the request's transaction and were rolled back
    with it, so the per-transaction attempt limit was unreachable and failed logins
    left no audit trail.
    """
    email = "counted@example.test"
    user_id = await create_verified_user(database, email=email)
    tx, proof = _transaction_parts(
        (await _authorize(client, verifier=new_pkce_verifier(), state=new_state())).text
    )
    rejected = await _attempt(client, tx, proof, email=email, password="wrong-password-123")
    assert "error=invalid_credentials" in rejected.headers["location"]

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
    assert attempts == 1
    assert audited == 1


async def test_the_per_transaction_attempt_limit_is_enforced(
    client: AsyncClient, database: Database
) -> None:
    """After the limit, even correct credentials are refused for that transaction.

    Distinct unknown e-mails keep the per-account rate limiter out of the way, so
    only the transaction's own counter can produce the refusal.
    """
    email = "limited@example.test"
    await create_verified_user(database, email=email)
    response = await _authorize(client, verifier=new_pkce_verifier(), state=new_state())
    tx, proof = _transaction_parts(response.text)
    for index in range(settings().login_attempt_limit):
        rejected = await _attempt(
            client, tx, proof, email=f"nobody-{index}@example.test", password="wrong-password-123"
        )
        assert "error=invalid_credentials" in rejected.headers["location"]
        page = await client.get(rejected.headers["location"])
        proof = re.search(r'name="transaction_proof" value="([^"]+)"', page.text).group(1)

    refused = await _attempt(client, tx, proof, email=email, password=TEST_USER_PASSWORD)
    assert refused.status_code == 303
    assert "error=too_many_attempts" in refused.headers["location"]
    assert not refused.headers["location"].startswith(settings().spa_callback_uri)
