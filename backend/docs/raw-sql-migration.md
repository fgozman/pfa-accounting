# Raw-SQL data access: status and completion recipe

The data layer is being moved from the SQLAlchemy ORM to hand-written SQL so every
statement sent to PostgreSQL is visible, reviewable and tunable at the call site.
This file records the design, the exact inventory, and the mechanical recipe for
the modules that are not converted yet, so the remaining work is unambiguous.

## Why

* **Control.** Query shape, join strategy, window functions, `FOR UPDATE` scope and
  the exact column list are decisions, not side effects of an ORM default.
  `SELECT *` never appears in application SQL.
* **Cost visibility.** Nothing is loaded implicitly and nothing is lazy: a row is
  the columns the query asked for, and N+1 patterns cannot hide behind an
  attribute access.
* **Portability of knowledge.** The SQL in `pfa/repo/*` can be pasted into `psql`,
  reviewed by a DBA, or replaced by a materialised view without touching business
  logic.

## Layout

```
pfa/repo/query.py        the only helpers: Row, one/all_rows/val/col, returning/run,
                         and predicate/limit composition (structure only, never values)
pfa/repo/accounts.py     users, e-mail tokens, MFA factors
pfa/oauth/repo.py        every oauth_* table (OAuth database, pfa/oauth/db.py): clients, redirect/origin registrations,
                         authorization transactions/codes, sessions, refresh tokens
pfa/repo/tenancy.py      workspaces, memberships, invitations, taxpayers, grants
pfa/repo/documents.py    counterparties-less document core: invoices, lines, revisions,
                         attachments, numbering, duplicates
pfa/repo/money.py        financial accounts, payments, allocations
pfa/repo/books.py        accounting events, assets, depreciation, adjustments,
                         inventory, period locks, register exports
pfa/repo/registers.py    grouped register queries (outstanding, assets, cash journal,
                         member list, period-close checklist)
pfa/repo/compliance.py   obligations, notifications, deliveries, per-user state,
                         form versions
pfa/repo/anaf.py         connections, capabilities, submission attempts, receipts
pfa/repo/operations.py   audit trail, transactional outbox, durable jobs, idempotency
pfa/models/*             the schema definition the migrations are checked against
                         (tests/test_migrations.py); not used to execute queries
```

**181 hand-written SQL statements** live in this layer.

`Row` is a frozen mapping with attribute access, so call sites read
`document.number` without an identity map. A missing column raises
`AttributeError` naming the columns that *are* available — a wrong projection is
caught immediately instead of silently returning `None`.

## Rules for every statement

1. **Literal SQL at the call site.** No query builder, no string interpolation of
   values. Structure-only fragments (`where(...)`, `order_by(...)`,
   `limit_clause(...)`) compose predicates; values are always bound parameters.
2. **Explicit column list** in every `SELECT`, and explicit `RETURNING` on writes.
3. **Explicit casts** where the driver needs to be told the type:
   `CAST(:x AS jsonb)` (with `jsonb(...)` serialisation), `CAST(:x AS uuid)`,
   `= ANY(CAST(:ids AS uuid[]))`, `(:seconds || ' seconds')::interval`.
4. **Enums are strings.** Rows come back as plain strings, so compare with `==`
   and never with `is` (`UserStatus.ACTIVE` *is* the string `"active"`, but an
   identity comparison fails on a value read from the database). All 62 existing
   identity comparisons were converted.
5. **NOT NULL columns without a database default must be listed in every
   `INSERT`.** The ORM used to supply Python-side defaults (`default=False`,
   `default=0`) that do not exist in PostgreSQL. The authoritative checklist:

```sql
SELECT table_name, string_agg(column_name, ', ' ORDER BY ordinal_position)
  FROM information_schema.columns
 WHERE table_schema = 'public'
   AND is_nullable = 'NO'
   AND column_default IS NULL
   AND column_name NOT IN ('id', 'created_at', 'updated_at')
 GROUP BY table_name ORDER BY table_name;
```

6. **Writes return the new state** (`UPDATE ... RETURNING <columns>`) instead of
   mutating an in-memory object and relying on a flush.
7. **`RETURNING` that matches nothing is an error** (`repo.returning`) unless the
   statement is legitimately allowed to match nothing (`repo.returning_optional`).

## Status

Converted to raw SQL:

| Module | Notes |
|---|---|
| `services/accounts.py` | users, verification/reset tokens (sessions revoked via `oauth_sessions`) |
| `oauth/flows.py` | clients, authorization transactions/codes, token exchange and rotation |
| `oauth/sessions.py` | session status, revocation, logout |
| `services/tenancy.py` | workspaces, memberships (last-ADMIN invariant), invitations, taxpayer grants |
| `services/documents.py` | invoices, lines, revisions, numbering, duplicates, attachments |
| `services/settlements.py` | payments, allocations, owner movements, processor fees |
| `services/books.py` | registers, close/reopen, exports |
| `services/assets.py` | assets, depreciation schedules, disposals, inventory |
| `services/obligations.py` | obligation catalogue upsert with the deadline-revision rule |
| `services/notifications.py` | reminder stages, deduplicated deliveries, per-user state |
| `services/audit.py` | append-only audit, outbox, job enqueue |
| `worker.py` | job/attempt/declaration lookups |
| `api/deps.py` | membership checks |
| `oauth/routes.py`, `api/routes/*` | none left: handlers call services only (enforced by `tests/test_architecture.py`) |

Not converted yet (each is a mechanical rewrite using the recipe above):

87 sites remain (down from 211):

| File | ORM call sites |
|---|---|
| `services/declaration_service.py` | 20 |
| `scripts/seed_dev.py` | 19 |
| `api/routes/workspaces.py` | 13 |
| `services/declarations.py` | 11 |
| `api/routes/compliance.py` | 8 |
| `services/obligations.py` | 6 (reference-data reads only) |
| `api/routes/anaf.py` | 6 |
| `anaf/efactura.py` | 3 |
| `api/deps.py` | 1 |

The declaration lifecycle (`declaration_service.py`, `declarations.py`) and the seed
are the substantial remainder; the rest are route-level reads of the fiscal profile
and ANAF rows.

## Recipe for a module

1. Add the statements to the matching `pfa/repo/<domain>.py`, one function per
   statement, with an explicit column list and typed parameters.
2. Replace reads: `select(Entity).where(...)` → `repo.<domain>.<query>(...)`;
   `session.get(Entity, id)` → the same query by primary key.
3. Replace inserts: `session.add(Entity(...))` + `flush()` → `INSERT ... RETURNING`
   (checksum the NOT NULL checklist above).
4. Replace mutations: `entity.field = value` + `flush()` → `UPDATE ... RETURNING`.
5. Replace relationship access with an explicit join (and fix any N+1 the ORM hid).
6. Run `make -C backend test` from the repository root (or `make test` from
   `backend/`): the 168 tests are the behaviour contract; the query count tests in
   `tests/test_query_counts.py` guard the grouped queries.

## Query-count guarantees

`tests/test_query_counts.py` counts statements through an engine event
listener and asserts the cost is independent of the number of rows:

| Endpoint | Guarantee |
|---|---|
| `GET /registers/outstanding_payables` | one grouped query (was one settlement lookup per document) |
| `GET /assets` | one grouped query (was one depreciation query per asset) |
| `GET /members` | one join (was one account lookup per member) |
| `GET /registers/close-checklist` | one `NOT EXISTS` per check (was one attachment lookup per document) |
