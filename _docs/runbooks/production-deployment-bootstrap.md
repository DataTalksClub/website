# Production deployment bootstrap

Status: procedure, ready to execute once its three prerequisites in §3 are closed
Date: 2026-09-03
Scope: turning `DataTalksClub/website`'s release pipeline from "cannot deploy" into "deploys on
push to `main`", against the `website-production` target in AWS account `387546586013`
Audience: an operator with admin on `DataTalksClub/website` and `DataTalksClub/aws-infra`, and
credentials for AWS account `387546586013`

This runbook exists because the owner asked for the post-apply setup to be done *"the same way we
do right now in CMP"*. Section 1 records what CMP actually does, measured rather than assumed, and
says exactly where the website must diverge and why. Sections 2–3 record the live state and the
blockers. Section 4 is the ordered procedure. Section 5 is the failure catalogue, including the
silent credential-exchange failure that is the reason this document was written.

Nothing here was validated against live AWS: `aws sts get-caller-identity` returns 403 from the
authoring environment, so no `terraform plan` was run and every AWS-side claim below is derived
from checked-in Terraform and from `deploy/deployment_targets.py`. GitHub-side claims *were*
measured, with `gh`, on 2026-09-03.

Companion documents: [`development-release.md`](development-release.md) is the sandbox-era release
runbook and describes the destroyed `website-sandbox` stack — read it for the mechanics of the
release controller, not for identifiers.
[`production-hosting-and-dns-migration.md`](production-hosting-and-dns-migration.md) owns DNS and
the apex swap, which this runbook does not touch.

---

## 1. How CMP deploys, and what the website can and cannot copy

Measured in `/home/alexey/git/course-management-platform` and against
`DataTalksClub/course-management-platform` on 2026-09-03. CMP has deployed to production 45 times
(`.prod-versions`), most recently 2026-08-31.

### 1.1 CMP's mechanism

| Question | CMP's answer |
| --- | --- |
| How does it authenticate to AWS? | **Long-lived IAM access keys.** `.github/workflows/deploy-dev.yaml` and `deploy-prod.yaml` pass `secrets.AWS_ACCESS_KEY_ID` / `secrets.AWS_SECRET_ACCESS_KEY` as step env. No OIDC, no `id-token: write`, no `aws-actions/configure-aws-credentials`. |
| Where does that identity come from? | `aws-infra` `main/cmp/iam_deploy.tf` — an `aws_iam_user` named `course-management-ci-cd-deploy-user` plus an `aws_iam_access_key`, whose secret is a Terraform output. |
| Does it use a GitHub environment? | **No.** `gh api repos/DataTalksClub/course-management-platform/environments` returns one environment, `copilot`, auto-created by the Copilot coding agent, with no protection rules. Nothing in either deploy workflow names an environment. |
| What trust policy pins the deploy identity? | None — an IAM user has no trust policy. Authorisation is the inline `ci-cd-user-policy`, scoped to `ecr:*` on the one repository ARN and `ecs:DescribeTaskDefinition` / `RegisterTaskDefinition` / `ListTaskDefinitions` / `UpdateService`. |
| What repository variables does it rely on? | **Zero.** `gh variable list` is empty. Region, account, ECR root and repository URI are literals in the workflow YAML. Everything else — the application's runtime environment — lives in the ECS task definition, managed by Terraform outside the CMP repository. |
| What secrets does it rely on? | Seven, all set by hand: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and five `E2E_DEV_*` values used only by the standalone smoke workflow. |
| What does the deploy actually run? | `deploy/deploy_dev.sh <tag> [env]`: `aws ecs describe-task-definition` → `deploy/update_task_def.py` rewrites the image tag and the `VERSION` env var → `aws ecs register-task-definition` → `aws ecs update-service`. Production is `deploy/deploy_prod.sh`, which reads the tag currently live on dev and calls the same script with `prod`. |
| What does it verify afterwards? | **Nothing.** `update-service` returns and the job ends — the last three production runs took 15, 17 and 20 seconds. There is no wait-for-stable, no health check, no rollback. The Playwright smoke suite (`e2e-smoke-dev.yaml`) runs on a daily cron against **dev only** and is, by its own comment, "intentionally NOT tied to deploys". |
| How is production released? | Manual `workflow_dispatch` with a `confirmProdDeploy` boolean and an optional tag; the workflow then commits the deployed tag to `.prod-versions` and pushes. |

### 1.2 What the website already does the same way

- **Immutable image, promoted not rebuilt.** CMP promotes the exact tag already running on dev.
  The website promotes the exact image *digest* published by the `publish` job. Same principle.
- **A recorded ledger of what is live.** CMP appends to `.prod-versions`. The website writes a
  release record and redacted evidence as run artifacts. Same principle, richer artifact.
- **Region and ECR are pinned, not discovered.** Both fail closed on a wrong physical target;
  CMP by literal, the website by `deploy/deployment_targets.py` comparing every variable against
  the selected target before any role is assumed.

### 1.3 Where the website must diverge, and why

1. **Authentication stays OIDC.** `aws_iam_user` and `aws_iam_access_key` are rejected by the
   `aws-infra` website delivery policy suite (`tests/policy/test_website_terraform_delivery_policy.py`
   enumerates both as forbidden resource types). `main/cmp` predates that suite and is
   grandfathered; new work is not. Copying CMP's identity model is not available to us, and it is
   the one thing about CMP's deployment that should not be treated as the reference.

   **Finding, recorded deliberately:** CMP holds a long-lived IAM access key in GitHub secrets,
   created 2026-03-11 and not rotated since. That is a standing item for CMP, out of scope here.
   No key material appears in this document and none should be copied into the website repository.

2. **The website deploy job must name a GitHub environment; CMP's must not.** This is the direct
   consequence of (1). With OIDC, the *only* thing distinguishing a deploy-capable token from a
   build-capable one is the `sub` claim, and the website's deployer role trusts exactly
   `repo:<immutable-repo>:environment:production`
   (`aws-infra` `modules/django-website/deployment-iam.tf`, `github_deployer_subject`). The
   environment is not a convenience; it is the authorisation boundary. CMP has no equivalent
   because its key works from anywhere.

3. **The website verifies after mutation; CMP does not.** `deploy/smoke.py` asserts the exact
   footer version string, the byte-exact `<link rel="canonical" href="https://datatalks.club/">`
   on `/` and `.../courses` on `/courses`, `noindex` on every checked route, the `/studio/`
   redirect to `/accounts/login/?next=%2Fstudio%2F`, absence of debug/404 tokens, and absence of
   production analytics. `deploy.cli promote` runs the migration task, rolls both services, runs
   that smoke, and rolls back on failure. This is not optional polish — it is what makes an
   automatic push-to-deploy pipeline safe, which CMP's 15-second fire-and-forget is not.

### 1.4 What the website should adopt from CMP

- **CMP's dev-then-promote split is the safer shape and the website does not have it.** CMP never
  deploys straight to production: `main` pushes go to dev, and production is a separate,
  human-confirmed dispatch of an already-proven tag. The website's deploy pipeline goes
  from push-to-`main` to production in one hop once `DEVELOPMENT_AUTO_DEPLOY` is `true`. PR #44
  records that `main/dev` has no deployment pipeline at all and that adding one needs a second
  GitHub environment and a second reviewed target in `DEPLOYMENT_TARGETS`. Until that exists,
  §4 step 13 deliberately leaves `DEVELOPMENT_AUTO_DEPLOY` at `false` longer than the minimum, and
  the environment gets a required reviewer (§4 step 8) so a human still confirms each release —
  the closest available equivalent of CMP's `confirmProdDeploy` checkbox.
- **CMP's `.prod-versions` file is a cheap, greppable answer to "what is live?"** The website's
  equivalent lives only in run artifacts, which expire. Worth a follow-up issue; not a blocker.

---

## 2. Measured live state of `DataTalksClub/website`

All rows read with `gh` on 2026-09-03. Repository is **public**, id `1326548167`, owner id
`72699292` — which fixes the immutable OIDC subject prefix at
`repo:DataTalksClub@72699292/website@1326548167`.

| Thing | State |
| --- | --- |
| Repository variables | **7**, of which 6 are stale `website-sandbox` values naming the destroyed account `817685572750` (`DEVELOPMENT_AWS_REGION`, `DEVELOPMENT_ECR_REPOSITORY_NAME`, `DEVELOPMENT_ECR_REPOSITORY_URI`, `DEVELOPMENT_KMS_KEY_ARN`, `DEVELOPMENT_PUBLISHER_ROLE_ARN`, `DEVELOPMENT_ROUTE53_HOSTED_ZONE_ID`). The seventh is `DEVELOPMENT_AUTO_DEPLOY = false`. |
| Repository secrets | **None.** Correct — `ci.yml` contains no `secrets.` reference at all. |
| `production` environment | **Already exists**, created 2026-09-02T19:39:29Z. **0 variables, 0 secrets, 0 protection rules, no deployment branch policy.** |
| `sandbox` environment | Exists since 2026-08-07. Holds **18 environment-scoped variables**, all pointing at the destroyed account. One protection rule: a deployment branch policy allowing only `main`. |
| OIDC subject customisation | `use_default: true`, **`use_immutable_subject: false`**, `sub_claim_prefix: repo:DataTalksClub@72699292/website@1326548167`. |
| Deploying workflow | `.github/workflows/ci.yml` only. `content-update.yml` and `scheduled-full-regression.yml` assume no AWS role. |

The `production` environment existing already means step 7 of §4 is a **verification**, not a
creation. Do not skip it: an environment with no branch policy accepts a deployment from any ref
that reaches the job, which is weaker than the `sandbox` environment it replaces.

---

## 3. Three blockers that must be closed first

These are not optional preparation. With any one of them open the pipeline fails, and two of the
three fail in the misleading way described in §5.1.

### 3.1 `ci.yml` requests the wrong GitHub environment — **blocking**

`.github/workflows/ci.yml` hardcodes `environment: name: sandbox` in all four environment-scoped
jobs: `auto-capture-prior` (line 1365), `deploy` (line 1758), `probe-deployer` (line 2370) and
`probe-wrong-environment-claim` (line 2518). The selected target's environment is `production`:

```
$ uv run --frozen python -c "from deploy.deployment_targets import SELECTED_TARGET as t; print(t.github_environment)"
production
```

Nothing ties the workflow's literal to that value, so the two drifted when the target was
re-pointed at `website-production`. A job declaring `environment: sandbox` receives the subject
`repo:DataTalksClub@72699292/website@1326548167:environment:sandbox`, and the deployer role's
trust policy admits only `...:environment:production`. **Creating or hardening the `production`
environment does nothing while this stands** — the workflow never asks for it.

Required change, in a separate reviewed commit before any of §4:

- Replace all four `name: sandbox` with `name: production` in `.github/workflows/ci.yml`.
- Update `core/tests/test_deployment_workflow.py:230`, which currently asserts
  `self.assertIn("name: sandbox", workflow)` and will fail on the corrected workflow. It should
  assert the workflow's environment literal equals `SELECTED_TARGET.github_environment`, so the
  two cannot drift apart again — that is the actual defect, not the literal.

Do not "fix" this from the Terraform side by setting `github_deployment_environment = "sandbox"`.
`main/website/terraform.tfvars.example` sets `production`, `deployment_targets.py` says
`production`, and the resource namespace is `website-production`; the workflow is the odd one out.

### 3.2 Stale `sandbox` environment variables shadow the correct repository variables — **blocking**

GitHub resolves `vars.X` from the job's environment first and the repository second. The `sandbox`
environment holds 18 `DEVELOPMENT_*` variables naming account `817685572750`. Until §4 step 6
deletes them, any job that runs with `environment: sandbox` reads the destroyed stack's identifiers
**even after the 25 correct repository variables are set**, and the repository-level values are
silently ignored in exactly the jobs that matter.

This is caught, loudly, by `uv run --frozen python -m deploy.legacy_development_compatibility deployer`
early in the `deploy` job — see §5.2 — but only after a wasted build. Delete the variables.

### 3.3 Immutable OIDC subject claims are off — **blocking**

`use_immutable_subject` is `false`. Tokens therefore carry
`repo:DataTalksClub/website:environment:production`, and the trust policy pins the
`owner@owner_id/name@repo_id` form. Every role assumption fails. §4 step 9 turns it on.

### 3.4 One non-blocker worth knowing: the `probe` operation is retired

`ci.yml`'s `operation: probe` path cannot be used as a production pre-flight.
`deploy/oidc_probe.py` hardcodes `SANDBOX_ACCOUNT_ID = "817685572750"`, the sandbox state bucket
and key, the sandbox hosted zone and KMS key, and the role names
`website-sandbox-github-{publisher,deployer}`; `deploy/oidc_claim_probe.py:24` compiles a
`ROLE_PATTERN` that matches only those two sandbox ARNs. Against production role ARNs the probe
refuses its own inputs. Re-pointing the probes at the selected target is a real follow-up; until
then the first manual `promote` in §4 step 12 is the pre-flight, and §4 step 11 is the offline
substitute for the claim probe.

---

## 4. The procedure

Run the steps in order. Each says how to tell it worked and what failure looks like.

Conventions: `WEBSITE` is a clean checkout of `DataTalksClub/website` at `main`; `INFRA` is a clean
checkout of `DataTalksClub/aws-infra` at `main`. Commands are written to be run from the named
repository root.

### Step 0 — land the `ci.yml` environment fix (§3.1)

Not part of the aws-infra merge; it is a website change and goes through the normal issue
lifecycle in `_docs/PROCESS.md`.

**Worked when:**

```bash
grep -c "name: production" .github/workflows/ci.yml   # -> 4
grep -c "name: sandbox"    .github/workflows/ci.yml   # -> 0
uv run --frozen python manage.py test core.tests.test_deployment_workflow
```

**Failed when:** the workflow test reports `AssertionError: 'name: sandbox' not found in ...` —
that is `test_deployment_workflow.py:230`, and it means the assertion was not updated with the
workflow.

### Step 1 — merge aws-infra #43, then #44

Order matters only in that #43 must land before the first release. #44's base,
`integration/website-deployment-contract`, is not merely a snapshot of `main` — it is the *same
commit*, `6d9ad764c65387faf8779f06364e9b642ea2fe00`, with an empty diff in both directions. So
retarget #44 at `main` rather than merging a redundant branch. If `main` moves before #44 lands,
the two diverge and the retarget becomes a real rebase; do this promptly.

```bash
cd "$INFRA"
gh pr merge 43 --merge --repo DataTalksClub/aws-infra
gh pr edit  44 --base main --repo DataTalksClub/aws-infra
gh pr merge 44 --merge --repo DataTalksClub/aws-infra
git checkout main && git pull
```

#43 first because it is the smaller change and #44's own description confirms the two do not
conflict — #43 touches only `main/website/terraform.tfvars.example`, #44 touches `outputs.tf`,
`production.tftest.hcl` and `README.md`.

**Worked when:** on `main`,
`grep CANONICAL_ORIGIN main/website/terraform.tfvars.example` shows `https://datatalks.club`, and
`grep -c DEVELOPMENT_ main/website/outputs.tf` is non-zero.

**Failed when:** `CANONICAL_ORIGIN` still reads `https://prod.datatalks.club`. Do not proceed —
`deploy/smoke.py` asserts the apex canonical byte-exactly on `/` and `/courses`, so the first
release would deploy successfully and then fail its own smoke, triggering a rollback.

The remaining steps split into two lanes that are independent of each other until step 11.
**Lane A** (steps 2–5) is AWS and needs credentials for account `387546586013`. **Lane B**
(steps 6–10) is GitHub and needs repository admin. Run them in parallel if two people are
available; the order *within* each lane is fixed.

---

**Lane A — infrastructure.**

There is no CI workflow that applies `main/website`. `terraform_delivery_enabled` is `false` for
this root, because the shared module pins its apply environment to the literal `development`,
which is the wrong trust boundary for production (`main/website/README.md`, "Known gaps"). The
repository's `website-terraform.yml` workflows target `sandbox/website` in the *other* account
(`817685572750`) and have nothing to do with this. **Every apply below is operator-driven from a
workstation.**

### Step 2 — apply `main/website`

The root has already been applied once with `services_enabled = false`. This apply is to
materialise the two outputs #44 adds; new outputs are written to state by an apply, not by
`terraform output` against stale state.

```bash
cd "$INFRA"
cp main/website/backend.hcl.example main/website/backend.hcl     # first time only
# substitute the account id: bucket = dtc-terraform-state-387546586013
cp main/website/terraform.tfvars.example main/website/terraform.tfvars   # first time only
# fill the value marked REPLACE: alarm_action_arns (the shared operator SNS topic)

terraform -chdir=main/website init -backend-config=backend.hcl
terraform -chdir=main/website plan        # read it before applying
terraform -chdir=main/website apply
```

State lives in `s3://dtc-terraform-state-387546586013/main/website/terraform.tfstate`, `eu-west-1`,
with S3-native locking (`use_lockfile = true`) — there is no DynamoDB lock table. `terraform.tfvars`
is auto-loaded, so no `-var-file` is needed; `-var-file=terraform.tfvars.example` appears in the
README only for the offline `terraform test` check.

The root's own prerequisites — the shared operator SNS topic, `main/common`'s public hosted zone,
and an apply that pauses on ACM DNS validation — are in `main/website/README.md`. Do not
substitute this runbook for it.

**Worked when:**

```bash
terraform -chdir=main/website output -json github_repository_variables | jq 'keys | length'   # -> 25
terraform -chdir=main/website output -json github_repository_variables \
  | jq -r '.DEVELOPMENT_DEPLOYER_ROLE_ARN'
# -> arn:aws:iam::387546586013:role/website-production-github-deployer
```

**Failed when:** `Output "github_repository_variables" not found in state` — #44 did not land, or
this workspace was never applied after it did. Re-run step 1, then apply.

### Step 3 — populate the secret containers

Terraform creates Secrets Manager *containers* and never values. Seven exist for this stack: six
module-owned, plus one created by the root.

| Container | Owner | Wired into ECS? |
| --- | --- | --- |
| `website-production/database-url` | module | **yes** — blocks task start if empty |
| `website-production/django-secret-key` | module | **yes** — blocks task start if empty |
| `website-production/github` | module | no |
| `website-production/integrations` | module | no |
| `website-production/oidc` | module | no |
| `website-production/webhook` | module | no |
| `website-production/origin-verify` | root (`main/website/secrets.tf`) | no |

Only the first two are referenced by a task definition `valueFrom`, so only those two can stop a
task from reaching `RUNNING`. The other five are still required for the application to function
and must be populated before the site is considered live.

Set each with `aws secretsmanager put-secret-value` from an operator session. **Never** put a
value in this repository, an issue, a log, a screenshot or a report.

**Worked when:** every container reports an `AWSCURRENT` version:

```bash
aws secretsmanager list-secrets --region eu-west-1 \
  --filters Key=name,Values=website-production/ \
  --query 'SecretList[].{name:Name,last:LastChangedDate}' --output table
```

Seven rows, each with a `last` value.

**Failed when:** a container has no version. This does not fail the *deployment* — it fails the
*task*, later, as `ResourceInitializationError: unable to pull secrets or registry auth` on a
stopped task. The migration task hits it first, so the release stops at the migration phase before
either service is touched. See §5.3.

### Step 4 — push the first ARM64 image by hand

`services_enabled = true` requires a real `container_image_digest`, which requires an image in
ECR, which the pipeline cannot supply because the pipeline will not run until the services are up.
Break the cycle by pushing one image manually — which has the useful side effect of proving ECR
access before the pipeline is trusted with it.

The stack is Graviton. The architecture is derived from one Terraform value, so read it rather
than assuming:

```bash
cd "$WEBSITE"
uv run --frozen python -m deploy.deployment_targets architecture
# task_cpu_architecture=ARM64
# build_platform=linux/arm64
# image_architecture=arm64
# runner_machine=aarch64
# runner_label=ubuntu-24.04-arm

sha="$(git rev-parse origin/main)"
uri=387546586013.dkr.ecr.eu-west-1.amazonaws.com/website-production

aws ecr get-login-password --region eu-west-1 \
  | docker login --username AWS --password-stdin "${uri%%/*}"
docker buildx build --platform linux/arm64 --provenance=false --push -t "$uri:$sha" .
```

**Worked when:** the manifest is a single `arm64` image, and you have its digest:

```bash
aws ecr describe-images --region eu-west-1 --repository-name website-production \
  --image-ids imageTag="$sha" \
  --query 'imageDetails[0].{digest:imageDigest,arch:imageManifestMediaType}'
```

**Failed when:** `docker buildx` produced a multi-arch index rather than a single image — with
`--provenance=false` it should not, but if it did, the `image_digest` you record below points at
an index and ECS will pull the wrong architecture. The symptom is a task that starts and then
dies immediately with `exec format error`.

### Step 5 — pin the image and enable the services

In `main/website/terraform.tfvars`, replace the placeholders shipped in
`terraform.tfvars.example` — `container_image_digest = "sha256:1111…"` and
`source_commit_sha = "0000…"` — with the digest from step 4 and the matching 40-character commit,
then flip `services_enabled = true`.

```hcl
container_image_digest = "sha256:<64 hex from step 4>"
source_commit_sha      = "<40 hex, the commit that built it>"
services_enabled       = true
```

```bash
terraform -chdir=main/website plan
terraform -chdir=main/website apply
```

`services_enabled` gates only `desired_count`, never resource existence: the cluster, both
services, all three task families, ECR, IAM, ALB, target groups, RDS, CloudFront and KMS were all
created by the earlier apply and have been sitting at zero tasks. This apply is what moves web to
2 and worker to 1, matching `DEVELOPMENT_WEB_RELEASE_DESIRED_COUNT` and
`DEVELOPMENT_WORKER_RELEASE_DESIRED_COUNT`.

It also flips the running-task CloudWatch alarms from `notBreaching` to live, so a task that
cannot start will now page.

**Worked when:** both services report `runningCount` equal to `desiredCount`, and the site
answers:

```bash
aws ecs describe-services --region eu-west-1 --cluster website-production \
  --services website-production-web website-production-worker \
  --query 'services[].{name:serviceName,desired:desiredCount,running:runningCount}' --output table
curl -fsS https://prod.datatalks.club/api/health/ | jq .
```

**Failed when:** `plan` refuses with a validation error on `services_enabled`. The root rejects
`true` while either runtime variable still holds its placeholder — that is the guard working, not
a bug. If instead the plan succeeds but tasks never reach `RUNNING`, it is step 3 (see §5.3) or
step 4's architecture (see above).

Do **not** leave `services_enabled = false` and let the pipeline scale the services up instead.
The release sets desired counts with `update-service`, and the next `terraform apply` would put
them back to zero — Terraform and the pipeline would fight, and Terraform would win at the least
convenient moment.

---

**Lane B — GitHub configuration.**

### Step 6 — delete the stale `sandbox` environment variables (§3.2)

```bash
for name in $(gh api repos/DataTalksClub/website/environments/sandbox/variables \
                --paginate --jq '.variables[].name'); do
  gh api --method DELETE "repos/DataTalksClub/website/environments/sandbox/variables/$name"
done
```

Leave the `sandbox` *environment* itself in place for now — its deployment activity log is the
record of the Gate-B evidence this repository still carries. Retiring it belongs with retiring the
`website-sandbox` profile in `deploy/deployment_targets.py`, which is a separate change.

**Worked when:**

```bash
gh api repos/DataTalksClub/website/environments/sandbox/variables --jq '.total_count'   # -> 0
```

**Failed when:** the count is still 18. The most likely cause is a token without `repo` admin
scope; `gh` reports `HTTP 403` per variable rather than failing the loop, so check the count, not
the loop's exit status.

### Step 7 — verify the `production` environment exists

It was created 2026-09-02, so this is a check, not a creation.

```bash
gh api repos/DataTalksClub/website/environments/production \
  --jq '{name, protection_rules, deployment_branch_policy}'
```

If it is ever missing, create it with:

```bash
gh api --method PUT repos/DataTalksClub/website/environments/production
```

**Worked when:** the call returns `"name": "production"` and HTTP 200.

**Failed when:** HTTP 404. Note that GitHub will also create an environment implicitly the first
time a workflow references it, which means a missing environment does *not* announce itself — the
job runs, an empty environment appears, and the failure surfaces one step later as §5.1.

### Step 8 — give `production` the protection CMP does not have

CMP has no environment and therefore no model to copy here. Copy the website's own `sandbox`
environment instead: it restricts deployments to `main`, which is the property that matters, since
a deploy token minted from any other ref would still satisfy the trust policy's environment pin.

```bash
gh api --method PUT repos/DataTalksClub/website/environments/production \
  -F "deployment_branch_policy[protected_branches]=false" \
  -F "deployment_branch_policy[custom_branch_policies]=true"

gh api --method POST repos/DataTalksClub/website/environments/production/deployment-branch-policies \
  -f name=main -f type=branch
```

Add a required reviewer as well, as the standing substitute for CMP's `confirmProdDeploy`
checkbox until the website has a dev-then-promote split (§1.4). Replace `<USER_ID>` with the
numeric id from `gh api users/<login> --jq .id`:

```bash
gh api --method PUT repos/DataTalksClub/website/environments/production \
  -F "reviewers[][type]=User" -F "reviewers[][id]=<USER_ID>" \
  -F "deployment_branch_policy[protected_branches]=false" \
  -F "deployment_branch_policy[custom_branch_policies]=true"
```

A required reviewer pauses every `deploy` job for approval. That is intended while
`DEVELOPMENT_AUTO_DEPLOY` is being armed; remove the reviewer only once a dev target exists and
production is genuinely a promotion of something already proven.

**Worked when:** `protection_rules` contains an entry of type `branch_policy` (and
`required_reviewers` if you added one), and the branch-policy list contains exactly `main`:

```bash
gh api repos/DataTalksClub/website/environments/production/deployment-branch-policies \
  --jq '[.branch_policies[].name]'    # -> ["main"]
```

**Failed when:** `deployment_branch_policy` is `null`. The `PUT` and the `POST` are two separate
calls and the `POST` fails with HTTP 404 if the `PUT` was skipped — the policy list cannot be
populated before the policy mode is enabled.

### Step 9 — enable immutable OIDC subject claims (§3.3)

```bash
gh api --method PUT repos/DataTalksClub/website/actions/oidc/customization/sub \
  -F use_default=true -F use_immutable_subject=true
```

If the API rejects the field, set it in the UI: **Settings → Actions → General → OpenID Connect →
use immutable subject claims**. The organisation has no OIDC subject template of its own
(`gh api orgs/DataTalksClub/actions/oidc/customization/sub` → HTTP 404), so the repository setting
is authoritative and there is no org-level default to fight.

**Worked when:**

```bash
gh api repos/DataTalksClub/website/actions/oidc/customization/sub
# {"use_default":true,"use_immutable_subject":true,
#  "sub_claim_prefix":"repo:DataTalksClub@72699292/website@1326548167"}
```

`sub_claim_prefix` must read `repo:DataTalksClub@72699292/website@1326548167`. Those two numbers
are the repository's immutable owner id and repository id and must match
`github_repository_owner_id = "72699292"` and `github_repository_id = "1326548167"` in
`main/website/terraform.tfvars.example`.

**Failed when:** `use_immutable_subject` is still `false`. There is no runtime error for this —
the workflow proceeds normally and dies at §5.1.

### Step 10 — set the 25 published variables

Requires Lane A step 2. This is #44's loop verbatim; it transcribes nothing.

```bash
cd "$INFRA"
terraform -chdir=main/website output -json github_repository_variables \
  | jq -r 'to_entries[] | [.key, .value] | @tsv' \
  | while IFS=$'\t' read -r name value; do
      gh variable set "$name" --repo DataTalksClub/website --body "$value"
    done
```

These go at **repository** level, not on the `production` environment, and that is deliberate: the
`publish` job and the `resolve-release` job read `DEVELOPMENT_ECR_REPOSITORY_URI`,
`DEVELOPMENT_ECR_REPOSITORY_NAME`, `DEVELOPMENT_AWS_REGION` and
`DEVELOPMENT_PUBLISHER_ROLE_ARN` while declaring **no** environment, so environment-scoped values
would be invisible to them. None of the 25 is secret.

Re-run this loop after any later apply that replaces a subnet, security group, target group or KMS
key. A stale AWS-generated identifier fails the release rather than being silently ignored.

**Worked when:** the variable list is 26 rows (25 + the pre-existing `DEVELOPMENT_AUTO_DEPLOY`),
and no row names the destroyed account:

```bash
gh variable list -R DataTalksClub/website | wc -l          # -> 26
gh variable list -R DataTalksClub/website | grep -c 817685572750   # -> 0
```

Spot-check the values that the pipeline compares byte-exactly against
`deploy/deployment_targets.py`:

| Variable | Required value |
| --- | --- |
| `DEVELOPMENT_AWS_REGION` | `eu-west-1` |
| `DEVELOPMENT_BASE_URL` | `https://prod.datatalks.club` |
| `DEVELOPMENT_ECR_REPOSITORY_NAME` | `website-production` |
| `DEVELOPMENT_ECR_REPOSITORY_URI` | `387546586013.dkr.ecr.eu-west-1.amazonaws.com/website-production` |
| `DEVELOPMENT_PUBLISHER_ROLE_ARN` | `arn:aws:iam::387546586013:role/website-production-github-publisher` |
| `DEVELOPMENT_DEPLOYER_ROLE_ARN` | `arn:aws:iam::387546586013:role/website-production-github-deployer` |
| `DEVELOPMENT_ECS_TASK_ROLE_ARN` | `arn:aws:iam::387546586013:role/website-production-task-application` |
| `DEVELOPMENT_ECS_EXECUTION_ROLE_ARN` | `arn:aws:iam::387546586013:role/website-production-task-execution` |
| `DEVELOPMENT_ECS_CLUSTER_ARN` | `arn:aws:ecs:eu-west-1:387546586013:cluster/website-production` |
| `DEVELOPMENT_ECS_WEB_SERVICE_NAME` | `website-production-web` |
| `DEVELOPMENT_ECS_WORKER_SERVICE_NAME` | `website-production-worker` |
| `DEVELOPMENT_ECS_WEB_TASK_FAMILY` | `website-production-web` |
| `DEVELOPMENT_ECS_WORKER_TASK_FAMILY` | `website-production-worker` |
| `DEVELOPMENT_ECS_MIGRATION_TASK_FAMILY` | `website-production-migration` |
| `DEVELOPMENT_ECS_CONTAINER_NAMES` | `{"migration":"migration","web":"web","worker":"worker"}` |
| `DEVELOPMENT_ECS_ASSIGN_PUBLIC_IP` | `false` |
| `DEVELOPMENT_WEB_RELEASE_DESIRED_COUNT` | `2` |
| `DEVELOPMENT_WORKER_RELEASE_DESIRED_COUNT` | `1` |
| `DEVELOPMENT_RESOURCE_PROJECT_TAG` | `website` |
| `DEVELOPMENT_RESOURCE_ENVIRONMENT_TAG` | `production` |

That table is not transcribed by hand — reproduce it at any time with:

```bash
cd "$WEBSITE"
uv run --frozen python -c "
from deploy.deployment_targets import SELECTED_TARGET as t, role_profile_expectations
for k, v in sorted(role_profile_expectations('deployer', t).items()):
    print(f'{k}={v}')"
```

The remaining five (`ECS_SUBNET_IDS`, `ECS_SECURITY_GROUP_IDS`, `WEB_TARGET_GROUP_ARN`,
`KMS_KEY_ARN`, `ROUTE53_HOSTED_ZONE_ID`) are AWS-generated. The pipeline shape-checks them against
this account, region and namespace rather than pinning literals, so they cannot be listed here.

**Failed when:** the count is 25 or fewer, or the `817685572750` grep is non-zero. A partial run
leaves a mixture of production and sandbox values, which is the worst state to be in — re-run the
whole loop, it is idempotent.

---

**Both lanes complete.**

### Step 11 — offline claim pre-flight

The `probe` operation is retired (§3.4), so verify the claim contract without it. Read the role's
trust policy and compare it, character for character, with what a `production`-environment token
will carry:

```bash
aws iam get-role --role-name website-production-github-deployer \
  --query 'Role.AssumeRolePolicyDocument' --output json \
  | jq -r '.Statement[].Condition.StringEquals."token.actions.githubusercontent.com:sub"'
# expect: repo:DataTalksClub@72699292/website@1326548167:environment:production
```

Build the expected string independently and diff the two:

```bash
prefix=$(gh api repos/DataTalksClub/website/actions/oidc/customization/sub --jq .sub_claim_prefix)
env=$(grep -A1 '^    environment:$' .github/workflows/ci.yml | grep 'name:' | head -1 | awk '{print $2}')
echo "$prefix:environment:$env"
```

**Worked when:** the two strings are identical. Also check the publisher, which has no
environment and therefore carries the ref form:

```bash
aws iam get-role --role-name website-production-github-publisher \
  --query 'Role.AssumeRolePolicyDocument' --output json \
  | jq -r '.Statement[].Condition.StringEquals."token.actions.githubusercontent.com:sub"'
# expect: repo:DataTalksClub@72699292/website@1326548167:ref:refs/heads/main
```

**Failed when:** they differ in any way. The three differences seen in practice are: the plain
`DataTalksClub/website` form (step 9 not done), `:environment:sandbox` (step 0 not done), and a
role name still carrying `website-sandbox` (step 10 not done, or a stale variable from step 6).

### Step 12 — first release, manually dispatched

Do **not** arm auto-deploy first. Dispatch one release by hand so a human is present for the
credential exchange, the migration and the smoke.

```bash
cd "$WEBSITE"
gh workflow run ci.yml --repo DataTalksClub/website --ref main \
  -f release_sha="$(git rev-parse origin/main)" \
  -f deploy_development=true \
  -f probe_development=false \
  -f operation=promote \
  -f failure_injection=none \
  -f reuse_existing_image=false
gh run watch --repo DataTalksClub/website "$(gh run list --repo DataTalksClub/website \
  --workflow ci.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
```

`release_sha` must be the full 40-character commit and must be the current tip of `main` — the
`deploy` job re-checks `git rev-parse HEAD == origin/main == release_sha` three separate times,
before OIDC, before the recovery checkpoint, and before mutation.

**Worked when:** the run is green through `deploy`, the run summary carries the deployment
evidence artifact, and the site answers with the deployed version and the apex canonical:

```bash
curl -fsS https://prod.datatalks.club/api/health/ | jq .
curl -fsS https://prod.datatalks.club/ | grep -o '<link rel="canonical"[^>]*>'
# expect exactly: <link rel="canonical" href="https://datatalks.club/">
```

Note that `prod.datatalks.club` is `noindex` for its whole life by design; the apex canonical is
correct there and is what #43 fixes.

**Failed when:** see §5. The most likely first failure by a wide margin is §5.1.

### Step 13 — arm auto-deploy, last

Only after step 12 has succeeded end to end at least once.

```bash
gh variable set DEVELOPMENT_AUTO_DEPLOY --repo DataTalksClub/website --body true
```

**Worked when:** `gh variable get DEVELOPMENT_AUTO_DEPLOY -R DataTalksClub/website` prints `true`,
and the next push to `main` runs `auto-capture-prior`, `publish` and `deploy` rather than stopping
at `ci-gate`.

**Failed when:** the body was set to `True`, `1` or `yes`. The gate is a string comparison —
`vars.DEVELOPMENT_AUTO_DEPLOY == 'true'` at `ci.yml` lines 1360, 1504 and 1750 — and anything else
silently skips all three deployment jobs. A run that is green but has no `deploy` job is this, not
success.

To disarm at any time, set it to `false`. That is the kill switch; it needs no code change and no
apply.

---

## 5. Failure catalogue

### 5.1 The silent one: credential exchange

**Symptom.** Every job is green. The `deploy` job (or `auto-capture-prior`, which reaches OIDC
first) fails on the step named `Configure AWS Credentials` — the `aws-actions/configure-aws-credentials@v4`
step — with:

```
Error: Could not assume role with OIDC: Not authorized to perform sts:AssumeRoleWithWebIdentity
```

**Why it misleads.** The message names an IAM action and reads like a missing permission on the
role's *policy*. It is not. The role's policy was never consulted: STS rejected the token at the
*trust* boundary because the `sub` claim did not match, and STS deliberately does not report which
claim differed or what it expected. Nothing in the GitHub run shows the subject that was presented.
An operator reading only the run will conclude the deployer role is under-privileged and start
widening its policy, which cannot fix it.

**The three causes, in the order they occur:**

| Presented subject | Cause | Fix |
| --- | --- | --- |
| `repo:DataTalksClub/website:environment:production` | Immutable subject claims off | §4 step 9 |
| `repo:DataTalksClub@72699292/website@1326548167:environment:sandbox` | `ci.yml` still names `sandbox` | §4 step 0 |
| Correct subject, wrong role ARN | A stale `DEVELOPMENT_DEPLOYER_ROLE_ARN` | §4 steps 6 and 10 |

**How to see the presented subject.** With AWS access, CloudTrail records the failed call —
`AssumeRoleWithWebIdentity` events carry the OIDC subject in `userIdentity.userName`:

```bash
aws cloudtrail lookup-events --region eu-west-1 --max-results 10 \
  --lookup-attributes AttributeKey=EventName,AttributeValue=AssumeRoleWithWebIdentity \
  --query 'Events[].CloudTrailEvent' --output text \
  | jq -r 'select(.errorCode != null) | [.eventTime, .errorCode, .userIdentity.userName] | @tsv'
```

Without AWS access, reconstruct it offline with the two commands in §4 step 11 — that comparison
distinguishes all three causes without a single AWS call.

### 5.2 The loud one: physical-target mismatch

**Symptom.** The `deploy` job fails *before* OIDC, on the step
`uv run --frozen python -m deploy.legacy_development_compatibility deployer`, with:

```
Deployment target validation failed safely: website-production configuration selects
unexpected physical values: DEPLOYER_ROLE_ARN, ECS_CLUSTER_ARN, ...
```

or, if variables are absent rather than wrong:

```
Deployment target validation failed safely: website-production configuration is missing
required values: ECS_SUBNET_IDS, WEB_TARGET_GROUP_ARN
```

**Meaning.** `deploy/deployment_targets.py` compared each variable against the selected target
before any credential was requested, and refused. This is the good failure mode: it names the
variables. Fix them in §4 step 10 and re-dispatch. If it names variables that step 10 set correctly,
the values are being shadowed by the `sandbox` environment (§3.2) and step 6 was skipped.

### 5.3 Migration-phase failure

**Symptom.** The release reaches the migration task and stops there; neither service is updated.
Common cause is an unpopulated secret container (§4 step 3), surfacing as
`ResourceInitializationError: unable to pull secrets or registry auth` on the stopped task.

**Meaning.** Nothing was mutated. The site is untouched. Fix the secret and re-dispatch.

### 5.4 Post-mutation smoke failure

**Symptom.** Services roll, then `deploy` fails with a `ReleaseContractError` from
`deploy/smoke.py`, and the controller runs its recovery path.

**The two to expect on a first production release:**

- `home page production canonical differs` — #43 did not land, so `CANONICAL_ORIGIN` is still
  `https://prod.datatalks.club` and the emitted `<link rel="canonical">` is not the apex.
- `home page footer lacks the exact version` — the running task is not the image just published,
  which means `update-service` succeeded but the old task definition is still serving. Check the
  ECS service's deployment list.

**Meaning.** The site was mutated and then rolled back. Read the evidence artifact on the run
before re-dispatching.

### 5.5 A green run with no `deploy` job

Not a failure of the pipeline — a gate that did not open. Either `DEVELOPMENT_AUTO_DEPLOY` is not
exactly the string `true` (§4 step 13), or the run was a `workflow_dispatch` with
`deploy_development=false`, or the ref is not `refs/heads/main`. All three deployment jobs carry
`github.ref == 'refs/heads/main'` in their `if:`.

---

## 6. Open items this runbook does not close

- **The website has no dev-then-promote split.** CMP's is the safer shape (§1.4). Adding one needs
  a second GitHub environment and a second reviewed target in `DEPLOYMENT_TARGETS`; PR #44 records
  it as deliberate follow-up. The required reviewer in §4 step 8 is a stopgap.
- **The OIDC probes are pinned to the destroyed sandbox** (§3.4). Re-pointing
  `deploy/oidc_probe.py` and `deploy/oidc_claim_probe.py` at `SELECTED_TARGET` would restore a
  real non-mutating pre-flight and make §4 step 11 unnecessary.
- **There is no `.prod-versions` equivalent.** What is live is recoverable only from expiring run
  artifacts.
- **CMP holds an unrotated long-lived IAM access key** created 2026-03-11
  (`aws-infra` `main/cmp/iam_deploy.tf`). Out of scope here, recorded so it is not lost.
- **Nothing in §4 steps 2–5, 11 or 12 was validated against live AWS.** The authoring environment
  had no credentials, so no `terraform plan` was possible and the AWS-side commands are derived
  from checked-in Terraform, not observed.
