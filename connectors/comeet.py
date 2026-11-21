import json
import re
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from django.utils.dateparse import parse_datetime
from selectolax.parser import HTMLParser

from connectors.base import BaseConnector, ConnectorFetchError
from connectors.registry import register_connector
from connectors.types import NormalizedJob
from connectors.utils.employment import map_employment_type
from connectors.utils.html import html_to_text
from connectors.utils.salary import parse_salary
from jobs.models import Source

_SLUG_PATTERN = re.compile(r"[A-Za-z0-9_-]+\Z")


@register_connector
class ComeetConnector(BaseConnector):
    platform_key = "comeet"
    kind = "ats"

    def discover_jobs(self, source: Source) -> Iterator[NormalizedJob]:
        config = source.config if isinstance(source.config, dict) else {}
        slug = _string_value(config.get("company_slug") or config.get("company"))
        if not slug or not _SLUG_PATTERN.fullmatch(slug):
            raise ValueError("Comeet source config must contain a valid company_slug")

        configured_url = _string_value(config.get("careers_url"))
        url = configured_url or f"https://www.comeet.com/jobs/{quote(slug, safe='')}"
        seen: set[str] = set()
        cursor: str | None = None
        while url:
            response = self.get(url, params={"cursor": cursor} if cursor else None)
            payload = _payload(response)
            items = _extract_jobs(payload)
            if items is None:
                raise ConnectorFetchError("Comeet returned malformed or invalid job data", url=url)
            for item in items:
                if not isinstance(item, dict):
                    raise ConnectorFetchError("Comeet returned an invalid job record", url=url)
                external_id = _identifier(item)
                if not external_id:
                    raise ConnectorFetchError("Comeet returned a job without a stable id", url=url)
                if external_id in seen:
                    continue
                seen.add(external_id)
                title = _string_value(item.get("name") or item.get("title") or item.get("position"))
                if not title:
                    raise ConnectorFetchError(f"Comeet job {external_id!r} has no title", url=url)
                yield _normalize(item, source, external_id, title, slug)
            next_url, cursor = _next_page(payload)
            if next_url:
                url = next_url
            elif not cursor:
                break


def _payload(response: Any) -> Any:
    content_type = response.headers.get("content-type", "")
    if "json" in content_type.lower():
        try:
            return response.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ConnectorFetchError("Comeet returned malformed JSON", url=str(response.url)) from exc
    parsed = _html_payload(response.text)
    if parsed is not None:
        return parsed
    parser = HTMLParser(response.text)
    cards = parser.css('[data-job-id], .job, .position')
    jobs = []
    for card in cards:
        text = card.text(deep=True)
        link = card.css_first("a")
        jobs.append({
            "id": card.attributes.get("data-job-id") or (link.attributes.get("href") if link else ""),
            "title": text.split("\n")[0].strip() if text else "",
            "location": card.attributes.get("data-location") or "",
            "url": link.attributes.get("href") if link else "",
            "description": text,
        })
    return {"jobPostings": jobs} if jobs else {}


def _html_payload(html: str) -> Any | None:
    parser = HTMLParser(html)
    for script in parser.css('script[type="application/ld+json"]'):
        try:
            parsed = json.loads(script.text())
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, (dict, list)):
            return parsed
    return None


def _extract_jobs(payload: Any) -> list[Any] | None:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return None
    for key in ("positions", "jobs", "jobPostings", "openings", "data", "items"):
        value = payload.get(key)
        if key in payload and not isinstance(value, (list, dict)):
            return None
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = _extract_jobs(value)
            if nested is not None:
                return nested
    graph = payload.get("@graph")
    if isinstance(graph, list):
        jobs = [item for item in graph if isinstance(item, dict) and item.get("@type") in {"JobPosting", "ListItem"}]
        if jobs:
            return jobs
    if payload.get("@type") == "JobPosting":
        return [payload]
    return [] if isinstance(payload, dict) else None


def _next_page(payload: Any) -> tuple[str | None, str | None]:
    if not isinstance(payload, dict):
        return None, None
    for key in ("next", "nextUrl", "next_url"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip(), None
    meta = payload.get("meta")
    if isinstance(meta, dict):
        cursor = meta.get("nextCursor") or meta.get("cursor")
        if isinstance(cursor, str):
            return None, cursor
    return None, None


def _normalize(item: dict[str, Any], source: Source, external_id: str, title: str, slug: str) -> NormalizedJob:
    location = _location(item)
    remote = _remote(item, location)
    salary = parse_salary(item.get("salary") or item.get("compensation"))
    source_url = _string_value(item.get("url") or item.get("absolute_url") or item.get("externalUrl"))
    if not source_url:
        source_url = f"https://www.comeet.com/jobs/{quote(slug, safe='')}/{quote(external_id, safe='')}"
    return NormalizedJob(
        external_id=external_id,
        title=title,
        company=source.name.strip(),
        description=html_to_text(item.get("description") or item.get("jobDescription")),
        location=location,
        source_url=source_url,
        apply_url=_string_value(item.get("applyUrl") or item.get("applicationUrl")) or source_url,
        salary_min=salary.minimum,
        salary_max=salary.maximum,
        salary_currency=salary.currency,
        salary_period=salary.period,
        employment_type=map_employment_type(item.get("employmentType") or item.get("type")),
        is_remote=remote,
        posted_at=_parse_timestamp(item.get("publishedAt") or item.get("datePosted") or item.get("createdAt")),
        department=_string_value(item.get("department")),
        raw=dict(item),
    )


def _identifier(item: dict[str, Any]) -> str:
    for key in ("id", "uid", "position_id", "externalId", "url"):
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
    return _string_value(location or item.get("locationsText") or item.get("locationName"))


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
