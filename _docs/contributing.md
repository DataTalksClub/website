# Contributing

Start with a groomed GitHub issue and follow [`PROCESS.md`](PROCESS.md). The authoritative implementation requirements are in [`specs/`](specs/README.md).

1. Copy `.env.example` to `.env` and replace its placeholders.
2. Run `make setup`, `make migrate`, and `make test-core`.
3. Implement and verify the assigned issue without committing.
4. Put all scratch files and screenshots below `.tmp/`.
5. Hand the uncommitted work to a separate tester, then to product acceptance.
6. After approval, commit with a body containing `Closes #N`.
7. The orchestrator merges locally with `--no-ff` and pushes `main`; no pull request is opened.
8. On-call observes the resulting CI run.

Raw reports use `needs grooming`. Groomed issues use one priority (`P0`, `P1`, or `P2`), the relevant area labels, and a type label when useful. Use `human` only for a specifically named verification that automation cannot perform.

Never commit secrets, `.env`, SQLite databases, generated browser state, screenshots, or production data.

Local development and ordinary CI use gitignored SQLite databases and need no PostgreSQL service.
An ambient `DATABASE_URL` cannot switch ordinary tests away from SQLite. Deployed settings remain
fail-closed and require PostgreSQL for the bounded migration/readiness/smoke path.

## Working on the community-base dependency

`community-base` is pinned to a released `vX.Y.Z` tag of DataTalksClub/community-base in
`pyproject.toml` and `uv.lock`. `make lock-check`, CI and Deploy Dev run
`scripts/check_community_base_source.py` before dependencies are installed; a local path,
editable, branch, registry, or pyproject/lock tag mismatch fails closed.

To edit the package while working here, clone DataTalksClub/community-base next to this
checkout (as a sibling directory) and run `make core-link`. Linking requires clean
`pyproject.toml` and `uv.lock`, snapshots their exact bytes under `.tmp/core-link/`, and
installs the sibling editable. `make core-unlink` restores the pinned dependency from the
snapshot and refuses conflicting manual edits. Never commit a linked tree: the source guard
rejects it and a branch/path source would ship unreviewed package code.
