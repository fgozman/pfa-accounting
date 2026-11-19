"""Workspaces, memberships, invitations and the last-ADMIN invariant.

Roles are per membership, never global.  There are exactly two: ``ADMIN`` adds
user management only; every other capability is identical for both roles and all
workspace documents are shared.

All statements live in :mod:`pfa.repo.tenancy`.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from pfa import clock
from pfa.config import Settings, get_settings
from pfa.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from pfa.models.base import InvitationStatus, MembershipRole, MembershipStatus
from pfa.repo import tenancy as repo
from pfa.security.tokens import hash_token, new_opaque_token
from pfa.services.accounts import normalize_email
from pfa.services.audit import emit_event, record_audit


async def create_workspace(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    name: str,
    cui: str | None = None,
):
    """Create a workspace and make the creator its first ADMIN."""
    workspace = await repo.insert_workspace(
        session, name=name.strip(), cui=cui, created_by_user_id=user_id
    )
    membership = await repo.insert_membership(
        session,
        workspace_id=workspace.id,
        user_id=user_id,
        role=MembershipRole.ADMIN.value,
    )
    await record_audit(
        session,
        action="workspace.created",
        entity_type="workspace",
        entity_id=workspace.id,
        workspace_id=workspace.id,
        actor_user_id=user_id,
        after={"name": workspace.name, "cui": workspace.cui},
    )
    return workspace, membership


async def list_workspaces(session: AsyncSession, user_id: uuid.UUID) -> list:
    return await repo.list_workspaces_for_user(session, user_id)


async def get_membership(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    for_update: bool = False,
):
    return await repo.get_membership(
        session, workspace_id=workspace_id, user_id=user_id, for_update=for_update
    )


async def require_membership(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    require_admin: bool = False,
    for_update: bool = False,
):
    """Every request checks the *current* membership, not a token claim."""
    membership = await repo.get_membership(
        session, workspace_id=workspace_id, user_id=user_id, for_update=for_update
    )
    if membership is None or membership.status != MembershipStatus.ACTIVE:
        raise PermissionDenied("Nu aveți acces la acest spațiu de lucru.")
    if require_admin and membership.role != MembershipRole.ADMIN:
        raise PermissionDenied("Această operațiune este permisă doar administratorilor.")
    return membership


async def is_active_member(
    session: AsyncSession, *, workspace_id: uuid.UUID, user_id: uuid.UUID
) -> bool:
    membership = await repo.get_membership(session, workspace_id=workspace_id, user_id=user_id)
    return membership is not None and membership.status == MembershipStatus.ACTIVE


async def get_workspace(session: AsyncSession, workspace_id: uuid.UUID):
    return await repo.get_workspace(session, workspace_id)


async def _lock_workspace(session: AsyncSession, workspace_id: uuid.UUID) -> None:
    """Serialise role changes for one workspace.

    Holding a row lock on the workspace guarantees that two simultaneous
    demotions/removals cannot both observe "two admins left" and both proceed.
    """
    if not await repo.lock_workspace(session, workspace_id):
        raise NotFound("Spațiul de lucru nu există.")


async def count_active_admins(session: AsyncSession, workspace_id: uuid.UUID) -> int:
    return await repo.count_active_admins(session, workspace_id)


async def change_role(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    target_user_id: uuid.UUID,
    new_role: MembershipRole,
):
    await require_membership(
        session, workspace_id=workspace_id, user_id=actor_user_id, require_admin=True
    )
    await _lock_workspace(session, workspace_id)
    target = await repo.get_membership(
        session, workspace_id=workspace_id, user_id=target_user_id, for_update=True
    )
    if target is None or target.status != MembershipStatus.ACTIVE:
        raise NotFound("Utilizatorul nu este membru activ al acestui spațiu de lucru.")
    if (
        target.role == MembershipRole.ADMIN
        and new_role != MembershipRole.ADMIN
        and await repo.count_active_admins(session, workspace_id) <= 1
    ):
        raise Conflict(
            "Ultimul administrator activ nu poate fi retrogradat. "
            "Numiți un alt administrator mai întâi."
        )
    updated = await repo.set_membership_role(session, target.id, new_role.value)
    await record_audit(
        session,
        action="membership.role_changed",
        entity_type="membership",
        entity_id=target.id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        before={"role": str(target.role)},
        after={"role": str(new_role), "user_id": str(target_user_id)},
    )
    return updated


async def remove_member(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    target_user_id: uuid.UUID,
    reason: str | None = None,
):
    await require_membership(
        session, workspace_id=workspace_id, user_id=actor_user_id, require_admin=True
    )
    await _lock_workspace(session, workspace_id)
    target = await repo.get_membership(
        session, workspace_id=workspace_id, user_id=target_user_id, for_update=True
    )
    if target is None or target.status != MembershipStatus.ACTIVE:
        raise NotFound("Utilizatorul nu este membru activ al acestui spațiu de lucru.")
    if (
        target.role == MembershipRole.ADMIN
        and await repo.count_active_admins(session, workspace_id) <= 1
    ):
        raise Conflict(
            "Ultimul administrator activ nu poate fi eliminat. "
            "Numiți un alt administrator mai întâi."
        )
    updated = await repo.mark_membership_removed(session, target.id, clock.now())
    # Removal immediately stops jobs, deliveries and downloads for this PFA and
    # does not delete the member's historical records.
    await emit_event(
        session,
        event_type="membership.removed",
        workspace_id=workspace_id,
        payload={"user_id": str(target_user_id), "reason": reason},
        dedupe_key=f"membership-removed:{target.id}",
    )
    await record_audit(
        session,
        action="membership.removed",
        entity_type="membership",
        entity_id=target.id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        before={"status": "active", "role": str(target.role)},
        after={"status": "removed", "reason": reason},
    )
    return updated


async def leave_workspace(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
):
    """Both roles may leave, as long as the last-ADMIN invariant holds."""
    await _lock_workspace(session, workspace_id)
    membership = await repo.get_membership(
        session, workspace_id=workspace_id, user_id=user_id, for_update=True
    )
    if membership is None or membership.status != MembershipStatus.ACTIVE:
        raise NotFound("Nu sunteți membru activ al acestui spațiu de lucru.")
    if (
        membership.role == MembershipRole.ADMIN
        and await repo.count_active_admins(session, workspace_id) <= 1
    ):
        raise Conflict(
            "Nu puteți părăsi spațiul de lucru ca ultim administrator activ. "
            "Transferați rolul de administrator mai întâi."
        )
    updated = await repo.mark_membership_removed(session, membership.id, clock.now())
    await record_audit(
        session,
        action="membership.left",
        entity_type="membership",
        entity_id=membership.id,
        workspace_id=workspace_id,
        actor_user_id=user_id,
    )
    return updated


async def list_members(session: AsyncSession, workspace_id: uuid.UUID) -> list:
    return await repo.list_memberships(session, workspace_id)


async def active_member_ids(session: AsyncSession, workspace_id: uuid.UUID) -> list[uuid.UUID]:
    return await repo.active_member_ids(session, workspace_id)


async def list_members_with_accounts(session: AsyncSession, workspace_id: uuid.UUID) -> list:
    """Members and their account e-mail in one statement (no query per member)."""
    from pfa.repo import registers

    return await registers.members_with_accounts(session, workspace_id=workspace_id)


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------


async def invite_member(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    email: str,
    role: MembershipRole = MembershipRole.DEFAULT,
    settings: Settings | None = None,
):
    settings = settings or get_settings()
    await require_membership(
        session, workspace_id=workspace_id, user_id=actor_user_id, require_admin=True
    )
    normalized = normalize_email(email)
    if "@" not in normalized:
        raise ValidationFailed("Adresa de email nu este validă.", field_errors={"email": "invalid"})

    from pfa.repo import accounts as accounts_repo

    existing_user = await accounts_repo.find_user_by_email(session, normalized)
    if existing_user is not None:
        membership = await repo.get_membership(
            session, workspace_id=workspace_id, user_id=existing_user.id
        )
        if membership is not None and membership.status == MembershipStatus.ACTIVE:
            raise Conflict("Utilizatorul este deja membru al acestui spațiu de lucru.")

    pending = await repo.pending_invitation(
        session, workspace_id=workspace_id, email_normalized=normalized
    )
    if pending is not None:
        await repo.revoke_invitation(session, pending.id, clock.now())

    token = new_opaque_token(32)
    invitation = await repo.insert_invitation(
        session,
        workspace_id=workspace_id,
        email_normalized=normalized,
        role=role.value,
        token_hash=hash_token(token),
        invited_by_user_id=actor_user_id,
        expires_at=clock.now() + timedelta(seconds=settings.invitation_ttl_seconds),
    )
    await emit_event(
        session,
        event_type="email.invitation",
        workspace_id=workspace_id,
        payload={"invitation_id": str(invitation.id), "email": normalized, "token": token},
        dedupe_key=f"invitation:{invitation.id}",
    )
    await record_audit(
        session,
        action="invitation.created",
        entity_type="invitation",
        entity_id=invitation.id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        after={"email": normalized, "role": str(role)},
    )
    return invitation, token


async def list_invitations(session: AsyncSession, workspace_id: uuid.UUID) -> list:
    return await repo.list_invitations(session, workspace_id)


async def revoke_invitation(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    invitation_id: uuid.UUID,
):
    await require_membership(
        session, workspace_id=workspace_id, user_id=actor_user_id, require_admin=True
    )
    invitation = await repo.get_invitation_for_update(
        session, workspace_id=workspace_id, invitation_id=invitation_id
    )
    if invitation is None:
        raise NotFound("Invitația nu există.")
    if invitation.status == InvitationStatus.ACCEPTED:
        raise Conflict("Invitația a fost deja acceptată.")
    updated = await repo.revoke_invitation(session, invitation.id, clock.now())
    await record_audit(
        session,
        action="invitation.revoked",
        entity_type="invitation",
        entity_id=invitation.id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
    )
    return updated


async def resend_invitation(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    invitation_id: uuid.UUID,
    settings: Settings | None = None,
):
    settings = settings or get_settings()
    await require_membership(
        session, workspace_id=workspace_id, user_id=actor_user_id, require_admin=True
    )
    invitation = await repo.get_invitation_for_update(
        session, workspace_id=workspace_id, invitation_id=invitation_id
    )
    if invitation is None:
        raise NotFound("Invitația nu există.")
    if invitation.status == InvitationStatus.ACCEPTED:
        raise Conflict("Invitația a fost deja acceptată.")
    token = new_opaque_token(32)
    updated = await repo.refresh_invitation(
        session,
        invitation_id=invitation.id,
        token_hash=hash_token(token),
        expires_at=clock.now() + timedelta(seconds=settings.invitation_ttl_seconds),
    )
    await emit_event(
        session,
        event_type="email.invitation",
        workspace_id=workspace_id,
        payload={"invitation_id": str(updated.id), "token": token, "resend": True},
        dedupe_key=f"invitation:{updated.id}:{updated.expires_at.isoformat()}",
    )
    return updated, token


async def accept_invitation(
    session: AsyncSession,
    *,
    token: str,
    user_id: uuid.UUID,
    user_email: str,
):
    """Acceptance requires a verified matching email; existing accounts join
    without creating duplicate identities."""
    invitation = await repo.get_invitation_by_token_for_update(session, hash_token(token))
    if invitation is None:
        raise ValidationFailed("Invitația nu este validă.")
    if invitation.status == InvitationStatus.REVOKED:
        raise Conflict("Invitația a fost revocată.")
    if invitation.status == InvitationStatus.ACCEPTED:
        raise Conflict("Invitația a fost deja acceptată.")
    if invitation.expires_at <= clock.now():
        raise ValidationFailed("Invitația a expirat.")
    if normalize_email(user_email) != invitation.email_normalized:
        raise PermissionDenied(
            "Invitația este emisă pentru altă adresă de email decât cea a contului autentificat."
        )

    # upsert: an existing membership is reactivated, never duplicated
    membership = await repo.insert_membership(
        session,
        workspace_id=invitation.workspace_id,
        user_id=user_id,
        role=invitation.role,
    )
    await repo.accept_invitation(session, invitation_id=invitation.id, user_id=user_id, at=clock.now())
    await record_audit(
        session,
        action="invitation.accepted",
        entity_type="invitation",
        entity_id=invitation.id,
        workspace_id=invitation.workspace_id,
        actor_user_id=user_id,
        after={"role": str(invitation.role)},
    )
    return membership


async def prune_expired_invitations(session: AsyncSession) -> int:
    return await repo.expire_pending_invitations(session, clock.now())


# ---------------------------------------------------------------------------
# Taxpayer records and consolidation grants
# ---------------------------------------------------------------------------


async def create_taxpayer(
    session: AsyncSession,
    *,
    created_by_user_id: uuid.UUID,
    display_name: str,
    cif: str | None = None,
    cnp_ciphertext: str | None = None,
    workspace_id: uuid.UUID | None = None,
    is_personal_source: bool = False,
):
    return await repo.insert_taxpayer(
        session,
        created_by_user_id=created_by_user_id,
        display_name=display_name,
        cif=cif,
        cnp_ciphertext=cnp_ciphertext,
        workspace_id=workspace_id,
        is_personal_source=is_personal_source,
    )


async def get_taxpayer(session: AsyncSession, taxpayer_id: uuid.UUID):
    return await repo.get_taxpayer(session, taxpayer_id)


async def workspace_for_taxpayer(
    session: AsyncSession, *, taxpayer_id: uuid.UUID, user_id: uuid.UUID
) -> uuid.UUID:
    """The workspace a user may prepare a personal declaration for a taxpayer in.

    The taxpayer's own workspace wins when the user is an active member of it;
    otherwise the first workspace that holds an access grant for the taxpayer and
    where the user is an active member.  Discovery goes through a SECURITY DEFINER
    helper returning identifiers only, so no personal record is exposed.
    """
    taxpayer = await repo.get_taxpayer(session, taxpayer_id)
    if taxpayer is None:
        raise NotFound("Contribuabilul nu există.")
    candidates = list(
        await repo.authorized_workspaces_for_user(
            session, taxpayer_id=taxpayer_id, user_id=user_id
        )
    )
    if taxpayer.workspace_id is not None and await is_active_member(
        session, workspace_id=taxpayer.workspace_id, user_id=user_id
    ):
        candidates.insert(0, taxpayer.workspace_id)
    if not candidates:
        raise PermissionDenied(
            "Nu aveți acces la un spațiu de lucru autorizat pentru acest contribuabil."
        )
    return candidates[0]


async def grant_taxpayer_access(
    session: AsyncSession,
    *,
    taxpayer_id: uuid.UUID,
    workspace_id: uuid.UUID,
    granted_by_user_id: uuid.UUID,
    sharing_notice_version: str,
):
    """Explicit authorization is required before a personal source record may be
    consolidated into a workspace return."""
    existing = await repo.active_grant(
        session, taxpayer_id=taxpayer_id, workspace_id=workspace_id
    )
    if existing is not None:
        return existing
    grant = await repo.grant_taxpayer_access(
        session,
        taxpayer_id=taxpayer_id,
        workspace_id=workspace_id,
        granted_by_user_id=granted_by_user_id,
        sharing_notice_version=sharing_notice_version,
    )
    await record_audit(
        session,
        action="taxpayer.access_granted",
        entity_type="taxpayer",
        entity_id=taxpayer_id,
        workspace_id=workspace_id,
        actor_user_id=granted_by_user_id,
        after={"sharing_notice_version": sharing_notice_version},
    )
    return grant


async def revoke_taxpayer_access(
    session: AsyncSession, *, taxpayer_id: uuid.UUID, workspace_id: uuid.UUID
) -> None:
    await repo.revoke_taxpayer_access(
        session, taxpayer_id=taxpayer_id, workspace_id=workspace_id, at=clock.now()
    )


async def authorized_taxpayer_ids(session: AsyncSession, workspace_id: uuid.UUID) -> list[uuid.UUID]:
    return await repo.authorized_taxpayer_ids(session, workspace_id)
