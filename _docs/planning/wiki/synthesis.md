# Synthesis

## Thesis

- [HUMAN] DataTalks.Club becomes one Django application containing the current public website, docs, FAQ, Podwiki, event registration, and the existing course-management platform.
- [INFERENCE dtc-main-site,dtc-docs,dtc-faq,dtc-podwiki] This is a compatibility migration before it is a redesign: current paths, fragments, assets, machine-readable endpoints, link destinations, metadata, and structured data form a public contract and need an automated parity gate.
- [HUMAN] GitHub remains the editorial source of truth for blog, podcast, docs, FAQ, Podwiki, people, and other migrated editorial content.
- [INFERENCE aisl-reference,github-webhook-validation] Django should atomically publish validated, commit-addressed read models from signed webhook, scheduled reconciliation, and manual Studio sync paths; a public request never waits for GitHub.
- [HUMAN] The current DTC course Django code is copied at a clean recorded commit and evolved in place rather than reimplemented.
- [INFERENCE dtc-course-platform] The smallest safe course-domain change adds a reusable Course parent and evolves each current edition-like Course row into a Cohort while keeping curriculum, enrollment, submissions, review, scores, and certificates cohort-owned.
- [HUMAN] Every management capability is available through Studio and an admin-only API.
- [INFERENCE aisl-reference,dtc-course-platform] Studio and API parity should be structural: a capability registry maps both interfaces to one permission-aware, audited, idempotent application service rather than duplicating behavior.

## Evidence that shapes the design

- [FACT dtc-main-site] The main repository contains 55 posts, 206 podcast files, 99 books, and 439 people profiles, and its renderer includes raw HTML, Liquid, Kramdown attributes, JavaScript integrations, MathJax, and charts.
- [FACT dtc-docs] Docs use title-based hierarchy plus pretty `/docs/` URLs, edit-on-GitHub links, heading-aware search, Mermaid, callouts, raw HTML, and Liquid URL helpers.
- [FACT dtc-faq] FAQ exposes 1,395 records, stable ten-character fragment IDs, and JSON endpoints whose exact schemas are already public contracts.
- [FACT dtc-podwiki] Podwiki has 282 public pages plus custom chips, timestamped citations, graph hash links, filters, JSON assets, and cross-site canonical relationships.
- [FACT dtc-main-site] `Person.short` is the stable key reused across authors, podcast guests, event speakers, and maintainers; roles are therefore relationships rather than exclusive profile types.
- [FACT dtc-course-platform] The course application already covers registration, enrollment, homework, projects, peer review, scoring, leaderboards, complaints, certificates, communications, and operational management.
- [FACT dtc-course-platform] About 760 Django tests and 48 end-to-end tests provide a characterization baseline for the course adoption, including corner cases that a clean-room rewrite could miss.
- [FACT dtc-course-platform] Some important uniqueness rules currently live in view logic, so data preflight and expand-and-contract migrations must precede new database constraints.
- [FACT aisl-reference] The reference implementation demonstrates commit-aware content sync, separate Studio behavior, hashed operator tokens, OpenAPI coverage, jobs, email/event patterns, and AWS deployment separation.
- [FACT dtc-aws-infra] The sandbox account is `817685572750`, the default application region is `eu-west-1`, and the delegated `dtcdev.click` zone has the explicit ID `Z05963572WVWFHDQZH5NE`; another same-name zone makes name-only lookup unsafe.

## Resulting boundaries

- [INFERENCE dtc-main-site,dtc-docs,dtc-faq,dtc-podwiki] Each content repository needs its own parser/renderer adapter behind a common release model. One generic Markdown pipeline would break known behavior.
- [INFERENCE dtc-main-site,dtc-docs,dtc-faq,dtc-podwiki,aisl-reference] Candidate syncs are fully parsed, rendered, linked, and validated before one database transaction activates them; failures leave the last known good release serving.
- [INFERENCE dtc-main-site] Public Person records may be linked to staff users, but public identity never grants authorization. A person's guest/speaker/host/author roles are derived from explicit ordered relationships.
- [INFERENCE dtc-course-platform] Course/cohort data is operational database state, not GitHub content. Existing Django app labels, migration history, behavior, compatibility routes, and tests should survive the initial copy.
- [INFERENCE dtc-course-platform] Curriculum versioning is deliberately deferred. Keeping separate cohort-owned curriculum rows and adding complete cohort duplication avoids expanding the migration-critical path.
- [INFERENCE django-email-6,aisl-reference] Django's email backend sits below a durable application outbox that records delivery intent before provider calls and exposes retries, ambiguous acknowledgement, suppression, and provider events.
- [INFERENCE django-security-6,aisl-reference] Public forms need verification, CSRF protection, throttling, and minimal PII; Studio needs MFA-backed identity, object-scoped permissions, reauthentication for sensitive actions, no-store responses, and complete audit records.
- [INFERENCE dtc-aws-infra,aisl-reference] Development should use an immutable web/worker image, PostgreSQL, ALB/TLS, ECR, secrets, logs/alarms, backups, and Terraform variables/modules that can later instantiate a production-account stack.

## Migration strategy

- [INFERENCE dtc-main-site,dtc-docs,dtc-faq,dtc-podwiki] Crawl the generated sites first and store a route/link/SEO manifest. Development uses production canonicals but is blocked from indexing.
- [INFERENCE dtc-course-platform] Copy the course source and make its existing tests pass inside the unified project before structural edits. Then add a temporary CourseFamily parent, backfill a human-reviewed edition mapping, and use migration state/database operations to reach Course plus Cohort without re-creating course data.
- [INFERENCE dtc-course-platform] Keep `courses.datatalks.club` serving compatibility views until browser and authenticated API consumers have migrated. Only then deploy a Terraform-managed redirect Lambda using an explicit path map and query preservation.
- [INFERENCE aisl-reference] Deliver vertical slices to `dev.dtcdev.click`, use immutable content releases and application images for rollback, and do not retire the old sites until their individual compatibility gates pass.

## Dissent and unresolved choices

- [OPEN] Studio can either remain read-only for GitHub-authored content in the MVP or gain GitHub App branch/PR authoring. The plan recommends sync/preview/diagnostics plus edit-on-GitHub first.
- [OPEN] Staff identity provider, production email addresses/provider, privacy retention, legacy timezone, and infrastructure cost posture require owner confirmation before production data or traffic.
- [OPEN] Authenticated course API consumers must be discovered from access logs and ownership records before the old hostname becomes redirects.
- [OPEN] Course-family mapping for unusual/one-off legacy editions requires a reviewed mapping file; year stripping is not authoritative.
- [OPEN] PostgreSQL should replace the separate Podwiki search backend only after relevance and public-contract parity tests pass.
