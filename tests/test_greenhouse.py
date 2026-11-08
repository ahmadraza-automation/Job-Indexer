import json
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from connectors.base import ConnectorFetchError
from connectors.greenhouse import GreenhouseConnector
from connectors.registry import get_connector
from jobs.models import FetchRun, JobSource, Source
from jobs.services import ingest

FIXTURES = Path(__file__).parent / "fixtures" / "greenhouse"


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _source() -> SimpleNamespace:
    return SimpleNamespace(
        config={"board_token": "exampleco"},
        name="Example Co",
    )


def _connector(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[GreenhouseConnector, httpx.Client]:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return GreenhouseConnector(client=client), client


def test_greenhouse_is_registered() -> None:
    connector = get_connector("greenhouse")
    assert isinstance(connector, GreenhouseConnector)
    connector.close()


def test_discovers_jobs_and_normalizes_greenhouse_fields() -> None:
    payload = _fixture("jobs.json")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=payload)

    connector, client = _connector(handler)
    try:
        jobs = list(connector.discover_jobs(_source()))
    finally:
        connector.close()
        client.close()

    assert len(jobs) == 1
    job = jobs[0]
    assert job.external_id == "12345"
    assert job.title == "Senior Software Engineer"
    assert job.company == "Example Co"
    assert job.location == "New York, NY"
    assert job.department == "Engineering"
    assert job.description == "Build & ship reliable systems."
    assert job.apply_url == "https://boards.greenhouse.io/exampleco/jobs/12345"
    assert job.source_url == job.apply_url
    assert job.posted_at is not None
    assert job.updated_at is not None
    assert job.raw["updated_at"] == "2026-10-30T12:15:00Z"
    assert job.raw["department"] == "Engineering"
    assert len(requests) == 1
    assert requests[0].url.params["content"] == "true"
    assert requests[0].url.path.endswith("/boards/exampleco/jobs")


def test_empty_board_returns_no_jobs() -> None:
    payload = _fixture("empty.json")
    connector, client = _connector(
        lambda request: httpx.Response(200, json=payload)
    )
    try:
        assert list(connector.discover_jobs(_source())) == []
    finally:
        connector.close()
        client.close()


def test_invalid_board_token_is_rejected_before_request() -> None:
    connector, client = _connector(
        lambda request: pytest.fail("invalid board token made an HTTP request")
    )
    try:
        with pytest.raises(ValueError, match="board_token"):
            list(
                connector.discover_jobs(
                    SimpleNamespace(
                        config={"board_token": "../invalid"},
                        name="Example Co",
                    )
                )
            )
    finally:
        connector.close()
        client.close()


def test_not_found_is_reported_as_a_fetch_failure() -> None:
    connector, client = _connector(lambda request: httpx.Response(404))
    try:
        with pytest.raises(ConnectorFetchError) as error:
            list(connector.discover_jobs(_source()))
        assert error.value.status_code == 404
    finally:
        connector.close()
        client.close()


def test_malformed_json_is_reported_as_a_fetch_failure() -> None:
    connector, client = _connector(
        lambda request: httpx.Response(200, text="{not json")
    )
    try:
        with pytest.raises(ConnectorFetchError, match="malformed JSON"):
            list(connector.discover_jobs(_source()))
    finally:
        connector.close()
        client.close()


def test_missing_optional_fields_are_normalized_to_empty_values() -> None:
    payload = _fixture("missing_fields.json")
    connector, client = _connector(
        lambda request: httpx.Response(200, json=payload)
    )
    try:
        job = next(connector.discover_jobs(_source()))
    finally:
        connector.close()
        client.close()

    assert job.external_id == "987"
    assert job.location == ""
    assert job.department == ""
    assert job.description == ""
    assert job.posted_at is None
    assert job.updated_at is None


def test_duplicate_ids_in_response_are_deduplicated() -> None:
    payload = _fixture("jobs.json")
    payload["jobs"].append(deepcopy(payload["jobs"][0]))
    connector, client = _connector(
        lambda request: httpx.Response(200, json=payload)
    )
    try:
        jobs = list(connector.discover_jobs(_source()))
    finally:
        connector.close()
        client.close()

    assert [job.external_id for job in jobs] == ["12345"]


@pytest.mark.django_db
def test_invalid_board_marks_fetch_run_failed_without_closing_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = Source.objects.create(
        platform="greenhouse",
        kind=Source.Kind.ATS,
        name="Missing Board",
        base_url="https://boards.greenhouse.io/missing-board",
        config={"board_token": "missing-board"},
    )
    connector, client = _connector(lambda request: httpx.Response(404))
    monkeypatch.setattr(ingest, "get_connector", lambda platform: connector)
    try:
        run = ingest.run_source(source)
    finally:
        client.close()

    source.refresh_from_db()
    assert run.status == FetchRun.Status.FAILED
    assert run.http_status == 404
    assert source.consecutive_failures == 1
    assert run.jobs_closed == 0
    assert JobSource.objects.filter(source=source).count() == 0


@pytest.mark.django_db
def test_ingestion_deduplicates_repeated_jobs_and_updates_changed_updated_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = Source.objects.create(
        platform="greenhouse",
        kind=Source.Kind.ATS,
        name="Example Co",
        base_url="https://boards-api.greenhouse.io",
        config={"board_token": "exampleco"},
    )
    first_payload = _fixture("jobs.json")
    changed_payload = deepcopy(first_payload)
    changed_payload["jobs"][0]["updated_at"] = "2026-11-01T12:15:00Z"
    responses = [first_payload, first_payload, changed_payload]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=responses.pop(0))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(
        ingest,
        "get_connector",
        lambda platform: GreenhouseConnector(client=client),
    )
    try:
        first_run = ingest.run_source(source)
        repeated_run = ingest.run_source(source)
        changed_run = ingest.run_source(source)
    finally:
        client.close()

    assert first_run.status == FetchRun.Status.SUCCESS
    assert first_run.jobs_new == 1
    assert repeated_run.jobs_new == 0
    assert repeated_run.jobs_updated == 0
    assert changed_run.jobs_updated == 1
    assert JobSource.objects.filter(source=source).count() == 1
    link = JobSource.objects.get(source=source)
    assert link.raw_payload["updated_at"] == "2026-11-01T12:15:00Z"
