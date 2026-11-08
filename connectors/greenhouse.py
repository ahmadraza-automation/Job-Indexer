import json
import re
from collections.abc import Iterator
from datetime import datetime
from html import unescape
from typing import Any
from urllib.parse import quote

from django.utils.dateparse import parse_datetime

from connectors.base import BaseConnector, ConnectorFetchError
from connectors.registry import register_connector
from connectors.types import NormalizedJob
from connectors.utils.html import html_to_text
from jobs.models import Source

_API_ROOT = "https://boards-api.greenhouse.io/v1/boards"
_BOARD_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]+\Z")


@register_connector
class GreenhouseConnector(BaseConnector):
    platform_key = "greenhouse"
    kind = "ats"

    def discover_jobs(self, source: Source) -> Iterator[NormalizedJob]:
        config = source.config
        token = config.get("board_token") if isinstance(config, dict) else None
        if (
            not isinstance(token, str)
            or not token.strip()
            or not _BOARD_TOKEN_PATTERN.fullmatch(token.strip())
        ):
            raise ValueError(
                "Greenhouse source config must contain a valid board_token"
            )
        token = token.strip()
        url = f"{_API_ROOT}/{quote(token, safe='')}/jobs"

        response = self.get(url, params={"content": "true"})
        try:
            payload: Any = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ConnectorFetchError(
                "Greenhouse returned malformed JSON",
                url=url,
            ) from exc

        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            raise ConnectorFetchError(
                "Greenhouse returned an invalid jobs response",
                url=url,
            )

        seen_ids: set[str] = set()
        for item in payload["jobs"]:
            if not isinstance(item, dict):
                raise ConnectorFetchError(
                    "Greenhouse returned an invalid job record",
                    url=url,
                )

            external_id = item.get("id")
            if isinstance(external_id, bool) or not isinstance(
                external_id, (str, int)
            ):
                raise ConnectorFetchError(
                    "Greenhouse returned a job without a valid id",
                    url=url,
                )
            external_id = str(external_id).strip()
            if not external_id:
                raise ConnectorFetchError(
                    "Greenhouse returned a job without a valid id",
                    url=url,
                )
            if external_id in seen_ids:
                continue
            seen_ids.add(external_id)

            title = item.get("title")
            if not isinstance(title, str) or not title.strip():
                raise ConnectorFetchError(
                    f"Greenhouse job {external_id!r} has no title",
                    url=url,
                )

            location_data = item.get("location")
            location = (
                location_data.get("name", "")
                if isinstance(location_data, dict)
                else ""
            )
            if not isinstance(location, str):
                location = ""

            department = _department_names(item.get("departments"))
            content = item.get("content")
            description = (
                html_to_text(unescape(content)) if isinstance(content, str) else ""
            )
            posted_at = _parse_timestamp(item.get("first_published"))
            updated_at = _parse_timestamp(item.get("updated_at"))
            raw = dict(item)
            raw["department"] = department

            yield NormalizedJob(
                external_id=external_id,
                title=title.strip(),
                company=source.name.strip(),
                location=location.strip(),
                department=department,
                description=description,
                apply_url=_string_value(item.get("absolute_url")),
                source_url=_string_value(item.get("absolute_url")),
                posted_at=posted_at,
                updated_at=updated_at,
                raw=raw,
            )


def _department_names(value: object) -> str:
    if not isinstance(value, list):
        return ""
    names = [
        item["name"].strip()
        for item in value
        if isinstance(item, dict)
        and isinstance(item.get("name"), str)
        and item["name"].strip()
    ]
    return ", ".join(dict.fromkeys(names))


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return parse_datetime(value)
    except ValueError:
        return None


def _string_value(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""
