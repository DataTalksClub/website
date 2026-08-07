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
2. Keep Terraform/OIDC administration separate from the application roles. Read back the
   publisher/deployer trust policies and confirm their exact audience and immutable subjects.
   Confirm the GitHub `sandbox` environment permits only the `main` deployment branch.
3. Configure the non-secret GitHub variables using the exact scope and accepted source below. The
   release-control repository configuration consists of the five repository rows plus the
   independent fail-closed `SANDBOX_AUTO_DEPLOY=false` switch. The `sandbox` environment contains
   the exact 18 environment rows and must not define or shadow
   `SANDBOX_ROUTE53_HOSTED_ZONE_ID`. Environment values are available only to the deployer job;
   repository values are available to both probe roles because the publisher deliberately has no
   environment.
4. Complete the live OIDC probe hold point described below. All allowed sessions, wrong claims,
   metadata reads, and permission denials must pass before continuing.
5. Only after the probe is green, populate `DATABASE_URL` and `DJANGO_SECRET_KEY` out of band. Do
   not read them through either application release role, and leave every other secret container
   empty.
6. Proceed to release A and later release exercises only after the two required secret versions
   have been verified through metadata without reading their values.

| GitHub variable | Scope | Accepted source |
| --- | --- | --- |
| `SANDBOX_AWS_REGION` | repository | Terraform output `aws_region` |
| `SANDBOX_ECR_REPOSITORY_URI` | repository | Terraform output `ecr_repository_uri` |
| `SANDBOX_ECR_REPOSITORY_NAME` | repository | Terraform output `ecr_repository_name` |
| `SANDBOX_PUBLISHER_ROLE_ARN` | repository | Terraform output `github_publisher_role_arn` |
| `SANDBOX_ROUTE53_HOSTED_ZONE_ID` | repository, probe-only | reviewed infrastructure input/invariant `Z05963572WVWFHDQZH5NE`; this is not a Terraform output and is never discovered by name |
| `SANDBOX_DEPLOYER_ROLE_ARN` | `sandbox` environment | Terraform output `github_deployer_role_arn` |
| `SANDBOX_ECS_CLUSTER_ARN` | `sandbox` environment | Terraform output `ecs_cluster_arn` |
| `SANDBOX_WEB_TARGET_GROUP_ARN` | `sandbox` environment | Terraform output `web_target_group_arn` |
| `SANDBOX_ECS_WEB_SERVICE_NAME` | `sandbox` environment | Terraform output `ecs_web_service_name` |
| `SANDBOX_ECS_WORKER_SERVICE_NAME` | `sandbox` environment | Terraform output `ecs_worker_service_name` |
| `SANDBOX_ECS_WEB_TASK_FAMILY` | `sandbox` environment | Terraform output `ecs_web_task_definition_family` |
| `SANDBOX_ECS_WORKER_TASK_FAMILY` | `sandbox` environment | Terraform output `ecs_worker_task_definition_family` |
| `SANDBOX_ECS_MIGRATION_TASK_FAMILY` | `sandbox` environment | Terraform output `ecs_migration_task_definition_family` |
| `SANDBOX_ECS_TASK_ROLE_ARN` | `sandbox` environment | Terraform output `ecs_task_role_arn` |
| `SANDBOX_ECS_EXECUTION_ROLE_ARN` | `sandbox` environment | Terraform output `ecs_task_execution_role_arn` |
| `SANDBOX_ECS_CONTAINER_NAMES` | `sandbox` environment | compact JSON from Terraform output `ecs_container_names` |
| `SANDBOX_ECS_SUBNET_IDS` | `sandbox` environment | compact JSON from Terraform output `ecs_subnet_ids` |
| `SANDBOX_ECS_SECURITY_GROUP_IDS` | `sandbox` environment | compact JSON from Terraform output `ecs_security_group_ids` |
| `SANDBOX_ECS_ASSIGN_PUBLIC_IP` | `sandbox` environment | Terraform output `ecs_assign_public_ip` as `true`/`false` |
| `SANDBOX_WEB_RELEASE_DESIRED_COUNT` | `sandbox` environment | Terraform output `web_release_desired_count` |
| `SANDBOX_WORKER_RELEASE_DESIRED_COUNT` | `sandbox` environment | Terraform output `worker_release_desired_count` |
| `SANDBOX_RESOURCE_PROJECT_TAG` | `sandbox` environment | Terraform output `resource_project_tag` |
| `SANDBOX_RESOURCE_ENVIRONMENT_TAG` | `sandbox` environment | Terraform output `resource_environment_tag` |

Do not export secret-container ARNs to the workflow. The normalized builder retains and compares
the task definitions' secret references without requesting secret values.

## Post-bootstrap OIDC probe

Before writing either secret or publishing any image, dispatch the current-main `CI` workflow
with its exact current-main SHA, `operation=probe`, `probe_sandbox=true`,
`deploy_sandbox=false`, `failure_injection=none`, `reuse_existing_image=false`, and all image and
release-record inputs empty. Probe mode
skips the normal quality/Django/Playwright jobs, the container build, publisher mutation,
deployment, and release artifacts. Its separate contract job still checks the lockfile,
deployment source, and focused release/probe tests.

Immediately before dispatch, prove `SANDBOX_AUTO_DEPLOY=false`; exact local, remote, controller,
and source `main`; the accepted main-only environment policy and exact role trusts/policies; the
five repository variables and exact 18 unshadowed environment variables above; both ECS services
at desired/running/pending `0/0/0`; and zero tasks, images, target registrations, and secret
versions. Capture a canonical, sorted full-record representation of each of the exact six
website-owned DNS records, including name, type, TTL, and complete alias target or record values,
and retain its digest as pre-probe evidence. An aggregate hosted-zone count is not a substitute
for these six canonical full records.

The publisher probe has no GitHub environment and therefore receives the exact main-ref subject.
The deployer probe uses `environment: sandbox` and therefore receives the exact sandbox subject.
Each validates the expected account, region, role ARN, and non-secret resource inputs before role
assumption. The wrong-claim jobs prove that a main-ref token cannot assume the deployer role, an
environment token cannot assume the publisher role, and a wrong-audience token cannot assume the
publisher role. The non-environment wrong-claim job uses the validated fixed non-secret deployer
ARN because environment-scoped variables are intentionally unavailable there.

Before the regional `DescribeTargetHealth` permission is assumed, the deployer job requires the
Terraform output to match exactly
`arn:aws:elasticloadbalancing:eu-west-1:817685572750:targetgroup/website-sandbox-web/<16 lowercase hex>`.
A target group from another region, account, workload name, or malformed/stale suffix fails before
OIDC role assumption.

Wrong-claim jobs request raw GitHub OIDC tokens only in process memory and call the unsigned STS
web-identity endpoint directly. They accept only `AccessDenied` for a wrong subject and the AWS
`InvalidIdentityToken` response for a deliberately wrong audience; a generic failed action,
network error, missing input, NotFound, or unexpected successful role assumption fails the run.
Neither the token nor an unexpectedly returned credential is printed or persisted.

Allowed calls are metadata-only: caller identity and exact-repository image metadata for the
publisher; plus exact cluster, service, task-definition, running-task, and target-health metadata
for the deployer. Safe denial probes cover Terraform-state metadata, IAM, CloudFront, ALB, RDS,
KMS, ECS deregistration, ECR deletion, and otherwise-allowed actions against cross-repository,
cross-cluster/service, cross-family, and production-shaped scopes. Secret sentinels remain
run-scoped and absent. Route 53 is the single narrow real-zone exception: the probe passes the
exact non-secret `Z05963572WVWFHDQZH5NE` ID without listing or selecting zones, then submits one
transactional request with exactly two byte-for-byte identical `DELETE` changes for the synthetic
TXT RRset `oidc-denial-probe-<numeric-run-id>.dtcdev.click.` and value
`"oidc-denial-probe-<numeric-run-id>"`. This name, type, and value cannot equal the managed web,
origin, or ACM-validation records. Route 53 validates the whole batch transactionally, documents
duplicate deletes as `InvalidChangeBatch`, and applies none of it if validation fails. Thus an
unexpectedly over-permitted application role still cannot mutate DNS. Only an AccessDenied-class
response passes. `InvalidChangeBatch`, `NoSuchHostedZone`, any other service result, network
failure, or success means the authorization boundary was not proven and fails loudly. The real
target group is used only by the allowed target-health read.

Probe logs contain one JSON line per allowed/denied action with the non-secret resource, assumed
role session ARN, role class, result, and timestamp. They never print an OIDC token, temporary
credential, secret value, state body, authorization header, or raw AWS exception. A probe creates
no image, upload, task revision/run, service update, invalidation, secret read/write, release
record, or artifact.

After every probe result, including a red result, repeat the services/tasks/images/targets/secret
version inventory and recapture the same exact six DNS records in the same canonical format.
Require every zero-mutation invariant to remain zero and the six complete DNS records and digest
to be byte-for-byte unchanged from the pre-probe evidence. Stop at the hold point on any mismatch;
do not explain it with aggregate record-count drift or continue to secrets or a release.

## Select and promote a release

Use the `CI` workflow on `main` with `workflow_dispatch`. Supply an exact full lowercase
40-character `release_sha`. The controller proves that commit exists and is an ancestor of
current `main`; arbitrary, short, uppercase, fork, feature, pull-request, and tag-only revisions
fail before either AWS role is assumed. Quality gates and the one container build use that
selected source checkout, while the release controller remains the reviewed current-main code.

Set `deploy_sandbox=true` and `operation=promote` only for an authorized release:

- Exact release A `0f0ae208526fa2e76848cf4f5a87bd4aa26687ec`, first bootstrap: set
  `reuse_existing_image=false`, leave `published_image_record` and `prior_release_record` empty,
  and use `failure_injection=none`. Both service counts must still be zero. Migration must exit
  `0` before web becomes nonzero; worker follows healthy web. Download both
  `sandbox-published-image-0f0ae208526fa2e76848cf4f5a87bd4aa26687ec-attempt-<attempt>` and the
  attempt-qualified successful release artifact after the run. If the same workflow run is
  re-run, use only that attempt's records; never overwrite or silently substitute an earlier one.
- Exact release B `e2b93beb1544170b6177ba55ea8fd6530b2e57a3` is built and published only
  by its controlled migration-failure run. That red deployment still leaves a successful
  publisher job and the independent
  `sandbox-published-image-e2b93beb1544170b6177ba55ea8fd6530b2e57a3-attempt-<attempt>` artifact. Every later B
  promotion sets `reuse_existing_image=true` and passes that compact JSON verbatim as
  `published_image_record`; it also passes the exact successful A JSON as
  `prior_release_record`.
- Later ordinary releases use the prior run's
  `sandbox-successful-release-<sha>-attempt-<attempt>` artifact. The
  active task ARNs, counts, source SHA, and digest must match it before registration or migration.

Leave `failure_injection=none` for every normal promotion and rollback. Controlled failure
choices are dispatch-only and promotion-only; the controller rejects them without a valid prior
release record, so they can never run during release-A bootstrap or rollback.

The build path builds once for `linux/amd64`, proves OCI revision and runtime user `10001:10001`,
and preserves that tested image as a short-lived artifact. The publisher either pushes a new
full-SHA tag once or proves an existing immutable tag has the same image-config digest. It then
uploads a compact non-secret published-image record containing source SHA, exact repository URI,
ECR digest, image-config digest, platform, and user. This artifact is produced before deployment
and is **not** a successful or rollback-eligible release record.

The reuse path performs no Docker build, load, login, pull, or push. Under the publisher role it
requires the full-SHA tag to resolve to the record's exact ECR digest and requires
`BatchGetImage` to resolve the manifest's exact recorded image-config digest. Missing records,
missing tags, malformed fields, repository/source mismatches, or digest mismatches fail closed.
The same recorded ECR digest then reaches the deployer. The deployer registers the exact digest
task definitions, runs migration, promotes web, verifies readiness and liveness SHA, promotes the
singleton worker, and runs the complete read-only smoke. Only deployment success produces a
rollback-eligible release artifact.

An automatic first attempt may build and cache the exact full-SHA tested image once. A GitHub
"Re-run all jobs" attempt must restore that immutable cache, load it, and repeat every provenance,
runtime-user, and liveness check. A cache miss on attempt 2 or later fails before checkout/build;
it must never rebuild the same SHA. The short-lived inter-job `release-image-<sha>` handoff keeps a
stable name and may be overwritten only after cache restore/reverification, so "re-run failed jobs"
can consume the earlier successful container job. Every audit-facing artifact name includes
`attempt-<github.run_attempt>` so a rerun preserves earlier evidence.

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

## Controlled failure drills

Use only exact release B `e2b93beb1544170b6177ba55ea8fd6530b2e57a3` and exact active release A
`0f0ae208526fa2e76848cf4f5a87bd4aa26687ec`. The workflow parses the prior JSON before any build
or role assumption and rejects every other main ancestor, B over a non-A prior, and release A.
Do not change the source SHA, image, secrets, task configuration, network, or outbound-email
safeguards between a failure drill and the corresponding clean promotion.

Use the exact #70 sequence below. `<A-release-json>`, `<B-release-json>`, `<A-image-json>`, and
`<B-image-json>` mean the single-line output of `jq -c . <downloaded-artifact-file>`; `<run-id>`
is the workflow run that produced the named artifact.

| Order | Operation | SHA | Failure | Reuse | Required records | Expected deployment result |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | promote A | `0f0ae208526fa2e76848cf4f5a87bd4aa26687ec` | none | false | none | green; A image + A release |
| 1R | retry promote A | same A | none | true | image=A; no prior | green; no rebuild |
| 2 | promote B | `e2b93beb1544170b6177ba55ea8fd6530b2e57a3` | migration | false | prior=A | red; B image only |
| 2R | retry B migration drill | same B | migration | true | image=B, prior=A | red; no rebuild; A unchanged |
| 3 | promote B | `e2b93beb1544170b6177ba55ea8fd6530b2e57a3` | none | true | image=B, prior=A | green; B release |
| 4 | rollback A | `0f0ae208526fa2e76848cf4f5a87bd4aa26687ec` | none | true | image=A, target=A, current=B | green; A active |
| 5 | promote B | `e2b93beb1544170b6177ba55ea8fd6530b2e57a3` | `post_mutation_smoke` | true | image=B, prior=A | red; compensated to A |
| 6 | promote B | `e2b93beb1544170b6177ba55ea8fd6530b2e57a3` | none | true | image=B, prior=A | green; final B active |

The first two dispatches are:

```console
gh workflow run CI --ref main -f release_sha=0f0ae208526fa2e76848cf4f5a87bd4aa26687ec \
  -f deploy_sandbox=true -f probe_sandbox=false -f operation=promote \
  -f failure_injection=none -f reuse_existing_image=false
gh workflow run CI --ref main -f release_sha=e2b93beb1544170b6177ba55ea8fd6530b2e57a3 \
  -f deploy_sandbox=true -f probe_sandbox=false -f operation=promote \
  -f failure_injection=migration -f reuse_existing_image=false \
  -f prior_release_record='<A-release-json>'
gh run download <run-id> \
  -n sandbox-published-image-e2b93beb1544170b6177ba55ea8fd6530b2e57a3-attempt-<attempt> \
  --dir .tmp/release-artifacts/b-image
```

Every later B promotion, including a retry of the migration drill, uses the exact B image record
and prior A record. Change only `failure_injection` among the table's allowed values:

```console
gh workflow run CI --ref main -f release_sha=e2b93beb1544170b6177ba55ea8fd6530b2e57a3 \
  -f deploy_sandbox=true -f probe_sandbox=false -f operation=promote \
  -f failure_injection=none -f reuse_existing_image=true \
  -f published_image_record='<B-image-json>' -f prior_release_record='<A-release-json>'
```

The only accepted build/retry shapes are: first A with `reuse=false` and no image/prior, A retry
with `reuse=true` plus exact A image and no prior, first B migration drill with `reuse=false` plus
prior A, and B migration retry with `reuse=true` plus exact B image and prior A. Missing records,
`reuse=false` plus a record, wrong/crossed A/B image records, or wrong/missing prior records fail
before a role is assumed.
An A or B manual retry is a **new dispatch** with `reuse=true` and the prior attempt-qualified image
record. A GitHub "Re-run" retains the old inputs and is useful only for an infrastructure failure;
it must restore the exact-SHA cache, repeats all image verification, and fails closed if that cache
is missing. It can never rebuild the SHA and cannot substitute for the required new retry shape.

The exact A rollback dispatch is:

```console
gh workflow run CI --ref main -f release_sha=0f0ae208526fa2e76848cf4f5a87bd4aa26687ec \
  -f deploy_sandbox=true -f probe_sandbox=false -f operation=rollback \
  -f failure_injection=none -f reuse_existing_image=true \
  -f published_image_record='<A-image-json>' -f target_release_record='<A-release-json>' \
  -f current_release_record='<B-release-json>'
```

- To prove migration failure isolation, the first dispatch sets `operation=promote`,
  `failure_injection=migration`, `reuse_existing_image=false`, leaves `published_image_record`
  empty, and supplies A as `prior_release_record`. A retry changes only to
  `reuse_existing_image=true` with the exact B image record. This is the only release-B build/push run.
  The
  publisher's independent B image record must be downloaded even though the deployment is red.
  The controller registers
  the normalized B definitions and launches that exact B migration task with one fixed,
  non-secret container command override. The retained `manage.py` entry point receives the
  intentionally nonexistent `__dtc_controlled_migration_failure__` Django subcommand, so Django
  exits nonzero without entering any command handler, migration path, or database write and before
  either ECS service is updated. The run must fail, must not produce a successful-release artifact,
  and both services must still match A exactly. It never runs a database reversal.
- To prove automatic compensation after mutation, first leave both services on A, then dispatch
  the same B source with `failure_injection=post_mutation_smoke`, `reuse_existing_image=true`, the
  exact B `published_image_record`, and A as `prior_release_record`. After B web and worker reach
  their gates, the controller deliberately
  fails at the deployed-smoke gate. Existing compensation restores both exact A task-definition
  ARNs and desired counts, waits for stability, proves A identity and public health, and leaves no
  successful-release artifact or mixed service pair.

An injected run is expected to be red. Inspect its non-secret ECS task/service evidence and verify
that no `sandbox-successful-release-*` artifact exists. The migration drill alone still has the B
published-image artifact because publication completed successfully first. Set
`failure_injection=none`, `reuse_existing_image=true`, and supply the same B image record before
the clean promotion of the same digest. Never use either injection for bootstrap, rollback, or as
a workaround for an uncontrolled failure.

## Manual immutable rollback

Only a successful release record is a rollback target. Bootstrap placeholder definitions are
never eligible. Dispatch the reviewed `main` workflow with:

- `deploy_sandbox=true`;
- `operation=rollback`;
- `release_sha` equal to the target record's exact reachable SHA;
- `reuse_existing_image=true` and `published_image_record` equal to the target image's compact
  `sandbox-published-image-<sha>-attempt-<attempt>` JSON;
- `target_release_record` equal to the compact successful target JSON; and
- `current_release_record` equal to the compact active successful JSON.

Rollback never builds, loads, logs Docker in, pulls, or pushes. The publisher verifies the target
full-SHA tag, ECR digest, and manifest config digest against the supplied image record, then the
deployer moves web and worker to the target exact ARNs. It runs the same read-only smoke and does
not run migration. A failed rollback compensates both services to the captured current record.

## Final release-B Terraform reconciliation

Never reconcile release A or an intermediate release state. Reach this second/final Terraform
apply hold point only after all of these exercises are complete: controlled B migration failure
while A remains active, normal B promotion, explicit B-to-A rollback, post-mutation B smoke
failure with exact compensation to A, and clean promotion of the already-built B digest so exact B
is active again.

Set Terraform's source SHA to exact accepted release B
`e2b93beb1544170b6177ba55ea8fd6530b2e57a3`, set its image digest to the exact active B digest,
and set `services_enabled = true`. Under #70, review and apply the second and final plan only when
its changes are limited to the approved B release metadata, service-enabled desired-count state,
two directly derived service-count alarm settings, and non-secret outputs. It must not replace the
application-managed active B task-definition ARNs or change network, DNS, IAM, database, KMS, or
Secrets Manager resources. Delete the ephemeral plan, then require an identical locked/refreshed
plan to be no-change and to keep both services enabled on B.

Only after that terminal no-change plan and exact active B verification may the #70 operator enable
automatic deployment by setting `SANDBOX_AUTO_DEPLOY=true`. Before the first merge or push, and
throughout bootstrap, drills, rollback, final B promotion, and Terraform reconciliation, the
repository-level variable must exist and be exactly `SANDBOX_AUTO_DEPLOY=false`. An absent
repository variable is not a disabled state because an organization-level `true` value could be
inherited. Establish the repository override and require the read-back output to be exactly
`false`:

```console
gh variable set SANDBOX_AUTO_DEPLOY --repo DataTalksClub/website --body false
gh variable get SANDBOX_AUTO_DEPLOY --repo DataTalksClub/website
```

Enable and immediately read back the exact value:

```console
gh variable set SANDBOX_AUTO_DEPLOY --repo DataTalksClub/website --body true
gh variable get SANDBOX_AUTO_DEPLOY --repo DataTalksClub/website
```

The output must be exactly `true`. If any #70 invariant regresses, evidence is incomplete, or the
active pair cannot be proven, disable future automatic runs first. Do not cancel an already-mutating
serialized run; let its controller finish compensation, then inspect it.

```console
gh variable set SANDBOX_AUTO_DEPLOY --repo DataTalksClub/website --body false
gh variable get SANDBOX_AUTO_DEPLOY --repo DataTalksClub/website
```

The output must be exactly `false`. Never delete this repository variable while the automatic-CD
workflow exists: deletion can expose an inherited organization-level `true` value and silently
enable deployment.

## Automatic deployment after bootstrap

Once explicitly enabled, a push to `main` runs every quality, Django, Playwright, and container
gate and builds the pushed source exactly once. Before the publisher can make any ECR call, a
separate sandbox-environment deployer job captures the active application state with read-only ECS
calls. A failed/invalid capture therefore prevents publication as well as deployment.

That capture is a distinct active-service-pair schema containing only source SHA, image digest,
exact web/worker task-definition ARNs, and desired counts. Automatic compensation needs no prior
migration identifier, so prior capture/recovery never guesses a cross-family revision or reads
family `latest`, and never broadens IAM to list task definitions. Registration still reads each exact configured family as its
normalized source template. Web and worker must be stable, nonzero, unmixed,
and backed by exact running tasks. Both definitions must carry exactly
`ReleaseManager=DataTalksClub/website`, `Project=website`, and `Environment=sandbox`; both must also
pass the existing normalized family, role, image, runtime-user, command, safety-environment, and
secret-reference checks.

After successful capture, the publisher verifies/pushes and the deployer consumes that same
current-run pair. Promotion captures web and worker again after task registration and immediately
before migration, so a publication/registration race fails without a migration or service update.
It captures them again after migration and immediately before the first service update to refresh
compensation state and catch a race during migration. Bootstrap-disabled, unstable, mixed, raced,
untagged, or unmanaged state fails before publication or service mutation as applicable.

Every OIDC-bearing job—including manual release jobs, allowed probes, and raw wrong-claim token
requests—fetches remote `main` immediately before requesting a token and requires its checked-out
controller to equal remote current `main`. Probes additionally require the resolved source to equal
that controller; automatic push jobs also require `GITHUB_SHA` and the candidate to match. A stale
queued push therefore makes no AWS call. Successful automatic deployment emits attempt-qualified
published-image, successful-release, read-only-smoke, and deployment-evidence artifacts.

The normal automatic sequence is quality/deployment-contract, Django with PostgreSQL, Playwright,
one tested image, exact active-pair capture, immutable publication, migration exit `0`, stable and
healthy web with exact SHA, singleton worker, read-only HTTP/browser smoke (including safe 404),
terminal exact-pair verification, and artifact finalization. The deployer session is fixed at 3600
seconds. Forward stage waits are capped at 180 seconds, a stopped migration gets a separate
120-second terminal-proof budget, browser smoke is capped at 180 seconds, each critical artifact
upload is capped at two minutes, and finalization recovery is capped at 12 minutes. This
conservative sequential envelope retains more than 20 minutes of the role session for recovery;
operators must not raise these limits ad hoc.

### Automatic failure triage

- A stale queued push fails its current-main check before the relevant OIDC request. It makes no AWS
  call at that stage. Do not rerun it after `main` advanced; inspect the latest main push instead.
- A failed active-pair capture publishes no image and performs no service mutation. Set the gate to
  `false`, inspect only bounded capture errors and exact ECS metadata, correct the managed/stable/
  tag/image condition through a groomed issue, then re-enable and use a new main push. Never invent
  a migration ARN or choose family `latest`.
- A same-run automatic rerun may only restore the first attempt's exact-SHA image cache and re-run
  all image checks. If the cache is absent, it fails closed before build. Do not weaken the guard;
  use a new reviewed main commit/run.
- A migration failure changes no service. A timeout or observation error stops the exact task and
  refuses to release workflow concurrency until ECS proves that ARN is `STOPPED`.
- A controller failure after the first service update restores both exact prior ARNs/counts, waits
  for terminal state, proves prior SHA/digest and public health, and records compensation. If that
  proof is absent or failed, disable the gate and use loud recovery below.
- A bounded evidence-build or artifact-upload failure after controller success invokes the strict,
  run-internal recovery context, restores the prior pair, removes the local success record, records
  `artifact_finalization_failed_compensated`, and ends red. The successful-release upload is not
  attempted until evidence and smoke uploads have passed.

GitHub runner termination, Actions control-plane loss, or total network loss can occur without a
failure step executing. ECS and GitHub artifacts cannot form an atomic transaction, so that
unobservable infrastructure-loss boundary cannot guarantee automatic compensation. Treat a run
with no terminal verdict/evidence as unresolved, disable future automatic deployment, compare both
services/tasks and endpoint SHA with the last accepted release, and use the attempt's
`sandbox-recovery-checkpoint-<run-id>-attempt-<attempt>` through the HUMAN break-glass procedure
below. This is an operational limitation, not permission to broaden application IAM.

### Deployment evidence interpretation

Artifact names end in `attempt-<github.run_attempt>`; never mix records from different attempts.
`sandbox-deployment-evidence-<run-id>-attempt-<attempt>` contains bounded non-secret JSON: run
URL/ID/attempt, controller/source SHA, image digest, captured prior, gate results, each current-main
checkpoint timestamp, actual controller stage events, final ARNs/counts, and redacted HTTP results.
It contains no raw headers/bodies, cookies, credentials, or recovery context.
`controller_succeeded_pending_artifact_finalization` means the AWS controller finished, but the
attempt-qualified successful-release artifact and green run are still required for acceptance.
`failed_without_success_record` means no rollback-eligible record exists.
`artifact_finalization_failed_compensated` means transport failed and the prior exact pair was
restored; the run is intentionally red. A failed/missing compensation, missing terminal stage,
`not_proven` checkpoint, non-success gate, or absent HTTP evidence is unresolved: keep the gate off.

The smoke artifact includes `http-evidence.json` plus desktop `1280x720` and mobile `390x844`
screenshots for home, Studio sign-in, and the deliberate safe 404. The HTTP JSON records only path,
status, allowlisted noindex/cache/canonical/version/denial booleans, and timestamps. Inspect each
screenshot; reject a debug page, traceback, broken layout, or wrong endpoint.

## Loud compensation failure recovery

If automatic compensation cannot restore a terminal pair, its error includes the prior web and
worker task-definition ARNs and desired counts. The GitHub deployer session is runner-only and is
not an interactive operator credential. After runner loss or loud compensation failure, obtain
explicit incident go-ahead from the DTC infrastructure owner and use the existing phone-gated
`arn:aws:iam::817685572750:role/phone-aws-sandbox-role` authority according to its own credential
vending runbook. This is a HUMAN break-glass action. First require `aws sts get-caller-identity` to
show account `817685572750` and that exact assumed-role name, fetch current remote `main`, download
the same attempt's checkpoint below `.tmp/`, and validate its strict repository, exact web/worker
ARN families, nullable bootstrap identity, and paired `0/0` or `1/1` counts. Never use a checkpoint
from another attempt and never trust it automatically.

Read the live services/tasks before mutation. If they are already the checkpoint pair, verify
terminal/public health and stop. If they are the attempted release or a mixed pair attributable to
the unresolved run, restore only the checkpoint's two exact task-definition ARNs and counts—never
`latest`, a bare family, or operator-selected revisions:

```console
aws ecs update-service --cluster <exact-cluster-arn> --service <exact-web-service-name> \
  --task-definition <exact-prior-web-task-definition-arn> --desired-count <prior-web-count>
aws ecs update-service --cluster <exact-cluster-arn> --service <exact-worker-service-name> \
  --task-definition <exact-prior-worker-task-definition-arn> --desired-count <prior-worker-count>
aws ecs wait services-stable --cluster <exact-cluster-arn> \
  --services <exact-web-service-name> <exact-worker-service-name>
```

Then run the HTTP smoke with the prior source SHA and inspect both services/running tasks against
the prior release record; for a `0/0` bootstrap checkpoint, require both services stable at zero and
skip public health. Confirm the worker's running plus pending count never exceeds one. Do not reverse
the successful database migration. If live state cannot be attributed exactly, the phone role is
unavailable, or either service cannot be proven terminal, stop and escalate—do not perform general
drift repair. Attach only non-secret run/checkpoint/cluster/service/task/SHA/digest evidence to the
incident and route any controller change through a groomed issue and independent acceptance.

## Evidence

Retain the independent `sandbox-published-image-<sha>-attempt-<attempt>` JSON,
`sandbox-successful-release-<sha>-attempt-<attempt>` JSON,
`sandbox-deployment-evidence-<run-id>-attempt-<attempt>` JSON, and
`sandbox-read-only-smoke-<sha>-attempt-<attempt>` HTTP evidence and desktop/mobile screenshots.
Before mutation, retain the non-secret strict
`sandbox-recovery-checkpoint-<run-id>-attempt-<attempt>` for HUMAN abrupt-runner recovery only.
For detected artifact finalization failure, retain
`sandbox-deployment-finalization-failure-<run-id>-attempt-<attempt>`. The smoke checks `/`,
liveness, readiness, anonymous Studio sign-in redirect, anonymous admin API denial, and a safe 404
without credentials or data mutation. It never creates users, registrations, enrollments,
submissions, outbox rows, or messages. Download all artifacts under `.tmp/`, for example:

```console
gh run download <run-id> --dir .tmp/release-artifacts/run-<run-id>-attempt-<attempt>
```
