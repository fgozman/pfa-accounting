-- Frozen copy of the oauth_* tables as they stood at revision 0015_oauth2_detach_users.
-- Used only by the downgrade of 0016_move_oauth_tables_out, which recreates them
-- (empty) in the application database.  The authorization server keeps its own
-- identical baseline in migrations_oauth/; the two histories are independent.

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
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT oauth_clients_pkey PRIMARY KEY (id),
    CONSTRAINT uq_oauth_client_env UNIQUE (client_id, environment)
);

CREATE TABLE public.oauth_redirect_uris (
    client_id uuid NOT NULL,
    exact_uri character varying(500) NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    CONSTRAINT oauth_redirect_uris_pkey PRIMARY KEY (id),
    CONSTRAINT uq_oauth_redirect_client_uri UNIQUE (client_id, exact_uri),
    CONSTRAINT oauth_redirect_uris_client_id_fkey FOREIGN KEY (client_id)
        REFERENCES public.oauth_clients(id) ON DELETE CASCADE
);

CREATE TABLE public.oauth_origins (
    client_id uuid NOT NULL,
    origin character varying(500) NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    CONSTRAINT oauth_origins_pkey PRIMARY KEY (id),
    CONSTRAINT uq_oauth_origin UNIQUE (client_id, origin),
    CONSTRAINT oauth_origins_client_id_fkey FOREIGN KEY (client_id)
        REFERENCES public.oauth_clients(id) ON DELETE CASCADE
);

CREATE TABLE public.oauth_authorization_transactions (
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
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    CONSTRAINT oauth_authorization_transactions_pkey PRIMARY KEY (id),
    CONSTRAINT uq_oauth_authz_tx_proof_hash UNIQUE (proof_hash),
    CONSTRAINT oauth_authorization_transactions_client_id_fkey FOREIGN KEY (client_id)
        REFERENCES public.oauth_clients(id) ON DELETE CASCADE
);

CREATE INDEX ix_oauth_authz_tx_expires
    ON public.oauth_authorization_transactions USING btree (expires_at);

CREATE TABLE public.oauth_authorization_codes (
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
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    CONSTRAINT oauth_authorization_codes_pkey PRIMARY KEY (id),
    CONSTRAINT uq_oauth_authz_code_hash UNIQUE (code_hash),
    CONSTRAINT oauth_authorization_codes_client_id_fkey FOREIGN KEY (client_id)
        REFERENCES public.oauth_clients(id) ON DELETE CASCADE,
    CONSTRAINT oauth_authorization_codes_transaction_id_fkey FOREIGN KEY (transaction_id)
        REFERENCES public.oauth_authorization_transactions(id) ON DELETE RESTRICT
);

CREATE TABLE public.oauth_sessions (
    user_id uuid NOT NULL,
    client_id uuid NOT NULL,
    scope character varying(500) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    absolute_expires_at timestamp with time zone NOT NULL,
    revoked_at timestamp with time zone,
    revoke_reason character varying(64),
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    CONSTRAINT oauth_sessions_pkey PRIMARY KEY (id),
    CONSTRAINT oauth_sessions_client_id_fkey FOREIGN KEY (client_id)
        REFERENCES public.oauth_clients(id) ON DELETE CASCADE
);

CREATE INDEX ix_oauth_sessions_user_active
    ON public.oauth_sessions USING btree (user_id, revoked_at);

CREATE TABLE public.oauth_refresh_tokens (
    token_hash character varying(64) NOT NULL,
    jti uuid NOT NULL,
    user_id uuid NOT NULL,
    session_id uuid NOT NULL,
    parent_id uuid,
    issued_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    used_at timestamp with time zone,
    revoked_at timestamp with time zone,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    CONSTRAINT oauth_refresh_tokens_pkey PRIMARY KEY (id),
    CONSTRAINT uq_oauth_refresh_token_hash UNIQUE (token_hash),
    CONSTRAINT uq_oauth_refresh_token_jti UNIQUE (jti),
    CONSTRAINT oauth_refresh_tokens_parent_id_fkey FOREIGN KEY (parent_id)
        REFERENCES public.oauth_refresh_tokens(id) ON DELETE SET NULL,
    CONSTRAINT oauth_refresh_tokens_session_id_fkey FOREIGN KEY (session_id)
        REFERENCES public.oauth_sessions(id) ON DELETE CASCADE
);

CREATE INDEX ix_oauth_refresh_tokens_session
    ON public.oauth_refresh_tokens USING btree (session_id);
