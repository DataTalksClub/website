.PHONY: setup lock-check lint format format-check typecheck migrations-check django-check deployment-check \
	test-core test test-ci test-ci-focused test-compatibility compatibility-source-artifacts-check \
	compatibility-artifacts-check check-links check-seo compatibility-real-gate-blocked-check \
	test-content test-factories test-migrations test-playwright-core test-playwright test-browser \
	test-accessibility \
	security-check security-artifact-scan \
	test-remote-readonly test-remote-mutation test-live-email test-live-provider test-all migrate run worker \
	terraform-seo-source-check terminology-check check-openapi check-management-parity \
	database-portability-check verify-dtc-content review-data review-data-dry-run \
	review-data-cleanup run-review-data verification-plan verification-run verification-full \
	verification-quality verification-container verification-content-invariants \
	verification-evidence-check verification-report-check

VERIFY_BASE_SHA ?= HEAD
VERIFY_HEAD_SHA ?= HEAD
VERIFY_OUTPUT_DIR ?= .tmp/verification
VERIFY_PLAN ?= $(if $(VERIFY_PLAN_PATH),$(VERIFY_PLAN_PATH),$(VERIFY_OUTPUT_DIR)/verification-plan.json)
VERIFY_EVIDENCE_DIR ?= $(VERIFY_OUTPUT_DIR)/evidence
VERIFY_REPORT ?= $(if $(VERIFY_REPORT_PATH),$(VERIFY_REPORT_PATH),$(VERIFY_OUTPUT_DIR)/verification-report.json)
VERIFY_INVARIANT ?= $(VERIFY_EVIDENCE_DIR)/content-invariants.json
VERIFY_CONTAINER_OUTPUT ?= $(VERIFY_EVIDENCE_DIR)/container-check.json
VERIFY_ISSUE ?= 113
VERIFY_WORKTREE ?= local
VERIFY_CONSUMER ?= engineer
VERIFY_PHASE ?= $(VERIFY_CONSUMER)
VERIFY_PRODUCER_ROLE ?= $(if $(filter tester,$(VERIFY_CONSUMER)),tester,engineer)
SECURITY_ARTIFACT_INPUTS ?= .tmp/security/security-baseline.json .tmp/security/security-vulnerability-scan.json .tmp/security/security-redaction-canary.json
SECURITY_ARTIFACT_CANARIES ?= synthetic-secret-canary synthetic-email@example.invalid synthetic-token-canary
SECURITY_VULNERABILITY_EVIDENCE ?= .tmp/security/security-vulnerability-scan.json

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

security-check:
	mkdir -p .tmp/security
	uv run --frozen python -m scripts.security_baseline --repository . --output .tmp/security/security-baseline.json
	uv run --frozen python -m scripts.security_vulnerability_scan --repository . --output "$(SECURITY_VULNERABILITY_EVIDENCE)"
	uv run --frozen python -m scripts.security_canary_artifact --output .tmp/security/security-redaction-canary.json
	$(MAKE) security-artifact-scan

security-artifact-scan:
	@test -n "$(SECURITY_ARTIFACT_INPUTS)" || (echo "SECURITY_ARTIFACT_INPUTS is required" >&2; exit 2)
	@test -n "$(SECURITY_ARTIFACT_CANARIES)" || (echo "SECURITY_ARTIFACT_CANARIES is required" >&2; exit 2)
	mkdir -p .tmp/security
	uv run --frozen python -m scripts.security_artifact_scan \
		$(foreach artifact,$(SECURITY_ARTIFACT_INPUTS),--input "$(artifact)") \
		$(foreach canary,$(SECURITY_ARTIFACT_CANARIES),--canary "$(canary)") \
		--output .tmp/security/security-artifact-scan.json

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
		DJANGO_SETTINGS_MODULE=website.settings.test uv run --frozen python manage.py test --noinput \
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
		DJANGO_SETTINGS_MODULE=website.settings.test uv run --frozen python manage.py test --parallel --noinput

test-ci:
	uv run --frozen pytest ci/tests tests_ci -q

verification-plan:
	mkdir -p "$(VERIFY_OUTPUT_DIR)" "$(VERIFY_EVIDENCE_DIR)"
	@base="$$(git rev-parse "$(VERIFY_BASE_SHA)")"; \
		head="$$(git rev-parse "$(VERIFY_HEAD_SHA)")"; \
		uv run --frozen python -m ci.classifier select \
			--repository . --event push --base "$$base" --after "$$head" \
			--github-sha "$$head" --release-sha "$$head" \
			--output "$(VERIFY_OUTPUT_DIR)/ci-selection.json"; \
		uv run --frozen python -m ci.verification plan \
			--repository . --base "$$base" --head "$$head" \
			--selection "$(VERIFY_OUTPUT_DIR)/ci-selection.json" \
			--evidence-directory "$(VERIFY_EVIDENCE_DIR)" --consumer "$(VERIFY_CONSUMER)" \
			--include-worktree \
			--output "$(VERIFY_PLAN)"

verification-run:
	uv run --frozen python -m ci.verification validate-plan --plan "$(VERIFY_PLAN)"
	uv run --frozen python -m ci.runner \
			--plan "$(VERIFY_PLAN)" --repository . \
			--output-directory "$(VERIFY_EVIDENCE_DIR)" \
			--issue "$(VERIFY_ISSUE)" --worktree "$(VERIFY_WORKTREE)" \
			--producer-role "$(VERIFY_PRODUCER_ROLE)"
	$(MAKE) verification-report-check

verification-quality: terminology-check database-portability-check security-check lint format-check typecheck \
	migrations-check django-check deployment-check test-ci

verification-container:
	uv run --frozen python -m ci.container_check --repository . \
		--output "$(VERIFY_CONTAINER_OUTPUT)"

verification-content-invariants:
	uv run --frozen python -m ci.content_invariants --repository . \
		--plan "$(VERIFY_PLAN)" --output "$(VERIFY_INVARIANT)"

verification-full: verification-quality
	DTC_TEST_RUN_ID="$${DTC_TEST_RUN_ID:-verification-full-migrate-$${PPID}}" \
		DJANGO_SETTINGS_MODULE=website.settings.test \
		uv run --frozen python manage.py migrate --noinput
	$(MAKE) test-factories
	$(MAKE) test-migrations
	$(MAKE) test
	$(MAKE) test-playwright
	$(MAKE) verification-container

verification-evidence-check:
	uv run --frozen python -m ci.verification validate-plan --plan "$(VERIFY_PLAN)"
	uv run --frozen python -m ci.verification validate-evidence-directory \
			--directory "$(VERIFY_EVIDENCE_DIR)" --plan "$(VERIFY_PLAN)" \
			--consumer "$(VERIFY_CONSUMER)"

verification-report-check: verification-evidence-check
	@pending=""; if test "$(VERIFY_PHASE)" = engineer; then pending="--allow-pending"; fi; \
		uv run --frozen python -m ci.verification report \
			--plan "$(VERIFY_PLAN)" --result-directory "$(VERIFY_EVIDENCE_DIR)" \
			--phase "$(VERIFY_PHASE)" --output "$(VERIFY_REPORT)"; \
		uv run --frozen python -m ci.verification validate-report \
			--plan "$(VERIFY_PLAN)" --report "$(VERIFY_REPORT)" \
			--evidence-directory "$(VERIFY_EVIDENCE_DIR)" $$pending

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
		DJANGO_SETTINGS_MODULE=website.settings.test uv run --frozen python manage.py test --noinput \
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
	uv run python scripts/build_legacy_manifest.py approved-expectations --check

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
