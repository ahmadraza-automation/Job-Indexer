# Polymer connector

The connector fetches the complete public careers URL configured on the source;
it does not construct an undocumented Polymer endpoint. Configure
`platform="polymer"`, `kind=Source.Kind.ATS`, the company name in `name`, and
`config={"base_url": "<public careers URL>", "company_slug": "<board slug>"}`.
The configured URL must be HTTPS and publicly readable.

The response parser accepts JSON job collections, Schema.org `JobPosting`
JSON-LD, and HTML job cards carrying a stable `data-job-id`, `data-posting-id`,
or supported `data-id`. It follows JSON next links and HTML next anchors on the
same host. Job IDs come only from platform identifiers; missing IDs or malformed
responses fail the fetch rather than creating unstable IDs or closing jobs.

**TODO: verify against live board:** Polymer public careers URL format, response
fields, and pagination markup. Fixtures use synthetic `Example Co` records.

```python
from jobs.models import Source

Source.objects.create(
    platform="polymer",
    kind=Source.Kind.ATS,
    name="Example Co",
    base_url="https://careers.example.invalid/exampleco",
    config={
        "base_url": "https://careers.example.invalid/exampleco",
        "company_slug": "exampleco",
    },
)
```
