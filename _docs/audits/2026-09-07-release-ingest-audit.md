# Release preparation and ingestion audit — 2026-09-07

This is a code audit and implementation handoff. It does not certify a deployed
environment or an actual production migration. Five findings below are P0:
application CI does not gate the active deployment pipeline; failed promotions
have no application recovery; CMP claims can cross database boundaries; CMP
claims can be lost after a committed batch; and the documented production course
pull refuses every deployed target.

The highest impact reproduced defect is REL-03. A claims file from another
database can make an import attach a **verified email address to an unrelated
account**. All reproductions used fabricated accounts and files; no production
or registration export was opened.

## Scope, evidence and interpretation

Reviewed the active `deploy-dev.yml` and `deploy-prod.yml` workflows, their shell
and Python helpers, the retained release controller and target registry, Make
rehearsal and importer targets, local preparation and verification, `scripts/prod`
target selection, editorial/event/media/learner/legacy entry points, and the
service code needed to establish importer correctness. Read `AGENTS.md`,
`_docs/PROCESS.md`, relevant operations specifications, and the deployment and
ingestion runbooks. The public-content and shared-curriculum architecture audits
cover broader `content_sync` changes separately.

Line numbers identify the files observed on this date. The shared-curriculum
worktree already contained substantial user-owned changes and continued to
change during the audit. These were preserved. In particular, REL-15 is an
integration concern against an in-flight curriculum change, not a claim that
those uncommitted changes had already passed acceptance.

Confidence labels mean:

- **Reproduced:** an offline synthetic test demonstrated the stated behavior.
- **Confirmed static:** reachable code establishes the defect, but the triggering
  AWS, destructive filesystem, or database concurrency operation was not run.
- **Conditional occurrence:** code proves the behavior when a stated condition
  exists; this audit did not establish that condition in production data.

P0 means release/migration blocker; P1 means important corrective work; P2 means
maintenance or later work. A finding marked conditional is not evidence of an
actual production incident.

## Findings at a glance

| ID | Priority | Problem | Evidence |
| --- | --- | --- | --- |
| REL-01 | P0 | Deployments can proceed while application CI is red | Confirmed static |
| REL-02 | P0 | Partial/failed promotion has no recovery or retained recovery receipt | Confirmed static |
| REL-03 | P0 | Claims from another target attach verified identity/history to wrong primary keys | Reproduced |
| REL-04 | P0 | Commit-before-claims crash loses mappings on ordinary resume | Reproduced |
| REL-05 | P0 | Production course pull is blocked by a local-seeder guard | Reproduced with configuration boundary stubbed |
| REL-06 | P1 | Deployment success does not prove the worker or all serving tasks run the release | Confirmed static |
| REL-07 | P1 | Promotion record lacks durable production receipt and deployment-code binding | Confirmed static |
| REL-08 | P1 | Task rewrite replaces every sidecar image and starts from latest family revision | Reproduced/static |
| REL-09 | P1 | Rebuild deletes the old dataset before validating target and prerequisites | Confirmed static |
| REL-10 | P1 | Checkout refresh erases changes without proving directory ownership | Confirmed static |
| REL-11 | P1 | Registrant dry-run accepts malformed CSV data | Reproduced |
| REL-12 | P1 | Maximum-length username collision never makes progress | Reproduced |
| REL-13 | P1 | Local preparation reimplements only part of the event pipeline | Confirmed static |
| REL-14 | P1 | Dataset gate counts media records without verifying media objects | Confirmed static |
| REL-15 | P1 | Dataset gate freezes curricula in code and cannot verify shared curriculum | Confirmed static/in-flight integration |
| REL-16 | P1 | Legacy answer replacement commits deletion before replacement | Confirmed static |
| REL-17 | P1 | Legacy certificate matching collapses people with the same display name | Conditional occurrence |
| REL-18 | P1 | Editorial release creation races and manufactures Git commit identities | Confirmed static |
| REL-19 | P2 | Multiple release mechanisms and outdated runbooks obscure the supported path | Confirmed static |

## REL-01 — Required application verification does not gate delivery

**Priority/confidence:** P0, confirmed static.

**Evidence:** `.github/workflows/deploy-dev.yml:3` triggers on every push to main;
`:15` defines a test job running only
`python -m unittest core.tests.test_cmp_style_deployment` at `:26`; `publish`
depends only on that job at `:29`; deployment depends only on publish at `:85`.
`.github/workflows/ci.yml:3` runs separately. Its quality, Django, browser,
container and evidence checks are not dependencies of this workflow.
`.github/workflows/deploy-prod.yml:40` selects the latest successful dev workflow
without checking the corresponding application CI verdict. The five deployment
contract tests pass and mostly inspect strings in files
(`core/tests/test_cmp_style_deployment.py:13`).

**Trigger/impact:** push an application regression that leaves these five tests
green. Dev can publish and deploy that commit even if security, migration,
backend or browser CI fails or has not finished. Its successful dev artifact can
then be selected for production. A page that happens to return HTTP 200 is not
the repository's required verification gate.

**Bounded fix:**

1. Define one successful verification result for the exact source SHA and
   required plan/evidence digests. Reuse the existing CI gate and evidence
   validators rather than defining a reduced second acceptance policy.
2. Make deployment depend on that result through a reusable workflow/job, or a
   completed-CI trigger with exact workflow/repository/SHA checks. Fail closed
   on missing, pending, canceled or unsuccessful verification.
3. Bind the image and dev release record to the verified source SHA. Production
   must revalidate that binding when selecting a candidate.
4. Keep the dev/production split and manual production confirmation.

**Acceptance:** a synthetic failed Django/security/browser verdict prevents
publishing or deployment; pending/canceled/no verdict is not green; a green
different SHA cannot authorize the candidate; valid exact-SHA evidence allows
the normal sequence. Exercise parsed workflow/job dependencies and gate
behavior, not only substring assertions.

**Dependencies:** none. Coordinate with REL-07 for release-record schema.

## REL-02 — Failed promotion leaves services changed and erases recovery evidence

**Priority/confidence:** P0, confirmed static.

**Evidence:** `deploy/deploy_website.sh:73` installs only a cleanup trap that
deletes the entire working directory. `:135` updates web, `:140` updates worker,
`:145` waits for stabilization, and `:148` checks the public endpoint. There is
no capture of both previously active definitions/counts and no rollback or
compensation handler. The active workflows invoke this shell through
`deploy/deploy_dev.sh:6` / `deploy/deploy_prod.sh:11`.

The retained controller already contains prior-state capture and recovery
machinery: `deploy/release.py:585`, `:979`, `:1357`, `:1406`. It is not used by
these workflows. Its existing configuration assumes one web task
(`deploy/release.py:316`); simply replacing the shell invocation with that
controller is therefore not a complete production fix.

**Trigger/impact:** web update succeeds, then the worker update, stabilization
or smoke fails. The workflow is red but one or both services remain on the new
release. If ECS independently rolls back one service, the pair can differ.
The temporary files needed to understand the attempted rollout are deleted.
Migration-side database changes are also not undone by restoring an image.

**Bounded fix:**

1. Before the first mutation, capture exact active web/worker task-definition
   ARNs, desired counts, deployment IDs, and prior release identity; persist a
   redacted recovery receipt in `.tmp`.
2. Adapt the tested release controller to the reviewed dev and production
   counts/targets, or extract its proven recovery logic into the one supported
   orchestrator. Preserve the current two-task production web configuration.
3. On any post-mutation failure, attempt bounded recovery of both workloads and
   verify the recovered pair. Report the original deployment as failed even
   when recovery succeeds.
4. Upload redacted receipts/evidence using an `always()` workflow step. Remove
   raw responses locally only after the durable safe receipt exists.
5. Specify expand/contract migration compatibility and backup/restore handling
   separately; do not advertise image rollback as database rollback.

**Acceptance:** fake AWS integration tests fail each stage after web mutation;
both exact prior targets are restored or an explicit actionable recovery
failure is retained. Runner interruption leaves a usable receipt. Successful
rollout preserves intended counts. No raw task environment/secrets appear in
uploaded artifacts.

**Dependencies:** share release receipt work with REL-07; no need to wait for
unrelated ingest changes.

## REL-03 — Claims are not bound to the source snapshot and target database

**Priority/confidence:** P0, reproduced.

**Evidence:** `scripts/prod/import_cmp_learners.py:110` defaults every run in a
checkout to the same claims file. `accounts/services/cmp_learner_import.py:248`
defines `.tmp/cmp_learner_import_claims.json`; `:275` loads a flat map containing
only source integer IDs and target integer IDs. There is no database identity,
source-export digest, schema version, or run identity. At `:573` a claimed
source account is skipped unconditionally; at `:632` its email row uses the
claimed target primary key, with no source-account identity comparison.

The history importer has the same global-default pattern at
`scripts/prod/import_cmp_learner_history.py:136`; user claims are passed through
at `:156`. `courses/services/cmp_learner_history_import.py:1117` and `:1143`
trust existing table claims when skipping rows.

**Reproduction:** in an isolated test database, create an unrelated synthetic
user. Put a foreign claims entry `source_id=1 -> that user's pk` in a scratch
file, then import a one-account synthetic export with a different address and
a verified `account_emailaddress` row. The account import skips its source row
and creates a verified email row on the unrelated user. The test asserted the
misassociation without printing any account values.

**Impact:** routine rehearsal followed by another target using the default
claims path can silently omit accounts, connect learner history to unrelated
primary keys, and transfer verified sign-in identity. This is more serious
than inaccurate progress reporting.

**Bounded fix:**

1. Introduce script-owned import-run metadata with a schema version, immutable
   source snapshot digest, target-database UUID and importer version. Store the
   target UUID in a script-owned table, not a public model field.
2. Require every claims store and progress watermark to match that metadata
   before any ORM write. A file path or database basename is insufficient: a
   rebuilt database can reuse the same path and different primary keys.
3. Default claims locations to a per-run directory. A production run should
   use an explicit durable import-run location, not a shared checkout default.
4. Validate claimed target rows exist and satisfy the intended identity
   relationship. Quarantine disagreement instead of accepting a numeric PK.
5. Reject old unbound claims until a separate reviewed recovery/conversion
   step establishes their provenance; never infer ownership from matching IDs.

**Acceptance:** two target databases with overlapping PKs cannot share claims;
a recreated target at the same path is refused; changed source snapshot is
refused; a valid same-run resume succeeds; a mismatched claim writes no email,
history or account rows. Use synthetic identities only.
Dry-run/status must validate and report the selected run binding without
creating rows or rewriting claims. Preserve the existing historical duplicate
identity quarantine and reviewed reconciliation mapping flow: a matching
normalized address is not permission to merge competing historical owners.

**Dependencies:** coordinate the run schema with REL-04 before editing the two
services in parallel. Keep import bookkeeping disposable as required by
`_docs/runbooks/ingest-script-inventory.md`.

## REL-04 — Claims and watermarks do not survive the actual crash boundary

**Priority/confidence:** P0, reproduced.

**Evidence:** `accounts/services/cmp_learner_import.py:567` commits accounts
and their progress watermark in one transaction; the JSON claims flush occurs
afterward at `:603`. Resume selects `id > last_source_id` at `:558`, so it never
revisits committed rows whose claims were lost. Email import skips missing
claims at `:632`. History advances its watermark at
`courses/services/cmp_learner_history_import.py:1171` and flushes mappings only
after commit at `:1177`.

The apparent regression test
`accounts/tests/test_cmp_learner_import.py:449` manually resets
`last_source_id=0` and `completed=False` at `:468` before resuming. Ordinary CLI
resume performs neither operation. That test demonstrates a manually repaired
scenario, not crash-safe ordinary resume.

**Reproduction:** force `CmpClaimsStore.flush()` to raise after one account
batch has committed, remove the fault, and invoke the same import again with
the untouched database progress. Result: account progress reports completed,
the claims store has zero mappings, and the source email row is never created.

**Bounded fix:**

1. Make account/history mappings and watermarks one atomic durability unit,
   preferably script-owned database tables written in the same transaction.
   File exports can be regenerated as receipts after commit.
2. If file-backed bookkeeping is retained, implement a durable write-ahead
   journal plus explicit startup reconciliation; a different ordering of two
   independent writes alone cannot make them atomic.
3. At startup, verify claims/progress consistency and refuse missing mappings
   before advancing dependent-table watermarks. Provide an explicit recovery
   command for already damaged runs.
4. Replace the misleading test with fault injection immediately after commit,
   before/during claims persistence, and before starting each dependent table.

**Acceptance:** every fault followed by an ordinary unmodified CLI rerun yields
the same mappings, verified-address rows and history as an uninterrupted run.
No manual progress reset is allowed in this acceptance test. Disk-full and
permission-denied persistence failures return bounded errors and preserve the
ability to recover.
The recovery path must preserve verified-owner ambiguity handling; it must not
repair missing mappings by blindly merging every equal-address historical row.

**Dependencies:** REL-03's import-run binding; implement the shared bookkeeping
contract first and update account/history users together.

## REL-05 — The deployed course pull always refuses the selected production target

**Priority/confidence:** P0, reproduced with only target setup and source lookup
stubbed; no production connection was made.

**Evidence:** `scripts/prod/sync_course_repositories.py:387` uses the shared
parser and `configure_target()` supporting reviewed deployed targets. At
`:407` it imports `assert_local_database()` from the local seeder and calls it
unconditionally before pulling. The guard at
`courses/services/local_course_seed.py:145` rejects any non-local/non-test
environment and any non-SQLite engine. The production procedure nevertheless
instructs the deployed form of this entry point in
`_docs/runbooks/production-data-migration.md:500`.

**Trigger/impact:** the documented command with matching
`--deployment-target website-production` and
`--allow-production-write website-production` passes target selection and then
exits 1 with `environment-not-local`. Production course curriculum bootstrap
cannot complete through the supported script.

**Bounded fix:**

1. Remove the importer’s dependency on the local seeder. Keep the seeder's own
   local-only guard intact.
2. Treat the result of `configure_target()` as the write authorization boundary;
   rely on its explicit paired target flags and reviewed deployment settings.
3. Verify the actual curriculum-ingestion service supports PostgreSQL and
   continues to use the same validation/transaction path as webhook ingestion.
4. Add a production-configuration CLI test that reaches the ingestion boundary
   using a fake service and no external connection, plus synthetic PostgreSQL
   integration coverage when that test service is available.

**Acceptance:** selected local and explicitly selected production targets reach
ingestion; missing/mismatched target flags are refused before writes; no seeder
is imported by `scripts/prod`; real curriculum validation remains active.

**Dependencies:** coordinate with the owner of in-flight shared-curriculum
ingestion changes. Do not weaken `assert_local_database()` globally.

## REL-06 — Success checks do not establish a coherent running release

**Priority/confidence:** P1, confirmed static.

**Evidence:** `deploy/deploy_website.sh:145` uses the generic ECS stable waiter.
`:149` accepts one matching `/api/health/` response and a homepage HTTP success.
`api/views/health.py:20` returns only runtime identity, with no database or job
check. The shell does not inspect each running task's digest or confirm the
worker's active definition. Dev explicitly sets worker desired count to zero
at `deploy/deploy_website.sh:46`, whereas production uses one at `:57`.

**Trigger/impact:** an independently rolled-back/incorrect worker can be stable
while web serves the expected identity. Dev success cannot exercise the worker
at all. A successful homepage request does not prove ingestion, email outbox,
deadline jobs or content freshness, and a hanging connection can exceed the
apparent retry budget because curl has no connect/overall timeout.

**Bounded fix:**

1. Verify exact task-definition ARN/digest and expected counts for each
   workload, including all serving web tasks and the singleton worker.
2. Use the existing release smoke contracts where applicable; include a
   bounded readiness/database check and a synthetic job proving worker
   execution without contacting a real provider.
3. Either run an isolated dev worker for that check or perform a one-off worker
   verification task and record the limitation of a zero-worker dev service.
4. Add explicit connect/request/phase deadlines to HTTP and AWS polling.

**Acceptance:** old stable worker, mixed web tasks, database-unready response,
empty/error page and hanging HTTP all prevent a success record. Synthetic
worker execution completes within a fixed budget and sends no live email.

**Dependencies:** REL-02 for failure recovery; REL-01 for the predeploy gate.

## REL-07 — Release selection is not a durable, fully bound promotion receipt

**Priority/confidence:** P1, confirmed static.

**Evidence:** `.github/workflows/deploy-prod.yml:35` checks out the dispatch
ref before selecting a dev candidate at `:40`; the deploy code therefore need
not be the candidate SHA. Candidate validation at `:59` checks only three
strings: image, version and source SHA. The only persisted success record is
the dev artifact at `.github/workflows/deploy-dev.yml:108`, with 90-day
retention at `:126`. The production workflow ends after the shell command at
`.github/workflows/deploy-prod.yml:76`; it records no resulting ECS pair or
production receipt.

**Trigger/impact:** code from one dispatch revision controls promotion of an
image built from another revision; reviewers cannot tell which controller
version and CI evidence authorized it. Later rollback/incident diagnosis lacks
a durable authoritative record of what production actually accepted. A JSON
image identity alone cannot substitute for observed service state.

**Bounded fix:**

1. Restrict accepted workflow refs and pin either the candidate's deployment
   code or an explicitly versioned controller. Record both application and
   controller SHA when they intentionally differ.
2. Extend the validated release schema with CI/evidence binding, dev run ID,
   image digest, candidate identity, target, observed service pair, timestamps
   and controller version. Do not store secret task environments.
3. Write a production success receipt only after terminal verification, and
   retain success/failure receipts under an explicit durable retention policy.
4. Require explicit selection of an older release when the latest applicable
   candidate is not usable; do not infer rollback safety from an expired
   artifact or a version string.

**Acceptance:** mismatched controller/candidate policy, expired/missing evidence
and wrong-SHA artifacts are refused before AWS mutation. A successful prod run
publishes an independently parseable receipt identifying the observed pair;
failure publishes recovery evidence and no success receipt.

**Dependencies:** REL-01 and REL-02 should share the same schema.

## REL-08 — Task definition rewrite clobbers sidecars and follows unselected revisions

**Priority/confidence:** P1, reproduced for sidecars; confirmed static for
family-revision selection. Actual deployed sidecar presence was not checked.

**Evidence:** `deploy/update_task_definition_image.py:63` loops through every
container and overwrites every image and identity environment. It has no
workload/container-name selection or shape validation. The shell describes the
family name at `deploy/deploy_website.sh:82`, selecting the latest registered
family definition, rather than the definition actively selected by the service.

**Reproduction:** a synthetic task containing `web` and `log-router` results in
both containers using the website image. A previously registered but unpromoted
revision can likewise supply roles, secrets, commands or networking assumptions
without being the currently accepted service configuration.

**Bounded fix:**

1. Select the expected application container by workload name; require exactly
   one match and preserve explicitly allowed sidecars unchanged. If the
   architecture forbids sidecars, refuse the task shape before registration.
2. Capture active service task-definition ARNs and validate the permitted
   migration-family source separately. Do not discover application configuration
   from whichever revision was most recently registered.
3. Reuse `deploy/task_definitions.py` validation for roles, secrets, commands,
   image identity and runtime settings where compatible with the current target.

**Acceptance:** multi-container task preserves the sidecar or safely refuses it;
missing/duplicate app container is rejected; unrelated latest family revision
cannot override active accepted configuration; malformed responses fail before
registration. Keep fixtures entirely synthetic.

**Dependencies:** REL-02 captures the necessary service ARNs; REL-19 removes
duplicated target/configuration ownership.

## REL-09 — Rebuild destroys the existing dataset before safety/preflight checks

**Priority/confidence:** P1, confirmed static. No destructive recipe was run.

**Evidence:** `Makefile:520` and `:755` delete the caller-selected database,
`-wal` and `-shm` files at `:521` / `:762`. The Python confinement check in
`scripts/prepare_local_data.py:95` is reached later. The Make variable accepts
an arbitrary path. Registration and CMP sources are checked/imported after
rebuild has begun (`scripts/prepare_local_data.py:248`, `:307`, `:315`). The
course-registry stage refuses an existing DB at `Makefile:505`, so a retry is
structured around removal rather than resuming a safe staged result.

**Trigger/impact:** override the dataset path incorrectly, run while a local
server is using its WAL, lose network access while refreshing checkouts, or
discover an unavailable source after deletion. The previously usable rehearsal
database is already gone; an out-of-`.tmp` target can be deleted before Python
refuses it. Deleting WAL files of a live SQLite database can corrupt state.

**Bounded fix:**

1. Move rebuild orchestration into a Python entry point with a single resolved
   target check before any mutation: `.tmp` containment, `.sqlite3` suffix,
   symlink policy, non-root path, source/target separation and exclusive ownership.
2. Preflight required source availability and integrity without changing the
   existing dataset. Keep required private-source choices explicit.
3. Build into a fresh sibling scratch directory/database, capture input
   digests, and run dataset verification there.
4. Only after success, close connections/checkpoint WAL and atomically switch
   the selected dataset under an exclusive local lock. Preserve the previous
   dataset as a named recoverable backup until explicitly retired.
5. Have both Make targets call the same orchestrator with options for legacy
   history, rather than maintain two deletion/build recipes.

**Acceptance:** bad path, symlink escape, unavailable source, failed import,
failed gate and concurrent reader/writer leave the original database and its
WAL untouched. Successful replacement exposes only the fully verified result.
Tests use disposable sentinel files inside the project `.tmp` only.

**Dependencies:** REL-13 for the shared event step; REL-14/15 for a meaningful
acceptance gate.

## REL-10 — Checkout refresh erases user data without checkout ownership checks

**Priority/confidence:** P1, confirmed static. No refresh command was executed.

**Evidence:** `Makefile:401` and `:449` check only whether `<checkout>/.git` is a
directory before `git reset --hard FETCH_HEAD` and `git clean -fdx` at
`:404`/`:405` and `:452`/`:453`. Both roots can be overridden. Existing origins,
uncommitted changes, and an owned disposable-checkout marker are not validated
before the destructive refresh. Git worktrees have a `.git` file, so the same
logic treats an existing worktree as if it needs cloning.

**Trigger/impact:** point a content checkout variable at an existing editable
repository. Refresh can erase tracked changes and all untracked/ignored files,
including local-only assets. An unexpected origin is fetched before importer
origin validation can refuse it. Worktree-based workflows fail awkwardly.

**Bounded fix:**

1. Extract the two clone/refresh loops into one checked checkout helper.
2. Require project-local owned scratch roots for disposable refreshes; validate
   exact repository origin and refuse dirty/unowned directories by default.
3. Prefer immutable revision-addressed checkout directories. Fetch should not
   require resetting an existing editable tree.
4. Detect repositories/worktrees using Git's own discovery. An explicit reset
   mode, if retained, must require an owned disposable checkout marker.

**Acceptance:** tracked edits, untracked and ignored sentinel files survive the
default refresh refusal; wrong origin never fetches; safe owned checkout can be
updated; `.git` file worktrees are correctly identified; path traversal/escape
is refused before Git mutation.

**Dependencies:** can proceed independently; REL-09 should call this helper.

## REL-11 — Registrant dry-run does not validate the rows that apply will ingest

**Priority/confidence:** P1, reproduced.

**Evidence:** `scripts/prod/import_event_registrants.py:131` discovers lazy
sources. Its dry-run branch at `:133` reports only `len(pending)`; it never
invokes their row readers. `scripts/prod/registration_sources/luma_registrants.py:191`
deliberately defers CSV parsing. Schema/event-ID/row-limit checks occur only at
`:155` when rows are eventually read.

**Reproduction:** provide one valid synthetic event checkpoint paired with a
CSV containing an invalid header. `--dry-run` exits 0 and reports one event.
The same row reader would reject it during apply. The dry-run also gives no
match/skip/replacement totals for `--refresh`.

**Bounded fix:**

1. Keep cheap discovery as a separately named plan mode if useful.
2. Make `--dry-run` parse every selected CSV through the real bounded reader and
   compute target resolution and proposed changes without writes.
3. For refresh, report aggregate counts of registrations added, changed,
   removed, and events blocked/unresolved; do not emit attendee values.
4. Reject malformed input with a bounded condition code and nonzero exit.

**Acceptance:** bad headers, mismatched event IDs, oversized/malformed CSV and
unresolved targets produce the same validation decisions in dry-run and apply;
all database rows and progress records are unchanged by dry-run; logs contain
counts and IDs only. A valid refresh plan predicts the applied aggregate diff.

**Dependencies:** no release work dependency; coordinate consent/registration
service changes with the backend owner.

## REL-12 — Username allocation can loop indefinitely on a valid-length collision

**Priority/confidence:** P1, reproduced with a bounded fake ORM query.

**Evidence:** `accounts/services/cmp_learner_import.py:446` truncates the base
to 150 characters, then `:452` appends a suffix and truncates the full result
back to 150. With an existing 150-character base, every candidate is identical.
Shorter near-limit bases also stop changing when suffix width grows.

**Reproduction:** call `_unique_username()` with a 150-character value and an
`exists()` fake that returns true twice, then raises a bounded stop. All three
queries receive exactly the same username.

**Bounded fix:**

1. Preserve the unsuffixed first candidate. On collision compute the suffix,
   truncate the base to `max_length - len(suffix)`, then append it.
2. Put a finite bound on sequential retries; use a deterministic source-key
   suffix or a bounded random strategy afterward according to username policy.
3. Handle a concurrent unique-constraint conflict with a bounded retry rather
   than assuming the pre-insert existence check reserves the name.

**Acceptance:** 150-character collision progresses immediately; suffix-width
transitions remain unique and within field length; repeated collisions terminate
within the documented limit; a synthetic concurrent insertion is handled.

**Dependencies:** none; avoid overlapping edits to the claims functions while
REL-03/04 are implemented.

## REL-13 — Local rehearsal duplicates and omits event orchestration behavior

**Priority/confidence:** P1, confirmed static.

**Evidence:** `scripts/prepare_local_data.py:260` calls identity and old event
content import directly; `:315` derives sources and stages aggregates directly.
It does not call the full event pipeline. The latter additionally discovers
new identities at `scripts/prod/import_events.py:1067`, imports their staged
descriptions at `:1073`, and performs unambiguous aggregate resolution at `:1091`.
It wraps all legs in one transaction at `:1032`; the rehearsal does not.

`production-prep-bootstrap` later calls `import-events` again at `Makefile:778`,
while `production-prep-dataset` does not (`Makefile:520`). The Make comment at
`:732` claims these variants differ only by legacy Zoomcamp history.

**Trigger/impact:** new events/descriptions/automatic mappings appear through
the production importer but are missing from the shorter rehearsal. Aggregate
validation failure leaves the rehearsal's earlier event writes committed even
though the production importer explicitly fixed that failure mode. The longer
bootstrap repeats source parsing and writes to compensate for drift.

**Bounded fix:**

1. Make one event pipeline callable by both entry points, with explicit inputs
   and a structured result. Reuse `import_events.run()` and its transaction
   boundary where possible.
2. Move the rehearsal's course/editorial ordering around that single event
   stage; do not reach into private event legs or maintain a second leg list.
3. Remove the duplicate later Make invocation after parity is established.
4. Update the rehearsal report to expose new-identity/content/resolution results
   separately, preserving their existing provenance distinctions.

**Acceptance:** identical synthetic sources produce equivalent event identities,
content and aggregate resolution through direct import and each rehearsal
variant. Fail the final validation leg and assert no partial event writes or
queued jobs. Each provider source is parsed once per intended pass.

**Dependencies:** REL-09's staged rebuild; coordinate current event service work.

## REL-14 — Dataset acceptance verifies media descriptors, not usable artwork

**Priority/confidence:** P1, confirmed static.

**Evidence:** `scripts/verify_local_dataset.py:210` counts `catalogue.media()`
records; `_editorial_failures()` at `:300` requires nonempty collections and
asset row totals only. `scripts/prepare_local_data.py:314` imports editorial
metadata but never hydrates public media. Existing media verification is a
separate command, `scripts/prod/sync_public_media_verify.py`, backed by
`content/media_tooling.py:312`.

**Trigger/impact:** a dataset has all required catalogue rows but its local
store is absent or missing objects. The gate can pass while image requests
fail. The repository's test media defaults deliberately use deterministic
memory fixtures; their success is not proof that a local or S3 deployment
contains the reviewed artwork.

**Bounded fix:**

1. Add a media-store verification component to the dataset acceptance report.
   Resolve records from the active database release and verify expected
   size/checksum/availability using the selected real store.
2. Keep networked hydration/publishing explicit and separate from verification;
   when objects are missing, report the exact bounded recovery command and
   aggregate missing count.
3. Distinguish synthetic-fixture acceptance from real-object acceptance in the
   report. Do not label memory fixtures production-media verification.
4. Include representative asset requests in the deployed smoke after the
   metadata/storage check.

**Acceptance:** records with missing/wrong-checksum bytes fail the real dataset
gate; complete objects pass; memory fixtures are explicitly marked synthetic;
verification writes nothing to S3. Add an empty-store fixture and one corrupt
object fixture rather than downloading actual media for unit tests.

**Dependencies:** REL-09 consumes this gate; broader database-only asset migration
is separately documented architecture debt.

## REL-15 — Dataset verifier freezes a snapshot and assumes cohort-owned curricula

**Priority/confidence:** P1, confirmed static; shared-curriculum effects require
coordination with the active unfinished implementation.

**Evidence:** `scripts/verify_local_dataset.py:54` hardcodes 2026 cohort slugs;
`:60` fixes module/unit totals to `(9,105)`, `(7,72)`, `(4,4)`;
`:102` counts only cohort-owned `modules`; `:125` rejects any additional
module-format cohort. `:277` fails whenever no event is future-dated according
to wall-clock time. Conversely, the CMP-content gate at `:292` only catches the
case where homework exists but the *entire* database has zero questions.

**Trigger/impact:** a legitimate upstream lesson addition or new delivery year
fails a "production shape" gate despite correct ingestion. Moving curriculum
ownership to shared current revisions makes the cohort-only counts obsolete.
One unrelated surviving question can hide omitted source questions. A frozen
event snapshot also starts failing solely because time passes.

**Bounded fix:**

1. Generate a versioned expectation manifest from the exact selected source
   snapshots/import receipts. Bind expected counts/identities to their source
   digest and the selected curriculum schema, not literals in runtime code.
2. Verify the active curriculum through the same course-context resolver used
   by public views, including shared revisions and delivery-linked homework.
3. Reconcile per-source/per-cohort content totals and unresolved categories
   against importer receipts; reject unexplained omissions.
4. Separate structural dataset integrity from a freshness policy. Record an
   explicit `as_of` and snapshot date; test upcoming-event expectations against
   reviewed source facts rather than requiring a perpetually future event.

**Acceptance:** valid source additions and a new cohort are accepted with a
matching manifest; removed/omitted rows fail; shared curriculum is counted once
and remains reachable; fixed-date tests do not change results next year; unknown
expectation schema fails clearly. Do not update hardcoded numbers as the fix.

**Dependencies:** finish/review the shared-curriculum contract first; integrate
its existing verifier rather than create another competing owner.

## REL-16 — Legacy homework replay can delete persisted answers before replacement

**Priority/confidence:** P1, confirmed static.

**Evidence:** `scripts/prod/legacy_zoomcamp/scoring_import.py:190` updates a
submission, then `:204` deletes its answers and `:205` bulk-creates replacements.
There is no surrounding transaction in `_import_homework()` or
`scripts/prod/import_legacy_zoomcamp.py:132`. The importer-wide iteration at
`:191` also has no atomic boundary for these related writes.

**Trigger/impact:** on replay, a process failure or database error after answer
deletion leaves a previously populated submission with no answers and possibly
new score totals. Re-running may repair it, but until then users and downstream
recalculation see inconsistent educational records.

**Bounded fix:**

1. Parse/validate the bounded source unit before writes.
2. Put learner/enrollment association, submission update, answer replacement
   and any tightly coupled score update in one bounded transaction per
   submission or homework batch. Avoid one transaction for the full historical
   migration.
3. Record script-owned batch progress only on successful commit and ensure
   recalculation runs after the necessary data commits.

**Acceptance:** fail immediately after delete and during answer insertion; prior
answers and totals remain intact. Normal replay creates no duplicate answers,
submissions or enrollments. Resume after a failed later batch preserves earlier
complete batches.

**Dependencies:** none; use the script-owned checkpoint conventions from REL-04
if progress is introduced.

## REL-17 — Certificate matching conflates graduates sharing a display name

**Priority/confidence:** P1, conditional occurrence. No actual duplicate-name
population was read or claimed.

**Evidence:** `scripts/prod/legacy_zoomcamp/certificate_import.py:29` builds a
dictionary keyed only by `name.lower()`; `:41` overwrites any earlier certificate
under that key. The importer resolves each graduate by email at `:66`, then
looks up their certificate only by display name at `:69`.

**Trigger/impact:** two different graduates share the same normalized name, or
the same name appears in multiple certificate-source files for an edition. Both
can receive the last certificate URL encountered, making certificate ownership
incorrect. The reported matched count does not detect ambiguity.

**Bounded fix:**

1. Build a multimap of candidate certificates and preserve collision counts.
2. Match only on an unambiguous source identity shared by both artifacts. If no
   stronger key exists, permit name matching only when it is unique on both
   sides and report ambiguous matches for a reviewed explicit mapping.
3. Leave an existing certificate unchanged on ambiguity and report aggregate
   blocked counts; never emit real names/addresses to logs.

**Acceptance:** two synthetic graduates with the same name cannot receive the
same last-wins certificate; unique exact matches still work; repeated imports
are stable; a reviewed identity mapping resolves an otherwise ambiguous case.

**Dependencies:** may need an owner-supplied mapping format for unavoidable
source ambiguity; all detection/unique matching work can be implemented first.

## REL-18 — Editorial importers race on release allocation and record fake commits

**Priority/confidence:** P1, confirmed static. Concurrency was not exercised
against PostgreSQL.

**Evidence:** public content allocates `max(sequence)+1` without a source lock at
`scripts/prod/import_public_content.py:143` and `:157`; FAQ repeats this at
`scripts/prod/import_faq.py:181` / `:195`; docs repeats it at
`scripts/prod/import_docs.py:260` / `:274`. All manufacture `commit_sha` from the
sequence (`import_public_content.py:168`, `import_faq.py:206`,
`import_docs.py:285`). Their readiness/activation transitions run after the
document-creation transaction, and replay always builds another full release.

**Trigger/impact:** two imports for one existing source can choose the same
sequence, producing an integrity failure. Different inputs can complete in a
different order from their source review, leaving one import to fail activation.
The existing service correctly rejects a stale `based_on_release_id` and
non-increasing sequence (`content/services.py:1242`); this audit found no bypass
of that protection. A fake 40-character sequence is indistinguishable in shape from an
actual Git revision but identifies no reviewed source commit. Repeated
unchanged rehearsals accumulate duplicate release/document rows.

**Bounded fix:**

1. Extract a small shared reviewed-artifact release helper for these three
   scripts; keep family-specific validation and document construction separate.
2. Lock the source when allocating sequence/base revision, or use the existing
   release-creation service if it supplies the required concurrency contract.
3. Record actual source revision(s) and reviewed artifact digest in explicit
   provenance fields. Do not synthesize Git SHA values from counters.
4. Preserve the service's stale-base and increasing-sequence activation guards.
   Translate a competing activation into a bounded failed/superseded import
   result instead of leaving an unexplained ready release and raw traceback.
5. Return a replay receipt when the same accepted artifact/version is already
   active; retain historical releases according to an explicit bounded policy.

**Acceptance:** two concurrent imports cannot allocate the same sequence, and
the existing rejection of stale content remains intact; identical artifact replay does not duplicate
the entire catalogue; changed artifact provenance remains inspectable; a failed
transition leaves a terminal failed/quarantined run with the previous release
active.

**Dependencies:** coordinate with the content architecture owner and existing
release services. This is not a request to add runtime JSON fallback.

## REL-19 — Release machinery and operator documentation have competing owners

**Priority/confidence:** P2, confirmed static. This is maintenance/debt rather
than an independent reason to delete working release code.

**Evidence:** active shell helpers hardcode targets/counts/buckets in
`deploy/deploy_website.sh:12` and `deploy/update_task_definition_image.py:77`;
`deploy/deployment_targets.py:370` separately owns reviewed environment values;
the much larger `deploy/release.py` / `aws_gateway.py` / `task_definitions.py`
controller remains wired into retained `ci.yml` machinery. Automatic prior
capture is explicitly disabled at `.github/workflows/ci.yml:1366`.

`_docs/runbooks/production-deployment-bootstrap.md:79` says the website's normal
deployment runs `deploy.cli promote`, smoke and recovery; `:116` says `ci.yml`
is the only deploying workflow. Neither describes today's active two-workflow
shell path. The sandbox runbook explicitly concerns a destroyed stack, but its
long procedural content remains near the current release instructions.
`deploy/deploy_website.sh:84` runs bare `python3`, contrary to the repository's
uv-only Python rule; neither active deploy job installs uv before this call.

**Bounded fix:**

1. After REL-01/02/06/08 establish the supported implementation, name one
   controller and one target-definition owner. Retain only small transport
   wrappers around it.
2. Label historical Gate-B/sandbox procedures as archived evidence with no
   executable current procedure; preserve artifacts needed to interpret past
   releases.
3. Rewrite the current runbook around the actual supported dev-then-production
   workflow, exact gates, recovery receipt and data-ingest prerequisites.
4. Add a concise command inventory linking each supported entry point to its
   owner and tests. Retire obsolete workflow inputs/branches only after mapping
   their users and tests.
5. Use setup-uv plus a pinned/frozen invocation for Python release tooling,
   or package that tooling in the reviewed runtime. Remove the bare-python
   exception instead of silently copying it into new scripts.

**Acceptance:** each deploy command in the current runbook maps to a live
workflow/CLI and the same target registry; an offline example reaches its safe
validation boundary; no duplicated runtime-target literal disagrees with the
registry; documented failure recovery exists and is tested.

**Dependencies:** follow the release correctness work. Do not start by deleting
the older controller, because it contains behavior the active path needs.

## Already documented debt and deliberate exclusions

The ingestion inventory and production migration runbook already describe
one-time staging artifacts, FAQ/docs source-builder gaps, continued editorial
sync migration, unknown/undecided upstream sources, and the planned removal of
disposable import machinery. Those are not presented here as newly discovered
defects. The database-only-content inventory remains authoritative for runtime
file-backed content violations. The content architecture audit should own the
full source-builder and active-content-sync duplication plan.

The paired production-write target flags are a useful existing boundary and
should be preserved. Import scripts using them do not become safe to run
against arbitrary production data merely because this report proposes fixes.
The account reconciliation mapping/apply workflow was inspected at its CLI and
artifact boundary; no mapping, merge or rollback was executed. The migration
runbook already records the lack of a general automatic merge rollback.

No full protected-data rehearsal, AWS request, GitHub dispatch, production
database query, registration export read, S3 write, reset/clean, or database
deletion was performed. Infrastructure trust policies, ECS circuit-breaker
settings, GitHub environment branch protection and real certificate/name
collisions were not verified live. They cannot be inferred from this report.

## Validation performed

1. `uv run --frozen python -m unittest core.tests.test_cmp_style_deployment`
   — **5 tests passed**. This demonstrates that the active contract suite does
   not catch the release gaps above.
2. `uv run --frozen ruff check deploy scripts/prod scripts/prepare_local_data.py scripts/verify_local_dataset.py --output-format concise`
   — **all checks passed**.
3. `uv run --frozen pytest .tmp/release-ingest-audit/test_reproductions.py scripts/tests/test_prod_write_target.py scripts/tests/test_prod_make_targets.py scripts/tests/test_prepare_local_data.py scripts/tests/test_prepare_local_data_order.py scripts/tests/test_verify_local_dataset.py -q`
   — **69 tests passed, 1,046 subtests passed in 53.47 seconds**. At this point
   the scratch reproduction file contained the four tests for REL-03, REL-04,
   REL-08 and REL-11. Their assertions deliberately describe the current bad
   behavior; passing means the defect was reproduced, not fixed.
4. `uv run --frozen pytest .tmp/release-ingest-audit/test_reproductions.py -q`
   — **6 tests passed in 44.93 seconds** after adding the bounded username
   collision and deployed-course guard probes (REL-12 and REL-05). Target
   configuration/source lookup were stubbed for the latter; its real local
   guard rejected the production runtime enum without opening a deployed
   connection. No failed reproduction harness assertion was counted as an
   application finding, and no application fix was made during these runs.

The first exploratory invocation of the existing focused suite finished, but
its final output was not retained; it is not counted as separate validation
evidence. The captured combined run above is the authoritative result.

Scratch reproductions live under
`.tmp/release-ingest-audit/test_reproductions.py`; they are not application code
or committed regression tests. Promote the relevant case into the owning test
module when implementing each fix, reversing the assertion to require the
corrected contract. Test harness databases/files remained in project-local
scratch storage.

## Suggested execution order and handoffs

1. **Migration integrity owner:** REL-03 and REL-04 share one script-owned
   import-run/mapping schema. Implement that contract together, then account
   importer, history importer and recovery tooling. Do not parallelize edits
   to the same two service files. REL-12 is a small subsequent isolated fix.
2. **Release owner:** REL-01 is independently actionable immediately. Design
   REL-02, REL-06, REL-07 and REL-08 around one controller and receipt schema.
   Preserve dev-then-production behavior and reviewed production task counts.
3. **Curriculum ingest owner:** fix REL-05 while coordinating current shared
   curriculum work; add the production configuration and PostgreSQL contract
   tests without a live deployment.
4. **Rehearsal owner:** REL-09 and REL-13 remove destructive/redundant orchestration.
   Then consume REL-14/15 gates. REL-10 can be implemented independently and
   exposed as the checkout helper this orchestration calls.
5. **Importer follow-up owner:** REL-11, REL-16 and REL-17 are bounded independent
   modules. REL-18 requires coordination with the existing content-release
   service owner.
6. **Documentation/consolidation owner:** finish REL-19 after the implementation
   choice is settled. Update command examples and remove claims contradicted by
   tested behavior; preserve historical evidence with clear archival status.

For each implementation, follow `_docs/PROCESS.md`: groom a bounded issue,
freeze the engineer handoff with the relevant verification plan, obtain
independent tester and PM acceptance, and only then use the authorized commit
and merge flow. The audit itself created no issues, commits, pushes or external
messages and authorizes none of those actions on its own.
