# Recruitee connector

Recruitee sources use the public Careers Site API. Configure a source with
`platform="recruitee"`, `kind="ats"`, the company name in `name`, and the
company's Recruitee subdomain in `config["company_slug"]`:

```python
from jobs.models import Source

Source.objects.create(
    platform="recruitee",
    kind=Source.Kind.ATS,
    name="Example Co",
    base_url="https://exampleco.recruitee.com",
    config={"company_slug": "exampleco"},
)
```

The connector calls `GET https://{company_slug}.recruitee.com/api/offers/`.
This endpoint returns all published offers in an `offers` array without
pagination. It maps the title, combined description/requirements, locations,
employment type, remote/hybrid status, publication date, salary, and career
and apply URLs. Missing optional fields normalize to empty values; failed
requests and malformed responses fail the fetch rather than closing jobs.

The endpoint and response fields follow the
[official Recruitee Careers Site API documentation](https://docs.recruitee.com/reference/offers).
