"""Detach the OAuth 2.0 tables from ``users``: drop the cross-boundary foreign keys.

Revision ID: 0015_oauth2_detach_users
Revises: 0014_oauth2_table_prefix

After 0014 the authorization server's tables are all ``oauth_*``; their only links
out of that set were foreign keys to ``users``.  A separate authorization service
cannot keep a foreign key into the application's database, so the columns become
plain subject identifiers (the JWT ``sub``) issued by the identity provider.

What the constraints used to guarantee, and what replaces it:

* *the subject exists* — the identity provider vouches for it at login; codes,
  sessions and refresh tokens are only ever created from an authenticated
  transaction;
* ``ON DELETE CASCADE`` on sessions/refresh tokens — accounts are never deleted
  (they are disabled); every credential change revokes the subject's sessions
  through ``AuthorizationServerClient.revoke_user_sessions``, and the API checks
  the account status on every request;
* ``ON DELETE RESTRICT`` on codes/transactions — moot for the same reason.

The downgrade restores the constraints exactly; it fails if rows reference a
subject that is not in ``users`` (which would be a data error to look at).
"""

from __future__ import annotations

from alembic import op

revision = "0015_oauth2_detach_users"
down_revision = "0014_oauth2_table_prefix"
branch_labels = None
depends_on = None

#: (table, column, constraint name, ON DELETE action) as created by 0001/0014
FOREIGN_KEYS = [
    (
        "oauth_authorization_transactions",
        "authenticated_user_id",
        "oauth_authorization_transactions_authenticated_user_id_fkey",
        "RESTRICT",
    ),
    (
        "oauth_authorization_codes",
        "user_id",
        "oauth_authorization_codes_user_id_fkey",
        "RESTRICT",
    ),
    ("oauth_sessions", "user_id", "oauth_sessions_user_id_fkey", "CASCADE"),
    ("oauth_refresh_tokens", "user_id", "oauth_refresh_tokens_user_id_fkey", "CASCADE"),
]


def upgrade() -> None:
    for table, _column, name, _on_delete in FOREIGN_KEYS:
        op.execute(f'ALTER TABLE public."{table}" DROP CONSTRAINT "{name}"')


def downgrade() -> None:
    for table, column, name, on_delete in FOREIGN_KEYS:
        op.execute(
            f'ALTER TABLE public."{table}" ADD CONSTRAINT "{name}" '
            f'FOREIGN KEY ("{column}") REFERENCES public.users(id) ON DELETE {on_delete}'
        )
