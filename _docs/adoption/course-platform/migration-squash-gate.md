# Phase-1 local migration history

Phase 1 explicitly authorizes a local-only squash because this deployment is not running in
production. The active `courses` graph is now one `0001_initial` migration generated from the
current phase-1 models. It creates `Cohort` (including `outcome`), keeps the legacy
`courses_course` table, and points the existing course-owned relations at `Cohort`. It does not
introduce the reusable Course entity or a new URL contract.

The pinned adoption ledgers and copied-file records retain the original numbered migration paths
as source provenance. Those historical records are not active copied destinations after this
squash; the active migration identity is the one-file `courses` graph above. The adoption contract
test's current-state check ignores those historical course migration checksum rows while
continuing to verify the other copied files and overlays.

The account backfill dependency was updated with the squash: `accounts.0005` now copies the one
enrollment profile field that still exists in the phase-1 schema, `certificate_name`. A fresh full
project replay and local reseed now complete successfully. The local database was rebuilt from a
timestamped backup, then restored with the pinned course catalog, representative homework
questions, future homework/project windows, outcomes, and local sign-in providers.

This remains a local-only development reset, not a production upgrade path. Production-like
parity, data reconciliation, and supported reverse/forward migration windows remain unverified.
