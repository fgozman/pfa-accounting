"""Initial schema baseline.

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-16

The baseline is a **frozen** DDL snapshot (``migrations/baseline/0001_initial_schema.sql``).
It must not be generated from the live SQLAlchemy models: revisions 0003+ alter
the schema as it stood at 0001, so a model-generated baseline would already
contain their changes and ``upgrade head`` on an empty database would fail
(e.g. ``column "intracommunity_operation_type" already exists``).

Postgres-specific security (roles, RLS, append-only triggers, leasing functions)
is deliberately in its own revision so the tenancy model is reviewable on its own.
"""

from __future__ import annotations

import re
from pathlib import Path

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

BASELINE = Path(__file__).resolve().parents[1] / "baseline" / "0001_initial_schema.sql"


def _statements() -> list[str]:
    sql = "\n".join(
        line for line in BASELINE.read_text(encoding="utf-8").splitlines() if not line.startswith("--")
    )
    return [statement.strip() for statement in re.split(r";\s*$", sql, flags=re.M) if statement.strip()]


def _tables() -> list[str]:
    return re.findall(r"^CREATE TABLE public\.(\w+) \(", BASELINE.read_text(encoding="utf-8"), re.M)


def upgrade() -> None:
    connection = op.get_bind()
    for statement in _statements():
        connection.exec_driver_sql(statement)


def downgrade() -> None:
    connection = op.get_bind()
    for table in _tables():
        connection.exec_driver_sql(f'DROP TABLE IF EXISTS public."{table}" CASCADE')
