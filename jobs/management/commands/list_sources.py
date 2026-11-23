import json

from django.core.management.base import BaseCommand

from jobs.models import Source


class Command(BaseCommand):
    help = "List configured employer and job board sources."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--platform")
        parser.add_argument("--json", action="store_true", dest="as_json")

    def handle(self, *args, **options) -> None:
        sources = Source.objects.all()
        if options["platform"]:
            sources = sources.filter(platform=options["platform"].strip().lower())

        records = [
            {
                "id": source.pk,
                "platform": source.platform,
                "name": source.name,
                "kind": source.kind,
                "url": source.base_url,
                "is_active": source.is_active,
                "check_interval_minutes": source.check_interval_minutes,
                "last_success_at": (
                    source.last_success_at.isoformat()
                    if source.last_success_at
                    else None
                ),
                "last_failure_at": (
                    source.last_failure_at.isoformat()
                    if source.last_failure_at
                    else None
                ),
                "consecutive_failures": source.consecutive_failures,
            }
            for source in sources.order_by("platform", "name", "pk")
        ]
        if options["as_json"]:
            self.stdout.write(json.dumps(records, indent=2))
            return
        for record in records:
            self.stdout.write(
                f"{record['id']}\t{record['platform']}\t{record['kind']}\t"
                f"{record['name']}\t{record['url']}\t"
                f"{'active' if record['is_active'] else 'inactive'}"
            )
