# PFA Accounting — Frontend

The React + TypeScript single-page application for the Romanian PFA accounting
product. Romanian-first, with an English translation.

This is a **self-contained static client project**. It owns its `package.json`,
lockfile, `.env` files, build and browser tests. It never reads or starts the
backend: it talks to the Python web services over HTTP only, using the public
`VITE_*` configuration below. The backend is a separate project (`../backend/`,
see its README).

---

## Project layout

```
frontend/
  package.json          scripts and pinned dependencies
  package-lock.json     deterministic installs (`npm ci`)
  Makefile              all frontend commands (see `make help`)
  .env.example          template for the public VITE_* configuration
  .env.development      committed localhost defaults (no secrets)
  index.html            SPA entry document
  vite.config.ts        dev server + Vitest configuration
  playwright.config.ts  browser-test configuration (desktop + mobile Chromium)
  openapi.json          pinned OpenAPI contract downloaded from the backend
  scripts/
    sync-openapi.mjs    fetch the contract from a running API
    vendor-swagger-ui.mjs  copy pinned Swagger UI assets to a target dir
  src/
    main.tsx            application bootstrap
    App.tsx             routes and layout
    styles.css          application styles
    auth/               in-memory tokens, PKCE round trip, single refresh coordinator
    api/                bearer client, idempotency keys, authenticated blob downloads,
                        generated OpenAPI types (schema.ts)
    pages/              Login, Callback, Signup, Home, Income, Expenses,
                        Declarations, Settings, VerifyEmail, PasswordReset,
                        AcceptInvitation, WorkspaceSetup, AuthorizationOriginNotice
    components/         Layout, StatusBadge
    i18n/               Romanian-first copy with English translation
    test/               Vitest setup
  e2e/                  Playwright journeys (app, auth, docs)
```

## Prerequisites

* Node.js 22 (or a recent LTS) with npm
* A running backend stack for anything that talks to the API (dev, e2e)

## Quick start

All commands run from `frontend/`.

```sh
npm ci
npm run dev            # http://127.0.0.1:5173
```

`frontend/.env.development` already points at the default local backend
(`http://127.0.0.1:8000`). Start the backend separately
(`make -C ../backend dev`), or start both from the repository root with
`make dev`.

## Configuration

Configuration is **public and build-time only** (`import.meta.env`). Never put a
client secret here: the SPA is a public OAuth client
(`token_endpoint_auth_method=none`).

| Variable | Default (`.env.development`) | Meaning |
|---|---|---|
| `VITE_API_BASE_URL` | `http://127.0.0.1:8000` | Origin every API request goes to |
| `VITE_AUTHORIZATION_ORIGIN` | `http://127.0.0.1:8000` | Origin that renders the login page and issues tokens |
| `VITE_SPA_CLIENT_ID` | `pfa-web-dev` | Registered public OAuth client |
| `VITE_SPA_CALLBACK_URI` | `http://127.0.0.1:5173/auth/callback` | Registered redirect target, exactly |

Copy `.env.example` to `.env.local` (or set the variables in the deployment
environment) to point at a different backend. The same values must be registered
in the backend configuration (`PFA_SPA_ORIGIN`, `PFA_SPA_CALLBACK_URI`,
`PFA_SPA_CLIENT_ID`) for sign-in to work.

## Commands

```sh
make help              # every target, with a one-line description
```

| Target | What it does |
|---|---|
| `install` | `npm ci` from the lockfile |
| `dev` | Vite dev server on `127.0.0.1:5173` |
| `build` | Type-check (`tsc -b`) and bundle to `dist/` |
| `preview` | Serve the production bundle |
| `test` | Vitest component tests (single run) |
| `test-watch` | Vitest in watch mode |
| `typecheck` | `tsc -b --noEmit` |
| `e2e` | Playwright against a running stack |
| `types` | Regenerate `src/api/schema.ts` from the pinned `openapi.json` |
| `sync-openapi` | Fetch a fresh contract from a running API, then regenerate types |
| `clean` | Remove build output and local report directories |

Equivalent npm scripts are available (`npm run dev`, `npm run build`,
`npm run test`, `npm run test:e2e`, `npm run generate:types`,
`npm run sync:openapi`).

---

## How the SPA talks to the backend

The SPA is a **static client**: every request it makes goes to the web-service
origin, never to the origin that serves the application. There is **no dev-server
proxy**, on purpose — a proxy silently hides the real cross-origin contract, and
then production breaks. With direct calls, the browser performs the actual CORS
preflight locally:

```sh
# what the browser really sends before GET /api/v1/me (Authorization header):
OPTIONS /api/v1/me   Origin: http://127.0.0.1:5173
                     Access-Control-Request-Headers: authorization
```

Only two things legitimately belong to the SPA origin: the application itself and
the registered OAuth redirect target `/auth/callback` (a browser navigation, not
a service call). The code exchange that follows goes to the API origin.

The backend's static host must serve the SPA for application paths **including
`/auth/callback`**, while `/auth/register`, `/auth/verify-email`,
`/auth/password-reset/*`, `/oauth/token` and `/oauth/revoke` belong to the Python
service. `vite.config.ts` documents this explicitly: a blanket `/auth/` proxy rule
would answer the callback with the API's JSON 404 instead of the application.

### Account lifecycle

Sign-up, activation, password reset and invitations are all handled by pages that
render without a session, because the token in the URL is the proof:
`/cont/inscriere`, `/verify-email`, `/password-reset[/confirm]` and
`/invitations/accept`. Activation links are single-use and short-lived; when one
expires, `/verify-email` (also reachable from the sign-in page and from the
server-rendered authorization page) offers to send a fresh one. The API answers
generically, so the page never reveals whether an address is registered. Once the
token has been exchanged the SPA removes it from the address bar and history
(`history.replaceState`), so a one-use credential does not linger in a bookmark,
a screen share or a later `Referer`.

### Token storage policy

The **access token lives only in browser memory**. The **refresh token is kept in
tab-scoped `sessionStorage`** so reloading the page restores the session instead of
forcing a login — a deliberate policy documented in specification §7.4. Nothing is
written to `localStorage`, IndexedDB, a URL or a cookie, so nothing outlives the tab
and nothing is attached to requests automatically; closing the tab or restarting the
browser requires signing in again. A stolen refresh token is contained by single-use
rotation with whole-family revocation on reuse, the 7-day absolute session lifetime,
server-side session checks and the strict CSP. The temporary PKCE verifier and
`state` are the only other values in `sessionStorage`, and only for the redirect
round trip.

The client sends bearer tokens with `credentials: "omit"`, keeps financial values
as decimal strings (never JavaScript numbers), reuses one idempotency key across a
retry, and downloads attachments through an authenticated fetch followed by a
local Blob download.

### Internationalisation

Copy is Romanian-first (`src/i18n/`), with an English translation selected in
Settings. Form codes are always secondary to a readable name.

---

## OpenAPI types

`openapi.json` is the **pinned contract** downloaded from the backend;
`src/api/schema.ts` is generated from it and committed. The build does not need a
running API.

```sh
make types             # regenerate from the pinned contract
```

To refresh the contract from a running backend (a plain HTTP call — the frontend
does not read the backend's filesystem):

```sh
make sync-openapi OPENAPI_URL=http://127.0.0.1:8000/openapi.json
```

The repository-root `make types` does the whole round trip: publish the contract
from the backend, update the pinned copy here, and regenerate types.

> The pinned `swagger-ui-dist` devDependency exists only to vendor the backend's
> self-hosted Swagger UI assets. That copy step is integration tooling:
> `make vendor-swagger-ui` at the repository root sets `SWAGGER_UI_TARGET_DIR` to
> the backend static directory.

---

## Testing

```sh
make test              # Vitest + React Testing Library (31 tests)
make typecheck         # TypeScript project check
```

The Playwright journeys (`e2e/`) need a running stack: API + worker + SPA with
the deterministic fake ANAF driver and file-based email capture. They are
configured entirely by environment variables (activation links are read from the
API's development mailbox, `GET /api/v1/dev/mailbox`, never from files):

| Variable | Meaning |
|---|---|
| `E2E_BASE_URL` | SPA origin the browser opens |
| `E2E_API_ORIGIN` | API origin for direct setup requests |

```sh
# against a stack you started yourself
E2E_BASE_URL=http://127.0.0.1:5180 \
E2E_API_ORIGIN=http://127.0.0.1:8010 \
make e2e
```

The repository-root `make e2e` starts the whole stack (isolated test database,
migrations, API, worker, SPA) and then runs this suite — that is the supported
way to run it end to end.

## Build and deploy

```sh
make build             # dist/ — static files, no server-side rendering
```

Serve `dist/` as static files and configure a history fallback so application
paths resolve to `index.html`. The registered callback URI
(`/auth/callback`) must be served by the static host, not the API.

Because activation and reset links carry a one-use token in the query string, the
host must send these headers (the dev server already sets `Referrer-Policy`):

```
Referrer-Policy: no-referrer
Cache-Control: no-store      # for /verify-email and /password-reset/confirm
```

`index.html` also carries `<meta name="referrer" content="no-referrer">` as a
baseline, and the application loads no third-party resources.
