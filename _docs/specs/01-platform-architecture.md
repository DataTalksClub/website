# 01 - Platform architecture

Status: draft

## Architectural principles

- Keep one deployable Django project with small domain apps. Do not start with microservices.
- Keep GitHub editorial ownership and database operational ownership explicit.
- Put business rules in application services shared by public views, Studio, jobs, and the admin API.
- Perform network side effects after database commit through durable jobs.
- Treat content imports, email sends, webhook deliveries, and operator commands as retryable and idempotent.
- Preserve the last known good content release whenever a new release fails.
- Prefer boring, inspectable components already used by AI Shipping Labs.

## Technology baseline

- Python 3.13 or newer supported by the selected Django release.
- Django 6.0 series, pinned through `uv.lock` and kept on a supported security release.
- SQLite is the deterministic default for local development and ordinary CI. Models, migrations,
  constraints, and service behavior use portable Django ORM contracts exercised by that suite.
- RDS PostgreSQL remains the durable store for deployed development and production. Its engine
  boundary is validated by exact-image migration, database-aware readiness, and deployed smoke.
- Django templates for public pages and Studio, with progressive enhancement rather than a separate single-page application.
- Django-Q2 with its ORM broker for asynchronous and scheduled work, avoiding a Redis dependency in the MVP.
- Relay's versioned tenant API for canonical templates, safe rendering, sender resolution, and
  transactional transport. Local development and tests use a contract-faithful fake and perform no
  provider send.
- Versioned assets in S3, resolved through stable public paths and cached at the edge.
- A backend-portable search projection that preserves the current FAQ and Podwiki public
  contracts. Ranking and indexing implementation remains owned by the content/search issue.
- OpenAPI generated from the actual admin API route and schema definitions.

Exact dependency versions are selected when implementation starts and are locked by `uv`. The project must not use floating production dependencies.

## Django applications

- `core`: environment settings, health endpoints, shared middleware, request IDs, site configuration, redirects, and audit primitives.
- `accounts`: custom email-based user model, private member profiles, Slack-access grants, staff authentication, groups, permissions, sessions, and API credentials.
- `content`: versioned content read models, exact-path routing, public hubs/details, people relationships, search documents, and asset resolution.
- `content_sync`: GitHub clients, signed webhooks, repository adapters, validation, rendering, release activation, and reconciliation jobs.
- `courses`: adopted existing course-platform app, evolved to reusable courses with cohorts while preserving enrollment, assignments, submissions, peer review, scoring, leaderboards, certificates, and learner dashboards.
- `events`: event lifecycle, speakers/hosts, registration workflow, cancellation, attendance, and exports.
- `email_app`: logical `EmailDelivery` intents, Relay correlation/idempotency metadata, redacted
  transport projections, callback/reconciliation commands, and the application service boundary
  shared by domains, jobs, Studio, and the admin API. It owns no canonical template renderer,
  provider adapter, provider attempt/event store, or sender worker.
- `studio`: staff HTML workflows. It contains presentation logic only and calls domain services.
- `api`: versioned admin-only JSON API and OpenAPI documentation. It calls the same domain services as Studio.
- `jobs`: job wrappers, retry policy, scheduling, heartbeat, and operator diagnostics.

Apps may split later only when ownership or scale justifies it.

## Data ownership

GitHub-owned and versioned:

- articles and blog posts;
- podcast episodes and transcripts;
- docs pages;
- FAQ courses, sections, questions, and JSON feed source;
- Podwiki pages, typed links, citations, graph source, and search source;
- people profiles;
- books, tools, conferences, and legacy editorial pages migrated from the main site.

Database-owned and editable through Studio/API:

- private member profiles, profile completion revisions, and Slack-access grants owned by accounts;
- courses, cohorts, teaching teams, course registrations, enrollments, assignments, submissions, peer review, scores, certificates, and learner preferences;
- events and event-person relationships;
- registrations and attendance;
- logical email intents, durable-job references, Relay correlation IDs, redacted transport status,
  callback/reconciliation freshness, and safe reason codes;
- navigation overrides, announcements, sponsors, and site settings selected for Studio ownership;
- redirect manifest and exceptional URL rules;
- content-source configuration and release activation state;
- staff roles, API credentials, and audit records.

GitHub-owned fields are never silently overridden in the database. A future Studio authoring feature must create a GitHub branch/pull request and cannot write an alternate published value.

`accounts.MemberProfile` and `content.Person` are separate identities. A member profile belongs
one-to-one to the durable authenticated user and represents private community/learner onboarding.
A Person remains the public GitHub-owned editorial record. Neither side is created, linked, updated,
published, or granted permissions by matching names, email, profile links, employment, seniority,
Slack state, or course activity. A future explicit reviewed relation may connect the independent
records without synchronizing their fields or authority.

### Member profile version 1

After the expand-and-contract migration, `MemberProfile` is the canonical store and existing
`CustomUser` profile columns are temporary compatibility projections written only by the accounts
application service. HTML, self API, Studio, admin API, and registration adapters share that service
and its validation.

| Field | Version 1 contract |
| --- | --- |
| Country | Required member-confirmed ISO 3166-1 alpha-2 code; region is derived. |
| Work status | Required: `employed`, `self_employed`, `student`, `between_roles`, `not_working`, or `prefer_not_to_say`. |
| Organization/workplace | Optional trimmed plain text, at most 160 characters; blank is valid for every work status. |
| Professional role/focus | Required: `data_engineer`, `data_scientist`, `data_analyst`, `ml_engineer`, `software_engineer_backend`, `software_engineer_other`, `student_stem`, `student_non_stem`, `other`, or `prefer_not_to_say`. |
| Seniority | Required: `learning`, `entry`, `mid`, `senior`, `lead_or_manager`, `executive_or_founder`, `not_applicable`, or `prefer_not_to_say`. |
| About/bio | Required trimmed plain text, 1–1,000 characters. |
| Ambitions/goals | Required trimmed plain text, 1–1,000 characters. |
| Why joined | Required trimmed plain text, 1–1,000 characters. |
| GitHub, LinkedIn, website | Optional, at most one HTTP/HTTPS URL of at most 500 characters each; reject userinfo, control characters, and unsafe schemes. |
| Certificate name and preferred timezone | Preserved optional account settings, reusable but not required for Slack completion. |

The code-defined choice values are migration-stable and not Studio configuration. Labels may be
translated later without changing stored values. Free text is escaped as text, never interpreted as
Markdown/HTML, and excluded from ordinary logs, audits, and metrics. Submitted links are never
fetched synchronously and render with safe external-link `rel` attributes.

Completion schema version 1 requires a verified account email and all required values. Store the
completion version, `completed_at`, current revision, and member-confirmed revision. Migrated or
suggested values remain unconfirmed until the member submits them. Valid ordinary edits preserve
completion; clearing a required value is rejected. A later required-field change uses a new schema
version and explicit rollout rather than silently redefining version 1.

The MVP adds no public member page/directory, Person inference, custom taxonomy editor, arbitrary
links, avatar upload, organization directory, profile ranking/recommendation/social graph, or staff
authority. It does not replace the account model, remove compatibility columns, add capacity or a
waitlist, create marketing campaigns, or integrate a Slack invitation/membership API.

## Request and job flows

### Public content request

1. A versioned generated route-cache registry classifies the route. Missing or conflicting
   classification fails CI and fails closed to private/disabled caching at runtime.
2. A deterministic viewer-request classifier removes spoofed internal markers and proves whether
   the request is credential-free anonymous traffic. Unknown or malformed credential-like state is
   private.
3. Resolve the exact request path against explicit Django routes and the active content route index.
4. Load only records belonging to each source's active release.
5. Render a code-owned page template using prevalidated HTML and normalized relations.
6. Emit unchanged production canonical and SEO metadata plus the registry-owned response policy.
7. CloudFront may store only an explicitly cacheable `GET`/`HEAD` anonymous-stable response for
   which Django emits the class's explicit public `s-maxage`, and which passes both request and
   response guards. Every authenticated, private, personalized, learner, registration, management,
   preview, operational, unsafe, search, or error response remains zero-TTL/no-store except the
   exact public-404 class.

No public request clones a repository, calls GitHub, parses Markdown, or mutates data.

### Content refresh

1. Receive a signed GitHub webhook or scheduled/manual reconciliation request.
2. Validate signature and deduplicate the GitHub delivery ID.
3. Enqueue a sync for one allowlisted repository, branch, and commit SHA.
4. Fetch an immutable commit, parse through the source adapter, validate all routes and references, render, and upload versioned assets.
5. Store a complete candidate release without affecting public queries.
6. Build the candidate search/graph projections and compute the changed public paths plus affected hubs,
   redirects, feeds, sitemap, search pages, and stable assets before activation.
7. Revalidate revisions and the global active-path namespace.
8. In one activation transaction, store the public route manifest, replace path claims and release
   state, switch the active pointer, and create one unique durable edge-invalidation intent for the
   distribution and content release.
9. After commit, a worker submits or coalesces invalidations and records provider state. The first
   release may use one bounded `/*` invalidation per activated release; path optimization may follow
   but cannot reduce correctness.
10. Record counts, warnings, failures, duration, actor, commit provenance, and safe invalidation
    state/latency.

An invalid or colliding candidate is quarantined. Persisted unique path claims allow exactly one
cross-source activation to win; the prior active release remains public after any failed swap. An
invalidation failure cannot roll back a committed content pointer. It retries durably, alerts on a
terminal result, and each route class bounds how long an old safe anonymous representation may
remain visible.

### Member signup, profile, Slack, and course registration

1. Email/password signup, when enabled, collects only email, its credential, and the required
   account/privacy acknowledgement. Social signup collects no profile field before provider return.
2. Verify email ownership before profile completion, Slack eligibility, or course confirmation.
   Preserve the requested Slack, active registration campaign, or account-settings intent as a
   server-side path-only value; never put an email, token, invite secret, or external next URL there.
3. Create/edit/resume the profile at `/accounts/profile/` or session-authenticated
   `GET`/`PATCH /api/v1/me/profile`. PATCH is CSRF-protected, allowlisted, revision/`If-Match`
   guarded, and uses the accounts service. GET reports required/missing fields, completion version,
   completion time, and revision.
4. On the first valid completion, atomically create or confirm one `SlackAccessGrant`, one unique
   `EmailDelivery` intent, and one durable job keyed by account, profile completion version, and
   non-secret invite version. Only the leased job may call Relay after commit, so an outage never
   rolls back the saved profile or eligibility. Live submission remains disabled until #22 approves
   this purpose.
5. Reveal Slack status/link only at `/accounts/community/slack/` to an eligible authenticated
   member through a private, no-store, noindex, referrer-safe response. The link is absent from the
   profile, grant, delivery context/body retention, logs, metrics, URLs, API examples, and evidence.
   The public `/slack` landing page is a separate surface that introduces the community and its
   channels without a join or invite link; this member-flow reveal does not change it.
6. For a new course registration, require the verified durable account and complete shared profile,
   then ask only the course-specific comment, versioned privacy acknowledgement, and separate
   optional unchecked marketing consent. Commit the registration without first creating an
   anonymous course registration. Its deliberately minimized immutable shared-profile snapshot
   contains only profile UUID, completion schema version, profile revision, snapshot timestamp,
   optional certificate/display name, confirmed country/region, organization, work status,
   professional role, and seniority. The normalized verified email, target campaign/cohort, course
   comment, privacy-notice evidence, and optional marketing-consent evidence remain separate
   registration-owned fields/evidence rather than fields of that shared-profile snapshot.

Incomplete existing accounts retain login, recovery, settings, dashboards, enrollments, and
history. They receive a non-blocking settings prompt and are gated only before first Slack access or
each new course registration. Refresh, back, duplicate submit, callback replay, expired verification,
and second-browser resume return to the first incomplete step without duplicate account, profile,
registration, grant, or delivery rows.

### Registration and email

1. Validate and rate-limit a public registration command.
2. In one database transaction, create or transition the registration, create one unique logical
   `EmailDelivery` intent, and create its durable job. Transaction rollback creates none of them.
3. Return a uniform success response after commit.
4. A worker leases the durable website job and calls Relay only after commit with the stable
   idempotency key, immutable template key/version, and allowed scalar data. Relay validates,
   renders, resolves the sender, and owns provider submission and lifecycle.
5. Signed Relay callbacks and scheduled/manual reconciliation update only the website's redacted
   status projection. Provider acceptance is distinct from delivery; lost or uncertain
   acknowledgement becomes `ambiguous` and is never automatically resent.

Accountless event registration retains this flow. Account-owned course registration instead uses
the verified member flow above and specification 04's exact separation between the minimized
shared-profile snapshot and registration-owned fields/evidence. These structural purposes fail
closed for live delivery until approved in #22; only the development Relay `courses` sender/purpose
approved in #21 may be enabled.

## Configuration

- Environment variables contain platform bootstrap settings only: environment, database URL, secret-key reference, allowed hosts, trusted origins, AWS region, and settings needed before the database is available.
- Secret values, including the Slack join URL, scoped Relay credentials, and the separate Relay
  callback signing secret, live in AWS Secrets Manager or another approved
  runtime secret channel and are never committed, stored in domain rows, or shown in Studio/API
  output or exports.
- Safe operational settings may live in database-backed configuration with typed validation, audit history, and explicit defaults.
- Every operator-tunable setting is declared once in `core/operational_settings.py` and resolved by
  `core.runtime_config.get_setting`, which reads the database row first, then the environment
  variable, then `django.conf.settings`, then the definition default. A write reaches the process
  that made it on commit and every other process within `STAMP_TTL_SECONDS`, so changing one of
  these values never requires a restart or a release. A database that cannot answer demotes its own
  layer rather than failing the read.
- The settings table holds no secret: `core.configuration` refuses to register a key, environment
  variable or settings attribute that names a credential, and its values are written to an audit
  trail and a revision history in the clear. A URL and an email address are stored as themselves,
  because the canonical origin, the mailer endpoint and the sender address are exactly what an
  operator has to change. Each such setting declares a validator that refuses userinfo, a query
  string and a fragment, so a credential cannot ride into the table inside a URL. Keeping secrets
  out of logs, audit records and error reports is the logging boundary's job and stays with
  `core.redaction`.
- Operators reach these values at `GET`/`PATCH /api/v1/admin/settings/operational`, under
  `core.read_operational_settings` and `core.change_operational_settings`, with the same
  compare-and-swap-on-`expected_revision` batch semantics as the public site settings.
- OAuth sign-in client credentials are `allauth` `SocialApp` rows, not settings rows, and are
  managed at `GET /api/v1/admin/auth/providers` and `PUT /api/v1/admin/auth/providers/{provider}`.
  The client secret is write-only: it is never returned by a read, never rendered into a page, and
  never written to the audit trail.
- Production startup fails closed if security-critical values are absent or still use development defaults.

## Health and graceful degradation

- `/health/live` checks only that the process can answer.
- `/health/ready` checks database connectivity, migrations, and required configuration without calling optional external providers.
- Content pages continue from the active release during GitHub failure.
- Registration, its logical delivery intent, and its durable job remain committed when workers or
  Relay are unavailable and report that email may be delayed.
- Member profile completion and Slack eligibility remain committed when the Slack secret or Relay
  is unavailable; the private reveal page shows a safe delayed/support state.
- Search failure does not make content pages unavailable.
- Studio/API failure must not prevent public content reads.

## Application release identity

Every new application release has one sealed schema-2 identity. The resolve boundary constructs it
once from one UTC instant as `VERSION=YYYYMMDD-HHMMSS-<source_sha[:7]>`, the lowercase 40-character
`SOURCE_SHA`, and the matching RFC3339 `constructed_at`. No build, reuse, deploy, rollback, or
recovery step may consult Git or a clock to reconstruct it.
The constructor, record readers, deployment controller, task capture, smoke, and Django runtime use
one compact VERSION parser that rejects regex-shaped but calendar-invalid UTC timestamps.

Publication attaches the immutable image and config digests to that sealed identity. The VERSION
and full-SHA ECR aliases must resolve to the same image, whose OCI version, revision, and creation
labels must match the sealed record. Web, worker, and migration use the same digest-pinned image and
receive exactly one each of `VERSION`, `SOURCE_SHA`, and `IMAGE_DIGEST`; `APP_VERSION` is only a
Python compatibility alias for `VERSION`, never a deployed environment variable.

Django exposes VERSION in all three public shells, in API metadata, and with the source SHA and
image digest on operational health surfaces and structured events. Local execution uses the
explicit `local-development-build-version-not-configured` fallback with nullable SHA/digest.
Deployed settings fail closed on that fallback or an incomplete/mismatched triplet.

## Acceptance criteria

- App boundaries above exist without circular dependencies.
- Studio and API use shared services for every mutation.
- A test proves no public content request performs GitHub I/O or Markdown rendering.
- A failed candidate sync leaves the active release and route index unchanged.
- A worker outage does not lose a committed registration or its pending email.
- No website request, service, or job calls Amazon SES or Datamailer directly, owns canonical
  mutable email content, or automatically retries an ambiguous Relay acknowledgement.
- Profile, Slack, course, Studio, and self-API writes share the accounts validation service; no flow
  creates or infers an editorial Person.
- The generated route registry, Django policy, Terraform assertions, and deployed smoke agree, and
  a public HIT cannot cross a credential/private boundary.
- All Python setup, lint, test, migration, and run commands are available through `uv`-based Make targets.
