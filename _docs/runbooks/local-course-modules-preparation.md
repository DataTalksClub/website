# Local production-prep dataset

This runbook builds the local rehearsal dataset: the real course curricula, the pinned course
catalogue, the sanitized CMP content, and the reviewed event registration aggregates, in one
SQLite database under `.tmp/`. It is a bounded local/test workflow; it is not a production
importer and must not be run against a production database.

## Course content comes in through the one course path

There is no separate local importer any more. The dataset pulls course repositories through
`manage.py pull_course_repositories`, which is the same
`content_sync.course_repository_ingest.ingest_course_repository` the signed GitHub push webhook
drives. `_docs/runbooks/course-content-push-and-pull.md` describes it; the only difference here
is the transport and two options:

- `--from-disk`, pointing at the checkouts `make content-checkouts` produced; and
- `--require-public-commit`, which refuses a HEAD that is not on a branch of the public GitHub
  repository, because every source-derived link the imported pages publish -- the edit link, the
  raw image URL, a source path a reader follows -- can only 404 while the commit is private.
  Reachability is read from the checkout's own remote-tracking branches, so the check needs no
  network.

Which repositories exist is registered `ContentSource` data. `make content-sources` writes the
pinned registration input at `content_sync/course_repository_sources.json` into the database it
is pointed at, and is idempotent.

Every cohort a repository publishes is imported, legacy and modules alike; the parser already
models the format dimension and neither route filters cohorts.

## Combined local rehearsal

The local-only orchestrator composes migrations, the reviewed event identity manifest, the
baseline course catalog, and the course-repository pull. It also validates the prepared
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
  --course-checkout-root .tmp/production-prep-dataset/course-sources \
  --current-registration-input .tmp/current-registration-input.json \
  --fresh

uv run --frozen python scripts/prepare_local_data.py \
  --database .tmp/production-prep-current.sqlite3 \
  --course-checkout-root .tmp/production-prep-dataset/course-sources \
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

The steps above compose into a single rebuild, in three stages:

1. `production-prep-course-registry` creates the dataset database and registers the course
   repositories. This has to come first, because registered `ContentSource` rows are the only
   place the answer to "which repositories exist" lives.
2. `production-prep-course-sources` clones or refreshes one checkout per registered source. It is
   `make content-checkouts` pointed at the dataset root, and it is the only step that touches the
   network.
3. `production-prep-local` builds the database offline from those checkouts and gates the result.

```bash
make production-prep-dataset
make run-production-prep-dataset      # serves it on :8001
```

Artifacts land under `.tmp/production-prep-dataset/`: `course-sources/` (one checkout per
registered source) and `dataset.sqlite3` (the database). Neither is committed. The build always
starts from an empty database -- stage 1 refuses to run against one that already exists -- so it
is a rebuild rather than an update; re-run it whenever a source moves.

Useful overrides: `PRODUCTION_PREP_DATASET_DATABASE` (build somewhere else, still under `.tmp/`),
`CONTENT_GIT_HOST` (where the repositories are cloned from; the owner comes from the registered
source), `PRODUCTION_PREP_DATASET_PORT`, and `PRODUCTION_PREP_DATASET_REGISTRATION_INPUT=`
(empty) to skip activating the reviewed current-event registration aggregates.

### Prerequisites, honestly

- **The protected event exports.** `.local/migration-data/events/luma-aggregate-v1/` and
  `.local/migration-data/events/eventbrite/aggregate-v1.zip` in the main checkout, regenerated by
  `scripts/prepare_event_registration_sources.py`. Their checksums must match
  `_docs/migration-data/event-registration-sources.json`, so a refreshed export needs that file
  updated in the same change. These directories hold real registrations; they stay outside git.
- **The registered course repositories.** Stage 2 clones them from `CONTENT_GIT_HOST`
  (`https://github.com` by default) using the owner and repository each registered source names,
  so no repository identity is written in the Makefile. The 2026 module curricula are published
  upstream now, so a clean clone satisfies `--require-public-commit` on its own; a checkout that
  has run ahead of its remote will be refused by name until it is pushed.
- **The protected CMP snapshot.** `PRODUCTION_PREP_CMP_SOURCE` defaults to
  `$(HOME)/git/course-management-platform/db/db.sqlite3`. `make production-prep-local` copies that
  file and runs the sanitizing importer before the catalogue seed and the course pull, so
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
