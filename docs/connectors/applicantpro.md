# ApplicantPro

- Platform key: `applicantpro`
- Kind: ATS
- Verified against live board: yes (the public page was reachable; job data was
  not server-rendered)
- Example URL: `https://zealcu.applicantpro.com/jobs/`
- Format found: HTML page with a Vue `<job-listings>` mount and bootstrap
  metadata; no job records were present in the fetched HTML.
- Stable ID: not confirmed from the static page.
- Config: `base_url` is the public jobs URL and `company_slug` is the
  ApplicantPro subdomain slug.

The connector delegates to the existing generic public-board parser. It
supports public JSON, JSON-LD, and stable-ID HTML cards if present, but treats
the observed client-rendered shell as a fetch failure rather than an empty
board. The observed-shell fixture and generic parser fixtures contain only
synthetic data.

TODO: Verify the anonymous public listing response/API and its pagination
before enabling live ingestion for ApplicantPro.
