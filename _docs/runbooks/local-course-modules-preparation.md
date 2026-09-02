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

## Combined local rehearsal

The local-only orchestrator composes migrations, the reviewed event identity manifest, the
baseline course catalog, and this three-course module preparation. It also validates the prepared
Eventbrite and Luma aggregate snapshots against the safe facts recorded in
`_docs/migration-data/event-registration-sources.json`. It stages aggregate evidence for both
providers, keeps legacy candidates review-required, and can activate only the exact current-event
bindings supplied through a separate input file. The command never prints or persists
attendee-level fields.

### Explicit current-event input

The optional input is a small, local JSON file. Each mapping must name the exact provider event
identity accepted by the adapter and the exact canonical source identity from
`events/event_identity_manifest.json`:

```json
{
  "schema_version": 1,
  "mapping_set_revision": 1,
  "mappings": [
    {
      "provider": "luma",
      "provider_event_identity": "https://luma.com/z25lskik",
      "canonical_source": {
        "repository": "DataTalksClub/datatalksclub.github.io",
        "revision": "ee43d3fa0929faf691178d79f19528e6f15a83e5",
        "source_key": "2026-06-02-llm-zoomcamp-2026-pre-course-live-q-a"
      }
    },
    {
      "provider": "luma",
      "provider_event_identity": "https://luma.com/yqpx18b5",
      "canonical_source": {
        "repository": "DataTalksClub/datatalksclub.github.io",
        "revision": "ee43d3fa0929faf691178d79f19528e6f15a83e5",
        "source_key": "2026-06-08-llm-zoomcamp-2026-course-launch"
      }
    },
    {
      "provider": "luma",
      "provider_event_identity": "https://luma.com/a8qa5s2s",
      "canonical_source": {
        "repository": "DataTalksClub/datatalksclub.github.io",
        "revision": "ee43d3fa0929faf691178d79f19528e6f15a83e5",
        "source_key": "2026-08-24-ai-dev-tools-zoomcamp-2026-pre-course-live-q-a"
      }
    }
  ]
}
```

The input contains no titles, dates, attendee values, or counts. The adapter finds each URL in the
protected Luma snapshot, derives the approved aggregate by its provider `event_id`, and the
service attaches it to the manifest event by the exact source identity. The resulting public
counts for this snapshot are 543 for event 344 (LLM pre-course), 1,248 for event 347 (LLM launch),
and 158 for event 364 (AI Dev Tools pre-course). Declined rows remain excluded from those totals.

The 31 August AI Dev Tools launch is intentionally absent: `https://luma.com/tsiusx8s` is not in
the 29 August protected snapshot, so it must not be added to this input and event 365 must remain
without a public registration total. Do not infer a replacement from a title or date. A later
snapshot may be used only after its checksum and exact provider identity are reviewed.

Run it against a new SQLite database, then run the same command without `--fresh` to prove replay:

```bash
uv run --frozen python scripts/prepare_local_data.py \
  --database .tmp/production-prep-current.sqlite3 \
  --course-modules-input .tmp/course-modules-input-fresh.json \
  --current-registration-input .tmp/current-registration-input.json \
  --fresh

uv run --frozen python scripts/prepare_local_data.py \
  --database .tmp/production-prep-current.sqlite3 \
  --course-modules-input .tmp/course-modules-input-fresh.json \
  --current-registration-input .tmp/current-registration-input.json
```

The script refuses databases outside `.tmp/` and forces the local SQLite settings. With the input,
the three listed Luma aggregates become active on a fresh database while all other Luma/Eventbrite
candidates remain review-required. The JSON report includes only aggregate counts and activation
state; the event pages are the final check that the three counts render as `N registered`. Without
the input, the sources are still staged for review and no public registration total is activated.
It is a rehearsal tool, not a production importer; provider credentials, live registrations, and
unreviewed mapping decisions must be supplied through a separately approved operational flow.

## One command: `make production-prep-dataset`

The steps above compose into a single rebuild. It refreshes the three course checkouts,
generates the module manifest from them, runs the local orchestrator against a fresh SQLite
database, and gates the result:

```bash
make production-prep-dataset
make run-production-prep-dataset      # serves it on :8001
```

Artifacts land under `.tmp/production-prep-dataset/`: `course-sources/` (the three checkouts),
`course-modules.json` (the generated manifest) and `dataset.sqlite3` (the database). None of them
is committed. The build always starts from an empty database, so it is a rebuild rather than an
update; re-run it whenever a source moves.

Useful overrides: `PRODUCTION_PREP_DATASET_DATABASE` (build somewhere else, still under `.tmp/`),
`PRODUCTION_PREP_COURSE_REMOTE` (where the course repositories are cloned from),
`PRODUCTION_PREP_DATASET_PORT`, and `PRODUCTION_PREP_DATASET_REGISTRATION_INPUT=` (empty) to skip
activating the reviewed current-event registration aggregates.

### Prerequisites, honestly

- **The protected event exports.** `.local/migration-data/events/luma-aggregate-v1/` and
  `.local/migration-data/events/eventbrite/aggregate-v1.zip` in the main checkout, regenerated by
  `scripts/prepare_event_registration_sources.py`. Their checksums must match
  `_docs/migration-data/event-registration-sources.json`, so a refreshed export needs that file
  updated in the same change. These directories hold real registrations; they stay outside git.
- **The three course repositories.** `PRODUCTION_PREP_COURSE_REMOTE` defaults to `$(HOME)/git`
  and *not* to GitHub, because the 2026 module curricula for `llm-zoomcamp` and
  `machine-learning-zoomcamp` are not published: `github.com/DataTalksClub` carries no
  `course.yaml` for either repository, and the commits that add the module layout exist only on
  the operator's local `main`. Only `ai-dev-tools-zoomcamp` has its module layout upstream. Until
  those commits are pushed, this dataset cannot be rebuilt on a machine that does not already
  have the local clones, and neither can production ingest the curricula through the course
  repository webhook.

  The manifest builder and the preparation service both refuse a checkout whose commit is not
  reachable from a branch of its public GitHub repository, because every source-derived link the
  imported pages publish — the edit link, the raw image URL, a source path a reader follows —
  can only 404 while the commit is private. To proceed anyway the operator must state why, with
  `--allow-unpublished-commit "<reason>"` on `scripts/build_course_modules_manifest.py`; the
  reason is written into the manifest as `unpublished_commit_reason` and echoed in the
  preparation summary next to `commit_public: false`, so an unpublished import is recorded
  rather than silent. `make production-prep-dataset` passes the reason through
  `PRODUCTION_PREP_UNPUBLISHED_COMMIT_REASON`; clear that variable once the curricula are
  pushed and the guard passes on its own. Reachability is read from the checkout's own
  remote-tracking branches, so the check needs no network.
- **The protected CMP snapshot.** `PRODUCTION_PREP_CMP_SOURCE` defaults to
  `$(HOME)/git/course-management-platform/db/db.sqlite3`. `make production-prep-local` copies that
  file and runs the sanitizing importer before the catalogue seed and module preparation, so
  homework questions and project copy come from CMP rather than the placeholder seed. Learner
  tables stay empty. `make review-data` remains the standalone review-database path.

### What the gate checks

`make production-prep-dataset-verify` re-runs the checks on their own and prints an aggregate-only
JSON report: every expected 2026 cohort by slug, `curriculum_format = modules` for exactly the
three converted cohorts with their module/unit counts, one family row per real course, the absence
of the upstream `fake-course` rows, and the count of future-dated events the public site would
render. A non-zero exit means one of those is wrong; the database is still written, so the report
is a diagnosis rather than a rollback.

Two known defects fail this gate today and are owned elsewhere: the duplicate AI Dev Tools course
family (#308) and the absence of future-dated events (#307). The events count comes from the
checked public projection, not from the database, so no amount of database preparation moves it —
it moves when `content/public_projection/events.json` is rebuilt from a fresher
`DataTalksClub/datatalksclub.github.io` revision.
