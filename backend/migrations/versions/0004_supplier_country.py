"""Intra-community supplier country/VAT id on documents.

Revision ID: 0004_supplier_country
Revises: 0003_intracommunity_operation
Create Date: 2026-09-17

A D390/D394 operation needs the counterparty's country (and, where required, its
VAT id). Documents created without a registered counterparty — the normal path in
the UI, which stores ``supplier_name_raw`` — had no place to record it, so the
declaration could not be classified. These are document-level supplier facts, the
same way ``supplier_name_raw`` already is.
"""

from __future__ import annotations

from alembic import op

revision = "0004_supplier_country"
down_revision = "0003_intracommunity_operation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE documents ADD COLUMN supplier_country varchar(2)")
    op.execute("ALTER TABLE documents ADD COLUMN supplier_vat_id varchar(32)")
    op.execute(
        "ALTER TABLE documents ADD CONSTRAINT ck_documents_supplier_country "
        "CHECK (supplier_country IS NULL OR supplier_country ~ '^[A-Z]{2}$')"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE documents DROP CONSTRAINT IF EXISTS ck_documents_supplier_country")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS supplier_vat_id")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS supplier_country")
