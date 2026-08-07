# AI Shipping Labs Django website

Locator: git@github.com:AI-Shipping-Labs/website.git@b3e5b23de428b67f8bed33476da19f4ecb2df515

Accessed: 2026-08-07

## Summary

The repository is a comparison implementation for GitHub sync, Studio, admin API, events, email, jobs, auditing, observability, and AWS deployment. Its patterns are adapted selectively; its product breadth is not copied.

## Claims

- [FACT aisl-reference] Content sync records source configuration, sync history, commits, counts, per-item diagnostics, locks, queued follow-up state, and last successful state.
- [FACT aisl-reference] Signed GitHub webhooks, remote-HEAD unchanged skips, local fixture sync, shallow clones, maximum-file limits, source provenance, and extensive lifecycle tests already exist.
- [FACT aisl-reference] A separate `/studio/` surface applies authenticated no-store behavior and has read-only treatment for synced content.
- [FACT aisl-reference] Operator API tokens use one-time plaintext with stored hashed credentials and an indexed lookup prefix; OpenAPI coverage/drift tests exist.
- [FACT aisl-reference] Events implement stable calendar UID/sequence and signed, link-scanner-safe cancellation workflows.
- [FACT aisl-reference] Email models track immutable recipient/subject details, idempotency keys, provider messages, and provider events, although the audited send/log ordering still leaves crash-ambiguity cases.
- [FACT aisl-reference] Django-Q2 with an ORM broker runs as a separate worker and supports named tasks, declarative schedules, and worker diagnostics.
- [FACT aisl-reference] Deployment separates migration, web, and worker concerns and uses immutable images, readiness checks, PostgreSQL, ECR/ECS, Secrets Manager, SES, CloudWatch, and GitHub OIDC patterns.

## Adaptation

- [INFERENCE aisl-reference] Reuse lifecycle, safety, and operational patterns, but use explicit DTC repository adapters rather than ASL's domain classification.
- [INFERENCE aisl-reference] Replace blanket staff checks with capability permissions and complete Studio/API parity.
- [INFERENCE aisl-reference] Use a durable email delivery outbox with lease/ambiguous states rather than relying on a check-send-log sequence.
- [INFERENCE aisl-reference] Keep critical sync/delivery state in domain tables rather than job-history rows.

## Limitations

- [FACT aisl-reference] The reference worktree contained unrelated untracked planning/scratch files during inspection; claims use committed application files at the recorded HEAD.
- [INFERENCE aisl-reference] The reference includes payments, memberships, courses, CRM, and other product-specific complexity outside this project's requirements.

## Related

- [INFERENCE aisl-reference] [GitHub-backed content architecture](../concepts/content-architecture.md)
