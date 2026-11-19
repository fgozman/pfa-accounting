"""The authorization server reached over HTTP (when it runs as its own service).

* :meth:`introspect` — RFC 7662 ``POST /oauth/introspect`` on every API request;
* :meth:`revoke_user_sessions` — ``POST /oauth/subjects/{id}/sessions/revoke`` when
  an account's credentials change.

Both authenticate with ``PFA_INTERNAL_SERVICE_TOKEN``.  Failures are never treated
as success: an unreachable server denies access (503) and makes a password reset
or email change fail, so no credential change can leave live sessions behind.
Introspection answers carry no reason for an inactive token (RFC 7662), so the
caller sees one generic 401 message.
"""

from __future__ import annotations

import uuid

import httpx

from pfa.clients.authorization_server import AccessGrant
from pfa.config import Settings
from pfa.errors import Unauthenticated, UpstreamUnavailable
from pfa.http.service_client import ServiceEndpoint

INTROSPECT_PATH = "/oauth/introspect"


def revoke_sessions_path(user_id: uuid.UUID) -> str:
    return f"/oauth/subjects/{user_id}/sessions/revoke"


class HttpAuthorizationServer:
    def __init__(self, endpoint: ServiceEndpoint) -> None:
        self.endpoint = endpoint

    @classmethod
    def from_settings(cls, settings: Settings) -> HttpAuthorizationServer:
        return cls(
            ServiceEndpoint(
                base_url=settings.authorization_server_url,
                token=settings.require_internal_service_token(),
                timeout_seconds=settings.internal_http_timeout_seconds,
            )
        )

    async def introspect(self, access_token: str) -> AccessGrant:
        response = await self._post(
            INTROSPECT_PATH,
            data={"token": access_token, "token_type_hint": "access_token"},
            failure="Serviciul de autorizare nu răspunde.",
        )
        body = _json(response)
        if response.status_code != 200 or "active" not in body:
            raise UpstreamUnavailable(
                "Răspuns neașteptat de la serviciul de autorizare.",
                details={"status": response.status_code},
            )
        if body["active"] is not True:
            raise Unauthenticated(
                "Token de acces invalid, expirat sau revocat. Autentificați-vă din nou."
            )
        try:
            return AccessGrant(
                user_id=uuid.UUID(str(body["sub"])),
                session_id=uuid.UUID(str(body["sid"])),
                client_id=str(body.get("client_id", "")),
                scope=frozenset(str(body.get("scope", "")).split()),
                jti=str(body.get("jti", "")),
            )
        except (KeyError, ValueError) as exc:
            raise UpstreamUnavailable("Răspuns invalid de la serviciul de autorizare.") from exc

    async def revoke_user_sessions(self, user_id: uuid.UUID, *, reason: str) -> None:
        response = await self._post(
            revoke_sessions_path(user_id),
            json={"reason": reason},
            failure="Sesiunile nu au putut fi revocate: serviciul de autorizare nu răspunde.",
        )
        if response.status_code != 200:
            raise UpstreamUnavailable(
                "Sesiunile nu au putut fi revocate.",
                details={"status": response.status_code},
            )

    async def _post(self, path: str, *, failure: str, **kwargs) -> httpx.Response:
        try:
            async with self.endpoint.client() as client:
                return await client.post(path, **kwargs)
        except httpx.HTTPError as exc:
            raise UpstreamUnavailable(failure) from exc


def _json(response: httpx.Response) -> dict:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}
