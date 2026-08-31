# Local reviewed course-module preparation

This runbook prepares only the reviewed 2026 cohorts for LLM Zoomcamp, ML Zoomcamp, and AI Dev
Tools Zoomcamp. It is a bounded local/test workflow; it is not a production importer and must not
be run against a production database.

## Source boundary

The caller supplies one JSON manifest with exactly three `sources` records. Each record must
include:

- `source_stable_id`: `llm-zoomcamp`, `ml-zoomcamp`, or `ai-dev-tools-zoomcamp`;
- `cohort_identifier`: `2026`;
- the repository owner/name, `main` branch, and full 40-character `commit_sha`;
- an absolute checkout `root`;
- a `files` map of repository-relative paths to their SHA-256 values; and
- `snapshot_sha256`, the deterministic SHA-256 over sorted `path\0file-sha256\n` entries.

The command checks the checkout revision, refuses symlinks and oversized snapshots, hashes every
listed file, rejects sensitive path components (including `.env`, credentials, registrations,
learners, and submissions), parses the snapshot, and filters out all implicit/legacy cohorts
before any database write. `homework_slug_overrides` may explicitly map a source homework path to
an existing local slug when adopting an already-operated homework row.

Generate this manifest from the reviewed checkouts, retain it under `.tmp`, and keep the exact
source commit and file hashes with the preparation evidence. Do not put learner data, provider
payloads, credentials, or raw production exports in the manifest.

## Validate and apply

Use the local SQLite settings and the same manifest for both commands:

```bash
DTC_ENVIRONMENT=local \
DTC_SQLITE_PATH=.tmp/local.sqlite3 \
DJANGO_SETTINGS_MODULE=website.settings.local \
uv run python manage.py prepare_local_course_modules \
  --input .tmp/course-modules-input.json --check

DTC_ENVIRONMENT=local \
DTC_SQLITE_PATH=.tmp/local.sqlite3 \
DJANGO_SETTINGS_MODULE=website.settings.local \
uv run python manage.py prepare_local_course_modules \
  --input .tmp/course-modules-input.json
```

The command uses the existing transactional curriculum importer with an explicit preservation
mode. It changes source-owned course/cohort provenance and the selected cohort's curriculum
format/flow. Existing cohort dates, visibility, registration URL, completion/scoring settings,
homework fields/questions, projects, campaigns, enrollments, submissions, and other operational
records are not rewritten. Project flow entries are references only; missing projects are rejected
rather than created.

Re-running the apply command with the same manifest is an idempotent replay. The JSON output
contains only source identities, commits/checksums, aggregate curriculum counts, replay state, and
course format/status counts.

There is no all-source production-data preparation orchestrator in this checkout. The Luma worker
and its prepared registration aggregate remain separate; this course command neither reads nor
modifies that data. Do not promote this local command into a production workflow without a
separately reviewed source transport and operational-data contract.
