# Romanian PFA accounting and ANAF reporting specification

Version: 1.1 · Research baseline: 16 September 2026 · Status: ready for implementation planning.

## 1. Purpose and scope

Build a multi-tenant application through which a Romanian persoană fizică autorizată (PFA) can record business activity, maintain accounting records, calculate tax obligations, and prepare the declarations applicable to its circumstances. The interface shall be Romanian-first, with English available. The frontend shall be a React TypeScript single-page application (SPA); web services shall use Python; the database shall be PostgreSQL.

“All declarations” means all obligations identified for a taxpayer's supported fiscal profile, transactions, and reporting period. It does not mean that every PFA files every form. Invoices and expenses alone are insufficient: collections, payments, tax registrations, personal contribution circumstances, prior losses, and sometimes payroll or other personal income are also needed.

The application must distinguish four capabilities: detecting an obligation, calculating its values, generating an accepted submission artifact, and submitting that artifact through an officially supported channel. An unavailable capability must be visible; no missing obligation may silently disappear from the dashboard.

This document specifies the product and engineering behavior. Its researched examples are not a complete, approved executable tax rule pack. Production support for a tax year requires the source, calculation, and form-validation gates in sections 5 and 14.

### 1.1 Supported profiles and delivery boundaries

The target product supports real-system taxation (sistem real), eligible income-norm taxation (normă de venit), non-VAT status, normal VAT registration under article 316, and special VAT registration under article 317. Registration and regime changes are effective-dated. Employees, withholding, foreign income, and special VAT schemes activate additional modules or a clearly marked assisted workflow.

The first release shall cover sole-owner PFAs without employees, standard domestic transactions and common EU/non-EU B2B service transactions, real-system and approved income-norm profiles, D212, D300, D301, D390, D394, applicable registration-change assistance, and RO e-Factura. Its onboarding must explicitly flag profiles requiring additional modules. Later milestones complete the broader target; the first release must not be advertised as universal declaration coverage.

## 2. Users, identity, and tenant boundaries

An individual registers with email and password, verifies the email, then creates a PFA workspace and becomes its first `ADMIN`. A user can belong to multiple PFAs; a PFA can have multiple users. Roles are assigned per membership, never globally.

There are exactly two application roles:

| Capability | DEFAULT | ADMIN |
|---|---|---|
| View all PFA documents, attachments, payments, registers and declarations | Yes | Yes |
| Add/edit business records, correct documents, manage fiscal details | Yes | Yes |
| Calculate, review, approve, generate and download declarations | Yes | Yes |
| Manage ANAF connection and submit when legally authorized | Yes | Yes |
| View shared deadlines and resolve obligations | Yes | Yes |
| Invite/remove users, change roles, cancel/resend invitations | No | Yes |

`ADMIN` adds user management only. Do not introduce owner, accountant, operator or viewer roles or per-document sharing permissions. All documents in a PFA workspace, including originals, generated returns and ANAF receipts, are shared with every active member regardless of who uploaded them. `created_by` is audit metadata, not an access restriction. ANAF delegation is an external legal prerequisite, not a third application role.

Invitations are email-bound, hashed, single-use and expire after seven days. Acceptance requires a verified matching email; existing accounts join without creating duplicate identities. An ADMIN may revoke a pending invitation or remove a member without deleting that member's historical records. Prevent removal, demotion or departure of the final active ADMIN with a transaction lock, including simultaneous requests. Both roles may leave if this invariant remains true. Removing membership immediately removes access to that PFA, including jobs and downloads, without removing access to other PFAs. Audit all membership changes. Role changes do not alter shared document access while membership remains active.

Every request checks OAuth scope, session status and current workspace membership; member-management mutations additionally require ADMIN. A client-supplied workspace identifier is not authorization. Platform support has no default access to taxpayer records.

D212 belongs to an individual taxpayer, not simply a PFA CUI. Prevent conflicting annual returns for the same taxpayer. Other workspaces and personal source records are not implicitly shared; the taxpayer explicitly authorizes consolidation. Before adding personal information to a workspace return, explain that the resulting declaration and its attached evidence will be visible to all PFA members. Once stored in the workspace, that document follows the same shared-access rule. Source access grants do not create document-level roles.

## 3. Required data and user workflows

### 3.1 Onboarding and fiscal profile

Collect legal name, CUI/CIF, separately protected CNP/NIF where required, registration details, address, fiscal domicile, CAEN activities, activity start/suspension/termination dates, bank accounts, accounting currency, tax regime, VAT codes and effective periods, VAT reporting frequency, and VAT cash-accounting status. Keep documentary evidence for registrations and changes.

For each tax year collect income-norm county/locality/activity and adjustment factors where relevant; pension and social-insurance circumstances; salary/other-income information needed for statutory exceptions; voluntary contribution elections; prior losses; foreign tax credits where supported; previous declarations; and payments made to the tax authority. Unknown answers remain unknown, rather than becoming zero or “no.”

A setup checklist explains which missing inputs block which declaration. Taxpayer identity is selected separately for each form according to its official instructions.

### 3.2 Sales invoices

Users can create invoices or import structured XML, CSV, or manually reviewed PDF/image data. Store issuer/customer identity, customer country and business/consumer classification, VAT identifier and verification evidence, series/number, issue/supply/due dates, currency, exchange-rate provenance, line quantities and prices, discounts, tax categories, exemption/reverse-charge justification, and totals.

The series, number and dates of a document are the user's facts: they are entered in the form and the application never generates them. This lets a document be recorded at any time, including an older invoice re-entered later, under the series and number printed on the paper. Uniqueness of series/number within the workspace is enforced for the invoices the PFA issues (a duplicate is refused with a clear message); a deleted document releases its series/number. An issued document can be corrected by editing it, because its series, number and dates are the user's own facts — but never silently: the state before the edit is kept in the append-only revision history, and the change is refused while the document is inside a generated or filed declaration, or after it has been transmitted through RO e-Factura (a credit note is then the correction). Deleting an issued invoice removes it, together with its lines, attachments, revision history and the settlements applied to it, and the append-only audit event preserves what the document contained. PDF is a human-readable rendition; e-Factura XML is a separate artifact.

OCR may propose data but must not post accounting entries or declare deductibility without review. Imports must detect duplicate documents by external identifier, issuer/number/date, and content hash, with a user-reviewed exception path.

### 3.3 Expenses, assets, and settlement

Expenses include supplier, supporting document, category, business purpose, currency, dates, VAT treatment, business-use share, deductibility treatment and justification, and settlement allocations. Income-tax deductibility and VAT recoverability are separate determinations.

Capture bank/cash receipts and payments independently of invoices. Support partial payments, one payment settling several documents, advances, refunds, bank fees, withholding, processor gross receipts versus net payouts, and approved non-cash settlements. An uploaded invoice is not evidence of payment. A recorded settlement is correctable and removable: the user sees what each one is applied to, may correct its amount, date, method, reference and note (a single allocation follows the corrected amount), and removing it releases the documents it was allocated to. Corrections and removals are audited, and a settlement never allocates more than the document still owes.

Maintain assets with acquisition cost, recoverable/nonrecoverable VAT, in-service date, depreciation method and schedule, business-use proportion, disposals and adjustments. Inventory movements and year-end inventory values require their own records. Owner contributions, loans, transfers between own accounts, and withdrawals must not become taxable revenue or deductible expenses merely because money moved.

### 3.4 Books and period close

Produce Registrul-jurnal de încasări și plăți, Registrul-inventar, Registrul de evidență fiscală as applicable, sales/purchase VAT journals, asset/depreciation schedules, and outstanding receivable/payable reports. Single-entry cash records and tax adjustments must remain reconcilable but distinct. The single-entry accounting framework and cash journal are described in [Order 170/2015](https://legislatie.just.ro/Public/DetaliiDocumentAfis/165937) and its [accounting regulations](https://legislatie.just.ro/Public/DetaliiDocumentAfis/267715).

Closing a period checks missing evidence, unclassified transactions, payment reconciliation, assets, VAT exceptions, and unresolved tax questions. Locks block ordinary edits; reopening requires a reason and creates a new revision. Accepted filings and their original inputs are immutable.

## 4. Obligation and declaration catalogue

For every candidate obligation return `required`, `not_required`, or `needs_information`, with the legal rule version, explanation, missing inputs, filing period, deadline, payment deadline, and supported delivery capabilities. Unsupported-but-required obligations remain prominent and prevent a “fully compliant” completion badge.

The following is the functional catalogue, not a substitute for form-specific eligibility predicates. Form names and detailed instructions are maintained by [ANAF's forms catalogue](https://static.anaf.ro/static/10/Anaf/AsistentaContribuabili_r/Toateformularele/Toate_Formularele.html).

| Output | Applicability to evaluate | Additional information / behavior |
|---|---|---|
| D212, Declarația unică | Personal annual income and social-contribution obligations; applicable current-year elections | Consolidate eligible activities and other personal data; support initial and corrective returns and chapter-specific correction rules |
| D300 | Normal VAT registration and reporting period | VAT ledgers, adjustments, balances carried forward and refund elections; do not infer period solely from invoices |
| D301 | Transactions requiring a special VAT return | Assess transaction-specific liability, including relevant acquisitions by non-normal-VAT registrants |
| D390 VIES | Reportable intra-community transactions | Evaluate article 316/317 status and transaction classification; EU country alone does not establish reportability |
| D394 | Applicable domestic reporting under normal VAT registration | Counterparty classifications (B2B partner identity versus an explicitly recorded B2C/consumer sale), document categories and period-specific reporting rules |
| D700 and applicable registration/mention forms, including D070 where appropriate | Fiscal registration, changes, options, cancellation | Guided event workflow; select the currently permitted form/channel, not an invoice-derived automatic registration |
| D112 | Employer payroll obligations | Payroll/personnel module or validated external payroll import; expenses alone cannot produce it |
| D100, D205, D207; D710 where relevant | Applicable withholding/payment and annual information obligations | Recipient identity/residence, income type, withholding event, treaty/evidence and correction rules |
| D307, D311 | Specific VAT adjustment/cancelled-registration circumstances | Dedicated scenarios and specialist review until supported |
| D398 and special-scheme reporting | Applicable OSS/IOSS or other supported special regimes | Separate country/rate/registration and consumption data; not inferred from all foreign sales |
| RO e-Factura | Invoice transmission obligations | Separate XML, deadlines, transmission and response workflow; not a replacement for tax returns |
| D406 / SAF-T | Evaluate legal-form exclusion | Do not enable as a default PFA return; PFAs are in the excluded categories in the cited order |

The PFA exclusion from D406 is documented in [Order 407/2025](https://static.anaf.ro/static/10/Anaf/legislatie/OPANAF_407_2025.pdf). D390 must reflect reportable intra-community operations rather than all foreign business, as illustrated by the [ANAF obligation calendar](https://static.anaf.ro/static/10/Anaf/AsistentaContribuabili_r/Calendar/Calendar_obligatii_fiscale_2025.htm); this historical calendar is not the source for current deadlines.

Additional detected domains—such as cash-register reporting, e-Transport, excise, specialized industry obligations, and reporting to authorities other than ANAF—must be marked outside the current generator's coverage and directed to an assisted workflow. The catalogue is extensible as legal requirements change.

### 4.1 Declaration lifecycle

`draft → calculated → validated → approved → exported or queued → submitted → accepted / rejected`.

An upload timeout introduces `submission_unknown`; an exported file introduces `awaiting_external_filing`, not `submitted`. Transport errors and fiscal validation rejections are distinct. ANAF receipt evidence is required for `accepted`; a successful HTTP response or upload identifier alone is insufficient. Manually reported acceptance remains visibly unverified until a receipt is attached and checked against taxpayer, form, period, and artifact.

Every declaration stores an immutable input snapshot, form/schema version, fiscal rule-pack version, calculation trace, generated artifact hash, approval and submission actors, remote reference, raw response, and receipt. A correction creates a linked new version. D300 corrections must follow its specific regularization/error-correction procedure rather than assuming every form accepts a generic rectificativă flag.

### 4.2 Generation contract

Each adapter must implement applicability, required-input validation, calculation mapping, official-schema rendering, official validation, correction strategy, supported filing channels, and receipt interpretation. Export the exact supported artifact: XML, approved PDF-with-XML package, or the form-specific official format. A preview PDF must be labelled “preview” and cannot be offered as submission-ready unless it meets the official requirements.

If the necessary year's form is unpublished, permit a labelled estimate/workpaper and block submission-ready generation. In particular, availability of the D212 used in 2026 does not establish the final schema for declaring 2026 income in 2027. The [D212 version page](https://static.anaf.ro/static/10/Anaf/Declaratii_R/declaratie_unica.html) and [official web form](https://www.anaf.ro/declaratii/duf) are the reference points.

## 5. Fiscal calculation engine

Implement a deterministic Python domain package independent of HTTP and UI. It accepts normalized accounting events, taxpayer facts and a versioned rule pack; it returns obligations, amounts, explanations and unresolved facts. The React application never supplies authoritative calculated tax values.

Each rule pack records jurisdiction, income year, valid-from/to dates, publication date, source URL and source snapshot/hash, legal article, parameters, formula version, reviewer and approval date, supported scenarios, and regression fixtures. Effective dates apply independently to income taxation, VAT transactions and form schemas. Historical declarations pin their original pack; new legislation produces a reviewed new version and an impact report, never silent historical rewriting.

### 5.1 Income and contributions

For real-system activities, start from recognized taxable collections and eligible paid business expenses, then apply the required non-cash adjustments, depreciation, limitations, carryforward losses and allowable personal-contribution deductions in the correct statutory sequence. Separate accounting cash movement, annual activity net income, taxable income, and CAS/CASS contribution bases. A simple `invoices minus expenses` formula is prohibited.

For income-norm activities, use the applicable published norm and legally permitted adjustments/proration; actual operating profit does not substitute for that base. Monitor regime eligibility and transitions, including the applicable turnover test and currency-conversion rule.

CAS/CASS calculations must model contribution elections, threshold equality, multiple income categories, exemptions, low-income exceptions, zero/loss cases, and statutory minimum-wage reference dates. Salary status must not automatically suppress all contributions. Loss-offset and contribution-deduction rules are independently versioned. Relevant legal anchors include articles 68–69, 118, 148–151 and 170–174 of the [Fiscal Code](https://legislatie.just.ro/Public/DetaliiDocument/228099).

A verified change to represent in separate year packs: the maximum CASS base for independent activities increases from 60 minimum wages for 2025 to 72 for 2026. Do not apply the newer ceiling to the previous income year. See [ANAF's 2026 independent-activity guidance](https://static.anaf.ro/static/10/Cluj/cj_DU_activ_indep_22apr2026.pdf).

### 5.2 VAT and foreign currency

Maintain separate rules for place of supply, tax point, customer status, reverse charge, exemptions, import/export evidence, VAT cash accounting, input-tax deductibility, credit notes and adjustments. Article 317 registration is not normal domestic VAT registration. Special-code eligibility and obligations must be evaluated even when domestic turnover is below the ordinary exemption threshold.

VAT-registration turnover is a distinct statutory measure, not the sum of bank deposits or D212 taxable income. The exemption threshold changed to RON 395,000 from 1 September 2025; store the earlier threshold and transition rules too. See [ANAF's threshold notice](https://static.anaf.ro/static/3/Cluj/20250912120026_cj_%20plafon_tva_12sep2025.pdf).

Standard/reduced VAT rates changed to 21%/11% from 1 August 2025, subject to categories and transitional provisions. Retain historical rates for older supplies and corrections; do not use the invoice entry date to choose a rate. See [ANAF's rate guidance](https://static.anaf.ro/static/10/Iasi/material_informativ_17-12-2025.pdf) and [D300 transition instructions](https://static.anaf.ro/static/10/Anaf/legislatie/OPANAF_2131_2025.pdf).

Store original currency, original amount, RON amount, rate value, rate date, source and conversion purpose. Invoice, settlement, VAT and annual eligibility conversions may require different legal dates and rates. Missing official exchange-rate data blocks affected final calculations unless an authorized user supplies evidenced data.

Use Python Decimal and PostgreSQL NUMERIC, never binary floating point. Preserve line-level precision and apply explicit form-specific rounding, including whole-RON rounding where required. Tax and payment deadlines use Europe/Bucharest and a versioned Romanian legal calendar, with distinct weekend/holiday and special-extension rules for each obligation.

## 6. ANAF and SPV integration

Application login and ANAF authorization are two separate trust relationships. An application account does not confer SPV rights. Never request or store the user's SPV password. Register the connector through ANAF's developer process and obtain the relevant taxpayer authorization. ANAF describes application registration and certificate-associated API authorization in its [OAuth registration guide](https://static.anaf.ro/static/10/Anaf/Informatii_R/API/Oauth_procedura_inregistrare_aplicatii_portal_ANAF.pdf).

Keep ANAF client credentials and tokens encrypted on the backend, bound to connector, environment, authorized identity and allowed taxpayers. The browser receives only connection status. The backend callback for ANAF has its own registered redirect URI and one-use state binding; it is separate from the SPA's application-login callback. Do not assume ANAF supports the application's own PKCE/token conventions or that an e-Factura token authorizes generic declaration filing.

### 6.1 e-Factura adapter

Generate the currently accepted Romanian CIUS/UBL invoice representation, validate against the official schema and business rules, upload, track processing status, retrieve response packages and preserve original XML and ANAF artifacts. Import received invoices with duplicate detection and reconciliation; import does not imply payment or deductible status.

Applicability must consider B2B/B2C/B2G, establishment, transaction location, exceptions and their effective dates. The current consolidated e-Factura law specifies a five-working-day transmission limit with an additional statutory invoice-issuance-date bound. Calculate both using the applicable rule version; avoid a hardcoded 120-hour timer. See [OUG 120/2021, consolidated](https://legislatie.just.ro/Public/DetaliiDocument/247243).

Use durable jobs, bounded exponential backoff with jitter, rate-limit handling and idempotency records. Where ANAF provides no idempotency facility, reconcile a remote reference or mark uncertain delivery for review rather than blindly resending. Connection expiry creates an actionable reconnect alert. Outages do not imply deadline extensions.

### 6.2 Tax-return filing adapters

For each form/year verify the documented channel, accepted file format, signing/certificate requirements, delegation, test availability and receipt retrieval before exposing automated submission. If no suitable supported API exists, provide a validated export, the official filing destination, instructions, and receipt import. Do not automate filing by undocumented SPV scraping.

Every submission needs approval of the immutable artifact and declared taxpayer. e-Factura auto-transmission may be enabled as an explicit workspace policy. Tax-return approvals are invalidated if the source snapshot, calculation or artifact changes. Certificates are used through an appropriate user-held or authorized remote signing mechanism; do not silently upload or retain private signing keys.

## 7. Application authentication and authorization

### 7.1 Required protocol and registration

Implement OAuth 2.0 **Authorization Code grant with PKCE S256** in Python web services. The React SPA is a **public client** with no client secret. Email/password authentication occurs on the authorization server's login page as part of an authorization transaction. OAuth grants API access; user authentication is performed by the authorization server. OpenID Connect is optional and must not be claimed without implementing its separate requirements.

Store registered clients and their allowed redirect URIs in PostgreSQL. The SPA knows its public client ID and configured callback, but never connects directly to the database. Redirect registration is managed through restricted deployment/admin operations, not arbitrary self-registration.

Example production registration (illustrative domains):

```json
{
  "client_id": "pfa-web-production",
  "client_type": "public",
  "token_endpoint_auth_method": "none",
  "grant_types": ["authorization_code", "refresh_token"],
  "response_types": ["code"],
  "redirect_uris": ["https://app.example.ro/auth/callback"],
  "allowed_origins": ["https://app.example.ro"],
  "require_pkce": true,
  "allowed_pkce_methods": ["S256"]
}
```

Match redirect URIs exactly against registered values: scheme, host, port, path and query. Reject wildcards, fragments, userinfo, prefix matching and open redirectors. Separate development, staging and production registrations; localhost HTTP exceptions are development-only. Invalid client/redirect requests show a local error and never redirect to the supplied URI. These protocol choices follow [OAuth security best practice, RFC 9700](https://www.rfc-editor.org/rfc/rfc9700.html).

### 7.2 Cookie-free authorization sequence

1. The SPA generates a cryptographically random PKCE verifier (43–128 valid characters), its S256 challenge, and a high-entropy one-use `state`. Store verifier/state temporarily in tab-scoped sessionStorage solely for the redirect round trip.
2. Navigate to `GET /oauth/authorize` with `response_type=code`, `client_id`, the exact `redirect_uri`, permitted `scope`, `state`, `code_challenge`, and `code_challenge_method=S256`.
3. The server validates the complete request and creates a short-lived authorization transaction. Render a minimal first-party login page on the authorization origin. Use an unpredictable, one-use transaction proof embedded in that page and retained in memory/hidden form data; do not set a login-session cookie.
4. The login page posts email/password and transaction proof to `POST /auth/authorization-transactions/{id}/authenticate`. Validate expected Origin, transaction expiry, attempt limits and proof. Credentials never reach the SPA or its callback. The server derives client, redirect and PKCE values from its stored transaction, not replacement fields in the login POST.
5. On success, record the authenticated user and approved scopes, create a one-use authorization code bound to the transaction, and send HTTP 303 to the stored callback with `code` and the unchanged `state`. Failed login remains on the authorization origin. No access/refresh token is issued by the password-authentication endpoint.
6. The SPA checks state against its stored value, rejects unsolicited callbacks, then exchanges the code through `POST /oauth/token` using form-encoded `grant_type=authorization_code`, `client_id`, `code`, `redirect_uri`, and `code_verifier`.
7. The backend atomically checks code expiry/use, client, identical redirect URI, and PKCE before consuming the code and issuing the token pair. Duplicate exchanges fail. The SPA clears temporary verifier/state and immediately removes callback parameters from browser history.
8. Protected API requests use `Authorization: Bearer <access_token>`. No cookies are read or written for application authentication. Credentialed fetch is disabled. A full-page reload restores the session from the tab-scoped refresh token; closing the tab or restarting the browser requires login again, under the token-storage policy below.

The authorization page is part of the Python authorization service, even though the business UI is a React SPA. The cookie-free requirement means there is no persistent authorization-server SSO session. Transaction storage in PostgreSQL replaces a cookie-based login session; transactions expire after five minutes and issued codes after sixty seconds. These lifetimes are product policy, not protocol constants.

### 7.3 JWT access and refresh tokens

Both access and refresh tokens shall be signed JWTs, as requested. Use separate audiences, token types and validation paths. Access tokens are short-lived (initial policy: five minutes); refresh tokens have a maximum session lifetime of seven days, rotate on every use and do not extend that absolute lifetime.

Access claims include `iss`, `aud=pfa-api`, immutable user `sub`, `client_id`, `scope`, `sid`, `jti`, `iat`, `nbf`, and `exp`. Do not include CNP, passwords, ANAF credentials or financial records. Refresh claims include `aud=pfa-auth-refresh`, `token_use=refresh`, subject, client, session/family ID, unique `jti`, issue and expiry times. Refresh tokens are sent only to token/revocation endpoints, in the request body, never as resource-API bearer credentials.

Validate issuer, exact audience, token type, allowed signing algorithm, signature, time bounds and session status. Reject `none`, algorithm confusion and refresh tokens at the API. Signing keys live outside PostgreSQL in a managed secret/key service; expose public keys through JWKS and retain verification keys for the necessary rotation overlap.

Persist hashes of refresh tokens plus family, parent, expiry, use and revocation records. Rotate under a database lock/transaction: invalidate old token, create one replacement and commit atomically. Reuse of an invalidated refresh token revokes its family and requires login. The SPA serializes refresh attempts; do not introduce an unbound replay grace window to hide client races. A lost successful refresh response may require reauthentication.

Store the **access token only in browser memory**. Store the **refresh token tab-scoped in `sessionStorage`** so that reloading the page restores the session without a login. Never persist either token in `localStorage`, IndexedDB, a URL or a cookie, so nothing outlives the tab and nothing is attached to requests automatically. The temporary PKCE verifier/state remain the only other authentication values allowed in `sessionStorage`, and only for the redirect round trip. A stolen refresh token is contained by single-use rotation with whole-family revocation on reuse, the seven-day absolute session lifetime, server-side session checks and the strict Content-Security-Policy. Closing the tab or restarting the browser requires logging in again.

Logout revokes the session/family and clears memory. APIs check session revocation in server-side storage so logout/password reset can invalidate access before JWT expiry. Password reset revokes all user sessions; expiry and membership changes are enforced server-side.

### 7.4 Account security and browser controls

Use Argon2id password hashes with deployment-benchmarked parameters, generic login/reset responses, rate limits and increasing backoff. Verification/reset links are random, hashed at rest, short-lived and one-use. Email changes require reauthentication and new-address verification. Provide optional MFA, with the same submission policy for both roles.

Require TLS, strict CORS origin allowlists, no credentialed CORS, CSP without unrestricted inline scripts, output escaping, dependency review, frame protection, `Referrer-Policy: no-referrer` on authentication pages, and `Cache-Control: no-store` on authentication/token responses. Exclude third-party scripts from login and callback pages. Redact passwords, codes, token bodies and authorization headers from proxy/application telemetry. PKCE does not protect tokens from malicious JavaScript already running in the SPA.

## 8. System architecture

Use a modular monolith initially: React/TypeScript SPA; Python FastAPI services with Pydantic contracts; SQLAlchemy and Alembic for PostgreSQL; a durable Python worker for imports, generation and ANAF jobs; encrypted object storage for originals, generated artifacts and receipts. These are implementation choices; pin supported dependency versions when building.

Modules: identity/OAuth, workspaces and permissions, invoicing/expenses, settlements, assets/books, fiscal rules, obligations/declarations, ANAF connectors, notifications, audit and administration. Keep tax calculations separate from ANAF transport and from document rendering.

Use a PostgreSQL-backed job queue initially, with transactional outbox and worker leases; an external queue may replace transport later. Committing an accounting change and its required background event must be atomic. Workers are restartable and idempotent; no request waits synchronously for ANAF processing.

Static SPA hosting must route application paths to the SPA but keep authorization endpoints on their own service routes. Protected files stream through bearer-authorized endpoints; do not expose public object URLs. Browser downloads use authenticated fetch followed by a local Blob download.

## 9. PostgreSQL data model

Use UUID primary keys, UTC timestamps (`timestamptz`), local business dates (`date`), `NUMERIC(20,6)` for intermediate monetary values, and higher explicit precision for exchange rates. JSONB is for versioned snapshots and external payloads, not a substitute for searchable core columns.

| Domain | Tables and principal fields |
|---|---|
| Accounts | `users(id, email_normalized, password_hash, verified_at, status)`, verification/reset tokens, MFA factors |
| Tenancy | `workspaces`, `memberships(workspace_id, user_id, role, status), invitations(workspace_id, email, role, token_hash, expires_at, accepted_at, revoked_at)`, `taxpayers`, scoped taxpayer-access grants |
| Fiscal profile | `pfa_profiles`, `activity_periods`, `vat_registrations`, `tax_regimes`, `taxpayer_year_facts`, `loss_carryforwards` |
| OAuth registration | `oauth_clients(client_id, type, enabled, allowed_scopes)`, `oauth_redirect_uris(client_id, exact_uri)`, `oauth_origins` |
| OAuth runtime | `oauth_authorization_transactions(proof_hash, client_id, redirect_uri, challenge, state, expires_at, consumed_at)`, `oauth_authorization_codes(code_hash, user_id, client_id, redirect_uri, challenge, scopes, expires_at, used_at)`, `oauth_sessions`, `oauth_refresh_tokens(token_hash, jti, family_id, parent_id, expires_at, used_at, revoked_at)`. Every table used only by the authorization server is prefixed `oauth_`, so it can move to a separate service; they live in the authorization server's own database (`PFA_OAUTH_DATABASE_URL`, own migrations), never in the application database; no foreign key or transaction spans the two (`user_id` is the opaque subject id issued by the identity provider). |
| Documents | `counterparties`, `documents`, `document_lines`, `document_revisions`, `attachments`, `number_series`, linked credit notes |
| Money | `financial_accounts`, `payments`, `payment_allocations`, `bank_imports`, `exchange_rates` |
| Books | `accounting_events`, `tax_adjustments`, `assets`, `depreciation_entries`, `inventory_entries`, `period_locks` |
| Compliance | `rule_packs`, `form_versions`, `obligations`, `calculation_runs`, `declarations`, `declaration_versions`, `declaration_source_grants` |
| ANAF | `anaf_connections`, `connector_capabilities`, `submission_attempts`, `remote_messages`, `receipts` |
| Operations | `outbox_events`, `jobs`, `idempotency_keys`, `audit_events`, `notifications` |

All workspace-owned rows carry `workspace_id`. Composite foreign keys prevent cross-workspace references; row-level security provides defense in depth. The application role cannot bypass RLS; transaction-local tenant settings are established only after membership checks and cleared by transaction boundaries. Workers set and verify equivalent tenant context. Personal source records require explicit taxpayer authorization; a declaration stored in a workspace and its attachments are shared with every active member.

Constraints: case-insensitive unique account email; unique workspace/user membership; unique client/redirect pair; unique invoice number per workspace/series; valid nonoverlapping effective fiscal periods where mutually exclusive; unique code/token hashes; and unique import/remote identifiers in their tenant/connector namespace. Allocation totals cannot exceed available settlement/document amounts except explicitly modeled advances, credit or exchange differences; enforce concurrent allocations transactionally.

Index workspace/date and workspace/status queries, pending jobs, obligations/deadlines and remote references. Keep audit records append-only. Store ciphertext references for sensitive identifiers and connector secrets; keys are managed separately. Prevent cascading deletion of accepted declarations and statutory evidence.

## 10. Web-service contract

Publish versioned OpenAPI and generate TypeScript types from it. OAuth endpoints follow OAuth response/error conventions; business endpoints use JSON and a consistent problem-details error with code, message, field errors and correlation ID. Financial values cross JSON as decimal strings; dates use ISO 8601. Lists use cursor pagination and bounded filters.

| Method and path | Purpose |
|---|---|
| `POST /auth/register`, `/auth/verify-email`, `/auth/password-reset/request`, `/auth/password-reset/confirm` | Account lifecycle; never issue tokens through a password grant |
| `GET /oauth/authorize` | Start authorization and render login |
| `POST /auth/authorization-transactions/{id}/authenticate` | Authenticate within a validated authorization transaction |
| `POST /oauth/token` | Authorization-code exchange or rotating refresh grant |
| `POST /oauth/revoke`, `GET /.well-known/jwks.json` | Revoke a token family; publish verification keys |
| `GET /api/v1/me`, `POST /api/v1/sessions/logout` | Account/membership view and authenticated session logout |
| `POST/GET /api/v1/workspaces` | Create/list authorized PFA workspaces |
| `GET/PATCH /api/v1/workspaces/{w}/fiscal-profile` | Versioned fiscal facts and history |
| `POST/GET /api/v1/workspaces/{w}/documents` | Sales/expense creation and import |
| `POST /api/v1/workspaces/{w}/documents/{d}/finalize` | Lock an issued document under the series/number the user entered |
| `DELETE /api/v1/workspaces/{w}/documents/{d}` | Delete a document and everything derived from it |
| `POST /api/v1/workspaces/{w}/documents/{d}/credit-notes` | Linked correction |
| `POST/GET /api/v1/workspaces/{w}/payments` | Record settlements and allocations (each with the documents it covers) |
| `PATCH/DELETE /api/v1/workspaces/{w}/payments/{p}` | Correct or remove a recorded settlement |
| `POST/GET /api/v1/workspaces/{w}/assets` | Asset and depreciation inputs |
| `GET /api/v1/workspaces/{w}/registers/{type}` | Accounting registers and authenticated export jobs |
| `GET /api/v1/workspaces/{w}/obligations` | Period obligations and missing information |
| `POST /api/v1/taxpayers/{t}/declarations` | Prepare personal D212 with independently authorized source workspaces |
| `POST /api/v1/workspaces/{w}/declarations` | Prepare workspace VAT/other business returns |
| `POST /api/v1/declarations/{id}/calculate`, `/validate`, `/approve`, `/export`, `/submit` | Lifecycle actions with membership and state checks |
| `GET /api/v1/declarations/{id}/trace`, `/artifacts`, `/receipts` | Explain values and retrieve evidence |
| `POST /api/v1/workspaces/{w}/anaf/connect`, `/anaf/sync` | Begin ANAF authorization or schedule synchronization |
| `GET /integrations/anaf/callback` | Backend-only external OAuth callback |
| `GET/POST /api/v1/workspaces/{w}/invitations` | ADMIN lists/creates invitations; resend/revoke actions are ADMIN-only |
| `POST /api/v1/invitations/accept` | Verified recipient accepts a one-use invitation |
| `GET/PATCH/DELETE /api/v1/workspaces/{w}/members/{user}` | ADMIN manages membership; enforce last-ADMIN invariant |
| `POST /api/v1/workspaces/{w}/leave` | Leave subject to last-ADMIN invariant |
| `GET /api/v1/workspaces/{w}/notifications` | Shared obligations with per-user read state |
| `POST /api/v1/workspaces/{w}/notifications/{n}/read` | Mark read for current user only |
| `GET /api/v1/jobs/{id}` | Poll durable job status with workspace membership checks |

Require idempotency keys for finalization, generation and submission mutations. Bind each key to user/workspace, action and request hash; return the original result for a retry and reject different content under the same key. Use revision/ETag checks to reject stale edits with a conflict. Asynchronous calls return HTTP 202 with an authorized job reference.

## 11. React SPA requirements

Pages: signup/verification; login initiation and callback; PFA setup; dashboard; sales; expenses; bank/cash reconciliation; assets; accounting registers; tax profile; obligations; declaration review; ANAF connection/messages; members; security settings.

The dashboard separates issued revenue, collected revenue, expenses paid, estimated taxes, actual declared liabilities and tax payments. Never label an estimate as an ANAF balance. Show deadlines, incomplete inputs, rejected submissions and integration outages.

The declaration review page shows period, taxpayer identity, rule/form version, source completeness, computed fields, click-through calculation evidence and differences from the prior version. Submission is a separate action from generation. Explain personal-data sharing before consolidation; all sections of a saved workspace declaration are visible to both roles.

Use accessible forms, keyboard navigation, Romanian date/number formatting and clear empty/error/loading states. Frontend validation is advisory; backend validation is authoritative. Use a single in-memory auth controller to coordinate refresh and pause/retry eligible requests once; non-idempotent retries must carry the original idempotency key. Clear cached tenant data when switching workspaces or logging out. Offline views must not imply filing or synchronization succeeded.

## 12. Operations, privacy, and reliability

Encrypt transport, database volumes, backups and object storage. Protect CNP and identity evidence with field-level access and log redaction. Scan attachments, reject active content and archive/XML expansion attacks, disable XML external entities and apply size limits. Hash artifacts for integrity.

Maintain category-specific retention rules supported by current legal review, with legal holds and an auditable deletion process. Account closure must not silently destroy required fiscal evidence. Provide customer data exports and document the lawful handling of retained records and backups.

Initial engineering targets: 99.9% monthly availability excluding separately reported ANAF availability; ordinary reads under 500 ms at p95 and writes under 1 s at p95 under an agreed load profile; 15-minute recovery-point objective and four-hour recovery-time objective. Establish the benchmark at 100 concurrent active users and 10,000 documents per workspace. Large exports and imports run asynchronously. These are product targets, not measured results.

Use PostgreSQL point-in-time recovery, encrypted object-store versioning, restore drills, staged migrations, secrets rotation and dependency scanning. Monitor job age, failed validations, submission-unknown cases, connector expiry, upcoming deadlines, cross-tenant access denials and refresh replay. Notifications are deduplicated and never include raw sensitive tax data.

## 13. Acceptance scenarios

| ID | Scenario and expected result |
|---|---|
| AUTH-01 | Verified user signs in through code + S256 PKCE; no application `Set-Cookie`; callback exactly matches its database registration |
| AUTH-02 | Wrong verifier, reused/expired code, wrong client/redirect, unsupported challenge method or unsolicited state fails without tokens |
| AUTH-03 | Malicious redirect is rejected locally; password POST cannot replace the approved redirect or challenge |
| AUTH-04 | Concurrent refresh produces at most one replacement; later old-token reuse revokes the family; refresh JWT cannot access accounting APIs |
| AUTH-05 | Logout/reset revokes access server-side; the access token persists nowhere and only the tab-scoped refresh token is kept in sessionStorage, so closing the tab requires login again |
| TEN-01 | A user changes a workspace/document/job/file ID to another tenant's ID: denied at API and database boundaries |
| TEN-02 | Workspace member cannot see unshared personal source records or prepare a conflicting second D212 for the same taxpayer/year; saved workspace declarations are shared |
| ACC-01 | RON 10,000 invoice collected RON 4,000 in December and RON 6,000 in January produces separate settlement-year events; no double counting on import |
| ACC-02 | Paid expense with 50% deductible business use has an independently calculated income-tax adjustment and VAT deduction |
| ACC-03 | Owner loan/deposit is visible in cash records but not automatically taxable business income |
| ACC-04 | Capital asset does not receive both full expense deduction and depreciation; disposal updates its schedule |
| TAX-01 | Approved fixtures test amounts immediately below, equal to and above each CAS/CASS/registration threshold, including zero/loss and exemption cases |
| TAX-02 | 2025 and 2026 cases pin different CASS ceiling parameters; recalculating a historical accepted return does not overwrite it |
| TAX-03 | Norm-tax profile uses published norm and applicable adjustments rather than actual profit |
| VAT-01 | Article 317 PFA buying a qualifying EU B2B service is assessed for D301/D390 without being treated as normal domestic VAT registered |
| VAT-02 | Non-EU service purchase may require D301 but does not become D390 merely because it is foreign; classification evidence determines result |
| VAT-03 | VAT tax point and cash-recognition date differ correctly; historical credit note retains the legally applicable original-rate treatment |
| DEC-01 | Unknown personal-income facts block final D212 approval; a missing form-year schema produces only an estimate/workpaper |
| DEC-02 | Generated artifact passes the pinned official validator and reconciles to registers; every material field has a trace |
| DEC-03 | Changing input after approval invalidates that approval; correction creates a new linked immutable version |
| ANAF-01 | HTTP upload success without receipt remains pending; timeout enters unknown state and reconciles before resubmission |
| ANAF-02 | Rejected receipt shows ANAF errors; accepted receipt is bound to taxpayer, period and submitted artifact |
| ANAF-03 | e-Factura retry/import does not create duplicate accounting documents; working-day deadline uses the applicable holiday calendar |
| OPS-01 | Worker crash/restart preserves one logical job; backup restore meets the agreed recovery targets and restores artifacts and receipt links |

Use unit tests for fiscal rules, integration tests against PostgreSQL and authorization endpoints, browser tests for the SPA flow, official-validator golden fixtures, and connector contract tests. Use synthetic/anonymized taxpayer data. Where ANAF offers no sandbox, use recorded sanitized fixtures and an explicitly authorized controlled pilot before enabling production submissions.

## 14. Delivery plan and release gates

1. **Foundation:** account lifecycle, cookie-free authorization proof of concept, PKCE/JWT rotation, tenant isolation, profile history, audit and document storage. Gate: AUTH/TEN tests pass and authentication receives security review.
2. **Accounting:** invoices, expense evidence, settlements, assets, registers, imports and close/reopen. Gate: representative accountant-approved books reconcile to source documents.
3. **Core declarations:** obligation catalogue, real/norm D212, standard VAT forms, registration assistance and exact official artifact generation. Gate: each advertised form/year/profile has approved rules, field mappings and validator fixtures.
4. **ANAF delivery:** e-Factura, receipts and approved per-form filing adapters; validated export remains available. Gate: prove authorization, signing, error/retry behavior and acceptance evidence for each supported channel.
5. **Extended coverage:** payroll, withholding, special VAT and exceptional transaction modules. Gate: required additional data, calculations, artifacts and receipts meet the same standards; remove onboarding restrictions only as support becomes complete.

Before a production fiscal pack is approved, its owner must complete: exact statutory rates and base definitions; legally relevant minimum-wage values/dates; deduction and loss rules; county/activity norm tables; all supported scenario predicates; filing and payment calendars; official form row mappings and artifacts; correction rules; and retention requirements. Obtain Romanian tax-professional review of those packs and a security review of the authorization implementation. These are release requirements, not claims that reviews have already happened.

Before enabling a submission adapter, record the verified official endpoint/channel, environment, capabilities, signing requirements, delegated taxpayer identity, schema/validator version and receipt behavior. If any of those is unknown, generation/export may be available but automated submission stays disabled with an explanation.

## 15. Completion definition

For a supported PFA profile, users can enter source evidence and settlements, resolve required fiscal questions, generate reconciled accounting records, see every evaluated obligation, produce officially validated submission artifacts, and retain verified filing receipts. Unsupported obligations remain visible. Every amount can be traced to inputs and a versioned rule. Both roles share all workspace documents, ADMIN alone manages users, dated notifications track outstanding filings and payments, and every implemented feature has automated acceptance coverage. PostgreSQL development and tests run through isolated Docker Compose environments. Every business API requires bearer authorization, every authorization code uses PKCE and a database-registered redirect URI, and no application authentication relies on cookies.

## 16. Simple user experience and deadline notifications

### 16.1 Navigation and everyday tasks

Use five primary navigation entries: **Acasă**, **Venituri**, **Cheltuieli**, **Declarații**, **Setări**. Put receipts, settlements, imports, assets and registers within the relevant page or an advanced section instead of adding primary navigation. ADMIN sees **Utilizatori** inside Settings. Show the active PFA name on every page; require an explicit workspace switch.

The home screen answers three questions: what needs attention, when it is due, and what to do next. Order overdue and rejected items first, then upcoming deadlines, then incomplete records. Use plain Romanian labels, explanatory text and dates such as “25 mai 2026”; color must not be the only status indicator. Keep form codes secondary to readable names.

For an income or expense record, the normal path is **Adaugă document → Verifică datele → Salvează**. Ask “Ai încasat?” or “Ai plătit?”; a Yes answer reveals amount, date and bank/cash source and creates a separate linked settlement. Support partial payment. Do not preselect Yes merely because a receipt attachment exists. Advanced tax details appear only when relevant. Save drafts without claiming they are tax-ready; retain user input on validation failures and session expiry using server-side authorized drafts, without persisting tokens locally.

Declaration flow: **Verifică informațiile → Generează → Descarcă pentru ANAF → Încarcă recipisa**. Provide a direct official destination and short form-specific upload instructions. If a validated API is available, offer **Trimite la ANAF** after reviewing the exact artifact. Display generation, filing acceptance and payment as separate statuses. Keep calculations and legal references under “Cum s-a calculat?”. Both roles follow the same workflow.

Usability acceptance: representative users with basic digital skills can enter a paid expense, record a partial collection, find their next deadline and download the correct return without developer assistance. All critical flows must work with keyboard navigation and a narrow mobile viewport; errors explain the corrective action beside the field.

### 16.2 Notification contract

An obligation has `workspace_id`, taxpayer, form/document type, reporting-period start/end, applicability state, triggering evidence IDs, rule version, `generation_available_from`, `filing_due_date`, optional filing cutoff/timezone, optional `payment_due_date`, deadline provenance, required missing inputs, declaration revision and resolution status. Generation readiness is separate from the legal deadline. Unknown deadlines display “Termen de verificat” and create an action; never invent a date.

Every actionable notification shows PFA, readable document name and form code, period, **absolute end date**, days remaining/overdue, why it applies, missing information and one next-step button. Keep filing and payment dates distinct. Example using the official historical deadline: “Declarația unică (D212), venituri 2025 — depunere până la 25 mai 2026 inclusiv — Verifică și generează.” ANAF confirms that date in its [May 2026 notice](https://static.anaf.ro/static/3/Cluj/20260522102135_25_%20mai_%202026%20.pdf). On 16 September 2026 an unresolved instance is overdue, not upcoming.

Evaluate obligations when documents, settlements, taxpayer facts, registration periods or approved rules change, and reconcile nightly. Create due items even when there are no documents if the relevant registered profile requires a nil return. A newly entered late document must immediately reconsider historical periods and possible correction obligations.

Default in-app reminders occur on detection and at 30, 14, 7, 3 and 1 calendar days before filing, on the due date, and weekly while overdue. Short-window obligations such as e-Factura appear immediately with a one-day reminder and due/overdue status. These reminder offsets do not change statutory working-day calculations. When several offsets were missed before detection, produce one current reminder rather than a flood. Run scheduled evaluation at least hourly using Europe/Bucharest; persist a transactional outbox so downtime catches up.

Deduplicate on workspace, obligation, deadline revision, reminder stage and recipient. Each active member sees the same underlying obligation; read/dismiss/snooze state is per user. Reading or dismissing a notification does not complete a filing, hide its deadline card, or hide it from colleagues. Snooze cannot suppress overdue status. Generation/download does not stop filing reminders. Verified acceptance resolves filing reminders; recorded payment evidence resolves payment reminders independently. Rejected filings reopen attention immediately. Removal from a PFA stops delivery and access.

In-app notifications are required. Email reminders are optional per user, contain minimal data and link back to an authenticated page. Changes to a legal deadline update all pending reminders, preserve the old date and reason in history, and notify affected members once. Deadline engines must use each form's rules and approved calendar rather than applying a universal “25th” rule; event-based registrations and e-Factura have separate deadline strategies.

Add `notification_deliveries`, `notification_user_states` and versioned `deadline_rules` to the data model. Notifications are a view of persisted obligations, not the source of compliance truth.

## 17. Docker Compose and development/test environment

The implementation repository must provide `compose.yaml`, `.env.example`, migrations, documented setup commands and a separate test Compose project. PostgreSQL is required for local development and integration tests; SQLite is not a substitute because isolation, NUMERIC, locking and RLS are acceptance requirements.

The implementation is organised as two independent projects: `backend/` (Python services, worker, migrations; it owns `backend/compose.yaml`, `backend/.env.example`, the separate `backend/compose.test.yaml` project and its own Makefile) and `frontend/` (the SPA; it owns its `package.json`, `.env` files and browser tests). The projects share no source or configuration and integrate over HTTP with OAuth 2.0 authorization-code + PKCE and an explicit CORS allowlist. The repository root provides integration commands that wire the two together for development and CI, but each project builds, tests and runs on its own.

The Compose PostgreSQL service must pin a supported major version and release digest, use a named development data volume, expose only `127.0.0.1:5432`, read local credentials from an ignored environment file, and use `pg_isready` for its health check. Production secrets must never be committed. Tests use a different project, database and disposable volume/port with no access to development data. Test helpers reject any database not explicitly configured as a test database.

Provide these implementation commands:

```sh
make setup      # install both projects
make up         # development PostgreSQL
make migrate    # migrations (owner/DDL role)
make seed-dev   # synthetic data
make dev        # SPA + Python API + worker
make test       # backend and frontend test suites
make e2e        # browser tests against the stack
```

`make dev` starts the SPA, Python API and worker across the two projects (each project also has its own `dev` command). `make seed-dev` is idempotent and creates synthetic real/norm PFAs, an ADMIN and DEFAULT per workspace, example documents and an exact localhost SPA callback registration. It must refuse production configuration. A separate migration/admin connection creates schema; runtime API and worker roles cannot bypass RLS. A seed must not give runtime roles superuser rights.

`make test` starts an isolated Compose PostgreSQL project, applies migrations, runs Python unit/integration tests and frontend component tests, and cleans up only its own resources. `make e2e` runs browser tests against the application with a deterministic fake ANAF service and test email capture. Parallel suites get separate databases or schemas with independently scoped connections. CI uses the same PostgreSQL major and migrations. An interruption must not delete the development volume. Document ordinary stop/restart and an explicitly destructive development reset separately.

These commands are the required implementation interface; this specification repository does not yet contain an application or executable test runner.

## 18. Feature coverage and acceptance-test traceability

Every feature in sections 1–17 must have a stable requirement ID and mapped automated tests before implementation is accepted. Maintain `tests/requirements.yaml` mapping requirement, test IDs and implementation status. CI fails for an implemented feature with no mapped test, skipped required tests, failing tests or missing migration checks. Coverage percentages supplement this feature mapping; they do not prove feature completeness.

Use pytest for domain/API tests, React Testing Library with Vitest for components and Playwright for end-to-end flows. Dependency versions are pinned at implementation time. All fiscal expected results are independently reviewed fixtures, not values generated by the same production calculator. Test monetary results exactly with Decimal; freeze time and isolate external network calls.

| Requirement | Required coverage, in addition to section 13 |
|---|---|
| IDENTITY | Register, duplicate email, verification success/expiry/replay, wrong credentials, rate limits, email change, reset, session revocation; no cookies on any authentication path |
| OAUTH | Valid PKCE login and refresh; all invalid client, redirect, state, verifier, scope, code and JWT variants; atomic replay protection; signing-key rotation; callback cleanup; CORS and no persistent tokens |
| MEMBERS | First ADMIN, invite existing/new user, expiry/replay/revocation/email mismatch, ADMIN-only management, role changes, concurrent last-ADMIN removal, departure, immediate removal effects |
| SHARING | ADMIN and DEFAULT see/edit each other's documents and see all generated artifacts/receipts; creator is irrelevant; cross-PFA API/file/job/export access denied; switching PFA clears cached data |
| PROFILE | Real and norm onboarding, missing facts, VAT types, effective-date changes, overlapping-period rejection, suspension/cessation, multiple activities and explicitly authorized personal consolidation |
| DOCUMENTS | Invoice issue/number races, draft edits, import/OCR review, duplicates, attachment type/size checks, credit notes, stale edits, authenticated downloads and missing evidence |
| SETTLEMENTS | Unpaid/partial/full receipts/payments, cross-year settlement, advances/refunds, gross/net processor fees, owner transfers/loans, foreign currency, concurrent over-allocation prevention |
| BOOKS | Deductibility versus VAT recovery, assets/depreciation/disposal, inventory, registers, close/reopen, reconciliation and historical immutability |
| REAL | Cash recognition, allowable expenses, adjustments, loss rules, contribution deductions, exemptions and threshold boundaries; compare D212 totals to independent expected results |
| NORM | County/activity/year selection, legal adjustments and prorating, missing norm data, mixed-activity eligibility, turnover-transition boundaries and required next-year regime; operating expenses do not reduce norm taxable base |
| VAT | Article 316/317/no-VAT profiles; domestic/EU/non-EU place-of-supply scenarios, registration turnover, rate transitions, tax point, reverse charge and credit notes |
| OBLIGATIONS | Each catalogue form has required/not-required/unknown fixtures, nil-return behavior, historical late entry, unsupported profile visibility and missing-schema blocking |
| FORMS | Each advertised form/year has accepted official-validator fixtures, negative artifacts, field-level trace, correct taxpayer identity, amendments and preserved prior snapshots |
| ANAF | Export/upload instructions, authorization expiry, validated XML, import, retries, unknown delivery reconciliation, rejection/acceptance receipts and mismatch rejection; no duplicate submissions |
| NOTIFICATIONS | Absolute filing/payment dates, working-day/holiday/leap-year/DST boundaries, each reminder stage, short deadlines, deduplication, catch-up, deadline revisions, per-user read/snooze, removal and completion semantics |
| UI | Both roles complete document-to-declaration workflows; Romanian formatting, mobile/keyboard operation, loading/error/empty states, expired login, missing fiscal data and clear upload instructions |
| API/DATA | OpenAPI contract, decimal serialization, pagination, invalid inputs, optimistic concurrency, idempotency, RLS, composite tenant foreign keys, audit trail and safe file handling |
| OPERATIONS | Worker/outbox crashes, leasing and retries, migrations on empty/existing databases, backup restoration including artifacts, redacted logs and isolated Compose test cleanup |
| EXTENSIONS | Every enabled payroll/withholding/special-scheme module gains the same applicability, calculation, artifact, failure, permission and UI coverage before being advertised |

Release requires a full journey for both real and norm: register → complete setup → invite DEFAULT → each user enters shared evidence → settle documents → view dated obligation → resolve missing facts → generate and validate the correct artifact → export → import a matching acceptance receipt. Include an independent payment completion step and rejection/recovery journey. The suite must demonstrate that a DEFAULT can complete all business steps while user management remains ADMIN-only.

This document defines required tests, not passed test results. Legal rule packs, current form schemas and submission channels remain release-gated as described in section 14.
