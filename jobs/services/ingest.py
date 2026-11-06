import hashlib
import json
import re
import unicodedata
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from typing import Any

from django.contrib.postgres.search import SearchVector
from django.db import transaction
from django.db.models import F, Value
from django.utils import timezone

from connectors.registry import get_connector
from connectors.types import NormalizedJob
from connectors.utils.location import split_location
from jobs.models import Company, FetchRun, Job, JobChange, JobSource, Source

JOB_FIELDS = (
    "title",
    "description",
    "location_raw",
    "city",
    "region",
    "country",
    "is_remote",
    "employment_type",
    "salary_min",
    "salary_max",
    "salary_currency",
    "salary_period",
    "posted_at",
)


def run_source(source: Source) -> FetchRun:
    """Fetch and ingest one source; failed or suspicious runs never close jobs."""
    fetch_run = FetchRun.objects.create(
        source=source,
        status=FetchRun.Status.PARTIAL,
    )
    try:
        jobs = _fetch_jobs(source)
        now = timezone.now()
        with transaction.atomic():
            locked_source = Source.objects.select_for_update().get(pk=source.pk)
            existing_links = list(
                JobSource.objects.select_related("job", "job__company")
                .filter(source=locked_source)
                .order_by("pk")
            )
            links_by_external_id = {
                link.external_id: link for link in existing_links
            }
            prepared_jobs = [
                (job, _external_id(job), _content_hash(job))
                for job in jobs
            ]
            incoming_ids = {external_id for _, external_id, _ in prepared_jobs}
            open_links = [
                link
                for link in existing_links
                if link.status == JobSource.Status.OPEN
            ]
            previously_open_count = len(open_links)
            disappeared_count = sum(
                link.external_id not in incoming_ids for link in open_links
            )
            suspicious = previously_open_count > 0 and (
                not jobs
                or disappeared_count * 100 > previously_open_count * 70
            )

            new_count = 0
            updated_count = 0
            seen_ids: set[str] = set()
            for normalized, external_id, content_hash in prepared_jobs:
                if external_id in seen_ids:
                    raise ValueError(
                        f"Connector returned duplicate external_id {external_id!r}"
                    )
                seen_ids.add(external_id)
                link = links_by_external_id.get(external_id)
                if link is None:
                    link = _create_job_source(
                        normalized,
                        external_id,
                        content_hash,
                        locked_source,
                        now,
                    )
                    links_by_external_id[external_id] = link
                    new_count += 1
                    continue

                link.job.refresh_from_db()
                if link.content_hash != content_hash:
                    _update_existing_link(
                        link,
                        normalized,
                        content_hash,
                        now,
                    )
                    updated_count += 1
                else:
                    link.last_seen_at = now
                    update_fields = ["last_seen_at"]
                    if link.missed_runs:
                        link.missed_runs = 0
                        update_fields.append("missed_runs")
                    link.save(update_fields=update_fields)
                    Job.objects.filter(pk=link.job_id).update(last_checked_at=now)

                if link.status != JobSource.Status.OPEN:
                    link.status = JobSource.Status.OPEN
                    link.missed_runs = 0
                    link.save(update_fields=["status", "missed_runs"])
                if (
                    link.status == JobSource.Status.OPEN
                    and link.job.status != Job.Status.OPEN
                ):
                    Job.objects.filter(pk=link.job_id).update(
                        status=Job.Status.OPEN,
                        closed_at=None,
                    )

            closed_count = 0
            touched_job_ids: set[int] = set()
            if not suspicious:
                for link in existing_links:
                    if (
                        link.status == JobSource.Status.CLOSED
                        or link.external_id in seen_ids
                    ):
                        continue
                    link.missed_runs += 1
                    fields = ["missed_runs"]
                    if link.missed_runs >= 2:
                        link.status = JobSource.Status.CLOSED
                        fields.append("status")
                        closed_count += 1
                        touched_job_ids.add(link.job_id)
                    link.save(update_fields=fields)

            _close_jobs_with_no_active_sources(touched_job_ids, now)
            fetch_run.jobs_found = len(jobs)
            fetch_run.jobs_new = new_count
            fetch_run.jobs_updated = updated_count
            fetch_run.jobs_closed = closed_count
            fetch_run.status = (
                FetchRun.Status.SUSPICIOUS
                if suspicious
                else FetchRun.Status.SUCCESS
            )
            fetch_run.finished_at = now
            fetch_run.save(
                update_fields=[
                    "jobs_found",
                    "jobs_new",
                    "jobs_updated",
                    "jobs_closed",
                    "status",
                    "finished_at",
                ]
            )
            locked_source.last_success_at = now
            locked_source.consecutive_failures = 0
            locked_source.save(
                update_fields=["last_success_at", "consecutive_failures"]
            )
        return fetch_run
    except Exception as exc:
        _mark_failed(fetch_run, source, exc)
        return fetch_run


def _fetch_jobs(source: Source) -> list[NormalizedJob]:
    connector = get_connector(source.platform)
    try:
        return list(connector.discover_jobs(source))
    finally:
        connector.close()


def _mark_failed(fetch_run: FetchRun, source: Source, exc: Exception) -> None:
    http_status = getattr(exc, "status_code", None)
    response = getattr(exc, "response", None)
    if http_status is None and response is not None:
        http_status = getattr(response, "status_code", None)
    if not isinstance(http_status, int) or not 0 < http_status <= 32767:
        http_status = None

    now = timezone.now()
    FetchRun.objects.filter(pk=fetch_run.pk).update(
        status=FetchRun.Status.FAILED,
        finished_at=now,
        error_message=str(exc) or exc.__class__.__name__,
        http_status=http_status,
    )
    Source.objects.filter(pk=source.pk).update(
        consecutive_failures=F("consecutive_failures") + 1
    )
    fetch_run.refresh_from_db()


def _create_job_source(
    normalized: NormalizedJob,
    external_id: str,
    content_hash: str,
    source: Source,
    now: datetime,
) -> JobSource:
    fingerprint = _fingerprint(normalized)
    job = Job.objects.filter(fingerprint=fingerprint).first()
    if job is None:
        company = _company_for_name(normalized.company)
        job = Job.objects.create(
            company=company,
            fingerprint=fingerprint,
            last_checked_at=now,
            **_job_values(normalized),
        )
        _update_search_vector(job.pk)

    link = JobSource.objects.create(
        job=job,
        source=source,
        external_id=external_id,
        source_url=normalized.source_url,
        apply_url=normalized.apply_url,
        raw_payload=normalized.raw,
        content_hash=content_hash,
        last_seen_at=now,
        status=JobSource.Status.OPEN,
        missed_runs=0,
    )
    Job.objects.filter(pk=job.pk).update(
        status=Job.Status.OPEN,
        closed_at=None,
        last_checked_at=now,
    )
    return link


def _update_existing_link(
    link: JobSource,
    normalized: NormalizedJob,
    content_hash: str,
    now: datetime,
) -> None:
    link.job.refresh_from_db()
    old_values = _job_values_from_model(link.job)
    new_values = _job_values(normalized)
    changed = {
        field: (old_values[field], new_values[field])
        for field in JOB_FIELDS
        if old_values[field] != new_values[field]
    }
    company = _company_for_name(normalized.company)
    if link.job.company_id != company.pk:
        changed["company"] = (link.job.company.name, company.name)

    _apply_job_changes(
        link.job,
        normalized,
        _fingerprint(normalized),
        company,
        now,
    )
    for field, (old_value, new_value) in changed.items():
        JobChange.objects.create(
            job_source=link,
            field=field,
            old_value=_stringify(old_value),
            new_value=_stringify(new_value),
        )

    source_changes = {
        "source_url": (link.source_url, normalized.source_url),
        "apply_url": (link.apply_url, normalized.apply_url),
        "raw_payload": (link.raw_payload, normalized.raw),
    }
    for field, (old_value, new_value) in source_changes.items():
        if old_value != new_value:
            JobChange.objects.create(
                job_source=link,
                field=field,
                old_value=_stringify(old_value),
                new_value=_stringify(new_value),
            )

    link.source_url = normalized.source_url
    link.apply_url = normalized.apply_url
    link.raw_payload = normalized.raw
    link.content_hash = content_hash
    link.last_seen_at = now
    link.last_changed_at = now
    link.missed_runs = 0
    link.save(
        update_fields=[
            "source_url",
            "apply_url",
            "raw_payload",
            "content_hash",
            "last_seen_at",
            "last_changed_at",
            "missed_runs",
        ]
    )


def _apply_job_changes(
    job: Job,
    normalized: NormalizedJob,
    fingerprint: str,
    company: Company,
    now: datetime,
) -> None:
    values = _job_values(normalized)
    changed_fields = [
        field for field, value in values.items() if getattr(job, field) != value
    ]
    if job.fingerprint != fingerprint:
        job.fingerprint = fingerprint
        changed_fields.append("fingerprint")
    if job.company_id != company.pk:
        job.company = company
        changed_fields.append("company")
    for field, value in values.items():
        setattr(job, field, value)
    job.fingerprint = fingerprint
    job.last_checked_at = now
    update_fields = [*set(changed_fields), "last_checked_at"]
    job.save(update_fields=update_fields)
    if changed_fields:
        _update_search_vector(job.pk)


def _job_values(normalized: NormalizedJob) -> dict[str, Any]:
    city, region, country = split_location(normalized.location)
    return {
        "title": normalized.title.strip(),
        "description": normalized.description,
        "location_raw": normalized.location,
        "city": city,
        "region": region,
        "country": country,
        "is_remote": normalized.is_remote,
        "employment_type": normalized.employment_type,
        "salary_min": normalized.salary_min,
        "salary_max": normalized.salary_max,
        "salary_currency": normalized.salary_currency,
        "salary_period": normalized.salary_period,
        "posted_at": normalized.posted_at,
    }


def _job_values_from_model(job: Job) -> dict[str, Any]:
    return {field: getattr(job, field) for field in JOB_FIELDS}


def _update_search_vector(job_id: int) -> None:
    company_name = Job.objects.values_list("company__name", flat=True).get(pk=job_id)
    Job.objects.filter(pk=job_id).update(
        search_vector=(
            SearchVector(
                "title",
                "description",
                "location_raw",
                "city",
                "region",
                "country",
                config="english",
            )
            + SearchVector(Value(company_name), config="english")
        )
    )


def _company_for_name(name: str) -> Company:
    company_name = name.strip()
    company, _ = Company.objects.get_or_create(
        normalized_name=_normalize(company_name),
        defaults={"name": company_name},
    )
    return company


def _close_jobs_with_no_active_sources(job_ids: set[int], now: datetime) -> None:
    for job_id in job_ids:
        has_active_source = JobSource.objects.filter(job_id=job_id).exclude(
            status=JobSource.Status.CLOSED
        ).exists()
        if not has_active_source:
            Job.objects.filter(pk=job_id).update(
                status=Job.Status.CLOSED,
                closed_at=now,
                last_checked_at=now,
            )


def _external_id(job: NormalizedJob) -> str:
    if job.external_id.strip():
        return job.external_id.strip()
    if job.source_url.strip():
        return "url:" + hashlib.sha256(job.source_url.strip().encode()).hexdigest()
    return "content:" + _content_hash(job)


def _content_hash(job: NormalizedJob) -> str:
    encoded = json.dumps(
        asdict(job),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _fingerprint(job: NormalizedJob) -> str:
    if not job.company.strip() or not job.title.strip():
        raise ValueError("Normalized jobs must include a title and company")
    identity = "\x1f".join(
        _normalize(value)
        for value in (job.company, job.title, job.location)
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = "".join(
        character if character.isalnum() else " " for character in normalized
    )
    return re.sub(r"\s+", " ", normalized).strip()


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, Decimal)):
        return value.isoformat() if isinstance(value, datetime) else str(value)
    return str(value)


def _stringify(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )
