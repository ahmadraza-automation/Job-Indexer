"""CareerPlug public jobs pages.

The public board at https://1-800-water-damage.careerplug.com/jobs renders
HTML job links; each job detail page contains ``.trix-content`` and
``.job-compensation`` content. TODO: verify pagination behavior against a
multi-page live board.
"""

import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import urljoin, urlsplit

from selectolax.parser import HTMLParser

from connectors.base import BaseConnector, ConnectorFetchError
from connectors.registry import register_connector
from connectors.types import NormalizedJob
from connectors.utils.employment import map_employment_type
from connectors.utils.html import html_to_text
from connectors.utils.jsonld import extract_job_postings
from connectors.utils.remote import detect_remote
from connectors.utils.salary import parse_salary
from jobs.models import Source

_SLUG_PATTERN = re.compile(r"[A-Za-z0-9_-]+\Z")
_JOB_PATH_PATTERN = re.compile(r"/jobs/(\d+)(?:/|$)")
_MAX_PAGES = 100


@register_connector
class CareerPlugConnector(BaseConnector):
    platform_key = "careerplug"
    kind = "ats"

    def discover_jobs(self, source: Source) -> Iterator[NormalizedJob]:
        base_url, slug = _config(source)
        board_url = urljoin(base_url.rstrip("/") + "/", "jobs")
        current_url = board_url
        seen_pages: set[str] = set()
        seen_ids: set[str] = set()

        for _ in range(_MAX_PAGES):
            if current_url in seen_pages:
                raise ConnectorFetchError(
                    "CareerPlug returned a pagination loop",
                    url=current_url,
                )
            seen_pages.add(current_url)
            response = self.get(current_url)
            parser = HTMLParser(response.text)
            links = _job_links(parser, current_url)
            for job_id, detail_url, listing_text in links:
                if job_id in seen_ids:
                    continue
                seen_ids.add(job_id)
                detail_response = self.get(detail_url)
                job = _normalize_detail(
                    detail_response.text,
                    source=source,
                    job_id=job_id,
                    detail_url=detail_url,
                    apply_url=urljoin(
                        detail_url.split("?", 1)[0].rstrip("/") + "/",
                        "apps/new",
                    ),
                    listing_text=listing_text,
                )
                yield job

            next_url = _next_url(parser, current_url)
            if next_url is None:
                if not links and _is_empty(parser):
                    return
                if not links:
                    raise ConnectorFetchError(
                        "CareerPlug returned malformed public jobs HTML",
                        url=current_url,
                    )
                return
            current_url = next_url

        raise ConnectorFetchError(
            "CareerPlug exceeded the public jobs page limit",
            url=current_url,
        )


def _config(source: Source) -> tuple[str, str]:
    config = source.config if isinstance(source.config, dict) else {}
    base_url = _string(config.get("base_url")).rstrip("/")
    slug = _string(config.get("company_slug"))
    parsed = urlsplit(base_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "CareerPlug source config must contain a valid HTTPS base_url"
        )
    if not slug or not _SLUG_PATTERN.fullmatch(slug):
        raise ValueError("CareerPlug source config must contain a valid company_slug")
    return base_url, slug


def _job_links(
    parser: HTMLParser,
    page_url: str,
) -> list[tuple[str, str, str]]:
    links: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    page_host = urlsplit(page_url).hostname
    for link in parser.css("a[href]"):
        detail_url = urljoin(page_url, link.attributes.get("href", ""))
        parsed = urlsplit(detail_url)
        match = _JOB_PATH_PATTERN.fullmatch(parsed.path.rstrip("/"))
        if (
            match is None
            or parsed.scheme != "https"
            or parsed.hostname != page_host
        ):
            continue
        job_id = match.group(1)
        if job_id in seen:
            continue
        seen.add(job_id)
        links.append((job_id, detail_url, link.text(separator=" ", deep=True)))
    return links


def _normalize_detail(
    html: str,
    *,
    source: Source,
    job_id: str,
    detail_url: str,
    apply_url: str,
    listing_text: str,
) -> NormalizedJob:
    structured = list(extract_job_postings(html, page_url=detail_url))
    if structured:
        job = structured[0]
        job.external_id = job.external_id or job_id
        job.company = job.company or source.name.strip()
        job.source_url = detail_url
        job.apply_url = apply_url
        if not job.location:
            job.location = _listing_field(listing_text, "Location")
        if not job.employment_type:
            job.employment_type = map_employment_type(
                _listing_field(listing_text, "Full / Part Time")
            )
        return job

    parser = HTMLParser(html)
    title_node = parser.css_first("h1.headline")
    info_node = parser.css_first(".job-info")
    description_node = parser.css_first(".trix-content")
    compensation_node = parser.css_first(".job-compensation")
    title = (
        title_node.text(deep=True).strip()
        if title_node is not None
        else _listing_title(listing_text)
    )
    if not title:
        raise ConnectorFetchError(
            f"CareerPlug job {job_id!r} has no title",
            url=detail_url,
        )
    info = info_node.text(separator=" ", deep=True) if info_node else ""
    location = _listing_field(listing_text, "Location") or _info_location(info)
    employment = (
        _listing_field(listing_text, "Full / Part Time")
        or _info_employment(info)
    )
    salary = parse_salary(
        compensation_node.text(deep=True) if compensation_node else ""
    )
    description_html = description_node.html if description_node else ""
    return NormalizedJob(
        external_id=job_id,
        title=title,
        company=source.name.strip(),
        description=html_to_text(description_html),
        location=location,
        source_url=detail_url,
        apply_url=apply_url,
        salary_min=salary.minimum,
        salary_max=salary.maximum,
        salary_currency=salary.currency,
        salary_period=salary.period,
        employment_type=map_employment_type(employment),
        is_remote=detect_remote(location),
        raw={
            "listing_text": listing_text,
            "detail_title": title,
            "detail_info": info,
            "description_html": description_html,
            "compensation": (
                compensation_node.text(deep=True) if compensation_node else ""
            ),
        },
    )


def _listing_title(text: str) -> str:
    match = re.split(r"\b(?:Location|Full\s*/\s*Part\s*Time)\s*:", text, maxsplit=1)
    return match[0].strip(" \t\r\n•") if match else ""


def _listing_field(text: str, label: str) -> str:
    match = re.search(
        rf"{re.escape(label)}\s*:\s*(.*?)(?=\s+(?:Location|Full\s*/\s*Part\s*Time)\s*:|$)",
        text,
        re.IGNORECASE,
    )
    return match.group(1).strip() if match else ""


def _info_location(info: str) -> str:
    parts = [part.strip() for part in info.split("•")]
    return parts[-1] if len(parts) > 1 else ""


def _info_employment(info: str) -> str:
    value = info.split("•", 1)[0].strip()
    return value if value.lower() in {"full time", "part time", "contract"} else ""


def _next_url(parser: HTMLParser, current_url: str) -> str | None:
    link = parser.css_first('a[rel~="next"][href], link[rel~="next"][href]')
    if link is None:
        for candidate in parser.css("a[href]"):
            label = " ".join(
                (
                    candidate.text(deep=True),
                    candidate.attributes.get("aria-label", ""),
                )
            ).lower()
            if label.strip() in {"next", "next page", "older"}:
                link = candidate
                break
    if link is None:
        return None
    next_url = urljoin(current_url, link.attributes.get("href", ""))
    current = urlsplit(current_url)
    parsed = urlsplit(next_url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != current.hostname
        or parsed.username
        or parsed.password
    ):
        raise ConnectorFetchError(
            "CareerPlug returned an invalid pagination URL",
            url=current_url,
        )
    return next_url


def _is_empty(parser: HTMLParser) -> bool:
    text = parser.root.text(separator=" ").lower()
    return any(
        phrase in text
        for phrase in ("no current openings", "no jobs found", "no jobs available")
    )


def _string(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
