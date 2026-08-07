# OIDC denial-sentinel audit

Date: 2026-08-07  
Issues: [DataTalksClub/website#81](https://github.com/DataTalksClub/website/issues/81),
[DataTalksClub/website#83](https://github.com/DataTalksClub/website/issues/83)
Status: implemented disposition; live verification remains gated by issue #70

## Purpose and decision rule

The failed probe runs reached missing resources before they reached an authorization decision.
The first remediation reviewed the sentinels after KMS; the complete-sequence review then found
the same unsafe pattern in six earlier calls. This audit now covers every live denial request in
execution order. A sentinel is retained only when its authorization resource exists, it cannot
mutate state even if unexpectedly authorized, and service validation cannot hide the intended
authorization result. Each retained service has its own accepted denial-code set. NotFound,
validation, transport, other service errors, and a normal response all fail closed.

The application-role policy authority is the Terraform-managed publisher and deployer inline
policies in
[`modules/django-website/deployment-iam.tf`](https://github.com/DataTalksClub/aws-infra/blob/0ecb894770dda6f7b2654a67631e97a26712862c/modules/django-website/deployment-iam.tf).
The live workflow does not replace that declarative contract. It provides narrowly bounded
evidence where a service offers a genuinely non-mutating request.

## Complete live-sequence disposition

| Order | Sentinel | Authorization target | Target exists? | If unexpectedly authorized | Implemented disposition |
| ---: | --- | --- | --- | --- | --- |
| 1 | foreign/production `ecr:DescribeImages` | synthetic repository ARNs | No | Read-only, but `RepositoryNotFoundException` can precede useful authorization evidence | **Remove.** Allow live describe only on exact `website-sandbox`; prove other repository shapes with policy tests and simulation. |
| 2 | `s3:GetObject` through `HeadObject` | exact Terraform-state object | Must be independently proven before dispatch | Reads headers only; a missing object can also appear as `403` without ListBucket | **Retain.** Bind the exact bucket, key, `us-east-1`, and `ExpectedBucketOwner=817685572750`; accept only `403`, `AccessDenied`, or `AccessDeniedException`. |
| 3 | `iam:UpdateRoleDescription` | run-scoped synthetic role | No | Changes an existing role description if a target collision occurs | **Remove.** Enforce absence and simulate against both exact application-role ARNs. |
| 4 | `route53:ChangeResourceRecordSets` | exact existing hosted zone | Yes | The byte-identical duplicate deletes make the whole transactional batch invalid, so no record can change | **Retain.** Keep the exact request and exact-six-record pre/post comparison. |
| 5 | `cloudfront:CreateInvalidation` | synthetic distribution | No | Creates an invalidation and can incur cost if a target collision occurs | **Remove.** Enforce absence and simulate against the exact website distribution ARN. |
| 6 | `elasticloadbalancing:ModifyTargetGroupAttributes` | synthetic target-group ARN | No | Changes target-group behavior if a target collision occurs | **Remove.** Enforce absence and simulate against the exact website target group. |
| 7 | `rds:ModifyDBInstance` | synthetic DB identifier | No | Records a database configuration change if a target collision occurs | **Remove.** Enforce absence and simulate against the exact website DB ARN. |
| 8 | `kms:CreateGrant` | exact existing runtime key and exact existing grantee role | Yes | `DryRun=True` creates no grant; `DryRunOperationException` proves the request would have been authorized | **Retain.** Only `AccessDenied` or `AccessDeniedException` passes; compare canonical grants before and after. |
| 9 | `ecr:BatchDeleteImage` | exact existing repository plus all-zero digest selector | Repository yes; digest proven absent | A normal missing-image response deletes nothing and still fails the probe | **Retain.** Only `AccessDenied` or `AccessDeniedException` passes; compare image inventory before and after. |

The resulting live denial sequence is exactly four calls, in order: S3 HEAD, Route 53 duplicate
delete, KMS dry-run, and ECR absent-digest delete. Any extra AWS client or denial action violates
the executable allowlist. Before creating any client, configuration validation requires exact
account `817685572750`, region `eu-west-1`, repository `website-sandbox`, hosted zone, KMS ARN,
state bucket/key/owner, and S3 client region `us-east-1`.

## Removed pre-KMS sentinels

The ECR [`DescribeImages`](https://docs.aws.amazon.com/AmazonECR/latest/APIReference/API_DescribeImages.html)
requests used nonexistent foreign and production-shaped repositories. Although the API is
read-only, the repository is the authorization resource and a missing-repository result can hide
the policy boundary. Live describe is now limited to exact `website-sandbox` metadata.

[`UpdateRoleDescription`](https://docs.aws.amazon.com/IAM/latest/APIReference/API_UpdateRoleDescription.html),
[`CreateInvalidation`](https://docs.aws.amazon.com/cloudfront/latest/APIReference/API_CreateInvalidation.html),
[`ModifyTargetGroupAttributes`](https://docs.aws.amazon.com/elasticloadbalancing/latest/APIReference/API_ModifyTargetGroupAttributes.html),
and [`ModifyDBInstance`](https://docs.aws.amazon.com/AmazonRDS/latest/APIReference/API_ModifyDBInstance.html)
have no safe dry-run. Their synthetic resources could yield NotFound before authorization and a
valid collision plus widened policy would mutate state or create an invalidation. The live probe
therefore creates no IAM, CloudFront, or RDS client; ELB is used only for the deployer's allowed
exact target-health read.

## Retained S3 and Route 53 sentinels

S3 [`HeadObject`](https://docs.aws.amazon.com/AmazonS3/latest/API/API_HeadObject.html) requires
`s3:GetObject` but returns no object body. The operator must independently prove the exact state
object and bucket owner immediately before dispatch because S3 can return a generic `403` for a
missing object when the caller lacks `ListBucket`. The request uses the exact bucket
`datamailer-sandbox-817685572750-us-east-1-tfstate`, key
`sandbox/website/terraform.tfstate`, `us-east-1`, and `ExpectedBucketOwner=817685572750`.
Only `403`, `AccessDenied`, and `AccessDeniedException` pass.

Route 53 documents that a change batch is transactional and that deleting the same record twice
is an invalid batch. The retained request uses the exact existing hosted zone and two
byte-for-byte identical deletes for one run-scoped TXT record. Thus unexpected authorization
still cannot apply a change. Only `AccessDenied` or `AccessDeniedException` passes;
`InvalidChangeBatch`, `NoSuchHostedZone`, transport failure, and success fail closed.

## KMS prerequisite redesign

KMS is not one of the later sentinels, but it is the gate that made this review necessary. The
probe now supplies the exact Terraform output
`arn:aws:kms:eu-west-1:817685572750:key/b9181223-d870-4bae-92d-fc28b7813887` and uses the existing
publisher or deployer IAM role as the grantee. The one request is `CreateGrant` with
`Operations=["Decrypt"]`, name `oidc-denial-probe-<role>-<numeric-run-id>`, and `DryRun=True`.

AWS documents that [`CreateGrant`](https://docs.aws.amazon.com/kms/latest/APIReference/API_CreateGrant.html)
adds a grant without dry-run, returns a grant ID/token on success, and accepts `DryRun` to check
whether a request would succeed. The KMS
[`DryRun` permission guidance](https://docs.aws.amazon.com/kms/latest/developerguide/testing-permissions.html)
says dry-run calls always fail without applying the operation: `DryRunOperationException` means
the request would succeed, `ValidationException` means the request is invalid, and
`AccessDeniedException` means permission is absent. Therefore only AccessDenied proves the
intended boundary; DryRunOperation, NotFound, invalid ARN, validation, invalid key state, network
failure, and success are probe failures. Pre/post grant inventories must remain byte-for-byte
identical.

## Removed live sentinels

### Secrets Manager value read

[`GetSecretValue`](https://docs.aws.amazon.com/secretsmanager/latest/apireference/API_GetSecretValue.html)
returns the decrypted `SecretString` or `SecretBinary` on success and documents
`ResourceNotFoundException`, invalid parameter/state, decryption, and internal-service failures.
It has no dry-run parameter. The former run-scoped absent name therefore tested existence and an
existing target would risk exposing a secret if permissions broadened.

The executable policy contract is stricter: neither GitHub publisher nor GitHub deployer policy
may contain `secretsmanager:GetSecretValue`. Runtime access remains separately owned by the ECS
execution role for the two exact bootstrap secret ARNs; that runtime permission is not inherited
by either GitHub application role.

### ECS task-definition deregistration

The `ecs:DeregisterTaskDefinition` action
([`DeregisterTaskDefinition`](https://docs.aws.amazon.com/AmazonECS/latest/APIReference/API_DeregisterTaskDefinition.html))
marks an existing revision `INACTIVE` and documents client/parameter failures for invalid input.
Those failures include `ClientException` and `InvalidParameterException`; neither proves the
authorization boundary.
The ECS
[`Service Authorization Reference`](https://docs.aws.amazon.com/service-authorization/latest/reference/list_amazonelasticcontainerservice.html)
does not assign a resource type to this action, so a synthetic revision is not a reliable
resource-scoped denial check. There is no dry-run.

The executable policy contract requires the action to be absent from both application roles.
Task-definition registration remains restricted to the three website family ARNs and exact
release tags; it does not imply deregistration.

### ECS foreign/production service reads and mutations

[`DescribeServices`](https://docs.aws.amazon.com/AmazonECS/latest/APIReference/API_DescribeServices.html)
can return per-service failures, and AWS documents `MISSING` when a service is absent in its
[`ECS API failure reasons`](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/api_failures_messages.html).
The synthetic cluster can also yield `ClusterNotFoundException`. Although a successful describe
is read-only, there is no existing safe cross-scope service in this workload that would isolate
authorization from existence.

[`UpdateService`](https://docs.aws.amazon.com/AmazonECS/latest/APIReference/API_UpdateService.html)
can change desired count and configuration, and can start or stop tasks. It has no dry-run; an
existing unexpectedly authorized target would be mutated.

The executable policy contract requires:

- the publisher policy contains neither `ecs:DescribeServices` nor `ecs:UpdateService`;
- the deployer `DescribeServices` resources are exactly the website web and worker service ARNs;
- deployer `UpdateService` uses separate exact web/worker resources, the exact website cluster
  condition, and the corresponding website task-family condition; and
- no production-shaped, foreign, wildcard service, cluster, or family resource appears.

### ECS foreign/production task launch

The `ecs:RunTask` action
([`RunTask`](https://docs.aws.amazon.com/AmazonECS/latest/APIReference/API_RunTask.html)) starts tasks
and has no dry-run. The former request combined absent family revisions with synthetic subnet and
security-group IDs, so missing-resource or client validation could conceal authorization. A valid
unexpectedly authorized request would launch a task and could incur cost or network/application
side effects.

The executable policy contract requires the action to be absent from the publisher and limits
the deployer to the exact website migration-family ARN with the exact website cluster condition.
The separately required `iam:PassRole` statement remains limited to the exact website task and
execution roles; it is not evidence for a foreign-family denial.

## Retained ECR sentinel

[`BatchDeleteImage`](https://docs.aws.amazon.com/AmazonECR/latest/APIReference/API_BatchDeleteImage.html)
authorizes against the repository and accepts image digests as selectors. AWS returns deleted
IDs and per-image failures in an HTTP 200 response; it separately documents invalid parameter,
repository-not-found, and server exceptions. The ECR
[`Service Authorization Reference`](https://docs.aws.amazon.com/service-authorization/latest/reference/list_ecr.html)
defines the repository as the action's resource type.

The redesigned request therefore uses the exact existing repository `website-sandbox`, never a
foreign or missing repository, and the syntactically valid all-zero 64-hex SHA-256 selector. The
pre-probe inventory must prove the digest absent. Exactly one call is made. If the role is denied,
the AccessDenied result proves the boundary. If authorization is unexpectedly available, ECR's
normal missing-image response cannot delete anything and the probe still fails loudly. A response
claiming deletion, a response containing only an image-not-found failure, validation/NotFound,
transport failure, and any other result all fail closed. The post-probe inventory must remain
identical.

## Verification contract

The removed sentinels use a three-part proof without recreating an unsafe request:

1. The website live-call allowlist permits exactly the four ordered denial calls. It rejects
   foreign or production ECR describe calls, `update_role_description`, `create_invalidation`,
   `modify_target_group_attributes`, `modify_db_instance`, `get_secret_value`,
   `deregister_task_definition`, foreign or production `describe_services`, `update_service`,
   and `run_task` for both probe roles.
2. Exact `aws-infra` tests evaluate the Terraform-generated publisher and deployer policies. They
   require deny by omission for Secrets Manager value reads and task-definition deregistration;
   exact service, cluster, and family allowlists for deployer Describe/Update/Run; publisher
   omission of those ECS actions; omission of IAM role updates, CloudFront invalidations, target
   group changes, RDS changes, and ECR delete from both roles; and exact repository scope for ECR
   describe.
3. Before the live probe, the operator canonically reads back both deployed inline policies and
   runs the complete `aws iam simulate-principal-policy` matrix in the release runbook. Every
   negative row must return `implicitDeny`; exact positive controls must return `allowed` with no
   missing context. Any extra/attached policy, shape mismatch, unexpected decision, or failed
   positive control stops issue #70.

Focused website tests independently exercise all four retained sentinels so a preceding failure
cannot hide the next boundary. They assert the exact S3 object and owner, exact Route 53 batch,
exact KMS request, and exact ECR request; use service-specific denial codes; exercise
representative NotFound, validation, transport, DryRunOperation, normal response, and success
outcomes; and assert exact one-call requests. The workflow contract separately limits the KMS ARN
to the two allowed probe jobs and validates it before role assumption.

The IAM simulator provides identity-policy evidence only. Before dispatch, the operator must
also canonically read the KMS key policy, prove the ECR repository policy is absent or has the
exact reviewed shape, and read the S3 state-bucket policy when operator authority permits. If a
required resource-policy contribution cannot be established, the preflight stops. The retained
bounded live calls provide the final composite evidence; executing the readback or simulator is
outside issue #83 and requires the separately authorized issue #81 Gate B.

Before any new live probe, the operator must also satisfy the complete preflight and postflight in
[`sandbox-release.md`](../runbooks/sandbox-release.md): exact policies and variables, zero runtime
mutation inventory, exact-six DNS bytes/digest, and exact KMS grant inventory. A mismatch or any
non-authorization result stops issue #70 without retrying the historical runs.
