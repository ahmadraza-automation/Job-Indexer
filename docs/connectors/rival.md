# Rival connector

- Platform key: `rival`
- Kind: ATS
- Verified against live board: no
- Example URL used: none found; no public Rival board URL was identified for
  direct verification.
- Format found: unknown. The connector uses the shared JSON, JSON-LD, and
  stable-ID HTML-card public-board parser without assuming a vendor endpoint.
- Fixtures: synthetic generic public-board payloads.

Configure `base_url` with the public careers URL and `company_slug` with the
company's board slug. The connector preserves platform-provided record IDs and
raises fetch errors for malformed data or unrecognized pages.

TODO: verify against live board: find a public board and confirm its endpoint,
pagination, and any salary fields.
