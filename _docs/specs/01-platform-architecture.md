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
- Amazon SES through a provider adapter; Django's console or in-memory backend in local development and tests.
- Versioned assets in S3, resolved through stable public paths and cached at the edge.
- A backend-portable search projection that preserves the current FAQ and Podwiki public
  contracts. Ranking and indexing implementation remains owned by the content/search issue.
- OpenAPI generated from the actual admin API route and schema definitions.

Exact dependency versions are selected when implementation starts and are locked by `uv`. The project must not use floating production dependencies.

## Django applications

- `core`: environment settings, health endpoints, shared middleware, request IDs, site configuration, redirects, and audit primitives.
- `accounts`: custom email-based user model, staff authentication, groups, permissions, sessions, and API credentials.
- `content`: versioned content read models, exact-path routing, public hubs/details, people relationships, search documents, and asset resolution.
- `content_sync`: GitHub clients, signed webhooks, repository adapters, validation, rendering, release activation, and reconciliation jobs.
- `courses`: adopted existing course-platform app, evolved to reusable courses with cohorts while preserving enrollment, assignments, submissions, peer review, scoring, leaderboards, certificates, and learner dashboards.
- `events`: event lifecycle, speakers/hosts, registration workflow, cancellation, attendance, and exports.
- `email_app`: templates, transactional outbox, delivery attempts, SES events, suppression, preview, and test sends.
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

- courses, cohorts, teaching teams, course registrations, enrollments, assignments, submissions, peer review, scores, certificates, and learner preferences;
- events and event-person relationships;
- registrations and attendance;
- email outbox, attempts, provider events, and suppression state;
- navigation overrides, announcements, sponsors, and site settings selected for Studio ownership;
- redirect manifest and exceptional URL rules;
- content-source configuration and release activation state;
- staff roles, API credentials, and audit records.

GitHub-owned fields are never silently overridden in the database. A future Studio authoring feature must create a GitHub branch/pull request and cannot write an alternate published value.

## Request and job flows

### Public content request

1. Resolve the exact request path against explicit Django routes and the active content route index.
2. Load only records belonging to each source's active release.
3. Render a code-owned page template using prevalidated HTML and normalized relations.
4. Emit production canonical and SEO metadata.
5. Apply cache policy by content type and environment.

No public request clones a repository, calls GitHub, parses Markdown, or mutates data.

### Content refresh

1. Receive a signed GitHub webhook or scheduled/manual reconciliation request.
2. Validate signature and deduplicate the GitHub delivery ID.
3. Enqueue a sync for one allowlisted repository, branch, and commit SHA.
4. Fetch an immutable commit, parse through the source adapter, validate all routes and references, render, and upload versioned assets.
5. Store a complete candidate release without affecting public queries.
6. Revalidate revisions and the global active-path namespace, then atomically replace path claims,
   release state, and the source's active-release pointer.
7. Refresh search/graph projections and invalidate only affected edge cache entries.
8. Record counts, warnings, failures, duration, actor, and commit provenance.

An invalid or colliding candidate is quarantined. Persisted unique path claims allow exactly one
cross-source activation to win; the prior active release remains public after any failed swap.

### Registration and email

1. Validate and rate-limit a public registration command.
2. In one database transaction, create or transition the registration and create a unique outbox message.
3. Return a uniform success response after commit.
4. A worker claims and sends the message, recording attempts and provider identifiers.
5. Signed SES events update delivery state idempotently.

## Configuration

- Environment variables contain platform bootstrap settings only: environment, database URL, secret-key reference, allowed hosts, trusted origins, AWS region, and settings needed before the database is available.
- Secret values live in AWS Secrets Manager and are never committed or shown in Studio exports.
- Safe operational settings may live in database-backed configuration with typed validation, audit history, and explicit defaults.
- Production startup fails closed if security-critical values are absent or still use development defaults.

## Health and graceful degradation

- `/health/live` checks only that the process can answer.
- `/health/ready` checks database connectivity, migrations, and required configuration without calling optional external providers.
- Content pages continue from the active release during GitHub failure.
- Registration remains committed when workers or SES are unavailable and reports that email may be delayed.
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
- All Python setup, lint, test, migration, and run commands are available through `uv`-based Make targets.
