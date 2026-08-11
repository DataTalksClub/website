.PHONY: setup lock-check lint format format-check typecheck migrations-check django-check deployment-check \
	test-core test test-ci test-ci-focused test-compatibility compatibility-source-artifacts-check \
	compatibility-artifacts-check check-links check-seo compatibility-real-gate-blocked-check \
	test-content test-factories test-migrations test-playwright-core test-playwright test-browser \
	test-accessibility \
	test-remote-readonly test-remote-mutation test-live-email test-live-provider test-all migrate run worker \
	terraform-seo-source-check terminology-check check-openapi check-management-parity \
	database-portability-check verify-dtc-content review-data review-data-dry-run \
	review-data-cleanup run-review-data

ADOPTION_INTEGRATION_PYTHON = \
	accounts/managers.py \
	accounts/tests/test_user.py \
	api/auth.py \
	api/models.py \
	api/tests/test_admin_health.py \
	scripts/build_local_review_db.py \
	scripts/capture_screenshots.py \
	scripts/check_database_portability.py \
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

lock-check:
	uv lock --check

lint:
	uv run ruff check . $(ADOPTION_INTEGRATION_PYTHON) $(COMPATIBILITY_PYTHON)

format:
	uv run ruff format . $(ADOPTION_INTEGRATION_PYTHON) $(COMPATIBILITY_PYTHON)

format-check:
	uv run ruff format --check . $(ADOPTION_INTEGRATION_PYTHON) $(COMPATIBILITY_PYTHON)

typecheck:
	uv run mypy manage.py website core content content_sync events email_app studio jobs deploy ci \
		test_support conftest.py sitecustomize.py \
		review_import \
		management_auth management_api management_registry.py \
		$(COMPATIBILITY_PYTHON) \
		scripts/build_local_review_db.py scripts/capture_screenshots.py \
		scripts/check_database_portability.py \
		scripts/render_course_platform_inventory.py \
		scripts/verify_course_platform_adoption.py

migrations-check:
	DJANGO_SETTINGS_MODULE=website.settings.test uv run python manage.py makemigrations --check --dry-run

django-check: check-openapi check-management-parity
	DJANGO_SETTINGS_MODULE=website.settings.test uv run python manage.py check

deployment-check:
	DTC_ENVIRONMENT=production VERSION=20260809-143205-aaaaaaa SOURCE_SHA=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa IMAGE_DIGEST=sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb DJANGO_SETTINGS_MODULE=website.settings.production DJANGO_SECRET_KEY="$$(uv run python -c 'import secrets; print(secrets.token_urlsafe(64))')" DATABASE_URL=postgresql://check:check@127.0.0.1:5432/check DJANGO_ALLOWED_HOSTS=example.invalid DJANGO_CSRF_TRUSTED_ORIGINS=https://example.invalid uv run python manage.py check --deploy --fail-level ERROR

terminology-check:
	uv run python scripts/check_development_terminology.py

database-portability-check:
	uv run python scripts/check_database_portability.py

verify-dtc-content:
	@test -n "$(CONTENT_CHECKOUT)" || (echo "CONTENT_CHECKOUT is required" >&2; exit 2)
	@test -n "$(CONTENT_COMMIT)" || (echo "CONTENT_COMMIT is required" >&2; exit 2)
	DJANGO_SETTINGS_MODULE=website.settings.test uv run python manage.py verify_dtc_content \
		--checkout "$(CONTENT_CHECKOUT)" \
		--expected-commit "$(CONTENT_COMMIT)"

terraform-seo-source-check:
	@test -n "$(AWS_INFRA_REPOSITORY)" || (echo "AWS_INFRA_REPOSITORY is required" >&2; exit 2)
	@test -n "$(AWS_INFRA_REVISION)" || (echo "AWS_INFRA_REVISION is required" >&2; exit 2)
	@test -n "$(AWS_INFRA_EXPECTED_COMMIT)" || (echo "AWS_INFRA_EXPECTED_COMMIT is required" >&2; exit 2)
	uv run python -m scripts.verify_development_seo_terraform \
		--repository "$(AWS_INFRA_REPOSITORY)" \
		--revision "$(AWS_INFRA_REVISION)" \
		--expected-commit "$(AWS_INFRA_EXPECTED_COMMIT)"

test-core:
	DTC_TEST_RUN_ID="$${DTC_TEST_RUN_ID:-make-$${PPID}}" \
		DJANGO_SETTINGS_MODULE=website.settings.test uv run --frozen python manage.py test \
		accounts core studio api management_auth management_api --parallel

check-openapi:
	DJANGO_SETTINGS_MODULE=website.settings.test uv run python manage.py generate_admin_openapi --check

check-management-parity:
	DJANGO_SETTINGS_MODULE=website.settings.test uv run python manage.py check_management_parity

test-content:
	DTC_TEST_RUN_ID="$${DTC_TEST_RUN_ID:-make-$${PPID}}" \
		DJANGO_SETTINGS_MODULE=website.settings.test uv run --frozen python manage.py test content.tests

test: test-compatibility
	DTC_TEST_RUN_ID="$${DTC_TEST_RUN_ID:-make-$${PPID}}" \
		DJANGO_SETTINGS_MODULE=website.settings.test uv run --frozen python manage.py test --parallel

test-ci:
	uv run --frozen pytest ci/tests -q

test-ci-focused:
	@test -n "$$CI_SELECTION_PATH" || (echo "CI_SELECTION_PATH is required" >&2; exit 2)
	DTC_TEST_RUN_ID="$${DTC_TEST_RUN_ID:-make-$${PPID}}" \
		DJANGO_SETTINGS_MODULE=website.settings.test uv run --frozen python -m ci.focused_tests \
		--selection "$$CI_SELECTION_PATH"

test-compatibility:
	DTC_TEST_RUN_ID="$${DTC_TEST_RUN_ID:-make-$${PPID}}" \
		uv run --frozen pytest compatibility/tests -q

test-factories:
	DTC_TEST_RUN_ID="$${DTC_TEST_RUN_ID:-make-$${PPID}}" \
		uv run --frozen pytest test_support/tests/test_factories.py \
		test_support/tests/test_runtime.py test_support/tests/test_safety.py \
		test_support/tests/test_marker_registry.py -q

test-migrations:
	DTC_TEST_RUN_ID="$${DTC_TEST_RUN_ID:-make-$${PPID}}" \
		DJANGO_SETTINGS_MODULE=website.settings.test uv run --frozen python manage.py test \
		test_support.tests.test_migrations accounts.tests.test_identity_migrations \
		content.tests.test_editorial_route_migration_contract content.tests.test_migrations

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
	DTC_TEST_RUN_ID="$${DTC_TEST_RUN_ID:-make-$${PPID}}" \
		DJANGO_SETTINGS_MODULE=website.settings.test DJANGO_ALLOW_ASYNC_UNSAFE=true \
		uv run --frozen pytest playwright_tests \
		-m 'core and not remote_readonly and not remote_mutation and not live_email and not live_provider' -v

test-playwright:
	DTC_TEST_RUN_ID="$${DTC_TEST_RUN_ID:-make-$${PPID}}" \
		DJANGO_SETTINGS_MODULE=website.settings.test DJANGO_ALLOW_ASYNC_UNSAFE=true \
		uv run --frozen pytest playwright_tests \
		-m '(core or full) and not remote_readonly and not remote_mutation and not live_email and not live_provider' -v

test-accessibility:
	DTC_TEST_RUN_ID="$${DTC_TEST_RUN_ID:-make-$${PPID}}" \
		DJANGO_SETTINGS_MODULE=website.settings.test DJANGO_ALLOW_ASYNC_UNSAFE=true \
		uv run --frozen pytest playwright_tests/test_accessibility.py \
		-m 'accessibility and not remote_readonly and not remote_mutation and not live_email and not live_provider' -v

test-browser: test-playwright

test-remote-readonly:
	DTC_TEST_SAFETY_COMMAND=remote_readonly uv run --frozen pytest -m remote_readonly -v

test-remote-mutation:
	DTC_TEST_SAFETY_COMMAND=remote_mutation uv run --frozen pytest -m remote_mutation -v

test-live-email:
	DTC_TEST_SAFETY_COMMAND=live_email uv run --frozen pytest -m live_email -v

test-live-provider:
	DTC_TEST_SAFETY_COMMAND=live_provider uv run --frozen pytest -m live_provider -v

.NOTPARALLEL: test-all
test-all: lock-check terminology-check database-portability-check lint format-check typecheck \
	migrations-check django-check deployment-check compatibility-source-artifacts-check \
	compatibility-artifacts-check check-links check-seo test-factories test-migrations test \
	test-playwright

migrate:
	uv run python manage.py migrate

run:
	uv run python manage.py runserver 0.0.0.0:8000

worker:
	uv run python manage.py run_job_worker

review-data:
	@test -n "$(SOURCE_DB)" || (echo "SOURCE_DB is required" >&2; exit 2)
	@test -n "$(SNAPSHOT_ID)" || (echo "SNAPSHOT_ID is required" >&2; exit 2)
	uv run python scripts/build_local_review_db.py build \
		--source-db "$(SOURCE_DB)" \
		--snapshot-id "$(SNAPSHOT_ID)"

review-data-dry-run:
	@test -n "$(SOURCE_DB)" || (echo "SOURCE_DB is required" >&2; exit 2)
	@test -n "$(SNAPSHOT_ID)" || (echo "SNAPSHOT_ID is required" >&2; exit 2)
	uv run python scripts/build_local_review_db.py build \
		--source-db "$(SOURCE_DB)" \
		--snapshot-id "$(SNAPSHOT_ID)" \
		--dry-run

review-data-cleanup:
	@test -n "$(SNAPSHOT_ID)" || (echo "SNAPSHOT_ID is required" >&2; exit 2)
	uv run python scripts/build_local_review_db.py cleanup \
		--snapshot-id "$(SNAPSHOT_ID)" \
		$(if $(filter true,$(INCLUDE_TARGET)),--include-target,)

run-review-data:
	DTC_ENVIRONMENT=local \
		DTC_SQLITE_PATH=.tmp/review-data/review.sqlite3 \
		DJANGO_SETTINGS_MODULE=website.settings.local_review \
		uv run python manage.py runserver 0.0.0.0:8000
