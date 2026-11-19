"""OpenTelemetry: traces, metrics and the trace context of log records.

Configuration follows the **standard** ``OTEL_*`` environment variables, so the
deployment controls everything without application settings:

* ``OTEL_EXPORTER_OTLP_ENDPOINT`` (e.g. ``http://alloy:4318``): where traces and
  metrics are pushed (OTLP/HTTP).  Unset: nothing is exported, but trace context
  is still created, propagated (W3C ``traceparent``) and written on every log line;
* ``OTEL_TRACES_SAMPLER`` / ``OTEL_TRACES_SAMPLER_ARG``: sampling (SDK default:
  ``parentbased_always_on``);
* ``OTEL_METRIC_EXPORT_INTERVAL``: metric push interval in ms (default 60000);
* ``OTEL_RESOURCE_ATTRIBUTES``: extra resource attributes (region, instance...);
* ``OTEL_SDK_DISABLED=true``: switches the SDK off entirely.

What is instrumented: inbound HTTP (FastAPI: one server span per request plus the
stable ``http.server.request.duration`` histogram), outbound HTTP (httpx: client
spans and ``traceparent`` injection between the application and the OAuth server),
SQL (SQLAlchemy: one span per statement, connection-pool metrics), and the worker's
jobs and outbox events.  Every structured log event is also counted
(``pfa.events``, by ``event.name`` and severity), which turns security and audit
events into alertable metrics.

Shared infrastructure: depends on neither the application nor the OAuth server.
"""

from __future__ import annotations

import atexit
import os
import threading
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from pfa import __version__
from pfa.config import Settings, get_settings

#: health probes produce no traces (they would drown real traffic)
EXCLUDED_URLS = "healthz,readyz"

_lock = threading.Lock()
_libraries_instrumented = False
_providers_configured = False

#: obtained through the API proxies, so they work before (and without) the SDK
tracer = trace.get_tracer("pfa")
meter = metrics.get_meter("pfa")

#: every structured log event, by ``event.name`` and ``severity_text``
events_counter = meter.create_counter(
    "pfa.events",
    unit="{event}",
    description="Structured log events emitted by the application, by event name and severity.",
)
#: background work, by ``pfa.job.kind`` (or outbox event type) and outcome
work_duration = meter.create_histogram(
    "pfa.worker.work.duration",
    unit="s",
    description="Duration of worker jobs and outbox deliveries.",
)


def _sdk_disabled() -> bool:
    return os.environ.get("OTEL_SDK_DISABLED", "").strip().lower() == "true"


def _exporting(signal: str) -> bool:
    """True when an OTLP endpoint is configured for ``signal`` (traces/metrics)."""
    if os.environ.get(f"OTEL_{signal.upper()}_EXPORTER", "otlp").strip().lower() == "none":
        return False
    return bool(
        os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        or os.environ.get(f"OTEL_EXPORTER_OTLP_{signal.upper()}_ENDPOINT")
    )


def instrument_libraries() -> None:
    """Patch SQLAlchemy and httpx once per process (idempotent).

    Called before any database engine or HTTP client exists (see ``pfa.db``), so
    every engine of both databases and every service-to-service call is covered.
    Instrumentations use the API's proxy providers: spans and metrics start flowing
    as soon as :func:`configure_telemetry` installs the SDK providers.
    """
    global _libraries_instrumented
    with _lock:
        if _libraries_instrumented:
            return
        # emit the stable semantic-convention names (http.*, db.*), not the legacy ones
        os.environ.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", "http,database")
        SQLAlchemyInstrumentor().instrument(enable_commenter=False)
        HTTPXClientInstrumentor().instrument()
        _libraries_instrumented = True


def configure_telemetry(service: str, settings: Settings | None = None) -> None:
    """Install the tracer and meter providers for this process (first call wins).

    A process runs one service; tests create many apps in one process, so later
    calls are no-ops.
    """
    global _providers_configured
    instrument_libraries()
    settings = settings or get_settings()
    with _lock:
        if _providers_configured or _sdk_disabled():
            return
        resource = Resource.create(
            {
                "service.name": os.environ.get("OTEL_SERVICE_NAME") or service,
                "service.version": __version__,
                "deployment.environment.name": settings.env,
            }
        )
        tracer_provider = TracerProvider(resource=resource)
        if _exporting("traces"):
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        trace.set_tracer_provider(tracer_provider)

        readers = []
        if _exporting("metrics"):
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
                OTLPMetricExporter,
            )

            readers.append(PeriodicExportingMetricReader(OTLPMetricExporter()))
        meter_provider = MeterProvider(resource=resource, metric_readers=readers)
        metrics.set_meter_provider(meter_provider)

        def _flush() -> None:  # deliver what is buffered on a clean exit
            tracer_provider.shutdown()
            meter_provider.shutdown()

        atexit.register(_flush)
        _providers_configured = True


def instrument_app(app: Any) -> None:
    """Server spans and HTTP metrics for one FastAPI app (outermost middleware)."""
    FastAPIInstrumentor.instrument_app(app, excluded_urls=EXCLUDED_URLS)


def trace_context() -> dict[str, str]:
    """``trace_id`` / ``span_id`` of the active span, for log correlation."""
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return {}
    return {
        "trace_id": format(context.trace_id, "032x"),
        "span_id": format(context.span_id, "016x"),
    }
