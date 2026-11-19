"""Discard derived accounting entries whose source settlement was already removed.

Revision ID: 0013_orphan_payment_events
Revises: 0012_series_optional

Earlier deletions of payments (including payments removed with an invoice) left the
cash-recognition accounting event behind. This double-counted receipts on the dashboard
and in subsequent calculations. Payment deletions themselves are recorded in the
append-only audit log; accounting_events are derived, not that log.
"""

from __future__ import annotations

from alembic import op

revision = "0013_orphan_payment_events"
down_revision = "0012_series_optional"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM accounting_events e
         WHERE e.payment_id IS NOT NULL
           AND NOT EXISTS (
               SELECT 1 FROM payments p
                WHERE p.id = e.payment_id AND p.workspace_id = e.workspace_id
           )
        """
    )


def downgrade() -> None:
    # Removed settlements and their derived events cannot be reconstructed from rows.
    pass
