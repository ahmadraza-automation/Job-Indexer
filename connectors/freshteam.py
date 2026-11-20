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
_API_ROOT = "https://{company}.freshteam.com/hire/widgets/jobs.json"
_JOB_TYPES = {
    1: "contract",
    2: "full_time",
    3: "internship",
}


@register_connector
class FreshteamConnector(BaseConnector):
    platform_key = "freshteam"
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
                "Freshteam source config must contain a valid company_slug"
            )
        company_slug = company_slug.strip()
        url = _API_ROOT.format(company=quote(company_slug, safe=""))

        response = self.get(url)
        try:
            payload: Any = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ConnectorFetchError(
                "Freshteam returned malformed JSON",
                url=url,
            ) from exc

        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            raise ConnectorFetchError(
                "Freshteam returned an invalid jobs response",
                url=url,
            )

        branches = _index_by_id(payload.get("branches"))
        job_roles = _index_by_id(payload.get("job_roles"))
        seen_ids: set[str] = set()
        for item in payload["jobs"]:
            if not isinstance(item, dict):
                raise ConnectorFetchError(
                    "Freshteam returned an invalid job record",
                    url=url,
                )
            external_id = _identifier(item)
            if not external_id:
                raise ConnectorFetchError(
                    "Freshteam returned a job without a valid id",
                    url=url,
                )
            if external_id in seen_ids:
                continue
            seen_ids.add(external_id)

            title = _string_value(item.get("title"))
            if not title:
                raise ConnectorFetchError(
                    f"Freshteam job {external_id!r} has no title",
                    url=url,
                )

            branch = branches.get(_id_key(item.get("branch_id")), {})
            job_role = job_roles.get(_id_key(item.get("job_role_id")), {})
            salary_min, salary_max, salary_currency, salary_period = _salary(
                item.get("ctc_details"),
                branch,
            )
            source_url = _string_value(item.get("url"))

            yield NormalizedJob(
                external_id=external_id,
                title=title,
                company=source.name.strip(),
                description=html_to_text(item.get("description")),
                location=_location(item, branch),
                source_url=source_url,
                apply_url=_string_value(item.get("application_url")) or source_url,
                salary_min=salary_min,
                salary_max=salary_max,
                salary_currency=salary_currency,
                salary_period=salary_period,
                employment_type=_employment_type(item.get("job_type")),
                is_remote=(
                    item.get("remote")
                    if isinstance(item.get("remote"), bool)
                    else None
                ),
                posted_at=_parse_timestamp(item.get("created_at")),
                raw=dict(item),
                department=_string_value(job_role.get("name")),
            )


def _index_by_id(value: object) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list):
        return {}
    return {
        _id_key(item.get("id")): item
        for item in value
        if isinstance(item, dict) and _id_key(item.get("id"))
    }


def _identifier(item: dict[str, Any]) -> str:
    for key in ("id", "unique_id", "url"):
        value = item.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    return ""


def _location(item: dict[str, Any], branch: dict[str, Any]) -> str:
    preferred_locations = _string_value(item.get("preferred_remote_job_locations"))
    if item.get("remote") is True and preferred_locations:
        return ", ".join(
            location.strip()
            for location in preferred_locations.split("|")
            if location.strip()
        )
    location = _string_value(branch.get("location"))
    if location:
        return location
    return ", ".join(
        part
        for part in (
            _string_value(branch.get("city")),
            _string_value(branch.get("state")),
            _string_value(branch.get("country_code")),
        )
        if part
    )


def _employment_type(value: object) -> str:
    if isinstance(value, int) and not isinstance(value, bool):
        return _JOB_TYPES.get(value, "")
    return map_employment_type(value)


def _salary(
    value: object,
    branch: dict[str, Any],
) -> tuple[Decimal | None, Decimal | None, str, str]:
    details = _object_value(value)
    return (
        _decimal_value(details.get("min")),
        _decimal_value(details.get("max")),
        _string_value(details.get("currency"))
        or _string_value(branch.get("currency")),
        _string_value(details.get("period") or details.get("frequency")),
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


def _decimal_value(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return None
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        return None
    return amount if amount.is_finite() else None


def _id_key(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return ""
    return str(value)


def _object_value(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string_value(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""
