"""iSolvedHire public-board fallback.

The reachable ``https://andpizza.isolvedhire.com/jobs/`` page returned a
client-rendered ``<job-listings>`` shell without server-rendered postings.
TODO: verify against live board: confirm the anonymous public listing endpoint;
keep the generic parser so an unrecognized shell is recorded as a fetch
failure, not an empty board.
"""

from collections.abc import Iterator

from connectors.base import BaseConnector
from connectors.registry import register_connector
from connectors.types import NormalizedJob
from connectors.utils.public_board import discover_public_board_jobs
from jobs.models import Source


@register_connector
class ISolvedHireConnector(BaseConnector):
    platform_key = "isolvedhire"
    kind = "ats"

    def discover_jobs(self, source: Source) -> Iterator[NormalizedJob]:
        yield from discover_public_board_jobs(self, source, self.platform_key)
