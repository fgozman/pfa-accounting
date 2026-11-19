"""Service-to-service authentication for internal endpoints.

Internal endpoints (token introspection, session revocation, credential checks)
are called by the other service only, never by a browser.  They require
``Authorization: Bearer <PFA_INTERNAL_SERVICE_TOKEN>`` and answer 404 while no
token is configured, so a deployment that does not split the services exposes
nothing.  Network isolation (private network, mTLS) is expected in addition.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Header

from pfa.config import get_settings
from pfa.errors import NotFound, Unauthenticated
from pfa.security.tokens import constant_time_equals


def require_service_token(
    authorization: Annotated[str | None, Header(include_in_schema=False)] = None,
) -> None:
    expected = get_settings().internal_service_token
    if not expected:
        raise NotFound("Resursa nu există.")
    scheme, _, presented = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not presented or not constant_time_equals(presented, expected):
        raise Unauthenticated("Autentificarea de serviciu nu este validă.")
