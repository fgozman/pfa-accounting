"""ANAF connectors.

The HTTPS connector is intentionally inert until a *verified* capability row
records the endpoint, signing requirement, schema/validator version and receipt
behaviour.  Until then, generation/export stays available and automated
submission stays disabled with an explanation.
"""

from __future__ import annotations

from functools import lru_cache

from pfa.anaf.base import AnafTransport, TransportResponse
from pfa.anaf.fake import FakeAnafState, FakeAnafTransport
from pfa.config import get_settings
from pfa.errors import CapabilityUnavailable


class HttpsAnafTransport:
    """Placeholder for the registered ANAF connector.

    Refusing to act without verified channel capabilities is deliberate: guessing
    an endpoint or an idempotency facility would risk duplicate or invalid
    filings.
    """

    def __init__(self, *, unverified_reason: str | None = None) -> None:
        self.unverified_reason = unverified_reason or (
            "Conectorul ANAF nu este înregistrat/verificat în acest mediu. Automatizarea "
            "depunerii este dezactivată; folosiți exportul validat și importați recipisa."
        )

    def _refuse(self) -> TransportResponse:
        raise CapabilityUnavailable(self.unverified_reason)

    def upload_invoice(self, xml: bytes, *, cif: str) -> TransportResponse:  # pragma: no cover
        return self._refuse()

    def invoice_status(self, remote_reference: str, *, cif: str) -> TransportResponse:  # pragma: no cover
        return self._refuse()

    def download_response(self, remote_reference: str, *, cif: str) -> bytes:  # pragma: no cover
        self._refuse()
        raise AssertionError("unreachable")

    def submit_declaration(
        self, xml: bytes, *, form_code: str, cif: str, caller_reference: str
    ) -> TransportResponse:  # pragma: no cover
        return self._refuse()

    def declaration_status(self, remote_reference: str, *, cif: str) -> TransportResponse:  # pragma: no cover
        return self._refuse()


@lru_cache(maxsize=1)
def get_transport() -> AnafTransport:
    settings = get_settings()
    if settings.anaf_driver == "fake":
        return FakeAnafTransport()
    return HttpsAnafTransport()


def reset_transport() -> None:
    get_transport.cache_clear()


__all__ = [
    "AnafTransport",
    "FakeAnafState",
    "FakeAnafTransport",
    "HttpsAnafTransport",
    "TransportResponse",
    "get_transport",
    "reset_transport",
]
