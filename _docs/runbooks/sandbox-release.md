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
   release-control repository configuration consists of the six repository rows plus the
   independent fail-closed `SANDBOX_AUTO_DEPLOY=false` switch. The `sandbox` environment contains
   the exact 18 environment rows and must not define or shadow
   `SANDBOX_ROUTE53_HOSTED_ZONE_ID` or `SANDBOX_KMS_KEY_ARN`. Environment values are available only
   to the deployer job; repository values are available to both probe roles because the publisher
   deliberately has no environment.
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
| `SANDBOX_KMS_KEY_ARN` | repository, probe-only | Terraform output `kms_key_arn`; it must equal `arn:aws:kms:eu-west-1:817685572750:key/b9181223-d870-4bae-92d-fc28b7813887` |
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
six repository variables and exact 18 unshadowed environment variables above; both ECS services
at desired/running/pending `0/0/0`; and zero tasks, images, target registrations, and secret
versions. Capture a canonical, sorted full-record representation of each of the exact six
website-owned DNS records, including name, type, TTL, and complete alias target or record values,
and retain its digest as pre-probe evidence. An aggregate hosted-zone count is not a substitute
for these six canonical full records. Also capture the exact KMS key's canonical grant inventory;
the post-probe inventory must be byte-for-byte identical.

As the operator, independently prove that S3 bucket
`datamailer-sandbox-817685572750-us-east-1-tfstate` belongs to account `817685572750` and that exact
key `sandbox/website/terraform.tfstate` exists before either application role is assumed. Do not
read the state body. A missing object can appear as `403` when the caller lacks `ListBucket`, so
the application-role HEAD denial is evidence only after this independent existence proof.

The publisher probe has no GitHub environment and therefore receives the exact main-ref subject.
The deployer probe uses `environment: sandbox` and therefore receives the exact sandbox subject.
Each validates the expected account, region, role ARN, and non-secret resource inputs before role
assumption. The wrong-claim jobs prove that a main-ref token cannot assume the deployer role, an
environment token cannot assume the publisher role, and a wrong-audience token cannot assume the
publisher role. The non-environment wrong-claim job uses the validated fixed non-secret deployer
ARN because environment-scoped variables are intentionally unavailable there.

Both allowed role jobs validate the repository-only `SANDBOX_KMS_KEY_ARN` before requesting OIDC
credentials and pass it exactly once to the probe. The probe validates the exact ARN again before
creating any AWS client. Its value must equal
`arn:aws:kms:eu-west-1:817685572750:key/b9181223-d870-4bae-92d2-fc28b7813887`. The probe then calls
`CreateGrant` exactly once with the existing probe role's IAM ARN as `GranteePrincipal`,
`Operations=["Decrypt"]`, deterministic role-and-run-scoped name
`oidc-denial-probe-<role>-<numeric-run-id>`, and `DryRun=True`. AWS KMS documents that a dry-run call
always fails without creating a grant: only `AccessDenied` or `AccessDeniedException` proves this
boundary. `DryRunOperationException` means the request was unexpectedly authorized. That result,
`NotFoundException`, validation/invalid-ARN/key-state errors, any other service or transport error,
and success all fail closed. The probe never logs a grant ID, grant token, raw exception, or
provider response body.

### Historical removed-sentinel policy gate (superseded; do not execute)

The missing Secrets Manager and ECS sentinels are not live calls. Their replacement is a
three-part proof: website tests assert the calls are absent; `DataTalksClub/aws-infra` tests assert
the exact Terraform policy allowlists and deny-by-omission contract; and the operator performs a
canonical deployed-policy readback plus an IAM simulator matrix before the live probe. The audit
in `_docs/audits/2026-08-07-oidc-denial-sentinels.md` is normative for those dispositions.

For each exact role name `website-sandbox-github-publisher` and
`website-sandbox-github-deployer`, capture sorted output from `list-role-policies`,
`list-attached-role-policies`, and `get-role-policy`. Require exactly the one Terraform-owned
same-name inline policy, zero attached policies, and byte-for-byte canonical policy JSON matching
the reviewed Terraform shape. The publisher must have no Secrets Manager or ECS permission. The
deployer must omit `secretsmanager:GetSecretValue` and `ecs:DeregisterTaskDefinition`; allow
`ecs:DescribeServices` only on exact web/worker service ARNs; allow `ecs:UpdateService` only in
separate exact web/worker statements with the exact website cluster and matching task-family
conditions; and allow `ecs:RunTask` only on the exact migration family with the exact website
cluster condition. Both roles must omit `ecr:BatchDeleteImage`.

For the matrix below, the exact state object is
`arn:aws:s3:::datamailer-sandbox-817685572750-us-east-1-tfstate/sandbox/website/terraform.tfstate`;
the exact zone is `arn:aws:route53:::hostedzone/Z05963572WVWFHDQZH5NE`; the exact key is
`arn:aws:kms:eu-west-1:817685572750:key/b9181223-d870-4bae-92d2-fc28b7813887`; the exact repository
is `arn:aws:ecr:eu-west-1:817685572750:repository/website-sandbox`; and the exact database is
`arn:aws:rds:eu-west-1:817685572750:db:website-sandbox`. The two exact role resources are
`arn:aws:iam::817685572750:role/website-sandbox-github-publisher` and
`arn:aws:iam::817685572750:role/website-sandbox-github-deployer`. Use deterministic simulator-only
foreign and production ECR resource shapes under the same account and region; they are never live
API targets.

Run `aws iam simulate-principal-policy` as the operator, never as an application role, against
every row below. Resolve `<database-secret-arn>` with Secrets Manager metadata only, the three
`<current-*-task-definition-arn>` values from the exact task-definition inventory, and
`<cloudfront-distribution-arn>` from the reviewed Terraform output; do not read a secret value.
Supply the listed `ecs:cluster` and `ecs:task-definition` context entries for conditional rows.
Expand every row naming multiple actions or resources into individual evaluations. Capture only
role, action, resource, `EvalDecision`, and missing-context names. Every negative row must be
`implicitDeny`; every positive control must be `allowed` with no missing context. This
positive-control requirement prevents a broken simulator invocation from being mistaken for
proof of denial.

| Role | Action and resource | Context | Expected |
| --- | --- | --- | --- |
| publisher | `ecr:DescribeImages` on exact `website-sandbox` repository ARN | none | `allowed` positive control |
| publisher | `ecr:DescribeImages` on foreign and production-shaped repository ARNs | none | `implicitDeny` |
| publisher | `s3:GetObject` on exact state-object ARN | none | `implicitDeny` |
| publisher | `secretsmanager:GetSecretValue` on `<database-secret-arn>` | none | `implicitDeny` |
| publisher | `ecs:DeregisterTaskDefinition` on `*` | none | `implicitDeny` |
| publisher | `ecs:DescribeServices` on exact website web service ARN | exact website `ecs:cluster` | `implicitDeny` |
| publisher | `ecs:UpdateService` on exact website web service ARN | exact cluster and web task definition | `implicitDeny` |
| publisher | `ecs:RunTask` on `<current-migration-task-definition-arn>` | exact website `ecs:cluster` | `implicitDeny` |
| publisher | `iam:UpdateRoleDescription` on each exact publisher/deployer role ARN | none | `implicitDeny` |
| publisher | `route53:ChangeResourceRecordSets` on exact hosted-zone ARN | none | `implicitDeny` |
| publisher | `cloudfront:CreateInvalidation` on `<cloudfront-distribution-arn>` | none | `implicitDeny` |
| publisher | `elasticloadbalancing:ModifyTargetGroupAttributes` on exact web target-group ARN | none | `implicitDeny` |
| publisher | `rds:ModifyDBInstance` on exact website DB ARN | none | `implicitDeny` |
| publisher | `kms:CreateGrant` on exact runtime-key ARN | none | `implicitDeny` |
| publisher | `ecr:BatchDeleteImage` on exact `website-sandbox` repository ARN | none | `implicitDeny` |
| deployer | `ecr:DescribeImages` on exact `website-sandbox` repository ARN | none | `allowed` positive control |
| deployer | `ecr:DescribeImages` on foreign and production-shaped repository ARNs | none | `implicitDeny` |
| deployer | `s3:GetObject` on exact state-object ARN | none | `implicitDeny` |
| deployer | `secretsmanager:GetSecretValue` on `<database-secret-arn>` | none | `implicitDeny` |
| deployer | `ecs:DeregisterTaskDefinition` on `*` | none | `implicitDeny` |
| deployer | `ecs:DescribeServices` on each exact website web/worker service ARN | exact website `ecs:cluster` | `allowed` positive controls |
| deployer | `ecs:DescribeServices` on foreign and production-shaped service ARNs | corresponding foreign/production cluster | `implicitDeny` |
| deployer | `ecs:UpdateService` on each exact website web/worker service ARN | exact cluster and matching `<current-*-task-definition-arn>` | `allowed` positive controls |
| deployer | `ecs:UpdateService` on foreign and production-shaped service ARNs | corresponding foreign/production cluster/family | `implicitDeny` |
| deployer | `ecs:RunTask` on `<current-migration-task-definition-arn>` | exact website `ecs:cluster` | `allowed` positive control |
| deployer | `ecs:RunTask` on foreign and production-shaped task-family ARNs | exact website `ecs:cluster` | `implicitDeny` |
| deployer | `iam:UpdateRoleDescription` on each exact publisher/deployer role ARN | none | `implicitDeny` |
| deployer | `route53:ChangeResourceRecordSets` on exact hosted-zone ARN | none | `implicitDeny` |
| deployer | `cloudfront:CreateInvalidation` on `<cloudfront-distribution-arn>` | none | `implicitDeny` |
| deployer | `elasticloadbalancing:ModifyTargetGroupAttributes` on exact web target-group ARN | none | `implicitDeny` |
| deployer | `rds:ModifyDBInstance` on exact website DB ARN | none | `implicitDeny` |
| deployer | `kms:CreateGrant` on exact runtime-key ARN | none | `implicitDeny` |
| deployer | `ecr:BatchDeleteImage` on exact `website-sandbox` repository ARN | none | `implicitDeny` |

Any extra policy, attached policy, wildcard, missing condition, unexpected simulator decision, or
missing positive control stops issue #70 before OIDC. Store all scratch inputs and redacted output
under the repository-local `.tmp/`; policy evidence must contain no session credentials or secret
values.

The simulator covers identity policies only. Canonically read the exact KMS key policy; require
the ECR `website-sandbox` repository policy to be absent or byte-match its reviewed shape; and
read the S3 state-bucket policy when operator authority permits. If a required resource-policy
contribution cannot be established, stop. The bounded S3, Route 53, KMS, and ECR live calls later
supply composite evidence that simulation alone cannot provide. This whole historical subsection,
including its grouped simulator table and optional policy language, is retained only as incident
context. It is not an executable procedure and is superseded by the Gate B contract below.

### #81 Gate B — readback and simulator preflight

Gate B is a separately PM-authorized, read-only operator step. It must finish with one offline
`PASS` summary before Gate C is considered. A `STOP`, missing result, unreadable policy, unexpected
allow, incomplete context, source mismatch, or ambiguous provider result never falls through to
the OIDC probe. Closing issue #84 does not authorize this procedure.

After #85, the only executable Gate B procedure is the sealed operator described below. The older
literal command forms and field mapping retained later in this section are contract provenance for
auditors; they are not commands to copy, expand, or run manually. Manual capture, manual simulator
row expansion, and manual construction of a filtered envelope are forbidden.

#### #85 sealed operator procedure

The operator is bound to two tracked, non-secret, code-pinned inputs:

- `deploy/gate_b_binding_seed.json` freezes the accepted CloudFront, target-group, task-definition,
  secret-container ARN, network, DNS, GitHub configuration, Terraform address-count, and parent
  operator-role facts recovered from the accepted bootstrap/output evidence. It never discovers
  or refreshes a binding from a live response. Its simplified six-record binding array and the
  earlier complete Route 53 evidence have separate canonical hashes. The normalized full-record
  digest is `c224be2350342c319c09d07ec1672fd867b48d30c7a8b7587b78758b5e2ebda8`; the earlier
  1,242-byte source-capture digest
  `4cadb0505d61e04a7e652b7f2c2e303bfa573407a65dffc30d9fbf6d2708b0e7` remains provenance only
  and is not recomputed or replaced by the normalized digest.
- `deploy/gate_b_execution_contract.json` binds the unchanged #84 manifest, validator, and
  simulator matrix to the exact tools, child environments, safety limits, five absence results,
  84 readback operations, and deterministic recipe for 90 simulator operations.

The graph contains exactly 174 evidence operations: 58 AWS readbacks including STS, 26 exact-key
GitHub reads, and 90 IAM simulations. The renderer produces structured argument arrays, never a
shell string. It exposes no arbitrary executable, action, resource, policy, caller, environment,
retry, or resume input. Exact-key/list calls prove a seeded target; they cannot select or replace
one. Any live mismatch is `STOP`, not permission to rewrite the seed.

Offline planning is safe before the credential gate opens. From the exact accepted website
controller checkout, create only the private root, select one new capture ID matching
`YYYYMMDDTHHMMSSZ-12lowerhex`, and verify the code-pinned graph:

```bash
umask 077
mkdir -p -- ".tmp"
chmod 0700 -- ".tmp"
uv run --frozen python -m deploy.gate_b_operator plan --seed deploy/gate_b_binding_seed.json --contract deploy/gate_b_execution_contract.json
```

The plan command is offline and prints only stable IDs, counts, `PASS`/`STOP`, and hashes. It does
not resolve credentials, call AWS or GitHub, create a capture, or run the #84 evidence chain. PM
must bind its exact seed, execution-graph, manifest, source, configuration, and tool hashes before
authorizing the following command once:

```bash
uv run --frozen python -m deploy.gate_b_operator capture --seed deploy/gate_b_binding_seed.json --contract deploy/gate_b_execution_contract.json --capture-id "${CAPTURE_ID}"
```

The capture command creates `.tmp/gate-b-${CAPTURE_ID}` and every child directory at mode `0700`.
Every private file is a single-link regular file owned by the effective operator, created with
exclusive/no-follow semantics at mode `0600`. A pre-existing output, symlink, hard link, lexical or
resolved escape, wrong owner/mode, mixed capture, size violation, timeout, interrupt, or incomplete
inventory is `STOP`.
The timestamp prefix of the capture ID must remain within five seconds of the first command and
all status times must fit the sealed 900-second capture window, 31-second per-command envelope,
and identity/readback/simulator phase order. Old triplets renamed into a new capture cannot pass.
The operator checks the capture clock immediately before and immediately after credential vending;
if either check is stale, it stops before the STS identity command.

The accepted AWS Gate credential process is validated and resolved exactly once. Its temporary
access key, secret key, session token, and expiration remain in operator memory and never enter an
argument, file, log, hash, error, or GitHub child. The code-pinned expiration grammar accepts
exactly the second-precision UTC forms `YYYY-MM-DDTHH:MM:SSZ` and
`YYYY-MM-DDTHH:MM:SS+00:00`; both normalize to the same aware UTC instant. Other offsets,
fractional seconds, noncanonical ISO 8601 forms, malformed calendar values, and surrounding or
trailing data are `STOP`. The operator requires 840 through 900 seconds of remaining lifetime
initially and retains a hard 120-second reserve before each provider command; it never retries or
refreshes credentials.
The private AWS Gate environment file is never content-hashed or read into the operator; its
validated descriptor is held only for the one credential subprocess. The first AWS operation is
the graph's exact
`sts:GetCallerIdentity` call. Before any resource or GitHub read, the operator requires account
`817685572750`, parent role `phone-aws-sandbox-role`, role ID `AROA34YO3VSHI2OCVBKTW`, and the same
`phone-sandbox-[0-9a-f]{8}` session suffix in the STS ARN and user ID. It seals that exact returned
triple into `bindings.json`, then runs the unchanged #84 binding validator. A failure stops before
operation two. Every remaining AWS child receives the same in-memory credential triple; GitHub
children receive none of it.

Before credential vending, the operator opens and verifies the credential interpreter/script,
AWS virtual-environment interpreter/entry point, and GitHub binary, and holds their exact inodes
for the full capture. Provider execution is limited to the complete authorized graph. The AWS
interpreter retains the accepted virtual-environment path as `argv[0]` while executing the held
interpreter descriptor, so its module context remains intact. The execution boundary permits the
credential argv once, permits only STS before the binding validator seals the identity, and permits
each remaining graph argv once with its exact working directory, timeout, and environment.
Timeout, interrupt, selector failure, first phase failure, and output overflow kill and reap every
active child process group before the worker pool is joined.

AWS children use the frozen AWS CLI path, explicit output arguments, the exact region frozen in
their sanitized environment, direct temporary credentials, disabled instance metadata, and null
shared config/credential files. GitHub children
use the frozen `gh` path and accepted local GitHub configuration without an AWS variable. Both
environments exclude inherited profiles, endpoints, web-identity/container providers, proxies,
debug/pager controls, and caller-supplied overrides. Each child uses `shell=False`, closed stdin,
the fixed repository working directory, bounded output, and the contract timeout. Exit-zero
captures require an empty error channel. An accepted absence requires empty response output and
one anchored AWS CLI error matching the exact graph node.

Each operation produces one private stdout/stderr/status triplet bound to its command ID, order,
argument hash, graph hash, capture ID, phase, timestamps, exit code, and exact stdout/stderr hashes.
`deploy.gate_b_assembler` has no network or subprocess capability. It requires the exact triplet
inventory. The operator parses results for immediate phase gating; the assembler independently
re-parses the exact base64-wrapped bytes and anchored AWS error form as final authority, applies only frozen
per-command selectors, and maps the capture deterministically into the unchanged #84 bindings,
policies, resources, and simulator envelopes. CloudFront origin headers and Secrets Manager
value-bearing fields are filtered by the literal CLI projection before persistence. ECR images,
ECS task ARNs, and target-health entries use count/type projections; only an array input,
nonnegative integer count, and absent continuation token pass. Unknown or ambiguous fields,
cardinality, or pagination stop assembly.
Route 53 accepts only the exact record array plus an optional nonempty AWS CLI `NextToken`; native
service pagination fields, malformed tokens, and any record not equal to the sealed full record stop.
Before either online or standalone `PASS`, the operator re-reads `bindings.json` and
`bindings.result.json` and requires both sealed files to parse exactly as the recomputed documents.

Only these five nonzero results are accepted, each for its exact service, operation, and seeded
target:

| Evidence | Exact accepted code |
| --- | --- |
| S3 bucket policy | `NoSuchBucketPolicy` |
| S3 state lock `HeadObject` | `404` |
| ECR all-zero digest | `ImageNotFoundException` |
| ECR repository policy | `RepositoryPolicyNotFoundException` |
| ECR registry-v2 policy | `RegistryPolicyNotFoundException` |

For the lock, `403`, `NoSuchKey`, generic `NotFound`, success, a transport failure, or any other
result is `STOP`. A Secrets Manager resource-policy absence is not a sixth error: all six calls
must exit zero, return the exact ARN/name, and omit `ResourcePolicy`. An error, null/empty member,
or policy body stops.

The operator gates its phases by running the unchanged #84 validators in order: manifest and
sealed bindings before readback; policies and resources before simulation; then simulator and
summary. It also writes `execution-attestation.json`, which binds the seed, graph, raw inventory,
four envelopes/results, final summary, operation counts, and sealed parent role. Only the final
filtered summary, execution attestation, and their hashes may reach GitHub. The stdout/stderr
triplets, provider JSON, policy bodies, credentials, provider messages, state metadata details,
custom headers, and paths remain private below the capture directory.

A timeout, expiry reserve, unexpected result, assembler/validator `STOP`, or incomplete attempt
consumes the authorization. Do not resume, retry, open a second capture, or fall through to Gate C.
A complete `PASS` still requires a new PM review and separate Gate C-only authorization.

The tracked contract is `deploy/gate_b_manifest.json`, bound to website
`07186fc9bf9cf353fa12b74e97018d7f951d0fe6` (tree
`9621d51fd8952a6c12af5ea62b207aa07c988ac5`) and infrastructure
`95d93f7e07ded19e482a0c6d6471fbd93fb608d8` (tree
`1c38fdf6872a448d92e8191282525bafd3ab3410`). It contains independently rendered publisher,
deployer, and KMS fixtures; exact identifiers; exact policy-absence results; readback assertions;
and atomic simulator rows. Never copy a live policy into the expected side.

`deploy.gate_b_evidence` is standard-library-only and performs no acquisition. It validates
already filtered JSON beneath repository `.tmp/`; it has no boto3, network, subprocess, Terraform,
GitHub API, or workflow integration. The following #84 bootstrap block is retained only to explain
the validator's original private-file assumptions; do not execute it. The #85 operator owns
capture-directory creation and invokes the validator. It enforces `umask 077`, mode `0700`
directories, mode `0600` files, one capture ID, no symlink/hardlink/path escape, and one source
binding. Do not enable shell tracing.

```bash
umask 077
mkdir -p -- ".tmp"
chmod 0700 -- ".tmp"
mkdir -m 0700 -- ".tmp/gate-b-${CAPTURE_ID}"
mkdir -m 0700 -- ".tmp/gate-b-${CAPTURE_ID}/raw"
test "$(stat -c '%a' ".tmp/gate-b-${CAPTURE_ID}")" = "700"
uv run --frozen python -m deploy.gate_b_evidence manifest
```

The final local `bindings.json` still contains only the #84 envelope fields and accepted
bootstrap/output values; it is never live discovery. It binds CloudFront ID/domain/derived ARN,
target-group suffix/ARN, three current task-definition revision ARNs, six secret ARN suffixes,
task subnets and security groups, VPC and ALB DNS/zone, the exact six DNS records, and exact GitHub
variable values. The old assumption that PM could know a dynamic AWS Gate STS ARN/user ID before
credential vending is superseded by the sealed #85 sequence above: PM accepts the exact parent
contract, then the first STS capture supplies the exact triple before operation two. The unchanged
validator still requires the returned caller identity to equal the sealed binding byte-for-byte,
checks account, region, workload, family, and parent relationships, and rejects both IAM-role and
STS assumed-role forms of the publisher, deployer, application-task, and execution-task roles as
the operator. It never accepts creation timestamps, KMS grant IDs, or guessed/list-discovered
identifiers.

Every input is one exact object envelope with only `schema_version`, `capture_id`, `website_sha`,
`infra_sha`, `kind`, `payload`, and `payload_sha256`. The digest is lowercase SHA-256 of the compact
UTF-8 JSON payload with recursively sorted object keys and fixed `,`/`:` separators. The exact
bundle and nested field inventories are frozen in `readback_manifest.bundle_schemas` and
`readback_manifest.field_schemas`; no unlisted field may be discarded or added. After deterministic
field selection/renaming, write the envelope, `chmod 0600` it, and run its validator immediately.

#### Superseded literal acquisition provenance

The following #84 forms explain the provenance of the machine-owned graph. Do not copy or execute
this block. The #85 execution contract expands every value and the bounded operator is the only
authorized executor. It uses `--no-cli-pager --output json`, exact seeded targets, and the private
capture directory. An unlisted command, discovery wildcard, `--debug`, policy input,
resource-policy input, caller override, Terraform plan/apply/state-body read, `GetObject`,
`GetSecretValue`, workflow dispatch, or mutation remains forbidden. A literal simulator resource
`*` is accepted only for a frozen row whose AWS action semantics require it.

```bash
gate_b_capture() {
  name="$1"
  shift
  [[ "$name" =~ ^[A-Za-z0-9_-]+$ ]]
  output=".tmp/gate-b-${CAPTURE_ID}/raw/${name}.json"
  error=".tmp/gate-b-${CAPTURE_ID}/raw/${name}.error.txt"
  status=".tmp/gate-b-${CAPTURE_ID}/raw/${name}.status.json"
  set +e
  "$@" >"$output" 2>"$error"
  code="$?"
  set -e
  printf '{"exit_code":%s}\n' "$code" >"$status"
  chmod 0600 -- "$output" "$error" "$status"
}
gate_b_capture sts-caller aws sts get-caller-identity --no-cli-pager --output json
gate_b_capture iam-publisher-role aws iam get-role --role-name website-sandbox-github-publisher --no-cli-pager --output json
gate_b_capture iam-publisher-inline-list aws iam list-role-policies --role-name website-sandbox-github-publisher --no-cli-pager --output json
gate_b_capture iam-publisher-attached-list aws iam list-attached-role-policies --role-name website-sandbox-github-publisher --no-cli-pager --output json
gate_b_capture iam-publisher-inline aws iam get-role-policy --role-name website-sandbox-github-publisher --policy-name website-sandbox-github-publisher --no-cli-pager --output json
gate_b_capture iam-deployer-role aws iam get-role --role-name website-sandbox-github-deployer --no-cli-pager --output json
gate_b_capture iam-deployer-inline-list aws iam list-role-policies --role-name website-sandbox-github-deployer --no-cli-pager --output json
gate_b_capture iam-deployer-attached-list aws iam list-attached-role-policies --role-name website-sandbox-github-deployer --no-cli-pager --output json
gate_b_capture iam-deployer-inline aws iam get-role-policy --role-name website-sandbox-github-deployer --policy-name website-sandbox-github-deployer --no-cli-pager --output json
gate_b_capture kms-key aws kms describe-key --key-id arn:aws:kms:eu-west-1:817685572750:key/b9181223-d870-4bae-92d2-fc28b7813887 --region eu-west-1 --no-cli-pager --output json
gate_b_capture kms-alias aws kms describe-key --key-id alias/website-sandbox-runtime --region eu-west-1 --no-cli-pager --output json
gate_b_capture kms-rotation aws kms get-key-rotation-status --key-id arn:aws:kms:eu-west-1:817685572750:key/b9181223-d870-4bae-92d2-fc28b7813887 --region eu-west-1 --no-cli-pager --output json
gate_b_capture kms-policy aws kms get-key-policy --key-id arn:aws:kms:eu-west-1:817685572750:key/b9181223-d870-4bae-92d2-fc28b7813887 --policy-name default --region eu-west-1 --no-cli-pager --output json
gate_b_capture kms-grants aws kms list-grants --key-id arn:aws:kms:eu-west-1:817685572750:key/b9181223-d870-4bae-92d2-fc28b7813887 --max-items 1000 --region eu-west-1 --no-cli-pager --output json
gate_b_capture s3-bucket aws s3api head-bucket --bucket datamailer-sandbox-817685572750-us-east-1-tfstate --expected-bucket-owner 817685572750 --region us-east-1 --no-cli-pager --output json
gate_b_capture s3-location aws s3api get-bucket-location --bucket datamailer-sandbox-817685572750-us-east-1-tfstate --expected-bucket-owner 817685572750 --region us-east-1 --no-cli-pager --output json
gate_b_capture s3-ownership aws s3api get-bucket-ownership-controls --bucket datamailer-sandbox-817685572750-us-east-1-tfstate --expected-bucket-owner 817685572750 --region us-east-1 --no-cli-pager --output json
gate_b_capture s3-encryption aws s3api get-bucket-encryption --bucket datamailer-sandbox-817685572750-us-east-1-tfstate --expected-bucket-owner 817685572750 --region us-east-1 --no-cli-pager --output json
gate_b_capture s3-versioning aws s3api get-bucket-versioning --bucket datamailer-sandbox-817685572750-us-east-1-tfstate --expected-bucket-owner 817685572750 --region us-east-1 --no-cli-pager --output json
gate_b_capture s3-public-access aws s3api get-public-access-block --bucket datamailer-sandbox-817685572750-us-east-1-tfstate --expected-bucket-owner 817685572750 --region us-east-1 --no-cli-pager --output json
gate_b_capture s3-policy aws s3api get-bucket-policy --bucket datamailer-sandbox-817685572750-us-east-1-tfstate --expected-bucket-owner 817685572750 --region us-east-1 --no-cli-pager --output json
gate_b_capture s3-state-object aws s3api head-object --bucket datamailer-sandbox-817685572750-us-east-1-tfstate --key sandbox/website/terraform.tfstate --expected-bucket-owner 817685572750 --region us-east-1 --no-cli-pager --output json
gate_b_capture s3-lock-object aws s3api head-object --bucket datamailer-sandbox-817685572750-us-east-1-tfstate --key sandbox/website/terraform.tfstate.tflock --expected-bucket-owner 817685572750 --region us-east-1 --no-cli-pager --output json
gate_b_capture ecr-repository aws ecr describe-repositories --repository-names website-sandbox --registry-id 817685572750 --region eu-west-1 --no-cli-pager --output json
gate_b_capture ecr-images aws ecr describe-images --repository-name website-sandbox --registry-id 817685572750 --region eu-west-1 --no-cli-pager --output json
gate_b_capture ecr-zero-digest aws ecr describe-images --repository-name website-sandbox --registry-id 817685572750 --image-ids imageDigest=sha256:0000000000000000000000000000000000000000000000000000000000000000 --region eu-west-1 --no-cli-pager --output json
gate_b_capture ecr-repository-policy aws ecr get-repository-policy --repository-name website-sandbox --registry-id 817685572750 --region eu-west-1 --no-cli-pager --output json
gate_b_capture ecr-registry-policy aws ecr get-registry-policy --region eu-west-1 --no-cli-pager --output json
gate_b_capture cloudfront-distribution aws cloudfront get-distribution --id "${CLOUDFRONT_DISTRIBUTION_ID}" --no-cli-pager --output json --query '{Id:Distribution.Id,ARN:Distribution.ARN,Status:Distribution.Status,DomainName:Distribution.DomainName,Enabled:Distribution.DistributionConfig.Enabled,Aliases:Distribution.DistributionConfig.Aliases}'
gate_b_capture target-group aws elbv2 describe-target-groups --target-group-arns "${WEB_TARGET_GROUP_ARN}" --region eu-west-1 --no-cli-pager --output json
gate_b_capture target-health aws elbv2 describe-target-health --target-group-arn "${WEB_TARGET_GROUP_ARN}" --region eu-west-1 --no-cli-pager --output json
gate_b_capture ecs-cluster aws ecs describe-clusters --clusters arn:aws:ecs:eu-west-1:817685572750:cluster/website-sandbox --region eu-west-1 --no-cli-pager --output json
gate_b_capture ecs-services aws ecs describe-services --cluster arn:aws:ecs:eu-west-1:817685572750:cluster/website-sandbox --services website-sandbox-web website-sandbox-worker --region eu-west-1 --no-cli-pager --output json
gate_b_capture ecs-running-tasks aws ecs list-tasks --cluster arn:aws:ecs:eu-west-1:817685572750:cluster/website-sandbox --desired-status RUNNING --region eu-west-1 --no-cli-pager --output json
gate_b_capture ecs-pending-tasks aws ecs list-tasks --cluster arn:aws:ecs:eu-west-1:817685572750:cluster/website-sandbox --desired-status PENDING --region eu-west-1 --no-cli-pager --output json
gate_b_capture ecs-stopped-tasks aws ecs list-tasks --cluster arn:aws:ecs:eu-west-1:817685572750:cluster/website-sandbox --desired-status STOPPED --region eu-west-1 --no-cli-pager --output json
gate_b_capture ecs-web-task-definition aws ecs describe-task-definition --task-definition "${WEB_TASK_DEFINITION_ARN}" --region eu-west-1 --no-cli-pager --output json
gate_b_capture ecs-worker-task-definition aws ecs describe-task-definition --task-definition "${WORKER_TASK_DEFINITION_ARN}" --region eu-west-1 --no-cli-pager --output json
gate_b_capture ecs-migration-task-definition aws ecs describe-task-definition --task-definition "${MIGRATION_TASK_DEFINITION_ARN}" --region eu-west-1 --no-cli-pager --output json
gate_b_capture rds-database aws rds describe-db-instances --db-instance-identifier website-sandbox --region eu-west-1 --no-cli-pager --output json
```

Run both following commands for each exact name: `website-sandbox/database-url`,
`website-sandbox/django-secret-key`, `website-sandbox/github`, `website-sandbox/integrations`,
`website-sandbox/oidc`, and `website-sandbox/webhook`. Substitute only that literal name; never use
`list-secrets`.

```bash
gate_b_capture "secret-${EXACT_BOOTSTRAP_SECRET_KEY}-metadata" aws secretsmanager describe-secret --secret-id "${EXACT_BOOTSTRAP_SECRET_NAME}" --region eu-west-1 --no-cli-pager --output json --query '{ARN:ARN,Name:Name,Description:Description,KmsKeyId:KmsKeyId,RotationEnabled:RotationEnabled,OwningService:OwningService,PrimaryRegion:PrimaryRegion,DeletedDate:DeletedDate,VersionIdsToStages:VersionIdsToStages}'
gate_b_capture "secret-${EXACT_BOOTSTRAP_SECRET_KEY}-policy" aws secretsmanager get-resource-policy --secret-id "${EXACT_BOOTSTRAP_SECRET_NAME}" --region eu-west-1 --no-cli-pager --output json
```

Run the first command below for each of the six already-bound record keys; the record name and type
come byte-for-byte from `bindings.json`, and `--max-items 1` is accepted only when the returned
record has that exact name/type. Run the repository-variable command for each of the seven exact
manifest names and the environment-variable command for each of the 18 exact manifest names.
These are exact-key reads, not list/name discovery.

```bash
gate_b_capture "route53-${EXACT_BOUND_RECORD_KEY}" aws route53 list-resource-record-sets --hosted-zone-id Z05963572WVWFHDQZH5NE --start-record-name "${EXACT_BOUND_RECORD_NAME}" --start-record-type "${EXACT_BOUND_RECORD_TYPE}" --max-items 1 --no-cli-pager --output json
gate_b_capture "github-repository-${EXACT_REPOSITORY_VARIABLE_NAME}" gh variable get "${EXACT_REPOSITORY_VARIABLE_NAME}" --repo DataTalksClub/website --json name,value
gate_b_capture "github-environment-${EXACT_ENVIRONMENT_VARIABLE_NAME}" gh variable get "${EXACT_ENVIRONMENT_VARIABLE_NAME}" --repo DataTalksClub/website --env sandbox --json name,value
gate_b_capture github-sandbox-branch-policy gh api --method GET --header 'Accept: application/vnd.github+json' repos/DataTalksClub/website/environments/sandbox/deployment-branch-policies?per_page=100
```

The state-object HEAD response is the only current-state metadata source: canonicalize its
non-body metadata and hash it for `state_metadata_sha256`. The lock-object request must return the
exact missing-key result while the independently accepted address inventory remains exactly 98;
no `terraform state`, state download, `GetObject`, plan, refresh, or apply is permitted.

For each of the 90 literal manifest rows, use exactly one of these two command forms. Copy the row
ID, exact role ARN for its `principal`, one action, one resolved resource, and its complete context
from the validated manifest/binding pair. Use the first form only for `{}` context and the second
only for a non-empty context. `--max-items 2` makes any pagination marker or more-than-one result a
hard `STOP`; policy input, resource policy, caller ARN, extra action/resource, and omitted context
are forbidden.

```bash
gate_b_capture "simulator-${EXACT_ROW_ID}" aws iam simulate-principal-policy --policy-source-arn "${EXACT_ROW_PRINCIPAL_ARN}" --action-names "${EXACT_ROW_ACTION}" --resource-arns "${EXACT_ROW_RESOURCE}" --max-items 2 --no-cli-pager --output json
gate_b_capture "simulator-${EXACT_ROW_ID}" aws iam simulate-principal-policy --policy-source-arn "${EXACT_ROW_PRINCIPAL_ARN}" --action-names "${EXACT_ROW_ACTION}" --resource-arns "${EXACT_ROW_RESOURCE}" --context-entries "${EXACT_ROW_CONTEXT_ENTRIES_JSON}" --max-items 2 --no-cli-pager --output json
```

For the second form, sort context keys bytewise and map each `{key: value}` pair to exactly
`{"ContextKeyName":key,"ContextKeyValues":[value],"ContextKeyType":type}`. `type` is `arn` when
the value starts with `arn:` and `string` otherwise. The validator derives and compares that exact
ordered array; no list-valued, inferred, omitted, or additional context entry is accepted.

#### Deterministic filtered-bundle assembly

Never give raw provider JSON to the validator. Assemble exactly four payloads by selecting and
renaming only fields frozen in `readback_manifest.field_schemas`: STS becomes
`caller_identity`; IAM role, inline-list, attached-list, and policy documents become the two exact
role objects; KMS key/alias/rotation/policy/grants become `kms`; S3, ECR, the six secrets, and
CloudFront become their same-named resource objects; ECS cluster/services/task definitions and
task counts, ELB target group/health, and RDS become `runtime`; exact Route 53 reads become the
sorted six-record array plus its canonical SHA-256; exact GitHub reads become the two maps and
`["main"]`; and the state/lock evidence becomes `terraform`. `null` is retained where the schema
requires it; it is never silently dropped.

Each captured status must be zero except the four exact absence contracts, the absent zero digest,
and absent lock object. For those, select only the parsed service error code into the documented
`*_error` field; raw stderr never enters a bundle. Set `grant_inventory_truncated` and every
simulator `is_truncated` to false only when the corresponding captured response has no next token.
For each simulator result, copy the exact invocation into `request`, preserve its sole
`EvaluationResults` member, and reject any response/provider field not mapped by the schema.
Canonicalize the completed payload, calculate `payload_sha256`, write the seven-field envelope,
and `chmod 0600` it. This mapping is exhaustive: if a source field is ambiguous, missing, plural
where one is required, or cannot be mapped without an ad-hoc query, stop and return to PM rather
than improvising.

The field mapping is exact:

| Filtered object | Captured source and mapping |
| --- | --- |
| `operator_identity` / `caller_identity` | STS `Account`, `Arn`, `UserId` → `account_id`, `arn`, `user_id`; both objects must be identical. |
| each IAM `role` | `Role.RoleName/Arn/Path/MaxSessionDuration/AssumeRolePolicyDocument/PermissionsBoundary`, exact inline-list names/body, and attached-list entries → the eight frozen role fields; absent boundary → explicit `null`. |
| `kms` | key metadata `Arn/KeyId/Enabled/KeyState/KeyManager/Origin/KeyUsage/KeySpec/MultiRegion`, alias target, rotation boolean, `default` policy, and complete `Grants`; absent next token → `grant_inventory_truncated=false`. |
| `s3` | exact bucket/key/owner constants plus location (`null` means `us-east-1`), sole ownership rule, sole default encryption algorithm, versioning status, four public-access booleans, successful state HEAD, and exact policy error. |
| `ecr` | sole repository `repositoryName/repositoryArn/registryId/imageTagMutability/encryptionConfiguration.kmsKey/imageScanningConfiguration.scanOnPush`, full image-list length, exact zero-digest error, and exact repository/registry policy errors. |
| each `secret` | exact `ARN/Name/Description/KmsKeyId/RotationEnabled/OwningService/PrimaryRegion/DeletedDate/VersionIdsToStages` plus the exact ARN/name response whose `ResourcePolicy` member is absent; preserve explicit nulls. |
| `cloudfront` | the filtered query's `Id/ARN/Status/DomainName/Enabled/Aliases.Items` plus bound Route 53 A/AAAA targets; no origin/header field is copied. |
| `ecs_cluster` | sole cluster `clusterArn/clusterName/status/registeredContainerInstancesCount/runningTasksCount/pendingTasksCount/activeServicesCount`. |
| each `ecs_service` | sole exact service `serviceArn/serviceName/status/desiredCount/runningCount/pendingCount/taskDefinition`. |
| each `task_definition` | `taskDefinitionArn/family/revision/status/taskRoleArn/executionRoleArn` from the exact bound revision response. |
| `target_group` / counts | sole target group `TargetGroupArn/TargetGroupName/Protocol/Port/VpcId/TargetType/HealthCheckPath`; health-description length → both `target_count` and zero-target proof. |
| `database` | sole instance `DBInstanceIdentifier/DBInstanceArn/DBInstanceStatus/StorageEncrypted/KmsKeyId/PubliclyAccessible`. |
| `dns_records` / `route53` | sole exact record from each exact-key read → six bound name/type/value objects in bound order; their compact canonical array supplies `records_sha256`, with exact zone and count. |
| `github` | each exact returned name/value → the seven repository and 18 environment maps; the exact policy response must contain only `main` → `["main"]`. |
| `terraform` | canonical non-body state HEAD metadata → `state_metadata_sha256`; accepted address inventory → `address_count=98`; exact missing lock object → `locked=false`. |
| simulator result | exact row ID and invocation → `request`; response next-token absence → `is_truncated=false`; preserve the sole evaluation's action/resource/decision/missing-context fields only. |

The RDS-managed master secret is outside the six bootstrap containers and this matrix; Gate B
never discovers or reads it. Read the exact six Route 53 records using the accepted bound names
and types, never a zone/name discovery. Preserve each complete record. GitHub evidence is filtered
to seven repository variables, 18 `sandbox` variables, `SANDBOX_AUTO_DEPLOY=false`, no KMS/Route53
shadow, and the one `main` branch policy.

Expected absence is service-specific and accepted only after the exact resource exists and the
operator has read authority: S3 `NoSuchBucketPolicy`, ECR
`RepositoryPolicyNotFoundException`, ECR registry-v2 `RegistryPolicyNotFoundException`, and a
successful Secrets Manager response with exact ARN/name and the `ResourcePolicy` member absent.
An empty/null member, policy body, generic 403/AccessDenied/NotFound, IAM `NoSuchEntity`, KMS
`NotFoundException`, Secrets Manager `ResourceNotFoundException`, or malformed response is `STOP`.
S3 must be `BucketOwnerEnforced`, AES256, versioned, and have all four public-access blocks true.
The all-zero digest must be absent from the existing exact ECR repository.

#### Canonical policies, grants, and simulator rows

IAM documents are RFC3986-decoded with `unquote`, never `unquote_plus`, and a schema-declared nested
JSON string is parsed exactly one additional time. Canonicalization recursively sorts object keys;
requires every statement to have a unique non-empty string `Sid`; sorts statements only by `Sid`;
sorts only arrays for `Action`, `NotAction`, `Resource`, `NotResource`, Principal identifiers, and
Condition values; preserves every other array order; and never coerces scalar/list forms. Expected
and deployed canonical UTF-8 bytes must match. Output contains stable identifiers, `PASS`/`STOP`,
and SHA-256 hashes—not policy bodies or provider payloads.

Each application role must match its exact frozen name, ARN/account, `/` path, 3600-second session
limit, trust, sole same-name inline policy, empty attached-policy list, and null permissions
boundary. The manifest itself has a code-pinned canonical SHA-256, so changing a role, fixture,
assertion, schema, source binding, or simulator row cannot redefine success.

The exact KMS policy contains only account-root IAM enablement and the CloudWatch Logs statement
for `/ecs/website-sandbox/{web,worker,migration}` and
`/aws/rds/instance/website-sandbox/{postgresql,upgrade}`. Neither application role may occur in
that policy or as a grant `GranteePrincipal`/`RetiringPrincipal`. Canonically hash the complete
non-secret grant inventory as the Gate C baseline; legitimate service-owned grant IDs are dynamic,
not fixtures, and the later post-probe inventory must be byte-for-byte identical. A next token,
invalid grant principal, scalar/unknown operation, malformed constraint, or duplicate grant ID is
`STOP`.

Run one `aws iam simulate-principal-policy` invocation per manifest row as the operator, never as
an application role. Each invocation has exactly one principal, one action, one resource, and only
its listed context. Do not pass `--policy-input-list`, `--resource-policy`, or `--caller-arn`.
Foreign/production rows are simulator-only and change exactly one resource or one context key from
a known positive. `DescribeServices` has no cluster context. Web/worker, foreign/production, both
role ARNs, all publisher ECR token/publish/read actions, both deployer ECR read actions,
`UpdateService` service/cluster/family, `RunTask` family/cluster, and both exact `PassRole`
resources plus its service dimension are atomic rows in the manifest.

Every response contains exactly one `EvaluationResults` element echoing action/resource, the exact
`allowed` or `implicitDeny` decision, and `MissingContextValues=[]`. Zero, duplicate, extra,
partial, paginated/truncated, malformed, missing-context, or unexpected results are `STOP`.
Simulation is identity-policy evidence only; it never substitutes for KMS, ECR repository plus
registry, S3 ownership plus bucket policy, six secret policies, or later bounded Gate C calls.

Run the offline modes in this order after assembling schema-defined filtered inputs. Every command
exits nonzero on `STOP`; never continue after a nonzero exit.

```bash
uv run --frozen python -m deploy.gate_b_evidence bindings --input ".tmp/gate-b-${CAPTURE_ID}/bindings.json" --output ".tmp/gate-b-${CAPTURE_ID}/bindings.result.json"
uv run --frozen python -m deploy.gate_b_evidence policies --bindings ".tmp/gate-b-${CAPTURE_ID}/bindings.json" --input ".tmp/gate-b-${CAPTURE_ID}/policies.json" --output ".tmp/gate-b-${CAPTURE_ID}/policies.result.json"
uv run --frozen python -m deploy.gate_b_evidence resources --bindings ".tmp/gate-b-${CAPTURE_ID}/bindings.json" --input ".tmp/gate-b-${CAPTURE_ID}/resources.json" --output ".tmp/gate-b-${CAPTURE_ID}/resources.result.json"
uv run --frozen python -m deploy.gate_b_evidence simulator --bindings ".tmp/gate-b-${CAPTURE_ID}/bindings.json" --input ".tmp/gate-b-${CAPTURE_ID}/simulator.json" --output ".tmp/gate-b-${CAPTURE_ID}/simulator.result.json"
uv run --frozen python -m deploy.gate_b_evidence summary --bindings ".tmp/gate-b-${CAPTURE_ID}/bindings.result.json" --policies ".tmp/gate-b-${CAPTURE_ID}/policies.result.json" --resources ".tmp/gate-b-${CAPTURE_ID}/resources.result.json" --simulator ".tmp/gate-b-${CAPTURE_ID}/simulator.result.json" --output ".tmp/gate-b-${CAPTURE_ID}/summary.json"
```

Only the final filtered summary and hashes may reach GitHub. Never post raw input/policy JSON,
provider responses, sensitive paths, origin custom headers, state bodies, OIDC tokens, session
credentials, authorization headers, secret values, or registration data. A new PM decision is
required after Gate B `PASS` before Gate C. The summary contains the canonical manifest hash, the
four validated result-document hashes, the KMS grant-baseline hash, source/capture identity, and
the exact bound caller identity—no raw evidence.

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
for the deployer. The live denial sequence is exactly: HEAD the independently proven existing
Terraform-state object with `ExpectedBucketOwner=817685572750`; submit the transactional Route 53
duplicate-delete batch; dry-run CreateGrant on the existing KMS key; and request deletion of a
proven-absent all-zero digest from the exact existing ECR repository. Foreign ECR reads and
mutation-shaped IAM, CloudFront, ELB, RDS, Secrets Manager, and ECS sentinels are removed and
replaced by exact Terraform/IAM policy contracts plus the simulator matrix above. The full review
and dispositions are recorded in `_docs/audits/2026-08-07-oidc-denial-sentinels.md`.
Route 53 is the single narrow real-zone exception: the probe passes the
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
Require every zero-mutation invariant to remain zero, the six complete DNS records and digest to
be byte-for-byte unchanged, and the exact KMS grant inventory to be byte-for-byte identical to
pre-probe evidence. Stop at the hold point on any mismatch; do not explain it with aggregate
record-count drift or continue to secrets or a release.

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
seconds. The general forward-stage and compensation wait remains 180 seconds. Only a forward
promotion or rollback that starts/replaces the singleton worker receives the explicit, code-owned
420-second worker-stabilization budget; 420 seconds is also its hard maximum. The controller still
requires one unique `PRIMARY` deployment, exact service and primary desired/running/pending counts,
`rolloutState=COMPLETED`, and no more than one running plus pending worker. A running task, queue
activity, heartbeat, or processed job is not completion.

The recovery-safe worst-case envelope is `180 + 120 + 180 + 180 + 420 + 180 + 360 + 720 = 2340`
seconds: migration observation, stopped-migration terminal proof, web stabilization, public
readiness/liveness, worker stabilization, deployed browser smoke, three critical two-minute
artifact uploads, and the 12-minute finalization-recovery cap. This deliberately conservative sum
includes mutually exclusive migration-stop and later recovery work. It leaves `3600 - 2340 = 1260`
seconds (21 minutes) of the fixed deployer session, exceeding the required 20-minute recovery
reserve. Automatic compensation and finalization recovery do not inherit the 420-second forward
worker budget: their exact-pair service waits stay at 180 seconds and remain inside the separate
12-minute recovery cap. Do not raise either timeout in workflow inputs or code. If 420 seconds is
insufficient, disable automatic deployment and file/groom another issue with new evidence.

The 420-second value comes from development run `31261677137` on 2026-08-08. Its singleton worker
was running and processing the durable relay while ECS kept the unique primary deployment
`IN_PROGRESS` beyond the old 180-second shared window. For timeout triage, inspect only bounded ECS
service deployment/count fields, exact task-definition/task status and recent service events, plus
redacted startup logs. A duplicate/missing primary, `FAILED` rollout, more than one running/pending
worker, mixed task definitions, nonterminal counts, or missing exact digest/SHA proof is a failure;
logs must never override it.

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
