"""Self-hosted interactive API documentation.

FastAPI's built-in Swagger page initialises Swagger UI with an **inline**
``<script>``.  A Content-Security-Policy without ``'unsafe-inline'`` blocks that
script, so the page renders blank — the bundle loads and nothing boots.

This module serves documentation that is
* **CSP-clean**: external scripts only, so ``script-src 'self'`` applies with no
  ``'unsafe-inline'`` and no CDN exception;
* **offline**: the Swagger UI assets are vendored from the pinned
  ``swagger-ui-dist`` package and served from this origin;
* **usable**: the "Authorize" button runs the real authorization-code + PKCE
  login, and the page documents that unavailable capabilities are shown rather
  than silently missing.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from pfa.config import Settings
from pfa.security.scopes import SUPPORTED_SCOPES

STATIC_DIR = Path(__file__).resolve().parent.parent / "static" / "swagger-ui"
#: vendored assets (css/js/favicon) are served from here by a StaticFiles mount
MOUNT_PATH = "/static/docs"
#: generated helper scripts live outside the mount path so the two cannot shadow
#: each other, and are ordinary routes
SCRIPT_PATH = "/docs"
FAVICON = "/static/docs/favicon-32x32.png"

DOCS_PAGE = """<!DOCTYPE html>
<html lang="ro">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <title>{title} — documentație API</title>
  <link rel="icon" type="image/png" sizes="32x32" href="{favicon}">
  <link rel="stylesheet" href="{static_path}/swagger-ui.css">
  <style>
    body {{ margin: 0; background: #fafafa; }}
    .docs-header {{
      font-family: system-ui, sans-serif; padding: 0.75rem 1rem;
      border-bottom: 1px solid #d5dbe2; background: #fff;
    }}
    .docs-header h1 {{ font-size: 1.05rem; margin: 0 0 0.25rem; }}
    .docs-header p {{ margin: 0; font-size: 0.9rem; color: #5d6b7a; }}
    .docs-header a {{ color: #1b4f8f; }}
    #swagger-ui .topbar {{ display: none; }}
  </style>
</head>
<body>
  <header class="docs-header">
    <h1>{title} <span>v{version}</span></h1>
    <p>
      {summary}
      <br>
      Documentul OpenAPI: <a href="/openapi.json">/openapi.json</a> ·
      Setări de autentificare: butonul <strong>Authorize</strong> acceptă fie un token
      de acces de scurtă durată, fie autentificarea reală (authorization code + PKCE).
    </p>
  </header>
  <div id="swagger-ui"></div>
  <script src="{static_path}/swagger-ui-bundle.js"></script>
  <script src="{script_path}/swagger-init.js"></script>
</body>
</html>
"""

#: Loaded as an external file so the page needs no inline script.  Kept in sync
#: with ``OPENAPI_OAUTH`` below: the OAuth2 client id and the redirect URI of the
#: Swagger UI popup must be registered byte-for-byte.
SWAGGER_INIT_JS = """/* Initialise Swagger UI. External file on purpose: no inline script, so the
   Content-Security-Policy can stay at script-src 'self'. */
window.addEventListener("load", function () {
  var config = window.__PFA_DOCS_CONFIG__;
  var ui = SwaggerUIBundle({
    url: "/openapi.json",
    dom_id: "#swagger-ui",
    deepLinking: true,
    docExpansion: "list",
    filter: true,
    displayRequestDuration: true,
    tryItOutEnabled: true,
    // Tokens live in memory only, exactly like the SPA: nothing is written to
    // localStorage.
    persistAuthorization: false,
    presets: [SwaggerUIBundle.presets.apis],
    plugins: [SwaggerUIBundle.plugins.DownloadUrl],
    requestInterceptor: function (request) {
      // The bearer token is injected by the authorisation dialog; never persist
      // or log it.
      return request;
    },
    onComplete: function () {
      document.title = config.title + " — documentație API";
    },
  });
  window.ui = ui;
  ui.initOAuth({
    clientId: config.clientId,
    appName: config.appName,
    usePkceWithAuthorizationCodeGrant: true,
    scopeSeparator: " ",
    scopes: config.scopes,
    oauth2RedirectUrl: config.oauth2RedirectUrl,
  });
});
"""

OAUTH2_REDIRECT_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Autentificare finalizată</title>
</head>
<body>
  <p>Autentificare finalizată. Această fereastră se închide automat.</p>
  <script src="{script_path}/oauth2-redirect.js"></script>
</body>
</html>
"""

OAUTH2_REDIRECT_JS = """/* Completes the Swagger UI OAuth2 popup without an inline script, so the same
   strict Content-Security-Policy applies. */
(function () {
  "use strict";
  if (window.opener && window.opener !== window) {
    var params = new URLSearchParams(window.location.search);
    var payload = { type: "oauth2-redirect", hash: window.location.hash };
    if (params.has("code") || params.has("error")) {
      payload.query = Object.fromEntries(params.entries());
      payload.state = params.get("state");
      payload.code = params.get("code");
      payload.error = params.get("error");
    }
    window.opener.postMessage(payload, window.location.origin);
  }
  window.setTimeout(function () {
    window.close();
  }, 150);
})();
"""


def docs_html(settings: Settings, *, title: str, version: str, summary: str) -> str:
    return DOCS_PAGE.format(
        title=title,
        version=version,
        summary=summary,
        static_path=MOUNT_PATH,
        script_path=SCRIPT_PATH,
        favicon=FAVICON,
    )


def oauth2_redirect_html() -> str:
    return OAUTH2_REDIRECT_PAGE.format(script_path=SCRIPT_PATH)


def docs_config_script(settings: Settings, *, app_name: str) -> str:
    """The per-deployment documentation configuration, as an external script."""
    import json

    config = {
        "title": "PFA Accounting API",
        "appName": app_name,
        "clientId": settings.docs_client_id,
        "oauth2RedirectUrl": settings.docs_redirect_uri,
        "scopes": " ".join(SUPPORTED_SCOPES),
    }
    return "window.__PFA_DOCS_CONFIG__ = " + json.dumps(config) + ";\n"


def install_docs_routes(
    app: FastAPI,
    settings: Settings,
    *,
    title: str,
    version: str,
    summary: str,
    app_name: str = "Documentație API PFA",
) -> None:
    """Mount the vendored assets and the documentation pages (non-production only)."""
    app.mount(MOUNT_PATH, StaticFiles(directory=STATIC_DIR), name="swagger-ui")
    app.state.docs_static_path = MOUNT_PATH

    @app.get("/docs", include_in_schema=False)
    async def swagger_ui() -> HTMLResponse:
        return HTMLResponse(
            docs_html(settings, title=title, version=version, summary=summary),
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/docs/oauth2-redirect", include_in_schema=False)
    async def oauth2_redirect() -> HTMLResponse:
        # Registered as the redirect URI of the documentation OAuth client; the
        # popup posts the authorization response back to the opener.
        return HTMLResponse(oauth2_redirect_html(), headers={"Cache-Control": "no-store"})

    @app.get(f"{SCRIPT_PATH}/swagger-init.js", include_in_schema=False)
    async def swagger_init() -> Response:
        return Response(
            content=docs_config_script(settings, app_name=app_name) + SWAGGER_INIT_JS,
            media_type="application/javascript",
            headers={"Cache-Control": "no-store"},
        )

    @app.get(f"{SCRIPT_PATH}/oauth2-redirect.js", include_in_schema=False)
    async def oauth2_redirect_js() -> Response:
        return Response(
            content=OAUTH2_REDIRECT_JS,
            media_type="application/javascript",
            headers={"Cache-Control": "no-store"},
        )
