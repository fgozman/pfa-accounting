"""Production logging and telemetry contract (OWASP ASVS 5.0 V16, OpenTelemetry).

Structured, correlated, traced, audited, non-blocking, injection-proof, secret-free.
"""

from __future__ import annotations

import json
import logging
import queue
import re
import sys
import uuid

import httpx
import pytest
from httpx import AsyncClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import text

from pfa.config import reload_settings
from pfa.db import Database
from pfa.http.service_client import ServiceEndpoint
from pfa.observability import context
from pfa.observability import logs as logs_module
from pfa.observability.logs import (
    JsonFormatter,
    TextFormatter,
    _FormattingQueueHandler,
    anonymize_ip,
    log_event,
    redact,
)
from pfa.services.audit import record_audit
from tests.conftest import create_verified_user, fetch_outbox_payload, oauth_login


def _record(event: str = "test.event", *, level: int = logging.INFO, exc=None, message="something happened", allow=False, **fields):
    logger = logging.getLogger("pfa.test")
    return logger.makeRecord(
        logger.name, level, __file__, 1, message, (), exc,
        extra={"event": event, "fields": fields, "allow_secrets": allow},
    )


def _json(record: logging.LogRecord, **formatter_options) -> dict:
    return json.loads(JsonFormatter(service="pfa-api", env="production", **formatter_options).format(record))


@pytest.fixture(scope="module")
def spans() -> InMemorySpanExporter:
    """Collect spans of the process-wide tracer provider (installed by create_app)."""
    from pfa.observability.telemetry import configure_telemetry

    configure_telemetry("pfa-api")
    provider = trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider)
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return exporter


# ---------------------------------------------------------------------------
# Format: OpenTelemetry log data model and semantic conventions
# ---------------------------------------------------------------------------


def test_json_lines_follow_the_documented_contract() -> None:
    bound = context.bind(correlation_id="corr-12345678", user_id="u-1", client_ip="203.0.113.7")
    try:
        line = JsonFormatter(service="pfa-api", env="production").format(
            _record(**{"empty": None}, status=200)
        )
    finally:
        context.reset(bound)
    payload = json.loads(line)
    assert "\n" not in line
    assert payload["timestamp"].endswith("Z") and "T" in payload["timestamp"]
    assert payload["severity_text"] == "INFO" and payload["severity_number"] == 9
    assert payload["body"] == "something happened"
    assert payload["event.name"] == "test.event"
    assert payload["service.name"] == "pfa-api"
    assert payload["service.version"]
    assert payload["deployment.environment.name"] == "production"
    assert payload["scope.name"] == "pfa.test"
    assert payload["pfa.correlation_id"] == "corr-12345678"
    assert payload["user.id"] == "u-1"
    assert payload["client.address"] == "203.0.113.7"
    assert payload["status"] == 200
    assert "empty" not in payload  # None attributes are dropped


@pytest.mark.parametrize(
    ("level", "text", "number"),
    [(logging.DEBUG, "DEBUG", 5), (logging.WARNING, "WARN", 13), (logging.ERROR, "ERROR", 17), (logging.CRITICAL, "FATAL", 21)],
)
def test_severity_follows_opentelemetry(level: int, text: str, number: int) -> None:
    payload = _json(_record(level=level))
    assert (payload["severity_text"], payload["severity_number"]) == (text, number)


def test_an_exception_is_one_line_with_semantic_convention_fields() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        exc = sys.exc_info()
    line = JsonFormatter(service="pfa-worker", env="production").format(
        _record("job.failed", level=logging.ERROR, exc=exc)
    )
    payload = json.loads(line)
    assert "\n" not in line
    assert payload["exception.type"] == "ValueError"
    assert payload["exception.message"] == "boom"
    assert "Traceback" in payload["exception.stacktrace"]


def test_log_lines_carry_the_active_trace(spans: InMemorySpanExporter) -> None:
    tracer = trace.get_tracer("pfa.test")
    with tracer.start_as_current_span("work") as span:
        payload = _json(_record())
        expected = span.get_span_context()
    assert payload["trace_id"] == format(expected.trace_id, "032x")
    assert payload["span_id"] == format(expected.span_id, "016x")
    assert "trace_id" not in _json(_record())  # no active span, no ids


# ---------------------------------------------------------------------------
# Log injection (ASVS 16.4.1)
# ---------------------------------------------------------------------------

FORGED = "/x\n2026-01-01T00:00:00.000Z INFO  pfa-api [audit] account.password_reset user"


def test_the_text_format_cannot_be_forged_by_a_value() -> None:
    record = _record("http.request", message="GET unmatched 404\r\nINFO fake", **{"url.path": FORGED})
    line = TextFormatter(service="pfa-api", env="production").format(record)
    assert "\n" not in line and "\r" not in line
    assert "\\n2026-01-01" in line


def test_text_tracebacks_cannot_start_a_record_line() -> None:
    try:
        raise ValueError("bad\n2026-01-01T00:00:00.000Z INFO  pfa-api [audit] forged")
    except ValueError:
        exc = sys.exc_info()
    line = TextFormatter(service="pfa-api", env="production").format(_record(level=logging.ERROR, exc=exc))
    first, *rest = line.split("\n")
    assert rest and all(part.startswith("    | ") for part in rest)


def test_json_output_is_ascii_escaped() -> None:
    line = JsonFormatter(service="pfa-api", env="production").format(
        _record(message="Contribuabil și declarație\u2028next")
    )
    assert line.isascii()
    assert json.loads(line)["body"] == "Contribuabil și declarație\u2028next"


# ---------------------------------------------------------------------------
# Sensitive data (ASVS 16.2.5) and privacy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "dirty",
    [
        "callback /integrations/anaf/callback?state=abc&code=SECRET-CODE",
        "link https://spa.example/password-reset/confirm?token=SECRET-TOKEN",
        "header Authorization: Bearer SECRET.TOKEN.VALUE",
        "jwt eyJhbGciOiJFZERTQSJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJl",
    ],
)
def test_secrets_are_masked_in_every_string(dirty: str) -> None:
    cleaned = redact(dirty)
    assert "SECRET" not in cleaned and "eyJzdWIiOiJ4In0" not in cleaned
    assert "[REDACTED]" in cleaned


def test_sensitive_attribute_names_are_masked_including_dotted_ones() -> None:
    payload = redact(
        {"password": "p", "pfa.oauth.refresh_token": "r", "Authorization": "a", "nested": {"code": "c"}}
    )
    assert payload == {
        "password": "[REDACTED]",
        "pfa.oauth.refresh_token": "[REDACTED]",
        "Authorization": "[REDACTED]",
        "nested": {"code": "[REDACTED]"},
    }


def test_email_addresses_are_masked_everywhere() -> None:
    payload = _json(_record(message="no account for ana.popescu@firma.ro", detail="cc ion@example.test"))
    assert "ana.popescu" not in json.dumps(payload) and "ion@" not in json.dumps(payload)
    assert "a***@firma.ro" in payload["body"]
    assert payload["detail"] == "cc i***@example.test"


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("full", "203.0.113.77"), ("anonymized", "203.0.113.0"), ("off", None)],
)
def test_client_addresses_follow_the_configured_minimisation(mode: str, expected: str | None) -> None:
    bound = context.bind(client_ip="203.0.113.77")
    try:
        payload = _json(_record(), client_ip_mode=mode)
    finally:
        context.reset(bound)
    assert payload.get("client.address") == expected


def test_ipv6_addresses_are_truncated_to_a_48_prefix() -> None:
    assert anonymize_ip("2001:db8:85a3:8d3:1319:8a2e:370:7348") == "2001:db8:85a3::"
    assert anonymize_ip("not-an-ip") == "unknown"


def test_development_may_show_a_mail_link_but_production_never_does() -> None:
    message = "[mail:password_reset] to=ana@firma.ro link=https://spa/confirm?token=SECRET-TOKEN"
    for env, shown in (("development", True), ("production", False)):
        line = TextFormatter(service="pfa-worker", env=env).format(
            _record("mail.delivered", message=message, allow=True)
        )
        assert ("SECRET-TOKEN" in line) is shown
        assert ("ana@firma.ro" in line) is shown


# ---------------------------------------------------------------------------
# Non-blocking output
# ---------------------------------------------------------------------------


def test_records_are_written_by_a_background_thread(capsys: pytest.CaptureFixture[str]) -> None:
    settings = reload_settings().model_copy(update={"log_format": "json"})
    logs_module.configure_logging("pfa-test", settings)
    try:
        log_event(logging.getLogger("pfa.test"), "queue.check", message="through the queue")
        logs_module.flush_logs()
        lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
        assert any(line["event.name"] == "queue.check" for line in lines)
        assert isinstance(
            next(h for h in logging.getLogger().handlers if getattr(h, "_pfa_handler", False)),
            _FormattingQueueHandler,
        )
    finally:
        logs_module.configure_logging("pfa-api", reload_settings())


def test_a_full_queue_drops_lines_and_reports_how_many() -> None:
    line_queue: queue.Queue = queue.Queue(maxsize=1)
    handler = _FormattingQueueHandler(line_queue)
    handler.setFormatter(JsonFormatter(service="pfa-api", env="production"))
    for _ in range(3):  # the writer is "stuck": nothing drains the queue
        handler.emit(_record())
    assert handler.dropped == 2
    line_queue.get_nowait()  # the writer catches up
    handler.emit(_record("after"))
    notice = json.loads(line_queue.get_nowait().getMessage())
    assert notice["event.name"] == "log.dropped"
    assert notice["pfa.log.dropped_count"] == 2
    assert notice["severity_text"] == "WARN"


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def test_every_structured_event_is_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    counted: list[tuple[int, dict]] = []

    class Counter:
        def add(self, amount: int, attributes: dict) -> None:
            counted.append((amount, attributes))

    monkeypatch.setattr(logs_module, "events_counter", Counter())
    log_event(logging.getLogger("pfa.test"), "oauth.refresh_token_reuse_detected", level=logging.WARNING)
    assert counted == [(1, {"event.name": "oauth.refresh_token_reuse_detected", "severity_text": "WARN"})]


# ---------------------------------------------------------------------------
# Access log, request context and tracing
# ---------------------------------------------------------------------------


def _events(caplog: pytest.LogCaptureFixture, event: str) -> list[logging.LogRecord]:
    return [record for record in caplog.records if getattr(record, "event", None) == event]


async def test_each_request_is_logged_once_with_semantic_convention_attributes(
    client: AsyncClient, database: Database, caplog: pytest.LogCaptureFixture
) -> None:
    email = "access-log@example.test"
    user_id = await create_verified_user(database, email=email)
    session = await oauth_login(client, email=email)
    caplog.set_level(logging.DEBUG)
    caplog.clear()

    response = await client.get("/api/v1/me?debug=1&token=SECRET-QUERY", headers=session.headers())
    assert response.status_code == 200

    (record,) = _events(caplog, "http.request")
    fields = record.fields
    assert fields["http.route"] == "/api/v1/me"
    assert fields["url.path"] == "/api/v1/me"
    assert fields["http.response.status_code"] == 200
    assert fields["http.request.method"] == "GET"
    assert fields["user.id"] == user_id
    assert 0 <= fields["http.server.request.duration"] < 60
    rendered = JsonFormatter(service="pfa-api", env="production").format(record)
    assert "SECRET-QUERY" not in rendered and "debug=1" not in rendered


async def test_refusals_are_warnings_and_routes_are_templates(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    response = await client.get(f"/api/v1/workspaces/{uuid.uuid4()}/dashboard")
    assert response.status_code == 401
    (record,) = _events(caplog, "http.request")
    assert record.levelno == logging.WARNING
    assert record.fields["http.route"] == "/api/v1/workspaces/{workspace_id}/dashboard"


async def test_requests_produce_server_and_sql_spans(
    client: AsyncClient, spans: InMemorySpanExporter
) -> None:
    spans.clear()
    response = await client.get("/readyz", headers={"X-Correlation-Id": "span-check-0001"})
    assert response.status_code == 200
    names = [span.name for span in spans.get_finished_spans()]
    # health probes are excluded from server spans, but the SQL they run is traced
    assert not any(name.startswith("GET /readyz") for name in names)
    assert any(span.attributes.get("db.system.name") == "postgresql" or span.attributes.get("db.system") == "postgresql" for span in spans.get_finished_spans())

    spans.clear()
    await client.get("/api/v1/me")
    server = [span for span in spans.get_finished_spans() if span.kind == trace.SpanKind.SERVER]
    assert server and server[0].attributes.get("http.route") == "/api/v1/me"
    assert server[0].attributes.get("http.response.status_code") == 401


async def test_correlation_ids_are_echoed_and_untrusted_ones_replaced(client: AsyncClient) -> None:
    kept = await client.get("/healthz", headers={"X-Correlation-Id": "trace-abc-12345678"})
    assert kept.headers["x-correlation-id"] == "trace-abc-12345678"
    replaced = await client.get("/healthz", headers={"X-Correlation-Id": "x\\n{\"event\":\"audit\"}"})
    assert len(replaced.headers["x-correlation-id"]) == 32
    minted = await client.get("/healthz")
    assert re.fullmatch(r"[0-9a-f]{32}", minted.headers["x-correlation-id"])


async def test_service_calls_propagate_correlation_and_trace_context(spans: InMemorySpanExporter) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update({key.lower(): value for key, value in request.headers.items()})
        return httpx.Response(200, json={})

    endpoint = ServiceEndpoint("http://other.internal", "t" * 40, transport=httpx.MockTransport(handler))
    tracer = trace.get_tracer("pfa.test")
    bound = context.bind(correlation_id="cross-service-0001")
    try:
        with tracer.start_as_current_span("caller") as span:
            async with endpoint.client() as client:
                await client.get("/ping")
            trace_id = format(span.get_span_context().trace_id, "032x")
    finally:
        context.reset(bound)
    assert seen["x-correlation-id"] == "cross-service-0001"
    assert seen["traceparent"].split("-")[1] == trace_id


def test_the_uvicorn_access_log_is_replaced() -> None:
    from pfa.main import create_app

    create_app(reload_settings())
    assert logging.getLogger("uvicorn.access").disabled is True


# ---------------------------------------------------------------------------
# Audit trail and security events (ASVS 16.3)
# ---------------------------------------------------------------------------


async def test_audit_events_are_logged_only_once_committed(
    database: Database, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="pfa.audit")
    with pytest.raises(RuntimeError):
        async with database.session() as session:
            await record_audit(session, action="test.rolled_back", entity_type="test")
            raise RuntimeError("operation failed after auditing")
    assert _events(caplog, "audit") == []

    async with database.session() as session:
        await record_audit(
            session, action="test.committed", entity_type="test",
            after={"secret_field_value": "not logged", "name": "x"},
        )
    (record,) = _events(caplog, "audit")
    assert record.fields["pfa.audit.action"] == "test.committed"
    assert record.fields["pfa.audit.changed_fields"] == ["name", "secret_field_value"]
    assert "not logged" not in JsonFormatter(service="pfa-api", env="production").format(record)


def _actions(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.fields["pfa.audit.action"] for r in _events(caplog, "audit")]


async def test_successful_and_failed_logins_are_both_audited(
    client: AsyncClient, database: Database, owner_database: Database, caplog: pytest.LogCaptureFixture
) -> None:
    from pfa.security.tokens import new_pkce_verifier, new_state, pkce_challenge_s256

    caplog.set_level(logging.INFO)
    settings = reload_settings()
    user_id = await create_verified_user(database, email="audited@example.test")
    page = await client.get(
        "/oauth/authorize",
        params={
            "response_type": "code", "client_id": settings.spa_client_id,
            "redirect_uri": settings.spa_callback_uri, "scope": "pfa.read", "state": new_state(),
            "code_challenge": pkce_challenge_s256(new_pkce_verifier()), "code_challenge_method": "S256",
        },
        headers={"Origin": settings.spa_origin},
    )
    proof = re.search(r'name="transaction_proof" value="([^"]+)"', page.text).group(1)
    tx = re.search(r"/auth/authorization-transactions/([^/]+)/authenticate", page.text).group(1)
    await client.post(
        f"/auth/authorization-transactions/{tx}/authenticate",
        data={"email": "audited@example.test", "password": "wrong-password-1", "transaction_proof": proof},
        headers={"Origin": settings.spa_origin, "X-Correlation-Id": "login-trace-0001"},
        follow_redirects=False,
    )
    await oauth_login(client, email="audited@example.test")

    async with owner_database.session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT action, correlation_id, ip_address FROM audit_events "
                    "WHERE actor_user_id = :user_id AND action LIKE 'account.login_%' ORDER BY created_at"
                ),
                {"user_id": user_id},
            )
        ).all()
    assert [row.action for row in rows] == ["account.login_failed", "account.login_succeeded"]
    assert rows[0].correlation_id == "login-trace-0001"
    assert all(row.ip_address for row in rows)
    assert {"account.login_failed", "account.login_succeeded", "oauth.code_exchanged"} <= set(_actions(caplog))
    (rejected,) = _events(caplog, "oauth.login_rejected")
    assert rejected.fields["pfa.oauth.reason"] == "invalid_credentials"


async def test_token_refresh_and_reuse_and_session_revocations_are_logged(
    client: AsyncClient, database: Database, caplog: pytest.LogCaptureFixture
) -> None:
    await create_verified_user(database, email="reuse-log@example.test")
    session = await oauth_login(client, email="reuse-log@example.test")
    settings = reload_settings()
    data = {"grant_type": "refresh_token", "client_id": settings.spa_client_id, "refresh_token": session.refresh_token}
    caplog.set_level(logging.INFO, logger="pfa.oauth.security")

    assert (await client.post("/oauth/token", data=data)).status_code == 200
    (refreshed,) = _events(caplog, "oauth.token_refreshed")
    assert refreshed.fields["pfa.oauth.client_id"] == settings.spa_client_id

    assert (await client.post("/oauth/token", data=data)).status_code == 400
    (reuse,) = _events(caplog, "oauth.refresh_token_reuse_detected")
    assert reuse.levelno == logging.WARNING
    (revoked,) = _events(caplog, "oauth.session_revoked")
    assert revoked.fields["pfa.oauth.reason"] == "refresh_reuse"


async def test_password_reset_request_and_revocation_of_all_sessions_are_audited(
    client: AsyncClient, database: Database, owner_database: Database, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    email = "reset-audit@example.test"
    await create_verified_user(database, email=email)
    await oauth_login(client, email=email)
    await client.post("/auth/password-reset/request", json={"email": email})
    token = (await fetch_outbox_payload(owner_database, event_type="email.password_reset_requested"))["token"]
    await client.post("/auth/password-reset/confirm", json={"token": token, "new_password": "Another-Strong-Pass-9"})

    actions = _actions(caplog)
    assert "account.password_reset_requested" in actions
    assert "account.password_reset" in actions
    assert "session.revoked_all" in actions
    (revoked,) = _events(caplog, "oauth.sessions_revoked")
    assert revoked.fields["pfa.oauth.reason"] == "password_reset"
    assert revoked.fields["pfa.oauth.revoked_sessions"] == 1
    assert revoked.levelno == logging.WARNING


async def test_an_email_change_is_audited_without_the_addresses(
    client: AsyncClient, database: Database, caplog: pytest.LogCaptureFixture
) -> None:
    from pfa.services import accounts

    caplog.set_level(logging.INFO)
    user_id = await create_verified_user(database, email="before@example.test")
    async with database.session() as session:
        user = await accounts.get_user(session, uuid.UUID(user_id))
        await accounts.change_email(
            session, user=user, new_email="after@example.test", current_password="Correct-Horse-Battery-9"
        )
    (record,) = [r for r in _events(caplog, "audit") if r.fields["pfa.audit.action"] == "account.email_changed"]
    rendered = JsonFormatter(service="pfa-api", env="production").format(record)
    assert "before@" not in rendered and "after@" not in rendered
