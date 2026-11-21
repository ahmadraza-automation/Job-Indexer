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
from jobs.models import Source

_COMPANY_SLUG_PATTERN = re.compile(r"[A-Za-z0-9_-]+\Z")


@register_connector
class BambooHRConnector(BaseConnector):
    platform_key = "bamboohr"
    kind = "ats"

    def discover_jobs(self, source: Source) -> Iterator[NormalizedJob]:
        config = source.config
        company_slug = config.get("company_slug") if isinstance(config, dict) else None
        if (
            not isinstance(company_slug, str)
            or not company_slug.strip()
            or not _COMPANY_SLUG_PATTERN.fullmatch(company_slug.strip())
        ):
            raise ValueError("BambooHR source config must contain a valid company_slug")
        company_slug = company_slug.strip()
        list_url = (
            f"https://{quote(company_slug, safe='')}.bamboohr.com/careers/list"
        )

        items: list[Any] = []
        page_url: str | None = list_url
        visited_pages: set[str] = set()
        while page_url and page_url not in visited_pages:
            visited_pages.add(page_url)
            response = self.get(page_url)
            try:
                payload: Any = response.json()
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ConnectorFetchError(
                    "BambooHR returned malformed JSON",
                    url=page_url,
                ) from exc

            page_items = _extract_items(payload)
            if page_items is None:
                raise ConnectorFetchError(
                    "BambooHR returned an invalid careers response",
                    url=page_url,
                )
            items.extend(page_items)
            page_url = _next_page(payload)

        seen_ids: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                raise ConnectorFetchError(
                    "BambooHR returned an invalid job record",
                    url=list_url,
                )
            external_id = _identifier(item)
            if not external_id:
                raise ConnectorFetchError(
                    "BambooHR returned a job without a valid id",
                    url=list_url,
                )
            if external_id in seen_ids:
                continue
            seen_ids.add(external_id)

            title = _string_value(
                item.get("jobOpeningName")
                or item.get("title")
                or item.get("name")
            )
            if not title:
                raise ConnectorFetchError(
                    f"BambooHR job {external_id!r} has no title",
                    url=list_url,
                )

            detail_url = (
                f"https://{quote(company_slug, safe='')}.bamboohr.com/careers/{quote(external_id, safe='')}"
            )
            detail_payload: dict[str, Any] = {}
            detail_response = self.get(detail_url)
            content_type = detail_response.headers.get("content-type", "")
            if "json" in content_type.lower():
                try:
                    detail_json: Any = detail_response.json()
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ConnectorFetchError(
                        "BambooHR returned malformed job detail JSON",
                        url=detail_url,
                    ) from exc
                if isinstance(detail_json, dict):
                    detail_payload = detail_json
                    detail_payload = _unwrap_detail(detail_payload)
            elif detail_response.text:
                detail_payload = _html_detail(detail_response.text)

            merged = dict(item)
            merged.update({key: value for key, value in detail_payload.items() if value not in (None, "", [], {})})
            description = html_to_text(
                merged.get("description")
                or merged.get("descriptionText")
                or merged.get("jobDescription")
            )
            location = _location(merged)
            remote = _remote_value(merged, location)
            source_url = _string_value(
                merged.get("jobUrl")
                or merged.get("url")
                or merged.get("careersUrl")
                or detail_url
            )
            yield NormalizedJob(
                external_id=external_id,
                title=_string_value(
                    merged.get("jobOpeningName")
                    or merged.get("title")
                    or merged.get("name")
                ) or title,
                company=source.name.strip(),
                description=description,
                location=location,
                source_url=source_url,
                apply_url=_string_value(
                    merged.get("applyUrl")
                    or merged.get("applicationUrl")
                ) or source_url,
                employment_type=_employment_type(merged),
                is_remote=remote,
                posted_at=_parse_timestamp(
                    merged.get("postedDate")
                    or merged.get("publishedDate")
                    or merged.get("createdDate")
                    or merged.get("datePosted")
                ),
                updated_at=_parse_timestamp(
                    merged.get("updatedDate") or merged.get("modifiedDate")
                ),
                department=_department(merged),
                raw=merged,
            )


def _extract_items(payload: Any) -> list[Any] | None:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return None
    for key in ("result", "jobs", "data", "postings"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            for nested_key in ("jobs", "postings", "result", "items"):
                nested = value.get(nested_key)
                if isinstance(nested, list):
                    return nested
    return [] if payload.get("meta") is not None else None


def _next_page(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("next", "nextUrl", "next_url"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    meta = payload.get("meta")
    if isinstance(meta, dict):
        for key in ("next", "nextUrl", "next_url"):
            value = meta.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _unwrap_detail(payload: dict[str, Any]) -> dict[str, Any]:
    for key in ("job", "result", "data", "posting"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value
    return payload


def _html_detail(html: str) -> dict[str, Any]:
    parser = HTMLParser(html)
    data: dict[str, Any] = {}
    for script in parser.css('script[type="application/ld+json"]'):
        try:
            parsed: Any = json.loads(script.text())
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            data.update(parsed)
            break
    description_node = parser.css_first(".job-description")
    if description_node is not None:
        data["description"] = description_node.text(deep=True)
    return data


def _identifier(item: dict[str, Any]) -> str:
    for key in ("id", "jobOpeningId", "jobId", "uuid", "jobUrl", "url"):
        value = item.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    return ""


def _location(item: dict[str, Any]) -> str:
    location = item.get("location")
    if isinstance(location, dict):
        parts = (
            _string_value(location.get("city")),
            _string_value(location.get("state")),
            _string_value(location.get("country")),
            _string_value(location.get("name")),
        )
        return ", ".join(dict.fromkeys(part for part in parts if part))
    location_text = _string_value(location)
    if location_text:
        return location_text
    return ", ".join(
        dict.fromkeys(
            part
            for part in (
                _string_value(item.get("city")),
                _string_value(item.get("state")),
                _string_value(item.get("country")),
            )
            if part
        )
    )


def _department(item: dict[str, Any]) -> str:
    department = item.get("department")
    if isinstance(department, dict):
        return _string_value(department.get("name") or department.get("label"))
    return _string_value(
        department
        or item.get("departmentLabel")
        or item.get("departmentName")
    )


def _employment_type(item: dict[str, Any]) -> str:
    value = (
        item.get("employmentStatus")
        or item.get("employmentType")
        or item.get("type")
        or item.get("workType")
    )
    return map_employment_type(value)


def _remote_value(item: dict[str, Any], location: str) -> bool | None:
    remote = item.get("isRemote")
    if isinstance(remote, bool):
        return remote
    text = " ".join(
        (
            location,
            _string_value(item.get("workplace")),
            _string_value(item.get("locationType")),
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
