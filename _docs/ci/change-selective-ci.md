# Change-selective CI

Normal release CI classifies the complete push range before choosing Django tests. The immutable
release SHA must match both the push `after` SHA and `github.sha`; the base is the push `before`
SHA. The classifier reads a full-history, rename/copy-aware, NUL-delimited Git diff and examines
both trees to allow only ordinary tracked files.

A focused selection is possible only when every changed path belongs to one reviewed application
root and none of the force-full rules apply. The code-owned root-to-test closure lives in
`ci/selection.py`. Shared applications, migrations, templates, static assets, workflow and deploy
configuration, dependencies, documentation, unknown paths, unsupported Git records, and ambiguous
source ranges all select the existing full `make test` target. Manual non-probe releases are always
full. The probe jobs remain outside this selector.

Central `test_support/` factories and runtime code, root `conftest.py`/`sitecustomize.py`, test
settings/runner code, migration files, marker registration, and Playwright fixtures/tests always
select full coverage. An app-owned factory below an already mapped application root may use that
root's reviewed closure; a missing or unmapped label never narrows the suite. A normal full profile
runs factory and migration contracts before the complete Django suite. Focused profiles retain the
existing exact sorted label runner.

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
and deploy require that aggregate gate to succeed.

The selector and its contract tests run from the current workflow controller checkout. Django and
the existing release checks run against the exact selected release checkout. This keeps a manual
promotion or rollback of an older reachable release compatible while still applying the current
full-only manual selection and aggregate gate.

## Scheduled coverage

`scheduled-full-regression.yml` starts at minute 17 every four UTC hours and has a separate queued
concurrency group. It has read-only contents and Actions-history permissions and no deployment or
AWS authority.

The selector searches at most 100 completed schedule runs of that workflow. Only a latest-attempt
job named `full-regression` with the exact positive run id, completed status, and successful
conclusion is a coverage anchor. An unsuccessful immediately
previous run always retries, even if an older anchor covers the same SHA. A successful selector-only
skip never becomes an anchor, and it cannot hide an unsuccessful run later than the older anchor.
Missing, malformed, incomplete, or unavailable history fails safe to running the full regression.

The scheduled selection and aggregate artifacts report the current SHA, stable reason, previous
run, coverage anchor, inspected depth, and component outcomes. `already_successfully_covered` is the
only intentional skip reason. The always-running scheduled gate accepts that exact skip shape or a
successful selected full run; unexpected skips, cancellations, timeouts, and failures fail it.

A selected scheduled run has independent quality, factory, migration, full Django, full local
Playwright, and container components. The fixed `full-regression` marker and always-running gate
require every component. All application-test components use fresh SQLite and neither provision nor
connect to PostgreSQL. Remote/live/provider tests remain excluded.

## Local verification

Use uv-backed project targets for deterministic checks:

```text
make test-ci
CI_SELECTION_PATH=.tmp/ci-selection/ci-selection.json make test-ci-focused
make test-factories
make test-migrations
make test-playwright
make test-all
make lint
make format-check
make typecheck
```

`actionlint` 1.7.12 predates GitHub's supported `concurrency.queue` syntax. The repository actionlint
configuration ignores only that one stale-schema diagnostic for the scheduled workflow; every
other workflow diagnostic remains active.
