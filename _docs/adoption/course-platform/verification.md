# Course-platform adoption verification

All results below were produced from source commit
`98a235283904b4ef9ad29e196298540756cf1bcc`. Copied characterization and E2E files remained
unchanged; the copy verifier checks their recorded bytes.

## Characterization results

| Surface | Result | Disposition |
| --- | --- | --- |
| Source-owned Django characterization | 756 passed, 0 skipped, 0 failed | Unchanged copied tests only |
| Unified Django suite | 787 passed, 0 skipped, 0 failed | Includes 6 target adoption-contract tests |
| Copied E2E suite | 48 collected: 45 passed, 1 skipped, 2 xfailed, 0 failed | Playwright 1.58, fresh local database, ephemeral local dependency configuration |
| Target foundation Playwright | 3 passed, 0 skipped, 0 failed | Desktop/mobile homepage and anonymous staff sign-in surface |
| Adoption contract | 6 passed | 768 copied checksums, 2 target shim checksums, 89 routes, 13 commands, app/migration identity, generated inventory |

The copied E2E suite was first run without a Datamailer preference service. Two account-settings
checks reached the correct page with HTTP 200 but timed out waiting for browser `networkidle`
because the page's preference fetch correctly returned 503 for the absent service. Repeating the
unchanged suite with an ephemeral localhost preference stub removed both failures. No stub or
credential was added to the repository.

The two declared xfails are the copied homework/project confirmation-email checks: no durable
worker and transactional provider were run to create send-audit rows. The single skip is the
copied teardown fallback after admin deletion was unavailable; it parked the generated course
hidden. All provisioning, enrollment/impersonation, homework, project, API, dashboard,
leaderboard, cadmin, helper, and fallback-cleanup checks passed.

## Migration evidence

- Original numbered migrations remain intact: 10 `accounts`, 40 `courses`, and 5 `data` migrations;
  `api` and `cadmin` retain their original no-numbered-migration state.
- Two independent fresh SQLite databases applied the complete original graph successfully.
- `makemigrations --check --dry-run` reports no model drift.
- The final graph is the verified original graph, so a fresh final install does not introduce a
  replacement-graph parity question.
- No anonymized production-like source snapshot was provided. Upgrade/import reconciliation,
  production count/key comparison, and supported reverse/forward windows therefore remain
  unverified. No squash was attempted and no original migration was removed.

## Inventory and repository checks

- `uv run python scripts/verify_course_platform_adoption.py`: 768 copied files verified against
  the clean pinned checkout and explicit integration-patch state, plus both required target-owned
  admin API compatibility shims verified against their per-file checksums and rationales.
- `behavior-inventory.md`: 89 routes (9 accounts, 29 compatibility API, 26 cadmin, 25 public
  course) and 13 management commands generated from Django's registries and smoke-resolved by the
  adoption-contract test.
- Ruff lint, Ruff format check, targeted mypy, Django system check, deployment check, and the full
  unified Django suite pass.

Representative screenshots and independent browser verification belong to the separate tester
stage required by `_docs/PROCESS.md`; the software-engineer stage does not accept its own work.
