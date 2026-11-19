"""Versioned Romanian legal calendar (Europe/Bucharest).

Working-day arithmetic is never approximated with a hardcoded timer: each
obligation's deadline rule references a calendar version, and legal holidays are
data, not code.  Weekend/holiday rules, special extensions and leap years are all
handled here.

The holiday table below is *unreviewed* research data: ``reviewed=False`` means a
Romanian tax professional must confirm it before a rule pack using it is
approved for production (see specification §14).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

BUCHAREST = ZoneInfo("Europe/Bucharest")


@dataclass(frozen=True, slots=True)
class Holiday:
    day: date
    name: str
    #: True for days that are statutory free days every year, False for days
    #: declared non-working only in specific years
    statutory: bool = True


#: Orthodox Easter dates used to derive Good Friday / Easter / Pentecost.
_ORTHODOX_EASTER = {
    2024: date(2024, 5, 5),
    2025: date(2025, 4, 20),
    2026: date(2026, 4, 12),
    2027: date(2027, 5, 2),
    2028: date(2028, 4, 16),
}


def _easter_derived(year: int) -> list[Holiday]:
    easter = _ORTHODOX_EASTER.get(year)
    if easter is None:
        return []
    return [
        Holiday(easter - timedelta(days=2), "Vinerea Mare"),
        Holiday(easter, "Paștele (prima zi)"),
        Holiday(easter + timedelta(days=1), "Paștele (a doua zi)"),
        Holiday(easter + timedelta(days=49), "Rusaliile (prima zi)"),
        Holiday(easter + timedelta(days=50), "Rusaliile (a doua zi)"),
    ]


#: Days declared non-working by a Government decision for a specific year
#: (bridge days and similar).  They are year-specific, so they are data.
_DECLARED_NON_WORKING: dict[int, list[date]] = {
    2025: [date(2025, 5, 2)],
}


def _year_holidays(year: int) -> list[Holiday]:
    fixed = [
        Holiday(date(year, 1, 1), "Anul Nou"),
        Holiday(date(year, 1, 2), "Anul Nou (a doua zi)"),
        Holiday(date(year, 1, 6), "Boboteaza"),
        Holiday(date(year, 1, 7), "Sfântul Ioan Botezătorul"),
        Holiday(date(year, 1, 24), "Ziua Unirii Principatelor Române", statutory=False),
        Holiday(date(year, 5, 1), "Ziua Muncii"),
        Holiday(date(year, 6, 1), "Ziua Copilului"),
        Holiday(date(year, 8, 15), "Adormirea Maicii Domnului"),
        Holiday(date(year, 11, 30), "Sfântul Andrei"),
        Holiday(date(year, 12, 1), "Ziua Națională a României"),
        Holiday(date(year, 12, 25), "Crăciunul (prima zi)"),
        Holiday(date(year, 12, 26), "Crăciunul (a doua zi)"),
    ]
    declared = [
        Holiday(day, "Zi liberă declarată prin hotărâre de guvern", statutory=False)
        for day in _DECLARED_NON_WORKING.get(year, [])
    ]
    return fixed + _easter_derived(year) + declared


@dataclass(frozen=True, slots=True)
class LegalCalendar:
    """A versioned calendar.  Distinct versions may extend or remove days."""

    version: str
    holidays: dict[date, Holiday] = field(default_factory=dict)
    #: dates explicitly declared working although they fall on a weekend
    working_exceptions: frozenset[date] = frozenset()
    #: dates with a special statutory extension (e.g. an extended filing window)
    extensions: dict[str, dict[str, str]] = field(default_factory=dict)
    source_url: str | None = None
    reviewed: bool = False

    # -- day classification ---------------------------------------------
    def is_weekend(self, day: date) -> bool:
        return day.weekday() >= 5

    def is_non_working(self, day: date) -> bool:
        if day in self.working_exceptions:
            return False
        if day in self.holidays:
            return True
        return self.is_weekend(day)

    def is_working(self, day: date) -> bool:
        return not self.is_non_working(day)

    def holiday_name(self, day: date) -> str | None:
        holiday = self.holidays.get(day)
        return holiday.name if holiday else None

    # -- arithmetic ------------------------------------------------------
    def next_working_day(self, day: date) -> date:
        candidate = day
        for _ in range(400):
            if self.is_working(candidate):
                return candidate
            candidate += timedelta(days=1)
        raise ValueError("no working day found within 400 days")  # pragma: no cover

    def previous_working_day(self, day: date) -> date:
        candidate = day
        for _ in range(400):
            if self.is_working(candidate):
                return candidate
            candidate -= timedelta(days=1)
        raise ValueError("no working day found within 400 days")  # pragma: no cover

    def add_working_days(self, start: date, count: int) -> date:
        """The date that is ``count`` working days after ``start``.

        The event day itself is day zero: the statutory phrasing is "within N
        working days from the event", and only working days advance the count.
        ``count=0`` returns the first working day on or after ``start``.
        """
        if count < 0:
            raise ValueError("count must be >= 0")
        if count == 0:
            return self.next_working_day(start)
        remaining = count
        candidate = start
        while remaining > 0:
            candidate += timedelta(days=1)
            if (candidate - start).days > 400:  # pragma: no cover - defensive
                raise ValueError("working-day count exceeded 400 days")
            if self.is_working(candidate):
                remaining -= 1
        return candidate

    def working_days_between(self, start: date, end: date) -> int:
        """Count of working days in ``(start, end]``."""
        if end < start:
            raise ValueError("end must not precede start")
        count = 0
        candidate = start + timedelta(days=1)
        while candidate <= end:
            if self.is_working(candidate):
                count += 1
            candidate += timedelta(days=1)
        return count

    def add_calendar_days(self, start: date, count: int) -> date:
        return start + timedelta(days=count)

    # -- time zone helpers ----------------------------------------------
    @staticmethod
    def start_of_day(day: date, tz: ZoneInfo = BUCHAREST) -> datetime:
        return datetime.combine(day, time(0, 0), tzinfo=tz)

    @staticmethod
    def end_of_day(day: date, tz: ZoneInfo = BUCHAREST) -> datetime:
        return datetime.combine(day, time(23, 59, 59, 999999), tzinfo=tz)

    @staticmethod
    def local_now(now: datetime | None = None, tz: ZoneInfo = BUCHAREST) -> datetime:
        value = now or datetime.now(tz=tz)
        return value.astimezone(tz) if value.tzinfo else value.replace(tzinfo=tz)


class CalendarRegistry:
    """Versioned calendars, looked up by (version, year)."""

    def __init__(self, calendars: dict[str, LegalCalendar]) -> None:
        self._calendars = calendars

    def get(self, version: str) -> LegalCalendar:
        try:
            return self._calendars[version]
        except KeyError as exc:  # pragma: no cover - defensive
            raise KeyError(f"unknown legal calendar version {version!r}") from exc

    def versions(self) -> list[str]:
        return sorted(self._calendars)


def _build_calendar(version: str, years: range, source_url: str | None, reviewed: bool) -> LegalCalendar:
    holidays: dict[date, Holiday] = {}
    for year in years:
        for holiday in _year_holidays(year):
            holidays[holiday.day] = holiday
    return LegalCalendar(
        version=version,
        holidays=holidays,
        source_url=source_url,
        reviewed=reviewed,
    )


#: Roman holidays are published in the Labour Code (art. 139) and confirmed
#: yearly by Government decision for the movable feasts.
RO_CALENDAR_2025 = _build_calendar(
    "RO-2025.1",
    range(2025, 2026),
    source_url="https://legislatie.just.ro/Public/DetaliiDocument/127660",
    reviewed=False,
)
RO_CALENDAR_2026 = _build_calendar(
    "RO-2026.1",
    range(2026, 2027),
    source_url="https://legislatie.just.ro/Public/DetaliiDocument/127660",
    reviewed=False,
)
#: A calendar covering several years, used by multi-year deadline evaluation.
RO_CALENDAR_2024_2027 = _build_calendar(
    "RO-2024-2027.1",
    range(2024, 2028),
    source_url="https://legislatie.just.ro/Public/DetaliiDocument/127660",
    reviewed=False,
)

CALENDARS = CalendarRegistry(
    {
        RO_CALENDAR_2025.version: RO_CALENDAR_2025,
        RO_CALENDAR_2026.version: RO_CALENDAR_2026,
        RO_CALENDAR_2024_2027.version: RO_CALENDAR_2024_2027,
    }
)
