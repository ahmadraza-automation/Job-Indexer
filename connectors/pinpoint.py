import json
import re
from collections.abc import Iterator
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
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
_API_ROOT = "https://{company}.pinpointhq.com/postings.json"


@register_connector
class PinpointConnector(BaseConnector):
    platform_key = "pinpoint"
    kind = "ats"

    def discover_jobs(self, source: Source) -> Iterator[NormalizedJob]:
        config = source.config
        company_slug = config.get("company_slug") if isinstance(config, dict) else None
        if (
            not isinstance(company_slug, str)
            or not company_slug.strip()
            or not _COMPANY_SLUG_PATTERN.fullmatch(company_slug.strip())
        ):
            raise ValueError(
                "Pinpoint source config must contain a valid company_slug"
            )
        company_slug = company_slug.strip()
        url = _API_ROOT.format(company=quote(company_slug, safe=""))

        response = self.get(url)
        try:
            payload: Any = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ConnectorFetchError(
                "Pinpoint returned malformed JSON",
                url=url,
            ) from exc

        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise ConnectorFetchError(
                "Pinpoint returned an invalid postings response",
                url=url,
            )

        seen_ids: set[str] = set()
        for item in payload["data"]:
            if not isinstance(item, dict):
                raise ConnectorFetchError(
                    "Pinpoint returned an invalid posting record",
                    url=url,
                )
            external_id = _identifier(item)
            if not external_id:
                raise ConnectorFetchError(
                    "Pinpoint returned a posting without a valid id",
                    url=url,
                )
            if external_id in seen_ids:
                continue
            seen_ids.add(external_id)

            title = _string_value(item.get("title"))
            if not title:
                raise ConnectorFetchError(
                    f"Pinpoint posting {external_id!r} has no title",
                    url=url,
                )

            job = _object_value(item.get("job"))
            department = _object_value(job.get("department")) or _object_value(
                item.get("department")
            )
            location = _object_value(item.get("location"))
            salary_min, salary_max, salary_currency, salary_period = _salary(item)
            source_url = _string_value(item.get("url"))

            yield NormalizedJob(
                external_id=external_id,
                title=title,
                company=source.name.strip(),
                description=html_to_text(item.get("description")),
                location=_string_value(location.get("name")),
                source_url=source_url,
                apply_url=_string_value(item.get("apply_url")) or source_url,
                salary_min=salary_min,
                salary_max=salary_max,
                salary_currency=salary_currency,
                salary_period=salary_period,
                employment_type=_employment_type(
                    item.get("employment_type_text")
                    or item.get("employment_type")
                ),
                is_remote=_remote_value(item.get("workplace_type")),
                posted_at=_parse_timestamp(
                    item.get("published_at") or item.get("created_at")
                ),
                raw=dict(item),
                department=_string_value(department.get("name")),
            )


def _identifier(item: dict[str, Any]) -> str:
    for key in ("id", "uuid", "url"):
        value = item.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    return ""


def _salary(
    item: dict[str, Any],
) -> tuple[Decimal | None, Decimal | None, str, str]:
    if item.get("compensation_visible") is False:
        return None, None, "", ""

    minimum = _decimal_value(item.get("compensation_minimum"))
    maximum = _decimal_value(item.get("compensation_maximum"))
    parsed = parse_salary(item.get("compensation"))
    currency = _string_value(item.get("compensation_currency")) or parsed.currency
    period = _string_value(item.get("compensation_frequency")) or parsed.period
    return (
        minimum if minimum is not None else parsed.minimum,
        maximum if maximum is not None else parsed.maximum,
        currency,
        period,
    )


def _remote_value(value: object) -> bool | None:
    workplace_type = _string_value(value).lower()
    if "remote" in workplace_type:
        return True
    if any(term in workplace_type for term in ("on-site", "onsite", "office")):
        return False
    return None


def _employment_type(value: object) -> str:
    employment_type = _string_value(value)
    normalized = employment_type.lower().replace("_", " ")
    if "full time" in normalized:
        return "full_time"
    if "part time" in normalized:
        return "part_time"
    if "intern" in normalized or "apprentice" in normalized:
        return "internship"
    if "contract" in normalized or "freelance" in normalized:
        return "contract"
    if "temporary" in normalized or "seasonal" in normalized:
        return "temporary"
    return map_employment_type(employment_type)


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


def _decimal_value(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return None
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        return None
    return amount if amount.is_finite() else None


def _object_value(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string_value(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""
