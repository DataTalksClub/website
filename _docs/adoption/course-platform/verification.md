# Course-platform adoption verification

All results below were produced from source commit
`98a235283904b4ef9ad29e196298540756cf1bcc`. The copy verifier checks all 768 pinned-source
checksums, then checks each intentional copied-destination overlay against its separately recorded
target checksum. Copied characterization and E2E files remain unchanged except for the explicit
overlays in `integration-patched-files.tsv`.

The characterization, E2E, and adoption totals below are the pre-phase-1 baseline from that source
commit. The migration evidence section records the current local Course-to-Cohort migration state
and its completed local replay.

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

- Phase 1 intentionally replaces the active `courses` history with one `0001_initial` migration.
  It defines `Cohort`, `Cohort.outcome`, the legacy `courses_course` table, and Cohort-backed
  course relations. The pinned numbered migration records remain historical provenance only.
- `makemigrations --check --dry-run` reports no model drift for the new graph.
- `make test-migrations` passes all 16 migration-aware tests, including the Cohort schema contract
  and the historical account profile backfill; `make migrations-check` reports no drift.
- A fresh `.tmp/local.sqlite3` replayed every migration to every leaf, then the local course,
  question, and social-provider fixtures were reseeded. No production database was touched.
- This local squash is not a production upgrade path. Production-like parity, data reconciliation,
  and reverse/forward migration windows remain unverified.

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
