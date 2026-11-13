import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

import connectors.lever as lever_module
from connectors.base import ConnectorFetchError
from connectors.lever import LeverConnector
from connectors.registry import get_connector

FIXTURES = Path(__file__).parent / "fixtures" / "lever"


def _fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _source(
    *,
    base_url: str = "https://api.lever.co",
    config: dict[str, Any] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        base_url=base_url,
        config=config if config is not None else {"company_slug": "exampleco"},
        name="Example Co",
    )


def _connector(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[LeverConnector, httpx.Client]:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    connector = LeverConnector(client=client)
    connector.rate_limit_interval = 0
    return connector, client


def test_lever_is_registered() -> None:
    connector = get_connector("lever")
    try:
        assert isinstance(connector, LeverConnector)
    finally:
        connector.close()


def test_discovers_and_normalizes_lever_postings() -> None:
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
    assert job.external_id == "a1b2c3d4-1111-4aaa-8bbb-1234567890ab"
    assert job.title == "Senior Backend Engineer"
    assert job.company == "Example Co"
    assert job.location == "New York, NY"
    assert job.department == "Engineering"
    assert job.employment_type == "full_time"
    assert job.description == "Build reliable services for our customers."
    assert job.source_url.endswith("/a1b2c3d4-1111-4aaa-8bbb-1234567890ab")
    assert job.apply_url.endswith("/apply")
    assert job.posted_at == datetime(2025, 1, 1, tzinfo=timezone.utc)
    assert job.is_remote is None
    assert job.raw["categories"]["team"] == "Platform"
    assert job.raw["workplaceType"] == "hybrid"
    assert job.raw["description_html"] == (
        "<div><p>Build reliable services for our customers.</p></div>"
    )
    assert job.raw["description_plain"] == job.description
    assert jobs[1].is_remote is True
    assert jobs[2].description == ""
    assert jobs[2].posted_at is None
    assert jobs[2].apply_url == ""
    assert len(requests) == 1
    assert requests[0].url.host == "api.lever.co"
    assert requests[0].url.path == "/v0/postings/exampleco"
    assert dict(requests[0].url.params) == {
        "mode": "json",
        "skip": "0",
        "limit": "100",
    }


def test_empty_board_returns_no_jobs() -> None:
    payload = _fixture("empty.json")
    connector, client = _connector(lambda _: httpx.Response(200, json=payload))
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
    connector, client = _connector(lambda _: httpx.Response(200, json=payload))
    try:
        with pytest.raises(ConnectorFetchError, match="valid id"):
            list(connector.discover_jobs(_source()))
    finally:
        connector.close()
        client.close()


def test_created_at_epoch_milliseconds_are_converted_to_utc() -> None:
    payload = _fixture("postings.json")
    connector, client = _connector(lambda _: httpx.Response(200, json=payload[:1]))
    try:
        job = next(connector.discover_jobs(_source()))
    finally:
        connector.close()
        client.close()

    assert job.posted_at == datetime(2025, 1, 1, tzinfo=timezone.utc)
    assert job.posted_at is not None
    assert job.posted_at.utcoffset().total_seconds() == 0


def test_uses_configured_eu_api_base_url() -> None:
    payload = _fixture("empty.json")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=payload)

    connector, client = _connector(handler)
    try:
        assert list(
            connector.discover_jobs(
                _source(base_url="https://api.eu.lever.co/")
            )
        ) == []
    finally:
        connector.close()
        client.close()

    assert requests[0].url.host == "api.eu.lever.co"


def test_paginates_using_skip_and_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(lever_module, "_PAGE_SIZE", 2)
    postings = _fixture("postings.json")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        skip = int(request.url.params["skip"])
        limit = int(request.url.params["limit"])
        return httpx.Response(200, json=postings[skip : skip + limit])

    connector, client = _connector(handler)
    try:
        jobs = list(connector.discover_jobs(_source()))
    finally:
        connector.close()
        client.close()

    assert len(jobs) == 3
    assert [request.url.params["skip"] for request in requests] == ["0", "2"]


def test_missing_company_slug_is_rejected_before_request() -> None:
    connector, client = _connector(
        lambda _: pytest.fail("invalid company slug made an HTTP request")
    )
    try:
        with pytest.raises(ValueError, match="company_slug"):
            list(
                connector.discover_jobs(
                    _source(config={"company_slug": "../invalid"})
                )
            )
    finally:
        connector.close()
        client.close()
