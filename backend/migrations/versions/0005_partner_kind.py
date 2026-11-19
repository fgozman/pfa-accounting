"""B2B/B2C nature of the other party of a document.

Revision ID: 0005_partner_kind
Revises: 0004_supplier_country
Create Date: 2026-09-20

D394 reports operations *per partner*: an operation with an unstated partner cannot be
classified, but a sale to a final consumer (B2C) legitimately has no partner data at
all. Without a recorded fact the application could not tell the two apart, so it
demanded a partner for every document — including consumer sales, where the user has
nothing to enter. ``partner_kind`` records the distinction; ``NULL`` keeps meaning
"never stated", which stays visible rather than being assumed.
"""

from __future__ import annotations

from alembic import op

revision = "0005_partner_kind"
down_revision = "0004_supplier_country"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE documents ADD COLUMN partner_kind varchar(16)")
    op.execute(
        "ALTER TABLE documents ADD CONSTRAINT ck_documents_partner_kind "
        "CHECK (partner_kind IS NULL OR partner_kind IN ('business', 'consumer'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE documents DROP CONSTRAINT IF EXISTS ck_documents_partner_kind")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS partner_kind")
