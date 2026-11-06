import pytest
from django.db import IntegrityError, transaction

from jobs.models import Company, FetchRun, Job, JobChange, JobSource, Source


@pytest.fixture
def source(db: None) -> Source:
    return Source.objects.create(
        platform="example-ats",
        kind=Source.Kind.ATS,
        name="Example ATS",
        base_url="https://jobs.example.com",
    )


@pytest.fixture
def job(db: None) -> Job:
    company = Company.objects.create(name="Example", normalized_name="example")
    return Job.objects.create(title="Engineer", company=company, fingerprint="a" * 64)


def test_source_defaults_and_config(source: Source) -> None:
    assert source.is_active is True
    assert source.check_interval_minutes == 60
    assert source.consecutive_failures == 0
    assert source.config == {}


def test_company_normalized_name_is_unique(db: None) -> None:
    Company.objects.create(name="Example Inc.", normalized_name="example")

    with pytest.raises(IntegrityError), transaction.atomic():
        Company.objects.create(name="Example", normalized_name="example")


def test_job_source_is_unique_per_source_external_id(
    source: Source, job: Job
) -> None:
    JobSource.objects.create(job=job, source=source, external_id="123")

    with pytest.raises(IntegrityError), transaction.atomic():
        JobSource.objects.create(job=job, source=source, external_id="123")


def test_jobs_can_have_multiple_sources(source: Source, job: Job) -> None:
    other_source = Source.objects.create(
        platform="example-board",
        kind=Source.Kind.BOARD,
        name="Example Board",
        base_url="https://board.example.com",
    )

    JobSource.objects.create(job=job, source=source, external_id="ats-123")
    JobSource.objects.create(job=job, source=other_source, external_id="board-456")

    assert job.sources.count() == 2
    assert job.status == Job.Status.OPEN


def test_fetch_runs_and_job_changes_can_be_recorded(
    source: Source, job: Job
) -> None:
    job_source = JobSource.objects.create(job=job, source=source, external_id="123")
    fetch_run = FetchRun.objects.create(source=source, status=FetchRun.Status.FAILED)
    change = JobChange.objects.create(
        job_source=job_source,
        field="title",
        old_value="Engineer",
        new_value="Senior Engineer",
    )

    assert fetch_run.jobs_closed == 0
    assert fetch_run.finished_at is None
    assert change.old_value == "Engineer"
