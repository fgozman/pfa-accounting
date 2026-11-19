"""JWT access and refresh tokens (EdDSA), with strict validation paths."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt

from pfa import clock as app_clock
from pfa.config import Settings, get_settings
from pfa.security.keys import ALGORITHM, KeyStore, KeyStoreError

#: tolerated clock skew between issuer and verifier
CLOCK_SKEW_SECONDS = 5

ACCESS_AUDIENCE = "pfa-api"
REFRESH_AUDIENCE = "pfa-auth-refresh"
ISSUER = "pfa-authorization-server"

TokenUse = Literal["access", "refresh"]


class TokenError(Exception):
    """Any rejected token.  The reason is deliberately coarse for the caller."""

    def __init__(self, reason: str, *, detail: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def _now(now: datetime | None) -> datetime:
    """Single injectable clock: token times never depend on the wall clock."""
    value = now or app_clock.now()
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def issue_access_token(
    store: KeyStore,
    settings: Settings | None = None,
    *,
    user_id: uuid.UUID,
    client_id: str,
    scope: str,
    session_id: uuid.UUID,
    jti: uuid.UUID | None = None,
    now: datetime | None = None,
) -> str:
    settings = settings or get_settings()
    issued = _now(now)
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": ACCESS_AUDIENCE,
        "sub": str(user_id),
        "client_id": client_id,
        "scope": scope,
        "sid": str(session_id),
        "jti": str(jti or uuid.uuid4()),
        "token_use": "access",
        "iat": int(issued.timestamp()),
        "nbf": int(issued.timestamp()),
        "exp": int((issued + timedelta(seconds=settings.access_token_ttl_seconds)).timestamp()),
    }
    return jwt.encode(
        claims,
        store.active().private_key,
        algorithm=ALGORITHM,
        headers={"kid": store.active_kid},
    )


def issue_refresh_token(
    store: KeyStore,
    settings: Settings | None = None,
    *,
    user_id: uuid.UUID,
    client_id: str,
    session_id: uuid.UUID,
    family_id: uuid.UUID,
    ttl_seconds: int | None = None,
    jti: uuid.UUID | None = None,
    now: datetime | None = None,
) -> tuple[str, uuid.UUID, datetime]:
    settings = settings or get_settings()
    issued = _now(now)
    token_jti = jti or uuid.uuid4()
    expires = issued + timedelta(
        seconds=ttl_seconds if ttl_seconds is not None else settings.refresh_token_ttl_seconds
    )
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": REFRESH_AUDIENCE,
        "sub": str(user_id),
        "client_id": client_id,
        "sid": str(session_id),
        "family_id": str(family_id),
        "jti": str(token_jti),
        "token_use": "refresh",
        "iat": int(issued.timestamp()),
        "nbf": int(issued.timestamp()),
        "exp": int(expires.timestamp()),
    }
    token = jwt.encode(
        claims,
        store.active().private_key,
        algorithm=ALGORITHM,
        headers={"kid": store.active_kid},
    )
    return token, token_jti, expires


def decode_token(
    store: KeyStore,
    token: str,
    *,
    expected_use: TokenUse,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate issuer, exact audience, type, algorithm, signature and time bounds."""
    audience = ACCESS_AUDIENCE if expected_use == "access" else REFRESH_AUDIENCE
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:  # malformed token
        raise TokenError("malformed_token", detail=str(exc)) from exc

    if header.get("alg") != ALGORITHM:
        # rejects `none` and algorithm confusion before any key is consulted
        raise TokenError("unsupported_algorithm", detail=str(header.get("alg")))
    kid = header.get("kid")
    if not kid:
        raise TokenError("missing_kid")
    key = store.public_key(str(kid))
    if key is None:
        raise TokenError("unknown_kid")

    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=[ALGORITHM],
            audience=audience,
            issuer=ISSUER,
            options={
                "require": ["exp", "iat", "nbf", "sub", "aud", "iss"],
                # Time bounds are validated below against the injected clock so
                # deadlines are deterministic and testable.  PyJWT would otherwise
                # use the process wall clock.
                "verify_exp": False,
                "verify_nbf": False,
                "verify_iat": False,
                "verify_aud": True,
                "verify_iss": True,
                "verify_signature": True,
            },
            leeway=0,
        )
    except jwt.InvalidAudienceError as exc:
        raise TokenError("wrong_audience", detail=str(exc)) from exc
    except jwt.InvalidIssuerError as exc:
        raise TokenError("wrong_issuer", detail=str(exc)) from exc
    except jwt.PyJWTError as exc:
        raise TokenError("invalid_signature", detail=str(exc)) from exc

    reference = _now(now)
    try:
        expires_at = datetime.fromtimestamp(int(claims["exp"]), tz=UTC)
        not_before = datetime.fromtimestamp(int(claims["nbf"]), tz=UTC)
        issued_at = datetime.fromtimestamp(int(claims["iat"]), tz=UTC)
    except (KeyError, TypeError, ValueError) as exc:  # pragma: no cover - defensive
        raise TokenError("invalid_time_claims", detail=str(exc)) from exc
    if reference >= expires_at:
        raise TokenError("expired")
    if reference < not_before:
        raise TokenError("not_yet_valid")
    if issued_at > reference + timedelta(seconds=CLOCK_SKEW_SECONDS):
        raise TokenError("issued_in_the_future")

    if claims.get("token_use") != expected_use:
        # a refresh token must never be accepted by the resource API
        raise TokenError("wrong_token_use")
    return claims


def jwt_from_store(store: KeyStore) -> KeyStore:
    """Small indirection so tests can inject a rotated store."""
    if store is None:  # pragma: no cover - defensive
        raise KeyStoreError("key store is required")
    return store
