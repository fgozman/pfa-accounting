# PFA Accounting — Backend

The Python side of the product: a multi-tenant FastAPI service, a durable worker,
a PostgreSQL schema with row-level security and the deterministic fiscal engine, for
a Romanian **persoană fizică autorizată**.

This is a **self-contained project**. It owns its virtualenv, `.env`, Compose
files, migrations, scripts and tests. It never reads or starts the SPA; the
frontend is a separate project (`../frontend/`, see its README).

* **Services** — FastAPI (`pfa/api/`), modular monolith
* **Worker** — durable outbox, jobs, leases, scheduled reconciliation (`pfa/worker.py`)
* **Database** — PostgreSQL with row-level security (`migrations/`)
* **Fiscal engine** — deterministic, HTTP/UI/database-independent package (`pfa/fiscal/`)
* **User-facing language** — Romanian-first copy is rendered by the frontend; the
  backend's authorization pages are server-rendered Romanian

---

## Project layout

```
backend/
  pyproject.toml        project metadata, pinned dependencies, pytest/ruff config
  Makefile              all backend commands (see `make help`)
  .env.example          copy to .env — every setting, no secrets
  compose.yaml          development PostgreSQL (named volume)
  compose.test.yaml     isolated test PostgreSQL (separate project/volume/port)
  alembic.ini           Alembic config of the application database (owner/DDL role)
  alembic_oauth.ini     Alembic config of the OAuth database (owner/DDL role)
  docker/               postgres init scripts (roles, grants, OAuth database)
  migrations/           application database: frozen baseline DDL, tenancy security
                        (RLS, triggers), revisions
  migrations_oauth/     OAuth database: frozen baseline DDL, revisions
  observability/        local Alloy + Loki + Tempo + Prometheus + Grafana (dashboard
                        and its generator) and a production Alloy pipeline
  docs/                 design notes (raw-SQL migration)
  pfa/
    config.py           environment settings (pydantic-settings)
    db.py               async engine, tenant context, RLS helpers
    errors.py           domain errors mapped to RFC 9457 problem details
    clock.py            injectable clock (tests freeze time)
    security/           Argon2id, opaque tokens & PKCE, Ed25519 JWT keys, rate limiting
    fiscal/             deterministic engine: money, calendar, rules, packs, engine, VAT, fx
    models/             SQLAlchemy models (schema source of truth)
    repo/               hand-written SQL, one module per domain
    services/           accounts, tenancy, fiscal_profile, documents, settlements, assets,
                        books, obligations, deadlines, notifications, declarations,
                        dashboard, anaf_connections, jobs, storage, idempotency, email,
                        audit/outbox, xml
    anaf/               transport interface, deterministic fake, e-Factura adapter
    api/                deps (auth, units of work), application routes
                        (accounts, me, workspaces, accounting, compliance, anaf)
    oauth/              OAuth 2.0 authorization server (separable; see below)
    clients/            application-side clients of the authorization server (in-process / HTTP)
    http/               shared HTTP infrastructure: problem details, security headers,
                        service-to-service auth and client
    observability/      structured logging (OpenTelemetry format), redaction, request
                        context, access log, OpenTelemetry traces and metrics
    wiring.py           in-process composition of the application and oauth/
    static/             vendored Swagger UI assets (Apache-2.0, see VENDORED.txt)
    scripts/            dev, migrate/test helpers, seed, keys, OpenAPI export
    worker.py           durable worker entry point
  tests/                pytest suite (isolated test database)
```

Tax calculations are separated from ANAF transport and from document rendering:
`pfa.fiscal` has no HTTP, database or UI dependency, so it can be unit-tested and
reviewed on its own.

---

## Prerequisites

* Python 3.12 and [uv](https://docs.astral.sh/uv/)
* Docker with the Compose plugin (development and test PostgreSQL)

## Quick start

All commands run from `backend/`.

```sh
cp .env.example .env
make setup                      # virtualenv + pinned deps + dev signing keys
make up                         # development PostgreSQL
make migrate                    # both databases (creates the OAuth one if missing)
make seed-dev                   # synthetic data (refuses production config)
make dev                        # API + worker
```

`make dev` starts the API (<http://127.0.0.1:8000>, docs at `/docs`) and the
durable worker. To develop the full product, start the frontend project in a
second terminal (`make -C ../frontend dev`, or `make dev` from the repository
root, which starts both).

Seeded development accounts (synthetic data only):

| Account | Password |
|---|---|
| `admin@example.test` | `Correct-Horse-Battery-9` |
| `member@example.test` | `Correct-Horse-Battery-9` |

`make seed-dev` is idempotent, refuses to run with `PFA_ENV=production`, and
creates a real-system PFA and an income-norm PFA, an `ADMIN` and `DEFAULT` member
for each, example documents, a partial collection, an owner contribution and the
exact localhost SPA callback registration.

## Commands

```sh
make help            # every target, with a one-line description
```

| Area | Targets |
|---|---|
| Setup | `env`, `venv`, `install`, `setup`, `keys`, `keys-rotate` |
| Database | `up`, `down`, `wait-db`, `migrate`, `migrate-test`, `seed-dev`, `reset-dev` (destructive) |
| Run | `dev` (API + worker), `api`, `worker` |
| Test | `test-db-up`, `test-db-down`, `test` |
| Quality | `lint`, `format` |
| Contract | `openapi` (publishes `backend/openapi.json`) |
| Housekeeping | `clean` |

## Configuration

Every setting comes from the environment or `backend/.env` (created from
`.env.example`, which is fully commented). `pfa/config.py` loads `.env` relative
to the project directory, so the backend works regardless of the working
directory it is started from.

Highlights:

* **Two database roles.** `PFA_MIGRATION_DATABASE_URL` is the owner (DDL) role
  used only by Alembic; `PFA_DATABASE_URL` is the runtime role, which cannot own
  tables or bypass RLS.
* **Origins are explicit.** `PFA_AUTHORIZATION_ORIGIN` (this service),
  `PFA_API_ORIGIN` (what the SPA calls; defaults to the authorization origin) and
  `PFA_SPA_ORIGIN` (CORS allowlist and e-mail links) are independent on purpose.
  `PFA_SPA_CALLBACK_URI` and `PFA_SPA_CLIENT_ID` must match the registered OAuth
  client byte-for-byte.
* **Keys live outside PostgreSQL.** `PFA_KEY_DIR` points at PEM files; never
  commit them.
* **ANAF transport** is `fake` by default. `https` requires a verified connector
  capability recorded in the database.

The SPA's public configuration (`VITE_*`) belongs to the frontend project and is
documented in `../frontend/README.md`. When the two are started together via the
repository-root `make dev`, the SPA's values are derived from these settings so
they cannot drift.

---

## Database

PostgreSQL is required: SQLite cannot provide the isolation, `NUMERIC` semantics,
locking or RLS guarantees this application relies on.

* `compose.yaml` pins a supported major version and release digest, uses a named
  development volume and binds `127.0.0.1` only.
* `compose.test.yaml` is a **separate Compose project** (name, database, volume
  and port 5433) with no access to the development volume. `make test-db-down`
  removes only its own resources.
* Test helpers reject any database whose URL does not identify it as a test
  database, so a mistyped URL cannot destroy development data.

```sh
make down        # stop, keep the development volume
make reset-dev   # DESTRUCTIVE: fresh volume, migrate and re-seed
```

## API documentation

With `make dev` running:

| URL | What it is |
|---|---|
| <http://127.0.0.1:8000/docs> | Swagger UI — browse and call every endpoint |
| <http://127.0.0.1:8000/openapi.json> | the OpenAPI 3 document itself |

The documentation is **self-hosted and CSP-clean**: Swagger UI is vendored from
the pinned `swagger-ui-dist` package into `pfa/static/swagger-ui/` (Apache-2.0,
checksums in `VENDORED.txt`) and the page loads **external scripts only** with no
third-party origin. That matters because FastAPI's built-in page boots Swagger UI
with an *inline* `<script>`, which a policy without `'unsafe-inline'` blocks: the
page answers `200` and renders nothing. One strict `Content-Security-Policy`
therefore covers the application and the documentation alike, and the docs keep
working without internet access.

Authentication is proved through **Authorize**, which offers both real paths:

1. **HTTPBearer** — paste a short-lived access token (5 minutes).
2. **OAuth2 (authorization code + PKCE)** — Swagger opens the same cookie-free
   authorization page the SPA uses (`usePkceWithAuthorizationCodeGrant`) and
   exchanges the code itself. No client secret: the documentation client
   (`<spa-client-id>-docs`, registered by `make seed-dev`) is a public client whose
   redirect URI `http://127.0.0.1:8000/docs/oauth2-redirect` is registered
   byte-for-byte.

`persistAuthorization` is **off**, so Swagger never writes a token to
`localStorage` — the same token-storage policy as the SPA.

The interactive UI is mounted only outside production (`PFA_DOCS_ENABLED` plus a
non-production `PFA_ENV`); `/openapi.json` stays published in every environment
for client generation.

### Refreshing the vendored assets

The exact `swagger-ui-dist` version is pinned in the frontend's `package.json`
(where the npm dependency lives). The copy step is an integration concern and is
run from the repository root:

```sh
make vendor-swagger-ui    # SWAGGER_UI_TARGET_DIR -> pfa/static/swagger-ui
```

### Publishing the OpenAPI contract

```sh
make openapi              # writes backend/openapi.json
```

The frontend pins its own copy of this document and generates TypeScript types
from it; see `../frontend/README.md`. The repository-root `make types` runs both
halves.

---

## Email in development

No SMTP provider is configured locally. The worker captures messages to a local
sink instead of sending them, and prints the link to the `make dev` output:

```
[mail:verification] to=you@example.com link=http://127.0.0.1:5173/verify-email?token=…
```

The quickest way to read the latest captured message is:

```sh
make mail                                   # newest first, with the link
make mail TEMPLATE=verification EMAIL=you@example.com   # optional filters
make -C backend mail                        # from the repository root
```

Captured files are the source of truth and live in `PFA_EMAIL_DIR` (default
`backend/.run/emails/*.json`, one JSON file per message with template, recipient,
link and token). The directory is its own setting so development, tests and e2e
never share an inbox; `make test` clears only `.run/test-emails`. Templates:
`verification`, `password_reset`, `invitation`, `reminder`.

> A resend for an **unknown** or **already-verified** address is a deliberate
> no-op (it still answers `202` without revealing account state), so it produces no
> file. Only a pending account gets mail.

You can also retrieve messages over HTTP — the endpoint returns `404` outside
development/test, so it cannot leak in production:

```sh
curl -s 'http://127.0.0.1:8000/api/v1/dev/mailbox?template=verification' | jq '.items[0].link'
curl -s 'http://127.0.0.1:8000/api/v1/dev/mailbox/latest?template=password_reset' | jq -r .link
```

---

## Declaration artifacts and official schemas

Generated declarations are only ever *official* if they validate against the ANAF
XSD for their form. The schemas are vendored verbatim under
`pfa/static/schemas/anaf/` (provenance and SHA-256 in `SOURCES.md`) and mapped in
`pfa/services/form_schemas.py`:

| Form | Official namespace | Root element |
|---|---|---|
| D212 | `mfp:anaf:dgti:d212:declaratie:v9` | `d212` |
| D300 | `mfp:anaf:dgti:d300:declaratie:v12` | `declaratie300` |
| D301 | `mfp:anaf:dgti:d301:declaratie:v1` | `declaratie301` |
| D390 | `mfp:anaf:dgti:d390:declaratie:v3` | `declaratie390` |
| D394 | `mfp:anaf:dgti:d394:declaratie:v5` | `declaratie394` |

On export the artifact is validated; if it does not conform it is stamped as a
workpaper (`preview="true"` and the reason **inside the file**), the approval cannot
be filed, and the export response/audit record `official_schema`, `schema_valid` and
`schema_errors`. The `form_versions.supported` flag alone is never enough.

The D390 generator now emits the official ``declaratie390`` structure and reports
``schema_valid: true``. The D212/D300/D301/D394 generators still emit the internal
workpaper structure and report ``schema_valid: false`` until their official field
mapping is implemented; see `../IMPLEMENTATION_STATUS.md` for the per-form gaps.

## Data access

Queries are hand-written SQL, not ORM navigation. Every statement lives in
`pfa/repo/` (one module per domain) and is written out at the call site with an
explicit column list, explicit casts for `jsonb`/`uuid`/arrays, and bound
parameters only. `Row` is a frozen mapping with attribute access, so a wrong
projection fails loudly (`AttributeError` naming the available columns) instead of
returning `None`.

Why: query shape, join strategy, window functions, `FOR UPDATE` scope and cost are
decisions rather than ORM side effects, and `SELECT *` never appears in application
SQL. The SQLAlchemy models describe the *schema* but are not used to execute
queries. They do **not** generate migrations: revision 0001 is a frozen DDL
snapshot (`migrations/baseline/0001_initial_schema.sql`) and every later change
is its own revision. `tests/test_migrations.py` migrates an empty database to head
(and back), runs the seed on it, and fails if the result drifts from the models.

Grouped aggregates replace the per-row lookups the ORM encouraged, and
`tests/test_query_counts.py` counts executed statements to keep it that way.

See `docs/raw-sql-migration.md` for the design rules, the NOT NULL checklist every
`INSERT` must satisfy, the conversion status per module and the recipe for the
remaining files.

## Authorization server boundary

The OAuth 2.0 authorization server is a self-contained package, `pfa/oauth/`, that
can be moved to its own service without touching application code.
`tests/test_architecture.py` enforces every rule below.

* **Own database.** The OAuth 2.0 tables (`oauth_clients`, `oauth_redirect_uris`,
  `oauth_origins`, `oauth_authorization_transactions`, `oauth_authorization_codes`,
  `oauth_sessions`, `oauth_refresh_tokens`) live in a separate database,
  `PFA_OAUTH_DATABASE_URL` (`pfa_oauth` in development, `pfa_oauth_test` in tests).
  They have their own metadata (`OAuthBase`) and their own migration history
  (`migrations_oauth/`, `alembic_oauth.ini`). The application database holds none
  of them (revision `0016`). No foreign key and no transaction spans the two:
  `user_id` columns hold the opaque subject id (JWT `sub`) issued by the identity
  provider. All SQL against these tables is in `pfa/oauth/repo.py`, which runs on
  `pfa/oauth/db.py` and never opens the application database.
* **Package.** `pfa/oauth/` holds the models, repository, flows (clients,
  transactions, codes, token endpoint, revocation), session lifecycle, token
  introspection, signing keys, login pages and routes (`/oauth/*`, the login POST,
  JWKS, `/api/v1/sessions/logout`). It imports only shared infrastructure
  (`pfa.config`, `errors`, `clock`, `db`, `http`, `security`, `repo.query`, `models.base`),
  which in turn depends on neither side.
* **Ports.** The two sides only know each other's interfaces:
  * the server needs an `IdentityProvider` (verify credentials, return a subject id)
    and an `AuditSink` (`pfa/oauth/ports.py`);
  * the application needs an `AuthorizationServerClient`: `introspect(token)` on
    every request and `revoke_user_sessions(user_id)` on credential changes
    (`pfa/clients/authorization_server.py`).

  `pfa/wiring.py` implements both in-process. It and `pfa/main.py` are the only
  application modules that import `pfa.oauth`. Each adapter opens its own unit of
  work, as a remote call would.
* **Application routes** (`pfa/api/routes/`: accounts, me, workspaces, accounting,
  compliance, anaf) never read the database directly. They take a unit of work from
  `pfa/api/deps.py` (`PublicSession`, `UserSession`, `workspace_session`,
  `DeclarationScope`, `JobScope`, ...) and call services. No `pfa.db`, repositories,
  SQLAlchemy or table models are imported in handlers.

**Two deployment shapes**, chosen by configuration (`pfa.wiring.wire`):

| | Monolith (default) | Split |
|---|---|---|
| Processes | `make api` | `make api` (application) + `make auth-server` (OAuth, :8001) |
| `PFA_AUTHORIZATION_SERVER_URL` | empty | internal URL of the server (application side) |
| `PFA_IDENTITY_PROVIDER_URL` | — | internal URL of the application (server side) |
| `PFA_INTERNAL_SERVICE_TOKEN` | — | same ≥ 32-char secret on both sides |
| Token check | in-process introspection | RFC 7662 `POST /oauth/introspect` |
| Session revocation | in-process | `POST /oauth/subjects/{id}/sessions/revoke` |
| Credential check | in-process `accounts.check_credentials` | `POST /internal/identity/authenticate` on the application |
| Server audit events | `audit_events` table | JSON lines on the `pfa.oauth.audit` logger |

In split mode the application serves no `/oauth/*` route and holds no signing key.
The standalone server publishes no OpenAPI document. The internal endpoints need
the service token, answer 404 while none is configured, and are never in
`openapi.json`. Point `PFA_AUTHORIZATION_ORIGIN` (and the SPA's
`VITE_AUTHORIZATION_ORIGIN`) at the server's public origin. Failures fail closed:
an unreachable server returns 503 for API calls and blocks password resets and
email changes, because sessions could not be revoked. `tests/test_service_split.py`
runs both services as separate ASGI apps that talk HTTP.

In both shapes the two databases are separate. In development they are on the
same PostgreSQL server; in production they can be on different servers. Only the
monolith process (or `pfa.wiring`'s in-process adapters) holds connections to
both. A standalone authorization server needs only `PFA_OAUTH_DATABASE_URL`.

**Upgrading an existing installation.** `make migrate` (`pfa.scripts.migrate`)
runs these steps in order:

1. Create the OAuth database if missing (never in production, where it must be
   provisioned) and migrate it.
2. Move any OAuth rows still in the application database
   (`pfa.scripts.move_oauth_data`). This locks the source tables against writes,
   copies in foreign-key order, verifies every row, and only then empties the
   source. It is idempotent, and it attaches moved rows to a client already
   registered on the OAuth side.
3. Migrate the application database. Its revision `0016` drops the old tables,
   and refuses while they still hold rows.

A downgrade of `0016` recreates the tables empty and does not copy rows back.

## Logging, audit and telemetry

The full inventory is in **`docs/logging-inventory.md`** (OWASP ASVS 5.0 V16): every
event, field, destination, retention and protection measure, plus the operator
decisions still required. Code: `pfa/observability/`; contract tests:
`tests/test_observability.py`.

* **Logs:** every process writes one JSON object per line to stdout. The format
  follows the OpenTelemetry log data model and semantic conventions (`timestamp`,
  `severity_text`, `body`, `event.name`, `service.name`, `trace_id`, `span_id`,
  `http.route`, `user.id`, `client.address`...); app attributes are under `pfa.*`.
  Development uses a readable, escaped one-line text format.
* **Audit:** every committed `audit_events` row is also an `audit` log event.
  Authentication is audited both ways (`account.login_succeeded` and
  `account.login_failed`), along with session revocations, token refreshes,
  replays, password resets and email changes.
* **Correlation:** `X-Correlation-Id` on every response and every audit row. It
  is propagated with W3C `traceparent` between the application and the OAuth
  server.
* **Traces and metrics:** OpenTelemetry. FastAPI, SQLAlchemy and httpx are
  instrumented; `pfa.events` counts every event and
  `pfa.worker.work.duration` times background work. Export is OTLP/HTTP via the
  standard `OTEL_*` variables.
* **Safety:**
  * no secrets, query strings or plain email addresses in logs;
  * no log injection;
  * logging never blocks a request (background writer with a bounded queue,
    `log.dropped` on overflow).

| Setting | Default | Meaning |
|---|---|---|
| `PFA_LOG_FORMAT` | `json` in staging/production, `text` in development/test | output format |
| `PFA_LOG_LEVEL` | `INFO` | minimum level (`DEBUG` adds health probes and CORS preflights) |
| `PFA_LOG_CLIENT_IP` | `full` | `full`, `anonymized` (IPv4 /24, IPv6 /48) or `off` (GDPR minimisation) |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | unset (no export; trace ids still in logs) | collector, e.g. `http://alloy:4318` |
| `OTEL_TRACES_SAMPLER`, `OTEL_TRACES_SAMPLER_ARG`, `OTEL_METRIC_EXPORT_INTERVAL`, `OTEL_RESOURCE_ATTRIBUTES`, `OTEL_SDK_DISABLED` | SDK defaults | standard OpenTelemetry configuration |

**Production checklist** (details and operator decisions in the inventory):

1. Run with `PFA_ENV=production`, collect stdout, and point
   `OTEL_EXPORTER_OTLP_ENDPOINT` at a collector.
   `observability/alloy/production-docker.alloy` is a validated Alloy pipeline for
   Docker hosts. It covers containers labelled `pfa.logs=true`, and takes the
   Loki and OTLP endpoints and credentials from the environment. On Kubernetes,
   use the Grafana `k8s-monitoring` Helm chart with the same processing.
2. Behind a reverse proxy, start uvicorn with `--proxy-headers
   --forwarded-allow-ips=<proxy address>`. Otherwise `client.address` and
   `audit_events.ip_address` hold the proxy's address.
3. Store logs and traces immutably (object lock), in a separate account, behind
   TLS and authentication, with role-based read access.
4. Decide and document the legal basis and retention (the inventory gives starting
   points), and keep hosts on NTP.
5. Alert, for example, on:
   * `sum(count_over_time({event=~"oauth.(code|refresh_token)_reuse_detected"}[5m])) > 0`
     (token replay);
   * `sum(count_over_time({event="audit"} | json | pfa_audit_action="account.login_failed" [5m])) > 20`
     (credential stuffing);
   * `sum(rate(http_server_request_duration_seconds_count{http_response_status_code=~"5.."}[5m])) / sum(rate(http_server_request_duration_seconds_count[5m])) > 0.02`;
   * `sum(count_over_time({event="job.failed", level="error"}[15m])) > 0`;
   * `sum(count_over_time({event="log.dropped"}[15m])) > 0`.

**Try it locally.**

```sh
make observability-up      # Alloy + Loki + Tempo + Prometheus + Grafana: http://127.0.0.1:3000 (admin/admin)
make dev-json-logs         # dev stack: JSON logs to .run/dev.log, OTLP traces/metrics to Alloy
```

The dashboard **PFA — audit, security and traffic** opens by default. You can:
* click a log line's trace link, or a latency exemplar, to open its trace (with
  every SQL statement);
* go from a trace back to its log lines;
* enter a correlation id to see every line of that request.

Regenerate the dashboard with `python observability/grafana/build_dashboard.py`.
Alloy's pipeline view is at http://127.0.0.1:12345.

## Security model

* **OAuth 2.0 Authorization Code + PKCE (S256)**; the SPA is a public client with
  no client secret. Email/password authentication happens on this service's own
  login page inside an authorization transaction; credentials never reach the SPA.
* **Cookie-free.** No application authentication reads or writes cookies, anywhere;
  credentialed CORS is disabled. Authorization transactions in PostgreSQL replace a
  login-session cookie.
* **Activation links are single-use, short-lived and replaceable.** A user whose
  link expired can request a new one at any time (`POST /auth/verify-email/resend`)
  following the OWASP token guidance: a 256-bit `secrets` token whose SHA-256 hash
  is the only stored form, an issuance that retires the previous unused link (one
  active link at a time) under a user row lock, generic responses for unknown or
  already-active addresses (no account enumeration), rate limiting per account and
  per source address, and no token or token-bearing URL in logs outside
  development/test. Delivered tokens are redacted from the outbox once the worker
  has processed the message. Full scan tests and the login-page path are in
  `backend/tests/test_auth_oauth.py` and `test_anaf_ops.py`.
* **Access tokens** are 5-minute Ed25519 JWTs with `aud=pfa-api`; **refresh tokens**
  are JWTs with `aud=pfa-auth-refresh` that rotate on every use under a row lock,
  never extend the 7-day absolute session lifetime, and revoke their whole family on
  reuse.
* **Signing keys live outside PostgreSQL.** `make keys` writes an Ed25519 key with
  mode `0600`; `make keys-rotate` retires the previous key after an overlap window
  so old tokens stay verifiable. Public keys are published at
  `/.well-known/jwks.json`.
* **Tenant isolation** uses PostgreSQL row-level security. The runtime role is not
  a superuser, does not own tables and has no `BYPASSRLS`, so policies always
  apply. Tenant context is `set_config(..., is_local => true)`, so it can never
  leak through a pooled connection. Composite foreign keys prevent cross-workspace
  references. Workers enumerate tenants through `SECURITY DEFINER` helpers that
  return identifiers only.
* **Roles are per membership.** `ADMIN` adds user management and nothing else; both
  roles see and edit every workspace document. `created_by` is audit metadata, not
  access control. The final active `ADMIN` cannot be removed, demoted or leave —
  enforced with a workspace row lock so simultaneous requests cannot both succeed.
* **Attachments** are size- and type-checked, active content is rejected, XML
  parsing disables external entities and DTDs, and downloads stream through
  bearer-authorized endpoints (never public object URLs).

## Fiscal rule packs

A rule pack records jurisdiction, income year, validity dates, publication date,
source URLs, legal article, parameter values, formula version, supported scenarios
and regression fixtures. Effective dates apply independently to income taxation,
VAT transactions and form schemas.

* `RO-INCOME-2025.1` — minimum wage 4,050 RON; CASS brackets 6/12/24 minimum wages
  with a 60-wage aggregate ceiling; CAS mandatory above 12 wages; VAT 19/9/5% until
  31 July 2025 then 21/11%; exemption threshold 300,000 RON until 31 August 2025
  then 395,000 RON.
* `RO-INCOME-2026.1` — same brackets with the **72-wage CASS ceiling**, the
  1 July 2026 minimum-wage change, and the 21/11% rates and 395,000 RON threshold
  carried forward.

Every pack, deadline rule and legal calendar in this repository is marked
`reviewed: false`. They are research baselines, **not approved executable tax
rules**. See `../IMPLEMENTATION_STATUS.md` and specification §14.

---

## Testing

```sh
make test       # isolated test DB + migrations + pytest
make lint       # ruff
```

`../tests/requirements.yaml` maps every requirement ID from specification §18
(and the §13 acceptance scenarios) to real test node ids; the repository-root
`make check-requirements` fails when an `implemented` requirement has no mapped
test, when a mapped test does not exist, or when migrations do not apply. Coverage
percentages supplement this mapping; they do not prove completeness.

The fiscal fixtures are written by hand from the rule-pack parameters and legal
rules, not produced by the calculator they test. Monetary assertions use `Decimal`
and compare exactly. Time is frozen and external network calls are replaced by the
deterministic fake ANAF service.

---

## What is deliberately unavailable

Unavailable capabilities are visible in the product rather than silent:

* forms without implemented detection (D112, D100, D205, D207, D710, D307, D311,
  D398) are returned by `GET /api/v1/workspaces/{w}/obligations` under
  `unassessed_forms`, each with an explanation and a pointer to the assisted
  workflow;
* D406/SAF-T is evaluated as `not_required` for a PFA and cites OPANAF 407/2025;
* when the official form/year schema is not published, generation produces a file
  labelled as an estimate/workpaper and automated submission stays blocked;
* automated ANAF submission requires a *verified* `connector_capabilities` row;
  without one, validated export plus receipt import remain available and the reason
  is shown.

See `../IMPLEMENTATION_STATUS.md` for the full gap list.
