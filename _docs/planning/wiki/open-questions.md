# Open questions

- [OPEN] Should Studio be read-only for GitHub-backed content in the first release, or should it create branches/pull requests? Recommended MVP: sync status plus edit-on-GitHub links, with PR authoring later.
- [OPEN] Should event registration remain accountless only, or optionally attach registrations to authenticated users later? Recommended MVP: accountless, with a nullable future user relation.
- [OPEN] Which production sender address and SES configuration should be used? Recommended development default: a verified `dtcdev.click` identity with non-production recipients or SES simulator addresses.
- [OPEN] Which event messages are in the first release beyond registration confirmation? Recommended MVP: confirmation, cancellation confirmation, event cancellation, and event reschedule; reminders and follow-ups next.
- [OPEN] What timezone should interpret legacy naive event and book timestamps? Recommended default: Europe/Berlin, while all database timestamps remain timezone-aware UTC.
- [OPEN] Does the first release need capacity and waitlists? Recommended default: no capacity limit or waitlist until the product rule is requested.
- [OPEN] Should Podwiki search move into PostgreSQL/Django or retain Zerosearch Lambda? Recommended MVP: retain the public contract but serve a Django/PostgreSQL index to reduce duplicate infrastructure, subject to relevance parity tests.
- [OPEN] Should existing SEO omissions be fixed during migration? Recommended rule: preserve working behavior first; additive sitemap/FAQ metadata improvements may ship only after parity is measured, not mixed into cutover.
- [OPEN] Should the sandbox runtime use a dedicated VPC/private subnets/NAT, or a lower-cost simplified network? Recommended design: a reusable production-shaped network with private application/database subnets, but make NAT count and runtime sizing environment variables to control sandbox cost.
- [OPEN] Should reusable/versioned course curriculum be added later? Recommended MVP: no; preserve current cohort-owned homework/projects/criteria and add a complete cohort-duplication service.
- [OPEN] Which current course-platform URLs and API consumers must remain unchanged after consolidation? Recommended rule: inventory production access logs and preserve or one-hop redirect every public/student route; keep a versioned compatibility API until consumers migrate.
- [OPEN] Should course registration create a user immediately or verify email before account creation? Recommended design: verified registration first, then attach or create the learner account during enrollment.
- [OPEN] Which existing course-platform data is canonical when the current `Course` row mixes course identity and cohort dates? Recommended migration: group edition rows into a course by stable family slug, then create one cohort per legacy row with a reviewed mapping file.
- [OPEN] Which authenticated old course API consumers must migrate before `courses.datatalks.club` becomes a redirect Lambda? Production access logs and consumer ownership are required; do not rely on cross-host redirects preserving authorization.
