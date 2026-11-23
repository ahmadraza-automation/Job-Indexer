"""Kula public-board fallback.

No tenant-specific public board URL was found during discovery. Fetches only
the configured public URL using the existing JSON, JSON-LD, and stable-ID
HTML-card parser.
TODO: verify against live board: confirm a tenant URL and response format
before using Kula-specific fields or endpoints.
"""

from collections.abc import Iterator

from connectors.base import BaseConnector
from connectors.registry import register_connector
from connectors.types import NormalizedJob
from connectors.utils.public_board import discover_public_board_jobs
from jobs.models import Source


@register_connector
class KulaConnector(BaseConnector):
    platform_key = "kula"
    kind = "ats"

    def discover_jobs(self, source: Source) -> Iterator[NormalizedJob]:
        yield from discover_public_board_jobs(self, source, self.platform_key)
