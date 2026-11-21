# JazzHR connector

JazzHR public apply pages are assumed to expose public JSON or JSON-LD/HTML.
Configure `platform="jazzhr"`, `kind="ats"`, company name, and
`config={"company_slug": "<subdomain>"}`. `careers_url` is optional.

The connector accepts JSON job arrays, HTML job cards, and JSON-LD JobPosting
data. It normalizes stable IDs, locations, remote status, employment type,
descriptions, URLs, salary, posted dates, and raw payloads.

## Assumption

The public tenant URL is assumed to be
`https://<subdomain>.applytojob.com/apply`. Public page implementations vary.
This is fixture-tested; a live endpoint check is pending.
