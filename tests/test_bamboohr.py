import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from connectors.bamboohr import BambooHRConnector
from connectors.base import ConnectorFetchError
from connectors.registry import get_connector
from jobs.models import JobSource, Source
from jobs.services import ingest

FIXTURES = Path(__file__).parent / "fixtures" / "bamboohr"


def _fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _source(config: dict[str, Any] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        config=config if config is not None else {"company_slug": "exampleco"},
        name="Example Co",
        base_url="https://exampleco.bamboohr.com",
    )


def _connector(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[BambooHRConnector, httpx.Client]:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    connector = BambooHRConnector(client=client)
    connector.rate_limit_interval = 0
    connector.retry_backoff_seconds = 0
    return connector, client


def test_bamboohr_is_registered() -> None:
    connector = get_connector("bamboohr")
    try:
        assert isinstance(connector, BambooHRConnector)
    finally:
        connector.close()


def test_discovers_and_normalizes_bamboohr_jobs() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/careers/list"):
            return httpx.Response(200, json=_fixture("list.json"))
        if request.url.path.endswith("/careers/301"):
            return httpx.Response(200, json=_fixture("detail.json"))
        return httpx.Response(200, json={"job": {}})

    connector, client = _connector(handler)
    try:
        jobs = list(connector.discover_jobs(_source()))
    finally:
        connector.close()
        client.close()

    assert len(jobs) == 2
    job = jobs[0]
    assert job.external_id == "301"
    assert job.title == "Data Engineer"
    assert job.company == "Example Co"
    assert job.location == "Austin, Texas, USA"
    assert job.department == "Data"
    assert job.description == "Build data pipelines."
    assert job.employment_type == "full_time"
    assert job.is_remote is False
    assert job.posted_at == datetime(2026, 10, 19, tzinfo=timezone.utc)
    assert job.updated_at == datetime(2026, 10, 21, 8, 20, tzinfo=timezone.utc)
    assert job.apply_url == "https://exampleco.bamboohr.com/careers/301/apply"
    assert requests[0].url.host == "exampleco.bamboohr.com"
    assert requests[0].url.path == "/careers/list"
    assert jobs[1].employment_type == "contract"
    assert jobs[1].is_remote is True


def test_empty_bamboohr_feed_returns_no_jobs() -> None:
    connector, client = _connector(
        lambda _: httpx.Response(200, json=_fixture("empty.json"))
    )
    try:
        assert list(connector.discover_jobs(_source())) == []
    finally:
        connector.close()
        client.close()


def test_malformed_bamboohr_payload_is_fetch_failure() -> None:
    connector, client = _connector(
        lambda _: httpx.Response(200, json=_fixture("malformed.json"))
    )
    try:
        with pytest.raises(ConnectorFetchError, match="invalid careers response"):
            list(connector.discover_jobs(_source()))
    finally:
        connector.close()
        client.close()


def test_malformed_bamboohr_detail_is_fetch_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/careers/list"):
            return httpx.Response(200, json=_fixture("list.json"))
        return httpx.Response(200, text="<html", headers={"content-type": "text/html"})

    connector, client = _connector(handler)
    try:
        jobs = list(connector.discover_jobs(_source()))
        assert len(jobs) == 2
    finally:
        connector.close()
        client.close()


def test_invalid_bamboohr_slug_is_rejected_before_request() -> None:
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
def test_bamboohr_ingestion_creates_job_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = Source.objects.create(
        platform="bamboohr",
        kind=Source.Kind.ATS,
        name="Example Co",
        base_url="https://exampleco.bamboohr.com",
        config={"company_slug": "exampleco"},
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/careers/list"):
            return httpx.Response(200, json=_fixture("list.json"))
        if request.url.path.endswith("/careers/301"):
            return httpx.Response(200, json=_fixture("detail.json"))
        return httpx.Response(200, json={"job": {}})

    connector, client = _connector(handler)
    monkeypatch.setattr(ingest, "get_connector", lambda platform: connector)
    try:
        run = ingest.run_source(source)
    finally:
        client.close()

    assert run.status == "success"
    assert run.jobs_found == 2
    assert run.jobs_new == 2
    assert JobSource.objects.filter(source=source).count() == 2
