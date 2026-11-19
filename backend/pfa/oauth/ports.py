"""What the authorization server needs from its host, as narrow interfaces.

The server does not know how accounts are stored or where audit records go.  The
host configures one implementation of each port at start-up
(:func:`pfa.wiring.wire_in_process` in the monolith); a standalone deployment
would configure HTTP-backed implementations instead.

An unconfigured port raises instead of silently doing nothing: skipping a
credential check or an audit record must never be the default.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol

from pfa.errors import DomainError

#: fixed, non-reflective reasons a login can be refused for
LOGIN_REJECTION_REASONS = ("invalid_credentials", "email_not_verified", "too_many_attempts")


class LoginRejected(DomainError):
    """The identity provider refused the credentials submitted on the login page.

    ``reason`` is one of :data:`LOGIN_REJECTION_REASONS`: a fixed code the login
    page can render without reflecting any request data.
    """

    code = "login_rejected"
    http_status = 401

    def __init__(self, reason: str, message: str, *, http_status: int | None = None) -> None:
        if reason not in LOGIN_REJECTION_REASONS:
            raise ValueError(f"unknown login rejection reason {reason!r}")
        super().__init__(message, details={"reason": reason})
        self.reason = reason
        if http_status is not None:
            self.http_status = http_status


@dataclass(frozen=True, slots=True)
class Subject:
    """An authenticated, active account as seen by the authorization server."""

    #: stable subject identifier; becomes the JWT ``sub``
    id: uuid.UUID


class IdentityProvider(Protocol):
    async def authenticate(
        self,
        *,
        email: str,
        password: str,
        client_ip: str | None,
        attempt_key: str,
    ) -> Subject:
        """Verify the credentials of an **active** account.

        Raises :class:`LoginRejected` for every credential-related refusal (wrong
        password, unverified or inactive account, rate limit).  ``attempt_key``
        scopes rate limiting to one authorization transaction.
        """
        ...


class AuditSink(Protocol):
    async def record(
        self,
        *,
        action: str,
        entity_type: str,
        entity_id: uuid.UUID,
        actor_user_id: uuid.UUID,
    ) -> None:
        """Record a security event.

        The sink owns its storage and unit of work: it can share no transaction with
        the server, whose tables live in another database.
        """
        ...


_identity_provider: IdentityProvider | None = None
_audit_sink: AuditSink | None = None


class PortNotConfigured(RuntimeError):
    pass


def configure(*, identity_provider: IdentityProvider, audit_sink: AuditSink) -> None:
    global _identity_provider, _audit_sink
    _identity_provider = identity_provider
    _audit_sink = audit_sink


def identity_provider() -> IdentityProvider:
    if _identity_provider is None:
        raise PortNotConfigured("pfa.oauth: no IdentityProvider configured (see pfa.wiring)")
    return _identity_provider


def audit_sink() -> AuditSink:
    if _audit_sink is None:
        raise PortNotConfigured("pfa.oauth: no AuditSink configured (see pfa.wiring)")
    return _audit_sink
