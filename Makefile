.PHONY: setup lint format format-check typecheck migrations-check django-check deployment-check \
	test-core test test-playwright-core test-playwright migrate run worker

setup:
	uv sync --locked
	mkdir -p .tmp/screenshots
	uv run playwright install chromium

lint:
	uv run ruff check .

format:
	uv run ruff format .

format-check:
	uv run ruff format --check .

typecheck:
	uv run mypy manage.py website core accounts content content_sync courses events email_app studio api jobs scripts

migrations-check:
	DJANGO_SETTINGS_MODULE=website.settings.test uv run python manage.py makemigrations --check --dry-run

django-check:
	DJANGO_SETTINGS_MODULE=website.settings.test uv run python manage.py check

deployment-check:
	DTC_ENVIRONMENT=production DJANGO_SETTINGS_MODULE=website.settings.production DJANGO_SECRET_KEY="$$(uv run python -c 'import secrets; print(secrets.token_urlsafe(64))')" DATABASE_URL=postgresql://check:check@127.0.0.1:5432/check DJANGO_ALLOWED_HOSTS=example.invalid DJANGO_CSRF_TRUSTED_ORIGINS=https://example.invalid uv run python manage.py check --deploy --fail-level ERROR

test-core:
	DJANGO_SETTINGS_MODULE=website.settings.test uv run python manage.py test accounts core studio api --parallel

test:
	DJANGO_SETTINGS_MODULE=website.settings.test uv run python manage.py test --parallel

test-playwright-core:
	DJANGO_SETTINGS_MODULE=website.settings.test DJANGO_ALLOW_ASYNC_UNSAFE=true uv run pytest playwright_tests -m core -v

test-playwright:
	DJANGO_SETTINGS_MODULE=website.settings.test DJANGO_ALLOW_ASYNC_UNSAFE=true uv run pytest playwright_tests -v

migrate:
	uv run python manage.py migrate

run:
	uv run python manage.py runserver 0.0.0.0:8000

worker:
	uv run python manage.py qcluster
