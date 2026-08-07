# 10 - Verification strategy

Status: draft

Verification maps every requirement to an automated or explicitly manual gate. Tests use `uv` and never write fixture data into a normal development database.

## Test layers

### Unit tests

- state transition guards for content, course/cohort, assignments, events, registrations, deliveries, and operations;
- email normalization, token hashing/versioning, idempotency keys, scoring, deadlines, timezone/DST, slug/path, and redirect behavior;
- source adapters and Markdown extensions with real legacy fixtures;
- permissions, serializers, writable-field allowlists, error envelopes, and redaction;
- rendering/accessibility helpers, search documents, graph edges, SEO metadata, and structured data.

### Database and service integration tests

- PostgreSQL uniqueness, transactions, row locks, revision conflicts, job leases, and concurrency invariants;
- candidate content activation and last-known-good retention;
- course/cohort ownership and historical cohort isolation;
- enrollment, submission, peer assignment, scoring, leaderboard, complaint, certificate, and reminder workflows;
- registration plus outbox atomicity;
- SES/provider event deduplication, reordering, suppression, and ambiguity;
- audit events and privacy retention/deletion propagation.

### Contract tests

- current main/docs/FAQ/Podwiki URL/link/metadata manifest;
- exact FAQ JSON field/path snapshots;
- Podwiki graph/search schema and deep-link behavior;
- current course-platform public/authenticated API schemas and guarded delete behavior;
- new public, learner, and admin OpenAPI schemas;
- management capability registry parity with Studio routes/API operations/permissions/audit;
- GitHub and SES webhook signature fixtures.

### Browser tests

Critical paths in desktop and mobile viewports:

- navigate main site, articles, podcast, people, docs, FAQ anchors, Podwiki search/graph, and course pages;
- register/verify/cancel an event without enumeration;
- register, create/link account, enroll, submit homework/project, perform peer review, inspect scores/leaderboard, and access certificate;
- staff login and role denial;
- Studio content sync/preview/activation, course/cohort authoring, event operations, template preview/test, and delivery diagnosis;
- stale-edit conflict, bulk confirmation, export safety, and API token one-time display.

Tests that create real data are clearly tagged and cannot target a shared environment by default.

### Infrastructure and deployment tests

- Terraform format/validate/static checks and sandbox plan review;
- policy checks preventing hosted-zone creation, public RDS, open task ports, unencrypted data, wildcard deploy permissions, and committed secrets;
- container non-root/runtime checks and vulnerability scan;
- migration dry run and `makemigrations --check`;
- readiness/liveness and exact deployed-SHA verification;
- backup verification, restore drill, image rollback, and worker/outbox reconciliation;
- TLS, DNS, edge/origin restrictions, noindex, cache policy, and controlled SES delivery.

## Release-critical scenarios

### URL and SEO

- Every legacy URL returns equivalent content or its exact approved one-hop redirect.
- Canonical, title, description, heading, robots, sitemap, structured data, fragments, internal links, external destinations, and assets match the approved manifest.
- There are no redirect chains/loops, blanket redirects, soft 404s, or accidental staging canonicals.
- Development and previews are non-indexable.

### Content sync

- Valid commit activates atomically.
- Invalid frontmatter, unsafe HTML/link, traversal/symlink, oversized content, missing person/reference, bad signature, and replayed delivery are rejected.
- Duplicate/out-of-order webhook cannot activate twice or move back to an older commit.
- GitHub outage leaves active content available and triggers freshness alert.
- Search/graph/asset build failure does not publish a partial release.

### Course/cohort

- One course supports multiple cohorts and one learner can enroll in several cohorts without cross-cohort submissions/scores.
- Editing/duplicating one cohort's homework/projects/criteria does not alter another cohort.
- Homework/project safe-delete rules protect existing responses.
- Scoring recomputation is idempotent and matches expected legacy fixtures.
- Peer assignment/review races do not duplicate or self-assign improperly.
- Leaderboard privacy, complaints, certificates, deadline boundaries, DST/leap-day behavior, and legacy API compatibility pass.
- Migration dry runs reconcile every source table and report unresolved mappings.

### Event and email

- Replayed registration/browser/network requests create one logical registration and one message per purpose/version.
- Verification/cancellation links enforce expiry, version, scope, revocation, redaction, and link-scanner-safe POST.
- Event cancellation/reschedule operations are resumable/idempotent and create correct ICS sequences.
- Worker crash before send, after provider acceptance, and before local acknowledgement produces the documented state and operator path.
- Provider events are authentic, unique, reorder-tolerant, correctly correlated, and do not mislabel accepted as delivered.
- Bounce/complaint suppression and explicit resend behavior pass.

### Authorization and management API

- Every role/capability has allowed and denied Studio/API tests.
- Cross-object, hidden-field, mass-assignment, PII, export, send, activation, grading, certificate, role, and token attacks fail.
- Mutating replays return the original idempotent result or conflict according to contract.
- Stale revision/`If-Match` writes return `409` without overwrite.
- Bulk limits, partial failure, operation progress/cancellation, and per-object authorization work.
- Credential expiry/revocation and staff disablement take effect within the approved bound.

### Security, privacy, and accessibility

- CSRF, session fixation/expiry/revocation, CORS, CSP/security headers, request limits, throttling, SSRF, XSS, CSV injection, and error leakage tests pass.
- Privacy export, correction, deletion/anonymization, retention, processor propagation, and restored-backup tombstone replay pass.
- Automated accessibility checks plus manual keyboard, screen-reader, zoom/reflow, contrast, focus, reduced-motion, and form-error tests pass critical flows.

### Operations

- GitHub, search, worker, SES, OIDC, database, and edge fault injection produces documented degradation and alerts.
- Restore meets RPO/RTO, retains correct active content, reapplies deletions, and does not send historical outbox rows.
- Rollback does not lose post-cutover registrations/enrollments or duplicate email.

## CI stages

Recommended Make targets:

- `make setup`: `uv sync --locked` plus safe local bootstrap;
- `make lint`: Ruff/format/static checks;
- `make typecheck`;
- `make test-core`: fast critical domain/permission/content contract subset;
- `make test`: full Django suite against PostgreSQL in CI;
- `make test-browser`: tagged local Playwright suite;
- `make check-openapi`: generate and fail on schema drift/undocumented routes;
- `make check-management-parity`;
- `make check-links` and `make check-seo`;
- `make test-migrations`;
- `make test-all`.

CI runs independent jobs where safe, publishes actionable artifacts, and blocks deployment on any release-critical failure. Scheduled jobs run slower full crawls, accessibility, dependency/security, restore, and deployed-environment smoke suites.

## Test data and production safety

- Factories create deterministic users, people, courses/cohorts, assignments, events, registrations, and messages.
- Source adapter fixtures are copied from representative committed content with provenance.
- Tests use isolated ephemeral databases; they never use the normal local or shared development database.
- Browser tests targeting remote environments default to read-only. Data-creating or email-sending tests require explicit environment tags and safeguards.
- Development email tests use dry-run/allowlist/simulator behavior except one explicitly controlled smoke recipient.
- Tokens, emails, and provider payloads are redacted from artifacts.

## Acceptance report

Each release candidate produces one report containing:

- source commit and image digest;
- active content commits and import counts;
- URL/link/SEO difference summary;
- course migration reconciliation;
- Studio/API capability coverage;
- unit/integration/browser/security/accessibility results;
- Terraform/deployment/backup/rollback evidence;
- remaining exceptions with owner, risk, expiry, and approval.

No verbal “looks good” replaces a failed release gate.
