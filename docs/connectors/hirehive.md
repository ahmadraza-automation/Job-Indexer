# HireHive

- Platform key: `hirehive`
- Kind: ATS
- Verified against live board: yes
- Example URL: `https://amcsgroup.hirehive.com/api/v2/jobs?page=1&page_size=2`
- Format found: JSON (`items`, `meta`, `links`); `links.next` paginates results.
- Stable ID: API `id`.
- Config: `base_url` is the company's HireHive host and `company_slug` is its board slug.

The connector cleans `description.html`, maps category and job type, reads the
published date, retains the creation date in the raw payload, and uses `hosted_url` for the job and apply
links. Compensation is read from populated `salary` or `compensation_tiers`
fields. Fixtures contain only synthetic records.

TODO: Verify populated salary representations against a live board that
publishes compensation; the checked board's salary field was null.
