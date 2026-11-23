# WeRecruit connector

- Platform key: `werecruit`
- Kind: ATS
- Verified against live board: yes
- Example board URL: `https://careers.werecruit.io/en/idiap`
- Example detail URL:
  `https://careers.werecruit.io/en/idiap/offers/postdoctoral-researcher-in-ai-assisted-hiring-37b0ef`
- Format found: server-rendered HTML with the complete listing embedded as
  JSON in `window.allOffers`; detail pages expose a `.description` HTML block
  and a `dcterms.modified` date.
- Pagination: the checked page contained the embedded listing and a
  client-side "see more" control; no next-page request was observed.
- Fixtures: synthetic sanitized structures; no live job text is stored.

Configure `base_url` with the public board page and `company_slug` with the
tenant slug. The external ID comes from the listing's `Id` field. Description
HTML is cleaned for the normalized job; if no explicit apply URL is present,
the public detail URL is used.

TODO: verify against live board whether large tenant listings are truncated
and confirm the exact apply action on other boards.
