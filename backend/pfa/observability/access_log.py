"""Structured access log and request context (pure ASGI middleware).

One ``http.request`` event per request, replacing uvicorn's access line:

* OpenTelemetry HTTP semantic conventions: ``http.route`` is the matched template
  (``/api/v1/workspaces/{workspace_id}/dashboard``), so dashboards aggregate per
  endpoint instead of per id; ``url.path`` is the concrete path.  The query string
  is **never** logged (it can carry OAuth codes, states, reset tokens);
* ``http.request.method``, ``http.response.status_code``,
  ``http.server.request.duration`` (seconds), ``user.id`` (when the request was
  authenticated), ``client.address``, ``user_agent.original``,
  ``pfa.correlation_id``, and the ``trace_id``/``span_id`` of the server span;
* level: ``error`` for 5xx, ``warning`` for 401/403/429 (security-relevant
  refusals), ``info`` otherwise; health probes and CORS preflights at ``debug``.

It also establishes the request context: a correlation id (a well-formed incoming
``X-Correlation-Id`` is kept so a request can be followed across services, any
other value is replaced), echoed in the response header, stored in
``request.state.correlation_id`` and bound to every log record and audit row
written while the request runs.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from pfa.observability import context
from pfa.observability.logs import log_event

logger = logging.getLogger("pfa.http.access")

#: liveness/readiness probes: frequent and uninteresting unless they fail
QUIET_PATHS = frozenset({"/healthz", "/readyz"})


def _header(scope: dict, name: bytes) -> str | None:
    for key, value in scope.get("headers") or ():
        if key == name:
            return value.decode("latin-1")
    return None


def _level(status: int, method: str, path: str) -> int:
    if status >= 500:
        return logging.ERROR
    if status in {401, 403, 429}:
        return logging.WARNING
    if path in QUIET_PATHS or method == "OPTIONS":
        return logging.DEBUG
    return logging.INFO


class RequestLoggingMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        correlation_id = context.accept_correlation_id(_header(scope, b"x-correlation-id"))
        state = scope.setdefault("state", {})
        state["correlation_id"] = correlation_id
        client = scope.get("client")
        client_ip = client[0] if client else None
        user_agent = _header(scope, b"user-agent")
        bound = context.bind(
            correlation_id=correlation_id, client_ip=client_ip, user_agent=user_agent
        )

        status = 500
        started = time.perf_counter()

        async def send_with_context(message: dict) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
                headers = list(message.get("headers") or [])
                if not any(key.lower() == b"x-correlation-id" for key, _ in headers):
                    headers.append((b"x-correlation-id", correlation_id.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_context)
        finally:
            duration = time.perf_counter() - started
            method = scope.get("method", "")
            path = scope.get("path", "")
            route = getattr(scope.get("route"), "path", None) or "unmatched"
            # set by whichever authentication ran (application or OAuth server)
            user_id = state.get("user_id")
            log_event(
                logger,
                "http.request",
                level=_level(status, method, path),
                message=f"{method} {route} {status} ({duration * 1000:.1f} ms)",
                attributes={
                    "http.request.method": method,
                    "http.route": route,
                    "url.path": path,
                    "http.response.status_code": status,
                    "http.server.request.duration": round(duration, 6),
                    "user.id": str(user_id) if user_id else None,
                    "user_agent.original": user_agent[:300] if user_agent else None,
                    "network.protocol.version": scope.get("http_version"),
                },
            )
            context.reset(bound)
