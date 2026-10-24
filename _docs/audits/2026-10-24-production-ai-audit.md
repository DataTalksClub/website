# Production AI public audit access

Website issue #469 mirrors primary CMP intake f663f70a (website intake bf2371c4).

## Observed 404 root cause

On 2026-10-24 the apex `datatalks.club` answers both `/courses/` and
`/courses/production-ai-agents-generative-media/` with an S3 404 through CloudFront.
The replacement Django host `prod.datatalks.club` answers the family and 2026
cohort with 200; its slashed family alias permanently redirects to the slashless
canonical page. The application routes exist and the replacement production
database has published course rows. The apex is still serving the legacy static
site, which has no files at these new application paths.

The active DEV host `dev.datatalks.club` answers the course list alias with
301 but the Production AI family/cohort with 404: its database has no matching
family or cohort. It currently routes to Contabo's `website-dev-web` container.
Both active Contabo environments advertise stale health source
`da74e6302e881543437654e985b78e60287d5c0a`, although their container image is
`dtc-website:4b1466c8440136a1c3f66461e8fa6ae8792a71e3`. Public release metadata
therefore does not establish the running source revision.

The configured historical `web.dtcdev.click` host does not resolve from the
worker. On-push CD uses `deploy-coolify.yml`; `deploy-dev.yml` is the retired,
manual AWS path. The current DEV backend is Contabo, not that historical AWS
target. A successful Coolify deployment does not produce the AWS DEV promotion
artifact consumed by `deploy-prod.yml`.

## Reading and participation

The family, cohort, public homework requirements, module and lesson pages remain
public, including before any assignments have been published. Approval does not
redirect a reader to the application form. An applicant sees pending review;
someone who has not applied sees an apply action. Materials remain available in
either state.

`Cohort.requires_registration_approval` mirrors CMP's per-delivery flag. Its
migration preserves the current approval campaigns' gates. Repointing, closing or
disabling a campaign does not unlock its old cohort. Enrollment and submission
mutations require approval for the exact cohort; an existing enrollment alone
does not bypass the check. Staff retain CMP's bypass. Other cohorts default to
their existing open behavior.

The Production AI ingestion script enables the flag and writes the public source
materials repository into database rows. Runtime views read those rows. The
script does not change which registration form is canonical and never creates
registrations, enrollments or messages.

## Launch switches

Keep the apex static origin unchanged until the website rollout gates in
`_docs/runbooks/production-hosting-and-dns-migration.md` pass. Before launch:

1. Prove this release and the public anonymous/pending/approved matrix on the
   active DEV target; run the reviewed Production AI campaign ingestion there if
   its course rows are absent, then verify all family/cohort/material links.
2. Promote the exact DEV-proven image through the supported target-specific
   release path. Do not supply a Coolify run as an AWS DEV release artifact.
3. Refresh database-owned course and material content through the maintained
   import/sync boundary; check visibility, identifiers and material destinations.
4. At the separately authorized apex cutover, change the origin/DNS routing from
   the legacy static site to the verified Django site, with its apex host settings,
   canonical policy and compatibility routes. Then verify the new `/courses`
   paths on the apex. No code-only patch can add a Django route to the static
   origin currently answering the apex.

This issue does not activate that apex cutover or alter registration ownership.
Exact validation, source SHAs and deploy results belong in the delivery report.
