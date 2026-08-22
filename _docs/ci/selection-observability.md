# Selective-CI selection observability

The scheduled full-regression selector writes a redacted aggregate report for the latest 20
completed `ci.yml` push runs on `main`. The window is bounded by run count, not elapsed time, and
workflow reruns count as completed runs. The JSON report is retained in the scheduled selector
artifact for 30 days and the same aggregate summary is appended to the Actions step summary.

The report contains profile counts/rates (`focused` and `full`), classifier-reason counts/rates,
and evidence-disposition counts/rates (`reused`, `rerun`, and `not_applicable`). Evidence reuse is
reported both per applicable component and per run with at least one reused component. It contains
no run IDs, source SHAs, changed paths, logs, credentials, or production data.

Empty history produces zeroed counts with an `empty` status. Missing, malformed, oversized, or
unavailable history produces an `unavailable` status. The report is descriptive only: history
failure cannot authorize a selection skip, and the scheduled selector continues to use its
fail-closed full-regression decision. A malformed historical artifact invalidates the aggregate
report rather than contributing untrusted data.

The top-level ownership test enumerates repository source directories and requires each to have an
owner prefix in `ci/ownership.json` or an explicit reviewed full-fallback reason in
`tests_ci/test_top_level_directory_guard.py`. Adding an unreviewed application therefore fails
the CI contract with instructions for the required graph/list update.
