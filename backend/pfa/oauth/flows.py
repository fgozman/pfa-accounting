"""OAuth 2.0 authorization server flows: Authorization Code + PKCE (S256), cookie-free.

Scope of this module: client registration, authorization transactions, codes,
the token endpoint (code exchange, refresh rotation) and RFC 7009 revocation.
Session lifecycle (``is_active``, revoke, logout) lives in :mod:`pfa.oauth.sessions`.

Design notes
------------
* The SPA is a **public client** (``token_endpoint_auth_method=none``).
* Email/password authentication happens on the authorization server's login page
  inside an authorization transaction; credentials never reach the SPA.
* Authorization transactions live in PostgreSQL, replacing a cookie-based login
  session.  No ``Set-Cookie`` is ever produced on an authentication path.
* Codes and refresh tokens are stored as hashes and consumed with conditional
  ``UPDATE ... WHERE used_at IS NULL`` statements, so a duplicate exchange or a
  replayed refresh token is detected by the database rather than by a read-then-write
  race in Python.
* All statements live in :mod:`pfa.oauth.repo`.  Credentials are verified by the
  host's :class:`~pfa.oauth.ports.IdentityProvider`, which answers with an opaque
  subject id or raises :class:`~pfa.oauth.ports.LoginRejected`; this module never
  sees how accounts are stored.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from pfa import clock
from pfa.config import Settings, get_settings
from pfa.errors import (
    Conflict,
    DomainError,
    NotFound,
    PermissionDenied,
    RateLimited,
    ValidationFailed,
)
from pfa.oauth import repo, sessions
from pfa.oauth.ports import LoginRejected, audit_sink, identity_provider
from pfa.observability.logs import log_event
from pfa.security.jwt import TokenError, decode_token, issue_access_token, issue_refresh_token
from pfa.security.keys import KeyStore
from pfa.security.ratelimit import token_limiter
from pfa.security.scopes import SUPPORTED_SCOPES
from pfa.security.tokens import (
    constant_time_equals,
    hash_token,
    new_opaque_token,
    verify_pkce,
)

#: security-relevant events of the authorization server (alert on ``warning``)
security_log = logging.getLogger("pfa.oauth.security")


class OAuthError(DomainError):
    """OAuth-protocol error carrying the RFC 6749 error code."""

    http_status = 400

    def __init__(self, error: str, description: str, *, http_status: int | None = None) -> None:
        super().__init__(description, details={"error": error})
        self.error = error
        self.description = description
        self.code = error
        if http_status is not None:
            self.http_status = http_status


# ---------------------------------------------------------------------------
# Client registration
# ---------------------------------------------------------------------------


def validate_redirect_uri(uri: str, *, allow_localhost_http: bool) -> str:
    """Reject wildcards, fragments, userinfo, unknown schemes and prefix tricks."""
    if not uri or len(uri) > 500:
        raise ValidationFailed("Redirect URI invalid.", field_errors={"redirect_uri": "invalid"})
    if "*" in uri:
        raise ValidationFailed("Wildcard-urile nu sunt permise în redirect URI.")
    parsed = urlparse(uri)
    if parsed.fragment:
        raise ValidationFailed("Fragmentul nu este permis în redirect URI.")
    if parsed.username or parsed.password:
        raise ValidationFailed("Userinfo nu este permis în redirect URI.")
    if parsed.scheme == "https":
        if not parsed.netloc:
            raise ValidationFailed("Redirect URI invalid.")
    elif parsed.scheme == "http":
        if not allow_localhost_http:
            raise ValidationFailed("HTTP este permis doar în development, pe localhost.")
        if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValidationFailed("HTTP este permis doar pe localhost în development.")
    else:
        raise ValidationFailed("Schema redirect URI nu este acceptată.")
    return uri


async def register_client(
    session: AsyncSession,
    *,
    client_id: str,
    environment: str,
    redirect_uris: list[str],
    allowed_origins: list[str],
    allowed_scopes: list[str] | None = None,
    settings: Settings | None = None,
    enabled: bool = True,
):
    """Restricted deployment/admin operation, never arbitrary self-registration."""
    settings = settings or get_settings()
    if not redirect_uris:
        raise ValidationFailed("Cel puțin un redirect URI este obligatoriu.")
    validated = [
        validate_redirect_uri(uri, allow_localhost_http=environment != "production")
        for uri in redirect_uris
    ]
    client = await repo.upsert_client(
        session,
        client_id=client_id,
        client_type="public",
        token_endpoint_auth_method="none",
        environment=environment,
        enabled=enabled,
        require_pkce=True,
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        allowed_scopes=allowed_scopes or list(SUPPORTED_SCOPES),
        allowed_pkce_methods=["S256"],
    )
    for uri in validated:
        await repo.register_redirect_uri(session, client_pk=client.id, exact_uri=uri)
    for origin in allowed_origins:
        await repo.register_origin(session, client_pk=client.id, origin=origin)
    reloaded = await repo.get_client(
        session, client_id=client_id, environment=environment
    )
    assert reloaded is not None  # just written in this transaction
    return reloaded


async def get_client(session: AsyncSession, client_id: str, *, environment: str):
    client = await repo.get_client(session, client_id=client_id, environment=environment)
    if client is None or not client.enabled:
        raise OAuthError("invalid_client", "Clientul OAuth nu este cunoscut sau este dezactivat.")
    return client


# ---------------------------------------------------------------------------
# Authorization transaction
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AuthorizationRequest:
    client_id: str
    redirect_uri: str
    response_type: str
    scope: str
    state: str
    code_challenge: str
    code_challenge_method: str


@dataclass(frozen=True, slots=True)
class TransactionHandle:
    transaction: object
    proof: str


def _validate_challenge(challenge: str, method: str, client) -> None:
    if method not in list(client.allowed_pkce_methods or []):
        raise OAuthError(
            "invalid_request", f"Metoda PKCE {method!r} nu este acceptată pentru acest client."
        )
    if method != "S256":
        raise OAuthError("invalid_request", "Doar S256 este acceptat.")
    # SHA-256 base64url digest without padding is exactly 43 characters
    if len(challenge) != 43:
        raise OAuthError("invalid_request", "code_challenge invalid pentru S256.")


def _validate_state(state: str) -> None:
    if not state or len(state) < 16 or len(state) > 500:
        raise OAuthError("invalid_request", "Parametrul state lipsește sau este invalid.")


async def create_authorization_transaction(
    session: AsyncSession,
    request: AuthorizationRequest,
    *,
    origin: str | None,
    settings: Settings | None = None,
) -> TransactionHandle:
    settings = settings or get_settings()
    client = await get_client(session, request.client_id, environment=settings.env)

    if request.response_type != "code":
        raise OAuthError("unsupported_response_type", "Doar response_type=code este acceptat.")
    if "authorization_code" not in list(client.grant_types or []):
        raise OAuthError("unauthorized_client", "Grantul authorization_code nu este permis.")
    # exact match only: no prefix, no case folding, no trailing-slash tolerance
    registered = set(client.redirect_uris or [])
    if request.redirect_uri not in registered:
        # Never redirect to an unregistered URI: fail locally.
        raise OAuthError(
            "invalid_request",
            "redirect_uri nu corespunde exact unei valori înregistrate.",
        )
    _validate_challenge(request.code_challenge, request.code_challenge_method, client)
    _validate_state(request.state)

    requested = [scope for scope in request.scope.split() if scope]
    if not requested:
        raise OAuthError("invalid_scope", "Cel puțin un scope este obligatoriu.")
    unknown = [scope for scope in requested if scope not in list(client.allowed_scopes or [])]
    if unknown:
        raise OAuthError("invalid_scope", f"Scope nepermis: {', '.join(sorted(unknown))}")

    origin_hash = hash_token(origin) if origin else None
    if origin and origin not in set(client.origins or []):
        raise OAuthError("invalid_request", "Originea cererii nu este înregistrată.")

    proof = new_opaque_token(32)
    transaction = await repo.insert_transaction(
        session,
        client_pk=client.id,
        redirect_uri=request.redirect_uri,
        scope=" ".join(requested),
        state=request.state,
        code_challenge=request.code_challenge,
        code_challenge_method=request.code_challenge_method,
        proof_hash=hash_token(proof),
        origin_hash=origin_hash,
        expires_at=clock.now()
        + timedelta(seconds=settings.authorization_transaction_ttl_seconds),
    )
    return TransactionHandle(transaction=transaction, proof=proof)


async def reissue_login_proof(session: AsyncSession, transaction_id: uuid.UUID) -> str:
    """Rotate the one-use proof of a still-usable transaction and return the new one.

    Used to re-render the login page after a failed attempt.  Raises
    :class:`NotFound` when the transaction is unknown, consumed or expired: the
    caller must restart the authorization from the client.
    """
    transaction = await repo.get_transaction(session, transaction_id)
    if (
        transaction is None
        or transaction.consumed_at is not None
        or transaction.expires_at <= clock.now()
    ):
        raise NotFound("Sesiunea de autorizare a expirat. Reluați autentificarea.")
    proof = new_opaque_token(32)
    await repo.rotate_transaction_proof(session, transaction.id, hash_token(proof))
    return proof


async def authenticate_transaction(
    session: AsyncSession,
    *,
    transaction_id: uuid.UUID,
    proof: str,
    email: str,
    password: str,
    origin: str | None,
    settings: Settings | None = None,
    client_ip: str | None = None,
):
    """Authenticate inside a validated transaction.

    No access or refresh token is issued by this endpoint.  Client, redirect URI
    and PKCE values always come from the stored transaction: nothing in the login
    POST can replace them.
    """
    settings = settings or get_settings()
    transaction = await repo.get_transaction_for_update(session, transaction_id)
    if transaction is None:
        raise NotFound("Tranzacția de autorizare nu există.")
    if transaction.consumed_at is not None:
        raise Conflict("Tranzacția de autorizare a fost deja finalizată.")
    if transaction.expires_at <= clock.now():
        raise ValidationFailed("Tranzacția de autorizare a expirat. Reluați autentificarea.")
    if not constant_time_equals(transaction.proof_hash, hash_token(proof)):
        log_event(
            security_log,
            "oauth.transaction_proof_invalid",
            level=logging.WARNING,
            attributes={"pfa.oauth.transaction_id": str(transaction.id)},
        )
        raise PermissionDenied("Dovada tranzacției nu este validă.")
    if origin and transaction.origin_hash:
        if not constant_time_equals(transaction.origin_hash, hash_token(origin)):
            log_event(
                security_log,
                "oauth.origin_mismatch",
                level=logging.WARNING,
                attributes={"pfa.oauth.transaction_id": str(transaction.id)},
            )
            raise PermissionDenied("Originea cererii nu corespunde tranzacției.")
    if transaction.attempt_count >= settings.login_attempt_limit:
        log_event(
            security_log,
            "oauth.login_attempts_exhausted",
            level=logging.WARNING,
            attributes={
                "pfa.oauth.transaction_id": str(transaction.id),
                "pfa.oauth.attempt_count": transaction.attempt_count,
            },
        )
        raise LoginRejected(
            "too_many_attempts",
            "Prea multe încercări pentru această tranzacție. Reluați autentificarea.",
            http_status=RateLimited.http_status,
        )

    try:
        subject = await identity_provider().authenticate(
            email=email,
            password=password,
            client_ip=client_ip,
            attempt_key=str(transaction.id),
        )
    except LoginRejected as rejection:
        attempts = await repo.increment_transaction_attempts(session, transaction.id)
        # Committed on purpose: the request fails, and without this the rollback
        # would undo the count, so the per-transaction attempt limit could never
        # be reached.  (An unreachable identity provider is not the user's attempt.)
        await session.commit()
        # no e-mail address here: the identity provider audits the account side
        log_event(
            security_log,
            "oauth.login_rejected",
            level=logging.WARNING if rejection.reason == "too_many_attempts" else logging.INFO,
            attributes={
                "pfa.oauth.reason": rejection.reason,
                "pfa.oauth.transaction_id": str(transaction.id),
                "pfa.oauth.attempt_count": attempts,
            },
        )
        raise

    return await repo.mark_transaction_authenticated(
        session, transaction_id=transaction.id, user_id=subject.id, at=clock.now()
    )


async def issue_authorization_code(session: AsyncSession, *, transaction) -> str:
    """Create a one-use code bound to the transaction's client/redirect/PKCE."""
    settings = get_settings()
    if transaction.authenticated_user_id is None:
        raise PermissionDenied("Tranzacția nu este autentificată.")
    if transaction.consumed_at is not None:
        raise Conflict("Tranzacția a fost deja finalizată.")
    raw = new_opaque_token(32)
    await repo.insert_code(
        session,
        code_hash=hash_token(raw),
        user_id=transaction.authenticated_user_id,
        client_pk=transaction.client_id,
        transaction_id=transaction.id,
        redirect_uri=transaction.redirect_uri,
        code_challenge=transaction.code_challenge,
        code_challenge_method=transaction.code_challenge_method,
        scope=transaction.scope,
        expires_at=clock.now() + timedelta(seconds=settings.authorization_code_ttl_seconds),
    )
    await repo.consume_transaction(session, transaction.id, clock.now())
    return raw


@dataclass(frozen=True, slots=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int
    scope: str
    session_id: uuid.UUID

    def as_response(self) -> dict:
        return {
            "access_token": self.access_token,
            "token_type": "Bearer",
            "expires_in": self.expires_in,
            "refresh_token": self.refresh_token,
            "scope": self.scope,
        }


async def _create_session_and_tokens(
    session: AsyncSession,
    *,
    store: KeyStore,
    user_id: uuid.UUID,
    client,
    scope: str,
    settings: Settings,
    now=None,
) -> TokenPair:
    now = now or clock.now()
    login_session = await repo.insert_session(
        session,
        user_id=user_id,
        client_pk=client.id,
        scope=scope,
        absolute_expires_at=now + timedelta(seconds=settings.refresh_token_ttl_seconds),
    )
    access = issue_access_token(
        store,
        settings,
        user_id=user_id,
        client_id=client.client_id,
        scope=scope,
        session_id=login_session.id,
        now=now,
    )
    refresh, jti, expires = issue_refresh_token(
        store,
        settings,
        user_id=user_id,
        client_id=client.client_id,
        session_id=login_session.id,
        family_id=login_session.id,
        now=now,
    )
    await repo.insert_refresh_token(
        session,
        token_hash=hash_token(refresh),
        jti=jti,
        user_id=user_id,
        session_id=login_session.id,
        expires_at=expires,
    )
    return TokenPair(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.access_token_ttl_seconds,
        scope=scope,
        session_id=login_session.id,
    )


async def exchange_code(
    session: AsyncSession,
    *,
    store: KeyStore,
    client_id: str,
    code: str,
    redirect_uri: str,
    code_verifier: str,
    settings: Settings | None = None,
) -> TokenPair:
    settings = settings or get_settings()
    wait = token_limiter.check(f"token:{client_id}")
    if wait:
        raise RateLimited("Prea multe cereri de token.", details={"retry_after_seconds": wait})
    client = await get_client(session, client_id, environment=settings.env)
    row = await repo.get_code_for_update(session, hash_token(code))
    if row is None:
        token_limiter.record_failure(f"token:{client_id}")
        raise OAuthError("invalid_grant", "Codul de autorizare nu este valid.")
    if row.used_at is not None:
        token_limiter.record_failure(f"token:{client_id}")
        if row.session_id is not None:
            # RFC 9700: a replayed authorization code must not leave live tokens
            # behind.  The revocation is committed before the request fails,
            # otherwise the rollback would undo the countermeasure.
            await _revoke_family(session, row.session_id, reason="code_reuse")
            await session.commit()
        log_event(
            security_log,
            "oauth.code_reuse_detected",
            level=logging.WARNING,
            attributes={
                "pfa.oauth.client_id": client_id,
                "session.id": str(row.session_id) if row.session_id else None,
                "user.id": str(row.user_id),
            },
        )
        raise OAuthError("invalid_grant", "Codul de autorizare a fost deja folosit.")
    if row.expires_at <= clock.now():
        raise OAuthError("invalid_grant", "Codul de autorizare a expirat.")
    if row.client_id != client.id:
        raise OAuthError("invalid_grant", "Codul aparține altui client.")
    if row.redirect_uri != redirect_uri:
        raise OAuthError("invalid_grant", "redirect_uri nu corespunde celui autorizat.")
    if row.code_challenge_method != "S256" or not verify_pkce(
        code_verifier, row.code_challenge, row.code_challenge_method
    ):
        token_limiter.record_failure(f"token:{client_id}")
        raise OAuthError("invalid_grant", "code_verifier nu corespunde code_challenge.")

    pair = await _create_session_and_tokens(
        session,
        store=store,
        user_id=row.user_id,
        client=client,
        scope=row.scope,
        settings=settings,
    )
    # single use, enforced by the database: a concurrent duplicate exchange updates
    # nothing and therefore fails
    consumed = await repo.mark_code_used(
        session, code_id=row.id, used_at=clock.now(), session_id=pair.session_id
    )
    if consumed == 0:
        await _revoke_family(session, pair.session_id, reason="code_reuse")
        await session.commit()
        log_event(
            security_log,
            "oauth.code_reuse_detected",
            level=logging.WARNING,
            attributes={
                "pfa.oauth.client_id": client_id,
                "session.id": str(pair.session_id),
                "user.id": str(row.user_id),
                "pfa.oauth.concurrent": True,
            },
        )
        raise OAuthError("invalid_grant", "Codul de autorizare a fost deja folosit.")

    await audit_sink().record(
        action="oauth.code_exchanged",
        entity_type="session",
        entity_id=pair.session_id,
        actor_user_id=row.user_id,
    )
    return pair


async def rotate_refresh_token(
    session: AsyncSession,
    *,
    store: KeyStore,
    client_id: str,
    refresh_token: str,
    settings: Settings | None = None,
) -> TokenPair:
    """Rotate a refresh token under a row lock.

    Reuse of an already-rotated token revokes the whole family.  There is no
    grace window: a lost successful response may require reauthentication.
    """
    settings = settings or get_settings()
    client = await get_client(session, client_id, environment=settings.env)
    try:
        claims = decode_token(store, refresh_token, expected_use="refresh", now=clock.now())
    except TokenError as exc:
        raise OAuthError("invalid_grant", f"Refresh token invalid ({exc.reason}).") from exc
    if claims.get("client_id") != client.client_id:
        raise OAuthError("invalid_grant", "Refresh token aparține altui client.")

    row = await repo.refresh_token_for_update(session, hash_token(refresh_token))
    if row is None:
        raise OAuthError("invalid_grant", "Refresh token necunoscut.")
    login_session = await repo.get_session_for_update(session, row.session_id)
    if login_session is None:
        raise OAuthError("invalid_grant", "Sesiunea nu mai există.")

    reuse = row.used_at is not None or row.revoked_at is not None
    if reuse:
        await _revoke_family(session, login_session.id, reason="refresh_reuse")
        # committed on purpose: the family revocation is a security response and
        # must survive the failing request's rollback
        await session.commit()
        log_event(
            security_log,
            "oauth.refresh_token_reuse_detected",
            level=logging.WARNING,
            attributes={
                "pfa.oauth.client_id": client_id,
                "session.id": str(login_session.id),
                "user.id": str(row.user_id),
            },
        )
        raise OAuthError("invalid_grant", "Refresh token refolosit: sesiunea a fost revocată.")
    if login_session.revoked_at is not None:
        raise OAuthError("invalid_grant", "Sesiunea a fost revocată.")
    if row.expires_at <= clock.now() or login_session.absolute_expires_at <= clock.now():
        await _revoke_family(session, login_session.id, reason="expired")
        await session.commit()
        raise OAuthError("invalid_grant", "Refresh token expirat.")

    now = clock.now()
    # conditional update: only the first concurrent rotation can mark it used
    consumed = await repo.mark_refresh_token_used(session, row.id, now)
    _ = consumed
    refresh, jti, expires = issue_refresh_token(
        store,
        settings,
        user_id=row.user_id,
        client_id=client.client_id,
        session_id=login_session.id,
        family_id=login_session.id,
        ttl_seconds=int((login_session.absolute_expires_at - now).total_seconds()),
        now=now,
    )
    await repo.insert_refresh_token(
        session,
        token_hash=hash_token(refresh),
        jti=jti,
        user_id=row.user_id,
        session_id=login_session.id,
        parent_id=row.id,
        expires_at=expires,
    )
    access = issue_access_token(
        store,
        settings,
        user_id=row.user_id,
        client_id=client.client_id,
        scope=login_session.scope,
        session_id=login_session.id,
        now=now,
    )
    # token issuance on refresh (OWASP: log token issuance and refresh)
    log_event(
        security_log,
        "oauth.token_refreshed",
        attributes={
            "pfa.oauth.client_id": client.client_id,
            "session.id": str(login_session.id),
            "user.id": str(row.user_id),
        },
    )
    return TokenPair(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.access_token_ttl_seconds,
        scope=login_session.scope,
        session_id=login_session.id,
    )


async def _revoke_family(session: AsyncSession, session_id: uuid.UUID, *, reason: str) -> None:
    await sessions.revoke(session, session_id, reason=reason)


async def revoke_token(
    session: AsyncSession,
    *,
    store: KeyStore,
    client_id: str,
    token: str,
    token_type_hint: str | None = None,
    settings: Settings | None = None,
) -> None:
    """RFC 7009 revocation.  Unknown tokens are reported as success."""
    settings = settings or get_settings()
    await get_client(session, client_id, environment=settings.env)
    row = await repo.refresh_token_by_hash(session, hash_token(token))
    if row is not None:
        await _revoke_family(session, row.session_id, reason="revoked_by_client")
        return
    # An access token is revoked through its session (the jti claim is the
    # token identity).
    try:
        claims = decode_token(store, token, expected_use="access")
    except TokenError:
        return
    sid = claims.get("sid")
    if sid:
        try:
            session_id = uuid.UUID(str(sid))
        except ValueError:
            return
        await _revoke_family(session, session_id, reason="revoked_by_client")
