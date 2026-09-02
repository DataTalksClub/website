# Agent notes

## Development process

- Read `_docs/PROCESS.md` before repository work and follow its issue lifecycle.
- Treat the specifications in `_docs/specs/` as the product and architecture authority.
- Product changes start from a groomed GitHub issue. Engineers implement and test without committing; a separate tester verifies the work and captures screenshots; a product manager accepts it; only then is it committed and locally merged without a pull request.
- Keep independent delivery lanes moving while another role owns a wait. Use completion-driven,
  longest-supported event waits; do not wake on a timer or shadow-poll agents, CI, or deployments.
  Resume on completion, failure, a concrete blocker, user direction, or an actionable wait failure,
  and use free capacity on unblocked work.
- Use `uv` or the `uv`-backed Make targets for all Python dependency, lint, type, migration, test, and run commands.
- Do not commit or push unless the current lifecycle role explicitly owns that step.
- Use the project-local `.tmp/` directory for screenshots, downloads, previews, and scratch data. Never put temporary artifacts elsewhere.
- Do not expose secrets, access tokens, registration data, or production data in logs, screenshots, issue comments, or reports.

## Repository boundaries

- Keep this project as one Django deployment with the app ownership described in `_docs/architecture/app-boundaries.md`.
- Business mutations belong in application services shared by public views, Studio, jobs, and the admin API.
- Network side effects happen after commit through durable jobs. Product-domain behavior is introduced only by its owning issue.
- Development hostname: none is deployed right now. The `web.dtcdev.click` stack was
  decommissioned on 2026-09-02 and its replacement `dev.datatalks.club` is not built yet, so
  automatic deployment is off. `deploy/development_target.py` holds the reviewed hostnames and
  selects one from `DTC_DEVELOPMENT_HOSTNAME`; do not hardcode a development host elsewhere.

## Design-system page shells

- Ordinary public course/platform pages extend `templates/core/content_page.html`.
  That parent owns the cream/yellow header surface, lavender content surface, and
  normal `.content-shell` width; child templates provide only page-specific
  metadata, styles, header, content, and scripts.
- Do not recreate the document shell, masthead/footer, or normal width in an
  ordinary child template. Use `.shell-breakout` for a genuinely wide table or
  diagram, with a page-specific reason.
- The only top-level layout exceptions are the homepage (`core/home.html`) and
  authentication pages. Auth pages may use their own width/layout while keeping
  the lavender content surface.
