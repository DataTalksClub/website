.PHONY: setup lock-check core-source-check core-link core-unlink lint format format-check typecheck migrations-check django-check deployment-check \
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
	import-public-content import-faq import-docs import-sponsors import-testimonials \
	import-editorial-content production-prep-bootstrap \
	import-cmp-content import-cmp-learners import-cmp-learners-status \
	import-cmp-learner-history import-cmp-learner-history-status \
	import-cmp-learner-data import-account-reconciliation \
	import-account-reconciliation-rollback-check \
	terraform-seo-source-check check-openapi check-management-parity \
	database-portability-check verify-dtc-content review-data review-data-dry-run \
	review-data-cleanup run-review-data verification-plan verification-run verification-full \
	verification-quality verification-container \
	verification-evidence-check verification-report-check

# The checkout does not carry the public projection media objects, so every CI
# job that runs tests -- Django, playwright and screenshots alike -- reads them
# from the deterministic offline fixture store. Local runs default to the same
# store, which is what makes a fresh clone green. A tester who has hydrated the
# real artwork with scripts/prod/sync_public_media_hydrate.py runs
# `PUBLIC_MEDIA_STORE_BACKEND=local make ...` and gets the real bytes.
TEST_MEDIA_STORE = PUBLIC_MEDIA_STORE_BACKEND="$${PUBLIC_MEDIA_STORE_BACKEND:-memory}"

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

# Files re-included in the quality gates inside the trees that pyproject.toml
# excludes.  Two kinds live here, and ci/tests/test_adoption_gate_coverage.py
# holds the boundary: entries that predate this list (the reviewed adopted
# baseline) and files authored or substantively changed after audit BE-16,
# which must be added here rather than riding their directory's historical
# exclusion.  Everything in this list is linted, formatted, and typechecked;
# the mypy ``ignore_errors = false`` override block in pyproject.toml names
# the modules this audit opted in, so they are checked strictly.
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
	scripts/prepare_course_platform_source.py \
	accounts/backends.py \
	accounts/identity_resolution.py \
	accounts/middleware.py \
	accounts/studio_authorization.py \
	accounts/auth.py \
	accounts/views/impersonation.py \
	accounts/tests/test_identity_quarantine_revocation.py \
	accounts/tests/test_email_authentication_lookup.py \
	accounts/tests/test_cmp_learner_import_run_binding.py \
	api/utils.py \
	api/crud.py \
	api/tests/test_json_body_shapes.py \
	courses/votes.py \
	courses/services/learner_duplicate_preflight.py \
	courses/management/commands/learner_duplicate_preflight.py \
	courses/tests/test_enrollment_mutation_safety.py \
	courses/tests/test_project_vote_budget.py \
	courses/tests/test_learner_duplicate_preflight.py \
	courses/tests/test_time_spent_parsing_conventions.py \
	courses/tests/test_project_submission_error_containment.py \
	courses/tests/test_project_eval_review_binding.py \
	courses/tests/test_cmp_learner_history_run_binding.py \
	scripts/tests/test_sync_course_repositories_cli.py \
	scripts/tests/test_legacy_zoomcamp_username_allocation.py \
	scripts/tests/test_scoring_import_atomicity.py \
	scripts/tests/test_certificate_matching.py \
	scripts/tests/test_reviewed_release_import.py \
	scripts/checkout_refresh.py \
	scripts/tests/test_checkout_refresh.py \
	scripts/rebuild_gate.py \
	scripts/tests/test_rebuild_gate.py \
	scripts/verify_local_dataset.py \
	scripts/tests/test_verify_local_dataset.py \
	course_management/mail_preferences.py \
	course_management/package_mail.py \
	courses/tests/test_package_mail_flows.py \
	accounts/tests/test_username_allocation.py

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

core-source-check:
	uv run python scripts/check_community_base_source.py

core-link:
	uv run python scripts/community_base_link.py link

core-unlink:
	uv run python scripts/community_base_link.py unlink

lint:
	uv run ruff check . $(ADOPTION_INTEGRATION_PYTHON) $(PRODUCTION_IMPORT_PYTHON)

format:
	uv run ruff format . $(ADOPTION_INTEGRATION_PYTHON) $(PRODUCTION_IMPORT_PYTHON)

format-check:
	uv run ruff format --check . $(ADOPTION_INTEGRATION_PYTHON) $(PRODUCTION_IMPORT_PYTHON)

typecheck:
	uv run mypy manage.py website core content content_sync events email_app studio deploy ci \
		test_support conftest.py sitecustomize.py \
		review_import \
		management_auth management_api management_registry.py \
		$(ADOPTION_INTEGRATION_PYTHON) \
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
	DTC_TEST_RUN_ID="$${DTC_TEST_RUN_ID:-make-$${PPID}}" $(TEST_MEDIA_STORE) \
		DJANGO_SETTINGS_MODULE=website.settings.test uv run --frozen python manage.py test --noinput \
		accounts core studio api management_auth management_api --parallel

check-openapi:
	DJANGO_SETTINGS_MODULE=website.settings.test uv run python manage.py generate_admin_openapi --check

check-management-parity:
	DJANGO_SETTINGS_MODULE=website.settings.test uv run python manage.py check_management_parity

test-content:
	DTC_TEST_RUN_ID="$${DTC_TEST_RUN_ID:-make-$${PPID}}" $(TEST_MEDIA_STORE) \
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
	DTC_TEST_RUN_ID="$${DTC_TEST_RUN_ID:-make-$${PPID}}" $(TEST_MEDIA_STORE) \
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
	DTC_TEST_RUN_ID="$${DTC_TEST_RUN_ID:-make-$${PPID}}" $(TEST_MEDIA_STORE) \
		DJANGO_SETTINGS_MODULE=website.settings.test uv run --frozen python -m ci.focused_tests \
		--selection "$$CI_SELECTION_PATH"

test-factories:
	DTC_TEST_RUN_ID="$${DTC_TEST_RUN_ID:-make-$${PPID}}" $(TEST_MEDIA_STORE) \
		uv run --frozen pytest test_support/tests/test_factories.py \
		test_support/tests/test_runtime.py test_support/tests/test_safety.py \
		test_support/tests/test_marker_registry.py -q

test-migrations:
	DTC_TEST_RUN_ID="$${DTC_TEST_RUN_ID:-make-$${PPID}}" $(TEST_MEDIA_STORE) \
		DJANGO_SETTINGS_MODULE=website.settings.test uv run --frozen python manage.py test --noinput \
		test_support.tests.test_migrations \
		content.tests.test_editorial_route_migration_contract

# A browser tier needs the same store for a harder reason than the Django tiers:
# a page that references a recorded object gets a 502 from the unhydrated local
# store, and the browser harness reads that as a console error and fails the
# test.  There is no "real artwork" to prefer on a checkout that has not been
# hydrated -- only a fail-closed 502 -- so the local tiers select what the CI
# playwright and screenshots jobs already select.
PLAYWRIGHT_ENV = DTC_TEST_RUN_ID="$${DTC_TEST_RUN_ID:-make-$${PPID}}" \
	DJANGO_SETTINGS_MODULE=website.settings.test DJANGO_ALLOW_ASYNC_UNSAFE=true \
	$(TEST_MEDIA_STORE)

test-playwright-core:
	$(PLAYWRIGHT_ENV) \
		uv run --frozen pytest playwright_tests \
		-p ci.playwright_flake_policy \
		-o faulthandler_timeout=120 \
		-m 'core and not quarantine and not remote_readonly and not remote_mutation and not live_email and not live_provider' -v

test-playwright-smoke:
	$(PLAYWRIGHT_ENV) \
		timeout --foreground --signal=TERM --kill-after=30s 600s uv run --frozen pytest playwright_tests \
		-p ci.playwright_flake_policy \
		-o faulthandler_timeout=120 \
		-m 'smoke and not quarantine and not remote_readonly and not remote_mutation and not live_email and not live_provider' -v

test-playwright:
	$(PLAYWRIGHT_ENV) \
		uv run --frozen pytest playwright_tests \
		-p ci.playwright_flake_policy \
		-o faulthandler_timeout=120 \
		-m '(smoke or core or full) and not quarantine and not remote_readonly and not remote_mutation and not live_email and not live_provider' -v

test-playwright-quarantined:
	set +e; $(PLAYWRIGHT_ENV) \
		uv run --frozen pytest playwright_tests -p ci.playwright_flake_policy \
		-o faulthandler_timeout=120 \
		-m 'quarantine and not remote_readonly and not remote_mutation and not live_email and not live_provider' -v; \
	status=$$?; if [ "$$status" -eq 5 ]; then exit 0; fi; exit "$$status"
test-accessibility:
	$(PLAYWRIGHT_ENV) \
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
# The refresh boundary is scripts/checkout_refresh.py: an existing checkout is
# reset only when this tooling created it, its origin is the registered
# repository, and it is completely clean -- anything else is refused before any
# Git mutation (audit REL-10).
content-checkouts:
	@set -eu; \
	mkdir -p "$(CONTENT_CHECKOUT_ROOT)"; \
	plan="$$(uv run --frozen python scripts/prod/sync_course_repositories.py \
		--database "$(CONTENT_DATABASE)" \
		--checkout-plan --from-disk "$(CONTENT_CHECKOUT_ROOT)")"; \
	printf '%s\n' "$$plan" \
	| uv run --frozen python scripts/checkout_refresh.py --host "$(CONTENT_GIT_HOST)"

# Ingest every registered source from its local checkout. Offline.
content-pull:
	uv run --frozen python scripts/prod/sync_course_repositories.py \
		--database "$(CONTENT_DATABASE)" \
		--from-disk "$(CONTENT_CHECKOUT_ROOT)" $(CONTENT_PULL_ARGS)

# --------------------------------------------------------------------------
# Editorial content drift check (issue #323)
#
# `make content-checkouts` above does NOT cover DataTalksClub/content: it
# selects only sources whose adapter_type is course_repository_v1, and the
# editorial source is not one. So the editorial checkout gets its own networked
# target, and the check that reads it gets its own offline one. Which repository
# either of them means stays a database question -- the registered enabled
# ContentSource for DataTalksClub/content -- never a name written here.
# --------------------------------------------------------------------------
.PHONY: content-checkout content-drift

CONTENT_CHECKOUT ?= .tmp/content-checkout
# The script refuses a relative checkout -- a report has to say which tree it read
# without depending on where it was run -- so the path is absolutised here rather
# than every operator having to type an absolute one.
CONTENT_CHECKOUT_ABS = $(abspath $(CONTENT_CHECKOUT))

# Clone or refresh the editorial checkout, and print the HEAD it landed on. The
# only target in this pair that touches the network, deliberately separate from
# the check so a report is never quietly one round trip old.  The refresh
# boundary is scripts/checkout_refresh.py, exactly as for content-checkouts.
content-checkout:
	@set -eu; \
	mkdir -p "$$(dirname "$(CONTENT_CHECKOUT_ABS)")"; \
	plan="$$(uv run --frozen python scripts/prod/sync_content_verify.py \
		--database "$(CONTENT_DATABASE)" \
		--checkout "$(CONTENT_CHECKOUT_ABS)" \
		--checkout-plan)"; \
	printf '%s\n' "$$plan" \
	| uv run --frozen python scripts/checkout_refresh.py --host "$(CONTENT_GIT_HOST)"

# Report drift between what the database serves and that checkout. Offline,
# read-only, and never invokes content-checkout: exit 1 means "we are behind",
# exit 2 means "I could not look". Make collapses any failed recipe to its own
# exit 2, so the script's code is echoed here; anything that has to tell the two
# apart -- a scheduler, a future CI job -- runs the script directly.
content-drift:
	@uv run --frozen python scripts/prod/sync_content_verify.py \
		--database "$(CONTENT_DATABASE)" \
		--checkout "$(CONTENT_CHECKOUT_ABS)" \
		$(if $(CONTENT_DRIFT_REVISION),--revision "$(CONTENT_DRIFT_REVISION)",) \
	|| { status=$$?; \
		echo "content-drift: sync_content_verify exited $$status (1 drift, 2 refusal)" >&2; \
		exit $$status; }

production-prep-local:
	@test -n "$(PRODUCTION_PREP_DATABASE)" || (echo "PRODUCTION_PREP_DATABASE is required" >&2; exit 2)
	@test -n "$(PRODUCTION_PREP_COURSE_SOURCE_DIR)" || (echo "PRODUCTION_PREP_COURSE_SOURCE_DIR is required" >&2; exit 2)
	uv run --frozen python scripts/prepare_local_data.py \
		--database "$(PRODUCTION_PREP_DATABASE)" \
		--course-checkout-root "$(PRODUCTION_PREP_COURSE_SOURCE_DIR)" \
		$(if $(PRODUCTION_PREP_CURRENT_REGISTRATION_INPUT),--current-registration-input "$(PRODUCTION_PREP_CURRENT_REGISTRATION_INPUT)",) \
		$(if $(PRODUCTION_PREP_CMP_SOURCE),--cmp-source-db "$(PRODUCTION_PREP_CMP_SOURCE)",) \
		$(if $(PRODUCTION_PREP_FRESH),--fresh,) $(PRODUCTION_PREP_LOCAL_ARGS)

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
	@uv run --frozen python scripts/rebuild_gate.py \
		"$(PRODUCTION_PREP_DATASET_DATABASE)"
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

# Step 4 of the documented bootstrap order: the reviewed one-time editorial
# inputs under temporary/content/. All five bootstrap an empty database, none
# depends on another, and every one is replay-safe -- the three catalogue
# importers write and activate a fresh release, the other two key each row on
# its natural key and report `replayed`.
import-public-content:
	uv run --frozen python scripts/prod/import_public_content.py \
		--database "$(IMPORT_DATABASE)" $(IMPORT_PUBLIC_CONTENT_ARGS)

import-faq:
	uv run --frozen python scripts/prod/import_faq.py \
		--database "$(IMPORT_DATABASE)" $(IMPORT_FAQ_ARGS)

import-docs:
	uv run --frozen python scripts/prod/import_docs.py \
		--database "$(IMPORT_DATABASE)" $(IMPORT_DOCS_ARGS)

import-sponsors:
	uv run --frozen python scripts/prod/import_sponsors.py \
		--database "$(IMPORT_DATABASE)" $(IMPORT_SPONSORS_ARGS)

import-testimonials:
	uv run --frozen python scripts/prod/import_testimonials.py \
		--database "$(IMPORT_DATABASE)" $(IMPORT_TESTIMONIALS_ARGS)

# The whole of step 4, for a database built by something other than
# `scripts/prepare_local_data.py` (which runs the same five itself). Recipe
# lines rather than prerequisites, so `make -j` cannot interleave five writers
# on one SQLite file.
import-editorial-content:
	$(MAKE) import-public-content IMPORT_DATABASE="$(IMPORT_DATABASE)"
	$(MAKE) import-faq IMPORT_DATABASE="$(IMPORT_DATABASE)"
	$(MAKE) import-docs IMPORT_DATABASE="$(IMPORT_DATABASE)"
	$(MAKE) import-sponsors IMPORT_DATABASE="$(IMPORT_DATABASE)"
	$(MAKE) import-testimonials IMPORT_DATABASE="$(IMPORT_DATABASE)"

# CMP export imports. The CMP production export is the largest single step of
# the migration -- 991 content rows plus 510,519 learner rows measured -- and
# the learner half of it is 20,009 real people. Every target below refuses
# before any Python starts when the variable naming the export or the
# destination database is unset; a missing variable is never a skip, a warning
# or a default. See _docs/runbooks/production-data-migration.md §4 and
# _docs/runbooks/account-reconciliation.md.
#
# The CMP production export to read, passed to `--source` and read in place --
# never copied into the worktree. Deliberately no default: a new dump lands in
# the export directory daily and there is no `latest` symlink, so guessing one
# is how the wrong export gets imported.
CMP_EXPORT ?=
# Which database receives the learner accounts and their history. Deliberately
# no default, and deliberately not IMPORT_DATABASE's: that one is the dev
# dataset `make run-production-prep-dataset` serves on port 8001, and which
# database receives 20,000 real people is a decision typed once per run.
IMPORT_LEARNER_DATABASE ?=
# The accounts claims file `import_cmp_learners.py` writes and
# `import_cmp_learner_history.py` reads back. Set it once and both targets use
# it; leave it empty and each importer uses its own default path.
CMP_CLAIMS_FILE ?=
# Where the history importer records which target row it created for a given
# CMP source id, one file per table. Durable resumability state.
CMP_CLAIMS_DIR ?=
ACCOUNT_RECONCILIATION_MAPPING ?=
ACCOUNT_RECONCILIATION_OUTPUT ?= .tmp/account-reconciliation-dry-run.json

# The third leg of step 3's catalogue order, after `import-legacy-zoomcamp` and
# `make content-pull`. Content only -- no account, enrollment, submission or
# registration row -- so the ordinary IMPORT_DATABASE default is safe here.
# `production-prep-bootstrap` already runs it through `production-prep-local`;
# this target is for a database built some other way.
import-cmp-content:
	@test -n "$(CMP_EXPORT)" || (echo "CMP_EXPORT is required: point it at the CMP export to import. There is deliberately no default." >&2; exit 2)
	@test -f "$(CMP_EXPORT)" || (echo "CMP_EXPORT=$(CMP_EXPORT) is not an existing file" >&2; exit 2)
	uv run --frozen python scripts/prod/import_cmp_content.py \
		--database "$(IMPORT_DATABASE)" \
		--source "$(CMP_EXPORT)" \
		$(IMPORT_CMP_CONTENT_ARGS)

# The learner-account half of migration step 4: 20,009 accounts and their email
# addresses. Resumable -- a killed run is re-run with the same variables.
import-cmp-learners:
	@test -n "$(IMPORT_LEARNER_DATABASE)" || (echo "IMPORT_LEARNER_DATABASE is required: name the database that receives the learner accounts. There is deliberately no default." >&2; exit 2)
	@test -n "$(CMP_EXPORT)" || (echo "CMP_EXPORT is required: point it at the CMP export to import. There is deliberately no default." >&2; exit 2)
	@test -f "$(CMP_EXPORT)" || (echo "CMP_EXPORT=$(CMP_EXPORT) is not an existing file" >&2; exit 2)
	uv run --frozen python scripts/prod/import_cmp_learners.py \
		--database "$(IMPORT_LEARNER_DATABASE)" \
		--source "$(CMP_EXPORT)" \
		$(if $(CMP_CLAIMS_FILE),--claims-file "$(CMP_CLAIMS_FILE)",) \
		$(IMPORT_CMP_LEARNERS_ARGS)

# How far a resumable run got, without opening the export at all. No --source,
# and no CMP_EXPORT required: that is the point of --status.
import-cmp-learners-status:
	@test -n "$(IMPORT_LEARNER_DATABASE)" || (echo "IMPORT_LEARNER_DATABASE is required: name the database whose progress you are reading. There is deliberately no default." >&2; exit 2)
	uv run --frozen python scripts/prod/import_cmp_learners.py \
		--database "$(IMPORT_LEARNER_DATABASE)" \
		--status \
		$(if $(CMP_CLAIMS_FILE),--claims-file "$(CMP_CLAIMS_FILE)",)

# The second half of migration step 4: registrations, enrollments, submissions,
# answers, reviews, scores and Wrapped statistics. It reconciles -- against the
# cohorts `import_cmp_content` wrote, and against the accounts claims file
# `import-cmp-learners` left behind, which is why CMP_CLAIMS_FILE is threaded
# through to --user-claims-file rather than left for an operator to remember.
import-cmp-learner-history:
	@test -n "$(IMPORT_LEARNER_DATABASE)" || (echo "IMPORT_LEARNER_DATABASE is required: name the database that receives the learner history. There is deliberately no default." >&2; exit 2)
	@test -n "$(CMP_EXPORT)" || (echo "CMP_EXPORT is required: point it at the CMP export to import. There is deliberately no default." >&2; exit 2)
	@test -f "$(CMP_EXPORT)" || (echo "CMP_EXPORT=$(CMP_EXPORT) is not an existing file" >&2; exit 2)
	uv run --frozen python scripts/prod/import_cmp_learner_history.py \
		--database "$(IMPORT_LEARNER_DATABASE)" \
		--source "$(CMP_EXPORT)" \
		$(if $(CMP_CLAIMS_DIR),--claims-dir "$(CMP_CLAIMS_DIR)",) \
		$(if $(CMP_CLAIMS_FILE),--user-claims-file "$(CMP_CLAIMS_FILE)",) \
		$(IMPORT_CMP_LEARNER_HISTORY_ARGS)

import-cmp-learner-history-status:
	@test -n "$(IMPORT_LEARNER_DATABASE)" || (echo "IMPORT_LEARNER_DATABASE is required: name the database whose progress you are reading. There is deliberately no default." >&2; exit 2)
	uv run --frozen python scripts/prod/import_cmp_learner_history.py \
		--database "$(IMPORT_LEARNER_DATABASE)" \
		--status \
		$(if $(CMP_CLAIMS_DIR),--claims-dir "$(CMP_CLAIMS_DIR)",)

# The whole of migration step 4, in the declared CMP_LEARNER_ORDER
# (scripts/prod/__init__.py). Recipe lines rather than prerequisites, and not
# only for the reason `import-editorial-content` records: the order here is
# semantic. The history importer resolves every foreign key against what the
# accounts importer wrote, so the reverse order is a silent partial import
# rather than an error.
import-cmp-learner-data:
	@test -n "$(IMPORT_LEARNER_DATABASE)" || (echo "IMPORT_LEARNER_DATABASE is required: name the database that receives the learner data. There is deliberately no default." >&2; exit 2)
	@test -n "$(CMP_EXPORT)" || (echo "CMP_EXPORT is required: point it at the CMP export to import. There is deliberately no default." >&2; exit 2)
	@test -f "$(CMP_EXPORT)" || (echo "CMP_EXPORT=$(CMP_EXPORT) is not an existing file" >&2; exit 2)
	$(MAKE) import-cmp-learners \
		IMPORT_LEARNER_DATABASE="$(IMPORT_LEARNER_DATABASE)" \
		CMP_EXPORT="$(CMP_EXPORT)" \
		CMP_CLAIMS_FILE="$(CMP_CLAIMS_FILE)" \
		IMPORT_CMP_LEARNERS_ARGS="$(IMPORT_CMP_LEARNERS_ARGS)"
	$(MAKE) import-cmp-learner-history \
		IMPORT_LEARNER_DATABASE="$(IMPORT_LEARNER_DATABASE)" \
		CMP_EXPORT="$(CMP_EXPORT)" \
		CMP_CLAIMS_FILE="$(CMP_CLAIMS_FILE)" \
		CMP_CLAIMS_DIR="$(CMP_CLAIMS_DIR)" \
		IMPORT_CMP_LEARNER_HISTORY_ARGS="$(IMPORT_CMP_LEARNER_HISTORY_ARGS)"

# Account reconciliation, dry run only -- the script's own default mode. The
# report goes to a file rather than stdout because the runbook's flow is to run
# it twice and `cmp` the two reports, and because it lists every source user id.
#
# There is deliberately no `--apply` target, and there must never be one.
# Applying a reviewed merge mapping is the one step in the whole migration with
# no rollback (_docs/runbooks/account-reconciliation.md §4), so it stays a
# consciously typed command against a human-reviewed mapping document.
# `scripts/tests/test_prod_make_targets.py` holds this file to that.
import-account-reconciliation:
	@test -n "$(IMPORT_LEARNER_DATABASE)" || (echo "IMPORT_LEARNER_DATABASE is required: name the database holding the accounts to reconcile. There is deliberately no default." >&2; exit 2)
	@test -n "$(SNAPSHOT_ID)" || (echo "SNAPSHOT_ID is required" >&2; exit 2)
	uv run --frozen python scripts/prod/import_account_reconciliation.py \
		--database "$(IMPORT_LEARNER_DATABASE)" \
		--snapshot-id "$(SNAPSHOT_ID)" \
		--output "$(ACCOUNT_RECONCILIATION_OUTPUT)"

# Proves the evidence needed to reverse an applied merge is still intact. It
# reverses nothing itself.
import-account-reconciliation-rollback-check:
	@test -n "$(IMPORT_LEARNER_DATABASE)" || (echo "IMPORT_LEARNER_DATABASE is required: name the database holding the reconciled accounts. There is deliberately no default." >&2; exit 2)
	@test -n "$(SNAPSHOT_ID)" || (echo "SNAPSHOT_ID is required" >&2; exit 2)
	@test -n "$(ACCOUNT_RECONCILIATION_MAPPING)" || (echo "ACCOUNT_RECONCILIATION_MAPPING is required: point it at the reviewed mapping document" >&2; exit 2)
	uv run --frozen python scripts/prod/import_account_reconciliation.py \
		--database "$(IMPORT_LEARNER_DATABASE)" \
		--snapshot-id "$(SNAPSHOT_ID)" \
		--rollback-check \
		--mapping "$(ACCOUNT_RECONCILIATION_MAPPING)"

# One command for the whole documented bootstrap order
# (_docs/runbooks/data-ingest.md §11), from an empty directory to a verified
# database. `production-prep-dataset` above is the same run without §11 step 3's
# first leg, the pre-2024 Zoomcamp history, which needs a separate checkout and
# takes about 35 minutes.
#
# Steps 1-5 only. Step 6 -- `import_event_registrants` and the Mailchimp
# importers -- reads attendee-level personal data and provider credentials, so
# it stays a deliberate, separately invoked run rather than something a
# rebuild does on its way past.
#
# The CMP learner importers (`import-cmp-learners`, `import-cmp-learner-history`
# and the `import-cmp-learner-data` pair) and the reconciliation entry point
# (`import-account-reconciliation`) are out for the same reason and two more. A
# local dataset rebuild is a routine developer action and must never pull 20,009
# real accounts and 472,690 learner rows into a dev SQLite file on its way past;
# the export is not frozen either -- a new dump lands daily and there is no
# `latest` symlink -- so an unattended rebuild could not pick one deliberately
# or record it in the run log the way the migration runbook requires. Account
# reconciliation merges real people, and its apply step has no rollback at all.
# `make import-cmp-content` is safe to add here and still is not: this target
# already runs it through `production-prep-local`, and a second call would
# import it twice.
production-prep-bootstrap:
	@if test -n "$(LEGACY_ZOOMCAMP_SOURCE)" && ! test -d "$(LEGACY_ZOOMCAMP_SOURCE)"; then \
		echo "LEGACY_ZOOMCAMP_SOURCE=$(LEGACY_ZOOMCAMP_SOURCE) is not a checkout." >&2; \
		echo "Clone DataTalksClub/zoomcamp-scoring and point LEGACY_ZOOMCAMP_SOURCE at it," >&2; \
		echo "or set it empty to skip the pre-2024 editions deliberately." >&2; \
		exit 2; \
	fi
	@uv run --frozen python scripts/rebuild_gate.py \
		"$(PRODUCTION_PREP_DATASET_DATABASE)"
	$(MAKE) production-prep-course-sources
	@if test -n "$(LEGACY_ZOOMCAMP_SOURCE)"; then \
		echo "$(MAKE) import-legacy-zoomcamp"; \
		$(MAKE) import-legacy-zoomcamp \
			IMPORT_DATABASE="$(PRODUCTION_PREP_DATASET_DATABASE)"; \
	else \
		echo "skipping the pre-2024 Zoomcamp history: LEGACY_ZOOMCAMP_SOURCE is empty"; \
	fi
	$(MAKE) production-prep-local \
		PRODUCTION_PREP_DATABASE="$(PRODUCTION_PREP_DATASET_DATABASE)" \
		PRODUCTION_PREP_COURSE_SOURCE_DIR="$(PRODUCTION_PREP_COURSE_SOURCE_DIR)" \
		PRODUCTION_PREP_CURRENT_REGISTRATION_INPUT="$(PRODUCTION_PREP_DATASET_REGISTRATION_INPUT)" \
		PRODUCTION_PREP_LOCAL_ARGS="$(PRODUCTION_PREP_LOCAL_ARGS)"
	$(MAKE) production-prep-dataset-verify

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
