# iSolvedHire connector

- Platform key: `isolvedhire`
- Kind: ATS
- Verified against live board: no (the public page was reachable, but its
  listings were a client-rendered shell)
- Example URL: `https://andpizza.isolvedhire.com/jobs/`
- Format found: HTML shell with a `<job-listings>` mount; no server-rendered
  jobs or confirmed public listing endpoint.
- Fixtures: sanitized shell structure and synthetic generic public-board
  payloads.

The connector uses the shared public-board parser with the configured
`base_url` and `company_slug`. The observed shell is intentionally treated as
an unrecognized response and raises a fetch error; it is not interpreted as an
empty board.

TODO: verify against live board: confirm the anonymous public listing endpoint
and response shape. Do not rely on client-only bootstrap data until its public
contract is confirmed.
