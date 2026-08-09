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
