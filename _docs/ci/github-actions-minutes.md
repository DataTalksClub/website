# GitHub Actions minutes and self-hosted runners

Written after the November 2026 incident where the org ran out of included minutes.

## Allowance

- The org is on the GitHub Free plan: 2,000 hosted minutes a month, counted for **private repos only**.
- Public repos (this one included) run on GitHub-hosted standard runners at no cost. Their usage shows in the billing view but is discounted to $0.
- No evidence that GitHub reduced the allowance. The budget is $0, so running out means the included minutes were used up.

## What used the minutes (1-23 Nov 2026)

About 2,380 billed minutes, almost all from two private repos:

- `aws-infra`: a 14-root Terraform validation matrix on every PR commit. Each job takes about 10 s but is billed as 1 min, and runs were not cancelled when a PR was pushed again.
- `dataops-knowledge`: 3-7 short jobs per push and PR, plus a daily cron.

Each job is rounded up to a whole minute, so many tiny jobs cost far more than their wall time.

## Where CI runs now

Production repos (DataTalksClub, AI-Shipping-Labs and the product repos under `alexeygrigorev`) run their Linux jobs on a dedicated CI VPS, `contabo-ci` (62.171.137.73, 2 vCPU, 4 GB RAM + 4 GB swap), with `runs-on: [self-hosted, contabo]`.

- Runners are registered per repo, because the token has no `admin:org` and personal-account repos cannot share org runners. Each repo has one runner named `contabo-<short>` (`aws-infra` has two). A listener takes about 100 MB, so the box cannot hold two per repo.
- Jobs run as the sandboxed `gha-runner` user: no sudo, not in the `docker` group. Docker is rootless for that user (`DOCKER_HOST=unix:///run/user/1002/docker.sock` in each runner's `.env`).
- Jobs that stay on GitHub-hosted runners: Windows/macOS, `ubuntu-24.04-arm` image builds, and anything triggered by `issues`, `issue_comment` or `pull_request_target`.
- AWS OIDC is unchanged. The role trust keys on the token `sub` (the repo), not on the runner.
- The Hetzner runners for `aws-infra` and `dataops-knowledge` (`[self-hosted, hetzner]`) stay registered as a fallback.

## Public repos on self-hosted runners

A fork PR can edit a workflow to target any label, so public repos need two guards:

- Fork-PR approval is set to `all_external_contributors` (Settings > Actions > Fork pull request workflows). Check with `gh api repos/<owner>/<repo>/actions/permissions/fork-pr-contributor-approval`.
- Every self-hosted job that can run on `pull_request` carries `if: github.event_name != 'pull_request' || github.event.pull_request.head.repo.full_name == github.repository`, so an approved fork PR still never reaches the box.

## Operations

```bash
ssh contabo-ci 'systemctl list-units "actions.runner.*"'
gh api repos/<owner>/<repo>/actions/runners --jq '.runners[]|[.name,.status]|@tsv'
```

Add a runner for another repo (as `alexey` on Hetzner; the token goes over stdin):

```bash
gh api -X POST repos/<owner>/<repo>/actions/runners/registration-token --jq .token \
  | ssh contabo-ci 'sudo /root/add-runner.sh <owner>/<repo> contabo-<short> <short>'
```

Other rules:

- Use `concurrency` with `cancel-in-progress` on PR workflows, and avoid matrices of very short jobs on hosted runners.
- The workspace persists between jobs. A job that writes secrets to disk must write them under `$RUNNER_TEMP` and delete them in the same job.
