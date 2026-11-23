"""Trakstar Hire public jobs RSS feed.

The public board at https://aservicesgroup.hire.trakstar.com/ exposed a
``/jobfeeds/{company_slug}`` RSS 2.0 feed with job IDs, dates, locations,
employment type, department, and application links. No next-page link was
observed. TODO: verify against live board whether large feeds are capped.
"""

import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin, urlsplit

from selectolax.parser import HTMLParser

from connectors.base import BaseConnector, ConnectorFetchError
from connectors.registry import register_connector
from connectors.types import NormalizedJob
from connectors.utils.employment import map_employment_type
from connectors.utils.html import html_to_text
from connectors.utils.remote import detect_remote
from connectors.utils.salary import Salary, parse_salary
from jobs.models import Source

_SLUG_PATTERN = re.compile(r"[A-Za-z0-9_-]+\Z")
_CURRENCY_PATTERN = re.compile(
    r"(?:[$€£¥₹]|\b(?:USD|CAD|AUD|EUR|GBP|JPY|INR)\b)",
    re.I,
)


@register_connector
class TrakstarConnector(BaseConnector):
    platform_key = "trakstar"
    kind = "ats"

    def discover_jobs(self, source: Source) -> Iterator[NormalizedJob]:
        base_url, company_slug = _config(source)
        feed_url = f"{base_url}/jobfeeds/{company_slug}"
        response = self.get(feed_url)
        try:
            root = ET.fromstring(response.content)
        except (ET.ParseError, UnicodeDecodeError) as exc:
            raise ConnectorFetchError(
                "Trakstar returned malformed RSS",
                url=feed_url,
            ) from exc

        channel = next(
            (element for element in root if _local_name(element.tag) == "channel"),
            None,
        )
        if _local_name(root.tag) != "rss" or channel is None:
            raise ConnectorFetchError(
                "Trakstar returned an invalid RSS feed",
                url=feed_url,
            )

        seen_ids: set[str] = set()
        for item in channel:
            if _local_name(item.tag) != "item":
                continue
            job = _normalize(item, source, feed_url)
            if not job.external_id:
                raise ConnectorFetchError(
                    "Trakstar returned a job without a stable id",
                    url=feed_url,
                )
            if not job.title:
                raise ConnectorFetchError(
                    f"Trakstar job {job.external_id!r} has no title",
                    url=feed_url,
                )
            if job.external_id not in seen_ids:
                seen_ids.add(job.external_id)
                yield job


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
        raise ValueError("Trakstar source config must contain a valid HTTPS base_url")
    if not company_slug or not _SLUG_PATTERN.fullmatch(company_slug):
        raise ValueError(
            "Trakstar source config must contain a valid company_slug"
        )
    return base_url, company_slug


def _normalize(
    item: ET.Element,
    source: Source,
    feed_url: str,
) -> NormalizedJob:
    values = {_local_name(child.tag): child for child in item}
    title = _child_text(values.get("title"))
    link = _child_text(values.get("link"))
    guid = _child_text(values.get("guid"))
    external_id = _job_id(guid) or _job_id(link)
    source_url = urljoin(feed_url, link or guid) if link or guid else feed_url
    description = _child_text(values.get("description"))
    description_parser = HTMLParser(description)
    job_description = description_parser.css_first("#job_description")
    description_html = job_description.html if job_description else description
    description_text = html_to_text(description_html)
    apply_node = description_parser.css_first("#how_to_apply a[href]")
    apply_url = (
        urljoin(source_url, apply_node.attributes.get("href", ""))
        if apply_node
        else source_url
    )
    location = ", ".join(
        part
        for part in (
            _child_text(values.get("locationCity")),
            _child_text(values.get("locationState")),
            _child_text(values.get("locationCountry")),
        )
        if part
    )
    salary = (
        parse_salary(description_text)
        if _CURRENCY_PATTERN.search(description_text)
        else Salary()
    )
    return NormalizedJob(
        external_id=external_id,
        title=title,
        company=source.name.strip(),
        description=description_text,
        location=location,
        source_url=source_url,
        apply_url=apply_url,
        salary_min=salary.minimum,
        salary_max=salary.maximum,
        salary_currency=salary.currency,
        salary_period=salary.period,
        employment_type=map_employment_type(
            _child_text(values.get("positionType"))
        ),
        is_remote=detect_remote(f"{location} {description_text}"),
        posted_at=_timestamp(_child_text(values.get("pubDate"))),
        department=_child_text(values.get("team")),
        raw={_local_name(child.tag): child.text or "" for child in item},
    )


def _job_id(value: str) -> str:
    if not value:
        return ""
    parsed = urlsplit(value)
    path = parsed.path.rstrip("/")
    return path.rsplit("/", 1)[-1] if path else ""


def _timestamp(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].split(":", 1)[-1]


def _child_text(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return "".join(element.itertext()).strip()


def _string(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
