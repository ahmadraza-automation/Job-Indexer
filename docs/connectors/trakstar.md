# Trakstar Hire connector

- Platform key: `trakstar`
- Kind: ATS
- Verified against live board: yes
- Example board URL: `https://aservicesgroup.hire.trakstar.com/`
- Feed URL checked: `https://aservicesgroup.hire.trakstar.com/jobfeeds/aservicesgroup`
- Format found: RSS 2.0 XML. Items provide the job URL/ID, title, description,
  publication date, location, employment type, and team. The apply link is
  embedded in the description HTML.
- Pagination: no `atom:next` link was observed; the checked feed exposed a
  self-link only.
- Fixtures: synthetic RSS items with no real company or job data.

Configure `base_url` with the public Trakstar board host and `company_slug`
with its board slug. The stable external ID is taken from the job URL or GUID,
never from a list position. The connector follows the framework's existing
HTTP retry and fetch-error behavior.

TODO: verify against live board that the RSS feed is not capped for large
boards and confirm salary representations on a board that publishes
compensation.
