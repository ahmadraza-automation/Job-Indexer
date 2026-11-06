import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True, slots=True)
class Salary:
    minimum: Decimal | None = None
    maximum: Decimal | None = None
    currency: str = ""
    period: str = ""


_CURRENCIES = {
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
    "₹": "INR",
    "USD": "USD",
    "CAD": "CAD",
    "AUD": "AUD",
    "EUR": "EUR",
    "GBP": "GBP",
    "JPY": "JPY",
    "INR": "INR",
}
_AMOUNT = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s*[kKmM]?"
_CURRENCY_PREFIX = r"(?:(?:USD|CAD|AUD|EUR|GBP|JPY|INR)\s*|[$€£¥₹]\s*)?"
_RANGE = re.compile(
    rf"{_CURRENCY_PREFIX}(?P<first>{_AMOUNT})\s*(?:-|–|—|to)\s*"
    rf"{_CURRENCY_PREFIX}(?P<second>{_AMOUNT})",
    re.IGNORECASE,
)
_SINGLE = re.compile(_AMOUNT, re.IGNORECASE)


def parse_salary(value: object) -> Salary:
    """Parse common salary text into amounts, ISO currency, and pay period."""
    if not isinstance(value, str) or not value.strip():
        return Salary()

    text = value.strip()
    upper = text.upper()
    currency = next(
        (code for token, code in _CURRENCIES.items() if token in upper),
        "",
    )
    if not currency:
        currency = next(
            (code for token, code in _CURRENCIES.items() if token in text),
            "",
        )

    period = ""
    if re.search(
        r"\b(?:per\s+year|a\s+year|annually|annual|yearly)\b|/(?:yr|year)\b",
        upper,
        re.IGNORECASE,
    ):
        period = "year"
    elif re.search(
        r"\b(?:per\s+month|a\s+month|monthly)\b|/(?:mo|month)\b",
        upper,
        re.IGNORECASE,
    ):
        period = "month"
    elif re.search(
        r"\b(?:per\s+hour|an?\s+hour|hourly)\b|/(?:hr|hour)\b",
        upper,
        re.IGNORECASE,
    ):
        period = "hour"
    elif re.search(r"\b(?:per\s+week|weekly)\b|/week\b", upper, re.IGNORECASE):
        period = "week"

    range_match = _RANGE.search(text)
    if range_match:
        return Salary(
            minimum=_parse_amount(range_match.group("first")),
            maximum=_parse_amount(range_match.group("second")),
            currency=currency,
            period=period,
        )

    amount_match = _SINGLE.search(text)
    amount = _parse_amount(amount_match.group(0)) if amount_match else None
    return Salary(minimum=amount, maximum=amount, currency=currency, period=period)


def _parse_amount(value: str) -> Decimal | None:
    cleaned = re.sub(r"\s+", "", value).replace(",", "")
    multiplier = Decimal(1)
    if cleaned[-1:].lower() == "k":
        multiplier = Decimal(1000)
        cleaned = cleaned[:-1]
    elif cleaned[-1:].lower() == "m":
        multiplier = Decimal(1_000_000)
        cleaned = cleaned[:-1]
    try:
        return Decimal(cleaned) * multiplier
    except InvalidOperation:
        return None
