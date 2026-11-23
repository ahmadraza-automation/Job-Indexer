# CareerPlug

- Platform key: `careerplug`
- Kind: ATS
- Verified against live board: yes
- Example URL: `https://1-800-water-damage.careerplug.com/jobs`
- Format found: HTML listing links and HTML detail pages. The detail page uses
  `.trix-content` for the description and `.job-compensation` for salary.
- Stable ID: numeric ID in `/jobs/{id}`.
- Config: `base_url` is the company's CareerPlug host and `company_slug` is its
  board slug.

The connector fetches each public detail page, cleans the description, maps
salary when present, and uses the observed `/apps/new` URL as the apply link.
Fixtures contain synthetic listings and details.

TODO: Verify listing pagination against a multi-page live board; the checked
listing did not expose a next page.
