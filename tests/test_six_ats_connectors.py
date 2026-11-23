from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from connectors.base import BaseConnector, ConnectorFetchError
from connectors.dover import DoverConnector
from connectors.gem import GemConnector
from connectors.manatal import ManatalConnector
from connectors.polymer import PolymerConnector
from connectors.recooty import RecootyConnector
from connectors.registry import get_connector
from connectors.zoho_recruit import ZohoRecruitConnector

FIXTURES = Path(__file__).parent / "fixtures"
CASES = (
    ("zoho_recruit", ZohoRecruitConnector, "zoho-101"),
    ("manatal", ManatalConnector, "manatal-101"),
    ("gem", GemConnector, "gem-101"),
    ("dover", DoverConnector, "dover-101"),
    ("polymer", PolymerConnector, "polymer-101"),
    ("recooty", RecootyConnector, "recooty-101"),
)
PLATFORM_CASES = tuple((platform, connector_class) for platform, connector_class, _ in CASES)


def _source(
    platform: str,
    config: dict[str, str] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        platform=platform,
        config=config
        if config is not None
        else {
            "base_url": f"https://boards.example.test/{platform}",
            "company_slug": "exampleco",
        },
        name="Example Co",
    )


def _fixture(platform: str, name: str) -> bytes:
    return (FIXTURES / platform / name).read_bytes()


def _connector(
    connector_class: type,
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[BaseConnector, httpx.Client]:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    connector = connector_class(client=client)
    connector.rate_limit_interval = 0
    connector.retry_backoff_seconds = 0
    return connector, client


@pytest.mark.parametrize(("platform", "connector_class"), PLATFORM_CASES)
def test_connector_is_registered(
    platform: str,
    connector_class: type,
) -> None:
    connector = get_connector(platform)
    try:
        assert isinstance(connector, connector_class)
    finally:
        connector.close()


@pytest.mark.parametrize(("platform", "connector_class", "job_id"), CASES)
def test_discovers_public_board_jobs_and_paginates(
    platform: str,
    connector_class: type,
    job_id: str,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        name = "page2.html" if request.url.params.get("page") == "2" else "jobs.html"
        return httpx.Response(
            200,
            content=_fixture(platform, name),
            headers={"content-type": "text/html"},
        )

    connector, client = _connector(connector_class, handler)
    try:
        jobs = list(connector.discover_jobs(_source(platform)))
    finally:
        connector.close()
        client.close()

    assert len(jobs) == 2
    job = jobs[0]
    assert job.external_id == job_id
    assert job.title == "Synthetic Backend Engineer"
    assert job.company == "Example Co"
    assert job.description == "Build reliable synthetic systems."
    assert job.location == "Sample City, Example Region, ZZ"
    assert job.source_url == f"https://boards.example.test/jobs/{job_id}"
    assert job.apply_url == f"https://boards.example.test/apply/{job_id}"
    assert job.employment_type == "full_time"
    assert job.is_remote is True
    assert job.posted_at == datetime(2026, 10, 20, 9, 30, tzinfo=timezone.utc)
    assert job.updated_at == datetime(2026, 10, 21, 11, 0, tzinfo=timezone.utc)
    assert job.department == "Engineering"
    assert job.raw["identifier"]["value"] == job_id
    assert jobs[1].external_id == job_id.replace("101", "102")
    assert jobs[1].title == "Synthetic Operations Associate"
    assert jobs[1].company == "Example Co"
    assert jobs[1].location == ""
    assert jobs[1].description == ""
    assert jobs[1].posted_at is None
    assert len(requests) == 2
    assert requests[0].url == f"https://boards.example.test/{platform}"
    assert requests[1].url == f"https://boards.example.test/{platform}?page=2"


@pytest.mark.parametrize(("platform", "connector_class"), PLATFORM_CASES)
def test_parses_json_job_collection(
    platform: str,
    connector_class: type,
) -> None:
    connector, client = _connector(
        connector_class,
        lambda _: httpx.Response(
            200,
            content=_fixture(platform, "json_jobs.json"),
            headers={"content-type": "application/json"},
        ),
    )
    try:
        jobs = list(connector.discover_jobs(_source(platform)))
    finally:
        connector.close()
        client.close()

    assert len(jobs) == 1
    assert jobs[0].external_id == f"{platform}-json-201"
    assert jobs[0].title == "Synthetic Data Analyst"
    assert jobs[0].company == "Example Co"
    assert jobs[0].description == "Analyze synthetic data."
    assert jobs[0].department == "Analytics"
    assert jobs[0].employment_type == "contract"
    assert jobs[0].is_remote is True
    assert jobs[0].raw["id"] == f"{platform}-json-201"


@pytest.mark.parametrize(("platform", "connector_class"), PLATFORM_CASES)
def test_empty_public_board_is_successful(
    platform: str,
    connector_class: type,
) -> None:
    connector, client = _connector(
        connector_class,
        lambda _: httpx.Response(
            200,
            content=_fixture(platform, "empty.html"),
            headers={"content-type": "text/html"},
        ),
    )
    try:
        assert list(connector.discover_jobs(_source(platform))) == []
    finally:
        connector.close()
        client.close()


@pytest.mark.parametrize(("platform", "connector_class"), PLATFORM_CASES)
def test_malformed_public_board_is_fetch_failure(
    platform: str,
    connector_class: type,
) -> None:
    connector, client = _connector(
        connector_class,
        lambda _: httpx.Response(
            200,
            content=_fixture(platform, "malformed.html"),
            headers={"content-type": "text/html"},
        ),
    )
    try:
        with pytest.raises(ConnectorFetchError, match="invalid public job data"):
            list(connector.discover_jobs(_source(platform)))
    finally:
        connector.close()
        client.close()


@pytest.mark.parametrize(("platform", "connector_class"), PLATFORM_CASES)
def test_missing_stable_identifier_is_fetch_failure(
    platform: str,
    connector_class: type,
) -> None:
    connector, client = _connector(
        connector_class,
        lambda _: httpx.Response(
            200,
            content=_fixture(platform, "missing_id.html"),
            headers={"content-type": "text/html"},
        ),
    )
    try:
        with pytest.raises(ConnectorFetchError, match="without a stable id"):
            list(connector.discover_jobs(_source(platform)))
    finally:
        connector.close()
        client.close()


@pytest.mark.parametrize(("platform", "connector_class"), PLATFORM_CASES)
def test_invalid_config_is_rejected_before_request(
    platform: str,
    connector_class: type,
) -> None:
    connector, client = _connector(
        connector_class,
        lambda _: pytest.fail("invalid config made an HTTP request"),
    )
    try:
        with pytest.raises(ValueError, match="company_slug"):
            list(
                connector.discover_jobs(
                    _source(platform, {"base_url": "https://example.test", "company_slug": "../bad"})
                )
            )
    finally:
        connector.close()
        client.close()


@pytest.mark.parametrize(("platform", "connector_class"), PLATFORM_CASES)
def test_http_404_is_reported_as_fetch_failure(
    platform: str,
    connector_class: type,
) -> None:
    connector, client = _connector(
        connector_class,
        lambda _: httpx.Response(404),
    )
    try:
        with pytest.raises(ConnectorFetchError) as error:
            list(connector.discover_jobs(_source(platform)))
        assert error.value.status_code == 404
    finally:
        connector.close()
        client.close()


@pytest.mark.parametrize(("platform", "connector_class"), PLATFORM_CASES)
def test_http_429_uses_framework_retry_policy(
    platform: str,
    connector_class: type,
) -> None:
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(429)

    connector, client = _connector(connector_class, handler)
    try:
        with pytest.raises(ConnectorFetchError) as error:
            list(connector.discover_jobs(_source(platform)))
        assert error.value.status_code == 429
    finally:
        connector.close()
        client.close()
    assert attempts == connector.retry_attempts
