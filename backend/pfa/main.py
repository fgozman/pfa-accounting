"""FastAPI application factory: the composition root of the monolith.

Two independent parts are mounted on one ASGI app:

* the application (:mod:`pfa.api.routes`);
* the OAuth 2.0 authorization server (:mod:`pfa.oauth.routes`).

They only reach each other through interfaces, connected by :func:`pfa.wiring.wire`.
This module is, with :mod:`pfa.wiring`, the only place that knows both.

With ``PFA_AUTHORIZATION_SERVER_URL`` set, the authorization server runs as its
own service (:mod:`pfa.oauth.app`) and this app serves the application only: no
``/oauth/*`` routes and no signing keys here, plus the internal credential-check
endpoint the server calls.  The SPA is served statically elsewhere.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from pfa import __version__
from pfa.api.deps import reset_object_store
from pfa.api.docs import install_docs_routes
from pfa.api.openapi import install_openapi
from pfa.api.routes import APPLICATION_ROUTERS, DEVELOPMENT_ROUTERS, internal_identity
from pfa.config import Settings, get_settings
from pfa.db import dispose_database, get_database
from pfa.http.problems import install_exception_handlers
from pfa.http.security_headers import SecurityHeadersMiddleware
from pfa.oauth import routes as authorization_server
from pfa.oauth.db import dispose_oauth_database, get_oauth_database
from pfa.oauth.keys import ensure_keys, get_keys
from pfa.observability.access_log import RequestLoggingMiddleware
from pfa.observability.logs import configure_logging
from pfa.observability.telemetry import configure_telemetry, instrument_app
from pfa.wiring import wire


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_telemetry("pfa-api", settings)
    configure_logging("pfa-api", settings)
    wire(settings)
    #: the authorization server is hosted here unless it runs as its own service
    hosts_authorization_server = not settings.authorization_server_is_remote

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if hosts_authorization_server:
            ensure_keys(settings)
            get_oauth_database()
        reset_object_store()
        get_database()
        yield
        await dispose_database()
        if hosts_authorization_server:
            await dispose_oauth_database()

    docs_available = settings.docs_available
    app = FastAPI(
        title="PFA Accounting API",
        version=__version__,
        description=(
            "Multi-tenant Romanian PFA accounting, tax calculation and ANAF reporting.\n\n"
            "Business endpoints require a bearer access token; application authentication "
            "is cookie-free. Use **Authorize** to either paste a short-lived access token or "
            "run the real authorization-code + PKCE login, then call any endpoint from here.\n\n"
            "Values that are not available are visible rather than silent: an obligation "
            "reports which of detect/calculate/generate/submit is supported and why, and an "
            "unverified deadline reads `Termen de verificat` instead of an invented date."
        ),
        lifespan=lifespan,
        # Documentation is mounted explicitly by install_docs_routes (external
        # scripts only, vendored assets).  The OpenAPI document stays published in
        # every environment for client generation.
        docs_url=None,
        redoc_url=None,
        openapi_url="/openapi.json",
    )
    if docs_available:
        install_docs_routes(
            app,
            settings,
            title=app.title,
            version=__version__,
            summary=(
                "Contabilitate PFA, calcul fiscal și raportare ANAF. "
                "Valorile indisponibile sunt vizibile, nu ascunse."
            ),
        )
    install_openapi(
        app,
        settings,
        title=app.title,
        version=__version__,
        description=app.description,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins(),
        allow_credentials=False,  # no credentialed CORS, ever
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Correlation-Id"],
        expose_headers=["X-Correlation-Id", "X-Artifact-Sha256"],
        max_age=600,
    )
    app.add_middleware(
        SecurityHeadersMiddleware,
        docs_available=docs_available,
        auth_form_targets=SecurityHeadersMiddleware.auth_form_targets(settings),
    )
    # added last = outermost of ours: times the request and sets the correlation id
    app.add_middleware(RequestLoggingMiddleware)
    # server span around everything (including the access log, which carries its ids)
    instrument_app(app)

    install_exception_handlers(app)
    # the authorization server and the application share no handler code
    if hosts_authorization_server:
        app.include_router(authorization_server.router)
    else:
        # the standalone server verifies credentials here (service token required)
        app.include_router(internal_identity.router)
    for router in (*APPLICATION_ROUTERS, *DEVELOPMENT_ROUTERS):
        app.include_router(router)

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict:
        return {"status": "ok", "version": __version__}

    @app.get("/readyz", tags=["ops"])
    async def readyz() -> dict:
        from sqlalchemy import text

        async with get_database().session() as session:
            await session.execute(text("SELECT 1"))
        if hosts_authorization_server:
            async with get_oauth_database().session() as session:
                await session.execute(text("SELECT 1"))
        return {
            "status": "ready",
            "environment": settings.env,
            "database": "reachable",
            # in split mode the keys live with the authorization server
            "keys": get_keys().active_kid if hosts_authorization_server else None,
            "authorization_server": ("in-process" if hosts_authorization_server else "remote"),
        }

    return app


app = create_app()
