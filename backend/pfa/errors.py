"""Domain errors.

Services raise these; the HTTP layer maps them to RFC 9457 problem details.
Keeping them independent of FastAPI lets the fiscal package be tested standalone.
"""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    code = "domain_error"
    http_status = 400

    def __init__(
        self,
        message: str,
        *,
        field_errors: dict[str, str] | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.field_errors = field_errors or {}
        self.details = details or {}


class NotFound(DomainError):
    code = "not_found"
    http_status = 404


class PermissionDenied(DomainError):
    code = "permission_denied"
    http_status = 403


class Unauthenticated(DomainError):
    code = "unauthenticated"
    http_status = 401


class ValidationFailed(DomainError):
    code = "validation_failed"
    http_status = 422


class Conflict(DomainError):
    code = "conflict"
    http_status = 409


class PreconditionFailed(DomainError):
    code = "precondition_failed"
    http_status = 428


class StaleRevision(Conflict):
    code = "stale_revision"


class IdempotencyMismatch(Conflict):
    code = "idempotency_key_reuse"


class StateTransitionError(Conflict):
    code = "invalid_state_transition"


class CapabilityUnavailable(DomainError):
    """A capability is not available; it must be visible, never silent."""

    code = "capability_unavailable"
    http_status = 409


class BlockingInputs(DomainError):
    """Required information is missing; unknown must never become zero."""

    code = "missing_required_information"
    http_status = 409

    def __init__(self, missing_inputs: list[str], message: str | None = None) -> None:
        super().__init__(
            message or "Informații obligatorii lipsesc.",
            details={"missing_inputs": missing_inputs},
        )
        self.missing_inputs = missing_inputs


class UnsupportedProfile(DomainError):
    code = "unsupported_profile"
    http_status = 422


class RateLimited(DomainError):
    code = "rate_limited"
    http_status = 429


class UpstreamUnavailable(DomainError):
    """A service this one depends on did not answer (or answered unexpectedly).

    Never treated as success: a failed token check denies access and a failed
    session revocation fails the operation that required it.
    """

    code = "upstream_unavailable"
    http_status = 503
