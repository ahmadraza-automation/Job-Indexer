# Breezy HR connector

Breezy HR sources use the public job-board JSON feed. Configure a source with
`platform="breezy"`, `kind="ats"`, the company name in `name`, and the Breezy
portal slug in `config["company_slug"]`:

```python
from jobs.models import Source

Source.objects.create(
    platform="breezy",
    kind=Source.Kind.ATS,
    name="Example Co",
    base_url="https://exampleco.breezy.hr",
    config={"company_slug": "exampleco"},
)
```

The connector calls `GET https://{company_slug}.breezy.hr/json`. The public
response is an array of postings and has no pagination. It maps the title,
HTML description, department, location and remote flag, employment type,
published date, salary text, and posting URL. The posting page is used as the
apply URL when the response has no separate application URL. Failed requests
and malformed responses fail the fetch rather than closing existing jobs.

The response shape was verified against the public
[Breezy HR JSON feed](https://jobs.breezy.hr/json).
