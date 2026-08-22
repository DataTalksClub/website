# CMP upstream synchronization runbook

Issue: [#145](https://github.com/DataTalksClub/website/issues/145)

The website adopts the Course Management Platform (CMP) by literal copy. It is
not a Git mirror and CMP changes are never deployed automatically. A sync is a
reviewed, source-pinned change to this repository.

## Repeatable recipe

Use this short sequence for the next CMP update. It deliberately resolves one
immutable commit and then copies only the allowlisted bytes; it never merges a
second Django project or reimplements CMP behavior.

From a clean CMP checkout, commit and push the desired change first:

```bash
export CMP_ROOT=/home/alexey/git/course-management-platform
export CMP_REPOSITORY=https://github.com/DataTalksClub/course-management-platform.git

if [ -n "$(git -C "$CMP_ROOT" status --porcelain=v1 --untracked-files=all)" ]; then
  echo "CMP checkout is dirty; commit or discard changes first" >&2
  exit 1
fi
git -C "$CMP_ROOT" push origin HEAD:main
export CMP_COMMIT_SHA="$(git -C "$CMP_ROOT" rev-parse HEAD)"
printf 'CMP commit: %s\n' "$CMP_COMMIT_SHA"
```

From the website checkout, run a report-only dry run using that exact SHA. A
unique checkout/report path makes the run reproducible and keeps old evidence:

```bash
export WEBSITE_ROOT=/home/alexey/git/dtc-website
cd "$WEBSITE_ROOT"
export CMP_REPORT=".tmp/cmp-sync-${CMP_COMMIT_SHA}.json"
export CMP_CHECKOUT=".tmp/cmp-source-sync-${CMP_COMMIT_SHA}"

uv run python scripts/sync_course_platform.py \
  --source-repository "$CMP_REPOSITORY" \
  --source-ref "$CMP_COMMIT_SHA" \
  --source-checkout "$CMP_CHECKOUT" \
  --dry-run \
  --report "$CMP_REPORT"
```

Review `CMP_REPORT` for `status=ready` when the preflight can apply, or
`status=no_change` when the requested SHA is already pinned. In both cases
check the exact current/requested SHAs,
allowlisted copy entries, excluded paths, and zero overlay conflicts or fatal
errors. Only then apply the same command with `--apply` instead of
`--dry-run`:

```bash
uv run python scripts/sync_course_platform.py \
  --source-repository "$CMP_REPOSITORY" \
  --source-ref "$CMP_COMMIT_SHA" \
  --source-checkout "$CMP_CHECKOUT" \
  --apply \
  --report "$CMP_REPORT"
```

Verify the result with the same gates used by the adoption contract:

```bash
uv run python scripts/verify_course_platform_adoption.py
make migrations-check
uv run pytest scripts/tests/test_sync_course_platform.py -q
uv run python manage.py test courses --noinput
```

Finish the normal issue lifecycle: inspect the diff, leave the branch
uncommitted for the independent tester and PM, then commit with `Closes #N`,
merge locally with `--no-ff`, push `main`, and wait for the exact CI/deploy run.
Do not apply a report with conflicts, deletions, migration rewrites, or a dirty
source; resolve those in a reviewed follow-up first.

## 1. Make a CMP commit eligible

Only a pushed commit is eligible. The source worktree must be clean, including
untracked files:

```bash
git -C /home/alexey/git/course-management-platform status \
  --porcelain=v1 --untracked-files=all
git -C /home/alexey/git/course-management-platform rev-parse HEAD
git -C /home/alexey/git/course-management-platform push origin main
```

The current local CMP checkout has uncommitted changes. The sync command will
reject that checkout; commit and push the desired changes first, then use the
resulting immutable 40-character SHA. A remote URL can be used instead of the
local path when checking a pushed commit.

## 2. Dry-run a specific commit

In a fresh website worktree, provision the exact checkout recorded in
`source-pin.json` before running the adoption verifier:

```bash
uv run python scripts/prepare_course_platform_source.py
# equivalent Make target:
make course-platform-source-checkout
uv run python scripts/verify_course_platform_adoption.py
```

Provisioning clones the pinned `source_repository` to the recorded
`source_checkout`, checks out the exact SHA detached, and verifies that the
checkout is clean. If that path already exists and is dirty (for example, a
previous interrupted `git checkout` left staged deletions), the helper refuses
to overwrite it. Resolve the checkout or choose a new path explicitly for the
sync dry-run; do not copy from the dirty worktree.

From the website repository, use an explicit source commit (a branch such as
`main` is resolved to an exact SHA in the report):

```bash
export CMP_COMMIT_SHA=98a235283904b4ef9ad29e196298540756cf1bcc  # replace with the pushed CMP SHA
uv run python scripts/sync_course_platform.py \
  --source-repository https://github.com/DataTalksClub/course-management-platform.git \
  --source-ref "$CMP_COMMIT_SHA" \
  --dry-run \
  --report .tmp/course-platform-sync-report.json
```

The default mode is also a dry run. The command clones or fetches a clean
detached checkout under `.tmp/cmp-source-sync` (or the path supplied with
`--source-checkout`), verifies the current pin from
[`source-pin.json`](source-pin.json), and writes a deterministic JSON report.
It does not write copied files, manifests, the pin, or the README. The report
contains the current pinned SHA, requested SHA, every changed path, excluded
metadata changes, copy candidates, overlay conflicts, and required follow-up.

An excluded `.claude/`, deployment, editor, or other non-allowlisted change is
reported as `excluded` and does not enter the target. A source checkout that is
dirty, unavailable, or not at a commit causes a blocked report and a non-zero
exit status.

## 3. Review the mapping and conflicts

The command uses the same allowlist and manifest as the adoption verifier:

| CMP source | Website destination |
| --- | --- |
| `accounts/` | `accounts/` |
| `api/` | `api/` |
| `cadmin/` | `studio_courses/` |
| `course_management/` | `course_management/` |
| `courses/` | `courses/` |
| `data/` | `data/` |
| `e2e/` | `e2e/` |
| `scripts/` | `scripts/` |
| `templates/` | `course_platform_templates/` |

`cadmin/templates/cadmin/...` is mapped to
`studio_courses/templates/studio_courses/...`; all other `cadmin` paths map by
the same relative suffix. The target-owned two-file `cadmin` compatibility
adapter is never copied over.

Every destination in `integration-patched-files.tsv` is an overlay. When its
upstream source bytes change, the dry-run preserves the target bytes and reports
the old source SHA, new source SHA, target-overlay SHA, recorded overlay SHA, and
rationale. Do not use `--apply` until the integration patch has been reviewed,
implemented, and its manifest evidence has been updated in a separate issue.

The command fails closed before writing for:

- allowlisted deletions, renames, or copies;
- an existing migration being rewritten or removed;
- a new mapped file colliding with a target-owned file;
- an unknown allowlist root or changed source-to-target mapping;
- a stale copied-file or overlay checksum; and
- a dirty source checkout.

New migration files may be copied when their destination is absent. The sync process never
replaces, squashes, or removes existing migrations. The phase-1 local `courses` squash is an
explicit post-sync operation recorded in `migration-squash-gate.md`, not a supported source-sync
or production-upgrade operation.

## 4. Apply a conflict-free sync

After the dry-run has been reviewed and no conflicts or fatal errors remain:

```bash
uv run python scripts/sync_course_platform.py \
  --source-repository https://github.com/DataTalksClub/course-management-platform.git \
  --source-ref "$CMP_COMMIT_SHA" \
  --apply \
  --report .tmp/course-platform-sync-report.json
```

The command stages and checks every byte before replacing a destination. It
mechanically copies only new/changed non-overlaid allowlisted files, regenerates
`copied-files.tsv`, updates `source-pin.json`, and updates the single pinned SHA
line in `README.md`. It never removes target files or edits the patch manifest.
An overlay conflict or safety error exits before target mutation.

## 5. Verify and review

Run the adoption and migration gates against the checkout named in
`source-pin.json`, followed by the focused sync tests and the copied-course
checks relevant to the changed paths:

```bash
uv run python scripts/verify_course_platform_adoption.py
make migrations-check
uv run pytest scripts/tests/test_sync_course_platform.py -q
uv run python manage.py test courses --noinput
```

Generate the normal versioned verification plan required by
[`_docs/PROCESS.md`](../../PROCESS.md). The engineer leaves the branch
uncommitted and frozen; the independent tester recomputes the plan and verifies
the acceptance criteria. The product manager accepts only after the tester
passes. The approved engineer commit records `Closes #145`; the orchestrator
merges locally with `--no-ff` and pushes `main`. No pull request or automatic
source merge is used.

## 6. Rollback

The safest rollback is to revert the approved website sync commit, restoring the
previous copied files, manifest, README pin, and `source-pin.json` together:

```bash
git revert <approved-website-sync-commit>
```

The revert still goes through tester/PM gates before local merge and push. Do
not run an unreviewed sync backwards to an old CMP SHA: if the newer source added
allowlisted files, the command intentionally rejects the resulting source
deletions. If a source-level rollback is required instead, create a reviewed
CMP commit that restores the prior bytes (without deleting migration history),
then run the same dry-run/apply procedure. This keeps the source pin,
checksums, and migration safety contract coherent.
