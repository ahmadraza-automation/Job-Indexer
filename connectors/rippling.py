import json
import re
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlsplit

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
class RipplingConnector(BaseConnector):
    platform_key = "rippling"
    kind = "ats"

    def discover_jobs(self, source: Source) -> Iterator[NormalizedJob]:
        config = source.config
        company_slug = (
            (config.get("company_slug") or config.get("board_slug"))
            if isinstance(config, dict)
            else None
        )
        if (
            not isinstance(company_slug, str)
            or not company_slug.strip()
            or not _COMPANY_SLUG_PATTERN.fullmatch(company_slug.strip())
        ):
            raise ValueError(
                "Rippling source config must contain a valid company_slug"
            )
        company_slug = company_slug.strip()

        base_url = source.base_url.strip().rstrip("/") if getattr(source, "base_url", "") else ""
        parsed_base_url = urlsplit(base_url)
        if (
            parsed_base_url.scheme in {"http", "https"}
            and parsed_base_url.netloc
            and not parsed_base_url.username
            and not parsed_base_url.password
        ):
            url = base_url
        else:
            url = f"https://ats.rippling.com/{quote(company_slug, safe='')}"

        seen_ids: set[str] = set()
        cursor: str | None = None
        while url:
            params = {"cursor": cursor} if cursor else None
            response = self.get(url, params=params)
            payload: Any
            content_type = response.headers.get("content-type", "")
            if "json" in content_type.lower():
                try:
                    payload = response.json()
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ConnectorFetchError(
                        "Rippling returned malformed JSON",
                        url=url,
                    ) from exc
            else:
                payload = _html_payload(response.text)
                if payload is None:
                    raise ConnectorFetchError(
                        "Rippling returned an invalid public job board response",
                        url=url,
                    )

            items = _extract_jobs(payload)
            if items is None:
                raise ConnectorFetchError(
                    "Rippling returned an invalid public job board response",
                    url=url,
                )
            for item in items:
                if not isinstance(item, dict):
                    raise ConnectorFetchError(
                        "Rippling returned an invalid job record",
                        url=url,
                    )
                external_id = _identifier(item)
                if not external_id:
                    raise ConnectorFetchError(
                        "Rippling returned a job without a valid id",
                        url=url,
                    )
                if external_id in seen_ids:
                    continue
                seen_ids.add(external_id)

                title = _string_value(
                    item.get("title")
                    or item.get("name")
                    or item.get("jobTitle")
                )
                if not title:
                    raise ConnectorFetchError(
                        f"Rippling job {external_id!r} has no title",
                        url=url,
                    )

                location = _location(item)
                remote = _remote_value(item, location)
                description = html_to_text(
                    item.get("description")
                    or item.get("descriptionHtml")
                    or item.get("jobDescription")
                )
                source_url = _string_value(
                    item.get("jobUrl")
                    or item.get("url")
                    or item.get("absolute_url")
                )
                if not source_url and external_id:
                    source_url = f"{url.rstrip('/')}/{quote(external_id, safe='')}"
                yield NormalizedJob(
                    external_id=external_id,
                    title=title,
                    company=source.name.strip(),
                    description=description,
                    location=location,
                    source_url=source_url,
                    apply_url=_string_value(
                        item.get("applyUrl")
                        or item.get("apply_url")
                        or item.get("applicationUrl")
                    ) or source_url,
                    employment_type=_employment_type(item),
                    is_remote=remote,
                    posted_at=_parse_timestamp(
                        item.get("postedAt")
                        or item.get("publishedAt")
                        or item.get("published_at")
                        or item.get("datePosted")
                        or item.get("createdAt")
                    ),
                    updated_at=_parse_timestamp(
                        item.get("updatedAt")
                        or item.get("updated_at")
                    ),
                    department=_department(item),
                    raw=item,
                )

            next_url, next_cursor = _next_page(payload)
            if next_url:
                url = next_url
                cursor = None
            elif next_cursor:
                cursor = next_cursor
            else:
                break


def _html_payload(html: str) -> Any | None:
    parser = HTMLParser(html)
    for script in parser.css("script"):
        attributes = script.attributes
        script_type = attributes.get("type", "")
        script_id = attributes.get("id", "")
        if script_id == "__NEXT_DATA__" or "json" in script_type:
            try:
                parsed = json.loads(script.text())
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, (dict, list)):
                return parsed
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

    for key in ("jobs", "postings", "openings", "results", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = _extract_jobs(value)
            if nested is not None:
                return nested
    for key in ("data", "props", "pageProps", "board"):
        value = payload.get(key)
        if isinstance(value, (dict, list)):
            nested = _extract_jobs(value)
            if nested is not None:
                return nested
    if any(
        key in payload
        for key in ("title", "name", "jobTitle")
    ) and any(
        key in payload
        for key in ("id", "jobId", "uuid", "slug")
    ):
        return [payload]
    return [] if payload.get("meta") is not None else None


def _next_page(payload: Any) -> tuple[str | None, str | None]:
    if not isinstance(payload, dict):
        return None, None
    for key in ("next", "nextUrl", "next_url"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip(), None
    data = payload.get("data")
    if isinstance(data, dict):
        nested_url, nested_cursor = _next_page(data)
        if nested_url or nested_cursor:
            return nested_url, nested_cursor
    meta = payload.get("meta")
    if isinstance(meta, dict):
        for key in ("nextCursor", "next_cursor", "cursor"):
            value = meta.get(key)
            if isinstance(value, str) and value.strip():
                return None, value.strip()
    for key in ("cursor", "nextCursor", "next_cursor"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return None, value.strip()
    return None, None


def _identifier(item: dict[str, Any]) -> str:
    for key in ("id", "jobId", "uuid", "slug", "url", "jobUrl"):
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
    if isinstance(location, list):
        names = [
            _location_value(value)
            for value in location
            if _location_value(value)
        ]
        return ", ".join(dict.fromkeys(names))
    return _string_value(location)


def _location_value(value: Any) -> str:
    if isinstance(value, dict):
        return _string_value(
            value.get("name")
            or value.get("city")
            or value.get("location")
        )
    return _string_value(value)


def _department(item: dict[str, Any]) -> str:
    department = item.get("department")
    if isinstance(department, dict):
        return _string_value(department.get("name") or department.get("label"))
    team = item.get("team")
    if isinstance(team, dict):
        return _string_value(team.get("name") or team.get("label"))
    return _string_value(department or team)


def _employment_type(item: dict[str, Any]) -> str:
    value = (
        item.get("employmentType")
        or item.get("employment_type")
        or item.get("type")
        or item.get("workType")
    )
    return map_employment_type(value)


def _remote_value(item: dict[str, Any], location: str) -> bool | None:
    remote = item.get("isRemote") or item.get("remote")
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
