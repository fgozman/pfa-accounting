-- Frozen schema of revision 0001_initial (PostgreSQL DDL).
--
-- This file is the schema as it stood when revision 0001 was written.  It is
-- deliberately NOT generated from the live SQLAlchemy models at migration time:
-- later revisions (0003+) alter this exact shape, so a baseline that tracked the
-- models would already contain their changes and every fresh `upgrade head`
-- would fail.  Never edit this file; add a new revision instead.
--
-- Statements are separated by ';' at end of line and contain no function bodies.

CREATE TABLE public.accounting_events (
    event_date date NOT NULL,
    event_kind character varying(32) NOT NULL,
    document_id uuid,
    payment_id uuid,
    asset_id uuid,
    amount_ron numeric(20,6) NOT NULL,
    vat_amount_ron numeric(20,6) NOT NULL,
    taxable_amount_ron numeric(20,6) NOT NULL,
    recognition character varying(16) NOT NULL,
    category character varying(64),
    description character varying(400),
    feeds_forms jsonb,
    created_by_user_id uuid,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    workspace_id uuid NOT NULL
);

CREATE TABLE public.activity_periods (
    caen_code character varying(8) NOT NULL,
    description character varying(200),
    starts_on timestamp with time zone NOT NULL,
    ends_on timestamp with time zone,
    suspended_from timestamp with time zone,
    suspension_reason character varying(200),
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    workspace_id uuid NOT NULL
);

CREATE TABLE public.anaf_connections (
    environment character varying(16) NOT NULL,
    connector_client_ref character varying(200),
    access_token_ciphertext character varying(2000),
    refresh_token_ciphertext character varying(2000),
    token_expires_at timestamp with time zone,
    allowed_taxpayers jsonb,
    authorized_cif character varying(20),
    authorized_identity character varying(200),
    scopes jsonb,
    status character varying(16) NOT NULL,
    connected_at timestamp with time zone,
    last_sync_at timestamp with time zone,
    expires_at timestamp with time zone,
    last_error character varying(500),
    pending_state_hash character varying(64),
    created_by_user_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    workspace_id uuid NOT NULL
);

CREATE TABLE public.assets (
    name character varying(300) NOT NULL,
    category character varying(64) NOT NULL,
    acquisition_date date NOT NULL,
    in_service_date date NOT NULL,
    acquisition_cost numeric(20,6) NOT NULL,
    recoverable_vat numeric(20,6) NOT NULL,
    nonrecoverable_vat numeric(20,6) NOT NULL,
    currency character varying(3) NOT NULL,
    acquisition_cost_ron numeric(20,6) NOT NULL,
    business_use_percent numeric(9,6) NOT NULL,
    depreciation_method character varying(40) NOT NULL,
    useful_life_months integer NOT NULL,
    status character varying(40) NOT NULL,
    document_id uuid,
    disposal_date date,
    disposal_proceeds numeric(20,6),
    disposal_notes character varying(400),
    created_by_user_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    workspace_id uuid NOT NULL
);

CREATE TABLE public.attachments (
    document_id uuid,
    evidence_kind character varying(40) NOT NULL,
    filename character varying(300) NOT NULL,
    content_type character varying(128) NOT NULL,
    size_bytes integer NOT NULL,
    sha256 character varying(64) NOT NULL,
    object_key character varying(400) NOT NULL,
    scan_status character varying(16) NOT NULL,
    uploaded_by_user_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    workspace_id uuid NOT NULL
);

CREATE TABLE public.audit_events (
    workspace_id uuid,
    actor_user_id uuid,
    actor_kind character varying(16) NOT NULL,
    action character varying(64) NOT NULL,
    entity_type character varying(48) NOT NULL,
    entity_id uuid,
    before jsonb,
    after jsonb,
    ip_address character varying(64),
    user_agent character varying(300),
    correlation_id character varying(64),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL
);

CREATE TABLE public.authorization_codes (
    code_hash character varying(64) NOT NULL,
    user_id uuid NOT NULL,
    client_id uuid NOT NULL,
    transaction_id uuid NOT NULL,
    redirect_uri character varying(500) NOT NULL,
    code_challenge character varying(128) NOT NULL,
    code_challenge_method character varying(8) NOT NULL,
    scope character varying(500) NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    used_at timestamp with time zone,
    session_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL
);

CREATE TABLE public.authorization_transactions (
    client_id uuid NOT NULL,
    redirect_uri character varying(500) NOT NULL,
    scope character varying(500) NOT NULL,
    state character varying(500) NOT NULL,
    code_challenge character varying(128) NOT NULL,
    code_challenge_method character varying(8) NOT NULL,
    proof_hash character varying(64) NOT NULL,
    origin_hash character varying(64),
    expires_at timestamp with time zone NOT NULL,
    consumed_at timestamp with time zone,
    authenticated_user_id uuid,
    authenticated_at timestamp with time zone,
    attempt_count integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL
);

CREATE TABLE public.bank_imports (
    account_id uuid,
    status character varying(16) NOT NULL,
    original_filename character varying(300),
    object_key character varying(400),
    rows_total integer NOT NULL,
    rows_imported integer NOT NULL,
    rows_duplicate integer NOT NULL,
    created_by_user_id uuid NOT NULL,
    details jsonb,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    workspace_id uuid NOT NULL
);

CREATE TABLE public.calculation_runs (
    workspace_id uuid NOT NULL,
    declaration_id uuid,
    taxpayer_id uuid,
    form_code character varying(16) NOT NULL,
    income_year integer NOT NULL,
    rule_pack_code character varying(64),
    rule_pack_version integer,
    engine_version character varying(32) NOT NULL,
    input_snapshot jsonb NOT NULL,
    input_snapshot_sha256 character varying(64) NOT NULL,
    result jsonb NOT NULL,
    trace jsonb NOT NULL,
    unresolved jsonb NOT NULL,
    created_by_user_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL
);

CREATE TABLE public.connector_capabilities (
    form_code character varying(16) NOT NULL,
    environment character varying(16) NOT NULL,
    channel character varying(40) NOT NULL,
    endpoint_url character varying(500),
    signing_requirement character varying(200),
    schema_version character varying(64),
    validator_version character varying(64),
    receipt_behavior character varying(500),
    delegation_supported boolean,
    test_environment_available boolean,
    verified_at timestamp with time zone,
    verified_by character varying(200),
    enabled boolean NOT NULL,
    evidence jsonb,
    notes character varying(1000),
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.counterparties (
    name character varying(300) NOT NULL,
    country_code character varying(2) NOT NULL,
    party_kind character varying(16) NOT NULL,
    vat_identifier character varying(32),
    vat_verified_at timestamp with time zone,
    vat_verification_source character varying(200),
    tax_identifier character varying(32),
    address character varying(400),
    county character varying(64),
    locality character varying(128),
    is_related_party boolean NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    workspace_id uuid NOT NULL
);

CREATE TABLE public.deadline_rules (
    form_code character varying(16) NOT NULL,
    jurisdiction character varying(8) NOT NULL,
    version integer NOT NULL,
    rule_kind character varying(32) NOT NULL,
    parameters jsonb NOT NULL,
    timezone character varying(64) NOT NULL,
    payment_rule_kind character varying(32),
    payment_parameters jsonb,
    effective_from date NOT NULL,
    effective_to date,
    source_url character varying(500),
    source_note character varying(500),
    reviewed boolean NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.declaration_source_grants (
    declaration_id uuid NOT NULL,
    source_workspace_id uuid,
    taxpayer_id uuid,
    granted_by_user_id uuid NOT NULL,
    granted_at timestamp with time zone DEFAULT now() NOT NULL,
    sharing_notice_version character varying(32),
    revoked_at timestamp with time zone,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    workspace_id uuid NOT NULL
);

CREATE TABLE public.declaration_versions (
    workspace_id uuid NOT NULL,
    declaration_id uuid NOT NULL,
    version integer NOT NULL,
    status character varying(40) NOT NULL,
    input_snapshot jsonb NOT NULL,
    input_snapshot_sha256 character varying(64) NOT NULL,
    calculation jsonb NOT NULL,
    trace jsonb NOT NULL,
    calculated_values jsonb NOT NULL,
    form_schema_version character varying(64),
    rule_pack_code character varying(64),
    rule_pack_version integer,
    artifact_object_key character varying(400),
    artifact_sha256 character varying(64),
    artifact_format character varying(24),
    artifact_is_preview boolean NOT NULL,
    created_by_user_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL
);

CREATE TABLE public.declarations (
    taxpayer_id uuid,
    form_code character varying(16) NOT NULL,
    form_name_ro character varying(200),
    income_year integer NOT NULL,
    period_start date NOT NULL,
    period_end date NOT NULL,
    status character varying(40) NOT NULL,
    form_version_id uuid,
    rule_pack_code character varying(64),
    rule_pack_version integer,
    taxpayer_identity jsonb NOT NULL,
    taxpayer_key character varying(64),
    current_version integer NOT NULL,
    correction_of_declaration_id uuid,
    estimate_only boolean NOT NULL,
    created_by_user_id uuid NOT NULL,
    approved_by_user_id uuid,
    approved_at timestamp with time zone,
    generated_at timestamp with time zone,
    submitted_at timestamp with time zone,
    accepted_at timestamp with time zone,
    rejected_at timestamp with time zone,
    submission_available boolean NOT NULL,
    submission_unavailable_reason character varying(500),
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    workspace_id uuid NOT NULL
);

CREATE TABLE public.deduction_rules (
    code character varying(64) NOT NULL,
    income_year integer NOT NULL,
    domain character varying(16) NOT NULL,
    parameters jsonb NOT NULL,
    limit_amount numeric(20,6),
    legal_reference character varying(200),
    effective_from date NOT NULL,
    effective_to date,
    source_url character varying(500),
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.depreciation_entries (
    asset_id uuid NOT NULL,
    income_year integer NOT NULL,
    period_month integer,
    amount numeric(20,6) NOT NULL,
    deductible_amount numeric(20,6) NOT NULL,
    book_value_after numeric(20,6) NOT NULL,
    rule_pack_id uuid,
    calculation_note character varying(300),
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    workspace_id uuid NOT NULL
);

CREATE TABLE public.document_imports (
    source character varying(40) NOT NULL,
    status character varying(16) NOT NULL,
    original_filename character varying(300),
    object_key character varying(400),
    detected_duplicates jsonb,
    created_by_user_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    workspace_id uuid NOT NULL
);

CREATE TABLE public.document_lines (
    document_id uuid NOT NULL,
    line_number integer NOT NULL,
    description character varying(400) NOT NULL,
    quantity numeric(20,6) NOT NULL,
    unit_price numeric(20,6) NOT NULL,
    discount_amount numeric(20,6) NOT NULL,
    vat_rate numeric(9,6) NOT NULL,
    vat_treatment character varying(40) NOT NULL,
    net_amount numeric(20,6) NOT NULL,
    vat_amount numeric(20,6) NOT NULL,
    gross_amount numeric(20,6) NOT NULL,
    business_use_percent numeric(9,6) NOT NULL,
    deductibility_percent numeric(9,6) NOT NULL,
    expense_category character varying(64),
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    workspace_id uuid NOT NULL
);

CREATE TABLE public.document_revisions (
    workspace_id uuid NOT NULL,
    document_id uuid NOT NULL,
    revision integer NOT NULL,
    reason character varying(300),
    snapshot jsonb NOT NULL,
    created_by_user_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL
);

CREATE TABLE public.documents (
    kind character varying(40) NOT NULL,
    status character varying(40) NOT NULL,
    source character varying(40) NOT NULL,
    counterparty_id uuid,
    supplier_name_raw character varying(300),
    series character varying(24),
    number character varying(32),
    issue_date date,
    supply_date date,
    due_date date,
    currency character varying(3) NOT NULL,
    total_amount numeric(20,6) NOT NULL,
    total_vat numeric(20,6) NOT NULL,
    total_amount_ron numeric(20,6),
    exchange_rate_id uuid,
    vat_treatment character varying(40) NOT NULL,
    deductibility character varying(40) NOT NULL,
    business_use_percent numeric(9,6) DEFAULT 100 NOT NULL,
    business_purpose character varying(400),
    deductibility_justification character varying(500),
    reverse_charge_justification character varying(500),
    expense_category character varying(64),
    is_tax_ready boolean NOT NULL,
    revision integer NOT NULL,
    external_id character varying(200),
    content_hash character varying(64),
    corrects_document_id uuid,
    correction_reason character varying(300),
    created_by_user_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    workspace_id uuid NOT NULL
);

CREATE TABLE public.email_verification_tokens (
    user_id uuid NOT NULL,
    email_normalized character varying(320) NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    token_hash character varying(64) NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    used_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.exchange_rates (
    workspace_id uuid,
    currency character varying(3) NOT NULL,
    quote_currency character varying(3) NOT NULL,
    rate_date date NOT NULL,
    rate numeric(28,12) NOT NULL,
    source character varying(32) NOT NULL,
    source_reference character varying(300),
    purpose character varying(32) NOT NULL,
    supplied_by_user_id uuid,
    evidence_object_key character varying(400),
    is_official boolean NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.financial_accounts (
    name character varying(200) NOT NULL,
    kind character varying(16) NOT NULL,
    currency character varying(3) NOT NULL,
    iban character varying(34),
    opening_balance numeric(20,6) NOT NULL,
    active boolean NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    workspace_id uuid NOT NULL
);

CREATE TABLE public.form_versions (
    form_code character varying(16) NOT NULL,
    income_year integer NOT NULL,
    schema_version character varying(64) NOT NULL,
    artifact_format character varying(24) NOT NULL,
    official_schema_url character varying(500),
    official_schema_sha256 character varying(64),
    validator_id character varying(200),
    filing_channel character varying(32) NOT NULL,
    supported boolean NOT NULL,
    channel_evidence jsonb,
    correction_strategy character varying(64),
    published boolean NOT NULL,
    effective_from date,
    effective_to date,
    notes character varying(1000),
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.idempotency_keys (
    workspace_id uuid,
    user_id uuid NOT NULL,
    action character varying(64) NOT NULL,
    key character varying(120) NOT NULL,
    request_sha256 character varying(64) NOT NULL,
    resource_type character varying(48),
    resource_id uuid,
    response jsonb,
    status_code integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone,
    id uuid DEFAULT gen_random_uuid() NOT NULL
);

CREATE TABLE public.income_norms (
    income_year integer NOT NULL,
    county character varying(64) NOT NULL,
    locality character varying(128),
    caen_code character varying(8) NOT NULL,
    activity_name character varying(200),
    norm_amount numeric(20,6) NOT NULL,
    adjustment_factors jsonb,
    source_url character varying(500),
    approved_on date,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.inventory_entries (
    income_year integer NOT NULL,
    entry_kind character varying(24) NOT NULL,
    occurred_on date NOT NULL,
    item_name character varying(300) NOT NULL,
    quantity numeric(20,6) NOT NULL,
    unit_cost numeric(20,6) NOT NULL,
    value numeric(20,6) NOT NULL,
    document_id uuid,
    note character varying(400),
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    workspace_id uuid NOT NULL
);

CREATE TABLE public.invitations (
    workspace_id uuid NOT NULL,
    email_normalized character varying(320) NOT NULL,
    role character varying(40) NOT NULL,
    token_hash character varying(64) NOT NULL,
    status character varying(40) NOT NULL,
    invited_by_user_id uuid NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    accepted_at timestamp with time zone,
    accepted_by_user_id uuid,
    revoked_at timestamp with time zone,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.jobs (
    kind character varying(48) NOT NULL,
    payload jsonb NOT NULL,
    status character varying(40) NOT NULL,
    priority integer NOT NULL,
    attempts integer NOT NULL,
    max_attempts integer NOT NULL,
    available_at timestamp with time zone DEFAULT now() NOT NULL,
    leased_by character varying(64),
    lease_expires_at timestamp with time zone,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    last_error character varying(1000),
    result jsonb,
    logical_key character varying(160),
    created_by_user_id uuid,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    workspace_id uuid NOT NULL
);

CREATE TABLE public.legal_calendar_days (
    day date NOT NULL,
    is_non_working boolean NOT NULL,
    reason character varying(200),
    calendar_version character varying(32) NOT NULL,
    source_url character varying(500),
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.loss_carryforwards (
    income_year integer NOT NULL,
    amount numeric(20,6) NOT NULL,
    consumed_amount numeric(20,6) NOT NULL,
    expires_after_year integer,
    source_calculation_id uuid,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    workspace_id uuid NOT NULL
);

CREATE TABLE public.memberships (
    workspace_id uuid NOT NULL,
    user_id uuid NOT NULL,
    role character varying(40) NOT NULL,
    status character varying(40) NOT NULL,
    joined_at timestamp with time zone DEFAULT now() NOT NULL,
    removed_at timestamp with time zone,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.mfa_factors (
    user_id uuid NOT NULL,
    kind character varying(32) NOT NULL,
    secret_ciphertext character varying(512) NOT NULL,
    confirmed_at timestamp with time zone,
    disabled_at timestamp with time zone,
    id uuid DEFAULT gen_random_uuid() NOT NULL
);

CREATE TABLE public.notification_deliveries (
    notification_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    recipient_user_id uuid NOT NULL,
    channel character varying(16) NOT NULL,
    status character varying(16) NOT NULL,
    attempted_at timestamp with time zone,
    delivered_at timestamp with time zone,
    error character varying(500),
    dedupe_key character varying(240) NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL
);

CREATE TABLE public.notification_user_states (
    notification_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    user_id uuid NOT NULL,
    read_at timestamp with time zone,
    dismissed_at timestamp with time zone,
    snoozed_until date,
    email_reminders boolean NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL
);

CREATE TABLE public.notifications (
    workspace_id uuid NOT NULL,
    obligation_id uuid NOT NULL,
    stage character varying(40) NOT NULL,
    reminder_offset_days integer,
    deadline_revision integer NOT NULL,
    title_ro character varying(300) NOT NULL,
    body_ro character varying(1000) NOT NULL,
    due_on date,
    payment_due_on date,
    next_step_kind character varying(32),
    next_step_ref character varying(64),
    dedupe_key character varying(200) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    resolved_at timestamp with time zone,
    id uuid DEFAULT gen_random_uuid() NOT NULL
);

CREATE TABLE public.number_series (
    document_kind character varying(40) NOT NULL,
    prefix character varying(24) NOT NULL,
    last_number integer NOT NULL,
    year integer,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    workspace_id uuid NOT NULL
);

CREATE TABLE public.oauth_clients (
    client_id character varying(64) NOT NULL,
    client_type character varying(16) NOT NULL,
    token_endpoint_auth_method character varying(32) NOT NULL,
    environment character varying(16) NOT NULL,
    enabled boolean NOT NULL,
    require_pkce boolean NOT NULL,
    grant_types jsonb NOT NULL,
    response_types jsonb NOT NULL,
    allowed_scopes jsonb NOT NULL,
    allowed_pkce_methods jsonb NOT NULL,
    client_secret_hash character varying(255),
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.oauth_origins (
    client_id uuid NOT NULL,
    origin character varying(500) NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL
);

CREATE TABLE public.oauth_redirect_uris (
    client_id uuid NOT NULL,
    exact_uri character varying(500) NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL
);

CREATE TABLE public.obligations (
    taxpayer_id uuid,
    form_code character varying(16) NOT NULL,
    form_name_ro character varying(200),
    obligation_kind character varying(24) NOT NULL,
    period_start date NOT NULL,
    period_end date NOT NULL,
    applicability character varying(40) NOT NULL,
    applicability_reason character varying(1000) NOT NULL,
    rule_pack_code character varying(64),
    rule_pack_version integer,
    legal_rule_version character varying(64),
    source_url character varying(500),
    generation_available_from date,
    generation_blocked_reason character varying(500),
    filing_due_date date,
    filing_cutoff_at timestamp with time zone,
    filing_timezone character varying(64) NOT NULL,
    payment_due_date date,
    deadline_rule_id uuid,
    deadline_provenance character varying(500),
    missing_inputs jsonb NOT NULL,
    triggering_evidence jsonb,
    declaration_id uuid,
    resolution_status character varying(40) NOT NULL,
    filing_resolved_at timestamp with time zone,
    payment_resolved_at timestamp with time zone,
    recurrence_key character varying(200) NOT NULL,
    deadline_revision integer NOT NULL,
    capabilities jsonb,
    unsupported_reason character varying(500),
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    workspace_id uuid NOT NULL
);

CREATE TABLE public.outbox_events (
    workspace_id uuid,
    event_type character varying(64) NOT NULL,
    payload jsonb NOT NULL,
    dedupe_key character varying(200),
    status character varying(16) NOT NULL,
    attempts integer NOT NULL,
    available_at timestamp with time zone DEFAULT now() NOT NULL,
    locked_by character varying(64),
    locked_until timestamp with time zone,
    processed_at timestamp with time zone,
    last_error character varying(1000),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL
);

CREATE TABLE public.password_reset_tokens (
    user_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    token_hash character varying(64) NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    used_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.payment_allocations (
    payment_id uuid NOT NULL,
    document_id uuid NOT NULL,
    amount numeric(20,6) NOT NULL,
    currency character varying(3) NOT NULL,
    allocation_kind character varying(16) NOT NULL,
    fx_difference_amount numeric(20,6) NOT NULL,
    note character varying(300),
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    workspace_id uuid NOT NULL
);

CREATE TABLE public.payments (
    account_id uuid,
    direction character varying(40) NOT NULL,
    kind character varying(40) NOT NULL,
    method character varying(40) NOT NULL,
    paid_on date NOT NULL,
    currency character varying(3) NOT NULL,
    amount numeric(20,6) NOT NULL,
    amount_ron numeric(20,6),
    exchange_rate_id uuid,
    bank_fee_amount numeric(20,6) NOT NULL,
    withholding_amount numeric(20,6) NOT NULL,
    reference character varying(200),
    note character varying(500),
    gross_amount numeric(20,6),
    is_owner_movement boolean NOT NULL,
    created_by_user_id uuid NOT NULL,
    external_id character varying(200),
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    workspace_id uuid NOT NULL
);

CREATE TABLE public.period_locks (
    period_start date NOT NULL,
    period_end date NOT NULL,
    period_kind character varying(24) NOT NULL,
    locked_by_user_id uuid NOT NULL,
    locked_at timestamp with time zone DEFAULT now() NOT NULL,
    reopened_at timestamp with time zone,
    reopened_by_user_id uuid,
    reopen_reason character varying(500),
    revision integer NOT NULL,
    close_checklist jsonb,
    is_open boolean NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    workspace_id uuid NOT NULL
);

CREATE TABLE public.pfa_profiles (
    legal_name character varying(200) NOT NULL,
    cui character varying(20) NOT NULL,
    trade_registry_number character varying(64),
    registered_address character varying(400),
    fiscal_domicile character varying(400),
    county character varying(64),
    locality character varying(128),
    phone character varying(32),
    iban character varying(34),
    cnp_ciphertext character varying(512),
    created_by_user_id uuid NOT NULL,
    active boolean NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    workspace_id uuid NOT NULL
);

CREATE TABLE public.receipts (
    declaration_id uuid NOT NULL,
    declaration_version integer NOT NULL,
    submission_attempt_id uuid,
    kind character varying(16) NOT NULL,
    remote_reference character varying(200),
    artifact_sha256 character varying(64),
    taxpayer_matches boolean,
    form_matches boolean,
    period_matches boolean,
    artifact_matches boolean,
    verified boolean NOT NULL,
    verified_at timestamp with time zone,
    verified_by_user_id uuid,
    manually_reported boolean NOT NULL,
    object_key character varying(400),
    sha256 character varying(64),
    rejection_errors jsonb,
    raw jsonb,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    workspace_id uuid NOT NULL
);

CREATE TABLE public.refresh_tokens (
    token_hash character varying(64) NOT NULL,
    jti uuid NOT NULL,
    user_id uuid NOT NULL,
    session_id uuid NOT NULL,
    parent_id uuid,
    issued_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    used_at timestamp with time zone,
    revoked_at timestamp with time zone,
    id uuid DEFAULT gen_random_uuid() NOT NULL
);

CREATE TABLE public.register_exports (
    register_type character varying(48) NOT NULL,
    period_start date NOT NULL,
    period_end date NOT NULL,
    format character varying(16) NOT NULL,
    status character varying(16) NOT NULL,
    job_id uuid,
    object_key character varying(400),
    sha256 character varying(64),
    row_count integer,
    document_kind character varying(40),
    deductibility character varying(40),
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    workspace_id uuid NOT NULL
);

CREATE TABLE public.remote_messages (
    connection_id uuid,
    direction character varying(16) NOT NULL,
    kind character varying(32) NOT NULL,
    remote_reference character varying(200),
    received_at timestamp with time zone DEFAULT now() NOT NULL,
    payload jsonb,
    processed_at timestamp with time zone,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    workspace_id uuid NOT NULL
);

CREATE TABLE public.rule_packs (
    code character varying(64) NOT NULL,
    jurisdiction character varying(8) NOT NULL,
    income_year integer NOT NULL,
    domain character varying(24) NOT NULL,
    title character varying(300) NOT NULL,
    version integer NOT NULL,
    valid_from date NOT NULL,
    valid_to date,
    publication_date date,
    source_url character varying(500),
    source_snapshot_sha256 character varying(64),
    legal_article character varying(200),
    formula_version character varying(32) NOT NULL,
    parameters jsonb NOT NULL,
    supported_scenarios jsonb,
    regression_fixtures jsonb,
    status character varying(24) NOT NULL,
    reviewer character varying(200),
    reviewed_at date,
    professionally_reviewed boolean NOT NULL,
    notes character varying(1000),
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.sessions (
    user_id uuid NOT NULL,
    client_id uuid NOT NULL,
    scope character varying(500) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    absolute_expires_at timestamp with time zone NOT NULL,
    revoked_at timestamp with time zone,
    revoke_reason character varying(64),
    id uuid DEFAULT gen_random_uuid() NOT NULL
);

CREATE TABLE public.submission_attempts (
    declaration_id uuid NOT NULL,
    declaration_version integer NOT NULL,
    connection_id uuid,
    channel character varying(40) NOT NULL,
    status character varying(16) NOT NULL,
    attempt_number integer NOT NULL,
    idempotency_key character varying(120) NOT NULL,
    request_sha256 character varying(64) NOT NULL,
    request_object_key character varying(400),
    remote_reference character varying(200),
    error_code character varying(64),
    error_message character varying(1000),
    error_class character varying(24),
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    next_retry_at timestamp with time zone,
    raw_response jsonb,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    workspace_id uuid NOT NULL
);

CREATE TABLE public.tax_adjustments (
    income_year integer NOT NULL,
    adjustment_kind character varying(48) NOT NULL,
    direction character varying(16) NOT NULL,
    amount numeric(20,6) NOT NULL,
    accounting_event_id uuid,
    document_id uuid,
    asset_id uuid,
    rule_pack_id uuid,
    legal_reference character varying(200),
    explanation character varying(500),
    computed_by character varying(16) NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    workspace_id uuid NOT NULL
);

CREATE TABLE public.tax_regimes (
    kind character varying(16) NOT NULL,
    income_year integer NOT NULL,
    effective_from timestamp with time zone NOT NULL,
    effective_to timestamp with time zone,
    norm_county character varying(64),
    norm_locality character varying(128),
    norm_activity character varying(200),
    norm_amount numeric(20,6),
    norm_source_url character varying(500),
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    workspace_id uuid NOT NULL
);

CREATE TABLE public.taxpayer_access_grants (
    taxpayer_id uuid NOT NULL,
    granted_by_user_id uuid NOT NULL,
    granted_at timestamp with time zone DEFAULT now() NOT NULL,
    revoked_at timestamp with time zone,
    sharing_notice_version character varying(32),
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    workspace_id uuid NOT NULL
);

CREATE TABLE public.taxpayer_year_facts (
    income_year integer NOT NULL,
    fact_key character varying(64) NOT NULL,
    value_kind character varying(16) NOT NULL,
    value_text character varying(500),
    value_decimal numeric(20,6),
    value_boolean boolean,
    answered_by_user_id uuid,
    answered_at timestamp with time zone,
    evidence_reference character varying(300),
    blocks_declarations jsonb,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    workspace_id uuid NOT NULL
);

CREATE TABLE public.taxpayers (
    workspace_id uuid,
    created_by_user_id uuid NOT NULL,
    display_name character varying(200) NOT NULL,
    cnp_ciphertext character varying(512),
    cif character varying(20),
    is_personal_source boolean NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.users (
    email_normalized character varying(320) NOT NULL,
    email_display character varying(320) NOT NULL,
    password_hash character varying(255) NOT NULL,
    status character varying(40) NOT NULL,
    verified_at timestamp with time zone,
    last_login_at timestamp with time zone,
    password_changed_at timestamp with time zone,
    mfa_required boolean NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.vat_registrations (
    kind character varying(32) NOT NULL,
    vat_code character varying(20),
    effective_from timestamp with time zone NOT NULL,
    effective_to timestamp with time zone,
    reporting_frequency character varying(16),
    cash_accounting boolean NOT NULL,
    evidence_reference character varying(300),
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    workspace_id uuid NOT NULL
);

CREATE TABLE public.workspaces (
    name character varying(200) NOT NULL,
    cui character varying(20),
    accounting_currency character varying(3) NOT NULL,
    timezone character varying(64) NOT NULL,
    created_by_user_id uuid NOT NULL,
    archived_at timestamp with time zone,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY public.accounting_events
    ADD CONSTRAINT accounting_events_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.activity_periods
    ADD CONSTRAINT activity_periods_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.anaf_connections
    ADD CONSTRAINT anaf_connections_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.assets
    ADD CONSTRAINT assets_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.attachments
    ADD CONSTRAINT attachments_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.audit_events
    ADD CONSTRAINT audit_events_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.authorization_codes
    ADD CONSTRAINT authorization_codes_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.authorization_transactions
    ADD CONSTRAINT authorization_transactions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bank_imports
    ADD CONSTRAINT bank_imports_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.calculation_runs
    ADD CONSTRAINT calculation_runs_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.connector_capabilities
    ADD CONSTRAINT connector_capabilities_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.counterparties
    ADD CONSTRAINT counterparties_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.deadline_rules
    ADD CONSTRAINT deadline_rules_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.declaration_source_grants
    ADD CONSTRAINT declaration_source_grants_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.declaration_versions
    ADD CONSTRAINT declaration_versions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.declarations
    ADD CONSTRAINT declarations_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.deduction_rules
    ADD CONSTRAINT deduction_rules_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.depreciation_entries
    ADD CONSTRAINT depreciation_entries_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.document_imports
    ADD CONSTRAINT document_imports_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.document_lines
    ADD CONSTRAINT document_lines_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.document_revisions
    ADD CONSTRAINT document_revisions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.email_verification_tokens
    ADD CONSTRAINT email_verification_tokens_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.exchange_rates
    ADD CONSTRAINT exchange_rates_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.financial_accounts
    ADD CONSTRAINT financial_accounts_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.form_versions
    ADD CONSTRAINT form_versions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.idempotency_keys
    ADD CONSTRAINT idempotency_keys_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.income_norms
    ADD CONSTRAINT income_norms_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.inventory_entries
    ADD CONSTRAINT inventory_entries_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.invitations
    ADD CONSTRAINT invitations_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.jobs
    ADD CONSTRAINT jobs_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.legal_calendar_days
    ADD CONSTRAINT legal_calendar_days_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.loss_carryforwards
    ADD CONSTRAINT loss_carryforwards_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.memberships
    ADD CONSTRAINT memberships_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.mfa_factors
    ADD CONSTRAINT mfa_factors_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.notification_deliveries
    ADD CONSTRAINT notification_deliveries_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.notification_user_states
    ADD CONSTRAINT notification_user_states_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.number_series
    ADD CONSTRAINT number_series_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.oauth_clients
    ADD CONSTRAINT oauth_clients_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.oauth_origins
    ADD CONSTRAINT oauth_origins_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.oauth_redirect_uris
    ADD CONSTRAINT oauth_redirect_uris_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.obligations
    ADD CONSTRAINT obligations_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.outbox_events
    ADD CONSTRAINT outbox_events_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.password_reset_tokens
    ADD CONSTRAINT password_reset_tokens_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.payment_allocations
    ADD CONSTRAINT payment_allocations_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.payments
    ADD CONSTRAINT payments_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.period_locks
    ADD CONSTRAINT period_locks_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.pfa_profiles
    ADD CONSTRAINT pfa_profiles_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.receipts
    ADD CONSTRAINT receipts_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.refresh_tokens
    ADD CONSTRAINT refresh_tokens_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.register_exports
    ADD CONSTRAINT register_exports_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.remote_messages
    ADD CONSTRAINT remote_messages_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.rule_packs
    ADD CONSTRAINT rule_packs_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.submission_attempts
    ADD CONSTRAINT submission_attempts_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.tax_adjustments
    ADD CONSTRAINT tax_adjustments_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.tax_regimes
    ADD CONSTRAINT tax_regimes_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.taxpayer_access_grants
    ADD CONSTRAINT taxpayer_access_grants_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.taxpayer_year_facts
    ADD CONSTRAINT taxpayer_year_facts_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.taxpayers
    ADD CONSTRAINT taxpayers_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.accounting_events
    ADD CONSTRAINT uq_accounting_event_source UNIQUE (workspace_id, document_id, payment_id, event_kind);

ALTER TABLE ONLY public.assets
    ADD CONSTRAINT uq_assets_id_workspace UNIQUE (id, workspace_id);

ALTER TABLE ONLY public.authorization_codes
    ADD CONSTRAINT uq_authz_code_hash UNIQUE (code_hash);

ALTER TABLE ONLY public.authorization_transactions
    ADD CONSTRAINT uq_authz_tx_proof_hash UNIQUE (proof_hash);

ALTER TABLE ONLY public.connector_capabilities
    ADD CONSTRAINT uq_connector_capability_key UNIQUE (form_code, environment, channel);

ALTER TABLE ONLY public.counterparties
    ADD CONSTRAINT uq_counterparties_id_workspace UNIQUE (id, workspace_id);

ALTER TABLE ONLY public.counterparties
    ADD CONSTRAINT uq_counterparty_workspace_name_country UNIQUE (workspace_id, name, country_code);

ALTER TABLE ONLY public.deadline_rules
    ADD CONSTRAINT uq_deadline_rule_version UNIQUE (form_code, version, effective_from);

ALTER TABLE ONLY public.declaration_source_grants
    ADD CONSTRAINT uq_declaration_source_grant UNIQUE (declaration_id, source_workspace_id, taxpayer_id);

ALTER TABLE ONLY public.declarations
    ADD CONSTRAINT uq_declaration_taxpayer_form_year UNIQUE (workspace_id, taxpayer_key, form_code, income_year);

ALTER TABLE ONLY public.declaration_versions
    ADD CONSTRAINT uq_declaration_version UNIQUE (declaration_id, version);

ALTER TABLE ONLY public.deduction_rules
    ADD CONSTRAINT uq_deduction_rule_code_year UNIQUE (code, income_year);

ALTER TABLE ONLY public.depreciation_entries
    ADD CONSTRAINT uq_depreciation_asset_period UNIQUE (asset_id, income_year, period_month);

ALTER TABLE ONLY public.document_lines
    ADD CONSTRAINT uq_document_line_number UNIQUE (document_id, line_number);

ALTER TABLE ONLY public.document_revisions
    ADD CONSTRAINT uq_document_revision UNIQUE (document_id, revision);

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT uq_document_workspace_external_id UNIQUE (workspace_id, external_id);

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT uq_document_workspace_series_number UNIQUE (workspace_id, series, number);

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT uq_documents_id_workspace UNIQUE (id, workspace_id);

ALTER TABLE ONLY public.email_verification_tokens
    ADD CONSTRAINT uq_email_verification_token_hash UNIQUE (token_hash);

ALTER TABLE ONLY public.exchange_rates
    ADD CONSTRAINT uq_exchange_rate_key UNIQUE (workspace_id, currency, quote_currency, rate_date, purpose, source);

ALTER TABLE ONLY public.financial_accounts
    ADD CONSTRAINT uq_financial_account_name UNIQUE (workspace_id, name);

ALTER TABLE ONLY public.financial_accounts
    ADD CONSTRAINT uq_financial_accounts_id_workspace UNIQUE (id, workspace_id);

ALTER TABLE ONLY public.form_versions
    ADD CONSTRAINT uq_form_version_key UNIQUE (form_code, income_year, schema_version);

ALTER TABLE ONLY public.idempotency_keys
    ADD CONSTRAINT uq_idempotency_scope UNIQUE (workspace_id, user_id, action, key);

ALTER TABLE ONLY public.income_norms
    ADD CONSTRAINT uq_income_norm_key UNIQUE (income_year, county, locality, caen_code);

ALTER TABLE ONLY public.invitations
    ADD CONSTRAINT uq_invitation_token_hash UNIQUE (token_hash);

ALTER TABLE ONLY public.jobs
    ADD CONSTRAINT uq_job_logical_key UNIQUE (workspace_id, logical_key);

ALTER TABLE ONLY public.legal_calendar_days
    ADD CONSTRAINT uq_legal_calendar_day UNIQUE (day, calendar_version);

ALTER TABLE ONLY public.loss_carryforwards
    ADD CONSTRAINT uq_loss_workspace_year UNIQUE (workspace_id, income_year);

ALTER TABLE ONLY public.memberships
    ADD CONSTRAINT uq_membership_workspace_user UNIQUE (workspace_id, user_id);

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT uq_notification_dedupe UNIQUE (dedupe_key);

ALTER TABLE ONLY public.notification_deliveries
    ADD CONSTRAINT uq_notification_delivery_dedupe UNIQUE (dedupe_key);

ALTER TABLE ONLY public.notification_user_states
    ADD CONSTRAINT uq_notification_user_state UNIQUE (notification_id, user_id);

ALTER TABLE ONLY public.number_series
    ADD CONSTRAINT uq_number_series_key UNIQUE (workspace_id, document_kind, prefix, year);

ALTER TABLE ONLY public.oauth_clients
    ADD CONSTRAINT uq_oauth_client_env UNIQUE (client_id, environment);

ALTER TABLE ONLY public.oauth_origins
    ADD CONSTRAINT uq_oauth_origin UNIQUE (client_id, origin);

ALTER TABLE ONLY public.oauth_redirect_uris
    ADD CONSTRAINT uq_oauth_redirect_client_uri UNIQUE (client_id, exact_uri);

ALTER TABLE ONLY public.obligations
    ADD CONSTRAINT uq_obligation_recurrence UNIQUE (workspace_id, recurrence_key);

ALTER TABLE ONLY public.outbox_events
    ADD CONSTRAINT uq_outbox_dedupe_key UNIQUE (dedupe_key);

ALTER TABLE ONLY public.password_reset_tokens
    ADD CONSTRAINT uq_password_reset_token_hash UNIQUE (token_hash);

ALTER TABLE ONLY public.payments
    ADD CONSTRAINT uq_payment_workspace_external_id UNIQUE (workspace_id, external_id);

ALTER TABLE ONLY public.payments
    ADD CONSTRAINT uq_payments_id_workspace UNIQUE (id, workspace_id);

ALTER TABLE ONLY public.period_locks
    ADD CONSTRAINT uq_period_lock_revision UNIQUE (workspace_id, period_start, period_end, revision);

ALTER TABLE ONLY public.receipts
    ADD CONSTRAINT uq_receipt_ref_kind UNIQUE (workspace_id, remote_reference, kind);

ALTER TABLE ONLY public.refresh_tokens
    ADD CONSTRAINT uq_refresh_token_hash UNIQUE (token_hash);

ALTER TABLE ONLY public.refresh_tokens
    ADD CONSTRAINT uq_refresh_token_jti UNIQUE (jti);

ALTER TABLE ONLY public.remote_messages
    ADD CONSTRAINT uq_remote_message_key UNIQUE (workspace_id, remote_reference, kind);

ALTER TABLE ONLY public.rule_packs
    ADD CONSTRAINT uq_rule_pack_code_version UNIQUE (code, version);

ALTER TABLE ONLY public.submission_attempts
    ADD CONSTRAINT uq_submission_attempt_number UNIQUE (workspace_id, declaration_id, declaration_version, attempt_number);

ALTER TABLE ONLY public.tax_adjustments
    ADD CONSTRAINT uq_tax_adjustment_source UNIQUE (workspace_id, income_year, adjustment_kind, accounting_event_id, document_id, asset_id);

ALTER TABLE ONLY public.taxpayer_access_grants
    ADD CONSTRAINT uq_taxpayer_grant UNIQUE (taxpayer_id, workspace_id);

ALTER TABLE ONLY public.taxpayer_year_facts
    ADD CONSTRAINT uq_year_fact_workspace_year_key UNIQUE (workspace_id, income_year, fact_key);

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.vat_registrations
    ADD CONSTRAINT vat_registrations_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.workspaces
    ADD CONSTRAINT workspaces_pkey PRIMARY KEY (id);

CREATE INDEX ix_accounting_events_workspace_date ON public.accounting_events USING btree (workspace_id, event_date);

CREATE INDEX ix_accounting_events_workspace_id ON public.accounting_events USING btree (workspace_id);

CREATE INDEX ix_accounting_events_workspace_kind ON public.accounting_events USING btree (workspace_id, event_kind);

CREATE INDEX ix_activity_periods_workspace_dates ON public.activity_periods USING btree (workspace_id, starts_on);

CREATE INDEX ix_activity_periods_workspace_id ON public.activity_periods USING btree (workspace_id);

CREATE INDEX ix_allocations_document ON public.payment_allocations USING btree (workspace_id, document_id);

CREATE INDEX ix_allocations_payment ON public.payment_allocations USING btree (workspace_id, payment_id);

CREATE INDEX ix_anaf_connections_workspace_id ON public.anaf_connections USING btree (workspace_id);

CREATE INDEX ix_anaf_connections_workspace_status ON public.anaf_connections USING btree (workspace_id, status);

CREATE INDEX ix_assets_workspace_id ON public.assets USING btree (workspace_id);

CREATE INDEX ix_assets_workspace_status ON public.assets USING btree (workspace_id, status);

CREATE INDEX ix_attachments_workspace_document ON public.attachments USING btree (workspace_id, document_id);

CREATE INDEX ix_attachments_workspace_id ON public.attachments USING btree (workspace_id);

CREATE INDEX ix_audit_events_entity ON public.audit_events USING btree (entity_type, entity_id);

CREATE INDEX ix_audit_events_workspace_created ON public.audit_events USING btree (workspace_id, created_at);

CREATE INDEX ix_audit_events_workspace_id ON public.audit_events USING btree (workspace_id);

CREATE INDEX ix_authz_tx_expires ON public.authorization_transactions USING btree (expires_at);

CREATE INDEX ix_bank_imports_workspace_id ON public.bank_imports USING btree (workspace_id);

CREATE INDEX ix_calculation_runs_declaration ON public.calculation_runs USING btree (declaration_id);

CREATE INDEX ix_calculation_runs_workspace_year ON public.calculation_runs USING btree (workspace_id, income_year);

CREATE INDEX ix_counterparties_workspace_id ON public.counterparties USING btree (workspace_id);

CREATE INDEX ix_counterparties_workspace_vat ON public.counterparties USING btree (workspace_id, vat_identifier);

CREATE INDEX ix_declaration_source_grants_workspace_id ON public.declaration_source_grants USING btree (workspace_id);

CREATE INDEX ix_declaration_versions_declaration ON public.declaration_versions USING btree (declaration_id);

CREATE INDEX ix_declarations_workspace_id ON public.declarations USING btree (workspace_id);

CREATE INDEX ix_declarations_workspace_period ON public.declarations USING btree (workspace_id, period_end);

CREATE INDEX ix_declarations_workspace_status ON public.declarations USING btree (workspace_id, status);

CREATE INDEX ix_depreciation_entries_workspace_id ON public.depreciation_entries USING btree (workspace_id);

CREATE INDEX ix_depreciation_workspace_year ON public.depreciation_entries USING btree (workspace_id, income_year);

CREATE INDEX ix_document_imports_workspace_id ON public.document_imports USING btree (workspace_id);

CREATE INDEX ix_document_lines_workspace_id ON public.document_lines USING btree (workspace_id);

CREATE INDEX ix_documents_content_hash ON public.documents USING btree (workspace_id, content_hash);

CREATE INDEX ix_documents_workspace_date ON public.documents USING btree (workspace_id, issue_date);

CREATE INDEX ix_documents_workspace_id ON public.documents USING btree (workspace_id);

CREATE INDEX ix_documents_workspace_status ON public.documents USING btree (workspace_id, status);

CREATE INDEX ix_exchange_rates_lookup ON public.exchange_rates USING btree (currency, quote_currency, rate_date, purpose);

CREATE INDEX ix_exchange_rates_workspace_id ON public.exchange_rates USING btree (workspace_id);

CREATE INDEX ix_financial_accounts_workspace_id ON public.financial_accounts USING btree (workspace_id);

CREATE INDEX ix_form_versions_lookup ON public.form_versions USING btree (form_code, income_year);

CREATE INDEX ix_idempotency_keys_workspace_id ON public.idempotency_keys USING btree (workspace_id);

CREATE INDEX ix_income_norms_lookup ON public.income_norms USING btree (income_year, county, caen_code);

CREATE INDEX ix_inventory_entries_workspace_id ON public.inventory_entries USING btree (workspace_id);

CREATE INDEX ix_inventory_workspace_year ON public.inventory_entries USING btree (workspace_id, income_year);

CREATE INDEX ix_invitations_pending ON public.invitations USING btree (workspace_id, email_normalized, status);

CREATE INDEX ix_jobs_lease ON public.jobs USING btree (status, available_at, priority);

CREATE INDEX ix_jobs_workspace_id ON public.jobs USING btree (workspace_id);

CREATE INDEX ix_jobs_workspace_status ON public.jobs USING btree (workspace_id, status);

CREATE INDEX ix_loss_carryforwards_workspace_id ON public.loss_carryforwards USING btree (workspace_id);

CREATE INDEX ix_memberships_user_active ON public.memberships USING btree (user_id, status);

CREATE INDEX ix_notifications_workspace_created ON public.notifications USING btree (workspace_id, created_at);

CREATE INDEX ix_number_series_workspace_id ON public.number_series USING btree (workspace_id);

CREATE INDEX ix_obligations_workspace_due ON public.obligations USING btree (workspace_id, filing_due_date);

CREATE INDEX ix_obligations_workspace_id ON public.obligations USING btree (workspace_id);

CREATE INDEX ix_obligations_workspace_period ON public.obligations USING btree (workspace_id, period_end);

CREATE INDEX ix_outbox_events_workspace_id ON public.outbox_events USING btree (workspace_id);

CREATE INDEX ix_outbox_pending ON public.outbox_events USING btree (status, available_at);

CREATE INDEX ix_payment_allocations_workspace_id ON public.payment_allocations USING btree (workspace_id);

CREATE INDEX ix_payments_workspace_date ON public.payments USING btree (workspace_id, paid_on);

CREATE INDEX ix_payments_workspace_id ON public.payments USING btree (workspace_id);

CREATE INDEX ix_period_locks_workspace_id ON public.period_locks USING btree (workspace_id);

CREATE INDEX ix_period_locks_workspace_range ON public.period_locks USING btree (workspace_id, period_start, period_end);

CREATE INDEX ix_pfa_profiles_workspace_active ON public.pfa_profiles USING btree (workspace_id, active);

CREATE INDEX ix_pfa_profiles_workspace_id ON public.pfa_profiles USING btree (workspace_id);

CREATE INDEX ix_receipts_declaration ON public.receipts USING btree (declaration_id);

CREATE INDEX ix_receipts_workspace_id ON public.receipts USING btree (workspace_id);

CREATE INDEX ix_refresh_tokens_session ON public.refresh_tokens USING btree (session_id);

CREATE INDEX ix_register_exports_workspace_id ON public.register_exports USING btree (workspace_id);

CREATE INDEX ix_remote_messages_workspace_id ON public.remote_messages USING btree (workspace_id);

CREATE INDEX ix_rule_packs_domain_year ON public.rule_packs USING btree (domain, income_year, status);

CREATE INDEX ix_sessions_user_active ON public.sessions USING btree (user_id, revoked_at);

CREATE INDEX ix_submission_attempts_pending ON public.submission_attempts USING btree (status, next_retry_at);

CREATE INDEX ix_submission_attempts_remote_ref ON public.submission_attempts USING btree (remote_reference);

CREATE INDEX ix_submission_attempts_workspace_id ON public.submission_attempts USING btree (workspace_id);

CREATE INDEX ix_tax_adjustments_workspace_id ON public.tax_adjustments USING btree (workspace_id);

CREATE INDEX ix_tax_adjustments_workspace_year ON public.tax_adjustments USING btree (workspace_id, income_year);

CREATE INDEX ix_tax_regimes_workspace_id ON public.tax_regimes USING btree (workspace_id);

CREATE INDEX ix_tax_regimes_workspace_year ON public.tax_regimes USING btree (workspace_id, income_year);

CREATE INDEX ix_taxpayer_access_grants_workspace_id ON public.taxpayer_access_grants USING btree (workspace_id);

CREATE INDEX ix_taxpayer_year_facts_workspace_id ON public.taxpayer_year_facts USING btree (workspace_id);

CREATE INDEX ix_taxpayers_workspace_id ON public.taxpayers USING btree (workspace_id);

CREATE INDEX ix_vat_registrations_workspace_effective ON public.vat_registrations USING btree (workspace_id, effective_from);

CREATE INDEX ix_vat_registrations_workspace_id ON public.vat_registrations USING btree (workspace_id);

CREATE INDEX ix_workspaces_cui ON public.workspaces USING btree (cui);

CREATE INDEX ix_year_facts_workspace_year ON public.taxpayer_year_facts USING btree (workspace_id, income_year);

CREATE UNIQUE INDEX uq_users_email_normalized ON public.users USING btree (email_normalized);

ALTER TABLE ONLY public.accounting_events
    ADD CONSTRAINT accounting_events_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES public.users(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.accounting_events
    ADD CONSTRAINT accounting_events_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.activity_periods
    ADD CONSTRAINT activity_periods_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.anaf_connections
    ADD CONSTRAINT anaf_connections_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES public.users(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.anaf_connections
    ADD CONSTRAINT anaf_connections_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.assets
    ADD CONSTRAINT assets_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES public.users(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.assets
    ADD CONSTRAINT assets_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.attachments
    ADD CONSTRAINT attachments_uploaded_by_user_id_fkey FOREIGN KEY (uploaded_by_user_id) REFERENCES public.users(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.attachments
    ADD CONSTRAINT attachments_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.authorization_codes
    ADD CONSTRAINT authorization_codes_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.oauth_clients(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.authorization_codes
    ADD CONSTRAINT authorization_codes_transaction_id_fkey FOREIGN KEY (transaction_id) REFERENCES public.authorization_transactions(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.authorization_codes
    ADD CONSTRAINT authorization_codes_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.authorization_transactions
    ADD CONSTRAINT authorization_transactions_authenticated_user_id_fkey FOREIGN KEY (authenticated_user_id) REFERENCES public.users(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.authorization_transactions
    ADD CONSTRAINT authorization_transactions_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.oauth_clients(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.bank_imports
    ADD CONSTRAINT bank_imports_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES public.users(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.bank_imports
    ADD CONSTRAINT bank_imports_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.calculation_runs
    ADD CONSTRAINT calculation_runs_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES public.users(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.counterparties
    ADD CONSTRAINT counterparties_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.declaration_source_grants
    ADD CONSTRAINT declaration_source_grants_granted_by_user_id_fkey FOREIGN KEY (granted_by_user_id) REFERENCES public.users(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.declaration_source_grants
    ADD CONSTRAINT declaration_source_grants_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.declaration_versions
    ADD CONSTRAINT declaration_versions_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES public.users(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.declarations
    ADD CONSTRAINT declarations_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES public.users(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.declarations
    ADD CONSTRAINT declarations_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.depreciation_entries
    ADD CONSTRAINT depreciation_entries_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.document_imports
    ADD CONSTRAINT document_imports_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES public.users(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.document_imports
    ADD CONSTRAINT document_imports_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.document_lines
    ADD CONSTRAINT document_lines_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.document_revisions
    ADD CONSTRAINT document_revisions_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES public.users(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES public.users(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.email_verification_tokens
    ADD CONSTRAINT email_verification_tokens_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.exchange_rates
    ADD CONSTRAINT exchange_rates_supplied_by_user_id_fkey FOREIGN KEY (supplied_by_user_id) REFERENCES public.users(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.financial_accounts
    ADD CONSTRAINT financial_accounts_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.payment_allocations
    ADD CONSTRAINT fk_allocation_document_same_workspace FOREIGN KEY (document_id, workspace_id) REFERENCES public.documents(id, workspace_id);

ALTER TABLE ONLY public.payment_allocations
    ADD CONSTRAINT fk_allocation_payment_same_workspace FOREIGN KEY (payment_id, workspace_id) REFERENCES public.payments(id, workspace_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.attachments
    ADD CONSTRAINT fk_attachment_same_workspace FOREIGN KEY (document_id, workspace_id) REFERENCES public.documents(id, workspace_id);

ALTER TABLE ONLY public.bank_imports
    ADD CONSTRAINT fk_bank_import_account_same_workspace FOREIGN KEY (account_id, workspace_id) REFERENCES public.financial_accounts(id, workspace_id);

ALTER TABLE ONLY public.depreciation_entries
    ADD CONSTRAINT fk_depreciation_asset_same_workspace FOREIGN KEY (asset_id, workspace_id) REFERENCES public.assets(id, workspace_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT fk_document_corrects_same_workspace FOREIGN KEY (corrects_document_id, workspace_id) REFERENCES public.documents(id, workspace_id);

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT fk_document_counterparty_same_workspace FOREIGN KEY (counterparty_id, workspace_id) REFERENCES public.counterparties(id, workspace_id);

ALTER TABLE ONLY public.document_lines
    ADD CONSTRAINT fk_document_line_same_workspace FOREIGN KEY (document_id, workspace_id) REFERENCES public.documents(id, workspace_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.document_revisions
    ADD CONSTRAINT fk_document_revision_same_workspace FOREIGN KEY (document_id, workspace_id) REFERENCES public.documents(id, workspace_id);

ALTER TABLE ONLY public.payments
    ADD CONSTRAINT fk_payment_account_same_workspace FOREIGN KEY (account_id, workspace_id) REFERENCES public.financial_accounts(id, workspace_id);

ALTER TABLE ONLY public.idempotency_keys
    ADD CONSTRAINT idempotency_keys_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.inventory_entries
    ADD CONSTRAINT inventory_entries_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.invitations
    ADD CONSTRAINT invitations_accepted_by_user_id_fkey FOREIGN KEY (accepted_by_user_id) REFERENCES public.users(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.invitations
    ADD CONSTRAINT invitations_invited_by_user_id_fkey FOREIGN KEY (invited_by_user_id) REFERENCES public.users(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.invitations
    ADD CONSTRAINT invitations_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.jobs
    ADD CONSTRAINT jobs_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.loss_carryforwards
    ADD CONSTRAINT loss_carryforwards_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.memberships
    ADD CONSTRAINT memberships_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.memberships
    ADD CONSTRAINT memberships_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.mfa_factors
    ADD CONSTRAINT mfa_factors_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.notification_deliveries
    ADD CONSTRAINT notification_deliveries_notification_id_fkey FOREIGN KEY (notification_id) REFERENCES public.notifications(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.notification_deliveries
    ADD CONSTRAINT notification_deliveries_recipient_user_id_fkey FOREIGN KEY (recipient_user_id) REFERENCES public.users(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.notification_user_states
    ADD CONSTRAINT notification_user_states_notification_id_fkey FOREIGN KEY (notification_id) REFERENCES public.notifications(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.notification_user_states
    ADD CONSTRAINT notification_user_states_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.number_series
    ADD CONSTRAINT number_series_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.oauth_origins
    ADD CONSTRAINT oauth_origins_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.oauth_clients(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.oauth_redirect_uris
    ADD CONSTRAINT oauth_redirect_uris_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.oauth_clients(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.obligations
    ADD CONSTRAINT obligations_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.password_reset_tokens
    ADD CONSTRAINT password_reset_tokens_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.payment_allocations
    ADD CONSTRAINT payment_allocations_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.payments
    ADD CONSTRAINT payments_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES public.users(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.payments
    ADD CONSTRAINT payments_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.period_locks
    ADD CONSTRAINT period_locks_locked_by_user_id_fkey FOREIGN KEY (locked_by_user_id) REFERENCES public.users(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.period_locks
    ADD CONSTRAINT period_locks_reopened_by_user_id_fkey FOREIGN KEY (reopened_by_user_id) REFERENCES public.users(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.period_locks
    ADD CONSTRAINT period_locks_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.pfa_profiles
    ADD CONSTRAINT pfa_profiles_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES public.users(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.pfa_profiles
    ADD CONSTRAINT pfa_profiles_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.receipts
    ADD CONSTRAINT receipts_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.refresh_tokens
    ADD CONSTRAINT refresh_tokens_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES public.refresh_tokens(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.refresh_tokens
    ADD CONSTRAINT refresh_tokens_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.sessions(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.refresh_tokens
    ADD CONSTRAINT refresh_tokens_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.register_exports
    ADD CONSTRAINT register_exports_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.remote_messages
    ADD CONSTRAINT remote_messages_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.oauth_clients(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.submission_attempts
    ADD CONSTRAINT submission_attempts_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.tax_adjustments
    ADD CONSTRAINT tax_adjustments_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.tax_regimes
    ADD CONSTRAINT tax_regimes_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.taxpayer_access_grants
    ADD CONSTRAINT taxpayer_access_grants_granted_by_user_id_fkey FOREIGN KEY (granted_by_user_id) REFERENCES public.users(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.taxpayer_access_grants
    ADD CONSTRAINT taxpayer_access_grants_taxpayer_id_fkey FOREIGN KEY (taxpayer_id) REFERENCES public.taxpayers(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.taxpayer_access_grants
    ADD CONSTRAINT taxpayer_access_grants_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.taxpayer_year_facts
    ADD CONSTRAINT taxpayer_year_facts_answered_by_user_id_fkey FOREIGN KEY (answered_by_user_id) REFERENCES public.users(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.taxpayer_year_facts
    ADD CONSTRAINT taxpayer_year_facts_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.taxpayers
    ADD CONSTRAINT taxpayers_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES public.users(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.taxpayers
    ADD CONSTRAINT taxpayers_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.vat_registrations
    ADD CONSTRAINT vat_registrations_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.workspaces
    ADD CONSTRAINT workspaces_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES public.users(id) ON DELETE RESTRICT;
