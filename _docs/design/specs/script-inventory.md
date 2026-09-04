# Script inventory and deletion candidates

**As of commit `fbab381`** on `main`. The worktree is shared with other
agents; re-verify any `path:line` below before acting on it.

This is an analysis document. Nothing was deleted or changed to produce it.

## 0. Read this before deleting anything

Three mechanisms make "unreferenced" scripts undeletable. Each one fails CI on deletion.

### 0.1 The adoption ledger pins 14 files under `scripts/`

`core/tests/test_course_platform_adoption.py:262` asserts `destination.is_file()` for every
row of `_docs/adoption/course-platform/copied-files.tsv`, and
`core/tests/test_course_platform_adoption.py:238` asserts that ledger has exactly **768 rows**.
`scripts/verify_course_platform_adoption.py:264-265` emits `missing destination: <path>` for
the same condition. The escape hatch — `retired_adoption_destinations`,
`scripts/verify_course_platform_adoption.py:74-84` — only tolerates deletions under
`courses/migrations/` or `courses/models/course.py`. Nothing under `scripts/` qualifies.

Pinned (`_docs/adoption/course-platform/copied-files.tsv:747-762`):
`add_data.py`, `add_more_test_data.py`, `add_user.py`, `analyze_scoring_bug.py`,
`create_superuser.py`, `debug_score_project.py`, `generate_production_like_leaderboard_data.py`,
`__init__.py`, `load_project_data.py`, `load_rds_export.py`, `move_criteria.py`,
`production_like_course_specs.json`, `pull_project_data.py`, `score_project_dev.py`,
`score_project.py`, `wrapped.py`.

Deleting any of them requires, in one change: removing the row from `copied-files.tsv`,
removing any matching row from `integration-patched-files.tsv`, extending
`retired_adoption_destinations` to cover `scripts/`, and lowering the 768 constant. That is a
groomed issue, not a cleanup commit.

### 0.2 The management-command registry is an exact set

`core/tests/test_course_platform_adoption.py:330-333` asserts
`{entry.name: entry.app for entry in command_entries()} == EXPECTED_COMMANDS`
(`core/tests/test_course_platform_adoption.py:30-56`). `command_entries()` enumerates the
commands in `SOURCE_COMMAND_APPS` (`accounts`, `api`, `studio_courses`, `courses`, `data`).
Deleting **or adding** a command in those five apps fails that test, and
`_docs/adoption/course-platform/behavior-inventory.md:172-194` must be re-rendered by
`scripts/render_course_platform_inventory.py`.

Commands in `events/`, `jobs/`, `core/`, `content_sync/` and `management_api/` are **not**
covered by that set and are freely deletable from the registry's point of view.

### 0.3 The junk data the owner sees is not produced by a deletable script

`Project Attempt N` and `Practice assignment for …` come from the **live** production-prep
path, not from any of the scripts flagged as placeholder generators:

```
make production-prep-dataset            Makefile:455
  -> scripts/prepare_local_data.py      Makefile:392
     -> call_command("seed_local_courses")             scripts/prepare_local_data.py:290
        -> courses/services/local_course_seed.py:376
           homework_description()  -> "Practice assignment for {title}. …"
                                                       courses/services/local_course_seed.py:276
           catalogue source: scripts/production_like_course_specs.json
                                                       courses/services/local_course_seed.py:68
              -> "Project Attempt 1" / "Project Attempt 2" / "Last Project Attempt"
                                                       scripts/production_like_course_specs.json:42-50
```

`scripts/production_like_course_specs.json` is additionally SHA-256-pinned and read by
`scripts/build_public_projection.py:2918` and asserted by
`content/tests/test_public_projection_builder.py:26`.

**Consequence:** deleting `seed_local_courses.py`, `seed_local_questions.py`, `add_data.py`,
`add_more_test_data.py` or `generate_production_like_leaderboard_data.py` will not remove one
row of that data from the running site. Changing what production-prep seeds is a change to
`courses/services/local_course_seed.py` and the pinned specs JSON — a separate, product-level
decision.

---

## 1. Summary table — `scripts/`

| Script | What it does | Verdict | Primary caller |
| --- | --- | --- | --- |
| `build_article_faq.py` | Recovers ten legacy blog-article FAQ sections from the legacy site repo into one provenance-carrying capture | Live (runbook) | `_docs/article-faq-recovery.md:37` |
| `build_event_description_bridge.py` | Builds the reviewed public-safe event-description bridge from a local exporter snapshot | Live (runbook) + test import | `_docs/event-description-bridge.md:19`; `content/tests/test_event_description_bridge.py:25` |
| `build_legacy_manifest.py` | Crawl/merge/compare/validate the versioned legacy SEO compatibility manifest | Live | `Makefile:299,301,306`; `_docs/compatibility/README.md:133-231` |
| `build_local_review_db.py` | 19-line shim into `review_import.cli.main()` | Live | `Makefile:485,492,499` |
| `build_pinned_legacy_sources.py` | Creates clean pinned legacy checkouts and deterministic generated-site inputs | Live | `Makefile:296`; `ci/tests/test_workflows.py:171` |
| `build_public_projection.py` | Sole builder of the checked, network-free public content projection | Live | `.github/workflows/content-update.yml:18,43` |
| `build_synthetic_design_review_db.py` | Builds a wholly synthetic issue-#237 design-review SQLite DB + manifest under `.tmp/` | Live (design contract) | `_docs/testing/issue-237-review-state-matrix.md:18` |
| `capture_screenshots.py` | Playwright screenshotter for local pages, for the tester role | **Uncertain** — see §4 | lint/typecheck lists only (`Makefile:48,83`) |
| `check_database_portability.py` | Fails when app code, CI or specs regain backend-specific behavior | Live (CI gate) | `Makefile:119`; `ci/tests/test_database_portability.py:8` |
| `create_local_admin.py` | Seeds the documented local admin + placeholder social providers (`website.settings.local`) | Live | `README.md:29` |
| `prepare_course_platform_source.py` | Provisions the clean detached CMP checkout from `source-pin.json` | Live | `Makefile:167`; `.github/workflows/content-update.yml:19,44` |
| `prepare_event_registration_sources.py` | Prepares protected Eventbrite/Luma exports for aggregate-only migration adapters | Live (migration) | `_docs/migration-checklist.md:94`; `events/tests/test_prepare_registration_sources.py:13` |
| `prepare_local_data.py` | Composes the bounded local production-data rehearsal (migrate → CMP import → seed → modules) | Live | `Makefile:392`; absorbed by the import consolidation |
| `process_illustration.py` | Trims a transparent source image and exports a WebP illustration via ImageMagick | Live (documented procedure) | `_docs/design/illustration-assets.md:22,31` |
| `render_course_platform_inventory.py` | Renders the adopted route + management-command inventory Markdown | Live | `scripts/build_pinned_legacy_sources.py:745`; `core/tests/test_course_platform_adoption.py:12` |
| `repin_projection_digests.py` | Recomputes only the derived digest/scope fields of the checked projection manifest | Live (runbook) | `_docs/runbooks/public-media-objects.md:191-192` |
| `security_artifact_scan.py` | Scans bounded artifacts for redaction canaries | Live (CI gate) | `Makefile:110` |
| `security_baseline.py` | Non-identity security evidence gate | Live (CI gate) | `Makefile:101` |
| `security_canary_artifact.py` | Writes the deterministic redacted canary artifact | Live (CI gate) | `Makefile:103` |
| `security_vulnerability_scan.py` | Locked-environment `pip-audit` advisory scan | Live (CI gate) | `Makefile:102` |
| `sync_course_platform.py` | Reports/applies a reviewed CMP upstream commit into this repo | Live | `Makefile:170,177`; `.github/workflows/content-update.yml:20,45` |
| `verify_course_platform_adoption.py` | Verifies the 768-row CMP copy ledger and cadmin allowlist | Live | `scripts/sync_course_platform.py:37`; `_docs/adoption/course-platform/README.md:17` |
| `verify_course_platform_vendor_assets.py` | Verifies locally served CMP vendor assets and their provenance | Test-only import | `core/tests/test_course_platform_vendor_assets.py:17` |
| `verify_development_seo_terraform.py` | Verifies the trusted Terraform source for development SEO policy | Live | `Makefile:132` |
| `verify_local_dataset.py` | Acceptance gate for `make production-prep-dataset` — aggregate cohort/module/event counts | Live | `Makefile:467` |
| `verify_static_manifest.py` | Verifies a built image contains the runtime static manifest | Live (CI gate) | `.github/workflows/ci.yml:1078,1104` |
| `podcast_platforms.json` | Podcast platform seed for the public projection | Live data | `scripts/build_public_projection.py:56` |
| `production_like_course_specs.json` | SHA-256-pinned upstream course catalogue | Live data — **do not delete** | `courses/services/local_course_seed.py:68`; `scripts/build_public_projection.py:2918` |

### Ledger-pinned CMP copies (§0.1) — none deletable in isolation

| Script | What it does | Verdict | Evidence |
| --- | --- | --- | --- |
| `add_data.py` | Configures local social apps and writes hand-written Homework 1/2 questions against `course_management.settings` | Superseded + ledger-pinned | Superseded by `seed_local_social_providers` (`accounts/management/commands/`) and `seed_local_questions`; pinned at `copied-files.tsv:747` |
| `add_more_test_data.py` | Generates 20 random users, submissions, answers, homeworks and projects | Superseded + ledger-pinned | Superseded by `generate_production_like_leaderboard_data.py`; pinned at `copied-files.tsv:748` |
| `add_user.py` | Creates one user with a random password | Superseded + ledger-pinned | Superseded by `create_local_admin.py` / `bootstrap_development_owner`; pinned at `copied-files.tsv:749` |
| `analyze_scoring_bug.py` | One-off demonstration of a historical scoring bug; prints "run pull_project_data.py and load_project_data.py first" (`:34`) | Dead-but-pinned | Only refs are the ledger (`copied-files.tsv:750`) |
| `create_superuser.py` | Creates/resets a superuser against `course_management.settings` | Dead-but-pinned; explicitly forbidden | `_docs/runbooks/development-owner-bootstrap.md:44` says do **not** invoke it; `scripts/load_rds_export.py:753` shells to it from disabled code |
| `debug_score_project.py` | Step-by-step scoring debugger with traceback capture | Dead-but-pinned | Only ref is `copied-files.tsv:752` |
| `generate_production_like_leaderboard_data.py` | Generates production-like courses, learners and leaderboard rows | **Live** + ledger-pinned | `README.md:59`; `courses/services/local_course_seed.py:31` names it as the supported path |
| `load_project_data.py` | Loads a project-data JSONL into a local DB | Test-imported + ledger-pinned | `courses/tests/test_load_project_data_script.py:9,25` |
| `load_rds_export.py` | Legacy broad RDS-export loader; `main()` disabled, returns 2 | **Keep** — claim verified, see §3 | `courses/tests/test_load_rds_export_script.py:9`; `review_import/tests/test_workflow.py:36,2411-2417` |
| `move_criteria.py` | Copies/moves review criteria between courses (`--dry-run`, `--delete-in-source`) | Uncertain-but-pinned | Only ref is `copied-files.tsv:756`; plausibly still wanted operationally |
| `pull_project_data.py` | Dumps all models for one project from the production DB to JSONL | Dead-but-pinned; **not** a duplicate of `load_project_data.py` | The two are the export/import halves of one pair (`scripts/analyze_scoring_bug.py:34`) |
| `score_project.py` | CLI wrapper around `courses.project_scoring.score_project` under `course_management.settings` | Superseded + ledger-pinned | Superseded by `score_project_dev.py` (same job, `website.settings.local`, `scripts/score_project_dev.py:22`) |
| `score_project_dev.py` | Scores a project against the local SQLite dev DB | Uncertain-but-pinned | `copied-files.tsv:760`; patched for local settings per `integration-patches.md:154` |
| `wrapped.py` | 17-line one-off: recompute 2025 wrapped statistics and set `is_visible = True` | Dead-but-pinned | No caller anywhere; supersedable by a management command |

### Import-consolidation path — do not decide here

| Path | What it does | Verdict |
| --- | --- | --- |
| `import_historical_zoomcamp_data.py` | Imports pre-2024 Zoomcamp scoring/certificate history from a `zoomcamp-scoring` checkout | Absorbed by the import consolidation. **Zero references** repo-wide, but it is the only entry point for its 6-module package |
| `historical_import/editions.py`, `certificate_import.py`, `scoring_import.py`, `homework_content.py`, `email_recovery.py`, `identity.py` | Edition discovery, certificate/scoring import, homework text, email recovery, identity mapping | Absorbed by the import consolidation. Imported only from `import_historical_zoomcamp_data.py:61-63` and each other |
| `review_import/` (`cli`, `workflow`, `manifest`, `admin`, `environment`, `middleware`) | The sanitized SQLite→SQLite local review-data import | **Live.** `Makefile:482-503` via `scripts/build_local_review_db.py`; `website/settings/local_review.py:48` |

---

## 2. Summary table — management commands

Commands in `accounts`, `courses`, `data` are pinned by §0.2. Everything below is Live unless
noted.

| Command | App | Verdict | Primary caller |
| --- | --- | --- | --- |
| `account_identity_inventory` | accounts | Live (runbook) | `_docs/runbooks/account-reconciliation.md:37` |
| `bootstrap_development_owner` | accounts | Live | `_docs/runbooks/development-owner-bootstrap.md`; `playwright_tests/test_development_owner_credentials.py:113` |
| `scripts/prod/import_account_reconciliation.py` (was `reconcile_accounts`) | accounts | Live (runbook) | `_docs/runbooks/account-reconciliation.md`; merge logic in `scripts/prod/account_reconciliation` |
| `seed_local_social_providers` | accounts | Live | `accounts/tests/test_local_provider_seed.py:85` + §0.2 |
| `audit_datamailer_recipient_lists` | courses | Live | `studio_courses/views/datamailer_operations.py:51` |
| `datamailer_campaign` / `datamailer_status` | courses | Live | Studio operations + tests |
| `import_development_course_content` | courses | **Live, keep** | `courses/tests/test_development_content_import.py:310-320`; §0.2. Imports *real* course content, not placeholders |
| `scripts/prod/sync_course_repositories.py` (was `pull_course_repositories`) | content_sync | Live | `scripts/prepare_local_data.py`; `Makefile` `content-pull`. The one route into the curriculum tables, shared with the signed GitHub push webhook |
| `register_course_repository` / `scripts/prod/sync_course_repository_sources.py` (was `seed_course_repository_sources`) | content_sync | Live | `Makefile` `content-sources`; `_docs/runbooks/course-content-push-and-pull.md` |
| `preview_peer_review_email` | courses | Live | `courses/tests/test_datamailer_peer_review.py:283` |
| `seed_local_courses` | courses | **Live, keep** | `README.md:47`; `scripts/prepare_local_data.py:290`. Source of the "Practice assignment" text (§0.3) |
| `seed_local_project_review` | courses | Live | `README.md:65`; `courses/tests/test_local_project_review_seed.py:101` |
| `seed_local_questions` | courses | **Live, keep** | `courses/tests/test_local_question_seed.py:289`; §0.2 |
| `send_deadline_reminders` | courses | Live (scheduled) | `courses/tests/deadline_reminder_base.py:56`; `_docs/runbooks/production-hosting-and-dns-migration.md:1452` |
| `sync_datamailer_contacts` / `sync_datamailer_recipient_lists` | courses | Live | `studio_courses/views/datamailer_operations.py:33,41` |
| `upsert_datamailer_templates` | courses | Live | `course_management/datamailer_templates/README.md:15` |
| `verify_development_course_content` | courses | Live (runbook) | `_docs/runbooks/development-course-content-bootstrap.md:72` |
| `datamailer_callback_status` / `datamailer_outbox_status` / `datamailer_send_status` | data | Live | `data/management/commands/monitoring_datamailer_health.py:5,9,13` |
| `monitoring_datamailer_health` | data | Live (production probe) | `_docs/runbooks/production-hosting-and-dns-migration.md:1452,2131` |
| `process_datamailer_outbox` | data | Live | `courses/tests/test_datamailer_outbox_memberships.py:62` |
| `scripts/prod/sync_public_media_hydrate.py` / `sync_public_media_publish.py` / `sync_public_media_verify.py` (was `public_media_hydrate` / `public_media_publish` / `public_media_verify`) | content | Live (runbook) | `_docs/runbooks/public-media-objects.md:105,111,117` |
| `register_course_repository` | content_sync | Test-only | `content_sync/tests/test_course_repository_registration.py:11` |
| `verify_dtc_content` | content_sync | Live (CI gate) | `Makefile:124` |
| `compatibility_gate` | core | Live (CI gate) | `Makefile:319` |
| `sync_studio_roles` | core | Test-only | `accounts/tests/test_studio_foundation.py:68` |
| `import_event_identities` | events | **Retired** | Redundant with `scripts/prod/import_events.py`'s `import_identities()`, which calls the same `events.identity.import_identity_manifest`. `scripts/prepare_local_data.py` now calls that function directly; the command and its `import_event_identity_manifest` alias are deleted |
| `backfill_event_qna` | events | **Dead** | Zero references repo-wide |
| `retry_event_qna` | events | Superseded | The service it wraps is reachable from Studio (`events/qna/studio_views.py:121`) and the admin API (`management_api/views.py:1091`); the CLI itself has no caller |
| `run_job_worker` | jobs | Live (production) | `entrypoint.sh:11`; `Makefile:480` |
| `run_job_scheduler` | jobs | **Superseded** | `run_job_worker` already runs `q2_scheduler` under a lease (`jobs/management/commands/run_job_worker.py:11,18-25`); zero references to the standalone |
| `relay_durable_jobs` | jobs | **Uncertain** | Zero references, but it is the durable-job expiry/heartbeat sweep — likely intended for cron |
| `check_management_parity` / `generate_admin_openapi` | management_api | Live (CI gates) | `Makefile:145,143` |

---

## 3. Verification of the stated starting points

**`load_rds_export.py` — the disabling comment's claim is TRUE. Keep the module.**
`scripts/load_rds_export.py:831-837` disables `main()` with the comment "Keep the copied helper
internals available to the adopted characterization tests". Two test modules do import those
internals:

- `courses/tests/test_load_rds_export_script.py:9-19` imports nine names
  (`ColumnDefault`, `ColumnCopyData`, `CopySummary`, `ImportedTable`, `TableCopyPlan`,
  `django_field_default`, `missing_required_columns`, `print_copy_summary`,
  `refresh_sqlite_sequences`).
- `review_import/tests/test_workflow.py:36,2411-2417` patches `parse_args`,
  `resolve_import_paths`, `rebuild_database`, `replace_rebuilt_database` and asserts
  `main()` returns the disabled path pointing at `scripts/build_local_review_db.py`.

It is also ledger-pinned (`copied-files.tsv:755`) and named as a permitted exception in
`_docs/architecture/database-portability.md:79`. Deleting it breaks two test modules, one CI
gate and one architecture contract.

**`create_superuser.py` vs `create_local_admin.py` — not duplicates.**
`create_local_admin.py:14` targets `website.settings.local` and is the documented local path
(`README.md:29`). `create_superuser.py:20` targets the preserved `course_management.settings`
and is explicitly forbidden by `_docs/runbooks/development-owner-bootstrap.md:44`. The second
is dead in practice but ledger-pinned.

**`pull_project_data.py` vs `load_project_data.py` — not duplicates.** Export half and import
half of one debugging pair (`scripts/analyze_scoring_bug.py:34`). `load_project_data.py` is
additionally test-imported.

**The placeholder generators.** Fully answered in §0.3: none of them is what puts
`Project Attempt N` / `Practice assignment for …` on the running site.

**`build_public_projection.py`, `build_pinned_legacy_sources.py`, `build_legacy_manifest.py`,
`repin_projection_digests.py` — confirmed sole-reproducer scripts.** Each is the only
executable record of how a checked-in artifact was produced:

- `build_public_projection.py` → `content/public_projection/*.json`; recorded as the builder
  inside the artifact itself (`content/public_data.py:584`,
  `content/public_projection/editorial_route_migration.json:23022`) and asserted as a schema
  constant (`_docs/compatibility/editorial-route-migration.schema.json:39`).
  `_docs/runbooks/production-hosting-and-dns-migration.md:1633` calls it "the sole builder".
- `build_legacy_manifest.py` → `_docs/compatibility/legacy-manifest.jsonl`,
  `legacy-manifest-differences.json`, `public-contracts.jsonl` (`Makefile:299-306`).
- `build_pinned_legacy_sources.py` → the pinned legacy checkouts (`Makefile:296`).
- `repin_projection_digests.py` → the projection manifest's digest/scope fields; exists
  precisely because a full rebuild is not currently possible (issue #253, per its docstring).
- Add to that list: `build_article_faq.py` → `content/article_faq.py:10` and
  `build_event_description_bridge.py` → the checked bridge. The course-module snapshot
  manifest and its builder are gone: course content now comes in through
  `scripts/prod/sync_course_repositories.py`, the same ingestion the push webhook drives.

---

## 4. Ranked deletion list — 6 candidates, safest first

The list is short because §0.1 and §0.2 block most of what looks dead. These six are the only
files with no reference, no ledger pin, and no registry pin at `fbab381`.

| # | Path | Evidence nothing references it | What breaks if the evidence is wrong |
| --- | --- | --- | --- |
| 1 | `events/management/commands/backfill_event_qna.py` | Repo-wide grep for `backfill_event_qna` (excluding `.venv`, `.git`, `.tmp`) returns only the file itself. Not in `SOURCE_COMMAND_APPS`, so not in `EXPECTED_COMMANDS`. No `deploy/`, Terraform, `entrypoint.sh` or workflow reference | A one-time Q&A backfill an operator was going to run by hand becomes unavailable; `events/qna/services.ensure_event_qna` stays reachable from Studio, so nothing automated breaks |
| 2 | `jobs/management/commands/run_job_scheduler.py` | Zero references. Superseded: `run_job_worker` imports the same `q2_scheduler` and lease helpers (`jobs/management/commands/run_job_worker.py:11,18-25`) and is the only command in `entrypoint.sh:11` | If a future deployment topology wants a scheduler container separate from workers, this is the file that provided it. Confirm the deploy plan first |
| 3 | `events/management/commands/import_event_identity_manifest.py` | **Done.** Deleted along with `import_event_identities.py` itself: `scripts/prepare_local_data.py` now calls `scripts/prod/import_events.py`'s `import_identities()` directly, the same function the alias's target ultimately called | n/a |
| 4 | `events/management/commands/retry_event_qna.py` | Zero references to the command. Its service `retry_event_qna_provision` remains reachable from `events/qna/studio_views.py:121`, `management_api/views.py:1091` and `events/qna/capabilities.py:151` | Loses the CLI escape hatch for retrying a blocked provisioning when Studio is down. Lower priority than 1-3 for that reason |
| 5 | `scripts/capture_screenshots.py` | No invocation anywhere. `Makefile:48` and `Makefile:83` list it only in the mypy/lint file sets, never in a recipe. No reference in `_docs/`, `.claude/`, or any workflow | This is the tester role's screenshot tool per `AGENTS.md`. If testers actually run it ad hoc, deleting it removes their tool. **Ask before deleting.** Also requires removing both `Makefile` lint entries |
| 6 | `jobs/management/commands/relay_durable_jobs.py` | Zero references. Wraps `relay_due_jobs`, `sweep_expired_jobs`, `prune_stale_heartbeats` | **Weakest candidate.** This looks like a cron entry point for durable-job recovery that was never wired. If durable jobs rely on an external sweep, deleting it silently strands expired jobs. Confirm against the jobs spec before touching |

Recommended: delete 1-3 now, ask the owner about 4-5, and leave 6 until the durable-jobs
operational model is confirmed.

### Deletable only as a groomed ledger change (not in the list above)

`scripts/wrapped.py` (17 lines, zero callers), `scripts/analyze_scoring_bug.py`,
`scripts/debug_score_project.py`, `scripts/create_superuser.py`, `scripts/add_user.py`,
`scripts/add_data.py`, `scripts/add_more_test_data.py`, `scripts/score_project.py`,
`scripts/pull_project_data.py` — nine files, ~2,000 lines, genuinely unused. Each needs the
§0.1 four-part ledger amendment. If the owner's goal is "fewer scripts in the folder", this is
where the volume is, and it is one issue, not nine.

---

## 5. Looks redundant — keep anyway

| Path | Why it looks deletable | Why it must stay |
| --- | --- | --- |
| `scripts/load_rds_export.py` | `main()` is disabled and returns 2 | Two test modules import its internals (§3). Also a named exception in `_docs/architecture/database-portability.md:79` |
| `scripts/load_project_data.py` | Sibling of the unused `pull_project_data.py` | `courses/tests/test_load_project_data_script.py:9,25` imports it |
| `scripts/production_like_course_specs.json` | Looks like fixture junk; source of "Project Attempt N" | SHA-256-pinned; read by `courses/services/local_course_seed.py:68` **and** `scripts/build_public_projection.py:2918`; asserted by `content/tests/test_public_projection_builder.py:26` |
| `courses/.../seed_local_courses.py`, `seed_local_questions.py`, `import_development_course_content.py` | Named as placeholder generators | All three are pinned by `EXPECTED_COMMANDS` (§0.2) and covered by tests; `seed_local_courses` is a step in `make production-prep-*`. `import_development_course_content` imports real content, not placeholders |
| `scripts/generate_production_like_leaderboard_data.py` | Looks like a fixture generator | `README.md:59` names it the supported path, and `courses/services/local_course_seed.py:31` defers to it for participants. Ledger-pinned |
| `scripts/build_synthetic_design_review_db.py` | Produces synthetic data | It is the durable build contract for the issue-#237 sitewide design review (`_docs/testing/issue-237-review-state-matrix.md:18`), and deliberately reads no production or pinned catalogue data |
| `scripts/build_local_review_db.py` | 19-line shim | Three `Makefile` recipes call it (`485,492,499`), and `review_import/tests/test_workflow.py:2426` asserts the disabled loader points at it by name |
| `scripts/verify_course_platform_vendor_assets.py` | No CLI caller | `core/tests/test_course_platform_vendor_assets.py:17` imports it — the import *is* the dependency |
| `scripts/validate_*.py` (three files) | Validate frozen historical audits | Each is the fail-closed contract for its audit and is exercised by a test under `scripts/tests/`. `_docs/adoption/course-platform/cadmin-reference-allowlist.tsv:41` names one explicitly as retained |
| `scripts/render_course_platform_inventory.py` | Only renders a doc | `scripts/build_pinned_legacy_sources.py:745` imports `route_entries` at runtime, and `core/tests/test_course_platform_adoption.py:12` imports five names |
| `scripts/verify_course_platform_adoption.py` | Only used by docs | `scripts/sync_course_platform.py:37,51,54` and `scripts/prepare_course_platform_source.py:15` import it |
| `scripts/repin_projection_digests.py` | Looks like a one-off fixup | It is the only supported way to move the projection digest scope without a full rebuild, which issue #253 says is impossible today |
| `scripts/process_illustration.py` | Looks like a personal tool | `_docs/design/illustration-assets.md:22,31` is the documented procedure for every homepage illustration |
| `scripts/historical_import/*` (6 modules) | Only one caller | That caller is `import_historical_zoomcamp_data.py`, which is in the import consolidation's path — decide the package's fate there, not here |

---

## 6. Open questions

1. **`scripts/capture_screenshots.py`** — is it part of the tester role's actual workflow? It
   has no invocation, but `AGENTS.md` makes screenshots a lifecycle step. Only the owner or a
   tester can answer.
2. **`jobs/management/commands/relay_durable_jobs.py`** — is durable-job expiry swept by an
   external scheduler, or does `run_job_worker` cover it? Nothing in `deploy/`, `entrypoint.sh`
   or Terraform invokes it, and no doc describes the intended topology.
3. **`scripts/move_criteria.py` and `scripts/score_project_dev.py`** — no caller, but both are
   plausible operational tools the owner uses by hand. Ledger-pinned either way.
4. **`_docs/runbooks/production-hosting-and-dns-migration.md:1175` references
   `scripts/smoke_test_relay.py`, which does not exist in the repository.** Either the runbook
   describes unbuilt work or the script was deleted without updating the runbook. Worth
   resolving before cutover, since it is named as a post-deploy smoke test.
5. **Is the CMP adoption ledger still load-bearing?** The whole of §0.1 exists to prove a
   literal copy of an upstream commit. If the adoption is considered complete, retiring the
   ledger would unblock ~2,000 lines of dead-script deletion in one move. That is a product
   decision, not an audit finding.
6. `scripts/import_historical_zoomcamp_data.py` has **zero** references of any kind — no Make
   target, no CI, no runbook, no test. Whether that means "not yet wired" or "abandoned" is a
   question for the import-consolidation work.
