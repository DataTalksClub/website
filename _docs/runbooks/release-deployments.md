# Release deployments

Current as of 2026-09-09. This is the **supported** deployment path; the older
runbooks in this directory are archived evidence (see their banners). The
audit findings REL-01/02/06/08/19 describe why the path looks this way.

## One controller, one target-definition owner

Exactly one component owns each release concern:

| Concern | Owner |
| --- | --- |
| Release controller (the only thing that mutates a reviewed target) | `deploy/deploy_website.sh`, invoked through the thin transport wrappers `deploy/deploy_dev.sh` and `deploy/deploy_prod.sh` |
| Target definitions (accounts, cluster/repository names, families, roles, counts, tags, hostnames) | `deploy/deployment_targets.py` — `DEPLOYMENT_TARGETS` is the closed allowlist; the orchestrator reads its profile from it and holds no target literals |
| CI verdict decision (may this source SHA ship?) | `deploy/ci_verdict.py` |
| Recovery records and bounded restore | `deploy/recovery_receipt.py` |
| Task-definition promotion gate | `deploy/update_task_definition_image.py` (validation reused from `deploy/task_definitions.py`) |

The reviewed targets are `website-development` (dev.datatalks.club; its
services and secrets live in the `website-dev` namespace inside the shared
production cluster and it publishes to the shared `website-production` ECR
repository) and `website-production` (prod.datatalks.club).
`website-sandbox` — the destroyed 2026-08 sandbox stack — stays in the
registry **retired**: it can be read for historical evidence but can never be
selected for a deployment.

`deploy/cli.py` and its larger machinery (`deploy/release.py`,
`deploy/aws_gateway.py`, the normalizing half of `deploy/task_definitions.py`)
remain checked in because the manually dispatched `ci.yml` release inputs and
the recorded Gate-B evidence still bind to them. They are **not** the
supported deployment path, and they must not be deleted first: the active
orchestrator reuses their validation (`task_definitions.validate_source_workload`,
`config_for_target`). Retirement is a separate, reviewed change that maps
their remaining users first.

## The supported path: dev, then production

### 1. Push to `main` → Deploy Dev

`.github/workflows/deploy-dev.yml` (also manually dispatchable) runs, in
order:

1. **test** — the community-base dependency source check (D0.1a) and the
   deployment-contract suite (`core.tests.test_cmp_style_deployment`).
2. **verify-ci** (REL-01) — `uv run --frozen python -m deploy.ci_verdict
   require --workflow ci.yml --sha <head sha>` waits, bounded, for the
   application CI workflow's aggregate `ci-gate` job to conclude success for
   this exact SHA and refuses missing, pending, canceled, blocked or
   unsuccessful verdicts. The deployment-contract checks in `test` are not a
   substitute; only the `ci-gate` verdict is acceptance.
3. **publish** — builds the ARM64 image on `ubuntu-24.04-arm`, pushes it to
   the reviewed ECR repository under `version = <UTC timestamp>-<short sha>`,
   and captures the immutable `repo@sha256:...` digest.
4. **deploy** (GitHub environment `development`) — assumes the dev deployer
   role and runs `deploy/deploy_dev.sh` with the exact published
   image/version/SHA. On success it uploads `dev-release-<sha>.json` — the
   dev-proven release record — and the recovery receipts, both with 90-day
   retention.

### 2. Dispatch Deploy Prod

`.github/workflows/deploy-prod.yml` is dispatch-only from `main` and gated on
the `confirm_production` input plus the `production` environment's protection
rules. It:

1. selects the latest **successful** `deploy-dev.yml` run on `main` — or, when
   the optional `dev_run_id` input names an earlier run, exactly that run,
   which must itself be a completed, successful main deploy (explicit rollback
   selection; nothing infers "latest" for you) — and downloads its
   `dev-release-*` artifact;
2. validates the record against that run's head SHA and run id, the
   construction timestamp, and the reviewed ECR repository (a malformed,
   mismatched, or wrong-run record aborts before any AWS call);
3. re-runs `deploy.ci_verdict require` for that exact SHA (REL-01 — the same
   acceptance policy, never a reduced second one);
4. assumes the production deployer role and runs
   `deploy/deploy_prod.sh .tmp/promotion/dev-release.json`, which passes the
   controller checkout SHA and the record's dev run id into the receipt (REL-07
   provenance bindings).

Production only ever receives the image digest that was proven in dev. On
success the receipt is flipped to `promoted` only after every terminal
verification, and records `promoted_at` plus the observed pair — the
task-definition ARNs the services were actually left on (REL-07).

## What one deploy does

`deploy/deploy_website.sh <dev|production> <repo@sha256:...> <version>
<source-sha>` — every physical value below comes from the selected reviewed
target (`DTC_DEPLOYMENT_TARGET` is derived from the CLI argument):

1. **Profile** — `python3 -m deploy.deployment_targets profile` (shell-quoted
   registry output; fails closed on a retired or unknown target), then the
   cluster, image-digest, source-SHA and version-shape checks.
2. **Discover** — one `describe-services` for both services: network
   configuration for one-off tasks and, per REL-08, each service's **active**
   task-definition ARN — the promotion source. The most recently registered
   family revision is never a source for a service.
3. **Receipt** (REL-02) — `recovery_receipt.py capture` writes the redacted
   prior state (task-definition ARNs, desired counts) to
   `.tmp/deploy-receipts/<target>-<version>.json` **before the first
   mutation**.
4. **Promote definitions** (REL-08) — for web, worker, then migration:
   describe the source (service ARNs; the migration family's latest
   registered revision, since one-off tasks have no service), then
   `python3 -m deploy.update_task_definition_image` validates it — exactly
   one container with the workload's name (sidecars are refused), exact
   target roles, the target's runtime platform, target-scoped
   `DATABASE_URL`/`DJANGO_SECRET_KEY` references plus development Relay
   references (see recovery handoff below), the reviewed command for
   the long-running services — and rewrites only the release identity. A
   refused source stops the deployment before its registration (the gate is
   enforced explicitly because `set -e` does not apply inside `$( )`).
5. **Migrate** — one Fargate task on the new migration definition runs the
   reviewed compound command: `migrate --noinput`, then
   `sync_relay_schedules`, then `import_mail_templates`. Its `/bin/sh -lc`
   entrypoint receives that complete command as one ECS override argument, so
   each `&&`-chained step must succeed. Non-zero exit stops the deployment
   with services untouched.
6. **Mutate** — both services move to the new definitions with the target's
   desired counts, then `wait services-stable`. This is the first mutation;
   from here any failure triggers the EXIT trap's bounded automatic recovery
   to the receipt state. Recovery failing too still leaves the deployment
   red, with the receipt naming the exact manual restore.
7. **Verify** (REL-06, all fail-closed) — both services on the promoted ARNs
   with matching counts and a single web deployment; every running task
   carrying the promoted revision; a one-off worker self-check
   (`manage.py jobs_ingress_selftest` — a synthetic durable job through the
   signed ingress, no real provider, no email) on the promoted worker
   definition; then `/api/health/` reporting exactly this release's
   version/SHA/digest, `/health/ready` (database and migrations), and `/`
   — each with connect and overall deadlines inside a bounded retry loop.
8. **Record** — `recovery_receipt.py mark-promoted`, and (dev only) the
   `dev-release-<sha>.json` artifact that production later consumes.

## Data-ingest prerequisites

The deploy pipeline migrates; it does not import content. Public website
content is database-owned: editorial, FAQ/docs, course and event registrations
move through their own runbooks against a reviewed deployment target, never
through the release pipeline. What a deploy requires is already in the image
or the migration task — there is no deploy-time data handshake. The local
equivalents (`uv run --frozen python scripts/production_data.py`,
`scripts/prepare_local_data.py`, `scripts/verify_local_dataset.py`) rebuild and verify a dataset; they are not
part of a deployment.

## Failure recovery

- Receipts live under `.tmp/deploy-receipts/` (allowlisted identifiers and
  counts only) and both workflows upload them with `if: always()`, 90-day
  retention.
- Any post-mutation failure (including a verification failure in step 7)
  runs bounded automatic recovery to the captured prior state. If recovery
  itself fails, the receipt names the exact prior ARNs and counts for manual
  restore; the deployment counts as failed either way.
- Failures before the first mutation (profile, shape checks, source
  validation, migration exit) change nothing and attempt no recovery.

### One-shot development website schema rebuild

The owner-authorized recovery for #440 is a manual `Deploy Dev` dispatch from
the current `main` revision. Enter `RESET dtc_website_dev.public` in
`confirm_dev_schema_reset`. Check that the workflow shows its first attempt,
that its exact source SHA passed `ci-gate`, and that the deployment target is
`website-development` in account `387546586013`, region `eu-west-1`. The
confirmation is refused on pushes, reruns, other refs, and production. The
normal dispatch leaves the confirmation empty. Do not rerun a reset attempt;
diagnose a failed run and use a fresh normal dispatch from current `main`.

The controller checks the dev deployer identity, reviewed ECS cluster and
service identities, and the website-only migration task secret. It captures
the prior service state in the redacted recovery receipt, drains both website
services, and proves that no other website writer task remains. Service stability
and zero service counts do not prove task termination: before draining, the
controller captures and validates both services' RUNNING and STOPPED desired-state
listings. After service stability it polls only those captured tasks for actual
`STOPPED`, with a 300-second deadline (an in-flight AWS read is bounded to another
60 seconds). At most 100 captured tasks are accepted. Missing task details,
identity changes, timeout, or AWS failure refuse the operation with a safe
category; no task is force-stopped. A final cluster-wide writer check still
refuses new or competing website writers, including migration tasks. The migration
task checks its actual database, role, schema owner, search path, recreate
privilege, active connections, and absence of additional schemas or public
extensions before resetting only `dtc_website_dev.public` in one transaction.
An unexpected additional schema or extension requires a separate review; this
one-shot path refuses it. Relay, AISL, and production are outside its target.

Before schema mutation may begin, a drain failure attempts receipt-backed
restoration of the captured service counts. Once the migration task may have
begun the reset, a failure stops both website services and leaves the run red.
If AWS cannot verify the stop, the run explicitly reports unresolved manual
recovery and the receipt records the observed counts. No old image is
automatically restored against a potentially new schema. A successful
run passes the ordinary exact-image migration, worker self-check, health,
readiness, and homepage gates before it writes a `dev-release` artifact.

Service restoration also requires a pullable, schema-compatible web image.
The September 29 reset attempt failed before SQL; restoring desired count one
then exposed a deleted image pinned by the old web task definition. Desired-count
changes alone cannot recover that outage. The separate migration exit 21 still
requires diagnosis; this task-drain correction neither resolves it nor authorizes
a second reset or replay of the prior deployment.

The rebuilt schema starts empty. A green deployment does not restore prior
development users, registrations, courses, jobs, or content. Follow
`development-course-content-bootstrap.md` and `data-ingest.md` for separately
authorized data and content work after the release is healthy.

### Development Relay recovery handoff (#442)

The authorized rebuild run `36601136596` migrated the development schema, then
failed Relay schedule synchronization (exit 22). Both website services remain
stopped. Source acceptance of the promotion validator does not prove live recovery.
Use the ordinary `deploy-dev.yml` path with `confirm_dev_schema_reset` empty;
never repeat the schema reset for this recovery.

Active development promotion requires exactly `DATABASE_URL`, `DJANGO_SECRET_KEY`,
`RELAY_API_KEY`, and `RELAY_WEBHOOK_SECRET`. The Relay references must use the same
`website-dev/integrations-<six-character AWS suffix>` container in the selected
target's account and region, with corresponding `:RELAY_API_KEY::` and
`:RELAY_WEBHOOK_SECRET::` JSON selectors. Promotion preserves the source
`RELAY_BASE_URL` and references for all three workloads; it cannot create missing
runtime configuration. Production and the retained manual normalizer keep their
existing two-secret contracts.

Before an ordinary deployment, the authorized infrastructure operator completes
[aws-infra #58](https://github.com/DataTalksClub/aws-infra/issues/58)'s non-printing
secret-shape preflight and reviews a fresh saved plan from its accepted `main/dev`
revision. The sandbox apply workflow cannot activate `main/dev`. Explicitly
reconcile the infrastructure README's task-definition/IAM-only limit with the
necessary stopped web/worker service-pointer updates, reviewing their image
references and live drift. Keep both desired counts zero during preparation;
stop for unrelated state, IAM, network, database, replacement or count changes.
New family revisions alone are insufficient: the controller reads service-selected
web/worker revisions and the latest migration family revision.

The operator supplies redacted evidence that those three sources carry the reviewed
Relay URL, selectors and execution access. After that prerequisite and exact-main
CI pass, on-call observes the ordinary deployment through migration, schedule and
template synchronization, runtime health, readiness and homepage checks. Record
the successful run, source SHA and image plus deployed
`sync_relay_schedules --dry-run` no-diff and `jobs_ingress_selftest` OK evidence.
Keep #442 open and #439 held until that evidence exists. Never include secret
contents or Terraform remote state in artifacts or reports.

## Command inventory

Every supported entry point, its owner and the tests that pin it:

| Entry point | Role | Tests |
| --- | --- | --- |
| `.github/workflows/deploy-dev.yml` | dev pipeline (test → verify-ci → publish → deploy) | `ci/tests/test_workflows.py`, `core/tests/test_cmp_style_deployment.py`, `core/tests/test_deployment_workflow.py` |
| `.github/workflows/deploy-prod.yml` | production promotion (dev-proven release only) | `ci/tests/test_workflows.py`, `core/tests/test_cmp_style_deployment.py` |
| `deploy/deploy_dev.sh` / `deploy/deploy_prod.sh` | transport wrappers around the controller | `ci/tests/test_deploy_release_verification.py::test_the_promotion_flow_wires_the_new_checks_into_both_workflows` |
| `deploy/deploy_website.sh` | release controller | `ci/tests/test_deploy_release_verification.py`, `ci/tests/test_deploy_recovery_receipt.py` |
| `deploy/deployment_targets.py` (`profile`, `architecture`, role profiles, `release-record`) | target-definition owner | `core/tests/test_cmp_style_deployment.py::TargetRegistryProfileTests`, `core/tests/test_deployment_workflow.py`, `scripts/tests/test_prod_write_target.py` |
| `deploy/ci_verdict.py` | CI verdict decision (REL-01) | `ci/tests/test_deploy_ci_gate.py` |
| `deploy/recovery_receipt.py` | receipt capture / bounded recovery (REL-02) | `ci/tests/test_deploy_recovery_receipt.py` |
| `deploy/update_task_definition_image.py` | promotion gate (REL-08) | `core/tests/test_cmp_style_deployment.py::TaskDefinitionImageUpdateTests` |
| `core/management/commands/prepare_deployment.py` | migration-only convenience command (not the deployed compound task) | `core/tests/test_prepare_deployment.py` |
| `scripts/prepare_local_data.py`, `scripts/verify_local_dataset.py` | local dataset build/verify (not a deployment step) | `scripts/tests/` |
| `scripts/production_data.py dataset` / `bootstrap` | local dataset rebuild via the removal gate | `scripts/tests/test_rebuild_gate.py` |

The workflows and the controller run release tooling through the locked
environment (`astral-sh/setup-uv` + `uv sync --locked` + `uv run --frozen`,
REL-19 item 5); the script's bare `python3` calls resolve to that pinned
interpreter because `uv run` puts the locked environment first on `PATH`.
