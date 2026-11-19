"""The authorization server as a standalone ASGI service.

Run it with::

    uvicorn pfa.oauth.app:create_authorization_server_app --factory --port 8001

It serves the public OAuth surface (``/oauth/authorize``, the login POST,
``/oauth/token``, ``/oauth/revoke``, JWKS, ``/api/v1/sessions/logout``) plus the
internal endpoints the application calls (``/oauth/introspect``, session
revocation).  Credentials are verified by the application over HTTP
(``PFA_IDENTITY_PROVIDER_URL``); security events go to the ``pfa.oauth.audit``
logger.  It uses its own database only (``PFA_OAUTH_DATABASE_URL``), never the
application's.  The application must then run with ``PFA_AUTHORIZATION_SERVER_URL``
pointing here, and both sides share ``PFA_INTERNAL_SERVICE_TOKEN``.

Like the rest of :mod:`pfa.oauth`, this module imports shared infrastructure only.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from pfa import __version__
from pfa.config import Settings, get_settings
from pfa.http.problems import install_exception_handlers
from pfa.http.security_headers import SecurityHeadersMiddleware
from pfa.oauth import ports
from pfa.oauth.audit_log import LoggingAuditSink
from pfa.oauth.db import dispose_oauth_database, get_oauth_database
from pfa.oauth.identity_http import HttpIdentityProvider
from pfa.oauth.keys import ensure_keys, get_keys
from pfa.oauth.routes import internal_router, router
from pfa.observability.access_log import RequestLoggingMiddleware
from pfa.observability.logs import configure_logging
from pfa.observability.telemetry import configure_telemetry, instrument_app


def create_authorization_server_app(
    settings: Settings | None = None,
    *,
    identity_provider: ports.IdentityProvider | None = None,
    audit_sink: ports.AuditSink | None = None,
) -> FastAPI:
    """Build the standalone server.

    ``identity_provider`` / ``audit_sink`` default to the HTTP identity provider and
    the log sink built from ``settings``; passing them is for tests and embedding.
    """
    settings = settings or get_settings()
    configure_telemetry("pfa-oauth", settings)
    configure_logging("pfa-oauth", settings)
    settings.require_internal_service_token()
    ports.configure(
        identity_provider=identity_provider or HttpIdentityProvider.from_settings(settings),
        audit_sink=audit_sink or LoggingAuditSink(),
    )

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        ensure_keys(settings)
        get_oauth_database()
        yield
        await dispose_oauth_database()

    app = FastAPI(
        title="PFA Authorization Server",
        version=__version__,
        lifespan=lifespan,
        # the public contract of this service is OAuth 2.0 itself, not an OpenAPI document
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins(),
        allow_credentials=False,  # no credentialed CORS, ever
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Correlation-Id"],
        expose_headers=["X-Correlation-Id"],
        max_age=600,
    )
    app.add_middleware(
        SecurityHeadersMiddleware,
        docs_available=False,
        auth_form_targets=SecurityHeadersMiddleware.auth_form_targets(settings),
    )
    app.add_middleware(RequestLoggingMiddleware)
    instrument_app(app)  # server span: outermost
    install_exception_handlers(app)
    app.include_router(router)
    app.include_router(internal_router)

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok", "service": "authorization-server", "version": __version__}

    @app.get("/readyz")
    async def readyz() -> dict:
        async with get_oauth_database().session() as session:
            await session.execute(text("SELECT 1"))
        return {
            "status": "ready",
            "service": "authorization-server",
            "environment": settings.env,
            "database": "reachable",
            "keys": get_keys().active_kid,
        }

    return app
