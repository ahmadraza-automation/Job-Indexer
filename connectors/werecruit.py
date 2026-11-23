"""WeRecruit public careers pages.

The verified ``https://careers.werecruit.io/en/idiap`` page embeds its
complete listing in ``window.allOffers``; each job detail page contains a
``.description`` block. TODO: verify against live board the apply action and
list limits on other tenants; the observed page included a client-side "see
more" control.
"""

import json
import re
from collections.abc import Iterator, Mapping
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlsplit

from django.utils.dateparse import parse_datetime
from selectolax.parser import HTMLParser

from connectors.base import BaseConnector, ConnectorFetchError
from connectors.registry import register_connector
from connectors.types import NormalizedJob
from connectors.utils.employment import map_employment_type
from connectors.utils.html import html_to_text
from connectors.utils.remote import detect_remote
from jobs.models import Source

_SLUG_PATTERN = re.compile(r"[A-Za-z0-9_-]+\Z")
_OFFERS_MARKER = re.compile(r"window\.allOffers\s*=\s*")


@register_connector
class WeRecruitConnector(BaseConnector):
    platform_key = "werecruit"
    kind = "ats"

    def discover_jobs(self, source: Source) -> Iterator[NormalizedJob]:
        base_url, _ = _config(source)
        response = self.get(base_url)
        offers = _offers(response.text, base_url)
        base_host = urlsplit(base_url).hostname
        seen_ids: set[str] = set()

        for offer in offers:
            external_id = _string(offer.get("Id"))
            title = _string(offer.get("TitleTranslated"))
            job_url = _string(offer.get("Url"))
            if not external_id:
                raise ConnectorFetchError(
                    "WeRecruit returned a job without a stable id",
                    url=base_url,
                )
            if not title:
                raise ConnectorFetchError(
                    f"WeRecruit job {external_id!r} has no title",
                    url=base_url,
                )
            if external_id in seen_ids:
                continue

            source_url = urljoin(base_url, job_url) if job_url else base_url
            parsed_job_url = urlsplit(source_url)
            if (
                parsed_job_url.scheme != "https"
                or parsed_job_url.hostname != base_host
                or parsed_job_url.username
                or parsed_job_url.password
            ):
                raise ConnectorFetchError(
                    "WeRecruit returned an invalid job URL",
                    url=base_url,
                )
            detail = self.get(source_url)
            detail_parser = HTMLParser(detail.text)
            description_node = detail_parser.css_first(".description")
            description_html = description_node.html if description_node else ""
            updated_meta = detail_parser.css_first(
                'meta[name="dcterms.modified"]'
            )
            updated_at = _timestamp(
                updated_meta.attributes.get("content", "") if updated_meta else ""
            )
            apply_value = (
                _string(offer.get("ApplyUrl"))
                or _string(offer.get("ApplicationUrl"))
            )
            apply_url = urljoin(base_url, apply_value) if apply_value else source_url
            location = ", ".join(
                value
                for value in (
                    _string(offer.get("Address_City")),
                    _string(offer.get("Address_Region")),
                    _string(offer.get("Address_State")),
                )
                if value
            )
            company = (
                _string(offer.get("Company_Name"))
                or _string(offer.get("Holding_Name"))
                or source.name.strip()
            )
            department = (
                _string(offer.get("Subsidiary_Name"))
                or _string(offer.get("DepartmentId"))
            )
            seen_ids.add(external_id)
            yield NormalizedJob(
                external_id=external_id,
                title=title,
                company=company,
                description=html_to_text(description_html),
                location=location,
                source_url=source_url,
                apply_url=apply_url,
                employment_type=map_employment_type(
                    _string(offer.get("TimeTranslated"))
                    or _string(offer.get("TypeTranslated"))
                ),
                is_remote=detect_remote(location),
                posted_at=_timestamp(offer.get("PublicationStartDate")),
                updated_at=updated_at,
                department=department,
                raw={
                    "listing": dict(offer),
                    "description_html": description_html,
                    "detail_updated": (
                        updated_meta.attributes.get("content", "")
                        if updated_meta
                        else ""
                    ),
                },
            )


def _config(source: Source) -> tuple[str, str]:
    config = source.config if isinstance(source.config, dict) else {}
    base_url = _string(config.get("base_url"))
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
        raise ValueError(
            "WeRecruit source config must contain a valid HTTPS base_url"
        )
    if not company_slug or not _SLUG_PATTERN.fullmatch(company_slug):
        raise ValueError(
            "WeRecruit source config must contain a valid company_slug"
        )
    return base_url, company_slug


def _offers(html: str, page_url: str) -> list[Mapping[str, Any]]:
    match = _OFFERS_MARKER.search(html)
    if match is None:
        raise ConnectorFetchError(
            "WeRecruit returned HTML without window.allOffers",
            url=page_url,
        )
    try:
        payload, _ = json.JSONDecoder().raw_decode(html, match.end())
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ConnectorFetchError(
            "WeRecruit returned malformed window.allOffers JSON",
            url=page_url,
        ) from exc
    if not isinstance(payload, list) or any(
        not isinstance(offer, Mapping) for offer in payload
    ):
        raise ConnectorFetchError(
            "WeRecruit returned an invalid window.allOffers list",
            url=page_url,
        )
    return payload


def _timestamp(value: object) -> datetime | None:
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
