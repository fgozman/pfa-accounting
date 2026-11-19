"""Account SQL: users, e-mail verification/reset tokens and MFA factors.

Every statement is explicit, lists its columns and binds its values.

Login sessions and refresh tokens belong to the authorization server and live in
:mod:`pfa.oauth.repo`; this module never touches an ``oauth_`` table.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from pfa.repo.query import Row, all_rows, col, one, returning, run, val

USER_COLUMNS = """
    id, email_normalized, email_display, password_hash, status, verified_at,
    last_login_at, password_changed_at, mfa_required, created_at, updated_at
"""


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


async def find_user_by_email(session: AsyncSession, email_normalized: str) -> Row | None:
    return await one(
        session,
        f"SELECT {USER_COLUMNS} FROM users WHERE email_normalized = :email",
        email=email_normalized,
    )


async def find_user_by_email_for_update(
    session: AsyncSession, email_normalized: str
) -> Row | None:
    """Look up a user and lock the row, serialising token issuance per account."""
    return await one(
        session,
        f"SELECT {USER_COLUMNS} FROM users WHERE email_normalized = :email FOR UPDATE",
        email=email_normalized,
    )


async def get_user(session: AsyncSession, user_id: uuid.UUID) -> Row | None:
    return await one(session, f"SELECT {USER_COLUMNS} FROM users WHERE id = :id", id=user_id)


async def user_by_id_for_update(session: AsyncSession, user_id: uuid.UUID) -> Row | None:
    return await one(
        session,
        f"SELECT {USER_COLUMNS} FROM users WHERE id = :id FOR UPDATE",
        id=user_id,
    )


async def insert_user(
    session: AsyncSession,
    *,
    email_normalized: str,
    email_display: str,
    password_hash: str,
    status: str,
) -> Row:
    return await returning(
        session,
        f"""
        INSERT INTO users (email_normalized, email_display, password_hash, status, mfa_required)
        VALUES (:email_normalized, :email_display, :password_hash, :status, false)
        RETURNING {USER_COLUMNS}
        """,
        email_normalized=email_normalized,
        email_display=email_display,
        password_hash=password_hash,
        status=status,
    )


async def set_password_hash(
    session: AsyncSession, user_id: uuid.UUID, password_hash: str, changed_at: datetime
) -> Row:
    return await returning(
        session,
        f"""
        UPDATE users
           SET password_hash = :password_hash,
               password_changed_at = :changed_at,
               updated_at = now()
         WHERE id = :id
        RETURNING {USER_COLUMNS}
        """,
        id=user_id,
        password_hash=password_hash,
        changed_at=changed_at,
    )


async def mark_email_verified(
    session: AsyncSession, user_id: uuid.UUID, verified_at: datetime
) -> Row:
    return await returning(
        session,
        f"""
        UPDATE users
           SET status = 'active', verified_at = :verified_at, updated_at = now()
         WHERE id = :id
        RETURNING {USER_COLUMNS}
        """,
        id=user_id,
        verified_at=verified_at,
    )


async def record_login(session: AsyncSession, user_id: uuid.UUID, at: datetime) -> Row:
    return await returning(
        session,
        f"""
        UPDATE users SET last_login_at = :at, updated_at = now()
         WHERE id = :id
        RETURNING {USER_COLUMNS}
        """,
        id=user_id,
        at=at,
    )


async def set_user_email(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    email_normalized: str,
    email_display: str,
    status: str,
) -> Row:
    return await returning(
        session,
        f"""
        UPDATE users
           SET email_normalized = :email_normalized,
               email_display = :email_display,
               status = :status,
               verified_at = NULL,
               updated_at = now()
         WHERE id = :id
        RETURNING {USER_COLUMNS}
        """,
        id=user_id,
        email_normalized=email_normalized,
        email_display=email_display,
        status=status,
    )


# ---------------------------------------------------------------------------
# E-mail verification and password reset tokens
# ---------------------------------------------------------------------------


async def insert_email_verification_token(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    email_normalized: str,
    token_hash: str,
    expires_at: datetime,
) -> Row:
    return await returning(
        session,
        """
        INSERT INTO email_verification_tokens (user_id, email_normalized, token_hash, expires_at)
        VALUES (:user_id, :email_normalized, :token_hash, :expires_at)
        RETURNING id, user_id, email_normalized, token_hash, expires_at, used_at
        """,
        user_id=user_id,
        email_normalized=email_normalized,
        token_hash=token_hash,
        expires_at=expires_at,
    )


async def email_verification_token_for_update(session: AsyncSession, token_hash: str) -> Row | None:
    return await one(
        session,
        """
        SELECT id, user_id, email_normalized, token_hash, expires_at, used_at
          FROM email_verification_tokens
         WHERE token_hash = :token_hash
         FOR UPDATE
        """,
        token_hash=token_hash,
    )


async def consume_email_verification_token(
    session: AsyncSession, token_id: uuid.UUID, used_at: datetime
) -> None:
    await run(
        session,
        "UPDATE email_verification_tokens SET used_at = :used_at WHERE id = :id",
        id=token_id,
        used_at=used_at,
    )


async def retire_email_verification_tokens(
    session: AsyncSession, user_id: uuid.UUID, used_at: datetime
) -> int:
    """Retire every unused verification token for a user.

    Issuing a new link supersedes the previous one: at most one activation link is
    valid at a time, so a link captured from an older e-mail cannot be replayed
    after the user asked for a fresh one.
    """
    return await run(
        session,
        """
        UPDATE email_verification_tokens
           SET used_at = :used_at
         WHERE user_id = :user_id AND used_at IS NULL
        """,
        user_id=user_id,
        used_at=used_at,
    )


async def insert_password_reset_token(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    token_hash: str,
    expires_at: datetime,
) -> Row:
    return await returning(
        session,
        """
        INSERT INTO password_reset_tokens (user_id, token_hash, expires_at)
        VALUES (:user_id, :token_hash, :expires_at)
        RETURNING id, user_id, token_hash, expires_at, used_at
        """,
        user_id=user_id,
        token_hash=token_hash,
        expires_at=expires_at,
    )


async def password_reset_token_for_update(session: AsyncSession, token_hash: str) -> Row | None:
    return await one(
        session,
        """
        SELECT id, user_id, token_hash, expires_at, used_at
          FROM password_reset_tokens
         WHERE token_hash = :token_hash
         FOR UPDATE
        """,
        token_hash=token_hash,
    )


async def consume_password_reset_token(
    session: AsyncSession, token_id: uuid.UUID, used_at: datetime
) -> None:
    await run(
        session,
        "UPDATE password_reset_tokens SET used_at = :used_at WHERE id = :id",
        id=token_id,
        used_at=used_at,
    )


async def email_exists(session: AsyncSession, email_normalized: str) -> bool:
    return bool(
        await val(
            session,
            "SELECT 1 FROM users WHERE email_normalized = :email",
            default=0,
            email=email_normalized,
        )
    )


async def user_ids_for_emails(session: AsyncSession, emails: list[str]) -> list[Row]:
    """Used by the seed and by tests to resolve several recipients at once."""
    if not emails:
        return []
    return await all_rows(
        session,
        "SELECT id, email_normalized FROM users WHERE email_normalized = ANY(CAST(:emails AS text[]))",
        emails=emails,
    )


async def confirmed_email(session: AsyncSession, user_id: uuid.UUID) -> str | None:
    return await val(
        session, "SELECT email_normalized FROM users WHERE id = :id", id=user_id
    )


async def active_mfa_factors(session: AsyncSession, user_id: uuid.UUID) -> list[Any]:
    return await col(
        session,
        """
        SELECT id FROM mfa_factors
         WHERE user_id = :user_id AND confirmed_at IS NOT NULL AND disabled_at IS NULL
        """,
        user_id=user_id,
    )
