# HiBob connector

HiBob career pages are assumed to expose public JSON, XML, or JSON-LD/HTML.
Configure `platform="hibob"`, `kind="ats"`, company name, and
`config={"company_slug": "<subdomain>"}`. `careers_url` is optional.

The connector accepts JSON arrays under common job keys, RSS/XML-style items,
HTML cards, and JSON-LD JobPosting data. It normalizes stable IDs, locations,
remote status, employment type, descriptions, URLs, salary, posted dates, and
raw payloads.

## Assumption

The exact HiBob tenant career endpoint is tenant-dependent. The assumed default
is `https://<subdomain>.hibob.com/careers`. This is fixture-tested; a live
endpoint check is pending.
