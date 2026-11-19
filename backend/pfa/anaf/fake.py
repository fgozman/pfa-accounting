"""Deterministic fake ANAF service for tests, local development and CI.

It is deliberately stubborn in the same ways the real service is: uploads can
time out, rejections carry fiscal errors, and a successful upload returns an
identifier that is *not* acceptance.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field

from pfa.anaf.base import TransportResponse


@dataclass
class FakeAnafState:
    #: scripted outcomes consumed in order: ok | timeout | reject | error
    scripted: list[str] = field(default_factory=list)
    uploads: list[dict] = field(default_factory=list)
    #: the raw XML of every upload, so a test can assert what was actually sent
    uploaded_xml: list[bytes] = field(default_factory=list)
    statuses: dict[str, str] = field(default_factory=dict)
    submissions: list[dict] = field(default_factory=list)
    #: number of times each idempotency/caller reference was seen
    seen_references: dict[str, int] = field(default_factory=dict)


class FakeAnafTransport:
    """Implements :class:`~pfa.anaf.base.AnafTransport` without any network."""

    def __init__(self, state: FakeAnafState | None = None) -> None:
        self.state = state or FakeAnafState()

    # -- helpers --------------------------------------------------------
    def _next_script(self) -> str:
        if self.state.scripted:
            return self.state.scripted.pop(0)
        return "ok"

    def script(self, *outcomes: str) -> None:
        self.state.scripted.extend(outcomes)

    # -- transport ------------------------------------------------------
    def upload_invoice(self, xml: bytes, *, cif: str) -> TransportResponse:
        outcome = self._next_script()
        digest = hashlib.sha256(xml).hexdigest()
        self.state.uploaded_xml.append(xml)
        if outcome == "timeout":
            # the upload may have succeeded remotely: the caller must reconcile
            self.state.uploads.append({"cif": cif, "sha256": digest, "outcome": "timeout"})
            return TransportResponse(
                ok=False,
                status_code=None,
                remote_reference=None,
                error_class="timeout",
                error_message="ANAF nu a răspuns în intervalul configurat.",
                unknown=True,
            )
        if outcome == "reject":
            reference = f"FAKE-INV-{uuid.uuid4().hex[:12]}"
            self.state.statuses[reference] = "rejected"
            self.state.uploads.append({"cif": cif, "sha256": digest, "outcome": "reject"})
            return TransportResponse(
                ok=True,
                status_code=200,
                remote_reference=reference,
                payload={
                    "stare": "nok",
                    "erori": [{"message": "BR-RO-002: CIF furnizor invalid"}],
                },
            )
        if outcome == "rate_limited":
            return TransportResponse(
                ok=False,
                status_code=429,
                remote_reference=None,
                error_class="rate_limited",
                error_code="too_many_requests",
                error_message="Limita de cereri ANAF a fost atinsă.",
                retry_after_seconds=30,
            )
        reference = f"FAKE-INV-{uuid.uuid4().hex[:12]}"
        self.state.statuses[reference] = "in_prelucrare"
        self.state.uploads.append({"cif": cif, "sha256": digest, "outcome": "ok"})
        return TransportResponse(
            ok=True,
            status_code=200,
            remote_reference=reference,
            payload={"stare": "in prelucrare", "id_descarcare": reference},
        )

    def invoice_status(self, remote_reference: str, *, cif: str) -> TransportResponse:
        status = self.state.statuses.get(remote_reference, "ok")
        return TransportResponse(
            ok=True,
            status_code=200,
            remote_reference=remote_reference,
            payload={"stare": status},
        )

    def download_response(self, remote_reference: str, *, cif: str) -> bytes:
        return (
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f"<Raspuns><id>{remote_reference}</id><stare>ok</stare>"
            f"<cif>{cif}</cif></Raspuns>"
        ).encode()

    def submit_declaration(
        self, xml: bytes, *, form_code: str, cif: str, caller_reference: str
    ) -> TransportResponse:
        seen = self.state.seen_references.get(caller_reference, 0)
        self.state.seen_references[caller_reference] = seen + 1
        if seen > 0:
            # idempotent replay: return the original reference instead of a new one
            original = next(
                (
                    item["remote_reference"]
                    for item in self.state.submissions
                    if item["caller_reference"] == caller_reference
                ),
                None,
            )
            if original:
                return TransportResponse(
                    ok=True,
                    status_code=200,
                    remote_reference=original,
                    payload={"stare": "ok", "replayed": True},
                )
        outcome = self._next_script()
        digest = hashlib.sha256(xml).hexdigest()
        if outcome == "timeout":
            return TransportResponse(
                ok=False,
                status_code=None,
                remote_reference=None,
                error_class="timeout",
                error_message="Timp de așteptare depășit la încărcare.",
                unknown=True,
            )
        reference = f"FAKE-{form_code}-{uuid.uuid4().hex[:12]}"
        self.state.submissions.append(
            {
                "form_code": form_code,
                "cif": cif,
                "caller_reference": caller_reference,
                "remote_reference": reference,
                "sha256": digest,
                "outcome": outcome,
            }
        )
        if outcome == "reject":
            return TransportResponse(
                ok=True,
                status_code=200,
                remote_reference=reference,
                payload={
                    "stare": "nok",
                    "formular": form_code,
                    "cif": cif,
                    "erori": [{"message": "Eroare de validare fiscală (fixture)"}],
                },
            )
        return TransportResponse(
            ok=True,
            status_code=200,
            remote_reference=reference,
            payload={"stare": "ok", "formular": form_code, "cif": cif},
        )

    def declaration_status(self, remote_reference: str, *, cif: str) -> TransportResponse:
        return TransportResponse(
            ok=True,
            status_code=200,
            remote_reference=remote_reference,
            payload={"stare": "ok"},
        )
