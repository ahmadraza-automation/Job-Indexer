from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from connectors.base import ConnectorFetchError
from connectors.registry import get_connector
from connectors.teamtailor import TeamtailorConnector
from jobs.models import JobSource, Source
from jobs.services import ingest

FIXTURES = Path(__file__).parent / "fixtures" / "teamtailor"


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _source(config: dict[str, Any] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        config=config if config is not None else {"company_slug": "exampleco"},
        name="Example Co",
        base_url="https://exampleco.teamtailor.com",
    )


def _connector(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[TeamtailorConnector, httpx.Client]:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    connector = TeamtailorConnector(client=client)
    connector.rate_limit_interval = 0
    connector.retry_backoff_seconds = 0
    return connector, client


def test_teamtailor_is_registered() -> None:
    connector = get_connector("teamtailor")
    try:
        assert isinstance(connector, TeamtailorConnector)
    finally:
        connector.close()


def test_discovers_and_normalizes_teamtailor_jobs() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=_fixture("jobs.rss"))

    connector, client = _connector(handler)
    try:
        jobs = list(connector.discover_jobs(_source()))
    finally:
        connector.close()
        client.close()

    assert len(jobs) == 2
    job = jobs[0]
    assert job.external_id == "tt-job-201"
    assert job.title == "Platform Engineer"
    assert job.company == "Example Co"
    assert job.location == "Stockholm, Sweden"
    assert job.department == "Infrastructure"
    assert job.description == "Operate the platform."
    assert job.employment_type == "full_time"
    assert job.is_remote is True
    assert job.posted_at == datetime(2026, 10, 20, 9, 15, tzinfo=timezone.utc)
    assert job.updated_at == datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
    assert requests[0].url.path == "/jobs.rss"


def test_empty_teamtailor_feed_returns_no_jobs() -> None:
    connector, client = _connector(
        lambda _: httpx.Response(200, content=_fixture("empty.rss"))
    )
    try:
        assert list(connector.discover_jobs(_source())) == []
    finally:
        connector.close()
        client.close()


def test_malformed_teamtailor_feed_is_fetch_failure() -> None:
    connector, client = _connector(
        lambda _: httpx.Response(200, content=_fixture("malformed.rss"))
    )
    try:
        with pytest.raises(ConnectorFetchError, match="malformed RSS/XML"):
            list(connector.discover_jobs(_source()))
    finally:
        connector.close()
        client.close()


def test_teamtailor_feed_url_override_is_used() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=_fixture("empty.rss"))

    connector, client = _connector(handler)
    try:
        assert list(
            connector.discover_jobs(
                _source({"company_slug": "exampleco", "feed_url": "https://feed.example.test/jobs.xml"})
            )
        ) == []
    finally:
        connector.close()
        client.close()
    assert str(requests[0].url) == "https://feed.example.test/jobs.xml"


def test_invalid_teamtailor_slug_is_rejected_before_request() -> None:
    connector, client = _connector(
        lambda _: pytest.fail("invalid slug made an HTTP request")
    )
    try:
        with pytest.raises(ValueError, match="company_slug"):
            list(connector.discover_jobs(_source({"company_slug": "../bad"})))
    finally:
        connector.close()
        client.close()


@pytest.mark.django_db
def test_teamtailor_ingestion_creates_job_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = Source.objects.create(
        platform="teamtailor",
        kind=Source.Kind.ATS,
        name="Example Co",
        base_url="https://exampleco.teamtailor.com",
        config={"company_slug": "exampleco"},
    )
    connector, client = _connector(
        lambda _: httpx.Response(200, content=_fixture("jobs.rss"))
    )
    monkeypatch.setattr(ingest, "get_connector", lambda platform: connector)
    try:
        run = ingest.run_source(source)
    finally:
        client.close()

    assert run.status == "success"
    assert run.jobs_found == 2
    assert JobSource.objects.filter(source=source).count() == 2
