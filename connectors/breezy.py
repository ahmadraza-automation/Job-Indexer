import json
import re
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from django.utils.dateparse import parse_datetime

from connectors.base import BaseConnector, ConnectorFetchError
from connectors.registry import register_connector
from connectors.types import NormalizedJob
from connectors.utils.employment import map_employment_type
from connectors.utils.html import html_to_text
from connectors.utils.salary import parse_salary
from jobs.models import Source

_COMPANY_SLUG_PATTERN = re.compile(r"[A-Za-z0-9_-]+\Z")
_API_ROOT = "https://{company}.breezy.hr/json"


@register_connector
class BreezyConnector(BaseConnector):
    platform_key = "breezy"
    kind = "ats"

    def discover_jobs(self, source: Source) -> Iterator[NormalizedJob]:
        config = source.config
        company_slug = config.get("company_slug") if isinstance(config, dict) else None
        if (
            not isinstance(company_slug, str)
            or not company_slug.strip()
            or not _COMPANY_SLUG_PATTERN.fullmatch(company_slug.strip())
        ):
            raise ValueError("Breezy source config must contain a valid company_slug")
        company_slug = company_slug.strip()
        url = _API_ROOT.format(company=quote(company_slug, safe=""))

        response = self.get(url)
        try:
            payload: Any = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ConnectorFetchError(
                "Breezy returned malformed JSON",
                url=url,
            ) from exc

        if not isinstance(payload, list):
            raise ConnectorFetchError(
                "Breezy returned an invalid postings response",
                url=url,
            )

        seen_ids: set[str] = set()
        for item in payload:
            if not isinstance(item, dict):
                raise ConnectorFetchError(
                    "Breezy returned an invalid posting record",
                    url=url,
                )
            external_id = _identifier(item)
            if not external_id:
                raise ConnectorFetchError(
                    "Breezy returned a posting without a valid id",
                    url=url,
                )
            if external_id in seen_ids:
                continue
            seen_ids.add(external_id)

            title = _string_value(item.get("name")) or _string_value(
                item.get("title")
            )
            if not title:
                raise ConnectorFetchError(
                    f"Breezy posting {external_id!r} has no title",
                    url=url,
                )

            job_type = item.get("type")
            if isinstance(job_type, dict):
                employment_type = job_type.get("name") or job_type.get("id")
            else:
                employment_type = job_type
            location_data = _object_value(item.get("location"))
            locations = item.get("locations")
            location = _locations(locations)
            if not location:
                location = _location_text(location_data)
            remote = location_data.get("is_remote")
            if not isinstance(remote, bool):
                remote = item.get("remote")
            if not isinstance(remote, bool):
                remote = None

            salary = parse_salary(item.get("salary"))
            source_url = _string_value(item.get("url"))
            yield NormalizedJob(
                external_id=external_id,
                title=title,
                company=source.name.strip(),
                description=html_to_text(item.get("description")),
                location=location,
                source_url=source_url,
                apply_url=_string_value(item.get("application_url")) or source_url,
                salary_min=salary.minimum,
                salary_max=salary.maximum,
                salary_currency=salary.currency,
                salary_period=salary.period,
                employment_type=map_employment_type(employment_type),
                is_remote=remote,
                posted_at=_parse_timestamp(
                    item.get("published_date") or item.get("creation_date")
                ),
                raw=dict(item),
                department=_string_value(item.get("department")),
            )


def _identifier(item: dict[str, Any]) -> str:
    for key in ("id", "friendly_id", "url"):
        value = item.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    return ""


def _locations(value: object) -> str:
    if not isinstance(value, list):
        return ""
    names = [
        _location_text(item)
        for item in value
        if isinstance(item, dict)
    ]
    return ", ".join(dict.fromkeys(name for name in names if name))


def _location_text(value: dict[str, Any]) -> str:
    name = _string_value(value.get("name"))
    if name:
        return name
    country = _object_value(value.get("country"))
    state = _object_value(value.get("state"))
    return ", ".join(
        part
        for part in (
            _string_value(value.get("city")),
            _string_value(state.get("name")),
            _string_value(country.get("name")),
        )
        if part
    )


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = parse_datetime(value)
    except ValueError:
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _object_value(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string_value(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""
