"""Account lifecycle endpoints: registration, e-mail verification, password reset.

Adapts HTTP to :mod:`pfa.services.accounts` only.  Never issues tokens through a
password grant: email/password authentication happens inside an OAuth
authorization transaction on the authorization origin (:mod:`pfa.api.routes.oauth`).
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, Field

from pfa.api.deps import PublicSession
from pfa.api.schemas import EmailAddress
from pfa.config import get_settings
from pfa.errors import RateLimited
from pfa.security.ratelimit import (
    password_reset_limiter,
    verification_resend_ip_limiter,
    verification_resend_limiter,
)
from pfa.services import accounts

router = APIRouter(tags=["auth"])


class RegisterRequest(BaseModel):
    email: EmailAddress
    password: str = Field(min_length=12, max_length=1024)


class VerifyEmailRequest(BaseModel):
    token: str = Field(min_length=16, max_length=200)


class ResendVerificationRequest(BaseModel):
    email: EmailAddress


class PasswordResetRequest(BaseModel):
    email: EmailAddress


class PasswordResetConfirm(BaseModel):
    token: str = Field(min_length=16, max_length=200)
    new_password: str = Field(min_length=12, max_length=1024)


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


@router.post("/auth/register", status_code=status.HTTP_202_ACCEPTED)
async def register(payload: RegisterRequest, request: Request, session: PublicSession) -> dict:
    user, _token = await accounts.register_user(
        session,
        email=payload.email,
        password=payload.password,
        client_ip=_client_ip(request),
    )
    return {
        "status": "verification_sent",
        "user_id": str(user.id),
        "message": (
            "Contul a fost creat. Verificați adresa de email pentru a activa contul."
        ),
    }


@router.post("/auth/verify-email")
async def verify_email(
    payload: VerifyEmailRequest, response: Response, session: PublicSession
) -> dict:
    user = await accounts.verify_email(session, token=payload.token)
    # the request body carried a one-use credential: never cache the exchange
    response.headers["Cache-Control"] = "no-store"
    return {"status": "verified", "user_id": str(user.id)}


@router.post("/auth/verify-email/resend", status_code=status.HTTP_202_ACCEPTED)
async def resend_email_verification(
    payload: ResendVerificationRequest,
    request: Request,
    response: Response,
    session: PublicSession,
) -> dict:
    """Send a fresh activation link when the previous one expired or was lost.

    The answer is always the same whether or not an unverified account exists, so
    the endpoint cannot be used to enumerate accounts.  It is rate limited both per
    account (protecting the mailbox owner from flooding) and per source address.
    """
    settings = get_settings()
    account_key = f"resend:{accounts.normalize_email(payload.email)}"
    ip_key = f"resend-ip:{_client_ip(request) or 'unknown'}"
    wait = max(
        verification_resend_limiter.check(account_key),
        verification_resend_ip_limiter.check(ip_key),
    )
    if wait:
        raise RateLimited(
            "Prea multe solicitări de retrimitere. Reîncercați mai târziu.",
            details={"retry_after_seconds": wait},
        )
    await accounts.resend_email_verification(
        session, email=payload.email, settings=settings
    )
    verification_resend_limiter.record_failure(account_key)
    verification_resend_ip_limiter.record_failure(ip_key)
    response.headers["Cache-Control"] = "no-store"
    return {
        "status": "accepted",
        "message": (
            "Dacă adresa corespunde unui cont neactivat, veți primi un link nou de verificare."
        ),
    }


@router.post("/auth/password-reset/request", status_code=status.HTTP_202_ACCEPTED)
async def request_password_reset(
    payload: PasswordResetRequest,
    request: Request,
    response: Response,
    session: PublicSession,
) -> dict:
    settings = get_settings()
    key = f"reset:{accounts.normalize_email(payload.email)}:{_client_ip(request)}"
    wait = password_reset_limiter.check(key)
    if wait:
        raise RateLimited(
            "Prea multe solicitări de resetare. Reîncercați mai târziu.",
            details={"retry_after_seconds": wait},
        )
    # Always the same answer: no account enumeration.
    await accounts.request_password_reset(session, email=payload.email, settings=settings)
    password_reset_limiter.record_failure(key)
    response.headers["Cache-Control"] = "no-store"
    return {
        "status": "accepted",
        "message": "Dacă adresa există, veți primi un link de resetare.",
    }


@router.post("/auth/password-reset/confirm")
async def confirm_password_reset(payload: PasswordResetConfirm, session: PublicSession) -> dict:
    await accounts.confirm_password_reset(
        session, token=payload.token, new_password=payload.new_password
    )
    return {
        "status": "password_changed",
        "message": "Parola a fost schimbată. Toate sesiunile au fost revocate.",
    }


@router.get("/auth/csrf-not-applicable", include_in_schema=False)
async def csrf_not_applicable() -> dict:
    """The application is cookie-free: there is no CSRF token surface."""
    return {"cookies": False, "credentialed_requests": False}
