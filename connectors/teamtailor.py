import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
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


@register_connector
class TeamtailorConnector(BaseConnector):
    platform_key = "teamtailor"
    kind = "ats"

    def discover_jobs(self, source: Source) -> Iterator[NormalizedJob]:
        config = source.config
        company_slug = config.get("company_slug") if isinstance(config, dict) else None
        if (
            not isinstance(company_slug, str)
            or not company_slug.strip()
            or not _COMPANY_SLUG_PATTERN.fullmatch(company_slug.strip())
        ):
            raise ValueError("Teamtailor source config must contain a valid company_slug")
        company_slug = company_slug.strip()

        configured_feed = config.get("feed_url") if isinstance(config, dict) else None
        if isinstance(configured_feed, str) and configured_feed.strip():
            url = configured_feed.strip()
        else:
            configured_base_url = getattr(source, "base_url", "")
            base_url = (
                configured_base_url.strip().rstrip("/")
                if isinstance(configured_base_url, str)
                else ""
            )
            # Assumption: Teamtailor career sites expose a public jobs.rss feed
            # on the company subdomain. Operators may override it with config.feed_url.
            url = f"{base_url}/jobs.rss" if base_url else (
                f"https://{quote(company_slug, safe='')}.teamtailor.com/jobs.rss"
            )

        response = self.get(url)
        try:
            root = ET.fromstring(response.content)
        except (ET.ParseError, UnicodeDecodeError) as exc:
            raise ConnectorFetchError(
                "Teamtailor returned malformed RSS/XML",
                url=url,
            ) from exc

        items = [
            element
            for element in root.iter()
            if _local_name(element.tag) in {"item", "entry"}
        ]
        seen_ids: set[str] = set()
        for item in items:
            values = _element_values(item)
            external_id = _identifier(values)
            if not external_id:
                raise ConnectorFetchError(
                    "Teamtailor returned a job without a valid id",
                    url=url,
                )
            if external_id in seen_ids:
                continue
            seen_ids.add(external_id)

            title = _string_value(values.get("title"))
            if not title:
                raise ConnectorFetchError(
                    f"Teamtailor job {external_id!r} has no title",
                    url=url,
                )
            source_url = _string_value(values.get("link"))
            if not source_url:
                for link in item:
                    if _local_name(link.tag) == "link":
                        source_url = _string_value(
                            link.attrib.get("href") or link.text
                        )
                        if source_url:
                            break
            description = html_to_text(
                values.get("description")
                or values.get("summary")
                or values.get("content")
            )
            location = _location(values)
            yield NormalizedJob(
                external_id=external_id,
                title=title,
                company=source.name.strip(),
                description=description,
                location=location,
                source_url=source_url,
                apply_url=_string_value(values.get("applyUrl")) or source_url,
                employment_type=_employment_type(values),
                is_remote=_remote_value(location, values),
                posted_at=_parse_timestamp(
                    values.get("pubDate")
                    or values.get("published")
                    or values.get("updated")
                    or values.get("date")
                ),
                updated_at=_parse_timestamp(
                    values.get("updated") or values.get("lastModified")
                ),
                department=_string_value(
                    values.get("department") or values.get("category")
                ),
                raw=_element_raw(item),
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
    for key in ("guid", "id", "jobId", "uuid", "link"):
        value = _string_value(values.get(key))
        if value:
            return value
    return ""


def _location(values: dict[str, str]) -> str:
    for key in ("location", "jobLocation", "office", "city"):
        value = _string_value(values.get(key))
        if value:
            return value
    return ""


def _employment_type(values: dict[str, str]) -> str:
    value = (
        values.get("employmentType")
        or values.get("employment_type")
        or values.get("type")
    )
    return map_employment_type(value)


def _remote_value(location: str, values: dict[str, str]) -> bool | None:
    text = " ".join(
        (
            location,
            _string_value(values.get("workplace")),
            _string_value(values.get("remote")),
        )
    ).lower()
    if (
        "remote" in text
        or re.search(r"\b(true|yes|1)\b", text)
    ):
        return True
    if any(term in text for term in ("on-site", "onsite", "office")):
        return False
    return None


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        parsed = None
    if parsed is None:
        parsed = parse_datetime(text)
    if parsed is None:
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _string_value(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""
