"""Official ANAF schemas are vendored, loadable and enforced.

Requirement IDs: FORMS, API/DATA (artifact integrity).

The point of these tests is the invariant: a file is only "official" if it
validates against the ANAF XSD for its form.  The current generators emit a
labelled workpaper structure, so they must fail schema validation — and the export
path must therefore never mark them submission-ready.
"""

from __future__ import annotations

import pytest
from lxml import etree

from pfa.services import form_schemas

SUPPORTED_FORMS = {"D212", "D300", "D301", "D390", "D394"}

XS = "{http://www.w3.org/2001/XMLSchema}"


def test_every_supported_form_has_a_vendored_official_schema() -> None:
    assert set(form_schemas.schemas_by_form()) >= SUPPORTED_FORMS


def test_vendored_schemas_are_self_contained_and_declare_their_namespace() -> None:
    for form_code in SUPPORTED_FORMS:
        schema = form_schemas.schema_for(form_code)
        assert schema is not None, form_code
        assert schema.path.exists(), schema.path
        document = etree.parse(str(schema.path))
        root = document.getroot()
        # no xs:include / xs:import means the schema validates offline
        assert root.find(f"{XS}import") is None
        assert root.find(f"{XS}include") is None
        assert root.get("targetNamespace") == schema.namespace
        first = root.find(f"{XS}element")
        assert first is not None
        assert first.get("name") == schema.root_element


def test_schema_hashes_are_recorded() -> None:
    sources = (form_schemas.SCHEMA_DIR / "SOURCES.md").read_text(encoding="utf-8")
    for schema in form_schemas.SCHEMAS:
        digest = form_schemas.schema_sha256(schema)
        assert len(digest) == 64
        assert digest in sources, f"{schema.filename} is not recorded in SOURCES.md"


def test_workpaper_roots_do_not_satisfy_the_official_schema() -> None:
    """This is why the export guard downgrades them to a workpaper."""
    cases = {
        "D212": b"<DeclaratieUnica/>",
        "D300": b"<DecontTVA/>",
        "D301": b"<DecontSpecialTVA/>",
        "D390": b"<DeclaratieRecapitulativa/>",
        "D394": b"<DeclaratieInformative/>",
    }
    for form_code, payload in cases.items():
        errors = form_schemas.validate_form(payload, form_code)
        assert errors, f"{form_code} workpaper should not validate"


def test_the_vendored_schema_accepts_a_conforming_document() -> None:
    """The schema really is usable: a minimal official D390 document validates."""
    payload = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<declaratie390 xmlns="mfp:anaf:dgti:d390:declaratie:v3" '
        b'luna="1" an="2025" d_rec="0" nume_declar="Popescu" prenume_declar="Ion" '
        b'functie_declar="Titular" cui="12345678" den="PFA Popescu Ion" '
        b'adresa="Str. Exemplu 1" totalPlata_A="0">'
        b'<rezumat nr_pag="1" nrOPI="0" bazaL="0" bazaT="0" bazaA="100" bazaP="0" '
        b'bazaS="0" bazaR="0" total_baza="100"/>'
        b'<cos tip="A" tara_m1="DE" cod_m1="DE123456789"/>'
        b'<operatie tip="A" tara="DE" denO="Furnizor GmbH" baza="100"/>'
        b"</declaratie390>"
    )
    assert form_schemas.validate_form(payload, "D390") == []


def test_conforming_document_fails_on_a_wrong_country_code() -> None:
    """Values are checked, not only the structure."""
    payload = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<declaratie390 xmlns="mfp:anaf:dgti:d390:declaratie:v3" '
        b'luna="1" an="2025" d_rec="0" nume_declar="Popescu" prenume_declar="Ion" '
        b'functie_declar="Titular" cui="12345678" den="PFA Popescu Ion" '
        b'adresa="Str. Exemplu 1" totalPlata_A="0">'
        b'<rezumat nr_pag="1" nrOPI="0" bazaL="0" bazaT="0" bazaA="100" bazaP="0" '
        b'bazaS="0" bazaR="0" total_baza="100"/>'
        b'<cos tip="A" tara_m1="DE" cod_m1="DE123456789"/>'
        b'<operatie tip="A" tara="ZZ" denO="Furnizor GmbH" baza="100"/>'
        b"</declaratie390>"
    )
    assert form_schemas.validate_form(payload, "D390")


def _d390_snapshot() -> dict:
    return {
        "period_start": "2026-09-01",
        "period_end": "2026-09-30",
        "d_rec": 0,
        "declarant": {
            "nume_declar": "Popescu",
            "prenume_declar": "Ion",
            "functie_declar": "Titular",
            "cui": "12345678",
            "den": "PFA Popescu Ion",
            "adresa": "Str. Exemplu 1, Cluj-Napoca",
            "telefon": "",
        },
        "operations": [
            {"tip": "S", "tara": "DE", "codO": "123456789", "denO": "Furnizor GmbH", "baza": 1234},
            {"tip": "A", "tara": "IT", "codO": "", "denO": "Fornitore SRL", "baza": 500},
        ],
    }


def test_generated_d390_satisfies_the_official_schema() -> None:
    """The D390 generator now emits the official structure, not a workpaper root."""
    from pfa.services.declarations import D390Adapter

    adapter = D390Adapter()
    snapshot = _d390_snapshot()
    outcome = adapter.calculate(snapshot, None)
    artifact = adapter.render(snapshot, outcome, None)

    assert b"declaratie390" in artifact.data
    assert b"mfp:anaf:dgti:d390:declaratie:v3" in artifact.data
    assert form_schemas.validate_form(artifact.data, "D390") == []
    # the rule pack is not professionally reviewed yet, so it is still a workpaper
    assert artifact.is_preview is True
    # the control sum follows the official formula
    assert outcome.calculated_values["totalPlata_A"] == 2 + 1234 + 500
    assert outcome.calculated_values["nrOPI"] == 2


def test_d390_export_of_a_stale_calculation_asks_for_recalculation() -> None:
    """A version calculated before the official mapping must not 500 on export."""
    from pfa.errors import StateTransitionError
    from pfa.services.declarations import CalculationOutcome, D390Adapter

    adapter = D390Adapter()
    legacy = CalculationOutcome(
        calculated_values={"rows": [], "total_base": "0", "total_vat": "0"}, trace=[]
    )
    with pytest.raises(StateTransitionError):
        adapter.render({"period_end": "2026-09-30"}, legacy, None)


def test_d390_cos_is_optional_per_the_official_structure_document() -> None:
    """ANAF's structural document says 0-n; the vendored XSD default (1) is relaxed."""
    from pfa.services.declarations import D390Adapter

    adapter = D390Adapter()
    snapshot = _d390_snapshot()
    artifact = adapter.render(snapshot, adapter.calculate(snapshot, None), None)
    assert b"<cos" not in artifact.data
    assert form_schemas.validate_form(artifact.data, "D390") == []
    # the vendored file is untouched: the relaxation is applied at load time only
    schema = form_schemas.schema_for("D390")
    assert schema is not None
    assert b'name="cos" type="CosType" maxOccurs="unbounded"' in form_schemas.schema_bytes(schema)


def test_missing_input_labels_never_leak_storage_keys() -> None:
    """User-facing messages name the blocking input; the key is an internal detail."""
    from pfa.services.labels import MISSING_INPUT_LABELS_RO, missing_input_label

    assert missing_input_label("counterparty_vat_id_verification")
    assert "_" not in missing_input_label("counterparty_vat_id_verification")
    # a `key:detail` suffix from a per-document block is still resolved
    assert missing_input_label("supplier_vat_id:abc") == MISSING_INPUT_LABELS_RO["supplier_vat_id"]
    # an unmapped key degrades to words, never to a raw identifier
    assert missing_input_label("some_new_fact") == "some new fact"
