.PHONY: setup lint format format-check typecheck migrations-check django-check deployment-check \
	test-core test test-playwright-core test-playwright migrate run worker

ADOPTION_INTEGRATION_PYTHON = \
	accounts/managers.py \
	accounts/tests/test_user.py \
	api/auth.py \
	api/models.py \
	api/tests/test_admin_health.py \
	scripts/capture_screenshots.py \
	scripts/render_course_platform_inventory.py \
	scripts/verify_course_platform_adoption.py

setup:
	uv sync --locked
	mkdir -p .tmp/screenshots
	uv run playwright install chromium

lint:
	uv run ruff check . $(ADOPTION_INTEGRATION_PYTHON)

format:
	uv run ruff format . $(ADOPTION_INTEGRATION_PYTHON)

format-check:
	uv run ruff format --check . $(ADOPTION_INTEGRATION_PYTHON)

typecheck:
	uv run mypy manage.py website core content content_sync events email_app studio jobs deploy \
		scripts/capture_screenshots.py scripts/render_course_platform_inventory.py \
		scripts/verify_course_platform_adoption.py

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
