import json
import re
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlsplit

from connectors.base import BaseConnector, ConnectorFetchError
from connectors.registry import register_connector
from connectors.types import NormalizedJob
from connectors.utils.employment import map_employment_type
from connectors.utils.html import html_to_text
from jobs.models import Source

_COMPANY_SLUG_PATTERN = re.compile(r"[A-Za-z0-9_-]+\Z")
_PAGE_SIZE = 100


@register_connector
class LeverConnector(BaseConnector):
    platform_key = "lever"
    kind = "ats"

    def discover_jobs(self, source: Source) -> Iterator[NormalizedJob]:
        config = source.config
        company_slug = config.get("company_slug") if isinstance(config, dict) else None
        if (
            not isinstance(company_slug, str)
            or not company_slug.strip()
            or not _COMPANY_SLUG_PATTERN.fullmatch(company_slug.strip())
        ):
            raise ValueError(
                "Lever source config must contain a valid company_slug"
            )
        company_slug = company_slug.strip()

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
            raise ValueError("Lever source base_url must be a valid API base URL")

        url = (
            f"{base_url}/v0/postings/{quote(company_slug, safe='')}"
        )
        skip = 0
        seen_ids: set[str] = set()

        while True:
            response = self.get(
                url,
                params={"mode": "json", "skip": skip, "limit": _PAGE_SIZE},
            )
            try:
                payload: Any = response.json()
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ConnectorFetchError(
                    "Lever returned malformed JSON",
                    url=url,
                ) from exc

            if not isinstance(payload, list):
                raise ConnectorFetchError(
                    "Lever returned an invalid postings response",
                    url=url,
                )
            if not payload:
                break

            page_new_count = 0
            for item in payload:
                if not isinstance(item, dict):
                    raise ConnectorFetchError(
                        "Lever returned an invalid job record",
                        url=url,
                    )

                external_id = item.get("id")
                if isinstance(external_id, bool) or not isinstance(
                    external_id, (str, int)
                ):
                    raise ConnectorFetchError(
                        "Lever returned a job without a valid id",
                        url=url,
                    )
                external_id = str(external_id).strip()
                if not external_id:
                    raise ConnectorFetchError(
                        "Lever returned a job without a valid id",
                        url=url,
                    )
                if external_id in seen_ids:
                    continue
                seen_ids.add(external_id)
                page_new_count += 1

                title = _string_value(item.get("text")) or _string_value(
                    item.get("title")
                )
                if not title:
                    raise ConnectorFetchError(
                        f"Lever job {external_id!r} has no title",
                        url=url,
                    )

                categories = item.get("categories")
                if not isinstance(categories, dict):
                    categories = {}

                description_html = _string_value(item.get("description"))
                description_plain = item.get("descriptionPlain")
                if not isinstance(description_plain, str):
                    description_plain = html_to_text(description_html)
                else:
                    description_plain = description_plain.strip()

                workplace_type = _string_value(item.get("workplaceType")).lower()
                is_remote = (
                    True
                    if workplace_type == "remote"
                    else False
                    if workplace_type in {"on-site", "onsite"}
                    else None
                )
                raw = dict(item)
                raw["description_html"] = description_html
                raw["description_plain"] = description_plain

                yield NormalizedJob(
                    external_id=external_id,
                    title=title,
                    company=source.name.strip(),
                    location=_string_value(categories.get("location")),
                    department=_string_value(categories.get("department")),
                    description=description_plain,
                    employment_type=map_employment_type(
                        categories.get("commitment")
                    ),
                    is_remote=is_remote,
                    apply_url=_string_value(item.get("applyUrl")),
                    source_url=_string_value(item.get("hostedUrl")),
                    posted_at=_parse_created_at(item.get("createdAt")),
                    raw=raw,
                )

            if len(payload) < _PAGE_SIZE:
                break
            if page_new_count == 0:
                raise ConnectorFetchError(
                    "Lever returned a repeated postings page",
                    url=url,
                )
            skip += len(payload)


def _parse_created_at(value: object) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _string_value(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""
