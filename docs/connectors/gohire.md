# GoHire

- Platform key: `gohire`
- Kind: ATS
- Verified against live board: yes (the board and a public job detail page were
  fetched)
- Example URLs:
  - `https://jobs.gohire.io/yourparkingspace-gnf5tges/`
  - `https://jobs.gohire.io/iproov-fzoa1liw/head-of-product-innovation-86015/`
- Format found: HTML board; detail page contains schema.org `JobPosting`
  JSON-LD.
- Stable ID: JSON-LD `identifier.value`, with the numeric ID in the detail
  route as a stable fallback.
- Config: `base_url` is `https://jobs.gohire.io` and `company_slug` is the
  board's route token.

The connector follows job-detail links from the public board and normalizes
the JSON-LD, including remote status, employment type, and posted date. The
checked YourParkingSpace board was empty; the iProov detail example is closed.
Fixtures are synthetic.

TODO: Verify listing-card markup and page transitions against a board with
open jobs. The fetched empty board exposed a hidden POST paging form, but no
live next-page control.
