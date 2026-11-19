"""Remove the cancelled status and every document that carried it.

Revision ID: 0009_remove_cancelled_status
Revises: 0008_number_uniqueness_scope
Create Date: 2026-09-20

Deleting a document now deletes it, so ``cancelled`` is a status nothing produces any
more. It is removed as a concept: the rows that still carry it go (with their lines,
attachments, revision history, applied settlements and any credit note that corrected
them), and the two partial unique indexes no longer need a status predicate.
"""

from __future__ import annotations

from alembic import op

revision = "0009_remove_cancelled_status"
down_revision = "0008_number_uniqueness_scope"
branch_labels = None
depends_on = None

#: the retired documents and, transitively, the credit notes that corrected one of them
DOOMED = """
    CREATE TEMP TABLE doomed_documents ON COMMIT DROP AS
        SELECT id FROM documents WHERE status = 'cancelled'
        UNION
        SELECT note.id
          FROM documents note
          JOIN documents target ON note.corrects_document_id = target.id
         WHERE target.status = 'cancelled'
"""

DEPENDENTS = (
    "payment_allocations",
    "attachments",
    "document_revisions",
)


def upgrade() -> None:
    op.execute(DOOMED)
    for table in DEPENDENTS:
        op.execute(f"DELETE FROM {table} WHERE document_id IN (SELECT id FROM doomed_documents)")
    # the per-invoice e-Factura obligation of a retired document has no subject left
    op.execute(
        """
        DELETE FROM notifications
         WHERE obligation_id IN (
            SELECT id FROM obligations
             WHERE recurrence_key IN (
                SELECT 'EFACTURA:' || id::text FROM doomed_documents
             )
         )
        """
    )
    op.execute(
        """
        DELETE FROM obligations
         WHERE recurrence_key IN (SELECT 'EFACTURA:' || id::text FROM doomed_documents)
        """
    )
    op.execute("DELETE FROM documents WHERE id IN (SELECT id FROM doomed_documents)")
    op.execute("DROP TABLE doomed_documents")

    # the same holds for documents deleted before this migration: an e-Factura obligation
    # keyed to a document that no longer exists is dead data
    op.execute(
        """
        DELETE FROM notifications
         WHERE obligation_id IN (
            SELECT id FROM obligations
             WHERE form_code = 'EFACTURA'
               AND NOT EXISTS (
                    SELECT 1 FROM documents
                     WHERE 'EFACTURA:' || documents.id::text = obligations.recurrence_key
               )
         )
        """
    )
    op.execute(
        """
        DELETE FROM obligations
         WHERE form_code = 'EFACTURA'
           AND NOT EXISTS (
                SELECT 1 FROM documents
                 WHERE 'EFACTURA:' || documents.id::text = obligations.recurrence_key
           )
        """
    )

    # no status predicate is needed now: a row that exists is a live document
    op.execute("DROP INDEX IF EXISTS uq_document_workspace_external_id")
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_document_workspace_external_id
            ON documents (workspace_id, external_id)
         WHERE external_id IS NOT NULL
        """
    )
    op.execute("DROP INDEX IF EXISTS uq_document_workspace_series_number")
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_document_workspace_series_number
            ON documents (workspace_id, series, number)
         WHERE kind IN ('sales_invoice', 'credit_note')
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_document_workspace_series_number")
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_document_workspace_series_number
            ON documents (workspace_id, series, number)
         WHERE kind IN ('sales_invoice', 'credit_note') AND status <> 'cancelled'
        """
    )
    op.execute("DROP INDEX IF EXISTS uq_document_workspace_external_id")
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_document_workspace_external_id
            ON documents (workspace_id, external_id)
         WHERE external_id IS NOT NULL AND status <> 'cancelled'
        """
    )
    # the deleted rows are not restored: the status itself is what this migration removes
