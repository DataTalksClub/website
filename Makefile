.PHONY: setup lock-check lint format format-check typecheck migrations-check django-check deployment-check \
	test-core test test-django-full test-ci test-ci-focused \
	test-content test-factories test-migrations test-playwright-core test-playwright test-browser \
	test-accessibility test-playwright-smoke test-playwright-quarantined \
	test-course-platform-sync course-platform-source-checkout course-platform-sync-dry-run course-platform-sync \
	content-update-check \
	security-check security-artifact-scan \
	test-remote-readonly test-remote-mutation test-live-email test-live-provider test-all migrate run worker \
	production-prep-local content-pull content-pull-plan content-checkouts content-sources \
	production-prep-course-registry production-prep-course-sources production-prep-dataset \
	production-prep-dataset-verify run-production-prep-dataset \
	import-legacy-zoomcamp import-events \
	terraform-seo-source-check check-openapi check-management-parity \
	database-portability-check verify-dtc-content review-data review-data-dry-run \
	review-data-cleanup run-review-data verification-plan verification-run verification-full \
	verification-quality verification-container \
	verification-evidence-check verification-report-check

VERIFY_BASE_SHA ?= HEAD
VERIFY_HEAD_SHA ?= HEAD
VERIFY_OUTPUT_DIR ?= .tmp/verification
VERIFY_PLAN ?= $(if $(VERIFY_PLAN_PATH),$(VERIFY_PLAN_PATH),$(VERIFY_OUTPUT_DIR)/verification-plan.json)
VERIFY_EVIDENCE_DIR ?= $(VERIFY_OUTPUT_DIR)/evidence
VERIFY_REPORT ?= $(if $(VERIFY_REPORT_PATH),$(VERIFY_REPORT_PATH),$(VERIFY_OUTPUT_DIR)/verification-report.json)
VERIFY_CONTAINER_OUTPUT ?= $(VERIFY_EVIDENCE_DIR)/container-check.json
# VERIFY_ISSUE is optional. Pass it to attribute local evidence to an issue;
# leave it unset and the evidence simply carries no issue.
VERIFY_WORKTREE ?= local
VERIFY_CONSUMER ?= engineer
VERIFY_PHASE ?= $(VERIFY_CONSUMER)
VERIFY_PRODUCER_ROLE ?= $(if $(filter tester,$(VERIFY_CONSUMER)),tester,engineer)
CMP_SOURCE_REF ?= main
CMP_SOURCE_REPOSITORY ?=
CMP_SOURCE_CHECKOUT ?=
CONTENT_UPDATE_FAMILY ?= all
CONTENT_UPDATE_OUTPUT_DIR ?= .tmp/content-update
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
	scripts/verify_course_platform_adoption.py \
	scripts/sync_course_platform.py \
	scripts/prepare_course_platform_source.py

# Entry points for imports that read real production data.  ``scripts/**`` is excluded
# from the default ruff and mypy roots, so this package opts back in explicitly.
PRODUCTION_IMPORT_PYTHON = \
	scripts/prod \
	courses/services/cmp_content_import.py \
	courses/services/cmp_learner_history_import.py

setup:
	uv sync --locked
	mkdir -p .tmp/screenshots
	uv run playwright install chromium

lock-check:
	uv lock --check

lint:
	uv run ruff check . $(ADOPTION_INTEGRATION_PYTHON) $(PRODUCTION_IMPORT_PYTHON)

format:
	uv run ruff format . $(ADOPTION_INTEGRATION_PYTHON) $(PRODUCTION_IMPORT_PYTHON)

format-check:
	uv run ruff format --check . $(ADOPTION_INTEGRATION_PYTHON) $(PRODUCTION_IMPORT_PYTHON)

typecheck:
	uv run mypy manage.py website core content content_sync events email_app studio jobs deploy ci \
		test_support conftest.py sitecustomize.py \
		review_import \
		management_auth management_api management_registry.py \
			scripts/build_local_review_db.py scripts/capture_screenshots.py \
		scripts/check_database_portability.py \
		scripts/render_course_platform_inventory.py \
	scripts/verify_course_platform_adoption.py \
	scripts/sync_course_platform.py \
	scripts/prepare_course_platform_source.py \
	$(PRODUCTION_IMPORT_PYTHON)

migrations-check:
	DJANGO_SETTINGS_MODULE=website.settings.test uv run python manage.py makemigrations --check --dry-run

django-check: check-openapi check-management-parity
	DJANGO_SETTINGS_MODULE=website.settings.test uv run python manage.py check

deployment-check:
	DTC_ENVIRONMENT=production VERSION=20260809-143205-aaaaaaa SOURCE_SHA=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa IMAGE_DIGEST=sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb DJANGO_SETTINGS_MODULE=website.settings.production DJANGO_SECRET_KEY="$$(uv run python -c 'import secrets; print(secrets.token_urlsafe(64))')" DATABASE_URL=postgresql://check:check@127.0.0.1:5432/check DJANGO_ALLOWED_HOSTS=example.invalid DJANGO_CSRF_TRUSTED_ORIGINS=https://example.invalid PUBLIC_MEDIA_STORE_BACKEND=s3 PUBLIC_MEDIA_S3_BUCKET=deployment-check-placeholder uv run python manage.py check --deploy --fail-level ERROR

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

test-course-platform-sync:
	uv run --frozen pytest scripts/tests/test_sync_course_platform.py -q

content-update-check:
	@set -eu; \
		families="$(CONTENT_UPDATE_FAMILY)"; \
		if test "$$families" = all; then families="courses podwiki faq docs"; fi; \
		for family in $$families; do \
			mkdir -p "$(CONTENT_UPDATE_OUTPUT_DIR)/$$family"; \
			DJANGO_SETTINGS_MODULE=website.settings.test uv run --frozen python -m ci.content_update \
				--family "$$family" --repository . \
				--output "$(CONTENT_UPDATE_OUTPUT_DIR)/$$family/report.json"; \
		done

course-platform-source-checkout:
	uv run python scripts/prepare_course_platform_source.py

course-platform-sync-dry-run:
	uv run python scripts/sync_course_platform.py \
		--source-ref "$(CMP_SOURCE_REF)" \
		$(if $(CMP_SOURCE_REPOSITORY),--source-repository "$(CMP_SOURCE_REPOSITORY)",) \
		$(if $(CMP_SOURCE_CHECKOUT),--source-checkout "$(CMP_SOURCE_CHECKOUT)",) \
		--dry-run

course-platform-sync:
	uv run python scripts/sync_course_platform.py \
		--source-ref "$(CMP_SOURCE_REF)" \
		$(if $(CMP_SOURCE_REPOSITORY),--source-repository "$(CMP_SOURCE_REPOSITORY)",) \
		$(if $(CMP_SOURCE_CHECKOUT),--source-checkout "$(CMP_SOURCE_CHECKOUT)",) \
		--apply

test: test-django-full

test-django-full:
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

# GNU make executes (not merely echoes) any recipe line containing $(MAKE) even under
# --dry-run, because recursive invocations are supposed to keep traversing the graph.
# The status-preserving block in verification-run contains $(MAKE), so a plain
# `make -n verification-run` would really start the runner against the plan instead of
# just printing the invocation. Detect the dry-run flag at parse time (the first word of
# MAKEFLAGS is the short-flag cluster, so only a genuine `n` matches) and prefix the
# block with a shell guard that exits successfully before any evidence is touched: the
# block is still fully echoed under -n, while a real run sees no guard at all.
ifeq (,$(findstring n,$(firstword -$(MAKEFLAGS))))
VERIFY_DRY_RUN_GUARD =
else
VERIFY_DRY_RUN_GUARD = exit 0;
endif

verification-run:
	uv run --frozen python -m ci.verification validate-plan --plan "$(VERIFY_PLAN)"
	@$(VERIFY_DRY_RUN_GUARD)runner_status=0; \
	uv run --frozen python -m ci.runner \
			--plan "$(VERIFY_PLAN)" --repository . \
			--output-directory "$(VERIFY_EVIDENCE_DIR)" \
			$(if $(VERIFY_ISSUE),--issue "$(VERIFY_ISSUE)",) --worktree "$(VERIFY_WORKTREE)" \
			--producer-role "$(VERIFY_PRODUCER_ROLE)" || runner_status=$$?; \
	report_status=0; \
	$(MAKE) verification-report-check || report_status=$$?; \
	if test "$$report_status" -ne 0; then exit "$$report_status"; fi; \
	exit "$$runner_status"

verification-quality: database-portability-check security-check lint format-check typecheck \
	migrations-check django-check deployment-check test-ci

verification-container:
	uv run --frozen python -m ci.container_check --repository . \
		--output "$(VERIFY_CONTAINER_OUTPUT)"

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

test-factories:
	DTC_TEST_RUN_ID="$${DTC_TEST_RUN_ID:-make-$${PPID}}" \
		uv run --frozen pytest test_support/tests/test_factories.py \
		test_support/tests/test_runtime.py test_support/tests/test_safety.py \
		test_support/tests/test_marker_registry.py -q

test-migrations:
	DTC_TEST_RUN_ID="$${DTC_TEST_RUN_ID:-make-$${PPID}}" \
		DJANGO_SETTINGS_MODULE=website.settings.test uv run --frozen python manage.py test --noinput \
		test_support.tests.test_migrations \
		content.tests.test_editorial_route_migration_contract

test-playwright-core:
	DTC_TEST_RUN_ID="$${DTC_TEST_RUN_ID:-make-$${PPID}}" \
		DJANGO_SETTINGS_MODULE=website.settings.test DJANGO_ALLOW_ASYNC_UNSAFE=true \
		uv run --frozen pytest playwright_tests \
		-p ci.playwright_flake_policy \
		-o faulthandler_timeout=120 \
		-m 'core and not quarantine and not remote_readonly and not remote_mutation and not live_email and not live_provider' -v

test-playwright-smoke:
	DTC_TEST_RUN_ID="$${DTC_TEST_RUN_ID:-make-$${PPID}}" \
		DJANGO_SETTINGS_MODULE=website.settings.test DJANGO_ALLOW_ASYNC_UNSAFE=true \
		timeout --foreground --signal=TERM --kill-after=30s 600s uv run --frozen pytest playwright_tests \
		-p ci.playwright_flake_policy \
		-o faulthandler_timeout=120 \
		-m 'smoke and not quarantine and not remote_readonly and not remote_mutation and not live_email and not live_provider' -v

test-playwright:
	DTC_TEST_RUN_ID="$${DTC_TEST_RUN_ID:-make-$${PPID}}" \
		DJANGO_SETTINGS_MODULE=website.settings.test DJANGO_ALLOW_ASYNC_UNSAFE=true \
		uv run --frozen pytest playwright_tests \
		-p ci.playwright_flake_policy \
		-o faulthandler_timeout=120 \
		-m '(smoke or core or full) and not quarantine and not remote_readonly and not remote_mutation and not live_email and not live_provider' -v

test-playwright-quarantined:
	set +e; DTC_TEST_RUN_ID="$${DTC_TEST_RUN_ID:-make-$${PPID}}" \
		DJANGO_SETTINGS_MODULE=website.settings.test DJANGO_ALLOW_ASYNC_UNSAFE=true \
		uv run --frozen pytest playwright_tests -p ci.playwright_flake_policy \
		-o faulthandler_timeout=120 \
		-m 'quarantine and not remote_readonly and not remote_mutation and not live_email and not live_provider' -v; \
	status=$$?; if [ "$$status" -eq 5 ]; then exit 0; fi; exit "$$status"
test-accessibility:
	DTC_TEST_RUN_ID="$${DTC_TEST_RUN_ID:-make-$${PPID}}" \
		DJANGO_SETTINGS_MODULE=website.settings.test DJANGO_ALLOW_ASYNC_UNSAFE=true \
		uv run --frozen pytest playwright_tests/test_accessibility.py \
		-o faulthandler_timeout=120 \
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
test-all: lock-check database-portability-check lint format-check typecheck \
	migrations-check django-check deployment-check test-factories test-migrations test \
	test-playwright

migrate:
	uv run python manage.py migrate

# Content reaches this site two ways, sharing one implementation
# (content_sync/course_repository_ingest.py). CI/CD pushes: a course repository
# posts a signed push event and the webhook enqueues a durable job that
# downloads the commit archive. These targets are the other entry point --
# the developer pull, which reads checkouts already on disk and makes no
# network call. Which repositories exist is registered ContentSource data, not
# a list written here.
CONTENT_CHECKOUT_ROOT ?= .tmp/course-checkouts
# Host base only. The owner comes from the registered source, like everything else
# about which repositories exist, so a source registered under a different owner is
# cloned from that owner rather than silently from DataTalksClub.
CONTENT_GIT_HOST ?= https://github.com
# These targets, like every scripts/prod entry point, take the database
# explicitly rather than reading it from the ambient environment. Overridden by
# the production-prep-* targets below, which point it at the dataset database.
CONTENT_DATABASE ?= .tmp/local.sqlite3

# Register the pinned course-repository sources. Which repositories exist is
# registered ContentSource data; this is only how a fresh database gets its rows.
content-sources:
	uv run --frozen python scripts/prod/sync_course_repository_sources.py \
		--database "$(CONTENT_DATABASE)"

# Print the registered sources and the checkout each would be read from.
content-pull-plan:
	@uv run --frozen python scripts/prod/sync_course_repositories.py \
		--database "$(CONTENT_DATABASE)" \
		--checkout-plan --from-disk "$(CONTENT_CHECKOUT_ROOT)"

# Clone or refresh a checkout per registered source. This is the only step that
# touches the network, and it is deliberately separate from the pull itself.
content-checkouts:
	@set -eu; \
	mkdir -p "$(CONTENT_CHECKOUT_ROOT)"; \
	plan="$$(uv run --frozen python scripts/prod/sync_course_repositories.py \
		--database "$(CONTENT_DATABASE)" \
		--checkout-plan --from-disk "$(CONTENT_CHECKOUT_ROOT)")"; \
	printf '%s\n' "$$plan" \
	| while IFS="$$(printf '\t')" read -r stable repository branch checkout; do \
		if test -d "$$checkout/.git"; then \
			git -C "$$checkout" fetch --quiet origin "$$branch"; \
			git -C "$$checkout" checkout --quiet "$$branch"; \
			git -C "$$checkout" reset --hard --quiet FETCH_HEAD; \
			git -C "$$checkout" clean --quiet -fdx; \
		else \
			git clone --quiet --branch "$$branch" \
				"$(CONTENT_GIT_HOST)/$$repository" "$$checkout"; \
		fi; \
		echo "$$stable $$(git -C "$$checkout" rev-parse HEAD)"; \
	done

# Ingest every registered source from its local checkout. Offline.
content-pull:
	uv run --frozen python scripts/prod/sync_course_repositories.py \
		--database "$(CONTENT_DATABASE)" \
		--from-disk "$(CONTENT_CHECKOUT_ROOT)" $(CONTENT_PULL_ARGS)

production-prep-local:
	@test -n "$(PRODUCTION_PREP_DATABASE)" || (echo "PRODUCTION_PREP_DATABASE is required" >&2; exit 2)
	@test -n "$(PRODUCTION_PREP_COURSE_SOURCE_DIR)" || (echo "PRODUCTION_PREP_COURSE_SOURCE_DIR is required" >&2; exit 2)
	uv run --frozen python scripts/prepare_local_data.py \
		--database "$(PRODUCTION_PREP_DATABASE)" \
		--course-checkout-root "$(PRODUCTION_PREP_COURSE_SOURCE_DIR)" \
		$(if $(PRODUCTION_PREP_CURRENT_REGISTRATION_INPUT),--current-registration-input "$(PRODUCTION_PREP_CURRENT_REGISTRATION_INPUT)",) \
		$(if $(PRODUCTION_PREP_CMP_SOURCE),--cmp-source-db "$(PRODUCTION_PREP_CMP_SOURCE)",) \
		$(if $(PRODUCTION_PREP_FRESH),--fresh,)

# One command that rebuilds the whole local dataset from its sources. See
# _docs/runbooks/local-course-modules-preparation.md for prerequisites.
PRODUCTION_PREP_DATASET_ROOT ?= .tmp/production-prep-dataset
PRODUCTION_PREP_DATASET_DATABASE ?= $(PRODUCTION_PREP_DATASET_ROOT)/dataset.sqlite3
PRODUCTION_PREP_COURSE_SOURCE_DIR ?= $(PRODUCTION_PREP_DATASET_ROOT)/course-sources
PRODUCTION_PREP_DATASET_PORT ?= 8001
# Protected CMP SQLite snapshot. The sanitizing importer copies it and never
# writes learner rows. Override when the snapshot lives somewhere else.
PRODUCTION_PREP_CMP_SOURCE ?= $(HOME)/git/course-management-platform/db/db.sqlite3
# Set empty to skip activating the reviewed current-event registration aggregates.
PRODUCTION_PREP_DATASET_REGISTRATION_INPUT ?= \
	_docs/migration-data/local-current-registration-input.json
PRODUCTION_PREP_DATASET_ENV = DTC_ENVIRONMENT=local \
	DJANGO_SETTINGS_MODULE=website.settings.local \
	DTC_SQLITE_PATH=$(PRODUCTION_PREP_DATASET_DATABASE)

# Stage 1. Create the dataset database and register which course repositories
# exist. This has to come first because that is the only place the answer lives:
# registered ContentSource rows, not a list in this file.
production-prep-course-registry:
	@test ! -e "$(PRODUCTION_PREP_DATASET_DATABASE)" || \
		(echo "$(PRODUCTION_PREP_DATASET_DATABASE) already exists; remove it to rebuild" >&2; exit 2)
	@mkdir -p "$(PRODUCTION_PREP_DATASET_ROOT)"
	$(PRODUCTION_PREP_DATASET_ENV) uv run --frozen python manage.py migrate --no-input
	$(MAKE) content-sources CONTENT_DATABASE="$(PRODUCTION_PREP_DATASET_DATABASE)"

# Stage 2. The only step that touches the network. It clones or refreshes one
# checkout per registered source, exactly as `make content-checkouts` does for a
# developer, because it is that target.
production-prep-course-sources: production-prep-course-registry
	$(MAKE) content-checkouts \
		CONTENT_CHECKOUT_ROOT="$(PRODUCTION_PREP_COURSE_SOURCE_DIR)" \
		CONTENT_DATABASE="$(PRODUCTION_PREP_DATASET_DATABASE)"

# Stage 3. Build the dataset offline from those checkouts.
production-prep-dataset:
	rm -f "$(PRODUCTION_PREP_DATASET_DATABASE)" \
		"$(PRODUCTION_PREP_DATASET_DATABASE)-shm" \
		"$(PRODUCTION_PREP_DATASET_DATABASE)-wal"
	$(MAKE) production-prep-course-sources
	$(MAKE) production-prep-local \
		PRODUCTION_PREP_DATABASE="$(PRODUCTION_PREP_DATASET_DATABASE)" \
		PRODUCTION_PREP_COURSE_SOURCE_DIR="$(PRODUCTION_PREP_COURSE_SOURCE_DIR)" \
		PRODUCTION_PREP_CURRENT_REGISTRATION_INPUT="$(PRODUCTION_PREP_DATASET_REGISTRATION_INPUT)"
	$(MAKE) production-prep-dataset-verify

production-prep-dataset-verify:
	uv run --frozen python scripts/verify_local_dataset.py \
		--database "$(PRODUCTION_PREP_DATASET_DATABASE)"

# Production imports. Every entry point lives in scripts/prod/; `import_*` is
# frozen history read once at migration, `sync_*` is an upstream we re-run
# against. See scripts/prod/__init__.py.
IMPORT_DATABASE ?= $(PRODUCTION_PREP_DATASET_DATABASE)

# Pre-2024 Zoomcamp scoring and certificate history. Frozen; one-time. This is
# the only importer that can populate an empty database, so it runs first.
LEGACY_ZOOMCAMP_SOURCE ?= $(HOME)/git/zoomcamp-scoring
import-legacy-zoomcamp:
	uv run --frozen python scripts/prod/import_legacy_zoomcamp.py \
		--database "$(IMPORT_DATABASE)" \
		--source-repo "$(LEGACY_ZOOMCAMP_SOURCE)" \
		$(IMPORT_LEGACY_ZOOMCAMP_ARGS)

# Event identities plus the Luma and Eventbrite registration aggregates. Both
# frozen; one-time. Aggregate counts only -- no attendee row is ever read into
# the database. Set empty to leave every mapping review-required.
IMPORT_EVENTS_REGISTRATION_INPUT ?= \
	_docs/migration-data/local-current-registration-input.json
import-events:
	uv run --frozen python scripts/prod/import_events.py \
		--database "$(IMPORT_DATABASE)" \
		$(if $(IMPORT_EVENTS_REGISTRATION_INPUT),--current-registration-input "$(IMPORT_EVENTS_REGISTRATION_INPUT)",) \
		$(IMPORT_EVENTS_ARGS)

run-production-prep-dataset:
	DTC_ENVIRONMENT=local \
		DTC_SQLITE_PATH=$(PRODUCTION_PREP_DATASET_DATABASE) \
		DJANGO_SETTINGS_MODULE=website.settings.local \
		uv run python manage.py runserver 0.0.0.0:$(PRODUCTION_PREP_DATASET_PORT)

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
