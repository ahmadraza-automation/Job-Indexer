import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from connectors.base import ConnectorFetchError
from connectors.recruitee import RecruiteeConnector
from connectors.registry import get_connector

FIXTURES = Path(__file__).parent / "fixtures" / "recruitee"


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
) -> tuple[RecruiteeConnector, httpx.Client]:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    connector = RecruiteeConnector(client=client)
    connector.rate_limit_interval = 0
    connector.retry_backoff_seconds = 0
    return connector, client


def test_recruitee_is_registered() -> None:
    connector = get_connector("recruitee")
    try:
        assert isinstance(connector, RecruiteeConnector)
    finally:
        connector.close()


def test_discovers_and_normalizes_recruitee_offers() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_fixture("offers.json"))

    connector, client = _connector(handler)
    try:
        jobs = list(connector.discover_jobs(_source()))
    finally:
        connector.close()
        client.close()

    assert len(jobs) == 2
    job = jobs[0]
    assert job.external_id == "1853589"
    assert job.title == "Senior Backend Engineer"
    assert job.company == "Example Co"
    assert job.description == "Build reliable services. Know Python."
    assert job.location == "London, New York"
    assert job.source_url.endswith("/o/senior-backend-engineer")
    assert job.apply_url.endswith("/c/new")
    assert str(job.salary_min) == "100"
    assert str(job.salary_max) == "150"
    assert job.salary_currency == "EUR"
    assert job.salary_period == "hour"
    assert job.employment_type == "full_time"
    assert job.is_remote is True
    assert job.posted_at == datetime(2025, 3, 1, 10, 0, tzinfo=timezone.utc)
    assert jobs[1].description == ""
    assert jobs[1].location == ""
    assert jobs[1].salary_min is None
    assert jobs[1].employment_type == ""
    assert jobs[1].is_remote is False
    assert jobs[1].posted_at == datetime(2025, 3, 2, 12, 0, tzinfo=timezone.utc)
    assert len(requests) == 1
    assert requests[0].url.host == "exampleco.recruitee.com"
    assert requests[0].url.path == "/api/offers/"
    assert not requests[0].url.params


def test_empty_company_returns_no_offers() -> None:
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


def test_malformed_offer_is_reported_as_fetch_failure() -> None:
    connector, client = _connector(
        lambda _: httpx.Response(200, json=_fixture("malformed.json"))
    )
    try:
        with pytest.raises(ConnectorFetchError, match="invalid offer record"):
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
