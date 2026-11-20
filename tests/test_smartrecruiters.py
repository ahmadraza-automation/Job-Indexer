import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

import connectors.smartrecruiters as smartrecruiters_module
from connectors.base import ConnectorFetchError
from connectors.registry import get_connector
from connectors.smartrecruiters import SmartRecruitersConnector

FIXTURES = Path(__file__).parent / "fixtures" / "smartrecruiters"


def _fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _source(
    *,
    config: dict[str, Any] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        config=config if config is not None else {"company": "exampleco"},
        name="Example Co",
    )


def _connector(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[SmartRecruitersConnector, httpx.Client]:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    connector = SmartRecruitersConnector(client=client)
    connector.rate_limit_interval = 0
    connector.retry_backoff_seconds = 0
    return connector, client


def _response_for_detail(request: httpx.Request) -> httpx.Response:
    posting_id = request.url.path.rsplit("/", 1)[-1]
    if posting_id == "12345":
        return httpx.Response(200, json=_fixture("detail.json"))
    if posting_id == "67890":
        return httpx.Response(200, json=_fixture("detail_missing_fields.json"))
    return httpx.Response(200, json=_fixture("detail_product.json"))


def test_smartrecruiters_is_registered() -> None:
    connector = get_connector("smartrecruiters")
    try:
        assert isinstance(connector, SmartRecruitersConnector)
    finally:
        connector.close()


def test_discovers_and_maps_postings_from_saved_fixtures() -> None:
    payload = _fixture("postings.json")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/companies/exampleco/postings":
            return httpx.Response(200, json=payload)
        return _response_for_detail(request)

    connector, client = _connector(handler)
    try:
        jobs = list(connector.discover_jobs(_source()))
    finally:
        connector.close()
        client.close()

    assert len(jobs) == 3
    job = jobs[0]
    assert job.external_id == "12345"
    assert job.title == "Senior Software Engineer"
    assert job.company == "Example Co"
    assert job.location == "New York, NY, US"
    assert job.department == "Engineering"
    assert job.employment_type == "full_time"
    assert job.description == "Build reliable services for our customers."
    assert job.apply_url == "https://jobs.smartrecruiters.com/exampleco/12345/apply"
    assert job.source_url.endswith("/12345-senior-software-engineer")
    assert job.posted_at == datetime(2025, 1, 15, 10, 30, tzinfo=timezone.utc)
    assert job.is_remote is False
    assert job.raw["detail"]["jobAd"]["sections"]["jobDescription"]["text"].startswith(
        "<p>"
    )
    assert jobs[1].location == ""
    assert jobs[1].department == ""
    assert jobs[1].description == ""
    assert jobs[1].employment_type == ""
    assert jobs[1].posted_at is None
    assert jobs[2].is_remote is True
    assert jobs[2].employment_type == "contract"
    assert len(requests) == 4
    assert requests[0].url.host == "api.smartrecruiters.com"
    assert requests[0].url.path == "/v1/companies/exampleco/postings"
    assert dict(requests[0].url.params) == {"limit": "100", "offset": "0"}
    assert requests[1].url.path.endswith("/postings/12345")


def test_empty_company_posting_list_returns_no_jobs() -> None:
    connector, client = _connector(
        lambda _: httpx.Response(200, json=_fixture("empty.json"))
    )
    try:
        assert list(connector.discover_jobs(_source())) == []
    finally:
        connector.close()
        client.close()


def test_paginates_until_total_found_is_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(smartrecruiters_module, "_PAGE_SIZE", 2)
    postings = _fixture("postings.json")["content"]
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/companies/exampleco/postings":
            offset = int(request.url.params["offset"])
            limit = int(request.url.params["limit"])
            return httpx.Response(
                200,
                json={
                    "totalFound": len(postings),
                    "content": postings[offset : offset + limit],
                },
            )
        return _response_for_detail(request)

    connector, client = _connector(handler)
    try:
        jobs = list(connector.discover_jobs(_source()))
    finally:
        connector.close()
        client.close()

    assert len(jobs) == 3
    list_requests = [
        request
        for request in requests
        if request.url.path == "/v1/companies/exampleco/postings"
    ]
    assert [request.url.params["offset"] for request in list_requests] == [
        "0",
        "2",
    ]
    assert all(request.url.params["limit"] == "2" for request in list_requests)


def test_unknown_company_is_reported_as_fetch_failure() -> None:
    connector, client = _connector(lambda _: httpx.Response(404))
    try:
        with pytest.raises(ConnectorFetchError) as error:
            list(connector.discover_jobs(_source()))
        assert error.value.status_code == 404
    finally:
        connector.close()
        client.close()


def test_malformed_posting_item_is_reported_as_fetch_failure() -> None:
    connector, client = _connector(
        lambda _: httpx.Response(200, json=_fixture("malformed.json"))
    )
    try:
        with pytest.raises(ConnectorFetchError, match="invalid job record"):
            list(connector.discover_jobs(_source()))
    finally:
        connector.close()
        client.close()


def test_transient_timeout_is_retried() -> None:
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


def test_invalid_company_is_rejected_before_request() -> None:
    connector, client = _connector(
        lambda _: pytest.fail("invalid company identifier made an HTTP request")
    )
    try:
        with pytest.raises(ValueError, match="company"):
            list(
                connector.discover_jobs(
                    _source(config={"company": "../invalid"})
                )
            )
    finally:
        connector.close()
        client.close()
