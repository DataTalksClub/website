# DataTalks.Club Django Website

## Problem

The current DataTalks.Club website is a static GitHub Pages site. Recreate it as a Django application so the existing public site can be preserved while adding server-side community functionality.

## Desired behavior

- A Django implementation of the main DataTalks.Club website.
- Public sections include the main website, docs, FAQ, podcast/blog content, and Podwiki.
- Visitors can register for events and receive transactional email associated with registration.
- Staff can manage operational and site content through a custom admin interface called Studio.
- Administrative actions are also available through an admin-only API.
- The implementation follows useful patterns from the AI Shipping Labs website without inheriting unrelated product complexity.
- Python dependencies and commands use `uv`.

## Acceptance criteria

- [ ] Produce implementation-ready specs rather than application code in this planning phase.
- [ ] Preserve current public URLs and SEO behavior wherever practical, with an explicit redirect plan for exceptions.
- [ ] Define which content stays in GitHub and how Django reads, caches, renders, and refreshes it.
- [ ] Blog, podcast, FAQ, docs, and Podwiki source content remains in GitHub repositories.
- [ ] Define the event, registration, attendee, consent, and email-delivery data models and workflows.
- [ ] Define Studio roles, permissions, content workflows, operational screens, and audit requirements.
- [ ] Define an authenticated admin-only API for all Studio actions, including API conventions and security controls.
- [ ] Define deployment, background jobs, observability, testing, migration, and rollout.
- [ ] Split delivery into independently verifiable milestones.

## Constraints

- Target repository: `/home/alexey/git/dtc-website`.
- Existing site source: `/home/alexey/git/datatalksclub.github.io`.
- Reference implementation: `/home/alexey/git/ai-shipping-labs`.
- Additional GitHub content repositories include `/home/alexey/git/datatalks.club-docs`, `/home/alexey/git/faq`, and `/home/alexey/git/podwiki`.
- Existing GitHub-hosted content remains the source of truth for blog, podcast, FAQ, docs, and Podwiki.
- Email sending must be asynchronous and safe against duplicate sends.
- Public event registration should not require a user account in the first release unless later decided otherwise.
- Planning must not overwrite unrelated local changes in source/reference repositories.
- Use `uv` for dependency management and Python command execution.

## Current understanding

Suspected components include the Django foundation and deployment configuration; public templates, navigation and SEO; Git repository content synchronization and Markdown/frontmatter rendering; search; events and registrations; transactional email; Studio; an admin-only API; background jobs; auditing; monitoring; backups; and migration tooling.

Open product questions include whether Studio writes GitHub content through pull requests or only inspects/syncs it; email and hosting providers; first-release event capabilities; accountless versus account-linked registration; desired domains for docs/FAQ/Podwiki; and the exact URL-compatibility target.

## Comparison sources

- The current DataTalks.Club website source and content repositories.
- The local AI Shipping Labs Django implementation, especially GitHub sync, Studio, API, events, email, background jobs, and deployment patterns.
- Official Django and provider documentation where details are version-sensitive.

## Non-goals

- Moving blog, podcast, FAQ, docs, or Podwiki authoring into the Django database.
- Reproducing AI Shipping Labs payments, memberships, courses, CRM, or unrelated features.
- Building native mobile applications.
- Implementing the Django application before the specs are reviewed and approved.
