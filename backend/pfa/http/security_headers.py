"""Security headers and Content-Security-Policy for every service.

One strict policy for application and documentation pages; the authorization
server's own pages get a ``form-action`` allowance for the SPA callback origin.
Shared by the monolith and the standalone authorization server.
"""

from __future__ import annotations

import uuid
from urllib.parse import urlsplit

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from pfa.config import Settings

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'none'; form-action 'self'"
)

#: Swagger UI assets are vendored and served from this origin, and the
#: documentation page uses external scripts only, so one strict policy covers the
#: application and the documentation alike: no CDN origin, no 'unsafe-inline'
#: script.  ``static_path`` must match ``pfa.api.docs.MOUNT_PATH``.
CSP_DOCS = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'none'; form-action 'self'"
)
DOCS_PATHS = ("/docs", "/redoc", "/static/docs")


def csp_for_auth_pages(spa_origins: set[str]) -> str:
    """Content-Security-Policy for the login page served by the authorization service.

    ``form-action`` is also evaluated against the *redirect target* of a form
    submission.  The login form legitimately ends at the SPA callback on a
    different origin, so that origin must be listed; otherwise Chromium blocks the
    whole submission and the visitor stays on the login page.  The allowance is
    scoped to the authorization pages only — application pages keep
    ``form-action 'self'``.
    """
    targets = " ".join(sorted(spa_origins))
    return (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; "
        f"frame-ancestors 'none'; form-action 'self' {targets}"
    )


AUTH_PATHS = (
    "/oauth/authorize",
    "/oauth/token",
    "/oauth/revoke",
    "/auth/",
    "/.well-known/jwks.json",
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        *,
        docs_available: bool = False,
        auth_form_targets: set[str] | None = None,
    ) -> None:
        super().__init__(app)
        self.docs_available = docs_available
        self.auth_csp = csp_for_auth_pages(auth_form_targets or set())

    async def dispatch(self, request: Request, call_next) -> Response:
        # set (and sanitised) by pfa.observability.access_log, which runs outside
        # this middleware; never trust the raw header here
        correlation_id = getattr(request.state, "correlation_id", None) or uuid.uuid4().hex
        request.state.correlation_id = correlation_id
        response = await call_next(request)
        response.headers.setdefault("X-Correlation-Id", correlation_id)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        path = request.url.path
        if self.docs_available and path.startswith(DOCS_PATHS):
            # documentation assets are same-origin and script-src 'self' applies
            response.headers.setdefault("Content-Security-Policy", CSP_DOCS)
        elif path.startswith(AUTH_PATHS):
            # the login form redirects to the SPA callback on another origin
            response.headers.setdefault("Content-Security-Policy", self.auth_csp)
        else:
            response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), camera=(), microphone=()"
        )
        if path.startswith(AUTH_PATHS):
            # no-referrer and no caching on every authentication page/response
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["Cache-Control"] = "no-store"
        return response

    @staticmethod
    def auth_form_targets(settings: Settings) -> set[str]:
        """Origins the authorization server's own pages may submit a form to.

        These are the origins of this deployment's registered redirect targets:
        the SPA origin and the exact callback URI, which are configuration values
        (never request data).
        """
        origins: set[str] = set()
        for candidate in (settings.spa_origin, settings.spa_callback_uri):
            parts = urlsplit(candidate)
            if parts.scheme and parts.netloc:
                origins.add(f"{parts.scheme}://{parts.netloc}")
        return origins
