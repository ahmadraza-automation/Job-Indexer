# Personio connector

Personio sources use the public XML feed at
`https://<company>.jobs.personio.de/xml`. Configure `platform="personio"`,
`kind="ats"`, the company name in `name`, and
`config={"company_slug": "<company>"}`. `base_url` may point to the company
Personio career site.

The connector validates the company slug, parses positions from the XML feed,
normalizes title, location, department, employment type, description, remote
status, URLs, dates, external IDs, and raw payload data. It skips duplicate IDs
and reports malformed XML or records as fetch failures.

## Assumption

Personio's public XML feed shape is assumed to use `position`, `id`, `name`,
`office`, `department`, `employmentType`, `jobDescriptions`, and `link`
elements. Common aliases are accepted; this fixture-driven implementation keeps
the feed parser easy to adjust if a deployment uses additional field names.

Example:

```python
from jobs.models import Source

Source.objects.create(
    platform="personio",
    kind=Source.Kind.ATS,
    name="Example Co",
    base_url="https://exampleco.jobs.personio.de",
    config={"company_slug": "exampleco"},
)
```
