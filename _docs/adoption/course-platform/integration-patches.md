# Course platform integration patches

Copied CMP files remain byte-identical to the pinned source. Integration is confined to target-owned files:

- `pyproject.toml` and `uv.lock`: bounded dependencies required by copied imports and E2E tests;
- `Makefile` and the Ruff/mypy configuration in `pyproject.toml`: keep byte-frozen adopted Python
  roots out of autoformat/static-style rewrites while explicitly checking target-owned integration
  shims; copied behavior is instead guarded by checksums and its unchanged characterization suite;
- `website/settings/base.py` and `website/settings/test.py`: adopted apps, middleware/context processor, CustomUser identity, compatibility/provider defaults, dedicated copied-template directory, and test-safe behavior;
- `website/urls.py`: source-compatible HTML, account, API, cadmin, admin/loginas routes alongside
  the unified scaffold routes; issue #95 restores the main-site root, moves global course discovery
  to `/courses/`, exposes the complete copied URLconf under the explicit `courses` namespace, and
  retains global non-empty legacy course reversals/inbound paths without modifying the copied
  URLconf or its outbound-link behavior;
- `website/admin_api_urls.py`: target-owned compatibility shim preserving the scaffold's
  namespaced `/api/v1/admin/health` route while the adopted compatibility API remains at `/api/`;
- `website/admin_api_views.py`: target-owned compatibility shim preserving the scaffold's
  staff-gated, versioned admin-health response without modifying copied CMP API behavior;
- `course_platform_templates/base.html`: target validated explicit-only canonical-link integration,
  labelled keyboard-accessible local website navigation, homepage brand destination, and Studio
  entry inside the existing CMP account menu; recorded separately in
  `integration-patched-files.tsv` while the source checksum remains in `copied-files.tsv`. Existing
  CMP login, session, account settings, preference, logout, impersonation, and course-admin
  behavior remains authoritative;
- `course_management/datamailer_outbox_dispatch.py`: issue #98 replaces the copied row-lock claim
  with portable conditional ORM claim and attempt fences while retaining the same delivery,
  acknowledgement, and retry outcomes;
- `scripts/load_rds_export.py`: issue #99 disables the legacy broad-copy entry point before argument
  parsing or path access and directs operators to the target-owned, allowlisted local review-data
  workflow; copied helper internals remain available for characterization and are not an approved
  operational import path;
- `scripts/generate_production_like_leaderboard_data.py` and `scripts/score_project_dev.py`: issue
  #98 selects the unified local SQLite settings and removes copied backend-specific SQL, lock
  retries, credential-file loading, and direct-SQL operator guidance while preserving catalog
  generation and project-scoring behavior through the copied ORM services;
- target scaffold Django and Playwright tests/configuration where the adopted `accounts.CustomUser`, course homepage, and source sign-in surface supersede placeholder scaffold contracts;
- `core/tests/test_course_platform_adoption.py`,
  `scripts/render_course_platform_inventory.py`,
  `scripts/verify_course_platform_adoption.py`, and `_docs/adoption/course-platform/`:
  reproducibility, inventory, and structural smoke evidence.

The two target-owned admin API compatibility shims are recorded with classification, rationale,
size, and SHA-256 in `target-owned-compatibility-shims.tsv`. The provenance verifier requires both
exact paths and validates their recorded bytes.

No scoring, submission, peer-review, leaderboard, certificate, communication, API serializer, cadmin, or other CMP business expectation is modified by integration.

The unified setting keeps the CSRF cookie readable by same-origin JavaScript because the copied loginas and submission browser flows obtain it and send the standard `X-CSRFToken` header. Session cookies remain HttpOnly.

Playwright is bounded to the source suite's `1.58` compatibility line rather than silently moving the copied live-browser harness to a newer minor release during adoption.

The copied `accounts.CustomUser` remains the adopted identity baseline: it retains AbstractUser's
`username` field, while its database `email` field is not made required or unique by this issue.
Allauth remains configured for email login as in the source settings. This supersedes the temporary
scaffold user model because preserving the source app label and migration identity is part of issue
#30. No authentication or authorization redesign is attempted here.
