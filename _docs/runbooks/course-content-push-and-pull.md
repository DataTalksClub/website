# Course content: push and pull, one implementation

Course repositories reach this site two ways.  Both call the same ingestion
service, so the two cannot drift into disagreeing about what a course repository
may contain.

| | Push | Pull |
| --- | --- | --- |
| Triggered by | CI/CD — a course repository's `Notify course platform` workflow | a developer running a command |
| Transport | signed GitHub push event → `POST /api/webhooks/github` → durable job → `https://codeload.github.com/.../tar.gz/<commit>` | a checkout already on disk |
| Network | yes, inside the job, after commit | none |
| Entry point | `api/views/course_repository_webhooks.py` → `content_sync/course_repository_sync.py` | `manage.py pull_course_repositories` |

Both entry points are thin.  Everything that decides what is admitted, how it is
parsed, and how it is projected lives in one place:

    content_sync/course_repository_ingest.py

`ingest_course_repository` branches exactly once, on whether a checkout root was
supplied.  After that there is a single code path: the same
`CourseRepositoryLimits`, the same `parse_course_repository`, the same
`import_course_repository_curriculum`.
`content_sync/tests/test_course_repository_transport_parity.py` runs one
repository through both transports and asserts the snapshot and the projected
rows are identical.

## Which repositories exist

Registered `ContentSource` rows, not a list in code or in the Makefile.  Add one:

```sh
uv run python manage.py register_course_repository \
    --stable-id data-engineering-zoomcamp \
    --display-name "Data Engineering Zoomcamp" \
    --owner DataTalksClub \
    --repository data-engineering-zoomcamp \
    --enabled
```

A repository without a `course.yaml` at its root cannot be ingested by either
route; that is a change to make in the course repository, not here.

## Pulling locally

```sh
make content-pull-plan      # what is registered, and where each checkout is read from
make content-checkouts      # clone or refresh a checkout per registered source (the only network step)
make content-pull           # ingest every registered source, offline
```

Point the pull at checkouts you already have:

```sh
make content-pull CONTENT_CHECKOUT_ROOT=$HOME/git
uv run python manage.py pull_course_repositories \
    --checkout llm-zoomcamp=$HOME/git/llm-zoomcamp
```

Narrow it with `--stable-id`, repeatable.

The pull refuses to write anything but a local or test SQLite database, and
refuses a checkout that is dirty or on the wrong branch, because the commit it
records would then not describe the bytes it imported.  For an edit-preview loop
pass `--allow-modified-checkout`; the waiver is printed with the commit that is
still being recorded.

## Reading a refusal

Both routes raise the same codes.  The code is what the durable job records; the
message that follows it names the file and the numbers, for example:

```
REFUSED [mlops-zoomcamp]: course_repository_file_too_large:
  05-monitoring/baseline_model_nyc_taxi_data.ipynb is 10761396 bytes,
  over the 8000000-byte per-file limit.
```

The push route logs the same detail under `course_repository_ingest_refused`
before the job fails, because `DurableJob.last_error_code` is a bounded
identifier and cannot carry it.

## Known follow-ups

* `make production-prep-dataset` still uses the separate reviewed-snapshot
  builder in `courses/services/local_course_modules.py`, which carries its own
  three-cohort allowlist and imports only one reviewed cohort per repository.
  It shares the parser and the importer with the routes above but not the
  ingestion service; retiring it onto `pull_course_repositories` is its own
  change.
* `mlops-zoomcamp` and `stock-markets-analytics-zoomcamp` have a `SITE.md` but
  no `course.yaml`, so neither route can ingest them yet.
