# Kula connector

- Platform key: `kula`
- Kind: ATS
- Verified against live board: no
- Example URL used: none found; no tenant-specific public board URL was
  identified for direct verification.
- Format found: unknown. The connector uses the shared JSON, JSON-LD, and
  stable-ID HTML-card public-board parser without constructing a Kula endpoint.
- Fixtures: synthetic generic public-board payloads.

Configure `base_url` with a public careers URL and `company_slug` with the
company's board slug. Stable IDs are taken from the public job record; invalid
or malformed responses fail the fetch.

TODO: verify against live board: find a tenant URL and confirm its feed or page
format, pagination, and salary fields before adding platform-specific parsing.
