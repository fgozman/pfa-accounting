"""The series of a document is optional; the number is not.

Revision ID: 0012_series_optional
Revises: 0011_activity_start
Create Date: 2026-09-21

Many documents carry only a number, so requiring a series blocked their finalization. The
number still identifies the document and stays unique per workspace for the invoices the
PFA issues — which needs ``COALESCE(series, '')`` in the index, because Postgres treats
NULLs as distinct and two number-only invoices would otherwise both be accepted.

That index immediately exposes documents that were recorded before the check covered the
no-series case: they are reported, never silently renumbered, because a number is a fact of
the issued paper.
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0012_series_optional"
down_revision = "0011_activity_start"
branch_labels = None
depends_on = None

DUPLICATES = """
    SELECT workspace_id, COALESCE(series, '') AS series, number, count(*) AS copies,
           string_agg(id::text, ', ' ORDER BY created_at) AS ids
      FROM documents
     WHERE kind IN ('sales_invoice', 'credit_note')
       AND number IS NOT NULL AND number <> ''
     GROUP BY 1, 2, 3
    HAVING count(*) > 1
"""


def upgrade() -> None:
    # an absent series or number is stored as NULL, so empty values never collide
    op.execute("UPDATE documents SET series = NULL WHERE series = ''")
    op.execute("UPDATE documents SET number = NULL WHERE number = ''")

    conflicts = op.get_bind().execute(text(DUPLICATES)).all()
    if conflicts:
        details = "; ".join(
            f"spațiul {row.workspace_id}: seria „{row.series}”, numărul „{row.number}” "
            f"({row.copies} documente: {row.ids})"
            for row in conflicts
        )
        raise RuntimeError(
            "Există documente emise cu același serie/număr, care nu pot fi distinse: "
            + details
            + ". Corectați numerele (sau ștergeți duplicatele) înainte de migrare; "
            "migrarea nu le redenumește singură."
        )

    op.execute("DROP INDEX IF EXISTS uq_document_workspace_series_number")
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_document_workspace_series_number
            ON documents (workspace_id, COALESCE(series, ''), number)
         WHERE kind IN ('sales_invoice', 'credit_note') AND number IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_document_workspace_series_number")
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_document_workspace_series_number
            ON documents (workspace_id, series, number)
         WHERE kind IN ('sales_invoice', 'credit_note')
        """
    )
