# Publish homework solutions

Homework questions stay in public course repositories. Author solutions in the
private `DataTalksClub/zoomcamp-solutions` repository, at
`<course>/<year>/<homework>/solution.md` with `answers.yaml` alongside it. The course directory name is its website slug. The homework directory can
be descriptive: `04-devops` maps to `homework_slug: hw04`. `cohort` identifies
the exact website delivery; the directory year must also match it.

The version-1 manifest has exactly these fields (synthetic example):

```yaml
schema_version: 1
cohort: '2026'
homework_slug: hw04
published: false
publish_at: null
answers:
  - question_id: reflection
    answer: Synthetic reference text
```

`published: true` **or** a reached `publish_at` timestamp releases the solution.
For a timed release use a quoted timestamp with a timezone, for example
`publish_at: '2026-12-01T17:00:00Z'`. To hide an imported solution again, set
`published: false` **and** `publish_at: null`, then sync. Scoring, homework creation,
submission state, and deadline do not change this gate. The question IDs are
reference labels; this importer never updates grading answers or student data.

## Import

Use a fine-grained read-only GitHub token with Contents: read and Metadata: read
on **only** `DataTalksClub/zoomcamp-solutions`, supplied through the secret
`ZOOMCAMP_SOLUTIONS_TOKEN` in the operator's environment. Do not put credentials
in arguments, manifests, images, public CI artifacts, or logs. The API mode
checks that the repository is private and fetches all files from one resolved
commit, directly into memory. There is no private source checkout in the image.

For local rehearsal, migrate and populate a project-local scratch database with
the required course/cohort/homework rows first, then run:

```bash
uv run --frozen python scripts/prod/sync_homework_solutions.py \
  --database .tmp/review.sqlite3
```

An existing private checkout outside this repository (for example
`~/prod/zoomcamp-solutions`) can replace the API credential:

```bash
uv run --frozen python scripts/prod/sync_homework_solutions.py \
  --database .tmp/review.sqlite3 \
  --checkout ~/prod/zoomcamp-solutions --ref main
```

The checkout mode reads immutable committed Git blobs, never working files or
symlink targets. Fetch the private checkout before syncing new commits. The API
mode defaults to `main`; either mode also accepts `--ref <commit>` for replay.

For a deployed target, use the same operator environment and release-bound
credentials as [production data migration](production-data-migration.md), with
the explicit target authorization required by `scripts.prod.target`:

```bash
uv run --frozen python scripts/prod/sync_homework_solutions.py \
  --deployment-target website-development \
  --allow-production-write website-development
```

For production repeat both target arguments with `website-production`. This is
a content operation after the schema release, separate from the normal image
deployment. It requires the deployed database credentials and sealed release
identity described in that runbook. No public repository push is needed to
publish, reschedule, revoke, or update an existing solution. Sync again after a
private push; a date already imported is evaluated at request time.

## Failure and visibility

The entire snapshot imports in one database transaction. Unknown/missing targets,
duplicate manifest keys, invalid dates/paths, missing/nonregular files, oversized
inputs and provider failures reject the operation; the prior database snapshot
remains intact. A successful complete snapshot removes solutions whose manifests
were removed. An empty snapshot is refused: revoke the last solution with its
publication fields rather than deleting the only manifest.

Before release the direct public route returns 404 and the homework has no
solution link. After release, anonymous and authenticated readers see **View
solution and answers** on the homework page, including the stepper. The route is
`/courses/<course>/<cohort>/homework/<homework>/solution`. It renders sanitized
Markdown and escaped structured answers, has no private-repository edit link,
and sends `no-store` and `noindex`. Public reads use database rows only; loss of
GitHub access does not trigger a fallback or expose an unpublished row.

Verify with synthetic content in development: import hidden, request both the
homework and direct route, publish in the private source and sync, check both
again on desktop/mobile, then revoke and sync. Do not publish real unreleased
answers merely to test the release mechanism.
