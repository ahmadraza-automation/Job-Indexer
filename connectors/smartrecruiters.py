import json
import re
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx
from django.utils.dateparse import parse_datetime

from connectors.base import BaseConnector, ConnectorFetchError
from connectors.registry import register_connector
from connectors.types import NormalizedJob
from connectors.utils.employment import map_employment_type
from connectors.utils.html import html_to_text
from jobs.models import Source

_API_ROOT = "https://api.smartrecruiters.com/v1/companies"
_COMPANY_PATTERN = re.compile(r"[A-Za-z0-9_-]+\Z")
_PAGE_SIZE = 100


@register_connector
class SmartRecruitersConnector(BaseConnector):
    platform_key = "smartrecruiters"
    kind = "ats"

    def discover_jobs(self, source: Source) -> Iterator[NormalizedJob]:
        config = source.config
        company = config.get("company") if isinstance(config, dict) else None
        if (
            not isinstance(company, str)
            or not company.strip()
            or not _COMPANY_PATTERN.fullmatch(company.strip())
        ):
            raise ValueError(
                "SmartRecruiters source config must contain a valid company"
            )
        company = company.strip()
        base_url = f"{_API_ROOT}/{quote(company, safe='')}/postings"

        offset = 0
        seen_ids: set[str] = set()
        while True:
            response = self.get(
                base_url,
                params={"limit": _PAGE_SIZE, "offset": offset},
            )
            payload = _response_json(response, base_url, "postings")
            if (
                not isinstance(payload, dict)
                or not isinstance(payload.get("content"), list)
                or isinstance(payload.get("totalFound"), bool)
                or not isinstance(payload.get("totalFound"), int)
                or payload["totalFound"] < 0
            ):
                raise ConnectorFetchError(
                    "SmartRecruiters returned an invalid postings response",
                    url=base_url,
                )

            postings = payload["content"]
            total_found = payload["totalFound"]
            if not postings:
                if offset < total_found:
                    raise ConnectorFetchError(
                        "SmartRecruiters returned an incomplete postings page",
                        url=base_url,
                    )
                break

            page_new_count = 0
            for posting in postings:
                if not isinstance(posting, dict):
                    raise ConnectorFetchError(
                        "SmartRecruiters returned an invalid job record",
                        url=base_url,
                    )
                raw_id = posting.get("id")
                if isinstance(raw_id, bool) or not isinstance(raw_id, (str, int)):
                    raise ConnectorFetchError(
                        "SmartRecruiters returned a job without a valid id",
                        url=base_url,
                    )
                external_id = str(raw_id).strip()
                if not external_id:
                    raise ConnectorFetchError(
                        "SmartRecruiters returned a job without a valid id",
                        url=base_url,
                    )
                if external_id in seen_ids:
                    continue
                seen_ids.add(external_id)
                page_new_count += 1

                detail_url = f"{base_url}/{quote(external_id, safe='')}"
                detail_response = self.get(detail_url)
                detail = _response_json(detail_response, detail_url, "posting")
                if not isinstance(detail, dict):
                    raise ConnectorFetchError(
                        "SmartRecruiters returned an invalid posting detail",
                        url=detail_url,
                    )

                title = _string_value(detail.get("name")) or _string_value(
                    posting.get("name")
                )
                if not title:
                    raise ConnectorFetchError(
                        f"SmartRecruiters job {external_id!r} has no title",
                        url=detail_url,
                    )

                yield _normalize_posting(
                    posting=posting,
                    detail=detail,
                    source=source,
                    external_id=external_id,
                    title=title,
                )

            offset += len(postings)
            if offset >= total_found:
                break
            if page_new_count == 0:
                raise ConnectorFetchError(
                    "SmartRecruiters returned a repeated postings page",
                    url=base_url,
                )


def _normalize_posting(
    *,
    posting: dict[str, Any],
    detail: dict[str, Any],
    source: Source,
    external_id: str,
    title: str,
) -> NormalizedJob:
    location = _object_value(detail.get("location")) or _object_value(
        posting.get("location")
    )
    location_parts = [
        _string_value(location.get("city")),
        _string_value(location.get("region")),
        _string_value(location.get("country")),
    ]

    department = _label(detail.get("department")) or _label(
        posting.get("department")
    )
    employment_type = _label(detail.get("typeOfEmployment")) or _label(
        posting.get("typeOfEmployment")
    )
    sections = _object_value(_object_value(detail.get("jobAd")).get("sections"))
    job_description = _object_value(sections.get("jobDescription"))
    description = html_to_text(job_description.get("text"))
    posted_at = _parse_timestamp(
        detail.get("releasedDate") or posting.get("releasedDate")
    )
    apply_url = _string_value(detail.get("applyUrl"))
    source_url = _string_value(detail.get("postingUrl")) or _string_value(
        posting.get("ref")
    )
    raw = dict(posting)
    raw["detail"] = detail

    return NormalizedJob(
        external_id=external_id,
        title=title,
        company=source.name.strip(),
        location=", ".join(part for part in location_parts if part),
        department=department,
        description=description,
        employment_type=map_employment_type(employment_type),
        is_remote=(
            location.get("remote")
            if isinstance(location.get("remote"), bool)
            else None
        ),
        apply_url=apply_url,
        source_url=source_url,
        posted_at=posted_at,
        raw=raw,
    )


def _response_json(response: httpx.Response, url: str, resource: str) -> Any:
    try:
        return response.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ConnectorFetchError(
            f"SmartRecruiters returned malformed {resource} JSON",
            url=url,
        ) from exc


def _object_value(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _label(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return _string_value(value.get("label"))
    return ""


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = parse_datetime(value)
    except ValueError:
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _string_value(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""
