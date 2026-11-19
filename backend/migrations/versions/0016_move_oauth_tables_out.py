"""Remove the OAuth 2.0 tables from the application database.

Revision ID: 0016_move_oauth_tables_out
Revises: 0015_oauth2_detach_users

The ``oauth_*`` tables now live in the authorization server's own database
(``PFA_OAUTH_DATABASE_URL``, history in ``migrations_oauth/``).  This revision drops
them here.

It never discards data silently: if any of the tables still holds a row, the
upgrade stops and asks for the copy step.  ``make migrate`` runs the steps in the
right order (``pfa.scripts.migrate``):

1. create the OAuth database if needed and migrate it to head;
2. ``python -m pfa.scripts.move_oauth_data``: copy every row there, verify it,
   then empty the tables here (one locked transaction);
3. upgrade this database, which runs this revision.

On an empty or fresh database (the tables come from the 0001 baseline, empty)
step 2 is a no-op.  The downgrade recreates the tables *empty* in their 0015 shape
(``migrations/baseline/0015_oauth_tables.sql``); rows are not copied back.
"""

from __future__ import annotations

import re
from pathlib import Path

from alembic import op

revision = "0016_move_oauth_tables_out"
down_revision = "0015_oauth2_detach_users"
branch_labels = None
depends_on = None

SNAPSHOT = Path(__file__).resolve().parents[1] / "baseline" / "0015_oauth_tables.sql"

#: drop order (children first)
TABLES = (
    "oauth_refresh_tokens",
    "oauth_sessions",
    "oauth_authorization_codes",
    "oauth_authorization_transactions",
    "oauth_origins",
    "oauth_redirect_uris",
    "oauth_clients",
)


class OAuthDataNotMoved(RuntimeError):
    pass


def upgrade() -> None:
    connection = op.get_bind()
    remaining = {}
    for table in TABLES:
        # constant table names: no user input reaches this SQL
        exists = connection.exec_driver_sql(
            f"SELECT to_regclass('public.{table}') IS NOT NULL"
        ).scalar()
        if exists:
            count = connection.exec_driver_sql(f'SELECT count(*) FROM public."{table}"').scalar()
            if count:
                remaining[table] = count
    if remaining:
        raise OAuthDataNotMoved(
            "The OAuth tables still hold rows that are not in the OAuth database yet: "
            f"{remaining}.  Run `make migrate` (or `python -m pfa.scripts.move_oauth_data` "
            "after migrating the OAuth database) and retry."
        )
    for table in TABLES:
        connection.exec_driver_sql(f'DROP TABLE IF EXISTS public."{table}"')


def downgrade() -> None:
    connection = op.get_bind()
    sql = "\n".join(
        line
        for line in SNAPSHOT.read_text(encoding="utf-8").splitlines()
        if not line.startswith("--")
    )
    for statement in re.split(r";\s*$", sql, flags=re.M):
        if statement.strip():
            connection.exec_driver_sql(statement.strip())
