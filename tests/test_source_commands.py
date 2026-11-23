from collections.abc import Iterator
from io import StringIO

import pytest
from django.core.management import call_command, CommandError
from django.db import IntegrityError, transaction

from connectors.base import ConnectorFetchError
from connectors.types import NormalizedJob
from jobs.models import Company, FetchRun, Job, JobSource, Source
from jobs.services import ingest


@pytest.mark.django_db
def test_same_platform_supports_distinct_sources_and_rejects_duplicate_identity() -> None:
    first = Source.objects.create(
        platform="greenhouse",
        kind=Source.Kind.ATS,
        name="First Employer",
        base_url="https://boards.greenhouse.io/first",
        config={"board_token": "first"},
    )
    second = Source.objects.create(
        platform="greenhouse",
        kind=Source.Kind.ATS,
        name="Second Employer",
        base_url="https://boards.greenhouse.io/second",
        config={"board_token": "second"},
    )

    assert first.pk != second.pk
    with pytest.raises(IntegrityError), transaction.atomic():
        Source.objects.create(
            platform="greenhouse",
            kind=Source.Kind.ATS,
            name="Duplicate First",
            base_url="https://boards-api.greenhouse.io",
            config={"board_token": "FIRST"},
        )


@pytest.mark.django_db
def test_add_source_validates_platform_config_and_duplicate_source() -> None:
    output = StringIO()
    with pytest.raises(CommandError, match="Unknown platform"):
        call_command(
            "add_source",
            "--platform",
            "not-registered",
            "--url",
            "https://jobs.example.com",
            "--slug",
            "example",
            stdout=output,
        )
    with pytest.raises(CommandError, match="--slug"):
        call_command(
            "add_source",
            "--platform",
            "greenhouse",
            "--url",
            "https://boards.greenhouse.io/example",
            "--slug",
            "bad/slug",
            stdout=output,
        )

    call_command(
        "add_source",
        "--platform",
        "greenhouse",
        "--url",
        "https://boards.greenhouse.io/example",
        "--slug",
        "example",
        "--name",
        "Example Co",
        stdout=output,
    )
    source = Source.objects.get()
    assert source.kind == Source.Kind.ATS
    assert source.name == "Example Co"
    assert source.config["board_token"] == "example"

    with pytest.raises(CommandError, match="already exists"):
        call_command(
            "add_source",
            "--platform",
            "greenhouse",
            "--url",
            "https://boards-api.greenhouse.io",
            "--slug",
            "EXAMPLE",
            stdout=output,
        )


class CommandConnector:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def discover_jobs(self, source: Source) -> Iterator[NormalizedJob]:
        if self.fail:
            raise ConnectorFetchError(
                "Board unavailable",
                url=source.base_url,
                status_code=503,
            )
        yield NormalizedJob(
            title="Software Engineer",
            company=source.name,
            external_id="cmd-1",
            location="Remote",
        )

    def close(self) -> None:
        pass


@pytest.mark.django_db
def test_index_source_reports_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = Source.objects.create(
        platform="greenhouse",
        kind=Source.Kind.ATS,
        name="Example Co",
        base_url="https://boards.greenhouse.io/example",
        config={"board_token": "example"},
    )
    monkeypatch.setattr(
        ingest,
        "get_connector",
        lambda platform: CommandConnector(),
    )
    output = StringIO()

    call_command("index_source", str(source.pk), stdout=output)

    assert (
        "fetched=1 created=1 updated=0 closed=0 status=success error="
        in output.getvalue()
    )
    assert Job.objects.get().title == "Software Engineer"


@pytest.mark.django_db
def test_index_source_reports_failure_without_closing_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = Source.objects.create(
        platform="greenhouse",
        kind=Source.Kind.ATS,
        name="Example Co",
        base_url="https://boards.greenhouse.io/example",
        config={"board_token": "example"},
    )
    company = Company.objects.create(name="Example Co", normalized_name="example co")
    job = Job.objects.create(
        title="Engineer",
        company=company,
        fingerprint="a" * 64,
    )
    JobSource.objects.create(
        source=source,
        job=job,
        external_id="existing",
        missed_runs=1,
    )
    monkeypatch.setattr(
        ingest,
        "get_connector",
        lambda platform: CommandConnector(fail=True),
    )
    output = StringIO()

    call_command("index_source", str(source.pk), stdout=output)

    job.refresh_from_db()
    source.refresh_from_db()
    run = FetchRun.objects.get(source=source)
    assert "fetched=0 created=0 updated=0 closed=0 status=failed" in output.getvalue()
    assert "error=Board unavailable" in output.getvalue()
    assert job.status == Job.Status.OPEN
    assert JobSource.objects.get().missed_runs == 1
    assert source.last_failure_at is not None
    assert run.status == FetchRun.Status.FAILED
