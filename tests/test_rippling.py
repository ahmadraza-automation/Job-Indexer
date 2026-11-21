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
from connectors.rippling import RipplingConnector
from jobs.models import JobSource, Source
from jobs.services import ingest

FIXTURES = Path(__file__).parent / "fixtures" / "rippling"


def _fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _source(config: dict[str, Any] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        config=config if config is not None else {"company_slug": "exampleco"},
        name="Example Co",
        base_url="https://ats.rippling.com/exampleco",
    )


def _connector(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[RipplingConnector, httpx.Client]:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    connector = RipplingConnector(client=client)
    connector.rate_limit_interval = 0
    connector.retry_backoff_seconds = 0
    return connector, client


def test_rippling_is_registered() -> None:
    connector = get_connector("rippling")
    try:
        assert isinstance(connector, RipplingConnector)
    finally:
        connector.close()


def test_discovers_and_normalizes_rippling_jobs() -> None:
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
    assert job.external_id == "rp-401"
    assert job.title == "Security Engineer"
    assert job.company == "Example Co"
    assert job.location == "San Francisco, CA, USA"
    assert job.department == "Security"
    assert job.description == "Protect customer data."
    assert job.employment_type == "full_time"
    assert job.is_remote is True
    assert job.posted_at == datetime(2026, 10, 17, 14, 45, tzinfo=timezone.utc)
    assert job.updated_at == datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)
    assert job.apply_url.endswith("/apply")
    assert requests[0].url.host == "ats.rippling.com"
    assert requests[0].url.path == "/exampleco"


def test_empty_rippling_board_returns_no_jobs() -> None:
    connector, client = _connector(
        lambda _: httpx.Response(200, json=_fixture("empty.json"))
    )
    try:
        assert list(connector.discover_jobs(_source())) == []
    finally:
        connector.close()
        client.close()


def test_malformed_rippling_payload_is_fetch_failure() -> None:
    connector, client = _connector(
        lambda _: httpx.Response(200, json=_fixture("malformed.json"))
    )
    try:
        with pytest.raises(ConnectorFetchError, match="invalid public job board"):
            list(connector.discover_jobs(_source()))
    finally:
        connector.close()
        client.close()


def test_rippling_html_next_data_is_supported() -> None:
    html = (
        '<script id="__NEXT_DATA__" type="application/json">'
        '{"props":{"pageProps":{"jobs":[{"id":"rp-html","title":"SRE",'
        '"location":"Remote","employmentType":"Full-time","isRemote":true}]}}}'
        "</script>"
    )
    connector, client = _connector(
        lambda _: httpx.Response(
            200,
            text=html,
            headers={"content-type": "text/html"},
        )
    )
    try:
        jobs = list(connector.discover_jobs(_source()))
    finally:
        connector.close()
        client.close()

    assert len(jobs) == 1
    assert jobs[0].external_id == "rp-html"
    assert jobs[0].is_remote is True


def test_invalid_rippling_slug_is_rejected_before_request() -> None:
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
def test_rippling_ingestion_creates_job_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = Source.objects.create(
        platform="rippling",
        kind=Source.Kind.ATS,
        name="Example Co",
        base_url="https://ats.rippling.com/exampleco",
        config={"company_slug": "exampleco"},
    )
    connector, client = _connector(
        lambda _: httpx.Response(200, json=_fixture("jobs.json"))
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
