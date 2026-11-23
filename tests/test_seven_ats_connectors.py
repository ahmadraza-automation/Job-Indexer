from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from connectors.base import BaseConnector, ConnectorFetchError
from connectors.firststage import FirstStageConnector
from connectors.hirebridge import HireBridgeConnector
from connectors.isolvedhire import ISolvedHireConnector
from connectors.kula import KulaConnector
from connectors.registry import get_connector
from connectors.rival import RivalConnector
from connectors.trakstar import TrakstarConnector
from connectors.werecruit import WeRecruitConnector

FIXTURES = Path(__file__).parent / "fixtures"
CASES = (
    ("firststage", FirstStageConnector),
    ("hirebridge", HireBridgeConnector),
    ("isolvedhire", ISolvedHireConnector),
    ("kula", KulaConnector),
    ("rival", RivalConnector),
    ("trakstar", TrakstarConnector),
    ("werecruit", WeRecruitConnector),
)
_JOB_IDS = {
    "firststage": ("synthetic-101", "synthetic-102"),
    "hirebridge": ("synthetic-101", "synthetic-102"),
    "isolvedhire": ("synthetic-101", "synthetic-102"),
    "kula": ("synthetic-101", "synthetic-102"),
    "rival": ("synthetic-101", "synthetic-102"),
    "trakstar": ("trakstar-101", "trakstar-102"),
    "werecruit": (
        "00000000-0000-4000-8000-000000000101",
        "00000000-0000-4000-8000-000000000102",
    ),
}
_CONFIGS = {
    "firststage": {
        "base_url": "https://careers.example.invalid/jobs",
        "company_slug": "exampleco",
    },
    "hirebridge": {
        "base_url": "https://careers.example.invalid/hirebridge",
        "company_slug": "exampleco",
    },
    "isolvedhire": {
        "base_url": "https://careers.example.invalid/isolvedhire",
        "company_slug": "exampleco",
    },
    "kula": {
        "base_url": "https://careers.example.invalid/kula",
        "company_slug": "exampleco",
    },
    "rival": {
        "base_url": "https://careers.example.invalid/rival",
        "company_slug": "exampleco",
    },
    "trakstar": {
        "base_url": "https://exampleco.hire.trakstar.com",
        "company_slug": "exampleco",
    },
    "werecruit": {
        "base_url": "https://careers.werecruit.io/en/exampleco",
        "company_slug": "exampleco",
    },
}


def _source(platform: str, config: dict[str, str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        platform=platform,
        config=config if config is not None else dict(_CONFIGS[platform]),
        name="Example Co",
    )


def _fixture(platform: str, name: str) -> tuple[bytes, str]:
    path = FIXTURES / platform / name
    content_type = {
        ".html": "text/html",
        ".json": "application/json",
        ".xml": "application/rss+xml",
    }[path.suffix]
    return path.read_bytes(), content_type


def _response(
    platform: str,
    name: str,
    request: httpx.Request,
) -> httpx.Response:
    body, content_type = _fixture(platform, name)
    return httpx.Response(
        200,
        content=body,
        headers={"content-type": content_type},
        request=request,
    )


def _success_handler(platform: str, requests: list[httpx.Request]) -> Callable:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if platform == "trakstar":
            name = "jobs.xml"
        elif platform == "werecruit":
            path = request.url.path
            name = (
                "detail2.html"
                if path.endswith("synthetic-data-engineer-102")
                else "detail.html"
                if path.endswith("synthetic-backend-engineer-101")
                else "jobs.html"
            )
        else:
            name = (
                "page2.json"
                if request.url.params.get("page") == "2"
                else "jobs.json"
            )
        return _response(platform, name, request)

    return handler


def _scenario_handler(platform: str, scenario: str) -> Callable:
    file_names = {
        "empty": {
            "firststage": "empty.json",
            "hirebridge": "empty.json",
            "isolvedhire": "empty.json",
            "kula": "empty.json",
            "rival": "empty.json",
            "trakstar": "empty.xml",
            "werecruit": "empty.html",
        },
        "malformed": {
            "firststage": "malformed.json",
            "hirebridge": "malformed.json",
            "isolvedhire": "malformed.json",
            "kula": "malformed.json",
            "rival": "malformed.json",
            "trakstar": "malformed.xml",
            "werecruit": "malformed.html",
        },
        "missing_id": {
            "firststage": "missing_id.json",
            "hirebridge": "missing_id.json",
            "isolvedhire": "missing_id.json",
            "kula": "missing_id.json",
            "rival": "missing_id.json",
            "trakstar": "missing_id.xml",
            "werecruit": "missing_id.html",
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return _response(platform, file_names[scenario][platform], request)

    return handler


def _connector(
    connector_class: type[BaseConnector],
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[BaseConnector, httpx.Client]:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    connector = connector_class(client=client)
    connector.rate_limit_interval = 0
    connector.retry_backoff_seconds = 0
    return connector, client


@pytest.mark.parametrize(("platform", "connector_class"), CASES)
def test_connector_is_registered(
    platform: str,
    connector_class: type[BaseConnector],
) -> None:
    connector = get_connector(platform)
    try:
        assert isinstance(connector, connector_class)
    finally:
        connector.close()


@pytest.mark.parametrize(("platform", "connector_class"), CASES)
def test_discovers_jobs_with_stable_platform_ids(
    platform: str,
    connector_class: type[BaseConnector],
) -> None:
    requests: list[httpx.Request] = []
    connector, client = _connector(
        connector_class,
        _success_handler(platform, requests),
    )
    try:
        jobs = list(connector.discover_jobs(_source(platform)))
    finally:
        connector.close()
        client.close()

    assert [job.external_id for job in jobs] == list(_JOB_IDS[platform])
    assert len(requests) == {
        "trakstar": 1,
        "werecruit": 3,
    }.get(platform, 2)
    job = jobs[0]
    assert job.title == "Synthetic Backend Engineer"
    assert job.company == "Example Co"
    assert "Build reliable synthetic systems." in job.description
    assert job.location
    assert job.source_url
    assert job.apply_url
    assert job.employment_type == "full_time"
    assert job.posted_at == datetime(2026, 10, 20, 9, 30, tzinfo=timezone.utc)
    assert job.raw
    if platform == "trakstar":
        assert job.salary_min == Decimal("80000")
        assert job.salary_max == Decimal("100000")
        assert job.salary_currency == "USD"
        assert job.salary_period == "year"
    if platform == "werecruit":
        assert job.updated_at == datetime(2026, 10, 21, tzinfo=timezone.utc)


@pytest.mark.parametrize(("platform", "connector_class"), CASES)
def test_empty_public_board_is_successful(
    platform: str,
    connector_class: type[BaseConnector],
) -> None:
    connector, client = _connector(
        connector_class,
        _scenario_handler(platform, "empty"),
    )
    try:
        assert list(connector.discover_jobs(_source(platform))) == []
    finally:
        connector.close()
        client.close()


@pytest.mark.parametrize(("platform", "connector_class"), CASES)
def test_malformed_public_board_is_fetch_failure(
    platform: str,
    connector_class: type[BaseConnector],
) -> None:
    connector, client = _connector(
        connector_class,
        _scenario_handler(platform, "malformed"),
    )
    try:
        with pytest.raises(ConnectorFetchError):
            list(connector.discover_jobs(_source(platform)))
    finally:
        connector.close()
        client.close()


@pytest.mark.parametrize(("platform", "connector_class"), CASES)
def test_missing_stable_identifier_is_fetch_failure(
    platform: str,
    connector_class: type[BaseConnector],
) -> None:
    connector, client = _connector(
        connector_class,
        _scenario_handler(platform, "missing_id"),
    )
    try:
        with pytest.raises(ConnectorFetchError):
            list(connector.discover_jobs(_source(platform)))
    finally:
        connector.close()
        client.close()


@pytest.mark.parametrize(("platform", "connector_class"), CASES)
def test_invalid_config_is_rejected_before_request(
    platform: str,
    connector_class: type[BaseConnector],
) -> None:
    connector, client = _connector(
        connector_class,
        lambda _: pytest.fail("invalid config made an HTTP request"),
    )
    config = dict(_CONFIGS[platform], company_slug="../invalid")
    try:
        with pytest.raises(ValueError, match="company_slug"):
            list(connector.discover_jobs(_source(platform, config)))
    finally:
        connector.close()
        client.close()


@pytest.mark.parametrize(("platform", "connector_class"), CASES)
def test_http_404_is_fetch_failure(
    platform: str,
    connector_class: type[BaseConnector],
) -> None:
    connector, client = _connector(
        connector_class,
        lambda request: httpx.Response(404, request=request),
    )
    try:
        with pytest.raises(ConnectorFetchError) as error:
            list(connector.discover_jobs(_source(platform)))
        assert error.value.status_code == 404
    finally:
        connector.close()
        client.close()


@pytest.mark.parametrize(("platform", "connector_class"), CASES)
def test_http_429_uses_framework_retry_policy(
    platform: str,
    connector_class: type[BaseConnector],
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(429, request=request)

    connector, client = _connector(connector_class, handler)
    try:
        with pytest.raises(ConnectorFetchError) as error:
            list(connector.discover_jobs(_source(platform)))
        assert error.value.status_code == 429
    finally:
        connector.close()
        client.close()
    assert len(requests) == connector.retry_attempts


@pytest.mark.parametrize(("platform", "connector_class"), CASES)
def test_http_5xx_uses_framework_retry_policy(
    platform: str,
    connector_class: type[BaseConnector],
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(503, request=request)

    connector, client = _connector(connector_class, handler)
    try:
        with pytest.raises(ConnectorFetchError) as error:
            list(connector.discover_jobs(_source(platform)))
        assert error.value.status_code == 503
    finally:
        connector.close()
        client.close()
    assert len(requests) == connector.retry_attempts


def test_isolvedhire_client_shell_is_not_marked_as_empty() -> None:
    body, content_type = _fixture("isolvedhire", "observed.html")
    connector, client = _connector(
        ISolvedHireConnector,
        lambda request: httpx.Response(
            200,
            content=body,
            headers={"content-type": content_type},
            request=request,
        ),
    )
    try:
        with pytest.raises(ConnectorFetchError, match="invalid public job data"):
            list(connector.discover_jobs(_source("isolvedhire")))
    finally:
        connector.close()
        client.close()
