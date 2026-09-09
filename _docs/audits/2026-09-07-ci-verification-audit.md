# CI verification and test-safety scope-gap audit — 2026-09-07

This is an addendum to the repository audit, not a second release audit. It covers the previously
underexamined CI classifier/plan/evidence/gate chain, local verification runner, scheduled history,
content-update workflow selection, and shared test-runtime safety. The 19 `REL-xx` findings in
[the release/ingest audit](2026-09-07-release-ingest-audit.md) are not counted again.

Five new important findings are substantiated below. Eight synthetic probes passed: a passing probe
means the current defect or explicitly documented limitation was reproduced, **not** that it was
fixed. No application, workflow, or test-support implementation was changed. No deployed system,
provider, production database, registration export, or GitHub API was contacted. Temporary Git
repositories and evidence use invented fixture content below `.tmp/ci-verification-audit/`.

The findings are based on the shared working tree on this date. Concurrent curriculum and settings
changes belong to their existing owners and were preserved. Line numbers refer to this snapshot.
This bounded audit cannot establish that every defect has been found or that a live release is safe.

## Priorities and coverage

`P0` means a demonstrated must-fix release blocker; `P1` means important correctness/safety work;
`P2` means later improvement. No new P0 is asserted here: the gate protocol defects are demonstrated,
but their synthetic artifact mixes are not evidence of an actual compromised workflow run. The
separate release report contains the deployment blockers.

| ID | Priority | Finding | Confidence |
| --- | --- | --- | --- |
| CI-01 | P1 | Gate accepts a valid verification bundle for a different release and selection | Reproduced |
| CI-02 | P1 | Runner can attest success against a source snapshot it did not actually keep fixed | Reproduced in commit and worktree modes |
| CI-03 | P1 | Same-second evidence timestamps allow an older pass to hide a newer failure | Reproduced in reuse selection; matching report-selection weakness confirmed statically |
| CI-04 | P1 | Content-update workflow omits the actual moved projection inputs and builder package | Reproduced path-filter mismatch; actual workflow dispatch not invoked |
| CI-05 | P1 | Remote target isolation is a caller assertion, not a host-bound safety property | Reproduced authorization only; target-isolation policy needs explicit classification |

Inspected entry points and boundaries:

- `ci/classifier.py`, `ci/selection.py`, ownership graph loading/impact selection, and
  `ci/focused_tests.py`: canonical diff handling, conservative fallback, selected labels.
- `ci/verification.py`, `ci/evidence.py`, `ci/gate.py`, `ci/runner.py`: source/input identities,
  provenance joins, retained output validation, result aggregation, local execution.
- `ci/history.py`, `ci/schedule.py`, `ci/quality_contract.py`: history trust/bounds, full-regression
  anchors, selected-source Makefile contract, environment sanitization.
- `.github/workflows/ci.yml`, `scheduled-full-regression.yml`, and `content-update.yml`: artifact
  restoration, aggregate gates, full-run/skip decisions, trigger coverage. Deployment workflows
  remain covered by `REL-xx`; they were not counted again.
- `test_support/runtime.py`, `network.py`, `safety.py`, `messaging.py`, relevant safety/runtime
  tests and harness integration: owner boundaries, opt-in authority, subprocess limitations,
  compressed artifact scanning. Content-fixture/bootstrap coupling remains in the architecture
  report rather than being duplicated here.
- `_docs/ci/change-selective-ci.md`, `_docs/specs/10-verification-strategy.md`, and
  `_docs/testing/deterministic-test-harness.md`: intended contracts and documented limitations.

This is source review plus narrow synthetic execution, not exhaustive line-by-line certification.
It did not rerun the existing CI, Django, browser, migration, or container suites already exercised
by the other audit owners.

## CI-01 — Independently valid artifacts are not joined to one release identity

**Priority:** P1. **Confidence:** reproduced. **Owner:** CI provenance/gate maintainer.

### Evidence and trigger

`ci/gate.py:74` validates the selection and its resolution against expected controller, release,
run, attempt, source range, profile, and digest. At `ci/gate.py:144` it separately loads a verification
plan and validates the report and retained evidence against that plan. At `ci/gate.py:156`, the gate
accepts success when both independent validations succeeded and all required job outcomes succeeded.
There is no equality check joining the plan's head or its embedded selection to the already-validated
classifier selection/resolution.

The synthetic probe constructs:

1. A valid docs-only plan for an actual temporary Git commit A, with valid CI-origin selector and
   evidence-validation envelopes, retained outputs, and a successful CI report.
2. A separate valid selection/provenance/resolution for an API-code change at different SHA B.
3. Expected release SHA B, expected controller SHA, expected run/attempt, and expected source range,
   with successful required job outcomes.

`normal_gate(...)` returns `verdict=success`. The plan/report's source is A while the release-side
selection's source is B; the docs-only component applicability also differs from the API selection.
No malformed envelope, missing artifact, or forged count was needed.

### Impact and exposure limits

The aggregate gate does not itself prove that the source/classification it approves is the one its
verification report covers. A wrong-but-internally-consistent restored bundle can be accepted.
This matters at the intended release-attestation boundary, independently of `REL-01`, which concerns
whether other deployment entry points require the gate at all.

The current workflow restores artifacts with explicit same-run/attempt names and patterns
(`.github/workflows/ci.yml:1292`) and passes expected identities (`.github/workflows/ci.yml:1326`).
Those transport controls reduce exposure; the probe does not show that an untrusted party can
replace those artifacts in Actions. They do not replace the missing semantic join inside the gate.

### Bounded fix

1. Add one helper in `ci/gate.py` or `ci/provenance.py` that receives the validated selection,
   resolution, plan, and explicit expected workflow identity. Do not add a second independent parser.
2. Require `plan.head == selection.head == resolution.release_sha`. Bind the plan's embedded
   `legacy_selection` to the exact validated selection structure/canonical digest, not merely its
   profile. Enforce the same event and reviewed base/range semantics.
3. Preserve the existing manual-dispatch rule: a selection with `base=None` can legitimately produce
   a plan normalized to `base=head`. Compare through a documented normalization helper; do not reject
   all historical/manual promotions by imposing raw equality on these two different fields.
4. Bind rerun evidence to the permitted current run/attempt and repository identity. Keep intentional
   historical **reused** evidence on its separately validated provenance/history path; do not require
   every reusable envelope to have the current run ID.
5. Return a safe, stable rejection reason such as `verification_release_mismatch` or
   `verification_selection_mismatch`. Add it to the gate artifact/summary without logging artifact
   payloads or secret environment values.
6. Keep missing, invalid, mismatched, and pending evidence failing closed. Do not fix this by ignoring
   the plan or removing the expected-release checks.

### Acceptance tests and dependencies

- Port the synthetic cross-release probe into `ci/tests/test_gate.py`, changing its expected verdict
  to failure. Add a same-head but different-selection/range/profile case.
- Keep the existing exact valid bundle passing. Retain missing-file, digest-tamper, unknown evidence
  ID, and unsuccessful-job cases.
- Cover ordinary push, manual full dispatch, historical promotion, and allowed attempt-1 classifier
  fallback on a rerun. Add an invalid same-run/wrong-attempt fresh envelope case.
- Run `uv run --frozen pytest -q ci/tests/test_gate.py ci/tests/test_provenance.py
  ci/tests/test_workflows.py tests_ci/test_verification.py tests_ci/test_evidence.py`.
- Dependency: coordinate with release gate/controller work in `REL-01`/`REL-19`; preserve the
  intentional current-controller/historical-source distinction. This fix does not require a database
  migration and should precede expansion of evidence reuse.

## CI-02 — Successful local evidence can describe stale source bytes

**Priority:** P1. **Confidence:** two reproduced cases. **Owner:** local verification runner maintainer.

### Evidence and trigger

`ci/verification.py:322` snapshots either committed Git files or the worktree while creating the
plan. `ci/runner.py:215` checks the actual HEAD once. Only worktree mode checks a content manifest,
and only before execution (`ci/runner.py:223`). The component loop at `ci/runner.py:230` does not
revalidate HEAD/source content after a component or before final success. At `ci/runner.py:296`,
envelopes are constructed from the original plan rather than a verified final source snapshot.

The probes use a docs-only temporary repository and replace the child command with a deterministic
synthetic implementation producing a valid pytest-style result log. They prove:

- **Commit mode:** change a tracked file after creating the plan but before running it. HEAD remains
  the planned commit. The runner executes and returns zero despite starting with dirty source.
- **Worktree mode:** begin with the exact planned manifest, then change a tracked file inside the
  component execution. The final manifest differs, but the runner returns zero and the generated
  tester report has `verdict=success`.

The child was stubbed only to avoid running unrelated suites. Real filesystem manifest and evidence
building/validation code executed. These are source-binding probes, not claims that the fake test
output represents application verification.

### Impact and exposure limits

An editor, another agent, generated-source step, or test that changes a relevant source file can
leave green evidence bound to a different snapshot. A frozen worktree is required by the documented
handoff process (`_docs/ci/change-selective-ci.md:393`), but this runner does not enforce the freeze
through execution. Separate clean checkouts in hosted CI reduce the ordinary hosted-workflow risk;
shared local worktrees are the more direct exposure.

`make verification-plan` already requests worktree mode (`Makefile:216`), so the commit-mode bug
primarily affects direct CLI/API use. The worktree-during-run defect applies to the normal local
path. Schema validation alone cannot detect either source change.

### Bounded fix

1. Extract a `verify_source_snapshot(plan, repository)` helper. In commit mode reject tracked dirty
   changes and relevant untracked/import-shadowing inputs, or execute in a separately owned immutable
   snapshot. In worktree mode compare the exact canonical worktree manifest to the planned digest.
2. Invoke it before execution, after each component, and immediately before reporting overall
   success. Recheck HEAD as well as content. Return a stable `source_changed_during_verification`
   failure and stop collecting successful claims from a mixed-source run.
3. For strict attestation, prefer executing from an owned frozen snapshot. Before/after checks alone
   cannot detect an edit that changes and then restores source while a child is running; document
   that limitation if the initial fix uses checks rather than isolation.
4. Preserve output logs from the failing component, but do not emit a success envelope for a
   mismatched snapshot. Invalidate previously accumulated results for that attempted aggregate;
   retain them as diagnostics, not reusable proof for the original snapshot.
5. Keep `.tmp` artifacts and other intended ignored outputs outside the source manifest so writing
   evidence does not invalidate its own plan. Do not fix drift by adding broad source exclusions.
6. At final local handoff, validate the candidate source again or build the final report from the
   frozen execution snapshot and record that snapshot identity explicitly.

### Acceptance tests and dependencies

- Port both cases to `tests_ci/test_runner.py`; each must fail with the new source-drift reason.
- Cover a changed tracked file, added import-shadowing file, HEAD movement, executable-bit/symlink
  change where supported by the manifest contract, and a clean unchanged run.
- Verify evidence/log creation under `.tmp` does not self-invalidate. Verify later components do not
  run after detected drift and no previous success is mistaken for the failed attempted run.
- Run `uv run --frozen pytest -q tests_ci/test_runner.py tests_ci/test_verification.py
  tests_ci/test_evidence.py`.
- Dependency: coordinate any envelope/state-schema change with CI-01 and CI-03. No application or
  database changes are required. Preserve the user's dirty files; never reset the working tree as
  part of verification.

## CI-03 — Second-resolution timestamps make evidence selection order-dependent

**Priority:** P1. **Confidence:** reuse false-positive reproduced; report tie behavior confirmed
statically. **Owner:** evidence schema/reuse maintainer.

### Evidence and trigger

`ci/evidence.py:116` and `ci/evidence.py:120` remove microseconds from completion timestamps.
`choose_reusable_evidence()` sorts only by `produced_at` at `ci/evidence.py:1048`. Python's stable sort
therefore leaves equal-second candidates in caller/file order. The later-failure loop skips all
candidates whose timestamp is less than **or equal to** the selected timestamp (`ci/evidence.py:1052`).

The probe creates valid success and failure envelopes for the same component/input/environment,
completed at 12:00:00.100000 and 12:00:00.900000 respectively. Both serialize to the same second.
With the earlier success first in the candidates list, reuse returns that success with reason
`exact_digest_match` even though the newer failed result is present. The envelope artifacts and
machine-output claims are valid; the later failure is not missing or malformed.

`ci/verification.py:600` has the same tie pattern when choosing one report result per component:
it replaces the current record only for a strictly greater `produced_at` at line 604. This companion
behavior was inspected, not separately claimed as a successful end-to-end mixed-result report.

### Impact and exposure limits

Fast repeated checks, clock-resolution collisions, or imported envelopes can make success/failure
selection depend on artifact order rather than result history. This contradicts the no-fallback-to-
an-older-pass rule in `_docs/ci/change-selective-ci.md:274`.

The normal local `verification-evidence-check` rejects duplicate rerun components
(`ci/verification.py:734`), which limits this particular path through the Make aggregate. Reuse
history legitimately handles multiple candidate envelopes, however. GitHub history invalidation may
also independently reject a later failed Actions run. The proven defect is the reuse selector's
ambiguity handling, not a claim that every normal workflow can bypass those additional controls.

### Bounded fix

1. Add a single ordering/ambiguity function shared by reuse selection and report-result collection.
   Do not maintain separate timestamp rules in `ci/evidence.py` and `ci/verification.py`.
2. Immediately fail closed when equally ranked candidate evidence disagrees on result, identity,
   or supersession. Return `ambiguous_latest_evidence`; require a fresh run rather than preferring
   success or using filename order.
3. Preserve subsecond UTC precision in newly written envelopes if supported by the schema. Keep
   old whole-second envelopes parseable, but do not invent ordering information they do not contain.
4. Use trusted run/attempt or an explicit producer run/sequence identity where available. A random
   evidence ID or lexicographic filename is not a chronological tie-breaker. Define how results from
   distinct local producers with unsynchronized clocks are treated; ambiguity must cause rerun.
5. Apply explicit `supersedes` semantics even when timestamps are equal. Ensure a known non-success
   cannot disappear behind a same-time success. Preserve expiry and artifact-digest checks.

### Acceptance tests and dependencies

- Port the probe into `tests_ci/test_evidence.py`; both input orders must refuse reuse.
- Cover success/failure, success/cancelled, success/timed-out, equal-time supersession, duplicate
  identical envelope, old whole-second timestamps, and genuinely later success after earlier failure.
- Cover the same ambiguity in report collection; all permutations must produce the same outcome.
- Keep existing later-failed-Actions-run, expiry, missing-artifact, tamper, and trust tests passing.
- Run `uv run --frozen pytest -q tests_ci/test_evidence.py tests_ci/test_verification.py
  ci/tests/test_gate.py`.
- Dependency: version any changed evidence schema/semantics deliberately and invalidate old cached
  reuse conservatively. Coordinate with CI-01, but the immediate ambiguity rejection can be a small
  independent change.

## CI-04 — Moved ingest projections do not trigger their dedicated validation workflow

**Priority:** P1. **Confidence:** reproduced static path-filter evaluation. **Owner:** content-update CI.

### Evidence and trigger

The actual reviewed input paths are declared by `ci/content_update.py:35`: course/Podwiki JSON under
`temporary/content/public_projection/`, FAQ at `temporary/content/faq_projection.json`, and Docs at
`temporary/content/docs_projection.json` (`ci/content_update.py:47`). The module imports its source
validation implementation from `scripts.projection_build` at `ci/content_update.py:259` and line 284.

The pull-request path filter in `.github/workflows/content-update.yml:7` and the independently copied
push filter at line 32 include `content/**`, `content_sync/**`, the old top-level builder wrapper,
and several other trees. They include neither `temporary/content/**` nor `scripts/projection_build/**`.

The two synthetic event cases parse the real YAML and verify that all of these representative inputs
miss every listed filter, while `ci/content_update.py` correctly matches:

- `temporary/content/faq_projection.json`
- `temporary/content/docs_projection.json`
- `temporary/content/public_projection/wiki.json`
- `scripts/projection_build/public_projection_source.py`

No GitHub workflow was dispatched. Matching is unambiguous here because there is no listed pattern
whose top-level prefix covers either omitted directory.

### Impact and exposure limits

A projection-only update or builder-only fix can skip the dedicated family matrix and its common
checksum/source-pin/projection/redaction checks (`.github/workflows/content-update.yml:96`). This is
a moved-path coverage regression, not a proposal to restore runtime file-backed content. The inputs
are intentionally temporary ingest material, not public-content runtime authority.

Ordinary CI can still run for these changes, and the ownership classifier can select full coverage.
Do not describe this as a universal test bypass or claim a broken projection necessarily deploys.
The missing guarantee is that the workflow expressly responsible for these reviewed source inputs
actually runs whenever those inputs change.

### Bounded fix

1. Add the actual input families and `scripts/projection_build/**` to both event filters. Check the
   relevant `scripts/staging/**` adapters and include only those transitively used by this contract;
   do not assume every staging script belongs in this workflow.
2. Keep the wrapper path if it remains a supported interface; removing it is a separate compatibility
   decision. Do not move source JSON back into application runtime packages to make old filters match.
3. Add a workflow-contract test deriving representative input paths from `_DECLARED_PROJECTION_PATHS`
   and verifying both event filters cover them. Also include implementation dependencies explicitly.
4. Prefer broad, easy-to-review input roots or one code-owned declared dependency list checked against
   both YAML sections. Do not add a third handwritten list with no equivalence test.
5. Update the content-update runbook's trigger description when the temporary ingest layout changes.

### Acceptance tests and dependencies

- Both push and pull-request filters must match every declared projection family and the builder
  implementation. Include rename old/new-path examples in the contract test.
- Verify unrelated-only paths retain the intended behavior and the existing matrix remains read-only.
- Run `uv run --frozen pytest -q ci/tests/test_workflows.py ci/tests/test_content_update.py`.
- Dependency: align paths with the existing database-only migration plan and architecture audit;
  do not treat the temporary projection directory as a new permanent runtime content API.

## CI-05 — A caller can label a shared production-stack origin “isolated development”

**Priority:** P1. **Confidence:** authorization reproduced without any connection. **Owner:** test
safety plus deployment-target policy maintainer. **Policy dependency:** explicit host/marker classification.

### Evidence and trigger

`test_support/safety.py:21` defines one common host set for all remote safety markers. It contains
the development hosts and `prod.datatalks.club`. `authorize_from_environment()` only checks that
`DTC_TEST_TARGET_CLASS` equals the literal `isolated_development` (`test_support/safety.py:97`); it does
not derive or validate this property from the selected hostname. Namespace syntax is checked at
line 100, but no namespace-bound resource/request policy is enforced by `SafetyAuthorization`.
`authorize_request()` enforces exact origin and restricts methods only for `remote_readonly`
(`test_support/safety.py:78`).

The synthetic probe supplies the exact `remote_mutation` opt-in, a valid synthetic namespace,
`DTC_TEST_TARGET_CLASS=isolated_development`, and base URL `https://prod.datatalks.club`. Authorization
then accepts a same-origin POST. Only Python validation functions are called; no URL is opened.

The deployment registry identifies this hostname with the `website-production` resource namespace,
production settings/environment, and production GitHub environment (`deploy/deployment_targets.py:470`).
However, its comment at line 466 explicitly calls the hostname staging-for-life and non-indexable
while the apex serves the legacy site. This audit therefore **does not claim it is currently the
live public apex, that it necessarily contains production user data, or that a live mutation occurred**.
The concrete problem is that the safety boundary does not prove its promised isolation property.

### Impact and exposure limits

Copying a valid remote-test environment and changing only the base URL can keep mutation authorized
against a different persistent stack. A syntactically valid namespace does not itself constrain what
the test's request changes. Existing safety tests reject the literal target-class value `production`
(`test_support/tests/test_safety.py:544`) but do not prove the selected origin really belongs to the
asserted class.

All four exact opt-in requirements still apply. Ordinary local/CI targets exclude remote/live tests,
so this is not a claim that an ordinary unit test automatically mutates a deployed stack. The defect
is important when operators deliberately run the opt-in remote commands and expect the class check
to be an independent safety guarantee.

### Bounded fix

1. Define a reviewed target-safety registry binding exact hostname to permitted markers and actual
   isolation class. Obtain an explicit product/operations decision on the persistent
   `website-production` staging hostname; do not infer destructive-test permission from `noindex`.
2. Derive target class from that registry. If the environment retains a requested class, require it
   to agree with the registry; never let the caller's label establish authority by itself.
3. Keep read-only diagnostics separate from remote mutation/live-provider authority. A hostname can
   be permitted for GET/HEAD checks without being permitted for synthetic mutations or live sends.
4. For mutation-capable targets, define the actual namespace isolation contract: scoped endpoints,
   server-enforced synthetic ownership, disposable tenant/database, or another reviewed boundary.
   Validate the applicable proof before constructing a client. Regex-valid namespace text is not
   sufficient evidence of isolation.
5. Keep errors redacted and reject before DNS/socket/client construction. Do not print endpoint
   credentials or recipient secrets when classification fails.
6. Update the current test that equates the remote host set with all deployable hosts so it verifies
   host/marker/class policy explicitly instead of accidentally granting every new deployment target
   the same mutation authority.

### Acceptance tests and dependencies

- Add a marker-by-host policy matrix. Every shared/non-isolated target must reject mutation/live
  markers even if the caller sets `isolated_development`.
- Retain permitted read-only origins, forbidden methods, exact-origin redirects, HTTPS-only,
  forbidden IP literals, missing namespace, and missing recipient-reference tests.
- Prove denied cases do not import the provider client or call DNS/connect. Use fake adapters only;
  never validate this change by sending a test POST to a real deployment.
- Run `uv run --frozen pytest -q test_support/tests/test_safety.py
  test_support/tests/test_marker_registry.py core/tests/test_development_target.py`.
- Dependency: a reviewed target-isolation policy is required before enabling any new mutation target.
  The fail-closed registry implementation can be prepared independently of application changes.

## Inspected non-findings, documented limits, and duplication opportunities

These are not additional numbered defects. Keeping them separate avoids inflating the backlog.

1. **Opaque/native subprocess networking is already a documented limitation.** The subprocess
   safety probe confirms `_assert_guarded_subprocess()` rejects direct `curl --version` but accepts
   an opaque `bash <script>` or `make <target>` command vector. It executes none of those commands.
   `test_support/network.py:168` guards Python process creation and injects the Python guard into
   child environments; `test_support/network.py:333` cannot inspect arbitrary native descendants.
   `_docs/testing/deterministic-test-harness.md:109` explicitly states this is not an OS network
   namespace and forbids ordinary tests from launching opaque/native egress-capable programs.
   Therefore this is a verified existing boundary, not a newly undisclosed bypass. Universal egress
   denial requires runner/container isolation; another shell-command regex is not a complete fix.

2. **Browser trace ZIP contents are scanned.** `test_support/messaging.py:183` opens ZIP members,
   rejects unsafe paths/symlinks, and checks decompressed content; `redact_trace_emails()` at line 161
   redacts before publication. It would be incorrect to claim Playwright traces are only scanned as
   compressed raw bytes. The separate `scripts/security_artifact_scan.py:43` is a narrower raw-file,
   explicit-canary scanner with an 8 MiB per-file bound. These are duplicate scanning abstractions
   with different policies, not interchangeable implementations. A future consolidation should
   retain byte/file/expanded-archive bounds, safe paths, generic email detection where required, and
   protected-value-free errors. Nested archives, image-pixel detection, and every possible binary
   encoding were not proven safe by this audit; no actual secret-containing artifact was used.

3. **Canonical history archive parsing has real bounds.** `ci/history.py:717` and
   `ci/schedule.py:431` reject traversal/symlinks and bound file counts/expanded bytes. Scheduled state
   requires exactly one state-envelope member. No unbounded-history or ZIP-slip claim is made for
   those inspected code paths. These related parsers and the two scanners could share a small safe
   archive-reading primitive, but their distinct trust and output contracts should remain explicit.

4. **Scheduled skips are not treated as new full-coverage anchors.** `ci/schedule.py:80` reruns after
   a previous non-success, and lines 91–123 look for the actual successful full-regression marker and
   reject intervening failures. Lines 124–154 compare the complete state digest. Repeated unchanged
   skips are intentional, not by themselves a defect or blocker. No remote history was queried.

5. **The classifier has conservative fallback.** Invalid/missing/non-ancestor ranges, unsupported
   file modes/statuses, malformed diffs, and unmapped ownership select full coverage in the inspected
   classifier/selection paths. A projection missed by the dedicated content workflow is not evidence
   that this ordinary classifier also silently selects zero tests.

6. **Quality-contract fallback is deliberately narrow.** `ci/quality_contract.py:88` requires the
   current explicit target set. Legacy behavior requires an exact historical Makefile digest rather
   than merely the absence of a target. Execution strips inherited Make flags at line 141. Those
   controls should survive orchestration consolidation; no generic historical quality skip is claimed.

7. **The local runtime has explicit ownership protections.** `test_support/runtime.py:122` acquires
   a nonblocking lock and token-attributed owner record; worker/database paths are checked at line
   197; cleanup verifies exact run identity at line 272. No broad-directory deletion or cross-run
   cleanup defect was demonstrated. Hostile concurrent filesystem replacement and process crashes
   were not exhaustively modeled here.

8. **Malformed-evidence behavior differs by entry point; no extra finding asserted.**
   `ci/evidence.py:983` deliberately skips non-envelope JSON while discovering candidates, whereas
   `ci/verification.py:715` strictly rejects invalid/duplicate `*-evidence.json` in a rerun directory.
   A proposed claim that every malformed newer artifact silently falls back to an old pass was not
   established across those combined entry points. Future consolidation must distinguish ordinary
   result JSON from malformed files that explicitly claim to be evidence.

## Exact checks, results, and reproducibility

New probes are retained as scratch, not installed into the product test suite:

`.tmp/ci-verification-audit/test_ci_probes.py`

Final command:

```text
uv run --frozen pytest -q .tmp/ci-verification-audit/test_ci_probes.py \
  --basetemp=.tmp/ci-verification-audit/pytest-final
```

**Result: 8 passed in 1.60s.** Coverage is one cross-release gate test, two runner source-mode tests,
one target-authorization test, one documented subprocess-boundary test, one timestamp-reuse test,
and two content-update event-filter tests. The timestamps in the probes are invented values, not
release receipts. No external executable capable of networking was invoked by the boundary probe.

Two earlier probe-development runs produced `1 failed, 6 passed` in 1.23s and 1.34s respectively.
Both failures were mistakes in the **new audit harness's assertion about the plan's changed-path
field**: first it looked in `legacy_selection`, then it expected strings instead of `{path, status}`
records. Only the scratch assertion was corrected. Those two failures are not application defects
and are not counted as findings. The equal-second probe was added before the final eight-test run.

Other focused-suite/typecheck/migration results belong to the root audit report and are not reported
again as this agent's new checks. Follow-up commands inside the finding cards are proposed acceptance
commands for implementers; they were not run here merely to repeat prior coverage.

## Small-model implementation sequence

1. **CI-04:** narrow independent workflow/test patch; no schema or production changes. Land the
   contract test so future path moves cannot silently skip the dedicated matrix.
2. **CI-01:** fix the identity join with negative fixtures before changing the producer. Preserve
   manual/historical source semantics and reviewed reused-origin rules.
3. **CI-02:** add local source-freeze enforcement, first with failing drift fixtures. Do not reset or
   discard dirty worktree content. If execution snapshots are chosen, implement them as a separate
   bounded change after the drift rejection is in place.
4. **CI-03:** centralize evidence ordering and reject ambiguous latest results. Add backward-compatible
   timestamp parsing/schema handling only after permutation tests specify the policy.
5. **CI-05:** record the target/marker/isolation decision, then implement the registry and denial tests.
   Do not broaden remote authority while waiting for that decision.

CI-01/02/03 share evidence contracts and should be integrated serially or with one explicitly assigned
owner. CI-04 can proceed independently. CI-05 can proceed independently after its policy dependency.
Optional scanner/archive consolidation should wait until these correctness changes are verified;
avoid combining it with release-controller or public-content migration refactors.
