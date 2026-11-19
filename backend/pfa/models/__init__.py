"""SQLAlchemy models.

Importing this package registers every mapped class on ``Base.metadata``; Alembic
and ``create_all`` rely on that side effect.  The authorization server's
``oauth_*`` tables are *not* here: they live in their own database with their own
metadata (:class:`pfa.oauth.models.OAuthBase`) and migrations (``migrations_oauth/``).
"""

from pfa.models import (
    accounts,
    anaf,
    books,
    compliance,
    documents,
    money,
    operations,
    tenancy,
)
from pfa.models.base import Base

__all__ = [
    "Base",
    "accounts",
    "anaf",
    "books",
    "compliance",
    "documents",
    "money",
    "operations",
    "tenancy",
]
