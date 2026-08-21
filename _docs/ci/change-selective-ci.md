# Risk-based verification and evidence reuse

The verification controller chooses work from source facts, not from filenames embedded in a
workflow. `ci/ownership.json` is the versioned ownership and impact graph. Its digest and policy
version are bound into every plan and evidence envelope. The same graph is used by local
engineering, push CI, scheduled-state calculation, tester review, and handoff reporting.

The accepted #104 source contract remains the first stage. A push is classified from the complete
`before..after` range using a full-history, rename/copy-aware, NUL-delimited Git diff. The immutable
release SHA must equal the push `after` SHA and `github.sha`. Manual non-probe releases remain full;
probe jobs remain separate. Invalid SHAs, missing trees, unsupported records, and ambiguous ranges
fail closed.

## Ownership and impact closure

The graph owns each known path exactly once and validates duplicate ownership, dangling edges,
cycles, unknown node kinds, and invalid patterns before making a decision. Its reviewed application
closures are:

| Changed owner | Django labels |
| --- | --- |
| `api` | `api` |
| `studio_courses` | `studio_courses` |
| `content` | `accounts content.tests content_sync core` |
| `courses` | `accounts api content.tests core courses data management_api studio studio_courses` |
| `data` | `api courses data studio_courses` |
| `jobs` | `events jobs` |
| `management_api` | `api management_api studio` |
| `management_auth` | `accounts api core management_api management_auth studio` |
| `review_import` | `accounts courses review_import` |
| `studio` | `accounts core studio` |

After migration, render, and root-configuration guards run, the selector derives non-application
full-run guards from the same owner metadata. `shared.*`
owners and owners carrying authentication/security/privacy risk use `shared_application`; surface
owners and test-infrastructure, dependency, or deployment risks use
`configuration_or_dependency`; documentation and compatibility-contract owners use
`documentation_or_contract`. The retained two-file `cadmin/` redirect adapter is explicitly owned
by `surface.cadmin`, and central `test_support/` is owned by `surface.test_support`. The former
`testless_application` reason is not used: `content_sync`, `events`, and `email_app` remain
explicitly classified from their graph metadata. A path with no owner or invalid path syntax still
uses `unknown_path` and the `full` profile.

A focused profile is allowed only for one ordinary application owner. Unknown or multi-application
impact, shared runtime, authentication/security/privacy code, migrations, global fixtures,
dependency/toolchain files, test infrastructure, compatibility contracts, deployment/runtime
files, graph/policy files, and ambiguous changes select a fresh full regression. New apps and suites
must be added to the graph in the same change; otherwise they deliberately receive the unknown/full
fallback.

Central `test_support/` factories and runtime code, root `conftest.py`/`sitecustomize.py`, test
settings/runner code, migration files, marker registration, and Playwright fixtures/tests always
select full coverage. An app-owned factory below an already mapped application root may use that
root's reviewed closure; a missing or unmapped label never narrows the suite.

The ownership contract also has a deterministic reverse-import check in
`tests_ci/test_ownership.py`. It reads only repository-local Python source for verification-labeled
app packages, excludes migration and test-prefixed paths, and inspects direct absolute `import` and
`from` statements in each module body with `ast`; it never imports or executes application code.
Relative and local imports are deliberately excluded from the app-level edge set. A literal dynamic
import of a mapped app is included, while an ambiguous dynamic import fails the contract and must
be resolved with an explicit reviewed ownership edge. The check compares every importing package's
test label with the changed `app.*` closure, so a new static reverse import cannot silently narrow
focused verification.

Templates, CSS, JavaScript, navigation, view/URL/context/serializer data shape, browser fixtures,
and screenshot-harness changes are render impact. They select the full browser suite and fresh
independent desktop/mobile screenshots. Critical route/state coverage is derived from the impacted
graph nodes; an unmapped render impact fails closed to every critical route/state. Backend-only
changes use the core browser profile. A large value-only content change is verified with exhaustive
deterministic artifacts binding record counts, stable identities and order, canonical URL order,
uniqueness, metadata completeness, and file digests; it does not get a probabilistic sample.

## Classifier reason glossary

The linked [`ci.classifier`](../../ci/classifier.py) and
[`ci.selection`](../../ci/selection.py) code emits the following `reason` values for push and
manual selection.

A full-run reason fails closed. Correct or complete the input or policy, then rerun without
overriding the profile. The scheduled coverage path is separate, and its
[four-hour full-regression backstop](../../.github/workflows/scheduled-full-regression.yml) is the
safety net.

| Reason | Cause | Remedy |
| --- | --- | --- |
| `single_application` | Every changed path maps to exactly one root in the graph-derived `APPLICATION_TEST_LABELS`, and no full-run guard applies. | Keep that application's reviewed closure complete. Focused Django tests are selected with the profile-appropriate browser checks. |
| `manual_dispatch` | A `workflow_dispatch` run has no trusted push base and is deliberately full. | Use a push for ordinary change selection; keep manual runs full. |
| `head_invalid` | The event is neither `push` nor `workflow_dispatch`, or the release SHA is malformed or zero. | Supply the exact 40-character release commit and a supported event; do not guess a head. |
| `head_mismatch` | The release SHA, push `after`, and `github.sha` do not agree. | Reconcile the immutable release identity and rerun against that exact SHA. |
| `base_zero` | The push has no parent, such as the first commit on a branch. | No narrowing is safe; use the full run. |
| `base_invalid` | The supplied base is not a lowercase full SHA. | Use the event's exact base SHA or let the classifier fail closed. |
| `head_unavailable` | The release commit is not an exact commit in the checkout. | Fetch or otherwise make the release commit available before classifying. |
| `base_unavailable` | The base commit is unavailable, or Git cannot evaluate the ancestry query. | Fetch the complete base and history; do not classify a partial checkout. |
| `non_ancestor_base` | The base is not an ancestor of the release commit. | Use the correct push base; a guessed or unrelated base requires full verification. |
| `diff_failed` | The canonical Git diff command failed. | Repair the repository/object state and rerun the canonical diff; do not substitute a filename list. |
| `diff_empty` | The canonical range contains no records. | Treat it as full and investigate the event/range if a change was expected. |
| `unsupported_status` | The name-status stream contains a change kind outside the supported ordinary/add/modify/delete/rename/copy parser rule. | Review the change and extend the parser rule before narrowing it. |
| `diff_unparseable` | The NUL-delimited name-status stream is malformed. | Recreate the canonical full-history diff and fix the producer or checkout; keep the full fallback. |
| `unsupported_file_mode` | A changed path has a non-ordinary mode, or tree-mode inspection failed. | Resolve the special-file/symlink/mode issue and review it as full coverage. |
| `unknown_path` | After the other guards pass, a path has invalid repository-path syntax or no unique graph owner/application mapping. | Add reviewed ownership metadata and closure for a real new app; otherwise correct the path or retain the full fallback. Never add an unreviewed test label to make the change focused. |
| `cross_application` | After higher-priority guards pass, the change spans more than one mapped application root. | Run the full suite; split the change only when that is a truthful product boundary. |
| `migration_changed` | Any changed path contains a `migrations` segment; this is the highest-priority path guard. | Run fresh migration and full Django verification; migration changes are never focused. |
| `template_changed` | No migration guard applies and a changed path contains a `templates` segment. This takes precedence over static, HTML-suffix, and owner/risk guards, including for `_docs/templates/...` and `templates/...`. | Run the full browser profile and independent desktop/mobile screenshots. |
| `static_changed` | No migration or `templates` guard applies and a changed path contains a `static` segment. This takes precedence over HTML-suffix and owner/risk guards. | Run the full browser profile and inspect render-impact evidence. |
| `html_changed` | No migration, `templates`, or `static` guard applies and a path has a `.htm`, `.html`, `.jinja`, `.jinja2`, `.tmpl`, or `.tpl` suffix. This includes `_docs/*.html`, which selects `html_changed` before documentation ownership. | Run the full browser profile and inspect the affected render surface. |
| `shared_application` | No earlier path guard applies and an impacted graph owner is `shared.*` or carries the `auth_security_privacy` risk flag. | Keep shared-runtime and authentication/security/privacy owners on the full guard; update the graph, selector, and tests together if the boundary changes. |
| `configuration_or_dependency` | No earlier path guard applies and a root-level path matches the graph's configuration rules, or the impacted graph includes a surface owner or a `test_infrastructure`, `dependency_toolchain`, or `deployment_runtime` risk flag after the shared and documentation branches. | Run the full control-plane and regression contracts; update the graph, selector precedence, and contract tests when the boundary changes. |
| `documentation_or_contract` | No earlier migration, render, root-configuration, or shared guard applies and the impacted graph includes `surface.documentation` or a `compatibility_contract` risk flag. Thus cadmin and compatibility paths use this reason, but `_docs/*.html` and `_docs/templates/...` use their earlier render reasons. | Run the documentation/compatibility checks and retain the full classifier fallback; do not narrow a policy change. |

The reason string is recorded in the selection artifact and Actions summary. If an artifact and the
current graph disagree, evidence/reuse validation rejects it and requires fresh verification. The
[plan and evidence envelope](#plan-and-evidence-envelope) explains the rule. The
[`ci/evidence.py` implementation](../../ci/evidence.py) enforces it.

## Adding a new application

Add an application in a single reviewed graph change. Use the graph as the canonical map for app
ownership and verification closure. An unmapped or ambiguous path must remain `unknown_path`/`full`
until all checks below pass.

1. Declare ownership metadata. In [`ci/ownership.json`](../../ci/ownership.json), add an
   `app.<name>` owner node for the application prefix and a `django.<name>` verification leaf for
   its test label. The owner node must provide the exact `prefixes`, `exact`, `downstream`,
   `components`, `environment_dimensions`, `validity_class`, `risk_flags`, and `render_flags`
   fields. The verification leaf must provide `test_labels` and remain a virtual leaf with empty
   `prefixes`, `exact`, and `downstream`. Use an existing allowed component, environment, validity,
   risk, and render value. Change [`ci/ownership.schema.json`](../../ci/ownership.schema.json) and
   [`ci/ownership.py`](../../ci/ownership.py) only when the metadata schema changes, with a
   reviewed schema/policy-version update.

2. Prove and record the closure. Set `downstream` to include the new app's own
   `django.<name>` node and every verification node whose application imports it. Check the
   AST-based reverse-import check in
   [`tests_ci/test_ownership.py`](../../tests_ci/test_ownership.py), resolve ambiguous dynamic
   imports with an explicit edge, and update the exact closure vectors in
   [`ci/tests/test_selection.py`](../../ci/tests/test_selection.py). A closure inferred only from
   filenames or from the nearest app is incomplete.

3. Review selection labels and precedence. `APPLICATION_TEST_LABELS` in
   [`ci/selection.py`](../../ci/selection.py) is generated from the ownership graph and must
   classify an ordinary mapped app as `single_application`. `_force_full_reason()` combines
   graph impact from [`ci/ownership.py`](../../ci/ownership.py) with the fixed precedence for
   migrations, render paths, root configuration, shared owners, documentation/compatibility, and
   other surface/risk owners. Review those files and the selection tests when the new app needs a
   new guard. Update `FULL_REASONS`, the classifier/selection tests, and this glossary
   together. An unowned path must still produce `unknown_path` and `full`.

4. Review the profile contract. In [`ci/verification.py`](../../ci/verification.py), verify
   that the graph metadata yields the intended `single_application`/focused Django profile,
   `core` browser profile for backend-only changes, or `full` browser plus screenshot profile for
   render impact. The focused labels are executed by
   [`ci/focused_tests.py`](../../ci/focused_tests.py). Put risk in `ci/ownership.json`
   (`risk_flags`/`render_flags`) rather than weakening `_profile`, `_risk_reason`, or
   `_component_requirement`. Add impacted route/state nodes to that file's `screenshot_contract`
   when a new public surface needs critical coverage.

5. Bind the CI contract. Check the component commands, relevant input patterns, environment
   dimensions, and validity class in [`ci/ownership.json`](../../ci/ownership.json). Then verify
   the always-run policy in [`ci/quality_contract.py`](../../ci/quality_contract.py), the aggregate
   gate in [`ci/gate.py`](../../ci/gate.py), the maintained targets in [`Makefile`](../../Makefile),
   and the job configuration in
   [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml). A normal app addition doesn't
   need a workflow or Makefile edit. If a command or contract changes, update its owning tests
   (for example `ci/tests/test_workflows.py` and `ci/tests/test_gate.py`) in the same issue.

6. Run the focused graph and CI checks. From the candidate worktree, run these commands:

   ```bash
   uv run --frozen pytest ci/tests/test_selection.py tests_ci/test_ownership.py -q
   make test-ci
   make verification-plan
   ```

   Then run the profile-appropriate fresh commands: `make test-ci-focused` for the selected
   closure, `make test` for full Django, `make test-playwright-core` for backend-only browser
   impact, or `make test-playwright` for render impact. Use `make verification-quality` and
   `make verification-full` when the graph, policy, shared infrastructure, or new app boundary
   warrants the broader contract.

7. Record evidence and keep the backstop. Validate the plan with
   the following commands:

   ```text
   make verification-evidence-check VERIFY_PLAN=.tmp/verification/verification-plan.json
   make verification-report-check VERIFY_PLAN=.tmp/verification/verification-plan.json
   ```

   The evidence/reuse contract permits reuse only when its evidence is exact and validated.
   Missing, stale, mismatched, or untrusted evidence causes a rerun. Confirm that the scheduled
   [`scheduled-full-regression.yml`](../../.github/workflows/scheduled-full-regression.yml)
   remains the four-hour full-regression backstop, not a reason to omit required fresh evidence.

## Plan and evidence envelope

`python -m ci.verification plan` emits a strict JSON plan containing the canonical source tree,
changed paths, direct and transitive nodes, risk flags, test labels, browser/render fingerprint,
component input manifests, and one disposition for every component. A release plan always reruns
the container component because publish/deploy requires an exact candidate image even when older
runtime inputs match.

Relevant inputs are Git object identities grouped as source, tests, fixtures, tools, and
configuration. Digests use deterministic length-prefixed bytes, so hostile filenames cannot create
delimiter collisions. The environment fingerprint contains only allowlisted non-secret settings
plus Python, Django, uv, Playwright, operating-system, architecture, browser, database, and runner
identity. On GitHub-hosted runners, that identity includes both the moving image label and concrete
`ImageVersion`; changing either invalidates reuse and changes scheduled verification state. Secrets
and production data are never evidence inputs or output.

The planner declares a separate exact environment for each component, including required
allowlisted settings such as `DJANGO_SETTINGS_MODULE`. The component's own job computes its actual
fingerprint, compares it byte-for-byte with that component contract, and retains the fingerprint as
a digest-bound artifact. Recording never copies the classifier job's environment. A different
`ImageVersion`, settings module, or other allowlisted execution value blocks the envelope and
aggregate gate and cannot be accepted during replay, report validation, or evidence reuse.

Manual promotion and rollback apply `quality-contract-v1` from the trusted current workflow
controller to the exact selected release checkout. The contract invokes the explicit maintained
quality targets (`terminology-check`, `database-portability-check`, `security-check`, lint, format,
type, migration, Django, deployment, and CI-policy checks) in that checkout. A pre-contract release such as
`a220728` is valid when all primitive targets exist; it does not need the future aggregate
`verification-quality` target. If an aggregate target is present, its declared prerequisites must
match the versioned contract exactly. A missing primitive, altered aggregate, duplicate definition,
unsafe Makefile, or failed target blocks quality and the aggregate release gate; no fallback or
skip is allowed. This keeps historical-source execution isolated while preventing selected source
from choosing or weakening the controller's quality policy.

An evidence envelope binds all of the following:

- source tree and relevant-input manifest/digest;
- graph digest and policy version;
- environment digest;
- component, allowlisted command, normalized result and exit status, component-specific
  command/test/assertion counts, and artifact SHA-256 records;
- producer provenance, start/completion/expiry, and optional supersession;
- for screenshots, the render digest, route/state, viewport/browser, pass verdict, and independent
  inspection flag.

Standard, visual, volatile, and live evidence expires after 7 days, 7 days, 24 hours, and 4 hours,
respectively. Reuse requires the exact relevant-input, environment, and policy digests, an unexpired
latest success, intact artifacts, and a trusted origin. CI accepts only bounded artifacts from
allowlisted GitHub Actions workflows on `main`; local engineer evidence is never silently promoted
to CI evidence. The tester may consume local evidence but recomputes the plan and validates every
artifact independently.

The classifier artifact is named `ci-selection-<run>-attempt-<attempt>`. It contains the canonical
`ci-selection.json` plus a code-owned `ci-selection-provenance.json` sidecar. Both are bounded,
schema-validated facts: source SHAs, controller and release identity, run/attempt, profile, reason,
changed-path count, mapped roots, closed test labels, and the SHA-256 digest of the exact canonical
selection bytes. It intentionally contains no filenames or unbounded logs. Every consumer resolves
the current attempt first. On a failed-job rerun, a reused classifier may use attempt one only when
the current artifact is absent and the sidecar proves attempt one belongs to this same run; there is
no latest-artifact or cross-run fallback. Malformed, duplicate, stale, mismatched, or non-canonical
evidence fails closed before tests or the aggregate gate. The `ci-gate` artifact records the resolved
attempt/mode, safe rejection reason when applicable, and all required job outcomes; capture, publish,
and deploy require that aggregate gate to succeed. The aggregate report additionally validates the
versioned ownership/evidence plan, every component envelope, retained output and environment
digests, and exhaustive rerun/reused/skipped/not-applicable buckets before the gate can succeed.
Missing, malformed, partial, expired, tampered, untrusted, or superseded evidence reruns. A later
failure, cancellation, timeout, stale/action-required outcome, or malformed history also reruns and
can never fall back to an older pass. Evidence history is bounded, archive identity is exact, and
ZIP traversal, symlinks, excessive files, and excessive expanded size are rejected.

Automated components retain the output that produced their claim. Pytest and Django result logs are
parsed into executed/pass/fail/skip counts; structured-content and container checks emit validated
machine records; screenshot claims remain bound to the inspected image records. The envelope binds
that output's path, size, SHA-256, format, and derived counts. Reuse, report validation, and release
gates reopen the retained output and independently derive those counts. A recorder-only status file,
caller-supplied count, missing output, or count/output mismatch is invalid evidence.

Playwright uses the tracked quarantine policy in
[`playwright-flake-policy.md`](playwright-flake-policy.md). Blocking targets exclude the
`quarantine` marker, while the scheduled monitor runs `make test-playwright-quarantined`.
Playwright evidence records `attempted`, `passed`, `failed`, `rerun`, and `quarantined`
alongside the existing test counts. A successful blocking run must contain the plugin's
complete summary and a matching pytest summary; partial output, an unexpected rerun, or a
count mismatch cannot satisfy the aggregate gate. The scheduled quarantine report is
non-blocking for deployment but retains a failure verdict and its digest-bound output.

Large structured-content artifacts fail closed unless every record has a non-empty identity from
the graph-declared stable fields and at least one valid canonical relative or HTTP(S) URL. Record
hashes are not synthesized as identities, empty catalogs do not pass, and URL completeness compares
records with URLs to the actual record count instead of accepting an empty `0/0` proof.

## Four-bucket report

Every report classifies every component exactly once:

- `rerun`: required work executed for this plan, with its result envelope;
- `reused`: an exact validated prior success, with provenance and expiry;
- `skipped`: required work without acceptable evidence; this fails the report, except that an
  engineer/CI report may be explicitly pending the independent screenshot gate;
- `not_applicable`: a component outside the computed impact, with a reason.

Missing or duplicated components, missing artifact files, an evidence/plan/report digest mismatch,
an unknown evidence ID, inconsistent counts, a required skip described as a pass, or pending human
evidence in a CI/tester-final report is invalid. CI publishes the selection, plan, component
envelopes, report, and gate result as
`verification-evidence-<run>-attempt-<attempt>`. Deployment still requires all release jobs and the
aggregate gate.

The report and Actions summary also carry the policy version, direct and downstream nodes, risk
flags, verification-state digest, and invalid-evidence reasons. Every bucket entry retains its exact
command and input/state proof. Rerun and reused entries additionally expose actual counts, evidence
ID, origin, expiry, output digest, and all artifact locations/digests; inapplicable or skipped entries
state explicitly that no evidence exists instead of presenting an empty success.

## Scheduled coverage

`scheduled-full-regression.yml` runs at minute 17 every four UTC hours with queued concurrency,
read-only contents, Actions-history permission, and no deployment/AWS authority. Its state digest
binds the Git tree manifest, graph/policy, and environment fingerprint. Commit metadata alone does
not force work: a different SHA with the same exact state can skip. Any changed state runs full.

Only a latest-attempt, completed, successful job named `full-regression` with the exact positive run
ID is a coverage anchor. Any later non-success, missing marker, malformed/incomplete/unavailable
history, or unavailable anchor state fails safe to full. Historical state is read only from the
retained aggregate artifact for that exact run and attempt; it is never recomputed with the current
runner environment, and an archive cannot self-assert another run, attempt, SHA, or repository. A
successful selector-only skip never becomes an anchor. The always-running scheduled gate accepts
only the exact unchanged-state skip or a successful selected full run.

A selected scheduled run retains independent quality, factory, migration, full Django, full local
Playwright, and container coverage. Remote/live/provider tests remain excluded.

## Local and role-specific commands

Generate and execute a local plan against the current committed `HEAD` plus its uncommitted
candidate changes:

```text
make verification-plan
make verification-run VERIFY_ISSUE=113 VERIFY_WORKTREE=issue-113-risk-evidence
make verification-evidence-check VERIFY_PLAN=.tmp/verification/verification-plan.json
make verification-report-check VERIFY_PLAN=.tmp/verification/verification-plan.json \
  VERIFY_REPORT=.tmp/verification/verification-report.json
make verification-plan VERIFY_CONSUMER=tester
make verification-run VERIFY_CONSUMER=tester VERIFY_PRODUCER_ROLE=tester \
  VERIFY_PHASE=tester VERIFY_ISSUE=113 VERIFY_WORKTREE=issue-113-risk-evidence
```

`VERIFY_ISSUE` is required: `verification-run` has no default issue number and fails closed without
one, because local evidence must never be silently attributed to an issue the caller did not name.
Override `VERIFY_BASE_SHA`, `VERIFY_HEAD_SHA`, `VERIFY_OUTPUT_DIR`, or `VERIFY_EVIDENCE_DIR` only
with explicit reviewed paths/revisions. `verification-run` executes only allowlisted argument
vectors and records each rerun result below `.tmp/verification/evidence/`.

Every component execution is bounded by an explicit per-component wall-clock timeout. The default
is 3600 seconds (one hour), which exceeds the longest legitimate local suite: the full Django run,
the core browser run, and the exact production-container build each finish well inside it. When the
bound expires the runner terminates and then kills the component's whole process group, retains the
partial output below the component's output artifact, records the result envelope as `timed_out`
with exit code 124 and the exact command, and continues with the remaining components, so
`verification-report-check` still emits `verification-report.json` with a `failure` verdict; a
timed-out component is an executed rerun with a failing result, never a skip. Override the bound
only for deliberate bounded-hang diagnostics by invoking the runner directly with
`--component-timeout-seconds <positive-seconds>`; the Makefile target always uses the documented
default. `verification-run` preserves the runner's nonzero status while allowing the report-check
step to run, so the aggregate report remains the final failure evidence.

The proportional fresh gates remain available:

```text
make verification-quality
make verification-container
make verification-full
```

`verification-full` includes quality and CI contract checks, a fresh SQLite migration, the complete
Django and Playwright suites, compatibility checks, and the exact production-container build,
static-manifest negative cases, runtime identity/provenance, and liveness checks. All dependencies
run through uv-backed targets.

The engineer posts the exact base/head, graph and plan digests, four-bucket report, commands/counts,
artifact paths, and screenshot requirement, then freezes the uncommitted worktree. The independent
tester recomputes the plan from that same base/head with `consumer=tester`, rejects unexplained
drift, validates all proposed reuse, executes reruns, captures/inspects required screenshots under
`.tmp/screenshots/`, and produces a tester-final report. Product acceptance remains a separate gate.

`actionlint` 1.7.12 predates GitHub's supported `concurrency.queue` syntax. The repository actionlint
configuration ignores only that stale-schema diagnostic for the scheduled workflow.
