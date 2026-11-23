"""HireHive public jobs API.

Uses the public ``/api/v2/jobs`` JSON endpoint and its ``meta``/``links``
pagination fields, observed on https://amcsgroup.hirehive.com. TODO: verify
salary fields against a live board that publishes compensation values.
"""

import json
import re
from collections.abc import Iterator, Mapping
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urljoin, urlsplit

from django.utils.dateparse import parse_datetime

from connectors.base import BaseConnector, ConnectorFetchError
from connectors.registry import register_connector
from connectors.types import NormalizedJob
from connectors.utils.employment import map_employment_type
from connectors.utils.html import html_to_text
from connectors.utils.remote import detect_remote
from connectors.utils.salary import Salary, parse_salary
from jobs.models import Source

_SLUG_PATTERN = re.compile(r"[A-Za-z0-9_-]+\Z")
_PAGE_SIZE = 100
_MAX_PAGES = 100


@register_connector
class HireHiveConnector(BaseConnector):
    platform_key = "hirehive"
    kind = "ats"

    def discover_jobs(self, source: Source) -> Iterator[NormalizedJob]:
        base_url, _ = _config(source)
        endpoint = f"{base_url}/api/v2/jobs"
        current_url = endpoint
        params: dict[str, int] | None = {"page": 1, "page_size": _PAGE_SIZE}
        visited: set[str] = set()
        seen_ids: set[str] = set()

        for _ in range(_MAX_PAGES):
            request_key = current_url
            if params:
                request_key = f"{current_url}?page={params['page']}&page_size={params['page_size']}"
            if request_key in visited:
                raise ConnectorFetchError(
                    "HireHive returned a pagination loop",
                    url=current_url,
                )
            visited.add(request_key)
            response = self.get(current_url, params=params)
            params = None
            try:
                payload: Any = response.json()
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
                raise ConnectorFetchError(
                    "HireHive returned malformed JSON",
                    url=current_url,
                ) from exc

            if not isinstance(payload, dict) or not isinstance(
                payload.get("items"), list
            ):
                raise ConnectorFetchError(
                    "HireHive returned an invalid jobs response",
                    url=current_url,
                )
            meta = payload.get("meta")
            links = payload.get("links")
            if meta is not None and not isinstance(meta, dict):
                raise ConnectorFetchError(
                    "HireHive returned invalid pagination metadata",
                    url=current_url,
                )
            if links is not None and not isinstance(links, dict):
                raise ConnectorFetchError(
                    "HireHive returned invalid pagination links",
                    url=current_url,
                )

            for item in payload["items"]:
                if not isinstance(item, dict):
                    raise ConnectorFetchError(
                        "HireHive returned an invalid job record",
                        url=current_url,
                    )
                job = _normalize(item, source, current_url)
                if not job.external_id:
                    raise ConnectorFetchError(
                        "HireHive returned a job without a stable id",
                        url=current_url,
                    )
                if not job.title:
                    raise ConnectorFetchError(
                        f"HireHive job {job.external_id!r} has no title",
                        url=current_url,
                    )
                if job.external_id not in seen_ids:
                    seen_ids.add(job.external_id)
                    yield job

            next_value = links.get("next") if isinstance(links, dict) else None
            has_next = (
                meta.get("has_next_page") is True
                if isinstance(meta, dict)
                else bool(next_value)
            )
            if not has_next:
                return

            if isinstance(next_value, str) and next_value.strip():
                next_url = urljoin(current_url, next_value.strip())
                parsed_next = urlsplit(next_url)
                parsed_base = urlsplit(base_url)
                if (
                    parsed_next.scheme != "https"
                    or parsed_next.hostname != parsed_base.hostname
                    or parsed_next.username
                    or parsed_next.password
                ):
                    raise ConnectorFetchError(
                        "HireHive returned an invalid pagination URL",
                        url=current_url,
                    )
                current_url = next_url
            elif isinstance(meta, dict) and isinstance(meta.get("page"), int):
                current_url = endpoint
                params = {
                    "page": meta["page"] + 1,
                    "page_size": _PAGE_SIZE,
                }
            else:
                raise ConnectorFetchError(
                    "HireHive indicated another page without a page reference",
                    url=current_url,
                )

        raise ConnectorFetchError(
            "HireHive exceeded the public jobs page limit",
            url=current_url,
        )


def _config(source: Source) -> tuple[str, str]:
    config = source.config if isinstance(source.config, dict) else {}
    base_url = _string(config.get("base_url")).rstrip("/")
    slug = _string(config.get("company_slug"))
    parsed = urlsplit(base_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("HireHive source config must contain a valid HTTPS base_url")
    if not slug or not _SLUG_PATTERN.fullmatch(slug):
        raise ValueError("HireHive source config must contain a valid company_slug")
    return base_url, slug


def _normalize(
    item: dict[str, Any],
    source: Source,
    page_url: str,
) -> NormalizedJob:
    identifier = _string(item.get("id"))
    title = _string(item.get("title"))
    country = item.get("country")
    country_name = (
        _string(country.get("name")) if isinstance(country, Mapping) else ""
    )
    location = ", ".join(
        value
        for value in (_string(item.get("location")), country_name)
        if value
    )
    description = item.get("description")
    description_html = (
        _string(description.get("html"))
        if isinstance(description, Mapping)
        else _string(description)
    )
    category = item.get("category")
    job_type = item.get("type")
    category_name = (
        _string(category.get("name")) if isinstance(category, Mapping) else ""
    )
    type_name = (
        _string(job_type.get("name")) if isinstance(job_type, Mapping) else ""
    )
    hosted_url = _string(item.get("hosted_url"))
    source_url = urljoin(page_url, hosted_url) if hosted_url else page_url
    salary = _salary(item)
    return NormalizedJob(
        external_id=identifier,
        title=title,
        company=_string(item.get("company")) or source.name.strip(),
        location=location,
        description=html_to_text(description_html),
        source_url=source_url,
        apply_url=_string(item.get("application_url")) or source_url,
        salary_min=salary.minimum,
        salary_max=salary.maximum,
        salary_currency=salary.currency,
        salary_period=salary.period,
        employment_type=map_employment_type(type_name),
        is_remote=detect_remote(location),
        posted_at=_timestamp(item.get("published_date")),
        updated_at=_timestamp(item.get("updated_date")),
        department=category_name,
        raw=dict(item),
    )


def _salary(item: dict[str, Any]) -> Salary:
    value = item.get("salary")
    if isinstance(value, str):
        return parse_salary(value)
    candidates: list[Mapping[str, Any]] = []
    if isinstance(value, Mapping):
        candidates.append(value)
    tiers = item.get("compensation_tiers")
    if isinstance(tiers, list):
        candidates.extend(tier for tier in tiers if isinstance(tier, Mapping))
    for candidate in candidates:
        minimum = _decimal(candidate.get("min_value") or candidate.get("min"))
        maximum = _decimal(candidate.get("max_value") or candidate.get("max"))
        if minimum is None and maximum is None:
            continue
        interval = _string(candidate.get("interval")).lower()
        period = {
            "annual": "year",
            "year": "year",
            "yearly": "year",
            "monthly": "month",
            "month": "month",
            "hourly": "hour",
            "hour": "hour",
            "weekly": "week",
            "week": "week",
        }.get(interval, "")
        return Salary(
            minimum=minimum,
            maximum=maximum,
            currency=_string(
                candidate.get("currency_code") or candidate.get("currency")
            ).upper(),
            period=period,
        )
    return Salary()


def _timestamp(value: object) -> datetime | None:
    text = _string(value)
    if not text:
        return None
    parsed = parse_datetime(text)
    if parsed is None:
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return amount if amount.is_finite() else None


def _string(value: Any) -> str:
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        return str(value).strip()
    return ""
