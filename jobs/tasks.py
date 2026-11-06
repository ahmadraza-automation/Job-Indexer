from datetime import timedelta

from celery import shared_task
from django.db.models import OuterRef, Subquery
from django.utils import timezone

from jobs.models import FetchRun, Source
from jobs.services.ingest import run_source


@shared_task
def run_source_task(source_id: int) -> int:
    source = Source.objects.get(pk=source_id)
    return run_source(source).pk


@shared_task
def schedule_active_sources() -> int:
    now = timezone.now()
    last_run = FetchRun.objects.filter(source_id=OuterRef("pk")).order_by(
        "-started_at"
    )
    sources = Source.objects.filter(is_active=True).annotate(
        last_run_at=Subquery(last_run.values("started_at")[:1])
    )
    scheduled = 0
    for source in sources.iterator():
        if source.last_run_at is None or source.last_run_at <= now - timedelta(
            minutes=source.check_interval_minutes
        ):
            run_source_task.delay(source.pk)
            scheduled += 1
    return scheduled
