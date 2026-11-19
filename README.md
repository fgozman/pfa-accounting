# PFA Accounting

Multi-tenant application for a Romanian **persoană fizică autorizată**: record
business activity, keep accounting records, calculate tax obligations and prepare
the declarations that apply to the taxpayer's profile.

The interface is Romanian-first with an English translation available.

This repository is organised as **two independent projects** plus a small set of
shared, product-level artifacts:

| Project | What it is | Documentation |
|---|---|---|
| [`backend/`](backend/) | Python FastAPI services, durable worker, PostgreSQL schema with row-level security, deterministic fiscal engine | [`backend/README.md`](backend/README.md) |
| [`frontend/`](frontend/) | React + TypeScript SPA: a static OAuth public client | [`frontend/README.md`](frontend/README.md) |

The two projects share **no source, configuration or scripts**. They integrate
over HTTP with the OAuth 2.0 authorization-code + PKCE flow and an explicit CORS
allowlist. Each project builds, tests and runs on its own.

---

## Architecture at a glance

| Part | Entry point | Notes |
|---|---|---|
| SPA | `frontend/src/main.tsx` | React 18 + Vite (`:5173`); path-based routing in `App.tsx`; calls the API origin directly (no dev proxy) |
| API | `backend/pfa/main.py` (`create_app`) | FastAPI modular monolith (`:8000`): application routers (`pfa/api/routes/`) and the OAuth 2.0 authorization server (`pfa/oauth/`) mounted side by side |
| Worker | `backend/pfa/worker.py` | Transactional outbox, durable jobs with backoff, hourly obligation/notification reconciliation |
| Application DB | `PFA_DATABASE_URL` | PostgreSQL with row-level security; tenant context is transaction-local |
| OAuth DB | `PFA_OAUTH_DATABASE_URL` | Separate database for clients, codes, sessions and refresh tokens; own migrations (`migrations_oauth/`) |
| Files | `backend/.run/`, `PFA_KEY_DIR` | AES-GCM encrypted object store, captured e-mails, Ed25519 signing keys (never in PostgreSQL) |
| ANAF | `backend/pfa/anaf/` | Deterministic fake driver by default; the HTTPS transport is a placeholder that refuses until a connector capability is verified |

The authorization server can also run as its own service (split mode,
`make -C backend auth-server` on `:8001`, enabled by
`PFA_AUTHORIZATION_SERVER_URL`). The two sides then talk over HTTP with a shared
`PFA_INTERNAL_SERVICE_TOKEN`. See the backend README for details.

Architecture diagrams (high-level, deployment shapes, request/sign-in/worker
flows, component dependencies) are in
**[`docs/architecture.md`](docs/architecture.md)**.

---

## Repository layout

```
backend/      Python project — own Makefile, pyproject.toml, .env, Compose files,
              migrations, scripts and tests (see backend/README.md)
frontend/     Node project — own Makefile, package.json, .env files, scripts and
              browser tests (see frontend/README.md)
docs/         cross-project documentation (architecture diagrams, Mermaid)
scripts/      integration tooling used by the root Makefile only:
              dev.py (both projects), e2e.py (full-stack Playwright),
              check_requirements.py (requirement-to-test traceability)
Makefile      integration orchestrator: wires the two projects together (optional)
specification.md          product specification (the shared source of truth)
IMPLEMENTATION_STATUS.md  honest, cross-project status and release gates
tests/requirements.yaml   requirement-to-test traceability for both projects
```

The root `Makefile` and `scripts/` exist only to run the two projects together
for full-product development and CI. Neither project depends on them.

---

## Quick start (full stack)

Prerequisites: Python 3.12 + [uv](https://docs.astral.sh/uv/), Node.js 22 + npm,
and Docker with the Compose plugin.

```sh
make setup        # backend virtualenv + dependencies + dev keys, then npm ci
make up           # development PostgreSQL
make migrate      # apply migrations (owner/DDL role)
make seed-dev     # synthetic data (refuses production configuration)
make dev          # API + worker + SPA, Ctrl-C stops all
make down         # stop PostgreSQL, keep the development volume
```

* API — <http://127.0.0.1:8000> (interactive docs at `/docs`)
* SPA — <http://127.0.0.1:5173>

No real email is sent in development: the worker captures each message to
`backend/.run/emails/` and prints the link. `make mail` shows the newest captured
message (link included).

Seeded development accounts (synthetic data only):

| Account | Password |
|---|---|
| `admin@example.test` | `Correct-Horse-Battery-9` |
| `member@example.test` | `Correct-Horse-Battery-9` |

`make dev` starts both projects and derives the SPA's public `VITE_*`
configuration from the backend settings, so the two origins cannot drift apart
locally. See `scripts/dev.py`.

To make a backend setting yours, edit `backend/.env` (copied from
`backend/.env.example`); the SPA's own local defaults live in
`frontend/.env.development`. Both are documented in the project READMEs.

### Run the projects separately

Each project is fully usable on its own:

```sh
# terminal 1 — backend
cd backend && cp .env.example .env && make setup && make up && make migrate && make seed-dev && make dev

# terminal 2 — frontend
cd frontend && npm ci && npm run dev
```

## Integration commands (root Makefile)

```sh
make help                 # every target, with a one-line description

# setup and database
make setup                # backend virtualenv + dev keys, then frontend npm ci
make up / make down       # start / stop the development PostgreSQL (volume kept)
make migrate              # migrate both databases (application + OAuth) as the owner role
make seed-dev             # idempotent synthetic seed (refuses production configuration)

# run
make dev                  # both projects (API + worker + SPA)
make dev-json-logs        # like dev, JSON logs to backend/.run/dev.log + OTLP to local Alloy
make dev-backend          # backend only (API + worker)
make dev-frontend         # frontend only
make observability-up     # local Alloy + Loki + Tempo + Prometheus + Grafana (:3000)
make observability-down   # stop the monitoring stack

# test and quality
make test                 # test-backend (pytest) + test-frontend (Vitest)
make e2e                  # full-stack Playwright (test DB, migrations, API, worker, SPA); alias: test-e2e
make check-requirements   # requirement-to-test traceability for both projects
make lint                 # ruff lint (backend + integration scripts)
make ci                   # lint + traceability + backend tests + frontend tests

# contract and assets
make openapi              # publish backend/openapi.json
make types                # publish OpenAPI, pin it in the frontend, regenerate TS types
make vendor-swagger-ui    # copy pinned Swagger UI assets into the backend

# misc
make mail                 # show the newest captured development e-mails (with the link)
make clean                # remove caches and build output (keeps volumes and .env)
```

`make e2e` and `make ci` are the only targets that need both projects; everything
else delegates to `make -C backend …` or `make -C frontend …`.

## Tests and traceability

| Suite | Where | Command |
|---|---|---|
| Backend unit/integration (pytest, isolated PostgreSQL) | `backend/tests/` | `make -C backend test` |
| Frontend component (Vitest + React Testing Library) | `frontend/src/**/*.test.tsx` | `make -C frontend test` |
| End-to-end (Playwright, desktop + mobile Chromium) | `frontend/e2e/` | `make e2e` |

`make e2e` runs on its own ports (API `:8010`, SPA `:5180`, test PostgreSQL
`:5433`) and never touches the development database or volume. Extra arguments
are passed to Playwright, for example
`python scripts/e2e.py app.spec.ts --project=desktop-chromium`.

`tests/requirements.yaml` maps every requirement ID from specification §18 (and
the §13 acceptance scenarios) to real test node ids in both projects.
`make check-requirements` fails when an `implemented` requirement has no mapped
test, when a mapped test does not exist, or when migrations do not apply. Coverage
percentages supplement this mapping; they do not prove completeness.

Development and tests never call the real ANAF service: they use the
deterministic fake driver (`PFA_ANAF_DRIVER=fake`) and a file-based e-mail capture
sink.

## Shared product artifacts

* **[`specification.md`](specification.md)** — the product specification
  (sections 1–18). Section 17 defines the required development interface;
  section 18 defines feature-coverage traceability.
* **[`IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md)** — what is
  implemented, partially implemented, or deliberately unavailable, and the
  external release gates that remain open.
* **[`tests/requirements.yaml`](tests/requirements.yaml)** — the machine-readable
  requirement-to-test mapping checked by `make check-requirements`.

## Fiscal and legal status

Every rule pack, deadline rule and legal calendar in this repository is marked
`reviewed: false`. They are **research baselines, not approved executable tax
rules**. Statutory rates, bases, deadlines and form schemas must be confirmed by a
Romanian tax professional before any output is relied upon. See
`IMPLEMENTATION_STATUS.md` and specification §14 for the open release gates.

## Security summary

* OAuth 2.0 authorization code + PKCE (S256); public SPA client, no client secret.
* Cookie-free: no application authentication reads or writes cookies, and
  credentialed CORS is disabled.
* Access token only in browser memory; the refresh token is kept in tab-scoped
  `sessionStorage` so a reload restores the session (never `localStorage`, a URL or
  a cookie). Signing keys (Ed25519) live outside PostgreSQL.
* Access tokens are checked on every request through token introspection, so
  logout and password reset take effect before the 5-minute JWT expires.
* OAuth data lives in its own database, separate from the application data.
* PostgreSQL row-level security with a non-superuser runtime role and
  per-transaction tenant context.
* Attachments are type/size checked, active content is rejected, XML parsing
  disables external entities, and downloads are bearer-authorized.

Details are in [`backend/README.md`](backend/README.md) and
[`frontend/README.md`](frontend/README.md).
