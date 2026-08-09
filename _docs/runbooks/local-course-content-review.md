# Local course-content review database

This workflow builds a private, content-only SQLite database for reviewing the adopted course
platform locally. It imports only the versioned table-and-column allowlist in
`review_import/manifest.py`. It does not import production accounts, sessions, tokens, social
accounts, registrations, enrollments, submissions, reviews, learner-authored text or links,
Datamailer data, jobs, audits, or provider payloads.

The older broad `scripts/load_rds_export.py` entry point is disabled and points to this replacement.
Use only the commands below for a protected snapshot.

## Build safely

Keep the original converted SQLite snapshot in its existing protected location outside this
repository. Select it explicitly; the command never searches for a “latest” backup. Choose a
short opaque snapshot identifier that contains no person, account, or secret information.

First run the no-publish validation:

```bash
make review-data-dry-run \
    SOURCE_DB=/protected/path/to/converted-snapshot.sqlite3 \
    SNAPSHOT_ID=cmp-2026-08-08
```

Dry run opens the source read-only, validates a private trial database, prints only safe aggregate
evidence, deletes the trial, and leaves the current artifact, report, and review database
unchanged.

Apply the same named snapshot:

```bash
make review-data \
    SOURCE_DB=/protected/path/to/converted-snapshot.sqlite3 \
    SNAPSHOT_ID=cmp-2026-08-08
```

By default, apply creates or updates exactly one wholly synthetic local administrator:
`review-admin@example.invalid`. Set `REVIEW_ADMIN_PASSWORD` in the local shell to override the
local-only default. The password is passed through the process environment and is never printed or
written to the report. Use `--no-admin` with the underlying Python command when no account is
needed.

Successful apply retains only:

- `.tmp/review-data/artifacts/<snapshot-id>.sqlite3`, the content-only sanitized database;
- `.tmp/review-data/reports/<snapshot-id>.json`, the redacted evidence report; and
- `.tmp/review-data/review.sqlite3`, the active local review database.

These paths are gitignored and created with private permissions. Do not upload or redistribute any
derived database, report, or screenshot.

## Browse locally

PostgreSQL is not required. Start Django against the review database with email, Datamailer
contact sync, immediate outbox dispatch, provider configuration, and the job scheduler forcibly
disabled regardless of shell or `.env` values:

```bash
make run-review-data
```

Open `http://localhost:8000/courses/`. Sign in only with the synthetic administrator when a staff
view is needed. Local review is read-only: mutating requests and external identity providers are
denied, while the linked CloudWatch page returns an explicit disabled state without constructing
an AWS client. Do not open or capture account, registration, enrollment, submission, review,
token, or communication screens. Store public-content-only screenshots below `.tmp/screenshots/`.

## Repeatability and cleanup

Repeating dry run or apply for the same source and code version must report the same table counts,
relationship counts, and logical checksum. SQLite bytes and the synthetic password hash may differ.
Only one build, dry run, or cleanup may own the private review-data root at a time. A concurrent
invocation fails closed with `concurrent-operation`; retry it after the active command finishes.

Remove one exact derived snapshot and report:

```bash
make review-data-cleanup SNAPSHOT_ID=cmp-2026-08-08
```

Removing the active local review database requires a separate explicit flag:

```bash
make review-data-cleanup SNAPSHOT_ID=cmp-2026-08-08 INCLUDE_TARGET=true
```

Cleanup accepts no glob, traversal, broad directory, home directory, repository root, or source
backup path. It is safe to run twice. The original snapshot is never changed or deleted.
