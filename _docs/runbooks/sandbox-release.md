# Sandbox immutable release runbook

This runbook covers application release control for `https://web.dtcdev.click`. Issue #69
defines and tests the mechanism. Issue #70 owns the first authorized Terraform apply, secret
population, image publication, ECS mutation, and live rollback evidence.

Never paste secret values into a workflow input, command, release record, screenshot, issue,
or log. A release record contains only source/digest/task-definition/count identifiers. A
database migration is forward-only: compensation and rollback never run a reverse migration.

## One-time bootstrap

1. Apply the reviewed `DataTalksClub/aws-infra` `sandbox/website` root with
   `services_enabled = false`. Confirm the effective web/worker desired counts are `0/0` and
   the configured release targets are `1/1`. The documented placeholder digest must not run.
2. Populate the required Secrets Manager values out of band. Do not read them through either
   application release role.
3. Keep Terraform/OIDC administration separate from the application roles. Confirm the GitHub
   `sandbox` environment permits only the `main` deployment branch.
4. Copy the non-secret Terraform outputs to GitHub variables using this mapping. Values scoped
   to the `sandbox` environment are available only to the deployer job; the region, ECR, and
   publisher values must be repository variables because the publisher deliberately has no
   environment.

| GitHub variable | Terraform output |
| --- | --- |
| `SANDBOX_AWS_REGION` | `aws_region` |
| `SANDBOX_ECR_REPOSITORY_URI` | `ecr_repository_uri` |
| `SANDBOX_ECR_REPOSITORY_NAME` | `ecr_repository_name` |
| `SANDBOX_PUBLISHER_ROLE_ARN` | `github_publisher_role_arn` |
| `SANDBOX_DEPLOYER_ROLE_ARN` | `github_deployer_role_arn` |
| `SANDBOX_ECS_CLUSTER_ARN` | `ecs_cluster_arn` |
| `SANDBOX_WEB_TARGET_GROUP_ARN` | `web_target_group_arn` |
| `SANDBOX_ECS_WEB_SERVICE_NAME` | `ecs_web_service_name` |
| `SANDBOX_ECS_WORKER_SERVICE_NAME` | `ecs_worker_service_name` |
| `SANDBOX_ECS_WEB_TASK_FAMILY` | `ecs_web_task_definition_family` |
| `SANDBOX_ECS_WORKER_TASK_FAMILY` | `ecs_worker_task_definition_family` |
| `SANDBOX_ECS_MIGRATION_TASK_FAMILY` | `ecs_migration_task_definition_family` |
| `SANDBOX_ECS_TASK_ROLE_ARN` | `ecs_task_role_arn` |
| `SANDBOX_ECS_EXECUTION_ROLE_ARN` | `ecs_task_execution_role_arn` |
| `SANDBOX_ECS_CONTAINER_NAMES` | compact JSON from `ecs_container_names` |
| `SANDBOX_ECS_SUBNET_IDS` | compact JSON from `ecs_subnet_ids` |
| `SANDBOX_ECS_SECURITY_GROUP_IDS` | compact JSON from `ecs_security_group_ids` |
| `SANDBOX_ECS_ASSIGN_PUBLIC_IP` | `ecs_assign_public_ip` as `true`/`false` |
| `SANDBOX_WEB_RELEASE_DESIRED_COUNT` | `web_release_desired_count` |
| `SANDBOX_WORKER_RELEASE_DESIRED_COUNT` | `worker_release_desired_count` |
| `SANDBOX_RESOURCE_PROJECT_TAG` | `resource_project_tag` |
| `SANDBOX_RESOURCE_ENVIRONMENT_TAG` | `resource_environment_tag` |

Do not export secret-container ARNs to the workflow. The normalized builder retains and compares
the task definitions' secret references without requesting secret values.

## Select and promote a release

Use the `CI` workflow on `main` with `workflow_dispatch`. Supply an exact full lowercase
40-character `release_sha`. The controller proves that commit exists and is an ancestor of
current `main`; arbitrary, short, uppercase, fork, feature, pull-request, and tag-only revisions
fail before either AWS role is assumed. Quality gates and the one container build use that
selected source checkout, while the release controller remains the reviewed current-main code.

Set `deploy_sandbox=true` and `operation=promote` only for an authorized release:

- Release A, first bootstrap: leave `prior_release_record` empty. Both service counts must still
  be zero. Migration must exit `0` before web becomes nonzero; worker follows healthy web.
- Release B and later: download the prior run's `sandbox-successful-release-<sha>` artifact and
  pass its compact JSON as `prior_release_record`. The active task ARNs, counts, source SHA, and
  digest must match it before registration or migration.

The workflow builds once for `linux/amd64`, proves OCI revision and runtime user
`10001:10001`, and preserves that tested image as a short-lived artifact. The publisher either
pushes a new full-SHA tag once or proves an existing immutable tag has the same image-config
digest. The deployer then registers the exact digest task definitions, runs migration, promotes
web, verifies readiness and liveness SHA, promotes the singleton worker, and runs the complete
read-only smoke. Only success produces a rollback-eligible release artifact.

After first bootstrap succeeds, set Terraform's real source SHA/digest and
`services_enabled = true`, review/apply that narrow reconciliation under #70, and confirm the
terminal plan does not return either service to disabled counts.

The controller entry points, also useful for offline argument inspection, are:

```console
uv run python -m deploy.cli promote --help
uv run python -m deploy.cli rollback --help
uv run python -m deploy.smoke --base-url https://web.dtcdev.click --source-sha <40hex>
```

The workflow supplies all cluster, service, family, container, network, role, tag, count,
repository, SHA, digest, and record arguments directly from the reviewed output mapping. Do not
replace them with name searches.

## Automatic compensation

A migration launch failure, timeout, missing exit code, or nonzero exit changes no service. A
failure after the web update attempts restoration of **both** prior exact service task-definition
ARNs and desired counts, even if worker had not moved. It waits for both services, enforces at
most one running/pending worker, validates prior digest/SHA, and verifies prior public health.
The database remains migrated forward.

The workflow concurrency group is `website-sandbox-release` with cancellation disabled. Never
cancel an in-progress release to start another one.

## Manual immutable rollback

Only a successful release record is a rollback target. Bootstrap placeholder definitions are
never eligible. Dispatch the reviewed `main` workflow with:

- `deploy_sandbox=true`;
- `operation=rollback`;
- `release_sha` equal to the target record's exact reachable SHA;
- `target_release_record` equal to the compact successful target JSON; and
- `current_release_record` equal to the compact active successful JSON.

The workflow rebuilds the selected source once and proves its existing ECR tag matches; rollback
will not republish a missing target. It moves web then worker to the target exact ARNs, runs the
same read-only smoke, and does not run migration. A failed rollback compensates both services to
the captured current record.

## Loud compensation failure recovery

If automatic compensation cannot restore a terminal pair, its error includes the prior web and
worker task-definition ARNs and desired counts. Under the authorized #70 deployer session, use
only those printed identifiers—never `latest` or a family name:

```console
aws ecs update-service --cluster <exact-cluster-arn> --service <exact-web-service-name> \
  --task-definition <exact-prior-web-task-definition-arn> --desired-count <prior-web-count>
aws ecs update-service --cluster <exact-cluster-arn> --service <exact-worker-service-name> \
  --task-definition <exact-prior-worker-task-definition-arn> --desired-count <prior-worker-count>
aws ecs wait services-stable --cluster <exact-cluster-arn> \
  --services <exact-web-service-name> <exact-worker-service-name>
```

Then run the HTTP smoke with the prior source SHA and inspect both services/running tasks against
the prior release record. Confirm the worker's running plus pending count never exceeds one. Do
not reverse the successful database migration. If either service cannot be proven exact, stop
and escalate with the non-secret cluster, service, task-definition, SHA, and digest identifiers.

## Evidence

Retain the successful release JSON and the `sandbox-read-only-smoke-<sha>` desktop/mobile HTML
screenshots. The smoke checks `/`, liveness, readiness, the anonymous Studio sign-in redirect,
and anonymous admin API denial without credentials or data mutation. It never creates users,
registrations, enrollments, submissions, outbox rows, or messages.
