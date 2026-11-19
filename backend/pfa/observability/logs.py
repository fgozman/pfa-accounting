"""Structured logging: one record per line on stdout, ready for a log pipeline.

Production (``PFA_LOG_FORMAT=json``, the default outside development/test) emits
one JSON object per line, aligned with the OpenTelemetry log data model and
semantic conventions, so Grafana Alloy/Loki, the OpenTelemetry Collector, Fluent
Bit, Vector, Elastic or Datadog ingest it without custom parsing::

    {"timestamp":"2026-11-08T19:36:01.123Z","severity_text":"INFO","severity_number":9,
     "body":"GET /api/v1/me 200 (12.4 ms)","event.name":"http.request",
     "service.name":"pfa-api","service.version":"0.1.0",
     "deployment.environment.name":"production","scope.name":"pfa.http.access",
     "trace_id":"4bf92f3577b34da6a3ce929d0e0e4736","span_id":"00f067aa0ba902b7",
     "pfa.correlation_id":"9f1c...","user.id":"6b0e...","client.address":"203.0.113.7",
     "http.request.method":"GET","http.route":"/api/v1/me","url.path":"/api/v1/me",
     "http.response.status_code":200,"http.server.request.duration":0.0124}

Naming rule: OpenTelemetry semantic-convention names where one exists
(``service.*``, ``http.*``, ``url.*``, ``client.*``, ``user.id``, ``session.id``,
``exception.*``, ``event.name``...), application attributes under the ``pfa.``
namespace (``pfa.correlation_id``, ``pfa.audit.action``, ``pfa.job.kind``...).
The full contract is in ``docs/logging-inventory.md``.

Guarantees:

* **no secrets**: credential-named fields are replaced, bearer tokens, JWTs,
  secret query parameters and e-mail addresses are masked in every string;
* **no log injection**: JSON is ASCII-escaped; the text format escapes control
  characters and indents tracebacks, so no value can forge a record;
* **non-blocking**: records are formatted in the caller (where the request context
  and the active span are) and written by a background thread from a bounded
  queue, so a slow log collector never stalls the event loop.  If the queue is
  full, lines are dropped and a ``log.dropped`` event reports how many.
"""

from __future__ import annotations

import atexit
import ipaddress
import json
import logging
import logging.handlers
import queue
import re
import sys
from datetime import UTC, datetime
from typing import Any

from pfa import __version__
from pfa.config import Settings, get_settings
from pfa.observability import context
from pfa.observability.telemetry import events_counter, trace_context

REDACTED = "[REDACTED]"

#: field names (last dotted segment) whose value is a credential, whatever the event
SENSITIVE_KEYS = frozenset(
    {
        "password",
        "new_password",
        "current_password",
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "code",
        "code_verifier",
        "transaction_proof",
        "proof",
        "state",
        "secret",
        "client_secret",
        "authorization",
        "cookie",
        "set_cookie",
        "api_key",
    }
)
_SECRET_QUERY = re.compile(
    r"(?i)([?&](?:token|code|state|code_verifier|code_challenge|transaction_proof|proof|"
    r"password|access_token|refresh_token|client_secret)=)[^&\s\"'#]+"
)
_BEARER = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9\-._~+/]+=*")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]+")
#: personal data: keep the first character and the domain (``a***@example.ro``)
_EMAIL = re.compile(r"\b([A-Za-z0-9])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")

#: OpenTelemetry severity: (text, number)
_SEVERITY = {
    logging.DEBUG: ("DEBUG", 5),
    logging.INFO: ("INFO", 9),
    logging.WARNING: ("WARN", 13),
    logging.ERROR: ("ERROR", 17),
    logging.CRITICAL: ("FATAL", 21),
}

#: attributes every LogRecord has; anything else was passed through ``extra``
_RECORD_ATTRIBUTES = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
    "event",
    "fields",
    "allow_secrets",
    "context",
}

_DEVELOPMENT_ENVS = frozenset({"development", "test"})
_QUEUE_SIZE = 10_000


# ---------------------------------------------------------------------------
# Redaction and privacy
# ---------------------------------------------------------------------------


def redact_text(value: str) -> str:
    value = _SECRET_QUERY.sub(rf"\1{REDACTED}", value)
    value = _BEARER.sub(rf"\1 {REDACTED}", value)
    value = _JWT.sub(REDACTED, value)
    return _EMAIL.sub(r"\1***@\2", value)


def _sensitive(key: str) -> bool:
    return key.rsplit(".", 1)[-1].lower().replace("-", "_") in SENSITIVE_KEYS


def redact(value: Any, *, key: str | None = None) -> Any:
    """Mask credentials and e-mail addresses in a log payload, recursively."""
    if key is not None and _sensitive(key):
        return REDACTED if value not in (None, "") else value
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {k: redact(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact(item) for item in value]
    return value


def anonymize_ip(address: str) -> str:
    """Truncate an address for data minimisation: IPv4 /24, IPv6 /48."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return "unknown"
    prefix = 24 if ip.version == 4 else 48
    return str(ipaddress.ip_network(f"{ip}/{prefix}", strict=False).network_address)


# ---------------------------------------------------------------------------
# Emitting events
# ---------------------------------------------------------------------------


def log_event(
    logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    message: str | None = None,
    exc_info: Any = None,
    allow_secrets: bool = False,
    attributes: dict[str, Any] | None = None,
    **fields: Any,
) -> None:
    """Emit one structured event and count it (metric ``pfa.events``).

    ``event`` is a stable dotted name (``http.request``, ``audit``,
    ``job.failed``...) to filter and alert on.  ``attributes`` carries dotted
    attribute names (``{"http.route": ...}``); plain keyword ``fields`` are for
    simple names.  ``allow_secrets`` lifts redaction **in development/test only**
    (the dev mail line shows its link there on purpose); it is ignored elsewhere.
    """
    events_counter.add(
        1, {"event.name": event, "severity_text": _SEVERITY.get(level, ("INFO", 9))[0]}
    )
    logger.log(
        level,
        message or event,
        exc_info=exc_info,
        extra={
            "event": event,
            "fields": {**fields, **(attributes or {})},
            "allow_secrets": allow_secrets,
            # captured now: the record stays correct whichever thread or handler
            # renders it later
            "context": {**trace_context(), **context.snapshot()},
        },
    )


def _record_fields(record: logging.LogRecord) -> dict[str, Any]:
    fields = dict(getattr(record, "fields", None) or {})
    # plain ``logger.info(..., extra={...})`` calls from libraries
    for name, value in record.__dict__.items():
        if name not in _RECORD_ATTRIBUTES and not name.startswith("_"):
            fields.setdefault(name, value)
    return fields


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


class _BaseFormatter(logging.Formatter):
    def __init__(self, *, service: str, env: str, client_ip_mode: str = "full") -> None:
        super().__init__()
        self.service = service
        self.env = env
        self.client_ip_mode = client_ip_mode

    def _payload(self, record: logging.LogRecord) -> dict[str, Any]:
        severity_text, severity_number = _SEVERITY.get(record.levelno, ("INFO", 9))
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "severity_text": severity_text,
            "severity_number": severity_number,
            "body": record.getMessage(),
            "event.name": getattr(record, "event", None) or "log",
            "service.name": self.service,
            "service.version": __version__,
            "deployment.environment.name": self.env,
            "scope.name": record.name,
        }
        # emit-time context (log_event), else the live one (library loggers)
        captured = getattr(record, "context", None)
        payload.update(
            captured if captured is not None else {**trace_context(), **context.snapshot()}
        )
        payload.update(
            {key: value for key, value in _record_fields(record).items() if value is not None}
        )
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception.type"] = record.exc_info[0].__name__
            payload["exception.message"] = str(record.exc_info[1])[:2000]
            payload["exception.stacktrace"] = self.formatException(record.exc_info)
        self._apply_ip_mode(payload)
        if not (self.env in _DEVELOPMENT_ENVS and getattr(record, "allow_secrets", False)):
            payload = redact(payload)
        return payload

    def _apply_ip_mode(self, payload: dict[str, Any]) -> None:
        address = payload.get("client.address")
        if not address or self.client_ip_mode == "full":
            return
        if self.client_ip_mode == "off":
            payload.pop("client.address")
        else:
            payload["client.address"] = anonymize_ip(str(address))


class JsonFormatter(_BaseFormatter):
    def format(self, record: logging.LogRecord) -> str:
        # ASCII escaping: no raw control or line-separator character can reach the
        # output, whatever the value (log injection, ASVS 16.4.1)
        return json.dumps(
            self._payload(record), default=str, ensure_ascii=True, separators=(",", ":")
        )


def _escape(value: Any) -> str:
    text = value if isinstance(value, str) else str(value)
    return _CONTROL.sub(
        lambda match: {"\n": "\\n", "\r": "\\r", "\t": "\\t"}.get(
            match.group(), f"\\x{ord(match.group()):02x}"
        ),
        text,
    )


class TextFormatter(_BaseFormatter):
    """Readable single-line format for development; same content as the JSON.

    Every value is escaped, and traceback lines are prefixed with ``    | ``: a
    value can never start a line that looks like a record.
    """

    _SHOWN_FIRST = ("timestamp", "severity_text", "service.name", "event.name", "body")
    _HIDDEN = frozenset(
        {
            "severity_number",
            "service.version",
            "deployment.environment.name",
            "scope.name",
            "exception.stacktrace",
        }
    )

    def format(self, record: logging.LogRecord) -> str:
        payload = self._payload(record)
        head = (
            f"{payload['timestamp']} {payload['severity_text']:<5} {payload['service.name']} "
            f"[{_escape(payload['event.name'])}] {_escape(payload['body'])}"
        )
        extras = " ".join(
            f"{key}={_escape(value)}"
            for key, value in payload.items()
            if key not in self._SHOWN_FIRST and key not in self._HIDDEN
        )
        line = f"{head}  {extras}" if extras else head
        stacktrace = payload.get("exception.stacktrace")
        if stacktrace:
            line += "".join(f"\n    | {_escape(part)}" for part in stacktrace.splitlines())
        return line


# ---------------------------------------------------------------------------
# Non-blocking output
# ---------------------------------------------------------------------------


class _StdoutHandler(logging.StreamHandler):
    """Writes to the *current* ``sys.stdout`` (test runners swap it per test)."""

    def __init__(self) -> None:
        super().__init__(sys.stdout)
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        self.stream = sys.stdout
        super().emit(record)


class _FormattingQueueHandler(logging.handlers.QueueHandler):
    """Formats in the calling thread, hands the finished line to the writer thread.

    Formatting needs the caller's context (request context variables, the active
    span); writing to stdout is the part that may block, so only that is deferred.
    """

    _pfa_handler = True

    def __init__(self, line_queue: queue.Queue) -> None:
        super().__init__(line_queue)
        self.dropped = 0

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        return logging.makeLogRecord(
            {
                "msg": self.format(record),
                "levelno": record.levelno,
                "levelname": record.levelname,
                "name": record.name,
            }
        )

    def enqueue(self, record: logging.LogRecord) -> None:
        try:
            if self.dropped:
                self.queue.put_nowait(self._dropped_notice())
                self.dropped = 0
            self.queue.put_nowait(record)
        except queue.Full:
            self.dropped += 1

    def _dropped_notice(self) -> logging.LogRecord:
        notice = logging.makeLogRecord(
            {
                "msg": f"{self.dropped} log record(s) dropped: the output was not keeping up",
                "levelno": logging.WARNING,
                "levelname": "WARNING",
                "name": "pfa.observability",
                "event": "log.dropped",
                "fields": {"pfa.log.dropped_count": self.dropped},
            }
        )
        return self.prepare(notice)


_listener: logging.handlers.QueueListener | None = None


def _stop_listener() -> None:
    """Flush the queue and stop the writer thread."""
    global _listener
    if _listener is not None:
        _listener.stop()
        _listener = None


atexit.register(_stop_listener)

#: libraries that are chatty at INFO without adding audit value
_QUIET_LOGGERS = (
    "sqlalchemy.engine",
    "httpx",
    "httpcore",
    "watchfiles",
    "asyncio",
    "opentelemetry",
)


def configure_logging(service: str, settings: Settings | None = None) -> None:
    """Route every log record of this process to stdout in the configured format.

    Idempotent (replaces only its own handler and writer thread), so app factories
    may call it more than once.  Uvicorn's own loggers go through the same
    handler; its access log is switched off because
    :mod:`pfa.observability.access_log` replaces it with a structured,
    query-string-free event.
    """
    global _listener
    settings = settings or get_settings()
    formatter_class = JsonFormatter if settings.effective_log_format == "json" else TextFormatter
    formatter = formatter_class(
        service=service, env=settings.env, client_ip_mode=settings.log_client_ip
    )

    _stop_listener()
    line_queue: queue.Queue = queue.Queue(maxsize=_QUEUE_SIZE)
    handler = _FormattingQueueHandler(line_queue)
    handler.setFormatter(formatter)
    _listener = logging.handlers.QueueListener(line_queue, _StdoutHandler())
    _listener.start()

    root = logging.getLogger()
    for existing in list(root.handlers):
        if getattr(existing, "_pfa_handler", False):
            root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(settings.log_level)

    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.propagate = False
    access.disabled = True
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def flush_logs() -> None:
    """Write everything queued so far (for tests and orderly shutdown)."""
    if _listener is not None:
        # the writer thread marks every line done: join() returns once all are written
        _listener.queue.join()
