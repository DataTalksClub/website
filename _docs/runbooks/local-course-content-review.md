# Local course-content review database

This workflow builds a private, content-only SQLite database for reviewing the adopted course
platform locally. It imports only the versioned table-and-column allowlist in
`review_import/manifest.py`. It does not import production accounts, sessions, tokens, social
accounts, registrations, enrollments, submissions, reviews, learner-authored text or links,
Datamailer data, jobs, audits, or provider payloads.

The older broad `scripts/load_rds_export.py` entry point is disabled and points to this replacement.
Use only the commands below for a protected snapshot.

## Where the CMP snapshot lives

The snapshot is **not** stored in this repository and must never be committed to it. On the
maintainer workstation the current converted snapshot is the working database of the sibling
Course Management Platform checkout:

```
/home/alexey/git/course-management-platform/db/db.sqlite3
```

It is gitignored in that repository (`.gitignore:79`), so it exists only on machines that have
built it. It carries real production learner data — accounts, enrollments, submissions — and is
therefore protected: do not copy it into this repository, into `_docs/`, into an issue comment, or
into any shared location.

### How it is produced

It is the SQLite rendering of an Amazon RDS snapshot export, not a `pg_dump`:

1. The `course-management-manual` Aurora cluster is exported to Parquet in S3 by the
   `rds-export` Terraform root in the `aws-infra` repository (`main/rds-export/`, bucket
   `course-management-rds-backups-<account-id>`, snapshot prefix `cmp-`). That root also owns the
   KMS key, the RDS export service role, and the cron IAM user for the runner.
2. The `alexeygrigorev/rds-export` utility converts the exported Parquet into a SQLite file.
3. In the course-management-platform checkout, `scripts/load_rds_export.py` loads that converted
   file into a freshly migrated Django SQLite schema and swaps the result into `db/db.sqlite3`.
   Prior conversions leave dated evidence in that repository's `.tmp/`, for example
   `.tmp/rds-import-<timestamp>.db` and `.tmp/db.sqlite3.before-rds-<timestamp>`.

To obtain a fresh snapshot, re-run that export/convert/load chain in the course-management-platform
and `aws-infra` repositories. Do not request or pass around a copy of the file itself.

### Working copy

Copy the snapshot to a private path outside this repository before importing. The importer refuses
a source inside the repository tree (`protected-source-inside-repository`) and re-fingerprints the
source during the run, so importing directly from a database another process may write to can fail
with `source-changed`:

```bash
install -m 700 -d "$SNAPSHOT_DIR"
install -m 600 /home/alexey/git/course-management-platform/db/db.sqlite3 \
    "$SNAPSHOT_DIR/cmp-source.sqlite3"
```

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

## Upstream schema drift

The adopted course platform is pinned
(`_docs/adoption/course-platform/source-pin.json`, currently
`course-management-platform@98a235283904b4ef9ad29e196298540756cf1bcc`) while the upstream database
keeps moving. A snapshot taken from a newer upstream can contain tables the migrated target schema
does not have.

Empty unknown tables are skipped: they are not in `review_import/manifest.py`, so they would not
be copied even if the target had them. The report lists them as
`skipped_empty_unknown_tables`. As of the current snapshot those tables are:

| table | upstream migration | rows in snapshot |
| --- | --- | --- |
| `courses_systemprojectevaluation` | `0041_system_project_evaluations` | 0 |
| `courses_systemevaluationcriteriaresponse` | `0041_system_project_evaluations` | 0 |
| `courses_emailcampaign` | `0043_remove_registrationcampaign_email_body_markdown_and_more` | 0 |

An unknown table that holds rows still fails closed (`schema-unknown-table`). That is a pin-move
decision, not a local workaround. Do not drop non-empty drift tables from the snapshot.

## Verify the import

A zero exit code is not sufficient. Check the report's `validation_results` block — every entry
must be `passed` (or `disabled` for `outbound_side_effects`) — then confirm the content and the
sanitization directly:

```bash
DB=.tmp/review-data/review.sqlite3
sqlite3 "file:$DB?mode=ro" "PRAGMA integrity_check; PRAGMA foreign_key_check;"
sqlite3 "file:$DB?mode=ro" "SELECT slug, visible, finished, start_date, end_date
    FROM courses_course ORDER BY slug;"
sqlite3 "file:$DB?mode=ro" "SELECT COUNT(*) FROM accounts_customuser;"
```

`accounts_customuser` must be exactly `1` — the synthetic review administrator — and every learner
table (`courses_enrollment`, `courses_submission`, `courses_projectsubmission`,
`courses_courseregistration`) must be `0`. The report's
`source_origin_denylist_zero_counts` block asserts the same thing for the whole denylist.

Re-running the dry run for the same source and code version must reproduce the same
`logical_checksum`; a changed checksum means the source or the allowlist moved.

### Cohort and family mapping

`Cohort` is `courses_course` (`courses/models/cohort.py:242`) and `Course` — the reusable family —
is `courses_course_family` (`courses/models/cohort.py:70`). The upstream snapshot has no family
table, so the importer synthesises one: it strips a trailing `-YYYY` from each cohort slug and
assigns a deterministic `uuid5` family id (`review_import/workflow.py:1058-1128`). Imported cohorts
get `curriculum_format = legacy`. A cohort whose slug does not end in `-YYYY` becomes its own
family, so upstream test rows land as first-class visible families.

### What this snapshot does and does not contain

The 2026-08-31 snapshot has 20 cohorts across 12 derived families. Two points matter when it is
used to reason about public course content:

- **It is not a complete 2026 catalogue.** It contains `de-zoomcamp-2026` and `llm-zoomcamp-2026`,
  but **not** `ml-zoomcamp-2026` and **not** `ai-dev-tools-zoomcamp-2026`. Its AI Dev Tools cohort
  is `ai-dev-tools-2025`, under family `ai-dev-tools` rather than `ai-dev-tools-zoomcamp`.
  Those two cohorts come from the separate manifest-driven flow in
  `_docs/runbooks/local-course-modules-preparation.md`, not from this snapshot.
- **It contains upstream test rows.** `fake-course` and `fake-course-2` are present with
  `visible = 1`, and each becomes its own visible family. Exclude them from any review of public
  output, and never let them reach a projection or a published page.

Note that the public homepage reads this database. `core/home_content.py` `course_catalog()` and the
featured panel resolve the newest visible cohort of every visible course family through
`courses/services/public_course_catalog.py`, and so do `/courses/ai-dev-tools-zoomcamp/cohorts/ai-dev-tools-2026`
and its registration preview. Refreshing the review database therefore changes what the homepage
advertises, and any visible row here — including the `fake-course` test rows above — would be
advertised on the front page. Confirm the cutover database carries no visible fixture course.

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
