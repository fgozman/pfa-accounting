"""Safe XML handling for e-Factura and official form artifacts.

External entities, DTD loading and network access are disabled, so XXE and XML
expansion attacks are rejected before parsing.  Size limits are enforced.
"""

from __future__ import annotations

import hashlib
from io import BytesIO

from lxml import etree

from pfa.errors import ValidationFailed

MAX_XML_BYTES = 10 * 1024 * 1024

_SAFE_PARSER = etree.XMLParser(
    resolve_entities=False,
    no_network=True,
    load_dtd=False,
    dtd_validation=False,
    huge_tree=False,
    remove_blank_text=False,
)


def parse_xml(data: bytes, *, max_bytes: int = MAX_XML_BYTES) -> etree._Element:
    if len(data) > max_bytes:
        raise ValidationFailed(
            f"Documentul XML depășește limita de {max_bytes} octeți.",
            field_errors={"xml": "too_large"},
        )
    head = data[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in head:
        raise ValidationFailed(
            "Documentul XML conține declarații de tip doctype/entity, care nu sunt acceptate.",
            field_errors={"xml": "doctype_rejected"},
        )
    try:
        return etree.parse(BytesIO(data), parser=_SAFE_PARSER).getroot()
    except etree.XMLSyntaxError as exc:
        raise ValidationFailed(
            "Documentul XML nu este valid sintactic.",
            field_errors={"xml": "syntax_error"},
            details={"detail": str(exc)},
        ) from exc


def canonical_c14n(element: etree._Element) -> bytes:
    return etree.tostring(element, method="c14n", exclusive=False, with_comments=False)


def stamp_workpaper(data: bytes, *, reason: str) -> bytes:
    """Mark an artifact as a non-submittable workpaper and record why.

    Used when a file does not satisfy the official form schema: the file itself
    must say it is not for filing, never only the surrounding record.
    """
    root = parse_xml(data)
    root.set("preview", "true")
    notice = root.find("Nota")
    if notice is None:
        notice = etree.Element("Nota")
        root.insert(0, notice)
    notice.text = reason
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def artifact_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_against_xsd(data: bytes, xsd: bytes) -> list[str]:
    """Return a list of official-schema validation errors (empty when valid)."""
    try:
        schema_doc = etree.parse(BytesIO(xsd), parser=_SAFE_PARSER)
        schema = etree.XMLSchema(schema_doc)
    except etree.XMLSchemaParseError as exc:  # pragma: no cover - schema problem
        raise ValidationFailed(f"Schema oficială nu poate fi încărcată: {exc}") from exc
    try:
        document = etree.parse(BytesIO(data), parser=_SAFE_PARSER)
    except etree.XMLSyntaxError as exc:
        return [f"syntax: {exc}"]
    if schema.validate(document):
        return []
    return [f"{error.line}: {error.message}" for error in schema.error_log]
