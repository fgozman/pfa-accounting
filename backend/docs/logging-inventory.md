# Logging and telemetry inventory

The inventory required by **OWASP ASVS 5.0, 16.1.1**: what is recorded at each
layer, in which format, where it goes, how it is used, who may access it, how it
is protected and how long it is kept. The code is in `pfa/observability/`; the
contract is enforced by `tests/test_observability.py`.

Sections marked **Operator decision** are not settled by the code. They belong to
whoever deploys and runs the system (the data controller under GDPR), and must
be filled in before production.

---

## 1. Layers and destinations (ASVS 16.2.3)

The application writes **only** to the destinations below. It never writes log
files and never sends logs anywhere else.

| Layer | What | Destination | Format |
|---|---|---|---|
| API (`pfa-api`), worker (`pfa-worker`), standalone OAuth server (`pfa-oauth`) | Structured events (section 3), library warnings/errors | **stdout** of the process | One JSON object per line (`PFA_LOG_FORMAT=json`, default outside dev/test) |
| Same processes | Traces (spans) and metrics | **OTLP/HTTP** to `OTEL_EXPORTER_OTLP_ENDPOINT` (the collector), only when set | OpenTelemetry protocol |
| Application database | Audit trail (system of record) | `audit_events` table, append-only by trigger | Rows |
| OAuth database | Session/token state (not a log) | `oauth_*` tables | Rows |
| PostgreSQL server | Server logs | stderr of the database service | PostgreSQL format: **Operator decision** (e.g. `log_connections`, `log_disconnections`, failed authentication) |
| Collector (Grafana Alloy) | Ships stdout and OTLP | Loki (logs), Tempo (traces), Prometheus/Mimir (metrics) | See `observability/alloy/` |

Development only: the dev launcher prints banners, and Vite prints its own
output. Neither is structured, and the log pipeline drops them.

## 2. Log record format (ASVS 16.2.1, 16.2.2, 16.2.4)

The format follows the OpenTelemetry log data model and semantic conventions.
Application-specific attributes use the `pfa.` namespace.

| Field | Meaning |
|---|---|
| `timestamp` | When: UTC, RFC 3339 with milliseconds (`2026-11-08T19:36:01.123Z`) |
| `severity_text`, `severity_number` | `DEBUG`/5, `INFO`/9, `WARN`/13, `ERROR`/17, `FATAL`/21 |
| `body` | Human-readable message |
| `event.name` | What: stable event name (section 3); `log` for library records |
| `service.name`, `service.version`, `deployment.environment.name` | Where: the emitting service |
| `scope.name` | The Python logger |
| `trace_id`, `span_id` | Link to the distributed trace (W3C trace context) |
| `pfa.correlation_id` | Request id, echoed as `X-Correlation-Id` and stored in `audit_events.correlation_id` |
| `user.id` | Who: the authenticated user or the token subject |
| `client.address` | From where: client IP, subject to `PFA_LOG_CLIENT_IP` (section 6) |
| `exception.type`, `exception.message`, `exception.stacktrace` | On errors. A traceback is one field. |
| event attributes | `http.*`, `url.path`, `user_agent.original`, `session.id`, `pfa.audit.*`, `pfa.oauth.*`, `pfa.job.*`... |

**Time sources.** Timestamps come from the host clock, so every host must run NTP
or chrony. **Operator decision:** document the time source per environment.

## 3. Events (ASVS 16.3)

| `event.name` | Level | When | Key attributes |
|---|---|---|---|
| `http.request` | INFO; WARN for 401/403/429; ERROR for 5xx; DEBUG for probes and preflights | Every HTTP request (replaces the uvicorn access log) | `http.request.method`, `http.route` (template), `url.path`, `http.response.status_code`, `http.server.request.duration`, `user.id`, `user_agent.original`. **Never the query string.** |
| `audit` | INFO | Every committed `audit_events` row; dropped on rollback | `pfa.audit.action`, `pfa.audit.entity_type`, `pfa.audit.entity_id`, `pfa.audit.actor_kind`, `pfa.audit.changed_fields`, `pfa.workspace.id`, `user.id`, `client.address`. Before/after **values** stay in the database. |
| `oauth.login_rejected` | INFO; WARN when attempts are exhausted | Credentials refused on the login page | `pfa.oauth.reason`, `pfa.oauth.transaction_id`, `pfa.oauth.attempt_count` |
| `oauth.login_attempts_exhausted` | WARN | Per-transaction attempt limit reached | `pfa.oauth.transaction_id`, `pfa.oauth.attempt_count` |
| `oauth.transaction_proof_invalid`, `oauth.origin_mismatch` | WARN | Tampered or cross-origin login form | `pfa.oauth.transaction_id` |
| `oauth.code_reuse_detected`, `oauth.refresh_token_reuse_detected` | WARN | Replay detected; the session family was revoked | `pfa.oauth.client_id`, `session.id`, `user.id` |
| `oauth.token_refreshed` | INFO | Refresh-token rotation (token issuance) | `pfa.oauth.client_id`, `session.id`, `user.id` |
| `oauth.session_revoked` | INFO | One session family revoked (logout, replay, expiry, client revocation) | `session.id`, `pfa.oauth.reason` |
| `oauth.sessions_revoked` | WARN if any revoked, else INFO | All of a user's sessions revoked (password reset, email change) | `user.id`, `pfa.oauth.reason`, `pfa.oauth.revoked_sessions` |
| `job.succeeded` / `job.failed` | INFO / WARN; ERROR when final | Background job | `pfa.job.id`, `pfa.job.kind`, `pfa.workspace.id`, `pfa.job.attempts`, `pfa.job.dead`, `pfa.job.duration` |
| `outbox.failed` | WARN; ERROR when final | Outbox delivery failure | `pfa.outbox.event_id`, `pfa.outbox.event_type`, `pfa.outbox.attempts`, `pfa.outbox.final` |
| `mail.delivered` | INFO | Email handed to the provider | `pfa.mail.template`, `pfa.mail.recipient` (masked) |
| `reconciliation.completed`, `worker.started` | INFO | Worker lifecycle | counts, durations |
| `log.dropped` | WARN | The output could not keep up and lines were dropped | `pfa.log.dropped_count` |

**Audit actions relevant to authentication** (`pfa.audit.action`):
* `account.login_succeeded` and `account.login_failed`: every authentication decision, with method `password` (ASVS 16.3.1);
* `account.registered`;
* `account.email_verified`, `account.verification_resent`;
* `account.password_reset_requested`, `account.password_reset`;
* `account.email_changed`;
* `oauth.code_exchanged` (token issuance);
* `session.logout`, `session.revoked_all`.

Business actions (documents, declarations, memberships, …) are audited as well.

**Authorization failures** (ASVS 16.3.2) are `http.request` events at WARN with
status 401/403. **Anti-automation** (16.3.3) is covered by 429 statuses, the
`oauth.*` refusals and replay events, and the per-transaction attempt limit.
**Unexpected errors** (16.3.4) are 5xx `http.request` events, uvicorn's
`Exception in ASGI application` records, and `job.failed`/`outbox.failed`. All
carry the exception fields; the client always receives a generic RFC 9457
problem body.

## 4. Metrics and traces

| Signal | Instrument | Attributes |
|---|---|---|
| `http.server.request.duration` (histogram, s) | FastAPI instrumentation, stable HTTP semantic conventions | `http.request.method`, `http.route`, `http.response.status_code` |
| `http.server.active_requests`, request/response body size | FastAPI instrumentation | idem |
| `db.client.connections.usage` | SQLAlchemy instrumentation | pool state |
| `pfa.events` (counter) | Every structured event | `event.name`, `severity_text` |
| `pfa.worker.work.duration` (histogram, s) | Worker jobs and outbox deliveries | `pfa.work.kind`, `pfa.work.outcome` |
| Spans | HTTP server, HTTP client (application ↔ OAuth server), every SQL statement, worker jobs/outbox events | standard attributes. SQL text has bound parameters; values are never inlined (no SQL commenter). |

Trace context crosses service boundaries as W3C `traceparent`. The correlation
id travels as `X-Correlation-Id`. Configuration uses the standard `OTEL_*`
variables (see `pfa/observability/telemetry.py`).

## 5. Sensitive data (ASVS 16.2.5)

| Data | Treatment |
|---|---|
| Passwords, tokens, codes, PKCE verifiers, transaction proofs, client secrets, `Authorization`/cookie headers | Never logged: attributes with these names are replaced by `[REDACTED]`; bearer tokens, JWTs and secret query parameters are masked inside every string |
| Query strings | Never logged (the access log records `url.path` only) |
| Email addresses | Masked in every string (`a***@example.ro`), including exception messages |
| Audit before/after values (fiscal and personal data) | Kept in `audit_events` only; logs carry the changed field **names** |
| SQL parameters | Not in spans or logs |
| Development only | The worker's mail line shows the full link and address, so they can be found locally. This is impossible outside `development`/`test`. |

## 6. Personal data and privacy (GDPR / Law 190/2018)

Logs contain personal data: `user.id`, `client.address`, `user_agent.original`,
and masked email addresses.

| Topic | Position |
|---|---|
| Purpose | Security monitoring, incident investigation, fraud prevention, service reliability |
| Legal basis | **Operator decision**. Typically legitimate interest (GDPR art. 6(1)(f); recital 49 names network and information security). Record it in the record of processing activities. |
| Minimisation | `PFA_LOG_CLIENT_IP=full` (default: needed to investigate attacks), `anonymized` (IPv4 /24, IPv6 /48) or `off`. Applies to logs; `audit_events` keeps the full address as the system of record. |
| Retention | **Operator decision.** Suggested starting points: application logs 90 days, security and audit events 1 year, traces 14 days, metrics 30 days to 13 months. The local stack uses Loki 90 d, Tempo 14 d, Prometheus 30 d. `audit_events` follows the legal retention of the accounting records it documents. |
| Data subject requests | Logs are keyed by `user.id`. Erasure from append-only security logs is normally refused under art. 17(3)(b)/(e) during the retention period. **Operator decision.** |
| Transfers | If the log store is a SaaS outside the EEA, a transfer mechanism is required. **Operator decision.** |

## 7. Protection of logs (ASVS 16.4)

| Requirement | Implementation / **Operator decision** |
|---|---|
| 16.4.1 Encoding against log injection | Implemented. JSON is ASCII-escaped. The text format escapes control characters and indents traceback lines. Client-supplied correlation ids are validated. Tested. |
| 16.4.2 No unauthorized access or modification | `audit_events` is append-only (database trigger), and the runtime role cannot `TRUNCATE`. Shipped logs are an **Operator decision**: store Loki/Tempo data in object storage with write-once retention (S3 Object Lock / GCS bucket lock / Azure immutable blobs) in a separate account, and grant read access by role (Grafana RBAC: security team and on-call only). |
| 16.4.3 Secure transport to a separate system | The processes only write stdout and OTLP to a local collector. **Operator decision:** collector → store over TLS with authentication (`production-docker.alloy` uses basic auth and an HTTPS endpoint), and the store outside the application's blast radius (separate host/account, no application credentials). |

## 8. Delivery guarantees

* **Logging never blocks a request.** Records are formatted in the caller and
  written by a background thread from a bounded queue of 10,000 lines. If the
  output stalls, new lines are dropped, counted, and reported by a `log.dropped`
  event once it recovers. A clean shutdown flushes the queue. A hard kill loses
  what is queued.
* **`audit` log events are emitted after commit.** A crash between commit and
  emission loses the log line, never the `audit_events` row, which remains the
  system of record.
* **Spans and metrics are batched** (5 s for spans, `OTEL_METRIC_EXPORT_INTERVAL`
  for metrics) and flushed on a clean exit.
* **Forking servers:** with gunicorn `--preload` (or any fork after start), call
  `configure_logging`/`configure_telemetry` in each worker (`post_fork`), because
  threads do not survive a fork. Running uvicorn workers without preload needs
  nothing.

## 9. How the logs are used

* **Dashboards:** `observability/grafana/dashboards/pfa-audit.json`, generated by
  `observability/grafana/build_dashboard.py`. It shows traffic, latency, errors,
  failed logins, replays, the audit trail, security events, slow traces, the
  service graph, and every line of one correlation id.
* **Alerts:** examples in the backend README.
* **Investigations:** start from an `audit_events` row or a user report. Then:
  * follow its `correlation_id` across all services' logs;
  * open the linked trace (`trace_id`);
  * check the user's other events (`user.id`).
