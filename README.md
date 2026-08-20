# DataTalks.Club website

This repository is the deliberately small Django foundation for the unified DataTalks.Club website. The architecture and migration requirements live in [`_docs/specs/`](_docs/specs/README.md), and contribution work follows [`_docs/PROCESS.md`](_docs/PROCESS.md).

## Prerequisites

- Python 3.13 or newer (selected automatically by `uv`)
- [`uv`](https://docs.astral.sh/uv/)
- Chromium installed by the setup target for browser tests

## Bootstrap

```bash
cp .env.example .env
# Replace the local secret placeholder, then:
make setup
make migrate
make run
```

The local site is served at `http://localhost:8000`. Local development uses the gitignored `.tmp/local.sqlite3` database by default. Set `DTC_SQLITE_PATH` to use another SQLite file; relative paths resolve from the repository root. The deployed development hostname is `web.dtcdev.click` and is always marked `noindex, nofollow`.

### Local course data

A freshly migrated database has no courses, while the homepage renders its course
catalogue from the checked public projection in `content/public_projection/courses.json`.
Seed the database so `/` and `/courses` show the same real courses:

```bash
uv run python manage.py seed_local_courses          # write the catalogue
uv run python manage.py seed_local_courses --check  # validate without writing
```

The command writes the 12 real cohorts, their homework titles, and their deadlines from
`scripts/production_like_course_specs.json`, the pinned
`DataTalksClub/course-management-platform@98a2352` catalogue that the public projection is
also built from; it verifies that file's SHA-256 and refuses to run if the projection and
the catalogue would disagree. It is repeatable, creates no learners, enrollments, or
submissions, and preserves operational state you have set locally (homework/project state,
registration URLs, scoring flags). It is a development tool: it refuses to run outside a
local or test SQLite database. Generating production-like participants, submissions, and
leaderboards remains `uv run python scripts/generate_production_like_leaderboard_data.py`.

To exercise the DE Zoomcamp Project 1 submission and peer-review routes locally,
run the separate synthetic scenario seed:

```bash
uv run python manage.py seed_local_project_review
```

It creates six realistic-but-synthetic submissions under the `example` namespace,
assigns peer reviews with the existing assignment service, and moves Project 1 into
peer review. It is repeatable, reports counts and state only, and refuses to overwrite
non-synthetic submissions or run outside local/test SQLite.

Local development and ordinary CI require no PostgreSQL installation or service. Tests always use isolated SQLite and ignore an ambient `DATABASE_URL`. Deployed development and production continue to use PostgreSQL/RDS through their fail-closed settings and deployment migration/readiness/smoke path.

## Common commands

```bash
make lint
make format-check
make typecheck
make migrations-check
make deployment-check
make test-core
make test-playwright-core
make test
make worker
```

Every Python command is run through `uv`, either directly or by these Make targets. Do not use `pip` for this project. Temporary artifacts belong under `.tmp/`; `.env`, databases, screenshots, browser state, and secrets are gitignored.

## Configuration

Settings modules are:

- `website.settings.local`: local development with project-local SQLite.
- `website.settings.test`: deterministic isolated SQLite for ordinary tests and checks.
- `website.settings.development`: `web.dtcdev.click`; production-shaped and always non-indexable.
- `website.settings.production`: production security settings and fail-closed bootstrap configuration.

Deployed development and production require a non-placeholder secret, a PostgreSQL `DATABASE_URL`, allowed hosts, and trusted CSRF origins. Readiness checks the database, unapplied migrations, and these bootstrap settings without calling GitHub, AWS, email, or any other optional provider. The retained `psycopg` dependency is for these deployed processes only; application services and migrations use portable Django contracts exercised on SQLite.

See [`_docs/architecture/app-boundaries.md`](_docs/architecture/app-boundaries.md) for dependency
direction, [`_docs/architecture/database-portability.md`](_docs/architecture/database-portability.md)
for the database boundary and remaining-term inventory, and
[`_docs/contributing.md`](_docs/contributing.md) for the full contribution handoff.
