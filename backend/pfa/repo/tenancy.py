"""Tenancy SQL: workspaces, memberships, invitations, taxpayers and grants.

The last-ADMIN invariant is enforced by taking a row lock on the workspace
(``SELECT ... FOR UPDATE``) before counting active administrators, so two
simultaneous demotions cannot both observe "two admins left" and proceed.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from pfa.repo.query import Row, all_rows, col, one, returning, run, val

MEMBERSHIP_COLUMNS = "id, workspace_id, user_id, role, status, joined_at, removed_at"

INVITATION_COLUMNS = """
    id, workspace_id, email_normalized, role, token_hash, status, invited_by_user_id,
    expires_at, accepted_at, accepted_by_user_id, revoked_at, created_at
"""

WORKSPACE_COLUMNS = "id, name, cui, accounting_currency, timezone, created_by_user_id, archived_at"


# ---------------------------------------------------------------------------
# Workspaces
# ---------------------------------------------------------------------------


async def insert_workspace(
    session: AsyncSession,
    *,
    name: str,
    cui: str | None,
    created_by_user_id: uuid.UUID,
) -> Row:
    return await returning(
        session,
        f"""
        INSERT INTO workspaces (name, cui, accounting_currency, timezone, created_by_user_id)
        VALUES (:name, :cui, 'RON', 'Europe/Bucharest', :created_by_user_id)
        RETURNING {WORKSPACE_COLUMNS}
        """,
        name=name,
        cui=cui,
        created_by_user_id=created_by_user_id,
    )


async def get_workspace(session: AsyncSession, workspace_id: uuid.UUID) -> Row | None:
    return await one(
        session, f"SELECT {WORKSPACE_COLUMNS} FROM workspaces WHERE id = :id", id=workspace_id
    )


async def lock_workspace(session: AsyncSession, workspace_id: uuid.UUID) -> bool:
    """Row lock that serialises role changes for one workspace."""
    return bool(
        await val(
            session,
            "SELECT 1 FROM workspaces WHERE id = :id FOR UPDATE",
            default=0,
            id=workspace_id,
        )
    )


async def list_workspaces_for_user(session: AsyncSession, user_id: uuid.UUID) -> list[Row]:
    return await all_rows(
        session,
        """
        SELECT w.id, w.name, w.cui, w.accounting_currency, w.timezone
          FROM workspaces w
          JOIN memberships m ON m.workspace_id = w.id
         WHERE m.user_id = :user_id
           AND m.status = 'active'
           AND w.archived_at IS NULL
         ORDER BY w.name
        """,
        user_id=user_id,
    )


# ---------------------------------------------------------------------------
# Memberships
# ---------------------------------------------------------------------------


async def get_membership(
    session: AsyncSession, *, workspace_id: uuid.UUID, user_id: uuid.UUID, for_update: bool = False
) -> Row | None:
    lock = " FOR UPDATE" if for_update else ""
    return await one(
        session,
        f"""
        SELECT {MEMBERSHIP_COLUMNS} FROM memberships
         WHERE workspace_id = :workspace_id AND user_id = :user_id{lock}
        """,
        workspace_id=workspace_id,
        user_id=user_id,
    )


async def get_membership_by_id(
    session: AsyncSession, membership_id: uuid.UUID, *, for_update: bool = False
) -> Row | None:
    lock = " FOR UPDATE" if for_update else ""
    return await one(
        session,
        f"SELECT {MEMBERSHIP_COLUMNS} FROM memberships WHERE id = :id{lock}",
        id=membership_id,
    )


async def insert_membership(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    role: str,
    status: str = "active",
) -> Row:
    return await returning(
        session,
        f"""
        INSERT INTO memberships (workspace_id, user_id, role, status)
        VALUES (:workspace_id, :user_id, :role, :status)
        ON CONFLICT (workspace_id, user_id) DO UPDATE
            SET role = EXCLUDED.role, status = EXCLUDED.status, removed_at = NULL
        RETURNING {MEMBERSHIP_COLUMNS}
        """,
        workspace_id=workspace_id,
        user_id=user_id,
        role=role,
        status=status,
    )


async def set_membership_role(
    session: AsyncSession, membership_id: uuid.UUID, role: str
) -> Row:
    return await returning(
        session,
        f"""
        UPDATE memberships SET role = :role, updated_at = now()
         WHERE id = :id
        RETURNING {MEMBERSHIP_COLUMNS}
        """,
        id=membership_id,
        role=role,
    )


async def mark_membership_removed(
    session: AsyncSession, membership_id: uuid.UUID, removed_at: datetime
) -> Row:
    return await returning(
        session,
        f"""
        UPDATE memberships SET status = 'removed', removed_at = :removed_at, updated_at = now()
         WHERE id = :id
        RETURNING {MEMBERSHIP_COLUMNS}
        """,
        id=membership_id,
        removed_at=removed_at,
    )


async def count_active_admins(session: AsyncSession, workspace_id: uuid.UUID) -> int:
    return int(
        await val(
            session,
            """
            SELECT count(*) FROM memberships
             WHERE workspace_id = :workspace_id AND role = 'ADMIN' AND status = 'active'
            """,
            default=0,
            workspace_id=workspace_id,
        )
    )


async def list_memberships(session: AsyncSession, workspace_id: uuid.UUID) -> list[Row]:
    return await all_rows(
        session,
        f"""
        SELECT {MEMBERSHIP_COLUMNS} FROM memberships
         WHERE workspace_id = :workspace_id
         ORDER BY joined_at
        """,
        workspace_id=workspace_id,
    )


async def active_member_ids(session: AsyncSession, workspace_id: uuid.UUID) -> list[uuid.UUID]:
    return await col(
        session,
        """
        SELECT user_id FROM memberships
         WHERE workspace_id = :workspace_id AND status = 'active'
        """,
        workspace_id=workspace_id,
    )


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------


async def insert_invitation(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    email_normalized: str,
    role: str,
    token_hash: str,
    invited_by_user_id: uuid.UUID,
    expires_at: datetime,
) -> Row:
    return await returning(
        session,
        f"""
        INSERT INTO invitations (
            workspace_id, email_normalized, role, token_hash, status, invited_by_user_id, expires_at
        )
        VALUES (
            :workspace_id, :email_normalized, :role, :token_hash, 'pending',
            :invited_by_user_id, :expires_at
        )
        RETURNING {INVITATION_COLUMNS}
        """,
        workspace_id=workspace_id,
        email_normalized=email_normalized,
        role=role,
        token_hash=token_hash,
        invited_by_user_id=invited_by_user_id,
        expires_at=expires_at,
    )


async def get_invitation_for_update(
    session: AsyncSession, *, workspace_id: uuid.UUID, invitation_id: uuid.UUID
) -> Row | None:
    return await one(
        session,
        f"""
        SELECT {INVITATION_COLUMNS} FROM invitations
         WHERE id = :id AND workspace_id = :workspace_id
         FOR UPDATE
        """,
        id=invitation_id,
        workspace_id=workspace_id,
    )


async def get_invitation_by_token_for_update(session: AsyncSession, token_hash: str) -> Row | None:
    return await one(
        session,
        f"""
        SELECT {INVITATION_COLUMNS} FROM invitations
         WHERE token_hash = :token_hash
         FOR UPDATE
        """,
        token_hash=token_hash,
    )


async def pending_invitation(
    session: AsyncSession, *, workspace_id: uuid.UUID, email_normalized: str
) -> Row | None:
    return await one(
        session,
        f"""
        SELECT {INVITATION_COLUMNS} FROM invitations
         WHERE workspace_id = :workspace_id
           AND email_normalized = :email_normalized
           AND status = 'pending'
        """,
        workspace_id=workspace_id,
        email_normalized=email_normalized,
    )


async def list_invitations(session: AsyncSession, workspace_id: uuid.UUID) -> list[Row]:
    return await all_rows(
        session,
        f"""
        SELECT {INVITATION_COLUMNS} FROM invitations
         WHERE workspace_id = :workspace_id
         ORDER BY created_at DESC
        """,
        workspace_id=workspace_id,
    )


async def revoke_invitation(session: AsyncSession, invitation_id: uuid.UUID, at: datetime) -> Row:
    return await returning(
        session,
        f"""
        UPDATE invitations SET status = 'revoked', revoked_at = :at, updated_at = now()
         WHERE id = :id
        RETURNING {INVITATION_COLUMNS}
        """,
        id=invitation_id,
        at=at,
    )


async def refresh_invitation(
    session: AsyncSession,
    *,
    invitation_id: uuid.UUID,
    token_hash: str,
    expires_at: datetime,
) -> Row:
    return await returning(
        session,
        f"""
        UPDATE invitations
           SET token_hash = :token_hash, status = 'pending', revoked_at = NULL,
               accepted_at = NULL, expires_at = :expires_at, updated_at = now()
         WHERE id = :id
        RETURNING {INVITATION_COLUMNS}
        """,
        id=invitation_id,
        token_hash=token_hash,
        expires_at=expires_at,
    )


async def accept_invitation(
    session: AsyncSession, *, invitation_id: uuid.UUID, user_id: uuid.UUID, at: datetime
) -> Row:
    return await returning(
        session,
        f"""
        UPDATE invitations
           SET status = 'accepted', accepted_at = :at, accepted_by_user_id = :user_id,
               updated_at = now()
         WHERE id = :id
        RETURNING {INVITATION_COLUMNS}
        """,
        id=invitation_id,
        user_id=user_id,
        at=at,
    )


async def expire_pending_invitations(session: AsyncSession, at: datetime) -> int:
    return await run(
        session,
        """
        UPDATE invitations SET status = 'revoked', revoked_at = :at
         WHERE status = 'pending' AND expires_at <= :at
        """,
        at=at,
    )


# ---------------------------------------------------------------------------
# Taxpayers and consolidation grants
# ---------------------------------------------------------------------------


async def insert_taxpayer(
    session: AsyncSession,
    *,
    created_by_user_id: uuid.UUID,
    display_name: str,
    cif: str | None,
    cnp_ciphertext: str | None,
    workspace_id: uuid.UUID | None,
    is_personal_source: bool,
) -> Row:
    return await returning(
        session,
        """
        INSERT INTO taxpayers (
            created_by_user_id, display_name, cif, cnp_ciphertext, workspace_id, is_personal_source
        )
        VALUES (:created_by_user_id, :display_name, :cif, :cnp_ciphertext, :workspace_id,
                :is_personal_source)
        RETURNING id, workspace_id, created_by_user_id, display_name, cif, is_personal_source
        """,
        created_by_user_id=created_by_user_id,
        display_name=display_name,
        cif=cif,
        cnp_ciphertext=cnp_ciphertext,
        workspace_id=workspace_id,
        is_personal_source=is_personal_source,
    )


async def get_taxpayer(session: AsyncSession, taxpayer_id: uuid.UUID) -> Row | None:
    return await one(
        session,
        """
        SELECT id, workspace_id, created_by_user_id, display_name, cif, is_personal_source
          FROM taxpayers WHERE id = :id
        """,
        id=taxpayer_id,
    )


async def grant_taxpayer_access(
    session: AsyncSession,
    *,
    taxpayer_id: uuid.UUID,
    workspace_id: uuid.UUID,
    granted_by_user_id: uuid.UUID,
    sharing_notice_version: str,
) -> Row:
    return await one(
        session,
        """
        INSERT INTO taxpayer_access_grants (
            taxpayer_id, workspace_id, granted_by_user_id, sharing_notice_version
        )
        VALUES (:taxpayer_id, :workspace_id, :granted_by_user_id, :sharing_notice_version)
        ON CONFLICT (taxpayer_id, workspace_id) DO UPDATE
            SET revoked_at = NULL, sharing_notice_version = EXCLUDED.sharing_notice_version
        RETURNING id, taxpayer_id, workspace_id, granted_by_user_id, granted_at,
                  revoked_at, sharing_notice_version
        """,
        taxpayer_id=taxpayer_id,
        workspace_id=workspace_id,
        granted_by_user_id=granted_by_user_id,
        sharing_notice_version=sharing_notice_version,
    ) or await one(
        session,
        """
        SELECT id, taxpayer_id, workspace_id, granted_by_user_id, granted_at, revoked_at,
               sharing_notice_version
          FROM taxpayer_access_grants
         WHERE taxpayer_id = :taxpayer_id AND workspace_id = :workspace_id
        """,
        taxpayer_id=taxpayer_id,
        workspace_id=workspace_id,
    )


async def active_grant(
    session: AsyncSession, *, taxpayer_id: uuid.UUID, workspace_id: uuid.UUID
) -> Row | None:
    return await one(
        session,
        """
        SELECT id, taxpayer_id, workspace_id, granted_by_user_id, granted_at, revoked_at,
               sharing_notice_version
          FROM taxpayer_access_grants
         WHERE taxpayer_id = :taxpayer_id AND workspace_id = :workspace_id
           AND revoked_at IS NULL
        """,
        taxpayer_id=taxpayer_id,
        workspace_id=workspace_id,
    )


async def revoke_taxpayer_access(
    session: AsyncSession, *, taxpayer_id: uuid.UUID, workspace_id: uuid.UUID, at: datetime
) -> int:
    return await run(
        session,
        """
        UPDATE taxpayer_access_grants SET revoked_at = :at
         WHERE taxpayer_id = :taxpayer_id AND workspace_id = :workspace_id
           AND revoked_at IS NULL
        """,
        taxpayer_id=taxpayer_id,
        workspace_id=workspace_id,
        at=at,
    )


async def authorized_taxpayer_ids(session: AsyncSession, workspace_id: uuid.UUID) -> list[uuid.UUID]:
    return await col(
        session,
        """
        SELECT taxpayer_id FROM taxpayer_access_grants
         WHERE workspace_id = :workspace_id AND revoked_at IS NULL
        """,
        workspace_id=workspace_id,
    )


async def authorized_workspaces_for_user(
    session: AsyncSession, *, taxpayer_id: uuid.UUID, user_id: uuid.UUID
) -> list[uuid.UUID]:
    """Workspaces that authorized a taxpayer *and* where the caller is an active member.

    Uses the ``SECURITY DEFINER`` helper so a grant can be discovered without a
    tenant context, and without exposing any taxpayer data.
    """
    return await col(
        session,
        "SELECT app.authorized_workspaces_for_user(:taxpayer_id, :user_id)",
        taxpayer_id=taxpayer_id,
        user_id=user_id,
    )
