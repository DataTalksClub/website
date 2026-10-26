# Coolify website worker

Use the Docker Compose build pack with `/docker-compose.coolify.yaml`. Enable
Coolify's source-commit build variable so both services build and use the same
revision-tagged image. Coolify supplies `.env` to both services. Assign existing
website domains to `web` on port 8000 only. The readiness path is
`/health/ready`, without a trailing slash.

Compose starts web with `entrypoint.sh web-migrate`: it runs the new image's
`manage.py db_preflight` (migration role + catalog ownership, fail closed),
then `manage.py migrate --noinput`, `sync_relay_schedules` and
`import_mail_templates` before Gunicorn — exit codes 20/21/22/23. A failure
in any of them exits before the HTTP server starts. Keep the worker's `service_healthy` dependency: it
prevents jobs from executing against an unapplied schema. The ordinary `web`
entrypoint stays available for controllers that run a separate migration task.
Run only one web service with this startup migration owner; scaling web requires
a separate serialized release task first. Review schema and data migrations
before deploying, including their compatibility with the previous worker.

A worker left in `Created` can be an intentional Compose dependency wait.
Inspect web readiness and deployment logs before treating it as a Docker start
failure. In the 2026-10-25 incident, missing courses migrations 0029/0030 made
web unhealthy; Compose exited before starting worker and the generic watchdog
started it after 181–215 seconds. The three API deployments used distinct source
commits; they were unrelated to the Berlin clock change at 01:00 UTC. The
watchdog is a recovery backstop, not evidence of successful deployment.

The worker service selects `website.settings.worker`, which inherits production
database, security and Relay email settings but runs domain jobs through the
existing synchronous lease-fenced runner inside this dedicated process.
Gunicorn must keep production settings. The shared entrypoint preserves the
caller's settings, including development deployments.

`entrypoint.sh worker` executes `jobs_worker`, bootstrapping Django once for the
life of the container. The command calls `jobs_run_due` and `jobs_sweep` with
`limit=100` and `skip_checks=True`, then pauses ten seconds. It closes obsolete
database connections each tick and continues after a command failure, reporting
the command and exception class without job data. Django checks remain in
deployment validation. Skipping checks alone removed W005 spam but left the
repeated application bootstrap burning CPU; keeping the process alive removes
that recurring cost. Web settings and healthchecks are unchanged.
The singleton worker has no public port or
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
