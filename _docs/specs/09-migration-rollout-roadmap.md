# 09 - Migration, rollout, and roadmap

Status: draft

The migration is delivered as working vertical slices on `web.dtcdev.click`. The existing main, docs, FAQ, Podwiki, and course platform remain deployable until their replacement passes its own compatibility gate.

## Milestone 0 - Decisions and immutable inventories

Deliverables:

- approve or amend the recommended defaults in [open decisions](open-decisions.md);
- production and generated-site URL/link/SEO manifest for all four content repositories;
- current course-platform HTML/API route inventory and consumer list;
- current course-platform database schema/data inventory and export process;
- content schema corpus with legacy variants and renderer feature inventory;
- source-to-target data ownership and authorization matrices;
- privacy data inventory and provisional retention approval;
- architecture decision records for content sync, course/cohort versioning, authentication, email semantics, search, and AWS topology.

Exit gate: no public route, source content kind, current course workflow, or management action is unclassified.

## Milestone 1 - Foundation and sandbox

Deliverables:

- `uv`-managed Django project, custom user model, PostgreSQL, Make targets, lint/type/test setup, and Docker image;
- core apps, shared service/command conventions, audit/request IDs, health endpoints, and configuration checks;
- Studio shell, admin API shell, OIDC integration, permissions, capability registry, token model, and OpenAPI generation;
- web/worker processes and durable job helpers;
- Terraform `sandbox/website` stack and GitHub OIDC delivery pipeline;
- `web.dtcdev.click` deployment with TLS, noindex, logs, alarms, backups, and rollback image.
- generated route-cache registry with every route initially private/disabled unless explicitly
  classified, plus source/policy tests for the anonymous classifier and zero-TTL rollback;
- one-shot readable schema-2 application VERSION propagated through immutable image publication,
  exact task runtime identity, public/health/API surfaces, and rollback/recovery records;
- adopted course-platform source/migrations/tests mounted in the unified Django project, with its characterization suite passing before domain changes.

Exit gate: authenticated Studio/API health capability works in development, unauthorized paths fail correctly, and no production data/content is loaded.

## Milestone 2 - Compatibility harness and main content

Deliverables:

- baseline manifest crawler and comparison reports;
- common content-source/release/document/relation/asset models;
- GitHub webhook verification, sync queue, reconciliation, candidate validation, preview, activation, and rollback;
- main-site adapter for people, articles, podcasts, books, tools, conferences, assets, and hub queries;
- exact main-site routes, metadata, structured data, sitemap, and internal link validation;
- Studio/API content source, run, preview, activation, and diagnostics capabilities.
- durable content-release invalidation intents using the correct first-release `/*` fallback and
  bounded class TTL behavior when provider submission fails.

Exit gate: every current main-site URL and link passes on development with the expected production canonical, and a broken candidate demonstrably leaves the active site unchanged.

## Milestone 3 - Docs, FAQ, and Wiki (legacy Podwiki)

Deliverables:

- docs adapter, navigation/breadcrumb/search behavior, Mermaid/callouts/code, heading anchors, and edit-on-GitHub;
- FAQ adapter, exact anchors, pages, JSON feeds, literal Jinja behavior, and edit-on-GitHub;
- Podwiki-source adapter at the sole canonical clean `/wiki` route family, with `/wiki/<slug>`
  editorial details, query search on `/wiki?q=`, typed chips/citations,
  catalogs, graph JSON/deep links, search filters, entity canonicals, and SEO dates; the
  owner-approved preservation exception leaves `/podwiki/` absent with no redirect;
- unified PostgreSQL search projection with current-contract adapters;
- cross-repository person/link/reference validation and complete asset routing.

Exit gate: the combined `/`, `/docs/`, `/faq/`, and sole canonical `/wiki` compatibility report
has no unexplained differences or broken internal links. `/podwiki/` is excluded by explicit owner
decision and remains a real `404` rather than a preserved or redirected route.

## Milestone 4 - Course and cohort foundation

Deliverables:

- expand-and-contract rename of the existing edition-like Course to Cohort plus a new reusable Course parent;
- mechanically retargeted existing homework/project/criteria/enrollment relationships to Cohort without replacing their business logic;
- Studio/API CRUD, duplication, lifecycle, permissions, revisions, idempotency, and audit for these resources;
- public course/cohort landing, registration, learner account linking, enrollment, dashboard, and calendar;
- verified account -> shared `MemberProfile` -> course-specific registration flow, deliberately
  minimized immutable shared-profile snapshots, separately registration-owned
  email/target/comment/notice/consent evidence, and compatibility projections for adopted account
  consumers;
- reviewed legacy `Course edition -> Course parent + Cohort` mapping generator;
- initial dry-run import with counts, stable-ID maps, and exception report;
- compatibility routing for current `courses.datatalks.club` pages and APIs.

Exit gate: multiple cohorts can use one course safely, existing cohort curriculum remains isolated, and a production-like database upgrades repeatably without learner/submission loss.

## Milestone 5 - Complete course workflows

Deliverables:

- homework definitions/questions, cohort homework, submissions, answer checks, scoring, and statistics;
- project definitions/criteria, cohort projects, submissions, peer assignment/review, scoring, voting, and statistics;
- leaderboards, privacy preferences, complaints, completion, certificates, and wrapped/reporting compatibility where retained;
- deadline schedules and unified email outbox migration;
- complete Studio/admin API course management parity;
- current public/data API compatibility and new versioned learner/admin APIs;
- full rehearsal import and score/certificate reconciliation;
- Terraform-managed `courses.datatalks.club` redirect Lambda plan and generated legacy-path map, held inactive until all API consumers are ready.

Exit gate: current course end-to-end and API suites have mapped equivalents, production-like totals reconcile, and operators can perform every supported workflow through both Studio and admin API.

## Milestone 6 - Events and transactional email

Deliverables:

- event lifecycle, person relationships, public detail/list, database event import, and calendars;
- accountless event verification, confirmation, management/cancellation, attendance, and privacy
  flow;
- versioned Studio/API email templates and preview/test/publish/rollback;
- durable email delivery/attempt/event/suppression model and SES adapter;
- profile completion, Slack-access grant, secret-at-send/reveal transactional purpose, rotation, and
  audited operator resend after the target EmailDelivery lifecycle is available;
- event and course message purposes, bulk operation resources, delivery diagnostics, and alerts;
- SES sandbox safeguards and one controlled real delivery smoke test.

Exit gate: registration, course communication, event changes, retries, provider events, ambiguity, suppression, and role/PII controls pass fault-injection and browser tests.

## Milestone 7 - Full rehearsal and performance

Deliverables:

- fresh content sync and final course database rehearsal;
- full URL/link/SEO, accessibility, security, privacy, API parity, load, backup/restore, and failure-mode reports;
- production-shaped Terraform plan and runbooks;
- data freeze/delta-import procedure;
- DNS/edge cutover procedure, lowered TTL where useful, smoke checklist, owners, and rollback triggers;
- Search Console/sitemap preparation without submitting development URLs;
- anonymous public MISS/HIT, credential/private bypass, poisoning, country suggestion, durable
  invalidation, WAF count/block, cheapest-sufficient plan eligibility, allowance alarms, and
  TTL-zero rollback reports.

Exit gate: all release criteria pass on `web.dtcdev.click`, open exceptions have explicit owner acceptance, and restore/rollback rehearsals succeed.

## Milestone 8 - Production cutover

1. Keep old static sites and course platform serving while the final content sync and database delta import run with outbound email disabled.
2. Reconcile data counts, checksums, scores, certificates, links, routes, and active content commits.
3. Enable the new production stack behind its edge endpoint and run internal smoke tests.
4. Make the new web revision ready, submit its idempotent application-SHA invalidation, and require
   provider completion before release finalization. Keep cache disabled/TTL zero unless the full
   route/viewer matrix, WAF, logging, plan eligibility, and alarms have passed their gates.
5. Switch canonical DNS/edge routing while retaining legacy course-host compatibility; deploy the redirect Lambda only after its browser/API consumer gate passes.
6. Enable workers and outbound transactional email exactly once after outbox reconciliation.
7. Submit the unchanged production sitemap and monitor robots/canonicals, errors, crawlers,
   registrations, enrollments, profile/Slack delivery, invalidation, cache, WAF, allowance, queue
   state, email, and top landing pages.
8. Keep legacy artifacts and databases read-only through the agreed rollback window.

Permanent redirects are enabled only after destinations pass production smoke tests.

## Rollback

Preferred rollback is an immutable application-image rollback with the new database and dynamic endpoints retained. Database changes follow expand/migrate/contract rules so the previous release can read newly written data.

Application rollback selects the exact recorded identity schema, VERSION, full source SHA, image
digest, task definitions, and service counts. It never derives a replacement timestamp or trusts a
mutable image tag. During the bounded transition an existing schema-1 target retains its full SHA
as VERSION; every newly published or successful release is schema 2.

Rollback must account for registrations/enrollments written after cutover:

- never point the entire site back to static hosting if that removes dynamic endpoints;
- keep registration, account, course, Studio, API, and webhook paths routed to a compatible Django revision;
- pause/reconcile workers before changing revisions;
- preserve idempotency/outbox state so two revisions cannot send the same message;
- invalidate under the rollback release identity so old/new templates and routes cannot remain
  mixed; on cache/classifier uncertainty set public TTLs to zero through reviewed Terraform input;
- use only the reviewed emergency WAF toggle, never a console-only rule, origin exposure, or broader
  caching action;
- do not reverse successful content/data migrations destructively;
- use retained content releases and legacy static artifacts for read-only fallback.

Quantitative rollback triggers include unexplained URL failure, elevated `5xx`, registration/enrollment failure, authentication failure, score/data corruption, email duplication/backlog, broken canonical/sitemap behavior, or an unrecoverable operator-security failure.

## Data migration controls

- Repeatable idempotent import commands support dry-run and exact source snapshot identifiers.
- Each import reports source/target counts, stable ID mapping, duplicates, missing relations, field transformations, checksum/totals, and rejected rows.
- Existing primary keys are retained in a mapping table even when target UUIDs differ.
- Consent is imported only with evidence; absence is not consent.
- Outbound email is disabled during rehearsal and import.
- Derived scores/statistics are recalculated and compared, not trusted blindly or silently replaced.
- Sampled human validation covers active and archived cohorts, complex projects/reviews, certificates, unusual legacy podcast/content, and high-traffic pages.
- Cutover has an explicit write freeze and final delta plan for each old system.

### Member-profile expand and contract

Create one `accounts.MemberProfile` per preserved `CustomUser` without deleting or renumbering any
account, social relation, course registration, enrollment, or learner record. Rehearse dry-run and
apply twice; compare aggregate counts/checksums and only synthetic/test identifiers.

Preserve the adopted account's certificate name, country/region, registration role,
GitHub/LinkedIn/website URLs, About text, preferred timezone, preferences, login/social relations,
and course relations throughout the migration.

For each account, seed non-empty adopted `CustomUser` profile columns first. Fill a remaining blank
only as a suggestion from the most recent linked `CourseRegistration`, ordered by `created_at` then
primary key; company may suggest organization. Report every conflict and never overwrite a
non-empty account value from history. Map all adopted role values to the stable version-1 choices.
CloudFront country is UI-only, lowest precedence, and never a migration source. All migrated or
suggested values are unconfirmed and cannot satisfy completion until the member submits them.

Activate `MemberProfile` as the single authority only after the shared accounts service can maintain
temporary `CustomUser` compatibility projections and every new HTML, self API, Studio, admin API,
and registration write uses it. Direct adapter writes fail tests. Remove compatibility columns only
in a later reviewed contract phase after all readers move and rollback evidence expires. Historical
minimized shared-profile snapshots, separately registration-owned normalized email, target
snapshots, comments, notice/consent evidence, and `accepted_newsletter` remain intact. New
registrations receive the exact separated shared-profile-snapshot and registration-owned-field
contract from specification 04.

### Cache and invalidation rollout

Move managed WAF/rate rules through representative count mode before reviewed blocking. Record the
current cheapest-sufficient plan comparison and exact subscription eligibility before any
subscription/apply; #78 gates repeated Terraform apply and #94 gates changes to the current
root/state identity. Development evidence does not silently select production pricing or mutate its
account/domain.

Enable positive TTL by reviewed route class only after Django headers, viewer classifier,
origin-response guard, Terraform policies, private-route bypass, poison canaries, logs, and alarms
agree. Content activation commits its invalidation intent with the pointer swap and never waits for
network I/O. Deployment waits for its application-SHA invalidation before finalization; rollback
uses its own identity/invalidation. Terminal content invalidation failure alerts but cannot reverse
the atomic content pointer; bounded class TTL is the correctness backstop.

## Documentation and handoff

Each milestone updates:

- operator and incident runbooks;
- content schema/authoring docs and edit-on-GitHub guidance;
- Studio/API user guide and generated OpenAPI;
- course/cohort workflow and migration mapping docs;
- privacy/retention and data-flow inventory;
- deployment, cache/WAF/invalidation/cost, rollback, backup, and restore docs;
- accepted departures from these specs and their rationale.

## Completion definition

The project is complete only when the unified production site is stable, management parity is verified, legacy course writes are disabled, link/SEO monitoring shows no unexplained regression, and rollback/retention obligations have passed their agreed window.

## Historical registration aggregate rollout

Historical totals use an expand-and-contract overlay keyed by the accepted #105 canonical source
identity and slug; they do not wait for the #45 database Event lifecycle. A changed protected source
checksum creates a new staged source/aggregate revision. Replaying the same
checksum/schema/policy/mapping-set revision is a deterministic no-op. Validation requires every
candidate to be exactly mapped or explicitly excluded, rejects changed canonical identity and
quarantine, and activates the reviewed set atomically. Prior aggregate revisions remain available
for reasoned, idempotent rollback.

When #45 introduces Event UUIDs, a guarded migration resolves each stored #105 source identity and
slug to exactly one Event. Missing, duplicate, or changed identity blocks cutover. When a later
row-level import replaces a coverage slot, the migration atomically marks the aggregate superseded,
activates the replacement pointer, reconciles the same public total, and only then switches reads;
the aggregate and replacement are never simultaneously counted.

Automated delivery uses synthetic protected-source fixtures only. An authorized operator separately
checks real Luma/Eventbrite checksums, exact/review/exclude/source-missing mappings, counts, status
and overlap policy, replay, activation, rollback, and invalidation. Only aggregate totals and bounded
codes may be captured. #112 stays open with `human` until that gate succeeds; #109 gates positive
edge TTL, not the zero-TTL public total.
