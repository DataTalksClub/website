# Database rotation and development release handoff

Date: 2026-08-26

## Status

- The website checkout is on `main`, fast-forwarded to `origin/main` at `46a92b9`, and is clean.
- The original merge request was already satisfied: the seven local commits were duplicates of rewritten upstream history and there was nothing additional to push from that branch.
- Current `origin/main` did not finish deploying. CI run `32707099538` passed source verification and publishing but failed during deployment with a migration/release contract error.
- `/health/live` continued reporting the old release `20260821-062421-11b2bd1`; `/health/readiness` failed closed with database unavailability.
- Automatic deployment was deliberately disabled by setting the repository variable `DEVELOPMENT_AUTO_DEPLOY=false` while investigating. It must be restored explicitly after repair.

## Root cause

The database master password rotated because RDS-managed master credentials were enabled for `website-sandbox`: Terraform sets `manage_master_user_password = true`, producing an approximately weekly rotation. The application receives `DATABASE_URL` from a separate workload-owned Secrets Manager entry named `website-sandbox/database-url`. That derived URL was not updated when RDS rotated the master credential, so running tasks retained a stale password and lost database connectivity.

This repeats the failure mode previously recovered manually in issue #160. GitHub issue #191 owns the durable fix.

## AWS access

AWS access initially returned HTTP 403 through the temporary gate. After the gate was opened, `aws sts get-caller-identity` succeeded. It authenticated as a temporary session using `phone-aws-sandbox-role` in account `817685572750`. That proves the gate/session path works; it does not itself prove every RDS mutation is authorized.

No secret values were read, displayed, or recorded during this investigation.

## Rotation discussion and open decision

The initial operational preference was to stop rotating the database password and continue storing the application secret in AWS, injecting it into containers as today. That would address the immediate drift but weakens the security posture compared with managed rotation.

A later direction allowed rotation to remain if it is fully automated and cannot recreate the current outage. The concern is valid: simply rotating a secret does not replace already-running ECS tasks, and those tasks can continue holding credentials that become invalid at reconnect time.

No implementation choice was recorded as final. The viable approaches are:

1. Disable RDS-managed rotation and keep the existing static AWS-secret injection. This is simplest operationally but requires documented manual rotation and detection controls.
2. Keep managed rotation and make tasks consume the RDS-managed credential directly at startup, with an automatic ECS service refresh/replacement triggered after successful rotation.
3. Keep managed rotation and automatically synchronize the existing `website-sandbox/database-url` within a bounded window before forcing task replacement.

Options 2 and 3 preserve regular rotation but require an explicit post-rotation task-refresh mechanism. Merely pointing ECS at the rotating secret prevents newly launched tasks from starting stale, but it does not solve the stale-credential risk for long-running web and worker tasks.

## Worktree state

The AWS infrastructure investigation used a detached, read-only inspection worktree under the website repository's `.tmp/aws-infra-191-origin-main`. Its purpose was isolation: inspect the exact deployed infrastructure revision without modifying the normal working checkout.

That scratch worktree is clean and can be removed safely. Other AWS infrastructure checkouts must not be removed indiscriminately:

- the primary `/home/alexey/git/aws-infra` checkout contains an in-progress merge conflict and staged/unstaged changes;
- `/home/alexey/git/.worktrees/aws-auth-main` is clean but tracks a feature branch;
- `/home/alexey/git/.worktrees/aws-infra-aisl-prod-recovery` is clean but tracks an on-call recovery branch;
- `/home/alexey/git/aws-infra-worktrees/issue-145-sandbox-oidc` has one local commit ahead of its upstream;
- `/tmp/dataops-aws-infra-173.rMU7hc` tracks issue 173.

Only the website-local `.tmp/aws-infra-191-origin-main` should be treated as disposable scratch evidence for this handoff.

## Remaining work

1. Record the owner's final rotation approach on issue #191.
2. Implement and verify that approach against the issue's fail-closed acceptance criteria.
3. Add a bounded alert or red-gate signal for repeated PostgreSQL authentication failures on the web and worker services.
4. Update `_docs/runbooks/development-release.md` with the chosen mechanism, verification procedure, and failure attribution steps.
5. Re-enable automatic deployment only when ready, dispatch/promote the current `main` release, and require liveness/readiness to report the exact current release triplet.
6. Complete independent tester, PM acceptance, and issue closure per `_docs/PROCESS.md`.

Work stopped here at the operator's request. No AWS mutation, issue update, deployment retry, or rotation change was made after this audit was written.
