# HiringThing

- Platform key: `hiringthing`
- Kind: ATS
- Verified against live board: yes
- Example URL: `https://skijapan.hiringthing.com/api/rss.xml`
- Format found: RSS 2.0 with `item` elements and HTML in
  `media:description`.
- Stable ID: numeric identifier in the public `/job/{id}/...` link.
- Config: `base_url` is the company's HiringThing host and `company_slug` is
  its board slug.

The connector uses the RSS item title, link, category, location, publication
date when present, and media description. The feed did not advertise a next
page when checked. Fixtures use synthetic RSS items.

TODO: Verify whether feeds are ever truncated or expose additional pagination
links; verify date and salary availability on other boards.
