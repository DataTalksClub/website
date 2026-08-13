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
cycles, unknown node kinds, and invalid patterns before making a decision. Its initial application
closures exactly preserve #104:

| Changed owner | Django labels |
| --- | --- |
| `api` | `api` |
| `studio_courses` | `studio_courses` |
| `content` | `accounts content.tests core` |
| `courses` | `accounts api content.tests core courses data studio_courses` |
| `data` | `api courses data studio_courses` |
| `jobs` | `jobs` |
| `management_api` | `api management_api` |
| `management_auth` | `api core management_api management_auth` |
| `review_import` | `accounts review_import` |
| `studio` | `accounts core studio` |

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

Templates, CSS, JavaScript, navigation, view/URL/context/serializer data shape, browser fixtures,
and screenshot-harness changes are render impact. They select the full browser suite and fresh
independent desktop/mobile screenshots. Critical route/state coverage is derived from the impacted
graph nodes; an unmapped render impact fails closed to every critical route/state. Backend-only
changes use the core browser profile. A large value-only content change is verified with exhaustive
deterministic artifacts binding record counts, stable identities and order, canonical URL order,
uniqueness, metadata completeness, and file digests; it does not get a probabilistic sample.

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
make verification-run VERIFY_WORKTREE=issue-113-risk-evidence
make verification-evidence-check VERIFY_PLAN=.tmp/verification/verification-plan.json
make verification-report-check VERIFY_PLAN=.tmp/verification/verification-plan.json \
  VERIFY_REPORT=.tmp/verification/verification-report.json
make verification-plan VERIFY_CONSUMER=tester
make verification-run VERIFY_CONSUMER=tester VERIFY_PRODUCER_ROLE=tester \
  VERIFY_PHASE=tester VERIFY_WORKTREE=issue-113-risk-evidence
```

Override `VERIFY_BASE_SHA`, `VERIFY_HEAD_SHA`, `VERIFY_OUTPUT_DIR`, or `VERIFY_EVIDENCE_DIR` only
with explicit reviewed paths/revisions. `verification-run` executes only allowlisted argument
vectors and records each rerun result below `.tmp/verification/evidence/`.

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
