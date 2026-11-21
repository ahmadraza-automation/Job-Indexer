# Workday connector

Workday sources use public career-site CXS endpoints:

- Site: `https://<tenant>.<wd_host>.myworkdayjobs.com/<site>`
- List: `POST /wday/cxs/<tenant>/<site>/jobs`
- Detail: `GET /wday/cxs/<tenant>/<site><externalPath>`

Configure `platform="workday"`, `kind="ats"`, the company name in `name`, and:

```python
{
    "tenant": "example",
    "wd_host": "wd5",
    "site": "Example_Careers",
    "fetch_details": True,
}
```

The connector validates `tenant`, `wd_host`, and `site` before making a
request. `wd_host` must use the public Workday form `wdN`. The list request
uses `limit=20` and increments `offset` until `total` is reached. `max_pages`
defaults to 100 and can be lowered in source config. Set
`request_delay_seconds` to a non-negative value to use the framework's
domain-level request delay (the default class delay is inherited).

`fetch_details` defaults to true. Failed detail calls, including HTTP and
malformed JSON failures, are deliberately non-fatal: the list payload remains
available and is normalized into the common job. Details supply description,
job requisition ID, employment type, remote type, start date, and external
apply URL when available.

External IDs are always derived from the list posting's full `externalPath`.
Detail `jobReqId` values are retained in the detail payload and used only for
normalization metadata, never for external-ID selection.

Posted labels such as `Posted Today`, `Posted 3 Days Ago`, and
`Posted 30+ Days Ago` are converted to approximate UTC dates at local midnight.
An ISO or date `startDate` from job detail is preferred.

## Endpoint assumption

The CXS request/response and detail URL formats above are assumed public
Workday formats. The public career-site CXS API shape can vary by tenant,
particularly field names and the presence of `jobPostingInfo.title`,
`externalUrl`, `remoteType`, and `locations`. The implementation accepts common
aliases and fixture-captured JSON is isolated for easy adjustment. External
URLs and all raw list/detail payloads are retained for reprocessing.
