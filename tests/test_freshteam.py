import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from connectors.base import ConnectorFetchError
from connectors.freshteam import FreshteamConnector
from connectors.registry import get_connector

FIXTURES = Path(__file__).parent / "fixtures" / "freshteam"


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
) -> tuple[FreshteamConnector, httpx.Client]:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    connector = FreshteamConnector(client=client)
    connector.rate_limit_interval = 0
    connector.retry_backoff_seconds = 0
    return connector, client


def test_freshteam_is_registered() -> None:
    connector = get_connector("freshteam")
    try:
        assert isinstance(connector, FreshteamConnector)
    finally:
        connector.close()


def test_discovers_and_normalizes_freshteam_jobs() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_fixture("jobs.json"))

    connector, client = _connector(handler)
    try:
        jobs = list(connector.discover_jobs(_source()))
    finally:
        connector.close()
        client.close()

    assert len(jobs) == 2
    job = jobs[0]
    assert job.external_id == "2000008856"
    assert job.title == "Business Development Manager"
    assert job.company == "Example Co"
    assert job.description == "Build customer relationships."
    assert job.location == "Bengaluru, India, Delhi, India"
    assert job.source_url.endswith("/jobs/Abc123/business-development-manager")
    assert job.apply_url == job.source_url
    assert str(job.salary_min) == "80000"
    assert str(job.salary_max) == "120000"
    assert job.salary_currency == "INR"
    assert job.salary_period == "year"
    assert job.employment_type == "full_time"
    assert job.is_remote is True
    assert job.posted_at == datetime(2025, 5, 10, 9, 30, tzinfo=timezone.utc)
    assert job.department == "Business Development"
    assert jobs[1].description == ""
    assert jobs[1].location == "Bengaluru, India"
    assert jobs[1].salary_min is None
    assert jobs[1].employment_type == ""
    assert jobs[1].is_remote is False
    assert jobs[1].posted_at is None
    assert len(requests) == 1
    assert requests[0].url.host == "exampleco.freshteam.com"
    assert requests[0].url.path == "/hire/widgets/jobs.json"
    assert not requests[0].url.params


def test_empty_company_returns_no_jobs() -> None:
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


def test_malformed_job_is_reported_as_fetch_failure() -> None:
    connector, client = _connector(
        lambda _: httpx.Response(200, json=_fixture("malformed.json"))
    )
    try:
        with pytest.raises(ConnectorFetchError, match="invalid job record"):
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
