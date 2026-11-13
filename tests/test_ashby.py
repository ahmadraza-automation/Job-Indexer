import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from connectors.ashby import AshbyConnector
from connectors.base import ConnectorFetchError
from connectors.registry import get_connector

FIXTURES = Path(__file__).parent / "fixtures" / "ashby"


def _fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _source(
    *,
    base_url: str = "https://api.ashbyhq.com",
    config: dict[str, Any] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        base_url=base_url,
        config=config if config is not None else {"board_name": "exampleco"},
        name="Example Co",
    )


def _connector(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[AshbyConnector, httpx.Client]:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    connector = AshbyConnector(client=client)
    connector.rate_limit_interval = 0
    return connector, client


def test_ashby_is_registered() -> None:
    connector = get_connector("ashby")
    try:
        assert isinstance(connector, AshbyConnector)
    finally:
        connector.close()


def test_discovers_and_normalizes_ashby_postings() -> None:
    payload = _fixture("postings.json")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=payload)

    connector, client = _connector(handler)
    try:
        jobs = list(connector.discover_jobs(_source()))
    finally:
        connector.close()
        client.close()

    assert len(jobs) == 3
    job = jobs[0]
    assert job.external_id == (
        "https://jobs.ashbyhq.com/exampleco/product-manager"
    )
    assert job.title == "Product Manager"
    assert job.company == "Example Co"
    assert job.department == "Product"
    assert job.location == "Houston, TX, San Francisco, Remote - US"
    assert job.is_remote is True
    assert job.employment_type == "full_time"
    assert job.description == "Lead product strategy."
    assert job.source_url == job.external_id
    assert job.apply_url.endswith("/application")
    assert job.posted_at == datetime(
        2025, 3, 1, 12, 30, 45, 123000, tzinfo=timezone.utc
    )
    assert job.raw["team"] == "Growth"
    assert job.raw["secondaryLocations"] == [
        {
            "location": "San Francisco",
            "address": {
                "addressLocality": "San Francisco",
                "addressRegion": "California",
                "addressCountry": "USA",
            },
        },
        {"location": "Remote - US"},
    ]
    assert job.raw["descriptionHtml"] == "<p>Lead product strategy.</p>"
    assert job.raw["compensation"]["compensationTierSummary"] == "$150K - $180K"
    assert jobs[1].posted_at == datetime(
        2025, 4, 15, 13, 0, tzinfo=timezone.utc
    )
    assert jobs[2].location == ""
    assert jobs[2].description == ""
    assert jobs[2].posted_at is None
    assert len(requests) == 1
    assert requests[0].url.host == "api.ashbyhq.com"
    assert requests[0].url.path == (
        "/posting-api/job-board/exampleco"
    )
    assert dict(requests[0].url.params) == {"includeCompensation": "true"}


def test_unlisted_postings_are_skipped() -> None:
    payload = _fixture("postings.json")
    connector, client = _connector(
        lambda _: httpx.Response(200, json=payload)
    )
    try:
        jobs = list(connector.discover_jobs(_source()))
    finally:
        connector.close()
        client.close()

    assert len(jobs) == 3
    assert all(job.title != "Unlisted Internal Role" for job in jobs)


def test_empty_board_returns_no_jobs() -> None:
    payload = _fixture("empty.json")
    connector, client = _connector(
        lambda _: httpx.Response(200, json=payload)
    )
    try:
        assert list(connector.discover_jobs(_source())) == []
    finally:
        connector.close()
        client.close()


def test_not_found_is_reported_as_a_fetch_failure() -> None:
    connector, client = _connector(lambda _: httpx.Response(404))
    try:
        with pytest.raises(ConnectorFetchError) as error:
            list(connector.discover_jobs(_source()))
        assert error.value.status_code == 404
    finally:
        connector.close()
        client.close()


def test_malformed_item_is_reported_as_a_fetch_failure() -> None:
    payload = _fixture("malformed.json")
    connector, client = _connector(
        lambda _: httpx.Response(200, json=payload)
    )
    try:
        with pytest.raises(ConnectorFetchError, match="invalid job record"):
            list(connector.discover_jobs(_source()))
    finally:
        connector.close()
        client.close()


def test_published_at_is_converted_to_utc() -> None:
    payload = _fixture("postings.json")
    payload["jobs"] = payload["jobs"][:1]
    connector, client = _connector(
        lambda _: httpx.Response(200, json=payload)
    )
    try:
        job = next(connector.discover_jobs(_source()))
    finally:
        connector.close()
        client.close()

    assert job.posted_at == datetime(
        2025, 3, 1, 12, 30, 45, 123000, tzinfo=timezone.utc
    )


def test_remote_flag_is_normalized_from_documented_boolean() -> None:
    payload = _fixture("postings.json")
    payload["jobs"] = payload["jobs"][:2]
    connector, client = _connector(
        lambda _: httpx.Response(200, json=payload)
    )
    try:
        jobs = list(connector.discover_jobs(_source()))
    finally:
        connector.close()
        client.close()

    assert [job.is_remote for job in jobs] == [True, False]


def test_uses_configured_api_base_url() -> None:
    payload = _fixture("empty.json")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=payload)

    connector, client = _connector(handler)
    try:
        assert list(
            connector.discover_jobs(
                _source(base_url="https://api.example.test/")
            )
        ) == []
    finally:
        connector.close()
        client.close()

    assert requests[0].url.host == "api.example.test"
