"""Notifications and reminders.

A notification is a *view* of a persisted obligation.  It never becomes the
source of compliance truth, reading or dismissing one never completes a filing,
and per-user read/dismiss/snooze state never hides an obligation from colleagues.

Reminder offsets are calendar days before filing.  Short-window obligations
(e-Factura) get an immediate notification and a one-day reminder.  Working-day
arithmetic always comes from the versioned legal calendar, never from these
offsets.  When several offsets were missed before detection, exactly one current
reminder is produced instead of a flood.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from pfa import clock
from pfa.config import get_settings
from pfa.errors import NotFound
from pfa.models.base import (
    NotificationResolution,
    NotificationStage,
    ObligationApplicability,
)
from pfa.models.compliance import Obligation
from pfa.repo import compliance as compliance_repo
from pfa.repo import tenancy as tenancy_repo
from pfa.services.audit import emit_event
from pfa.services.deadlines import UNKNOWN_DEADLINE_PROVENANCE, days_remaining, is_overdue
from pfa.services.labels import missing_input_label

#: obligations whose whole legal window is short get a different reminder shape
SHORT_WINDOW_FORMS = {"EFACTURA"}

DEFAULT_OFFSETS = (30, 14, 7, 3, 1, 0, -7)


@dataclass(frozen=True, slots=True)
class NotificationCandidate:
    obligation: Obligation
    stage: NotificationStage
    offset: int | None
    title_ro: str
    body_ro: str
    next_step_kind: str
    next_step_ref: str | None


def _next_step(obligation: Obligation) -> tuple[str, str | None]:
    if obligation.obligation_kind == "registration":
        # registration forms are assisted: the place to act is the fiscal profile, not a
        # declaration the application cannot generate
        return "review_registration", obligation.form_code
    if obligation.applicability == ObligationApplicability.NEEDS_INFORMATION:
        return "resolve_missing_information", obligation.form_code
    if obligation.declaration_id is not None:
        return "review_declaration", str(obligation.declaration_id)
    if obligation.form_code == "EFACTURA":
        return "transmit_invoice", obligation.form_code
    return "prepare_declaration", obligation.form_code


def _stage_for(obligation: Obligation, as_of: date) -> tuple[NotificationStage, int | None]:
    due = obligation.filing_due_date
    if due is None:
        return NotificationStage.DETECTED, None
    if is_overdue(due, as_of):
        return NotificationStage.OVERDUE, -7
    remaining = days_remaining(due, as_of)
    if remaining == 0:
        return NotificationStage.DUE, 0
    if obligation.form_code in SHORT_WINDOW_FORMS:
        # short legal windows: appear immediately, then a single one-day reminder
        if remaining == 1:
            return NotificationStage.REMINDER, 1
        return NotificationStage.DETECTED, None
    # the *latest* reminder offset that has already been reached, so a detection
    # after several missed offsets yields exactly one current reminder
    for offset in sorted(DEFAULT_OFFSETS):
        if remaining <= offset:
            return NotificationStage.REMINDER, offset
    return NotificationStage.DETECTED, None


def build_candidate(obligation: Obligation, as_of: date) -> NotificationCandidate | None:
    if obligation.resolution_status != NotificationResolution.OPEN:
        return None
    if obligation.applicability == ObligationApplicability.NOT_REQUIRED:
        return None

    stage, offset = _stage_for(obligation, as_of)
    due = obligation.filing_due_date
    remaining = days_remaining(due, as_of) if due else None

    period = (
        f"{obligation.period_start.strftime('%d.%m.%Y')}"
        if obligation.period_start == obligation.period_end
        else f"{obligation.period_start.strftime('%d.%m.%Y')}–{obligation.period_end.strftime('%d.%m.%Y')}"
    )
    name = obligation.form_name_ro or obligation.form_code
    if due is None:
        body = (
            f"{name} ({obligation.form_code}), perioada {period} — "
            f"{UNKNOWN_DEADLINE_PROVENANCE}. Motivele aplicabilității: "
            f"{obligation.applicability_reason}"
        )
        title = f"{obligation.form_code} — termen de verificat"
    elif stage == NotificationStage.OVERDUE:
        body = (
            f"{name} ({obligation.form_code}), perioada {period} — depunere până la "
            f"{due.strftime('%d.%m.%Y')} inclusiv — restanță de {abs(remaining or 0)} zile. "
            f"{obligation.applicability_reason}"
        )
        title = f"{obligation.form_code} — restanță"
    elif stage == NotificationStage.DUE:
        body = (
            f"{name} ({obligation.form_code}), perioada {period} — scadență astăzi, "
            f"{due.strftime('%d.%m.%Y')} inclusiv."
        )
        title = f"{obligation.form_code} — scadență astăzi"
    else:
        body = (
            f"{name} ({obligation.form_code}), perioada {period} — depunere până la "
            f"{due.strftime('%d.%m.%Y')} inclusiv — {remaining} zile rămase."
        )
        title = f"{obligation.form_code} — {remaining} zile până la termen"
    if obligation.payment_due_date and obligation.payment_due_date != due:
        body += (
            f" Plata are termen separat: {obligation.payment_due_date.strftime('%d.%m.%Y')}."
        )
    if obligation.missing_inputs:
        labels = ", ".join(missing_input_label(key) for key in obligation.missing_inputs)
        body += f" Informații lipsă: {labels}."

    step_kind, step_ref = _next_step(obligation)
    return NotificationCandidate(
        obligation=obligation,
        stage=stage,
        offset=offset,
        title_ro=title,
        body_ro=body,
        next_step_kind=step_kind,
        next_step_ref=step_ref,
    )


def dedupe_key(candidate: NotificationCandidate) -> str:
    obligation = candidate.obligation
    offset = "unknown" if candidate.offset is None else str(candidate.offset)
    return (
        f"{obligation.workspace_id}:{obligation.id}:{obligation.deadline_revision}:"
        f"{candidate.stage}:{offset}"
    )


async def evaluate_notifications(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    obligations: list,
    as_of: date | None = None,
) -> list:
    as_of = as_of or clock.today()
    created = []
    for obligation in obligations:
        candidate = build_candidate(obligation, as_of)
        if candidate is None:
            # the reason for the reminder disappeared (evidence edited or deleted):
            # withdraw the open reminder, keep the record for the audit trail
            if obligation.applicability == ObligationApplicability.NOT_REQUIRED:
                await compliance_repo.resolve_notifications_for_obligation(
                    session, obligation_id=obligation.id, resolved_at=clock.now()
                )
            continue
        key = dedupe_key(candidate)
        existing = await compliance_repo.notification_by_dedupe(session, key)
        await compliance_repo.resolve_other_notifications_for_obligation(
            session,
            obligation_id=obligation.id,
            current_id=existing.id if existing is not None else None,
            resolved_at=clock.now(),
        )
        if existing is not None:
            created.append(existing)
            continue
        notification = await compliance_repo.insert_notification(
            session,
            workspace_id=workspace_id,
            obligation_id=obligation.id,
            stage=candidate.stage.value,
            reminder_offset_days=candidate.offset,
            deadline_revision=obligation.deadline_revision,
            title_ro=candidate.title_ro,
            body_ro=candidate.body_ro,
            due_on=obligation.filing_due_date,
            payment_due_on=obligation.payment_due_date,
            next_step_kind=candidate.next_step_kind,
            next_step_ref=candidate.next_step_ref,
            dedupe_key=key,
        )
        created.append(notification)
        await _fan_out(session, notification=notification, workspace_id=workspace_id)
    return created


async def _fan_out(session: AsyncSession, *, notification, workspace_id: uuid.UUID) -> None:
    """One in-app delivery state per active member: reading is per user, the
    obligation is shared."""
    member_ids = await tenancy_repo.active_member_ids(session, workspace_id)
    await compliance_repo.insert_deliveries(
        session,
        workspace_id=workspace_id,
        notification_id=notification.id,
        dedupe_key=notification.dedupe_key,
        recipient_user_ids=member_ids,
        delivered_at=clock.now(),
    )
    await emit_event(
        session,
        event_type="notification.created",
        workspace_id=workspace_id,
        payload={"notification_id": str(notification.id), "stage": str(notification.stage)},
        dedupe_key=f"notification-created:{notification.dedupe_key}",
    )


async def announce_deadline_revision(
    session: AsyncSession,
    *,
    obligation,
    previous_due: date | None,
):
    """A changed legal deadline updates pending reminders and notifies once."""
    if previous_due is None or obligation.filing_due_date is None:
        return None
    if previous_due == obligation.filing_due_date:
        return None
    key = (
        f"{obligation.workspace_id}:{obligation.id}:{obligation.deadline_revision}:"
        f"{NotificationStage.DEADLINE_REVISED}"
    )
    existing = await compliance_repo.notification_by_dedupe(session, key)
    if existing is not None:
        return existing
    notification = await compliance_repo.insert_notification(
        session,
        workspace_id=obligation.workspace_id,
        obligation_id=obligation.id,
        stage=NotificationStage.DEADLINE_REVISED.value,
        reminder_offset_days=None,
        deadline_revision=obligation.deadline_revision,
        title_ro=f"{obligation.form_code} — termen legal modificat",
        body_ro=(
            f"Termenul pentru {obligation.form_code} s-a modificat de la "
            f"{previous_due.strftime('%d.%m.%Y')} la "
            f"{obligation.filing_due_date.strftime('%d.%m.%Y')}. "
            f"Motiv: {obligation.deadline_provenance or 'actualizare a regulii de termen'}."
        ),
        due_on=obligation.filing_due_date,
        payment_due_on=obligation.payment_due_date,
        next_step_kind="review_deadline",
        next_step_ref=obligation.form_code,
        dedupe_key=key,
    )
    await _fan_out(session, notification=notification, workspace_id=obligation.workspace_id)
    return notification


async def notification_feed(
    session: AsyncSession, *, workspace_id: uuid.UUID, user_id: uuid.UUID, limit: int = 100
) -> list[dict]:
    """Feed ordered: rejected/overdue first, then upcoming, then incomplete."""
    rows = await compliance_repo.notification_feed(
        session, workspace_id=workspace_id, user_id=user_id, limit=limit
    )
    today = clock.today()
    items = []
    for row in rows:
        due = row.due_on
        overdue = due is not None and due < today
        items.append(
            {
                "id": str(row.id),
                "obligation_id": str(row.obligation_id),
                "stage": str(row.stage),
                "title_ro": row.title_ro,
                "body_ro": row.body_ro,
                "due_on": due.isoformat() if due else None,
                "payment_due_on": row.payment_due_on.isoformat() if row.payment_due_on else None,
                "days_remaining": days_remaining(due, today) if due else None,
                "overdue": overdue,
                "read": row.read_at is not None,
                "dismissed": row.dismissed_at is not None,
                "snoozed_until": row.snoozed_until.isoformat() if row.snoozed_until else None,
                "snooze_suppressed": bool(
                    row.snoozed_until and row.snoozed_until >= today and not overdue
                ),
                "next_step_kind": row.next_step_kind,
                "next_step_ref": row.next_step_ref,
                "deadline_revision": row.deadline_revision,
            }
        )
    order = {
        "rejected": 0,
        "overdue": 1,
        "due": 2,
        "reminder": 3,
        "detected": 4,
        "deadline_revised": 5,
    }
    items.sort(key=lambda item: (order.get(item["stage"], 9), item["due_on"] or "9999-12-31"))
    return items


async def mark_read(
    session: AsyncSession, *, workspace_id: uuid.UUID, user_id: uuid.UUID, notification_id: uuid.UUID
):
    await _state(session, workspace_id=workspace_id, user_id=user_id, notification_id=notification_id)
    await compliance_repo.mark_state_read(
        session, notification_id=notification_id, user_id=user_id, at=clock.now()
    )
    return await _state(
        session, workspace_id=workspace_id, user_id=user_id, notification_id=notification_id
    )


async def dismiss(
    session: AsyncSession, *, workspace_id: uuid.UUID, user_id: uuid.UUID, notification_id: uuid.UUID
):
    await _state(session, workspace_id=workspace_id, user_id=user_id, notification_id=notification_id)
    await compliance_repo.mark_state_dismissed(
        session, notification_id=notification_id, user_id=user_id, at=clock.now()
    )
    return await _state(
        session, workspace_id=workspace_id, user_id=user_id, notification_id=notification_id
    )


async def snooze(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    notification_id: uuid.UUID,
    until: date,
):
    """Snooze cannot suppress overdue status."""
    await _state(session, workspace_id=workspace_id, user_id=user_id, notification_id=notification_id)
    await compliance_repo.mark_state_snoozed(
        session, notification_id=notification_id, user_id=user_id, until=until
    )
    return await _state(
        session, workspace_id=workspace_id, user_id=user_id, notification_id=notification_id
    )


async def set_email_reminders(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    enabled: bool,
    notification_id: uuid.UUID | None = None,
) -> int:
    return await compliance_repo.set_email_reminders(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        enabled=enabled,
        notification_id=notification_id,
    )


async def resolve_filing(session: AsyncSession, *, obligation, resolved_at=None) -> None:
    """Verified acceptance resolves filing reminders."""
    at = resolved_at or clock.now()
    await compliance_repo.resolve_obligation_filing(
        session, obligation_id=obligation.id, resolved_at=at
    )
    await compliance_repo.resolve_notifications_for_obligation(
        session, obligation_id=obligation.id, resolved_at=at
    )


async def resolve_payment(session: AsyncSession, *, obligation, resolved_at=None) -> None:
    """Recorded payment evidence resolves payment reminders independently."""
    await compliance_repo.resolve_obligation_payment(
        session, obligation_id=obligation.id, resolved_at=resolved_at or clock.now()
    )


async def reopen_after_rejection(session: AsyncSession, *, obligation) -> None:
    """A rejected filing reopens attention immediately."""
    await compliance_repo.reopen_obligation(session, obligation_id=obligation.id)
    await compliance_repo.resolve_notifications_for_obligation(
        session, obligation_id=obligation.id, resolved_at=None
    )


async def _state(
    session: AsyncSession, *, workspace_id: uuid.UUID, user_id: uuid.UUID, notification_id: uuid.UUID
):
    notification = await compliance_repo.get_notification(
        session, workspace_id=workspace_id, notification_id=notification_id
    )
    if notification is None:
        raise NotFound("Notificarea nu există.")
    return await compliance_repo.upsert_user_state(
        session,
        notification_id=notification_id,
        workspace_id=workspace_id,
        user_id=user_id,
    )


def reminder_offsets() -> tuple[int, ...]:
    return get_settings().reminder_offsets_days or DEFAULT_OFFSETS


def next_day_after_detection(day: date) -> date:
    return day + timedelta(days=1)
