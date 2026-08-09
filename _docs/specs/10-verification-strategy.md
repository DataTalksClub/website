# 10 - Verification strategy

Status: draft

Verification maps every requirement to an automated or explicitly manual gate. Tests use `uv` and never write fixture data into a normal development database.

## Test layers

### Unit tests

- state transition guards for content, course/cohort, assignments, events, registrations, deliveries, and operations;
- member-profile required/optional fields and stable choices, completion versions/revisions, country
  derivation/suggestion, safe URL validation, Slack eligibility/invite versions, immutable minimized
  shared-profile-snapshot selection, and separate registration-owned-field/evidence selection;
- email normalization, token hashing/versioning, idempotency keys, scoring, deadlines, timezone/DST, slug/path, and redirect behavior;
- source adapters and Markdown extensions with real legacy fixtures;
- permissions, serializers, writable-field allowlists, error envelopes, and redaction;
- rendering/accessibility helpers, search documents, graph edges, SEO metadata, and structured data.

### Database and service integration tests

- portable declarative constraints, transactions, optimistic revision conflicts, idempotency,
  job lease/result fences, and service invariants exercised on SQLite;
- candidate content activation, cross-source active-path contention, atomic rollback, bounded
  retry, and last-known-good retention;
- course/cohort ownership and historical cohort isolation;
- enrollment, submission, peer assignment, scoring, leaderboard, complaint, certificate, and reminder workflows;
- registration plus outbox atomicity;
- profile completion plus Slack grant/delivery atomicity, compatibility projections, and migration
  reconciliation on SQLite, with PostgreSQL constraints/concurrency exercised where engine behavior
  is material;
- SES/provider event deduplication, reordering, suppression, and ambiguity;
- audit events and privacy retention/deletion propagation.

### Contract tests

- current main/docs/FAQ/Podwiki URL/link/metadata manifest;
- exact FAQ JSON field/path snapshots;
- Podwiki graph/search schema and deep-link behavior;
- current course-platform public/authenticated API schemas and guarded delete behavior;
- new public, learner, and admin OpenAPI schemas;
- management capability registry parity with Studio routes/API operations/permissions/audit;
- member self HTML/API validation parity, exact profile/Slack management routes and OpenAPI,
  generated route-cache registry parity across Django/Terraform/smoke, and cache-policy fixtures;
- GitHub and SES webhook signature fixtures.

### Browser tests

Critical paths in desktop and mobile viewports:

- navigate the clean Blog, Podcast, Books, People, Events, Courses, and Wiki hubs; clean editorial
  details and direct redirects from `.html` and slash aliases; internal event/person relationships;
  Docs; FAQ anchors; Wiki query
  search/graph; and course pages;
- register/verify/cancel an event without enumeration;
- register, create/link account, enroll, submit homework/project, perform peer review, inspect scores/leaderboard, and access certificate;
- start from Slack or a course campaign, verify minimal account ownership, resume profile completion,
  confirm/edit a country suggestion, reveal a redacted Slack-success state, register without repeated
  shared questions, edit the profile, and prove both an earlier course shared-profile snapshot and
  its separate registration-owned values/evidence are unchanged;
- staff login and role denial;
- Studio content sync/preview/activation, course/cohort authoring, event operations, template preview/test, and delivery diagnosis;
- stale-edit conflict, bulk confirmation, export safety, and API token one-time display.

Tests that create real data are clearly tagged and cannot target a shared environment by default.

### Infrastructure and deployment tests

- Terraform format/validate/static checks and development plan review;
- policy checks preventing hosted-zone creation, public RDS, open task ports, unencrypted data, wildcard deploy permissions, and committed secrets;
- container non-root/runtime checks and vulnerability scan;
- migration dry run and `makemigrations --check`;
- one-shot UTC VERSION construction, strict sealed/published record parsing, dual-alias and remote
  OCI-label proof, and rejection of clock/Git reconstruction during reuse;
- exact digest-pinned task environments for web/worker/migration and rejection of inherited,
  duplicate, overridden, mixed-schema, malformed, or local-fallback identity;
- readiness/liveness, footer, API/OpenAPI/event, and exact deployed VERSION/SHA/digest verification;
- backup verification, restore drill, image rollback, and worker/outbox reconciliation;
- TLS, DNS, edge/origin restrictions, noindex, cache policy, and controlled SES delivery;
- per-route anonymous MISS -> HIT/Age and credential/private bypass; query/header/cookie poisoning;
  country-only zero-TTL forwarding; content/deploy/rollback invalidation; WAF count/block-before-
  origin; current plan eligibility/cost scenarios; allowance alarms; and TTL-zero rollback.

## Release-critical scenarios

### URL and SEO

- Every legacy URL returns equivalent content or its exact approved one-hop redirect.
- Canonical, title, description, heading, robots, sitemap, structured data, fragments, internal links, external destinations, and assets match the approved manifest.
- There are no redirect chains/loops, blanket redirects, soft 404s, or accidental staging canonicals.
- Development and previews are non-indexable.
- Production editorial paths/content, including existing `/blog/*.html` pages, robots.txt, sitemap,
  canonicals, structured data, and indexing behavior are identical on cache MISS and HIT. Positive
  caching introduces no SEO/indexing change.

### Anonymous edge cache, WAF, and cost

- Every route is generated into exactly one reviewed cache class. Unclassified or mismatched
  Django/Terraform/smoke state fails CI and runtime defaults private/disabled.
- For each class test `GET`/`HEAD`/unsafe method, empty/allowed/unknown/duplicate/malformed query,
  gzip/Brotli, expiry, ETag/304, redirect, 404, 400/401/403/405/409/429/5xx, stale origin, invalid
  origin Cache-Control, `Set-Cookie`, private/no-store, and `Vary: *`.
- Warm an anonymous public object, then request with two sessions, Authorization principals,
  CSRF-only and unknown/malformed cookies, signed/preview/management credentials, and anonymous
  again. No body, navigation, CSRF, PII, account, learner, or capability state crosses the boundary.
- Poisoning fixtures cover alternate/duplicate Host, internal/forwarded/country header spoofing,
  header case/duplicates, encoded separators, path normalization, cache-buster/tracking/duplicate
  queries, Vary/conflicting directives, and compression variants.
- Country present/missing/unknown/special/lowercase/forged, direct origin, local/test mode, manual
  replacement, and confirmation prove an optional editable suggestion with no stored/logged raw
  IP/header and normal onboarding without edge data.
- Content activation detail/hub/feed/sitemap/redirect/stable-asset changes, concurrent/duplicate
  intents, throttled/timeout/terminal invalidation, deploy finalization, rollback, and replay preserve
  atomic pointer and bounded stale behavior.
- Safe WAF fixtures cover common exploit, bad input, known reputation, ordinary/shared-NAT burst,
  cache buster, dynamic/API rates, distributed sources, accessibility client, and emergency toggle.
  Count evidence precedes block; blocked work never reaches ALB/Django/database/email.
- A redacted cost model covers actual baseline, normal, 10x, cache-busting, WAF-blocked, and
  distributed-bot cases. Test plan eligibility failure and deterministic cheapest-sufficient fallback
  without weakening cache, security, logging, or automation.
- Kill/timeout the edge function, invalidation worker, Django, and origin. Classification fails
  private, stale remains class-bounded, named alarms fire, and Terraform TTL-zero/emergency rollback
  restores safe dynamic service without exposing origin.
- Amend the inherited all-zero-TTL source/deployed assertions to expect this generated matrix while
  preserving every private/no-store, development noindex/nofollow, canonical, robots, sitemap, and
  analytics guard.

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
- A new verified registration uses confirmed `MemberProfile` values once, asks only the bounded
  course comment/privacy/optional-consent step, snapshots exactly the minimized shared-profile
  fields in specification 04, stores normalized email/target/comment/privacy/consent only as
  separate registration-owned fields/evidence, and alters neither set after profile edit, campaign
  repoint, or later registration.

### Member onboarding and Slack

- Required/optional boundaries cover every version-1 status/role/seniority choice, whitespace,
  length, Unicode, URL scheme/userinfo/control character, unknown choice, stale revision, mass
  assignment, and direct completion bypass across HTML, self API, Studio, and admin API.
- Minimal password and social signup/return, duplicate normalized email, unverified/expired
  verification, path-only intent, refresh/back/double-submit, second-browser resume, logout, and
  existing incomplete account converge on the first incomplete step without duplicate records.
- Migration covers account-only, registration-only, conflicting, blank, legacy-role,
  multiple-registration, reconciled-duplicate, and incomplete accounts. Dry-run/apply twice preserve
  adopted records, report conflicts safely, establish compatibility projections, and require member
  confirmation.
- First completion, transaction rollback, worker down, duplicate intent, lease expiry,
  transient/permanent/suppressed/ambiguous provider outcomes, authorized resend, invite rotation,
  secret unavailable, quarantine/disable/delete, and restart create one logical grant/delivery per
  key and never retain/expose the join URL.
- Owner versus another account, support masked/full PII, allowed/denied correction/resend,
  stale/replay/idempotency, export/deletion/retention, and audit/artifact canaries pass. Every signup,
  profile, Slack, registration, migration, correction, export, and deletion scenario asserts zero
  Person write/link/permission side effect.

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
- Member/profile/Slack/self/Studio/admin responses are private/no-store/noindex and absent from
  sitemap/search/public serializers. Logs, audits, metrics, traces, screenshots, browser history,
  OpenAPI examples, and retained email bodies contain no profile value, raw country/IP/header, email,
  credential, or Slack secret canary.

### Operations

- GitHub, search, worker, SES, OIDC, database, and edge fault injection produces documented degradation and alerts.
- Restore meets RPO/RTO, retains correct active content, reapplies deletions, and does not send historical outbox rows.
- Rollback does not lose post-cutover registrations/enrollments or duplicate email.

### Browser and deployed human gates

At desktop about 1440×900 and mobile about 390×844, synthetic browser scenarios cover Slack-first
and course-first signup, accessible profile errors/focus/preserved values, editable/missing country
suggestion, refresh/back/resume, second-course prefill, immutable first shared-profile snapshot and
separate registration-owned email/target/comment/privacy/consent evidence, delayed secret or
delivery, quarantine, and Studio masked/correct/resend/denied/stale states. People remains visibly
editorial and separate from Members. Inspect every screenshot under the project-local `.tmp/`
screenshots tree for expected responsive content rather than an error/debug page; never capture a
Slack link or unnecessary synthetic profile/email value.

For edge acceptance, load approved public editorial/catalog pages twice and inspect `X-Cache`/Age,
body, canonical/noindex, robots/sitemap where applicable, assets, keyboard behavior, and responsive
layout. After warming, revisit through two signed-in accounts and exercise login, profile/Slack,
registration, learner dashboard, Studio/admin/cadmin, admin API denial, health, search/query,
preview, deliberate 404, and safe 5xx fixtures. Activate synthetic content and deploy/roll back a
synthetic revision to prove bounded invalidation. Safe WAF fixtures must show non-cacheable denial
and no origin work.

The HUMAN gate uses an authorized environment and redacted evidence. An operator configures the real
Slack secret without reading it into evidence, then a synthetic verified member completes the flow
and receives/reveals a working link. The same gate proves the public MISS -> HIT/private-bypass
matrix, country suggestion, invalidation, WAF block-before-origin, selected plan/allowance alarms,
development noindex, and TTL-zero rollback. It captures no credential, origin guard, country/IP,
profile PII, or Slack secret.

## CI stages

Recommended Make targets:

- `make setup`: `uv sync --locked` plus safe local bootstrap;
- `make lint`: Ruff/format/static checks;
- `make typecheck`;
- `make test-core`: fast critical domain/permission/content contract subset;
- `make test`: full Django suite against isolated SQLite in ordinary CI;
- `make test-browser`: tagged local Playwright suite;
- `make check-openapi`: generate and fail on schema drift/undocumented routes;
- `make check-management-parity`;
- `make check-links` and `make check-seo`;
- generated route-cache registry/policy parity, poisoning/WAF fixtures, and redacted cost-model
  checks introduced with the owning implementation issue;
- `make test-migrations`;
- `make test-all`.

CI runs independent jobs where safe, publishes actionable artifacts, and blocks deployment on any
release-critical failure. Ordinary quality, Django, core Playwright, and container jobs start no
PostgreSQL service. The deployment path separately runs the exact tested image's migrations against
RDS, then requires database-aware readiness and deployed smoke. Scheduled jobs run slower full
crawls, accessibility, dependency/security, restore, and deployed-environment smoke suites.

## Test data and production safety

- Factories create deterministic users, people, courses/cohorts, assignments, events, registrations, and messages.
- Source adapter fixtures are copied from representative committed content with provenance.
- Tests use isolated ephemeral databases; they never use the normal local or shared development database.
- Browser tests targeting remote environments default to read-only. Data-creating or email-sending tests require explicit environment tags and safeguards.
- Member, staff, and cache-boundary fixtures use synthetic identities/values. Screenshots and
  deployed evidence never capture an email, profile free text/link, country value, token, origin
  guard, or Slack join secret.
- Development email tests use dry-run/allowlist/simulator behavior except one explicitly controlled smoke recipient.
- Tokens, emails, and provider payloads are redacted from artifacts.

## Acceptance report

Each release candidate produces one report containing:

- identity schema, VERSION, source commit, construction timestamp, and image/config digests;
- active content commits and import counts;
- URL/link/SEO difference summary;
- anonymous cache/private bypass, invalidation, WAF, current plan/cost/allowance, alarms, and
  TTL-zero rollback evidence;
- course migration reconciliation;
- member-profile migration, minimized shared-profile-snapshot, separate registration-evidence
  reconciliation, and Slack secret-leak canary result;
- Studio/API capability coverage;
- unit/integration/browser/security/accessibility results;
- Terraform/deployment/backup/rollback evidence;
- remaining exceptions with owner, risk, expiry, and approval.

No verbal “looks good” replaces a failed release gate.
