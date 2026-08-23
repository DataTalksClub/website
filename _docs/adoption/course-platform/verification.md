# Course-platform adoption verification

All results below were produced from source commit
`98a235283904b4ef9ad29e196298540756cf1bcc`. The copy verifier checks all 768 pinned-source
checksums, then checks each intentional copied-destination overlay against its separately recorded
target checksum. Copied characterization and E2E files remain unchanged except for the explicit
overlays in `integration-patched-files.tsv`.

The characterization, E2E, and adoption totals below are the pre-phase-1 baseline from that source
commit. The migration evidence section records the repaired compatibility graph for the deployed
legacy course boundary; it does not claim a populated deployment or production readiness.

## Characterization results

| Surface | Result | Disposition |
| --- | --- | --- |
| Source-owned Django characterization | 756 passed, 0 skipped, 0 failed | Unchanged copied tests only |
| Unified Django suite | 787 passed, 0 skipped, 0 failed | Includes 6 target adoption-contract tests |
| Copied E2E suite | 48 collected: 45 passed, 1 skipped, 2 xfailed, 0 failed | Playwright 1.58, fresh local database, ephemeral local dependency configuration |
| Target foundation Playwright | 3 passed, 0 skipped, 0 failed | Desktop/mobile homepage and anonymous staff sign-in surface |
| Adoption contract | 6 passed | 768 pinned-source checksums, copied-destination overlay checksums, 4 target shim checksums, 89 routes, 13 commands, app/migration identity, generated inventory |

The copied E2E suite was first run without a Datamailer preference service. Two account-settings
checks reached the correct page with HTTP 200 but timed out waiting for browser `networkidle`
because the page's preference fetch correctly returned 503 for the absent service. Repeating the
unchanged suite with an ephemeral localhost preference stub removed both failures. No stub or
credential was added to the repository.

The two declared xfails are the copied homework/project confirmation-email checks: no durable
worker and transactional provider were run to create send-audit rows. The single skip is the
copied teardown fallback after admin deletion was unavailable; it parked the generated course
hidden. All provisioning, enrollment/impersonation, homework, project, API, dashboard,
leaderboard, Studio Courses, helper, and fallback-cleanup checks passed.

## Migration evidence

- The deployed lower boundary is `11b2bd11f85625a21b12e5f0c9b04c12a6b1d664`, with
  `courses.0001_initial` through `courses.0041_courseregistrationcountsourcerun_and_more`.
- `courses.0001_squashed_0029` replaces the main legacy branch (`0001`–`0026`, `0028`, and
  `0029`) only. `courses.0027` remains a separate branch because `courses.0031` explicitly merges
  it with `courses.0030`; the fresh plan therefore runs `0027` after `0030` and before `0031`.
  `courses.0030`–`0041` remain available as named modules because the account backfill dependency
  must stay outside the replacement without creating a circular graph.
- `courses.0042_course_schema_bridge` preserves the legacy `courses_course` table and row IDs,
  creates a stable one-family-per-legacy-course mapping, and leaves current post-squash operations
  in `0043`–`0051`.
- The compatibility tests construct the raw legacy applied-history state with
  `MigrationLoader(replace_migrations=False)`, populate redacted course/enrollment/homework/
  project/review/statistics/certificate/registration data, and compare row counts, primary keys,
  foreign keys, checksums, content-type/permission rows, and `django_migrations` provenance after
  upgrade and replay.
- The bridge rollback/retry test forces a failed historical data operation, verifies the atomic
  rollback, then reruns the same candidate successfully. Fresh migration, migration import
  isolation, `makemigrations --check --dry-run`, and `make test-migrations` are local-only checks;
  no development or production database is used.
- Exact-image populated deployment, readiness, deployed smoke, failure classification, and
  post-push CI/on-call evidence remain HUMAN gates for issue #220.

## Inventory and repository checks

- The 71 pinned `cadmin/*` source files map to `studio_courses/*` destinations. The active app,
  26 callbacks, imports, tests, and template namespace all resolve through `studio_courses`; no
  copied destination remains below `cadmin/`.
- The target-owned `cadmin/` package contains exactly `__init__.py` and `legacy_urls.py`. The
  reviewed reference allowlist classifies every other occurrence as legacy compatibility,
  immutable source provenance, or historical specification and records an owner and removal gate.

- `uv run python scripts/verify_course_platform_adoption.py`: the 768-row source ledger verified
  against the clean pinned checkout; every copied destination verified against either that source
  checksum or its explicit integration-patch checksum; and all four required target-owned
  compatibility shims verified against their per-file checksums and rationales. The same verifier
  requires every remaining `cadmin` path or text reference to have an exact owner and removal gate.
- `behavior-inventory.md`: 89 routes (9 accounts, 29 compatibility API, 26 Studio Courses, 25 public
  course) and 13 management commands generated from Django's registries and smoke-resolved by the
  adoption-contract test.
- Ruff lint, Ruff format check, targeted mypy, Django system check, deployment check, and the full
  unified Django suite pass.

Representative screenshots and independent browser verification belong to the separate tester
stage required by `_docs/PROCESS.md`; the software-engineer stage does not accept its own work.
