import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from connectors.base import ConnectorFetchError
from connectors.registry import get_connector
from connectors.workable import WorkableConnector

FIXTURES = Path(__file__).parent / "fixtures" / "workable"


def _fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _source(
    *,
    base_url: str = "https://www.workable.com/api/accounts",
    config: dict[str, Any] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        base_url=base_url,
        config=config if config is not None else {"subdomain": "exampleco"},
        name="Example Co",
    )


def _connector(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[WorkableConnector, httpx.Client]:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    connector = WorkableConnector(client=client)
    connector.rate_limit_interval = 0
    return connector, client


def test_workable_is_registered() -> None:
    connector = get_connector("workable")
    try:
        assert isinstance(connector, WorkableConnector)
    finally:
        connector.close()


def test_discovers_and_normalizes_workable_jobs() -> None:
    payload = _fixture("jobs.json")
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
    assert job.external_id == "3c47ff"
    assert job.title == "Account Executive"
    assert job.company == "Example Co"
    assert job.department == "Account Executive"
    assert job.location == (
        "London, England, United Kingdom, New York, New York, United States"
    )
    assert job.is_remote is False
    assert job.employment_type == "full_time"
    assert job.description == (
        "Grow our customer relationships. Requirements Excellent communication skills."
    )
    assert job.posted_at == datetime(2025, 1, 15, 10, 30, tzinfo=timezone.utc)
    assert job.source_url == "https://exampleco.workable.com/jobs/3949357"
    assert job.apply_url.endswith("/candidates/new")
    assert job.raw["shortcode"] == "AE84C38EE2"
    assert job.raw["full_description"].startswith("<div>")
    assert jobs[1].is_remote is True
    assert jobs[2].is_remote is None
    assert jobs[2].description == ""
    assert jobs[2].posted_at is None
    assert jobs[2].apply_url == ""
    assert len(requests) == 1
    assert requests[0].url.host == "www.workable.com"
    assert requests[0].url.path == "/api/accounts/exampleco"
    assert dict(requests[0].url.params) == {"details": "true"}


def test_empty_account_returns_no_jobs() -> None:
    payload = _fixture("empty.json")
    connector, client = _connector(
        lambda _: httpx.Response(200, json=payload)
    )
    try:
        assert list(connector.discover_jobs(_source())) == []
    finally:
        connector.close()
        client.close()


def test_invalid_subdomain_404_is_reported_as_fetch_failure() -> None:
    connector, client = _connector(lambda _: httpx.Response(404))
    try:
        with pytest.raises(ConnectorFetchError) as error:
            list(connector.discover_jobs(_source()))
        assert error.value.status_code == 404
    finally:
        connector.close()
        client.close()


def test_malformed_item_is_reported_as_fetch_failure() -> None:
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


def test_created_at_is_converted_to_utc() -> None:
    payload = _fixture("jobs.json")
    payload["jobs"] = payload["jobs"][1:2]
    connector, client = _connector(
        lambda _: httpx.Response(200, json=payload)
    )
    try:
        job = next(connector.discover_jobs(_source()))
    finally:
        connector.close()
        client.close()

    assert job.posted_at == datetime(2025, 2, 20, 13, 15, tzinfo=timezone.utc)


def test_job_shortcode_is_used_when_id_is_missing() -> None:
    posting = _fixture("jobs.json")["jobs"][0]
    posting.pop("id")
    payload = {"jobs": [posting]}
    connector, client = _connector(
        lambda _: httpx.Response(200, json=payload)
    )
    try:
        job = next(connector.discover_jobs(_source()))
    finally:
        connector.close()
        client.close()

    assert job.external_id == "AE84C38EE2"


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
                _source(base_url="https://apply.workable.com/api/v1/widget/accounts")
            )
        ) == []
    finally:
        connector.close()
        client.close()

    assert requests[0].url.host == "apply.workable.com"
