"""Document SQL: counterparties, invoices, lines, revisions, attachments.

Issued documents are immutable while they exist (corrections use a linked credit note),
their series/number is a fact entered by the user and unique per workspace, and deleting
a document removes it: the append-only audit event is the record that it existed.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from pfa.repo.query import Row, all_rows, one, returning, run, uuid_list, val, where
from pfa.repo.query import jsonb as to_jsonb

DOCUMENT_COLUMNS = """
    id, workspace_id, kind, status, source, counterparty_id, supplier_name_raw, series, number,
    issue_date, supply_date, due_date, currency, total_amount, total_vat,
    total_amount_ron, exchange_rate_id, vat_treatment, deductibility,
    business_use_percent, business_purpose, deductibility_justification,
    reverse_charge_justification, expense_category, intracommunity_operation_type, is_tax_ready, revision,
    external_id, content_hash, corrects_document_id, correction_reason, created_by_user_id,
    supplier_country, supplier_vat_id, partner_kind
"""

LINE_COLUMNS = """
    id, document_id, line_number, description, quantity, unit_price, discount_amount,
    vat_rate, vat_treatment, net_amount, vat_amount, gross_amount, business_use_percent,
    deductibility_percent, expense_category
"""


async def get_document(
    session: AsyncSession, *, workspace_id: uuid.UUID, document_id: uuid.UUID, for_update: bool = False
) -> Row | None:
    lock = " FOR UPDATE" if for_update else ""
    return await one(
        session,
        f"""
        SELECT {DOCUMENT_COLUMNS} FROM documents
         WHERE id = :document_id AND workspace_id = :workspace_id{lock}
        """,
        document_id=document_id,
        workspace_id=workspace_id,
    )


async def list_lines(session: AsyncSession, document_id: uuid.UUID) -> list[Row]:
    return await all_rows(
        session,
        f"""
        SELECT {LINE_COLUMNS} FROM document_lines
         WHERE document_id = :document_id
         ORDER BY line_number
        """,
        document_id=document_id,
    )


async def find_duplicates(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    kind: str | None,
    external_id: str | None,
    series: str | None,
    number: str | None,
    issue_date: date | None,
    counterparty_id: uuid.UUID | None,
    supplier_name_raw: str | None,
    content_hash: str | None,
) -> list[Row]:
    """Duplicate detection on external identifier, issuer/number/date and content hash.

    Each predicate is a fixed SQL fragment; the values are bound.  A deleted document is
    gone, so it never makes its own replacement look like a duplicate.
    """
    predicates = [
        "external_id IS NOT NULL AND external_id = :external_id" if external_id else None,
        (
            # The same kind of document, from the same partner, with the same series,
            # number and date.  The kind matters: an expense and a sale that happen to
            # share a number, a date and a partner name are different documents, and the
            # expense list cannot even show the sale that would "duplicate" it.
            "kind = :kind AND number = :number AND issue_date = :issue_date "
            "AND series IS NOT DISTINCT FROM :series "
            "AND counterparty_id IS NOT DISTINCT FROM :counterparty_id "
            "AND supplier_name_raw IS NOT DISTINCT FROM :supplier_name_raw"
            if kind and number and issue_date
            else None
        ),
        (
            "content_hash IS NOT NULL AND content_hash = :content_hash"
            if content_hash
            else None
        ),
    ]
    clause = " OR ".join(f"({predicate})" for predicate in predicates if predicate)
    if not clause:
        return []
    return await all_rows(
        session,
        f"""
        SELECT {DOCUMENT_COLUMNS} FROM documents
         WHERE workspace_id = :workspace_id AND ({clause})
        """,
        workspace_id=workspace_id,
        kind=kind,
        external_id=external_id,
        series=series,
        number=number,
        issue_date=issue_date,
        counterparty_id=counterparty_id,
        content_hash=content_hash,
        supplier_name_raw=supplier_name_raw,
    )


async def find_by_external_id(
    session: AsyncSession, *, workspace_id: uuid.UUID, external_id: str
) -> Row | None:
    """The document holding an import key; a deleted one has released it."""
    return await one(
        session,
        f"""
        SELECT {DOCUMENT_COLUMNS} FROM documents
         WHERE workspace_id = :workspace_id AND external_id = :external_id
        """,
        workspace_id=workspace_id,
        external_id=external_id,
    )


async def insert_document(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    values: dict,
) -> Row:
    return await returning(
        session,
        f"""
        INSERT INTO documents (
            workspace_id, kind, status, source, counterparty_id, supplier_name_raw, series,
            number, issue_date, supply_date, due_date, currency, total_amount, total_vat,
            vat_treatment, deductibility, business_use_percent, business_purpose,
            deductibility_justification, reverse_charge_justification, expense_category,
            intracommunity_operation_type, supplier_country, supplier_vat_id, partner_kind,
            is_tax_ready, revision, external_id, content_hash, corrects_document_id,
            correction_reason, created_by_user_id
        )
        VALUES (
            :workspace_id, :kind, :status, :source, :counterparty_id, :supplier_name_raw, :series,
            :number, :issue_date, :supply_date, :due_date, :currency, :total_amount, :total_vat,
            :vat_treatment, :deductibility, :business_use_percent, :business_purpose,
            :deductibility_justification, :reverse_charge_justification, :expense_category,
            :intracommunity_operation_type, :supplier_country, :supplier_vat_id, :partner_kind,
            false, 1, :external_id, :content_hash, :corrects_document_id, :correction_reason,
            :created_by_user_id
        )
        RETURNING {DOCUMENT_COLUMNS}
        """,
        workspace_id=workspace_id,
        **values,
    )


async def insert_line(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    line_number: int,
    description: str,
    quantity: Decimal,
    unit_price: Decimal,
    discount_amount: Decimal,
    vat_rate: Decimal,
    vat_treatment: str,
    net_amount: Decimal,
    vat_amount: Decimal,
    gross_amount: Decimal,
    business_use_percent: Decimal,
    deductibility_percent: Decimal,
    expense_category: str | None,
) -> Row:
    return await returning(
        session,
        f"""
        INSERT INTO document_lines (
            workspace_id, document_id, line_number, description, quantity, unit_price,
            discount_amount, vat_rate, vat_treatment, net_amount, vat_amount, gross_amount,
            business_use_percent, deductibility_percent, expense_category
        )
        VALUES (
            :workspace_id, :document_id, :line_number, :description, :quantity, :unit_price,
            :discount_amount, :vat_rate, :vat_treatment, :net_amount, :vat_amount, :gross_amount,
            :business_use_percent, :deductibility_percent, :expense_category
        )
        RETURNING {LINE_COLUMNS}
        """,
        workspace_id=workspace_id,
        document_id=document_id,
        line_number=line_number,
        description=description,
        quantity=quantity,
        unit_price=unit_price,
        discount_amount=discount_amount,
        vat_rate=vat_rate,
        vat_treatment=vat_treatment,
        net_amount=net_amount,
        vat_amount=vat_amount,
        gross_amount=gross_amount,
        business_use_percent=business_use_percent,
        deductibility_percent=deductibility_percent,
        expense_category=expense_category,
    )


async def delete_lines(session: AsyncSession, document_id: uuid.UUID) -> int:
    return await run(
        session, "DELETE FROM document_lines WHERE document_id = :document_id", document_id=document_id
    )


async def update_document_totals(
    session: AsyncSession,
    *,
    document_id: uuid.UUID,
    total_amount: Decimal,
    total_vat: Decimal,
    revision: int,
) -> Row:
    return await returning(
        session,
        f"""
        UPDATE documents
           SET total_amount = :total_amount, total_vat = :total_vat, revision = :revision,
               updated_at = now()
         WHERE id = :document_id
        RETURNING {DOCUMENT_COLUMNS}
        """,
        document_id=document_id,
        total_amount=total_amount,
        total_vat=total_vat,
        revision=revision,
    )


async def update_document_fields(
    session: AsyncSession, *, document_id: uuid.UUID, fields: dict
) -> Row:
    """Update a fixed set of editable columns (draft edit)."""
    assignments = ", ".join(f"{column} = :{column}" for column in fields)
    return await returning(
        session,
        f"""
        UPDATE documents SET {assignments}, updated_at = now()
         WHERE id = :document_id
        RETURNING {DOCUMENT_COLUMNS}
        """,
        document_id=document_id,
        **fields,
    )


async def finalize(
    session: AsyncSession,
    *,
    document_id: uuid.UUID,
    series: str | None,
    number: str,
) -> Row:
    return await returning(
        session,
        f"""
        UPDATE documents
           SET status = 'finalized', series = :series, number = :number, updated_at = now()
         WHERE id = :document_id AND status = 'draft'
        RETURNING {DOCUMENT_COLUMNS}
        """,
        document_id=document_id,
        series=series,
        number=number,
    )


async def snapshot_revision(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    revision: int,
    reason: str,
    snapshot: dict,
    created_by_user_id: uuid.UUID,
) -> Row:
    return await returning(
        session,
        """
        INSERT INTO document_revisions (
            workspace_id, document_id, revision, reason, snapshot, created_by_user_id
        )
        VALUES (:workspace_id, :document_id, :revision, :reason, CAST(:snapshot AS jsonb),
                :created_by_user_id)
        RETURNING id, document_id, revision, reason
        """,
        workspace_id=workspace_id,
        document_id=document_id,
        revision=revision,
        reason=reason,
        snapshot=to_jsonb(snapshot),
        created_by_user_id=created_by_user_id,
    )


async def allocate_number(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    document_kind: str,
    prefix: str,
    year: int,
) -> int:
    """Deprecated: numbers are entered by the user (see :func:`find_by_series_number`).

    Retained only so previously allocated series can still be inspected; nothing in the
    application allocates a number any more.
    """
    row = await returning(
        session,
        """
        INSERT INTO number_series (workspace_id, document_kind, prefix, year, last_number)
        VALUES (:workspace_id, :document_kind, :prefix, :year, 1)
        ON CONFLICT (workspace_id, document_kind, prefix, year) DO UPDATE
            SET last_number = number_series.last_number + 1, updated_at = now()
        RETURNING last_number
        """,
        workspace_id=workspace_id,
        document_kind=document_kind,
        prefix=prefix,
        year=year,
    )
    return int(row.last_number)


async def find_by_series_number(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    series: str,
    number: str,
    exclude_document_id: uuid.UUID | None = None,
) -> Row | None:
    """The live document holding a series/number, so the user gets a clear conflict."""
    clause = where(
        "workspace_id = :workspace_id",
        # an absent series compares as an empty string, exactly like the unique index
        "COALESCE(series, '') = :series",
        "number = :number",
        "id <> :exclude_document_id" if exclude_document_id else None,
    )
    return await one(
        session,
        f"SELECT {DOCUMENT_COLUMNS} FROM documents {clause}",
        workspace_id=workspace_id,
        series=series,
        number=number,
        exclude_document_id=exclude_document_id,
    )


async def count_credit_notes(session: AsyncSession, *, document_id: uuid.UUID) -> int:
    """Credit notes that correct this document; they keep it from disappearing."""
    return int(
        await val(
            session,
            "SELECT count(*) FROM documents WHERE corrects_document_id = :document_id",
            document_id=document_id,
        )
        or 0
    )


async def delete_document(session: AsyncSession, *, document_id: uuid.UUID) -> int:
    """Remove the document; its lines cascade.  Returns the number of rows deleted."""
    return await run(session, "DELETE FROM documents WHERE id = :document_id", document_id=document_id)


async def delete_revisions(session: AsyncSession, *, document_id: uuid.UUID) -> int:
    return await run(
        session,
        "DELETE FROM document_revisions WHERE document_id = :document_id",
        document_id=document_id,
    )


async def delete_attachments(session: AsyncSession, *, document_id: uuid.UUID) -> list[Row]:
    """Drop the attachment rows, returning them so the stored objects can be removed."""
    return await all_rows(
        session,
        "DELETE FROM attachments WHERE document_id = :document_id RETURNING id, object_key",
        document_id=document_id,
    )


async def delete_allocations(session: AsyncSession, *, document_id: uuid.UUID) -> int:
    """Un-apply settlements.  Payments recorded **for** this document are deleted with it
    (see :func:`delete_orphan_payments`); a payment covering other documents keeps them."""
    return await run(
        session,
        "DELETE FROM payment_allocations WHERE document_id = :document_id",
        document_id=document_id,
    )


async def payments_of_document(session: AsyncSession, *, document_id: uuid.UUID) -> list:
    """The settlements that were allocated to this document."""
    return await all_rows(
        session,
        """
        SELECT DISTINCT p.*
          FROM payments p
          JOIN payment_allocations a ON a.payment_id = p.id
         WHERE a.document_id = :document_id
        """,
        document_id=document_id,
    )


async def delete_orphan_payments(
    session: AsyncSession, *, workspace_id: uuid.UUID, affected_payment_ids: list[uuid.UUID]
) -> list[Row]:
    """Only delete document-linked payments left without allocations by this deletion.

    Independent, unallocated advances/receipts in the same workspace must survive.
    """
    if not affected_payment_ids:
        return []
    return await all_rows(
        session,
        """
        DELETE FROM payments p
         WHERE p.workspace_id = :workspace_id
           AND p.id = ANY(CAST(:affected_payment_ids AS uuid[]))
           AND NOT EXISTS (SELECT 1 FROM payment_allocations a WHERE a.payment_id = p.id)
           AND p.kind IN ('customer_receipt', 'supplier_payment')
        RETURNING p.id
        """,
        workspace_id=workspace_id,
        affected_payment_ids=uuid_list(affected_payment_ids),
    )


async def insert_attachment(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    document_id: uuid.UUID | None,
    evidence_kind: str,
    filename: str,
    content_type: str,
    size_bytes: int,
    sha256: str,
    object_key: str,
    scan_status: str,
    uploaded_by_user_id: uuid.UUID,
) -> Row:
    return await returning(
        session,
        """
        INSERT INTO attachments (
            workspace_id, document_id, evidence_kind, filename, content_type, size_bytes,
            sha256, object_key, scan_status, uploaded_by_user_id
        )
        VALUES (
            :workspace_id, :document_id, :evidence_kind, :filename, :content_type, :size_bytes,
            :sha256, :object_key, :scan_status, :uploaded_by_user_id
        )
        RETURNING id, document_id, filename, content_type, size_bytes, sha256, object_key,
                  scan_status, evidence_kind
        """,
        workspace_id=workspace_id,
        document_id=document_id,
        evidence_kind=evidence_kind,
        filename=filename,
        content_type=content_type,
        size_bytes=size_bytes,
        sha256=sha256,
        object_key=object_key,
        scan_status=scan_status,
        uploaded_by_user_id=uploaded_by_user_id,
    )


async def get_attachment(
    session: AsyncSession, *, workspace_id: uuid.UUID, attachment_id: uuid.UUID
) -> Row | None:
    return await one(
        session,
        """
        SELECT id, document_id, filename, content_type, size_bytes, sha256, object_key,
               scan_status, evidence_kind
          FROM attachments
         WHERE id = :attachment_id AND workspace_id = :workspace_id
        """,
        attachment_id=attachment_id,
        workspace_id=workspace_id,
    )


async def insert_import(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    source: str,
    status: str,
    original_filename: str | None,
    object_key: str | None,
    detected_duplicates: list | None,
    created_by_user_id: uuid.UUID,
) -> Row:
    return await returning(
        session,
        """
        INSERT INTO document_imports (
            workspace_id, source, status, original_filename, object_key, detected_duplicates,
            created_by_user_id
        )
        VALUES (
            :workspace_id, :source, :status, :original_filename, :object_key,
            CAST(:detected_duplicates AS jsonb), :created_by_user_id
        )
        RETURNING id, source, status, original_filename, object_key, detected_duplicates
        """,
        workspace_id=workspace_id,
        source=source,
        status=status,
        original_filename=original_filename,
        object_key=object_key,
        detected_duplicates=to_jsonb(detected_duplicates or []),
        created_by_user_id=created_by_user_id,
    )


async def list_documents(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    kind: str | None,
    status: str | None,
    date_from: date | None,
    date_to: date | None,
    cursor: date | None,
    limit: int,
) -> list[Row]:
    clause = where(
        "workspace_id = :workspace_id",
        "kind = :kind" if kind else None,
        "status = :status" if status else None,
        "issue_date >= :date_from" if date_from else None,
        "issue_date <= :date_to" if date_to else None,
        "issue_date < :cursor" if cursor else None,
    )
    bounded = min(max(int(limit), 1), 200)
    return await all_rows(
        session,
        f"""
        SELECT {DOCUMENT_COLUMNS},
               (
                   SELECT string_agg(line.description, ', ' ORDER BY line.line_number)
                     FROM document_lines line
                    WHERE line.document_id = documents.id
               ) AS description
          FROM documents
        {clause}
         ORDER BY issue_date DESC, id DESC
         LIMIT {bounded}
        """,
        workspace_id=workspace_id,
        kind=kind,
        status=status,
        date_from=date_from,
        date_to=date_to,
        cursor=cursor,
    )


async def settled_amount(
    session: AsyncSession, *, workspace_id: uuid.UUID, document_id: uuid.UUID
) -> Decimal:
    value = await val(
        session,
        """
        SELECT coalesce(sum(CASE WHEN allocation_kind IN ('credit', 'refund')
                                 THEN -amount ELSE amount END), 0)
          FROM payment_allocations
         WHERE workspace_id = :workspace_id AND document_id = :document_id
        """,
        default=Decimal("0"),
        workspace_id=workspace_id,
        document_id=document_id,
    )
    return Decimal(value)


async def document_has_settlement(
    session: AsyncSession, *, workspace_id: uuid.UUID, document_id: uuid.UUID
) -> bool:
    return bool(
        await val(
            session,
            """
            SELECT 1 FROM payment_allocations
             WHERE workspace_id = :workspace_id AND document_id = :document_id
               AND allocation_kind IN ('settlement', 'advance')
             LIMIT 1
            """,
            default=0,
            workspace_id=workspace_id,
            document_id=document_id,
        )
    )


async def period_is_closed(session: AsyncSession, *, workspace_id: uuid.UUID, day: date) -> Row | None:
    """The closed period covering ``day``, if any."""
    return await one(
        session,
        """
        SELECT id, period_start, period_end FROM period_locks
         WHERE workspace_id = :workspace_id
           AND is_open = false
           AND period_start <= :day AND period_end >= :day
         LIMIT 1
        """,
        workspace_id=workspace_id,
        day=day,
    )


async def finalized_documents_between(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    kind: str,
    date_from: date,
    date_to: date,
) -> list[Row]:
    return await all_rows(
        session,
        f"""
        SELECT {DOCUMENT_COLUMNS} FROM documents
         WHERE workspace_id = :workspace_id
           AND kind = :kind
           AND status = 'finalized'
           AND issue_date >= :date_from AND issue_date <= :date_to
         ORDER BY issue_date, number
        """,
        workspace_id=workspace_id,
        kind=kind,
        date_from=date_from,
        date_to=date_to,
    )


async def documents_in_period(
    session: AsyncSession, *, workspace_id: uuid.UUID, date_from: date, date_to: date
) -> list[Row]:
    return await all_rows(
        session,
        f"""
        SELECT {DOCUMENT_COLUMNS} FROM documents
         WHERE workspace_id = :workspace_id
           AND issue_date >= :date_from AND issue_date <= :date_to
         ORDER BY issue_date, number
        """,
        workspace_id=workspace_id,
        date_from=date_from,
        date_to=date_to,
    )


__all__ = ["Row", "all_rows", "datetime", "run"]


def _alias_note_columns(columns: str) -> str:
    """Qualify every selected column, because both joined tables share their names."""
    return ", ".join(f"note.{column.strip()} AS {column.strip()}" for column in columns.split(","))


async def finalized_credit_notes_between(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    corrected_kind: str,
    date_from: date,
    date_to: date,
) -> list[Row]:
    """Credit notes in the period that correct a document of the given kind.

    A storno reduces the VAT of the operation it corrects, so the register and the return
    need it alongside the document it belongs to.
    """
    return await all_rows(
        session,
        f"""
        SELECT {_alias_note_columns(DOCUMENT_COLUMNS)} FROM documents note
          JOIN documents original ON original.id = note.corrects_document_id
         WHERE note.workspace_id = :workspace_id
           AND note.kind = 'credit_note'
           AND note.status = 'finalized'
           AND note.issue_date >= :date_from AND note.issue_date <= :date_to
           AND original.kind = :corrected_kind
         ORDER BY note.issue_date, note.number, note.id
        """,
        workspace_id=workspace_id,
        corrected_kind=corrected_kind,
        date_from=date_from,
        date_to=date_to,
    )
