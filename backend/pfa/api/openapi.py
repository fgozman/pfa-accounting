"""OpenAPI document customisation for interactive documentation.

Adds the OAuth 2.0 authorization-code + PKCE flow that the API actually uses as an
*alternative* to pasting a bearer token, on every operation that requires one.
The flow points at the real authorization and token endpoints, so "Authorize"
performs the same cookie-free login as the SPA.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from pfa.config import Settings
from pfa.security.scopes import SUPPORTED_SCOPES

SCOPE_DESCRIPTIONS = {
    "openid": "Identificatorul contului (rezervat; OIDC nu este implementat).",
    "profile": "Profilul de bază al contului.",
    "pfa.read": "Citirea spațiilor de lucru autorizate și a documentelor partajate.",
    "pfa.write": "Adăugarea și corectarea documentelor, încasărilor, plăților și declarațiilor.",
    "pfa.manage": "Administrarea spațiului de lucru: invitații, roluri, conexiunea ANAF.",
}


def install_openapi(app: FastAPI, settings: Settings, *, title: str, version: str, description: str) -> None:
    """Install a custom ``openapi()`` on the application instance."""

    def custom_openapi() -> dict:
        if app.openapi_schema:
            return app.openapi_schema

        schema = get_openapi(
            title=title,
            version=version,
            description=description,
            routes=app.routes,
        )
        components = schema.setdefault("components", {})
        schemes = components.setdefault("securitySchemes", {})

        # FastAPI registers HTTPBearer automatically because it is used as a
        # dependency; describe it in the same, explicit way as the OAuth flow.
        schemes["OAuth2"] = {
            "type": "oauth2",
            "description": (
                "Autentificarea aplicației este separat de autorizarea ANAF. Fluxul este "
                "authorization code cu PKCE (S256), fără cookie-uri și fără secret de client: "
                "„Authorize” deschide pagina serviciului de autorizare, iar tokenul returnat "
                "este valabil 5 minute."
            ),
            "flows": {
                "authorizationCode": {
                    "authorizationUrl": f"{settings.authorization_origin}/oauth/authorize",
                    "tokenUrl": f"{settings.authorization_origin}/oauth/token",
                    "refreshUrl": f"{settings.authorization_origin}/oauth/token",
                    "scopes": {
                        scope: SCOPE_DESCRIPTIONS.get(scope, scope) for scope in SUPPORTED_SCOPES
                    },
                }
            },
        }

        # Every operation that requires a bearer token also accepts the OAuth2
        # flow (OpenAPI lists alternatives as separate objects in `security`).
        for path_item in schema.get("paths", {}).values():
            for operation in path_item.values():
                if not isinstance(operation, dict):
                    continue
                requirements = operation.get("security")
                if not requirements:
                    continue
                if any("HTTPBearer" in item for item in requirements):
                    if not any("OAuth2" in item for item in requirements):
                        requirements.append(
                            {"OAuth2": ["pfa.read", "pfa.write", "pfa.manage"]}
                        )

        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]
