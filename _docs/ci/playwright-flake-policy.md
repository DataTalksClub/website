# Playwright flake policy

Status: active for local and GitHub Actions Playwright verification.

## Decision and owner

This repository uses tracked quarantine, not automatic retries. The owner is the
CI/testing maintainers; the tracking issue for an individual test owns its root-cause
investigation and exit decision. Automatic retries remain disabled until the evidence
contract can independently account for every extra attempt.

## Entry and exit rules

A test may enter quarantine only when all of the following are true:

- the failure is intermittent or is blocked by a known environment/harness defect;
- the test has a GitHub tracking issue with one named owner, the test node id, observed
  failure evidence, suspected cause, and a review/exit plan; and
- the test is marked at the test level with the issue number:
  `@pytest.mark.quarantine(issue=208)`.

The Playwright policy plugin rejects a quarantine marker without exactly one positive
`issue` number. It does not call GitHub at test time; the issue and its owner are reviewed
when the marker is added and in the normal issue lifecycle. A quarantine marker is never
an approval for an expected product failure, a remote mutation, or a live-provider test.

Remove the marker after the root cause is fixed, the tracking issue records the fix, and
the test passes in the blocking command and two consecutive scheduled runs. Close the
tracking issue only after the marker is removed. If the fix is not ready, the issue owner
must renew the review plan before the next review date; quarantine is not a permanent
failure bucket.

## Commands and blocking behavior

The blocking commands are explicit and exclude quarantined tests:

```text
make test-playwright-smoke
make test-playwright-core
make test-playwright
```

All three commands load `ci.playwright_flake_policy` and use `not quarantine` in their marker
expression. Smoke is the bounded first-pass tier; core and full retain the broader render and
high-risk coverage. The normal CI aggregate gate consumes their ordinary Playwright evidence and
cannot pass a partial or internally inconsistent successful run.

The scheduled monitor is also explicit:

```text
make test-playwright-quarantined
```

The `playwright-quarantine` job in `.github/workflows/scheduled-full-regression.yml` runs
this command every scheduled trigger after the selector succeeds, with a 45-minute bound.
It is intentionally outside `full-regression` and `scheduled-gate`: a quarantined failure
is visible in its uploaded report and step summary but does not block deployment. A
collection error, timeout, malformed summary, or partial run is still a failed monitor
result, never a pass.

The quarantine target treats pytest's exit code 5 (no quarantined tests currently
collected) as a complete empty monitor run. Any other non-zero exit remains a failure.

## Evidence and report semantics

The plugin emits one `DTC_FLAKE_POLICY_V1` summary. For a complete run, the counters mean:

- `attempted`: selected test cases with a terminal outcome;
- `passed`: passed and xpassed cases;
- `failed`: failed/error cases;
- `skipped`: skipped and xfailed cases;
- `rerun`: extra attempts; this policy requires `0`;
- `quarantined`: tracked quarantine cases discovered by collection. Blocking commands
  exclude them; the scheduled command selects them.

`attempted` must equal `passed + failed + skipped`. Successful blocking Playwright evidence
also requires the policy summary, a normal pytest completion summary with matching counts,
`rerun=0`, zero failures, and a complete collection. Missing or contradictory output is
invalid evidence. Therefore a log containing an early passing subset cannot be promoted to
a successful aggregate report. Failed or timed-out runs retain whatever verified counts
their output contains, or zero counts when the output is truncated, and remain failures.

The standard verification report exposes `attempted`, `passed`, `failed`, `rerun`, and
`quarantined` in the Playwright evidence/counts line. The scheduled quarantine job also
uploads `playwright-quarantine-report.json`, whose verdict is `failure` for a failed,
partial, rerun, or malformed monitor run even though the job is non-blocking.
