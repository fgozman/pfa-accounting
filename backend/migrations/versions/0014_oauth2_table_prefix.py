"""Prefix every OAuth 2.0-only table with ``oauth_``.

Revision ID: 0014_oauth2_table_prefix
Revises: 0013_orphan_payment_events

The authorization server owns a closed set of tables.  The registration tables were
already prefixed (``oauth_clients``, ``oauth_redirect_uris``, ``oauth_origins``); the
runtime tables were not, which made the boundary invisible in the schema.  After
this revision every table used exclusively by OAuth 2.0 matches ``oauth_%``, so the
authorization server can be moved to its own service (and database) by taking
exactly those tables.

The only references that cross the boundary are the foreign keys to ``users``
(the authenticated subject); no other table references an ``oauth_`` table.

Renames are metadata-only in PostgreSQL: data, grants and dependent objects are
kept.  Constraint and index names are renamed too, so they keep describing the
table they belong to.
"""

from __future__ import annotations

from alembic import op

revision = "0014_oauth2_table_prefix"
down_revision = "0013_orphan_payment_events"
branch_labels = None
depends_on = None

#: old table name -> new table name
TABLES = {
    "authorization_transactions": "oauth_authorization_transactions",
    "authorization_codes": "oauth_authorization_codes",
    "sessions": "oauth_sessions",
    "refresh_tokens": "oauth_refresh_tokens",
}

#: (new table name, old constraint name, new constraint name)
CONSTRAINTS = [
    (
        "oauth_authorization_transactions",
        "authorization_transactions_pkey",
        "oauth_authorization_transactions_pkey",
    ),
    (
        "oauth_authorization_transactions",
        "authorization_transactions_client_id_fkey",
        "oauth_authorization_transactions_client_id_fkey",
    ),
    (
        "oauth_authorization_transactions",
        "authorization_transactions_authenticated_user_id_fkey",
        "oauth_authorization_transactions_authenticated_user_id_fkey",
    ),
    (
        "oauth_authorization_transactions",
        "uq_authz_tx_proof_hash",
        "uq_oauth_authz_tx_proof_hash",
    ),
    ("oauth_authorization_codes", "authorization_codes_pkey", "oauth_authorization_codes_pkey"),
    (
        "oauth_authorization_codes",
        "authorization_codes_client_id_fkey",
        "oauth_authorization_codes_client_id_fkey",
    ),
    (
        "oauth_authorization_codes",
        "authorization_codes_transaction_id_fkey",
        "oauth_authorization_codes_transaction_id_fkey",
    ),
    (
        "oauth_authorization_codes",
        "authorization_codes_user_id_fkey",
        "oauth_authorization_codes_user_id_fkey",
    ),
    ("oauth_authorization_codes", "uq_authz_code_hash", "uq_oauth_authz_code_hash"),
    ("oauth_sessions", "sessions_pkey", "oauth_sessions_pkey"),
    ("oauth_sessions", "sessions_client_id_fkey", "oauth_sessions_client_id_fkey"),
    ("oauth_sessions", "sessions_user_id_fkey", "oauth_sessions_user_id_fkey"),
    ("oauth_refresh_tokens", "refresh_tokens_pkey", "oauth_refresh_tokens_pkey"),
    (
        "oauth_refresh_tokens",
        "refresh_tokens_parent_id_fkey",
        "oauth_refresh_tokens_parent_id_fkey",
    ),
    (
        "oauth_refresh_tokens",
        "refresh_tokens_session_id_fkey",
        "oauth_refresh_tokens_session_id_fkey",
    ),
    ("oauth_refresh_tokens", "refresh_tokens_user_id_fkey", "oauth_refresh_tokens_user_id_fkey"),
    ("oauth_refresh_tokens", "uq_refresh_token_hash", "uq_oauth_refresh_token_hash"),
    ("oauth_refresh_tokens", "uq_refresh_token_jti", "uq_oauth_refresh_token_jti"),
]

#: old index name -> new index name (plain indexes, not constraint-backed)
INDEXES = {
    "ix_authz_tx_expires": "ix_oauth_authz_tx_expires",
    "ix_sessions_user_active": "ix_oauth_sessions_user_active",
    "ix_refresh_tokens_session": "ix_oauth_refresh_tokens_session",
}


def upgrade() -> None:
    for old, new in TABLES.items():
        op.execute(f'ALTER TABLE public."{old}" RENAME TO "{new}"')
    for table, old, new in CONSTRAINTS:
        op.execute(f'ALTER TABLE public."{table}" RENAME CONSTRAINT "{old}" TO "{new}"')
    for old, new in INDEXES.items():
        op.execute(f'ALTER INDEX public."{old}" RENAME TO "{new}"')


def downgrade() -> None:
    reverse_tables = {new: old for old, new in TABLES.items()}
    for old, new in INDEXES.items():
        op.execute(f'ALTER INDEX public."{new}" RENAME TO "{old}"')
    for table, old, new in CONSTRAINTS:
        op.execute(f'ALTER TABLE public."{table}" RENAME CONSTRAINT "{new}" TO "{old}"')
    for new, old in reverse_tables.items():
        op.execute(f'ALTER TABLE public."{new}" RENAME TO "{old}"')
