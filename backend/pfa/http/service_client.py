"""Outbound HTTP to another internal service (shared by both sides of the split)."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from opentelemetry import propagate

from pfa.observability import context


@dataclass(frozen=True, slots=True)
class ServiceEndpoint:
    """Where another service lives and how to authenticate to it.

    ``transport`` exists for in-process testing (``httpx.ASGITransport``); in a
    deployment it is ``None`` and real HTTP is used.
    """

    base_url: str
    token: str
    timeout_seconds: float = 5.0
    transport: httpx.AsyncBaseTransport | None = None

    def client(self) -> httpx.AsyncClient:
        """A client for one call, carrying the caller's correlation id.

        The W3C trace context (``traceparent``) of the active span is injected
        too, so the other service's spans and log lines join the same trace.  It
        is explicit rather than left to the httpx instrumentation, which only
        hooks httpx's built-in transports (on real HTTP it re-injects with its own
        client span, the more precise parent).
        """
        headers = {"Authorization": f"Bearer {self.token}"}
        correlation_id = context.correlation_id()
        if correlation_id:
            headers["X-Correlation-Id"] = correlation_id
        propagate.inject(headers)
        return httpx.AsyncClient(
            base_url=self.base_url.rstrip("/"),
            headers=headers,
            timeout=self.timeout_seconds,
            transport=self.transport,
        )
