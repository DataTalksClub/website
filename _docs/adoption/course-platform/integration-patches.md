# Course platform integration patches

`copied-files.tsv` records the byte-identical pinned CMP source. Copied destinations remain exact
unless an intentional target overlay is recorded with its current checksum and rationale in
`integration-patched-files.tsv`. Integration is confined to target-owned files and those explicit
overlays:

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
  labelled keyboard-accessible local website navigation using the approved clean public hubs while
  omitting the removed People catalogue, plus the homepage brand destination, unified
  DataTalks.Club title, and Studio entry inside the existing CMP account menu; recorded separately
  in `integration-patched-files.tsv` while the source checksum remains in `copied-files.tsv`.
  Existing CMP login, session, account settings, preference, logout, and impersonation behavior
  remains authoritative; issue #100 adds the one shared account menu, capability-gated Studio
  access, same-host login, and a non-visual durable-account test hook, issue #105 preserves the
  approved clean public navigation, and issue #59 makes Studio the single capability-gated
  management entry point. Issue #65 adds skip-target focus, current-location semantics, resilient
  account-route lookup, local interaction helpers, and the shared accessibility stylesheet without
  changing navigation destinations. Issue #75 serves the preserved Tailwind Play CDN 3.4.17 and Font Awesome
  5.15.1 assets from target-owned static paths, with exact source/target checksums and licenses in
  `_docs/provenance/course-platform-vendor-assets.json`; the Tailwind configuration remains before
  the runtime script and the browser no longer requires either production CDN. Issue #114 adds the
  escaped public-safe announcement after the shared header and before main content without changing
  copied account, navigation, preference, or session behavior. Issue #125 replaces only the copied
  footer markup with the target-owned shared legal-footer partial used by both target shells and
  loads its provider-free analytics-preferences behavior; course, account, navigation, preference,
  and session behavior remains unchanged;
- `courses/templates/projects/project.html`: issue #75 replaces the external commit-ID screenshot
  with concise accessible text explaining where to copy the first seven commit characters. Project
  submission fields, validation, persistence, and operator controls remain unchanged;
- `studio_courses/`, copied staff links under `courses/`, and `accounts/views/impersonation.py`:
  issues #59 and #116
  mounts the complete copied operations surface canonically at `/studio/courses`, gives its route
  names and visitor-facing labels the logical Studio Courses name, and returns copied internal
  redirects to those canonical names. The authorized trailing-slash root redirects permanently
  and directly to the slashless canonical while preserving query strings and unsafe methods;
  unauthorized requests remain at the safe login boundary. The Python package and all copied
  query, form, mutation, scoring, repair, communication, and observability behavior remain in
  place. Issue #116 performs the package, import, test, template, and CSS namespace move
  mechanically; no course operation is rewritten. Staff-only `/cadmin...` compatibility redirects
  now live only in the target-owned `cadmin/legacy_urls.py` adapter and preserve query
  strings and non-GET methods without a redirect chain. Both canonical operations and compatibility
  redirects require an explicit `site_admin` or `course_operator` role; generic Django staff and
  every unrelated Studio role fail closed before resource lookup or mutation;
- `courses/static/courses.css`: issue #59 retains the adopted design system while constraining
  Studio Courses page headers to the mobile viewport; long course titles wrap instead of being
  clipped or expanding the document width;
- copied account, logout, and social-account templates: issue #59 replaces the obsolete
  Course Management page-title suffix with DataTalks.Club while leaving authentication behavior
  unchanged;
- `course_management/datamailer_outbox_dispatch.py`: issue #98 replaces the copied row-lock claim
  with portable conditional ORM claim and attempt fences while retaining the same delivery,
  acknowledgement, and retry outcomes;
- `courses/migrations/0004_update_correct_answer_indexes.py`,
  `courses/migrations/0005_update_answers_with_indexes.py`, and
  `courses/migrations/0006_course_first_homework_scored.py`: issue #75 resolves models through the
  historical migration app registry and uses the stored legacy question values so fresh and
  maintained migration replay does not import current runtime models;
- `courses/tests/test_datamailer_signals.py` and
  `courses/tests/test_datamailer_transactional.py`: issue #75 explicitly opts the copied
  characterizations into their mocked synchronization and transactional-send paths while the
  shared deterministic test settings keep contact sync and delivery disabled by default;
- `api/openapi/spec.py`: issue #59 gives the staff-facing copied API schema the unified
  DataTalks.Club Courses name without changing its operations or authentication behavior;
- `api/openapi/course_schemas.py`, `api/views/health.py`, and
  `course_management/observability/events.py`: issue #110 extends the adopted health route/schema
  and structured event envelope with the sealed runtime
  `VERSION`, `SOURCE_SHA`, and `IMAGE_DIGEST` identity. The change retains existing health status
  semantics, keeps source/digest nullable for local execution, and does not use release metadata as
  metric dimensions;
- `data/tests/test_observability.py`: issue #110 characterizes those three stable structured event
  fields and retains the adopted CloudWatch dimension set exactly as `environment` and `event`;
- `accounts/models.py`: issue #100 expands the adopted `CustomUser` in place with normalized
  identity state plus durable alias, quarantine, and reconciliation-run evidence; it does not
  replace or renumber the copied user table;
- `accounts/auth.py`, `accounts/tests_auth.py`: issue #100 replaces unsafe raw-provider email
  selection and social auto-link characterization with verified-claim-only, fail-closed linking
  through a portable compare-and-set account claim and redacted conflict evidence;
- `accounts/views/login.py`,
  `accounts/templates/accounts/login.html`: issue #100 adds explicit cross-host reauthentication,
  propagates validated path-only `next` values to provider login, and gives returning users
  readable recovery guidance without adding another login system; issue #105 shortens the
  visitor-facing sign-in labels and generic unavailable guidance without changing the adopted
  provider or development-owner login behavior; issue #65 adds linked help/error semantics,
  a focusable error summary, and announced busy/status state without changing authentication;
- `course_platform_templates/accounts/account_settings.html`,
  `courses/templates/courses/enrollment.html`, and `courses/templates/courses/register.html`:
  issue #65 adds shared label, help, required, error-summary, value-preservation, toggle, status,
  and current-location semantics while retaining copied account, enrollment, and registration
  behavior;
- `courses/static/settings_toggles.js` and `courses/static/user_menu.js`: issue #65 retains copied
  preference and account-menu behavior while exposing busy/completion status and restoring focus
  after keyboard dismissal;
- `studio_courses/templates/studio_courses/campaign_form.html` and
  `studio_courses/templates/studio_courses/include/campaign_field.html`: issue #65 keeps the accepted Studio Courses
  routes and campaign behavior while linking the actual error state to a focusable summary and to
  each invalid field;
- `course_management/datamailer_templates/definitions/common.py`: issue #65 keeps the current
  submission-confirmation purpose and HTML while restoring its action destination to the current
  plain-text alternative; the accessibility fixture renders and validates the registry definitions
  directly and does not introduce a parallel message purpose;
- `accounts/tests_account_settings.py`: issue #100 updates the copied shell characterization so
  Studio appears through the explicit course-operator capability policy rather than `is_staff`
  alone;
- `review_import/manifest.py`: issue #100 classifies adopted account, email, provider, session,
  token, alias, quarantine, and reconciliation rows as sensitive so content-only review imports
  cannot create or project identity state;
- `scripts/load_rds_export.py`: issue #99 disables the legacy broad-copy entry point before argument
  parsing or path access and directs operators to the target-owned, allowlisted local review-data
  workflow; copied helper internals remain available for characterization and are not an approved
  operational import path;
- `scripts/generate_production_like_leaderboard_data.py` and `scripts/score_project_dev.py`: issue
  #98 selects the unified local SQLite settings and removes copied backend-specific SQL, lock
  retries, credential-file loading, and direct-SQL operator guidance while preserving catalog
  generation and project-scoring behavior through the copied ORM services;
- target scaffold Django and Playwright tests/configuration where the adopted `accounts.CustomUser`,
  course homepage, and source sign-in surface supersede placeholder scaffold contracts; issue #105
  fronts `/courses/` with the read-only public projection while delegating to the adopted course
  list whenever the unified database contains course rows;
- `core/tests/test_course_platform_adoption.py`,
  `scripts/render_course_platform_inventory.py`,
  `scripts/verify_course_platform_adoption.py`, and `_docs/adoption/course-platform/`:
  reproducibility, inventory, and structural smoke evidence.

The two target-owned admin API shims and two-file legacy `/cadmin` route adapter are recorded with
classification, rationale, size, and SHA-256 in `target-owned-compatibility-shims.tsv`. The
provenance verifier requires all four exact paths and validates their recorded bytes.

No scoring, submission, peer-review, leaderboard, certificate, communication, API serializer, or
other CMP business behavior is modified by this integration. The copied management presentation
and route names change under issue #59; its operational logic remains characterized by the copied
suite.

## Protected copied course templates

The logical template names derived from the pinned `courses/templates/**` rows in
`copied-files.tsv` are the visual source of truth for learner and course presentation. Project-level
`templates/<logical-name>` files and templates from earlier installed apps must not shadow those
copied destinations. The adoption contract fails closed on either a project collision or a loader
origin that does not resolve to the destination recorded in the pinned ledger.

The target-owned `public/course_hub.html` remains a separate read-only projection for deployments
with no adopted `Course` rows. Because that is a legitimate runtime state, it uses the same pinned
CMP hero, section order, card hierarchy, spacing, and controls while retaining the projection's
real catalog records and canonical `/courses/<slug>` destinations. It does not shadow a copied
logical template or create database rows. Focused Django and browser contracts exercise the
no-row projection, the database-backed visible catalog, and the database-backed empty-visible
state independently so deployment parity cannot depend on seeded data.

If a future integration requirement must change a copied course template, change the adopted file
in place and keep the overlay readable. Record its pinned source hash, current target hash, narrow
rationale, and focused behavior/browser tests in the integration ledger. A parallel
higher-precedence template is not an integration mechanism and must not be used to redesign the CMP
section, card, control, or content hierarchy.

The unified setting keeps the CSRF cookie readable by same-origin JavaScript because the copied loginas and submission browser flows obtain it and send the standard `X-CSRFToken` header. Session cookies remain HttpOnly.

Playwright is bounded to the source suite's `1.58` compatibility line rather than silently moving the copied live-browser harness to a newer minor release during adoption.

The copied `accounts.CustomUser` remains the adopted identity baseline: it retains AbstractUser's
`username` field, while its database `email` field is not made required or directly unique. Issue
#100 adds an active-only normalized-email constraint through additive migrations while retaining
legacy identifiers and login compatibility. Allauth still provides the adopted provider flow, now
with verified ownership and fail-closed linking. Preserving the source app/table and original
migration prefix remains part of issue #30; the intentional evolution is recorded in
`integration-patched-files.tsv` and `_docs/architecture/single-durable-account.md`.
