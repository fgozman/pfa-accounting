"""The business-use share of an expense is stated by the user, not assumed.

Revision ID: 0010_business_use_unstated
Revises: 0009_remove_cancelled_status
Create Date: 2026-09-20

Every expense carried a business-use percentage because the column was NOT NULL with a
default of 100, and the expense form shipped a default of 50% with a "vehicle" category
and "partially deductible" treatment. A user who only typed an amount silently got four
tax determinations they never made. The column is now nullable: ``NULL`` means "not
stated", which the declaration reports as an unresolved input instead of multiplying the
deductible amount by an invented share.
"""

from __future__ import annotations

from alembic import op

revision = "0010_business_use_unstated"
down_revision = "0009_remove_cancelled_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE documents ALTER COLUMN business_use_percent DROP NOT NULL")
    op.execute("ALTER TABLE documents ALTER COLUMN business_use_percent DROP DEFAULT")


def downgrade() -> None:
    # rows that never stated a share fall back to the previous implicit 100%
    op.execute("UPDATE documents SET business_use_percent = 100 WHERE business_use_percent IS NULL")
    op.execute("ALTER TABLE documents ALTER COLUMN business_use_percent SET DEFAULT 100")
    op.execute("ALTER TABLE documents ALTER COLUMN business_use_percent SET NOT NULL")
