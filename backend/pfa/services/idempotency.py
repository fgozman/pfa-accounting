"""Idempotency keys for finalization, generation and submission mutations.

A key is bound to user + workspace + action + request hash.  A retry with the
same content returns the original result; different content under the same key is
rejected.  Concurrent duplicates see a conflict while the first is in flight.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pfa import clock
from pfa.errors import Conflict, IdempotencyMismatch, ValidationFailed

IDEMPOTENCY_TTL = timedelta(hours=24)


def canonical_request_hash(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class IdempotencyOutcome:
    response: dict | None
    status_code: int | None
    replayed: bool


async def _claim(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID | None,
    user_id: uuid.UUID,
    action: str,
    key: str,
    request_sha256: str,
) -> uuid.UUID | None:
    row = (
        await session.execute(
            text(
                """
                INSERT INTO idempotency_keys
                    (workspace_id, user_id, action, key, request_sha256, expires_at)
                VALUES (:workspace_id, :user_id, :action, :key, :sha, :expires)
                ON CONFLICT (workspace_id, user_id, action, key) DO NOTHING
                RETURNING id
                """
            ),
            {
                "workspace_id": workspace_id,
                "user_id": user_id,
                "action": action,
                "key": key,
                "sha": request_sha256,
                "expires": clock.now() + IDEMPOTENCY_TTL,
            },
        )
    ).scalar_one_or_none()
    return row


async def execute_idempotent(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID | None,
    user_id: uuid.UUID,
    action: str,
    key: str,
    request_payload: Any,
    handler: Callable[[], Awaitable[tuple[dict, int, uuid.UUID | None, str | None]]],
) -> IdempotencyOutcome:
    """Run ``handler`` at most once per (workspace, user, action, key).

    ``handler`` returns ``(response_body, status_code, resource_id, resource_type)``.
    """
    if not key or len(key) > 120:
        raise ValidationFailed(
            "Cheia de idempotency lipsește sau este prea lungă.",
            field_errors={"Idempotency-Key": "required_max_120"},
        )
    request_sha256 = canonical_request_hash(request_payload)
    claimed = await _claim(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        action=action,
        key=key,
        request_sha256=request_sha256,
    )
    if claimed is None:
        existing = (
            await session.execute(
                text(
                    """
                    SELECT request_sha256, response, status_code
                    FROM idempotency_keys
                    WHERE workspace_id IS NOT DISTINCT FROM :workspace_id
                      AND user_id = :user_id AND action = :action AND key = :key
                    """
                ),
                {
                    "workspace_id": workspace_id,
                    "user_id": user_id,
                    "action": action,
                    "key": key,
                },
            )
        ).mappings().first()
        if existing is None:  # pragma: no cover - race with expiry
            raise Conflict("Cheia de idempotency nu a putut fi rezolvată; reîncercați.")
        if existing["request_sha256"] != request_sha256:
            raise IdempotencyMismatch(
                "Aceeași cheie de idempotency a fost folosită cu un conținut diferit."
            )
        if existing["response"] is None:
            raise Conflict("Operațiunea cu această cheie este deja în curs.")
        return IdempotencyOutcome(
            response=existing["response"],
            status_code=existing["status_code"],
            replayed=True,
        )

    response, status_code, resource_id, resource_type = await handler()
    await session.execute(
        text(
            """
            UPDATE idempotency_keys
               SET response = CAST(:response AS jsonb),
                   status_code = :status_code,
                   resource_id = :resource_id,
                   resource_type = :resource_type
             WHERE id = :id
            """
        ),
        {
            "response": json.dumps(response, default=str),
            "status_code": status_code,
            "resource_id": resource_id,
            "resource_type": resource_type,
            "id": claimed,
        },
    )
    return IdempotencyOutcome(response=response, status_code=status_code, replayed=False)
