# Comeet connector

Comeet careers are expected to be available as public JSON or JSON-LD/HTML at a
configured career URL. Configure `platform="comeet"`, `kind="ats"`, company name,
and `config={"company_slug": "<slug>"}`. `careers_url` is optional.

The connector accepts Comeet-style `positions`, `jobs`, `jobPostings`, HTML
cards, and JSON-LD JobPosting data. It normalizes stable IDs, locations,
remote status, employment type, descriptions, URLs, salary, posted dates, and
raw payloads.

## Assumption

The exact public endpoint and field names are tenant-dependent. The assumed
default is `https://www.comeet.com/jobs/<company_slug>`. This is fixture-tested;
a live endpoint check is pending.
