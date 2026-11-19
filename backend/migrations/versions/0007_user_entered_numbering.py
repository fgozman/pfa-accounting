"""The series/number of a document is entered by the user, not allocated.

Revision ID: 0007_user_entered_numbering
Revises: 0006_cancelled_not_duplicate
Create Date: 2026-09-20

The application allocated series/number transactionally at finalization. The number on a
document is a fact of the issued paper, so it is now entered by the user (any date, any
series, including documents recorded later). Uniqueness of (workspace, series, number)
is still a database guarantee, but only among the documents that still count: a deleted
document releases its number, exactly as it releases its import key.
"""

from __future__ import annotations

from alembic import op

revision = "0007_user_entered_numbering"
down_revision = "0006_cancelled_not_duplicate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE documents DROP CONSTRAINT IF EXISTS uq_document_workspace_series_number"
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_document_workspace_series_number
            ON documents (workspace_id, series, number)
         WHERE status <> 'cancelled'
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_document_workspace_series_number")
    op.execute(
        "ALTER TABLE documents ADD CONSTRAINT uq_document_workspace_series_number "
        "UNIQUE (workspace_id, series, number)"
    )
