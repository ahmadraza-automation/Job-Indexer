"""GoHire public job boards and JobPosting pages.

The public page at https://jobs.gohire.io/iproov-fzoa1liw/head-of-product-innovation-86015/
contains schema.org JobPosting JSON-LD; the sampled board at
https://jobs.gohire.io/yourparkingspace-gnf5tges/ was empty. TODO: verify
listing-card and paging behavior against a board with open jobs.
"""

import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

from selectolax.parser import HTMLParser

from connectors.base import BaseConnector, ConnectorFetchError
from connectors.registry import register_connector
from connectors.types import NormalizedJob
from connectors.utils.jsonld import extract_job_postings
from jobs.models import Source

_SLUG_PATTERN = re.compile(r"[A-Za-z0-9_-]+\Z")
_DETAIL_PATTERN = re.compile(r"[^/]+-(\d+)/?$")
_MAX_PAGES = 100


@register_connector
class GoHireConnector(BaseConnector):
    platform_key = "gohire"
    kind = "ats"

    def discover_jobs(self, source: Source) -> Iterator[NormalizedJob]:
        base_url, company_slug = _config(source)
        board_url = urljoin(
            base_url.rstrip("/") + "/",
            f"{quote(company_slug, safe='')}/",
        )
        current_url = board_url
        page_number = 1
        seen_pages: set[str] = set()
        seen_ids: set[str] = set()

        for _ in range(_MAX_PAGES):
            page_key = f"{current_url}#page={page_number}"
            if page_key in seen_pages:
                raise ConnectorFetchError(
                    "GoHire returned a pagination loop",
                    url=current_url,
                )
            seen_pages.add(page_key)
            if page_number == 1:
                response = self.get(current_url)
            else:
                response = self.request(
                    "POST",
                    current_url,
                    data={
                        "page": str(page_number),
                        "remoteDdValue": "all_Id",
                        "typeDdValue": "0",
                        "jobTitleSearched": "",
                        "cityOrCountrySearched": "",
                    },
                )
            parser = HTMLParser(response.text)
            page_number = _form_page_number(parser, page_number)
            links = _detail_links(parser, current_url, company_slug)
            for detail_url, route_id in links:
                if route_id in seen_ids:
                    continue
                detail_response = self.get(detail_url)
                jobs = list(
                    extract_job_postings(
                        detail_response.text,
                        page_url=detail_url,
                    )
                )
                if len(jobs) != 1:
                    raise ConnectorFetchError(
                        "GoHire returned an invalid JobPosting detail page",
                        url=detail_url,
                    )
                job = jobs[0]
                if not job.title:
                    raise ConnectorFetchError(
                        f"GoHire job {route_id!r} has no title",
                        url=detail_url,
                    )
                job.external_id = job.external_id or route_id
                if not job.external_id:
                    raise ConnectorFetchError(
                        "GoHire returned a job without a stable id",
                        url=detail_url,
                    )
                job.source_url = job.source_url or detail_url
                job.apply_url = job.apply_url or job.source_url
                if not job.company:
                    job.company = source.name.strip()
                seen_ids.add(route_id)
                yield job

            next_url = _next_url(parser, current_url)
            if next_url is not None:
                current_url = next_url
                continue
            if _has_next_post_page(parser, page_number):
                page_number += 1
                continue
            if not links and _is_empty(parser):
                return
            if not links and page_number > 1:
                return
            if not links:
                raise ConnectorFetchError(
                    "GoHire returned malformed public board HTML",
                    url=current_url,
                )
            return

        raise ConnectorFetchError(
            "GoHire exceeded the public board page limit",
            url=current_url,
        )


def _config(source: Source) -> tuple[str, str]:
    config = source.config if isinstance(source.config, dict) else {}
    base_url = _string(config.get("base_url")).rstrip("/")
    company_slug = _string(config.get("company_slug"))
    parsed = urlsplit(base_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("GoHire source config must contain a valid HTTPS base_url")
    if not company_slug or not _SLUG_PATTERN.fullmatch(company_slug):
        raise ValueError("GoHire source config must contain a valid company_slug")
    return base_url, company_slug


def _detail_links(
    parser: HTMLParser,
    page_url: str,
    company_slug: str,
) -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    seen: set[str] = set()
    board_host = urlsplit(page_url).hostname
    for link in parser.css("a[href]"):
        detail_url = urljoin(page_url, link.attributes.get("href", ""))
        parsed = urlsplit(detail_url)
        parts = [part for part in parsed.path.split("/") if part]
        if (
            parsed.scheme != "https"
            or parsed.hostname != board_host
            or not parts
            or parts[0] != company_slug
        ):
            continue
        match = _DETAIL_PATTERN.fullmatch(parts[-1])
        if match is None:
            continue
        route_id = match.group(1)
        if detail_url not in seen:
            seen.add(detail_url)
            links.append((detail_url, route_id))
    return links


def _next_url(parser: HTMLParser, current_url: str) -> str | None:
    link = parser.css_first('a[rel~="next"][href]')
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
            "GoHire returned an invalid pagination URL",
            url=current_url,
        )
    return next_url


def _has_next_post_page(parser: HTMLParser, current_page: int) -> bool:
    form = parser.css_first("form#pagingFormId")
    if form is None:
        return False
    page_input = form.css_first('input[name="page"]')
    if page_input is None:
        return False
    try:
        page_number = int(page_input.attributes.get("value", "1"))
    except ValueError:
        return False
    if page_number != current_page:
        return False
    for control in parser.css("button, a"):
        label = " ".join(
            (
                control.text(deep=True),
                control.attributes.get("aria-label", ""),
            )
        ).strip().lower()
        classes = control.attributes.get("class", "").lower()
        if label in {"next", "next page", "›", ">"} and "pagination" in classes:
            return True
    return False


def _form_page_number(parser: HTMLParser, fallback: int) -> int:
    form = parser.css_first("form#pagingFormId")
    page_input = form.css_first('input[name="page"]') if form else None
    if page_input is None:
        return fallback
    try:
        page_number = int(page_input.attributes.get("value", ""))
    except ValueError:
        return fallback
    return page_number if page_number > 0 else fallback


def _is_empty(parser: HTMLParser) -> bool:
    text = parser.root.text(separator=" ").lower()
    return "there are currently no job openings" in text


def _string(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
