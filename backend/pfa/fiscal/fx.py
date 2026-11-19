"""Foreign-currency conversion.

Invoice, settlement, VAT and annual eligibility conversions may require
different legal dates and rates, so the *purpose* of the conversion selects the
rate.  Missing official rate data blocks affected final calculations unless an
authorized user supplies evidenced data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from pfa.fiscal.money import RATE, ZERO, d, quantize, round_cents

ConversionPurpose = Literal["invoice", "settlement", "vat", "annual_eligibility"]

KNOWN_SOURCES = ("bnm", "ecb", "anaf", "user_evidenced")


class MissingExchangeRateError(LookupError):
    """Raised when no official rate exists for a required conversion."""

    def __init__(self, currency: str, purpose: str, day: date) -> None:
        super().__init__(
            f"no exchange rate for {currency}->RON with purpose {purpose!r} on {day.isoformat()}"
        )
        self.currency = currency
        self.purpose = purpose
        self.day = day


@dataclass(frozen=True, slots=True)
class ExchangeRate:
    currency: str
    quote_currency: str
    rate_date: date
    rate: Decimal
    source: str
    purpose: ConversionPurpose = "invoice"
    source_reference: str | None = None
    is_official: bool = True
    supplied_by_user_id: str | None = None

    def __post_init__(self) -> None:
        if d(self.rate) <= ZERO:
            raise ValueError("exchange rate must be positive")


@dataclass(frozen=True, slots=True)
class Conversion:
    currency: str
    original_amount: Decimal
    rate: Decimal
    rate_date: date
    rate_source: str
    purpose: ConversionPurpose
    ron_amount: Decimal

    def as_dict(self) -> dict:
        return {
            "currency": self.currency,
            "original_amount": str(quantize(self.original_amount)),
            "rate": str(quantize(self.rate, RATE)),
            "rate_date": self.rate_date.isoformat(),
            "rate_source": self.rate_source,
            "purpose": self.purpose,
            "ron_amount": str(round_cents(self.ron_amount)),
        }


def convert(amount: Decimal, rate: Decimal, *, currency: str, purpose: ConversionPurpose, rate_date: date, rate_source: str) -> Conversion:
    ron = round_cents(d(amount) * d(rate))
    return Conversion(
        currency=currency,
        original_amount=d(amount),
        rate=quantize(d(rate), RATE),
        rate_date=rate_date,
        rate_source=rate_source,
        purpose=purpose,
        ron_amount=ron,
    )


def annual_eligibility_rate_date(year: int) -> date:
    """Date used for the annual turnover/eligibility conversion.

    The legal reference is the last published official rate of the year; the
    versioned rule pack owns the exact convention.  Here it is 31 December.
    """
    return date(year, 12, 31)


def select_rate(
    rates: tuple[ExchangeRate, ...],
    *,
    currency: str,
    purpose: ConversionPurpose,
    day: date,
    allow_evidenced_fallback: bool = True,
) -> ExchangeRate:
    candidates = [
        rate
        for rate in rates
        if rate.currency == currency.upper()
        and rate.purpose == purpose
        and rate.rate_date <= day
    ]
    if not candidates:
        if allow_evidenced_fallback:
            # an authorized user may supply evidenced data; it is stored with its
            # own source and is never presented as an official rate
            fallback = [
                rate
                for rate in rates
                if rate.currency == currency.upper()
                and rate.purpose == purpose
                and rate.source == "user_evidenced"
            ]
            if fallback:
                return max(fallback, key=lambda rate: rate.rate_date)
        raise MissingExchangeRateError(currency, purpose, day)
    official = [rate for rate in candidates if rate.is_official]
    pool = official or candidates
    return max(pool, key=lambda rate: (rate.rate_date, rate.source == "bnm"))


def convert_with_rates(
    amount: Decimal,
    *,
    currency: str,
    purpose: ConversionPurpose,
    day: date,
    rates: tuple[ExchangeRate, ...],
) -> Conversion:
    if currency.upper() == "RON":
        return Conversion(
            currency="RON",
            original_amount=d(amount),
            rate=Decimal("1"),
            rate_date=day,
            rate_source="ron",
            purpose=purpose,
            ron_amount=round_cents(amount),
        )
    rate = select_rate(rates, currency=currency, purpose=purpose, day=day)
    return convert(
        amount,
        rate.rate,
        currency=currency.upper(),
        purpose=purpose,
        rate_date=rate.rate_date,
        rate_source=rate.source,
    )
