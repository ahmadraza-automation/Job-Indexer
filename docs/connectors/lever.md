# Lever connector

Lever sources use the public Postings API and do not require credentials.
Configure a source with `platform="lever"`, `kind="ats"`, the company name in
`name`, the company's posting site slug in `config["company_slug"]`, and the
API host in `base_url`. Use `https://api.lever.co` for the global region or
`https://api.eu.lever.co` for the EU region.

The connector fetches all published postings using the API's `skip` and `limit`
pagination. It maps the posting ID, title, plain-text description, category
location and department, commitment, apply/hosted URLs, workplace type, and
creation timestamp. The original Lever payload is retained in `raw`, including
the HTML description and all categories. Invalid slugs, HTTP failures, and
malformed responses fail the fetch so ingestion does not treat them as job
closures.

Example in the Django shell:

```python
from jobs.models import Source

Source.objects.create(
    platform="lever",
    kind=Source.Kind.ATS,
    name="Example Co",
    base_url="https://api.lever.co",
    config={"company_slug": "exampleco"},
)
```
