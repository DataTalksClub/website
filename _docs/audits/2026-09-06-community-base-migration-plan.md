# DTC website migration to community-base

Date: 2026-09-06. Plan owner: DataTalksClub/website issue #317.

This is a delivery plan, not evidence that the website has adopted the package. The first execution task installs only the released kernel. Runtime settings, jobs, mail, Studio, content sync, accounts, events and courses move in separately reviewed issues.

## Authority, baseline and release gates

- [`_docs/PROCESS.md`](../PROCESS.md) controls issue grooming, engineer ownership, independent tester, independent PM acceptance, engineer commit, orchestrator local `--no-ff` merge/push, and on-call CI observation. There are no site pull requests. Each role posts its own issue report.
- The normative DTC specifications are [`01-platform-architecture.md`](../specs/01-platform-architecture.md), [`02-url-link-seo-compatibility.md`](../specs/02-url-link-seo-compatibility.md), [`03-github-content-and-people.md`](../specs/03-github-content-and-people.md), [`04-courses-and-cohorts.md`](../specs/04-courses-and-cohorts.md), [`05-events-registration-email.md`](../specs/05-events-registration-email.md), [`06-studio-and-admin-api.md`](../specs/06-studio-and-admin-api.md), [`07-security-privacy-operations.md`](../specs/07-security-privacy-operations.md), [`08-aws-development-terraform.md`](../specs/08-aws-development-terraform.md), [`09-migration-rollout-roadmap.md`](../specs/09-migration-rollout-roadmap.md), [`10-verification-strategy.md`](../specs/10-verification-strategy.md) and [`open-decisions.md`](../specs/open-decisions.md). These paths are relative to this audit.
- Shared decisions and package boundaries are at https://github.com/DataTalksClub/community-base/blob/main/docs/01-decisions.md and https://github.com/DataTalksClub/community-base/blob/main/docs/02-architecture.md. Execution and stop rules are `docs/03-playbooks.md` and `docs/04-quality-gates.md` in that repository. Canonical issue dependencies are in https://github.com/DataTalksClub/community-base/tree/main/docs/plan.
- The canonical D0.1a/b/c/d split and no-PR correction are merged in [community-base #111](https://github.com/DataTalksClub/community-base/pull/111), on package main `1e1102a`. D0.1 is complete only after D0.1d; D0.1a has no dependency on the D0.1 umbrella. Refresh this status before D0.1a runtime work.
- Research baseline: DTC local main `feb4a72303ad75fc8f2df1684aab33d9bf7c24d9`, clean and ahead of origin/main by 173 commits at inspection. This plan does not authorize rewriting or discarding those commits. Re-record the merged baseline before every implementation; branch drift invalidates inventories.
- Published package v0.3.0 includes kernel/config/API/jobs/mail/Studio/content sync. Package main also contains newer domain capability work with provisional kept-label migrations. Never use package main, a branch, or an editable source as a deployed dependency.
- C3.7 requires prepared AISL and DTC identity donor evidence; C4.3 requires the prepared AISL events donor. C5.3 v0.6.0 is the future adoption-ready domain release, after those checkpoints and C5.2. At inspection curriculum/coursework directories do not yet exist in the package checkout. No task may assume an absent package API exists.
- A successful synthetic fixture, package test or published foundational release does not prove donor migration compatibility, real Relay conformance, a development deployment, or D13 production duration. Record unavailable evidence as `Not run here, needs: <owning issue and exact evidence>`.

## Current source compared with the original shared analysis

| Capability | Verified current source | Consequence |
|---|---|---|
| Accounts | `accounts/models.py:CustomUser`, `AccountIdentityAlias`, `AccountIdentityQuarantine`, `AccountReconciliationRun`, `CmpLearnerImportProgress`; AUTH_USER_MODEL in `website/settings/base.py` is accounts.CustomUser | Preserve legacy/active/quarantined/absorbed identity states, active normalized-email uniqueness, survivor mappings, password hashes, social identities, permissions and learner references. |
| Membership | Spec01 and spec06 already define private MemberProfile, Slack grants, revisioned self API and management behavior. No `class MemberProfile`, `class SlackAccessGrant` or `class EmailDelivery` was found in current source. CustomUser still carries country, region, registration_role, profile links, about_me, certificate name, timezone, newsletter_subscribed and home_dismissals. | Treat the rich membership contract as required target behavior. Do not invent existing rows, mark spec-only work implemented, or copy profile projections into confirmed completion automatically. Re-inventory when its separate issue lands. |
| Events | `events/models.py` contains Event, EventContent, EventSpeaker, EventLink, EventAlias, five Q&A models, historical source/revision/slot/displacement/total state, EventRegistrantIdentity, EventRegistration, EventRegistrantInterestSignal, EventRegistrantImportProgress. | Events are already DB-owned. D4 is a lossless replacement of an active domain, not simply a one-time GitHub import. Include current registrants, provenance and aggregate arbitration. |
| Event identity | `events/identity.py:resolve_public_id`, `resolve_uuid`, `resolve_legacy_path`, `create_event_identity`; spec05 requires immutable UUID management identity and numeric public identity | Package integer primary keys require an explicit site UUID-to-package-ID extension. Management APIs stay UUID-addressed and old public aliases remain one-hop redirects. |
| Relay | `email_app/urls.py`, `views.py`, `relay_links.py`, `services.py`; model PendingUnsubscribe | Anonymous tracking and unsubscribe bridge exists. This is not proof of a package sender, production jobs cutover or four clean production weeks. |
| Jobs | `jobs/models.py:DurableJob`, WorkerHeartbeat, SchedulerLease; dispatch.py, execution.py, registry.py, schedules.py; django_q remains installed | Inventory both local durable execution and qcluster/scheduler behavior. Preserve dedupe, leases and recovery while changing transport. |
| Settings | `core/models.py:OperationalSetting`, `AuditEvent`, `IdempotencyRecord`; `settings_batch.py:update_settings`, `SettingsRevisionConflict`; operational/site settings services | Preserve atomic grouped updates, revision checks, idempotency, capability enforcement and audit; a direct key/value copy alone is insufficient. |
| Content | `content/models.py:ContentSource`, ContentRelease and active-path state; `content/services.py` includes sanitization and domain DTOs; `content_sync/dtc_content/`, `legacy_main/`, course repository adapters | Decision #226 targets direct upsert, but the current source still has a release pipeline. Remove only explicitly retired symbols and preserve safe publication, source provenance, namespace collision handling and edge invalidation. |
| Courses | `courses/models/cohort.py`, curriculum.py, homework.py, project.py, wrapped.py, curriculum_import.py, cmp_import.py; `courses/course_family_catalog.py` | Preserve current cohort ownership, import provenance, registration counts, read state and all assessment history, not only the original model list. Keep nonempty extension apps. |
| Public design and operations | DTC public inline design, cache/security boundary, immutable compatibility files, change-selective CI, deployment workflows | Shared public templates must preserve DTC design and route policies. Studio alone adopts the package Tailwind shell. Package instructions do not replace local review/deploy gates. |

No credentials, recipient addresses, tokens, user free text, production snapshots or rendered email bodies go into the plan, logs or issue reports. Synthetic fixtures use nonidentifying values; report user IDs only.

## Work structure and tracking

The D0-D5 IDs below are canonical parent issues. Letter-suffixed work packets are proposed splits until added to the shared phase file and synchronized into STATUS.md. File the canonical amendment first. Do not pretend a proposed child ID is already a completed canonical issue.

For every packet: create one DTC issue; PM grooms; use one named engineer and isolated worktree; freeze its uncommitted handoff for a separate tester; PM accepts only after tester passes; engineer commits with `Closes #N` or `Refs #N`; orchestrator merges/pushes; on-call alone observes CI/development deployment. Record the site issue URL and status in a separate status-only community-base PR in the same session. Site `done` requires green development deployment when runtime changes are involved.

Suggested sequence:

1. Approve this plan and the canonical D0 split/process corrections.
2. D0.1a kernel bootstrap; D0.1b settings parity inventory; D0.2 pin advisory automation can follow independently.
3. D0.1c settings copy/cutover, then D0.1d contraction after rollback window.
4. Relay R1 prerequisites run in their own repository/process. D1 inventory/fixture preparation may proceed without live Relay; live cutover remains blocked by conformance and deployment evidence.
5. D2 Studio and content parser packets can proceed once their released package interfaces and DTC dependencies are satisfied.
6. Complete package C5.2, then D3.1 donor preparation; feed C3.7. AISL supplies its independent donor preparation and C4.3 evidence. Only after C5.3 release do D3.2, D4 and D5 runtime adoption.
7. Begin D13 evidence at confirmed DTC production Relay cutover, while later DTC domain adoption proceeds. Hand off the complete four-week record to Relay R6.1/AISL after D5.2.

Parent coverage and completion gates:

| Parent | Bounded packets | Parent completion gate |
|---|---|---|
| D0 | D0.1a, D0.1b, D0.1c, D0.1d, D0.2 | D0.1 completes only after D0.1d; D0.2 remains independently gated by D0.1a |
| D1 | D1.1a, D1.1b, D1.1c, D1.2a, D1.2b, D1.2c, D1.3 | jobs and mail cutovers plus Relay conformance/deployment evidence |
| D2 | D2.1a, D2.1b, D2.2a, D2.2b, D2.2c, D2.2d | every Studio route and content source has an owner and verified parser/release outcome |
| D3 | D3.1a, D3.1b, D3.1c, D3.2a, D3.2b, D3.2c | identity donor evidence, extensions, shared accounts and onboarding/community checks |
| D4 | D4.1a, D4.1b, D4.1c, D4.1d, D4.2 | lossless event data/URL/registration proof and freeze deployment |
| D5 | D5.1a, D5.1b, D5.1c, D5.1d, D5.1e, D5.2 | curriculum/coursework parity, self-paced proof and freeze handoff |

D0.1a/b/c/d are registered canonical child issues. All other letter-suffixed
packets in this document are proposed planning splits until they are added to the
canonical shared phase file and synchronized into `docs/plan/STATUS.md`. No
packet may be selected by its proposed label before that registration.

## Verification contract used by every packet

These commands are executable in this repository today:

```text
make verification-plan VERIFY_ISSUE=<issue-number>
make verification-run VERIFY_ISSUE=<issue-number>
make verification-evidence-check VERIFY_PLAN=.tmp/verification/verification-plan.json
make verification-report-check VERIFY_PLAN=.tmp/verification/verification-plan.json VERIFY_REPORT=.tmp/verification/verification-report.json
make verification-plan VERIFY_CONSUMER=tester VERIFY_ISSUE=<issue-number>
make verification-run VERIFY_CONSUMER=tester VERIFY_PRODUCER_ROLE=tester VERIFY_PHASE=tester VERIFY_ISSUE=<issue-number>
```

Substitute the assigned numeric issue ID. The engineer and tester record exact base/head, observed status, graph digest, plan digest, commands/counts, artifact paths/digests and each rerun/reused/skipped/not_applicable component. The tester recomputes from the engineer's frozen base/head and explains drift before continuing. No required skip or pending screenshot may survive tester-final. Changed inputs invalidate previous evidence.

The graph in `ci/ownership.json` determines the necessary scope. A config/dependency/migration change can force broad verification. A docs-only plan may classify product screenshots not_applicable; a runtime backend issue requires the applicable smoke tier. Ordinary rendered impact uses `make test-playwright-core`; template/browser-harness changes use `make test-playwright`. Only the tester's captured and inspected desktop/mobile images satisfy screenshot gates. Add graph edges and policy tests when adding app owners; do not weaken the selector.

Focused commands named in packets supplement this contract:

```text
uv run python manage.py check --settings=website.settings.test
uv run python manage.py makemigrations --check --dry-run --settings=website.settings.test
make check-management-parity
make check-openapi
make test-ci
make test-migrations
```

Expected: checks clean, no migration drift, generated schema/registry parity unchanged except reviewed additions, test gates exit zero. Bare repository-wide `uv run pytest -q` in old canonical steps is not a substitute for this repository's guarded runner and graph. Run focused labels through `uv run python manage.py test <labels> --settings=website.settings.test` or a verified explicit pytest file path where supported; the generated plan remains authoritative.

### How to judge results

A packet is green only when its exact base/head, graph and plan digests are
recorded, the generated command set is run, and each command exits with the
stated result. The expected fixture or permitted development copy is the oracle:
compare before/after counts, stable IDs, foreign keys, timestamps, auth state,
URLs, permissions and provider ownership, and explain every difference. Check a
positive path and each named denial, invalid input, duplicate, retry, outage or
rollback case; a zero exit alone is insufficient. For a changed page, the
independent tester captures and reads desktop and mobile screenshots for the
specified route and state, and an error page or broken layout fails the packet.
For docs/tooling-only work, product screenshots are `not_applicable` only when
the graph confirms no render impact. Reused evidence is valid only with its
validated envelope, artifact digest and unchanged inputs; otherwise rerun it.
Missing, guessed or invalid evidence is recorded as `Not run here, needs:` with
the owning issue or operator and exact evidence required.

For every schema/data issue add a fixture-backed, rerunnable management command or migration test with explicit old-to-new field map before copying real data. Rehearse against an operator-provided development-only local copy using the site's approved settings. Use exact `COUNT(*)` and stable non-secret field/FK checksums rather than pg_stat estimates. Repeat import: zero new rows, unchanged checksum. Test invalid records: zero partial writes. Reverse and forward migrations or documented irreversible boundary must be tested. Fresh SQLite and synthetic PostgreSQL coverage plus development-copy evidence remain separate. Artifacts belong under `.tmp/`, never `/tmp` or committed data files.

A packet is blocked when it lacks any required tag, API, schema mapping, role gate, freeze, development-copy evidence, or conformance result. Twice-failing verification after a genuine fix attempt, row loss, altered canonical decisions, donor migration-name mismatch, provisional-release attempt or production-data/credential need triggers the shared stop rule. Record the exact blocker; do not improvise.

## D0: install and configuration

### D0.1a Released kernel bootstrap

Depends on C2.4 release v0.3.0 and approved canonical split. Freeze: no. First runtime execution packet.

This packet is DTC issue [#318](https://github.com/DataTalksClub/website/issues/318),
the first runtime task. It installs the released kernel and dependency/tooling
guard only; settings migration, app installation, routing, schema changes and
Relay credential use belong to later packets.

Read: pyproject.toml, Makefile, website/settings/base.py, core/tests/test_settings.py, ci/ownership.json, [`_docs/ci/change-selective-ci.md`](../ci/change-selective-ci.md); package v0.3.0 `community_base/kernel/apps.py`, `community_base/kernel/access.py` and `community_base/kernel/conf.py`.

Write: dependency manifests, local link targets, fail-closed pin guard wired into existing checks, base settings and one meaningful core integration contract. New helper files must have a real invoked use.

Steps:

1. Verify published tag and release asset. Add community-base>=0.3.0,<0.4 with git source tag v0.3.0; run `uv lock` then `uv sync --frozen`.
2. Add only community_base.kernel.apps.KernelConfig. Add COMMUNITY_BASE SITE_KEY=dtc, RegisteredOnlyPolicy, JOBS_BACKEND=relay, MAIL_BACKEND=relay, STUDIO_TITLE=DataTalks.Club Studio. These declarations cause no network call or switch of existing jobs/mail.
3. Implement `make core-link`/`make core-unlink` with clean dependency-file guard and exact restoration of captured manifests/lockfile under `.tmp/`. Refuse pre-existing edits, repeated snapshots and missing recovery state. A temporary link must never destroy a caller's dependency changes.
4. Parse TOML/lock sources for the pin guard; reject local/editable, branch, floating and mismatched sources. The old canonical `grep && exit 1 || true` sample always returns success and must not be copied.
5. Prove cb_kernel loads, version 0.3.0, anonymous denied level 5, authenticated allowed level 5, paid levels denied; AUTH_USER_MODEL remains accounts.CustomUser and no new migrations arise.

Commands: `uv run python -c 'import community_base; print(community_base.__version__)'` prints 0.3.0; system/drift checks above pass; link then guard fails, unlink then guard passes and manifests match before bytes. Perform roundtrip in an isolated scratch copy if the implementation has legitimate uncommitted manifest edits. Run the versioned verification plan and independent gates.

Non-goals: installing config/API/accounts/events, route changes, data copying, Relay credentials, production operations. Rollback: revert approved dependency/settings/guard commit before any later packet depends on it; resync previous lock.

### D0.1b Settings parity inventory

Depends on D0.1a. Freeze: no. Outputs a reviewed mapping and synthetic parity tests; does not switch live readers/writers.

Read/write boundary: core/operational_settings.py, operational_settings_service.py, site_settings.py, settings_batch.py, models.py; studio/views.py and urls.py; management_registry.py; management_api/urls.py; core/tests/test_site_settings.py and test_settings.py.

Inventory every declared key/type/default/env/settings fallback, validator, secret treatment, source badge and public consumer. Inventory OperationalSetting revision, IdempotencyRecord behavior, AuditEvent field policy, grouped all-or-nothing updates, reason requirements and stale revision response. Compare exact v0.3.0 package registry/service/API behavior. Classify every row as shared parity, DTC adapter or blocking package gap; completeness means every key, callsite and response field has one disposition. An unknown or unclassified entry fails the packet and returns it to grooming.

Verification: focused settings tests plus management/OpenAPI checks; synthetic current and target examples have equal typed values, validation failures, secret masking and atomicity. Wrong role is denied; stale batch returns conflict with zero writes. Output concrete D0.1c file/migration plan and package blockers. Rollback: remove nonruntime inventory/test harness if abandoned.

### D0.1c Copy settings and switch guarded adapters

Depends on D0.1b and published release resolving all shared parity blockers. Freeze: no, with explicit deployment write coordination.

Create core/settings_keys.py imported by core/apps.py; install cb_config/cb_api. Add a core historical-model data migration using a fixed field mapping; preserve audit history rather than turning old/new sensitive values into log text. Mount package /api/v1/settings endpoints without shadowing /api/v1/admin or session /api/v1/me. Keep old Studio/admin paths as compatibility adapters until intentional deprecation; do not remove role/revision semantics.

Before switching the settings UI, prove the released package's settings
template, template tags and route dependencies in an installed-tag fixture with
the minimum shared Studio shell. If that fixture needs the full shell migration,
record a package or D2.1 prerequisite and keep the old settings UI. Switch
readers and writers in one reviewed deployment boundary; retain old tables
read-only for a bounded rollback window. Rehearsal verifies key counts/types and
known values, masked secret roundtrip, historical audit linkage, no partial
batch commits, stale revision rejection and idempotent copy. Run focused
settings, management/OpenAPI, full graph and desktop/mobile settings screenshots.
Operator can edit an allowed key and see it on public consumer; unauthorized
staff cannot gain API or settings access.

Rollback: restore old reader/writer only after confirming no divergent writes; use the reviewed reverse mapping for post-copy changes. Never blindly repoint at stale settings rows. Do not contract yet.

### D0.1d Remove retired settings storage

Depends on D0.1c green development deploy and reviewed rollback-window closure. Remove only inventoried migrated models/shims/functions; retain core audit/idempotency/sponsor/navigation/operation primitives and site-specific consumers. Add old-table removal migration, preserve historical migration imports, update app boundaries and spec06.

Verification: exact retired-symbol search excluding historical migrations is empty; parity tests and graph pass; no lost audit records. A remaining reader or unmapped revision behavior blocks removal. Rollback beyond table removal requires the documented development backup/reverse migration path, not a package pin change alone.

### D0.2 Reviewed update advisory

Depends on D0.1a guard. Implement `.github/workflows/bump-community-base.yml` only after canonical no-PR correction: discover compatible published tags and open a deduplicated issue or emit an advisory for ordinary grooming. Never auto-switch tags or create a site PR. Existing guard stays authoritative; release lookup failure is actionable, not 'already latest'.

Verify workflow contract tests, manual invocation with no update -> 'already latest'; synthetic newer compatible version -> one issue/advisory; rerun -> no duplicate; provisional/adoption-gated release -> blocker. No public render change; on-call owns post-push CI.

## D1: jobs, mail and Relay proof

### D1.1a Handler, schedule and durable-state inventory

Parent D1.1; preparation only until its canonical split permits earlier work. Read jobs/models.py, dispatch.py:dispatch_after_commit, registry.py, execution.py, heartbeat.py, scheduler.py, schedules.py, tasks.py, all importing apps, deploy/task_definitions.py and deployed settings.

Write an exact handler-name/schedule/payload/state/dedupe/lease matrix with consumer source paths, queued/running/dead disposition, timeout limits, completion semantics and owner. Do not assume a small queue or equate old job IDs with package IDs. Find every qcluster/worker/scheduler entry point and existing Datamailer coupling. Include uninstall criteria and a no-dual-executor drain protocol.

Verify `uv run python manage.py test jobs --settings=website.settings.test` plus graph; record handler/schedule counts and one test for after-commit dispatch, transaction rollback, retry and lease fence. No network needed. Output D1.1b mapping and unresolved R1.2 contract fields.

### D1.1b Fixture-backed package jobs adapters and state transfer

Depends on D1.1a, C1.5-or-later compatible release; live activation also needs R1.1/R1.2 green. Own site handler adapters, new migration/copy command, settings/URLs/middleware registration and deployment schedule step.

Register every handler using the same name and opaque scalar payload. Preserve dedupe keys, due times and retries; explicitly reject/import quarantine malformed payloads. Running jobs drain before copy or use a reviewed stopped-state handoff. Do not mark an ambiguous provider side effect retryable. Install cb_jobs, use synthetic backend in tests, and keep runtime backend switch gated. New signed ingress must be session-free and bounded by existing core middleware policy; test bad signature, stale timestamp, duplicate delivery, attempt replay and oversized payload.

Copy fixture pending jobs once; second copy no new intents. Transaction rollback produces zero runnable jobs; old and new executors cannot both claim one side effect. Tests cover recovery after death and leased callback timeout. No delete of old tables until state reconciliation.

### D1.1c Development jobs cutover and old-runner removal

Depends on b, real R1.1/R1.2 conformance and coordinated development deployment. Configure endpoint/tenant credential references through deployment secret names without reading values. Migrate, drain/fence old execution, register package schedules, switch ingress, remove worker/qcluster containers and old scheduler only after tests prove all owners moved.

`uv run python manage.py sync_relay_schedules --dry-run` after real synchronization -> no diff; `uv run python manage.py jobs_ingress_selftest` in approved development runtime -> OK; one due schedule reaches succeeded within its interval. Current deployed task definitions contain no qcluster/old job runner. Site jobs diagnostics preserve dead/retry visibility. Record exact deployment and Relay conformance evidence.

Rollback stops new dispatch/schedules, drains or classifies in-flight attempts, restores previous executor/pin only from reconciled states; never replay an ambiguous accepted job. Canonical D1.1 completes only after this deployment gate.

### D1.2a Purpose and legacy-outbox map

Depends on D1.1 inventory; no live send. Read email_app, course_management/datamailer_outbox*.py, course_management/datamailer/, data/models.py, studio_courses Datamailer views, courses/deadline_reminder_*.py, accounts email preference code, and spec05.

For each producer record purpose, trigger, recipient owner, category, template key/version/context schema, existing dedupe key, retry/ambiguous-ack behavior and read-only historical dependencies. Classify every Datamailer table/read/write: sender to replace, input/history to retain until approved import, dead code safe to remove. Preserve current newsletter_subscribed values; don't infer marketing consent from course/event registration.

Map PendingUnsubscribe and recipient-link contract exactly: /t/o/<token>.gif always valid GIF under failure, /t/c/<token> only redirects on Relay verdict, /unsubscribe/<token> persists/replays when unavailable, no configured Relay -> 404, session-free/no Set-Cookie/no recipient token logging.

### D1.2b Mail intent and bridge adapters

Depends on D1.1 jobs handoff, a, released package API, and R1.3/R1.4/R1.5 contract proofs for live activation. Install cb_mail; preserve existing link paths and all tests under email_app/tests. Reuse transport solely through package send with stable old idempotency keys. Map pending unsubscribe through reversible copy; replay existing rows once.

Create reviewed email_templates markdown source and catalog manifest as a future output, not an assumed current directory. Publish only after real catalog validation; local tests use fakes. Resolve Slack/invite context inside a leased handler after commit, not persisted secret-bearing bodies. Provider acceptance remains distinct from delivered; duplicate/out-of-order callback event IDs cannot regress terminal projections or enqueue duplicate sends. Implement reconciliation for ambiguous acknowledgement.

Verify purpose fixture coverage, wrong/missing context denial, preference suppression, callback duplicate/out-of-order behavior, bridge outage contract, redaction and one intent per dedupe key. Focused email_app/courses/jobs tests plus generated graph. Browser tests cover click failure confirmation and unsubscribe success during outage without exposing tokens.

### D1.2c Development mail cutover and legacy contraction

Depends on b and real Relay tenant/template/callback/preference conformance. Deploy published template versions with non-secret manifest; use owner-controlled synthetic development recipient through the approved workflow. Course confirmation -> one intent -> delivered callback. Open/click/unsubscribe routes remain compatible. Retire old sender calls; keep historical inputs until their count/checksum/import acceptance passes. Delete data/Datamailer tables only in a separately reversible or explicitly irreversible contraction migration after rollback window, not because a grep still finds a history import.

Canonical old blanket 'no Datamailer code' must be reconciled with DTC's authoritative read-only historical-input contract before deletion. Update spec05 ownership only for verified behavior. Rollback preserves accepted/ambiguous send state and never resends merely because transport changed.

### D1.3 Controlled production Relay transition

Depends on complete D1.1/D1.2 and R1 prerequisites, announced freeze, recent development rehearsal, normal site production promotion and owner-operated checks. Agents prepare changes and synthetic evidence; production credentials/data remain outside agent access.

Owner/on-call evidence: signed ingress selftest OK, scheduled job succeeds, one approved transactional send delivered, Relay status contract green and no dead intents, runtime definition contains no worker. Record exact DTC/Relay release SHAs, environment identity, UTC traffic start, schedule/send evidence IDs and incident owner. Do not assume DTC is still pre-apex; resolve current deployed host through existing deployment authority.

Rollback uses existing reviewed deploy workflow and reconciled queue/mail states. Status 'production proof started' is distinct from canonical phase complete and D13 proven.

### Relay dependency and D13 handoff

R1.1 production environment/tenant/SES delivery, R1.2 webhook leases/retry/concurrency, R1.3 immutable catalog/preview/context, R1.4 signed callbacks/reconciliation and R1.5 categories/double opt-in each need a versioned real-service conformance report. Link bridge availability or package fixture tests never close them.

After D1.3 record at least four consecutive weeks of DTC production Relay traffic. Evidence ledger fields: UTC start/end, exact DTC/Relay release identities and tenant reference without credentials, each status contract interval and source, incident ID/severity/attribution, gap or failure, reviewer and acceptance date. A Relay-attributed P1 resets the consecutive interval; a missing/unknown status interval cannot be asserted green. Report seven green days for phase1 exit separately from the four-week D13 gate.

At D5.2 completion hand off ledger to R6.1 and AISL plan owner. R6.1 also needs its canonical D5.2 dependency. AISL keeps django_q/ses_local until that gate; site preparation and shared-code extraction do not imply permission for AISL to use Relay early. No agent fabricates four weeks of evidence from one status screenshot.

## D2: Studio and content sync

### D2.1a Staff route and authorization adapter

Depends on D0.1a, C2.4, and any released hook correction needed by DTC. Read `accounts/studio_authorization.py`, `accounts/studio_roles.py`, `accounts/studio_sessions.py`, `management_registry.py`, `studio/urls.py`, `studio_courses/`, `templates/studio/base.html`; [`06-studio-and-admin-api.md`](../specs/06-studio-and-admin-api.md) capabilities.

Inventory every Studio destination and API counterpart with route name, area, required capability, reauthentication, masking, reason/revision/idempotency guard and audit owner. Compare package released authorizer interface; a missing hook is a package issue/tag blocker, not license to use is_staff alone. Register site sections while keeping API capability registrations. Wrong-role direct URL, navigation-hidden-but-addressable route and expired/revoked staff session must all deny safely.

Verify focused studio/accounts authorization, `make check-management-parity`, `make check-openapi`, generated graph. No visual shell switch until all routes and state ownership are mapped.

### D2.1b Shared shell and site navigation

Depends on a. Install cb_studio; change Studio templates to extend community_base/studio/base.html; remove templates/studio/base.html after all references move. Register Site, Access, Audit, Events/Q&A/historical totals, Courses and current member/email capabilities without inventing unimplemented screens. Keep admin/API adapters until migration of each route.

`uv run python manage.py studio_routes --check` -> every route exactly owned/unlisted/section-only. Run Studio/courses focused tests, full browser tier and inspected desktop/mobile screenshots for shell, settings, event Q&A, courses, forbidden access and empty/error state. Scope public inline-style enforcement to public routes only; public base design/cache/navigation remain equivalent. Update design-system and spec06 ownership.

Rollback shell/template/registration commit as a unit while preserving backend URL compatibility.

### D2.2a Direct-upsert contract and article/people parser pilot

Depends on D0.1a, D1.1, D2.1a/b (or a separately reviewed minimal jobs and
Studio prerequisite), C2.4, and the approved canonical D2 split that preserves
current DTC publication guarantees. Read
`content_sync/dtc_content/{adapter,preparation,repository,contract,media,parity}.py`,
`content_sync/legacy_main/adapter.py`; `content/models.py`, `content/services.py`,
`content/public_routes.py`; `content/tests/test_activation_concurrency.py` and
`content/tests/test_sanitizer_concurrency.py`; [`02-url-link-seo-compatibility.md`](../specs/02-url-link-seo-compatibility.md),
[`03-github-content-and-people.md`](../specs/03-github-content-and-people.md) and
[`open-decisions.md`](../specs/open-decisions.md), decision #226.

First produce per-content-type source/identity/path/relation/asset/search/visibility map and exact fixture baseline. Completeness means every current type, field, relation, path and asset has a target, retained owner or blocking disposition; an unknown entry fails the packet. Install cb_content_sync and create DTC parser adapters for articles and people. Reuse domain sanitation/render validation rather than delete it. A parser consumes an immutable accepted checkout, writes DB rows transactionally, and never becomes a request-time file fallback. Keep other type owners unchanged.

Publish batch must preserve last-known-good public state on parser/path conflict; proposed direct-upsert transaction/namespace guard and durable edge invalidation must be specified and tested before replacing release activation. If package primitive lacks it, block that cutover on a package extension while retaining site orchestration.

Verify existing dtc_content fixture tests and content article/person/URL/SEO tests; dry fixture sync counts and identity/path/HTML-safe checksums equal baseline; second identical sync zero change; malformed/colliding input leaves previous public records unchanged. New people removal fails while live DB events/courses reference their short key.

### D2.2b Podcast and books parser ownership

Depends on a pilot accepted. Own new parser adapters plus podcast/books domain integration; keep audio/transcript links, episode redirects, book identities, host/guest/person edges, assets and HTML sanitization. Events are excluded because already DB-owned. Preserve public fixed paths, canonical URLs, feed schemas and private/draft filtering.

Verify content_sync/tests/test_dtc_content_adapter.py plus existing podcast tests, machine-contract samples and sitemap tests. Fixture counts/identities/paths match baseline; no events created, deleted or edited by editorial sync. Inspect desktop/mobile episode and book pages plus unavailable-asset state under full template tier if templates changed.

### D2.2c Docs, FAQ, podwiki and remaining editorial types

Depends on b. Map docs, FAQ sections/questions/JSON feed, Podwiki nodes/edges/citations/search, tools/conferences/legacy pages before retiring old adapters. Preserve fragment IDs from `_docs/compatibility/faq-fragment-contracts.jsonl` and podwiki-graph-fragment-contracts.jsonl, exact-path redirect/machine responses and search safe-query limits.

Verify corresponding fixture/parser/content search/FAQ/docs/wiki tests and compatibility samples; every current type classified; source replay zero changes; deletion policy never drops referenced operational identity. Failure retains last-good DB state and source provenance.

### D2.2d Retire release-only storage after all source owners migrate

Depends on c and migration of course-repository source references; D5 may own the final curriculum dependency. Inventory ContentSource FK consumers, ContentRelease, ActiveContentPath, FrozenReleaseChild and all services.py references before any drop. Keep sanitization, DTOs and unrelated content services. Keep courses' immutable source graph boundary; it must not import parser code.

Only drop genuinely unused release state with reviewed reversible migration and retained historical provenance. Runtime requests remain database-only with no hardcoded/file fallback. Verify content activation safety replacement tests, compatibility route/search/sitemap contracts, migration counts and generated graph. Update spec01 content refresh/spec03/app boundaries to final actual design. Canonical D2.2 cannot finish while any old source is active.

## D3: identity, onboarding and community

### D3.1a Exact donor field and migration inventory

Depends on C5.2 per canonical plan; research may precede but implementation waits. Read `accounts/migrations/`, `accounts/models.py`, `accounts/identity_resolution.py`, `accounts/backends.py`, `accounts/auth.py`, `accounts/services/social_connections.py`, `accounts/studio_authorization.py`, `accounts/studio_roles.py`, `accounts/studio_sessions.py`, `accounts/tests/`; `courses/registration.py`, `courses/models/` and [`single-durable-account.md`](../architecture/single-durable-account.md).

Create a field-by-field destination map: framework/shared identity; courses.LearnerProfile learner extension; accounts_ext.IdentityState and reconciliation/import records; other DTC-specific preference/home fields. Completeness means every source field, FK/M2M, credential, permission, migration and API surface has one shared, extension, retained or blocking disposition; an unknown entry fails the packet. Re-inventory MemberProfile/Slack grants and any mail intent implementation at actual donor SHA. Never create a second copy of a newly implemented profile or overwrite member confirmation.

Inventory every FK/M2M, auth/allauth/permission/contenttype/session relationship, custom backend, raw SQL, string model ref, historical migration import and API token. Resolve existing Token compatibility rather than delete live credentials. Record source migration names, schemas and exact test counts for C3.7. Explicitly test normalized-email uniqueness/absorbed/quarantine denial.

### D3.1b Expand extension models and redirect readers

Depends on a and reviewed field map. Create future courses.LearnerProfile and accounts_ext app with preserved OneToOne user IDs; move identity tables with database/state operations appropriate to exact old schema. Copy portable ORM values in batches, preserving NULL/blank distinctions, booleans, timezone and profiles; keep old columns through rollback window. Preserve identity uniqueness enforcement even when its inputs move between tables; absence of an equivalent DB/service invariant blocks cutover.

Refactor site readers/writers to extension APIs, not duplicate fields on shared User. Current signup closed behavior, social account linking and member-confirmed consent survive. Tests cover source and target row counts, one extension/user, rollback, absorbed accounts cannot authenticate, social identity ownership, current permission/session semantics, course registration and timezone/certificate settings.

### D3.1c Rename User and reconcile shared schema

Depends on b. Rehearse RenameModel CustomUser→User and accounts_user table change from actual donor migration history. Update AUTH_USER_MODEL and every swappable/relation/manager/backend reference coherently; never fake a migration simply to silence inconsistent history. Reconcile needed shared fields with documented defaults; copied legacy values do not count as verified profile completion.

Verify exact row/FK/M2M counts, password hash byte equality, allauth/permission/session compatibility, no migration drift, forward/reverse rehearsal and development login. Freeze final donor SHA/model state/migration names/tests and hand to C3.7. Stop if production credentials are required or `replaces` inventory differs.

### D3.2a Shared accounts installation under announced freeze

Depends on D3.1, C3.7 and published C5.3 v0.6.0. Follow P7 only after tested exact DTC migration-state procedure is approved. Shared accounts label ownership replaces local accounts; site extensions persist. Any django_migrations state edits/fake operations are isolated to the rehearsal-proven procedure, with before/after ledger and dependency checks. No general delete-all-history recipe.

Install accounts/questionnaires/onboarding/community/notifications/comments/voting only when each required migration is compatible. Preserve account routes, public base contract, route-cache privacy, verified-user ownership, tokens, anti-abuse and Studio capability rules. Registration enablement is an explicit groomed product criterion because current plain signup is closed.

Verify get_user_model module is the package; login/logout/password reset/social linking/staff denial/identity quarantine tests and inspected desktop/mobile forms pass. Synthetic foreign user cannot access another profile or notification. Rollback requires extension/schema compatibility and previous release image, not a pin alone.

### D3.2b DTC profile, default/learner flows and Slack

Depends on a, completed profile parity matrix and package/DTC privacy hooks. Configure default profile + Welcome questionnaire and learners profile + Course goals. Required values/version/member-confirmed revision follow spec01; email verified before completion. Preserve profile/Person separation, session GET/PATCH /api/v1/me/profile with CSRF+If-Match, management capability masking and correction reason, no bulk export.

First completion atomically grants once and enqueues one allowed Slack delivery intent; handler resolves current secret after commit. Public /slack landing remains; protected reveal URL /accounts/community/slack/ follows approved contract and no-store. Unverified/incomplete member cannot reveal. Failed/ambiguous email is not automatic resend; refresh completion creates no duplicate grant/mail. Registration resumes from a safe server-side path and cannot infer marketing consent.

Verify spec01/05/06 matrix and focused accounts/courses/email tests; desktop/mobile new/default/learner/resume/conflict/denial flows inspected. Do not claim profile fields missing from the package are supported; file shared issue/release first or an approved site extension.

### D3.2c Event/course comments, notifications and final contraction

Depends on b and exact target event/course object adapters (may wait for D4/D5). Define documented ownership for object references during transition; don't persist unstable source PKs into generic relations. Enable comments and event/cohort notifications per approved audience/privacy rules, test moderation, foreign-user denial and duplicate signal suppression.

Remove old accounts code only after all site-specific imports move; update normative docs for verified ownership. Canonical D3.2 remains open for any explicitly required event/course integration awaiting D4/D5; avoid inventing completion by installation alone.

## D4: lossless shared events adoption

### D4.1a Event schema, identity and current-registration map

Depends on published C5.3 for implementation; inspect earlier without installing untagged code. Read `events/models.py`, `events/identity.py`, `events/services.py`, `events/qna/services.py`, current registration/import modules located by `rg --files events`, `events/tests/`, [`05-events-registration-email.md`](../specs/05-events-registration-email.md) and [`event-qna-integration.md`](../architecture/event-qna-integration.md).

Map every field in Event/EventContent/EventSpeaker/EventLink and all registration/Q&A/history/import models. Completeness means every field, FK, route, alias, permission and aggregate has one target, retained owner or blocking disposition; an unknown entry fails before a table is rebuilt. Preserve legacy UUID in future site identity extension with unique OneToOne shared Event; preserve public_id immutable positive and same value; slug and approved aliases remain. Site management routes accept UUID only. Host external_ref preserves canonical Person.short and order/role; never infer a private member from public Person.

Map EventRegistrantIdentity and EventRegistration statuses, current sources, interest signals and import progress separately from aggregate totals; existing row-projection-vs-aggregate arbitration in events/services.py must not double count. No table drop before all rows and references are classified. Output synthetic fixture covering collisions, aliases, Q&A votes/invites, displacement rollback and current/legacy registration coexistence.

### D4.1b Extract DTC extensions while old events remain

Depends on a. Create future event_qna, historical_registrations and explicit event identity/provenance extension app as approved by field map. Keep source UUID foreign key until shared-ID map exists; later migrate through checked mapping. Preserve each session/question/vote/cohost-invite/rate-limit relation and historical source/revision/slot/displacement/total-state association and permission.

Use portable state/database migration operations with preserved tables or lossless copy; rerun no duplicates; unavailable mapping fails atomically. Focused event Q&A/current registration/history tests pass, row counts and FK checksums unchanged. Public registration totals before/after equal for each source combination, including undo/replace of aggregates.

### D4.1c Rehearsed shared-events import and compatible presentation

Depends on b, released C5.3/C4.3 and D3 identity hooks. P5 must export ALL mapped data, not its old truncated list. On development copy record source snapshot digest and counts, stop writers, rebuild only the collision-owned events label/schema through approved migration procedure, import deterministic IDs/maps, relink extension tables and validate every foreign key.

Set EVENT_URL_STYLE=public_id. Preserve /events/<positive-public-id>/<current-title-slug>, UUID/date aliases exactly one hop, canonical/SEO, speaker links, sanitized body, timezone/ICS UID+sequence, protected join fields and registration state. Keep event_qna and historical totals Studio/API on UUID compatibility adapters. Editorial sync must not write events; prove with fixture replay.

Verification: counts for every mapped table, alias identity and row relationships unchanged; second import no change; invalid/missing public ID aborts entire relevant transaction. Existing identity/slugs/content/current_registration/qna/history tests pass. Browser desktop/mobile event list/detail/Q&A/denial and one-hop aliases inspected. No observed deployment result is inferred from this rehearsal.

### D4.1d Registration/series/reminder parity

Depends on c and Relay mail conformance. Validate existing imported registration semantics plus new anonymous free-event flow. One row/event+normalized-email, hashed version-scoped verify/manage tokens, uniform response, cancel POST, reactivation version, no event marketing opt-in inference. DTC event-mail opt-out suppresses every event-purpose message including verification; no unintended transactionals exception. Site policy permits required levels 0/5 only.

Test series future-occurrence registration and opt-out, reminders idempotent, ICS updates on reschedule/cancel, hidden join window, feedback ownership, pending expiry, mail outage and duplicate verify. Registration may remain pending when preference suppresses its email, per spec05. Public/private cache boundaries remain safe. Package gaps block adoption pending tagged fix.

### D4.2 Freeze and deploy

Depends on complete D4.1 packets, recent rehearsal, announced freeze and site workflow. Deploy development then production only through existing authorized process/owner operations if live. Check list/numeric detail, UUID admin, one-hop alias, anonymous verification through Relay, current registration count, Q&A session, speaker link, ICS/join security and reminders. Record deployed release and evidence, remove freeze after pass. Row loss or failed smoke triggers reviewed schema/data/runtime rollback and stop. Update specs03/05 accurately: DB ownership already existed; package ownership is new.

## D5: curriculum and coursework

### D5.1a Exhaustive course/assessment donor map

Depends on C5.3 for runtime implementation; fixture/inventory preparation may precede as an explicitly groomed task. Read `courses/models/{cohort,curriculum,homework,project,wrapped,testimonial,cmp_import}.py`, `courses/models/curriculum_import.py`, `courses/course_family_catalog.py`, `courses/migration_family_identity.py`, `courses/services/curriculum_import.py`, `courses/registration.py`, `courses/scoring.py`, `studio_courses/` and [`04-courses-and-cohorts.md`](../specs/04-courses-and-cohorts.md).

Inventory Course/Cohort/Module/Unit/Enrollment/UnitReadState/CurriculumFlowItem/CohortBuildItem, all homework question/answer/submission/statistics, all project vote/criteria/assignment/review/response/score/statistics, campaign/registration/complaint/testimonial/wrapped/history progress; enumerate certificate storage/URL generation and teaching-team relations even if no dedicated model. Completeness means every source field, FK/M2M, route, certificate and teaching relation has one shared, extension, retained or blocking disposition. Map source provenance and immutable course-family identities. Record retained LearnerProfile and identity extensions. No broad 'all coursework' placeholder qualifies as a field map.

Record fixtures and invariant totals: cohorts and enrollments, learner/completion/read states, campaign/cohort snapshots, historical+native registration counts, answer values, scores/ranks/ties, deadlines/timezones, review assignment ownership and certificate URLs. Unknown target field/model becomes C5 package blocker; do not drop data to fit simplified package.

### D5.1b Curriculum and enrollment copy with stable route adapters

Depends on a and published package curriculum parity. Install cb_curriculum; map course families and cohorts, retain all provenance and stable lookup keys. Cohort content mode/self-paced mapping is explicit; do not flatten cohort-specific curriculum into shared mutable units. Copy enrollment/read progress with users resolved through shared identity; preserve cohort scope and access level 0/5.

Refactor DTC curriculum import consumer to package graph/service while parser stays site-owned. Ensure same immutable checkout replay gives zero changes; malformed import cannot publish partial curriculum. Keep courses.LearnerProfile and route adapters. Verify migration counts/checksums and focused curriculum/read/enrollment/course family tests, plus _docs/compatibility/course-route-contracts.json tests. Enrollment state and access before/after equal for every fixture learner/cohort.

### D5.1c Homework, questions, submissions and scores

Depends on b. Copy homework/question/submission/answer/statistics preserving IDs or explicit stable maps, deadline timezone, question type, answer semantics and learner uniqueness. Migrate scoring services through package API without changing historic outcomes. Submission ownership and late policy remain current spec04 behavior; no overwrite of grades during resync.

Verify old/new deterministic fixture scores exactly equal; missing FK/duplicate submissions fail before partial copy; rerun zero inserts; registered learner sees own submission; foreign-user edits denied. Run focused existing homework/scoring/deadline tests and graph; inspect changed submission forms under applicable browser tier.

### D5.1d Projects, peer review, leaderboards and certificates

Depends on c. Migrate Project/ProjectSubmission/ProjectVote/ReviewCriteria/ProjectCriteriaAssignment/PeerReview/CriteriaResponse/ProjectEvaluationScore/ProjectStatistics/LeaderboardComplaint plus applicable wrapped/testimonial state. Preserve review ownership/assignment randomness already materialized, score units, tie ranking, complaint state and certificate identity/storage paths. Neither bulk score recalculation nor certificate reissuance is an implicit migration step.

Verify fixture before/after exact assignments, scores/ranks and certificate URLs; duplicate copy inert; unauthorized reviewer cannot access another assignment; complaints resolve under same roles. Focused peer-review/leaderboard/certificate tests plus browser project submit/review/certificate download pass. If stored generated files are referenced, operator-approved development fixture file integrity is checked without production access.

### D5.1e Campaigns, registration, routes and shell contraction

Depends on b-d and D3 member profile parity. Preserve campaigns' immutable registration target snapshots, account+cohort uniqueness, consent evidence, course-specific comment, member-confirmed profile snapshot, public counts and notification dedupe. Retain current `/courses` and legacy learner/cadmin URL/status/redirect contract from JSON.

Mount package views behind DTC compatibility adapters and keep public shell. Remove studio_courses/course_management/cadmin/review_import/compatibility code only where every live importer/route/model is moved and no read-only historical input remains. courses persists if it owns LearnerProfile or route adapter; the canonical 'delete courses' instruction must be corrected. Clear orphan contenttype/permission references only through tested migration with preserved authorization.

Verify compatibility contract, all affected course/Studio/API tests, management/OpenAPI parity, migration drift and graph. Desktop/mobile catalog, cohort, registration/resume/conflict, homework, project and certificate screens pass. Archived/invisible cohort and anonymous access fail safely.

### D5.2 Freeze, self-paced proof and phase handoff

Depends on complete D5.1, all tagged fixes, recent development-copy rehearsal, freeze and normal deployment gate. Checks on development: cohort page; register eligible member; homework submit+score; leaderboard; project peer review; existing certificate download; create one self-paced course in Studio and show a unit to a registered member under DTC access levels. Counts/checksums remain reconciled. Production promotion/checks are owner-operated through site workflow if applicable.

Rollback uses the reviewed copy reversal and runtime release; never delete new learner submissions to simplify recovery. Stop writes and reconcile any post-copy work before rollback. Update spec04/app boundaries and shared STATUS.md after green deployment. Send D13 production ledger to Relay/AISL owner; phase5 completion does not erase R6.1/AISL gates.

## Luna max issue handoff template

Use one packet per issue. Replace every bracket before the engineer starts; an unfilled dependency, source symbol or expected outcome returns to PM grooming. The selected execution model is gpt-5.6-luna at max reasoning when dispatching agents; the independent tester and PM remain separate roles.

```text
Issue: <number and canonical parent/child>
Goal: <one observable behavior>
Owner/worktree: <engineer; absolute isolated path>
Baseline: <site SHA, package tag and immutable commit, clean status>
Depends on: <closed issue URLs and exact release/deploy/conformance artifacts>
Read first: <normative spec sections; exact existing source paths/symbols>
Own files: <bounded explicit paths; new paths labelled create>
Do not edit: <neighbor domains/other-agent files; no reverting others>
Current behavior: <fixture-backed example>
Target behavior: <same example after change>
Data map: <every field/ID/FK, old/new table, deterministic copy/reverse policy>
Steps: <numbered edits and tests; stop after each failed check>
Verification: <exact commands, fixture/environment, expected results>
Browser: <URLs, roles, states, viewport, expected denial/error; or graph-based N/A>
Acceptance: <checkboxes for every invariant and externally visible contract>
Rollback: <write stop, data reconciliation, previous runtime, tested reverse boundary>
Stops: <specific missing tags/APIs/evidence plus canonical stop conditions>
Docs/status: <files and canonical status-only PR owner>
Handoff: <base/head, status, plan/graph digests, four buckets, actual counts and artifacts>
Commit gate: <engineer freezes uncommitted diff; tester pass then PM accept before commit>
```

No one executes an entire phase in a single Luna agent turn. If a packet needs more than one independently reviewable change, PM splits it again before runtime edits and updates canonical tracking. Never ask an executor to 'make compatible' without concrete mappings, tests and preserved public behavior.
