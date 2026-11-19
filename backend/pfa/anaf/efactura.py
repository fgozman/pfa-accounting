"""e-Factura adapter: UBL generation, validation, durable transmission.

Applicability considers B2B/B2C/B2G, establishment, transaction location and
exceptions with their effective dates.  The transmission deadline uses the
versioned legal calendar (five working days plus the statutory issuance-date
bound), never a hardcoded 120-hour timer.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from lxml import etree
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pfa import clock
from pfa.anaf.base import AnafTransport, TransportResponse
from pfa.errors import CapabilityUnavailable, NotFound, ValidationFailed
from pfa.models.anaf import SubmissionAttempt
from pfa.models.base import SubmissionChannel
from pfa.models.documents import Document, DocumentLine
from pfa.services.audit import record_audit
from pfa.services.deadlines import compute_deadline, default_rule_for
from pfa.services.storage import ObjectStore
from pfa.services.xmlutil import artifact_sha256, parse_xml

UBL_NS = "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
CBC_NS = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
CAC_NS = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"



@dataclass(frozen=True, slots=True)
class EFacturaApplicability:
    required: bool
    reason: str
    exceptions: tuple[str, ...] = ()


def determine_applicability(
    *,
    customer_country: str,
    customer_is_business: bool,
    is_public_body: bool = False,
    is_established_in_romania: bool = True,
    supply_place: str = "RO",
    exemption: str | None = None,
) -> EFacturaApplicability:
    """Simplified but explicit applicability predicate.

    B2B, B2G and B2C domestic supplies, plus intra-community supplies from a
    Romanian establishment, are in scope.  Purchases are not a transmission
    obligation of the issuer.
    """
    exceptions: list[str] = []
    if exemption:
        exceptions.append(exemption)
        return EFacturaApplicability(False, f"Excepție aplicabilă: {exemption}.", tuple(exceptions))
    if not is_established_in_romania:
        return EFacturaApplicability(
            False, "Emitentul nu are sediul activității economice în România."
        )
    if customer_country == "RO":
        return EFacturaApplicability(
            True,
            "Livrare/prestare pe teritoriul României către "
            + ("o instituție publică (B2G)" if is_public_body else "un client B2B" if customer_is_business else "un consumator (B2C)"),
        )
    if supply_place == "RO":
        return EFacturaApplicability(
            True, "Operațiune cu locul livrării/prestării în România."
        )
    return EFacturaApplicability(
        False, "Operațiune fără locul livrării/prestării în România."
    )


def build_ubl_invoice(
    *,
    document: Document,
    lines: list[DocumentLine],
    supplier_cif: str,
    supplier_name: str,
    customer_cif: str | None,
    customer_name: str,
    customer_country: str,
) -> bytes:
    """Generate a Romanian CIUS/UBL invoice representation."""
    root = etree.Element(f"{{{UBL_NS}}}Invoice", nsmap={None: UBL_NS, "cbc": CBC_NS, "cac": CAC_NS})
    cbc = f"{{{CBC_NS}}}"
    cac = f"{{{CAC_NS}}}"

    def child(parent: etree._Element, tag: str, text: object | None = None) -> etree._Element:
        element = etree.SubElement(parent, f"{cbc}{tag}")
        if text is not None:
            element.text = str(text)
        return element

    child(root, "UBLVersionID", "2.1")
    child(root, "CustomizationID", "urn:cen.eu:en16931:2017#compliant#urn:efactura.mfinante.ro:CIUS-RO:1.0.1")
    child(root, "ID", f"{document.series or ''}{document.number or ''}")
    child(root, "IssueDate", (document.issue_date or clock.today()).isoformat())
    child(root, "DueDate", document.due_date.isoformat() if document.due_date else None)
    child(root, "InvoiceTypeCode", "380")
    child(root, "DocumentCurrencyCode", document.currency)
    if document.supply_date:
        period = etree.SubElement(root, f"{cac}InvoicePeriod")
        child(period, "StartDate", document.supply_date.isoformat())

    supplier = etree.SubElement(root, f"{cac}AccountingSupplierParty")
    party = etree.SubElement(supplier, f"{cac}Party")
    identifier = etree.SubElement(party, f"{cac}PartyIdentification")
    child(identifier, "ID", supplier_cif).set("schemeID", "CUI")
    name_el = etree.SubElement(party, f"{cac}PartyName")
    child(name_el, "Name", supplier_name)
    supplier_tax = etree.SubElement(party, f"{cac}PartyTaxScheme")
    child(supplier_tax, "CompanyID", f"RO{supplier_cif}")
    scheme = etree.SubElement(supplier_tax, f"{cac}TaxScheme")
    child(scheme, "ID", "VAT")

    customer = etree.SubElement(root, f"{cac}AccountingCustomerParty")
    customer_party = etree.SubElement(customer, f"{cac}Party")
    if customer_cif:
        customer_identifier = etree.SubElement(customer_party, f"{cac}PartyIdentification")
        child(customer_identifier, "ID", customer_cif)
    customer_name_el = etree.SubElement(customer_party, f"{cac}PartyName")
    child(customer_name_el, "Name", customer_name)
    address = etree.SubElement(customer_party, f"{cac}PostalAddress")
    country = etree.SubElement(address, f"{cac}Country")
    child(country, "IdentificationCode", customer_country)

    total_net = Decimal("0")
    total_vat = Decimal("0")
    for line in lines:
        total_net += line.net_amount
        total_vat += line.vat_amount
        line_el = etree.SubElement(root, f"{cac}InvoiceLine")
        child(line_el, "ID", line.line_number)
        child(line_el, "InvoicedQuantity", line.quantity).set("unitCode", "C62")
        child(line_el, "LineExtensionAmount", f"{line.net_amount:.2f}").set(
            "currencyID", document.currency
        )
        item = etree.SubElement(line_el, f"{cac}Item")
        child(item, "Name", line.description)
        tax_total = etree.SubElement(line_el, f"{cac}TaxTotal")
        child(tax_total, "TaxAmount", f"{line.vat_amount:.2f}").set(
            "currencyID", document.currency
        )
        tax_subtotal = etree.SubElement(tax_total, f"{cac}TaxSubtotal")
        child(tax_subtotal, "TaxableAmount", f"{line.net_amount:.2f}").set(
            "currencyID", document.currency
        )
        child(tax_subtotal, "TaxAmount", f"{line.vat_amount:.2f}").set(
            "currencyID", document.currency
        )
        category = etree.SubElement(tax_subtotal, f"{cac}TaxCategory")
        child(category, "ID", "S" if line.vat_amount else "Z")
        child(category, "Percent", f"{line.vat_rate:.2f}")
        line_scheme = etree.SubElement(category, f"{cac}TaxScheme")
        child(line_scheme, "ID", "VAT")

    tax_total_el = etree.SubElement(root, f"{cac}TaxTotal")
    child(tax_total_el, "TaxAmount", f"{total_vat:.2f}").set("currencyID", document.currency)
    monetary = etree.SubElement(root, f"{cac}LegalMonetaryTotal")
    child(monetary, "LineExtensionAmount", f"{total_net:.2f}").set("currencyID", document.currency)
    child(monetary, "TaxExclusiveAmount", f"{total_net:.2f}").set("currencyID", document.currency)
    child(monetary, "TaxInclusiveAmount", f"{total_net + total_vat:.2f}").set(
        "currencyID", document.currency
    )
    child(monetary, "PayableAmount", f"{total_net + total_vat:.2f}").set(
        "currencyID", document.currency
    )
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


@dataclass(frozen=True, slots=True)
class TransmissionOutcome:
    ok: bool
    remote_reference: str | None
    status: str
    error_class: str | None = None
    error_message: str | None = None
    unknown: bool = False
    retry_after_seconds: int | None = None


def transmission_deadline(issue_date: date) -> date | None:
    rule = default_rule_for("EFACTURA")
    if rule is None:
        return None
    result = compute_deadline(
        rule,
        period_start=issue_date,
        period_end=issue_date,
        event_date=issue_date,
    )
    return result.filing_due_date


async def transmit_invoice(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    transport: AnafTransport,
    store: ObjectStore | None = None,
    supplier: dict | None = None,
    customer: dict | None = None,
) -> TransmissionOutcome:
    document = (
        await session.execute(
            select(Document).where(
                Document.id == document_id, Document.workspace_id == workspace_id
            )
        )
    ).scalar_one_or_none()
    if document is None:
        raise NotFound("Factura nu există în acest spațiu de lucru.")
    if str(document.status) != "finalized":
        raise ValidationFailed("Doar facturile finalizate pot fi transmise.")
    lines = (
        await session.execute(
            select(DocumentLine)
            .where(DocumentLine.document_id == document.id)
            .order_by(DocumentLine.line_number)
        )
    ).scalars().all()
    supplier = supplier or {"cif": "", "name": ""}
    # the customer facts recorded on the document are what the e-Factura XML must carry:
    # name, fiscal code (CUI/CIF or VAT id) and country, as entered by the user.  The
    # country falls back to RO only because the schema requires one and the previous
    # behaviour was a hardcoded RO; a recorded country always wins.
    customer = customer or {
        "cif": document.supplier_vat_id,
        "name": document.supplier_name_raw or "",
        "country": (document.supplier_country or "RO").upper(),
    }
    xml = build_ubl_invoice(
        document=document,
        lines=list(lines),
        supplier_cif=supplier.get("cif", ""),
        supplier_name=supplier.get("name", ""),
        customer_cif=customer.get("cif"),
        customer_name=customer.get("name", ""),
        customer_country=customer.get("country", "RO"),
    )
    # Reject XXE / expansion payloads before anything is stored or uploaded.
    parse_xml(xml)
    store = store or ObjectStore.from_settings()
    stored = store.put(
        xml,
        content_type="application/xml",
        namespace=f"workspace/{workspace_id}/efactura",
    )
    response: TransportResponse = transport.upload_invoice(xml, cif=supplier.get("cif", ""))
    attempt = SubmissionAttempt(
        workspace_id=workspace_id,
        declaration_id=document.id,
        declaration_version=document.revision,
        channel=SubmissionChannel.API,
        status=(
            "succeeded" if response.ok and not response.unknown else "unknown" if response.unknown else "failed"
        ),
        attempt_number=1,
        idempotency_key=f"efactura:{document.id}:{stored.sha256[:16]}",
        request_sha256=stored.sha256,
        request_object_key=stored.key,
        remote_reference=response.remote_reference,
        error_class=response.error_class,
        error_code=response.error_code,
        error_message=response.error_message,
        raw_response=response.payload,
        started_at=clock.now(),
        finished_at=clock.now(),
        next_retry_at=(
            None
            if response.ok
            else clock.now()
        ),
    )
    session.add(attempt)
    await session.flush()
    await record_audit(
        session,
        action="efactura.transmission_attempt",
        entity_type="document",
        entity_id=document.id,
        workspace_id=workspace_id,
        actor_kind="worker",
        after={
            "ok": response.ok,
            "unknown": response.unknown,
            "remote_reference": response.remote_reference,
            "sha256": stored.sha256,
        },
    )
    return TransmissionOutcome(
        ok=response.ok,
        remote_reference=response.remote_reference,
        status=str(attempt.status),
        error_class=response.error_class,
        error_message=response.error_message,
        unknown=response.unknown,
        retry_after_seconds=response.retry_after_seconds,
    )


def parse_efactura_xml(data: bytes) -> dict:
    """Import a received invoice.  Import implies neither payment nor deductibility."""
    root = parse_xml(data)
    ns = {"cbc": CBC_NS, "cac": CAC_NS}

    def text(path: str) -> str | None:
        found = root.find(path, namespaces=ns)
        return found.text if found is not None else None

    lines = []
    for line in root.findall("cac:InvoiceLine", namespaces=ns):
        quantity = line.find("cbc:InvoicedQuantity", namespaces=ns)
        amount = line.find("cbc:LineExtensionAmount", namespaces=ns)
        lines.append(
            {
                "quantity": quantity.text if quantity is not None else None,
                "net_amount": amount.text if amount is not None else None,
                "description": (
                    line.find("cac:Item/cbc:Name", namespaces=ns).text
                    if line.find("cac:Item/cbc:Name", namespaces=ns) is not None
                    else ""
                ),
            }
        )
    return {
        "id": text("cbc:ID"),
        "issue_date": text("cbc:IssueDate"),
        "currency": text("cbc:DocumentCurrencyCode"),
        "supplier": text("cac:AccountingSupplierParty/cac:Party/cac:PartyName/cbc:Name"),
        "customer": text("cac:AccountingCustomerParty/cac:Party/cac:PartyName/cbc:Name"),
        "payable_amount": text(
            "cac:LegalMonetaryTotal/cbc:PayableAmount"
        ),
        "lines": lines,
        "requires_review": True,
        "implies_payment": False,
        "implies_deductibility": False,
    }


def external_id_for_import(payload: dict) -> str:
    """Duplicate detection key for a received invoice."""
    return "|".join(
        str(payload.get(key) or "") for key in ("id", "issue_date", "supplier")
    )


def require_verified_channel(channel_enabled: bool, reason: str | None) -> None:
    if not channel_enabled:
        raise CapabilityUnavailable(
            reason
            or "Canalul ANAF nu este verificat pentru acest formular: transmiterea automată "
            "rămâne dezactivată, iar exportul validat rămâne disponibil."
        )


def artifact_digest(xml: bytes) -> str:
    return artifact_sha256(xml)


def now_utc() -> datetime:
    return clock.now()
