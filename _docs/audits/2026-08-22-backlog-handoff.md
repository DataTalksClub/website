# Backlog handoff — 2026-08-22 (continued 2026-08-23)

This note records the current backlog state and the safe continuation point for
the next agent. It is an internal engineering handoff; it is not product
acceptance or permission to bypass the repository lifecycle.

## Repository state

- `origin/main` is `efda44d` (`Retire CMP upstream drift workflow`).
- The shared root is a dirty `issue-216-engineer` checkout with mixed podcast,
  course-repository, projection, and test changes. Preserve it; do not use it
  as an engineer or tester worktree.
- Changes must follow `_docs/PROCESS.md`: groomed issue, isolated engineer
  worktree, independent tester evidence, PM acceptance, then local merge,
  commit, push, and deployment verification.

## CMP deployment reconciliation — 2026-08-23

- The sibling `course-management-platform` checkout was pulled with
  `git pull --ff-only` and is clean at `a9e7dbf` (`Update production versions:
  20260822-125047-c3b35e3`). The deployed functional commit is `c3b35e3`
  (`Show Not submitted status after failed project submission`). The full
  source-pin-to-head delta is 40 files, `+2,010/-43` lines, and also includes
  peer-review visibility (`80d67df`), system project-evaluation fallback
  (`894696c`), score hiding (`83242da`), and the deployed version marker. The
  website adoption decision must consider that whole delta, not only `c3b35e3`.
- This confirms the CMP deployment, but it does not authorize or prove website
  adoption. The website remains source-pinned to
  `98a235283904b4ef9ad29e196298540756cf1bcc`; its adoption ledger and frozen
  compatibility evidence still describe that baseline.
- The existing website-side `has_submission` adaptation is feature-correct in
  focused and independent checks, but its official envelope remains failed on
  the four candidate-introduced adoption-ledger drifts and the full Playwright
  timeout. Keep it out of the merge/PM gate until a groomed adoption/evidence
  issue refreshes only the affected rows and the official envelope passes.
- Fresh bounded audits for #14, #23, and #22 are complete and recorded. The
  completed lane results and the next safe continuation point are listed
  below; until those decision paths are accepted, do not move the source pin,
  copy the CMP tree literally, or rerun deployment from this backlog audit.

## Backlog continuation — 2026-08-23

- Five inherited-model read-only lanes were run in parallel: #191, #54, #218,
  #9, and Relay #2. Their results are recorded below; all remain blocked or
  decision-only, and no engineer candidate was authorized.
- The #65 and #66 follow-up questions were audited locally after the agent
  quota was reached. #65 needs a fresh current-main tester/manual evidence
  refresh; #66 needs #26’s approved SLO/RPO/RTO matrix before any child work.
- Replacement agent spawns were rejected by the five-thread runner limit. No
  files, issues, CI, AWS, provider, deployment, commit, or push mutation was
  made during this continuation. Preserve the dirty root and all unrelated
  worktrees.

## Parallel lanes

No agent lanes are currently active. All five requested parallel audit slots
have completed, and the runner rejected replacement spawns after reaching its
five-thread quota. The completed results are recorded below; the next bounded
backlog question is a documentation-only course canonical-route ownership
record, which remains blocked on owner decisions.

The #65 WCAG/critical-flow and #66 observability/SLO/backup-restore audits,
plus Relay #2, were handled as read-only local/agent audits after the quota was
reached; their results are recorded below. No code, GitHub, AWS, provider, or
deployment mutation was made.

Completed lane reports remain recorded below; they are not active agents. When
a lane completes, close it, record its evidence, and immediately replace it
with the next bounded backlog question. Do not merge a candidate from a tester
report alone: PM acceptance and the required operational gates are also
needed.

## Decisions already established

- #218 is groomed but not implementation-ready. The former `courses-work`
  lineage is already on `origin/main` (including commits marked `Refs #218`),
  but #218 remains OPEN/unassigned with 0/23 checks complete and no engineer,
  tester, or PM acceptance. It still needs the Course → Cohort decisions and
  dependencies (#14, #15, #51, #53, #55, #56, and #60), plus fail-closed
  migration, API, Studio, flow-rendering, and accessibility acceptance
  evidence. Do not select the stale phase-two worktrees as candidates.
- The fresh #218 audit confirms no stale phase-two candidate is usable: the
  clean `issue-216-phase2` worktree is at `3220ced`, 127 commits behind and
  one commit ahead, while the former `courses-work` tip already landed without
  #218 lifecycle gates. Current main still lacks reviewed mapping input,
  preflight counts/checksums/quarantine, production-like migration evidence,
  and accepted decisions for criterion reuse, project-flow placement, Unit
  metadata, and history/export behavior. Groom a PM-owned contract-closure
  phase before any implementation or branch reuse.
- #149 remains blocked on a named course-domain decision for system
  evaluations, peer-review assignment visibility, score visibility, and
  notifications. Do not move the CMP source pin or copy CMP files literally.
- #112’s independent tester found an uncaught `TypeError` when the historical
  source registry contains mixed key types. Engineer retry3 filters unsafe keys
  before sorting and passes focused checks; the fresh independent tester then
  timed out in the full Playwright component, so PM acceptance is still
  pending.
- #133’s current-head tester passed its feature-focused checks (2,480 Django,
  209 core Playwright, 7 focused browser, 2 Studio, 601 quality, container,
  and 24 synthetic screenshots) but could not complete the official envelope.
  Do not promote it to PM acceptance until the shared fixture/identity/evidence
  contract is repaired and rerun.
- The #112 runner diagnosis and #210 tester independently reproduce the same
  accessibility-registry diagnostics, stale-owner/setup failures, and missing
  terminal Playwright summary on `efda44d`. Keep both candidates in tester
  FAIL; route the shared runner issue to #76 before rerunning any PM gate.
- The current #112 audit confirms the aggregate implementation is merged, but
  the latest Studio-redaction candidate remains an uncommitted five-file diff
  with an independent tester `FAIL`: the required full `make test-playwright`
  timed out after 3,600 seconds. Preserve the current and superseded dirty
  #112 snapshots; no archive or merge is authorized until a named tester
  completes the official gate and PM accepts it.
- #76 is not engineer-ready: it is a broad release parent with missing
  contract/owner/artifact matrix and open #63/#64/#65/#66/#73/#74/#77 gates.
  Groom a separate child for socket-stall reproduction, owner isolation,
  descendant draining, and timeout evidence before implementation.
- #210’s independent tester passed CI, screenshots, evidence validation, and
  descendant cleanup, but the full Playwright run timed out and the official
  envelope also hit stale-owner setup state. The candidate remains unaccepted;
  #112’s runner diagnosis now has corroborating evidence from #210.
- The fresh #210 audit keeps the issue OPEN/rejected for acceptance. The current
  dirty candidate has no reusable full-browser terminal result: the direct run
  stopped at `test_complete_accessibility_registry` with exit 124, the official
  runs hit stale runtime ownership, and the host is at 97% filesystem use with
  long-lived browser/Xvfb/server processes. Establish owner-attributed runtime
  and storage preflight before any new tester run; do not accept smoke-only
  evidence or clean the candidate worktrees blindly.
- #165 is implemented, merged, independently tested, and PM accepted, but
  remains OPEN/REOPENED because exact-SHA deployment/live evidence failed at
  the pre-mutation recovery checkpoint; #102/#191 owner decisions remain.
- #198 is implemented, tester-passed, PM-accepted, and merged. Its homepage
  contract is present on `origin/main`; it remains open only because the
  current deployment failed before mutation and `/health/ready` was not
  healthy, so no duplicate homepage implementation is warranted.
- #212 is implemented, tester/PM accepted, and merged. Its fail-closed
  selection and aggregate observability are on `origin/main`; closure remains
  blocked only by the current exact-SHA deployment/readiness gate.
- #213 is implemented, tester/PM accepted, and merged into `origin/main`, but
  GitHub reopened it after the qualifying push/release run failed. The current
  compatibility-selection contract is green, including scheduled execution;
  the remaining gate is exact-merge-SHA deployment/live evidence. No
  issue-213 worktree is registered, and historical candidates are preserved
  only in named stashes and `.tmp` audit artifacts.
- The fresh #213 release audit confirms that the CMP dry-run `32553205352` did
  not deploy anything, while current-main push run `32566343252` passed CI,
  image publication, and `ci-gate` but failed before mutation because captured
  terminal counts differed. Scheduled runs `32574642766`, `32585538218`, and
  `32597445447` passed tests only; live still serves `11b2bd`. Keep #213 open
  and route the controller mismatch to the release owner.
- #191 remains OPEN/P0/BLOCKED before engineering. The RDS-managed master
  credential can rotate independently of the static application `DATABASE_URL`
  secret, but no owner has selected the source-of-truth, refresh boundary,
  alert, IAM, or redacted verification mechanism. Do not mutate AWS, secrets,
  Terraform, or deployment state from this backlog lane.
- #72 remains OPEN/P0/incomplete. Current inventories are useful slices but no
  accepted project-wide traceability matrix, owner register, ADR set, or
  fail-closed freshness/exception validator exists. A documentation-only child
  may freeze that matrix at `efda44d`; migration, import, redirect, and runtime
  work remain blocked by the listed decisions.
- #204 remains CLOSED/COMPLETED. Its accepted contract requires the current
  Homework/Projects headings and rejects the retired accordion; the accepted
  commits are ancestors of `origin/main`. The current deployment checkpoint
  failure is a release/infrastructure problem, not a reason to change or rerun
  #204.
- #74 remains OPEN/P0/NO-GO. The exact-current-main publish succeeded for
  `efda44d`, but deploy stopped before mutation at the ECS
  `service_running_mismatch`/terminal-count checkpoint; live serves the older
  `11b2bd1` release, `/health/ready` is 503, and HTTP smoke never ran. Keep
  production/cutover unchanged until the release owner repairs the ECS
  capture mismatch and obtains fresh exact-SHA live/ready evidence.
- The fresh #74 audit confirms the live image is still
  `20260821-062421-11b2bd1` (digest `sha256:36922ec4…`): `/health/live` is 200
  on that old image, `/health/ready` is 503, and scheduled green tests do not
  constitute cutover evidence. #213’s CI boundary is separate and does not
  clear #74. Keep deployment halted until one authorized release proves the
  exact current SHA with both health endpoints at HTTP 200.
- #202 is implemented and technically tester/PM accepted on `origin/main`,
  but its exact-SHA on-call release failed before mutation at
  `service_running_mismatch`. Fresh evidence also found that the runner can
  fail to record schema-valid envelopes for failed `content_invariants` or
  irregular artifacts; do not re-open or re-engineer #202 for that separate
  child.
- The completed ECS audit corrected the issue mapping: #165 owns the bounded
  deployment-reason contract, while #202 owns the failed-artifact-envelope
  fix. Both are ancestors of `efda44d`; the remaining #165 gate is #102’s
  unresolved HUMAN recovery choice followed by one authorized exact-SHA
  deployment/live-health evidence package. Do not rerun #202 or treat the
  failed `service_running_mismatch` capture as a product regression.
- The fresh #165 audit pins #102’s remaining choice to two explicit options:
  accept the redacted recovery artifact from run `31616994243`, or authorize a
  separate development-only `post_mutation_smoke` drill. The artifact is
  technically complete, but no owner choice is recorded; after that choice,
  one approved current-main deployment must attach exact-SHA health/smoke
  evidence to #165. Do not weaken the fail-closed guard or rerun historical
  failures.
- The fresh #102 audit confirms the human gate is still undecided: the
  retained `31616994243` artifact has no owner-decision field and no later
  comment changes the status. Do not dispatch a drill until the owner records
  either acceptance of that artifact or authorization for an exclusive,
  readiness-checked development-only `post_mutation_smoke` with
  `DEVELOPMENT_AUTO_DEPLOY=false` and redacted evidence.
- #76 is OPEN/P0 and not implementation-ready. It is the release-critical
  traceability umbrella, not a duplicate #202 implementation: its producer
  mapping, approval owners, freshness rules, and #73/#74 consumer contract
  remain unfrozen. The #202 behavior is already on `origin/main`; only a
  separately groomed local-runner gap would justify new implementation.
- #66 is OPEN/P0 and not implementation-ready as an epic. Its current
  observability groundwork is partial, while release acceptance still depends
  on #26 SLO/RPO/RTO decisions, #63–#65, #72, #76/#77, privacy, migration,
  infrastructure, and on-call evidence. Do not start a broad #66 lane; a
  repository-only event/log child can be groomed only after the owner gates.
- The fresh #66 audit confirms the issue remains OPEN/BLOCKED and that even
  the proposed context-aware event/log child is only ready to groom. #26 must
  first freeze separate dev/prod targets, measurements, thresholds, windows,
  owners, alerts, runbooks, escalation, and rollback criteria; no #66
  operational acceptance or deployment/restore evidence exists.
- #179 is implemented on `origin/main` but remains OPEN/REOPENED: the
  placeholder member-story decision and exact-current-SHA deployment/readiness
  evidence are missing. Do not rebuild the homepage before the editorial owner
  chooses “approved stories” or “remove the band.”
- The completed #130 audit confirms its old CMP visual candidate is superseded:
  its historical implementation is merged, but current `origin/main` uses
  design-5a inline homepage/events surfaces and #130 remains open only for a
  stale HUMAN gate. Do not revive its old candidate; reconcile #179 (or a
  successor) to own current `/`, `/unified/`, `/events`, and event-detail
  visual acceptance, including the unresolved member-story choice.
- The completed #128 audit confirms its historical CMP-course implementation
  is merged but current course hashes intentionally differ from the pinned CMP
  baseline because of design-5a overlays. The recent CMP `c3b35e3` project
  status change is not present on the website and must not be absorbed into
  #128 without a product decision: either supersede #128 with the design-5a
  contract or re-groom CMP parity from current `origin/main`.
- The fresh post-deployment #128 audit confirms no literal adoption is safe.
  Only `c3b35e3` is a narrow candidate for target-native adaptation in the
  project context/template/tests; `83242da`/`894696c` require new evaluation
  models, migration, scoring, API, privacy, audit, Studio, and durable-job
  contracts; `80d67df` needs a separate course-list adaptation; and `8146d46`
  remains blocked by the website email/Relay boundary. Keep the pin at
  `98a2352`, refresh adoption evidence only through groomed lifecycle work, and
  do not copy the CMP tree literally.
- The completed #99 audit confirms its synthetic/local review implementation is
  merged and tester/PM accepted, but the issue remains OPEN behind the HUMAN
  gate. No real private snapshot apply/repeat/browse/screenshots are
  authorized; the next safe step is a named snapshot preflight and
  `make review-data-dry-run` with redacted evidence. The CMP source pin is
  provenance only and must not be changed for this content rehearsal.
- The fresh #99 audit confirms a conditional yes only for a named-operator,
  clean-current-head schema/mapping preflight and no-publish dry-run. The
  workflow still derives family/year by suffix fallback rather than the catalog,
  so every source slug must be explicitly mapped; no apply, repeatability,
  private browsing, screenshots, deployment, or issue closure is authorized.
- The completed #127 audit confirms the internal-event parent is OPEN/P0 and
  blocked before engineering. #131’s safe description bridge is already
  merged, but there is no DB-backed event content/revision service, native
  registration, or Luma-to-native CTA path; the issue also contains stale
  slug-only routes. Re-groom numeric routes and split a bridge-activation
  child from the registration/CTA phase gated by #45/#46/#17/#22/#23/#49.
- The completed #71 audit confirms no `courses.datatalks.club` redirect Lambda,
  API Gateway route, or reviewed destination map exists in `aws-infra`; only
  the in-app compatibility redirect is present. #71 is OPEN/P0/HUMAN with no
  engineer/tester/PM/on-call evidence and remains gated by #15/#16/#29/#60/
  #73/#78/#94/#74. Keep DNS and infrastructure unchanged until #16 receives
  an explicit owner decision and the route matrix is complete.
- The fresh #71 audit confirms AWS has no course-host redirect Lambda/API
  Gateway/path map. Existing CMP ECS/ALB and unrelated Slack redirect
  infrastructure do not satisfy it. Require #16’s redacted owner/method/auth/
  volume/disposition/destination/test/rollback matrix, plus #15/#29/#60/#73
  gates, before any Lambda/path-map, DNS, AWS, or activation work.
- The completed #45 audit confirms the internal Event contract is still
  OPEN/BLOCKED and not engineer-ready. Numeric routes are authoritative, but
  the issue text is stale, the Event model remains identity/provenance only,
  no content/revision/registration service exists, and a numeric invalidation
  path mismatches the UUID validator. Reconcile #45/#46 and accept #18/#19,
  #17/#22/#23/#32/#33/#40 before creating a model-only child.
- The completed #16 audit confirms the compatibility decision remains
  OPEN/BLOCKED: all 115 routes are marked `preserve`, all production probes
  are `not_performed`, and the required owner/method/auth/volume/disposition
  matrix is absent. Keep APIs direct and the redirect Lambda inactive until a
  versioned redacted consumer/launch matrix is accepted alongside #15/#29/
  #60/#71.
- The fresh #16 audit confirms the current 115-route contract is still
  preserve/not-probed: no consumer owner, method, auth, volume, disposition,
  one-hop destination map, API compatibility period, or rollback target is
  approved. Keep direct compatibility views/APIs, redirect Lambda/DNS/AWS
  inactive, and require a versioned redacted route matrix plus owner acceptance.
- The completed #23 audit confirms privacy remains OPEN/P0/decision with no
  named controller or accepted `ACCEPT`/`ACCEPT WITH CHANGE`/`REJECT`. Current
  inventories are provisional; consent, retention, rights/deletion replay,
  legacy Datamailer payload redaction, and EventRegistration privacy are not
  approved. Groom only a synthetic, versioned privacy-decision child before
  #64/import/cutover work.
- The fresh #23 audit confirms no authorized privacy decision exists. Current
  flows still lack versioned notice/consent evidence, complete controller /
  processor and lawful-basis mapping, retention/disposal schedules, rights
  workflows, tombstone/processor propagation, and CMP-purpose disposition. Keep
  #22/#23 open, block production data import and CMP notification adoption, and
  require one versioned redacted privacy matrix with explicit ACCEPT,
  ACCEPT WITH CHANGE, or REJECT.
- The completed #46 audit confirms native event registration/cancellation is
  OPEN/P0/BLOCKED and has no implementation candidate. The authoritative route
  is numeric (`/events/<public-id>/<slug>/register`), while the issue body is
  stale; no EventRegistration, verification/cancellation tokens, or delivery
  runtime exists. Resolve #17, then reconcile #45/#46 and #22/#23/#49 before
  creating an engineering handoff.
- The completed #15 audit confirms the legacy Course/Cohort mapping remains
  OPEN/BLOCKED with 0/4 checks and no owner decision. The current 12-row
  catalog lacks legacy IDs/slugs, aliases, dispositions, reviewer/version
  metadata, source digest binding, and fail-closed duplicate/ambiguity tests;
  do not treat it, the regex year fallback, or phase-two branch as migration
  authority.
- The completed #28 audit confirms high-risk action policy remains
  OPEN/P0/decision with no accepted action matrix. Current capabilities,
  idempotency, audit, and test-only high-risk guards are foundations, not
  production reauthentication; #20/#23 inputs are required before #32/#33
  implementation. Groom only a synthetic decision record covering proof
  freshness/binding, previews, replay/conflict, partial failure, audit, and
  dual-approval deferral.
- The fresh #28 audit confirms no implementation-authorizing decision exists:
  the versioned action registry, reauthentication/step-up proof, preview digest,
  idempotency/concurrency, high-risk audit schema, rollback/break-glass, and
  dual-approval trigger are all incomplete. Require one redacted owner decision
  on #28 before #32/#33 or other mutation work.
- The completed #20 audit confirms staff OIDC/MFA/session/break-glass remains
  OPEN/P0/decision with no owner acceptance. Provider-neutral Studio sessions,
  development login, and AWS deployment OIDC do not establish staff identity;
  provider, MFA, claims, offboarding, session, and break-glass decisions must
  precede #61/#32/#33.
- The fresh #20 audit confirms no provider-specific identity decision or
  validation exists. The owner still must choose the provider and record issuer,
  claims, MFA denial, provisioning/offboarding SLA, session/revocation,
  outage, break-glass custodians/storage/rotation, and redacted validation
  evidence. Keep #20 and #61/#32/#33 blocked until an explicit owner decision.
- The completed #17 audit confirms the registration semantics decision is
  still OPEN/unaccepted. Current authority separates accountless event
  registration (`pending_verification`) from account-owned course registration
  requiring verified identity and a member-confirmed profile, but runtime still
  allows anonymous CourseRegistration and legacy Datamailer callbacks. Record
  the owner decision and reconcile stale issue wording before #46/#54 work.
- The completed #54 audit confirms account-owned course registration is OPEN,
  blocked, and not engineer-ready. Runtime still permits anonymous
  CourseRegistration, mutable campaign targeting, required newsletter consent,
  and direct Datamailer callbacks; no MemberProfile/CourseInterest/EmailDelivery
  exists. Re-groom a course-only registration child after #108/#17/#22/#23/
  #49 and the #14/#15/#51–#53 ownership gates; remove #45/#46 as direct deps.
- The fresh #54 audit confirms the same boundary on current `origin/main`:
  anonymous registration, mutable campaign targeting, legacy profile writes,
  direct Datamailer callbacks, and raw staff PII serialization remain, while
  CourseInterest, MemberProfile, EmailDelivery, and registration conversion are
  absent. Groom one PM-owned course-registration decision child; keep runtime,
  email, privacy, event, and production-data changes blocked.
- The completed #55 audit confirms cohort-owned homework/scoring/history is
  OPEN/BLOCKED with cross-cohort ownership gaps, synchronous non-idempotent
  repair/rescore paths, incomplete parity, and no reconciliation evidence. A
  safe future child is cohort-consistent Submission/Answer ownership only,
  after #14/#15 and #51–#54 are re-audited; exclude formulas, bulk repair,
  notifications, migration, and CMP changes.
- The completed #60 audit confirms the course-graph migration remains
  OPEN/BLOCKED: current Course/Cohort code is a local-reset graph, not a
  production upgrade path; 115 route probes and 4,918 compatibility
  differences remain unresolved, and no course-wide reconciliation/rollback
  evidence exists. Reuse #15 for the reviewed mapping and re-groom #60 only
  after #14–#16 and #51–#59 gates are accepted.
- The completed #56 audit confirms project/peer-review/scoring remains
  OPEN/P0/BLOCKED with only partial cohort work. Peer assignment lacks locks,
  idempotency, and uniqueness; deletion, Studio/API parity, and outcome
  reconciliation are incomplete. Groom a narrow replay-safe cohort-scoped
  assignment child only after #14/#15 and #51/#53 foundations are accepted.
- The completed #59 audit confirms Course/Cohort Studio/API parity is
  OPEN/P0/BLOCKED: the 115-route inventory is not an action-policy matrix,
  only nine registration-count capabilities are registered, object scope and
  high-risk preview/reauth/idempotency are missing, and Django-admin/cadmin
  dependencies remain. Groom a PM-only parity inventory before implementation.
- The fresh #59 audit confirms that PM-only matrix is ready to groom but cannot
  claim parity or unblock engineering. It must reconcile Studio/cadmin, 45
  admin-API operations, 11 Django-admin handlers, and 35 commands with owners,
  shared services, object/field policy, audit, idempotency/concurrency,
  async/partial-failure behavior, and removal gates; unresolved cells stay
  blocked by #32/#33, #51–#58, #20/#28, #21/#22, and Relay #1–#3.
- The completed #32 audit confirms the Studio/admin/high-risk boundary remains
  OPEN/BLOCKED: provider-neutral roles/sessions and audit storage exist, but
  #20/#28/#23 decisions, production identity, high-risk proof/preview/dual
  approval, audit export, and break-glass controls are not accepted. Groom a
  PM-only capability/control handoff matrix before runtime changes.
- The fresh #32 audit confirms the registry, API parity, Studio authorization,
  audit, and private/no-store foundations do not constitute acceptance. Missing
  are approved identity/break-glass (#20/#61), high-risk policy (#28), complete
  role/object/field parity, audit export, protected Django admin, and API
  lifecycle controls. Groom a PM-only parity matrix; do not blanket-register
  capabilities or start runtime implementation.
- The completed #52 audit confirms scoped staff/API authorization remains OPEN
  and blocked: legacy Studio/API still use blanket `is_staff`/raw-token paths,
  no Course/Cohort object scopes or view-as safeguards exist, and #20/#28/
  #32/#33/#51 gates remain unresolved. The safe child is a non-behavioral
  authorization inventory plus a guard against introducing new legacy-auth
  paths; do not change runtime auth yet.
- The completed #61 audit confirms staff OIDC/MFA/break-glass remains
  OPEN/P0/blocked with only provider-neutral sessions and development login.
  No provider/MFA/offboarding/outage/break-glass decision or implementation
  exists; #20 is the direct owner gate, while #32/#63/#78 must not be treated
  as substitutes. Resolve #20 before provider-specific engineering.
- The fresh #61 audit confirms #20 still has no owner decision and #61 has no
  provider-specific implementation or acceptance. Existing `StaffSession` and
  Studio authorization are provider-neutral; no selected issuer/claims/MFA,
  provisioning/offboarding, session bounds, outage, or break-glass record
  exists. Keep #61 blocked and limit any follow-up to a decision-free handoff.
- The completed #57 audit confirms leaderboard/score/complaint privacy remains
  OPEN/P0/BLOCKED: scoring has no durable idempotent reconciliation, public
  breakdown access is not sufficiently gated, complaints lack a complete
  lifecycle, and versioned consent/retention/deletion decisions are absent.
  Groom a decision-only disclosure contract under #23 before runtime work.
- The completed #58 audit confirms certificates/graduate/Wrapped remain
  OPEN/BLOCKED with no explicit Certificate history, protected graduate
  export, cohort-owned Wrapped schema, or idempotent/audited recalculation.
  Re-groom a synthetic cohort-owned certificate record child only after
  #14/#15/#51 and #28 gates; keep messaging and Studio/API bulk work separate.
- The completed #64 audit confirms privacy runtime work is OPEN/P0/BLOCKED:
  inventories are provisional, consent is bundled/unenumerated, rights and
  deletion replay/retention jobs are absent, and exports/audit payloads expose
  data. Groom only a synthetic, redacted privacy traceability inventory until
  #23 accepts the privacy matrix; no runtime/import/provider work.
- The fresh #64 audit confirms the safe next slice is documentation-only
  `#64-A: Runtime privacy and deletion traceability register`, anchored to
  `origin/main@efda44d` and synthetic evidence. It may inventory fields,
  routes, exports, jobs, providers, caches, audits, deletion hooks, propagation,
  status, and freshness, but must not choose lawful basis/retention or add
  runtime models, routes, jobs, migrations, imports, or provider calls before
  #23’s approved matrix.
- The completed #65 audit confirms the automated accessibility implementation
  is merged, but the issue remains OPEN/HUMAN: current-main full Playwright,
  real screen-reader checks, manual matrix, and fresh screenshots are missing.
  Do not reuse old candidate evidence or let #210’s dirty testing changes count
  as #65 acceptance; run a fresh tester-only evidence refresh later.
- The completed #76 audit confirms the release-quality umbrella remains
  OPEN/P0 and is not an implementation slice: current CI lacks the full
  Playwright/manual/fault/restore evidence and the requirement→owner→artifact
  traceability graph; #73/#74 remain NO-GO. Groom one PM-only producer matrix
  and reconcile #77’s 18-versus-20 decision count; do not duplicate #210.
- The fresh #76 audit confirms it is a coordination/traceability umbrella, not
  an unrestricted implementation lane. A narrow documentation child is ready
  to groom: map all 10 specs and 20 decisions to issue, producer, test,
  artifact schema/digest, accountable owner, freshness, exception, and #73/#74
  consumer fields, with explicit NO-GO for missing or stale inputs. No report
  generator, CI rerun, deployment, or rehearsal is authorized from #76.
- The completed #66 audit confirms observability/backup/restore remains
  OPEN/P0/needs grooming. Telemetry and ECS compensation foundations exist,
  but approved SLO/RPO/RTO targets, alert ownership, cross-provider fault
  evidence, RDS restore/tombstone/outbox reconciliation, and expiry monitoring
  are missing; keep #73/#74 NO-GO and groom only a repository event-contract
  child.
- The completed #73 audit confirms the full migration/fault/restore rehearsal
  is OPEN/P0/NO-GO with no candidate or acceptance evidence. The current
  `efda44d` deploy stopped before mutation at terminal-count capture, live
  readiness is 503/old release, `/courses` and course sitemap return 500, and
  auto-deploy is still true; resolve #77/#191/#102 and readiness before any
  rehearsal or deployment rerun.
- The fresh #73 audit confirms no current artifact clears #73/#74. The green
  scheduled run is ordinary SQLite/CI coverage only; current push run
  `32566343252` published but failed pre-mutation with
  `service_running_mismatch`; the older successful deploy is stale `11b2bd1`.
  Missing evidence includes four-repository reconciliation, import/delta,
  restore/RPO/RTO, fault/edge, post-cutover writes, rollback, production-shaped
  Terraform/freeze, and the #77 aggregate. Keep rehearsal and deployment
  stopped until a dated redacted matrix and owner decisions are accepted.
- The completed #26 audit confirms SLO/RPO/RTO/alert ownership remains
  OPEN/P0/NO-GO: no accepted target/measurement/owner/escalation matrix exists
  for availability, latency, freshness, Relay acceptance, RPO, or RTO. Keep
  #66/#73/#74 unstarted until the owner records the versioned decision and
  rollback treatment for writes, queues, Relay state, and content pointers.
- The fresh #26 audit confirms the only `ACCEPT WITH CHANGE` text is a draft,
  not owner acceptance. Every dev/prod row still lacks approved value or N/A,
  scope, measurement, threshold/window, alert action, accountable owner,
  runbook, escalation, and rollback/review criteria. Keep instrumentation,
  AWS alerting, backup/restore, rollback, CI, and deployment out of #26’s
  decision record; a repository-only #66 event/log child can follow later.
- The completed #77 audit confirms the release-acceptance report gate is
  OPEN/NO-GO: current CI emits only the selective nine-component report, no
  frozen ten-spec/20-decision traceability graph or aggregate checksum, and
  `efda44d` never reached deployed HTTP evidence. Reconcile the 18-versus-20
  decision count in a PM-only report contract before #73/#74 authorization.
- The fresh #77 audit confirms current publication evidence is bound to
  `efda44d` (version `20260822-095810-efda44d`, image digest
  `sha256:dbef6f30…`, config digest `sha256:5f67676e…`), but deployment records
  `failed_without_success_record`, `released=null`, and `http_smoke=null` after
  `service_running_mismatch`. Treat it as non-clearing evidence. Update #77
  documentation-only to make 20 decisions normative, freeze the producer /
  artifact / approval / freshness matrix, and mark #73/#74 consumers.
- The completed #198 audit confirms its homepage subtitle implementation is
  merged, tester/PM accepted, and present in the current source contract, but
  its exact-current-SHA human deploy gate remains unresolved: run
  `32566343252` stopped before mutation at terminal-count capture, live serves
  the older `11b2bd1`, and `/health/ready` is 503. Route this to release and
  readiness owners; do not reimplement homepage copy.
- The completed #53 audit confirms the Course/Cohort lifecycle parent remains
  OPEN/BLOCKED with only a partial prototype: route contract, lifecycle state,
  duplication completeness, public overrides/people, Studio/API parity, and
  reviewed mapping are unaccepted. Freeze those contract inputs via PM after
  #14/#15 and #51 clear; do not treat #218 or phase-two code as acceptance.
- The fresh #53 audit confirms the implementation remains partial: current
  `/courses/<family>/<identifier>` routes differ from the draft cohort path;
  lifecycle is still binary/legacy; duplication is admin-only and omits
  settings/provenance review; public overrides/people and Studio/API parity are
  absent; and characterization tests are not acceptance. Groom one versioned
  Course/Cohort contract matrix after #14/#15/#51, with owner/PM acceptance.
- The completed #51 audit confirms Course→Cohort migration remains
  OPEN/BLOCKED: the reviewed mapping, relation retargeting,
  quarantine/preflight, forward/backward rehearsal, and no-recreation evidence
  are missing. The safe next child is a side-effect-free mapping preflight only
  after #14/#15 owner acceptance; no schema writes, URL changes, or CMP-pin
  changes.
- The fresh #51 audit confirms that child must consume an approved versioned
  #15 mapping, reject missing/duplicate/ambiguous/conflicting/stale/
  alias-colliding rows, and emit a deterministic in-memory relationship
  preflight plan with synthetic isolation tests. It must not run migrations,
  call ORM writes, persist quarantine, access production, add constraints,
  change redirects/APIs, or alter the CMP pin.
- The completed #49 audit confirms the durable EmailDelivery boundary is
  OPEN/P0 and blocked before engineering: website has no delivery model,
  Relay client, callback/reconciliation runtime, or ambiguity-safe state, while
  legacy Datamailer remains active. Advance Relay #1 → #2 → #3 and resolve
  #22/#23 before creating a website #49 candidate.
- The completed #14 audit confirms Course/Cohort ownership remains OPEN/P0/
  decision with no owner acceptance. Current family/cohort code is present, but
  duplication is incomplete and history/criteria ownership still have gaps;
  phase-two worktrees are clean but stale/unaccepted. Do not dispatch #51/#53/
  #55/#56/#60/#218 until the owner records the family-versus-cohort and
  definition-only duplication decision.
- The fresh #14 audit confirms the owner must still accept or replace the
  literal contract: Course owns family/default metadata; Cohort owns dated
  settings, curriculum, learner records, scores, certificates, and history;
  duplication copies definitions/settings only and isolates the copy; and
  versioned curriculum is deferred. Existing duplication tests are shallow and
  omit stored Unit content, so no migration, CMP adoption, or peer-review
  adoption may proceed before this decision is recorded in #14, `open-decisions.md`,
  and spec 04.
- The completed #22 audit confirms the transactional-email catalog is still
  OPEN/P0/decision with no accepted purpose owner, Relay sender/reply-to,
  immutable template/version, typed context, idempotency, retention, or legal
  basis. Keep #48/#49/#50 and all non-course sends disabled until a
  product-owner decision record reconciles event/course/Slack/newsletter
  purposes with #23 and Relay #1/#2/#3.
- The fresh #22 audit confirms the existing issue is the correct decision-only
  gate, but it remains unaccepted. The owner must explicitly retain or defer
  each CMP-related purpose and record owner, audience, trigger,
  consent/preference classification, Relay sender/template key, context,
  idempotency/version inputs, and retention class. Keep #49/#50 and CMP
  notification adoption blocked until #22 and #23 are accepted.
- The completed #21 audit confirms the website/Relay integration epic is
  OPEN/P0 and not an implementation slice. Website has no `EmailDelivery`
  runtime, Relay remains pre-hardening, and #22/#23 plus Relay #1/#2/#3 gate
  all integration work. The safe sequence is PM acceptance of #22, then
  Relay #1 → #2 → #3, then website #48/#49/#50; no sends or provider calls.
- The completed #184 audit confirms the canonical Slack-link implementation is
  merged and tester/PM accepted, but the issue is OPEN/REOPENED only because
  exact-current-SHA deployment/live evidence is missing. The code is present
  on `origin/main`; no new docs/code work or dependency expansion is needed.
- The completed #102 audit confirms the bounded recovery implementation is
  merged and tester/PM accepted; its retained recovery artifact passed, while
  the current `efda44d` deployment failed earlier at fail-closed service-count
  capture. Keep #102 open for its HUMAN drill/decision gate and do not treat
  the current release failure as a #102 regression or authorize a new drill
  before readiness is repaired.
- The completed #179 audit confirms the current design-5a homepage/events
  surfaces are technically present on `origin/main`, but the issue remains
  OPEN/HUMAN with only copy-marker acceptance. Keep #179 scoped to `/` and
  `/unified/` plus the unresolved member-story decision; groom a successor for
  current `/events`, past-event, and event-detail visual acceptance. Do not
  revive the old #130/#179 candidates or reuse their tester evidence.
- The completed #109 audit confirms the current website-origin cache candidate
  is a dirty 16-path diff at `efda44d` with tester `FAIL`, while the older
  cache-contract branches are 166 commits behind. CloudFront/WAF remains
  blocked by #78/#94 and missing design/cost ownership; split website-origin
  and aws-infra-edge slices before any merge or deployment.
- #191 is OPEN/P0 and blocked before engineering: no owner has selected the
  RDS rotation source-of-truth mechanism, named implementation/operations
  owners, or authorized redacted verification; `/health/ready` remains 503.
- #60 is OPEN/P0 and not engineer-ready. All 115 route probes remain
  unperformed, 4,918 compatibility differences are unresolved, and no
  production-like full-graph migration/reconciliation/rollback evidence exists;
  #14–#16 and #51–#59 must be accepted first.
- The fresh #60 audit confirms the dependency order remains
  `#14 → #15 → #51 → #52 → #53 → #54 → (#55, #56) → #57 → #58 → #59 → #60`;
  #16 gates compatibility cutover. The safe child is a documentation/preflight
  evidence envelope with exact SHAs/digests, mapping/route/rehearsal schemas,
  and synthetic fail-closed cases. It must not access production, import data,
  migrate schema, activate redirects, deploy, or rerun CI.
- #186 is OPEN/P0 and not engineer-ready: the owner must choose whether to
  close self-service signup, preserve it with verified-email/abuse controls, or
  define a new contract; current code leaves the email signup path open despite
  `ACCOUNT_ALLOW_REGISTRATION = False`.
- #94 is OPEN and blocked by #78: the legacy `sandbox/website` Terraform root,
  state keys, and OIDC boundary remain in place; no engineer lane is safe until
  immutable development-environment activation and a fresh readiness preflight
  pass.
- The fresh #94 audit confirms no `development/website` root or activation
  evidence exists: state remains `sandbox/website/terraform.tfstate`, workflow
  bindings still name `sandbox`, GitHub has no development environment or
  variables, and `DEVELOPMENT_AUTO_DEPLOY` is true. Keep #94 stopped; after
  #78 closes, perform only the authorized read-only 98-address/state/lock/
  identity/no-change-plan preflight.
- #78 is source/operator-accepted but still OPEN/HUMAN: GitHub OIDC remains
  default, no protected `development` environment or variables exist, and no
  live plan/apply/activation evidence exists. Do not mutate AWS/GitHub settings
  from this backlog lane.
- The fresh #78 audit confirms source acceptance is not live activation: GitHub
  still has zero Actions environments/variables and `main.protected=false`; the
  OIDC readback remains default, while AWS preflight stops at HTTP 403. The
  smallest safe action is a named-owner, read-only GitHub/AWS preflight with
  redacted readbacks; no OIDC enablement, IAM bootstrap, workflow dispatch, or
  Terraform apply is authorized.
- #63 is OPEN and incomplete despite the merged #141 non-identity baseline; a
  PM-owned child still needs the route/export authorization matrix, legacy-auth
  inventory, named #52 owner, and release-consumer contract.
- The fresh #63 audit confirms #141’s accepted baseline explicitly excludes
  OIDC/MFA, reauthentication, break-glass, raw-token migration, scoped exports,
  and high-risk semantics. It supports a safe #63/#52 repository/documentation
  child for the residual route/export inventory and a deterministic guard
  against new legacy-auth uses; the child is not groomed or owner-assigned.
  Do not change runtime auth, exports, privacy retention/deletion, or
  compatibility behavior from this audit.
- #64 is OPEN/P0 and blocked by #23: privacy inventory, consent/rights,
  retention/tombstones, and masked Studio/API parity are not implemented or
  accepted.
- #23 is OPEN/P0 and decision-ready but unaccepted. The authorized owner still
  must approve the controller/processor, lawful-basis, retention, minors,
  rights/deletion, propagation, and backup-tombstone contract before #64 or
  production data work can proceed.
- #32 is OPEN/P0 and blocked before engineering. It has no implementation or
  accepted child; its identity, roles, audit export, and high-risk controls
  remain downstream of the unresolved #20/#28 decisions and #61 provider
  contract.
- #33 is OPEN/P0, groomed but blocked before engineering. Existing credential
  routes and tests are development-scoped foundations only; production API
  identity, high-risk semantics, audit/export, and on-call evidence depend on
  #20/#28/#32/#61. Do not start an engineer lane yet.
- The fresh #33 audit confirms `/api/v1/admin/` is strict Bearer-only with
  hashed/scoped/expiring/revocable principals, while legacy `/api/` raw-token,
  cadmin, and provider-neutral Studio paths remain separate. The safe child is
  documentation-only coexistence/deprecation mapping under #52; do not alter
  authentication, routes, token storage, models, credentials, or deployment.
- The fresh #107 audit confirms its development credential foundation is
  merged, but current bootstrap permissions broadened beyond retained
  health-only token evidence and the exact-head deployment failed before
  mutation. Keep #107 OPEN/HUMAN; first establish a green ready development
  deployment, then rerun current-head tester/PM scope checks before any
  redacted human bootstrap or credential issuance.
- #51 is OPEN/P0, PM-groomed, and blocked before engineering. The partial
  Course/Cohort split on `origin/main` still lacks the accepted #14
  curriculum/duplication decision, #15 reviewed legacy-edition mapping,
  fail-closed preflight, and production-like migration evidence. The clean
  phase-two worktree is stale and is not a merge candidate.
- #57 is OPEN/P0, groomed but blocked. Current leaderboard privacy and
  complaint handling remain partial; the public export and score-breakdown
  surfaces lack an accepted field-level privacy/export matrix, while durable
  recomputation and Studio/API parity are missing. Do not start until
  #23/#14/#15/#28 and #52/#54–#56 are resolved.
- #59 is OPEN/P0 and not implementation-ready. The management registry covers
  only nine registration-count capabilities; Studio/cadmin, compatibility API,
  Django admin, and commands still lack an owner-mapped action matrix and
  shared-service/parity contract. Keep it blocked pending #20/#28, #14/#15,
  #32/#33, #48–#51, and #54–#58 decisions.
- #58 is OPEN/P0, groomed but blocked. Certificates, graduates, and Wrapped
  remain legacy/partial: no cohort-scoped certificate lifecycle, reconciled
  graduate export, versioned Wrapped payload, Studio/API parity, or accepted
  privacy/public-sharing decision. Keep communication mechanics under #50 and
  wait for #28/#50/#54–#57 plus the privacy owner before engineering.
- #65 is OPEN/HUMAN and not accepted. The automated accessibility slice is
  merged, but evidence drifted after the candidate; current-main full
  accessibility and real manual screen-reader/browser evidence are missing.
  No engineer work is justified; a fresh tester rebaseline is the next safe
  action.
- The fresh #65 rebaseline confirms the automated/product slice was accepted
  and deployed earlier, but the issue remains OPEN/HUMAN: the latest candidate
  evidence was based on `35cb2e1`, while current `origin/main` is `efda44d`,
  and the terminal current-main Playwright plus named real screen-reader/manual
  matrix are still absent. Start only a tester-owned refresh from current main;
  do not reuse the stale candidate or add a new accessibility implementation.
- #54 is OPEN/P0 and not assignable. Registration still permits anonymous
  legacy behavior and lacks CourseInterest, RegistrationWindow, conversion,
  MemberProfile, EmailDelivery, and complete Studio/API contracts. PM should
  split a focused registration/conversion child, retain #17/#22/#23/#49 and
  #51/#53 gates, remove stale #46 as a direct dependency, and wait for the
  Relay/management handoffs.
- #77 is OPEN/BLOCKED with no aggregate release-acceptance report, go/no-go
  gate, frozen checksum, exception register, or #73/#74 consumer contract.
  Existing CI verification evidence is only a nine-component controller, not
  spec-10 release acceptance. Keep it blocked until #72/#76 and all producer
  contracts are reconciled.
- #74 is OPEN/NO-GO. The latest exact-current-main push failed before mutation
  at `service_running_mismatch`; the live site is still old `11b2bd…`, and
  `/health/ready` is 503. Do not rerun deployment, switch DNS, enable senders,
  or retire legacy writes until #26, #72, #73, #76, #77, #78, and #191 gates
  are explicitly resolved.
- #73 is OPEN/P0 and NO-GO. It has no rehearsal pack, production-shaped
  preflight, four-repository reconciliation, restore/rollback/Terraform report,
  or owner/exception register. Re-groom only after #26/#29/#72/#78/#191 and
  the #76/#77 evidence contracts are resolved.
- #78 is OPEN/HUMAN. The source/operator slice is accepted, but live activation
  is absent: OIDC still uses defaults, there are no GitHub environments or
  variables, `development` is 404, `main` is unprotected, and the AWS gate
  denied preflight. Do not enable infrastructure or start #94 from this lane.
- #29 is OPEN/P0 and unresolved. The preservation-first policy is supported,
  but no owner has accepted the production domain/DNS/edge/environment/RACI
  register. Live probes still show legacy robots/sitemap/route behavior and
  readiness 503; production cutover remains NO-GO.
- The fresh #29 audit confirms missing production RACI/targets, CloudFront/WAF/
  invalidation, cost alarms, rollback/monitoring evidence, #78 activation, and
  #16/#71 route mapping. Keep DNS/AWS, WAF, redirect workloads, and path-map
  activation disabled until the decision matrix is accepted.
- The fresh #5 audit confirms the owner must choose the canonical syntax
  (`/courses/<course>/cohorts/<cohort>/` versus current two-segment paths) and
  approve an explicit one-to-one legacy mapping with IDs, aliases, owners,
  dispositions, and direct-compatibility cases. Keep redirects, consumer
  migration, DNS/AWS, and CI out of this decision child.
- The fresh #9 audit confirms #9 is an open infrastructure epic, not the owner
  of the course URL decision: #5, #16, #29, and #71 remain open with no named
  ACCEPT/ACCEPT WITH CHANGE/REJECT. Current main still emits two-segment course
  URLs while a separate content-review route emits the draft `/cohorts/` shape;
  the 115-row route contract is all `preserve`/`not_performed` and lacks owners,
  methods, auth, volume, and migration dispositions. Groom only the bounded
  route-ownership audit; do not change routes, redirects, Lambda, DNS, or AWS.
- #53 is OPEN/P0 and blocked. The partial Course/Cohort foundation still lacks
  an accepted lifecycle state model, explicit Course defaults/overrides,
  canonical route/alias policy, complete duplication graph, scoped
  authorization, archive/history guards, and production-like migration. It
  requires #14/#15 decisions before any fresh engineer lane.
- #55 is OPEN/P0, blocked and needs grooming. Existing homework/scoring code
  is substantial but lacks cross-cohort DB invariants, durable/idempotent
  scoring/repair operations, shared services, audit, and management parity.
  Resolve #14/#15 and re-groom explicit learner, scoring/repair, and Studio/API
  children before creating an engineer lane; keep notifications under #50.
- #26 is OPEN/P0 and blocked as a decision issue. The proposed production
  targets (availability, latency, freshness, Relay acceptance, DB RPO, and
  service RTO) and development targets are not owner-approved, and no alert
  ownership/runbook matrix or measured restore evidence exists. Do not dispatch
  #66/#73/#74 work until the owner records an explicit decision.
- #191 is OPEN and blocked at the source-of-truth decision gate. RDS rotates
  its master credential while ECS receives a separate static `DATABASE_URL`; no
  reconciliation/refresh contract, stale-secret detection, named owners, or
  authorized development-only rotation verification exists. Do not touch AWS,
  secrets, or readiness from this backlog lane.
- The fresh #191 audit confirms the same fail-closed boundary on clean refs:
  Terraform enables RDS-managed credentials but ECS still reads the separate
  static `website-sandbox/database-url`; no website-specific rotation sync,
  stale-secret alarm, or named implementation/operations owner exists. Keep
  #191 OPEN/P0/BLOCKED and request one redacted owner decision before any AWS,
  secrets, IAM, or rotation-shaped verification.
- #49 is well-groomed but unimplemented and blocked on Relay/template/privacy
  decisions. The generic #31 job substrate exists, but no EmailDelivery model,
  Relay request-hash contract, ambiguous-provider state, callback consumer, or
  reconciliation job exists. Complete Relay #1–#3 and #48 before opening a
  fresh #49 engineer lane.
- #50 is OPEN/P0 and blocked before engineering. There is no Relay client,
  EmailDelivery model, durable delivery handler, callback/reconciliation,
  freeze/import, or safe redacted projection; current registration/course
  sends still bypass the target boundary through Datamailer. Resolve #22/#23
  and #48/#49 contracts before any migration or sender work.
- #64 is OPEN/P0 and blocked on #23’s missing privacy-owner decision. No
  rights-request, consent-evidence, retention/tombstone, export-masking, or
  processor-propagation contract is accepted. Only a source-only redacted
  inventory child is safe after #23; no protected-data import or privacy
  runtime implementation should start.
- #72 is OPEN/P0 and not engineer-ready. Its course, compatibility, editorial,
  security, and legal inventories are stale or incomplete; no current
  owner-approved requirement→issue→test→artifact→approval matrix exists, and
  the spec has 20 decision sections while downstream #77 still says 18. Keep
  it documentation-only until PM publishes a fresh reconciled baseline.
- #56 is OPEN/P0 and not mergeable. Its closest phase-two branch
  `issue-216-phase2` at `3220ced` is 127 commits behind and has the same
  functional patch already merged as `641d517`; do not cherry-pick or revive
  it. Re-groom from current `origin/main` only after #14/#15/#20/#23/#28 and
  the #31–#54 service/domain dependencies are accepted.
- #61 is OPEN/P0 and blocked before engineering. Its read-only audit found no
  staff OIDC/MFA, provider-bound session, or production break-glass path; the
  direct blocker is the unresolved #20 provider/MFA/session decision. Do not
  start a provider adapter until #20 is accepted and #61 is re-groomed.
- #28 is OPEN/P0 and blocked before engineering. Its read-only audit found no
  owner acceptance for the action registry, preview/confirmation, reauth,
  idempotency, audit, or dual-approval policy. The current text is explicitly
  a proposal; keep #32/#33 and other high-risk behavior changes blocked until
  an authorized owner records an explicit decision and redacted control record.
- #52 is OPEN/P0 and not implementation-ready. The safe next slice is a
  read-only legacy-auth/export inventory with named owners and deprecation
  guards; behavior changes remain blocked by #20/#23/#28/#32/#33 and the
  Course/Cohort decisions.
- The fresh #52 audit confirms a strictly non-behavioral route/action inventory
  plus no-new-legacy-auth guard is ready to groom, but no child issue or owner
  exists. It must classify raw tokens, staff gates, scopes, sensitive exports,
  side effects, owners, evidence, freshness, and exceptions without changing
  auth, exports, privacy, or compatibility behavior.
- #183 and #56 have substantial uncommitted candidates/audits but no current
  PM acceptance; both are blocked by missing lifecycle evidence and shared
  accessibility or product-contract gates.
- #98, #107, #108, and #213 are already implemented or documentation-accepted
  slices. Their remaining work is human/on-call release, identity, privacy,
  RDS, or deployment evidence; do not start duplicate implementation lanes.
- Scheduled regression run `32585538218` later completed green for exact
  `efda44d`; this is test evidence only and does not prove a deployed current
  release or clear the #74 cutover gate.
- Decision audits confirm #14 has no owner response and #15 has only a
  synthetic 12-row catalog, not a reviewed legacy-ID/source-bound mapping.
  Consequently #51, #60, #218, and #56 remain blocked at their decision or
  migration gates.
- The completed #15 audit confirms that the issue is OPEN/BLOCKED and has no
  owner `ACCEPT`, `ACCEPT WITH CHANGE`, or `REJECT` decision. The current
  catalog and projections contain no legacy numeric IDs, explicit cohort
  slugs, aliases, one-off dispositions, review metadata, mapping revision, or
  fail-closed validator; existing tests do not cover missing, duplicate,
  ambiguous, conflicting, stale, or alias-colliding rows. Keep #51, #53, #60,
  and #218 out of implementation until a pinned, reviewed mapping is approved.
- The fresh #15 audit confirms the current 12-row catalog is only a development
  helper. It has no complete legacy inventory, numeric IDs, target aliases,
  one-off dispositions, reviewer/version metadata, source digest, or
  fail-closed missing/duplicate/ambiguous/stale/collision validator. The safe
  next child is documentation/decision-only mapping v1; no migration, import,
  CMP pin change, redirect, deployment, or production access is authorized.
- The CMP independent tester found the target-owned adaptation feature-correct:
  focused Django 14/14, expanded project tests 51/51, core Playwright
  209/209, independent state/browser checks, visual checks, accessibility
  checks, quality 601/601, and container checks all passed. The official
  envelope still failed because the shared adoption ledger drifted for the
  four candidate files and the Playwright component timed out after 183 PASS
  results; no candidate feature failure was found. Keep the candidate
  unmerged and route the ledger/harness result through a separate audit.
- The completed #17 audit confirms that the registration decision is OPEN,
  unaccepted, and not ready for engineering. The live code still permits
  anonymous `CourseRegistration` records, has no email-verification gate,
  `MemberProfile`, `CourseInterest`, registration-window lifecycle,
  registration-to-enrollment conversion, or `EmailDelivery` boundary, while
  event and course identity semantics remain unresolved. Keep #54 and the
  account/profile work blocked until the owner explicitly decides the
  event/course split, verification, replay/expiry, privacy, and linking rules.
- Relay #1 remains OPEN, groomed, and unassigned. Relay `main` has mutable
  templates, no immutable version/checksum in queued messages, no scoped or
  expiring template-management credentials, weak context validation, unsafe
  `autoescape=False` rendering, and a dry-run path that still upserts contacts.
  No Relay engineer/tester/PM gates exist. Website #48/#49/#50 must wait for
  the Relay #1 contract; a Relay-only engineer slice is safe, but no website
  integration should start from the dirty website checkout.
- The fresh Relay #1 audit confirms credentials are unscoped/unexpired, template
  rows are mutable, rendering/context validation is unsafe/incomplete, dry-run
  writes contacts, and queued sends lack immutable template versions. Groom a
  Relay-only draft/publish/version/render contract with scoped credentials,
  strict zero-write dry-run, and `template_key + template_version` snapshots;
  keep website/CMP sends disabled.
- The completed #22 audit confirms that the transactional-email purpose
  catalog is OPEN/P0, groomed but unaccepted. The current website has eight
  active Datamailer definitions with no approved owner/sender/reply-to,
  immutable version, complete idempotency, or retention mapping; the two
  submission-confirmation keys are absent from the PM brief. Keep #48/#49/#50
  blocked until #22 and privacy/Relay contracts are explicitly accepted.
- The fresh #48 audit confirms no communication adoption is safe: #22 purpose
  catalog, Relay #1–#3 contracts, website #32/#33/#49 controls, and CMP overlay
  disposition are unresolved. CMP `8146d46`/`c3b35e3` remain source-sync
  decisions, not opportunistic website changes. Groom a documentation-only
  decision record; keep Datamailer history read-only and do not add sends,
  Relay clients, pins, or delivery code.
- The completed #16 audit confirms that the course redirect decision is
  structurally groomed but still OPEN/unaccepted. The 115-route inventory has
  no owner/method/auth/volume/disposition/test fields, authenticated probes
  are absent, no complete one-hop course-host map or Lambda rehearsal exists,
  and APIs must remain direct compatibility responses. Keep redirects
  inactive until #15 mapping and #5/#9/#10 owner decisions are complete.
- The CMP adoption-gate audit confirms the four frozen ledger drifts are
  candidate-introduced: `project.html` +20 bytes, the two tests +235/+171,
  and `project_page_context.py` +63. The feature evidence remains PASS, but
  PM acceptance cannot waive the official-envelope failure. A separately
  groomed adoption/evidence repair must refresh only those overlay rows,
  rerun the verifier, and rerun the official envelope; the independent
  Playwright timeout remains a separate CI/runner concern.
- The completed #149 audit was a historical snapshot: it confirmed no CMP
  system-evaluation or notification adoption on the website while CMP ended at
  `598c028f`. The website remained pinned to `98a2352`, its adoption ledger
  covered only that baseline, and #149 had no engineer, tester, or PM
  acceptance. The fresh post-deployment #149 re-audit now covers the newer
  `a9e7dbf` head; keep the pin unchanged until it records the
  course-domain adopt/defer/reject decision, refreshes the #148 overlay review
  against `efda44d`, and splits system evaluation, peer-review visibility, and
  Relay-dependent notifications into separate groomed slices.
- The fresh post-deployment #149 audit confirms CMP `a9e7dbf`/`c3b35e3` is
  deployed and green in its own repository, but website adoption remains
  fail-closed: #149 has no named course-domain decision, engineer/tester/PM
  lifecycle, or on-call evidence; #148 remains review-only with 13 overlay
  conflicts. The smallest safe child is a decision-only #149 record choosing
  adopt/defer/reject for system evaluation, peer-review visibility, and score
  hiding, while deferring notifications to #22/#21. Do not move the pin or
  copy CMP files literally.
- Relay #2 remains groomed but blocked by Relay #1. Current Relay accepts
  blank/missing idempotency keys, replays changed requests without a hash
  conflict, lacks concurrent loser recovery, immutable template versions,
  fenced/ambiguous provider states, claim-time suppression, and redacted
  timelines. Website #49/#50 remain unimplemented and dependency-blocked;
  no website email integration should start yet.
- The fresh Relay #2 audit confirms Relay `main` is clean at `c0fdfac` and
  those gaps remain current: #1 still owns the immutable template/version
  prerequisite, while #2 needs one fenced transactional-message vertical
  slice covering mandatory keys, canonical hashes, concurrent replay handling,
  durable leases, claim-time suppression, bounded retries, ambiguity quarantine,
  redacted status, and PostgreSQL concurrency tests. Keep website/CMP changes,
  credentials, provider calls, and real sends out of scope.
- The completed #18 audit confirms that capacity/waitlists are not accepted
  even though the current event surface has no seat enforcement. The owner
  should explicitly record `ACCEPT: no capacity/waitlist in MVP`, clarify
  that this is a scope deferral rather than an unlimited-capacity promise,
  and avoid presenting the aggregate `N registered` value as authoritative
  seat availability. #45/#46 remain gated.
- The completed #20 audit confirms that staff OIDC and production break-glass
  policy are still OPEN/unaccepted. The repository has provider-neutral local
  roles/sessions and scoped service principals, but no selected issuer/claims,
  MFA evidence, disablement/offboarding SLA, session policy, or production
  recovery custody/exercise. Keep #61/#32/#33 provider-specific work blocked.
- The completed #21 parent audit confirms Relay ownership/spec reconciliation
  is narrowly satisfied, but #21 is not parent-ready: #22 is unaccepted,
  Relay #1–#3 are unimplemented, Datamailer still has active send paths, no
  deployed Relay/OpenAPI evidence exists, and #48→#49 sequencing plus stale
  #106 references need grooming correction. Keep #21/#48–#50 open and do not
  provision credentials or enable sends.
- The completed #19 audit confirms the legacy-timezone decision is still
  OPEN/unaccepted. Current code has partial Europe/Berlin projection fallback,
  but no DST gap/fold handling, retained IANA timezone/exception record, or
  end-to-end public/ICS/notification evidence; do not start timestamp import
  or event schedule modeling until the owner decides the scope and rules.
- The completed #27 audit confirms analytics/tracking preservation is
  OPEN/unaccepted. The site currently loads no provider and only stores a
  consent preference, while 806 legacy rows reference GTM without a reviewed
  keep/remove/privacy disposition. Keep analytics disabled until the owner
  records an explicit policy and the compatibility evidence is updated.
- The completed #24 audit confirms PostgreSQL search is not owner-accepted or
  implementation-ready. Current Wiki search is a baked projection scan with
  no ranking/tokenization/fallback parity, FAQ/Podwiki query fixtures, or
  Lambda relevance threshold; `/wiki` and legacy `/podwiki` contract treatment
  is unresolved. Keep #44 blocked and do not add search schema or retire the
  Lambda before the owner freezes those decisions.
- The completed #40 audit confirms full Person work is not implementation-ready:
  #39↔#40 has a dependency cycle, the current 438-profile JSON is a frozen
  projection rather than a canonical source-backed boundary, host/instructor/
  maintainer roles and aliases/removal guards are missing, and Studio/API
  parity is absent. Re-groom a bounded Person adapter/resolver child before
  dispatch; do not widen #39 or link accounts/staff.
- The completed #38 audit confirms the editorial GitHub sync issue is groomed
  but not implementation-ready as a full delivery. Generic HMAC/replay/jobs
  and immutable release foundations exist, but no source-specific ingress,
  fetch/reconciliation/coalescing, persisted candidate quarantine, preview,
  invalidation, freshness alerts, or Studio/API parity exists. Re-groom a
  narrow signed dtc-content intake/reconciliation child only; keep activation,
  management, public cutover, and other adapters out of scope.
- The completed #45 audit confirms Event lifecycle work is blocked and its
  issue body is stale: it still describes slug-only routes while the accepted
  numeric `/events/<public-id>/<title-slug>` contract is current. #18/#19/#40
  remain unresolved; Event content/lifecycle/calendar/Studio work is absent,
  and #46/#49/#112 boundaries must be split before engineering. Do not start
  the full Event epic from its current text.
- The completed #39 audit confirms broad main-site collection migration is
  not release-ready. Existing baked projections cover 796 canonical finals/
  1,592 aliases, but no source-backed candidate, complete 5,533-row
  import-or-exception ledger, unified adapter, full #35 parity, or independent
  tester/PM evidence exists. Re-groom a Person-free remaining-main adapter
  child after resolving the #39/#40 dependency cycle; keep docs/FAQ/Podwiki
  adapters separate.
- The completed #41 audit confirms Docs has only a partial static projection:
  106 pages/39 assets versus 175 compatibility paths, with no source adapter,
  candidate validation, full link/SEO parity, Mermaid/callout coverage, or
  docs-specific gate. Re-groom a network-free `dtc-docs` adapter child before
  coupling it to GitHub sync or unified search.
- The completed #43 audit confirms Podwiki work is not implementation-ready:
  the issue conflicts with the authoritative `/wiki` (not `/podwiki`) route,
  has a #43↔#44 dependency cycle, no source adapter/fixtures, and static
  projections lose citation timestamps/aliases/dates and truncate relations.
  Re-groom a `/wiki`-canonical parser/fixture child before search/graph
  activation; keep #38/#40/#24/#44 integration downstream.
- The completed #42 audit confirms FAQ has a strong frozen projection (1,401
  questions, 1,401 fragments) but no source adapter/import lifecycle. Edit
  links are present in data but not rendered, deep-anchor behavior and exact
  source JSON parity are unproved, and importer security fixtures are absent.
  Groom an offline pinned FAQ parity child before GitHub sync/activation.
- The completed #111 audit confirms event registration questions/sponsor
  consent are not implementation-ready. No native registration, question,
  answer, sponsor-consent, newsletter-intent, or management surface exists;
  #17/#23/#46/#47/#49/#64 remain gates. Only a synthetic, non-activated
  immutable question-catalog child is safe after PM re-grooming.
- The completed #108 audit confirms the parent is not implementation-ready:
  it remains OPEN/P0/HUMAN with all 14 criteria unchecked and no runtime,
  tester, deployment, or human rehearsal evidence. The safe next slice is a
  freshly groomed accounts-only `MemberProfile` foundation; exclude signup,
  registration mutations, Slack/Relay, Studio/admin, privacy deletion, and
  production import until their owner contracts are accepted.
- The completed #47 audit confirms event registration operations, attendance,
  change/cancellation, and exports are not implementation-ready. The current
  site has no native operation services, attendance records, export/ICS
  contract, or durable job boundary; historical totals and Q&A artifacts do
  not satisfy that contract. Numeric public-event paths and UUID-based
  invalidation also need an owner decision, while #28/#32/#33/#45/#46/#49/
  #50/#111/#112 remain upstream gates. Re-groom a narrow read-only contract
  or export-inventory child before implementation.
- GitHub reports #216 and #217 are now CLOSED; the dirty root remains the
  user's pre-existing `issue-216-engineer` snapshot and must not be treated as
  a clean merge candidate without ownership/commit verification. The CMP
  candidate remains separate and unaccepted due its official-envelope gate.
- #26 is NO-GO/BLOCKED. It has no explicit owner acceptance of separate
  development/production SLO, RPO, RTO, alert-ownership, and rollback targets.
  The current deployment stopped before mutation at `service_running_mismatch`,
  so it supplies neither current-release smoke evidence nor the operational
  rehearsal needed by #73/#74.
- #53 is OPEN/P0 and not engineer-ready. Existing Course/Cohort code is only
  partial: lifecycle, canonical route/alias mapping, complete duplication,
  public overrides, Studio/API parity, scoped authorization, and migration
  acceptance remain missing. It is blocked by #14/#15 and related #40/#51/#52/
  #59/#60 decisions and evidence.
- #54 is OPEN and not implementation-ready. Its registration, interest,
  enrollment-conversion, profile, email-delivery, and Studio/API contracts are
  incomplete; #17/#22/#23/#46/#49/#50/#51/#52/#53/#59 remain explicit gates.
- #55 is OPEN/BLOCKED. Current code has legacy homework/scoring behavior but
  lacks accepted cross-cohort isolation, migration reconciliation, revisioned
  operations, Studio/API parity, and Relay notification handoff; it depends on
  the unresolved #14/#15 and the #51/#52/#53/#54/#59 chain.
- #58 is OPEN/BLOCKED. Graduate/certificate/Wrapped behavior is partial and
  lacks accepted retention, high-risk mutation, permission, Relay, and
  reconciliation contracts; #28/#50/#54–#57 remain gates.
- #59 is OPEN/BLOCKED. A #30-derived owner-mapped parity matrix covering
  Studio, cadmin, Django admin, commands, compatibility APIs, shared services,
  permissions, audit, idempotency, and removal gates is still missing; #32/#33
  and #51–#58 remain dependencies.
- The #133 tester replacement was rejected before execution by the agent
  safety filter twice; a current-head aggregate-only tester is now running, and
  no #133 evidence has been promoted to acceptance yet.
- #74/#77 remain NO-GO/BLOCKED: the current deployment stopped before mutation
  on `service_running_mismatch`, and the release evidence/report contract is
  not complete. Do not rerun deployment from a backlog audit.
- CMP is pinned at `98a235283904b4ef9ad29e196298540756cf1bcc`; current CMP main
  is `a9e7dbf` (production-version marker for functional commit `c3b35e3`).
  The deployed head includes project-evaluation, peer-review visibility, score
  hiding, and failed-submission rendering changes. The website is target-styled,
  so a bounded adaptation is being evaluated under the CMP integration lane
  rather than copied literally.
- A pristine sync dry-run against `c3b35e3` is currently blocked before diff
  generation because the adoption ledger still names the retired source path
  `courses/migrations/0002_alter_enrollment_student.py`, which no longer exists
  after the website migration squash. Do not move `source-pin.json` or run a
  broad upstream apply until that ledger/adoption repair is separately reviewed.
- The CMP drift workflow is removed from `main`. Failed run `32563688625` was
  deleted; historical successful runs were retained as audit history.
- The old drift failure was caused by the website's later migration squash
  leaving the sync ledger with a retired copied migration path. Repair the
  adoption verifier/ledger before the next source-pin update.

## Worktree policy

Dirty or unmerged candidates remain preserved, including the active #109,
#112, #133, #183, and #210 snapshots and the dirty podcast/root work. A clean
duplicate snapshot at `.tmp/podcast-verify-1580a8b` was removed because it was
detached at the exact root commit and contained no unique changes. The clean
Course/Cohort and homework phase-two branches, and the event-Q&A integration
snapshot, still contain unique unaccepted work; review their ownership before
any cleanup.

## Next actions

1. Collect the five active lane reports on completion and replace each closed
   lane immediately with the next bounded backlog question.
2. Route PASS/FAIL and human-gate results to the owning issues; keep blocked
   work out of merge/PM acceptance.
3. Reconcile accepted candidates against current `origin/main`, then run the
   independent tester and PM gates again if the base moved.
4. Merge only fully accepted work, push the exact SHA, and verify the matching
   deployment before closing an issue.
5. Remove only clean, unique-change-free worktrees after checking their branch,
   commits, and active ownership; archive any unmerged diff before deletion.
