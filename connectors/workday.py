import json
import re
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from django.utils.dateparse import parse_datetime

from connectors.base import BaseConnector, ConnectorFetchError
from connectors.registry import register_connector
from connectors.types import NormalizedJob
from connectors.utils.employment import map_employment_type
from connectors.utils.html import html_to_text
from jobs.models import Source

_SLUG_PATTERN = re.compile(r"[A-Za-z0-9_-]+\Z")
_WD_HOST_PATTERN = re.compile(r"wd\d+\Z", re.IGNORECASE)
_LIMIT = 20
_DEFAULT_MAX_PAGES = 100


@register_connector
class WorkdayConnector(BaseConnector):
    platform_key = "workday"
    kind = "ats"

    def discover_jobs(self, source: Source) -> Iterator[NormalizedJob]:
        tenant, wd_host, site = _config(source)
        fetch_details = _fetch_details(source.config)
        max_pages = _max_pages(source.config)
        request_delay = _request_delay(source.config)
        if request_delay is not None:
            self.rate_limit_interval = request_delay

        base_url = (
            f"https://{quote(tenant, safe='')}.{quote(wd_host.lower(), safe='')}"
            ".myworkdayjobs.com"
        )
        list_url = (
            f"{base_url}/wday/cxs/{quote(tenant, safe='')}/"
            f"{quote(site, safe='')}/jobs"
        )
        seen_ids: set[str] = set()
        offset = 0
        page_count = 0

        while page_count < max_pages:
            response = self.request(
                "POST",
                list_url,
                json={
                    "appliedFacets": {},
                    "limit": _LIMIT,
                    "offset": offset,
                    "searchText": "",
                },
            )
            try:
                payload: Any = response.json()
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ConnectorFetchError(
                    "Workday returned malformed JSON",
                    url=list_url,
                ) from exc

            if not isinstance(payload, dict):
                raise ConnectorFetchError(
                    "Workday returned an invalid jobs response",
                    url=list_url,
                )
            total = payload.get("total")
            postings = payload.get("jobPostings")
            if not isinstance(postings, list) or (
                isinstance(total, bool)
                or not isinstance(total, (int, float))
            ):
                raise ConnectorFetchError(
                    "Workday returned an invalid jobs response",
                    url=list_url,
                )
            total = int(total)
            if total < 0:
                raise ConnectorFetchError(
                    "Workday returned an invalid jobs response",
                    url=list_url,
                )
            page_count += 1

            for item in postings:
                if not isinstance(item, dict):
                    raise ConnectorFetchError(
                        "Workday returned an invalid job record",
                        url=list_url,
                    )
                normalized = self._normalize_job(
                    source=source,
                    tenant=tenant,
                    base_url=base_url,
                    site=site,
                    item=item,
                    fetch_details=fetch_details,
                    seen_ids=seen_ids,
                )
                if normalized is not None:
                    yield normalized

            if total == 0 or not postings:
                break
            offset += _LIMIT
            if offset >= total:
                break

    def _normalize_job(
        self,
        *,
        source: Source,
        tenant: str,
        base_url: str,
        site: str,
        item: dict[str, Any],
        fetch_details: bool,
        seen_ids: set[str],
    ) -> NormalizedJob | None:
        external_path = _string_value(item.get("externalPath"))
        detail: dict[str, Any] = {}

        if fetch_details and external_path:
            detail_url = (
                f"{base_url}/wday/cxs/{quote(tenant, safe='')}/"
                f"{quote(site, safe='')}{external_path}"
            )
            try:
                detail_response = self.get(detail_url)
                detail_payload: Any = detail_response.json()
            except ConnectorFetchError:
                detail_payload = None
            except (json.JSONDecodeError, UnicodeDecodeError):
                detail_payload = None
            if isinstance(detail_payload, dict):
                info = detail_payload.get("jobPostingInfo")
                if isinstance(info, dict):
                    detail = info

        title = _string_value(detail.get("title")) or _string_value(item.get("title"))
        if not title:
            raise ConnectorFetchError(
                "Workday returned a job without a valid title",
                url=base_url,
            )

        # External IDs must come only from list data so detail availability does
        # not change a posting's identity.
        external_id = external_path
        if not external_id:
            bullet_fields = item.get("bulletFields")
            if isinstance(bullet_fields, list):
                for value in bullet_fields:
                    candidate = _string_value(value)
                    if candidate:
                        external_id = candidate
                        break
        if not external_id:
            raise ConnectorFetchError(
                f"Workday job {title!r} has no valid external id",
                url=base_url,
            )
        if external_id in seen_ids:
            return None
        seen_ids.add(external_id)

        location = _location(detail, item)
        remote = _remote_flag(detail, location)
        employment_type = map_employment_type(
            detail.get("timeType")
            or detail.get("employmentType")
            or detail.get("timeTypeText")
        )

        description_html = _string_value(detail.get("description"))
        description = html_to_text(description_html)
        if not description:
            description = _string_value(detail.get("jobDescription"))
            description = html_to_text(description)

        source_url = _string_value(detail.get("externalUrl"))
        if not source_url and external_path:
            source_url = (
                f"{base_url}/{quote(site, safe='')}"
                f"{quote(external_path, safe='/')}"
            )

        posted_at = _parse_posted_date(
            detail.get("startDate") or item.get("postedOn")
        )
        if detail.get("startDate") and posted_at is None:
            posted_at = _parse_posted_date(item.get("postedOn"))

        raw: dict[str, Any] = {
            "list": dict(item),
            "detail": dict(detail) if detail else None,
        }
        return NormalizedJob(
            external_id=external_id,
            title=title,
            company=source.name.strip() or tenant,
            location=location,
            is_remote=remote,
            employment_type=employment_type,
            description=description,
            apply_url=source_url,
            source_url=source_url,
            posted_at=posted_at,
            raw=raw,
        )


def _config(source: Source) -> tuple[str, str, str]:
    config = source.config if isinstance(source.config, dict) else {}
    tenant = _string_value(config.get("tenant"))
    wd_host = _string_value(config.get("wd_host"))
    site = _string_value(config.get("site"))
    if not tenant or not _SLUG_PATTERN.fullmatch(tenant):
        raise ValueError(
            "Workday source config must contain a valid tenant"
        )
    if not wd_host or not _WD_HOST_PATTERN.fullmatch(wd_host):
        raise ValueError(
            "Workday source config must contain a valid wd_host"
        )
    if not site or not _SLUG_PATTERN.fullmatch(site):
        raise ValueError(
            "Workday source config must contain a valid site"
        )
    return tenant, wd_host, site


def _fetch_details(config: object) -> bool:
    if not isinstance(config, dict):
        return True
    value = config.get("fetch_details", True)
    if not isinstance(value, bool):
        raise ValueError(
            "Workday source config fetch_details must be true or false"
        )
    return value


def _max_pages(config: object) -> int:
    if not isinstance(config, dict):
        return _DEFAULT_MAX_PAGES
    value = config.get("max_pages", _DEFAULT_MAX_PAGES)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(
            "Workday source config max_pages must be a positive integer"
        )
    return value


def _request_delay(config: object) -> float | None:
    if not isinstance(config, dict):
        return None
    value = config.get("request_delay_seconds")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            "Workday source config request_delay_seconds must be non-negative"
        )
    delay = float(value)
    if delay < 0:
        raise ValueError(
            "Workday source config request_delay_seconds must be non-negative"
        )
    return delay


def _location(detail: dict[str, Any], item: dict[str, Any]) -> str:
    location = _string_value(detail.get("location"))
    if location:
        return location
    locations = detail.get("locations")
    if isinstance(locations, list):
        names = [_string_value(value) for value in locations]
        names = [name for name in names if name]
        if names:
            return ", ".join(dict.fromkeys(names))
    return _string_value(item.get("locationsText"))


def _remote_flag(detail: dict[str, Any], location: str) -> bool | None:
    remote_type = _string_value(
        detail.get("remoteType") or detail.get("remote")
    ).lower()
    if "remote" in remote_type:
        return True
    if remote_type and any(
        term in remote_type for term in ("on-site", "onsite", "office", "hybrid")
    ):
        return False
    text = location.lower()
    if "remote" in text:
        return True
    if any(term in text for term in ("on-site", "onsite", "office")):
        return False
    return None


def _parse_posted_date(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    relative = re.fullmatch(
        r"(?i)posted\s+(today|(\d+|\d+\+)\s+days?\s+ago)",
        text,
    )
    if relative:
        label = relative.group(1).lower()
        if label == "today":
            days = 0
        else:
            match = re.search(r"\d+", label)
            days = int(match.group()) if match else 0
        approximate = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ) - timedelta(days=days)
        return approximate

    parsed = parse_datetime(text)
    if parsed is not None:
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    try:
        parsed_date = datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return None
    return parsed_date.replace(tzinfo=timezone.utc)


def _string_value(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""
