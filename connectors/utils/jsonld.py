import json
from collections.abc import Iterator, Mapping
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from connectors.types import NormalizedJob
from connectors.utils.employment import map_employment_type
from connectors.utils.html import html_to_text
from connectors.utils.remote import detect_remote
from connectors.utils.salary import Salary, parse_salary


def extract_job_postings(
    html: str,
    *,
    page_url: str = "",
) -> Iterator[NormalizedJob]:
    """Yield valid schema.org JobPosting records found in an HTML document."""
    if not isinstance(html, str) or not html:
        return
    parser = HTMLParser(html)
    for script in parser.css('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.text())
        except (json.JSONDecodeError, TypeError):
            continue
        for record in _job_records(payload):
            normalized = _normalize_job(record, page_url)
            if normalized is not None:
                yield normalized


def _job_records(payload: Any) -> Iterator[Mapping[str, Any]]:
    if isinstance(payload, list):
        for item in payload:
            yield from _job_records(item)
        return
    if not isinstance(payload, Mapping):
        return
    record_type = payload.get("@type", "")
    if isinstance(record_type, list):
        is_job = "JobPosting" in record_type
    else:
        is_job = record_type == "JobPosting"
    if is_job:
        yield payload
    graph = payload.get("@graph")
    if graph is not None:
        yield from _job_records(graph)


def _normalize_job(
    record: Mapping[str, Any],
    page_url: str,
) -> NormalizedJob | None:
    title = _text(record.get("title"))
    organization = record.get("hiringOrganization")
    company = _text(organization.get("name")) if isinstance(organization, Mapping) else ""
    if not title or not company:
        return None

    location = _job_location(record.get("jobLocation"))
    description = html_to_text(_text(record.get("description")))
    salary = _job_salary(record.get("baseSalary"))
    employment_value = record.get("employmentType")
    employment_type = map_employment_type(employment_value)
    remote_type = str(record.get("jobLocationType", ""))
    is_remote: bool | None = (
        True
        if "TELECOMMUTE" in remote_type.upper()
        else detect_remote(f"{location} {description}")
    )

    raw_apply_url = _text(record.get("applicationUrl"))
    apply_url = urljoin(page_url, raw_apply_url) if raw_apply_url else ""
    source_url = urljoin(page_url, _text(record.get("url")) or page_url)
    identifier = record.get("identifier")
    if isinstance(identifier, Mapping):
        external_id = _text(identifier.get("value"))
    else:
        external_id = _text(identifier)

    return NormalizedJob(
        title=title,
        company=company,
        description=description,
        location=location,
        source_url=source_url,
        apply_url=apply_url,
        external_id=external_id,
        salary_min=salary.minimum,
        salary_max=salary.maximum,
        salary_currency=salary.currency,
        salary_period=salary.period,
        employment_type=employment_type,
        is_remote=is_remote,
        posted_at=_parse_datetime(record.get("datePosted")),
        raw=dict(record),
    )


def _job_location(value: Any) -> str:
    locations = value if isinstance(value, list) else [value]
    for location in locations:
        if isinstance(location, Mapping):
            address = location.get("address", location)
            if isinstance(address, Mapping):
                country = address.get("addressCountry")
                if isinstance(country, Mapping):
                    country = country.get("name") or country.get("@id")
                parts = [
                    _text(address.get(key))
                    for key in ("addressLocality", "addressRegion")
                ]
                parts.append(_text(country))
                result = ", ".join(part for part in parts if part)
                if result:
                    return result
    return ""


def _job_salary(value: Any) -> Salary:
    if isinstance(value, str):
        return parse_salary(value)
    if not isinstance(value, Mapping):
        return Salary()

    currency = _text(value.get("currency")).upper()
    salary_value = value.get("value")
    if isinstance(salary_value, Mapping):
        minimum = _decimal(salary_value.get("minValue"))
        maximum = _decimal(salary_value.get("maxValue"))
        amount = _decimal(salary_value.get("value"))
        minimum = minimum if minimum is not None else amount
        maximum = maximum if maximum is not None else amount
        period = _period(_text(salary_value.get("unitText")))
    else:
        amount = _decimal(salary_value)
        minimum = maximum = amount
        period = _period(_text(value.get("unitText")))
    return Salary(minimum, maximum, currency, period)


def _period(value: str) -> str:
    normalized = value.lower()
    if normalized in {"year", "yearly", "annual", "annually"}:
        return "year"
    if normalized in {"month", "monthly"}:
        return "month"
    if normalized in {"hour", "hourly"}:
        return "hour"
    if normalized in {"week", "weekly"}:
        return "week"
    return ""


def _parse_datetime(value: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    try:
        result = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _text(value: Any) -> str:
    if isinstance(value, (str, int, float)):
        return str(value).strip()
    return ""
