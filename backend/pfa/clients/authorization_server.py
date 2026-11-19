"""The application's view of the OAuth 2.0 authorization server.

The application (accounts, workspaces, accounting, ...) never imports
:mod:`pfa.oauth` and never touches an ``oauth_*`` table.  Everything it needs from
the authorization server goes through :class:`AuthorizationServerClient`:

* :meth:`~AuthorizationServerClient.introspect` on every API request: is this
  access token valid and is its session still live (RFC 7662 semantics);
* :meth:`~AuthorizationServerClient.revoke_user_sessions` when an account's
  credentials change (password reset, email change).

In the monolith the client is in-process (:mod:`pfa.wiring`).  Once the
authorization server runs separately, configure an HTTP implementation instead
(introspection endpoint + a service-to-service revocation call); nothing else in
the application changes.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class AccessGrant:
    """What a valid access token grants, as reported by the authorization server."""

    user_id: uuid.UUID
    session_id: uuid.UUID
    client_id: str
    scope: frozenset[str]
    jti: str


class AuthorizationServerClient(Protocol):
    async def introspect(self, access_token: str) -> AccessGrant:
        """Return the grant of an active token.

        Raises :class:`~pfa.errors.Unauthenticated` when the token is invalid,
        expired, or its session was revoked.
        """
        ...

    async def revoke_user_sessions(self, user_id: uuid.UUID, *, reason: str) -> None:
        """Revoke every login session (refresh-token family) of an account."""
        ...


class AuthorizationServerNotConfigured(RuntimeError):
    pass


_client: AuthorizationServerClient | None = None


def configure(client: AuthorizationServerClient) -> None:
    global _client
    _client = client


def authorization_server() -> AuthorizationServerClient:
    """The configured client.  Raises rather than skipping revocation or token checks."""
    if _client is None:
        raise AuthorizationServerNotConfigured(
            "No authorization server client configured (see pfa.wiring)"
        )
    return _client
