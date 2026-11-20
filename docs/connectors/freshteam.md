# Freshteam connector

Freshteam sources use the public jobs widget JSON feed. Configure a source with
`platform="freshteam"`, `kind="ats"`, the company name in `name`, and the
Freshteam subdomain in `config["company_slug"]`:

```python
from jobs.models import Source

Source.objects.create(
    platform="freshteam",
    kind=Source.Kind.ATS,
    name="Example Co",
    base_url="https://exampleco.freshteam.com",
    config={"company_slug": "exampleco"},
)
```

The connector calls
`GET https://{company_slug}.freshteam.com/hire/widgets/jobs.json`. The response
contains a `jobs` array plus `branches` and `job_roles` lookup arrays; it does
not paginate. Branches provide location and currency, while job roles provide
department names. The connector maps job descriptions, salary details,
employment type, remote status, creation date, and posting URL. The posting
page is used as the apply URL when no separate application URL is provided.
Failed requests and malformed responses fail the fetch rather than closing
existing jobs.

The endpoint and response fields were verified against the public
[Freshteam jobs widget feed](https://zetwerk.freshteam.com/hire/widgets/jobs.json).
