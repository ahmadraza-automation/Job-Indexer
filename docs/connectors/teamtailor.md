# Teamtailor connector

Teamtailor sources use the public careers RSS/XML feed. Configure
`platform="teamtailor"`, `kind="ats"`, the company name in `name`, and
`config={"company_slug": "<company>"}`. By default the connector assumes
`https://<company>.teamtailor.com/jobs.rss`. Set `config["feed_url"]` when a
company exposes the feed at another public URL.

The connector parses RSS items or Atom entries, maps title, company, location,
department, employment type, description, remote status, URLs, dates, external
IDs, and raw payload data. Malformed feeds and invalid items fail the fetch
instead of marking existing jobs closed.

## Assumption

The public feed shape is assumed to expose `jobs.rss` on the company
Teamtailor subdomain. RSS and namespaced Teamtailor fields are accepted
(`location`, `department`, `employment_type`, `remote`, and `updated`), and
`feed_url` provides an explicit override for deployed career sites.

Example:

```python
from jobs.models import Source

Source.objects.create(
    platform="teamtailor",
    kind=Source.Kind.ATS,
    name="Example Co",
    base_url="https://exampleco.teamtailor.com",
    config={"company_slug": "exampleco"},
)
```
