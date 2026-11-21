import json
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
from connectors.utils.salary import parse_salary
from jobs.models import Source

_SLUG_PATTERN = re.compile(r"[A-Za-z0-9_-]+\Z")


@register_connector
class JobviteConnector(BaseConnector):
    platform_key = "jobvite"
    kind = "ats"

    def discover_jobs(self, source: Source) -> Iterator[NormalizedJob]:
        config = source.config if isinstance(source.config, dict) else {}
        slug = _string_value(config.get("company_slug") or config.get("company"))
        if not slug or not _SLUG_PATTERN.fullmatch(slug):
            raise ValueError("Jobvite source config must contain a valid company_slug")
        url = _string_value(config.get("careers_url")) or f"https://jobs.jobvite.com/{quote(slug, safe='')}/jobs"
        response = self.get(url)
        content_type = response.headers.get("content-type", "")
        if "json" in content_type.lower():
            try:
                payload: Any = response.json()
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ConnectorFetchError("Jobvite returned malformed JSON", url=url) from exc
            items = _extract_jobs(payload)
        elif "xml" in content_type.lower() or response.content.lstrip().startswith(b"<"):
            try:
                root = ET.fromstring(response.content)
            except (ET.ParseError, UnicodeDecodeError) as exc:
                raise ConnectorFetchError("Jobvite returned malformed XML", url=url) from exc
            items = _xml_items(root)
        else:
            raise ConnectorFetchError("Jobvite returned a malformed or unsupported careers response", url=url)
        if items is None:
            raise ConnectorFetchError("Jobvite returned an invalid jobs response", url=url)
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                raise ConnectorFetchError("Jobvite returned an invalid job record", url=url)
            external_id = _identifier(item)
            if not external_id:
                raise ConnectorFetchError("Jobvite returned a job without a stable id", url=url)
            if external_id in seen:
                continue
            seen.add(external_id)
            title = _string_value(item.get("title") or item.get("name") or item.get("jobTitle"))
            if not title:
                raise ConnectorFetchError(f"Jobvite job {external_id!r} has no title", url=url)
            yield _normalize(item, source, external_id, title, slug)


def _extract_jobs(payload: Any) -> list[Any] | None:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return None
    for key in ("jobs", "jobPostings", "requisitions", "openings", "data", "items"):
        value = payload.get(key)
        if key in payload and not isinstance(value, (list, dict)):
            return None
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = _extract_jobs(value)
            if nested is not None:
                return nested
    return [] if payload else None


def _xml_items(root: ET.Element) -> list[dict[str, Any]]:
    jobs = []
    for item in root.iter():
        if item.tag.rsplit("}", 1)[-1].lower() in {"item", "job", "position"}:
            values = {}
            for child in item.iter():
                if child.text and child.text.strip():
                    values.setdefault(child.tag.rsplit("}", 1)[-1], child.text.strip())
            if values:
                jobs.append(values)
    return jobs


def _normalize(item: dict[str, Any], source: Source, external_id: str, title: str, slug: str) -> NormalizedJob:
    location = _location(item)
    remote = _remote(item, location)
    salary = parse_salary(item.get("salary") or item.get("compensation"))
    source_url = _string_value(item.get("url") or item.get("jobLink") or item.get("absolute_url"))
    if not source_url:
        source_url = f"https://jobs.jobvite.com/{quote(slug, safe='')}/job/{quote(external_id, safe='')}"
    return NormalizedJob(
        external_id=external_id,
        title=title,
        company=source.name.strip(),
        location=location,
        description=html_to_text(item.get("description") or item.get("jobDescription")),
        source_url=source_url,
        apply_url=_string_value(item.get("applyUrl") or item.get("applyURL")) or source_url,
        salary_min=salary.minimum,
        salary_max=salary.maximum,
        salary_currency=salary.currency,
        salary_period=salary.period,
        employment_type=map_employment_type(item.get("employmentType") or item.get("category") or item.get("type")),
        is_remote=remote,
        posted_at=_parse_timestamp(item.get("date") or item.get("postedOn") or item.get("datePosted")),
        department=_string_value(item.get("department")),
        raw=dict(item),
    )


def _identifier(item: dict[str, Any]) -> str:
    for key in ("eid", "id", "jobId", "requisitionId", "guid", "url"):
        value = item.get(key)
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            result = str(value).strip()
            if result:
                return result
    return ""


def _location(item: dict[str, Any]) -> str:
    location = item.get("location")
    if isinstance(location, dict):
        return ", ".join(dict.fromkeys(_string_value(location.get(key)) for key in ("city", "state", "country") if _string_value(location.get(key))))
    return _string_value(location or item.get("locationsText"))


def _remote(item: dict[str, Any], location: str) -> bool | None:
    if isinstance(item.get("isRemote"), bool):
        return item["isRemote"]
    text = f"{location} {_string_value(item.get('workplace'))}".lower()
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
