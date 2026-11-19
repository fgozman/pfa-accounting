"""ANAF SQL: connections, capabilities, submission attempts, receipts."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from pfa.repo.query import Row, all_rows, one, returning, where
from pfa.repo.query import jsonb as to_jsonb

ATTEMPT_COLUMNS = """
    id, workspace_id, declaration_id, declaration_version, connection_id, channel, status,
    attempt_number, idempotency_key, request_sha256, request_object_key, remote_reference,
    error_code, error_message, error_class, started_at, finished_at, next_retry_at, raw_response
"""


async def get_submission_attempt(session: AsyncSession, attempt_id: uuid.UUID) -> Row | None:
    return await one(
        session, f"SELECT {ATTEMPT_COLUMNS} FROM submission_attempts WHERE id = :id", id=attempt_id
    )


async def insert_submission_attempt(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    declaration_id: uuid.UUID,
    declaration_version: int,
    connection_id: uuid.UUID | None,
    channel: str,
    status: str,
    attempt_number: int,
    idempotency_key: str,
    request_sha256: str,
    request_object_key: str | None,
) -> Row:
    return await returning(
        session,
        f"""
        INSERT INTO submission_attempts (
            workspace_id, declaration_id, declaration_version, connection_id, channel, status,
            attempt_number, idempotency_key, request_sha256, request_object_key
        )
        VALUES (
            :workspace_id, :declaration_id, :declaration_version, :connection_id, :channel,
            :status, :attempt_number, :idempotency_key, :request_sha256, :request_object_key
        )
        RETURNING {ATTEMPT_COLUMNS}
        """,
        workspace_id=workspace_id,
        declaration_id=declaration_id,
        declaration_version=declaration_version,
        connection_id=connection_id,
        channel=channel,
        status=status,
        attempt_number=attempt_number,
        idempotency_key=idempotency_key,
        request_sha256=request_sha256,
        request_object_key=request_object_key,
    )


async def update_submission_attempt(
    session: AsyncSession,
    *,
    attempt_id: uuid.UUID,
    status: str,
    remote_reference: str | None,
    error_class: str | None,
    error_code: str | None,
    error_message: str | None,
    raw_response: dict | None,
) -> Row:
    return await returning(
        session,
        f"""
        UPDATE submission_attempts
           SET status = :status, remote_reference = :remote_reference, error_class = :error_class,
               error_code = :error_code, error_message = :error_message,
               raw_response = CAST(:raw_response AS jsonb), finished_at = now()
         WHERE id = :id
        RETURNING {ATTEMPT_COLUMNS}
        """,
        id=attempt_id,
        status=status,
        remote_reference=remote_reference,
        error_class=error_class,
        error_code=error_code,
        error_message=error_message,
        raw_response=to_jsonb(raw_response),
    )


async def get_connection(session: AsyncSession, connection_id: uuid.UUID) -> Row | None:
    return await one(
        session,
        """
        SELECT id, workspace_id, environment, status, connected_at, expires_at, last_error,
               pending_state_hash, authorized_cif, authorized_identity, scopes, allowed_taxpayers
          FROM anaf_connections WHERE id = :id
        """,
        id=connection_id,
    )


async def connection_workspace(session: AsyncSession, connection_id: uuid.UUID) -> uuid.UUID | None:
    value = await one(
        session, "SELECT app.anaf_connection_workspace(:id) AS workspace_id", id=connection_id
    )
    return value.workspace_id if value else None


async def insert_connection(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    environment: str,
    connector_client_ref: str | None,
    pending_state_hash: str,
    created_by_user_id: uuid.UUID,
) -> Row:
    return await returning(
        session,
        """
        INSERT INTO anaf_connections (
            workspace_id, environment, connector_client_ref, status, pending_state_hash,
            created_by_user_id
        )
        VALUES (:workspace_id, :environment, :connector_client_ref, 'pending', :pending_state_hash,
                :created_by_user_id)
        RETURNING id, workspace_id, environment, status, pending_state_hash
        """,
        workspace_id=workspace_id,
        environment=environment,
        connector_client_ref=connector_client_ref,
        pending_state_hash=pending_state_hash,
        created_by_user_id=created_by_user_id,
    )


async def list_connections(session: AsyncSession, workspace_id: uuid.UUID) -> list[Row]:
    return await all_rows(
        session,
        """
        SELECT id, environment, status, connected_at, expires_at, last_error
          FROM anaf_connections
         WHERE workspace_id = :workspace_id
         ORDER BY created_at DESC
        """,
        workspace_id=workspace_id,
    )


async def activate_connection(
    session: AsyncSession, *, connection_id: uuid.UUID, state_hash: str
) -> Row | None:
    """Consume the one-use state binding and mark the connection active."""
    return await one(
        session,
        """
        UPDATE anaf_connections
           SET status = 'active', connected_at = now(), pending_state_hash = NULL,
               last_error = NULL
         WHERE id = :id AND pending_state_hash = :state_hash
        RETURNING id, workspace_id, status, connected_at
        """,
        id=connection_id,
        state_hash=state_hash,
    )


async def fail_connection(session: AsyncSession, *, connection_id: uuid.UUID, error: str) -> int:
    from pfa.repo.query import run

    return await run(
        session,
        """
        UPDATE anaf_connections
           SET status = 'error', last_error = :error, pending_state_hash = NULL
         WHERE id = :id
        """,
        id=connection_id,
        error=error,
    )


async def enabled_capabilities(
    session: AsyncSession, *, form_code: str | None = None, environment: str | None = None
) -> list[Row]:
    clause = where(
        "enabled = true",
        "form_code = :form_code" if form_code else None,
        "environment = :environment" if environment else None,
    )
    return await all_rows(
        session,
        f"""
        SELECT id, form_code, environment, channel, endpoint_url, signing_requirement,
               schema_version, validator_version, receipt_behavior, delegation_supported,
               test_environment_available, verified_at
          FROM connector_capabilities
        {clause}
         ORDER BY form_code
        """,
        form_code=form_code,
        environment=environment,
    )
