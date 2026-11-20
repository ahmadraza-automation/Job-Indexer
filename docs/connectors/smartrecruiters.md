# SmartRecruiters connector

SmartRecruiters sources use the public Posting API. Configure a source with
`platform="smartrecruiters"`, `kind="ats"`, the company name in `name`, and the
company identifier from its SmartRecruiters career site in
`config["company"]`:

```python
from jobs.models import Source

Source.objects.create(
    platform="smartrecruiters",
    kind=Source.Kind.ATS,
    name="Example Co",
    base_url="https://careers.smartrecruiters.com/exampleco",
    config={"company": "exampleco"},
)
```

The connector calls the public
`GET https://api.smartrecruiters.com/v1/companies/{company}/postings` endpoint
with `limit` and `offset` until all postings have been listed, then requests
each posting's detail endpoint. It maps the job-ad description, location,
department, employment type, release date, and application URL. The list and
detail responses are preserved in `raw`. Missing optional fields normalize to
empty values; invalid responses and HTTP failures fail the fetch rather than
closing existing jobs. The shared connector client handles request timeouts and
retries transient errors.

The endpoint and response fields follow the
[official SmartRecruiters Posting API documentation](https://developers.smartrecruiters.com/docs/endpoints).
