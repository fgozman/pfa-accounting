"""Workspace dashboard figures.

Issued revenue, collected revenue and open obligations are kept apart: an
estimate is never presented as an ANAF balance.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pfa.fiscal.money import round_cents
from pfa.models.base import DocumentKind, DocumentStatus
from pfa.models.books import AccountingEvent
from pfa.models.documents import Document
from pfa.services import obligations as obligations_service


@dataclass(frozen=True, slots=True)
class DashboardSummary:
    income_year: int
    #: finalized sales invoices issued in the year
    issued_revenue: Decimal
    #: revenue recognised on collection (realization) in the year
    collected_revenue: Decimal
    documents_count: int
    #: actionable obligations still open
    open_obligations: list


async def dashboard_summary(
    session: AsyncSession, *, workspace_id: uuid.UUID, income_year: int
) -> DashboardSummary:
    year_start = date(income_year, 1, 1)
    year_end = date(income_year, 12, 31)

    issued = (
        (
            await session.execute(
                select(Document).where(
                    Document.workspace_id == workspace_id,
                    Document.kind == DocumentKind.SALES_INVOICE,
                    Document.status == DocumentStatus.FINALIZED,
                    Document.issue_date >= year_start,
                    Document.issue_date <= year_end,
                )
            )
        )
        .scalars()
        .all()
    )
    collected = (
        (
            await session.execute(
                select(AccountingEvent).where(
                    AccountingEvent.workspace_id == workspace_id,
                    AccountingEvent.event_kind == "revenue",
                    AccountingEvent.recognition == "realization",
                    AccountingEvent.event_date >= year_start,
                    AccountingEvent.event_date <= year_end,
                )
            )
        )
        .scalars()
        .all()
    )
    obligations = await obligations_service.list_obligations(
        session, workspace_id=workspace_id, income_year=income_year, only_actionable=True
    )
    return DashboardSummary(
        income_year=income_year,
        issued_revenue=round_cents(sum((item.total_amount for item in issued), Decimal("0"))),
        collected_revenue=round_cents(
            sum((item.taxable_amount_ron for item in collected), Decimal("0"))
        ),
        documents_count=len(issued),
        open_obligations=[row for row in obligations if str(row.resolution_status) == "open"],
    )
