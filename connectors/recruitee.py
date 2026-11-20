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
from jobs.models import Source

_COMPANY_SLUG_PATTERN = re.compile(r"[A-Za-z0-9_-]+\Z")
_API_ROOT = "https://{company}.recruitee.com/api/offers/"


@register_connector
class RecruiteeConnector(BaseConnector):
    platform_key = "recruitee"
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
                "Recruitee source config must contain a valid company_slug"
            )
        company_slug = company_slug.strip()
        url = _API_ROOT.format(company=quote(company_slug, safe=""))

        response = self.get(url)
        try:
            payload: Any = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ConnectorFetchError(
                "Recruitee returned malformed JSON",
                url=url,
            ) from exc

        if not isinstance(payload, dict) or not isinstance(
            payload.get("offers"), list
        ):
            raise ConnectorFetchError(
                "Recruitee returned an invalid offers response",
                url=url,
            )

        seen_ids: set[str] = set()
        for item in payload["offers"]:
            if not isinstance(item, dict):
                raise ConnectorFetchError(
                    "Recruitee returned an invalid offer record",
                    url=url,
                )
            external_id = _identifier(item)
            if not external_id:
                raise ConnectorFetchError(
                    "Recruitee returned an offer without a valid id",
                    url=url,
                )
            if external_id in seen_ids:
                continue
            seen_ids.add(external_id)

            title = _string_value(item.get("title"))
            if not title:
                raise ConnectorFetchError(
                    f"Recruitee offer {external_id!r} has no title",
                    url=url,
                )

            salary = _object_value(item.get("salary"))
            description = "\n".join(
                value
                for key in ("description", "requirements", "highlight")
                if (value := _string_value(item.get(key)))
            )
            posted_at = _parse_timestamp(
                item.get("published_at") or item.get("created_at")
            )
            salary_min = _decimal_value(salary.get("min"))
            salary_max = _decimal_value(salary.get("max"))

            yield NormalizedJob(
                external_id=external_id,
                title=title,
                company=_string_value(item.get("company_name"))
                or source.name.strip(),
                description=html_to_text(description),
                location=_locations(item),
                source_url=_string_value(item.get("careers_url")),
                apply_url=_string_value(item.get("careers_apply_url")),
                salary_min=salary_min,
                salary_max=salary_max,
                salary_currency=_string_value(salary.get("currency")),
                salary_period=_string_value(salary.get("period")),
                employment_type=_employment_type(
                    item.get("employment_type_code")
                    or item.get("employment_type")
                ),
                is_remote=_remote_value(item),
                posted_at=posted_at,
                raw=dict(item),
                department=_string_value(item.get("department")),
            )


def _identifier(item: dict[str, Any]) -> str:
    for key in ("id", "guid", "slug"):
        value = item.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    return ""


def _locations(item: dict[str, Any]) -> str:
    values: list[str] = []
    primary = _string_value(item.get("location"))
    if primary:
        values.append(primary)

    locations = item.get("locations")
    if isinstance(locations, list):
        for location in locations:
            if isinstance(location, dict):
                name = _string_value(location.get("name")) or ", ".join(
                    part
                    for part in (
                        _string_value(location.get("city")),
                        _string_value(location.get("state")),
                        _string_value(location.get("country")),
                    )
                    if part
                )
            else:
                name = _string_value(location)
            if name:
                values.append(name)
    return ", ".join(dict.fromkeys(values))


def _remote_value(item: dict[str, Any]) -> bool | None:
    if item.get("remote") is True or item.get("hybrid") is True:
        return True
    if item.get("on_site") is True or item.get("remote") is False:
        return False
    return None


def _employment_type(value: object) -> str:
    employment_type = _string_value(value)
    normalized = employment_type.lower().replace("_", " ")
    if "fulltime" in normalized or "full time" in normalized:
        return "full_time"
    if "parttime" in normalized or "part time" in normalized:
        return "part_time"
    if "intern" in normalized:
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
        return parse_datetime(value) or datetime.strptime(
            value, "%Y-%m-%d %H:%M:%S UTC"
        ).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


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
