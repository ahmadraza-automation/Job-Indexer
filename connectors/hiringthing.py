"""HiringThing public RSS feed.

The public board at https://skijapan.hiringthing.com links to
``/api/rss.xml``; the feed is RSS 2.0 with ``media:description`` HTML.
TODO: verify whether live feeds expose additional pages beyond the observed RSS.
"""

import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin, urlsplit

from connectors.base import BaseConnector, ConnectorFetchError
from connectors.registry import register_connector
from connectors.types import NormalizedJob
from connectors.utils.employment import map_employment_type
from connectors.utils.html import html_to_text
from connectors.utils.remote import detect_remote
from jobs.models import Source

_SLUG_PATTERN = re.compile(r"[A-Za-z0-9_-]+\Z")
_JOB_ID_PATTERN = re.compile(r"/job/([^/]+)/?")
_MEDIA_DESCRIPTION = "{http://search.yahoo.com/mrss/}description"
_ATOM_LINK = "{http://www.w3.org/2005/Atom}link"
_MAX_PAGES = 100


@register_connector
class HiringThingConnector(BaseConnector):
    platform_key = "hiringthing"
    kind = "ats"

    def discover_jobs(self, source: Source) -> Iterator[NormalizedJob]:
        base_url, _ = _config(source)
        current_url = urljoin(base_url.rstrip("/") + "/", "api/rss.xml")
        visited: set[str] = set()
        seen_ids: set[str] = set()

        for _ in range(_MAX_PAGES):
            if current_url in visited:
                raise ConnectorFetchError(
                    "HiringThing returned a feed pagination loop",
                    url=current_url,
                )
            visited.add(current_url)
            response = self.get(current_url)
            try:
                root = ET.fromstring(response.content)
            except (ET.ParseError, UnicodeDecodeError) as exc:
                raise ConnectorFetchError(
                    "HiringThing returned malformed RSS",
                    url=current_url,
                ) from exc
            channel = next(
                (node for node in root if _local_name(node.tag) == "channel"),
                None,
            )
            if _local_name(root.tag) != "rss" or channel is None:
                raise ConnectorFetchError(
                    "HiringThing returned an invalid RSS feed",
                    url=current_url,
                )

            items = [
                node for node in channel if _local_name(node.tag) == "item"
            ]
            for item in items:
                job = _normalize(item, source, current_url)
                if not job.external_id:
                    raise ConnectorFetchError(
                        "HiringThing returned a job without a stable id",
                        url=current_url,
                    )
                if not job.title:
                    raise ConnectorFetchError(
                        f"HiringThing job {job.external_id!r} has no title",
                        url=current_url,
                    )
                if job.external_id not in seen_ids:
                    seen_ids.add(job.external_id)
                    yield job

            next_url = _next_feed_url(channel, current_url)
            if next_url is None:
                return
            current_url = next_url

        raise ConnectorFetchError(
            "HiringThing exceeded the public RSS page limit",
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
            "HiringThing source config must contain a valid HTTPS base_url"
        )
    if not slug or not _SLUG_PATTERN.fullmatch(slug):
        raise ValueError(
            "HiringThing source config must contain a valid company_slug"
        )
    return base_url, slug


def _normalize(item: ET.Element, source: Source, page_url: str) -> NormalizedJob:
    values = {_local_name(child.tag): child for child in item}
    title = _child_text(values.get("title"))
    link = _child_text(values.get("link"))
    parsed_link = urlsplit(link)
    match = _JOB_ID_PATTERN.search(parsed_link.path)
    guid = _child_text(values.get("guid"))
    if match is None:
        match = _JOB_ID_PATTERN.search(urlsplit(guid).path)
    external_id = match.group(1) if match else ""
    media_description = next(
        (child for child in item if child.tag == _MEDIA_DESCRIPTION),
        None,
    )
    description = (
        (media_description.text or "")
        if media_description is not None
        else _child_text(values.get("description"))
    )
    source_url = urljoin(page_url, link) if link else page_url
    category = _child_text(values.get("category"))
    location = _child_text(values.get("location"))
    return NormalizedJob(
        external_id=external_id,
        title=title,
        company=source.name.strip(),
        description=html_to_text(description),
        location=location,
        source_url=source_url,
        apply_url=source_url,
        employment_type=map_employment_type(category),
        is_remote=detect_remote(location),
        posted_at=_timestamp(_child_text(values.get("pubDate"))),
        raw={_local_name(child.tag): child.text or "" for child in item},
    )


def _next_feed_url(channel: ET.Element, current_url: str) -> str | None:
    next_value = next(
        (
            element.attrib.get("href", "").strip()
            for element in channel
            if element.tag == _ATOM_LINK
            and element.attrib.get("rel", "").lower() == "next"
            and element.attrib.get("href", "").strip()
        ),
        "",
    )
    if not next_value:
        return None
    next_url = urljoin(current_url, next_value)
    current = urlsplit(current_url)
    parsed = urlsplit(next_url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != current.hostname
        or parsed.username
        or parsed.password
    ):
        raise ConnectorFetchError(
            "HiringThing returned an invalid RSS pagination URL",
            url=current_url,
        )
    return next_url


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
