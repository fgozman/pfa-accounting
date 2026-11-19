"""Rule-pack registry: lookup by income year and domain."""

from __future__ import annotations

from datetime import date

from pfa.fiscal.packs import ro_2025, ro_2026
from pfa.fiscal.rules import RulePack, RulePackError

PACKS: dict[int, RulePack] = {
    2025: ro_2025.PACK,
    2026: ro_2026.PACK,
}

#: Packs whose parameters were superseded; historical declarations pin these and
#: are never silently rewritten.
SUPERSEDED: dict[str, RulePack] = {}


def get_pack(income_year: int, domain: str = "income") -> RulePack:
    pack = PACKS.get(income_year)
    if pack is None or pack.domain != domain:
        raise RulePackError(
            f"no {domain} rule pack is available for income year {income_year}. "
            "Supported years: " + ", ".join(str(year) for year in sorted(PACKS))
        )
    return pack


def get_pack_by_code(code: str) -> RulePack:
    for pack in PACKS.values():
        if pack.code == code:
            return pack
    if code in SUPERSEDED:
        return SUPERSEDED[code]
    raise RulePackError(f"unknown rule pack {code!r}")


def pack_for_date(day: date, domain: str = "income") -> RulePack:
    for pack in PACKS.values():
        if pack.domain != domain:
            continue
        if pack.valid_from <= day and (pack.valid_to is None or day <= pack.valid_to):
            return pack
    raise RulePackError(f"no {domain} rule pack valid on {day.isoformat()}")


def supported_years() -> list[int]:
    return sorted(PACKS)


__all__ = [
    "PACKS",
    "SUPERSEDED",
    "RulePack",
    "RulePackError",
    "get_pack",
    "get_pack_by_code",
    "pack_for_date",
    "supported_years",
]
