"""ApplicantPro public careers board fallback.

https://zealcu.applicantpro.com/jobs/ returned an HTML page with a
``<job-listings>`` Vue mount and bootstrap data, but no server-rendered jobs.
TODO: verify the anonymous public listing response/API; use the existing
public-board parser so an unrendered shell is recorded as a fetch failure.
"""

from collections.abc import Iterator

from connectors.base import BaseConnector
from connectors.registry import register_connector
from connectors.types import NormalizedJob
from connectors.utils.public_board import discover_public_board_jobs
from jobs.models import Source


@register_connector
class ApplicantProConnector(BaseConnector):
    platform_key = "applicantpro"
    kind = "ats"

    def discover_jobs(self, source: Source) -> Iterator[NormalizedJob]:
        yield from discover_public_board_jobs(self, source, self.platform_key)
