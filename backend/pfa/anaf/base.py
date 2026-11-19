"""ANAF transport interfaces.

Application login and ANAF authorization are two separate trust relationships: an
application account confers no SPV rights, and the user's SPV password is never
requested or stored.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True, slots=True)
class TransportResponse:
    ok: bool
    status_code: int | None
    remote_reference: str | None
    payload: dict = field(default_factory=dict)
    #: transport | fiscal_validation | timeout | rate_limited
    error_class: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    retry_after_seconds: int | None = None
    #: True when the outcome cannot be determined and must be reconciled
    unknown: bool = False


class AnafTransport(Protocol):
    """Transport contract.  No implementation may guess an accepted state."""

    def upload_invoice(self, xml: bytes, *, cif: str) -> TransportResponse: ...

    def invoice_status(self, remote_reference: str, *, cif: str) -> TransportResponse: ...

    def download_response(self, remote_reference: str, *, cif: str) -> bytes: ...

    def submit_declaration(
        self, xml: bytes, *, form_code: str, cif: str, caller_reference: str
    ) -> TransportResponse: ...

    def declaration_status(self, remote_reference: str, *, cif: str) -> TransportResponse: ...
