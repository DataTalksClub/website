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

Repeated development plan/apply is blocked on #78's accepted validation path. Coordinate #94 before
targeting the current root/state identity; this work does not rename the accepted legacy physical
resources or import unrelated infrastructure. Source policy and local tests may proceed without AWS
mutation.

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
- CloudFront distribution for stable TLS, security headers, and positive-TTL caching of only
  generated-registry anonymous public routes.
- One Terraform-managed CloudFront-scope AWS WAF ACL with staged managed/rate rules;
  application-level protections remain required.
- Origin access protected so the ALB is not a useful bypass of edge controls.

### CloudFront cache and origin-request policies

The website's versioned route-cache registry is the reviewed source for Django behavior, generated
Terraform input/policy assertions, and deployed smoke. Terraform plan validation fails if a Django
route is absent or its class differs from the expected CloudFront behavior. Missing/unknown routes
remain zero-TTL.

CloudFront uses the exact classes in specification 02: fingerprinted static 365 days, stable release
assets 24 hours, editorial details 600 seconds, public hubs/feeds/sitemap 300 seconds, anonymous
course/event catalog/details 60 seconds, explicit permanent redirects 24 hours, and clean
credential-free query-free public 404s 30 seconds. Search/arbitrary query, private/dynamic,
operational, unsafe methods, disallowed errors, and any private response are disabled. Every cache
policy has `min_ttl = 0`; error caching is zero except for the public-404 behavior. Stale-if-error
uses only the bounded class values and never private/time-sensitive state.

Per-behavior cache/origin-request policies forward only reviewed allowlists. Public objects key on
normalized path and CloudFront-normalized gzip/Brotli as allowed; a named hub may include only its
canonical positive `page`. Public keys exclude arbitrary query, cookies, viewer headers, Host,
User-Agent, Referer, Accept-Language, forwarding headers, and country. Remove the temporary
all-viewer policy from cacheable behaviors; keep request context only on a zero-TTL/private behavior
that needs it.

A deterministic versioned viewer-request CloudFront Function with no key-value store removes a
viewer-supplied classification lookalike. It marks only proven credential-free requests as
`anonymous-v1`; Authorization, signed URL/cookie, session/auth/CSRF or unknown credential-shaped
cookie, preview/management token, malformed encoding, or function failure becomes private. The
marker is forwarded and keys public HTML. Explicit private paths have separate zero-TTL behaviors.
Credentialed mixed-path requests go to origin, and an origin-response guard forces private/no-store
before storage. If the chosen function/policy ordering cannot prove isolation, that route remains
zero-TTL.

CloudFront removes any viewer-supplied country lookalike and forwards its own
`CloudFront-Viewer-Country` only to the zero-TTL onboarding/profile consumer behaviors. The existing
CloudFront origin-facing prefix-list, expected Host, TLS 1.2, and generated origin-verification
checks remain intact. Country is never a public cache-key input, and Terraform/tests never read back
or reproduce the origin secret. Local/direct-origin use has no country signal.

### Invalidation and deploy ordering

Terraform grants the worker only the bounded distribution invalidation actions it needs. Content
activation commits a unique secret-free intent with its public route manifest, then submits/coalesces
after commit. The first release may send exactly one `/*` per activated content release. Provider
IDs/state, bounded retry, age, and terminal failure are observable; invalidation paths never contain
query secrets, profile/email data, management links, or preview tokens.

Fingerprint assets need no deployment invalidation. After a new web revision is ready and before
release finalization, submit at most one idempotent `/*` keyed by exact application SHA and wait for
`Completed` within the documented bound. Failure fails finalization and keeps/restores a known-good
revision. A retry reuses the same logical intent. Rollback creates another invalidation under the
rollback release identity so routes/templates cannot remain mixed.

### WAF, logging, plan, and alarms

The WAF ACL implements specification 07's managed protections and count-mode five-minute starting
thresholds: 2,000 ordinary public cacheable reads; 300 search/unknown-query/origin-bound reads; 300
API reads; 60 signup/login/profile/Slack/course/event registration requests; plus one reviewed
Terraform emergency rule. Run count mode for at least seven representative development days before
reviewed blocking. Blocked responses are non-cacheable and do not reach ALB/origin. User-Agent is
not crawler identity; targeted/advanced bots, fraud/account-takeover, CAPTCHA, and challenge are not
silently enabled.

Before any pricing subscription or apply, a redacted read-only report uses current AWS docs and 30
days of workload metrics, or an explicit shorter-window projection, to compare pay-as-you-go and
then-current flat plans under normal, 10x, cache-busting, and distributed-bot scenarios. It records
exact account/distribution eligibility, behavior/rule/function limits, logging and bot feature
levels, allowance headroom, residual origin/compute/database/log/invalidation costs, and unsupported
associations. Select the cheapest sufficient option by the rule in `open-decisions.md`: Free only if
the whole contract fits; prefer eligible Pro; otherwise compare pay-as-you-go with Business; retain
pay-as-you-go if flat lifecycle is not reproducible in accepted automation; require a new approved
issue for Premium/advanced products. Recheck prices/features at implementation time.

Use standard, not real-time, CloudFront/WAF logs with encryption, bounded retention, least privilege,
and field omission/redaction for Cookie, Authorization, session/CSRF, complete query, IP, country,
origin-verification values, preview/management tokens, Slack/profile data, and bodies. Metrics use
bounded route/viewer/cache/WAF/invalidation labels. Add named-owner/runbook alarms for 50/80/100% of
selected allowance/forecast, cache hit ratio below 70% after warm-up, origin rate above twice normal
peak for 15 minutes, WAF/block/rate and 4xx/5xx anomalies, invalidation failure/age, edge-function
errors, and ALB/ECS/RDS cost/load rising despite cache/WAF.

Emergency controls are reviewed Terraform inputs for the rate/block rule and for cache
disable/TTL-zero rollback. Neither can expose the origin or broaden cacheability.

The MVP adds no key-value-store classifier, Origin Shield, multi-origin failover, real-time logs,
advanced/targeted bot or fraud product, CAPTCHA/challenge, console-only rule/subscription, public
origin, or Redis application cache. Any such expansion needs its own reviewed issue.

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
- Secrets Manager entries for Django secret key, database credentials/URL, OIDC, GitHub, webhook,
  Slack join URL, and other integration secrets. Terraform creates containers/policies; secret
  values are written through an approved out-of-band process.
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
- no caching of authenticated/PII routes;
- the same noindex/nofollow result on cache HIT, MISS, redirect, error, asset, and WAF denial;
- production-equivalent content retains its production canonical, and positive caching does not
  change production robots, sitemap, structured-data, URL, or indexing behavior.

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
- The generated public/private route matrix, per-behavior allowlists, anonymous classifier,
  response guard, zero-TTL private paths, country-only consumer forwarding, and origin lock agree
  across source, Terraform policy tests, and deployed smoke.
- Content/deploy/rollback invalidation is durable, idempotent, bounded, secret-free, observable, and
  cannot finalize a mixed application release after provider failure.
- WAF count evidence precedes blocking, blocked fixtures perform no origin work, and the selected
  cheapest-sufficient plan/logging/allowance configuration is reproducible with named alarms and a
  tested TTL-zero/emergency-rule rollback.
- GitHub Actions uses OIDC and deploys an immutable digest with migration/readiness gates.
- Backups, alarms, recipient safeguards, and a rollback deploy are tested.
- The same infrastructure definition can plan in the production account with environment-specific inputs and no development resource/state dependency.
