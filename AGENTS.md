# Agent notes

## Development process

- Read `_docs/PROCESS.md` before repository work and follow its issue lifecycle.
- Treat the specifications in `_docs/specs/` as the product and architecture authority.
- Product changes start from a groomed GitHub issue. Engineers implement and test without committing; a separate tester verifies the work and captures screenshots; a product manager accepts it; only then is it committed and locally merged without a pull request.
- Use `uv` or the `uv`-backed Make targets for all Python dependency, lint, type, migration, test, and run commands.
- Do not commit or push unless the current lifecycle role explicitly owns that step.
- Use the project-local `.tmp/` directory for screenshots, downloads, previews, and scratch data. Never put temporary artifacts elsewhere.
- Do not expose secrets, access tokens, registration data, or production data in logs, screenshots, issue comments, or reports.

## Repository boundaries

- Keep this project as one Django deployment with the app ownership described in `_docs/architecture/app-boundaries.md`.
- Business mutations belong in application services shared by public views, Studio, jobs, and the admin API.
- Network side effects happen after commit through durable jobs. Product-domain behavior is introduced only by its owning issue.
- Development hostname: `web.dtcdev.click`.
