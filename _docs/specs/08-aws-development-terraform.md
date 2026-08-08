# 08 - AWS development and Terraform

Status: draft

## Deployment target

- Development URL: `https://web.dtcdev.click`.
- AWS development account: `817685572750`.
- Primary runtime region: `eu-west-1`.
- Transactional SES region: `us-east-1`.
- Terraform source: `DataTalksClub/aws-infra` under a new independent `sandbox/website` root.
- Application source: `DataTalksClub/website`.

The Terraform root and all `website-sandbox*` resource names are legacy physical
identifiers for this development deployment. They remain unchanged until #94;
see the [development legacy-identifier boundary](../compatibility/development-legacy-identifiers.md).

Read-only inventory on 2026-08-07 found one default VPC with public subnets and no ECS cluster, RDS instance, or load balancer in `eu-west-1`. Implementation must re-inventory immediately before planning because this state can change.

## DNS safety requirement

The delegated `dtcdev.click` hosted zone is exactly:

`Z05963572WVWFHDQZH5NE`

A second same-name Route 53 zone exists in the account. The website stack must accept/use the delegated zone ID explicitly and must never select a zone by name alone or create a new `dtcdev.click` zone. Terraform plan review fails if it proposes hosted-zone creation or changes registrar delegation.

The website stack owns only its `web.dtcdev.click` records and certificate-validation records.

## Terraform layout

Recommended structure in `DataTalksClub/aws-infra`:

```text
modules/
  django-website/
    network.tf
    edge.tf
    compute.tf
    database.tf
    storage.tf
    email.tf
    observability.tf
    deploy-iam.tf
    variables.tf
    outputs.tf
sandbox/
  website/
    main.tf
    variables.tf
    terraform.tfvars.example
    versions.tf
    backend.hcl.example
    README.md
main/
  website/
    # added later, instantiating the same module with production inputs
```

If the infrastructure repository's maintainers prefer no shared modules, the same portability requirements still apply: no account IDs, zone discovery, network IDs, sizes, retention, or hostnames hardcoded inside workload resources.

Use Terraform >= 1.6 and a constrained AWS provider version consistent with the infrastructure repository. Commit source and examples only, never backend credentials, real tfvars, plans, state, or secret values.

## State

- Use the development bootstrap's encrypted, versioned S3 backend and lock mechanism.
- Use an independent state key such as `sandbox/website/terraform.tfstate`.
- Cross-stack inputs are explicit variables or documented remote-state outputs.
- The website root never manages shared backend buckets or the shared `dtcdev.click` zone.
- Production uses a separate backend/account/state; state is never copied from development as a promotion mechanism.

## Runtime topology

### Edge and DNS

- Route 53 alias for `web.dtcdev.click`.
- ACM certificates in the region required by the selected edge/origin service.
- CloudFront distribution for stable TLS/caching/security headers and future same-path asset caching.
- AWS WAF managed/rate rules where cost is accepted; application-level protections remain required.
- Origin access protected so the ALB is not a useful bypass of edge controls.

### Network

For cost-aware development portability:

- dedicated VPC across at least two availability zones;
- public subnets for ALB and development ECS tasks with public IPs;
- task security group accepts application traffic only from the ALB security group;
- isolated private database subnets accept PostgreSQL only from application tasks;
- no NAT gateway in the default development profile; tasks use tightly controlled public egress;
- module variables allow production to use private application subnets plus NAT or VPC endpoints.

Direct task ports are never internet-accessible even when a task has a public IP.

### Compute

- ECR repository with scan-on-push and lifecycle policy.
- ECS Fargate cluster.
- Separate web and Django-Q2 worker services from the same immutable image.
- One-off predeploy migration task; web/worker entrypoints never race migrations.
- One explicit scheduler owner registers recurring jobs.
- ALB target group and static liveness/dependency-aware readiness checks.
- Rolling or blue/green deployment with immutable SHA/version labels and automatic failed-readiness rollback.

### Database and storage

- RDS PostgreSQL, encrypted storage, non-public endpoint, automated backups, and final-snapshot policy.
- Small single-AZ development sizing with storage autoscaling; production inputs enable Multi-AZ, stronger deletion protection, longer backup retention, and larger instances.
- Secrets Manager entries for Django secret key, database credentials/URL, OIDC, GitHub, webhook, and integration secrets. Terraform creates containers/policies; secret values are written through an approved out-of-band process.
- S3 bucket for immutable release assets, exports, and controlled operational artifacts with encryption, versioning where appropriate, public-access block, lifecycle policies, and least-privilege access.
- CloudFront/application resolution keeps public asset paths unchanged.

### Email

- Reference the existing verified `dtcdev.click` SES identity in `us-east-1`; do not take ownership of unrelated shared DNS/identity resources.
- Create workload-owned configuration set/event destinations/SNS handling only where ownership boundaries are clear.
- Task role has only required SES actions and configured sender/region controls.
- SES event ingress verifies provider signatures and deduplicates event IDs.
- Development recipient allowlist/dry-run safeguards prevent accidental broad sends.

### Observability and cost

- CloudWatch log groups with explicit retention and encryption.
- Metrics/alarms for ALB/ECS/RDS, worker heartbeat, outbox age, sync freshness/failure, SES outcomes, and budget.
- Dashboard and notification targets with named owners.
- AWS Budgets/cost anomaly alert for the workload.
- Tags include project, environment, owner, managed-by, service, and data classification where useful.

## Continuous delivery

GitHub Actions in `DataTalksClub/website`:

1. Run lint, type, migration, unit/integration, OpenAPI drift, security, URL contract, and selected browser tests.
2. Build one image and scan it.
3. Authenticate through GitHub OIDC, not long-lived AWS access keys.
4. Push a SHA/timestamp-tagged image to ECR.
5. Run a one-off migration task.
6. Deploy web and worker using the exact image digest.
7. Poll readiness and verify `/health` returns the expected commit/version.
8. Run non-destructive development smoke tests, including a controlled email path.
9. Record deployed SHA and preserve a rollback target.

Terraform plan/apply remains in the infrastructure repository with separate OIDC permissions and protected approval appropriate to the environment. Application deployment cannot mutate infrastructure outside its narrowly scoped ECS/ECR actions.

## Development SEO controls

Infrastructure and Django jointly ensure:

- `X-Robots-Tag: noindex, nofollow` at the edge and application;
- restrictive development `robots.txt`;
- no production sitemap submission;
- production canonical URLs on equivalent content;
- no caching of authenticated/PII routes.

## Production-account migration

Promotion creates a new `main/website` root with production variables. It does not copy development state.

Production differences are explicit inputs:

- production account/backend/hosted zone/domain/certificates;
- private application networking and egress;
- RDS Multi-AZ, backup retention, deletion protection, sizing, and alarms;
- task count/autoscaling and edge/WAF policy;
- sender domain/SES identity and notification targets;
- secret values and OIDC trust;
- production retention, budgets, RPO/RTO, and log destinations.

Application images and database/content migration artifacts are promoted after verification; development credentials and data are not.

## Legacy course-host redirect Lambda

After the course platform is fully absorbed and old API consumers are migrated, add a separate small Terraform workload for `courses.datatalks.club`:

- Lambda with a generated, reviewed legacy-path map and a single redirect response implementation;
- API Gateway HTTP API or equivalent minimal HTTPS front door with a catch-all route;
- ACM certificate and Route 53 alias for the legacy hostname;
- query-string preservation, one-hop destination, cache headers, structured unknown-path metrics, and no PII logging;
- `301` for mapped GET/HEAD pages and carefully gated `308` for methods whose clients have been tested;
- immutable artifact, least-privilege role, alarms, access logs, and instant DNS/config rollback;
- development rehearsal on a non-production legacy hostname before switching `courses.datatalks.club`.

The redirect stack must import/reference the existing course-host DNS safely and must not delete the old ECS/database infrastructure until the observation and rollback window expires.

## Acceptance criteria

- development Terraform plan creates only workload-owned resources and references the exact delegated zone ID.
- No secrets, state, backend credentials, or real tfvars enter Git.
- `web.dtcdev.click` has valid TLS, health checks, noindex behavior, separate web/worker services, private RDS, and working asset paths.
- GitHub Actions uses OIDC and deploys an immutable digest with migration/readiness gates.
- Backups, alarms, recipient safeguards, and a rollback deploy are tested.
- The same infrastructure definition can plan in the production account with environment-specific inputs and no development resource/state dependency.
