"""Identity provider reached over HTTP: the standalone server's view of accounts.

Calls the application's internal credential-check endpoint::

    POST {PFA_IDENTITY_PROVIDER_URL}/internal/identity/authenticate
    Authorization: Bearer <PFA_INTERNAL_SERVICE_TOKEN>
    {"email", "password", "client_ip", "attempt_key"}

    200 {"subject_id": "<uuid>"}
    401/429 {"rejection": "<reason>", "message": "<text>"}

Anything else (timeout, 5xx, malformed body) is an
:class:`~pfa.errors.UpstreamUnavailable`: a login never succeeds by default, and
it is not counted as a failed attempt either.
"""

from __future__ import annotations

import uuid

import httpx

from pfa.config import Settings
from pfa.errors import UpstreamUnavailable
from pfa.http.service_client import ServiceEndpoint
from pfa.oauth.ports import LOGIN_REJECTION_REASONS, LoginRejected, Subject

AUTHENTICATE_PATH = "/internal/identity/authenticate"


class HttpIdentityProvider:
    def __init__(self, endpoint: ServiceEndpoint) -> None:
        self.endpoint = endpoint

    @classmethod
    def from_settings(cls, settings: Settings) -> HttpIdentityProvider:
        if not settings.identity_provider_url:
            raise RuntimeError(
                "PFA_IDENTITY_PROVIDER_URL must point at the application when the "
                "authorization server runs standalone."
            )
        return cls(
            ServiceEndpoint(
                base_url=settings.identity_provider_url,
                token=settings.require_internal_service_token(),
                timeout_seconds=settings.internal_http_timeout_seconds,
            )
        )

    async def authenticate(
        self,
        *,
        email: str,
        password: str,
        client_ip: str | None,
        attempt_key: str,
    ) -> Subject:
        try:
            async with self.endpoint.client() as client:
                response = await client.post(
                    AUTHENTICATE_PATH,
                    json={
                        "email": email,
                        "password": password,
                        "client_ip": client_ip,
                        "attempt_key": attempt_key,
                    },
                )
        except httpx.HTTPError as exc:
            raise UpstreamUnavailable("Serviciul de identitate nu răspunde.") from exc

        body = _json(response)
        if response.status_code == 200 and "subject_id" in body:
            try:
                return Subject(id=uuid.UUID(str(body["subject_id"])))
            except ValueError as exc:
                raise UpstreamUnavailable("Răspuns invalid de la serviciul de identitate.") from exc
        if response.status_code in {401, 429} and body.get("rejection") in LOGIN_REJECTION_REASONS:
            raise LoginRejected(
                str(body["rejection"]),
                str(body.get("message") or "Autentificare refuzată."),
                http_status=response.status_code,
            )
        raise UpstreamUnavailable(
            "Răspuns neașteptat de la serviciul de identitate.",
            details={"status": response.status_code},
        )


def _json(response: httpx.Response) -> dict:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}
