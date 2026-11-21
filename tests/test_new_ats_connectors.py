import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from connectors.comeet import ComeetConnector
from connectors.hibob import HiBobConnector
from connectors.join_com import JOINConnector
from connectors.jobvite import JobviteConnector
from connectors.jazzhr import JazzHRConnector
from connectors.base import ConnectorFetchError
from connectors.registry import get_connector
from jobs.models import JobSource, Source
from jobs.services import ingest

FIXTURES = Path(__file__).parent / "fixtures"


CASES = [
    (
        "comeet",
        ComeetConnector,
        {"company_slug": "example"},
        "jobs.json",
        "empty.json",
        "malformed.json",
        "CM-101",
    ),
    (
        "join",
        JOINConnector,
        {"company_slug": "example"},
        "jobs.json",
        "empty.json",
        "malformed.json",
        "JOIN-201",
    ),
    (
        "hibob",
        HiBobConnector,
        {"company_slug": "example"},
        "jobs.html",
        "empty.html",
        "malformed.json",
        "HB-301",
    ),
    (
        "jobvite",
        JobviteConnector,
        {"company_slug": "example"},
        "jobs.json",
        "empty.json",
        "malformed.json",
        "JV-401",
    ),
    (
        "jazzhr",
        JazzHRConnector,
        {"company_slug": "example"},
        "jobs.html",
        "empty.html",
        "malformed.json",
        "JZ-501",
    ),
]


def _source(platform: str, config: dict[str, str]) -> SimpleNamespace:
    return SimpleNamespace(
        platform=platform,
        config=config,
        name="Example Co",
        base_url="https://example.test",
    )


def _fixture(platform: str, name: str) -> tuple[bytes, str]:
    path = FIXTURES / platform / name
    content = path.read_bytes()
    if name.endswith(".html"):
        return content, "text/html"
    return content, "application/json"


@pytest.mark.parametrize(
    ("platform", "connector_class", "_config", "_jobs", "_empty", "_malformed", "_id"),
    CASES,
)
def test_connector_registry(
    platform: str,
    connector_class: type,
    _config: dict[str, str],
    _jobs: str,
    _empty: str,
    _malformed: str,
    _id: str,
) -> None:
    connector = get_connector(platform)
    try:
        assert isinstance(connector, connector_class)
    finally:
        connector.close()


@pytest.mark.parametrize(
    ("platform", "connector_class", "_config", "_jobs", "_empty", "_malformed", "_id"),
    CASES,
)
def test_normalizes_saved_fixture(
    platform: str,
    connector_class: type,
    _config: dict[str, str],
    _jobs: str,
    _empty: str,
    _malformed: str,
    _id: str,
) -> None:
    content, content_type = _fixture(platform, _jobs)

    def handler(request: httpx.Request) -> httpx.Response:
        if content_type == "application/json":
            return httpx.Response(200, content=content, headers={"content-type": content_type})
        return httpx.Response(200, content=content, headers={"content-type": content_type})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    connector = connector_class(client=client)
    connector.rate_limit_interval = 0
    connector.retry_backoff_seconds = 0
    try:
        jobs = list(connector.discover_jobs(_source(platform, _config)))
    finally:
        connector.close()
        client.close()

    assert len(jobs) == 1
    job = jobs[0]
    assert job.external_id == _id
    assert job.title
    assert job.company == "Example Co"
    assert job.location
    assert job.description
    assert job.source_url
    assert job.apply_url
    assert job.employment_type
    assert job.salary_min is not None
    assert job.salary_currency
    assert job.posted_at is not None
    assert job.raw


@pytest.mark.parametrize(
    ("platform", "connector_class", "_config", "_jobs", "_empty", "_malformed", "_id"),
    CASES,
)
def test_empty_saved_fixture(
    platform: str,
    connector_class: type,
    _config: dict[str, str],
    _jobs: str,
    _empty: str,
    _malformed: str,
    _id: str,
) -> None:
    content, content_type = _fixture(platform, _empty)

    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                content=content,
                headers={"content-type": content_type},
            )
        )
    )
    connector = connector_class(client=client)
    connector.rate_limit_interval = 0
    try:
        assert list(connector.discover_jobs(_source(platform, _config))) == []
    finally:
        connector.close()
        client.close()


@pytest.mark.parametrize(
    ("platform", "connector_class", "_config", "_jobs", "_empty", "_malformed", "_id"),
    CASES,
)
def test_malformed_saved_fixture(
    platform: str,
    connector_class: type,
    _config: dict[str, str],
    _jobs: str,
    _empty: str,
    _malformed: str,
    _id: str,
) -> None:
    content, content_type = _fixture(platform, _malformed)
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                content=content,
                headers={"content-type": content_type},
            )
        )
    )
    connector = connector_class(client=client)
    connector.rate_limit_interval = 0
    try:
        with pytest.raises(ConnectorFetchError):
            list(connector.discover_jobs(_source(platform, _config)))
    finally:
        connector.close()
        client.close()


@pytest.mark.parametrize(
    ("platform", "connector_class", "_config", "_jobs", "_empty", "_malformed", "_id"),
    CASES,
)
def test_config_validation(
    platform: str,
    connector_class: type,
    _config: dict[str, str],
    _jobs: str,
    _empty: str,
    _malformed: str,
    _id: str,
) -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: pytest.fail("invalid config made an HTTP request")
        )
    )
    connector = connector_class(client=client)
    connector.rate_limit_interval = 0
    try:
        with pytest.raises(ValueError, match="company_slug"):
            list(
                connector.discover_jobs(
                    _source(platform, {"company_slug": "../invalid"})
                )
            )
    finally:
        connector.close()
        client.close()


@pytest.mark.parametrize(
    ("platform", "connector_class", "_config", "_jobs", "_empty", "_malformed", "_id"),
    CASES,
)
@pytest.mark.django_db
def test_ingestion_creates_job_source(
    platform: str,
    connector_class: type,
    _config: dict[str, str],
    _jobs: str,
    _empty: str,
    _malformed: str,
    _id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = Source.objects.create(
        platform=platform,
        kind=Source.Kind.ATS,
        name="Example Co",
        base_url="https://example.test",
        config=_config,
    )
    content, content_type = _fixture(platform, _jobs)

    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                content=content,
                headers={"content-type": content_type},
            )
        )
    )
    connector = connector_class(client=client)
    connector.rate_limit_interval = 0
    monkeypatch.setattr(ingest, "get_connector", lambda platform_key: connector)
    try:
        run = ingest.run_source(source)
    finally:
        client.close()

    assert run.status == "success"
    assert run.jobs_new == 1
    assert JobSource.objects.filter(source=source, external_id=_id).count() == 1
