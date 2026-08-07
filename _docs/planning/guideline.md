# Implementation guideline

Status: distilled draft; owner approval required

## Objective and invariants

Build one `uv`-managed Django application for DataTalks.Club that serves the main site, docs, FAQ, Podwiki, event registration, and the adopted course platform. It must preserve existing public contracts before introducing product or SEO changes.

The following invariants govern every implementation slice:

1. [HUMAN] GitHub remains authoritative for migrated editorial content. Studio manages its source configuration, sync, validation, preview, activation, rollback, and diagnostics, but the MVP edits content through GitHub.
2. [INFERENCE dtc-main-site,dtc-docs,dtc-faq,dtc-podwiki] No public route, fragment, asset, JSON endpoint, internal/external link, canonical, indexability signal, or structured-data difference is accepted without an explicit reviewed exception or one-hop redirect.
3. [HUMAN] Public Person is the canonical profile for guests, speakers, hosts, authors, maintainers, and related roles. Roles are ordered relationships; staff access is separate.
4. [HUMAN] Existing course-platform source, migrations, behavior, APIs, and tests are adopted at a recorded clean commit and evolved, not reimplemented.
5. [HUMAN] Course is reusable identity and Cohort is a dated delivery. Existing curriculum and learner work remain cohort-owned in the consolidation release.
6. [HUMAN] Every management operation has both a Studio entry point and an admin API operation backed by the same service, permission, validation, idempotency, and audit behavior.
7. [INFERENCE django-email-6,aisl-reference] Registration and course email is driven by durable database intent, never by an unrecorded provider call in a web request.
8. [INFERENCE dtc-aws-infra] Infrastructure is Terraform-managed, development runs at `web.dtcdev.click` in sandbox account `817685572750`, and environment/account differences are variables rather than copied architecture.

The detailed normative specifications are indexed in [`_docs/specs/README.md`](../specs/README.md). If this guideline and a numbered specification conflict, stop and update both before implementation.

## Current behavior that must survive

The static surface is not one homogeneous Markdown site. [FACT dtc-main-site] The main site uses legacy `.html` routes, nested frontmatter, raw HTML, Liquid/Kramdown, includes, JavaScript integrations, MathJax, charts, and rich SEO output. [FACT dtc-docs] Docs use title-referenced hierarchy, pretty URLs, heading-aware search, Mermaid, callouts, breadcrumbs, and edit-on-GitHub. [FACT dtc-faq] FAQ has stable ten-character anchors and exact public JSON schemas, and its Jinja-looking examples must remain literal. [FACT dtc-podwiki] Podwiki adds typed chips, timestamped citations, graph/search deep links, generated JSON, aliases, and git-derived dates.

The course system is already a substantial Django product. [FACT dtc-course-platform] It implements registrations, enrollments, homework question types, submissions and scoring, projects, peer review, voting, leaderboards, complaints, certificates, communication, dashboards, statistics, and historical Wrapped behavior. Its custom staff surface and API are an operational requirements inventory. [FACT dtc-course-platform] About 760 Django tests and 48 end-to-end tests form the starting characterization suite.

Do not generalize away source-specific behavior or replace course logic because a new abstraction looks cleaner. Compatibility evidence comes first; refactoring follows only when a required model, authorization, interface, or reliability boundary demands it.

## Application shape

Use a single Django project with separable domain apps and deployable web and worker processes. The intended boundaries are:

- `core`: settings, custom user model, request IDs, health/readiness, common IDs/timestamps, audit context, service/result conventions;
- `content`: source configuration, sync runs, candidates, releases, source documents, relations, assets, validations, and search projection;
- source adapters for `main_site`, `docs`, `faq`, and `podwiki`, each owning parsing/rendering/compatibility rules;
- `people`: public Person and ordered typed relationships to content, events, books, tools, courses, and cohorts;
- adopted course apps: preserved app labels/migration lineage and existing behavior, incrementally reorganized behind shared services;
- `events`: event lifecycle, participation roles, accountless registrations, verification/manage tokens, attendance, and calendars;
- `communications`: templates, immutable revisions, delivery outbox, attempts, provider events, suppressions, and bulk operations;
- `studio`: staff HTML composition only; no domain mutation outside application services;
- `admin_api`: `/api/v1/admin/`, scoped credentials, OpenAPI, idempotency/revision handling, and capability discovery;
- `public_api`: intentionally supported public/learner contracts plus adapters for existing course endpoints;
- `jobs`: named durable tasks/schedules, leases, retries, diagnostics, and operator controls.

Use server-rendered Django for public and Studio pages unless a narrowly interactive component justifies progressive enhancement. Do not make public rendering depend on GitHub, an email provider, or a job broker being available.

## GitHub content pipeline

[INFERENCE aisl-reference,github-webhook-validation] A signed GitHub webhook verifies raw-body HMAC-SHA256 using a constant-time comparison, records the delivery ID uniquely, and enqueues a repository plus commit SHA. Scheduled reconciliation and manual Studio/API sync recover missed hooks. Only configured repositories, branches, paths, and file limits are accepted.

Each adapter produces a candidate release containing source provenance, normalized records, raw source, rendered HTML, stable identifiers, route/fragment declarations, relations, assets, timestamps, and diagnostics. Rendering must sanitize untrusted/user-provided HTML according to a per-source policy while retaining explicitly supported legacy HTML.

Activation order:

1. fetch an allowlisted immutable commit into a bounded temporary workspace;
2. parse all supported source variants;
3. render through that repository's compatibility renderer;
4. resolve hierarchy and cross-record/person relations;
5. stage content-addressed assets;
6. build route, fragment, link, search, metadata, and structured-data projections;
7. validate the whole candidate against schemas and compatibility rules;
8. atomically switch the active release in one database transaction;
9. invalidate shared caches and publish completion diagnostics.

A failed or partial candidate never changes the active release. Keep last-known-good releases and expose diff/rollback through Studio and API. Never allow a webhook to choose arbitrary repository URLs or execute repository code.

## URL, link, and SEO compatibility

Before view implementation, crawl each current generated site and commit a machine-readable manifest containing status, redirect chain, canonical path, content kind/key, fragments, asset URLs, internal/external destinations, title/description, robots directives, social metadata, structured data, sitemap inclusion, and selected semantic fingerprints.

Build views from this manifest, not memory. Preserve slash and `.html` behavior exactly, including `/faq/json/...`, Podwiki graph/search URLs, existing assets, edit-on-GitHub links, and fragments. Redirect exceptions are explicit, one hop, and tested with queries. The development hostname sends `X-Robots-Tag: noindex, nofollow`, is excluded from production analytics/sitemaps, and emits production canonicals.

The parity report is a release artifact. A cutover gate fails on any unexplained missing/extra path, redirect chain, link destination, fragment, asset failure, indexability mismatch, canonical mismatch, schema regression, or high-value semantic/rendering difference.

## People

Preserve `Person.short` as the stable source key while adding an internal UUID and aliases where needed. A Person stores profile fields and provenance; content and operational domains store explicit ordered relationships such as author, guest, host, speaker, instructor, maintainer, or contributor. One person can hold many simultaneous roles.

Person import must reject ambiguous keys and broken references. Public Person may optionally link to an authenticated StaffUser or Learner, but that link grants no permission and does not merge public/editorial identity with private account data automatically.

## Adopted course platform and model evolution

Start from a clean recorded commit of `DataTalksClub/course-management-platform`. Copy maintained source, migrations, templates/static files, commands, fixtures, compatibility endpoints, and tests; exclude secrets, databases, generated artifacts, and unrelated working-tree changes. Preserve Django app labels and migration history. Make the entire copied suite pass in the unified project before changing behavior.

The migration is expand-and-contract:

1. add temporary `CourseFamily` plus a nullable parent relation on the current edition-like `Course`;
2. create and human-review a complete mapping from every legacy edition slug/ID to family and cohort slugs; never infer only by stripping a year;
3. backfill and validate the parent of every edition;
4. add UUID/legacy-ID mappings and cohort-aware registration constraints without removing old compatibility fields;
5. rename domain/migration state so legacy Course becomes Cohort and CourseFamily becomes Course without re-creating populated tables;
6. mechanically retarget curriculum, enrollment, submissions, reviews, scoring, templates, services, URLs, APIs, and tests to Cohort;
7. preflight and quarantine duplicates/inconsistencies before adding database uniqueness/protection constraints;
8. retain aliases/compatibility views through the rollback window, then contract unused fields.

Course owns stable family slug, identity, descriptions, branding, repositories/links, public state, people, and course-scoped staff. Cohort owns dates/timezone, registration lifecycle, visibility, scoring/completion settings, communications, teaching team, curriculum rows, enrollments, learner work, leaderboards, and certificates. Add a complete duplicate-cohort service. Do not add reusable/versioned curriculum in this migration.

Port every current `cadmin` and relevant Django-admin operation into the capability inventory. Compatibility `cadmin` routes may remain briefly while their Studio/API replacements are verified. Preserve existing public/data APIs as adapters; place new learner interfaces under `/api/v1/` and management under `/api/v1/admin/`.

Keep `courses.datatalks.club` routed to compatible application views until its browser and authenticated API consumers are inventoried and migrated. The final Terraform Lambda uses an explicit generated path map, preserves queries, emits one-hop `301` for GET/HEAD HTML, and uses `308` only for clients proven to preserve method, body, and authorization. Unknown/authenticated paths remain directly served until safe.

## Events, registration, and email

An Event has stable UUID/slug, title/description, start/end/IANA timezone, venue/meeting information, lifecycle, registration window, privacy text version, capacity policy, calendar UID/sequence, and ordered Person roles. MVP events are one-off and have no capacity/waitlist unless the owner changes that decision.

Registration is accountless. Normalize email, rate-limit and CSRF-protect submission, store consent evidence, and create a pending registration plus one-time hashed verification token. Verification atomically confirms the record and enqueues confirmation email. Resubmission is idempotent and does not reveal whether an email is registered. Signed/opaque management links permit cancellation without an account while resisting link-scanner side effects; state mutation requires an explicit confirmation action.

All messages start as a committed `EmailDelivery` with purpose, recipient snapshot, template revision, context hash, idempotency key, status, and correlation IDs. Workers lease rows, render plain-text plus HTML, call an environment-specific backend, and record attempts/provider IDs/events. Model queued, leased, sent, delivered, failed, suppressed, cancelled, and provider-acknowledgement-ambiguous states. Retries use backoff and idempotency; ambiguous sends prefer a rare duplicate to silently missing a critical message, and operators can inspect/reconcile them.

Use console/in-memory backends for local/tests and SES in `us-east-1` for sandbox after verifying identity/configuration. Retain Datamailer as a migration adapter until existing list/template/idempotency behavior is mapped and old outboxes are drained/frozen.

## Studio, API, permissions, and audit

Define a capability registry with stable action key, resource scope, service command/query, permission, Studio route, API operation ID, sensitivity, audit event, and async behavior. CI rejects management capabilities missing either interface or mapping to different services.

Suggested staff scopes are site operator, content operator, event operator, communications operator, course owner/instructor/grader/support, privacy operator, and auditor. Staff sign-in should use MFA-enforced OIDC; keep tightly controlled local break-glass access. Public/learner endpoints are not “admin API” but their mutable operations still use the same domain services.

Admin tokens are one-time plaintext with stored hash and lookup prefix, explicit scopes/object bounds, expiry, revocation, and last-used metadata. Mutations support request/idempotency IDs, optimistic revisions or `If-Match`, structured errors, per-row bulk results, dry-run, asynchronous operation resources, and guarded archive/delete. High-risk actions require reauthentication and explicit confirmation.

Every mutation records actor/principal, action, target, before/after or structured diff, request/correlation/idempotency identifiers, reason, result, IP/user agent as appropriate, and timestamp. Do not log secrets, raw tokens, or unnecessary message bodies/PII. Audit records are append-only to ordinary operators.

## Security, privacy, accessibility, and operations

[FACT django-security-6] Django's CSRF, host validation, HTTPS, proxy, and cookie features require correct production configuration; framework defaults do not replace authorization, throttling, sanitization, or secrets management. Enforce secure cookies, HSTS after validation, allowlisted hosts/origins, CSP, content-type/frame/referrer policies, and safe proxy headers. Validate uploads and outbound URLs; move remote validation out of synchronous requests and block private/link-local metadata destinations.

Maintain a data inventory with purpose, lawful/consent basis, access roles, retention, deletion/anonymization, export, and audit treatment. Collect the least registration/learner PII needed. Privacy retention values stay provisional until owner review; production import cannot proceed on unapproved assumptions.

Target WCAG 2.2 AA for public and Studio paths: semantic structure, keyboard operation, visible focus, labels/errors, contrast, skip links, reduced motion, accessible tables/dialogs, and screen-reader status for asynchronous work. Run automated checks plus manual keyboard/screen-reader sampling.

Expose liveness/readiness, structured logs with request/job IDs, metrics for latency/errors/registrations/sync/jobs/email, and actionable alarms. Backups need automated restore tests; jobs use leases, idempotency, retry limits, and dead-letter/operator recovery. Define and rehearse RPO/RTO, deployment rollback, database expand/contract, and incident runbooks before production.

## AWS and Terraform

Keep infrastructure implementation in `DataTalksClub/aws-infra`, one state root per workload/environment. Re-read live inventory before planning because it can change. In sandbox, explicitly reference hosted-zone ID `Z05963572WVWFHDQZH5NE`; never create or select `dtcdev.click` by name alone.

The website stack includes ECR, ECS/Fargate web and worker services, one-off migration task, ALB/TLS, PostgreSQL, Secrets Manager/SSM, CloudWatch logs/alarms, S3 content assets, SES permissions, Route 53 record, backup/retention controls, and GitHub OIDC least-privilege deployment. Sandbox may use a cost-adjusted network shape, but modules/variables expose account, region, zones, network IDs, DNS zone/name, scaling/sizing, retention, deletion protection, sender identity, alarm topics, and tags for production instantiation.

Terraform does not build images, run application migrations implicitly, import production data, or manage editorial content. Delivery order is image -> migration task -> web health -> worker, with rollback to an immutable prior image. The eventual course-host redirect is a separate small production root/module with path-map artifact, logs/alarms, rollback origin, and a sandbox rehearsal hostname.

## Delivery order and gates

1. Approve defaults, capture immutable URL/content/course/data inventories, and classify all management actions.
2. Establish the `uv` Django foundation, adopted course baseline, Studio/API shells, permissions/audit, web/worker image, and sandbox Terraform deployment.
3. Build manifest tooling and migrate main-site GitHub content with atomic sync and exact compatibility.
4. Add docs, FAQ, and Podwiki adapters and search-contract parity.
5. Execute Course/Cohort expand-and-contract, compatibility routing, learner flows, and complete Studio/API management parity using existing tests as the baseline.
6. Add event registration and the unified durable email subsystem; migrate course communications deliberately.
7. Rehearse content/data sync, links/SEO, security, privacy, accessibility, performance, backups/restores, failure injection, cutover, and rollback.
8. Cut production traffic only after every gate passes; activate the course-host redirect Lambda only after consumer-specific validation.

Each slice must be deployable to `web.dtcdev.click`, observable, rollback-safe, and accompanied by operator/API/authoring documentation.

## Verification

Required test layers are unit/property tests for parsers, transitions, normalization, permissions, scoring, tokens, and idempotency; service/DB tests for constraints and concurrency; renderer fixtures from every legacy schema; contract/OpenAPI tests; route/link/SEO manifest comparisons; copied course characterization and E2E tests; browser and accessibility tests; migration forward/backward rehearsals on production-like copies; email/provider fault injection; security negative tests; Terraform validation/plan/policy checks; backup/restore and deployment rollback drills.

The project is not ready for production merely because pages render. Completion requires zero unexplained compatibility differences, complete Studio/API capability parity, reconciled course totals/scores/certificates, stable event/email behavior under retry/crash, approved privacy/identity settings, green security/accessibility evidence, and rehearsed restore/rollback.

## Decisions still blocking production

- [OPEN] Approve read-only-plus-GitHub-links versus Studio-created pull requests for GitHub content.
- [OPEN] Supply authoritative mapping for unusual legacy course editions and inventory authenticated course API consumers.
- [OPEN] Select staff OIDC provider and break-glass ownership.
- [OPEN] Confirm production sender/reply-to identity and long-term SES versus Datamailer role.
- [OPEN] Approve privacy contact, minors policy, educational-record retention, anonymization/deletion, and provisional SLO/RPO/RTO values.
- [OPEN] Confirm the timezone for legacy naive timestamps.
- [OPEN] Accept the recommended sandbox network/cost shape and the PostgreSQL Podwiki search replacement subject to parity.

Implementation may begin with the reversible foundation and inventories while these are open. Production data import, external email, indexable traffic, and the course-host redirect may not.
