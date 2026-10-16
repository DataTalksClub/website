# Linting

The project runs two Ruff layers: a small mandatory blocking gate, and an
expanded advisory report that stages candidate rules before they become
blocking. Locally the blocking gate is:

```bash
make lint
```

That target is `uv run python scripts/ci.py lint`, which runs
`uv run --frozen ruff check` against the default `pyproject.toml` profile
(`B`, `DJ`, `E`, `F`, `I`, and `UP`). The `pyproject.toml` exclude list keeps
the historically adopted directories (`accounts/`, `api/`, `courses/`,
`course_management/`, `studio_courses/`, `e2e/`, `scripts/`, and migrations)
out of the default sweep; `scripts/ci.py` then passes the adoption opt-in
paths (`ADOPTION_INTEGRATION_PYTHON` and `PRODUCTION_IMPORT_PYTHON`)
explicitly, so modules opted back into the gates are still linted. `lint` is
also one of the quality tasks that `scripts/ci.py quality` — the release
`verification-quality` contract — runs.

Run the staged report locally with:

```bash
make lint-advisory
```

The advisory command runs `uv run ruff check --config ruff-advisory.toml
--statistics --exit-zero .` against a standalone configuration, so the
`pyproject.toml` excludes do not apply and the whole repository is measured.
It stages the rule groups `B`, `BLE`, `C4`, `SIM`, `RET`, `ARG`, `T20`, and
`C901`; `B` is already blocking, so it appears in both reports.

## Current Staging Policy

- Keep `B`, `DJ`, `E`, `F`, `I`, and `UP` as the only mandatory Ruff selections
  until advisory counts are reviewed.
- Treat `ruff-advisory.toml` as the staging config for new checks.
- Keep generated, scratch, and framework-signature noise out of the advisory
  report narrowly:
  - migrations, via the advisory config's `exclude`;
  - scratch checkouts and downloads under `.tmp/`, via the same `exclude`;
  - Django signal, check, and management-command callback signatures;
  - test/mock/fixture and Playwright callback arguments;
  - CLI script and deploy-helper stdout output.
- Do not ignore broad exceptions or complex functions in core application code
  by default; those are the audit findings the report is meant to expose.

## Promotion Path

1. Track `make lint-advisory` output in CI for at least a few feature cycles
   without failing builds.
2. Fix low-risk autofixable findings opportunistically: `C4`, `RET`, simple
   `SIM`, and obvious `B` issues.
3. Promote low-noise production checks first by moving selected rules from
   `ruff-advisory.toml` into `pyproject.toml`.
4. For `T20`, keep scripts and deploy helpers ignored, then make app and test
   code mandatory once remaining prints are removed or converted to logging.
5. For `BLE001`, replace broad catches with specific exceptions in core paths.
   Where defensive broad catches remain, document why and log with
   traceback/context before adding a narrow ignore.
6. For `C901`, start mandatory enforcement with a high threshold around the
   current hotspots, then lower it gradually as the largest content-sync,
   service, and view functions are split.
7. For `ARG`, convert truly unused parameters to underscore names only when
   signatures are not controlled by Django, mocks, or fixtures.

## Reading the Report

`make lint-advisory` is non-blocking by design. New feature work should not
stop because this target reports findings, but new or touched code should
avoid increasing broad exception counts, debug prints, or function complexity
when reasonable.
