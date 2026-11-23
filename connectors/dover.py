"""Dover public-board connector.

Fetches the configured public careers URL as JSON or HTML; HTML parsing reads
JobPosting JSON-LD first and then stable-ID job cards. TODO: verify against live
board: public Dover URL format, response fields, and pagination markup.
"""

from collections.abc import Iterator

from connectors.base import BaseConnector
from connectors.registry import register_connector
from connectors.types import NormalizedJob
from connectors.utils.public_board import discover_public_board_jobs
from jobs.models import Source


@register_connector
class DoverConnector(BaseConnector):
    platform_key = "dover"
    kind = "ats"

    def discover_jobs(self, source: Source) -> Iterator[NormalizedJob]:
        yield from discover_public_board_jobs(self, source, self.platform_key)
