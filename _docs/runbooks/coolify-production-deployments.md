# Live website production on Coolify

Status checked 2026-11-10. This runbook describes the live Contabo production
application. The AWS release workflows elsewhere in this repository do not
deploy this Coolify application.

## Current owner and release path

- Production is the `website-production-rolling-web` Coolify Dockerfile
  application on contabo-a. It serves the production website domains from one
  rolling web container.
- The health-gated rolling follower in `alexeygrigorev/machine-management`
  promotes source from `main` after staging. The follower builds on the host;
  `.github/workflows/deploy-coolify.yml` deploys staging only.
- Application code changes follow branch → PR → green CI → merge. A merge to
  `main` is followed by the rolling follower. Do not dispatch the legacy AWS
  `deploy-prod.yml` workflow for this Coolify application; its deploy job is
  statically disabled and the GitHub workflow will be disabled after this
  change merges.
- The follower, not this repository, owns the production application UUID,
  Coolify trigger, container drain and image rollback. Keep its deployment
  records and the previous image available until live verification passes.

After each promotion, verify the Coolify deployment record is finished, the
production `/health/ready` response reports the merged source SHA, and the
homepage and one representative public page return successfully. If a GitHub
environment gate holds a deploy run, report its run link and leave the gate
unapproved.

## Worker and durable jobs

Production currently has no worker resource. The web application uses the
Relay jobs backend, but a healthy web process does not prove that queued work
is being processed. Do not infer worker health from web readiness.

The image provides the read-only `manage.py jobs_pending_check` command for a
monitor integration. It exits non-zero when due pending intents are older than
30 minutes and reports only an aggregate count and oldest age, never handlers,
payloads, recipient data or intent IDs. The host-side schedule and alert route
remain owned by the monitoring repository.

Before activating a worker, an operator must review pending intents by handler
and age, then decide which deliveries or imports may run. Starting a worker
claims pending work; stale mail can be inappropriate to send and old imports
can repeat work. Never bulk-update job statuses. Preserve the rows for
reconciliation when a handler needs an explicit retry or discard decision.

The application worker entrypoint is `./entrypoint.sh worker` with
`DJANGO_SETTINGS_MODULE=website.settings.worker`. A production worker resource
must use the same immutable image and production environment as web, run as a
singleton on the existing `coolify` network, have no public route, and retain
the 240 MiB memory and 1 CPU limits. Its healthcheck must use the stdlib
heartbeat check in `core.worker_healthcheck`; do not probe it with a Django
management command. Web must retain production settings.

The existing `docker-compose.coolify.yaml` worker service is a Compose target,
not a declaration of the current rolling production resource. Adding or
starting the production worker requires the separate Coolify resource to be
reviewed and deployed after the pending-intent decision. The web process still
submits jobs through Relay; moving to a dispatch-only backend requires an
upstream `community-base` change and tests in both consuming projects.

## Rollback and data safety

Retain the previous production image and app configuration. Use the rolling
follower's verifier and rollback path; do not change DNS or CDN configuration
as part of an application rollback. Do not delete production job rows or
credentials. A restored web release must remain compatible with the current
database schema and any worker that is active.
