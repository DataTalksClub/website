# Backend, authorization, learner integrity, and durable work audit

Date: 2026-09-07. Baseline HEAD: `ca49f1bf1d8bd56b943f4f635f8a7f4f05c8b242` on `main`.

This is one part of the [repository audit](2026-09-07-repository-audit.md). It reports the working tree observed during the investigation, including existing uncommitted curriculum work. Source files changed concurrently outside this audit; use the named symbols as well as line references when implementing. No application fixes, production access, sends, commits, or remote changes were performed for this report.

## Reading the findings

`P0` means a must-fix before exposing the affected capability or completing production cutover; it does not claim an incident occurred. `P1` means an important correctness, reliability, or security follow-up. `P2` means a maintainability improvement. “Reproduced” means a synthetic local test demonstrated the stated behavior. “Static” means the implementation path was traced but the complete scenario was not executed. “Known migration gap” distinguishes target requirements from an unexpected regression in adopted code.

Normative references: [_docs/specs/04-courses-and-cohorts.md](../specs/04-courses-and-cohorts.md), [05-events-registration-email.md](../specs/05-events-registration-email.md), [06-studio-and-admin-api.md](../specs/06-studio-and-admin-api.md), [07-security-privacy-operations.md](../specs/07-security-privacy-operations.md), and [app boundaries](../architecture/app-boundaries.md). Implementation work still follows [PROCESS.md](../PROCESS.md); these task cards are grooming input, not authorization to bypass its review gates.

## Findings and implementation cards

### BE-01 — Django admin and impersonation bypass the Studio authorization boundary

Priority: P0 before production exposure. Confidence: reproduced for admin access and policy edge case; static for the full impersonation route. Category: known adopted management boundary gap.

Evidence:

- `website/urls.py:112` mounts `loginas.urls`, followed by `admin.site.urls`, without the capability/session adapter used by Studio.
- `website/loginas_policy.py:1` authorizes solely with `request.user.is_staff and not target_user.is_staff`. Its docstring promises to exclude superusers, but `is_superuser=True, is_staff=False` passes. An identical function lives in `course_management/settings.py:405`.
- `accounts/admin.py:6` registers `CustomUser` with a plain `ModelAdmin`. Default model permissions govern it, rather than the explicit Studio capability and staff-session checks in `accounts/studio_authorization.py:55`.
- `accounts/views/impersonation.py:11` exempts the stop action from CSRF.
- The installed `loginas` entry view already has POST and CSRF decorators. Its separately mounted `/admin/logout/` exit lacks those decorators and precedes the ordinary Django admin URL include. Both exit paths need coverage if the package include remains.
- Synthetic test: the same active staff session with no Studio evidence receives `/studio/` → 403 and `/admin/` → 200. A direct policy test accepts the nonstaff-superuser target.

Impact: disabling a Studio session or withholding a capability does not close every management entry point. A staff user can reach the impersonation policy without a capability check and act as another learner. The admin index reproduction does not establish that a permissionless staff user can edit every model; model permissions still apply. The policy nevertheless conflicts with spec 06's capability-scoped, read-only-by-default support view and separately protected superuser break-glass admin.

Fix plan:

1. Make production URL construction exclude both built-in admin and loginas unless an explicit, reviewed break-glass mode is enabled. Keep ordinary development/test behavior separately configurable.
2. If break-glass admin is required, enforce active superuser status, current approved staff authentication evidence, and an audit event at the site boundary. Do not grant it implicitly to all staff.
3. Replace unrestricted impersonation with the existing capability framework and read-only support views. Until that exists, deny loginas on the deployed application rather than adding another broad permission shortcut.
4. If temporary impersonation remains locally, deny inactive, quarantined, staff, and superuser targets explicitly. Preserve entry's existing POST/CSRF protection and enforce it for both exit paths, including the package's `/admin/logout/`. Consolidate the two policy functions only after comparing adopted-source constraints.
5. Add deployment system checks that reject production settings exposing an unguarded admin/loginas route.

Acceptance: an ordinary staff session, a revoked Studio session, and a staff user lacking the support capability cannot enter protected management through `/admin/` or loginas; an allowed break-glass principal satisfies the separate contract; unsupported methods return 405; a POST without CSRF is rejected. Exercise the production URL mode without real credentials. Depends on a break-glass policy choice only if retaining that surface; default closure is a bounded containment task.

### BE-02 — Live legacy tokens form a second, unscoped management authority

Priority: P0 before management cutover. Confidence: static; the legacy API is demonstrably live in the BE-14 reproduction. Category: known migration gap.

Evidence: `accounts/models.py:154` stores the entire legacy `Token.key`; `accounts/auth.py:485` looks it up directly, accepts a header after a permissive `replace("Token ", "", 1)`, and checks only durable resolution plus `is_active`. `api/safety.py:31` grants management writes for `is_staff or is_superuser`. `api/views/projects.py:97` and sibling course/homework routes use this path. In contrast, `management_api/authentication.py:63` checks scoped, expiring, revocable `APICredential` records and registered capabilities. Removing the legacy token admin registration does not disable existing tokens or consumers.

Impact: the new management credential lifecycle does not revoke or constrain legacy access. A legacy staff token can reach operations regardless of the new token's scope, expiry, or revocation. This is not a claim that learner tokens currently receive answer keys: question and submission export routes were inspected and explicitly require staff access.

Fix plan:

1. Inventory the live legacy route-to-capability mapping and client migration needs using route metadata; publish only counts and route names, never token values.
2. Route compatibility URLs through the same capability authorization and domain services as canonical admin endpoints. Preserve direct authorized responses where redirecting would drop credentials.
3. Implement a bounded legacy credential migration or explicit retirement date. Rotate clients to one-time-disclosed, hashed, scoped credentials; do not silently reinterpret a broad old token as a new unrestricted principal.
4. Deny retired token authentication at the shared decorator boundary, with safe 401/403 responses and no reflected header values.
5. Once consumers are migrated, remove raw-token persistence through a reviewed data migration and update compatibility documentation.

Acceptance: revocation and expiry deny the same operation at both canonical and compatibility paths; read-only scope cannot write through a legacy URL; inactive/quarantined principals fail closed; authorized course automation still works; no raw token reaches response metadata or logs. Split credential migration from individual adapter rewrites so each task has a small route set. Related to BE-01 and BE-08, but neither is fixed by closing the other.

### BE-03 — An unrelated open project's URL can authorize changes to a closed review

Priority: P0 for grading integrity. Confidence: reproduced.

Evidence: `courses/views/project_eval_submit.py:194` loads `PeerReview` by ID alone and verifies reviewer ownership. `project_eval_submit_page` at line 148 separately resolves the URL's course/project and takes that project's rubric. The POST gate at line 135 checks `page.project.state`, without binding it to either submission referenced by the review.

Reproduction: create a learner-owned review in completed project A; create peer-reviewing project B in the same cohort; POST to B's review URL with A's review ID. The synthetic request returns 302 and saves the changed note on A's review. This does not require accessing another learner's review. With distinct rubrics, the same missing binding can associate answers with the wrong project's criteria.

Fix plan:

1. Resolve the canonical cohort and project first.
2. Fetch the review with all ownership predicates: ID, reviewer student, reviewer project, and evaluated submission project must match that resolved project. Select related rows needed by the view.
3. Return a safe 404/denial for any mismatch before rendering context, creating enrollment, accepting votes, or saving responses.
4. Recheck the owning project's state inside the mutation transaction; coordinate with the service that closes/scorers the project so a stale page cannot race closure.
5. Keep vote updates bound to the same resolved submission. Do not treat URL-derived project context as an independent authority.

Acceptance: GET and POST with another project or cohort's path fail without changes; a valid owned review works; a different learner cannot read or write it; completed project A remains unchanged even when project B is open. Include a rubric that differs between A and B, not only same-cohort legacy criteria. No schema migration is needed for the first containment patch. BE-04 is a separate validator fix in nearby code; assign sequentially if one agent owns both.

### BE-04 — Peer-review choices can inflate scores or make scoring fail

Priority: P0 for grading integrity. Confidence: reproduced.

Evidence: `courses/views/project_eval_submit_save.py:90` validates criterion IDs but not answer values, choice cardinality, or option bounds. Line 104 persists the raw comma-joined values. `courses/models/project.py:405` converts each value to `int`, subtracts one, and sums the indexed options. `courses/views/project_eval_submit_context.py:153` also casts persisted values without a safe invalid-data path.

Reproductions: a radio criterion whose maximum is 3 accepts `4,4,4` and scores 9. `999` is saved and later raises `IndexError` in `get_score()`. A zero index addresses the last option through Python's negative indexing; nonnumeric strings can break subsequent view/scoring conversion.

Fix plan:

1. Normalize every answer against its assigned `ReviewCriteria` before any write. Define radio and checkbox rules explicitly; preserve the intended policy for omitted optional answers.
2. Reject non-integers, indices below 1 or above the option count, repeated indices, more than one radio choice, and oversized answer sets. Do not let numeric coercion silently change user input.
3. Save canonical answer values only after the complete rubric validates. Preserve the existing transaction at `project_eval_submit_save.py:36` covering responses and review state; the defect is missing answer-value validation, not absence of that transaction.
4. Make scoring defensive against previously stored invalid values: emit a safe diagnostic keyed by review/criterion ID and prevent an incomplete grading run from being declared successful. Do not silently assign an arbitrary corrected grade.
5. Add a read-only invalid-response inventory and a separately reviewed repair procedure for existing data. No blind deletion or automatic rescoring of historical learners.

Acceptance: valid radio/checkbox choices preserve scores; `0`, negatives, excessive indices, repeated values, strings, multiple radio selections, and forged criteria all fail atomically. An invalid stored record produces a controlled grading failure rather than an unhandled exception or inflated score. Tests belong with `test_project_criteria_integrity.py` and project scoring tests. Depends on no new infrastructure; coordinate the persistence portion with BE-03 and BE-12.

### BE-05 — Three time-spent parsers disagree and accept non-finite/negative hours

Priority: P1. Confidence: reproduced for shared parser and peer-review conversion; static for each downstream statistical effect.

Evidence: `courses/views/submission_formatting.py:50` accepts `nan`, `inf`, and negative floats. Homework uses that parser through `homework_submission_fields.py`; `project_submission_edit.py:192` uses `tryparsefloat` directly and can silently turn invalid input into `None`; `project_eval_submit_save.py:129` calls raw `float` and does not raise the `ValidationError` that its caller handles.

Reproduction: `parse_time_spent_hours("nan", "work")` returns NaN, `"inf"` returns infinity, and `"-2"` returns -2. Peer-review input `2,5` raises uncaught `ValueError` while homework accepts decimal commas. Browser number widgets do not protect the server from malformed POSTs.

Fix plan:

1. Introduce one domain-level optional-hours parser, keeping views as adapters. Support the existing decimal-comma convention consistently.
2. Require `math.isfinite(value)` and nonnegative hours. Add a maximum only if an owning product rule defines one; do not invent an arbitrary cap during cleanup.
3. Specify whether an empty edited field clears the old value or preserves it, then apply that choice consistently across homework, project, and review.
4. Return field-specific `ValidationError`s and preserve the raw bound form values on rejection.
5. Add model/service validation for non-view writes and a diagnostic for existing impossible values; repair historical data separately.

Acceptance: the same table of empty, zero, decimal dot/comma, negative, malformed, NaN, and infinity inputs has the same outcome on all three forms; errors never save partial data or cause a 500. Fix BE-06's error rendering before relying on it for project validation. This consolidation removes an observed behavior divergence, not merely repeated code.

### BE-06 — Invalid project submissions mutate other records and can fail during error rendering

Priority: P1. Confidence: reproduced.

Evidence: `courses/views/project_submission_edit.py:27` calls `project_submission_for_update`, which creates enrollment when absent, and saves `certificate_name` before URL/field validation. `project_submit_post` at line 43 has no enclosing transaction. `courses/views/project.py:74` handles `ValidationError` by calling the same mutating/validating parser again.

Reproductions: invalid repository input returns the form with no project submission, but the enrollment and certificate-name change persist. An invalid learning-in-public URL raises once, then raises again while building the error response, escaping the handler.

Fix plan:

1. Separate request parsing from persistence: construct a bound form or input object containing raw values without writes or network activity.
2. Validate all fields once. Error rendering consumes that bound object, never a function that saves an account or creates enrollment.
3. Inside one transaction, resolve/create enrollment as allowed by registration policy, apply any permitted certificate-name change, and save the submission. The immediate containment patch can preserve the existing after-commit callback behavior; it must not claim durable mail delivery.
4. Return the normal success redirect only after the transaction commits. After BE-13 supplies the delivery service, add its intent within the same transaction and replace the callbacks; that integration is a separate dependent task.
5. Maintain existing valid-value rendering and submission update semantics; do not redesign the entire course form shell.

Acceptance for immediate containment: every rejected field leaves submission, enrollment, certificate name, and audit-success events unchanged and schedules no callback; raw submitted values and actionable field errors remain visible; invalid learning links return a form response instead of 500; valid create/update preserves the existing callback contract. After BE-13, separately require one durable logical notification and unchanged intent/job counts on rejection. Coordinate nearby numeric changes with BE-05. Do not block the parsing/error-response fix on the larger mail migration.

### BE-07 — GET requests create/delete learner records, and invalid toggles create enrollments

Priority: P1; fix before exposing these mutation links broadly. Confidence: reproduced for destructive GET and invalid toggle; static for enrollment GET.

Evidence: `courses/views/project_eval_actions.py:80` and `:119` only require login, so `projects_eval_add` and `projects_eval_delete` mutate on GET. Existing tests in `courses/tests/test_project_optional_eval.py` intentionally use GET. `courses/views/course_enrollment.py:31` creates enrollment before validating `field`; `enrollment_view` creates one before checking the method. `project_eval_submit_context.py:31` also creates enrollment while preparing a page.

Reproductions: GET of an optional-review deletion URL returns 302 and deletes the review and its criterion responses. An unsupported enrollment-toggle field returns 400 after creating enrollment.

Impact: navigation, prefetching, and a cross-site top-level GET can change state without a CSRF-protected mutation. Merely opening a settings page can enroll a user or trigger enrollment signals. A rejected request can still change counts and user state.

Fix plan:

1. Add `require_POST` to review add/delete and retain Django CSRF protection. Replace every invoking anchor with a small POST form and CSRF token; update JS and compatibility links deliberately.
2. Validate the target submission belongs to the project and is not the learner's own submission before creating volunteer/enrollment records. Return 404 on missing/mismatched targets, not an uncaught `.get()` exception.
3. Render enrollment settings from an existing or unsaved object on GET. Create enrollment only through the accepted registration/enrollment mutation.
4. Parse and allowlist toggle inputs before any write. A preference endpoint should require an existing enrollment unless its documented product contract expressly permits enrollment creation.
5. Remove writes from review context construction; a resolved valid review already supplies authoritative participation relations.

Acceptance: GET/HEAD on mutation endpoints returns 405 and changes no tables; POST without CSRF fails; valid POST add/delete works; invalid/missing/self/cross-project targets leave enrollments and volunteer submissions unchanged; loading enrollment/settings/review pages is read-only. The existing tests that encode GET mutations must be updated intentionally. UI details are owned here to avoid duplicating the UX report.

### BE-08 — Identity quarantine blocks new login but does not consistently revoke existing access

Priority: P1, or P0 when quarantine is used as an immediate containment control. Confidence: reproduced for an existing private session; static for all alternate authentication paths.

Evidence: `accounts/backends.py:25` excludes quarantined accounts during authentication, but `accounts/middleware.py:20` only handles absorbed identities. `accounts/identity_resolution.py:155` returns any non-absorbed user unchanged; its alias helper at line 150 excludes inactive/absorbed survivors but not quarantined survivors. The legacy token decorator only checks `is_active`. `management_api/authentication.py` checks linked-user activity, without identity-state eligibility.

Reproduction: log in a synthetic account, change only `identity_state` to quarantined, then POST the private account toggle. The response remains 200 and the account setting changes. No claim is made about a disabled account: `is_active=False` already has separate denial behavior.

Fix plan:

1. Define one account eligibility predicate for legacy/active versus quarantined/absorbed/disabled identities; reuse the stricter rules already present in `resolve_accounts_by_email`.
2. Apply it when resolving an existing session and token principal, not only on initial login. Flush or deny the affected session with a safe reason code.
3. Require alias survivors to be eligible; reject quarantine/alias chains consistently across browser, social, legacy token, and human management credential paths.
4. Clear both `request.user` and cached authentication state when invalidating a session.
5. Confirm whether quarantine is intended to revoke all access; the current login implementation suggests that interpretation. If a restricted review-only state is desired, define its explicit capabilities instead of retaining ordinary access accidentally.

Acceptance: after quarantine, existing private sessions and human/legacy tokens cannot mutate or retrieve protected account data; resolving an absorbed identity to a quarantined survivor fails; active reviewed survivor continuity still works; denials contain no identifying fields. Keep service principals without a human owner governed by their own rules.

### BE-09 — Email authentication scans every eligible account in Python

Priority: P1 for an imported membership database. Confidence: static complexity finding, not a measured production benchmark.

Evidence: `accounts/backends.py:34` normalizes the supplied email, iterates `self._eligible().order_by("pk").iterator()`, and normalizes/compares every account. The model already has an indexed `normalized_email` field. The batch resolver in `accounts/identity_resolution.py:36` queries that field directly.

Impact: each email/password attempt incurs work proportional to the total account count, including unknown addresses. Importing more learners makes sign-in slower and increases request-side database/CPU load. `iterator()` limits materialization but does not eliminate the full scan.

Fix plan:

1. Finish/verify the normalized-email backfill in an explicit migration or ingest phase; preserve duplicate detection and quarantine policy.
2. Query the indexed normalized key and fetch at most the number of candidates needed to detect ambiguity, including unavailable collisions where the identity contract requires denial.
3. If null legacy keys must remain temporarily supported, constrain fallback to that population and give its removal a bounded migration condition; do not reintroduce an all-user scan.
4. Reuse the accounts-owned normalization/resolution logic where its semantics match. Keep username compatibility a separate bounded lookup.

Acceptance: active/legacy, case-normalized, unknown, duplicate, inactive, and quarantined scenarios preserve intended outcomes. Inspect the generated query for a normalized-email predicate and bounded result set; a query-count assertion alone would miss the full-table scan. Use synthetic data for scaling checks and never print candidate emails/passwords.

### BE-10 — A repeated unsubscribe can reuse an exhausted job that will never run

Priority: P1. Confidence: reproduced with a terminal job and a repeated accepted request.

Evidence: `email_app/services.py:56` replaces a row only when `PendingUnsubscribe.status` is nonpending. A job exhausted by `jobs/execution.py` remains failed while its domain row stays pending. Reacceptance reuses the same pending ID and deduplication key at line 75. `jobs/dispatch.py:77` returns the existing immutable intent; terminal jobs are not claimable. `email_app/jobs.py:36` also permanently fails an unconfigured Relay without settling the pending row.

Impact: after a sufficiently long outage or misconfiguration, the user can receive another accepted response while no runnable work exists to apply the opt-out. The positive guarantee in the service docstring therefore exceeds the recovery behavior. Existing retry tests cover transport failures but not domain/job terminal-state divergence.

Fix plan:

1. Represent replay generation and durable job association explicitly on the pending intent, or introduce a bounded service that creates a new generation when the associated job is terminal and work remains unapplied.
2. Preserve a pending opt-out across failures and expose safe age/terminal-job diagnostics. Do not delete it merely to make monitoring green.
3. On a fresh user request or audited operator recovery, create a new immutable job key exactly once under concurrency. Do not reset a leased/terminal job behind the job framework's state rules.
4. Define recovery for configuration restoration and long outages; keep immediate replay idempotent at Relay.

Acceptance: simulate job exhaustion and missing configuration, restore availability, submit again, and prove runnable work exists and eventually applies the pending opt-out. Repeated requests create one fresh generation, token data stays out of job payloads/logs, and ordinary retries do not multiply jobs. Coordinate model changes with BE-11.

### BE-11 — A worker can delete a newer unsubscribe choice while finishing an older one

Priority: P1. Confidence: reproduced with a deterministic network-call interleaving.

Evidence: `email_app/services.py:67` changes the scope on the same pending row. `replay_pending_unsubscribe` reads scope at line 96, calls Relay outside a transaction at line 102, then unconditionally deletes by primary key at line 111 after success. The accepted scope has no revision check.

Reproduction: pending scope is `client`; the worker begins sending it; during the mocked network call the recipient requests `global`, updating the same row. The old `client` call succeeds and the worker deletes the row containing `global`. The newer choice has neither a row nor independent runnable job.

Fix plan:

1. Add an intent generation/version that changes whenever the desired scope changes; capture the version with the worker's read.
2. Make success acknowledgement conditional on the version/scope actually sent. An older completion must not delete or settle a newer generation.
3. Ensure a new scope schedules independent runnable work even if the previous job is already leased. Prefer immutable generation-specific payload identifiers and deduplication keys.
4. Increment attempt counters atomically and retain safe outcome metadata for the acknowledged generation only.
5. Do not hold a database lock across the Relay HTTP request. Keep eventual replay idempotent.

Acceptance: reproduce both narrow-to-wide and wide-to-narrow changes during a blocked transport call; the final accepted choice remains pending until Relay acknowledges it. Old successes/rejections cannot erase newer work. Include concurrent reacceptance plus terminal-job recovery from BE-10.

### BE-12 — Learner uniqueness and vote-budget invariants are missing at the write boundary

Priority: P1; grading migration prerequisite. Confidence: static schema/write-path analysis. Category: explicitly documented migration debt in spec 04's invariant preflight requirement.

Evidence: `courses/models/project.py:109`, `:358`, and `:393` define `ProjectSubmission`, `PeerReview`, and `CriteriaResponse` without uniqueness constraints for their logical identities. `courses/models/homework.py:273` and `:324` similarly lack submission/answer uniqueness. The writers use `.first()`, `get_or_create`, and `update_or_create`, which do not supply cross-request uniqueness without a constraint. `courses/votes.py:12` separately counts votes and inserts; its `(submission, voter)` unique constraint prevents a duplicate vote on one submission, but cannot enforce the three-vote budget across distinct submissions.

Impact: concurrent creates can produce multiple logical submissions, review pairs, or answers; later `.get()`/`update_or_create()` calls can raise multiple-row errors or aggregate duplicate scores. Two concurrent requests observing two votes can each insert a different third vote, exceeding the intended budget. No production duplicate count or PostgreSQL race was measured.

Fix plan, split into small tasks:

1. Build a read-only preflight keyed only by record/user IDs for duplicates and inconsistent student/enrollment/course references. Do not print answers or personal data.
2. Decide canonical logical identities for normal versus volunteer submissions and optional versus assigned reviews. Use explicit approved merge/quarantine rules for existing conflicts.
3. Add database unique constraints after a safe migration preflight. Likely identities include `(homework, student)`, `(submission, question)`, `(project, student, volunteer_review_only)`, review pair, and `(review, criteria)`; confirm business semantics before locking these in.
4. Keep writes in owning services and handle uniqueness races by deterministic reread/retry. Validate same-course/student relations that simple foreign keys cannot express.
5. Enforce the vote budget by serializing mutations on a stable per-voter/project parent or using a database-backed budget/CAS mechanism. `transaction.atomic()` around count-plus-insert alone is insufficient. Preserve SQLite portability and isolate any deployed-engine verification to the approved test gate.

Acceptance: sequential duplicate creation is rejected by the DB; repeated valid requests remain idempotent; parallel vote attempts cannot exceed the cap; unvote releases capacity; optional and assigned reviews cannot double-count a peer pair; the preflight refuses ambiguous legacy data without deleting it. Dependencies: reconciliation/preflight before constraint migrations. BE-03/BE-04 can be contained independently first.

### BE-13 — New sends still use Datamailer and the logical EmailDelivery boundary is absent

Priority: P0 for Relay/email cutover, not a claim of an observed production send. Confidence: static. Category: known migration gap.

Evidence: `email_app/models.py:1` explicitly says `EmailDelivery` is deferred; the only model there is `PendingUnsubscribe`. `courses/views/project_submission_edit.py:83` registers callbacks after saving, but persists no delivery intent atomically with the submission. `courses/views/project_confirmation.py:11` imports `send_transactional_email` from the adopted Datamailer implementation. `course_management/datamailer/sync/transactional.py:14` invokes `DatamailerClient.transactional.send_transactional`; `client.py:98` performs a direct HTTP request when configuration is present. Membership sync also calls Datamailer. Spec 05 requires send-disabled history, an atomic logical delivery/job, and Relay-owned transport.

Impact: enabling the legacy configuration still permits new transport work. A crash between business commit and callback loses the notification; callback failure can surface after the submission is saved. Merely naming a callback `on_commit` does not create durable work. This report does not assert duplicate sends occurred or that a configured live sender exists.

Fix plan:

1. Separate read-only history/reconciliation from every write/send/requeue entry point. Add one explicit fail-closed guard for new legacy sends, including management commands, signals, outbox dispatch, and compatibility API actions.
2. Implement the spec-owned `EmailDelivery` logical intent model and service, with immutable business version/idempotency key, recipient/purpose validation, and a redacted transport projection.
3. Commit the delivery intent and `DurableJob` atomically with each business mutation. Start with one approved course purpose rather than converting every template at once.
4. Have the leased worker resolve only approved routing and call Relay after commit. Preserve ambiguous acknowledgement as an explicit unresolved state; do not auto-resend unknown outcomes.
5. Migrate callers in bounded groups: registration/submission, grading, reminders, certificates. Remove direct Datamailer write calls from each group and retain history reads.
6. Add purpose-specific preference and recipient checks from spec 05. Unknown purpose/sender/template version fails closed. Coordinate preference ownership with BE-15.

Acceptance: a synthetic transport spy sees no new Datamailer sends anywhere in the supported application; rollback of a business transaction leaves no intent/job; a post-commit crash does not lose the intent; replay cannot create duplicate logical mail; a timeout after Relay acceptance becomes ambiguous; suppressed/unknown recipients never reach transport. This is a multi-issue dependency chain requiring approved Relay contracts, not a single cleanup patch for a small model.

### BE-14 — Compatibility JSON parsers accept shapes their callers cannot handle

Priority: P1. Confidence: reproduced for a live project PATCH with an array payload.

Evidence: `api/utils.py:32` accepts any valid JSON value. `api/crud.py:149` forwards it to `patch_instance_response`; `api/safety.py` iterates `data.items()`. Creation paths such as `api/views/project_create.py:64` immediately use `.get()`, and bulk response code assumes each item is an object. A body-size limit does not validate JSON shape or field type.

Reproduction: an authenticated synthetic staff legacy token PATCHes an existing project with `[]`; an uncaught `AttributeError` escapes the view. Other scalar/object-field combinations need route-specific regression cases rather than assumptions that every route fails identically.

Fix plan:

1. Add a shared object parser for single-object operations and a separately named bounded object-list parser for documented bulk operations.
2. Reject arrays/scalars/null for PATCH and upsert routes expecting objects. Validate bulk item types before any mutation and report safe indexed errors.
3. Validate strings, booleans, state values, dates, and finite numbers before assigning to models. Reuse the canonical management JSON boundary where response compatibility allows it.
4. Define partial-versus-atomic bulk semantics explicitly; do not accidentally persist early valid objects before discovering malformed later items.
5. Keep malformed request values out of reflected error messages and logs.

Acceptance: object, array, number, string, boolean, null, invalid UTF-8/JSON, heterogeneous bulk items, and wrong field types produce controlled documented responses; no 500 or unexpected partial write. Valid legacy clients retain their response contract. Coordinate adapter retirement with BE-02 so this does not create a third parser framework.

### BE-15 — Newsletter preference migration has a stale-snapshot and specification boundary to resolve

Priority: P1 before importing preferences into a live writable audience. Confidence: static; known scope/specification mismatch, not a claim of unauthorized sends.

Evidence: `accounts/services/mailchimp_subscription_import.py:1` documents an earlier owner-approved subscribed-only scope; `_process_subscribed_file` at line 148 sets every matched false `newsletter_subscribed` value to true. It stores no source snapshot time, preference change time, or provenance. `accounts/models.py:81` defaults the field true. The current spec 05 separately calls for subscribed and unsubscribed imports and preservation when absent from both. Meanwhile `accounts/views/email_preferences.py:15` exposes only three course categories via Datamailer, and recipient unsubscribe goes to Relay; there is no demonstrated unified newsletter-preference authority at this seam.

Impact: replay of an older subscribed export overwrites a newer false local value. Repeating the importer is idempotent only when no intervening preference change occurs. Also, current code cannot perform the newer spec's unsubscribed-list operation. The existing subscribed-only behavior was deliberately scoped; a smaller model must not silently reinterpret that history or assume Mailchimp, Relay, and the local field are already synchronized.

Fix plan:

1. Groom a preference-authority decision against the current spec: name the canonical active preference service and define migration-cutoff behavior for historical Mailchimp snapshots.
2. If imports can run after live preference changes, store source identity/as-of evidence and preference mutation provenance; reject stale replays or ensure later recipient opt-outs win according to that contract.
3. Implement subscribed and unsubscribed operations separately if the current spec remains authoritative. Update the deliberately older importer docstrings and runbook together.
4. Connect member settings and recipient-link behavior to the same approved preference authority; do not add an independent fourth toggle or treat newsletter consent as a course/event preference.
5. Use synthetic snapshots to verify behavior. Do not load production audience exports merely to reproduce a policy-ordering issue.

Acceptance: subscribed then explicit opt-out then stale replay preserves the newer decision according to the approved migration policy; unsubscribed import changes only the intended preference; absent contacts retain state; duplicate/unknown identities follow the account resolver contract; repeated same-snapshot runs converge without new delivery work. Depends on BE-13's routing/purpose work for actual send enforcement, but migration-cutoff protection can be defined first.

### BE-16 — Quality gates exclude whole app trees, including new integration code

Priority: P2 for incremental coverage, P1 when introducing new security-sensitive code into an excluded tree. Confidence: static. Category: intentional adoption baseline with a growing maintenance cost.

Evidence: `pyproject.toml:48` excludes `accounts`, `api`, `courses`, `studio_courses`, `course_management`, `data`, `e2e`, and `scripts` from default Ruff traversal. `pyproject.toml:74` silences mypy errors across the adopted app modules. Make's explicit integration/production-import lists reinclude selected files, not the entire evolving surface. Several findings above sit in excluded code.

Fix plan:

1. Keep adopted source provenance explicit; do not turn this task into whole-repo reformatting.
2. Extend explicit lint/typecheck opt-ins for newly authored or substantively changed modules as each bounded fix lands. Separate stable adopted files from new integration files.
3. Add a CI policy/test that newly added application modules must be covered or carry a reviewed, narrow exemption. A directory-wide historical exception should not silently exempt all future code.
4. Fix each opted-in module's actual issues in the owning change; do not add broad ignore rules solely to get a green result.

Acceptance: representative new files in excluded app directories are checked; existing reviewed baseline exemptions remain traceable; the scope of a green gate is reported accurately. No claim is made that adding typechecking alone would catch the authorization or race conditions in this report.

### BE-17 — Project results crash for legitimate empty and no-review states

Priority: P1. Confidence: reproduced through routed synthetic requests.

Evidence: `courses/views/project_results.py::_project_results_context` calls `_project_results_scores(submission)` even when the authenticated reader has no non-volunteer submission. The helper dereferences `submission.project`. The template already contains the intended "You did not make a submission for this project" state, but the view crashes before reaching it. Separately, `annotate_scores_with_option_votes` indexes `votes_by_criteria[score.review_criteria_id]` even when that criterion has no responses. The supported no-review path in `courses/project_review_scores.py::calculate_median_score` legitimately creates evaluation scores without response rows.

Reproduction: (1) Sign in as a synthetic learner who did not submit, then GET the cohort/project results URL: `AttributeError` instead of the existing empty state. (2) Generate the existing submitter's scores with `calculate_median_score`, save them, leave submitted review responses absent, and GET the same learner's results: `KeyError` during option-vote annotation. These are valid states, not malicious criteria values or corrupt records. They are independent of BE-04's input-validation fix.

Fix plan:

1. Give the context an explicit no-submission branch, preserving `is_authenticated=True`, course/project context, `submission=None`, and empty scores/feedback. Do not query results with a null submission as a substitute for that branch.
2. Keep the existing template empty state and own-submission filtering. Do not auto-create an enrollment/submission on this GET or reveal another learner's result.
3. When a score has no vote responses, use an empty vote map and render zero votes for each option. Make `_score_option_vote_counts` read `option_votes.get(index, 0)`, or deliberately supply a zero-default mapping; passing an ordinary empty dictionary to its current indexed read would merely move the `KeyError`. Preserve the awarded fallback score; zero votes does not mean a zero score.
4. Add tests to `courses/tests/test_project_results.py` for authenticated non-submitter, volunteer-only submitter, no-review fallback scores, an individual criterion without responses, and the existing populated vote-count case. Also preserve the anonymous sign-in message.

Acceptance: every legitimate state returns 200 with the intended empty/result message; score ordering and option totals remain correct; no GET creates records; only the reader's own result is displayed. Ownership is `courses/views/project_results.py` and its tests; template changes are only needed if the existing empty state proves insufficient. No schema change, historic-data repair, new score calculation, or credential migration is required.

## Evidence ledger and limits

Safe Django system check:

```bash
PUBLIC_MEDIA_STORE_BACKEND=memory DTC_TEST_RUN_ID=audit-root-20260907 \
  DJANGO_SETTINGS_MODULE=website.settings.test \
  uv run --frozen python manage.py check
```

Result: `System check identified no issues (0 silenced).` This verifies configuration checks, not release readiness or the behavior findings.

Synthetic backend probes are in `.tmp/audit-20260907/test_backend_reproductions.py`. They deliberately assert the observed broken behavior; passing means a defect was reproduced, not fixed. They use the owned test database and mocked transport, with no live recipients or remote calls. Durable reproduction descriptions are included in the findings because `.tmp` is not a tracked deliverable.

The initial eight probes passed. A later expanded run had twelve passing probes and one fixture error: the constructed failed job omitted its required `completed_at`. The fixture was corrected to satisfy the existing job constraint; that intermediate failure was not an application finding. The final combined command was:

```bash
PUBLIC_MEDIA_STORE_BACKEND=memory DTC_TEST_RUN_ID=audit-backend-complete-20260907 \
  uv run --frozen pytest .tmp/audit-20260907/test_backend_reproductions.py \
  courses/tests/test_project_criteria_integrity.py \
  email_app/tests/test_unsubscribe_replay.py \
  core/tests/test_course_platform_policy.py -q --tb=short
```

Result: **29 passed, 3 subtests passed in 40.48 seconds**. This combines 15 synthetic defect probes with 14 existing regression tests. It confirms BE-01's two bounded cases, BE-03/04/05/06/07/08/10/11/14 reproductions and the surrounding tested baseline; it does not certify fixes. All final probes use valid model-state fixtures.

Default-scope Ruff plus selected integration/production-import files reported nine findings during the initial snapshot: import ordering/unused import checks and invalid minimal notebook fixtures in the new shared-curriculum work, including generated `shared-lesson/` paths. All referenced files were pre-existing work in progress; no auto-fix was applied. Subsequent edits outside this audit can change that count. Use the architecture report for the substantive asset-storage issue behind the generated paths.

The completion pass added `.tmp/audit-20260907/test_results_reproductions.py` for BE-17. `DTC_TEST_RUN_ID=audit-results-20260907 PUBLIC_MEDIA_STORE_BACKEND=memory uv run --frozen pytest .tmp/audit-20260907/test_results_reproductions.py courses/tests/test_project_results.py -q --tb=short` completed with **3 passed in 27.43 seconds**: two defect probes plus the existing populated-results regression. Its log is `.tmp/audit-20260907/results-probes.log`.

The later configured `make typecheck` checked 546 source files and reported five errors in the pre-existing shared-curriculum test WIP. Final `make lint` reported six findings, while migration drift and database portability passed. See the [coverage ledger](2026-09-07-audit-coverage.md) for the commands, qualifications, and logs.

No full Django suite, deployed PostgreSQL concurrency test, live security scan, cloud configuration inspection, or real-provider delivery was performed by this audit owner. Existing strict management authentication, durable job fencing/dispatch, answer-key staff checks, response body bounds, account normalization, and synthetic test safety were inspected; the findings do not imply those mechanisms are absent. The supplemental CI report separately identifies limits in test-safety enforcement.

## Recommended backend execution batches

1. Contain alternate authority and grading defects: BE-01, BE-02 mapping/containment, BE-03, BE-04. These have separate boundaries; review/criteria files need sequential ownership.
2. Repair learner writes and feedback: BE-06, then BE-05; BE-07 can proceed separately except shared templates/context helpers. BE-17 is a narrow independent result-context correction.
3. Repair current identity continuity: BE-08, then BE-09 with preserved duplicate handling.
4. Version unsubscribe intent and recovery together: BE-10 and BE-11, with independent tests for both failure modes.
5. Preflight and constrain learner data: BE-12; never add historical uniqueness constraints without first resolving conflicting data.
6. Complete larger cutover contracts in groomed slices: BE-13, BE-15, remaining BE-02 compatibility migration. Apply BE-14 to retained endpoints. Expand BE-16 checks as modules change.

For every task, give the implementing model the specific finding, owning files/symbols, acceptance cases, current base SHA, and allowed scope. Require a separate reviewer to run the new adversarial case; a success-path test alone does not demonstrate that these fixes work.
