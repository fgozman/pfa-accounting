"""Register and settlement SQL.

These are the queries that used to run once per row.  They are grouped
aggregates now: the number of statements does not depend on how many documents or
assets exist.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from pfa.repo.query import Row, all_rows

#: ``payment_allocations`` reduce the settled amount for credit notes and refunds
_SETTLED = """
        SELECT document_id,
               coalesce(sum(CASE WHEN allocation_kind IN ('credit', 'refund')
                                 THEN -amount ELSE amount END), 0) AS settled
          FROM payment_allocations
         WHERE workspace_id = :workspace_id
         GROUP BY document_id
"""


async def outstanding_documents(
    session: AsyncSession, *, workspace_id: uuid.UUID, kind: str
) -> list[Row]:
    """Documents of one kind that still have an outstanding balance.

    One statement for the whole register: the settled totals are aggregated per
    document and joined, instead of a per-document round trip.
    """
    return await all_rows(
        session,
        f"""
        SELECT document_id, series, number, issue_date, due_date, total_amount, settled, outstanding
          FROM (
                SELECT d.id AS document_id,
                       d.series,
                       d.number,
                       d.issue_date,
                       d.due_date,
                       d.total_amount,
                       coalesce(a.settled, 0) AS settled,
                       d.total_amount - coalesce(a.settled, 0) AS outstanding
                  FROM documents d
                  LEFT JOIN ({_SETTLED}) a ON a.document_id = d.id
                 WHERE d.workspace_id = :workspace_id
                   AND d.kind = :kind
                   AND d.status = 'finalized'
               ) balances
         WHERE outstanding > 0
         ORDER BY issue_date, number
        """,
        workspace_id=workspace_id,
        kind=kind,
    )


async def asset_register(session: AsyncSession, *, workspace_id: uuid.UUID) -> list[Row]:
    """Assets with their depreciation total and book value, in one statement."""
    return await all_rows(
        session,
        """
        SELECT asset_id,
               name,
               category,
               in_service_date,
               acquisition_cost_ron,
               business_use_percent,
               depreciation_method,
               useful_life_months,
               status,
               depreciated,
               acquisition_cost_ron - depreciated AS book_value
          FROM (
                SELECT a.id AS asset_id,
                       a.name,
                       a.category,
                       a.in_service_date,
                       a.acquisition_cost_ron,
                       a.business_use_percent,
                       a.depreciation_method,
                       a.useful_life_months,
                       a.status,
                       coalesce(d.depreciated, 0) AS depreciated
                  FROM assets a
                  LEFT JOIN (
                        SELECT asset_id, sum(amount) AS depreciated
                          FROM depreciation_entries
                         WHERE workspace_id = :workspace_id
                         GROUP BY asset_id
                  ) d ON d.asset_id = a.id
                 WHERE a.workspace_id = :workspace_id
                   AND a.status <> 'draft'
               ) register
         ORDER BY in_service_date, name
        """,
        workspace_id=workspace_id,
    )


async def cash_journal(
    session: AsyncSession, *, workspace_id: uuid.UUID, period_start: date, period_end: date
) -> list[Row]:
    """Cash movements with a running balance computed by the database.

    The balance is a window function, so the ordering and the arithmetic are part
    of one statement instead of being accumulated in Python.
    """
    return await all_rows(
        session,
        """
        SELECT paid_on,
               kind,
               reference,
               amount_ron,
               direction,
               is_owner_movement,
               sum(
                   CASE WHEN is_owner_movement THEN 0
                        WHEN direction = 'inflow' THEN amount_ron
                        ELSE -amount_ron
                   END
               ) OVER (ORDER BY paid_on, created_at, id
                       ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS balance
          FROM payments
         WHERE workspace_id = :workspace_id
           AND paid_on >= :period_start
           AND paid_on <= :period_end
         ORDER BY paid_on, created_at, id
        """,
        workspace_id=workspace_id,
        period_start=period_start,
        period_end=period_end,
    )


async def close_checklist(
    session: AsyncSession, *, workspace_id: uuid.UUID, period_start: date, period_end: date
) -> dict[str, list[str]]:
    """The checks run before a period may be closed.

    Five grouped statements, one per category.  The previous implementation issued
    one attachment lookup per purchase document, so the cost grew with the number
    of documents in the period.
    """
    drafts = await all_rows(
        session,
        """
        SELECT id FROM documents
         WHERE workspace_id = :workspace_id
           AND issue_date >= :period_start AND issue_date <= :period_end
           AND status = 'draft'
         ORDER BY issue_date, number
        """,
        workspace_id=workspace_id,
        period_start=period_start,
        period_end=period_end,
    )
    unclassified = await all_rows(
        session,
        """
        SELECT id FROM documents
         WHERE workspace_id = :workspace_id
           AND issue_date >= :period_start AND issue_date <= :period_end
           AND kind = 'purchase_invoice'
           AND deductibility = 'undetermined'
         ORDER BY issue_date, number
        """,
        workspace_id=workspace_id,
        period_start=period_start,
        period_end=period_end,
    )
    # NOT EXISTS instead of a lookup per document
    missing_evidence = await all_rows(
        session,
        """
        SELECT d.id
          FROM documents d
         WHERE d.workspace_id = :workspace_id
           AND d.issue_date >= :period_start AND d.issue_date <= :period_end
           AND d.kind = 'purchase_invoice'
           AND NOT EXISTS (
                 SELECT 1 FROM attachments a
                  WHERE a.workspace_id = d.workspace_id AND a.document_id = d.id
               )
         ORDER BY d.issue_date, d.number
        """,
        workspace_id=workspace_id,
        period_start=period_start,
        period_end=period_end,
    )
    unreconciled = await all_rows(
        session,
        """
        SELECT id FROM payments
         WHERE workspace_id = :workspace_id
           AND paid_on >= :period_start AND paid_on <= :period_end
           AND reference IS NULL
         ORDER BY paid_on
        """,
        workspace_id=workspace_id,
        period_start=period_start,
        period_end=period_end,
    )
    assets_without_schedule = await all_rows(
        session,
        """
        SELECT a.id
          FROM assets a
         WHERE a.workspace_id = :workspace_id
           AND a.status = 'in_service'
           AND a.depreciation_method <> 'no_depreciation'
           AND NOT EXISTS (
                 SELECT 1 FROM depreciation_entries e
                  WHERE e.workspace_id = a.workspace_id AND e.asset_id = a.id
               )
         ORDER BY a.in_service_date, a.name
        """,
        workspace_id=workspace_id,
    )
    return {
        "draft_documents": [str(row.id) for row in drafts],
        "unclassified_documents": [str(row.id) for row in unclassified],
        "missing_evidence": [str(row.id) for row in missing_evidence],
        "unreconciled_payments": [str(row.id) for row in unreconciled],
        "assets_without_schedule": [str(row.id) for row in assets_without_schedule],
    }


async def members_with_accounts(session: AsyncSession, *, workspace_id: uuid.UUID) -> list[Row]:
    """Members joined to their account in one statement (was one query per member)."""
    return await all_rows(
        session,
        """
        SELECT m.user_id,
               m.role,
               m.status,
               m.joined_at,
               u.email_display AS email
          FROM memberships m
          JOIN users u ON u.id = m.user_id
         WHERE m.workspace_id = :workspace_id
         ORDER BY m.joined_at
        """,
        workspace_id=workspace_id,
    )
