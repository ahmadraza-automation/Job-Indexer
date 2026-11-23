# FirstStage connector

- Platform key: `firststage`
- Kind: ATS
- Verified against live board: no
- Example URL used: none found; no public board URL was identified in the checked
  ATS company data or source search.
- Format found: unknown. The connector uses the shared JSON, JSON-LD, and
  stable-ID HTML-card public-board parser without assuming a FirstStage endpoint.
- Fixtures: synthetic generic public-board payloads.

Configure `base_url` with the public careers URL and `company_slug` with the
company's board slug. Stable IDs are read from the platform payload; malformed
responses or records without a stable ID fail the fetch.

TODO: verify against live board: find a public board, confirm its response
shape and pagination, and map optional salary fields before adding
FirstStage-specific parsing.
