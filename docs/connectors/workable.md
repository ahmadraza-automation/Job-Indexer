# Workable connector

Workable sources use the public full-details job widget endpoint, so no API
token is required. Configure a source with `platform="workable"`,
`kind="ats"`, the company name in `name`, the account subdomain in
`config["subdomain"]`, and the API root in `base_url`:

```python
Source.objects.create(
    platform="workable",
    kind=Source.Kind.ATS,
    name="Example Co",
    base_url="https://www.workable.com/api/accounts",
    config={"subdomain": "exampleco"},
)
```

The connector calls
`GET {base_url}/{subdomain}?details=true`. This is the public endpoint
Workable documents for retrieving published jobs and their descriptions. It
is preferred over `/spi/v3/jobs` because the latter requires an authenticated
API token and does not satisfy the public-board requirement. The public
response has an account object containing a `jobs` array; the endpoint
documentation does not describe paging or a continuation token, so the
connector consumes the returned array as a whole.

Workable documents stable `id` and generated `shortcode` job fields. The
connector uses `id`, falling back to `shortcode`, then `url` if those are
missing. It maps `location.location_str` (or city, region, country), includes
additional entries from `locations`, uses `location.telecommuting` for remote
status, and converts `created_at` to UTC. The full posting is retained in
`raw`; `full_description` is preferred for HTML text with `description` as a
fallback, and plain text is derived from that HTML.

The endpoint and field names follow Workable's official
[careers-page API guide](https://help.workable.com/hc/en-us/articles/115012771647-Using-the-Workable-API-to-create-a-careers-page),
[public job widget guide](https://help.workable.com/hc/en-us/articles/115012801727-How-to-embed-jobs-on-your-website-job-widget),
and [API reference](https://workable.readme.io/reference/jobs).
