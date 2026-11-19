"""OAuth SQL: every statement against the authorization server's ``oauth_`` tables.

Clients, redirect/origin registrations, authorization transactions, authorization
codes, login sessions and refresh tokens.  No other repository module reads or
writes these tables, and no statement here touches a table outside the OAuth
boundary (in particular, never ``users``), so the set can move to its own
service and database unchanged.

Two rules from the specification are enforced by these statements rather than by
application code where possible:

* a redirect URI is compared **exactly** (the registration table is the only source
  of truth, and the token exchange compares the stored value);
* an authorization code and a refresh token are single use, so the consume
  statements are written as conditional ``UPDATE ... WHERE used_at IS NULL`` and
  the caller checks the row count.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from pfa.repo.query import Row, all_rows, jsonb, one, returning, run, val

CLIENT_COLUMNS = """
    c.id, c.client_id, c.client_type, c.token_endpoint_auth_method, c.environment,
    c.enabled, c.require_pkce, c.grant_types, c.response_types, c.allowed_scopes,
    c.allowed_pkce_methods
"""

TRANSACTION_COLUMNS = """
    id, client_id, redirect_uri, scope, state, code_challenge, code_challenge_method,
    proof_hash, origin_hash, expires_at, consumed_at, authenticated_user_id,
    authenticated_at, attempt_count, created_at
"""

CODE_COLUMNS = """
    id, code_hash, user_id, client_id, transaction_id, redirect_uri, code_challenge,
    code_challenge_method, scope, expires_at, used_at, session_id, created_at
"""

SESSION_COLUMNS = (
    "id, user_id, client_id, scope, created_at, absolute_expires_at, revoked_at, revoke_reason"
)

REFRESH_COLUMNS = (
    "id, token_hash, jti, user_id, session_id, parent_id, issued_at, expires_at, used_at, revoked_at"
)


# ---------------------------------------------------------------------------
# Client registration
# ---------------------------------------------------------------------------


async def get_client(
    session: AsyncSession, *, client_id: str, environment: str
) -> Row | None:
    """One statement for the client plus its registered redirect URIs and origins.

    The previous implementation loaded the relationships eagerly; aggregating them
    here keeps it to a single round trip without an ORM identity map.
    """
    return await one(
        session,
        f"""
        SELECT {CLIENT_COLUMNS},
               coalesce((SELECT array_agg(r.exact_uri ORDER BY r.exact_uri)
                           FROM oauth_redirect_uris r WHERE r.client_id = c.id), '{{}}') AS redirect_uris,
               coalesce((SELECT array_agg(o.origin ORDER BY o.origin)
                           FROM oauth_origins o WHERE o.client_id = c.id), '{{}}') AS origins
          FROM oauth_clients c
         WHERE c.client_id = :client_id AND c.environment = :environment
        """,
        client_id=client_id,
        environment=environment,
    )


async def upsert_client(
    session: AsyncSession,
    *,
    client_id: str,
    client_type: str,
    token_endpoint_auth_method: str,
    environment: str,
    enabled: bool,
    require_pkce: bool,
    grant_types: list[str],
    response_types: list[str],
    allowed_scopes: list[str],
    allowed_pkce_methods: list[str],
) -> Row:
    return await returning(
        session,
        """
        INSERT INTO oauth_clients (
            client_id, client_type, token_endpoint_auth_method, environment, enabled,
            require_pkce, grant_types, response_types, allowed_scopes, allowed_pkce_methods
        )
        VALUES (
            :client_id, :client_type, :token_endpoint_auth_method, :environment, :enabled,
            :require_pkce, CAST(:grant_types AS jsonb), CAST(:response_types AS jsonb),
            CAST(:allowed_scopes AS jsonb), CAST(:allowed_pkce_methods AS jsonb)
        )
        ON CONFLICT (client_id, environment) DO UPDATE
            SET enabled = EXCLUDED.enabled, updated_at = now()
        RETURNING id, client_id, environment, enabled
        """,
        client_id=client_id,
        client_type=client_type,
        token_endpoint_auth_method=token_endpoint_auth_method,
        environment=environment,
        enabled=enabled,
        require_pkce=require_pkce,
        grant_types=jsonb(grant_types),
        response_types=jsonb(response_types),
        allowed_scopes=jsonb(allowed_scopes),
        allowed_pkce_methods=jsonb(allowed_pkce_methods),
    )


async def register_redirect_uri(
    session: AsyncSession, *, client_pk: uuid.UUID, exact_uri: str
) -> None:
    await run(
        session,
        """
        INSERT INTO oauth_redirect_uris (client_id, exact_uri)
        VALUES (:client_pk, :exact_uri)
        ON CONFLICT (client_id, exact_uri) DO NOTHING
        """,
        client_pk=client_pk,
        exact_uri=exact_uri,
    )


async def register_origin(session: AsyncSession, *, client_pk: uuid.UUID, origin: str) -> None:
    await run(
        session,
        """
        INSERT INTO oauth_origins (client_id, origin)
        VALUES (:client_pk, :origin)
        ON CONFLICT (client_id, origin) DO NOTHING
        """,
        client_pk=client_pk,
        origin=origin,
    )


async def client_redirect_uris(session: AsyncSession, client_pk: uuid.UUID) -> list[str]:
    return await all_rows(
        session,
        "SELECT exact_uri FROM oauth_redirect_uris WHERE client_id = :client_pk",
        client_pk=client_pk,
    )


# ---------------------------------------------------------------------------
# Authorization transactions
# ---------------------------------------------------------------------------


async def insert_transaction(
    session: AsyncSession,
    *,
    client_pk: uuid.UUID,
    redirect_uri: str,
    scope: str,
    state: str,
    code_challenge: str,
    code_challenge_method: str,
    proof_hash: str,
    origin_hash: str | None,
    expires_at: datetime,
) -> Row:
    return await returning(
        session,
        f"""
        INSERT INTO oauth_authorization_transactions (
            client_id, redirect_uri, scope, state, code_challenge, code_challenge_method,
            proof_hash, origin_hash, expires_at, attempt_count
        )
        VALUES (
            :client_pk, :redirect_uri, :scope, :state, :code_challenge, :code_challenge_method,
            :proof_hash, :origin_hash, :expires_at, 0
        )
        RETURNING {TRANSACTION_COLUMNS}
        """,
        client_pk=client_pk,
        redirect_uri=redirect_uri,
        scope=scope,
        state=state,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        proof_hash=proof_hash,
        origin_hash=origin_hash,
        expires_at=expires_at,
    )


async def get_transaction(session: AsyncSession, transaction_id: uuid.UUID) -> Row | None:
    return await one(
        session,
        f"SELECT {TRANSACTION_COLUMNS} FROM oauth_authorization_transactions WHERE id = :id",
        id=transaction_id,
    )


async def get_transaction_for_update(
    session: AsyncSession, transaction_id: uuid.UUID
) -> Row | None:
    return await one(
        session,
        f"SELECT {TRANSACTION_COLUMNS} FROM oauth_authorization_transactions WHERE id = :id FOR UPDATE",
        id=transaction_id,
    )


async def rotate_transaction_proof(
    session: AsyncSession, transaction_id: uuid.UUID, proof_hash: str
) -> None:
    """Replace the one-use proof of a still-valid transaction."""
    await run(
        session,
        """
        UPDATE oauth_authorization_transactions SET proof_hash = :proof_hash
         WHERE id = :id AND consumed_at IS NULL
        """,
        id=transaction_id,
        proof_hash=proof_hash,
    )


async def increment_transaction_attempts(session: AsyncSession, transaction_id: uuid.UUID) -> int:
    row = await returning(
        session,
        """
        UPDATE oauth_authorization_transactions SET attempt_count = attempt_count + 1
         WHERE id = :id
        RETURNING attempt_count
        """,
        id=transaction_id,
    )
    return int(row.attempt_count)


async def mark_transaction_authenticated(
    session: AsyncSession, *, transaction_id: uuid.UUID, user_id: uuid.UUID, at: datetime
) -> Row:
    return await returning(
        session,
        f"""
        UPDATE oauth_authorization_transactions
           SET authenticated_user_id = :user_id, authenticated_at = :at
         WHERE id = :id
        RETURNING {TRANSACTION_COLUMNS}
        """,
        id=transaction_id,
        user_id=user_id,
        at=at,
    )


async def consume_transaction(session: AsyncSession, transaction_id: uuid.UUID, at: datetime) -> int:
    """Consume a transaction; returns 0 when it was already consumed."""
    return await run(
        session,
        """
        UPDATE oauth_authorization_transactions SET consumed_at = :at
         WHERE id = :id AND consumed_at IS NULL
        """,
        id=transaction_id,
        at=at,
    )


# ---------------------------------------------------------------------------
# Authorization codes
# ---------------------------------------------------------------------------


async def insert_code(
    session: AsyncSession,
    *,
    code_hash: str,
    user_id: uuid.UUID,
    client_pk: uuid.UUID,
    transaction_id: uuid.UUID,
    redirect_uri: str,
    code_challenge: str,
    code_challenge_method: str,
    scope: str,
    expires_at: datetime,
) -> Row:
    return await returning(
        session,
        f"""
        INSERT INTO oauth_authorization_codes (
            code_hash, user_id, client_id, transaction_id, redirect_uri, code_challenge,
            code_challenge_method, scope, expires_at
        )
        VALUES (
            :code_hash, :user_id, :client_pk, :transaction_id, :redirect_uri, :code_challenge,
            :code_challenge_method, :scope, :expires_at
        )
        RETURNING {CODE_COLUMNS}
        """,
        code_hash=code_hash,
        user_id=user_id,
        client_pk=client_pk,
        transaction_id=transaction_id,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        scope=scope,
        expires_at=expires_at,
    )


async def get_code_for_update(session: AsyncSession, code_hash: str) -> Row | None:
    return await one(
        session,
        f"SELECT {CODE_COLUMNS} FROM oauth_authorization_codes WHERE code_hash = :code_hash FOR UPDATE",
        code_hash=code_hash,
    )


async def mark_code_used(
    session: AsyncSession, *, code_id: uuid.UUID, used_at: datetime, session_id: uuid.UUID
) -> int:
    """Consume the code and record the session it produced.

    Conditional on ``used_at IS NULL``, so a concurrent duplicate exchange cannot
    consume it twice: the second one updates nothing and is reported as a replay.
    """
    return await run(
        session,
        """
        UPDATE oauth_authorization_codes SET used_at = :used_at, session_id = :session_id
         WHERE id = :id AND used_at IS NULL
        """,
        id=code_id,
        used_at=used_at,
        session_id=session_id,
    )


# ---------------------------------------------------------------------------
# Login sessions (refresh-token families) and refresh tokens
# ---------------------------------------------------------------------------


async def insert_session(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    client_pk: uuid.UUID,
    scope: str,
    absolute_expires_at: datetime,
) -> Row:
    return await returning(
        session,
        f"""
        INSERT INTO oauth_sessions (user_id, client_id, scope, absolute_expires_at)
        VALUES (:user_id, :client_pk, :scope, :absolute_expires_at)
        RETURNING {SESSION_COLUMNS}
        """,
        user_id=user_id,
        client_pk=client_pk,
        scope=scope,
        absolute_expires_at=absolute_expires_at,
    )


async def get_session_for_update(session: AsyncSession, session_id: uuid.UUID) -> Row | None:
    return await one(
        session,
        f"SELECT {SESSION_COLUMNS} FROM oauth_sessions WHERE id = :id FOR UPDATE",
        id=session_id,
    )


async def session_is_active(session: AsyncSession, session_id: uuid.UUID, *, now: datetime) -> bool:
    return bool(
        await val(
            session,
            """
            SELECT 1 FROM oauth_sessions
             WHERE id = :id AND revoked_at IS NULL AND absolute_expires_at > :now
            """,
            default=0,
            id=session_id,
            now=now,
        )
    )


async def revoke_session(session: AsyncSession, session_id: uuid.UUID, *, reason: str) -> int:
    return await run(
        session,
        """
        UPDATE oauth_sessions SET revoked_at = now(), revoke_reason = :reason
         WHERE id = :id AND revoked_at IS NULL
        """,
        id=session_id,
        reason=reason,
    )


async def revoke_session_refresh_tokens(session: AsyncSession, session_id: uuid.UUID) -> int:
    return await run(
        session,
        """
        UPDATE oauth_refresh_tokens SET revoked_at = now()
         WHERE session_id = :session_id AND revoked_at IS NULL
        """,
        session_id=session_id,
    )


async def revoke_user_sessions(session: AsyncSession, user_id: uuid.UUID, *, reason: str) -> int:
    """Revoke every live session and refresh token of a user, in one statement each."""
    revoked = await run(
        session,
        """
        UPDATE oauth_sessions
           SET revoked_at = now(), revoke_reason = :reason
         WHERE user_id = :user_id AND revoked_at IS NULL
        """,
        user_id=user_id,
        reason=reason,
    )
    await run(
        session,
        """
        UPDATE oauth_refresh_tokens SET revoked_at = now()
         WHERE user_id = :user_id AND revoked_at IS NULL
        """,
        user_id=user_id,
    )
    return revoked


async def insert_refresh_token(
    session: AsyncSession,
    *,
    token_hash: str,
    jti: uuid.UUID,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    expires_at: datetime,
    parent_id: uuid.UUID | None = None,
) -> Row:
    return await returning(
        session,
        f"""
        INSERT INTO oauth_refresh_tokens (token_hash, jti, user_id, session_id, parent_id, expires_at)
        VALUES (:token_hash, :jti, :user_id, :session_id, :parent_id, :expires_at)
        RETURNING {REFRESH_COLUMNS}
        """,
        token_hash=token_hash,
        jti=jti,
        user_id=user_id,
        session_id=session_id,
        parent_id=parent_id,
        expires_at=expires_at,
    )


async def refresh_token_for_update(session: AsyncSession, token_hash: str) -> Row | None:
    return await one(
        session,
        f"SELECT {REFRESH_COLUMNS} FROM oauth_refresh_tokens WHERE token_hash = :token_hash FOR UPDATE",
        token_hash=token_hash,
    )


async def refresh_token_by_hash(session: AsyncSession, token_hash: str) -> Row | None:
    return await one(
        session,
        f"SELECT {REFRESH_COLUMNS} FROM oauth_refresh_tokens WHERE token_hash = :token_hash",
        token_hash=token_hash,
    )


async def mark_refresh_token_used(
    session: AsyncSession, token_id: uuid.UUID, used_at: datetime
) -> None:
    await run(
        session,
        "UPDATE oauth_refresh_tokens SET used_at = :used_at WHERE id = :id",
        id=token_id,
        used_at=used_at,
    )


__all__ = [
    "client_redirect_uris",
    "consume_transaction",
    "get_client",
    "get_code_for_update",
    "get_session_for_update",
    "get_transaction",
    "get_transaction_for_update",
    "increment_transaction_attempts",
    "insert_code",
    "insert_refresh_token",
    "insert_session",
    "insert_transaction",
    "mark_code_used",
    "mark_refresh_token_used",
    "mark_transaction_authenticated",
    "refresh_token_by_hash",
    "refresh_token_for_update",
    "register_origin",
    "register_redirect_uri",
    "revoke_session",
    "revoke_session_refresh_tokens",
    "revoke_user_sessions",
    "rotate_transaction_proof",
    "session_is_active",
    "upsert_client",
]
