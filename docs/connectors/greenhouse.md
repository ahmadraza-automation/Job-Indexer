# Greenhouse connector

Greenhouse sources use the public Job Board API and do not require credentials.
Configure a source with `platform="greenhouse"`, `kind="ats"`, the company name
in `name`, and its board slug in `config["board_token"]`.

The connector fetches the complete job list with content enabled. The source
name becomes the normalized company name; Greenhouse departments, timestamps,
and the original response are retained in the normalized job and raw payload.
Repeated job IDs in one response are ignored. Invalid tokens, HTTP failures, and
malformed responses fail the fetch so ingestion does not treat them as job
closures.

Example in the Django shell:

```python
from jobs.models import Source

Source.objects.create(
    platform="greenhouse",
    kind=Source.Kind.ATS,
    name="Example Co",
    base_url="https://boards.greenhouse.io/exampleco",
    config={"board_token": "exampleco"},
)
```
