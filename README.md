# DataTalks.Club website

This repository is the deliberately small Django foundation for the unified DataTalks.Club website. The architecture and migration requirements live in [`_docs/specs/`](_docs/specs/README.md), and contribution work follows [`_docs/PROCESS.md`](_docs/PROCESS.md).

## Prerequisites

- Python 3.13 or newer (selected automatically by `uv`)
- [`uv`](https://docs.astral.sh/uv/)
- PostgreSQL 16+ for normal development
- Chromium installed by the setup target for browser tests

## Bootstrap

```bash
cp .env.example .env
# Replace every placeholder in .env, create the PostgreSQL database, then:
make setup
make migrate
make run
```

The local site is served at `http://localhost:8000`. The deployed development hostname is `web.dtcdev.click` and is always marked `noindex, nofollow`.

SQLite is an explicit local/testing escape hatch only. For a narrow check without PostgreSQL, set `DTC_USE_SQLITE=1`; never use this mode for concurrency, locking, migration compatibility, or deployed environments. Tests use isolated SQLite unless `DATABASE_URL` is supplied, as it is in CI.

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

- `website.settings.local`: local development; PostgreSQL by default, explicit SQLite escape hatch.
- `website.settings.test`: isolated tests; SQLite unless CI supplies PostgreSQL.
- `website.settings.development`: `web.dtcdev.click`; production-shaped and always non-indexable.
- `website.settings.production`: production security settings and fail-closed bootstrap configuration.

Development and production require a non-placeholder secret, `DATABASE_URL`, allowed hosts, and trusted CSRF origins. Readiness checks the database, unapplied migrations, and these bootstrap settings without calling GitHub, AWS, email, or any other optional provider.

See [`_docs/architecture/app-boundaries.md`](_docs/architecture/app-boundaries.md) for dependency direction and [`_docs/contributing.md`](_docs/contributing.md) for the full contribution handoff.
