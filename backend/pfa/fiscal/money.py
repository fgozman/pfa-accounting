"""Exact decimal arithmetic for fiscal calculations.

Everything uses :class:`decimal.Decimal`.  Binary floating point is never used
for monetary values, and rounding is explicit at each step: intermediate values
keep six decimals, statutory amounts are rounded to the legal precision
(two decimals, or whole RON where a form requires it).
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal, InvalidOperation

#: intermediate monetary precision used in PostgreSQL NUMERIC(20, 6)
PRECISION = Decimal("0.000001")
CENT = Decimal("0.01")
RON = Decimal("1")

ZERO = Decimal("0")
ONE = Decimal("1")
HUNDRED = Decimal("100")


class MoneyError(ValueError):
    pass


def d(value: Decimal | int | str | float | None) -> Decimal:
    """Coerce to Decimal without binary float artefacts.

    ``float`` input is accepted only through its string representation, which
    keeps 0.1 exact as "0.1" rather than 0.1000000000000000055511151231257827.
    """
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise MoneyError("booleans are not monetary values")
    if isinstance(value, float):
        return Decimal(repr(value))
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:  # pragma: no cover - defensive
        raise MoneyError(f"cannot convert {value!r} to Decimal") from exc


def quantize(value: Decimal, exponent: Decimal = PRECISION) -> Decimal:
    return d(value).quantize(exponent, rounding=ROUND_HALF_UP)


def round_cents(value: Decimal) -> Decimal:
    """Round to the statutory two-decimal precision (ROUND_HALF_UP)."""
    return quantize(value, CENT)


def round_ron(value: Decimal) -> Decimal:
    """Whole-RON rounding, used where a form requires an integer amount."""
    return quantize(value, RON)


def round_up_ron(value: Decimal) -> Decimal:
    return d(value).quantize(RON, rounding=ROUND_CEILING)


def percent_of(base: Decimal, rate_percent: Decimal) -> Decimal:
    """``rate_percent`` is an exact percentage, e.g. Decimal("21") for 21%."""
    return quantize(d(base) * d(rate_percent) / HUNDRED)


def apply_rate(base: Decimal, rate: Decimal) -> Decimal:
    """``rate`` is a fraction, e.g. Decimal("0.21") for 21%."""
    return quantize(d(base) * d(rate))


def sum_decimals(values: Iterable[Decimal]) -> Decimal:
    total = ZERO
    for value in values:
        total += d(value)
    return quantize(total)


def max_zero(value: Decimal) -> Decimal:
    return value if d(value) > ZERO else ZERO


def allocate_proportionally(total: Decimal, weights: list[Decimal]) -> list[Decimal]:
    """Split ``total`` across ``weights`` without losing or inventing a cent."""
    total = quantize(total)
    weight_sum = sum_decimals(weights)
    if weight_sum == ZERO:
        if total == ZERO:
            return [ZERO for _ in weights]
        raise MoneyError("cannot allocate a non-zero total across zero weights")
    shares = [round_cents(total * d(weight) / weight_sum) for weight in weights]
    drift = round_cents(total - sum_decimals(shares))
    if drift != ZERO:
        # assign the rounding residue to the largest share, deterministically
        index = max(range(len(shares)), key=lambda i: (shares[i], -i))
        shares[index] = round_cents(shares[index] + drift)
    return shares


def as_str(value: Decimal) -> str:
    """Canonical wire representation: decimal string, never a float."""
    return format(quantize(value), "f")
