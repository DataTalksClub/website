# Agent notes

Only what an agent cannot get from the code. Everything else lives with the
thing it describes — if a rule can be a test or a spec, it belongs there, not
here.

- `_docs/PROCESS.md` owns the issue lifecycle and who may commit. `_docs/specs/`
  is the product and architecture authority.
- Use `uv`, or the `uv`-backed Make targets, for every Python command.
- Screenshots, downloads, previews and scratch data go in the project-local
  `.tmp/`.
- Keep secrets, tokens, registration data and production data out of logs,
  screenshots, issues and reports. In a log, identify a person by user id, never
  by email address.

Where the rest went: app and service boundaries are in
`_docs/architecture/app-boundaries.md`; the page shell is in
`_docs/design/design-5a.md` and enforced by
`courses/tests/test_content_page_shell.py`; the development host is in
`deploy/development_target.py`.
