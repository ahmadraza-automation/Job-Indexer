import re
import xml.etree.ElementTree as ET
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
from jobs.models import Source

_COMPANY_SLUG_PATTERN = re.compile(r"[A-Za-z0-9_-]+\Z")
_API_ROOT = "https://{company}.jobs.personio.de/xml"


@register_connector
class PersonioConnector(BaseConnector):
    platform_key = "personio"
    kind = "ats"

    def discover_jobs(self, source: Source) -> Iterator[NormalizedJob]:
        config = source.config
        company_slug = config.get("company_slug") if isinstance(config, dict) else None
        if (
            not isinstance(company_slug, str)
            or not company_slug.strip()
            or not _COMPANY_SLUG_PATTERN.fullmatch(company_slug.strip())
        ):
            raise ValueError("Personio source config must contain a valid company_slug")
        company_slug = company_slug.strip()
        url = _API_ROOT.format(company=quote(company_slug, safe=""))

        response = self.get(url)
        try:
            root = ET.fromstring(response.content)
        except (ET.ParseError, UnicodeDecodeError) as exc:
            raise ConnectorFetchError(
                "Personio returned malformed XML",
                url=url,
            ) from exc

        positions = [
            element
            for element in root.iter()
            if _local_name(element.tag) in {"position", "job", "jobPosting"}
        ]
        seen_ids: set[str] = set()
        for position in positions:
            values = _element_values(position)
            external_id = _identifier(values)
            if not external_id:
                raise ConnectorFetchError(
                    "Personio returned a position without a valid id",
                    url=url,
                )
            if external_id in seen_ids:
                continue
            seen_ids.add(external_id)

            title = _string_value(
                values.get("name")
                or values.get("title")
                or values.get("jobName")
            )
            if not title:
                raise ConnectorFetchError(
                    f"Personio position {external_id!r} has no title",
                    url=url,
                )

            description = _description(position, values)
            source_url = _string_value(
                values.get("link")
                or values.get("url")
                or values.get("jobUrl")
                or f"https://{company_slug}.jobs.personio.de/job/{quote(external_id, safe='')}"
            )
            location = _location(values)
            employment = (
                values.get("employmentType")
                or values.get("employment")
                or values.get("schedule")
                or values.get("type")
            )
            remote = _remote_value(location, values)
            yield NormalizedJob(
                external_id=external_id,
                title=title,
                company=source.name.strip(),
                description=description,
                location=location,
                source_url=source_url,
                apply_url=_string_value(values.get("applyUrl")) or source_url,
                employment_type=map_employment_type(employment),
                is_remote=remote,
                posted_at=_parse_timestamp(
                    values.get("date")
                    or values.get("createdAt")
                    or values.get("publishedAt")
                    or values.get("pubDate")
                ),
                updated_at=_parse_timestamp(
                    values.get("updatedAt") or values.get("updated")
                ),
                department=_string_value(
                    values.get("department") or values.get("category")
                ),
                raw=_element_raw(position),
            )


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].split(":", 1)[-1]


def _element_values(element: ET.Element) -> dict[str, str]:
    values: dict[str, str] = {}
    for child in element.iter():
        name = _local_name(child.tag)
        if child.text and child.text.strip():
            values.setdefault(name, child.text.strip())
    return values


def _element_raw(element: ET.Element) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for child in element:
        name = _local_name(child.tag)
        if len(child):
            result[name] = _element_raw(child)
        elif child.text and child.text.strip():
            result[name] = child.text.strip()
    return result


def _identifier(values: dict[str, str]) -> str:
    for key in ("id", "jobId", "positionId", "uuid", "link", "url"):
        value = _string_value(values.get(key))
        if value:
            return value
    return ""


def _description(position: ET.Element, values: dict[str, str]) -> str:
    for child in position.iter():
        if _local_name(child.tag) in {"jobDescription", "description", "value"}:
            if child.text:
                text = html_to_text(child.text)
                if text:
                    return text
    return html_to_text(values.get("description"))


def _location(values: dict[str, str]) -> str:
    return ", ".join(
        dict.fromkeys(
            location
            for location in (
                _string_value(values.get("office")),
                _string_value(values.get("location")),
                _string_value(values.get("city")),
                _string_value(values.get("country")),
            )
            if location
        )
    )


def _remote_value(location: str, values: dict[str, str]) -> bool | None:
    text = " ".join(
        (
            location,
            _string_value(values.get("workplace")),
            _string_value(values.get("recruitingCategory")),
        )
    ).lower()
    if "remote" in text:
        return True
    if any(term in text for term in ("on-site", "onsite", "office")):
        return False
    return None


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    parsed = parse_datetime(value)
    if parsed is None:
        try:
            parsed = datetime.strptime(value.strip(), "%Y-%m-%d")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _string_value(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""
