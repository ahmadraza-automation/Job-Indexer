# JOIN.com connector

JOIN careers are assumed to be available as public JSON or HTML at a configured
career URL. Configure `platform="join"`, `kind="ats"`, company name, and
`config={"company_slug": "<slug>"}`. `careers_url` is optional.

The connector accepts JOIN-style `jobs`, `positions`, `vacancies`, HTML cards,
and JSON-LD JobPosting data. It normalizes stable IDs, locations, remote status,
employment type, descriptions, URLs, salary, posted dates, and raw payloads.

## Assumption

The exact public endpoint and field names are tenant-dependent. The assumed
default is `https://join.com/companies/<company_slug>`. This is fixture-tested;
a live endpoint check is pending.
