# BambooHR connector

BambooHR sources use the public careers JSON endpoint
`https://<company>.bamboohr.com/careers/list`. Configure
`platform="bamboohr"`, `kind="ats"`, the company name in `name`, and
`config={"company_slug": "<company>"}`.

The connector reads careers list payloads, follows public `next` pagination
links when supplied, and requests each job detail at
`https://<company>.bamboohr.com/careers/<id>`. It normalizes title, location,
department, employment type, description, remote status, URLs, dates, external
IDs, and raw payload data.

## Assumption

The list endpoint is assumed to return `result`, `jobs`, `data`, or `postings`
arrays and the per-job detail is assumed to use `/careers/<id>`. The connector
also accepts JSON detail wrappers and HTML JSON-LD/details as field layouts
vary. Detail failures remain visible as fetch failures so ingestion never
mistakes a fetch problem for a closed job.
