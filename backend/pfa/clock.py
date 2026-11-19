"""Time helpers.

Time is injectable so tests can freeze it and so deadline logic never depends on
the process time zone.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

#: legal time zone of Romanian deadlines (also ``pfa.fiscal.calendar.BUCHAREST``); defined
#: here so this shared module depends on no domain package
BUCHAREST = ZoneInfo("Europe/Bucharest")

_override: datetime | None = None


def now() -> datetime:
    if _override is not None:
        return _override
    return datetime.now(tz=UTC)


def today() -> date:
    return local_now().date()


def local_now(tz: ZoneInfo = BUCHAREST) -> datetime:
    return now().astimezone(tz)


def freeze(moment: datetime) -> None:
    """Freeze time for tests."""
    global _override
    _override = moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def unfreeze() -> None:
    global _override
    _override = None
