# Hireology

- Platform key: `hireology`
- Kind: ATS
- Verified against live board: yes
- Example URL: `https://careers.hireology.com/365labs`
- Format found: server-rendered HTML job cards with `data-qa-id` attributes;
  detail pages contain schema.org `JobPosting` JSON-LD.
- Detail example:
  `https://careers.hireology.com/365labs/2838380/description?ref=career_site&ref_m=api`
- Stable ID: JSON-LD `identifier.value`.
- Config: `base_url` is `https://careers.hireology.com` and `company_slug` is the
  tenant slug.

The connector reads each card's public detail link, then maps JSON-LD
description, location, employment type, dates, and salary. The application
anchor is `#application` on the detail page. Fixtures use synthetic JSON-LD
and cards.

TODO: Verify pagination and empty-board markup against a board with multiple
pages or no open jobs; the checked board returned three jobs on one page.
