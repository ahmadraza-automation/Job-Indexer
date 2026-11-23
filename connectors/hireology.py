"""Hireology public careers pages.

The public board at https://careers.hireology.com/365labs renders
``data-qa-id=job-card`` cards; each card links to a detail page containing
schema.org JobPosting JSON-LD. TODO: verify pagination markup against a board
with multiple pages.
"""

import re
from collections.abc import Iterator
from urllib.parse import quote, urljoin, urlsplit

from selectolax.parser import HTMLParser

from connectors.base import BaseConnector, ConnectorFetchError
from connectors.registry import register_connector
from connectors.types import NormalizedJob
from connectors.utils.jsonld import extract_job_postings
from jobs.models import Source

_SLUG_PATTERN = re.compile(r"[A-Za-z0-9_-]+\Z")
_JOB_ID_PATTERN = re.compile(r"/(\d+)/description(?:/|$)")
_MAX_PAGES = 100


@register_connector
class HireologyConnector(BaseConnector):
    platform_key = "hireology"
    kind = "ats"

    def discover_jobs(self, source: Source) -> Iterator[NormalizedJob]:
        base_url, slug = _config(source)
        board_url = urljoin(base_url.rstrip("/") + "/", f"{quote(slug, safe='')}/")
        current_url = board_url
        seen_pages: set[str] = set()
        seen_jobs: set[str] = set()

        for _ in range(_MAX_PAGES):
            if current_url in seen_pages:
                raise ConnectorFetchError(
                    "Hireology returned a pagination loop",
                    url=current_url,
                )
            seen_pages.add(current_url)
            response = self.get(current_url)
            parser = HTMLParser(response.text)
            cards = parser.css('[data-qa-id="job-card"]')
            for card in cards:
                link = card.css_first('a[data-qa-id="job-card-title"][href]')
                if link is None:
                    raise ConnectorFetchError(
                        "Hireology returned a job card without a detail link",
                        url=current_url,
                    )
                detail_url = urljoin(current_url, link.attributes.get("href", ""))
                parsed_detail = urlsplit(detail_url)
                match = _JOB_ID_PATTERN.search(parsed_detail.path)
                if (
                    parsed_detail.scheme != "https"
                    or parsed_detail.hostname != urlsplit(board_url).hostname
                    or not parsed_detail.path.startswith(f"/{slug}/")
                    or match is None
                ):
                    raise ConnectorFetchError(
                        "Hireology returned an invalid job detail link",
                        url=current_url,
                    )
                listing_id = match.group(1)
                if listing_id in seen_jobs:
                    continue
                seen_jobs.add(listing_id)
                detail_response = self.get(detail_url)
                jobs = list(
                    extract_job_postings(detail_response.text, page_url=detail_url)
                )
                if len(jobs) != 1:
                    raise ConnectorFetchError(
                        "Hireology returned an invalid job detail page",
                        url=detail_url,
                    )
                job = jobs[0]
                if not job.external_id:
                    raise ConnectorFetchError(
                        "Hireology returned a job without a stable id",
                        url=detail_url,
                    )
                job.source_url = detail_url
                job.apply_url = f"{detail_url.split('?', 1)[0]}#application"
                if not job.company:
                    job.company = source.name.strip()
                yield job

            next_url = _next_url(parser, current_url)
            if next_url is None:
                if not cards and _is_empty(parser):
                    return
                if not cards:
                    raise ConnectorFetchError(
                        "Hireology returned malformed public jobs HTML",
                        url=current_url,
                    )
                return
            current_url = next_url

        raise ConnectorFetchError(
            "Hireology exceeded the public jobs page limit",
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
        raise ValueError("Hireology source config must contain a valid HTTPS base_url")
    if not slug or not _SLUG_PATTERN.fullmatch(slug):
        raise ValueError("Hireology source config must contain a valid company_slug")
    return base_url, slug


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
            "Hireology returned an invalid pagination URL",
            url=current_url,
        )
    return next_url


def _is_empty(parser: HTMLParser) -> bool:
    if parser.css_first('[data-qa-id="jobs-results"]'):
        output = parser.css_first("output")
        if output is not None and "0 jobs" in output.text(deep=True).lower():
            return True
    text = parser.root.text(separator=" ").lower()
    return any(
        phrase in text
        for phrase in ("no current openings", "no jobs found", "no jobs available")
    )


def _string(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""
