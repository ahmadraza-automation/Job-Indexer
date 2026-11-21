# Rippling connector

Rippling sources use the public board on `ats.rippling.com`. Configure
`platform="rippling"`, `kind="ats"`, the company name in `name`, and
`config={"company_slug": "<company>"}`. `base_url` should normally be
`https://ats.rippling.com/<company>`.

The connector accepts public JSON job lists and HTML pages containing embedded
JSON/Next.js data. It follows `next` URLs or cursors when present and
normalizes title, location, department, employment type, description, remote
status, URLs, dates, external IDs, and raw payload data.

## Assumption

The public board URL is assumed to be
`https://ats.rippling.com/<company_slug>`. Rippling may expose the jobs through
JSON keys such as `jobs`, `postings`, `openings`, `results`, `items`, or nested
Next.js `pageProps` data. The broad payload extraction keeps the connector easy
to adjust once the exact deployed endpoint shape is fixture-captured.
