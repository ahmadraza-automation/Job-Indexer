# Pinpoint connector

Pinpoint sources use the public Job Postings JSON endpoint. Configure a source
with `platform="pinpoint"`, `kind="ats"`, the company name in `name`, and the
company's Pinpoint subdomain in `config["company_slug"]`:

```python
from jobs.models import Source

Source.objects.create(
    platform="pinpoint",
    kind=Source.Kind.ATS,
    name="Example Co",
    base_url="https://exampleco.pinpointhq.com",
    config={"company_slug": "exampleco"},
)
```

The connector calls `GET https://{company_slug}.pinpointhq.com/postings.json`.
The feed returns all postings in a top-level `data` array and does not
paginate. It maps the title, description, department, location, employment
type, workplace type, compensation, and posting URL. The documented feed does
not include a posting date, so `posted_at` remains unset unless a tenant
includes a `published_at` or `created_at` value. Failed requests and malformed
responses fail the fetch rather than closing existing jobs.

The endpoint and response fields follow the
[official Pinpoint Job Postings JSON documentation](https://developers.pinpointhq.com/docs/jobs-json-endpoint).
