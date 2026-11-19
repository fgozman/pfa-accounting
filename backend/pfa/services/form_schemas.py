"""Official ANAF form schemas (XSD).

The XSDs published by ANAF are vendored verbatim under
``pfa/static/schemas/anaf`` — see ``SOURCES.md`` there for the source URLs and
hashes.  They are the authority for the XML structure of each declaration.

The rule this module enforces: **an artifact that does not validate against the
official schema for its form is never presented as an official, submittable file**.
Generation today emits a labelled workpaper; the schema check is what proves that
claim instead of asserting it by hand, and it is what will validate the real
mapping once the official field mapping is implemented.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from pathlib import Path

from lxml import etree

from pfa.services.xmlutil import validate_against_xsd

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "static" / "schemas" / "anaf"

#: Documented differences between a vendored XSD and the official structural document
#: that accompanies it.  ANAF's D390 structure (OPANAF 705/2020,
#: ``structura_D390_2020_300424.pdf``) lists ``<cos>`` as **"0-n apariţii"** and
#: ANAF-accepted files omit it for non-rectifying declarations, while the published XSD
#: leaves the element at the default ``minOccurs="1"``.  The structural document is
#: authoritative for cardinality, so ``<cos>`` is optional here; when present it is
#: validated normally.  The vendored file itself is never modified.
_CARDINALITY_RELAXATIONS: dict[str, dict[str, int]] = {"D390": {"cos": 0}}
_XS = "{http://www.w3.org/2001/XMLSchema}"


@dataclass(frozen=True, slots=True)
class FormSchema:
    """One vendored official schema and the form it belongs to."""

    form_code: str
    #: official target namespace, which also identifies the schema version
    namespace: str
    root_element: str
    filename: str
    source_url: str

    @property
    def path(self) -> Path:
        return SCHEMA_DIR / self.filename

    @property
    def schema_id(self) -> str:
        return f"{self.form_code}:{self.namespace.rsplit(':', 1)[-1]}"


SCHEMAS: tuple[FormSchema, ...] = (
    FormSchema(
        form_code="D212",
        namespace="mfp:anaf:dgti:d212:declaratie:v9",
        root_element="d212",
        filename="D212_v9.xsd",
        source_url="https://static.anaf.ro/static/10/Anaf/Declaratii_R/AplicatiiDec/d212_20250113.xsd",
    ),
    FormSchema(
        form_code="D300",
        namespace="mfp:anaf:dgti:d300:declaratie:v12",
        root_element="declaratie300",
        filename="D300_v12.xsd",
        source_url="https://static.anaf.ro/static/10/Anaf/Declaratii_R/AplicatiiDec/d300_v12_11022026.xml",
    ),
    FormSchema(
        form_code="D301",
        namespace="mfp:anaf:dgti:d301:declaratie:v1",
        root_element="declaratie301",
        filename="D301_v5.xsd",
        source_url="https://static.anaf.ro/static/10/Anaf/Declaratii_R/AplicatiiDec/d301_20200130.xsd",
    ),
    FormSchema(
        form_code="D390",
        namespace="mfp:anaf:dgti:d390:declaratie:v3",
        root_element="declaratie390",
        filename="D390_v3.xsd",
        source_url="https://static.anaf.ro/static/10/Anaf/Declaratii_R/AplicatiiDec/d390_12022021.xsd",
    ),
    FormSchema(
        form_code="D394",
        namespace="mfp:anaf:dgti:d394:declaratie:v5",
        root_element="declaratie394",
        filename="D394_v5.xsd",
        source_url="https://static.anaf.ro/static/10/Anaf/Declaratii_R/AplicatiiDec/d394_20250917.xml",
    ),
)

_BY_FORM: dict[str, FormSchema] = {schema.form_code: schema for schema in SCHEMAS}


def schema_for(form_code: str) -> FormSchema | None:
    return _BY_FORM.get(form_code)


def schemas_by_form() -> dict[str, FormSchema]:
    return dict(_BY_FORM)


def schema_bytes(schema: FormSchema) -> bytes:
    return schema.path.read_bytes()


def schema_sha256(schema: FormSchema) -> str:
    return hashlib.sha256(schema_bytes(schema)).hexdigest()


@lru_cache(maxsize=64)
def _schema_bytes_for_validation(form_code: str) -> bytes:
    schema = schema_for(form_code)
    if schema is None:
        return b""
    data = schema_bytes(schema)
    relaxations = _CARDINALITY_RELAXATIONS.get(form_code)
    if not relaxations:
        return data
    tree = etree.parse(BytesIO(data))
    for element in tree.iter(f"{_XS}element"):
        name = element.get("name")
        if name in relaxations:
            element.set("minOccurs", str(relaxations[name]))
    return etree.tostring(tree, xml_declaration=True, encoding="UTF-8")


@lru_cache(maxsize=64)
def _validate(data: bytes, form_code: str) -> tuple[str, ...]:
    schema = schema_for(form_code)
    if schema is None:
        return (f"Nu există o schemă oficială pentru {form_code}.",)
    return tuple(validate_against_xsd(data, _schema_bytes_for_validation(form_code)))


def validate_form(data: bytes, form_code: str) -> list[str]:
    """Official-schema validation errors; empty means the artifact conforms."""
    return list(_validate(data, form_code))
