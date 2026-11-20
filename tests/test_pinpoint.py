import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from connectors.base import ConnectorFetchError
from connectors.pinpoint import PinpointConnector
from connectors.registry import get_connector

FIXTURES = Path(__file__).parent / "fixtures" / "pinpoint"


def _fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _source(
    *,
    config: dict[str, Any] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        config=config if config is not None else {"company_slug": "exampleco"},
        name="Example Co",
    )


def _connector(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[PinpointConnector, httpx.Client]:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    connector = PinpointConnector(client=client)
    connector.rate_limit_interval = 0
    connector.retry_backoff_seconds = 0
    return connector, client


def test_pinpoint_is_registered() -> None:
    connector = get_connector("pinpoint")
    try:
        assert isinstance(connector, PinpointConnector)
    finally:
        connector.close()


def test_discovers_and_normalizes_pinpoint_postings() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_fixture("postings.json"))

    connector, client = _connector(handler)
    try:
        jobs = list(connector.discover_jobs(_source()))
    finally:
        connector.close()
        client.close()

    assert len(jobs) == 2
    job = jobs[0]
    assert job.external_id == "118475"
    assert job.title == "Senior Marketing Designer"
    assert job.company == "Example Co"
    assert job.description == "Create thoughtful marketing design."
    assert job.location == "United Kingdom"
    assert job.source_url.endswith("/en/postings/118475")
    assert job.apply_url == job.source_url
    assert str(job.salary_min) == "55000"
    assert str(job.salary_max) == "65000"
    assert job.salary_currency == "GBP"
    assert job.salary_period == "year"
    assert job.employment_type == "full_time"
    assert job.is_remote is True
    assert job.posted_at is None
    assert jobs[1].description == ""
    assert jobs[1].location == ""
    assert jobs[1].salary_min is None
    assert jobs[1].employment_type == "contract"
    assert jobs[1].is_remote is None
    assert len(requests) == 1
    assert requests[0].url.host == "exampleco.pinpointhq.com"
    assert requests[0].url.path == "/postings.json"
    assert not requests[0].url.params


def test_empty_company_returns_no_postings() -> None:
    connector, client = _connector(
        lambda _: httpx.Response(200, json=_fixture("empty.json"))
    )
    try:
        assert list(connector.discover_jobs(_source())) == []
    finally:
        connector.close()
        client.close()


def test_unknown_company_is_reported_as_fetch_failure() -> None:
    connector, client = _connector(lambda _: httpx.Response(404))
    try:
        with pytest.raises(ConnectorFetchError) as error:
            list(connector.discover_jobs(_source()))
        assert error.value.status_code == 404
    finally:
        connector.close()
        client.close()


def test_malformed_posting_is_reported_as_fetch_failure() -> None:
    connector, client = _connector(
        lambda _: httpx.Response(200, json=_fixture("malformed.json"))
    )
    try:
        with pytest.raises(ConnectorFetchError, match="invalid posting record"):
            list(connector.discover_jobs(_source()))
    finally:
        connector.close()
        client.close()


def test_timeout_is_retried() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadTimeout("temporary timeout", request=request)
        return httpx.Response(200, json=_fixture("empty.json"))

    connector, client = _connector(handler)
    try:
        assert list(connector.discover_jobs(_source())) == []
    finally:
        connector.close()
        client.close()

    assert attempts == 2


def test_invalid_company_slug_is_rejected_before_request() -> None:
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
