# dev.datatalks.club on Contabo Coolify (2026-10-26 cutover)

## What serves dev

`dev.datatalks.club` (via CloudFront) terminates on contabo-a traefik and
routes to the Coolify **website-staging** app (`wodajblzizrjunwme3ossav3`),
which Coolify rebuilds from `main` on every push. The routers live in the
file provider (`/data/coolify/proxy/dynamic/origin-enforce.yml`), keep the
same `X-Origin-Verify` allow/deny contract as prod/origin, and point at
the Coolify docker service
`http-0-wodajblzizrjunwme3ossav3-web-2567@docker`. That service name is
stable across deploys (it names the compose service and the port, not the
timestamped containers), so no per-deploy proxy edit is needed.
Direct-to-origin requests without the header still get 403. The
Coolify-generated docker routers for the same host are shadow (lower
priority) and stay as fallback.

## Push-to-dev contract (enforced in deploy-coolify.yml)

On every push to `main` the workflow, before touching production:

1. writes the release identity (`VERSION`, `SOURCE_SHA`, `IMAGE_DIGEST`)
   to the staging app env via the Coolify API,
2. triggers a staging deploy,
3. polls `https://dev.datatalks.club/health/ready` until `source_sha`
   equals the pushed SHA (15 min cap),
4. probes a unique bad-token confirm URL and requires HTTP 200 with the
   invalid-link page (missing routes 404; the pre-2026-10-26
   `select_for_update` bug 500s).

Any step failing fails the workflow run before the production deploy step.

## Database

Staging uses the shared dev database (`website_dev`, owner role) so dev
traffic can never touch the production `website` database. The staging app
connects as the owner role, which also owns future migrations; the retired
manual `website-dev` compose keeps working against the same database.

## Leftovers (do not delete without Alexey's OK)

- Manual compose project `/data/website-dev` (`website-dev-web`,
  image `dtc-website:4d8f93e`, `website_dev` database): unrouted since the
  cutover but still running and healthy. Retire it (stop, then remove)
  only when a future schema migration makes the old code unhealthy, with
  Alexey's explicit OK.
- `website-dev-backend` service block in `origin-enforce.yml`: inert (no
  routers reference it). Keep until `website-dev-web` is retired.
- `origin-enforce.yml.bak-pre-staging-cutover-20261026`: pre-cutover copy.
- Staging `RELAY_SYNC_SCHEDULES=false` Coolify env: the boot-time Relay
  schedule sync registers production callback URLs and Relay rejects them
  from dev (`origin_not_allowed`), so staging skips the sync while
  production (flag unset) still fails closed on sync errors.
- Staging `docker_compose_domains.web.domain=http://dev.datatalks.club`:
  the source of the dev routers for this compose-pack app (the `fqdn`
  field only drives Dockerfile-pack apps). The `custom_labels` value is
  inert for this app; leave it alone.
