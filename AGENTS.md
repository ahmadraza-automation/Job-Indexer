# JobIndexer

## Goal

Index jobs from 58 ATS platforms and 20 job board platforms into one searchable, continuously updated dataset. This project will be technically reviewed by an acquirer, so code must be clean, typed, commented, and easy for another team to take over.

## Stack

Python 3.12, Django 5, Django REST Framework, PostgreSQL (full-text search), Celery + Redis, httpx, selectolax, Playwright (last resort only), drf-spectacular, pytest, Docker Compose.

## Rules

1. One connector class per platform in `connectors/`, inheriting `BaseConnector`.
2. Each connector implements `discover_jobs(source) -> Iterator[NormalizedJob]`.
3. A fetch failure must NEVER mark jobs as closed. Record it in `FetchRun` instead.
4. Every connector is registered in a registry with platform key and kind (`ats`/`board`).
5. Every connector has a unit test using a saved fixture in `tests/fixtures/`.
6. Secrets only via `.env`, never hardcoded.
7. When a connector is added, also update `docs/connectors/<platform>.md` and `COVERAGE.md`.
8. Run tests yourself after each task and fix failures before finishing.
9. Commit to git after each completed task with a clear message.

## Adding a Greenhouse source

Create a `Source` with `platform="greenhouse"`, `kind="ats"`, and the company's
name in `name`. Set `config={"board_token": "<Greenhouse board slug>"}`; the
connector validates the slug and retrieves jobs from Greenhouse's public board
API. For example, in `python manage.py shell`:

```python
from jobs.models import Source

Source.objects.create(
    platform="greenhouse",
    kind=Source.Kind.ATS,
    name="Example Co",
    base_url="https://boards.greenhouse.io/exampleco",
    config={"board_token": "exampleco"},
)
```
