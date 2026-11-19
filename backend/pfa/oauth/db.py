"""The authorization server's own database (``PFA_OAUTH_DATABASE_URL``).

Every ``oauth_*`` table lives there and nowhere else.  The server never uses the
application database (:func:`pfa.db.get_database`), and the application never
opens this one, except the in-process adapters in :mod:`pfa.wiring`, which stand
in for HTTP calls.
"""

from __future__ import annotations

from collections.abc import Callable

from pfa.config import get_settings
from pfa.db import Database

_database: Database | None = None


def get_oauth_database() -> Database:
    global _database
    if _database is None:
        settings = get_settings()
        _database = Database.from_url(settings.oauth_database_url, settings)
    return _database


def override_oauth_database(factory: Callable[[], Database] | None) -> None:
    """Test hook: replace or reset the process-wide OAuth database."""
    global _database
    _database = factory() if factory else None


async def dispose_oauth_database() -> None:
    global _database
    if _database is not None:
        await _database.dispose()
        _database = None
