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
