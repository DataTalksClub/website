# OIDC denial-sentinel audit

Date: 2026-08-07  
Issue: [DataTalksClub/website#81](https://github.com/DataTalksClub/website/issues/81)  
Status: implemented disposition; live verification remains gated by issue #70

## Purpose and decision rule

The failed probe runs reached missing resources before they reached an authorization decision.
This audit reviews every live denial sentinel after KMS. A sentinel is retained only when it uses
the intended authorization resource, cannot mutate state even if unexpectedly authorized, and
does not rely on a missing resource or invalid input being authorized before service validation.
Only `AccessDenied` or `AccessDeniedException` proves a retained boundary. NotFound, validation,
transport, other service errors, and a normal response all fail closed.

The application-role policy authority is the Terraform-managed publisher and deployer inline
policies in
[`modules/django-website/deployment-iam.tf`](https://github.com/DataTalksClub/aws-infra/blob/0ecb894770dda6f7b2654a67631e97a26712862c/modules/django-website/deployment-iam.tf).
The live workflow does not replace that declarative contract. It provides narrowly bounded
evidence where a service offers a genuinely non-mutating request.

## Disposition summary

| Sentinel | Authorization target | Target exists? | NotFound/validation path | If unexpectedly authorized | Implemented disposition |
| --- | --- | --- | --- | --- | --- |
| `secretsmanager:GetSecretValue` | run-scoped secret name | No | `ResourceNotFoundException`, invalid parameter/state, or decryption failure can precede useful authorization evidence | A normal response contains decrypted secret material | **Remove.** Enforce absence from both GitHub application-role policies; never read a real secret in the probe. |
| `ecs:DeregisterTaskDefinition` | synthetic family revision `:999999999` | No | `ClientException` or `InvalidParameterException` is documented; the action has no resource-level authorization type | An existing target is marked `INACTIVE` | **Remove.** Enforce that neither application role allows the action. |
| `ecr:BatchDeleteImage` | exact existing `website-sandbox` repository plus all-zero digest | Repository yes; digest proven absent | A valid missing digest is returned in an HTTP 200 `failures` list; invalid input may raise `InvalidParameterException` | The proven-absent digest deletes nothing | **Retain.** One exact request; AccessDenied only passes; every response/error other than AccessDenied fails. |
| `ecs:DescribeServices` for foreign/production scopes | synthetic cluster and service | No | `ClusterNotFoundException` or a response-level `MISSING` failure can occur | Read-only, but no existing safe cross-scope target proves the boundary | **Remove.** Enforce publisher absence and deployer allowlist of the exact web/worker service ARNs. |
| `ecs:UpdateService` for foreign/production scopes | synthetic cluster and service | No | cluster/service NotFound and parameter validation can occur | A real target can change count or trigger deployment | **Remove.** Enforce publisher absence and deployer exact-service, exact-cluster, exact-family statements. |
| `ecs:RunTask` for foreign/production families | synthetic task revision plus synthetic networking | No | missing task definition, invalid subnet/security group, or other client validation can occur | A valid target can launch a billed task with side effects | **Remove.** Enforce publisher absence and deployer exact migration-family plus exact-cluster statement. |

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

[`DeregisterTaskDefinition`](https://docs.aws.amazon.com/AmazonECS/latest/APIReference/API_DeregisterTaskDefinition.html)
marks an existing revision `INACTIVE` and documents client/parameter failures for invalid input.
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

[`RunTask`](https://docs.aws.amazon.com/AmazonECS/latest/APIReference/API_RunTask.html) starts tasks
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

1. The website absence tests assert that `get_secret_value`, `deregister_task_definition`, foreign or
   production `describe_services`, `update_service`, and `run_task` calls do not occur for either
   probe role.
2. Exact `aws-infra` tests evaluate the Terraform-generated publisher and deployer policies. They
   require deny by omission for Secrets Manager value reads and task-definition deregistration;
   exact service, cluster, and family allowlists for deployer Describe/Update/Run; publisher
   omission of those ECS actions; and omission of ECR delete from both roles.
3. Before the live probe, the operator canonically reads back both deployed inline policies and
   runs the complete `aws iam simulate-principal-policy` matrix in the release runbook. Every
   negative row must return `implicitDeny`; exact positive controls must return `allowed` with no
   missing context. Any extra/attached policy, shape mismatch, unexpected decision, or failed
   positive control stops issue #70.

Focused website tests independently exercise both retained sentinels so a preceding failure
cannot hide the next boundary. They require only AccessDenied/AccessDeniedException for KMS and
ECR; exercise representative NotFound, validation, transport, normal missing-image response,
DryRunOperation, and success outcomes; and assert exact one-call requests. The workflow contract
separately limits the KMS ARN to the two allowed probe jobs and validates it before role
assumption.

Before any new live probe, the operator must also satisfy the complete preflight and postflight in
[`sandbox-release.md`](../runbooks/sandbox-release.md): exact policies and variables, zero runtime
mutation inventory, exact-six DNS bytes/digest, and exact KMS grant inventory. A mismatch or any
non-authorization result stops issue #70 without retrying the historical runs.
