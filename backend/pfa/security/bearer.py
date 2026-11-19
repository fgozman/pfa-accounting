"""The ``Authorization: Bearer`` HTTP security scheme, shared by every service.

Declared as a real security scheme so the OpenAPI document advertises it and
interactive tooling can send a token.  ``auto_error=False`` keeps our own
problem-details 401 instead of FastAPI's default 403.  Validation is not done
here: the authorization server introspects its own tokens, and the application
asks it through :mod:`pfa.clients.authorization_server`.
"""

from __future__ import annotations

from fastapi.security import HTTPBearer

bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="HTTPBearer",
    description=(
        "JWT de acces emis de serviciul de autorizare (aud=pfa-api), valabil 5 minute. "
        "Se trimite ca `Authorization: Bearer <token>`."
    ),
)

__all__ = ["bearer_scheme"]
