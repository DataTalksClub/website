# Course content: one path, two transports

There is exactly one route into the curriculum tables. Course repositories reach
it two ways, and the transport is the only difference between them, so the two
cannot drift into disagreeing about what a course repository may contain.

| | Push | Pull |
| --- | --- | --- |
| Triggered by | CI/CD — a course repository's `Notify course platform` workflow | a developer running a command |
| Transport | signed GitHub push event → `POST /api/webhooks/github` → durable job → `https://codeload.github.com/.../tar.gz/<commit>` | `git archive HEAD` in a checkout on disk |
| Network | yes, inside the job, after commit | none |
| Entry point | `api/views/course_repository_webhooks.py` → `content_sync/course_repository_sync.py` | `manage.py pull_course_repositories` |

Both entry points are thin. Everything that decides what is admitted, how it is
parsed, and how it is projected lives in one place:

    content_sync/course_repository_ingest.py

`ingest_course_repository` branches exactly once, on whether a checkout root was
supplied. After that there is a single code path: the same
`CourseRepositoryLimits`, the same `parse_course_repository`, the same
`import_course_repository_curriculum`.
`content_sync/tests/test_course_repository_transport_parity.py` runs one
repository through both transports and asserts the snapshot and the projected
rows are identical.

Legacy and modules cohorts are two formats that one path handles, not two paths.
A repository's cohorts are parsed together and projected in one import, whichever
transport carried them.

## Why the pull runs `git archive`

Both transports read a `git archive` tar. codeload serves one; the pull produces
the same thing locally. That is deliberate rather than incidental.

A repository's `.gitattributes` can carry `export-ignore` and `export-subst`.
`git archive` honours them; a working-tree walk or a `git ls-files` listing does
not. A pull that read the checkout therefore imported files the push route would
never see. No course repository carries a `.gitattributes` today, which is
exactly what made the divergence survivable, so the fixture in
`content_sync/tests/fixtures/course_repository/export_ignore_overlay/` carries one
and the parity tests run against it.

`git archive` on a local tree-ish makes no network call and names no remote, and
the pull runs it with the operator's global and system git configuration
disabled so the tar depends on the repository and the commit rather than on the
machine.

Because the snapshot is the commit's exported tree, uncommitted edits are never
imported. `--allow-modified-checkout` lets a dirty or off-branch checkout
proceed, but it imports HEAD and says so on stderr; commit an edit to see it.

## Which repositories exist

Registered `ContentSource` rows. Not a list in code, not a list in the Makefile.

A fresh database has no rows, so it gets them from the pinned registration input
at `content_sync/course_repository_sources.json`:

```sh
make content-sources        # idempotent; leaves an already-registered source alone
```

That file is a registration input, not a second source of truth — the same
relationship `scripts/production_like_course_specs.json` has with the seeded
course catalogue. Register something outside it directly:

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
make content-sources        # register the pinned sources into this database
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

Naming one checkout names the run: `--checkout X=PATH` on its own limits the run
to `X`. With `--from-disk`, where there are many sources, it stays an override of
one of them. `--stable-id` narrows explicitly and is repeatable.

`--verbosity 0` prints only the JSON summary line.

The pull refuses to write anything but a local or test SQLite database, and
refuses a checkout that is dirty or on the wrong branch, because the commit it
records would then not describe the checkout an operator is looking at.

`--require-public-commit` additionally refuses a HEAD that is not on a branch of
the public GitHub repository. Imported pages link back to the commit they came
from, so a commit only one machine has publishes source links, images and edit
affordances that can only 404. Reachability is read from the checkout's own
remote-tracking branches, so the check stays offline.
`make production-prep-dataset` uses it; ad-hoc pulls do not, because a scratch
fixture checkout has no remote at all.

## Reading a refusal

Both routes raise the same codes. The code is what the durable job records; the
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

* **A CMP-owned homework that already carries the repository's slug is refused**,
  with `course_repository_homework_slug_collision`. The retired local importer
  adopted such a row through `preserve_existing_records=True`; one path means one
  adoption rule, and this is it, for the local dataset and for production alike.
  Reconciling a CMP row with a repository row is `courses/services/cmp_content_import.py`'s
  job — it already pairs them by slug, then by exact title — so the fix is to make
  the local CMP import bind the same way rather than to give the course path a
  second mode. Nothing hits this today (`llm-zoomcamp` declares `homework-0N`
  against CMP's `hwN`), but `ml-zoomcamp` declares `hw01..hw10`, which is exactly
  what CMP calls them.
* `mlops-zoomcamp` and `stock-markets-analytics-zoomcamp` have a `SITE.md` but
  no `course.yaml`, so neither route can ingest them yet, and neither is in the
  registration input.
* `data-engineering-zoomcamp` has gained a `course.yaml` upstream but is not
  registered: adding it changes which cohorts the local dataset carries, and
  `scripts/verify_local_dataset.py` pins that shape. Registering it is a
  product decision with its own change.
