"""Intra-community operation type on documents (D390/D394 classification).

Revision ID: 0003_intracommunity_operation
Revises: 0002_tenancy_security
Create Date: 2026-09-17

D390/D394 must classify every reported operation as L/T/A/P/S/R (livrări,
triunghiular, achiziții, prestări, achiziții de servicii, regim special agricol).
The type is a property of the document — it cannot be derived reliably from the
amounts — so it is stored explicitly and left NULL when it is not an
intra-community operation.
"""

from __future__ import annotations

from alembic import op

revision = "0003_intracommunity_operation"
down_revision = "0002_tenancy_security"
branch_labels = None
depends_on = None

ALLOWED = "'L', 'T', 'A', 'P', 'S', 'R'"


def upgrade() -> None:
    op.execute("ALTER TABLE documents ADD COLUMN intracommunity_operation_type varchar(1)")
    op.execute(
        "ALTER TABLE documents ADD CONSTRAINT ck_documents_intracommunity_operation_type "
        f"CHECK (intracommunity_operation_type IS NULL OR intracommunity_operation_type IN ({ALLOWED}))"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE documents DROP CONSTRAINT IF EXISTS ck_documents_intracommunity_operation_type"
    )
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS intracommunity_operation_type")
