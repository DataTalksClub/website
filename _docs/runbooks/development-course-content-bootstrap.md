# Development course-content bootstrap

This runbook applies only to a development deployment — the host
`deploy/development_target.py` selects. It loads public CMP catalog and curriculum rows; it
must never import learner, administrator, registration, submission, review, session, token, email,
job, or provider data.

## Frozen artifact contract

Only the independently audited sanitized artifact is accepted:

- allowlist version: `cmp-public-review-v1`;
- allowlist schema SHA-256: `06a23857b9d8a4265c520ad67a6285fc0ed604007f6280a02ca7fb2d6a35c96e`;
- file SHA-256: `ac55cb0cb10cc0924dd8c9a9e63fe9b09ae809cac8aac14d6da2ce46c3586d04`;
- logical content SHA-256: `eb5fd5f8e7d27aee107d925ea7e17c60274a4695f4fd7783e5c67f79a59e0a20`;
- cross-database semantic SHA-256: `2687838817b2918a7691206c9bc6b79082f2e1c8356f099c699aab1395e73426`.

It contains 20 courses, 2 campaigns, 119 homeworks, 604 questions, 109 homework
statistics, 48 projects, 169 review criteria, 41 project statistics, and one Wrapped statistics
row. Wrapped has eight public course-stat links and an empty leaderboard. The protected raw backup
is prohibited input and must never be uploaded or passed to this command.

## One-off task boundary `[HUMAN]`

Use a dedicated one-off development ECS task based on the exact released image. It is not a web,
worker, scheduler, or normal migration task. Give it the normal development database secret plus a
short-lived task role limited to the exact versioned transport object:

- `s3:GetObjectVersion` and `s3:DeleteObjectVersion` for that object version only;
- `kms:Decrypt` for the exact development KMS key only.

Upload the audited artifact as a private, short-lived, versioned S3 object encrypted with that KMS
key. Do not put it in Git, the container image, CI artifacts, issue attachments, command output, or
logs. Disable worker/scheduler execution and all outbound message/provider behavior on the one-off
task.

The released non-root container stages the version-pinned download beneath the fixed writable
ephemeral root `/tmp`, never beneath the read-only application tree and never from a caller-supplied
path or `TMPDIR`. The command creates one atomically named mode-`0700` directory and one
no-follow/exclusive mode-`0600` regular file, verifies their ownership and permissions, and removes
both on success and on every fail-closed path. The task needs no root user, broad storage mount, or
additional filesystem permission.

Run:

```text
uv run python manage.py import_development_course_content \
  --s3-bucket <private-bucket> \
  --s3-key <short-lived-object-key> \
  --s3-version-id <exact-version> \
  --expected-bucket-owner <development-account-id> \
  --expected-kms-key-arn <exact-development-kms-key-arn>
```

The command accepts only `DTC_ENVIRONMENT=development`. It verifies encryption, object version,
private local permissions, exact file/schema/content hashes, empty denied source tables, and an
empty Wrapped leaderboard before beginning database work. The target must have either all nine
public tables empty with no course activity, or already contain the exact semantic dataset. Any
partial or different target aborts. Writes and the idempotency receipt are atomic; protected table
fingerprints must remain unchanged. A successful S3 invocation deletes the exact transport object
version. If deletion fails, rerun the same command: the database import replays without writes and
the deletion is retried.

Never run this command from a migration, release hook, web container, worker, scheduler, or against
production. Never use `scripts/load_rds_export.py` or the production-like data generator for this
bootstrap.

## Read-only verification

From a second one-off task using the same released image and development database, run:

```text
uv run python manage.py verify_development_course_content \
  --representative-slug de-zoomcamp-2026
```

The safe JSON result must report the frozen counts and checksums, callbacks
`courses.views.course_list.course_list` and `courses.views.course.course_view`, repository-relative
origins for `courses/templates/courses/course_list.html` and
`courses/templates/courses/course.html`, and representative counts `1` course, `8` homeworks, `3`
projects, and `50` questions. Then verify `/courses` and `/courses/de-zoomcamp-2026` return `200`, an
unknown clean slug returns a real `404` without a canonical, and slash/legacy aliases redirect once
to the clean detail while preserving the raw query. A public `POST` without a CSRF token to either
alias is rejected by Django's CSRF middleware with `403`; do not exempt the aliases from CSRF.

Record only this public-safe evidence. Do not record artifact locations, database identifiers,
private row counts, fingerprints, object keys, credentials, or production data.
