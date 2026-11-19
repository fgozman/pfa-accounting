"""Series/number uniqueness applies to the documents the PFA itself issues.

Revision ID: 0008_number_uniqueness_scope
Revises: 0007_user_entered_numbering
Create Date: 2026-09-20

Migration 0007 made (workspace, series, number) unique for every document. That is right
for the invoices the PFA issues (and their credit notes), but wrong for purchases: a
supplier's number belongs to that supplier, so two suppliers may legitimately both send
an invoice numbered "1". Uniqueness is therefore scoped to issued documents; a repeated
purchase is caught by duplicate detection, which compares the partner as well.
"""

from __future__ import annotations

from alembic import op

revision = "0008_number_uniqueness_scope"
down_revision = "0007_user_entered_numbering"
branch_labels = None
depends_on = None

ISSUED = "kind IN ('sales_invoice', 'credit_note') AND status <> 'cancelled'"


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_document_workspace_series_number")
    op.execute(
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_document_workspace_series_number
            ON documents (workspace_id, series, number)
         WHERE {ISSUED}
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_document_workspace_series_number")
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_document_workspace_series_number
            ON documents (workspace_id, series, number)
         WHERE status <> 'cancelled'
        """
    )
