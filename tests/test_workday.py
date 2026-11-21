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
from connectors.workday import WorkdayConnector, _parse_posted_date
from jobs.models import JobSource, Source
from jobs.services import ingest

FIXTURES = Path(__file__).parent / "fixtures" / "workday"


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _source(
    config: dict[str, Any] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        config=config
        if config is not None
        else {
            "tenant": "example",
            "wd_host": "wd5",
            "site": "Example_Careers",
            "fetch_details": True,
        },
        name="Example Co",
    )


def _connector(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[WorkdayConnector, httpx.Client]:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    connector = WorkdayConnector(client=client)
    connector.rate_limit_interval = 0
    connector.retry_backoff_seconds = 0
    return connector, client


def test_workday_is_registered() -> None:
    connector = get_connector("workday")
    try:
        assert isinstance(connector, WorkdayConnector)
    finally:
        connector.close()


@pytest.mark.parametrize(
    ("value", "days_ago"),
    [
        ("Posted Today", 0),
        ("Posted 3 Days Ago", 3),
        ("Posted 30+ Days Ago", 30),
    ],
)
def test_posted_date_relative_variants(
    value: str,
    days_ago: int,
) -> None:
    parsed = _parse_posted_date(value)
    assert parsed is not None
    assert parsed.tzinfo == timezone.utc
    assert parsed.hour == 0
    assert (datetime.now(timezone.utc).date() - parsed.date()).days in {
        days_ago,
        days_ago - 1,
    }


def test_workday_paginates_multiple_pages_and_normalizes_details() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            offset = request.content
            payload = json.loads(offset)["offset"]
            if payload == 0:
                return httpx.Response(200, json=_fixture("list_page1.json"))
            return httpx.Response(200, json=_fixture("list_page2.json"))
        if request.url.path.endswith("/REQ-1001/Senior-Backend-Engineer"):
            return httpx.Response(200, json=_fixture("detail.json"))
        return httpx.Response(404)

    connector, client = _connector(handler)
    try:
        jobs = list(connector.discover_jobs(_source()))
    finally:
        connector.close()
        client.close()

    post_requests = [request for request in requests if request.method == "POST"]
    assert len(post_requests) == 2
    assert json.loads(post_requests[0].content)["limit"] == 20
    assert json.loads(post_requests[0].content)["offset"] == 0
    assert json.loads(post_requests[1].content)["offset"] == 20
    assert len(jobs) == 3
    job = jobs[0]
    assert job.external_id == "REQ-1001"
    assert job.title == "Senior Backend Engineer"
    assert job.company == "Example Co"
    assert job.location == "Seattle, Washington, United States"
    assert job.description == "Build reliable systems."
    assert job.employment_type == "full_time"
    assert job.is_remote is True
    assert job.apply_url == "https://example.com/jobs/REQ-1001"
    assert job.posted_at == datetime(2026, 10, 18, tzinfo=timezone.utc)
    assert job.raw["detail"]["jobReqId"] == "REQ-1001"


def test_empty_workday_results_return_no_jobs() -> None:
    connector, client = _connector(
        lambda _: httpx.Response(200, json=_fixture("empty.json"))
    )
    try:
        assert list(connector.discover_jobs(_source())) == []
    finally:
        connector.close()
        client.close()


def test_malformed_workday_payload_is_fetch_failure() -> None:
    connector, client = _connector(
        lambda _: httpx.Response(200, json=_fixture("malformed.json"))
    )
    try:
        with pytest.raises(ConnectorFetchError, match="invalid jobs response"):
            list(connector.discover_jobs(_source()))
    finally:
        connector.close()
        client.close()


def test_malformed_json_text_is_fetch_failure() -> None:
    connector, client = _connector(
        lambda _: httpx.Response(200, text="{not-json")
    )
    try:
        with pytest.raises(ConnectorFetchError, match="malformed JSON"):
            list(connector.discover_jobs(_source()))
    finally:
        connector.close()
        client.close()


@pytest.mark.parametrize(
    "missing_key",
    ["tenant", "wd_host", "site"],
)
def test_workday_config_validation(
    missing_key: str,
) -> None:
    config = {
        "tenant": "example",
        "wd_host": "wd5",
        "site": "Example_Careers",
    }
    config.pop(missing_key)
    connector, client = _connector(
        lambda _: pytest.fail("invalid config made an HTTP request")
    )
    try:
        with pytest.raises(ValueError, match=missing_key):
            list(connector.discover_jobs(_source(config)))
    finally:
        connector.close()
        client.close()


def test_detail_failure_falls_back_to_list_data() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            payload = _fixture("list_page1.json")
            payload["jobPostings"] = payload["jobPostings"][:1]
            payload["total"] = 1
            return httpx.Response(200, json=payload)
        return httpx.Response(404)

    connector, client = _connector(handler)
    try:
        jobs = list(connector.discover_jobs(_source()))
    finally:
        connector.close()
        client.close()

    assert len(jobs) == 1
    assert jobs[0].external_id == "/job/REQ-1001/Senior-Backend-Engineer"
    assert jobs[0].location == "Seattle, WA"
    assert jobs[0].description == ""
    assert jobs[0].posted_at is not None
    assert jobs[0].raw["detail"] is None


def test_fetch_details_false_skips_detail_requests() -> None:
    detail_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            payload = _fixture("list_page1.json")
            payload["jobPostings"] = payload["jobPostings"][:1]
            payload["total"] = 1
            return httpx.Response(200, json=payload)
        detail_requests.append(request)
        return httpx.Response(200, json=_fixture("detail.json"))

    config = {
        "tenant": "example",
        "wd_host": "wd5",
        "site": "Example_Careers",
        "fetch_details": False,
    }
    connector, client = _connector(handler)
    try:
        jobs = list(connector.discover_jobs(_source(config)))
    finally:
        connector.close()
        client.close()

    assert detail_requests == []
    assert len(jobs) == 1
    assert jobs[0].location == "Seattle, WA"


@pytest.mark.django_db
def test_workday_ingestion_creates_job_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = Source.objects.create(
        platform="workday",
        kind=Source.Kind.ATS,
        name="Example Co",
        base_url="https://example.wd5.myworkdayjobs.com/Example_Careers",
        config={
            "tenant": "example",
            "wd_host": "wd5",
            "site": "Example_Careers",
            "fetch_details": True,
        },
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            payload = _fixture("list_page1.json")
            payload["jobPostings"] = payload["jobPostings"][:1]
            payload["total"] = 1
            return httpx.Response(200, json=payload)
        return httpx.Response(200, json=_fixture("detail.json"))

    connector, client = _connector(handler)
    monkeypatch.setattr(ingest, "get_connector", lambda platform: connector)
    try:
        run = ingest.run_source(source)
    finally:
        client.close()

    assert run.status == "success"
    assert run.jobs_found == 1
    assert run.jobs_new == 1
    assert JobSource.objects.filter(source=source).count() == 1
