from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from connectors.applicantpro import ApplicantProConnector
from connectors.base import BaseConnector, ConnectorFetchError
from connectors.careerplug import CareerPlugConnector
from connectors.gohire import GoHireConnector
from connectors.hirehive import HireHiveConnector
from connectors.hireology import HireologyConnector
from connectors.hiringthing import HiringThingConnector
from connectors.registry import get_connector

FIXTURES = Path(__file__).parent / "fixtures"
CASES = (
    ("hirehive", HireHiveConnector, "hirehive-101", Decimal("70000")),
    ("hireology", HireologyConnector, "hireology-101", Decimal("70000")),
    ("hiringthing", HiringThingConnector, "101", None),
    ("careerplug", CareerPlugConnector, "101", Decimal("70000")),
    ("applicantpro", ApplicantProConnector, "applicantpro-101", None),
    ("gohire", GoHireConnector, "101", None),
)

_CONFIGS = {
    "hirehive": {
        "base_url": "https://exampleco.hirehive.com",
        "company_slug": "exampleco",
    },
    "hireology": {
        "base_url": "https://careers.example.test",
        "company_slug": "exampleco",
    },
    "hiringthing": {
        "base_url": "https://exampleco.hiringthing.com",
        "company_slug": "exampleco",
    },
    "careerplug": {
        "base_url": "https://exampleco.careerplug.com",
        "company_slug": "exampleco",
    },
    "applicantpro": {
        "base_url": "https://exampleco.applicantpro.com/jobs/",
        "company_slug": "exampleco",
    },
    "gohire": {
        "base_url": "https://jobs.example.test",
        "company_slug": "exampleco-token",
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
        path = request.url.path
        if platform == "hirehive":
            name = (
                "page2.json"
                if request.url.params.get("page") == "2"
                else "jobs.json"
            )
        elif platform == "hireology":
            if "/description" in path:
                name = "detail2.html" if "/102/" in path else "detail.html"
            else:
                name = (
                    "page2.html"
                    if request.url.params.get("page") == "2"
                    else "jobs.html"
                )
        elif platform == "hiringthing":
            name = (
                "page2.xml"
                if request.url.params.get("page") == "2"
                else "jobs.xml"
            )
        elif platform == "careerplug":
            if path.rstrip("/").rsplit("/", 1)[-1].isdigit():
                name = "detail2.html" if path.endswith("/102") else "detail.html"
            else:
                name = (
                    "page2.html"
                    if request.url.params.get("page") == "2"
                    else "jobs.html"
                )
        elif platform == "applicantpro":
            name = (
                "page2.json"
                if request.url.params.get("page") == "2"
                else "jobs.json"
            )
        else:
            if request.method == "POST":
                name = "page2.html"
            elif path.rstrip("/").split("/")[-1].endswith("-102"):
                name = "detail2.html"
            elif path.rstrip("/").split("/")[-1].endswith("-101"):
                name = "detail.html"
            else:
                name = "jobs.html"
        return _response(platform, name, request)

    return handler


def _scenario_handler(
    platform: str,
    scenario: str,
    requests: list[httpx.Request],
) -> Callable:
    file_names = {
        "empty": {
            "hirehive": "empty.json",
            "hireology": "empty.html",
            "hiringthing": "empty.xml",
            "careerplug": "empty.html",
            "applicantpro": "empty.html",
            "gohire": "empty.html",
        },
        "malformed": {
            "hirehive": "malformed.json",
            "hireology": "malformed.html",
            "hiringthing": "malformed.xml",
            "careerplug": "malformed.html",
            "applicantpro": "malformed.html",
            "gohire": "malformed.html",
        },
        "missing_id": {
            "hirehive": "missing_id.json",
            "hireology": "missing_id.html",
            "hiringthing": "missing_id.xml",
            "careerplug": "missing_id.html",
            "applicantpro": "missing_id.json",
            "gohire": "missing_id.html",
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
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


@pytest.mark.parametrize(("platform", "connector_class", "_job_id", "_salary"), CASES)
def test_connector_is_registered(
    platform: str,
    connector_class: type[BaseConnector],
    _job_id: str,
    _salary: Decimal | None,
) -> None:
    connector = get_connector(platform)
    try:
        assert isinstance(connector, connector_class)
    finally:
        connector.close()


@pytest.mark.parametrize(("platform", "connector_class", "job_id", "salary"), CASES)
def test_discovers_jobs_and_paginates(
    platform: str,
    connector_class: type[BaseConnector],
    job_id: str,
    salary: Decimal | None,
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

    assert len(jobs) == 2
    assert [job.external_id for job in jobs] == [job_id, job_id.replace("101", "102")]
    job = jobs[0]
    assert job.title == "Synthetic Backend Engineer"
    assert job.company == "Example Co"
    assert job.description == "Build reliable synthetic systems."
    assert job.location
    assert job.source_url
    assert job.apply_url
    assert job.employment_type == "full_time"
    assert job.raw
    if salary is not None:
        assert job.salary_min == salary
        assert job.salary_currency == "USD"
        assert job.salary_period == "year"
    assert any(request.method == "POST" for request in requests) is (
        platform == "gohire"
    )
    assert len(requests) >= 2


@pytest.mark.parametrize(("platform", "connector_class", "_job_id", "_salary"), CASES)
def test_empty_public_board_is_successful(
    platform: str,
    connector_class: type[BaseConnector],
    _job_id: str,
    _salary: Decimal | None,
) -> None:
    connector, client = _connector(
        connector_class,
        _scenario_handler(platform, "empty", []),
    )
    try:
        assert list(connector.discover_jobs(_source(platform))) == []
    finally:
        connector.close()
        client.close()


@pytest.mark.parametrize(("platform", "connector_class", "_job_id", "_salary"), CASES)
def test_malformed_public_board_is_fetch_failure(
    platform: str,
    connector_class: type[BaseConnector],
    _job_id: str,
    _salary: Decimal | None,
) -> None:
    connector, client = _connector(
        connector_class,
        _scenario_handler(platform, "malformed", []),
    )
    try:
        with pytest.raises(ConnectorFetchError):
            list(connector.discover_jobs(_source(platform)))
    finally:
        connector.close()
        client.close()


@pytest.mark.parametrize(("platform", "connector_class", "_job_id", "_salary"), CASES)
def test_missing_stable_identifier_is_fetch_failure(
    platform: str,
    connector_class: type[BaseConnector],
    _job_id: str,
    _salary: Decimal | None,
) -> None:
    connector, client = _connector(
        connector_class,
        _scenario_handler(platform, "missing_id", []),
    )
    try:
        with pytest.raises(ConnectorFetchError):
            list(connector.discover_jobs(_source(platform)))
    finally:
        connector.close()
        client.close()


@pytest.mark.parametrize(("platform", "connector_class", "_job_id", "_salary"), CASES)
def test_invalid_config_is_rejected_before_request(
    platform: str,
    connector_class: type[BaseConnector],
    _job_id: str,
    _salary: Decimal | None,
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


@pytest.mark.parametrize(("platform", "connector_class", "_job_id", "_salary"), CASES)
def test_http_404_is_fetch_failure(
    platform: str,
    connector_class: type[BaseConnector],
    _job_id: str,
    _salary: Decimal | None,
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


@pytest.mark.parametrize(("platform", "connector_class", "_job_id", "_salary"), CASES)
def test_http_429_uses_framework_retry_policy(
    platform: str,
    connector_class: type[BaseConnector],
    _job_id: str,
    _salary: Decimal | None,
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


@pytest.mark.parametrize(("platform", "connector_class", "_job_id", "_salary"), CASES)
def test_http_5xx_uses_framework_retry_policy(
    platform: str,
    connector_class: type[BaseConnector],
    _job_id: str,
    _salary: Decimal | None,
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


def test_applicantpro_static_shell_is_not_marked_as_empty() -> None:
    body, content_type = _fixture("applicantpro", "observed.html")
    connector, client = _connector(
        ApplicantProConnector,
        lambda request: httpx.Response(
            200,
            content=body,
            headers={"content-type": content_type},
            request=request,
        ),
    )
    try:
        with pytest.raises(ConnectorFetchError, match="invalid public job data"):
            list(connector.discover_jobs(_source("applicantpro")))
    finally:
        connector.close()
        client.close()
