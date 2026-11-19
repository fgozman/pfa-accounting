"""Compliance SQL: obligations, notifications and their per-user state.

An obligation is the evaluated compliance state of one form for one period; a
notification is a *view* of a persisted obligation, never the source of truth.
That distinction is what these statements implement: notifications are keyed by a
dedupe string that includes the deadline revision, so a revised deadline
re-notifies while an unchanged one does not.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from pfa.repo.query import Row, all_rows, col, one, returning, run, val, where
from pfa.repo.query import jsonb as to_jsonb

OBLIGATION_COLUMNS = """
    id, workspace_id, taxpayer_id, form_code, form_name_ro, obligation_kind, period_start,
    period_end, applicability, applicability_reason, rule_pack_code, rule_pack_version,
    legal_rule_version, source_url, generation_available_from, generation_blocked_reason,
    filing_due_date, filing_cutoff_at, filing_timezone, payment_due_date, deadline_rule_id,
    deadline_provenance, missing_inputs, triggering_evidence, declaration_id,
    resolution_status, filing_resolved_at, payment_resolved_at, recurrence_key,
    deadline_revision, capabilities, unsupported_reason
"""

NOTIFICATION_COLUMNS = """
    id, workspace_id, obligation_id, stage, reminder_offset_days, deadline_revision, title_ro,
    body_ro, due_on, payment_due_on, next_step_kind, next_step_ref, dedupe_key, created_at,
    resolved_at
"""


# ---------------------------------------------------------------------------
# Obligations
# ---------------------------------------------------------------------------


async def get_obligation_by_recurrence(
    session: AsyncSession, *, workspace_id: uuid.UUID, recurrence_key: str
) -> Row | None:
    return await one(
        session,
        f"""
        SELECT {OBLIGATION_COLUMNS} FROM obligations
         WHERE workspace_id = :workspace_id AND recurrence_key = :recurrence_key
        """,
        workspace_id=workspace_id,
        recurrence_key=recurrence_key,
    )


#: columns the evaluator may set; a fixed whitelist so statement text is never
#: built from caller data
OBLIGATION_INPUT_COLUMNS = (
    "taxpayer_id",
    "form_code",
    "form_name_ro",
    "obligation_kind",
    "period_start",
    "period_end",
    "applicability",
    "applicability_reason",
    "rule_pack_code",
    "rule_pack_version",
    "legal_rule_version",
    "source_url",
    "generation_available_from",
    "generation_blocked_reason",
    "filing_due_date",
    "filing_cutoff_at",
    "filing_timezone",
    "payment_due_date",
    "deadline_rule_id",
    "deadline_provenance",
    "missing_inputs",
    "triggering_evidence",
    "capabilities",
    "unsupported_reason",
)

_JSONB_OBLIGATION_COLUMNS = frozenset(
    {"missing_inputs", "triggering_evidence", "capabilities"}
)


async def upsert_obligation(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    recurrence_key: str,
    values: dict,
) -> Row:
    """Insert or update one obligation, bumping the deadline revision on change.

    The revision only advances when the filing deadline actually changes, which is
    what makes a revised legal deadline re-notify without re-notifying on every
    evaluation.
    """
    unknown = set(values) - set(OBLIGATION_INPUT_COLUMNS)
    if unknown:  # pragma: no cover - programming error, not user input
        raise ValueError(f"unknown obligation columns: {sorted(unknown)}")

    columns = [column for column in OBLIGATION_INPUT_COLUMNS if column in values]
    # jsonb parameters must be sent as JSON text, not as Python objects
    bound = {
        column: (to_jsonb(value) if column in _JSONB_OBLIGATION_COLUMNS else value)
        for column, value in values.items()
    }
    insert_values = ", ".join(
        f"CAST(:{column} AS jsonb)" if column in _JSONB_OBLIGATION_COLUMNS else f":{column}"
        for column in columns
    )
    updates = ", ".join(f"{column} = EXCLUDED.{column}" for column in columns)
    return await returning(
        session,
        f"""
        INSERT INTO obligations (
            workspace_id, recurrence_key, resolution_status, deadline_revision, {", ".join(columns)}
        )
        VALUES (
            :workspace_id, :recurrence_key, 'open', 1, {insert_values}
        )
        ON CONFLICT (workspace_id, recurrence_key) DO UPDATE
            SET {updates},
                deadline_revision = CASE
                    WHEN obligations.filing_due_date IS DISTINCT FROM EXCLUDED.filing_due_date
                     AND obligations.applicability <> 'not_required'
                    THEN obligations.deadline_revision + 1
                    ELSE obligations.deadline_revision
                END,
                updated_at = now()
        RETURNING {OBLIGATION_COLUMNS}
        """,
        workspace_id=workspace_id,
        recurrence_key=recurrence_key,
        **bound,
    )


async def list_obligations(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    income_year: int | None,
    actionable_only: bool,
) -> list[Row]:
    clause = where(
        "workspace_id = :workspace_id",
        "period_start >= :year_start" if income_year else None,
        "applicability <> 'not_required'" if actionable_only else None,
    )
    return await all_rows(
        session,
        f"""
        SELECT {OBLIGATION_COLUMNS} FROM obligations
        {clause}
         ORDER BY filing_due_date NULLS LAST, form_code
        """,
        workspace_id=workspace_id,
        year_start=date(income_year, 1, 1) if income_year else None,
    )


async def get_obligation(session: AsyncSession, *, workspace_id: uuid.UUID, obligation_id: uuid.UUID) -> Row | None:
    return await one(
        session,
        f"""
        SELECT {OBLIGATION_COLUMNS} FROM obligations
         WHERE id = :id AND workspace_id = :workspace_id
        """,
        id=obligation_id,
        workspace_id=workspace_id,
    )


async def obligations_overlapping(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    form_code: str,
    period_start: date,
    period_end: date,
) -> list[Row]:
    return await all_rows(
        session,
        f"""
        SELECT {OBLIGATION_COLUMNS} FROM obligations
         WHERE workspace_id = :workspace_id
           AND form_code = :form_code
           AND period_end >= :period_start
           AND period_start <= :period_end
        """,
        workspace_id=workspace_id,
        form_code=form_code,
        period_start=period_start,
        period_end=period_end,
    )


async def link_obligations_to_declaration(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    declaration_id: uuid.UUID,
    form_code: str,
    period_start: date,
    period_end: date,
) -> int:
    return await run(
        session,
        """
        UPDATE obligations SET declaration_id = :declaration_id, updated_at = now()
         WHERE workspace_id = :workspace_id
           AND form_code = :form_code
           AND period_end >= :period_start AND period_start <= :period_end
        """,
        workspace_id=workspace_id,
        declaration_id=declaration_id,
        form_code=form_code,
        period_start=period_start,
        period_end=period_end,
    )


async def resolve_obligation_filing(
    session: AsyncSession, *, obligation_id: uuid.UUID, resolved_at: datetime
) -> None:
    await run(
        session,
        """
        UPDATE obligations
           SET resolution_status = 'filed', filing_resolved_at = :resolved_at, updated_at = now()
         WHERE id = :id
        """,
        id=obligation_id,
        resolved_at=resolved_at,
    )


async def resolve_obligation_payment(
    session: AsyncSession, *, obligation_id: uuid.UUID, resolved_at: datetime
) -> None:
    await run(
        session,
        """
        UPDATE obligations
           SET payment_resolved_at = :resolved_at,
               resolution_status = CASE WHEN resolution_status = 'open' THEN 'paid'
                                        ELSE resolution_status END,
               updated_at = now()
         WHERE id = :id
        """,
        id=obligation_id,
        resolved_at=resolved_at,
    )


async def reopen_obligation(session: AsyncSession, *, obligation_id: uuid.UUID) -> None:
    await run(
        session,
        """
        UPDATE obligations
           SET resolution_status = 'open', filing_resolved_at = NULL, updated_at = now()
         WHERE id = :id
        """,
        id=obligation_id,
    )


async def supported_form_versions(session: AsyncSession, *, income_year: int) -> list[Row]:
    return await all_rows(
        session,
        """
        SELECT id, form_code, income_year, schema_version, artifact_format, validator_id,
               filing_channel, supported, published, correction_strategy, channel_evidence
          FROM form_versions
         WHERE income_year = :income_year
        """,
        income_year=income_year,
    )


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


async def notification_by_dedupe(session: AsyncSession, dedupe_key: str) -> Row | None:
    return await one(
        session,
        f"SELECT {NOTIFICATION_COLUMNS} FROM notifications WHERE dedupe_key = :dedupe_key",
        dedupe_key=dedupe_key,
    )


async def insert_notification(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    obligation_id: uuid.UUID,
    stage: str,
    reminder_offset_days: int | None,
    deadline_revision: int,
    title_ro: str,
    body_ro: str,
    due_on: date | None,
    payment_due_on: date | None,
    next_step_kind: str | None,
    next_step_ref: str | None,
    dedupe_key: str,
) -> Row:
    return await returning(
        session,
        f"""
        INSERT INTO notifications (
            workspace_id, obligation_id, stage, reminder_offset_days, deadline_revision,
            title_ro, body_ro, due_on, payment_due_on, next_step_kind, next_step_ref, dedupe_key
        )
        VALUES (
            :workspace_id, :obligation_id, :stage, :reminder_offset_days, :deadline_revision,
            :title_ro, :body_ro, :due_on, :payment_due_on, :next_step_kind, :next_step_ref,
            :dedupe_key
        )
        RETURNING {NOTIFICATION_COLUMNS}
        """,
        workspace_id=workspace_id,
        obligation_id=obligation_id,
        stage=stage,
        reminder_offset_days=reminder_offset_days,
        deadline_revision=deadline_revision,
        title_ro=title_ro,
        body_ro=body_ro,
        due_on=due_on,
        payment_due_on=payment_due_on,
        next_step_kind=next_step_kind,
        next_step_ref=next_step_ref,
        dedupe_key=dedupe_key,
    )


async def insert_deliveries(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    notification_id: uuid.UUID,
    dedupe_key: str,
    recipient_user_ids: list[uuid.UUID],
    delivered_at: datetime,
) -> None:
    """One in-app delivery and one user-state row per active member."""
    for recipient in recipient_user_ids:
        await run(
            session,
            """
            INSERT INTO notification_deliveries (
                notification_id, workspace_id, recipient_user_id, channel, status,
                attempted_at, delivered_at, dedupe_key
            )
            VALUES (:notification_id, :workspace_id, :recipient, 'in_app', 'delivered',
                    :delivered_at, :delivered_at, :dedupe_key)
            ON CONFLICT (dedupe_key) DO NOTHING
            """,
            notification_id=notification_id,
            workspace_id=workspace_id,
            recipient=recipient,
            delivered_at=delivered_at,
            dedupe_key=f"{dedupe_key}:in_app:{recipient}",
        )
        await run(
            session,
            """
            INSERT INTO notification_user_states (notification_id, workspace_id, user_id,
                                                  email_reminders)
            VALUES (:notification_id, :workspace_id, :recipient, false)
            ON CONFLICT (notification_id, user_id) DO NOTHING
            """,
            notification_id=notification_id,
            workspace_id=workspace_id,
            recipient=recipient,
        )


async def notification_feed(
    session: AsyncSession, *, workspace_id: uuid.UUID, user_id: uuid.UUID, limit: int
) -> list[Row]:
    """Open notifications with the caller's per-user state joined in."""
    bounded = min(max(int(limit), 1), 200)
    return await all_rows(
        session,
        f"""
        SELECT n.id, n.obligation_id, n.stage, n.title_ro, n.body_ro, n.due_on, n.payment_due_on,
               n.next_step_kind, n.next_step_ref, n.deadline_revision,
               s.read_at, s.dismissed_at, s.snoozed_until
          FROM (
            SELECT notifications.*,
                   row_number() OVER (
                     PARTITION BY obligation_id
                     ORDER BY deadline_revision DESC, created_at DESC,
                              reminder_offset_days ASC NULLS LAST, id DESC
                   ) AS reminder_rank
              FROM notifications
             WHERE workspace_id = :workspace_id AND resolved_at IS NULL
          ) n
          LEFT JOIN notification_user_states s
                 ON s.notification_id = n.id AND s.user_id = :user_id
         WHERE n.reminder_rank = 1
         ORDER BY n.created_at DESC
         LIMIT {bounded}
        """,
        workspace_id=workspace_id,
        user_id=user_id,
    )


async def get_notification(session: AsyncSession, *, workspace_id: uuid.UUID, notification_id: uuid.UUID) -> Row | None:
    return await one(
        session,
        f"""
        SELECT {NOTIFICATION_COLUMNS} FROM notifications
         WHERE id = :id AND workspace_id = :workspace_id
        """,
        id=notification_id,
        workspace_id=workspace_id,
    )


async def upsert_user_state(
    session: AsyncSession,
    *,
    notification_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Row:
    return await returning(
        session,
        """
        INSERT INTO notification_user_states (notification_id, workspace_id, user_id, email_reminders)
        VALUES (:notification_id, :workspace_id, :user_id, false)
        ON CONFLICT (notification_id, user_id) DO UPDATE SET notification_id = EXCLUDED.notification_id
        RETURNING notification_id, workspace_id, user_id, read_at, dismissed_at, snoozed_until,
                  email_reminders
        """,
        notification_id=notification_id,
        workspace_id=workspace_id,
        user_id=user_id,
    )


async def mark_state_read(session: AsyncSession, *, notification_id: uuid.UUID, user_id: uuid.UUID, at: datetime) -> None:
    await run(
        session,
        """
        UPDATE notification_user_states SET read_at = :at
         WHERE notification_id = :notification_id AND user_id = :user_id
        """,
        notification_id=notification_id,
        user_id=user_id,
        at=at,
    )


async def mark_state_dismissed(
    session: AsyncSession, *, notification_id: uuid.UUID, user_id: uuid.UUID, at: datetime
) -> None:
    await run(
        session,
        """
        UPDATE notification_user_states SET dismissed_at = :at
         WHERE notification_id = :notification_id AND user_id = :user_id
        """,
        notification_id=notification_id,
        user_id=user_id,
        at=at,
    )


async def mark_state_snoozed(
    session: AsyncSession, *, notification_id: uuid.UUID, user_id: uuid.UUID, until: date
) -> None:
    await run(
        session,
        """
        UPDATE notification_user_states SET snoozed_until = :until
         WHERE notification_id = :notification_id AND user_id = :user_id
        """,
        notification_id=notification_id,
        user_id=user_id,
        until=until,
    )


async def resolve_other_notifications_for_obligation(
    session: AsyncSession, *, obligation_id: uuid.UUID, current_id: uuid.UUID | None, resolved_at: datetime
) -> None:
    """Keep only the current reminder visible while retaining earlier stages for audit."""
    if current_id is None:
        await run(
            session,
            """UPDATE notifications SET resolved_at = :resolved_at
                 WHERE obligation_id = :obligation_id AND resolved_at IS NULL""",
            obligation_id=obligation_id,
            resolved_at=resolved_at,
        )
    else:
        await run(
            session,
            """UPDATE notifications SET resolved_at = :resolved_at
                 WHERE obligation_id = :obligation_id AND resolved_at IS NULL AND id <> :current_id""",
            obligation_id=obligation_id,
            current_id=current_id,
            resolved_at=resolved_at,
        )
    if current_id is not None:
        await run(
            session,
            "UPDATE notifications SET resolved_at = NULL WHERE id = :current_id",
            current_id=current_id,
        )


async def resolve_notifications_for_obligation(
    session: AsyncSession, *, obligation_id: uuid.UUID, resolved_at: datetime | None
) -> int:
    return await run(
        session,
        "UPDATE notifications SET resolved_at = :resolved_at WHERE obligation_id = :obligation_id",
        obligation_id=obligation_id,
        resolved_at=resolved_at,
    )


async def delete_obligation(session: AsyncSession, *, obligation_id: uuid.UUID) -> int:
    """Remove a derived obligation whose subject no longer exists."""
    return await run(session, "DELETE FROM obligations WHERE id = :id", id=obligation_id)


async def delete_notifications_for_obligation(
    session: AsyncSession, *, obligation_id: uuid.UUID
) -> int:
    return await run(
        session,
        "DELETE FROM notifications WHERE obligation_id = :obligation_id",
        obligation_id=obligation_id,
    )


async def drop_pending_deliveries(
    session: AsyncSession, *, workspace_id: uuid.UUID, user_id: uuid.UUID
) -> int:
    return await run(
        session,
        """
        DELETE FROM notification_deliveries
         WHERE workspace_id = :workspace_id AND recipient_user_id = :user_id
           AND status = 'pending'
        """,
        workspace_id=workspace_id,
        user_id=user_id,
    )


async def set_email_reminders(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    enabled: bool,
    notification_id: uuid.UUID | None,
) -> int:
    clause = where(
        "workspace_id = :workspace_id",
        "user_id = :user_id",
        "notification_id = :notification_id" if notification_id else None,
    )
    return await run(
        session,
        f"UPDATE notification_user_states SET email_reminders = :enabled {clause}",
        workspace_id=workspace_id,
        user_id=user_id,
        notification_id=notification_id,
        enabled=enabled,
    )


async def obligation_ids_for_workspace(session: AsyncSession, workspace_id: uuid.UUID) -> list[uuid.UUID]:
    return await col(
        session, "SELECT id FROM obligations WHERE workspace_id = :workspace_id", workspace_id=workspace_id
    )


async def rule_pack_metadata(session: AsyncSession, code: str) -> Row | None:
    return await one(
        session,
        """
        SELECT id, code, version, income_year, status, professionally_reviewed, parameters
          FROM rule_packs WHERE code = :code
        """,
        code=code,
    )


__all__ = ["Row", "all_rows", "to_jsonb", "val"]


async def obligation_by_recurrence_key(
    session: AsyncSession, *, workspace_id: uuid.UUID, recurrence_key: str
) -> Row | None:
    return await one(
        session,
        f"""
        SELECT {OBLIGATION_COLUMNS} FROM obligations
         WHERE workspace_id = :workspace_id AND recurrence_key = :recurrence_key
        """,
        workspace_id=workspace_id,
        recurrence_key=recurrence_key,
    )


async def retire_obligation(
    session: AsyncSession, *, workspace_id: uuid.UUID, obligation_id: uuid.UUID, reason: str
) -> int:
    """Mark an obligation as no longer applicable, keeping the row for the audit trail.

    Used when the triggering evidence disappears (for example an e-Factura obligation
    whose invoice was deleted): ``not_required`` removes it from the actionable list
    and from the dashboard without deleting the history.
    """
    return await run(
        session,
        """
        UPDATE obligations
           SET applicability = 'not_required',
               applicability_reason = :reason,
               updated_at = now()
         WHERE id = :id AND workspace_id = :workspace_id
        """,
        id=obligation_id,
        workspace_id=workspace_id,
        reason=reason,
    )


async def count_declaration_corrections(session: AsyncSession, *, declaration_id: uuid.UUID) -> int:
    """Corrections that point at this declaration (they must be removed first)."""
    return int(
        await val(
            session,
            "SELECT count(*) FROM declarations WHERE correction_of_declaration_id = :id",
            default=0,
            id=declaration_id,
        )
    )


async def delete_declaration_dependents(
    session: AsyncSession, *, workspace_id: uuid.UUID, declaration_id: uuid.UUID
) -> None:
    """Clean up what cannot survive the declaration, and keep the append-only history.

    ``declaration_versions``, ``calculation_runs`` and ``receipts`` are append-only by
    schema (a DELETE trigger rejects them) and are the audit of what was calculated and
    filed, so they are deliberately left in place.  Source grants only authorized this
    declaration, and obligations keep their own history: they just stop pointing at a
    declaration that no longer exists.
    """
    await run(
        session,
        "DELETE FROM declaration_source_grants "
        "WHERE workspace_id = :workspace_id AND declaration_id = :id",
        workspace_id=workspace_id,
        id=declaration_id,
    )
    await run(
        session,
        "UPDATE obligations SET declaration_id = NULL, updated_at = now() WHERE declaration_id = :id",
        id=declaration_id,
    )
    await run(
        session,
        "UPDATE declarations SET correction_of_declaration_id = NULL "
        "WHERE correction_of_declaration_id = :id",
        id=declaration_id,
    )


async def delete_declaration(
    session: AsyncSession, *, workspace_id: uuid.UUID, declaration_id: uuid.UUID
) -> int:
    return await run(
        session,
        "DELETE FROM declarations WHERE id = :id AND workspace_id = :workspace_id",
        id=declaration_id,
        workspace_id=workspace_id,
    )
