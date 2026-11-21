# Jobvite connector

Jobvite job boards are assumed to expose public JSON or RSS/XML. Configure
`platform="jobvite"`, `kind="ats"`, company name, and
`config={"company_slug": "<company>"}`. `careers_url` is optional.

The connector accepts JSON under common job keys and XML job/item records. It
normalizes stable IDs, locations, remote status, employment type, descriptions,
URLs, salary, posted dates, and raw payloads.

## Assumption

The public tenant URL is assumed to be
`https://jobs.jobvite.com/<company>/jobs`. Field layouts vary between public
feeds and tenant pages. This is fixture-tested; a live endpoint check is
pending.
