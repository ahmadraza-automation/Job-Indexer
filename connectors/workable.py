import json
import re
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlsplit

from django.utils.dateparse import parse_datetime

from connectors.base import BaseConnector, ConnectorFetchError
from connectors.registry import register_connector
from connectors.types import NormalizedJob
from connectors.utils.employment import map_employment_type
from connectors.utils.html import html_to_text
from jobs.models import Source

_SUBDOMAIN_PATTERN = re.compile(r"[A-Za-z0-9_-]+\Z")


@register_connector
class WorkableConnector(BaseConnector):
    platform_key = "workable"
    kind = "ats"

    def discover_jobs(self, source: Source) -> Iterator[NormalizedJob]:
        config = source.config
        subdomain = config.get("subdomain") if isinstance(config, dict) else None
        if (
            not isinstance(subdomain, str)
            or not subdomain.strip()
            or not _SUBDOMAIN_PATTERN.fullmatch(subdomain.strip())
        ):
            raise ValueError("Workable source config must contain a valid subdomain")
        subdomain = subdomain.strip()

        base_url = source.base_url.strip().rstrip("/")
        parsed_base_url = urlsplit(base_url)
        if (
            parsed_base_url.scheme not in {"http", "https"}
            or not parsed_base_url.netloc
            or parsed_base_url.username
            or parsed_base_url.password
            or parsed_base_url.query
            or parsed_base_url.fragment
        ):
            raise ValueError("Workable source base_url must be a valid API base URL")

        url = f"{base_url}/{quote(subdomain, safe='')}"
        response = self.get(url, params={"details": "true"})
        try:
            payload: Any = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ConnectorFetchError(
                "Workable returned malformed JSON",
                url=url,
            ) from exc

        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            raise ConnectorFetchError(
                "Workable returned an invalid jobs response",
                url=url,
            )

        seen_ids: set[str] = set()
        for item in payload["jobs"]:
            if not isinstance(item, dict):
                raise ConnectorFetchError(
                    "Workable returned an invalid job record",
                    url=url,
                )

            title = _string_value(item.get("title"))
            if not title:
                raise ConnectorFetchError(
                    "Workable returned a job without a valid title",
                    url=url,
                )

            job_url = _string_value(item.get("url"))
            raw_id = item.get("id")
            if isinstance(raw_id, bool) or not isinstance(raw_id, (str, int)):
                external_id = ""
            else:
                external_id = str(raw_id).strip()
            if not external_id:
                external_id = _string_value(item.get("shortcode"))
            if not external_id:
                external_id = job_url
            if not external_id:
                raise ConnectorFetchError(
                    f"Workable job {title!r} has no valid id, shortcode, or URL",
                    url=url,
                )
            if external_id in seen_ids:
                continue
            seen_ids.add(external_id)

            location_data = item.get("location")
            location = _location_text(location_data)
            locations = item.get("locations")
            all_locations = [_location_text(value) for value in locations] \
                if isinstance(locations, list) else []
            all_locations = list(dict.fromkeys(value for value in all_locations if value))
            if all_locations:
                location = ", ".join(
                    dict.fromkeys(([location] if location else []) + all_locations)
                )

            description_html = _string_value(item.get("full_description")) or (
                _string_value(item.get("description"))
            )
            description_plain = html_to_text(description_html)

            telecommuting = (
                location_data.get("telecommuting")
                if isinstance(location_data, dict)
                else None
            )
            if not isinstance(telecommuting, bool):
                workplace_type = _string_value(
                    location_data.get("workplace_type")
                ).lower() if isinstance(location_data, dict) else ""
                telecommuting = True if workplace_type == "remote" else None

            yield NormalizedJob(
                external_id=external_id,
                title=title,
                company=source.name.strip(),
                location=location,
                department=_string_value(item.get("department")),
                description=description_plain,
                employment_type=map_employment_type(item.get("employment_type")),
                is_remote=telecommuting,
                apply_url=_string_value(item.get("application_url")),
                source_url=job_url or _string_value(item.get("shortlink")),
                posted_at=_parse_created_at(item.get("created_at")),
                raw=dict(item),
            )


def _location_text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, dict):
        return ""
    location_str = _string_value(value.get("location_str"))
    if location_str:
        return location_str

    parts = [
        _string_value(value.get("city")),
        _string_value(value.get("region"))
        or _string_value(value.get("state_code"))
        or _string_value(value.get("subregion")),
        _string_value(value.get("country"))
        or _string_value(value.get("country_name")),
    ]
    return ", ".join(part for part in parts if part)


def _parse_created_at(value: object) -> datetime | None:
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


def _string_value(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""
