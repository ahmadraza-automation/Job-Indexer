"""FirstStage public-board fallback.

No live FirstStage board URL was found during discovery. Fetches the configured
public URL using the existing JSON, JSON-LD, and stable-ID HTML-card parser.
TODO: verify the public board URL and response format against a live board.
"""

from collections.abc import Iterator

from connectors.base import BaseConnector
from connectors.registry import register_connector
from connectors.types import NormalizedJob
from connectors.utils.public_board import discover_public_board_jobs
from jobs.models import Source


@register_connector
class FirstStageConnector(BaseConnector):
    platform_key = "firststage"
    kind = "ats"

    def discover_jobs(self, source: Source) -> Iterator[NormalizedJob]:
        yield from discover_public_board_jobs(self, source, self.platform_key)
