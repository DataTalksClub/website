# Coolify website worker

Use the Docker Compose build pack with `/docker-compose.coolify.yaml`. Enable
Coolify's source-commit build variable so both services build and use the same
revision-tagged image. Coolify supplies `.env` to both services. Assign existing
website domains to `web` on port 8000 only. The readiness path is
`/health/ready`, without a trailing slash.

The worker service selects `website.settings.worker`, which inherits production
database, security and Relay email settings but runs domain jobs through the
existing synchronous lease-fenced runner inside this dedicated process.
Gunicorn must keep production settings. The shared entrypoint preserves the
caller's settings, including development deployments.

`entrypoint.sh worker` runs `jobs_run_due --limit 100` and `jobs_sweep --limit
100`, then pauses ten seconds. The singleton worker has no public port or
router and a 240 MiB memory ceiling. Both services use the existing external
`coolify` network and the website database on pg-shared through its private
bridge; do not create a separate worker database.

This implements the per-application execution part of Option C. Relay remains
the mail transport. Production web's existing generic Relay wake-up and signed
ingress are migration residue; retiring that integration is separate work.
Do not enable generic Relay callbacks as part of activating the local worker.
No shared-package source change or new queue service is required.

Before draining, review aggregate handler/status counts and confirm handlers
are safe to replay. Let `jobs_run_due` claim and execute pending jobs normally;
never bulk-update statuses to succeeded. Check the same cohort's identities,
handler outcomes, attempts and lease release afterward. Q&A provisioning should
preserve its native sessions' lifecycle state; content invalidation currently
validates an intent without a network request.

Verify both command results, job progress, and a 30-minute soak: sample worker
memory below 250 MiB, confirm unchanged container identity/start time, zero
restart increase and no OOM, and check public homepage and readiness. A running
shell loop alone is insufficient because the entrypoint continues after
command errors. It has no HTTP listener, so do not attach the web healthcheck
to the worker.

Retain the previous image and application configuration on the host before
deployment. For rollback, stop the worker before restoring the previous web
release; preserve durable intent rows for reconciliation. No DNS/CDN changes
or secret deletion is needed.
