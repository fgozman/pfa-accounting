"""Interactive API documentation: reachable, usable, and absent in production.

Requirement IDs: API_DATA (published OpenAPI contract), UI (developer access to
the contract), OPERATIONS (environment gating).

The regression these tests exist for: FastAPI's built-in Swagger page initialises
Swagger UI with an *inline* ``<script>``.  With a policy that does not allow
``'unsafe-inline'`` the bundle loads but Swagger never boots, so ``/docs`` renders
blank while still answering ``200``.  Asserting the status code is therefore not
enough — the page's script references, the assets and (in the browser suite) the
rendered DOM are all checked.
"""

from __future__ import annotations

from httpx import AsyncClient

from pfa.config import reload_settings
from pfa.oauth import flows as oauth


async def test_docs_page_uses_external_scripts_only_and_no_third_party_origin(
    client: AsyncClient,
) -> None:
    response = await client.get("/docs")
    assert response.status_code == 200

    body = response.text
    # every script/style is a separate same-origin resource
    assert "<script src=\"/static/docs/swagger-ui-bundle.js\"></script>" in body
    assert "<script src=\"/docs/swagger-init.js\"></script>" in body
    assert "<script>" not in body, "an inline script would require 'unsafe-inline'"
    assert "cdn.jsdelivr.net" not in body
    assert "onload=" not in body and "onclick=" not in body

    csp = response.headers["content-security-policy"]
    assert "script-src 'self'" in csp
    assert "unsafe-inline" not in csp.split("style-src")[0]  # never for scripts
    assert "http://" not in csp.split("connect-src")[0].replace("'self'", "")
    # application paths keep exactly the same policy
    assert (await client.get("/healthz")).headers["content-security-policy"] == csp


async def test_docs_assets_are_served_from_this_origin(client: AsyncClient) -> None:
    for path, content_type in (
        ("/static/docs/swagger-ui.css", "text/css"),
        ("/static/docs/swagger-ui-bundle.js", "javascript"),
        ("/docs/swagger-init.js", "javascript"),
        ("/docs/oauth2-redirect.js", "javascript"),
        ("/static/docs/favicon-32x32.png", "image/png"),
    ):
        response = await client.get(path)
        assert response.status_code == 200, path
        assert content_type in response.headers["content-type"], path
        assert len(response.content) > 0

    # the license of the vendored assets travels with them
    assert (await client.get("/static/docs/LICENSE")).status_code == 200
    assert (await client.get("/static/docs/VENDORED.txt")).status_code == 200

    init = (await client.get("/docs/swagger-init.js")).text
    settings = reload_settings()
    # the popup redirect URI must be the registered one, byte for byte
    assert settings.docs_redirect_uri in init
    assert settings.docs_client_id in init
    assert "usePkceWithAuthorizationCodeGrant: true" in init
    # tokens are never persisted in the browser
    assert "persistAuthorization: false" in init


async def test_oauth2_redirect_page_completes_the_popup(client: AsyncClient) -> None:
    page = await client.get("/docs/oauth2-redirect")
    assert page.status_code == 200
    assert "<script>" not in page.text  # no inline script
    assert "/docs/oauth2-redirect.js" in page.text
    script = (await client.get("/docs/oauth2-redirect.js")).text
    assert "window.opener.postMessage" in script
    assert "window.location.origin" in script  # never a wildcard target


async def test_openapi_document_advertises_both_authentication_schemes(
    client: AsyncClient,
) -> None:
    """Without a security scheme the "Authorize" button never appears."""
    document = (await client.get("/openapi.json")).json()
    schemes = document["components"]["securitySchemes"]
    assert "HTTPBearer" in schemes
    assert schemes["HTTPBearer"]["scheme"] == "bearer"
    assert schemes["OAuth2"]["type"] == "oauth2"

    flow = schemes["OAuth2"]["flows"]["authorizationCode"]
    settings = reload_settings()
    assert flow["authorizationUrl"] == f"{settings.authorization_origin}/oauth/authorize"
    assert flow["tokenUrl"] == f"{settings.authorization_origin}/oauth/token"
    assert "pfa.write" in flow["scopes"]

    # protected operations offer both alternatives; public ones offer neither
    protected = document["paths"]["/api/v1/me"]["get"]["security"]
    assert {"HTTPBearer": []} in protected
    assert any("OAuth2" in item for item in protected)
    for path in ("/auth/register", "/oauth/token", "/.well-known/jwks.json"):
        for operation in document["paths"][path].values():
            assert not operation.get("security"), path

    # the documentation plumbing stays out of the published contract
    for path in document["paths"]:
        assert not path.startswith(("/docs", "/static/docs")), path


async def test_swagger_authorize_uses_the_real_cookie_free_login(client: AsyncClient) -> None:
    """The documented OAuth client completes the same flow as the SPA."""
    settings = reload_settings()
    from pfa.oauth.db import get_oauth_database

    async with get_oauth_database().session() as session:
        await oauth.register_client(
            session,
            client_id=settings.docs_client_id,
            environment=settings.env,
            redirect_uris=[settings.docs_redirect_uri],
            allowed_origins=[settings.authorization_origin],
            allowed_scopes=list(oauth.SUPPORTED_SCOPES),
            settings=settings,
        )

    from pfa.security.tokens import new_pkce_verifier, new_state, pkce_challenge_s256

    verifier = new_pkce_verifier()
    state = new_state()
    response = await client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": settings.docs_client_id,
            "redirect_uri": settings.docs_redirect_uri,
            "scope": "pfa.read",
            "state": state,
            "code_challenge": pkce_challenge_s256(verifier),
            "code_challenge_method": "S256",
        },
    )
    assert response.status_code == 200, response.text
    assert "transaction_proof" in response.text
    assert "set-cookie" not in {key.lower() for key in response.headers}


async def test_documentation_is_not_mounted_in_production() -> None:
    settings = reload_settings().model_copy(update={"env": "production"})

    from pfa.main import create_app

    app = create_app(settings)
    assert app.docs_url is None
    assert app.redoc_url is None
    # the contract itself stays published for client generation
    assert app.openapi_url == "/openapi.json"
    mounted = {getattr(route, "path", "") for route in app.routes}
    assert "/docs" not in mounted
    assert "/docs/oauth2-redirect" not in mounted
    assert "/static/docs" not in mounted
    assert "/api/v1/me" in app.openapi()["paths"]
