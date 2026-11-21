import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from connectors.base import ConnectorFetchError
from connectors.personio import PersonioConnector
from connectors.registry import get_connector
from jobs.models import JobSource, Source
from jobs.services import ingest

FIXTURES = Path(__file__).parent / "fixtures" / "personio"


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _source(config: dict[str, Any] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        config=config if config is not None else {"company_slug": "exampleco"},
        name="Example Co",
        base_url="https://exampleco.jobs.personio.de",
    )


def _connector(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[PersonioConnector, httpx.Client]:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    connector = PersonioConnector(client=client)
    connector.rate_limit_interval = 0
    connector.retry_backoff_seconds = 0
    return connector, client


def test_personio_is_registered() -> None:
    connector = get_connector("personio")
    try:
        assert isinstance(connector, PersonioConnector)
    finally:
        connector.close()


def test_discovers_and_normalizes_personio_jobs() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=_fixture("jobs.xml"))

    connector, client = _connector(handler)
    try:
        jobs = list(connector.discover_jobs(_source()))
    finally:
        connector.close()
        client.close()

    assert len(jobs) == 2
    job = jobs[0]
    assert job.external_id == "personio-101"
    assert job.title == "Senior Software Engineer"
    assert job.company == "Example Co"
    assert job.location == "Berlin"
    assert job.department == "Engineering"
    assert job.description == "Build reliable systems."
    assert job.employment_type == "full_time"
    assert job.is_remote is None
    assert job.posted_at == datetime(2026, 10, 20, tzinfo=timezone.utc)
    assert job.updated_at == datetime(2026, 10, 22, 8, 30, tzinfo=timezone.utc)
    assert job.apply_url == "https://exampleco.jobs.personio.de/job/101"
    assert requests[0].url.host == "exampleco.jobs.personio.de"
    assert requests[0].url.path == "/xml"


def test_empty_personio_feed_returns_no_jobs() -> None:
    connector, client = _connector(
        lambda _: httpx.Response(200, content=_fixture("empty.xml"))
    )
    try:
        assert list(connector.discover_jobs(_source())) == []
    finally:
        connector.close()
        client.close()


def test_malformed_personio_xml_is_fetch_failure() -> None:
    connector, client = _connector(
        lambda _: httpx.Response(200, content=_fixture("malformed.xml"))
    )
    try:
        with pytest.raises(ConnectorFetchError, match="malformed XML"):
            list(connector.discover_jobs(_source()))
    finally:
        connector.close()
        client.close()


def test_personio_timeout_is_retried() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadTimeout("temporary timeout", request=request)
        return httpx.Response(200, content=_fixture("empty.xml"))

    connector, client = _connector(handler)
    try:
        assert list(connector.discover_jobs(_source())) == []
    finally:
        connector.close()
        client.close()
    assert attempts == 2


def test_invalid_personio_slug_is_rejected_before_request() -> None:
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
def test_personio_ingestion_creates_job_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = Source.objects.create(
        platform="personio",
        kind=Source.Kind.ATS,
        name="Example Co",
        base_url="https://exampleco.jobs.personio.de",
        config={"company_slug": "exampleco"},
    )
    connector, client = _connector(
        lambda _: httpx.Response(200, content=_fixture("jobs.xml"))
    )
    monkeypatch.setattr(ingest, "get_connector", lambda platform: connector)
    try:
        run = ingest.run_source(source)
    finally:
        client.close()

    assert run.status == "success"
    assert run.jobs_found == 2
    assert run.jobs_new == 2
    assert JobSource.objects.filter(source=source).count() == 2
