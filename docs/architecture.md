# PFA Accounting — Architecture

These diagrams are taken from the code, not from the READMEs. Each one names the
modules it was drawn from so it can be checked and kept up to date.

| # | Diagram | Main sources |
|---|---|---|
| 1 | [High-level architecture](#1-high-level-architecture) | `backend/pfa/main.py`, `wiring.py`, `worker.py`, `db.py`, `frontend/src/api/client.ts` |
| 2 | [Deployment shapes: monolith vs split](#2-deployment-shapes-monolith-vs-split) | `backend/pfa/wiring.py`, `oauth/app.py`, `clients/authorization_server_http.py` |
| 3 | [Main request flow: create a document](#3-main-request-flow-create-a-document) | `api/routes/accounting.py`, `api/deps.py`, `oauth/introspection.py`, `services/documents.py` |
| 4 | [Supporting flow: sign-in (OAuth code + PKCE)](#4-supporting-flow-sign-in-oauth-code--pkce) | `frontend/src/auth/AuthController.ts`, `backend/pfa/oauth/routes.py` |
| 5 | [Supporting flow: background worker](#5-supporting-flow-background-worker) | `backend/pfa/worker.py`, `services/audit.py` |
| 6 | [Backend component dependencies](#6-backend-component-dependencies) | `import` statements under `backend/pfa/` |
| 7 | [Frontend component dependencies](#7-frontend-component-dependencies) | `import` statements under `frontend/src/` |

---

## 1. High-level architecture

There are two independent projects. `frontend/` is a React SPA that acts as a
public OAuth client. `backend/` is a FastAPI modular monolith plus a separate
worker process. They talk only over HTTP: CORS without credentials, bearer tokens,
and no cookies.

```mermaid
flowchart LR
    user(["User (browser)"])

    subgraph FE["frontend/ — React SPA (Vite, :5173)"]
        spa["App.tsx + pages/*"]
        auth["AuthController<br/>tokens in memory,<br/>refresh token in sessionStorage"]
        client["api/client.ts<br/>apiFetch (credentials: omit)"]
    end

    subgraph BE["backend/ — Python"]
        subgraph API["API process: pfa.main:create_app (:8000)"]
            mw["Middleware<br/>OTel · RequestLogging · SecurityHeaders · CORS"]
            approutes["Application routers<br/>pfa.api.routes.*"]
            oauthpkg["Authorization server<br/>pfa.oauth.routes"]
            docs["/docs (vendored Swagger UI)<br/>/openapi.json"]
        end
        worker["Worker process<br/>pfa.worker (outbox · jobs · hourly reconcile)"]
    end

    subgraph PG["PostgreSQL"]
        appdb[("Application DB<br/>PFA_DATABASE_URL<br/>row-level security")]
        oauthdb[("OAuth DB<br/>PFA_OAUTH_DATABASE_URL")]
    end

    subgraph FS["Local filesystem (backend/.run, keys/)"]
        keys[["Ed25519 signing keys<br/>PFA_KEY_DIR (PEM)"]]
        objects[["Encrypted object store<br/>AES-GCM, content-addressed"]]
        mail[["E-mail capture sink<br/>PFA_EMAIL_DIR (JSON files)"]]
    end

    anaf["ANAF transport<br/>fake driver (default) or<br/>HttpsAnafTransport placeholder (refuses)"]
    otel["OTLP collector (optional)<br/>Alloy → Loki / Tempo / Prometheus"]

    user --> spa
    spa --> auth
    spa --> client
    auth -- "redirect to /oauth/authorize,<br/>POST /oauth/token" --> mw
    client -- "Bearer JWT · /api/v1/*" --> mw
    mw --> approutes
    mw --> oauthpkg
    mw --> docs

    approutes --> appdb
    approutes --> objects
    oauthpkg --> oauthdb
    oauthpkg --> keys

    worker --> appdb
    worker --> objects
    worker --> mail
    worker --> anaf

    API -. "logs/traces/metrics" .-> otel
    worker -. "logs/traces/metrics" .-> otel
```

Notes, each checked against the code:

* `pfa/main.py` mounts the application routers and the OAuth routers on one ASGI
  app. They are connected only through ports configured by `pfa/wiring.py`.
* `pfa/db.py` sets the RLS tenant context with
  `set_config('app.workspace_id' | 'app.actor' | 'app.user_id', …, true)`. The
  context is transaction-local.
* `pfa/anaf/__init__.py:get_transport()` returns `FakeAnafTransport` when
  `anaf_driver == "fake"`. Otherwise it returns `HttpsAnafTransport`, which refuses
  every call until a verified connector capability exists.
* `services/email.py:EmailSink` writes one JSON file per message. No SMTP sender
  is implemented.

---

## 2. Deployment shapes: monolith vs split

`pfa.wiring.wire(settings)` picks the adapters. The deciding setting is
`settings.authorization_server_is_remote` (`PFA_AUTHORIZATION_SERVER_URL` is set).

```mermaid
flowchart TB
    subgraph M["Monolith (default) — one process"]
        direction LR
        mApp["Application<br/>pfa.api.*"] -- "AuthorizationServerClient<br/>= InProcessAuthorizationServer" --> mAS["pfa.oauth"]
        mAS -- "IdentityProvider = AccountsIdentityProvider<br/>AuditSink = AuditLogSink" --> mApp
    end

    subgraph S["Split — two processes"]
        direction LR
        sApp["Application (:8000)<br/>pfa.main + internal_identity router"]
        sAS["Authorization server (:8001)<br/>pfa.oauth.app:create_authorization_server_app"]
        sApp -- "HttpAuthorizationServer<br/>POST /oauth/introspect<br/>POST /oauth/subjects/{id}/sessions/revoke" --> sAS
        sAS -- "HttpIdentityProvider<br/>POST /internal/identity/authenticate" --> sApp
        sAS -. "LoggingAuditSink<br/>(pfa.oauth.audit logger)" .-> sLog[["stdout logs"]]
    end
```

In split mode both sides authenticate each other with
`PFA_INTERNAL_SERVICE_TOKEN` (`pfa/http/service_auth.py`, `pfa/http/service_client.py`).
The application then serves no `/oauth/*` routes and holds no signing keys.

---

## 3. Main request flow: create a document

This is the request behind the Income and Expenses pages:
`POST /api/v1/workspaces/{workspace_id}/documents`, sent by
`frontend/src/pages/Income.tsx` through `apiFetch`. It shows the path every
authenticated, workspace-scoped write takes: token introspection, account check,
membership check, RLS tenant session, service, repository, audit, then commit.
The diagram shows the default monolith wiring.

```mermaid
sequenceDiagram
    autonumber
    participant SPA as SPA page<br/>(Income.tsx)
    participant C as api/client.ts<br/>apiFetch
    participant MW as Middleware<br/>(OTel, RequestLogging,<br/>SecurityHeaders, CORS)
    participant R as accounting router<br/>create_document
    participant D as api/deps.py
    participant ASrv as InProcessAuthorizationServer<br/>→ oauth.introspection
    participant ODB as OAuth DB
    participant S as services/documents.py
    participant RP as repo/documents.py
    participant AU as services/audit.py
    participant DB as Application DB (RLS)

    SPA->>C: apiFetch(path, {method: POST, body})
    C->>MW: POST /api/v1/workspaces/{ws}/documents<br/>Authorization: Bearer JWT, credentials omit
    MW->>MW: bind correlation id, start server span
    MW->>R: route + validate DocumentPayload (pydantic)

    rect rgb(240,245,255)
    note over R,DB: Dependency resolution (bearer_principal is resolved once per request)
    R->>D: bearer_principal()
    D->>ASrv: introspect(access_token)
    ASrv->>ASrv: decode_token (Ed25519 sig, aud, exp, use=access)
    ASrv->>ODB: sessions.is_active(sid)
    ODB-->>ASrv: active
    ASrv-->>D: AccessGrant(user_id, session_id, scope, jti)
    D->>DB: session() → accounts.find_active_user(user_id)
    DB-->>D: user
    D->>D: require_scope(pfa.read, pfa.write)
    D->>DB: user_session(user_id) → tenancy.is_active_member(ws, user)
    DB-->>D: true (otherwise 403 PermissionDenied)
    D->>DB: tenant_session(ws, actor=user)<br/>set_config app.workspace_id/actor/user_id
    D->>DB: SELECT app.current_workspace()
    DB-->>D: ws (otherwise TenantContextError)
    end

    R->>S: create_document(session, workspace_id, actor_user_id, payload)
    S->>RP: period_is_closed / find_duplicates / find_by_external_id
    RP->>DB: SELECT … (RLS-filtered)
    DB-->>RP: rows
    alt period closed, duplicate, or number in use
        S-->>R: raise Conflict / ValidationFailed
        R-->>MW: problems.py → RFC 9457 problem+json (409/422)
    else valid
        S->>S: compute line net/VAT/gross (Decimal)
        S->>RP: insert_document + insert_line × N
        RP->>DB: INSERT documents, document_lines
        S->>AU: record_audit(action=document.created)
        AU->>DB: INSERT audit_events (append-only)
        AU->>AU: queue audit log record in session.info
        S-->>R: document row
        R->>D: dependency exit
        D->>DB: COMMIT
        note over AU,DB: SQLAlchemy after_commit hook emits the "audit" log event
        R-->>MW: 201 DocumentView
    end
    MW-->>C: response + X-Correlation-Id, security headers

    alt 401 and a refresh token is held
        C->>C: authController.refresh() (single-flight)
        C->>MW: retry once, same Idempotency-Key if one was set
    else non-2xx
        C-->>SPA: throw ApiError(ProblemDetails)
    else 2xx
        C-->>SPA: parsed JSON
    end
```

Other workspace-scoped mutations follow the same skeleton. They swap in a
different `deps` scope (`admin_workspace_session`, `declaration_scope`,
`job_scope`, …) and service. Some, such as declaration export or submission, also
wrap the service call in `services/idempotency.execute_idempotent` or write to
the outbox with `emit_event` / `enqueue_job`.

---

## 4. Supporting flow: sign-in (OAuth code + PKCE)

The access token used in diagram 3 comes from this flow. No cookies are
involved: the authorization transaction is stored in the OAuth DB.

```mermaid
sequenceDiagram
    autonumber
    participant U as User
    participant A as AuthController (SPA)
    participant O as pfa.oauth.routes
    participant F as pfa.oauth.flows
    participant IdP as IdentityProvider port<br/>(AccountsIdentityProvider)
    participant ODB as OAuth DB
    participant DB as Application DB

    U->>A: click "Autentificare"
    A->>A: create verifier, state, S256 challenge<br/>store round-trip in sessionStorage
    A->>O: redirect GET /oauth/authorize?client_id&redirect_uri&code_challenge&state
    O->>F: create_authorization_transaction
    F->>ODB: INSERT transaction
    O-->>U: server-rendered login page (transaction id + one-use proof)
    U->>O: POST /auth/authorization-transactions/{id}/authenticate
    O->>F: authenticate_transaction
    F->>IdP: authenticate(email, password)
    IdP->>DB: accounts.check_credentials (Argon2id, rate limit, audit)
    IdP-->>F: Subject(id) or LoginRejected
    F->>ODB: issue_authorization_code
    O-->>A: 303 redirect to /auth/callback?code&state
    A->>A: verify state, check round-trip age (5 min max)
    A->>O: POST /oauth/token (code, code_verifier)
    O->>F: exchange_code
    F->>ODB: consume code, create session
    O-->>A: access JWT (5 min) + refresh JWT
    A->>A: tokenMemory.set, scrub URL
    A->>O: GET /api/v1/me (Bearer)
    Note over A,O: /api/v1/me is served by the application router me.py,<br/>using the same auth path as diagram 3
```

---

## 5. Supporting flow: background worker

`pfa/worker.py` handles every asynchronous side effect that request handlers
schedule through the transactional outbox (`emit_event`) and the job queue
(`enqueue_job`) in `services/audit.py`.

```mermaid
flowchart TD
    loop["Worker.run_forever<br/>poll every 2 s"] --> ob["process_outbox_once<br/>app.lease_outbox(worker, 20, 120 s)"]
    loop --> jb["process_jobs_once<br/>lease jobs (300 s)"]
    loop --> rc{"≥ 1 h since<br/>last reconcile?"}

    ob --> obEmail{"event_type<br/>email.*?"}
    obEmail -- yes --> mail["render template →<br/>EmailSink (JSON file)"]
    obEmail -- no --> obWs["tenant_session(ws, actor=worker)<br/>membership.removed ·<br/>declaration.approval_invalidated"]
    mail --> obDone["worker_session: mark processed<br/>(redact e-mail payload)"]
    obWs --> obDone
    ob -. "on error" .-> retry["exponential backoff + jitter<br/>(final after 8 attempts)"]

    jb --> kind{"job.kind"}
    kind -- "obligations.reconcile" --> recon1["obligations + notifications services"]
    kind -- "efactura.transmit" --> ef["anaf.efactura.transmit_invoice"]
    kind -- "anaf.submit_declaration" --> sub["declaration_service + ANAF transport"]
    kind -- "register.export · anaf.sync ·<br/>notifications.email" --> stub["placeholder results"]
    ef --> transport["get_transport()<br/>fake / https placeholder"]
    sub --> transport
    jb -. "on error" .-> retry

    rc -- yes --> all["list_workspace_ids (SECURITY DEFINER)<br/>→ per-workspace tenant_session"]
    all --> eval["evaluate_obligations +<br/>evaluate_notifications<br/>(current and previous year)"]
```

---

## 6. Backend component dependencies

Arrows mean "imports". `tests/test_architecture.py` enforces these layering
rules:

* routers never import `pfa.db`, repositories or SQLAlchemy;
* only `pfa.main` and `pfa.wiring` import both `pfa.oauth` and the application;
* `pfa.oauth` imports shared infrastructure only.

```mermaid
flowchart TB
    subgraph Composition["Composition roots"]
        main["pfa.main"]
        wiring["pfa.wiring"]
        workerm["pfa.worker"]
        oauthapp["pfa.oauth.app<br/>(split mode)"]
    end

    subgraph AppHTTP["Application HTTP layer"]
        routes["pfa.api.routes<br/>accounts · me · workspaces ·<br/>accounting · compliance · anaf ·<br/>dev · internal_identity"]
        deps["pfa.api.deps<br/>units of work + scopes"]
        schemas["pfa.api.schemas"]
    end

    subgraph Domain["Application domain"]
        services["pfa.services<br/>documents · settlements · assets · books ·<br/>declarations · declaration_service · obligations ·<br/>notifications · deadlines · tenancy · accounts ·<br/>fiscal_profile · facts · dashboard · anaf_connections ·<br/>idempotency · audit · storage · email · form_schemas"]
        anafpkg["pfa.anaf<br/>base · fake · efactura"]
        fiscal["pfa.fiscal<br/>money · rules · engine · vat ·<br/>calendar · fx · packs (ro_2025, ro_2026)"]
        repo["pfa.repo<br/>hand-written SQL per domain"]
        clients["pfa.clients<br/>AuthorizationServerClient<br/>(in-process / HTTP)"]
    end

    subgraph AuthZ["Authorization server (separable)"]
        oauth["pfa.oauth<br/>routes · flows · sessions ·<br/>introspection · repo · keys · ports"]
    end

    subgraph Shared["Shared infrastructure"]
        db["pfa.db"]
        security["pfa.security<br/>jwt · keys · passwords ·<br/>tokens · ratelimit · scopes"]
        http["pfa.http<br/>problems · security_headers ·<br/>service_auth · service_client"]
        obs["pfa.observability"]
        core["pfa.config · errors · clock"]
        models["pfa.models<br/>(schema + enums)"]
        query["pfa.repo.query"]
    end

    main --> routes
    main --> oauth
    main --> wiring
    main --> http
    main --> obs
    wiring --> clients
    wiring --> oauth
    wiring --> services
    oauthapp --> oauth
    oauthapp --> http
    workerm --> services
    workerm --> anafpkg
    workerm --> repo

    routes --> deps
    routes --> schemas
    routes --> services
    routes --> anafpkg
    deps --> clients
    deps --> services
    deps --> db

    services --> repo
    services --> fiscal
    services --> clients
    services --> models
    services --> security
    anafpkg --> services
    repo --> query

    oauth --> db
    oauth --> security
    oauth --> http
    oauth --> query
    oauth --> obs

    clients --> http
    services --> core
    oauth --> core
```

Observations from the imports:

* `pfa.fiscal` imports nothing outside itself. It has no HTTP, database or UI
  dependency, which keeps it unit-testable.
* `pfa.repo.*` modules import only `pfa.repo.query`. Services choose the queries,
  and repos run them.
* `pfa.anaf.efactura` imports `services.audit`, `services.deadlines`,
  `services.storage` and `services.xmlutil`. ANAF and services therefore depend on
  each other in both directions (`services.anaf_connections` imports
  `anaf.efactura`).
* `services.declaration_service` holds the declaration workflow (approve, export,
  submit). `services.declarations` holds the calculation and generators on top of
  `pfa.fiscal`.

---

## 7. Frontend component dependencies

The router is hand-rolled: `App.tsx` switches on `window.location.pathname`.
`src/api/schema.ts` is generated from `openapi.json` (`npm run generate:types`),
but no module imports it yet.

```mermaid
flowchart TB
    main["main.tsx"] --> app["App.tsx<br/>path-based routing, workspace context, i18n"]

    subgraph Public["Public pages (no workspace)"]
        login["Login"]
        callback["Callback"]
        notice["AuthorizationOriginNotice"]
        signup["Signup"]
        verify["VerifyEmail"]
        reset["PasswordReset"]
        invite["AcceptInvitation"]
        wsSetup["WorkspaceSetup"]
    end

    subgraph Workspace["Workspace pages (inside Layout)"]
        home["Home"]
        income["Income"]
        expenses["Expenses"]
        settle["Settlements"]
        decl["Declarations"]
        settings["Settings"]
    end

    subgraph Comp["components/"]
        layout["Layout"]
        forms["FiscalIdentityForm · FiscalRegimeForm ·<br/>TaxFactsForm"]
        docui["VatFields · CountrySelect ·<br/>ForeignInvoiceHint · StatusBadge"]
    end

    subgraph Infra["Infrastructure"]
        useApi["api/useApi"]
        client["api/client<br/>apiFetch · downloadArtifact · ApiError"]
        devmail["api/devMailbox"]
        authc["auth/AuthController"]
        tokens["auth/tokens<br/>memory + sessionStorage"]
        util["i18n · labels · format · countryCodes"]
    end

    app --> Public
    app --> layout
    app --> Workspace
    app --> client
    app --> authc

    login --> authc
    callback --> authc
    notice --> authc
    signup --> devmail
    verify --> devmail
    signup --> client
    verify --> client
    reset --> client
    invite --> client
    wsSetup --> client

    Workspace --> useApi
    Workspace --> client
    Workspace --> docui
    settings --> forms
    forms --> client
    Workspace --> util

    useApi --> client
    devmail --> client
    client --> authc
    client --> tokens
    authc --> tokens
    authc -. "apiUrl()" .-> client
```

`api/client.ts` and `auth/AuthController.ts` import each other.
`apiFetch` calls `authController.refresh()` on a 401. `AuthController` uses
`apiUrl()` to reach `/api/v1/me` and `/api/v1/sessions/logout` on the API origin.
