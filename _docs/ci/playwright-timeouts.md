# Playwright timeout contract

Issue #205 bounds the Playwright and long-running CI jobs and gives Playwright pytest targets a
diagnostic per-test execution budget. The existing marker expressions and test profiles remain the
source of test selection; this contract adds no retries or quarantine.

## Hosted job budgets

The budgets cover dependency setup, browser installation, the longest accepted green execution,
fixtures and teardown, and evidence recording. The accepted full run for issue #199 measured about
49 seconds for quality, 181 seconds for Django, and 1,074 seconds for Playwright. The current
integration base retains the later issue #206 increase of the normal push Playwright reserve to 60
minutes.

| Workflow job | `timeout-minutes` |
| --- | ---: |
| Push `classification`; scheduled `selector` | 10 |
| Push or scheduled `quality` | 15 |
| Push or scheduled `django` | 30 |
| Push `playwright` | 60 |
| Scheduled `playwright` | 45 |
| Push `screenshots` | 30 |

The normal and scheduled workflows use explicit budgets for every corresponding long-running job.
The scheduled workflow has no screenshot job, so its Playwright job is the outer bound for that
profile.

## Local per-test diagnostic budget

`test-playwright-smoke`, `test-playwright-core`, `test-playwright`, `test-browser` (through its
`test-playwright` prerequisite), and `test-accessibility` pass pytest's built-in
`-o faulthandler_timeout=120` option. After 120 seconds, Python emits a diagnostic traceback for
the running test and thread state, including the active pytest node when available. This option is
dependency-free and diagnostic: it does not turn a wedged process into a successful run or replace
the outer termination guards.

The hosted job `timeout-minutes` value bounds setup, fixtures, teardown, and other work outside the
test body. The local runner contract from issue #199 additionally bounds each component at 3,600
seconds, terminates its process group, retains partial output, and emits a timed-out aggregate
report. These layers make a hang observable at the nearest useful boundary while preserving the
failure.

## Evidence contract

Successful test output must end at a terminal pytest summary or unittest status. The evidence parser
ignores only ordinary `make` directory enter/leave lines and Django test-database teardown lines
around that terminal status. It rejects known interruption markers such as `Timeout`,
`KeyboardInterrupt`, `Terminated`, and `SIGTERM` or `SIGKILL`. An earlier summary followed by an
interrupted or incomplete log cannot validate success.

Failure and timed-out envelopes retain their output and result. If a partial log has no parseable
counts, its derived counts are zero; it is still digest-bound failure evidence. The verification
report keeps the executed component in `rerun`, records `failure` or `timed_out`, and returns a
failure verdict. Missing, partial, or interrupted output cannot become a success or a skip.

The CI regression test creates a synthetic hanging pytest test, runs it with the built-in
faulthandler diagnostic and a short process-level bound, and asserts a nonzero result with a named
timeout. Production jobs use the workflow and runner bounds above; the regression's short bound
keeps the test suite deterministic without adding a runtime dependency.
