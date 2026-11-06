from collections.abc import Iterator

import httpx
import pytest

from connectors.base import BaseConnector, ConnectorFetchError
from connectors.registry import get_connector, list_connectors, register_connector
from connectors.types import NormalizedJob


class ExampleConnector(BaseConnector):
    platform_key = "test-platform"
    kind = "ats"
    rate_limit_interval = 0
    retry_backoff_seconds = 0

    def discover_jobs(self, source: object) -> Iterator[NormalizedJob]:
        yield NormalizedJob(title="Engineer", company="Example")


def test_registry_registers_and_returns_connector_instances() -> None:
    register_connector(ExampleConnector)

    connector = get_connector("test-platform")
    try:
        assert isinstance(connector, ExampleConnector)
        assert "test-platform" in list_connectors()
    finally:
        connector.close()


def test_registry_rejects_invalid_connectors() -> None:
    with pytest.raises(TypeError):
        register_connector(str)  # type: ignore[arg-type]

    class InvalidKindConnector(ExampleConnector):
        platform_key = "invalid-kind"
        kind = "other"

    with pytest.raises(ValueError, match="kind"):
        register_connector(InvalidKindConnector)


def test_registry_rejects_duplicate_platform_key() -> None:
    register_connector(ExampleConnector)

    class DuplicateConnector(ExampleConnector):
        pass

    with pytest.raises(ValueError, match="already registered"):
        register_connector(DuplicateConnector)


def test_unknown_connector_has_clear_error() -> None:
    with pytest.raises(KeyError, match="not-registered"):
        get_connector("not-registered")


def test_http_client_headers_timeout_and_success() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["user_agent"] = request.headers["user-agent"]
        return httpx.Response(200, text="ok")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    connector = ExampleConnector(client=client)
    connector.rate_limit_interval = 0
    try:
        response = connector.get("https://example.test/jobs")
        assert response.text == "ok"
        assert "JobIndexer" in str(captured["user_agent"])
    finally:
        connector.close()

    default_connector = ExampleConnector()
    try:
        assert default_connector._client.timeout.read == 15.0
    finally:
        default_connector.close()


@pytest.mark.parametrize("status_code", [404, 429, 503])
def test_http_errors_raise_connector_fetch_error(status_code: int) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status_code)

    connector = ExampleConnector(client=httpx.Client(transport=httpx.MockTransport(handler)))
    connector.rate_limit_interval = 0
    try:
        with pytest.raises(ConnectorFetchError) as error:
            connector.get("https://example.test/jobs")
        assert error.value.url == "https://example.test/jobs"
        assert error.value.status_code == status_code
        assert calls == (1 if status_code == 404 else connector.retry_attempts)
    finally:
        connector.close()


def test_http_transport_errors_are_retried_then_wrapped() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("connection refused", request=request)

    connector = ExampleConnector(client=httpx.Client(transport=httpx.MockTransport(handler)))
    connector.rate_limit_interval = 0
    try:
        with pytest.raises(ConnectorFetchError, match="connection refused"):
            connector.get("https://example.test/jobs")
        assert calls == connector.retry_attempts
    finally:
        connector.close()
