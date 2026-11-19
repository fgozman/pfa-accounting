"""Account lifecycle: registration, verification, credential checks, reset, email change.

All statements live in :mod:`pfa.repo.accounts`; this module only orchestrates
them, applies policy (rate limits, password hashing) and records audit/outbox
events in the same transaction.

Login sessions are owned by the authorization server.  When credentials change,
this module asks it to revoke them through
:class:`~pfa.clients.authorization_server.AuthorizationServerClient`; it never reads
or writes an ``oauth_`` table and never imports :mod:`pfa.oauth`.

Revocation runs in the authorization server's own unit of work (as it would
across a network), *before* this transaction commits: if the account change then
fails, sessions were revoked needlessly, which is the safe direction.

Passwords are hashed with Argon2id.  Verification and reset links are random,
hashed at rest, single-use and short-lived.  Login, reset and registration
responses are deliberately generic so they cannot be used to enumerate accounts.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from pfa import clock
from pfa.clients.authorization_server import authorization_server
from pfa.config import Settings, get_settings
from pfa.errors import Conflict, DomainError, NotFound, RateLimited, ValidationFailed
from pfa.models.base import UserStatus
from pfa.repo import accounts as repo
from pfa.security.passwords import hash_password, needs_rehash, verify_password
from pfa.security.ratelimit import login_limiter, register_limiter
from pfa.security.tokens import hash_token, new_opaque_token
from pfa.services.audit import emit_event, record_audit


class ValidationError(DomainError):
    """Invalid credentials.  Kept deliberately generic."""

    code = "invalid_credentials"
    http_status = 401


def normalize_email(email: str) -> str:
    return email.strip().casefold()


@dataclass(frozen=True, slots=True)
class IssuedToken:
    token: str
    expires_at: datetime


async def get_user(session: AsyncSession, user_id: uuid.UUID):
    user = await repo.get_user(session, user_id)
    if user is None:
        raise NotFound("Contul nu există.")
    return user


async def find_active_user(session: AsyncSession, user_id: uuid.UUID):
    """The account behind a token subject, or ``None`` when missing or not active."""
    user = await repo.get_user(session, user_id)
    if user is None or user.status != UserStatus.ACTIVE:
        return None
    return user


async def register_user(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    settings: Settings | None = None,
    client_ip: str | None = None,
):
    settings = settings or get_settings()
    normalized = normalize_email(email)
    if not normalized or "@" not in normalized:
        raise ValidationFailed("Adresa de email nu este validă.", field_errors={"email": "invalid"})

    limiter_key = f"register:{client_ip or 'unknown'}"
    wait = register_limiter.check(limiter_key)
    if wait:
        raise RateLimited("Prea multe încercări de înregistrare. Reîncercați mai târziu.")

    if await repo.find_user_by_email(session, normalized) is not None:
        register_limiter.record_failure(limiter_key)
        # Generic message: do not confirm whether the address is registered.
        raise Conflict("Contul nu a putut fi creat cu datele furnizate.")

    user = await repo.insert_user(
        session,
        email_normalized=normalized,
        email_display=email.strip(),
        password_hash=hash_password(password, settings),
        status=UserStatus.PENDING_VERIFICATION.value,
    )
    token = await issue_email_verification(session, user, settings=settings)
    await record_audit(
        session,
        action="account.registered",
        entity_type="user",
        entity_id=user.id,
        actor_user_id=user.id,
        ip_address=client_ip,
    )
    return user, token


async def issue_email_verification(
    session: AsyncSession, user, *, settings: Settings | None = None
) -> IssuedToken:
    """Issue a fresh, single-use activation link for a user.

    Any previous unused link is retired first, so a user always has exactly one
    valid activation link and a superseded link cannot be replayed.
    """
    settings = settings or get_settings()
    raw = new_opaque_token(32)
    token_hash = hash_token(raw)
    now = clock.now()
    expires = now + timedelta(seconds=settings.email_token_ttl_seconds)
    await repo.retire_email_verification_tokens(session, user.id, now)
    await repo.insert_email_verification_token(
        session,
        user_id=user.id,
        email_normalized=user.email_normalized,
        token_hash=token_hash,
        expires_at=expires,
    )
    await emit_event(
        session,
        event_type="email.verification_requested",
        payload={"user_id": str(user.id), "token": raw, "email": user.email_display},
        # The token hash gives each issued link its own logical event: a resend is
        # a genuinely new message, and a frozen clock cannot collapse two of them.
        dedupe_key=f"email-verification:{user.id}:{token_hash}",
    )
    return IssuedToken(token=raw, expires_at=expires)


async def resend_email_verification(
    session: AsyncSession, *, email: str, settings: Settings | None = None
) -> IssuedToken | None:
    """Issue a new activation link for a still-unverified account.

    Deliberately generic: an unknown address, an already-active account and a
    disabled account all return ``None`` without sending anything, so the endpoint
    cannot be used to enumerate accounts.  Rate limiting lives at the HTTP layer.
    """
    settings = settings or get_settings()
    # Lock the account so two concurrent resends cannot both mint a link: the
    # second waits, then supersedes the first. Exactly one link stays valid.
    user = await repo.find_user_by_email_for_update(session, normalize_email(email))
    if user is None or user.status != UserStatus.PENDING_VERIFICATION.value:
        return None
    token = await issue_email_verification(session, user, settings=settings)
    await record_audit(
        session,
        action="account.verification_resent",
        entity_type="user",
        entity_id=user.id,
        actor_user_id=user.id,
    )
    return token


async def verify_email(session: AsyncSession, *, token: str, settings: Settings | None = None):
    settings = settings or get_settings()
    row = await repo.email_verification_token_for_update(session, hash_token(token))
    if row is None:
        raise ValidationFailed("Linkul de verificare nu este valid.")
    if row.used_at is not None:
        raise Conflict("Linkul de verificare a fost deja folosit.")
    if row.expires_at <= clock.now():
        raise ValidationFailed("Linkul de verificare a expirat.")
    user = await get_user(session, row.user_id)
    if user.email_normalized != row.email_normalized:
        raise ValidationFailed("Linkul de verificare nu corespunde adresei curente.")

    await repo.consume_email_verification_token(session, row.id, clock.now())
    user = await repo.mark_email_verified(session, user.id, clock.now())
    await record_audit(
        session,
        action="account.email_verified",
        entity_type="user",
        entity_id=user.id,
        actor_user_id=user.id,
    )
    _ = settings
    return user


async def authenticate(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    settings: Settings | None = None,
    client_ip: str | None = None,
    origin_key: str | None = None,
):
    """Verify credentials.  Raises a generic error for every failure mode."""
    settings = settings or get_settings()
    limiter_key = f"login:{normalize_email(email)}:{origin_key or client_ip or 'unknown'}"
    wait = login_limiter.check(limiter_key)
    if wait:
        raise RateLimited(
            "Prea multe încercări de autentificare. Reîncercați mai târziu.",
            details={"retry_after_seconds": wait},
        )

    user = await repo.find_user_by_email(session, normalize_email(email))
    if user is None or not verify_password(password, user.password_hash, settings):
        backoff = login_limiter.record_failure(limiter_key)
        await record_audit(
            session,
            action="account.login_failed",
            entity_type="user",
            entity_id=user.id if user else None,
            actor_user_id=user.id if user else None,
            ip_address=client_ip,
        )
        raise ValidationError(
            "Datele de autentificare nu sunt corecte.",
            details={"retry_after_seconds": backoff},
        )
    if user.status == UserStatus.DISABLED.value:
        raise ValidationError("Datele de autentificare nu sunt corecte.")
    if user.status == UserStatus.PENDING_VERIFICATION.value:
        raise ValidationError(
            "Contul nu este verificat. Confirmați adresa de email.",
            details={"reason": "email_not_verified", "user_id": str(user.id)},
        )

    password_hash = user.password_hash
    if needs_rehash(password_hash, settings):
        password_hash = hash_password(password, settings)
        user = await repo.set_password_hash(session, user.id, password_hash, clock.now())
    login_limiter.reset(limiter_key)
    user = await repo.record_login(session, user.id, clock.now())
    # every authentication decision is audited, successes included (ASVS 16.3.1)
    await record_audit(
        session,
        action="account.login_succeeded",
        entity_type="user",
        entity_id=user.id,
        actor_user_id=user.id,
        ip_address=client_ip,
        after={"method": "password"},
    )
    return user


#: why a credential check was refused; part of the identity-provider wire contract
CREDENTIAL_REJECTIONS = ("invalid_credentials", "email_not_verified", "too_many_attempts")


@dataclass(frozen=True, slots=True)
class CredentialCheck:
    """Outcome of a credential check performed for the authorization server.

    Exactly one of ``user_id`` (accepted) and ``rejection`` (refused) is set.
    """

    user_id: uuid.UUID | None
    rejection: str | None = None
    message: str = ""
    http_status: int = 200

    @property
    def accepted(self) -> bool:
        return self.user_id is not None


async def check_credentials(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    client_ip: str | None = None,
    attempt_key: str | None = None,
    settings: Settings | None = None,
) -> CredentialCheck:
    """Verify the credentials of an *active* account, reporting refusals as values.

    Refusals are returned rather than raised so the caller's unit of work commits:
    the ``account.login_failed`` audit record must survive a failed login.
    """
    try:
        user = await authenticate(
            session,
            email=email,
            password=password,
            settings=settings,
            client_ip=client_ip,
            origin_key=attempt_key,
        )
    except RateLimited as exc:
        return CredentialCheck(None, "too_many_attempts", exc.message, exc.http_status)
    except ValidationError as exc:
        reason = (exc.details or {}).get("reason")
        rejection = "email_not_verified" if reason == "email_not_verified" else "invalid_credentials"
        return CredentialCheck(None, rejection, exc.message, exc.http_status)
    if user.status != UserStatus.ACTIVE.value:
        return CredentialCheck(None, "email_not_verified", "Contul nu este activ.", 401)
    return CredentialCheck(user.id)


async def request_password_reset(
    session: AsyncSession, *, email: str, settings: Settings | None = None
) -> IssuedToken | None:
    """Always returns success to the caller; ``None`` when no account matched."""
    settings = settings or get_settings()
    user = await repo.find_user_by_email(session, normalize_email(email))
    if user is None or user.status == UserStatus.DISABLED.value:
        return None
    raw = new_opaque_token(32)
    expires = clock.now() + timedelta(hours=1)
    await repo.insert_password_reset_token(
        session, user_id=user.id, token_hash=hash_token(raw), expires_at=expires
    )
    await record_audit(
        session,
        action="account.password_reset_requested",
        entity_type="user",
        entity_id=user.id,
        actor_user_id=user.id,
    )
    await emit_event(
        session,
        event_type="email.password_reset_requested",
        payload={"user_id": str(user.id), "token": raw, "email": user.email_display},
        dedupe_key=f"password-reset:{user.id}:{expires.isoformat()}",
    )
    return IssuedToken(token=raw, expires_at=expires)


async def confirm_password_reset(
    session: AsyncSession,
    *,
    token: str,
    new_password: str,
    settings: Settings | None = None,
):
    settings = settings or get_settings()
    row = await repo.password_reset_token_for_update(session, hash_token(token))
    if row is None:
        raise ValidationFailed("Linkul de resetare nu este valid.")
    if row.used_at is not None:
        raise Conflict("Linkul de resetare a fost deja folosit.")
    if row.expires_at <= clock.now():
        raise ValidationFailed("Linkul de resetare a expirat.")

    await repo.consume_password_reset_token(session, row.id, clock.now())
    user = await repo.set_password_hash(
        session, row.user_id, hash_password(new_password, settings), clock.now()
    )
    await authorization_server().revoke_user_sessions(user.id, reason="password_reset")
    await record_audit(
        session,
        action="account.password_reset",
        entity_type="user",
        entity_id=user.id,
        actor_user_id=user.id,
    )
    return user


async def change_email(
    session: AsyncSession,
    *,
    user,
    new_email: str,
    current_password: str,
    settings: Settings | None = None,
) -> IssuedToken:
    """Email change requires reauthentication and verification of the new address."""
    settings = settings or get_settings()
    # Serialise email changes: the password is verified against the locked row and
    # the new address cannot be swapped between the check and the update.
    locked = await repo.user_by_id_for_update(session, user.id)
    if locked is None:
        raise NotFound("Contul nu există.")
    user = locked
    if not verify_password(current_password, user.password_hash, settings):
        raise ValidationError("Parola actuală nu este corectă.")
    normalized = normalize_email(new_email)
    if await repo.find_user_by_email(session, normalized) is not None:
        raise Conflict("Adresa de email nu poate fi folosită.")

    user = await repo.set_user_email(
        session,
        user_id=user.id,
        email_normalized=normalized,
        email_display=new_email.strip(),
        status=UserStatus.PENDING_VERIFICATION.value,
    )
    await authorization_server().revoke_user_sessions(user.id, reason="email_changed")
    # the addresses themselves stay out of the audit row (personal data)
    await record_audit(
        session,
        action="account.email_changed",
        entity_type="user",
        entity_id=user.id,
        actor_user_id=user.id,
        after={"verification_required": True},
    )
    return await issue_email_verification(session, user, settings=settings)


async def email_exists(session: AsyncSession, email: str) -> bool:
    return await repo.email_exists(session, normalize_email(email))
