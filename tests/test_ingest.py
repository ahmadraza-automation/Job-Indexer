from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from django.utils import timezone

from connectors.base import BaseConnector, ConnectorFetchError
from connectors.types import NormalizedJob
from jobs.models import (
    Company,
    FetchRun,
    Job,
    JobChange,
    JobSource,
    Source,
)
from jobs.services import ingest
from jobs.tasks import schedule_active_sources


class FakeConnector(BaseConnector):
    platform_key = "test-ats"
    kind = "ats"
    rate_limit_interval = 0

    def __init__(
        self,
        jobs: list[NormalizedJob] | Exception,
    ) -> None:
        self.jobs = jobs
        self.closed = False

    def discover_jobs(self, source: Source) -> Iterator[NormalizedJob]:
        if isinstance(self.jobs, Exception):
            raise self.jobs
        yield from self.jobs

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def source(db: None) -> Source:
    return Source.objects.create(
        platform="test-ats",
        kind=Source.Kind.ATS,
        name="Test ATS",
        base_url="https://jobs.example.com",
    )


def make_job(
    *,
    external_id: str = "job-1",
    title: str = "Software Engineer",
    company: str = "Example Inc",
    description: str = "Build systems",
    location: str = "Seattle, WA, US",
    raw: dict[str, str] | None = None,
) -> NormalizedJob:
    return NormalizedJob(
        title=title,
        company=company,
        description=description,
        location=location,
        source_url=f"https://jobs.example.com/{external_id}",
        apply_url=f"https://jobs.example.com/{external_id}/apply",
        external_id=external_id,
        salary_min=Decimal("100000"),
        salary_max=Decimal("130000"),
        salary_currency="USD",
        salary_period="year",
        employment_type="full_time",
        is_remote=False,
        raw=raw or {"id": external_id},
    )


def use_connector(
    monkeypatch: pytest.MonkeyPatch,
    jobs: list[NormalizedJob] | Exception,
) -> FakeConnector:
    connector = FakeConnector(jobs)
    monkeypatch.setattr(ingest, "get_connector", lambda platform: connector)
    return connector


@pytest.mark.django_db
def test_new_job_creates_company_canonical_job_and_source_link(
    source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_connector(monkeypatch, [make_job()])

    run = ingest.run_source(source)

    assert run.status == FetchRun.Status.SUCCESS
    assert (run.jobs_found, run.jobs_new, run.jobs_updated) == (1, 1, 0)
    assert Company.objects.get().normalized_name == "example inc"
    job = Job.objects.get()
    assert job.city == "Seattle"
    assert job.region == "WA"
    assert job.country == "US"
    assert job.search_vector is not None
    assert JobSource.objects.get().status == JobSource.Status.OPEN
    source.refresh_from_db()
    assert source.consecutive_failures == 0
    assert source.last_success_at is not None


@pytest.mark.django_db
def test_changed_job_updates_fields_and_records_changes(
    source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_connector(monkeypatch, [make_job()])
    ingest.run_source(source)
    monkeypatch.setattr(
        ingest,
        "get_connector",
        lambda platform: FakeConnector(
            [
                make_job(
                    title="Senior Software Engineer",
                    description="Build great systems",
                )
            ]
        ),
    )

    run = ingest.run_source(source)

    job = Job.objects.get()
    assert run.jobs_updated == 1
    assert job.title == "Senior Software Engineer"
    assert job.description == "Build great systems"
    assert job.search_vector is not None
    assert set(JobChange.objects.values_list("field", flat=True)) >= {
        "title",
        "description",
    }


@pytest.mark.django_db
def test_changed_company_updates_canonical_company_and_records_change(
    source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_connector(monkeypatch, [make_job()])
    ingest.run_source(source)
    monkeypatch.setattr(
        ingest,
        "get_connector",
        lambda platform: FakeConnector([make_job(company="Another Company")]),
    )

    ingest.run_source(source)

    job = Job.objects.get()
    assert job.company.name == "Another Company"
    assert JobChange.objects.get(field="company").new_value == '"Another Company"'


@pytest.mark.django_db
def test_unchanged_job_only_updates_seen_and_checked_timestamps(
    source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_connector(monkeypatch, [make_job()])
    ingest.run_source(source)
    link = JobSource.objects.get()
    job = link.job
    old_link_seen = link.last_seen_at
    old_job_checked = job.last_checked_at
    monkeypatch.setattr(
        ingest, "get_connector", lambda platform: FakeConnector([make_job()])
    )

    run = ingest.run_source(source)

    link.refresh_from_db()
    job.refresh_from_db()
    assert run.jobs_updated == 0
    assert link.last_seen_at > old_link_seen
    assert job.last_checked_at > old_job_checked
    assert JobChange.objects.count() == 0


@pytest.mark.django_db
def test_fetch_failure_records_error_and_never_closes_jobs(
    source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_connector(monkeypatch, [make_job()])
    ingest.run_source(source)
    link = JobSource.objects.get()
    link.missed_runs = 1
    link.save(update_fields=["missed_runs"])
    connector = use_connector(
        monkeypatch,
        ConnectorFetchError(
            "ATS unavailable",
            url="https://jobs.example.com",
            status_code=503,
        ),
    )

    run = ingest.run_source(source)

    link.refresh_from_db()
    source.refresh_from_db()
    assert run.status == FetchRun.Status.FAILED
    assert run.error_message == "ATS unavailable"
    assert run.http_status == 503
    assert source.consecutive_failures == 1
    assert link.status == JobSource.Status.OPEN
    assert link.missed_runs == 1
    assert link.job.status == Job.Status.OPEN
    assert connector.closed


@pytest.mark.django_db
def test_http_exception_records_status_and_generator_failure_is_atomic(
    source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_connector(monkeypatch, [make_job()])
    ingest.run_source(source)
    existing = JobSource.objects.get()
    existing.missed_runs = 1
    existing.save(update_fields=["missed_runs"])
    request = httpx.Request("GET", "https://jobs.example.com")
    response = httpx.Response(502, request=request)
    http_error = httpx.HTTPStatusError(
        "bad gateway",
        request=request,
        response=response,
    )

    class PartialFailureConnector(FakeConnector):
        def discover_jobs(self, source: Source) -> Iterator[NormalizedJob]:
            yield make_job(
                title="A partially fetched replacement",
            )
            raise http_error

    connector = PartialFailureConnector([])
    monkeypatch.setattr(ingest, "get_connector", lambda platform: connector)

    run = ingest.run_source(source)

    existing.refresh_from_db()
    source.refresh_from_db()
    assert run.status == FetchRun.Status.FAILED
    assert run.http_status == 502
    assert "bad gateway" in run.error_message
    assert Job.objects.count() == 1
    assert Job.objects.get().title == "Software Engineer"
    assert existing.missed_runs == 1
    assert existing.status == JobSource.Status.OPEN
    assert source.consecutive_failures == 1


@pytest.mark.django_db
def test_disappeared_job_is_marked_missed_once_not_closed(
    source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    jobs = [
        make_job(external_id=f"job-{number}", title=f"Engineer {number}")
        for number in range(10)
    ]
    use_connector(monkeypatch, jobs)
    ingest.run_source(source)
    monkeypatch.setattr(
        ingest,
        "get_connector",
        lambda platform: FakeConnector(jobs[1:]),
    )

    run = ingest.run_source(source)

    link = JobSource.objects.get(external_id="job-0")
    assert run.status == FetchRun.Status.SUCCESS
    assert link.missed_runs == 1
    assert link.status == JobSource.Status.OPEN
    assert link.job.status == Job.Status.OPEN


@pytest.mark.django_db
def test_empty_successful_run_with_existing_jobs_is_suspicious(
    source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_connector(monkeypatch, [make_job()])
    ingest.run_source(source)
    monkeypatch.setattr(ingest, "get_connector", lambda platform: FakeConnector([]))

    run = ingest.run_source(source)

    link = JobSource.objects.get()
    assert run.status == FetchRun.Status.SUSPICIOUS
    assert link.missed_runs == 0
    assert link.status == JobSource.Status.OPEN
    assert link.job.status == Job.Status.OPEN


@pytest.mark.django_db
def test_disappeared_job_closes_after_two_safe_runs(
    source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    jobs = [
        make_job(external_id=f"job-{number}", title=f"Engineer {number}")
        for number in range(10)
    ]
    use_connector(monkeypatch, jobs)
    ingest.run_source(source)
    monkeypatch.setattr(
        ingest,
        "get_connector",
        lambda platform: FakeConnector(jobs[1:]),
    )
    first_missing_run = ingest.run_source(source)
    monkeypatch.setattr(
        ingest,
        "get_connector",
        lambda platform: FakeConnector(jobs[1:]),
    )

    second_missing_run = ingest.run_source(source)

    link = JobSource.objects.get(external_id="job-0")
    assert first_missing_run.status == FetchRun.Status.SUCCESS
    assert link.missed_runs == 2
    assert link.status == JobSource.Status.CLOSED
    assert link.job.status == Job.Status.CLOSED
    assert link.job.closed_at is not None
    assert second_missing_run.jobs_closed == 1


@pytest.mark.django_db
def test_suspicious_drop_closes_nothing(
    source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    jobs = [
        make_job(external_id=f"job-{number}", title=f"Engineer {number}")
        for number in range(10)
    ]
    use_connector(monkeypatch, jobs)
    ingest.run_source(source)
    monkeypatch.setattr(
        ingest,
        "get_connector",
        lambda platform: FakeConnector([jobs[0], jobs[1]]),
    )

    run = ingest.run_source(source)

    assert run.status == FetchRun.Status.SUSPICIOUS
    assert run.jobs_closed == 0
    assert JobSource.objects.filter(status=JobSource.Status.CLOSED).count() == 0
    assert JobSource.objects.filter(status=JobSource.Status.OPEN).count() == 10


@pytest.mark.django_db
def test_closed_job_reappearing_is_reopened(
    source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = make_job()
    keep_open = make_job(external_id="job-2", title="Data Engineer")
    use_connector(monkeypatch, [first, keep_open])
    ingest.run_source(source)
    monkeypatch.setattr(
        ingest, "get_connector", lambda platform: FakeConnector([keep_open])
    )
    ingest.run_source(source)
    ingest.run_source(source)
    assert Job.objects.get(title=first.title).status == Job.Status.CLOSED

    monkeypatch.setattr(
        ingest, "get_connector", lambda platform: FakeConnector([first, keep_open])
    )
    run = ingest.run_source(source)

    link = JobSource.objects.get(external_id="job-1")
    assert run.status == FetchRun.Status.SUCCESS
    assert link.status == JobSource.Status.OPEN
    assert link.missed_runs == 0
    assert link.job.status == Job.Status.OPEN
    assert link.job.closed_at is None


@pytest.mark.django_db
def test_matching_fingerprint_from_another_source_reuses_canonical_job(
    source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_connector(monkeypatch, [make_job()])
    ingest.run_source(source)
    other_source = Source.objects.create(
        platform="test-board",
        kind=Source.Kind.BOARD,
        name="Test Board",
        base_url="https://board.example.com",
    )
    duplicate = make_job(
        external_id="board-1",
        company="EXAMPLE INC",
        title="software engineer",
    )
    monkeypatch.setattr(
        ingest,
        "get_connector",
        lambda platform: FakeConnector([duplicate]),
    )

    run = ingest.run_source(other_source)

    assert run.jobs_new == 1
    assert Company.objects.count() == 1
    assert Job.objects.count() == 1
    assert Job.objects.get().title == "Software Engineer"
    assert JobSource.objects.count() == 2
    assert JobSource.objects.get(source=other_source).job_id == Job.objects.get().pk


@pytest.mark.django_db
def test_scheduler_enqueues_sources_when_check_interval_is_due(
    source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    source.check_interval_minutes = 15
    source.save(update_fields=["check_interval_minutes"])
    previous_run = FetchRun.objects.create(
        source=source,
        status=FetchRun.Status.SUCCESS,
    )
    FetchRun.objects.filter(pk=previous_run.pk).update(
        started_at=timezone.now() - timedelta(minutes=16)
    )
    scheduled: list[int] = []
    monkeypatch.setattr(
        "jobs.tasks.run_source_task.delay",
        lambda source_id: scheduled.append(source_id),
    )

    count = schedule_active_sources()

    assert count == 1
    assert scheduled == [source.pk]
