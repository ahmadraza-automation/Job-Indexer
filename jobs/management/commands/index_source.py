import json

from django.core.management.base import BaseCommand, CommandError

from jobs.models import FetchRun, Source
from jobs.services.ingest import run_source


class Command(BaseCommand):
    help = "Index one source, or all sources for a platform."

    def add_arguments(self, parser) -> None:
        parser.add_argument("source_id", nargs="?", type=int)
        parser.add_argument("--platform")
        parser.add_argument("--all", action="store_true", dest="all_sources")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--json", action="store_true", dest="as_json")

    def handle(self, *args, **options) -> None:
        source_id = options["source_id"]
        platform = options["platform"]
        if source_id is not None:
            if platform or options["all_sources"]:
                raise CommandError("Use either <id> or --platform X --all")
            sources = Source.objects.filter(pk=source_id)
        elif platform and options["all_sources"]:
            sources = Source.objects.filter(platform=platform.strip().lower())
        else:
            raise CommandError("Provide <id> or --platform X --all")

        selected = list(sources.order_by("pk"))
        if not selected:
            raise CommandError("No matching source was found")

        results = []
        for source in selected:
            if options["dry_run"]:
                results.append(_result(source, None, dry_run=True))
            else:
                results.append(_result(source, run_source(source)))
        if options["as_json"]:
            payload = results[0] if source_id is not None else results
            self.stdout.write(json.dumps(payload, indent=2))
            return
        for result in results:
            self.stdout.write(
                f"source={result['id']} fetched={result['fetched']} "
                f"created={result['created']} updated={result['updated']} "
                f"closed={result['closed']} status={result['status']} "
                f"error={result['error']}"
            )


def _result(
    source: Source,
    fetch_run: FetchRun | None,
    *,
    dry_run: bool = False,
) -> dict:
    if dry_run:
        return {
            "id": source.pk,
            "fetched": 0,
            "created": 0,
            "updated": 0,
            "closed": 0,
            "status": "dry_run",
            "error": "",
        }
    assert fetch_run is not None
    return {
        "id": source.pk,
        "fetched": fetch_run.jobs_found,
        "created": fetch_run.jobs_new,
        "updated": fetch_run.jobs_updated,
        "closed": fetch_run.jobs_closed,
        "status": fetch_run.status,
        "error": fetch_run.error_message,
    }
