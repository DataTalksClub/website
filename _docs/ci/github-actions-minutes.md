# GitHub Actions minutes across the DataTalksClub org

Written after the November 2026 incident where the org ran out of included minutes.

## Allowance

- The org is on the GitHub Free plan: 2,000 hosted minutes a month, counted for **private repos only**.
- Public repos (this one included) run on GitHub-hosted runners at no cost. Their usage shows in the billing view but is discounted to $0.
- No evidence that GitHub reduced the allowance. The budget is $0, so running out means the included minutes were used up.

## What used the minutes (1-23 Nov 2026)

About 2,380 billed minutes, almost all from two private repos:

- `aws-infra`: a 14-root Terraform validation matrix on every PR commit. Each job takes about 10 s but is billed as 1 min, and runs were not cancelled when a PR was pushed again.
- `dataops-knowledge`: 3-7 short jobs per push and PR, plus a daily cron.

Each job is rounded up to a whole minute, so many tiny jobs cost far more than their wall time.

## Policy

- Private repos run on self-hosted Hetzner runners (`runs-on: [self-hosted, hetzner]`), repo-level, under the sandboxed `gha-runner` user. Done for `aws-infra` (#169) and `dataops-knowledge` (#46).
- Public repos, including this one, stay on `ubuntu-latest`. Never register a self-hosted runner for a public repo.
- Use `concurrency` with `cancel-in-progress` on PR workflows, and avoid matrices of very short jobs on hosted runners.
