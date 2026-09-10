.PHONY: run migrate data

run:
	uv run python manage.py runserver 0.0.0.0:8000

migrate:
	uv run python manage.py migrate

data:
	uv run python scripts/seed_local_data.py
