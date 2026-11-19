"""When the activity started, so the initial registration (D070) can be detected.

Revision ID: 0011_activity_start
Revises: 0010_business_use_unstated
Create Date: 2026-09-20

D070 is the *initial* fiscal registration, so it belongs to the year the activity began.
Nothing in the application recorded that year: the ``activity_periods`` table exists but is
never written, so D070 could never be raised. ``pfa_profiles.started_on`` records the fact
the user knows; ``NULL`` keeps meaning "not stated", in which case no initial registration
is claimed.
"""

from __future__ import annotations

from alembic import op

revision = "0011_activity_start"
down_revision = "0010_business_use_unstated"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE pfa_profiles ADD COLUMN started_on date")


def downgrade() -> None:
    op.execute("ALTER TABLE pfa_profiles DROP COLUMN IF EXISTS started_on")
