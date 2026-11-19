"""Authorization server database: initial schema.

Revision ID: 0001_oauth_schema
Revises:
Create Date: 2026-09-24

Creates the seven ``oauth_*`` tables from the frozen baseline
(``migrations_oauth/baseline/0001_oauth_schema.sql``) and grants DML on them to
the runtime role, i.e. the user of ``PFA_OAUTH_DATABASE_URL``.  The runtime role
gets no DDL right and owns nothing.

Existing deployments copy their rows from the application database with
``python -m pfa.scripts.move_oauth_data`` (run by ``make migrate``) before the
application's revision ``0016_move_oauth_tables_out`` drops the old tables.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from alembic import op
from sqlalchemy.engine import make_url

from pfa.config import get_settings

revision = "0001_oauth_schema"
down_revision = None
branch_labels = None
depends_on = None

BASELINE = Path(__file__).resolve().parents[1] / "baseline" / "0001_oauth_schema.sql"

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


def runtime_role() -> str:
    url = os.environ.get("PFA_OAUTH_DATABASE_URL") or get_settings().oauth_database_url
    role = make_url(url).username
    if not role or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", role):
        raise RuntimeError(f"cannot derive the runtime role from PFA_OAUTH_DATABASE_URL: {role!r}")
    return role


def _statements() -> list[str]:
    sql = "\n".join(
        line
        for line in BASELINE.read_text(encoding="utf-8").splitlines()
        if not line.startswith("--")
    )
    return [part.strip() for part in re.split(r";\s*$", sql, flags=re.M) if part.strip()]


def upgrade() -> None:
    connection = op.get_bind()
    for statement in _statements():
        connection.exec_driver_sql(statement)
    role = runtime_role()
    connection.exec_driver_sql(f'GRANT USAGE ON SCHEMA public TO "{role}"')
    connection.exec_driver_sql(
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{role}"'
    )
    connection.exec_driver_sql(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "{role}"'
    )


def downgrade() -> None:
    connection = op.get_bind()
    for table in TABLES:
        connection.exec_driver_sql(f'DROP TABLE IF EXISTS public."{table}" CASCADE')
