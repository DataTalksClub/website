# Coolify website worker

Live production currently runs the `website-production-rolling-web` Dockerfile
application and has no worker resource. This Compose stack is used for staging
and other Compose deployments; it is not evidence that production jobs are
running. See [`coolify-production-deployments.md`](coolify-production-deployments.md)
for the live topology and production worker activation gate.

Use the Docker Compose build pack with `/docker-compose.coolify.yaml`. Enable
Coolify's source-commit build variable so both services build and use the same
revision-tagged image. Coolify supplies `.env` to both services. Assign existing
website domains to `web` on port 8000 only. The readiness path is
`/health/ready`, without a trailing slash.

Compose starts web with `entrypoint.sh web-migrate`: it runs the new image's
`manage.py web_bootstrap --noinput`, which performs Django checks, runs the
database-role and catalog-ownership preflight, and applies migrations in one
process. Migrations receive a 120-second PostgreSQL statement timeout while
ordinary app connections retain their configured 30-second timeout. Preflight
and migration failures remain fatal (exit code 21); Relay schedule and
mail-template synchronization remain best effort and report failures to
stderr. Retry the named management command after Relay recovers. Keep the worker's `service_healthy` dependency: it
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
database connections and updates `/tmp/jobs-worker.heartbeat` each tick. The
stdlib-only healthcheck marks the container unhealthy when that heartbeat is
missing or older than 90 seconds, including when a handler wedges the loop.
Command failures include redacted traceback locations in structured logs and
the loop continues to the next poll. Django checks remain in deployment
validation. Web checks call the venv interpreter directly every 30 seconds;
they do not start `uv` or Django for the probe. The singleton worker has no
public port or router, a 240 MiB memory ceiling, and a 1 CPU limit. Both
services use the existing external
`coolify` network and the website database on pg-shared through its private
bridge; do not create a separate worker database.

This implements the per-application execution part of Option C for Compose
deployments. Relay remains
the mail transport. Production web's existing generic Relay wake-up and signed
ingress are migration residue; retiring that integration is separate work.
Do not enable generic Relay callbacks as part of activating the local worker.
The current package can execute work in the dedicated worker, but moving web
submission to a dispatch-only backend requires an upstream `community-base`
change and tests in both consuming projects.

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
