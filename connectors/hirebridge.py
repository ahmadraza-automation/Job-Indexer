"""HireBridge public-board fallback.

The attempted ``recruit.hirebridge.com`` list URL returned an application
error, so no public job format is verified. Uses the existing JSON, JSON-LD,
and stable-ID HTML-card parser for a configured public URL.
TODO: verify against live board: find a real public board and confirm its
company identifier.
"""

from collections.abc import Iterator

from connectors.base import BaseConnector
from connectors.registry import register_connector
from connectors.types import NormalizedJob
from connectors.utils.public_board import discover_public_board_jobs
from jobs.models import Source


@register_connector
class HireBridgeConnector(BaseConnector):
    platform_key = "hirebridge"
    kind = "ats"

    def discover_jobs(self, source: Source) -> Iterator[NormalizedJob]:
        yield from discover_public_board_jobs(self, source, self.platform_key)
