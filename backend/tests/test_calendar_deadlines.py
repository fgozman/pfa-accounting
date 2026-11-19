"""Calendar, deadlines and notification contract.

Requirement IDs: NOTIFICATIONS, ANAF-03 (working-day arithmetic).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from pfa.fiscal.calendar import CALENDARS, RO_CALENDAR_2025, RO_CALENDAR_2026
from pfa.models.base import NotificationResolution, NotificationStage, ObligationApplicability
from pfa.models.compliance import Obligation
from pfa.services.deadlines import (
    UNKNOWN_DEADLINE_PROVENANCE,
    DeadlineRule,
    compute_deadline,
    default_rule_for,
)
from pfa.services.notifications import build_candidate


def test_weekends_and_legal_holidays_are_not_working_days() -> None:
    calendar = RO_CALENDAR_2025
    assert calendar.is_working(date(2025, 6, 3))  # Tuesday
    assert not calendar.is_working(date(2025, 6, 7))  # Saturday
    assert not calendar.is_working(date(2025, 6, 8))  # Pentecost
    assert calendar.holiday_name(date(2025, 6, 8)) == "Rusaliile (prima zi)"
    assert not calendar.is_working(date(2025, 1, 1))
    assert not calendar.is_working(date(2025, 4, 18))  # Good Friday


def test_working_day_addition_skips_holidays_and_weekends() -> None:
    calendar = RO_CALENDAR_2025
    # Wednesday 30 April 2025: 1 May (Thursday) and 2 May (Friday) are holidays,
    # so five working days land on Friday 9 May.
    assert calendar.add_working_days(date(2025, 4, 30), 5) == date(2025, 5, 9)
    # the event day is day zero
    assert calendar.add_working_days(date(2025, 6, 2), 0) == date(2025, 6, 2)
    # 7 June 2025 is a Saturday; 8 and 9 June are Pentecost holidays
    assert calendar.add_working_days(date(2025, 6, 7), 0) == date(2025, 6, 10)


def test_working_days_between_counts_only_working_days() -> None:
    calendar = RO_CALENDAR_2026
    count = calendar.working_days_between(date(2026, 1, 1), date(2026, 1, 9))
    # 1 and 2 January (New Year), 6 and 7 January (Epiphany and St John) are legal
    # holidays, so only 5, 8 and 9 January are working days
    assert count == 3


def test_leap_year_and_year_boundary_are_handled() -> None:
    calendar = CALENDARS.get("RO-2024-2027.1")
    # 29 February 2028 is a Tuesday and therefore a working day
    assert calendar.is_working(date(2028, 2, 29))
    assert calendar.add_working_days(date(2028, 2, 28), 3) == date(2028, 3, 2)


def test_dst_transition_does_not_break_local_dates() -> None:
    # Europe/Bucharest switches to summer time on 30 March 2025 (last Sunday of March)
    start = datetime(2025, 3, 29, 12, 0, tzinfo=UTC).astimezone()
    assert start is not None
    local = datetime(2025, 3, 30, 1, 30, tzinfo=UTC).astimezone(
        __import__("zoneinfo").ZoneInfo("Europe/Bucharest")
    )
    assert local.utcoffset() is not None
    assert RO_CALENDAR_2025.is_working(date(2025, 3, 31))


# ---------------------------------------------------------------------------
# Deadline strategies
# ---------------------------------------------------------------------------


def test_d212_deadline_is_25_may_of_the_following_year() -> None:
    rule = default_rule_for("D212")
    assert rule is not None
    result = compute_deadline(
        rule, period_start=date(2025, 1, 1), period_end=date(2025, 12, 31)
    )
    assert result.filing_due_date == date(2026, 5, 25)
    assert result.payment_due_date == date(2026, 5, 25)
    assert result.filing_cutoff_at is not None
    assert result.filing_cutoff_at.tzinfo is not None


def test_d300_deadline_is_the_25th_of_the_following_month() -> None:
    rule = default_rule_for("D300")
    result = compute_deadline(rule, period_start=date(2025, 8, 1), period_end=date(2025, 8, 31))
    assert result.filing_due_date == date(2025, 9, 25)


def test_efactura_deadline_uses_working_days_not_an_hour_timer() -> None:
    rule = default_rule_for("EFACTURA")
    assert rule is not None
    assert "120" not in str(rule.parameters)
    # 30 April 2025 + 5 working days = 9 May 2025 (1 and 2 May are holidays)
    result = compute_deadline(
        rule, period_start=date(2025, 4, 30), period_end=date(2025, 4, 30), event_date=date(2025, 4, 30)
    )
    assert result.filing_due_date == date(2025, 5, 9)
    assert "zile lucrătoare" in result.provenance


def test_unverified_event_registration_rule_never_invents_a_date() -> None:
    rule = default_rule_for("D700")
    result = compute_deadline(rule, period_start=date(2025, 1, 1), period_end=date(2025, 12, 31))
    assert result.filing_due_date is None
    assert result.provenance == UNKNOWN_DEADLINE_PROVENANCE
    assert "verified_registration_deadline" in result.missing_information


def test_unknown_rule_kind_is_rejected_loudly() -> None:
    bad = DeadlineRule(
        form_code="X",
        version=1,
        rule_kind="guesswork",
        parameters={},
        effective_from=date(2025, 1, 1),
    )
    with pytest.raises(ValueError):
        compute_deadline(bad, period_start=date(2025, 1, 1), period_end=date(2025, 12, 31))


# ---------------------------------------------------------------------------
# Notification contract
# ---------------------------------------------------------------------------


def obligation(**overrides) -> Obligation:
    values = {
        "workspace_id": uuid.uuid4(),
        "recurrence_key": "D212:2025",
        "form_code": "D212",
        "form_name_ro": "Declarația unică",
        "obligation_kind": "filing",
        "period_start": date(2025, 1, 1),
        "period_end": date(2025, 12, 31),
        "applicability": ObligationApplicability.REQUIRED,
        "applicability_reason": "Există activitate în anul de venit.",
        "missing_inputs": [],
        "deadline_provenance": "D212 v1",
        "deadline_revision": 1,
        "resolution_status": NotificationResolution.OPEN,
        "filing_due_date": date(2026, 5, 25),
        "payment_due_date": date(2026, 5, 25),
    }
    values.update(overrides)
    return Obligation(**values)


def test_notification_stages_cover_each_reminder_offset() -> None:
    date(2026, 5, 25)
    cases = {
        date(2026, 4, 25): NotificationStage.REMINDER,  # 30 days
        date(2026, 5, 11): NotificationStage.REMINDER,  # 14 days
        date(2026, 5, 18): NotificationStage.REMINDER,  # 7 days
        date(2026, 5, 22): NotificationStage.REMINDER,  # 3 days
        date(2026, 5, 24): NotificationStage.REMINDER,  # 1 day
        date(2026, 5, 25): NotificationStage.DUE,
        date(2026, 6, 1): NotificationStage.OVERDUE,
    }
    for as_of, expected in cases.items():
        candidate = build_candidate(obligation(), as_of)
        assert candidate is not None
        assert candidate.stage is expected, as_of
        assert candidate.body_ro.count("25.05.2026") >= 1


def test_notification_shows_absolute_date_days_remaining_and_next_step() -> None:
    candidate = build_candidate(obligation(), date(2026, 5, 18))
    assert candidate is not None
    assert "25.05.2026" in candidate.body_ro
    assert "7 zile" in candidate.body_ro
    assert candidate.next_step_kind == "prepare_declaration"
    assert candidate.obligation.form_code in candidate.body_ro


def test_overdue_on_the_research_baseline_date() -> None:
    candidate = build_candidate(obligation(), date(2026, 9, 16))
    assert candidate is not None
    assert candidate.stage == NotificationStage.OVERDUE
    assert "restanță" in candidate.body_ro


def test_unknown_deadline_produces_an_action_not_an_invented_date() -> None:
    candidate = build_candidate(
        obligation(
            form_code="D700",
            filing_due_date=None,
            payment_due_date=None,
            deadline_provenance=UNKNOWN_DEADLINE_PROVENANCE,
        ),
        date(2026, 9, 16),
    )
    assert candidate is not None
    assert candidate.stage == NotificationStage.DETECTED
    assert "Termen de verificat" in candidate.body_ro


def test_missing_information_is_listed_and_blocks_the_next_step() -> None:
    candidate = build_candidate(
        obligation(
            applicability=ObligationApplicability.NEEDS_INFORMATION,
            missing_inputs=["cas_election", "prior_losses"],
        ),
        date(2026, 5, 18),
    )
    assert candidate is not None
    assert candidate.next_step_kind == "resolve_missing_information"
    # the body names what the user has to complete, never the storage key
    assert "opțiunea CAS" in candidate.body_ro
    assert "pierderile reportate" in candidate.body_ro
    assert "cas_election" not in candidate.body_ro


def test_short_window_obligation_gets_a_one_day_reminder() -> None:
    candidate = build_candidate(
        obligation(
            form_code="EFACTURA",
            obligation_kind="transmission",
            period_start=date(2026, 9, 14),
            period_end=date(2026, 9, 14),
            filing_due_date=date(2026, 9, 21),
            payment_due_date=None,
        ),
        date(2026, 9, 16),
    )
    assert candidate is not None
    assert candidate.stage == NotificationStage.DETECTED  # appears immediately
    later = build_candidate(
        obligation(
            form_code="EFACTURA",
            obligation_kind="transmission",
            period_start=date(2026, 9, 14),
            period_end=date(2026, 9, 14),
            filing_due_date=date(2026, 9, 21),
            payment_due_date=None,
        ),
        date(2026, 9, 20),
    )
    assert later is not None
    assert later.stage == NotificationStage.REMINDER
    assert later.offset == 1


def test_resolved_obligations_produce_no_notification() -> None:
    assert build_candidate(obligation(resolution_status=NotificationResolution.FILED), date(2026, 5, 18)) is None
    assert (
        build_candidate(
            obligation(applicability=ObligationApplicability.NOT_REQUIRED), date(2026, 5, 18)
        )
        is None
    )


def test_catch_up_produces_one_current_reminder() -> None:
    """Several missed offsets must not produce a flood."""
    as_of = date(2026, 5, 24)  # 1 day before the deadline
    candidate = build_candidate(obligation(), as_of)
    assert candidate is not None
    assert candidate.offset == 1
    # the key includes the deadline revision, so a revised deadline re-notifies
    assert str(candidate.obligation.deadline_revision) in (
        f"{candidate.obligation.workspace_id}:{candidate.obligation.id}:"
        f"{candidate.obligation.deadline_revision}:{candidate.stage}:{candidate.offset}"
    )


def test_dst_boundary_is_handled_for_cutoffs() -> None:
    rule = default_rule_for("D212")
    result = compute_deadline(rule, period_start=date(2025, 1, 1), period_end=date(2025, 12, 31))
    assert result.filing_cutoff_at is not None
    assert result.filing_cutoff_at.hour == 23
    assert result.filing_cutoff_at.utcoffset() == timedelta(hours=3)


def test_2025_and_2026_calendars_are_separate_versions() -> None:
    assert RO_CALENDAR_2025.version != RO_CALENDAR_2026.version
    assert RO_CALENDAR_2025.reviewed is False  # awaiting professional review
