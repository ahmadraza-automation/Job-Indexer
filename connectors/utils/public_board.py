import json
import re
from collections.abc import Iterator, Mapping
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlsplit

from django.utils.dateparse import parse_datetime
from selectolax.parser import HTMLParser

from connectors.base import BaseConnector, ConnectorFetchError
from connectors.types import NormalizedJob
from connectors.utils.employment import map_employment_type
from connectors.utils.html import html_to_text
from jobs.models import Source

_SLUG_PATTERN = re.compile(r"[A-Za-z0-9_-]+\Z")
_JOB_KEYS = ("jobs", "jobPostings", "positions", "openings", "items", "results")
_ID_KEYS = (
    "id",
    "jobId",
    "job_id",
    "jobPostingId",
    "uuid",
    "externalId",
    "external_id",
)
_NEXT_KEYS = ("next", "next_url", "nextUrl", "nextPageUrl", "next_page_url")


def discover_public_board_jobs(
    connector: BaseConnector,
    source: Source,
    platform: str,
) -> Iterator[NormalizedJob]:
    """Fetch a configured public board URL and normalize its public job data."""
    config = source.config if isinstance(source.config, dict) else {}
    board_url = _string(config.get("base_url"))
    company_slug = _string(config.get("company_slug"))
    parsed_url = urlsplit(board_url)
    if (
        not board_url
        or parsed_url.scheme != "https"
        or not parsed_url.hostname
        or parsed_url.username
        or parsed_url.password
    ):
        raise ValueError(f"{platform} source config must contain a valid HTTPS base_url")
    if not company_slug or not _SLUG_PATTERN.fullmatch(company_slug):
        raise ValueError(
            f"{platform} source config must contain a valid company_slug"
        )

    visited_pages: set[str] = set()
    seen_jobs: set[str] = set()
    current_url = board_url
    for _ in range(100):
        if current_url in visited_pages:
            raise ConnectorFetchError(
                f"{platform} returned a pagination loop",
                url=current_url,
            )
        visited_pages.add(current_url)
        response = connector.get(current_url)
        content_type = response.headers.get("content-type", "").lower()
        if "json" in content_type or response.text.lstrip().startswith(("{", "[")):
            try:
                payload: Any = response.json()
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
                raise ConnectorFetchError(
                    f"{platform} returned malformed JSON",
                    url=current_url,
                ) from exc
            records, next_value, valid = _json_page(payload)
        else:
            records, next_value, valid = _html_page(response.text)

        if not valid:
            raise ConnectorFetchError(
                f"{platform} returned malformed or invalid public job data",
                url=current_url,
            )

        for record in records:
            job = _normalize(record, source, current_url)
            if not job.external_id:
                raise ConnectorFetchError(
                    f"{platform} returned a job without a stable id",
                    url=current_url,
                )
            if not job.title:
                raise ConnectorFetchError(
                    f"{platform} job {job.external_id!r} has no title",
                    url=current_url,
                )
            if job.external_id in seen_jobs:
                continue
            seen_jobs.add(job.external_id)
            yield job

        if not next_value:
            return
        next_url = urljoin(current_url, next_value)
        parsed_next = urlsplit(next_url)
        if (
            parsed_next.scheme != "https"
            or parsed_next.hostname != parsed_url.hostname
            or parsed_next.username
            or parsed_next.password
        ):
            raise ConnectorFetchError(
                f"{platform} returned an invalid pagination URL",
                url=current_url,
            )
        current_url = next_url

    raise ConnectorFetchError(
        f"{platform} exceeded the public board page limit",
        url=current_url,
    )


def _json_page(payload: Any) -> tuple[list[dict[str, Any]], str, bool]:
    if isinstance(payload, list):
        records = _dict_records(payload)
        return records, "", len(records) == len(payload)
    if not isinstance(payload, dict):
        return [], "", False

    if _is_job_posting(payload):
        return [payload], "", True

    for key in _JOB_KEYS:
        if key not in payload:
            continue
        value = payload[key]
        if isinstance(value, list):
            records = _dict_records(value)
            if len(records) != len(value):
                return [], "", False
            return records, _next_page(payload), True
        if isinstance(value, dict):
            records, next_value, valid = _json_page(value)
            return records, next_value or _next_page(payload), valid
        return [], "", False

    graph = payload.get("@graph")
    if isinstance(graph, list):
        return [item for item in graph if isinstance(item, dict) and _is_job_posting(item)], "", True
    return [], "", False


def _html_page(html: str) -> tuple[list[dict[str, Any]], str, bool]:
    if not html.strip():
        return [], "", False
    parser = HTMLParser(html)
    records: list[dict[str, Any]] = []
    scripts = parser.css('script[type="application/ld+json"]')
    for script in scripts:
        try:
            payload = json.loads(script.text())
        except (json.JSONDecodeError, TypeError):
            continue
        records.extend(_job_postings(payload))

    if not records:
        records = _html_cards(parser)
    next_value = _html_next(parser)
    if records:
        return records, next_value, True
    if _empty_board(parser):
        return [], next_value, True
    return [], "", False


def _job_postings(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [record for item in payload for record in _job_postings(item)]
    if not isinstance(payload, dict):
        return []
    records = [_as_dict(payload)] if _is_job_posting(payload) else []
    graph = payload.get("@graph")
    if graph is not None:
        records.extend(_job_postings(graph))
    return records


def _html_cards(parser: HTMLParser) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    selectors = (
        "[data-job-id]",
        "[data-posting-id]",
        ".job-card[data-id]",
        ".job-item[data-id]",
    )
    seen_nodes: set[int] = set()
    for selector in selectors:
        for card in parser.css(selector):
            node_id = id(card)
            if node_id in seen_nodes:
                continue
            seen_nodes.add(node_id)
            link = card.css_first("a[href]")
            heading = card.css_first("h1, h2, h3, h4, h5, h6")
            time_node = card.css_first("time[datetime]")
            location_node = card.css_first(
                '[class*="location"], [data-location]'
            )
            description_node = card.css_first(
                '[class*="description"], [data-description]'
            )
            department_node = card.css_first(
                '[class*="department"], [data-department]'
            )
            employment_node = card.css_first(
                '[class*="employment"], [data-employment-type]'
            )
            records.append(
                {
                    "id": (
                        card.attributes.get("data-job-id")
                        or card.attributes.get("data-posting-id")
                        or card.attributes.get("data-id")
                        or ""
                    ),
                    "title": (
                        card.attributes.get("data-title")
                        or (heading.text(deep=True) if heading else "")
                        or (link.text(deep=True) if link else "")
                    ),
                    "url": link.attributes.get("href") if link else "",
                    "applicationUrl": (
                        link.attributes.get("href") if link else ""
                    ),
                    "location": (
                        location_node.attributes.get("data-location")
                        or location_node.text(deep=True)
                        if location_node
                        else ""
                    ),
                    "description": (
                        description_node.attributes.get("data-description")
                        or description_node.text(deep=True)
                        if description_node
                        else ""
                    ),
                    "department": (
                        department_node.attributes.get("data-department")
                        or department_node.text(deep=True)
                        if department_node
                        else ""
                    ),
                    "employmentType": (
                        employment_node.attributes.get("data-employment-type")
                        or employment_node.text(deep=True)
                        if employment_node
                        else ""
                    ),
                    "datePosted": (
                        time_node.attributes.get("datetime") if time_node else ""
                    ),
                    "isRemote": card.attributes.get("data-remote"),
                }
            )
    return records


def _normalize(
    record: dict[str, Any],
    source: Source,
    page_url: str,
) -> NormalizedJob:
    identifier = record.get("identifier")
    if isinstance(identifier, Mapping):
        identifier = identifier.get("value")
    external_id = next(
        (_string(record.get(key)) for key in _ID_KEYS if _string(record.get(key))),
        _string(identifier),
    )
    if not external_id:
        external_id = _string(identifier)

    title = _string(record.get("title") or record.get("name"))
    organization = record.get("hiringOrganization")
    company = (
        _string(organization.get("name"))
        if isinstance(organization, Mapping)
        else ""
    )
    company = company or _string(record.get("company"))
    location = _location(record.get("jobLocation") or record.get("location"))
    if not location:
        location = _string(record.get("locationsText"))
    description = html_to_text(
        record.get("description")
        or record.get("jobDescription")
        or record.get("content")
    )
    raw_source_url = _string(
        record.get("url")
        or record.get("absolute_url")
        or record.get("jobUrl")
        or record.get("externalUrl")
    )
    source_url = urljoin(page_url, raw_source_url) if raw_source_url else page_url
    raw_apply_url = _string(
        record.get("applyUrl")
        or record.get("applicationUrl")
        or record.get("application_url")
    )
    apply_url = urljoin(page_url, raw_apply_url) if raw_apply_url else source_url
    department = record.get("department") or record.get("departmentName")
    if isinstance(department, Mapping):
        department = department.get("name")
    employment = (
        record.get("employmentType")
        or record.get("employment_type")
        or record.get("type")
        or ""
    )
    remote_value = record.get("isRemote", record.get("remote"))
    workplace = _string(
        record.get("jobLocationType") or record.get("workplaceType")
    )
    location_and_workplace = f"{location} {workplace}".lower()
    if isinstance(remote_value, bool):
        is_remote: bool | None = remote_value
    elif _string(remote_value).lower() in {"true", "yes", "remote"}:
        is_remote = True
    elif _string(remote_value).lower() in {"false", "no", "onsite", "on-site"}:
        is_remote = False
    elif "TELECOMMUTE" in workplace.upper():
        is_remote = True
    elif "remote" in location_and_workplace or "hybrid" in location_and_workplace:
        is_remote = True
    elif any(
        term in location_and_workplace for term in ("on-site", "onsite", "office")
    ):
        is_remote = False
    else:
        is_remote = None

    return NormalizedJob(
        external_id=external_id,
        title=title,
        company=company or source.name.strip(),
        description=description,
        location=location,
        source_url=source_url,
        apply_url=apply_url,
        employment_type=map_employment_type(employment),
        is_remote=is_remote,
        posted_at=_parse_datetime(
            record.get("datePosted")
            or record.get("publishedAt")
            or record.get("published_at")
            or record.get("createdAt")
        ),
        updated_at=_parse_datetime(
            record.get("dateModified")
            or record.get("updatedAt")
            or record.get("updated_at")
        ),
        raw=dict(record),
        department=_string(department),
    )


def _location(value: Any) -> str:
    locations = value if isinstance(value, list) else [value]
    parts: list[str] = []
    for item in locations:
        if isinstance(item, Mapping):
            address = item.get("address", item)
            if isinstance(address, Mapping):
                country = address.get("addressCountry") or address.get("country")
                if isinstance(country, Mapping):
                    country = country.get("name") or country.get("@id")
                part = ", ".join(
                    value
                    for value in (
                        _string(address.get("addressLocality") or address.get("city")),
                        _string(address.get("addressRegion") or address.get("state")),
                        _string(country),
                    )
                    if value
                )
            else:
                part = ""
        else:
            part = _string(item)
        if part and part not in parts:
            parts.append(part)
    return ", ".join(parts)


def _html_next(parser: HTMLParser) -> str:
    node = parser.css_first('a[rel~="next"], link[rel~="next"]')
    if node is None:
        for link in parser.css("a[href]"):
            label = " ".join(
                (
                    link.text(deep=True),
                    link.attributes.get("aria-label", ""),
                    link.attributes.get("class", ""),
                )
            ).lower()
            if re.search(r"\b(next|older)\b", label):
                node = link
                break
    return node.attributes.get("href", "") if node else ""


def _next_page(payload: dict[str, Any]) -> str:
    for key in _NEXT_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    links = payload.get("links")
    if isinstance(links, dict):
        value = links.get("next")
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, dict):
            return _string(value.get("href") or value.get("url"))
    return ""


def _empty_board(parser: HTMLParser) -> bool:
    if parser.css_first("[data-jobs-empty], [data-no-openings]"):
        return True
    text = parser.root.text(separator=" ").lower()
    return any(
        phrase in text
        for phrase in (
            "no current openings",
            "no open positions",
            "no open roles",
            "no jobs found",
            "no jobs available",
        )
    )


def _dict_records(values: list[Any]) -> list[dict[str, Any]]:
    return [value for value in values if isinstance(value, dict)]


def _is_job_posting(value: dict[str, Any]) -> bool:
    record_type = value.get("@type", "")
    return record_type == "JobPosting" or (
        isinstance(record_type, list) and "JobPosting" in record_type
    )


def _as_dict(value: Mapping[str, Any]) -> dict[str, Any]:
    return dict(value)


def _parse_datetime(value: object) -> datetime | None:
    text = _string(value)
    if not text:
        return None
    parsed = parse_datetime(text)
    if parsed is None:
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _string(value: Any) -> str:
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        return str(value).strip()
    return ""
