# Ashby connector

Ashby sources use the public Job Postings API and do not require credentials.
Configure a source with `platform="ashby"`, `kind="ats"`, the company name in
`name`, the job board name in `config["board_name"]`, and the API root in
`base_url` (normally `https://api.ashbyhq.com`).

The connector requests compensation with `includeCompensation=true` and reads
postings from the documented `jobs` array. The API's documented posting schema
does not include an `id` field, so the connector uses one if present and
otherwise uses the documented `jobUrl` as the external ID. It maps the title,
department, location, remote status, employment type, plain-text description,
URLs, and UTC publication time. Team, secondary locations, HTML description,
and compensation are retained in the raw payload. Postings with
`isListed=false` are skipped. HTTP failures and malformed responses fail the
fetch so ingestion does not treat them as job closures.

The response shape and field names follow the
[official Ashby Job Postings API documentation](https://developers.ashbyhq.com/docs/public-job-posting-api).

Example in the Django shell:

```python
from jobs.models import Source

Source.objects.create(
    platform="ashby",
    kind=Source.Kind.ATS,
    name="Example Co",
    base_url="https://api.ashbyhq.com",
    config={"board_name": "exampleco"},
)
```
