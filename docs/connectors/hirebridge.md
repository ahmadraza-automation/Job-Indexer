# HireBridge connector

- Platform key: `hirebridge`
- Kind: ATS
- Verified against live board: no
- Example URL used: `https://recruit.hirebridge.com/v3/jobs/list.aspx?cid=216`
  returned a generic application error, not a company job board.
- Format found: HTML error page; no public job format verified.
- Fixtures: synthetic generic public-board payloads.

The connector does not construct an unverified HireBridge endpoint. Configure
`base_url` with a public careers URL and `company_slug` with the company's board
slug. The shared parser accepts JSON, JobPosting JSON-LD, or stable-ID HTML job
cards; fetch failures and malformed responses remain failures rather than
empty-board results.

TODO: verify against live board: find a working public board and confirm its
company identifier, pagination, and salary fields.
