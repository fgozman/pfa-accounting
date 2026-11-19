"""A cancelled document is retired, not a duplicate of the replacement.

Revision ID: 0006_cancelled_not_duplicate
Revises: 0005_partner_kind
Create Date: 2026-09-20

Cancelling a document is the correction path when no declaration uses it: the number
stays, the row stays for the audit trail, and the user records the document again. The
replacement was then rejected as a duplicate of the retired row — by the content hash,
the number/date pair and the import key. Duplicate detection now ignores cancelled
documents, and the import key is unique only among the documents that still count, so
the same source can be recorded again after a cancellation.
"""

from __future__ import annotations

from alembic import op

revision = "0006_cancelled_not_duplicate"
down_revision = "0005_partner_kind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE documents DROP CONSTRAINT IF EXISTS uq_document_workspace_external_id"
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_document_workspace_external_id
            ON documents (workspace_id, external_id)
         WHERE external_id IS NOT NULL AND status <> 'cancelled'
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_document_workspace_external_id")
    op.execute(
        "ALTER TABLE documents ADD CONSTRAINT uq_document_workspace_external_id "
        "UNIQUE (workspace_id, external_id)"
    )
