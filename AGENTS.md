# Agent notes

Only what an agent cannot get from the code. Everything else lives with the
thing it describes — if a rule can be a test or a spec, it belongs there, not
here.

- `_docs/PROCESS.md` owns the issue lifecycle and who may commit. `_docs/specs/`
  is the product and architecture authority.
- Use `uv`, or the `uv`-backed Make targets, for every Python command.
- Screenshots, downloads, previews and scratch data go in the project-local
  `.tmp/`.
- For illustration generation, use the `imagegen` skill and its built-in tool.
  Never ask this user to configure `OPENAI_API_KEY` for image generation, or
  inspect credentials to select a model. If the built-in model version is not
  exposed, record it as unverified and continue. The full workflow and quality
  checks live in `.agents/skills/website-illustrations/SKILL.md`.
- Keep secrets, tokens, registration data and production data out of logs,
  screenshots, issues and reports. In a log, identify a person by user id, never
  by email address.
- Logic lives in scripts — a production-injection script pushes data from a
  legacy/upstream source into production and holds none of it itself. Data
  lives in the database (what a public request reads) or in `~/prod`, outside
  the repo — raw ingestion input and its staged/prepped-for-ingestion form
  alike. Never a checked-in or staged file inside the repo in between.
  Public website content in particular is database-owned: runtime code must
  never read it from hardcoded Python values, checked-in JSON, or a
  file-backed projection/fallback. A live sync straight from a source
  checkout into the database (e.g. the `community_base.content_sync` engine
  and its `SyncedDocument` rows) is the database-owned pattern and is fine —
  the violation is a build step that writes a checked-in or staged
  *projection* (an intermediate JSON tree something else reads later), not a
  synced row. See `_docs/architecture/database-only-content.md` for the
  current violation inventory and removal plan.
- `community-base` (`~/git/community-base`) is a shared package consumed by
  this site and by AI Shipping Labs (`~/git/ai-shipping-labs`). If you change
  anything in `community-base`, run the test suite in both consuming projects,
  not just this one, before considering the change done.
- Never use `git stash` when other agents may be working in this repo
  concurrently: `refs/stash` is one shared stack across every worktree of the
  same repository, not per-worktree, so a `stash pop` can pull in a different
  agent's uncommitted changes. Use a throwaway `git worktree` for a clean
  baseline comparison instead.

Where the rest went: app and service boundaries are in
`_docs/architecture/app-boundaries.md`; the page shell is in
`_docs/design/design-system.md` and enforced by
`courses/tests/test_content_page_shell.py`; the development host is in
`deploy/development_target.py`; the prod ingest pipeline — every source, its
run order, and what `scripts/production_data.py` does — is in
`_docs/runbooks/data-ingest.md`.
