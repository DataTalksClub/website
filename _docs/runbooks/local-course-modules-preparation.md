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
