from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError

from connectors.registry import list_connectors
from jobs.management.commands._source_utils import (
    build_source_config,
    source_kind,
)
from jobs.models import Source


class Command(BaseCommand):
    help = "Add an employer or job board source."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--platform", required=True)
        parser.add_argument("--url", required=True)
        parser.add_argument("--slug")
        parser.add_argument("--name")
        parser.add_argument("--interval", type=int, default=60)

    def handle(self, *args, **options) -> str:
        platform = options["platform"].strip().lower()
        if platform not in list_connectors():
            raise CommandError(
                f"Unknown platform {platform!r}; registered platforms: "
                f"{', '.join(list_connectors())}"
            )
        if options["interval"] <= 0:
            raise CommandError("--interval must be a positive number of minutes")
        try:
            connector_kind, config = build_source_config(
                platform,
                options["url"].strip(),
                options["slug"],
            )
        except ValidationError as exc:
            raise CommandError(str(exc)) from exc

        name = (options["name"] or options["slug"] or platform).strip()
        if not name:
            raise CommandError("--name must not be empty")
        source = Source(
            platform=platform,
            kind=source_kind(connector_kind),
            name=name,
            base_url=options["url"].strip(),
            config=config,
            check_interval_minutes=options["interval"],
        )
        try:
            source.save(force_insert=True)
        except IntegrityError as exc:
            raise CommandError(
                "A source for this platform and employer/board already exists"
            ) from exc
        self.stdout.write(str(source.pk))
        return str(source.pk)
