"""Access-token introspection: is this token valid *and* is its session still live?

This is what a resource server needs on every request (RFC 7662 semantics): the JWT
alone is not enough, because logout and password reset must invalidate access
before the token expires.  In the monolith it is called in-process through
:mod:`pfa.wiring`; as a separate service it would back an ``/oauth/introspect``
endpoint.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from pfa import clock
from pfa.errors import Unauthenticated
from pfa.oauth import sessions
from pfa.oauth.db import get_oauth_database
from pfa.oauth.keys import get_keys
from pfa.security.jwt import TokenError, decode_token


@dataclass(frozen=True, slots=True)
class ActiveToken:
    subject_id: uuid.UUID
    session_id: uuid.UUID
    client_id: str
    scope: frozenset[str]
    jti: str
    #: expiry as a Unix timestamp (RFC 7662 ``exp``)
    expires_at: int | None = None


async def introspect_access_token(token: str) -> ActiveToken:
    """Validate signature, audience, expiry and token use, then the session record.

    Raises :class:`~pfa.errors.Unauthenticated` for every inactive token.
    """
    try:
        claims = decode_token(get_keys(), token, expected_use="access", now=clock.now())
    except TokenError as exc:
        raise Unauthenticated(
            "Token de acces invalid sau expirat.", details={"reason": exc.reason}
        ) from exc
    try:
        subject_id = uuid.UUID(str(claims["sub"]))
        session_id = uuid.UUID(str(claims["sid"]))
    except (KeyError, ValueError) as exc:
        raise Unauthenticated(
            "Token de acces invalid sau expirat.", details={"reason": "malformed_claims"}
        ) from exc

    async with get_oauth_database().session() as session:
        active = await sessions.is_active(session, session_id)
    if not active:
        raise Unauthenticated("Sesiunea a fost revocată. Autentificați-vă din nou.")
    return ActiveToken(
        subject_id=subject_id,
        session_id=session_id,
        client_id=str(claims.get("client_id")),
        scope=frozenset(str(claims.get("scope", "")).split()),
        jti=str(claims.get("jti")),
        expires_at=int(claims["exp"]) if claims.get("exp") is not None else None,
    )
