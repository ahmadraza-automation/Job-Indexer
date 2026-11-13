import json
import re
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlsplit

from django.utils.dateparse import parse_datetime

from connectors.base import BaseConnector, ConnectorFetchError
from connectors.registry import register_connector
from connectors.types import NormalizedJob
from connectors.utils.employment import map_employment_type
from connectors.utils.html import html_to_text
from jobs.models import Source

_BOARD_NAME_PATTERN = re.compile(r"[A-Za-z0-9_-]+\Z")


@register_connector
class AshbyConnector(BaseConnector):
    platform_key = "ashby"
    kind = "ats"

    def discover_jobs(self, source: Source) -> Iterator[NormalizedJob]:
        config = source.config
        board_name = config.get("board_name") if isinstance(config, dict) else None
        if (
            not isinstance(board_name, str)
            or not board_name.strip()
            or not _BOARD_NAME_PATTERN.fullmatch(board_name.strip())
        ):
            raise ValueError("Ashby source config must contain a valid board_name")
        board_name = board_name.strip()

        base_url = source.base_url.strip().rstrip("/")
        parsed_base_url = urlsplit(base_url)
        if (
            parsed_base_url.scheme not in {"http", "https"}
            or not parsed_base_url.netloc
            or parsed_base_url.username
            or parsed_base_url.password
            or parsed_base_url.query
            or parsed_base_url.fragment
        ):
            raise ValueError("Ashby source base_url must be a valid API base URL")

        url = (
            f"{base_url}/posting-api/job-board/{quote(board_name, safe='')}"
        )
        response = self.get(url, params={"includeCompensation": "true"})
        try:
            payload: Any = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ConnectorFetchError(
                "Ashby returned malformed JSON",
                url=url,
            ) from exc

        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            raise ConnectorFetchError(
                "Ashby returned an invalid jobs response",
                url=url,
            )

        seen_ids: set[str] = set()
        for item in payload["jobs"]:
            if not isinstance(item, dict):
                raise ConnectorFetchError(
                    "Ashby returned an invalid job record",
                    url=url,
                )
            if item.get("isListed") is False:
                continue

            title = _string_value(item.get("title"))
            if not title:
                raise ConnectorFetchError(
                    "Ashby returned a job without a valid title",
                    url=url,
                )

            job_url = _string_value(item.get("jobUrl"))
            raw_id = item.get("id")
            if isinstance(raw_id, bool) or not isinstance(raw_id, (str, int)):
                external_id = ""
            else:
                external_id = str(raw_id).strip()
            external_id = external_id or job_url
            if not external_id:
                raise ConnectorFetchError(
                    f"Ashby job {title!r} has no valid id or jobUrl",
                    url=url,
                )
            if external_id in seen_ids:
                continue
            seen_ids.add(external_id)

            location = _string_value(item.get("location"))
            secondary_locations = _secondary_locations(
                item.get("secondaryLocations")
            )
            if secondary_locations:
                location = ", ".join(
                    dict.fromkeys(([location] if location else []) + secondary_locations)
                )

            description_html = _string_value(item.get("descriptionHtml"))
            description_plain = item.get("descriptionPlain")
            if not isinstance(description_plain, str):
                description_plain = html_to_text(description_html)
            else:
                description_plain = description_plain.strip()

            is_remote = item.get("isRemote")
            if not isinstance(is_remote, bool):
                is_remote = None

            raw = dict(item)

            yield NormalizedJob(
                external_id=external_id,
                title=title,
                company=source.name.strip(),
                location=location,
                department=_string_value(item.get("department")),
                description=description_plain,
                employment_type=map_employment_type(item.get("employmentType")),
                is_remote=is_remote,
                apply_url=_string_value(item.get("applyUrl")),
                source_url=job_url,
                posted_at=_parse_published_at(item.get("publishedAt")),
                raw=raw,
            )


def _secondary_locations(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(
        dict.fromkeys(
            location
            for item in value
            if isinstance(item, dict)
            if (location := _string_value(item.get("location")))
        )
    )


def _parse_published_at(value: object) -> datetime | None:
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
