# Transactional send policy

The approved policy for CMP and Website is:

| Application | Development | Production |
| --- | --- | --- |
| CMP | `RELAY_TRANSACTIONAL_DRY_RUN=1` | `RELAY_TRANSACTIONAL_DRY_RUN=0` |
| Website | `RELAY_TRANSACTIONAL_DRY_RUN=1` | `RELAY_TRANSACTIONAL_DRY_RUN=0` |

Controlled delivery checks use only the recipient explicitly approved in the
owning intake. Never use real learners as test recipients or replay historical
jobs as a delivery check. The private acceptance report holds message and Relay
delivery-event evidence; acceptance requires a `delivered` event, not just API
acceptance.

Website resolves `relay.transactional_dry_run` from its operational configuration.
A database override wins over the process environment. Inspect the effective value
and its source in each running web and worker process after configuration changes.
Coolify environment settings are independent of the ECS deployment target's
`safety_environment`; save the flag explicitly on each Coolify application.

The Website preference resolver suppresses new package mail requests, recording
`SUPPRESSED` with reason `transactional_dry_run`. It does not recheck already queued
deliveries. Inspect pending, submitted, running, and retryable mail intents before
activation. The retired `DATAMAILER_TRANSACTIONAL_DRY_RUN` flag does not guard the
package backend. An old image without the resolver guard cannot establish dry-run
protection merely by setting the new flag; absent Relay credentials prevent
transport but do not constitute dry-run support.

Production uses database `website` as `website_app`; development uses
`website_dev` as `website_dev`. Preserve these separate database URLs and send
policies during promotion. Before correcting a database connection, verify the
exact image's migration plan, role ownership and privileges, effective settings,
and runnable mail-job counts. Retain the prior connection privately for rollback.
Normal `web-migrate` startup also synchronizes schedules and mirrors templates.

Website production reads media using the dedicated sandbox IAM user
`dtc-website-prod-media-reader`. Its inline policy permits `s3:GetObject` and
`s3:PutObject` on `arn:aws:s3:::dtc-website-media/images/*` (the content sync
uploads media objects) and an unconditioned `s3:ListBucket` on the bucket, with
no groups or attached policies (aws-infra PR #165). The access key is stored in
the production application's encrypted Coolify environment, never in this
repository or reports. The S3 prefix is empty. Verify reads using the
application's checksum-enabled GET and public `/images/` URLs. An object absent
from S3 returns `404` (`object-missing`). See `public-media-objects.md`.
