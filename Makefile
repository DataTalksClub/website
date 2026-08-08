.PHONY: setup lint format format-check typecheck migrations-check django-check deployment-check \
	test-core test test-compatibility compatibility-source-artifacts-check \
	compatibility-artifacts-check check-links check-seo compatibility-real-gate-blocked-check \
	test-content test-content-postgresql test-playwright-core test-playwright migrate run worker

ADOPTION_INTEGRATION_PYTHON = \
	accounts/managers.py \
	accounts/tests/test_user.py \
	api/auth.py \
	api/models.py \
	api/tests/test_admin_health.py \
	scripts/capture_screenshots.py \
	scripts/render_course_platform_inventory.py \
	scripts/verify_course_platform_adoption.py

COMPATIBILITY_PYTHON = \
	compatibility \
	scripts/build_legacy_manifest.py \
	scripts/build_pinned_legacy_sources.py

setup:
	uv sync --locked
	mkdir -p .tmp/screenshots
	uv run playwright install chromium

lint:
	uv run ruff check . $(ADOPTION_INTEGRATION_PYTHON) $(COMPATIBILITY_PYTHON)

format:
	uv run ruff format . $(ADOPTION_INTEGRATION_PYTHON) $(COMPATIBILITY_PYTHON)

format-check:
	uv run ruff format --check . $(ADOPTION_INTEGRATION_PYTHON) $(COMPATIBILITY_PYTHON)

typecheck:
	uv run mypy manage.py website core content content_sync events email_app studio jobs deploy \
		$(COMPATIBILITY_PYTHON) \
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

test-content:
	DJANGO_SETTINGS_MODULE=website.settings.test uv run python manage.py test content.tests

test-content-postgresql:
	DJANGO_SETTINGS_MODULE=website.settings.test uv run python -c 'import django; django.setup(); from django.db import connection; assert connection.vendor == "postgresql", "DATABASE_URL must select PostgreSQL"'
	DJANGO_SETTINGS_MODULE=website.settings.test uv run python manage.py test content.tests -v 2

test: test-compatibility
	DJANGO_SETTINGS_MODULE=website.settings.test uv run python manage.py test --parallel

test-compatibility:
	uv run pytest compatibility/tests -q

compatibility-source-artifacts-check:
	uv run python scripts/build_pinned_legacy_sources.py --check

compatibility-artifacts-check:
	uv run python scripts/build_legacy_manifest.py validate \
		_docs/compatibility/legacy-manifest.jsonl
	uv run python scripts/build_legacy_manifest.py compare \
		_docs/compatibility/legacy-manifest.jsonl \
		--output .tmp/compatibility/legacy-manifest-differences.check.json
	cmp _docs/compatibility/legacy-manifest-differences.json \
		.tmp/compatibility/legacy-manifest-differences.check.json

check-links:
	uv run pytest compatibility/tests/test_links.py compatibility/tests/test_runtime.py -q

check-seo:
	uv run pytest compatibility/tests/test_expectations.py compatibility/tests/test_report.py \
		compatibility/tests/test_target.py compatibility/tests/test_parity.py \
		compatibility/tests/test_monitoring.py -q

compatibility-real-gate-blocked-check:
	mkdir -p .tmp/compatibility
	rm -f .tmp/compatibility/checked-real-seo-parity-report.json
	@if DJANGO_SETTINGS_MODULE=website.settings.test uv run python manage.py compatibility_gate \
		--route-sha256 0000000000000000000000000000000000000000000000000000000000000000 \
		--asset-sha256 1111111111111111111111111111111111111111111111111111111111111111 \
		--projection-sha256 2222222222222222222222222222222222222222222222222222222222222222 \
		--output .tmp/compatibility/checked-real-seo-parity-report.json; then \
		echo "Unapproved checked inputs unexpectedly passed" >&2; exit 1; \
	fi
	uv run python -c 'import hashlib,json,pathlib; root=pathlib.Path("_docs/compatibility"); p=json.load(open(".tmp/compatibility/checked-real-seo-parity-report.json", encoding="utf-8")); digest=lambda name: hashlib.sha256((root/name).read_bytes()).hexdigest(); assert p["status"] == "BLOCKED" and p["expectation_count"] == 0; assert p["manifest_sha256"] == digest("legacy-manifest.jsonl"); assert p["differences_sha256"] == digest("legacy-manifest-differences.json"); assert p["public_contracts_sha256"] == digest("public-contracts.jsonl")'

test-playwright-core:
	DJANGO_SETTINGS_MODULE=website.settings.test DJANGO_ALLOW_ASYNC_UNSAFE=true uv run pytest playwright_tests -m core -v

test-playwright:
	DJANGO_SETTINGS_MODULE=website.settings.test DJANGO_ALLOW_ASYNC_UNSAFE=true uv run pytest playwright_tests -v

migrate:
	uv run python manage.py migrate

run:
	uv run python manage.py runserver 0.0.0.0:8000

worker:
	uv run python manage.py run_job_worker
