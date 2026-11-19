"""Login-session lifecycle: the authorization server's public surface for sessions.

A *session* is a refresh-token family (``oauth_sessions`` + ``oauth_refresh_tokens``).
This module is deliberately small and depends only on :mod:`pfa.oauth.repo` and
the audit port:

* :mod:`pfa.oauth.introspection` checks :func:`is_active` for every API request,
  so logout and password reset invalidate access before the JWT expires;
* the application revokes a subject's sessions when credentials change
  (:func:`revoke_all_for_user`, reached through ``AuthorizationServerClient``);
* the flows revoke a family on code/refresh-token reuse (:func:`revoke`);
* the logout endpoint calls :func:`logout`.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from pfa import clock
from pfa.oauth import repo
from pfa.oauth.ports import audit_sink
from pfa.observability.logs import log_event

#: session lifecycle is security-relevant (OWASP: log session termination)
security_log = logging.getLogger("pfa.oauth.security")


async def is_active(session: AsyncSession, session_id: uuid.UUID) -> bool:
    """Access tokens are only as good as the server-side session record."""
    return await repo.session_is_active(session, session_id, now=clock.now())


async def revoke(session: AsyncSession, session_id: uuid.UUID, *, reason: str) -> None:
    """Revoke one session and every refresh token of its family."""
    tokens = await repo.revoke_session_refresh_tokens(session, session_id)
    revoked = await repo.revoke_session(session, session_id, reason=reason)
    if revoked or tokens:
        log_event(
            security_log,
            "oauth.session_revoked",
            message=f"session revoked ({reason})",
            attributes={
                "session.id": str(session_id),
                "pfa.oauth.reason": reason,
                "pfa.oauth.revoked_refresh_tokens": tokens,
            },
        )


async def revoke_all_for_user(session: AsyncSession, user_id: uuid.UUID, *, reason: str) -> int:
    """Revoke every refresh family of a user (logout everywhere, reset, email change)."""
    revoked = await repo.revoke_user_sessions(session, user_id, reason=reason)
    log_event(
        security_log,
        "oauth.sessions_revoked",
        level=logging.WARNING if revoked else logging.INFO,
        message=f"{revoked} session(s) revoked ({reason})",
        attributes={
            "user.id": str(user_id),
            "pfa.oauth.reason": reason,
            "pfa.oauth.revoked_sessions": revoked,
        },
    )
    await audit_sink().record(
        action="session.revoked_all",
        entity_type="user",
        entity_id=user_id,
        actor_user_id=user_id,
    )
    return revoked


async def logout(session: AsyncSession, *, session_id: uuid.UUID, user_id: uuid.UUID) -> None:
    await revoke(session, session_id, reason="logout")
    await audit_sink().record(
        action="session.logout",
        entity_type="session",
        entity_id=session_id,
        actor_user_id=user_id,
    )


__all__ = ["is_active", "logout", "revoke", "revoke_all_for_user"]
