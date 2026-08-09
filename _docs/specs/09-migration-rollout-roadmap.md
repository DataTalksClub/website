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

## Milestone 1 - Foundation and development

Deliverables:

- `uv`-managed Django project, custom user model, project-local SQLite for development and
  ordinary CI, portable Django contracts, Make targets, lint/type/test setup, and Docker image;
- core apps, shared service/command conventions, audit/request IDs, health endpoints, and configuration checks;
- Studio shell, admin API shell, OIDC integration, permissions, capability registry, token model, and OpenAPI generation;
- web/worker processes and durable job helpers;
- Terraform `sandbox/website` stack and GitHub OIDC delivery pipeline;
- `web.dtcdev.click` deployment with TLS, noindex, logs, alarms, backups, and rollback image.
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

Exit gate: every current main-site URL and link passes on development with the expected production canonical, and a broken candidate demonstrably leaves the active site unchanged.

## Milestone 3 - Docs, FAQ, and Podwiki

Deliverables:

- docs adapter, navigation/breadcrumb/search behavior, Mermaid/callouts/code, heading anchors, and edit-on-GitHub;
- FAQ adapter, exact anchors, pages, JSON feeds, literal Jinja behavior, and edit-on-GitHub;
- Podwiki adapter, typed chips/citations, catalogs, graph JSON/deep links, search filters, entity canonicals, and SEO dates;
- unified backend-portable search projection with current-contract adapters;
- cross-repository person/link/reference validation and complete asset routing.

Exit gate: the combined `/`, `/docs/`, `/faq/`, and `/podwiki/` compatibility report has no unexplained differences or broken internal links.

## Milestone 4 - Course and cohort foundation

Deliverables:

- expand-and-contract rename of the existing edition-like Course to Cohort plus a new reusable Course parent;
- mechanically retargeted existing homework/project/criteria/enrollment relationships to Cohort without replacing their business logic;
- Studio/API CRUD, duplication, lifecycle, permissions, revisions, idempotency, and audit for these resources;
- public course/cohort landing, registration, learner account linking, enrollment, dashboard, and calendar;
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
- full rehearsal import and score/certificate reconciliation.
- Terraform-managed `courses.datatalks.club` redirect Lambda plan and generated legacy-path map, held inactive until all API consumers are ready.

Exit gate: current course end-to-end and API suites have mapped equivalents, production-like totals reconcile, and operators can perform every supported workflow through both Studio and admin API.

## Milestone 6 - Events and transactional email

Deliverables:

- event lifecycle, person relationships, public detail/list, database event import, and calendars;
- accountless verification, confirmation, management/cancellation, attendance, and privacy flow;
- versioned Studio/API email templates and preview/test/publish/rollback;
- durable email delivery/attempt/event/suppression model and SES adapter;
- event and course message purposes, bulk operation resources, delivery diagnostics, and alerts;
- SES development safeguards and one controlled real delivery smoke test.

Exit gate: registration, course communication, event changes, retries, provider events, ambiguity, suppression, and role/PII controls pass fault-injection and browser tests.

## Milestone 7 - Full rehearsal and performance

Deliverables:

- fresh content sync and final course database rehearsal;
- full URL/link/SEO, accessibility, security, privacy, API parity, load, backup/restore, and failure-mode reports;
- production-shaped Terraform plan and runbooks;
- data freeze/delta-import procedure;
- DNS/edge cutover procedure, lowered TTL where useful, smoke checklist, owners, and rollback triggers;
- Search Console/sitemap preparation without submitting development URLs.

Exit gate: all release criteria pass on `web.dtcdev.click`, open exceptions have explicit owner acceptance, and restore/rollback rehearsals succeed.

## Milestone 8 - Production cutover

1. Keep old static sites and course platform serving while the final content sync and database delta import run with outbound email disabled.
2. Reconcile data counts, checksums, scores, certificates, links, routes, and active content commits.
3. Enable the new production stack behind its edge endpoint and run internal smoke tests.
4. Switch canonical DNS/edge routing while retaining legacy course-host compatibility; deploy the redirect Lambda only after its browser/API consumer gate passes.
5. Enable workers and outbound transactional email exactly once after outbox reconciliation.
6. Submit the production sitemap and monitor errors, crawlers, registrations, enrollments, queue state, email, and top landing pages.
7. Keep legacy artifacts and databases read-only through the agreed rollback window.

Permanent redirects are enabled only after destinations pass production smoke tests.

## Rollback

Preferred rollback is an immutable application-image rollback with the new database and dynamic endpoints retained. Database changes follow expand/migrate/contract rules so the previous release can read newly written data.

Rollback must account for registrations/enrollments written after cutover:

- never point the entire site back to static hosting if that removes dynamic endpoints;
- keep registration, account, course, Studio, API, and webhook paths routed to a compatible Django revision;
- pause/reconcile workers before changing revisions;
- preserve idempotency/outbox state so two revisions cannot send the same message;
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

## Documentation and handoff

Each milestone updates:

- operator and incident runbooks;
- content schema/authoring docs and edit-on-GitHub guidance;
- Studio/API user guide and generated OpenAPI;
- course/cohort workflow and migration mapping docs;
- privacy/retention and data-flow inventory;
- deployment, rollback, backup, and restore docs;
- accepted departures from these specs and their rationale.

## Completion definition

The project is complete only when the unified production site is stable, management parity is verified, legacy course writes are disabled, link/SEO monitoring shows no unexplained regression, and rollback/retention obligations have passed their agreed window.
