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

`.github/workflows/deploy-prod.yml` is dispatch-only and gated on the
`confirm_production` input plus the `production` environment's protection
rules. It:

1. selects the latest **successful** `deploy-dev.yml` run on `main` and
   downloads its `dev-release-*` artifact;
2. validates the record against that run's head SHA and the reviewed ECR
   repository (a malformed or mismatched record aborts before any AWS call);
3. re-runs `deploy.ci_verdict require` for that exact SHA (REL-01 — the same
   acceptance policy, never a reduced second one);
4. assumes the production deployer role and runs
   `deploy/deploy_prod.sh .tmp/promotion/dev-release.json`.

Production only ever receives the image digest that was proven in dev.

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
   `DATABASE_URL`/`DJANGO_SECRET_KEY` references, the reviewed command for
   the long-running services — and rewrites only the release identity. A
   refused source stops the deployment before its registration (the gate is
   enforced explicitly because `set -e` does not apply inside `$( )`).
5. **Migrate** — one Fargate task on the new migration definition runs
   `prepare_deployment` (Django migrations, including the data migrations
   that carry code-owned content). Non-zero exit stops the deployment with
   services untouched.
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
equivalents (`make production-prep-*`, `scripts/prepare_local_data.py`,
`scripts/verify_local_dataset.py`) rebuild and verify a dataset; they are not
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
| `core/management/commands/prepare_deployment.py` | migration task command | `tests_ci/test_management_commands.py` |
| `scripts/prepare_local_data.py`, `scripts/verify_local_dataset.py` | local dataset build/verify (not a deployment step) | `scripts/tests/` |
| `make production-prep-dataset` / `production-prep-bootstrap` | local dataset rebuild via the removal gate | `scripts/tests/test_rebuild_gate.py` |

The workflows and the controller run release tooling through the locked
environment (`astral-sh/setup-uv` + `uv sync --locked` + `uv run --frozen`,
REL-19 item 5); the script's bare `python3` calls resolve to that pinned
interpreter because `uv run` puts the locked environment first on `PATH`.
