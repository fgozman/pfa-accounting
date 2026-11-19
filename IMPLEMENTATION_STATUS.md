# Implementation status

Baseline: specification v1.1 (research baseline 16 September 2026).
This file is the honest status record: what is implemented, what is partially
implemented, and what is deliberately unavailable. Unavailable capabilities are
*visible in the product* (see "Visibility of gaps"), never silently absent.

`tests/requirements.yaml` is the machine-readable version of this table, and
`make check-requirements` fails when an `implemented` requirement loses its tests.

| Requirement | Status | Notes |
|---|---|---|
| IDENTITY | implemented | Argon2id, generic responses, hashed single-use tokens, rate limits, session revocation on reset |
| OAUTH | implemented | Code + PKCE S256, cookie-free, single-use codes, rotating refresh with family revocation, JWKS, key rotation overlap |
| MEMBERS | implemented | Two roles only, invitations, last-ADMIN invariant under a workspace row lock, removal effects |
| SHARING | implemented | All workspace documents shared with every active member; creator is audit metadata |
| PROFILE | partial | Versioned fiscal profile, VAT registrations, regimes and year facts; the regime (real/norm) and VAT registration are selectable in Setări and stored as effective-dated periods; CAEN periods stored but not surfaced as an onboarding checklist |
| DOCUMENTS | partial | Numbering, immutability, credit notes, duplicate review, attachments; only manual entry and e-Factura XML import are wired end to end (no generic CSV/OCR importer) |
| SETTLEMENTS | partial | Partial payments, cross-year splits, advances, owner movements, concurrency guard, foreign currency; processor gross/net service exists but has no HTTP endpoint yet |
| BOOKS | partial | Cash journal, VAT journals, asset/inventory registers, receivables/payables, close/reopen; Registrul-inventar and Registrul de evidență fiscală are derived from the same rows without separate official formatting |
| REAL | implemented | Cash recognition, per-expense deductibility and business use, depreciation, loss carryforward, contribution deductions, exemptions, threshold boundaries, whole-RON rounding |
| NORM | implemented | Published norm, adjustment factors, proration, final tax (contributions not deducted), missing-norm handling |
| VAT | implemented | Place of supply, tax point vs cash date, reverse charge, art. 316 vs 317, rate transitions, registration turnover, D301/D390 split |
| THRESHOLDS | implemented | 2025 vs 2026 packs pin different CASS ceilings; historical packs are never rewritten |
| OBLIGATIONS | implemented | `required` / `not_required` / `needs_information` with rule version, reasons, missing inputs, deadlines, capabilities; nil returns; late entry re-evaluates historical periods |
| FORMS | partial | Adaptors for D212, D300, D301, D390, D394; submission-ready output requires an approved rule pack **and** an official-validator fixture, which are release-gated |
| DECISIONS | implemented | Lifecycle, immutable snapshots and versions, approval invalidation on input/artifact change, linked corrections, receipt binding |
| ANAF | partial | e-Factura generation/validation/transmission wired to a transport interface with durable jobs, backoff and unknown-delivery reconciliation; HTTPS connector inert until a verified capability exists |
| NOTIFICATIONS | implemented | Persisted obligations + per-user read/dismiss/snooze, deduplicated deliveries, catch-up suppression, one-use-per-revision keys, per-form deadline strategies, working-day arithmetic from the versioned calendar |
| UI | partial | Five primary navigation entries, workspace switcher, Romanian formatting, keyboard/mobile operation, loading/error/empty states; Playwright suite requires a running stack (`make e2e`) |
| API/DATA | implemented | OpenAPI + generated TS types, decimal strings, problem details with correlation ids, cursor-ish bounded lists, idempotency, optimistic concurrency, RLS, composite FKs, append-only audit |
| OPERATIONS | implemented | Transactional outbox, leased idempotent jobs, backoff with jitter, worker restart safety, migration check, isolated Compose test project, email capture sink |
| EXTENSIONS | external | Payroll, withholding, special VAT and special schemes are catalogued and routed to an assisted workflow; they must gain full coverage before being advertised |

## Release gates that are not yet closed

These are external, legally gated steps. The code marks them explicitly rather
than pretending they are done:

1. **Rule-pack approval.** Every rule pack, deadline rule and legal calendar
   carries `reviewed: false` / `professionally_reviewed: false`. A Romanian tax
   professional must confirm statutory rates and base definitions, minimum-wage
   values and dates, deduction and loss rules, county/activity norm tables,
   scenario predicates, filing and payment calendars, official form row mappings,
   correction rules and retention requirements.
2. **Official form schemas and validators.** `form_versions` rows are seeded with
   `supported: false` except where a fixture is recorded. Until a form/year has an
   approved schema plus an accepted official-validator fixture, generation emits a
   file labelled as an estimate/workpaper and submission is blocked.
3. **Submission channels.** Automated submission is enabled per form only when a
   verified `connector_capabilities` row records the endpoint/channel,
   environment, signing requirements, delegation, schema/validator version and
   receipt behaviour. Until then, validated export plus receipt import is the
   supported path and the reason is returned to the user.
4. **Security review.** AUTH-01…AUTH-05 and the TEN tests are the preconditions
   for that review, not the review itself.
5. **Real ANAF connectivity.** No production ANAF credentials or certificates are
   in this repository. Local development, tests and the e2e suite use the
   deterministic fake service (`PFA_ANAF_DRIVER=fake`).

## Visibility of gaps

| Where | What it shows |
|---|---|
| `GET /api/v1/workspaces/{w}/obligations` → `unassessed_forms` | Forms whose detection is not implemented, with a reason and `detection_available: false` |
| obligation `capabilities` + `unsupported_reason` | Which of detect / calculate / generate / submit is unavailable and why |
| declaration `estimate_only` and artifact `is_preview` | That the file is a workpaper and cannot be filed |
| declaration `submission_available` / `submission_unavailable_reason` | Why "Trimite la ANAF" is disabled, with the export+receipt path instead |
| `Termen de verificat` | An unverified deadline, never an invented date |
| dashboard `estimated_taxes` | Clearly labelled as an internal estimate, not an ANAF balance |
| notification `rejection_errors` | ANAF's own validation messages on a rejected filing |

## Data-layer refactor in progress

The data layer is being converted from the SQLAlchemy ORM to hand-written SQL for full
query control. **181 SQL statements** now live in `pfa/repo/*`; **87 ORM call sites
remain** (from 211). Converted: accounts, oauth, tenancy, documents, settlements,
books, assets, obligations, notifications, audit/outbox/jobs, the worker's job
lookups, `api/deps.py` and the authorization pages. Remaining: the declaration
lifecycle, the development seed and some route-level profile/ANAF reads — inventory
and recipe in `backend/docs/raw-sql-migration.md`.

Responses are typed (`pfa/api/schemas/responses.py`, 51 schemas) for identity,
workspaces, members, documents and their lines, settlements, obligations,
notifications and the dashboard; the remaining endpoints still return untyped objects.

## Known implementation gaps

* Generic CSV import and OCR review are modelled (`document_imports`,
  `require_ocr_review`) but not exposed as endpoints; e-Factura XML import is.
* Processor gross-versus-net settlement exists in the service layer
  (`record_processor_settlement`) without an HTTP route.
* `Registrul-inventar` and `Registrul de evidență fiscală` are not separately
  formatted official outputs.
* The in-process rate limiter must be backed by a shared store for multi-instance
  deployments (`pfa/security/ratelimit.py` documents this).
* Verification-resend and password-reset responses are generic but not
  constant-time: a pending account performs more database work than an unknown
  address, so latency could in principle reveal existence. Equalising this needs a
  fixed-latency response policy, which is not implemented.
* MFA factors are modelled and hashed, but no enrolment/verification flow is
  exposed yet.
* Official declaration XML: **D390** now emits the official, schema-valid structure;
  the other generators still emit the internal workpaper structure. Conforming them
  requires, per form: **D212** the `oblig_realizat`/`oblig_estimat` sections plus
  declarant identity (nume/prenume/CNP); **D300** the R1–R75 line mapping from the
  VAT journals; **D394** the per-counterparty informative line mapping and CAEN/type
  classification; **D301** the per-operation classification. D390 sales-side
  operations (L/T/P/R) additionally need the place-of-supply facts, and D390/D394
  still rely on the counterparty `country_code`. An approved rule pack and an accepted
  official-validator fixture remain release gates, so D390 stays `is_preview` until
  then even though it is now schema-valid.
* Notification e-mail delivery uses a file-capture sink; production needs an
  SMTP/API sender behind the same interface. Development retrieves captured links
  through `GET /api/v1/dev/mailbox` (404 outside development/test), from the
  worker's stdout, or from `.run/emails/*.json`.
* Fixed after review: clicking **Autentificare** navigated to the SPA's own origin
  instead of the authorization service, because the SPA had no
  `VITE_AUTHORIZATION_ORIGIN`; the login button then appeared to do nothing. `make
  dev` now derives the SPA configuration from the backend settings, a committed
  `frontend/.env.development` covers `npm run dev`, a misconfiguration is reported
  instead of silently looping, and a request that lands on the SPA's
  `/oauth/authorize` gets an explicit recovery page.
* Fixed after review: even with the right origin, the login form was blocked by
  `form-action 'self'` — Chromium applies `form-action` to the *redirect target*,
  and the form legitimately ends at the SPA callback on another origin. The
  authorization pages now list that origin; application pages keep
  `form-action 'self'`. Covered by
  `backend/tests/test_auth_oauth.py::test_login_page_csp_allows_the_callback_redirect`.
* Fixed after review: the dev server proxied the whole `/auth/` prefix, so the
  registered callback URI `/auth/callback` was answered by the API with a JSON 404
  instead of the SPA. Proxied paths are now listed explicitly and the callback is
  owned by the SPA.
* Fixed after review: a failed login left the browser on the POST URL with no way
  to retry safely. It now returns `303` to the canonical authorization URL with a
  fixed error code and a fresh one-use proof, so the history holds no stale POST
  and a reload is safe.
* Fixed after review: an account with no PFA showed an endless "Se încarcă…"
  instead of onboarding. `WorkspaceSetupPage` creates the first workspace and the
  creator becomes its first ADMIN.
* Fixed after review: `/docs` answered `200` but rendered nothing. Two causes: no
  security scheme was published, so the "Authorize" button did not exist, and the
  strict `Content-Security-Policy` blocked FastAPI's **inline** Swagger UI boot
  script (adding a CDN exception was not enough). The documentation is now
  self-hosted with vendored `swagger-ui-dist` assets and external scripts only, so
  one strict CSP covers the application and the docs; the OpenAPI document
  advertises `HTTPBearer` plus the real OAuth2 authorization-code + PKCE flow; and
  the UI is mounted only outside production. Covered by `backend/tests/test_docs.py`
  (6 tests) and `frontend/e2e/docs.spec.ts`, which asserts the rendered DOM,
  off-origin requests and console errors precisely because a status code cannot
  detect a blank page.
* Fixed after review: the SPA had no route for `/verify-email`,
  `/password-reset/confirm` or `/invitations/accept`, so activation, reset and
  invitation links pointed at pages that did not exist. The routes and pages now
  exist and are covered by `frontend/src/pages/VerifyEmail.test.tsx`.
* Fixed after review: an expired activation link was a dead end — the account
  existed, so re-registering failed, yet there was no way to obtain a new link.
  `POST /auth/verify-email/resend` now issues a fresh single-use link following the
  OWASP token guidance: 256-bit `secrets` token, only its SHA-256 hash is stored,
  issuing retires the previous unused link (so exactly one is valid), the account
  row is locked while minting (concurrent resends cannot both win), the answer is
  generic for unknown or already-active addresses (no account enumeration), it is
  rate limited per account *and* per source address, and the token is never logged
  outside development/test. The SPA verification page and the authorization login
  page both offer it, and the SPA removes the one-use token from the address bar
  once exchanged. Covered by
  `backend/tests/test_auth_oauth.py::test_verification_resend_issues_a_new_link_and_retires_the_old_one`,
  `::test_concurrent_resends_leave_exactly_one_active_link` and
  `frontend/src/pages/VerifyEmail.test.tsx`.
* Hardened after review: delivered activation/reset tokens are redacted from the
  outbox once the worker has processed the message, and the worker logs a token or
  token-bearing URL only in development/test (elsewhere just the template and a
  masked recipient). The SPA sets `Referrer-Policy: no-referrer` (header and meta)
  so a token in the URL cannot leak through a later cross-origin request.
* Fixed after review: links on the server-rendered authorization login page were
  relative (`/cont/inscriere`), so they were requested from the authorization
  origin and answered `404`. They are now absolute URLs on the SPA origin.
* Fixed after review: the fiscal regime (real vs. income norm) and the VAT
  registration could be read in Setări but not set anywhere in the UI. There is now
  a form in Setări → Profil fiscal that writes both, plus the `regime` year fact the
  declaration engine reads. Saving is versioned and idempotent: a change closes the
  previous period on the first day of the chosen income year and opens the new one,
  so repeated saves do not duplicate rows. Covered by
  `backend/tests/test_declarations.py::test_regime_and_vat_selection_is_idempotent_and_versioned`
  and `frontend/src/components/FiscalRegimeForm.test.tsx`.
* Fixed after review: Setări → Profil fiscal showed an empty **CUI** and Denumire for
  workspaces whose fiscal-profile row had not been written yet (the row is created
  lazily, while the CUI lives on the workspace). `GET /fiscal-profile` now falls back
  to the workspace's name and CUI, `PATCH` defaults the created profile from them,
  and there is an explicit *Date de identificare* form to edit them. Covered by
  `frontend/src/components/FiscalIdentityForm.test.tsx`.
* Fixed after review: VAT could not be entered on documents — the income and expense
  forms hard-coded `vat_treatment: "undetermined"` and `vat_rate: 0`, so every sale
  and purchase was recorded without VAT even though the API accepts both. Both forms
  now offer a VAT treatment (standard / reduced / second reduced / non-deductible /
  exempt / reverse charge / out of scope / undetermined) and the rate printed on the
  document; the entrance is what the engine uses, and the registration configured in
  Setări decides between D300 and the special D301. Covered by
  `frontend/src/pages/IncomeVat.test.tsx`.
* Fixed after review: the expense line description repeated the expense category, and
  nothing explained how to record a foreign-currency invoice (amounts are stored in
  RON only). Cheltuieli now has a real *Descriere* field and both Venituri and
  Cheltuieli show the convention under it: original amount × rate = RON, with the
  invoice attached. Covered by `frontend/src/pages/DescriptionHint.test.tsx`.
* Fixed after review: the „Facturi emise" and „Cheltuieli înregistrate" tables showed
  only series/date/total/status, so there was no way to tell what a document was
  about. The list response now carries a `description` aggregated from the document
  lines (one query, no N+1) and both tables render a *Descriere* column. Covered by
  `backend/tests/test_accounting.py::test_document_list_includes_the_line_description`
  and `frontend/src/pages/DocumentList.test.tsx`.
* Fixed after review: the expense list showed stored enum codes (`partially_deductible`,
  `vehicle`) and a raw decimal for usage. Lists now render labels: deductibility
  (Integral/Parțial/Nedeductibilă/Nedeterminată), **Categorie** as its own column,
  percentages without trailing zeros (`50%`), and document status via `statusLabel`
  (added `finalized`/`cancelled`). Legacy lines that stored the category code as
  their description show the default description (`Achiziție`) instead of the
  category. Covered by `frontend/src/pages/DocumentList.test.tsx` and
  `frontend/src/format.test.ts`.
* Fixed after review: the dashboard's „Verifică și generează" only navigated, and
  nothing in the UI ever called `POST /declarations`, so the review page was always
  empty ("no prepared declarations") and no file could be generated or downloaded.
  The dashboard now prepares the declaration (or reopens the one already prepared),
  passes its id to the review page, which selects it; preparing links the matching
  obligation so the same draft is reused instead of duplicated. Covered by
  `backend/tests/test_declarations.py::test_preparing_a_declaration_links_the_obligation`
  and `frontend/src/pages/Home.test.tsx`.
* Fixed after review: „Deschide" on the Declarations page did open the review panel,
  but the panel renders *below* the list, so with a populated table the click looked
  ineffective. The selected row is now marked (`Deschisă` + highlight) and the panel
  is scrolled into view. Covered by `frontend/src/pages/Declarations.test.tsx`.
* Official ANAF form schemas (XSD) are now **vendored** for D212, D300, D301, D390 and
  D394 under `backend/pfa/static/schemas/anaf/` (provenance and SHA-256 in
  `SOURCES.md`), registered in `pfa/services/form_schemas.py`, and **enforced** on
  export: an artifact that does not validate against the official XSD for its form
  is stamped as a workpaper (`preview="true"` plus the reason inside the file) and
  can never be approved-to-file or submitted. The export response and audit record
  carry `official_schema`, `schema_valid` and `schema_errors`. Previously the
  `form_versions.supported` flag alone produced a file labelled official, which no
  schema had ever checked. Covered by `backend/tests/test_form_schemas.py` and
  `test_declarations.py::test_supported_flag_without_a_conforming_artifact_stays_a_workpaper`.
  The official **field mapping** is still not implemented — see the gap list below.
* **D390 (Declarația recapitulativă VIES) now generates the official structure.** The
  XML is `<declaratie390>` in `mfp:anaf:dgti:d390:declaratie:v3` with the official
  header, `<rezumat>` (nrOPI, the six bases, total_baza, control sum) and one
  `<operatie>` per classified operation, written as whole lei — and it validates
  against the ANAF XSD (`schema_valid: true`). The L/T/A/P/S/R type is a new explicit
  document fact (`documents.intracommunity_operation_type`, migration 0003) and the
  country comes from the counterparty; neither is guessed, and anything missing is
  reported as a blocking input. The `<cos>` cardinality in the published XSD
  (`minOccurs=1`) is relaxed to 0 at load time to match the official structural
  document, which says "0-n apariţii"; the vendored file is never modified.
  Covered by `backend/tests/test_form_schemas.py` and
  `test_declarations.py::test_d390_classification_uses_the_counterparty_country_and_operation_type`.
* Fixed after review: exporting a D390 whose stored calculation predated the official
  mapping raised `KeyError: totalPlata_A` → 500, which a browser can only report as
  "CORS Missing Allow Origin". A stale calculation now raises a handled `409` asking for
  recalculation. The same path needed the supplier's country: documents created without a
  registered counterparty (the UI's normal path) can now record `supplier_country` and
  `supplier_vat_id` (migration `0004_supplier_country`), and the D390/D394 classification
  falls back to them, so the flow is completable from the expense form.
* Fixed after review: the „Valori calculate" table in the declaration review rendered the raw
  `calculated_values` keys (`balance_payable`, `collected_total`, …). They are now labelled in
  Romanian (TVA de plată, TVA colectată, CAS/CASS datorată, bazele D390 L/T/A/P/S/R, …) with a
  humanised fallback for unknown keys, integer counts shown without decimals, and metadata keys
  (year, rule pack, engine version) kept out of the figures table. Covered by
  `frontend/src/pages/Declarations.test.tsx`.
* Fixed after review: a document could be saved as „Taxabil — cotă standard" with an empty
  VAT rate, so its total held no VAT while the declaration applied the statutory rate from
  the rule pack — the invoice and D300 disagreed. A taxable document (standard / reduced /
  second reduced) with no rate on a non-zero base is now rejected (`422`,
  `lines=vat_rate_required`), and both the income and expense forms require the rate for a
  taxable treatment before saving. Covered by
  `backend/tests/test_accounting.py::test_taxable_document_requires_its_vat_rate` and
  `frontend/src/pages/ExpensesVat.test.tsx`.
* Fixed after review: the „Facturi emise" list showed only the gross total, so the VAT on
  each invoice was invisible. Both document lists now carry a **TVA** column next to Total
  (income and expenses), taken from the `total_vat` the list already returned. Covered by
  `frontend/src/pages/DocumentList.test.tsx`.
* Fixed after review: the D300 did **not** reconcile with the invoices. `build_journals`
  recomputed the VAT as `base × pack_rate(category, date)`, ignoring the VAT recorded on
  each document, so a 21% invoice in March 2025 (pack rate 19%) or a 0%-VAT document
  produced a different declaration than the books. The VAT transaction now carries the
  recorded `vat_amount_ron` and `recorded_rate` from the document, `assess_transaction`
  reports that VAT (the rule-pack rate is still used for reverse-charge self-assessment,
  where the invoice legitimately carries none), and the journal line shows the invoice's
  rate. Verified live: sales 1000 + 21% and purchase 500 + 21% → D300 collected 210,
  deductible 105, payable 105. Covered by
  `backend/tests/test_declarations.py::test_d300_reports_the_vat_recorded_on_the_documents`.
  Declarations calculated before this change must be recalculated („Verifică").
* Fixed after review: documents could not be edited or removed from the UI — `update_draft`
  and `cancel_draft` existed in the service, but no route exposed them and no `DELETE`
  existed. Added `PATCH /documents/{id}` (draft edit with optimistic locking on `revision`)
  and `DELETE /documents/{id}` (at the time, a soft cancel). Cancellation was refused with
  a clear `409` when the document was already inside a generated or filed declaration.
  Issued documents were still refused an edit at that point (corrections went through a
  credit note); superseded by the entry below. The income and expense lists offer „Editează" for drafts and „Șterge" (with
  confirmation). Superseded: `DELETE` now deletes for real, see the entry below. Covered by
  `backend/tests/test_accounting.py::test_draft_can_be_edited_and_deleted`,
  `::test_issued_document_is_immutable_but_can_be_deleted`,
  `backend/tests/test_declarations.py::test_document_in_a_generated_declaration_cannot_be_deleted`
  and `frontend/src/pages/DocumentList.test.tsx`.
* Fixed after review: two follow-ups to document cancellation. The lists showed the
  cancelled document with status „anulată" among the active ones — cancelled documents
  are now hidden by default, with an „Afișează și documentele anulate" toggle (the row
  stays in the audit trail). And cancelling an invoice left its **per-invoice e-Factura
  obligation** open, so an old invoice kept showing „EFACTURA — restanță" on Acasă:
  deleting now retires that obligation (`not_required` + reason), resolves its reminders,
  and re-evaluates the period obligations without the document. Covered by
  `backend/tests/test_declarations.py::test_deleting_an_invoice_retires_its_e_factura_obligation`.
* Fixed after review: D390 was raised for **every** taxable document in a VAT-active
  period, so a domestic-only PFA got a recapitulative obligation it could never fill in —
  and validating it was blocked with „informații obligatorii necunoscute". The trigger now
  requires intra-community evidence (an L/T/A/P/S/R type or a non-RO supplier country),
  and its `missing_inputs` says which of the two is missing. Separately, a refused
  validation now **lists the blocking inputs by name** in the declaration panel (with where
  to complete them) instead of only the generic message. Covered by
  `backend/tests/test_declarations.py::test_d390_obligation_requires_intracommunity_evidence`
  and `frontend/src/pages/Declarations.test.tsx`.
* Added `DELETE /declarations/{id}` and a „Șterge declarația" action, so a draft or a
  workpaper declaration can be removed instead of lingering (e.g. a blocked D390). A
  declaration that is approved or already filing (`queued`/`submitted`/
  `submission_unknown`/`accepted`) cannot be deleted — it is a compliance record and is
  corrected by a rectifying declaration — and a declaration that has a correction is
  protected until the correction is removed. What is append-only by schema
  (`declaration_versions`, `calculation_runs`, `receipts`) is deliberately kept as the
  audit of what was calculated and filed; obligations and corrections simply stop
  pointing at the removed declaration. Deleting also **reopens the obligation and its
  reminders**, so the filing the user still has to make is actionable on Acasă again.
  Covered by
  `backend/tests/test_declarations.py::test_declaration_can_be_deleted_until_it_is_approved`,
  `::test_approved_declaration_cannot_be_deleted` and `frontend/src/pages/Declarations.test.tsx`.
* Fixed after review: the dashboard button always prepared a *declaration*, so an
  e-Factura obligation answered „Formularul EFACTURA nu are un adaptor de calcul".
  e-Factura is a transmission task, not a form: the button now says **„Transmite prin
  e-Factura"** and calls `POST /efactura/transmit/{document_id}` using the obligation's
  triggering evidence, then shows the job confirmation. `next_step_kind` is honoured for
  every card (`review_declaration` opens the linked declaration, `resolve_missing_information`
  goes to the review page), and preparing `EFACTURA` as a declaration returns a clear
  `409` explaining that the invoice is transmitted instead. Covered by
  `frontend/src/pages/Home.test.tsx` and
  `backend/tests/test_declarations.py::test_efactura_is_not_prepared_as_a_declaration`.
* Fixed after report ("if the client is from Romania I cannot select Romania; where do I
  enter the CUI?"): two gaps in the partner data, both fixed.
  * The country selector offered only the D390 nomenclator (EU member states), so a
    Romanian client or supplier could not be recorded at all. Added
    `PARTNER_COUNTRIES` (Romania first, then the EU list) and the selector is now always
    visible for a business partner, not only for an intra-community operation.
  * The partner's fiscal code had no field of its own for domestic partners: the only
    input was "Cod TVA furnizor", inside the reverse-charge block, and the income form
    labelled it "Cod TVA client". Both are now "Cod fiscal … (CUI/CIF sau cod TVA)" and
    always visible for a business partner, with a hint on what to enter (Romania: the
    CUI/CIF, with or without the RO prefix; another member state: the VAT id with the
    country prefix). The value is stored in `documents.supplier_vat_id`.
  * The recorded facts were then **not used**: `transmit_invoice` always sent an empty
    customer to e-Factura, and the D394 workpaper carried no partner at all. The
    e-Factura XML now carries the recorded name, fiscal code and country, and every
    D390/D394 transaction row includes the partner identity (name, fiscal id, country,
    B2B/B2C). Covered by
    `test_the_transmitted_xml_carries_the_recorded_customer` and the income form test
    that selects Romania and sends `RO12345678`.
* Checked after the series became optional: declarations handle a document with no series.
  `test_declarations_work_with_a_document_that_has_no_series` prepares, calculates, validates
  and exports D300 and D394 over a series-less invoice and asserts that **no `"None"` leaks**
  into the calculated values or the artifact, that the invoice is in the return's totals
  (collected 210 for a 1000 + 21% sale) and that the VAT journal register lists it by number.
  Verified live too: prepare `201`, calculate `200` (collected 1680.00, deductible 840.00),
  export `200` with no `None` in the body. Every other place that concatenates a series
  already guards with `or ''` (e-Factura `ID`, settlement labels, revision descriptions,
  document labels in both lists). The only validation block on that workspace is the honest
  one from an expense whose business-use share was never stated; it now names the document
  (`business_use_percent:<id>`) like the income-tax side does, covered by
  `test_an_expense_without_a_share_blocks_the_vat_return_by_document`.
* Checked for the other document kinds after the series became optional
  (`test_every_document_kind_works_without_a_series`: a purchase, a credit note, the
  journals and the e-Factura XML, all with `NULL` series). Two real defects surfaced, both
  about **credit notes**:
  * D300 treated a credit note incoherently: it was excluded from the operations (so a
    storno never reduced the VAT) while its classification could still block the return.
    Now a note is included as a **negative adjustment of the operation it corrects**: the
    direction is the corrected document's (sales side reduces collected VAT, purchase side
    reduces deductible VAT), the amounts are negated, and the note is never demanded as
    classification — its link to the corrected document is the evidence. A note left
    without a corrected document is skipped, not blocking.
  * The VAT registers excluded credit notes by kind, so the journal disagreed with the
    decont. Both journals now include the notes correcting a document of their direction,
    with negative amounts (`repo.finalized_credit_notes_between`). Covered in the test: the
    journal lists the storno and the register provides the document's number for both.
  * The e-Factura XML of a series-less invoice carries its number (no `None`) — asserted.
  * Note: a credit note for a *purchase* is accepted by the application; in Romanian
    practice the supplier issues that storno. Reported as a modelling question rather than
    silently restricted.
* Changed by request („The field Serie is not required since some does not have"): the
  **series** is now optional; the **number** stays required, because it is what identifies
  the document. `finalize` asks only for the number, an absent series is stored as `NULL`,
  and the form labels the field „Serie (opțională)" with a hint saying the number is the
  required one. Uniqueness for issued invoices still holds without a series — which needed
  `COALESCE(series, '')` in the unique index (migration `0012_series_optional`), because
  Postgres treats NULLs as distinct and two number-only invoices with the same number would
  otherwise both be accepted. That stricter index immediately exposed documents recorded
  through the old hole: the migration **reports them and refuses to run** rather than
  renumbering a document by itself („Corectați numerele … înainte de migrare"). The
  empty-string series/number values were normalised to NULL, and the duplicate check
  (`find_by_series_number`) compares `COALESCE(series, '')` so an absent series is not a
  free pass. Covered by `test_a_document_without_a_series_is_issued_by_its_number` (issued
  without a series, and the same number still refused) and the updated finalize test.
* Checked the remaining unassessed list after adding D392: of the other uncovered forms,
  **none can be responsibly produced by a small-PFA app's own data** — D112/D100/D205/D710
  need a payroll or withholding module (an employer, which the first release excludes),
  D207 is filed by the *payer* of the PFA's income (not by the PFA itself), D307/D311 are
  regime-specific adjustments requiring specialist review per ANAF guidance, and D398
  (OSS/IOSS) needs per-member-state consumption figures that do not exist in the
  registration data. They stay visible on the dashboard with their reason. The one absent
  form the release's own audience does owe was D392 — added above.
* **Added by request — D392 (art. 259^1)**: the annual informative return that a
  **non-VAT payer** owes by **25 February** of the following year for the operations
  invoiced to legal entities settled with **other payment instruments than cash or
  e-Factura** was missing from the catalogue entirely — the biggest gap left in the first
  release after the cheque of the covered forms (a small PFA without VAT registration who
  invoices to companies hits exactly this). Added:
  * the catalogue entry (annual, assisted, `detect=True`) with the legal basis
    (Cod fiscal art. 259^1, ANAF form issued by Ordin 1718/2022) and the reason generation
    is assisted: the monthly totals per payment instrument come from the bank statements;
  * detection in `evaluate_obligations`: no active article 316 registration in the year,
    yet finalized sales exist → `needs_information` with the deadline reason,
    `other_instruments_monthly_totals` as the blocking input and the invoices as evidence;
  * covered by `test_d392_is_detected_for_a_non_vat_payer_with_sales` and removed from the
    „fără detecție" list in the dashboard.
* Checked twice after the fix: the requirements mapping had the trace test's name
  **truncated** (`test_declarations_list_carries…` instead of
  `test_the_declarations_list_carries…`), which made `make check-requirements` fail with
  „test node not found" even though the pytest node existed; both yaml entries now carry the
  exact node and the check is green again.
* Fixed after report („In Declarații I see Cum s-a calculat? → Arată explicațiile even it
  does not work all the time"): the button did not work **at all** from the list. The review
  panel shows the selected declaration from the *list* endpoint, which never carried the
  `trace` — only `GET /declarations/{id}` did — so „Arată explicațiile" always opened an
  empty list. The list now returns `trace`, `calculated_values` and the artifact fields of
  the **latest** version of each declaration (`latest_versions_for`, one query, DISTINCT ON
  semantics with the newest version first), so the panel shows the same thing however the
  page loaded its data. And when there is nothing to explain yet (a declaration that was
  never calculated), the panel says so explicitly instead of toggling an empty list — the
  button appears only when there is something to show. Covered by
  `test_the_declarations_list_carries_the_trace_of_latest_version` (two versions, the list
  carries the newest) and the page test for the „never calculated" wording.
* Changed by request: expenses now follow the **same flow as income** when adding or
  editing — Adaugă document → Verifică datele → Salvează — instead of saving on the first
  click with no chance to review. The page gained the same three-step header, a
  „Verificați datele" summary (serie/număr, furnizor + țară, descriere, sumă, data, TVA,
  categorie, deductibilitate + pondere, scop, plătit) with „Salvează"/„Înapoi", and a
  „Document salvat" panel. What is checked before saving is now gathered in one place: the
  missing pieces are listed together (reverse-charge justification, supplier identity, the
  VAT rate) instead of one-at-a-time refusals. Existing tests were updated to the new
  flow, and the refusal wording tests follow the gathered message.
* Changed by request: choosing **„Integral deductibilă"** now defaults **„Pondere utilizare în
  scop de afaceri"** to **100**, so the common case no longer needs the share typed in by
  hand. It stays an explicit, visible default rather than a hidden assumption: the value
  appears in the field, it is editable, a share the user already stated is never
  overwritten, and the tax-details section opens as soon as the expense is classified (so
  the filled value is never behind a collapsed section). Covered by
  „defaults the business-use share to 100 for a fully deductible expense" and „never
  overwrites a share the user already stated".
* Fixed after report („I receive the error when I try to validate D301 … Justificarea
  clasificării operațiunii (document 9b33d1f8)"): the blocked document was an ordinary
  **sale**. D301 built its rows from **every** document in the period, so a domestic sale
  was asked for a reverse-charge justification — and, worse, its collected VAT was added to
  the special return's total. The snapshot now selects the operations each form actually
  reports: D301 takes purchases with the **reverse charge** treatment, D390 purchases, D394
  both. Three related defects fixed with it:
  * A reverse charge carries no VAT on the paper, so the return reported **0**; it now
    reports the VAT the beneficiary **self-assesses**, computed from the rate recorded on
    the document. When that rate is missing it blocks with `vat_rate:<id>` instead of
    silently reporting zero.
  * Because that rate is evidence the return needs, the expense form asks for it: the VAT
    rate is now shown and required for a reverse charge too, labelled „Cotă de autotaxare
    (%)" with the explanation that the supplier's invoice carries no VAT but D301 reports it.
  * `D301Adapter.calculate` and `required_inputs` had two different ideas of what is
    missing; the calculation now uses the same per-form definition as the validation gate,
    and `D394Adapter` overrides it to ask for the **partner** (its actual subject) rather
    than a reverse-charge justification.
  * Covered by `test_d301_covers_only_the_operations_of_the_special_return` (the sale is
    excluded and the total is only the acquisition's self-assessed VAT),
    `test_d301_asks_for_the_self_assessment_rate_instead_of_reporting_zero` and
    `test_d394_asks_for_the_partner_not_for_a_reverse_charge_justification`.
* Fixed after report („after saving an expense and then editing it, Justificarea taxării
  inverse is empty; amounts and percentages show 100.000000 instead of 100"): two
  independent defects.
  * The justification **was never returned** by the read path: it was accepted, stored and
    used by D301, but `_document_view` and the response schema did not carry it, so the edit
    form hydrated an empty field and the next save wiped what the user had entered. Both now
    expose `reverse_charge_justification` (list and detail). Covered by
    `test_the_detail_endpoint_returns_the_reverse_charge_justification` and the expense form
    test that reopens a document and expects the text back.
  * Amounts and percentages are stored with six decimals, and the edit form hydrated the
    stored string directly, so a field read „2000.000000" or „100.000000". Added a shared
    `trimDecimal` in `format.ts` (the helper existed in three private copies) and used it
    wherever a stored number is loaded into a form: expense amount, VAT rate, business-use
    share, income amount and VAT rate, settlement amount, norm amount.
* **Added by request — recorded receipts and payments can be managed.** The API could list
  settlements but not correct or remove one, so an over-allocated document had no way out.
  Added `PATCH /payments/{id}` and `DELETE /payments/{id}` (audited as
  `settlement.updated` / `settlement.deleted`), `GET /payments` now returns what each
  settlement is applied to, and a new page **Încasări și plăți** lists them with
  Editează / Șterge. Rules made explicit: a correction only counts the *other* allocations
  against the document, and when a settlement is applied to exactly one document its
  allocation follows the corrected amount; removing a settlement releases the document.
  Fixed while adding this: the „Ai încasat?" / „Ai plătit?" defaults recorded the **net**
  amount typed in the form instead of the document's total, so a full receipt under-paid
  its own invoice — they now default to the document total (with VAT). Covered by
  `test_a_recorded_receipt_can_be_corrected_and_removed`, the new page tests and
  „records a receipt for the document's total, not for the net amount". Specification §3.3
  and the endpoint table updated.
* **Added by request — management for the money movements that are not for a document**
  („contribuția proprietarului, retragerile, împrumuturile, plățile de taxe"): they were
  modelled (`PaymentKind` + `is_owner_movement` in the RJIP) but no surface recorded them,
  and nothing said how they are registered for accounting.
  * „Încasări și plăți" now records them through a dedicated form: **Aportul titularului**
    (încasare), **Retragerea titularului** (utilizarea venitului net), **Transferul între
    conturile personale**, **Împrumut primit / Rambursat**, **Plată de taxe
    (impozit/CAS/CASS)** — each showing, while recorded, the rule that governs it:
    OMFP 170/2015 (RJIP, formularul 14-1-1/b) plus Cod fiscal art. 68 alin. (2) — aporturile
    și împrumuturile „nu sunt venituri brute" — and art. 68 alin. (4) — „impozitul pe venit
    datorat nu este deductibilă", CAS/CASS deducându-se numai prin Declarația Unică.
  * The list labels each row with its kind, so the accounting nature is visible at a
    glance („Aportul titularului", „Plată de taxe", …).
  * These kinds **cannot be allocated to any document** (422, `allocations: not_allowed`):
    the money is not for a document, per art. 68 alin. (2). They enter the RJIP as owner
    movements (the balance column), never as receipts/payments of the activity, and never
    reach the fiscal register — the cash journal already separates them.
  * The `POST /payments` response now carries per-kind `note` with the registration rule.
  * Covered by
    `test_the_other_money_kinds_are_registered_as_the_legislation_says`: all six kinds are
    recorded, each answer carries its legal rule, they appear in the RJIP as owner
    movements, and allocating them to a document is refused with `allocations: not_allowed`.
* Fixed after report („Edited the document and I see Alocarea depășește suma disponibilă a
  documentului"): the edit **recorded a second settlement**. The „Ai încasat?" / „Ai plătit?"
  question belonged to the add flow, but the state stayed on while editing, so saving an edit
  also posted a new receipt/payment allocated to the same document — which the allocation
  guard then refused. Editing now resets that state and the fieldset is replaced by a note
  that settlements are recorded separately (a correction must not allocate twice). The guard
  itself now also names the document and the amounts („disponibil X, cerut Y") and says to
  change the existing allocation instead of adding one.
  Consequence handled honestly: lowering a document below what is already settled is now
  reported rather than shown as a negative „rest" — `SettlementSummary` gained `over_settled`
  and `over_amount`, and the panel says „peste valoarea documentului cu X — verificați
  încasările înregistrate". Covered by
  `test_an_over_settled_document_is_reported_not_hidden` and the list test „never records a
  second receipt while editing a document".
* Fixed after report („When edit the document I see Documentul pare a fi deja înregistrat"):
  the edit was being turned into a **new** document. Two causes, both real:
  * A page reload reset the workspace to the first one in the list (the session is restored
    asynchronously, and the „no session yet" branch cleared the workspace state), so the
    form came back in „add" mode, in another workspace. The selected workspace is now
    tab-scoped and survives a reload; it is cleared only on an explicit logout.
  * The edit intent itself was memory-only, so a reload (or a dev hot reload) lost it and
    the next save created a second document — which the duplicate check then refused. The
    document being edited is remembered in the tab **together with its workspace**, is
    restored on mount, and is dropped when it belongs to another workspace or no longer
    exists (instead of leaving a stale 404).
  * And when a create *is* refused as a duplicate, the refusal names the document it
    matched (`ApiError.duplicateDocumentIds`), and the form offers „Deschide-l pentru
    editare", which opens that document in the editor — the user's intent is to correct it,
    not to be blocked.
  * Covered by the list tests „resumes an interrupted edit after a reload", „does not resume
    an edit that belongs to another workspace" and „offers to edit the existing document
    when a create is refused as a duplicate".
* **Changed by request — an issued document can be edited.** The series, number and dates
  are the user's own facts, so refusing every correction that is not a credit note pushed
  the user into deleting and re-recording a document. `PATCH /documents/{id}` now accepts a
  finalized document as well as a draft:
  * the previous state is written to the append-only `document_revisions` with reason
    `issued_edit` before the change, and `revision` still advances (optimistic locking), so
    an edit is visible rather than silent;
  * two refusals remain, because they would change something that has already left the
    application: a document inside a generated or filed declaration (`409`, same guard as
    deletion) and an invoice already transmitted through RO e-Factura (`409`, „folosiți o
    notă de credit sau ștergeți factura și reemiteți-o");
  * the obligations and reminders of that year are recomputed, so the e-Factura deadline and
    the D300/D390/D394 evidence follow the correction;
  * the income and expense lists offer „Editează" for every document, the form says whether
    it is editing a draft or an issued document, and an issued document is not finalized a
    second time.
  * Covered by `test_an_issued_document_can_be_edited_with_its_previous_state_kept`,
    `test_an_issued_document_inside_a_filed_declaration_cannot_be_edited`,
    `test_a_transmitted_invoice_cannot_be_edited` and the list test asserting the edit path
    does not call `/finalize`. Specification §3.1 updated.
* Fixed after report („Validarea este blocată: … classification"): the D301 block named an
  internal key and there was **no field** that could satisfy it.
  * The classification evidence for a reverse charge is the document's justification, and
    the expense form never sent one (`reverse_charge_justification` existed in the API and
    the model but had no control; the neutral defaults then removed the `business_purpose`
    that used to satisfy it by accident). The expense form now has a required
    „Justificarea taxării inverse" field whenever the treatment is „Taxare inversă", and it
    is what clears the block.
  * The key was untranslated: `classification:<document>` rendered as „classification".
    Both label maps now cover `classification`, `vat_transaction_classification`,
    `external_services_classification`, `expense_business_use` and `expense_deductibility`,
    and a per-document key shows the document reference („… (document 0e413e21)").
  * The declarations error now shows, **per missing input**, where it is completed
    (`missingInputHint`), instead of one generic paragraph that mentioned the wrong place
    for most cases.
  * Covered by `test_the_reverse_charge_justification_is_what_unblocks_d301` (the block names
    the exact document and the justified one is not flagged) and the expense form tests
    (the field appears for reverse charge, saving without it is refused, and it is sent).
* Fixed after report („The fields from settings that are only for norma de venit are
  displayed only in that case"): the per-year facts form listed every fact for every
  workspace, so a „Sistem real" PFA was shown — and warned about — „Venitul normat anual",
  „Ajustarea normei" and „Luni de activitate", fields that cannot matter for it. The
  catalogue now declares when a fact applies (`applies_when`: norm fields need
  `regime = norm`, the salary amount needs `salary_income_present = true`), the form hides
  a fact whose condition does not hold, and the save notice reports only applicable
  blockers. A hidden field is never answered, so it is sent as unknown rather than as a
  number. Covered by `test_norm_only_facts_declare_when_they_apply` and the form test that
  toggles the regime.
* Fixed after report („What means Transmiterea prin RO e-Factura a fost înregistrată … I don't
  have yet integration with e-Factura"): the message claimed a filing that had not
  happened. `POST /efactura/transmit/{id}` queued the job whenever the driver was `fake`
  (the default in development) **without requiring an authorized ANAF connection**, and
  Home then said „Transmiterea … a fost înregistrată". Two changes:
  * The endpoint now requires an **active ANAF connection** for the workspace before it
    queues anything; without one it refuses with a clear `409` („nu există o conexiune ANAF
    autorizată … autorizați conexiunea în Setări → Conexiune ANAF, sau generați XML-ul și
    încărcați-l în SPV"). The connector-capability check is unchanged for the real driver.
  * The `202` response now reports `transport` and `simulated`, and the message is
    explicitly a simulation when the ANAF service is not configured („Transmitere SIMULATĂ:
    … fișierul nu a fost trimis nicăieri"). Home renders that instead of „a fost
    înregistrată", and the real note now also states that acceptance is confirmed only by
    the ANAF receipt.
  * Tests: `test_transmission_is_refused_without_an_authorized_connection` (new),
    `test_transmission_is_queued_and_uses_the_fake_service` now authorizes first and asserts
    `simulated`/`transport`/„SIMULATĂ", the two job tests authorize first, and the Home test
    pins that a simulated transmission never says „a fost înregistrată".
* Fixed after report („Check this Informații lipsă: verified registration form if it is OK"):
  it was not OK — three defects in one line.
  * The key was never translated, so it rendered through the raw-key fallback
    („verified registration form"). A label now exists in both label maps for rows already
    stored with it.
  * Nothing the user could do resolved it: it was a hardcoded flag, not an input the
    application reads. Removed — the real limitation is the **assisted** coverage (the form
    cannot be generated at all), which the obligation already states through
    `unsupported_reason`.
  * The same event raised **both** D070 (initial registration) and D700 (later change). They
    are now split by event: D070 belongs to the year the activity started, D700 to a later
    registration change, and the trigger is recorded as `triggering_evidence`.
  * `_next_step` gained `review_registration`: for a registration obligation the place to
    act is Setări → Profil fiscal, not a declaration the application cannot produce. Home
    shows „Verifică înregistrarea fiscală" for it.
  * While fixing this: nothing recorded **when the activity started** (the `activity_periods`
    table is never written), so D070 could never be raised. Added
    `pfa_profiles.started_on` (migration `0011_activity_start`) with a „Data începerii
    activității" field in the identity form; `NULL` means "not stated", so no initial
    registration is claimed. A fiscal-profile change (registration, regime, start date) now
    also refreshes the obligations of the affected years, like the tax-facts endpoint does.
  * Covered by `test_registration_forms_are_split_by_event_and_demand_no_fake_input`
    (D700 for a later change with no missing inputs, D070 for the year the activity started,
    neither demanded twice, and the `review_registration` next step).
* Fixed after report („I see in Cheltuieli înregistrate Utilizare 50% even I don't selected
  the percentage"): the expense form shipped four tax determinations the user never made —
  category „Vehicul", „Parțial deductibilă", business use 50% and the purpose „Deplasare la
  client" — and the section holding them was opened automatically because of those same
  defaults. Fixed at the root:
  * The form now starts neutral: „Cheltuieli generale", „— nespecificată —" and an empty
    share; the details section opens only once the user makes it relevant.
  * `documents.business_use_percent` is nullable (migration `0010_business_use_unstated`):
    `NULL` means **not stated**, and the form sends `null` instead of a number. The list
    prints „—" rather than a percentage nobody chose.
  * An unstated share is an **unresolved input**, not an assumed 100%: the income-tax
    engine (`expense_business_use:<id>`) and the VAT engine (`business_use_percent`) both
    report it, and the expense is not deducted from an invented share. It only applies when
    the share can change the result — a non-deductible expense needs no share.
  * Bug found while fixing this: the validation gate read `unresolved` from the version's
    **calculated values**, where it is never written, so unresolved inputs (undetermined
    deductibility, unverified counterparty VAT, an unstated share) never actually blocked
    validation. It now reads them from the latest calculation run, which is where the
    engine stores them.
  * Covered by `test_an_expense_without_a_stated_business_use_is_reported_not_assumed`
    (only the stated expense is deducted, validation names the document that needs a share)
    and the expense form test asserting the neutral defaults and the `null` share.
* Fixed after report („In expenses I receive Documentul pare a fi deja înregistrat … even
  if the serial and number is not duplicate and no document is shown in Cheltuieli
  înregistrate"): the number/date/partner duplicate predicate did **not compare the
  document kind**, so an expense collided with a *sale* that happened to share a series, a
  number, a date and a partner name. The expense list could not show the offending
  document because it was a sales invoice — hence „no document is shown". The predicate now
  requires the same `kind`, and it also compares the **series** and normalises series/number
  exactly as they are stored (so a real duplicate cannot slip through because of
  whitespace or letter case). The content hash is normalised the same way. Covered by
  `test_an_expense_never_collides_with_a_sale_that_shares_its_number`, which also pins that
  a same-kind repeat with *different amounts* is still refused, and that a different series
  is a different document.
* Fixed after report („Informații lipsă: Opțiunea CAS, Opțiunea CASS, Pierderile
  reportate, Venituri din salarii, Statutul de pensionar"): the facts D212 blocks on had no
  place to answer them. Setări had only a raw `fact_key: value` list and three buttons that
  wrote to **`getFullYear() - 1`** — so answering for the year that was blocked was
  impossible, and the other facts had no control at all. Added a per-year facts surface:
  * `backend/pfa/services/facts.py` — a typed catalogue (label, kind, allowed values, unit,
    help, and which declarations block on the fact) plus validation: unknown key, wrong
    type, value outside an allowed choice and months outside 1–12 are `422` with
    `field_errors`.
  * `GET/PATCH /workspaces/{w}/tax-facts` — the form is driven by the catalogue, so it
    cannot drift from what the declaration builders read. Answering **refreshes the
    obligations for that year in the same call**, so „Informații lipsă" clears immediately
    instead of waiting for a reconcile. An empty control means *not answered*: the fact
    returns to unknown and keeps blocking (it never becomes zero or "no"), and clearing is
    recorded in the audit event `tax_facts.updated`.
  * The generic `fiscal-profile` write path now delegates to the same service, so there is
    one implementation of the facts.
  * `frontend/src/components/TaxFactsForm.tsx` (Setări → „Informații fiscale pe an"):
    year selector, one control per fact grouped by theme, a warning naming the declaration
    a missing fact blocks, and a save notice that reports only real blockers. Home's
    „Completează informațiile" now opens Setări instead of Declarații.
  * The two facts that were demanded but read nowhere are now meaningful:
    `salary_income_present` **gates** the amount („no salary income" is an answered zero,
    „yes" without an amount stays unknown), and `pension_status` is reported in the
    calculation trace — while the exemption itself remains the explicit, evidenced
    `cas_exempt`/`cass_exempt` facts rather than being inferred from the status.
  * Covered by `test_tax_facts_are_answerable_per_year_and_refresh_the_obligation`,
    `test_tax_facts_reject_unknown_keys_and_wrong_types` and
    `frontend/src/components/TaxFactsForm.test.tsx`.
* **Changed by request — the `cancelled` status is gone.** Deleting a document deletes it,
  so a retired state has no producer and no reader any more; keeping it meant every list,
  duplicate check and unique index had to carry a `status <> 'cancelled'` exception.
  Removed: `DocumentStatus.CANCELLED`, `repo.cancel`, the status predicates in
  `find_duplicates`/`find_by_external_id`/`find_by_series_number`, the
  „Afișează și documentele anulate" toggle and the list filter, and the `cancelled` status
  labels. Migration `0009_remove_cancelled_status` removes the rows that still carried it
  (with their lines, attachments, revision history, applied settlements and any credit
  note that corrected one), removes the e-Factura obligations of documents deleted before
  it, and recreates both partial unique indexes without a status predicate. Deleting a
  document now also **removes** its e-Factura obligation and reminders instead of retiring
  them, so no derived row is left pointing at a document that no longer exists. Covered by
  `test_a_deleted_document_does_not_block_recording_it_again` and
  `test_deleting_an_invoice_removes_its_e_factura_obligation`.
* **Changed by request — numbering and deletion.** The series, number and dates of a
  document are now **entered by the user**: the application never allocates a number
  (`_allocate_number`, `repo.allocate_number` and the `number_series` counter are no longer
  on any path), so an invoice can be recorded at any time — including an older one entered
  later — under the series/number printed on the paper. Consequences and the safeguards
  that remain:
  * `POST /documents/{id}/finalize` requires the number (missing → `409` listing `series`
    and `number`) and accepts the values in its body as well; the form sends them at
    create/update.
  * Uniqueness is enforced per workspace **for the invoices the PFA issues** (sales
    invoices and credit notes) from the moment they are recorded, with a clear `409`
    naming the existing document (`migration 0007`, rescoped by `0008`). A purchase keeps
    the supplier's number, which is only unique per supplier: two suppliers may both send
    an invoice numbered "1" (duplicate detection now compares the partner, so the same
    supplier's identical invoice is still caught).
  * `DELETE /documents/{id}` is now a **real deletion**: the row, its lines, attachments
    (and stored objects), revision history and the settlements applied to it are removed,
    and its series/number and import key are released. The append-only `document.deleted`
    audit event preserves the deleted content, which is the compensating control for
    removing a row. Two refusals remain, because they would silently falsify a filing: a
    document inside a generated or filed declaration, and a document a credit note points
    at.
  * Fixed in the same change: `evaluate_obligations` raised `UnboundLocalError: pack` when
    the year had no rule pack (reached by deleting a document dated in such a year); `pack`
    is now initialised before the `try`.
  * Covered by `test_finalize_locks_the_document_under_the_number_the_user_entered`,
    `test_a_supplier_number_is_only_unique_per_supplier`,
    `test_deleting_a_document_leaves_the_audit_trail_and_releases_money`,
    `test_deleting_a_document_from_a_year_without_a_rule_pack` and the income/expense form
    tests. Specification §3.1/§5 and the endpoint table were updated.
* Fixed after report ("If I delete an invoice and then add again I see 'Documentul pare a fi
  deja înregistrat'"): a cancelled document is retired, but duplicate detection still
  counted it, so the replacement — the documented correction path, cancel and record
  again — was rejected by its own retired predecessor. `find_duplicates` and
  `find_by_external_id` now ignore cancelled documents, and the import key is unique only
  among the documents that still count (migration `0006_cancelled_not_duplicate` replaces
  the `uq_document_workspace_external_id` constraint with a partial unique index). Live
  duplicates are still detected. Covered by
  `test_a_cancelled_document_does_not_block_recording_it_again`, plus the existing
  duplicate test that proves detection was not disabled.
* Fixed after report ("Where can I enter the value for Clasificarea partenerului?"): the
  question had no answer, which was the defect. (1) The D394 obligation demanded
  `counterparty_classification` on **every** quarter, unconditionally, including quarters
  with no documents; it is now derived from evidence and lists the exact documents whose
  partner cannot be classified. (2) Sales invoices had **no partner field at all**, so a
  partner could not be recorded even when it existed: the income form now has "Tip client"
  plus "Client" (name, country, VAT id), and the expense form has the mirror "Tip
  furnizor" ("Furnizor persoană juridică (B2B)" / "Persoană fizică (B2C)"); both record
  the facts on the document. Choosing the B2C option hides the name and, for expenses,
  the intra-community block, so no field is shown whose value is then dropped. (3)
  ``documents.partner_kind`` (migration `0005_partner_kind`: `business` | `consumer` | NULL)
  records the B2B/B2C nature explicitly, because a consumer sale is complete without
  partner data while an unstated partner is an unknown that stays visible. The flag fires
  only for a document that is not an explicit B2C sale and carries no partner identity.
  Covered by
  `test_d394_asks_for_a_partner_only_when_it_cannot_be_classified` (named partner → clear,
  B2C → clear, unstated → flagged with evidence, invalid value → 422) and the income form
  tests.
* Fixed after report ("I don't see Decontul de TVA (D300) for September"): the dashboard
  obligations table rendered the *deadline-rule provenance* in the column labelled
  "Perioada", so a monthly decont could only be identified by its due date — September's
  D300 is due on 25 October, which made it look absent. The period is now shown
  (`formatPeriod`: "septembrie 2026", "T3 2026", "anul 2026", else the exact range) and the
  rule provenance moved under the deadline, where it belongs. The obligation itself was
  present and correct (`required`, due 25.10.2026). Covered by
  `frontend/src/format.test.ts` and the Home table test.
* Fixed after report ("Informații lipsă: counterparty_vat_id_verification"): two defects.
  (1) A D390 obligation demanded a **VIES verification** the user cannot enter; the
  blocking list now holds only user-actionable facts — the L/T/A/P/S/R operation type,
  plus the partner VAT id only where the D390 structure requires it (L/T/P/R). (2) A
  monthly D390 whose intra-community evidence had been edited or cancelled was never
  re-evaluated, so a stale obligation kept demanding the old input; D390 is now upserted
  for every month it could apply to (retired as `not_required`, blocking list emptied,
  when nothing is reportable) and `evaluate_notifications` withdraws the open reminder of
  a retired obligation (`resolved_at` set, record kept). Messages also name blocking
  inputs (`pfa/services/labels.py`, `missingInputLabel`) instead of printing storage keys.
  Covered by `test_d390_retires_with_its_reminder_when_the_evidence_is_cancelled` and the
  label tests.
* Changed by request: the SPA kept both tokens in memory only, so every page reload
  forced a new OAuth login. The **refresh token is now stored tab-scoped in
  `sessionStorage`** (the access token stays memory-only) and `loadSession()` restores the
  session by rotating it; a refused or expired refresh clears it and falls back to the
  login page. Nothing uses `localStorage`, IndexedDB, a URL or a cookie, so closing the tab
  still requires a login. Tradeoff, stated explicitly: `sessionStorage` is readable by
  same-origin JavaScript, so XSS could still steal the refresh token — contained by
  single-use rotation with whole-family revocation on reuse, the 7-day absolute session
  lifetime, server-side session checks and the strict CSP. Specification §7.2/§7.4 and the
  AUTH-05 acceptance criterion were updated to match. Covered by
  `frontend/src/auth/AuthController.test.ts`.
* Service-level objectives in specification §12 (99.9% availability, p95 read
  latency, RPO/RTO) are product targets and are **not** measured by this
  repository.
