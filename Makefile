.PHONY: up down test migrate shell

up:
	docker compose up -d --build

down:
	docker compose down

test:
	docker compose exec web pytest

migrate:
	docker compose exec web python manage.py migrate

shell:
	docker compose exec web python manage.py shell
