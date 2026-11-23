# JobIndexer

JobIndexer is the foundation for a searchable, continuously updated dataset of jobs sourced from ATS platforms and job boards.

## Requirements

- Docker Desktop for Windows with Docker Compose enabled
- Git

## Run with Docker Compose (PowerShell)

From the project directory:

```powershell
Copy-Item .env.example .env
```

Edit `.env` and set `DJANGO_SECRET_KEY` and `POSTGRES_PASSWORD` to private values. Then start the application:

```powershell
docker compose up -d --build
Invoke-WebRequest http://localhost:8000/health
docker compose exec web pytest
```

The health endpoint returns HTTP 200 at `/health`. Compose starts PostgreSQL, Redis, the Django web service, a Celery worker, and Celery beat. The web service applies database migrations during startup.

Useful commands:

```powershell
docker compose down
docker compose exec web python manage.py migrate
docker compose exec web python manage.py shell
```

The `Makefile` also provides `up`, `down`, `test`, `migrate`, and `shell` targets where GNU Make is installed.

## Adding an employer or board

Add one source per employer or board, even when multiple sources use the same
platform. The `add_source` command checks the connector registry, validates the
URL and slug, and rejects duplicate platform/employer identities:

```powershell
docker compose exec web python manage.py add_source --platform greenhouse --url https://boards.greenhouse.io/exampleco --slug exampleco --name "Example Co" --interval 60
docker compose exec web python manage.py list_sources --platform greenhouse
docker compose exec web python manage.py list_sources --json
docker compose exec web python manage.py index_source 1
docker compose exec web python manage.py index_source --platform greenhouse --all --dry-run --json
```

For platform-specific configuration such as Workday tenant/site details, use
the Django admin or `python manage.py shell` to create the `Source` with the
required connector config. Each source is refreshed independently; a failed
fetch is recorded and does not close its jobs.

## Local development

Create and activate a Python 3.12 virtual environment, install dependencies with `pip install -r requirements.txt`, and configure `.env` for a reachable PostgreSQL and Redis instance. Run `python manage.py migrate` and `python manage.py runserver`; run tests with `pytest`.

